from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing_extensions import Annotated

import logging

from api_response import success_response
from auth import get_current_user, normalize_app_role
from db_stage3 import get_conn, now_iso
from dify_wf15 import run_wf15_unlock
from training_plan import build_stage_definitions
from training_cycle_service import (
    create_stage_cycle,
    get_active_cycle,
    get_cycle,
    refresh_module_snapshots,
    release_current_stage_days,
    stage_review_payload,
    void_cycle,
)

router = APIRouter(prefix="/api/training-cycle", tags=["training-cycle"])

_log = logging.getLogger("jewelry_qipei.training_cycle")


class RefreshDayRequest(BaseModel):
    cycle_id: str
    day_index: int


class CompleteTaskRequest(BaseModel):
    cycle_id: str
    task_code: str
    day_index: int


class StartStageRequest(BaseModel):
    user_id: str
    plan_id: str = ""
    stage_no: int = 1
    cycle_type: str = "onboarding"
    previous_cycle_id: str = ""
    source_reset_days: int | None = None
    release_all: bool = False


class ReleaseStageDaysRequest(BaseModel):
    cycle_id: str


class ReviewStageRequest(BaseModel):
    cycle_id: str


class VoidCycleRequest(BaseModel):
    cycle_id: str


class AdminManualTaskCheckRequest(BaseModel):
    cycle_id: str
    task_code: str
    day_index: int


class AdminManualTaskUncheckRequest(BaseModel):
    cycle_id: str
    task_code: str
    day_index: int


def _manager_only(current_user: dict) -> None:
    if normalize_app_role(str(current_user.get("role") or "")) not in {"admin", "store_manager"}:
        raise HTTPException(status_code=403, detail="仅管理员或店长可执行该操作")


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _stage_name_by_no(stage_no: int) -> str:
    for item in build_stage_definitions():
        if int(item.get("stage_no") or 0) == int(stage_no):
            return str(item.get("stage_name") or "")
    return ""


def _build_stage_unlock_event(
    *,
    cycle: dict[str, Any],
    payload: dict[str, Any],
    next_cycle_id: str,
    wf15_result: dict[str, Any],
) -> dict[str, Any] | None:
    if not payload.get("is_pass"):
        return None

    current_stage = int(cycle.get("stage_no") or 1)
    total_stages = len(build_stage_definitions())
    next_stage = current_stage + 1 if current_stage < total_stages else current_stage
    next_stage_name = _stage_name_by_no(next_stage)
    if current_stage >= total_stages:
        next_stage_name = "独立上岗"

    return {
        "type": "stage_unlocked" if current_stage < total_stages else "onboarding_completed",
        "stage": next_stage,
        "name": next_stage_name,
        "passed_stage": current_stage,
        "passed_stage_name": str(cycle.get("stage_name") or _stage_name_by_no(current_stage)),
        "review_score": float(payload.get("review_score") or 0),
        "next_cycle_id": next_cycle_id,
        "next_route": str((wf15_result or {}).get("next_route") or "training_path"),
    }


def _extract_plan_cycle_days(row: dict[str, Any] | None) -> int:
    if not row:
        return 90
    plan_meta = _parse_json_dict(row.get("plan_meta_json"))
    payload = _parse_json_dict(row.get("payload_json"))
    for value in (plan_meta.get("plan_cycle_days"), payload.get("plan_cycle_days")):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        if digits:
            try:
                return max(30, min(180, int(digits)))
            except Exception:
                continue
    return 90


def _employee_no(row_id: Any) -> str:
    try:
        return f"EMP{int(row_id):05d}"
    except (TypeError, ValueError):
        return str(row_id or "").strip()


def _load_user_identity(conn, user_id: str) -> dict[str, str]:
    row = conn.execute(
        """
        SELECT
            CAST(u.id AS TEXT) AS row_id,
            COALESCE(u.user_id, '') AS user_id,
            COALESCE(u.username, '') AS username,
            COALESCE(u.display_name, '') AS display_name,
            COALESCE(u.name, '') AS name,
            COALESCE(u.role, '') AS role,
            COALESCE(u.store_id, '') AS store_id,
            COALESCE(ep.employee_name, '') AS employee_name,
            COALESCE(ep.position, '') AS position,
            COALESCE(ep.store_id, '') AS profile_store_id,
            COALESCE(ep.mentor_name, '') AS mentor_name
        FROM users u
        LEFT JOIN employee_profiles ep
          ON ep.employee_id = COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT))
          OR ep.user_id = COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT))
        WHERE CAST(u.id AS TEXT) = ? OR u.user_id = ?
        ORDER BY ep.id DESC
        LIMIT 1
        """,
        (user_id, user_id),
    ).fetchone()
    if not row:
        return {
            "user_id": user_id,
            "employee_no": _employee_no(user_id),
            "display_name": "",
            "username": "",
            "role": "",
            "store_id": "",
            "position": "",
            "mentor_name": "",
        }
    display_name = str(row["employee_name"] or row["display_name"] or row["name"] or row["username"] or "").strip()
    position = str(row["position"] or "").strip()
    role = str(row["role"] or "").strip()
    if not position:
        role_map = {
            "admin": "管理员",
            "store_manager": "店长",
            "senior_consultant": "资深顾问",
            "trainee": "导购",
        }
        position = role_map.get(normalize_app_role(role), role or "导购")
    return {
        "user_id": str(row["user_id"] or row["row_id"] or user_id).strip() or user_id,
        "employee_no": _employee_no(row["row_id"] or user_id),
        "display_name": display_name,
        "username": str(row["username"] or "").strip(),
        "role": role,
        "store_id": str(row["profile_store_id"] or row["store_id"] or "").strip(),
        "position": position,
        "mentor_name": str(row["mentor_name"] or "").strip(),
    }


def _compose_store_label(store_name: str, store_id: str) -> str:
    name = str(store_name or "").strip()
    sid = str(store_id or "").strip()
    if name and sid and name != sid:
        return f"{name}({sid})"
    return name or sid


def _resolve_training_identity_labels(
    conn,
    *,
    store_id: str,
    role: str,
    fallback_position: str = "",
) -> dict[str, str]:
    sid = str(store_id or "").strip()
    role_key = str(role or "").strip()
    store_name = ""
    role_label = ""
    if sid:
        store_row = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(store_name), ''), NULLIF(TRIM(name), ''), '') AS store_name
            FROM stores
            WHERE LOWER(TRIM(COALESCE(store_id, ''))) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if store_row:
            store_name = str(store_row["store_name"] or "").strip()
    if role_key:
        role_row = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(display_name), ''), '') AS display_name
            FROM role_settings
            WHERE LOWER(TRIM(COALESCE(role_key, ''))) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (role_key,),
        ).fetchone()
        if role_row:
            role_label = str(role_row["display_name"] or "").strip()
    if not role_label:
        role_map = {
            "admin": "管理员",
            "store_manager": "店长",
            "senior_consultant": "资深顾问",
            "trainee": "导购",
        }
        role_label = str(fallback_position or "").strip() or role_map.get(normalize_app_role(role_key), role_key)
    return {
        "store_name": store_name,
        "store_label": _compose_store_label(store_name, sid),
        "role_label": role_label,
    }


def _load_user_identity(conn, user_id: str) -> dict[str, str]:
    row = conn.execute(
        """
        SELECT
            CAST(u.id AS TEXT) AS row_id,
            COALESCE(u.user_id, '') AS user_id,
            COALESCE(u.username, '') AS username,
            COALESCE(u.display_name, '') AS display_name,
            COALESCE(u.name, '') AS name,
            COALESCE(u.role, '') AS role,
            COALESCE(u.store_id, '') AS store_id,
            COALESCE(ep.employee_name, '') AS employee_name,
            COALESCE(ep.position, '') AS position,
            COALESCE(ep.store_id, '') AS profile_store_id,
            COALESCE(ep.mentor_name, '') AS mentor_name
        FROM users u
        LEFT JOIN employee_profiles ep
          ON ep.employee_id = COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT))
          OR ep.user_id = COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT))
        WHERE CAST(u.id AS TEXT) = ? OR u.user_id = ?
        ORDER BY ep.id DESC
        LIMIT 1
        """,
        (user_id, user_id),
    ).fetchone()
    if not row:
        return {
            "user_id": user_id,
            "employee_no": _employee_no(user_id),
            "display_name": "",
            "username": "",
            "role": "",
            "role_label": "",
            "store_id": "",
            "store_name": "",
            "store_label": "",
            "position": "",
            "mentor_name": "",
        }
    display_name = str(row["employee_name"] or row["display_name"] or row["name"] or row["username"] or "").strip()
    position = str(row["position"] or "").strip()
    role = str(row["role"] or "").strip()
    store_id = str(row["profile_store_id"] or row["store_id"] or "").strip()
    labels = _resolve_training_identity_labels(
        conn,
        store_id=store_id,
        role=role,
        fallback_position=position,
    )
    return {
        "user_id": str(row["user_id"] or row["row_id"] or user_id).strip() or user_id,
        "employee_no": _employee_no(row["row_id"] or user_id),
        "display_name": display_name,
        "username": str(row["username"] or "").strip(),
        "role": role,
        "role_label": labels["role_label"],
        "store_id": store_id,
        "store_name": labels["store_name"],
        "store_label": labels["store_label"],
        "position": position or labels["role_label"],
        "mentor_name": str(row["mentor_name"] or "").strip(),
    }


def _resolve_actor_store_id(conn, current_user: dict[str, Any]) -> str:
    actor_user_id = str(current_user.get("user_id") or "").strip()
    if not actor_user_id:
        return ""
    identity = _load_user_identity(conn, actor_user_id)
    return str(identity.get("store_id") or "").strip()


def _ensure_store_manager_same_store(
    conn,
    current_user: dict[str, Any],
    *,
    target_store_id: str,
    detail: str,
) -> None:
    if normalize_app_role(str(current_user.get("role") or "")) != "store_manager":
        return
    actor_store_id = _resolve_actor_store_id(conn, current_user)
    target_store_id = str(target_store_id or "").strip()
    if not actor_store_id or not target_store_id or actor_store_id != target_store_id:
        raise HTTPException(status_code=403, detail=detail)


def _load_growth_plan_row(conn, *, user_id: str, plan_id: str = "") -> dict[str, Any] | None:
    if plan_id:
        row = conn.execute(
            """
            SELECT *
            FROM growth_plan_records
            WHERE plan_id = ? AND (user_id = ? OR employee_id = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (plan_id, user_id, user_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT *
            FROM growth_plan_records
            WHERE user_id = ? OR employee_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, user_id),
        ).fetchone()
    plan = dict(row) if row else None
    if not plan:
        return None
    target_plan_id = str(plan.get("plan_id") or "").strip()
    if not target_plan_id:
        return plan
    cycle_stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN status IN ('active', 'waiting_review') THEN 1 ELSE 0 END) AS active_count
        FROM training_cycles
        WHERE user_id = ? AND plan_id = ?
        """,
        (user_id, target_plan_id),
    ).fetchone()
    total_count = int((cycle_stats["total_count"] if cycle_stats else 0) or 0)
    active_count = int((cycle_stats["active_count"] if cycle_stats else 0) or 0)
    if total_count > 0 and active_count == 0:
        return None
    return plan


def _create_seed_growth_plan(conn, *, user_id: str, actor_user_id: str) -> dict[str, Any]:
    identity = _load_user_identity(conn, user_id)
    plan_id = f"gp_tm_{uuid.uuid4().hex[:10]}"
    now = now_iso()
    payload = {
        "source": "training_manage_seed",
        "workflow_mode": "training_manage_seed",
        "target_direction": "围绕阶段训练补齐关键能力短板",
        "employee_stage": "在岗提升",
        "plan_cycle_days": 90,
        "stage_goals": [
            "先完成当前两阶段 7 天训练。",
            "围绕学习、练习、考核结果沉淀成长复盘。",
            "根据阶段评估结果继续补强短板。",
        ],
        "recommended_courses": [
            {"course_code": "TM-101", "course_name": "产品知识与表达", "priority": "high"},
            {"course_code": "TM-201", "course_name": "异议处理与成交推进", "priority": "high"},
        ],
        "practice_tasks": [
            {
                "title": "完成阶段训练中的每日练习任务。",
                "module_code": "product_basics",
                "scene_code": "product_consultation",
                "difficulty": "standard",
            },
            {
                "title": "在门店场景中复盘关键成交动作。",
                "module_code": "closing_conversion",
                "scene_code": "closing_conversion",
                "difficulty": "standard",
            },
        ],
    }
    plan_meta = {
        "source": "training_manage_seed",
        "plan_cycle_days": 90,
        "seeded_by_training_manage": True,
    }
    growth_plan_text = (
        "# 培训管理补齐成长计划\n\n"
        "- 来源：培训管理发起培训时自动补齐\n"
        "- 目标：保证培训周期与成长计划一一关联\n"
        "- 执行：先完成当前两阶段 7 天训练，再依据评估结果持续优化\n"
    )
    conn.execute(
        """
        INSERT INTO growth_plan_records (
            user_id, growth_plan_text, plan_meta_json, source_workflow, created_at,
            plan_id, employee_id, employee_name, position, store_id, mentor_name,
            ability_summary, target_direction, payload_json, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            growth_plan_text,
            json.dumps(plan_meta, ensure_ascii=False),
            "training_manage_seed",
            now,
            plan_id,
            user_id,
            identity["display_name"],
            identity["position"],
            identity["store_id"],
            identity.get("mentor_name") or "",
            "",
            "围绕阶段训练补齐关键能力短板",
            json.dumps(payload, ensure_ascii=False),
            actor_user_id,
        ),
    )
    return _load_growth_plan_row(conn, user_id=user_id, plan_id=plan_id) or {}


def _ensure_growth_plan_link(conn, *, user_id: str, requested_plan_id: str, actor_user_id: str) -> dict[str, Any]:
    plan = _load_growth_plan_row(conn, user_id=user_id, plan_id=requested_plan_id)
    if plan:
        return plan
    if requested_plan_id:
        raise HTTPException(status_code=404, detail="未找到指定成长计划")
    latest = _load_growth_plan_row(conn, user_id=user_id)
    if latest:
        return latest
    return _create_seed_growth_plan(conn, user_id=user_id, actor_user_id=actor_user_id)


def _growth_plan_summary(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "has_plan": False,
            "plan_id": "",
            "source_workflow": "",
            "target_direction": "",
            "plan_cycle_days": 90,
            "created_at": "",
        }
    payload = _parse_json_dict(row.get("payload_json"))
    return {
        "has_plan": True,
        "plan_id": str(row.get("plan_id") or "").strip(),
        "source_workflow": str(row.get("source_workflow") or "").strip(),
        "target_direction": str(row.get("target_direction") or payload.get("target_direction") or "").strip(),
        "plan_cycle_days": _extract_plan_cycle_days(row),
        "created_at": str(row.get("created_at") or "").strip(),
    }


def _group_cycle_days(tasks_rows: list[dict[str, Any]], total_days: int, current_day: int, unlock_map: dict[str, Any]) -> list[dict[str, Any]]:
    days = []
    for day_index in range(1, total_days + 1):
        day_tasks = [row for row in tasks_rows if int(row["day_index"]) == day_index]
        completed_count = sum(1 for row in day_tasks if str(row["status"]) == "completed")
        released = bool(unlock_map.get(str(day_index), False))
        if day_tasks and all(str(row["status"]) == "completed" for row in day_tasks):
            status = "completed"
        elif released or day_index <= current_day:
            status = "unlocked"
        else:
            status = "locked"
        days.append(
            {
                "day_index": day_index,
                "status": status,
                "tasks": day_tasks,
                "completed_count": completed_count,
                "total_tasks": len(day_tasks),
            }
        )
    return days


def _advance_cycle_after_day_complete(conn, *, cycle: dict[str, Any], day_index: int, now: str) -> None:
    total_days = int(cycle["total_days"] or 7)
    if day_index < total_days:
        next_day = day_index + 1
        unlock_map = json.loads(cycle.get("day_unlock_json") or "{}")
        unlock_map[str(next_day)] = True
        conn.execute(
            "UPDATE training_cycles SET current_day = CASE WHEN current_day < ? THEN ? ELSE current_day END, day_unlock_json = ?, updated_at = ? WHERE cycle_id = ?",
            (next_day, next_day, json.dumps(unlock_map, ensure_ascii=False), now, cycle["cycle_id"]),
        )
        conn.execute(
            """
            UPDATE cycle_daily_tasks
            SET status = CASE WHEN status = 'locked' THEN 'released' ELSE status END,
                release_status = CASE WHEN release_status = 'locked' THEN 'released' ELSE release_status END,
                released_at = COALESCE(released_at, ?), updated_at = ?
            WHERE cycle_id = ? AND day_index = ?
            """,
            (now, now, cycle["cycle_id"], next_day),
        )
    else:
        conn.execute(
            "UPDATE training_cycles SET status = 'waiting_review', stage_status = 'waiting_review', stage_completed_at = ?, updated_at = ? WHERE cycle_id = ?",
            (now, now, cycle["cycle_id"]),
        )


def _recalculate_cycle_progress(conn, *, cycle_id: str, now: str) -> dict[str, Any] | None:
    cycle = get_cycle(conn, cycle_id)
    if not cycle:
        return None
    total_days = max(int(cycle.get("total_days") or 0), 1)
    task_rows = conn.execute(
        """
        SELECT day_index, status, task_source, release_status
        FROM cycle_daily_tasks
        WHERE cycle_id = ?
        ORDER BY day_index ASC, sort_order ASC, id ASC
        """,
        (cycle_id,),
    ).fetchall()
    tasks_by_day: dict[int, list[dict[str, Any]]] = {day: [] for day in range(1, total_days + 1)}
    has_retry_tasks = False
    for row in task_rows:
        item = dict(row)
        day_no = int(item.get("day_index") or 0)
        if day_no in tasks_by_day:
            tasks_by_day[day_no].append(item)
        if str(item.get("task_source") or "") == "system_retry":
            has_retry_tasks = True

    prefix_complete_days = 0
    for day_index in range(1, total_days + 1):
        day_tasks = tasks_by_day.get(day_index) or []
        if day_tasks and all(str(task.get("status") or "") == "completed" for task in day_tasks):
            prefix_complete_days += 1
            continue
        break

    all_days_complete = prefix_complete_days >= total_days
    if all_days_complete:
        current_day = total_days
        cycle_status = "waiting_review"
        stage_status = "waiting_review"
        unlock_limit = total_days
    else:
        current_day = min(prefix_complete_days + 1, total_days)
        cycle_status = "active"
        stage_status = "failed" if has_retry_tasks else "active"
        unlock_limit = current_day

    unlock_map = {str(day): bool(day <= unlock_limit) for day in range(1, total_days + 1)}
    conn.execute(
        """
        UPDATE training_cycles
        SET status = ?, stage_status = ?, current_day = ?, day_unlock_json = ?, updated_at = ?
        WHERE cycle_id = ?
        """,
        (cycle_status, stage_status, current_day, json.dumps(unlock_map, ensure_ascii=False), now, cycle_id),
    )
    conn.execute(
        """
        UPDATE cycle_daily_tasks
        SET status = CASE WHEN status = 'completed' THEN status ELSE 'released' END,
            release_status = CASE WHEN release_status = 'locked' THEN 'released' ELSE release_status END,
            released_at = CASE
                WHEN status = 'completed' THEN released_at
                ELSE COALESCE(released_at, ?)
            END,
            updated_at = ?
        WHERE cycle_id = ? AND day_index <= ?
        """,
        (now, now, cycle_id, unlock_limit),
    )
    conn.execute(
        """
        UPDATE cycle_daily_tasks
        SET status = CASE WHEN status = 'completed' THEN status ELSE 'locked' END,
            updated_at = ?
        WHERE cycle_id = ? AND day_index > ?
        """,
        (now, cycle_id, unlock_limit),
    )
    conn.execute(
        """
        UPDATE users
        SET current_cycle_day = CASE WHEN training_cycle_id = ? THEN ? ELSE current_cycle_day END,
            updated_at = CASE WHEN training_cycle_id = ? THEN ? ELSE updated_at END
        WHERE CAST(id AS TEXT) = ? OR user_id = ?
        """,
        (cycle_id, current_day, cycle_id, now, str(cycle.get("user_id") or ""), str(cycle.get("user_id") or "")),
    )
    return get_cycle(conn, cycle_id)


@router.get("/admin/list")
def admin_list_cycles(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    q: str = "",
):
    """Admin/store_manager: list all users with their active training cycle."""
    actor_role = normalize_app_role(str(current_user.get("role") or ""))
    if actor_role not in {"admin", "store_manager"}:
        raise HTTPException(status_code=403, detail="仅管理员或店长可查看")
    actor_id = str(current_user.get("user_id") or "")
    needle = (q or "").strip()
    like = f"%{needle}%" if needle else None

    with get_conn() as conn:
        # Store scope for store_manager
        scope = None
        if actor_role != "admin":
            row = conn.execute(
                "SELECT COALESCE(store_id, '') AS s FROM users WHERE CAST(id AS TEXT) = ?",
                (actor_id,),
            ).fetchone()
            scope = (row["s"] or "").strip() if row else ""

        params: list[Any] = []
        where_parts: list[str] = ["1=1"]
        if scope is not None:
            where_parts.append("COALESCE(u.store_id,'') = ?")
            params.append(scope)
        if like is not None:
            where_parts.append(
                "("
                "u.username LIKE ? OR "
                "COALESCE(u.display_name,'') LIKE ? OR "
                "CAST(u.id AS TEXT) LIKE ? OR "
                "UPPER(printf('EMP%05d', u.id)) LIKE UPPER(?)"
                ")"
            )
            params.extend([like, like, like, like])

        where_sql = " AND ".join(where_parts)
        sql = f"""
            SELECT u.id, u.username, u.role, u.display_name,
                   COALESCE(u.store_id,'') AS store_id
            FROM users u
            WHERE {where_sql}
            ORDER BY u.id ASC
        """
        users = conn.execute(sql, tuple(params)).fetchall()

        result = []
        for u in users:
            uid = str(u["id"])
            cycle = get_active_cycle(conn, uid)
            store_id = str(u["store_id"] or "").strip()
            role = str(u["role"] or "").strip()
            labels = _resolve_training_identity_labels(
                conn,
                store_id=store_id,
                role=role,
            )
            result.append({
                "user_id": uid,
                "employee_no": _employee_no(uid),
                "username": u["username"],
                "display_name": u["display_name"] or u["username"],
                "role": role,
                "role_label": labels["role_label"],
                "store_id": store_id,
                "store_name": labels["store_name"],
                "store_label": labels["store_label"],
                "cycle_id": cycle["cycle_id"] if cycle else "",
                "cycle_type": cycle.get("cycle_type", "") if cycle else "",
                "stage_no": cycle.get("stage_no", 0) if cycle else 0,
                "stage_name": cycle.get("stage_name", "") if cycle else "",
                "stage_status": cycle.get("stage_status", "") if cycle else "",
                "current_day": cycle.get("current_day", 0) if cycle else 0,
                "total_days": cycle.get("total_days", 0) if cycle else 0,
                "unlock_mode": cycle.get("unlock_mode", "daily") if cycle else "",
                "full_release_by_admin": bool(cycle.get("full_release_by_admin")) if cycle else False,
            })

    return success_response({"items": result}, workflow_code="training_cycle")


@router.get("/admin/detail")
def admin_cycle_detail(
    user_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    target_user_id = str(user_id or "").strip()
    if not target_user_id:
        raise HTTPException(status_code=400, detail="user_id 不能为空")

    with get_conn() as conn:
        identity = _load_user_identity(conn, target_user_id)
        _ensure_store_manager_same_store(
            conn,
            current_user,
            target_store_id=identity["store_id"],
            detail="仅可查看本门店员工",
        )

        plan_row = _load_growth_plan_row(conn, user_id=target_user_id)
        cycle = get_active_cycle(conn, target_user_id)
        days: list[dict[str, Any]] = []
        overall_progress = 0
        if cycle:
            rows = conn.execute(
                "SELECT * FROM cycle_daily_tasks WHERE cycle_id = ? ORDER BY day_index ASC, sort_order ASC, id ASC",
                (cycle["cycle_id"],),
            ).fetchall()
            tasks = [dict(row) for row in rows]
            unlock_map = json.loads(cycle.get("day_unlock_json") or "{}")
            days = _group_cycle_days(tasks, int(cycle["total_days"]), int(cycle["current_day"]), unlock_map)
            completed_days = sum(1 for day in days if day["status"] == "completed")
            overall_progress = round((completed_days / max(int(cycle["total_days"]), 1)) * 100)

    return success_response(
        {
            "user": identity,
            "growth_plan": _growth_plan_summary(plan_row),
            "cycle": cycle,
            "days": days,
            "overall_progress": overall_progress,
        },
        workflow_code="training_cycle",
    )


@router.get("/progress")
def get_cycle_progress(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "")
    with get_conn() as conn:
        cycle = get_active_cycle(conn, uid)
        if not cycle:
            return success_response(data={"cycle": None, "days": []}, workflow_code="training_cycle")
        rows = conn.execute(
            "SELECT * FROM cycle_daily_tasks WHERE cycle_id = ? ORDER BY day_index ASC, sort_order ASC, id ASC",
            (cycle["cycle_id"],),
        ).fetchall()
        tasks = [dict(row) for row in rows]
        unlock_map = json.loads(cycle.get("day_unlock_json") or "{}")
        days = _group_cycle_days(tasks, int(cycle["total_days"]), int(cycle["current_day"]), unlock_map)
        completed_days = sum(1 for day in days if day["status"] == "completed")
        overall_progress = round((completed_days / max(int(cycle["total_days"]), 1)) * 100)
        module_indexes = refresh_module_snapshots(conn, user_id=uid, persist=True)

    return success_response(
        data={
            "cycle": {
                "cycle_id": cycle["cycle_id"],
                "total_days": cycle["total_days"],
                "current_day": cycle["current_day"],
                "status": cycle["status"],
                "stage_no": cycle.get("stage_no", 1),
                "stage_name": cycle.get("stage_name", ""),
                "stage_status": cycle.get("stage_status", "active"),
                "cycle_type": cycle.get("cycle_type", "onboarding"),
                "full_release_by_admin": bool(cycle.get("full_release_by_admin")),
            },
            "days": days,
            "scores": {
                "product_knowledge": round(next((item["overall_index"] for item in module_indexes if item["module_code"] == "product_basics"), 0)),
                "compliance": round(next((item["overall_index"] for item in module_indexes if item["module_code"] == "compliance_expression"), 0)),
                "sales_communication": round(next((item["overall_index"] for item in module_indexes if item["module_code"] == "needs_discovery"), 0)),
                "response": round(next((item["overall_index"] for item in module_indexes if item["module_code"] == "independent_service"), 0)),
                "overall": round(sum(item["overall_index"] for item in module_indexes if float(item["overall_index"] or 0) > 0) / max(len([item for item in module_indexes if float(item["overall_index"] or 0) > 0]), 1), 2),
            },
            "overall_progress": overall_progress,
        },
        workflow_code="training_cycle",
    )


@router.post("/refresh-day")
def refresh_day(
    body: RefreshDayRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "")
    with get_conn() as conn:
        cycle = get_cycle(conn, body.cycle_id, uid)
        if not cycle:
            raise HTTPException(status_code=404, detail="训练周期不存在")
        unlock_map = json.loads(cycle.get("day_unlock_json") or "{}")
        unlock_map[str(body.day_index)] = True
        now = now_iso()
        conn.execute(
            "UPDATE training_cycles SET day_unlock_json = ?, current_day = CASE WHEN current_day < ? THEN ? ELSE current_day END, updated_at = ? WHERE cycle_id = ?",
            (json.dumps(unlock_map, ensure_ascii=False), body.day_index, body.day_index, now, body.cycle_id),
        )
        conn.execute(
            """
            UPDATE cycle_daily_tasks
            SET status = CASE WHEN status = 'locked' THEN 'released' ELSE status END,
                release_status = CASE WHEN release_status = 'locked' THEN 'released' ELSE release_status END,
                released_at = COALESCE(released_at, ?),
                updated_at = ?
            WHERE cycle_id = ? AND day_index = ?
            """,
            (now, now, body.cycle_id, body.day_index),
        )
        rows = conn.execute(
            "SELECT * FROM cycle_daily_tasks WHERE cycle_id = ? AND day_index = ? ORDER BY sort_order ASC, id ASC",
            (body.cycle_id, body.day_index),
        ).fetchall()
    return success_response(
        data={"day_index": body.day_index, "tasks": [dict(row) for row in rows]},
        workflow_code="training_cycle",
    )


@router.post("/complete-task")
def complete_task(
    body: CompleteTaskRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "")
    with get_conn() as conn:
        task = conn.execute(
            """
            SELECT id, target_count, current_count, status
            FROM cycle_daily_tasks
            WHERE cycle_id = ? AND user_id = ? AND task_code = ? AND day_index = ?
            """,
            (body.cycle_id, uid, body.task_code, body.day_index),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if str(task["status"]) == "locked":
            raise HTTPException(status_code=400, detail="当前任务尚未解锁，请先完成前一天任务")
        if str(task["status"]) == "completed":
            return success_response(
                data={"task_status": "completed", "current_count": int(task["current_count"] or 0), "all_day_complete": False},
                workflow_code="training_cycle",
            )
        new_count = int(task["current_count"] or 0) + 1
        new_status = "completed" if new_count >= int(task["target_count"] or 1) else "in_progress"
        now = now_iso()
        conn.execute(
            """
            UPDATE cycle_daily_tasks
            SET current_count = ?, status = ?, release_status = CASE WHEN ? = 'completed' THEN 'completed' ELSE release_status END,
                updated_at = ?, completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END
            WHERE id = ?
            """,
            (new_count, new_status, new_status, now, new_status, now, task["id"]),
        )
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM cycle_daily_tasks WHERE cycle_id = ? AND day_index = ? AND status != 'completed'",
            (body.cycle_id, body.day_index),
        ).fetchone()
        all_day_complete = int((remaining["c"] if remaining else 0) or 0) == 0
        cycle = get_cycle(conn, body.cycle_id, uid)
        if cycle and all_day_complete:
            _advance_cycle_after_day_complete(conn, cycle=cycle, day_index=body.day_index, now=now)
    return success_response(
        data={"task_status": new_status, "current_count": new_count, "all_day_complete": all_day_complete},
        workflow_code="training_cycle",
    )


@router.post("/admin/manual-task-check")
def admin_manual_task_check(
    body: AdminManualTaskCheckRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    actor_role = normalize_app_role(str(current_user.get("role") or ""))
    with get_conn() as conn:
        cycle = get_cycle(conn, body.cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail="培训周期不存在")
        identity = _load_user_identity(conn, str(cycle["user_id"] or ""))
        _ensure_store_manager_same_store(
            conn,
            current_user,
            target_store_id=identity["store_id"],
            detail="仅可操作本门店员工",
        )
        task = conn.execute(
            """
            SELECT id, target_count, current_count, status
            FROM cycle_daily_tasks
            WHERE cycle_id = ? AND task_code = ? AND day_index = ?
            LIMIT 1
            """,
            (body.cycle_id, body.task_code, body.day_index),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        now = now_iso()
        if str(task["status"]) != "completed":
            conn.execute(
                """
                UPDATE cycle_daily_tasks
                SET current_count = CASE WHEN target_count > current_count THEN target_count ELSE current_count END,
                    status = 'completed',
                    release_status = 'completed',
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, task["id"]),
            )
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM cycle_daily_tasks WHERE cycle_id = ? AND day_index = ? AND status != 'completed'",
            (body.cycle_id, body.day_index),
        ).fetchone()
        all_day_complete = int((remaining["c"] if remaining else 0) or 0) == 0
        cycle = get_cycle(conn, body.cycle_id)
        if cycle and all_day_complete:
            _advance_cycle_after_day_complete(conn, cycle=cycle, day_index=body.day_index, now=now)
            cycle = get_cycle(conn, body.cycle_id)
    return success_response(
        data={
            "cycle_id": body.cycle_id,
            "task_code": body.task_code,
            "day_index": body.day_index,
            "task_status": "completed",
            "all_day_complete": all_day_complete,
            "cycle": cycle,
        },
        workflow_code="training_cycle",
    )


@router.post("/admin/manual-task-uncheck")
def admin_manual_task_uncheck(
    body: AdminManualTaskUncheckRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    actor_role = normalize_app_role(str(current_user.get("role") or ""))
    with get_conn() as conn:
        cycle = get_cycle(conn, body.cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail="培训周期不存在")
        identity = _load_user_identity(conn, str(cycle["user_id"] or ""))
        _ensure_store_manager_same_store(
            conn,
            current_user,
            target_store_id=identity["store_id"],
            detail="仅可操作本门店员工",
        )
        task = conn.execute(
            """
            SELECT id, target_count, current_count, status
            FROM cycle_daily_tasks
            WHERE cycle_id = ? AND task_code = ? AND day_index = ?
            LIMIT 1
            """,
            (body.cycle_id, body.task_code, body.day_index),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        now = now_iso()
        next_count = max(min(int(task["current_count"] or 0), int(task["target_count"] or 1)) - 1, 0)
        next_status = "in_progress" if next_count > 0 else "released"
        if str(task["status"]) == "completed":
            conn.execute(
                """
                UPDATE cycle_daily_tasks
                SET current_count = ?,
                    status = ?,
                    release_status = 'released',
                    completed_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (next_count, next_status, now, task["id"]),
            )
        cycle = _recalculate_cycle_progress(conn, cycle_id=body.cycle_id, now=now)
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM cycle_daily_tasks WHERE cycle_id = ? AND day_index = ? AND status != 'completed'",
            (body.cycle_id, body.day_index),
        ).fetchone()
        all_day_complete = int((remaining["c"] if remaining else 0) or 0) == 0
    return success_response(
        data={
            "cycle_id": body.cycle_id,
            "task_code": body.task_code,
            "day_index": body.day_index,
            "task_status": next_status,
            "all_day_complete": all_day_complete,
            "cycle": cycle,
        },
        workflow_code="training_cycle",
    )


@router.get("/daily-feedback/{day_index}")
def get_daily_feedback(
    day_index: int,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "")
    with get_conn() as conn:
        indexes = refresh_module_snapshots(conn, user_id=uid, persist=False)
    weak_dimensions = [item["module_code"] for item in sorted(indexes, key=lambda row: float(row["overall_index"] or 0))[:3] if float(item["overall_index"] or 0) < 80]
    avg_practice = round(sum(float(item["practice_index"] or 0) for item in indexes if float(item["practice_index"] or 0) > 0) / max(len([item for item in indexes if float(item["practice_index"] or 0) > 0]), 1), 2)
    return success_response(
        data={
            "day_index": day_index,
            "scores": {item["module_code"]: item["overall_index"] for item in indexes},
            "weak_dimensions": weak_dimensions,
            "avg_practice_score": avg_practice,
            "difficulty": "advanced" if avg_practice >= 80 else "standard",
            "practice_count": len(indexes),
        },
        workflow_code="training_cycle",
    )


@router.post("/start-stage")
def start_stage(
    body: StartStageRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    with get_conn() as conn:
        existing = get_active_cycle(conn, body.user_id)
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"已有进行中的培训周期：{existing['cycle_id']}",
            )
        linked_plan = _ensure_growth_plan_link(
            conn,
            user_id=body.user_id,
            requested_plan_id=body.plan_id,
            actor_user_id=str(current_user.get("user_id") or ""),
        )
        cycle = create_stage_cycle(
            conn,
            user_id=body.user_id,
            plan_id=str(linked_plan.get("plan_id") or ""),
            stage_no=max(int(body.stage_no or 1), 1),
            cycle_type=body.cycle_type or "onboarding",
            previous_cycle_id=body.previous_cycle_id or "",
            source_reset_days=body.source_reset_days,
            release_all=body.release_all,
        )
    return success_response(
        {"cycle": cycle, "growth_plan": _growth_plan_summary(linked_plan)},
        workflow_code="training_cycle",
    )


@router.post("/release-stage-days")
def release_stage_days(
    body: ReleaseStageDaysRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    with get_conn() as conn:
        cycle = release_current_stage_days(conn, body.cycle_id)
        rows = conn.execute(
            "SELECT * FROM cycle_daily_tasks WHERE cycle_id = ? ORDER BY day_index ASC, sort_order ASC, id ASC",
            (body.cycle_id,),
        ).fetchall()
    return success_response(
        {"cycle": cycle, "tasks": [dict(row) for row in rows]},
        workflow_code="training_cycle",
    )


@router.post("/review-stage")
def review_stage(
    body: ReviewStageRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    uid: str = ""
    next_cycle_id = ""
    wf15_result: dict[str, Any] = {}

    with get_conn() as conn:
        cycle = get_cycle(conn, body.cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail="训练周期不存在")
        uid = str(cycle["user_id"])
        payload = stage_review_payload(conn, cycle_id=body.cycle_id, user_id=uid)
        if not payload:
            raise HTTPException(status_code=404, detail="阶段评估数据不存在")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO training_stage_reviews (
                cycle_id, user_id, stage_no, stage_name, review_score, is_pass,
                review_summary, ability_delta_json, recommended_actions_json, generated_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.cycle_id,
                uid,
                int(cycle["stage_no"] or 1),
                str(cycle["stage_name"] or ""),
                float(payload["review_score"]),
                int(payload["is_pass"]),
                payload["review_summary"],
                payload["ability_delta_json"],
                payload["recommended_actions_json"],
                str(current_user.get("user_id") or ""),
                now,
            ),
        )
        if payload["is_pass"]:
            conn.execute(
                "UPDATE training_cycles SET status = 'completed', stage_status = 'passed', completed_at = ?, updated_at = ? WHERE cycle_id = ?",
                (now, now, body.cycle_id),
            )
            stage_no = int(cycle["stage_no"] or 1)
            if stage_no < 2:
                next_cycle = create_stage_cycle(
                    conn,
                    user_id=uid,
                    plan_id=str(cycle["plan_id"] or ""),
                    stage_no=stage_no + 1,
                    cycle_type=str(cycle.get("cycle_type") or "onboarding"),
                    previous_cycle_id=body.cycle_id,
                )
                next_cycle_id = str(next_cycle.get("cycle_id") or "")
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET training_cycle_id = CASE WHEN training_cycle_id = ? THEN '' ELSE training_cycle_id END,
                        current_cycle_day = CASE WHEN training_cycle_id = ? THEN 0 ELSE current_cycle_day END,
                        updated_at = ?
                    WHERE CAST(id AS TEXT) = ? OR user_id = ?
                    """,
                    (body.cycle_id, body.cycle_id, now, uid, uid),
                )
        else:
            conn.execute(
                "UPDATE training_cycles SET status = 'active', stage_status = 'failed', updated_at = ? WHERE cycle_id = ?",
                (now, body.cycle_id),
            )
            existing = conn.execute(
                "SELECT COUNT(*) AS c FROM cycle_daily_tasks WHERE cycle_id = ? AND task_source = 'system_retry'",
                (body.cycle_id,),
            ).fetchone()
            if int((existing["c"] if existing else 0) or 0) == 0:
                remedials = [
                    ("retry_1", "practice_chat", "补训任务 1", "针对第一薄弱模块进行对话补训"),
                    ("retry_2", "practice_chat", "补训任务 2", "针对第二薄弱模块进行对话补训"),
                    ("retry_3", "learning_review", "补训总结", "复盘补训要点并准备重新评估"),
                ]
                for index, item in enumerate(remedials, start=1):
                    conn.execute(
                        """
                        INSERT INTO cycle_daily_tasks (
                            cycle_id, user_id, day_index, task_code, task_type, branch, title, description,
                            status, target_count, current_count, score_json, dimension_focus, route_page, completed_at,
                            module_code, module_name, task_source, release_status, released_at, ai_score, ai_feedback,
                            next_action, evaluation_status, sort_order, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            body.cycle_id,
                            uid,
                            int(cycle["total_days"] or 7),
                            item[0],
                            item[1],
                            "practice" if item[1] == "practice_chat" else "learning",
                            item[2],
                            item[3],
                            "released",
                            1,
                            0,
                            "{}",
                            "final_review",
                            "practical_training" if item[1] == "practice_chat" else "growth_plan",
                            None,
                            "final_review",
                            "综合复盘",
                            "system_retry",
                            "released",
                            now,
                            None,
                            "",
                            "完成补训后重新发起阶段评估",
                            "pending",
                            100 + index,
                            now,
                            now,
                        ),
                    )

    # --- 调用 WF15 Dify 工作流生成解锁包 ---
    retry_count_row = None
    with get_conn() as conn:
        retry_count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM cycle_daily_tasks WHERE cycle_id = ? AND task_source = 'system_retry'",
            (body.cycle_id,),
        ).fetchone()
    retry_task_count = int((retry_count_row["c"] if retry_count_row else 0) or 0)

    wf15_call = run_wf15_unlock(
        user_id=uid,
        cycle_id=body.cycle_id,
        cycle_type=str(cycle.get("cycle_type") or "onboarding"),
        stage_no=int(cycle["stage_no"] or 1),
        stage_name=str(cycle["stage_name"] or ""),
        review_score=float(payload["review_score"]),
        is_pass=int(payload["is_pass"]),
        stage_status="passed" if payload["is_pass"] else "failed",
        has_growth_plan=1,
        passed_stage1=1 if int(cycle["stage_no"] or 1) >= 1 and payload["is_pass"] else 0,
        passed_stage2=1 if int(cycle["stage_no"] or 1) >= 2 and payload["is_pass"] else 0,
        next_cycle_id=next_cycle_id,
        retry_task_count=retry_task_count,
        trigger_source="review_stage",
        unlock_matrix_version="v1",
        current_unlocks_json="{}",
    )

    if not wf15_call.get("ok"):
        _log.warning(
            "WF15 Dify call failed, continuing with local result. reason=%s error=%s",
            wf15_call.get("reason"), wf15_call.get("error"),
        )
    else:
        wf15_result = wf15_call.get("data") or {}

    response_data = {
        "review_score": payload["review_score"],
        "is_pass": payload["is_pass"],
        "review_summary": payload["review_summary"],
        "next_stage_unlocked": bool(payload["next_stage_unlocked"]),
        "next_cycle_id": next_cycle_id,
    }

    if wf15_result:
        response_data["wf15"] = {
            "unlock_scope": wf15_result.get("unlock_scope", ""),
            "module_unlocks": wf15_result.get("module_unlocks", {}),
            "next_route": wf15_result.get("next_route", ""),
            "next_action": wf15_result.get("next_action", ""),
            "user_message": wf15_result.get("user_message", ""),
            "manager_message": wf15_result.get("manager_message", ""),
            "panel_summary": wf15_result.get("panel_summary", ""),
            "recommended_actions": wf15_result.get("recommended_actions", []),
            "unlock_diff": wf15_result.get("unlock_diff", {}),
            "should_notify_user": wf15_result.get("should_notify_user", 0),
            "should_notify_manager": wf15_result.get("should_notify_manager", 0),
        }

    unlock_event = _build_stage_unlock_event(
        cycle=cycle,
        payload=payload,
        next_cycle_id=next_cycle_id,
        wf15_result=wf15_result,
    )
    if unlock_event:
        response_data["unlock_event"] = unlock_event

    return success_response(
        response_data,
        workflow_code="training_cycle",
        mock=not bool(wf15_result),
    )


@router.post("/void-cycle")
def void_training_cycle(
    body: VoidCycleRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    _manager_only(current_user)
    with get_conn() as conn:
        void_cycle(conn, body.cycle_id)
    return success_response({"cycle_id": body.cycle_id, "status": "voided"}, workflow_code="training_cycle")
