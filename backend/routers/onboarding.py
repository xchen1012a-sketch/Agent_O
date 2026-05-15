from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing_extensions import Annotated

from api_response import success_response
from auth import get_current_user, normalize_app_role
from db_stage3 import get_conn, json_text, now_iso
from training_cycle_service import create_stage_cycle, get_active_cycle
from training_plan import build_unlock_matrix

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class OnboardingRequest(BaseModel):
    ability_summary: str = ""
    target_direction: str = ""
    employee_stage: str = ""
    position: str = ""
    mentor_name: str = ""
    notes: str = ""


def _load_user_row(conn, uid: str):
    return conn.execute(
        """
        SELECT CAST(id AS TEXT) AS row_id, COALESCE(user_id, '') AS user_id, COALESCE(username, '') AS username,
               COALESCE(display_name, '') AS display_name, COALESCE(name, '') AS name, COALESCE(role, '') AS role,
               COALESCE(store_id, '') AS store_id, COALESCE(onboarding_completed, 0) AS onboarding_completed,
               COALESCE(training_cycle_id, '') AS training_cycle_id, COALESCE(current_cycle_day, 0) AS current_cycle_day
        FROM users
        WHERE CAST(id AS TEXT) = ? OR user_id = ?
        LIMIT 1
        """,
        (uid, uid),
    ).fetchone()


def _build_growth_markdown(display_name: str, position: str, target_direction: str, ability_summary: str) -> str:
    return "\n".join(
        [
            f"# 新人成长计划 - {display_name}",
            "",
            "## 基本信息",
            f"- 岗位：{position or '导购'}",
            f"- 培养方向：{target_direction or '提升产品讲解与销售转化'}",
            f"- 当前能力：{ability_summary or '待评估'}",
            "",
            "## 阶段目标",
            "- 阶段 1：基础认知",
            "- 阶段 2：销售转化与上岗",
        ]
    )


@router.get("/status")
def get_onboarding_status(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "")
    with get_conn() as conn:
        user = _load_user_row(conn, uid)
        has_plan = bool(
            conn.execute(
                "SELECT 1 FROM growth_plan_records WHERE user_id = ? OR employee_id = ? LIMIT 1",
                (uid, uid),
            ).fetchone()
        )
        cycle = get_active_cycle(conn, uid)
        onboarding_completed = bool(user["onboarding_completed"]) if user else False
        current_stage_no = int(cycle.get("stage_no") or 0) if cycle else 0
        current_stage_name = str(cycle.get("stage_name") or "") if cycle else ""
        stage_status = str(cycle.get("stage_status") or "") if cycle else ""
        matrix = build_unlock_matrix(
            stage_no=current_stage_no or 1,
            stage_passed=stage_status == "passed",
            onboarding_completed=onboarding_completed,
            is_retraining=bool(cycle and str(cycle.get("cycle_type") or "") == "retraining"),
        )
    return success_response(
        data={
            "onboarding_completed": onboarding_completed,
            "has_plan": has_plan,
            "cycle_id": cycle.get("cycle_id") if cycle else "",
            "current_day": int(cycle.get("current_day") or 0) if cycle else 0,
            "onboarding_locked": not onboarding_completed,
            "current_stage_no": current_stage_no,
            "current_stage_name": current_stage_name,
            "stage_status": stage_status,
            "module_unlocks": matrix["modules"],
            "force_redirect_page": matrix["force_redirect_page"],
        },
        workflow_code="onboarding",
    )


@router.post("/complete")
def complete_onboarding(
    body: OnboardingRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "")
    with get_conn() as conn:
        user = _load_user_row(conn, uid)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if normalize_app_role(str(user["role"] or "")) != "trainee":
            raise HTTPException(status_code=403, detail="仅新员工需要完成新手引导")
        if bool(user["onboarding_completed"]):
            raise HTTPException(status_code=400, detail="当前账号已完成新手引导")

        now = now_iso()
        display_name = str(user["display_name"] or user["name"] or user["username"] or "新员工").strip()
        position = str(body.position or "").strip() or "导购"
        store_id = str(user["store_id"] or "").strip()
        conn.execute(
            """
            INSERT INTO employee_profiles (
                employee_id, user_id, employee_name, position, job_title,
                store_id, role, source, mentor_name, self_intro, historical_learning, initial_ability,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                employee_name = excluded.employee_name,
                position = excluded.position,
                job_title = excluded.job_title,
                store_id = excluded.store_id,
                mentor_name = excluded.mentor_name,
                self_intro = excluded.self_intro,
                historical_learning = excluded.historical_learning,
                initial_ability = excluded.initial_ability,
                updated_at = excluded.updated_at
            """,
            (
                uid,
                uid,
                display_name,
                position,
                position,
                store_id,
                "trainee",
                "onboarding",
                body.mentor_name or "",
                body.notes or "",
                body.target_direction or "",
                body.ability_summary or "",
                now,
                now,
            ),
        )

        plan_id = f"gp_ob_{uuid.uuid4().hex[:10]}"
        plan_meta = {
            "source": "onboarding",
            "employee_stage": body.employee_stage or "new_employee",
            "mentor_name": body.mentor_name or "",
            "notes": body.notes or "",
            "target_direction": body.target_direction or "",
            "ability_summary": body.ability_summary or "",
        }
        conn.execute(
            """
            INSERT INTO growth_plan_records (
                user_id, growth_plan_text, plan_meta_json, source_workflow, created_at,
                plan_id, employee_id, employee_name, position, store_id, mentor_name,
                ability_summary, target_direction, payload_json, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                _build_growth_markdown(display_name, position, body.target_direction, body.ability_summary),
                json_text(plan_meta),
                "onboarding",
                now,
                plan_id,
                uid,
                display_name,
                position,
                store_id,
                body.mentor_name or "",
                body.ability_summary or "",
                body.target_direction or "",
                json_text(plan_meta),
                uid,
            ),
        )

        cycle = create_stage_cycle(conn, user_id=uid, plan_id=plan_id, stage_no=1, cycle_type="onboarding")
        conn.execute(
            """
            UPDATE users
            SET onboarding_completed = 1, onboarding_completed_at = ?, training_cycle_id = ?, current_cycle_day = 1, updated_at = ?
            WHERE CAST(id AS TEXT) = ? OR user_id = ?
            """,
            (now, cycle.get("cycle_id") or "", now, uid, uid),
        )

    return success_response(
        data={
            "cycle_id": cycle.get("cycle_id") or "",
            "plan_id": plan_id,
            "day1_tasks": [
                {
                    "task_code": item.get("task_code"),
                    "title": item.get("title"),
                    "task_type": item.get("task_type"),
                    "module_code": item.get("module_code"),
                    "module_name": item.get("module_name"),
                }
                for item in (cycle.get("daily_plan_json") and json.loads(cycle["daily_plan_json"])[0]["tasks"] or [])
            ],
        },
        workflow_code="onboarding",
    )
