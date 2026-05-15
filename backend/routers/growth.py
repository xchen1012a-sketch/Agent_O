from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

import config as app_config
from api_response import dify_failure_response, make_request_id, success_response
from auth import get_current_user, normalize_app_role
from database import SessionLocal
from db_stage3 import get_conn, json_text, upsert_employee_profile
from dify_stage4a import run_growth1_workflow, run_growth2_workflow
from routers.performance import build_employee_performance_bundle
from training_cycle_service import create_stage_cycle, get_active_cycle, void_cycle

router = APIRouter(prefix="/api/growth", tags=["growth"])
_log = logging.getLogger("jewelry_qipei.router.growth")


class GrowthPlanRequest(BaseModel):
    employee_id: str = Field("", description="员工 ID")
    employee_name: str = Field("", description="员工姓名")
    position: str = Field("", description="岗位")
    store_id: str = Field("", description="门店 ID")
    mentor_name: str = Field("", description="带教人")
    ability_summary: str = Field("", description="当前能力描述")
    target_direction: str = Field("", description="培养方向")
    employee_stage: str = Field("", description="员工阶段")
    plan_cycle: str = Field("", description="计划重推周期，例如 60天 / 90天")
    current_performance: str = Field("", description="当前业绩概况")
    target_performance: str = Field("", description="目标业绩概况")
    gap_metrics: str = Field("", description="优先差距指标")
    notes: str = Field("", description="补充说明")


class GrowthEvaluateRequest(BaseModel):
    employee_id: str = Field("", description="员工 ID")
    employee_name: str = Field("", description="员工姓名")
    plan_id: str = Field("", description="成长计划 ID")
    learning_summary: str = Field("", description="学习结果摘要")
    practice_summary: str = Field("", description="陪练结果摘要")
    manager_feedback: str = Field("", description="主管反馈")
    score: float | None = Field(None, description="外部评分")
    # Stage4A 工作流2 映射可选字段（不破坏原接口）
    module_code: str = Field("", description="学习模块编码")
    module_name: str = Field("", description="学习模块名称")
    question_text: str = Field("", description="题目内容")
    user_answer: str = Field("", description="员工作答")
    standard_answer: str = Field("", description="标准答案")
    knowledge_tag: str = Field("", description="知识标签")
    current_profile: str = Field("", description="当前能力画像")
    current_scores: str = Field("", description="当前能力分")


class GrowthTaskManualCheckRequest(BaseModel):
    plan_id: str = Field("", description="成长计划 ID")
    employee_id: str = Field("", description="员工 ID")
    task_code: str = Field("", description="任务编码")
    checked: bool = Field(True, description="是否标记完成")
    note: str = Field("", description="备注")


class RetrainingResetRequest(BaseModel):
    user_id: str = Field("", description="员工 ID")
    reset_days: int = Field(30, description="计划重推周期天数")


_MANAGER_ROLES = {"admin", "store_manager"}
_ROLE_LABELS = {
    "admin": "管理员",
    "store_manager": "店长",
    "senior_consultant": "资深顾问",
    "trainee": "导购",
}


class _GrowthPlanExecutionError(Exception):
    def __init__(self, call: dict[str, Any]):
        super().__init__("growth_plan_execution_failed")
        self.call = call if isinstance(call, dict) else {}


def _extract_markdown_modules(markdown: str) -> list[str]:
    text = (markdown or "").strip()
    if not text:
        return []
    lines = [x.strip() for x in text.splitlines()]
    out: list[str] = []
    for line in lines:
        m = re.match(r"^##\s*模块\d+\s*[：:]\s*(.+?)\s*$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _strip_markdown_comment(markdown: str) -> str:
    text = (markdown or "").strip()
    if not text.startswith("<!--"):
        return text
    end = text.find("-->")
    if end < 0:
        return text
    return text[end + 3 :].strip()


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


def _score_level(score: float | None) -> str:
    if score is None:
        return "待评估"
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 50:
        return "一般"
    return "待提升"


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return default


def _extract_plan_cycle_days(value: Any, default: int = 90) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        days = _to_int(digits, default)
        return max(30, min(180, days))
    return default


def _normalize_priority(priority: Any, default: str = "medium") -> str:
    raw = str(priority or "").strip().lower()
    if raw in {"high", "高", "高优先级"}:
        return "high"
    if raw in {"medium", "mid", "中", "中优先级"}:
        return "medium"
    if raw in {"low", "低", "低优先级"}:
        return "low"
    return default


def _build_course_recommendation(
    course_code: str,
    course_name: str,
    priority: Any,
    *,
    order_index: int = 0,
) -> dict[str, str]:
    name = str(course_name or "").strip() or f"培训主题 {order_index + 1}"
    priority_key = _normalize_priority(priority)
    focus = "围绕当前成长计划的核心短板做专项补齐。"
    if any(token in name for token in ("产品", "知识", "材质", "工艺")):
        focus = "重点补齐产品卖点、材质工艺和价值表达，提升推荐时的专业度。"
    elif any(token in name for token in ("异议", "嫌贵", "价格")):
        focus = "重点强化价格异议、犹豫观望等场景下的回应与推进能力。"
    elif any(token in name for token in ("成交", "收口", "转化")):
        focus = "重点训练试戴引导、成交确认和收口动作，减少客户流失。"

    if priority_key == "high":
        reason = "该主题与当前最紧迫的能力短板直接相关，建议本周优先投入训练。"
        action = "建议 3 天内完成学习，并配套 2 次针对性陪练与 1 次门店复盘。"
        outcome = "完成后应能更完整地输出关键话术，并在接待现场稳定使用。"
    elif priority_key == "low":
        reason = "该主题适合作为后续巩固项，在核心短板补齐后再持续跟进。"
        action = "建议穿插安排碎片学习，并结合日常接待做轻量复盘。"
        outcome = "帮助把已有动作沉淀成长期稳定习惯，减少能力回落。"
    else:
        reason = "该主题用于承接高优先级训练后的巩固，避免短期训练后再次松动。"
        action = "建议在完成高优先级主题后 1 周内补上，并至少安排 1 次复练。"
        outcome = "帮助把新学动作固化到日常接待流程，提升输出稳定性。"

    return {
        "course_code": str(course_code or "").strip(),
        "course_name": name,
        "priority": priority_key,
        "training_focus": focus,
        "recommendation_reason": reason,
        "recommended_action": action,
        "expected_outcome": outcome,
    }


def _parse_iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _load_employee_performance_linkage(employee_id: str) -> dict[str, Any]:
    target_id = (employee_id or "").strip()
    if not target_id:
        return {}
    try:
        with SessionLocal() as db:
            bundle = build_employee_performance_bundle(db, target_id)
            linkage = bundle.get("performance_linkage")
            return linkage if isinstance(linkage, dict) else {}
    except Exception:
        return {}


def _growth_plan_replan_state(
    row,
    *,
    current_linkage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_linkage = current_linkage if isinstance(current_linkage, dict) else {}
    payload = _parse_json_dict(row["payload_json"]) if row else {}
    plan_meta = _parse_json_dict(row["plan_meta_json"]) if row else {}
    plan_cycle_days = _extract_plan_cycle_days(
        plan_meta.get("plan_cycle_days")
        or payload.get("plan_cycle_days")
        or current_linkage.get("plan_cycle_days"),
        int(current_linkage.get("plan_cycle_days") or 90),
    )
    created_at = _parse_iso_dt(row["created_at"] if row else "")
    now = datetime.now(timezone.utc)
    next_replan_at = (created_at + timedelta(days=plan_cycle_days)) if created_at else now
    days_until_replan = max(0, (next_replan_at.date() - now.date()).days)
    replan_due = bool(created_at is None or now >= next_replan_at)
    return {
        "plan_cycle_days": plan_cycle_days,
        "next_replan_at": next_replan_at.isoformat(),
        "days_until_replan": days_until_replan,
        "replan_due": replan_due,
    }


def _is_manager_role(role: str | None) -> bool:
    return normalize_app_role(role) in _MANAGER_ROLES


def _role_label(role: str | None) -> str:
    normalized = normalize_app_role(role)
    return _ROLE_LABELS.get(normalized) or str(role or "").strip()


def _resolve_employee_identity(conn, employee_id: str) -> dict[str, str]:
    target = (employee_id or "").strip()
    identity = {
        "employee_id": target,
        "employee_name": "",
        "position": "",
        "store_id": "",
        "store_name": "",
        "mentor_name": "",
    }
    if not target:
        return identity

    try:
        row = conn.execute(
            """
            SELECT employee_id, user_id, employee_name, position, store_id, COALESCE(mentor_name, '') AS mentor_name
            FROM employee_profiles
            WHERE employee_id = ? OR user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target, target),
        ).fetchone()
    except Exception:
        row = None
    if row:
        identity["employee_id"] = str(row["employee_id"] or row["user_id"] or target).strip() or target
        identity["employee_name"] = str(row["employee_name"] or "").strip()
        identity["position"] = str(row["position"] or "").strip()
        identity["store_id"] = str(row["store_id"] or "").strip()
        identity["mentor_name"] = str(row["mentor_name"] or "").strip()

    try:
        user_row = conn.execute(
            """
            SELECT
                CAST(id AS TEXT) AS row_id,
                COALESCE(user_id, '') AS user_id,
                COALESCE(display_name, '') AS display_name,
                COALESCE(name, '') AS name,
                COALESCE(role, '') AS role,
                COALESCE(store_id, '') AS store_id
            FROM users
            WHERE user_id = ? OR CAST(id AS TEXT) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target, target),
        ).fetchone()
    except Exception:
        user_row = None
    if user_row:
        identity["employee_id"] = (
            identity["employee_id"]
            or str(user_row["user_id"] or user_row["row_id"] or target).strip()
            or target
        )
        identity["employee_name"] = (
            identity["employee_name"]
            or str(user_row["display_name"] or user_row["name"] or "").strip()
        )
        identity["position"] = identity["position"] or _role_label(user_row["role"])
        identity["store_id"] = identity["store_id"] or str(user_row["store_id"] or "").strip()

    store_id = identity.get("store_id") or ""
    if store_id:
        try:
            store_row = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(store_name), ''), NULLIF(TRIM(name), ''), store_id) AS store_name
                FROM stores
                WHERE LOWER(TRIM(store_id)) = LOWER(TRIM(?))
                LIMIT 1
                """,
                (store_id,),
            ).fetchone()
        except Exception:
            store_row = None
        if store_row:
            identity["store_name"] = str(store_row["store_name"] or "").strip()
    if not identity["store_name"]:
        identity["store_name"] = store_id

    return identity


def _resolve_actor_store_id(conn, current_user: dict[str, Any]) -> str:
    actor_user_id = str(current_user.get("user_id") or "").strip()
    if not actor_user_id:
        return ""
    identity = _resolve_employee_identity(conn, actor_user_id)
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


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, list) else []
        except Exception:
            return []
    return []


def _pick_plan_modules(plan_payload: dict[str, Any]) -> list[str]:
    modules: list[str] = []
    for item in plan_payload.get("stage_goals") or []:
        text = str(item or "").strip()
        if text:
            modules.append(text)
    if not modules:
        for item in plan_payload.get("recommended_courses") or []:
            if isinstance(item, dict):
                text = str(item.get("course_name") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                modules.append(text)
    if not modules:
        for item in plan_payload.get("practice_tasks") or []:
            if isinstance(item, dict):
                text = str(item.get("title") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                modules.append(text)
    return modules[:3]


def _build_daily_task_blueprint(plan_payload: dict[str, Any]) -> list[dict[str, Any]]:
    modules = _pick_plan_modules(plan_payload)
    linkage = plan_payload.get("performance_linkage") if isinstance(plan_payload.get("performance_linkage"), dict) else {}
    priority_gaps = linkage.get("priority_gaps") if isinstance(linkage.get("priority_gaps"), list) else []
    primary_gap = priority_gaps[0] if priority_gaps else {}
    secondary_gap = priority_gaps[1] if len(priority_gaps) > 1 else {}
    primary_metric = str(primary_gap.get("metric_label") or "核心指标").strip() or "核心指标"
    primary_topic = str(primary_gap.get("training_topic") or "").strip() or "高价值推荐与成交推进"
    primary_scene = str(primary_gap.get("recommended_scene") or "").strip() or "顾客犹豫不决不下单"
    secondary_topic = str(secondary_gap.get("training_topic") or "").strip() or "异议处理与推荐表达"
    secondary_scene = str(secondary_gap.get("recommended_scene") or "").strip() or "顾客嫌贵"
    focus_1 = modules[0] if len(modules) > 0 else "核心产品知识与话术"
    focus_2 = modules[1] if len(modules) > 1 else "异议处理与推荐表达"
    focus_3 = modules[2] if len(modules) > 2 else "正式考核冲刺"
    return [
        {
            "task_code": "day1_learning_review",
            "day_index": 1,
            "branch": "learning",
            "title": f"完成业绩差距学习复盘：{focus_1}",
            "description": f"围绕“{primary_metric}”差距先补知识卡片与目标拆解，系统会按学习评估记录自动勾选。",
            "metric_type": "learning_eval_count",
            "target_count": 1,
            "route_page": "growth_plan",
            "route_tab": "evaluate",
            "route_label": "去学习评估",
            "priority": "high",
            "target_metric_label": primary_metric,
        },
        {
            "task_code": "day2_practice_round1",
            "day_index": 2,
            "branch": "practice",
            "title": f"完成第 1 次补短板陪练：{primary_topic}",
            "description": f"优先练“{primary_scene}”场景，把{primary_metric}差距转成真实话术演练。",
            "metric_type": "practice_eval_count",
            "target_count": 1,
            "route_page": "practical_training",
            "route_label": "去练习分支",
            "priority": "high",
            "target_metric_label": primary_metric,
            "recommended_scene": primary_scene,
            "module_code": "objection_handling",
        },
        {
            "task_code": "day3_practice_round2",
            "day_index": 3,
            "branch": "practice",
            "title": f"完成第 2 次补短板陪练：{secondary_topic}",
            "description": f"继续围绕“{secondary_scene}”场景做第二轮训练，巩固第二优先差距对应动作。",
            "metric_type": "practice_eval_count",
            "target_count": 2,
            "route_page": "practical_training",
            "route_label": "继续练习",
            "priority": "medium",
            "module_code": "objection_handling",
        },
        {
            "task_code": "day4_learning_apply",
            "day_index": 4,
            "branch": "learning",
            "title": f"完成第 4 天应用复盘：{focus_2}",
            "description": "把前 3 天学习和陪练中的高频问题整理成一份复盘，提交后自动记录为当天完成。",
            "metric_type": "learning_eval_count",
            "target_count": 2,
            "route_page": "growth_plan",
            "route_tab": "evaluate",
            "route_label": "提交学习评估",
            "priority": "medium",
        },
        {
            "task_code": "day4_assessment_finish",
            "day_index": 5,
            "branch": "assessment",
            "title": f"完成正式考核：{focus_3}",
            "description": f"考核结果将进入“{primary_metric}”业绩观察口径，建议完成前 4 天任务后再进入。",
            "metric_type": "assessment_total_count",
            "target_count": 1,
            "route_page": "assessment",
            "route_label": "去考核分支",
            "priority": "high",
        },
        {
            "task_code": "day5_assessment_pass",
            "day_index": 6,
            "branch": "assessment",
            "title": f"考核达标并跟进{primary_metric}变化",
            "description": "系统按正式考核通过记录自动勾选，后续建议对照看板复盘指标变化。",
            "metric_type": "assessment_pass_count",
            "target_count": 1,
            "route_page": "assessment",
            "route_label": "查看考核结果",
            "priority": "high",
        },
        {
            "task_code": "day7_growth_summary",
            "day_index": 7,
            "branch": "learning",
            "title": "完成第 7 天总结复盘",
            "description": "围绕本轮成长计划补交一份 7 天总结，沉淀有效动作，完成后自动结束本轮每日任务。",
            "metric_type": "learning_eval_count",
            "target_count": 3,
            "route_page": "growth_plan",
            "route_tab": "evaluate",
            "route_label": "完成学习评估",
            "priority": "high",
        },
    ]


def _normalize_daily_task_blueprint(
    task_blueprint: list[dict[str, Any]],
    plan_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_days = set(range(1, 8))
    days = {
        max(1, int(item.get("day_index") or 0))
        for item in task_blueprint
        if isinstance(item, dict)
    }
    if len(task_blueprint) == 7 and days == expected_days:
        return task_blueprint
    return _build_daily_task_blueprint(plan_payload)


def _load_latest_growth_plan_row(conn, employee_id: str):
    return conn.execute(
        """
        SELECT *
        FROM growth_plan_records
        WHERE employee_id = ? OR user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (((employee_id or "").strip()), ((employee_id or "").strip())),
    ).fetchone()


def _load_visible_growth_plan_row(conn, employee_id: str):
    row = _load_latest_growth_plan_row(conn, employee_id)
    if not row:
        return None
    employee_key = (employee_id or "").strip()
    target_plan_id = str(row["plan_id"] or "").strip()
    if not employee_key:
        return row
    # Check by plan_id first (exact match)
    if target_plan_id:
        cycle_stats = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN status IN ('active', 'waiting_review') THEN 1 ELSE 0 END) AS active_count
            FROM training_cycles
            WHERE user_id = ? AND plan_id = ?
            """,
            (employee_key, target_plan_id),
        ).fetchone()
        total_count = int((cycle_stats["total_count"] if cycle_stats else 0) or 0)
        active_count = int((cycle_stats["active_count"] if cycle_stats else 0) or 0)
        if total_count > 0 and active_count == 0:
            return None
        if active_count > 0:
            return row
    # Also check if the user has ANY active training cycle (handles plan_id mismatch
    # after growth plan re-generation: new plan_id has no cycle, old cycle was voided)
    any_active = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM training_cycles
        WHERE user_id = ? AND status IN ('active', 'waiting_review')
        """,
        (employee_key,),
    ).fetchone()
    has_active = int((any_active["c"] if any_active else 0) or 0) > 0
    if not has_active:
        # No active cycle at all — check if user ever had cycles (all ended/voided)
        any_total = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM training_cycles
            WHERE user_id = ?
            """,
            (employee_key,),
        ).fetchone()
        total = int((any_total["c"] if any_total else 0) or 0)
        if total > 0:
            return None
    return row


def _load_growth_plan_job_row(conn, task_id: str):
    return conn.execute(
        """
        SELECT *
        FROM growth_plan_jobs
        WHERE task_id = ?
        LIMIT 1
        """,
        ((task_id or "").strip(),),
    ).fetchone()


def _create_growth_plan_job(
    conn,
    *,
    task_id: str,
    employee_id: str,
    requested_by: str,
    requested_role: str,
    request_payload: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO growth_plan_jobs (
            task_id, employee_id, requested_by, requested_role, status,
            message, error_message, plan_id, request_payload_json, result_payload_json,
            created_at, updated_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            (employee_id or "").strip(),
            (requested_by or "").strip(),
            (requested_role or "").strip(),
            "pending",
            "任务已提交，等待生成成长计划",
            "",
            "",
            json_text(request_payload),
            "{}",
            now,
            now,
            None,
        ),
    )


def _update_growth_plan_job(
    conn,
    *,
    task_id: str,
    status_text: str,
    message: str = "",
    error_message: str = "",
    plan_id: str = "",
    result_payload: dict[str, Any] | None = None,
    completed: bool = False,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    completed_at = now if completed else None
    conn.execute(
        """
        UPDATE growth_plan_jobs
        SET status = ?,
            message = ?,
            error_message = ?,
            plan_id = CASE WHEN ? != '' THEN ? ELSE plan_id END,
            result_payload_json = CASE WHEN ? != '' THEN ? ELSE result_payload_json END,
            updated_at = ?,
            completed_at = CASE WHEN ? IS NOT NULL THEN ? ELSE completed_at END
        WHERE task_id = ?
        """,
        (
            (status_text or "").strip() or "pending",
            (message or "").strip(),
            (error_message or "").strip(),
            (plan_id or "").strip(),
            (plan_id or "").strip(),
            json_text(result_payload or {}) if isinstance(result_payload, dict) else "",
            json_text(result_payload or {}) if isinstance(result_payload, dict) else "",
            now,
            completed_at,
            completed_at,
            (task_id or "").strip(),
        ),
    )


def _task_manual_status_map(conn, plan_id: str) -> dict[str, dict[str, Any]]:
    if not plan_id:
        return {}
    rows = conn.execute(
        """
        SELECT task_code, status, note, checked_by, checked_role, created_at
        FROM growth_task_manual_records
        WHERE plan_id = ?
        ORDER BY id DESC
        """,
        (plan_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_code = str(row["task_code"] or "").strip()
        if task_code and task_code not in out:
            out[task_code] = {
                "status": str(row["status"] or "").strip(),
                "note": str(row["note"] or "").strip(),
                "checked_by": str(row["checked_by"] or "").strip(),
                "checked_role": str(row["checked_role"] or "").strip(),
                "checked_at": str(row["created_at"] or "").strip(),
            }
    return out


def _task_metric_counts(conn, employee_id: str, plan_created_at: str) -> dict[str, int]:
    employee_id = (employee_id or "").strip()
    since = (plan_created_at or "").strip()
    learning_row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM learning_eval_records
        WHERE employee_id = ? AND created_at >= ?
        """,
        (employee_id, since),
    ).fetchone()
    practice_row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM practice_eval_records
        WHERE employee_id = ? AND created_at >= ?
        """,
        (employee_id, since),
    ).fetchone()
    assessment_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN COALESCE(is_pass, 0) = 1 THEN 1 ELSE 0 END) AS pass_count
        FROM assessment_records
        WHERE user_id = ? AND finished_at >= ?
        """,
        (employee_id, since),
    ).fetchone()
    return {
        "learning_eval_count": int((learning_row["c"] if learning_row else 0) or 0),
        "practice_eval_count": int((practice_row["c"] if practice_row else 0) or 0),
        "assessment_total_count": int((assessment_row["total_count"] if assessment_row else 0) or 0),
        "assessment_pass_count": int((assessment_row["pass_count"] if assessment_row else 0) or 0),
    }


def _resolve_daily_tasks(
    conn,
    *,
    plan_id: str,
    employee_id: str,
    plan_created_at: str,
    task_blueprint: list[dict[str, Any]],
    is_manager: bool,
) -> list[dict[str, Any]]:
    metrics = _task_metric_counts(conn, employee_id, plan_created_at)
    manual_map = _task_manual_status_map(conn, plan_id)
    prepared: list[dict[str, Any]] = []
    for item in sorted(
        task_blueprint,
        key=lambda x: (
            int(x.get("day_index") or 0),
            str(x.get("task_code") or ""),
        ),
    ):
        task = dict(item)
        task_code = str(task.get("task_code") or "").strip()
        metric_type = str(task.get("metric_type") or "").strip()
        target_count = max(1, int(task.get("target_count") or 1))
        current_count = int(metrics.get(metric_type, 0))
        auto_done = current_count >= target_count
        manual_state = manual_map.get(task_code) or {}
        manual_done = str(manual_state.get("status") or "").strip() == "complete"
        done = auto_done or manual_done
        task["current_count"] = current_count
        task["target_count"] = target_count
        task["_is_done"] = done
        task["done_by"] = "system" if auto_done else ("manager" if manual_done else "")
        task["manual_checked"] = manual_done
        task["manual_note"] = manual_state.get("note") or ""
        task["manual_checked_at"] = manual_state.get("checked_at") or ""
        task["progress_text"] = f"{min(current_count, target_count)}/{target_count}"
        prepared.append(task)

    day_done_map: dict[int, bool] = {}
    for task in prepared:
        day_index = max(1, int(task.get("day_index") or 1))
        day_done_map.setdefault(day_index, True)
        day_done_map[day_index] = bool(day_done_map[day_index] and task.get("_is_done"))

    unlocked_day = 1
    while day_done_map.get(unlocked_day):
        unlocked_day += 1

    tasks: list[dict[str, Any]] = []
    for task in prepared:
        day_index = max(1, int(task.get("day_index") or 1))
        done = bool(task.pop("_is_done", False))
        if done:
            task["status"] = "done"
            task["done_label"] = "系统自动完成" if task.get("done_by") == "system" else "管理层补记完成"
            task["unlock_hint"] = ""
        elif day_index <= unlocked_day:
            task["status"] = "unlocked"
            task["done_label"] = "进行中"
            task["unlock_hint"] = ""
        else:
            task["status"] = "locked"
            task["done_label"] = "未解锁"
            task["unlock_hint"] = f"完成第 {day_index - 1} 天任务后自动解锁"
            task["progress_text"] = "--"
        task["manager_can_check"] = bool(is_manager and task["status"] == "unlocked" and task.get("done_by") != "system")
        tasks.append(task)
    return tasks


def _build_growth_overview_payload(conn, *, employee_id: str, viewer_role: str) -> dict[str, Any]:
    row = _load_visible_growth_plan_row(conn, employee_id)
    is_manager = _is_manager_role(viewer_role)
    identity = _resolve_employee_identity(conn, employee_id)
    current_linkage = _load_employee_performance_linkage(employee_id)
    if not row:
        return {
            "employee_id": employee_id,
            "employee": identity,
            "has_plan": False,
            "is_manager": is_manager,
            "can_generate": True,
            "can_edit": is_manager,
            "generation_rule": (
                f"员工可按 {int(current_linkage.get('plan_cycle_days') or 90)} 天计划重推周期重新生成成长计划；"
                "每次重新生成后，系统会开启新的两阶段 7 天训练，管理层可随时调整。"
            ),
            "plan": None,
            "plan_meta": {
                "plan_cycle_days": int(current_linkage.get("plan_cycle_days") or 90),
                "performance_linkage": current_linkage,
            },
            "daily_tasks": [],
            "progress": {"completed_count": 0, "total_count": 0},
        }

    payload = _parse_json_dict(row["payload_json"])
    plan_meta = _parse_json_dict(row["plan_meta_json"])
    replan_state = _growth_plan_replan_state(row, current_linkage=current_linkage)
    task_blueprint = _normalize_daily_task_blueprint(
        _parse_json_list(plan_meta.get("daily_tasks")),
        payload,
    )
    daily_tasks = _resolve_daily_tasks(
        conn,
        plan_id=str(row["plan_id"] or ""),
        employee_id=employee_id,
        plan_created_at=str(row["created_at"] or ""),
        task_blueprint=task_blueprint,
        is_manager=is_manager,
    )
    completed_count = len([x for x in daily_tasks if x.get("status") == "done"])
    total_count = len(daily_tasks)
    plan_payload = dict(payload)
    plan_payload["plan_id"] = str(row["plan_id"] or "")
    plan_payload["employee"] = {
        "employee_id": str(identity.get("employee_id") or row["employee_id"] or employee_id),
        "employee_name": str(identity.get("employee_name") or row["employee_name"] or ""),
        "position": str(identity.get("position") or row["position"] or ""),
        "store_id": str(identity.get("store_id") or row["store_id"] or ""),
        "store_name": str(identity.get("store_name") or row["store_id"] or ""),
        "mentor_name": str(row["mentor_name"] or ""),
    }
    plan_payload["profile_summary"] = str(plan_payload.get("profile_summary") or row["ability_summary"] or "")
    plan_payload["target_direction"] = str(plan_payload.get("target_direction") or row["target_direction"] or "")
    plan_payload["ability_summary"] = str(row["ability_summary"] or "")
    plan_payload["created_at"] = str(row["created_at"] or "")
    if current_linkage and not isinstance(plan_payload.get("performance_linkage"), dict):
        plan_payload["performance_linkage"] = current_linkage
    if current_linkage and not isinstance(plan_meta.get("performance_linkage"), dict):
        plan_meta["performance_linkage"] = current_linkage
    plan_meta["plan_cycle_days"] = int(plan_meta.get("plan_cycle_days") or replan_state["plan_cycle_days"])
    plan_meta["next_replan_at"] = str(plan_meta.get("next_replan_at") or replan_state["next_replan_at"])
    plan_meta["replan_due"] = bool(replan_state["replan_due"])
    return {
        "employee_id": employee_id,
        "employee": {
            "employee_id": str(identity.get("employee_id") or employee_id),
            "employee_name": str(identity.get("employee_name") or row["employee_name"] or ""),
            "position": str(identity.get("position") or row["position"] or ""),
            "store_id": str(identity.get("store_id") or row["store_id"] or ""),
            "store_name": str(identity.get("store_name") or row["store_id"] or ""),
            "mentor_name": str(row["mentor_name"] or ""),
        },
        "has_plan": True,
        "is_manager": is_manager,
        "can_generate": bool(is_manager or replan_state["replan_due"]),
        "can_edit": is_manager,
        "generation_rule": (
            f"已到 {replan_state['plan_cycle_days']} 天计划重推节点，可重新生成成长计划；重新生成后将开启新的两阶段 7 天训练。"
            if replan_state["replan_due"]
            else (
                f"距下次员工可自助重生成还有 {replan_state['days_until_replan']} 天；"
                f"当前为 {replan_state['plan_cycle_days']} 天计划重推周期，到期后可重新生成成长计划并开启新的两阶段 7 天训练，"
                "管理层可随时发起回访重学。"
            )
        ),
        "plan": plan_payload,
        "plan_meta": plan_meta,
        "daily_tasks": daily_tasks,
        "progress": {
            "completed_count": completed_count,
            "total_count": total_count,
            "pending_count": max(0, total_count - completed_count),
        },
        "replan_due": bool(replan_state["replan_due"]),
        "days_until_replan": int(replan_state["days_until_replan"]),
        "next_replan_at": replan_state["next_replan_at"],
    }


def _build_growth_plan_mock(
    *,
    plan_id: str,
    employee_id: str,
    employee_name: str,
    position: str,
    store_id: str,
    store_name: str,
    mentor_name: str,
    ability_summary: str,
    target_direction: str,
    performance_linkage: dict[str, Any] | None = None,
    employee_stage: str = "",
    plan_cycle_days: int = 90,
) -> dict[str, Any]:
    performance_linkage = performance_linkage if isinstance(performance_linkage, dict) else {}
    primary_gap = performance_linkage.get("primary_gap") if isinstance(performance_linkage.get("primary_gap"), dict) else {}
    primary_metric = str(primary_gap.get("metric_label") or "销售转化").strip() or "销售转化"
    primary_topic = str(primary_gap.get("training_topic") or "高价值推荐与成交推进").strip() or "高价值推荐与成交推进"
    primary_scene = str(primary_gap.get("recommended_scene") or "顾客犹豫不决不下单").strip() or "顾客犹豫不决不下单"
    return {
        "plan_id": plan_id,
        "employee": {
            "employee_id": employee_id,
            "employee_name": employee_name,
            "position": position,
            "store_id": store_id,
            "store_name": store_name or store_id,
            "mentor_name": mentor_name,
        },
        "profile_summary": ability_summary
        or "当前基础话术较稳定，但产品价值表达和异议处理仍需加强。",
        "target_direction": target_direction,
        "employee_stage": employee_stage or str(performance_linkage.get("employee_stage") or "在岗提升"),
        "plan_cycle_days": int(plan_cycle_days or performance_linkage.get("plan_cycle_days") or 90),
        "stage_goals": [
            f"7 天内补齐“{primary_metric}”对应的关键话术与知识卡。",
            f"2 周内围绕“{primary_topic}”完成 3 次补短板陪练。",
            "1 个月内把训练结果带回门店接待，并复盘指标变化。",
        ],
        "recommended_courses": [
            _build_course_recommendation("JP-101", primary_topic, "high", order_index=0),
            _build_course_recommendation("JP-203", "高客单异议处理", "high", order_index=1),
            _build_course_recommendation("JP-305", "门店成交收口技巧", "medium", order_index=2),
        ],
        "practice_tasks": [
            {
                "title": f"完成 2 轮“{primary_scene}”场景陪练。",
                "module_code": "objection_handling",
                "scene_code": "objection_handling",
                "difficulty": "standard",
            },
            {
                "title": "完成 1 轮价格异议场景陪练。",
                "module_code": "objection_handling",
                "scene_code": "objection_handling",
                "difficulty": "advanced",
            },
            {
                "title": f"每周提交 1 次围绕“{primary_metric}”变化的门店复盘记录。",
            },
        ],
        "review_points": [
            "是否能主动识别客户购买动机。",
            "是否能把材质、工艺和情绪价值连成完整话术。",
            "是否能在结尾明确推进试戴或成交动作。",
        ],
        "growth_plan_markdown": "",
        "plan_meta": {},
        "normalized_profile": "",
        "performance_linkage": performance_linkage,
        "workflow_mode": "mock",
    }


def _summarize_growth_plan_failure(call: dict[str, Any]) -> dict[str, str]:
    payload = call if isinstance(call, dict) else {}
    reason = str(payload.get("reason") or "").strip() or "dify_exception"
    error = str(payload.get("error") or "").strip()
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    raw_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    detail = str(raw_data.get("detail") or "").strip() or error or reason
    low = f"{error} {detail}".lower()
    if "unauthorized" in low or "invalid api key" in low:
        message = "Dify 鉴权失败，请检查成长计划应用 API Key"
    elif "server disconnected without sending a response" in low:
        message = "成长计划生成等待过久，连接被上游提前断开"
    elif "timeout" in low:
        message = "成长计划生成超时，请稍后重试"
    elif str(raw_data.get("http_status") or "") == "504":
        message = "成长计划生成超时，请稍后重试"
    else:
        message = "成长计划生成失败"
    return {
        "message": message,
        "reason": reason,
        "error": error,
        "detail": detail[:1000],
    }


def _resolve_growth_plan_context(
    body: GrowthPlanRequest,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    employee_id = (body.employee_id or "").strip() or str(current_user.get("user_id") or "")
    actor_user_id = str(current_user.get("user_id") or "")
    actor_role = str(current_user.get("role") or "")
    is_manager = _is_manager_role(actor_role)
    if employee_id != actor_user_id and not is_manager:
        raise HTTPException(status_code=403, detail="员工只能为本人生成成长计划，仅管理层可代员工生成或修改")

    with get_conn() as conn:
        identity = _resolve_employee_identity(conn, employee_id)
        employee_id = identity.get("employee_id") or employee_id
        latest_row = _load_visible_growth_plan_row(conn, employee_id)

    performance_linkage = _load_employee_performance_linkage(employee_id)
    if latest_row and not is_manager:
        replan_state = _growth_plan_replan_state(latest_row, current_linkage=performance_linkage)
        if not replan_state["replan_due"]:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"当前成长计划仍在 {replan_state['plan_cycle_days']} 天计划重推周期内，"
                    f"距下次可自助重生成还有 {replan_state['days_until_replan']} 天"
                ),
            )

    if not is_manager and not (
        identity.get("employee_id") or identity.get("employee_name") or identity.get("position")
    ):
        raise HTTPException(status_code=400, detail="未找到人员档案，请先在人员管理中创建员工")

    _jwt_display_name = str(current_user.get("display_name") or current_user.get("name") or "").strip()
    _jwt_store_id = str(current_user.get("store_id") or "").strip()
    employee_name = (
        identity.get("employee_name")
        or (body.employee_name or "").strip()
        or _jwt_display_name
        or "待确认员工"
    )
    position = (
        identity.get("position")
        or (body.position or "").strip()
        or _role_label(actor_role)
        or "导购"
    )
    store_id = (
        identity.get("store_id")
        or (body.store_id or "").strip()
        or _jwt_store_id
        or "STORE01"
    )
    employee_stage = (body.employee_stage or "").strip() or str(performance_linkage.get("employee_stage") or "")
    plan_cycle_days = _extract_plan_cycle_days(body.plan_cycle, int(performance_linkage.get("plan_cycle_days") or 90))
    current_performance = (body.current_performance or "").strip() or str(performance_linkage.get("current_performance_text") or "")
    target_performance = (body.target_performance or "").strip() or str(performance_linkage.get("target_performance_text") or "")
    gap_metrics = (body.gap_metrics or "").strip() or str(performance_linkage.get("gap_metrics_text") or "")
    performance_context = str(performance_linkage.get("performance_context_card") or "").strip()
    target_direction = (
        (body.target_direction or "").strip()
        or (str(performance_linkage.get("growth_direction") or "") if performance_linkage.get("has_sales_data") else "")
        or "提升产品讲解与成交转化"
    )
    mentor_name = str(identity.get("mentor_name") or "").strip() or (body.mentor_name or "").strip()

    return {
        "employee_id": employee_id,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "is_manager": is_manager,
        "identity": identity,
        "performance_linkage": performance_linkage,
        "employee_name": employee_name,
        "position": position,
        "store_id": store_id,
        "employee_stage": employee_stage,
        "plan_cycle_days": plan_cycle_days,
        "current_performance": current_performance,
        "target_performance": target_performance,
        "gap_metrics": gap_metrics,
        "performance_context": performance_context,
        "target_direction": target_direction,
        "mentor_name": mentor_name,
        "ability_summary": (body.ability_summary or "").strip(),
        "notes": (body.notes or "").strip(),
    }


def _execute_growth_plan_context(context: dict[str, Any]) -> dict[str, Any]:
    employee_id = str(context.get("employee_id") or "").strip()
    actor_user_id = str(context.get("actor_user_id") or "").strip()
    actor_role = str(context.get("actor_role") or "").strip()
    identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
    performance_linkage = context.get("performance_linkage") if isinstance(context.get("performance_linkage"), dict) else {}
    employee_name = str(context.get("employee_name") or "").strip() or "待确认员工"
    position = str(context.get("position") or "").strip() or "导购"
    store_id = str(context.get("store_id") or "").strip() or "STORE01"
    employee_stage = str(context.get("employee_stage") or "").strip()
    plan_cycle_days = _extract_plan_cycle_days(context.get("plan_cycle_days"), 90)
    current_performance = str(context.get("current_performance") or "").strip()
    target_performance = str(context.get("target_performance") or "").strip()
    gap_metrics = str(context.get("gap_metrics") or "").strip()
    performance_context = str(context.get("performance_context") or "").strip()
    target_direction = str(context.get("target_direction") or "").strip() or "提升产品讲解与成交转化"
    mentor_name = str(context.get("mentor_name") or "").strip()
    ability_summary = str(context.get("ability_summary") or "").strip()
    notes = str(context.get("notes") or "").strip()
    plan_id = make_request_id("gp")

    self_intro = (
        "\n".join(
            [
                f"员工姓名：{employee_name}",
                f"岗位：{position}",
                f"门店：{store_id}",
                f"员工阶段：{employee_stage or '待识别'}",
                f"培养方向：{target_direction}",
                f"补充说明：{notes or '无'}",
                f"业绩上下文：{performance_context or '暂无真实业绩数据'}",
            ]
        ).strip()
        or "珠宝门店员工成长计划输入"
    )
    historical_learning = "\n".join(
        [
            notes or "无",
            f"当前业绩：{current_performance or '暂无'}",
            f"目标业绩：{target_performance or '待设定'}",
            f"优先差距：{gap_metrics or '暂无显著差距'}",
        ]
    ).strip()
    initial_ability = "\n".join(
        [
            ability_summary or "无",
            f"业绩驱动方向：{target_direction}",
        ]
    ).strip()

    call = run_growth1_workflow(
        user_id=employee_id,
        job_title=position,
        self_intro=self_intro,
        historical_learning=historical_learning,
        initial_ability=initial_ability,
        employee_stage=employee_stage,
        plan_cycle=f"{plan_cycle_days}天",
        current_performance=current_performance,
        target_performance=target_performance,
        gap_metrics=gap_metrics,
        performance_context=performance_context,
    )

    use_dify = bool(call.get("ok"))
    if use_dify:
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        growth_markdown = (wf_data.get("growth_plan_markdown") or "").strip()
        if not growth_markdown:
            use_dify = False
            call = {
                "ok": False,
                "reason": "empty_workflow_output",
                "error": "growth_plan_markdown_empty",
                "raw": call.get("raw") if isinstance(call, dict) else {},
            }
        else:
            modules = _extract_markdown_modules(growth_markdown)
            data = {
                "plan_id": plan_id,
                "employee": {
                    "employee_id": employee_id,
                    "employee_name": employee_name,
                    "position": position,
                    "store_id": store_id,
                    "store_name": identity.get("store_name") or store_id,
                    "mentor_name": mentor_name,
                },
                "profile_summary": ability_summary
                or (wf_data.get("normalized_profile") or "").strip()
                or "已生成成长计划，请查看下方详细内容。",
                "target_direction": target_direction,
                "employee_stage": employee_stage or str(performance_linkage.get("employee_stage") or "在岗提升"),
                "plan_cycle_days": plan_cycle_days,
                "stage_goals": modules,
                "recommended_courses": [
                    _build_course_recommendation(
                        f"M{i+1:02d}",
                        m,
                        "high" if i == 0 else "medium",
                        order_index=i,
                    )
                    for i, m in enumerate(modules)
                ],
                "practice_tasks": [],
                "review_points": [],
                "growth_plan_markdown": growth_markdown,
                "plan_meta": wf_data.get("plan_meta") or {},
                "normalized_profile": (wf_data.get("normalized_profile") or "").strip(),
                "markdown_meta": wf_data.get("markdown_meta") or {},
                "performance_linkage": performance_linkage,
                "workflow_mode": "dify",
                "workflow_reason": "",
            }
    if not use_dify and not (
        app_config.DIFY_STAGE4A_FORCE_MOCK or app_config.DIFY_ALLOW_FALLBACK_TO_MOCK
    ):
        raise _GrowthPlanExecutionError(call if isinstance(call, dict) else {})
    if not use_dify:
        _log.info("growth_plan using mock employee_id=%s", employee_id)
        data = _build_growth_plan_mock(
            plan_id=plan_id,
            employee_id=employee_id,
            employee_name=employee_name,
            position=position,
            store_id=store_id,
            store_name=identity.get("store_name") or store_id,
            mentor_name=mentor_name,
            ability_summary=ability_summary,
            target_direction=target_direction,
            performance_linkage=performance_linkage,
            employee_stage=employee_stage,
            plan_cycle_days=plan_cycle_days,
        )
        data["workflow_reason"] = (call.get("reason") or "fallback_to_mock") if isinstance(call, dict) else "fallback_to_mock"
        data["dify_error"] = (call.get("error") or "") if isinstance(call, dict) else ""

    plan_meta_payload = _parse_json_dict(data.get("plan_meta"))
    plan_meta_payload["daily_tasks"] = _build_daily_task_blueprint(data)
    plan_meta_payload["plan_cycle_days"] = plan_cycle_days
    plan_meta_payload["next_replan_at"] = (datetime.now(timezone.utc) + timedelta(days=plan_cycle_days)).isoformat()
    plan_meta_payload["employee_stage"] = employee_stage or str(performance_linkage.get("employee_stage") or "")
    plan_meta_payload["performance_linkage"] = performance_linkage
    data["plan_meta"] = plan_meta_payload
    data["performance_linkage"] = performance_linkage
    data["employee_stage"] = employee_stage or str(performance_linkage.get("employee_stage") or "")
    data["plan_cycle_days"] = plan_cycle_days

    with get_conn() as conn:
        upsert_employee_profile(
            conn,
            employee_id=employee_id,
            employee_name=employee_name,
            position=position,
            store_id=store_id,
            role=actor_role,
            source="growth_plan",
        )
        row = conn.execute(
            """
            INSERT INTO growth_plan_records (
                user_id, growth_plan_text, plan_meta_json, source_workflow,
                plan_id, employee_id, employee_name, position, store_id, mentor_name,
                ability_summary, target_direction, payload_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_id,
                (data.get("growth_plan_markdown") or "").strip(),
                json_text(plan_meta_payload),
                "growth1" if use_dify else "growth1_mock",
                plan_id,
                employee_id,
                employee_name,
                position,
                store_id,
                mentor_name,
                ability_summary,
                target_direction,
                json_text(data),
                actor_user_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        data["db_record_id"] = int(row.lastrowid or 0)
        active_cycle = get_active_cycle(conn, employee_id)
        effective_cycle = active_cycle
        if not active_cycle:
            effective_cycle = create_stage_cycle(
                conn,
                user_id=employee_id,
                plan_id=plan_id,
                stage_no=1,
                cycle_type="onboarding",
                previous_cycle_id="",
            )
            data["auto_cycle_id"] = effective_cycle.get("cycle_id") or ""
        if effective_cycle:
            cycle_id = str(effective_cycle.get("cycle_id") or "").strip()
            current_day = int(effective_cycle.get("current_day") or 1)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE users
                SET onboarding_completed = 1,
                    onboarding_completed_at = CASE
                        WHEN COALESCE(onboarding_completed_at, '') = '' THEN ?
                        ELSE onboarding_completed_at
                    END,
                    training_cycle_id = ?,
                    current_cycle_day = ?,
                    updated_at = ?
                WHERE CAST(id AS TEXT) = ? OR user_id = ?
                """,
                (now, cycle_id, current_day, now, employee_id, employee_id),
            )

    _log.info("growth_plan success employee_id=%s use_dify=%s plan_id=%s", employee_id, use_dify, plan_id)
    return {
        "data": data,
        "workflow_code": "growth1" if use_dify else "growth1_mock",
        "mock": not use_dify,
    }


def _run_growth_plan_job(task_id: str) -> None:
    task_id = (task_id or "").strip()
    if not task_id:
        return
    try:
        with get_conn() as conn:
            row = _load_growth_plan_job_row(conn, task_id)
            if not row:
                return
            context = _parse_json_dict(row["request_payload_json"])
            _update_growth_plan_job(
                conn,
                task_id=task_id,
                status_text="running",
                message="正在生成成长计划",
            )

        result = _execute_growth_plan_context(context)

        with get_conn() as conn:
            _update_growth_plan_job(
                conn,
                task_id=task_id,
                status_text="succeeded",
                message="成长计划已生成",
                plan_id=str(result["data"].get("plan_id") or "").strip(),
                result_payload=result,
                completed=True,
            )
    except _GrowthPlanExecutionError as exc:
        failure = _summarize_growth_plan_failure(exc.call)
        with get_conn() as conn:
            _update_growth_plan_job(
                conn,
                task_id=task_id,
                status_text="failed",
                message=failure["message"],
                error_message=failure["detail"],
                result_payload={
                    "reason": failure["reason"],
                    "error": failure["error"],
                    "detail": failure["detail"],
                },
                completed=True,
            )
    except Exception as exc:
        _log.exception("growth plan background task failed task_id=%s", task_id)
        with get_conn() as conn:
            _update_growth_plan_job(
                conn,
                task_id=task_id,
                status_text="failed",
                message="成长计划生成失败",
                error_message=str(exc)[:1000],
                completed=True,
            )


def _build_growth_eval_mock(
    *,
    evaluation_id: str,
    plan_id: str,
    employee_id: str,
    employee_name: str,
    learning_summary: str,
    practice_summary: str,
    manager_feedback: str,
    score: float | None,
) -> dict[str, Any]:
    base_score = float(score) if score is not None else 82.0
    return {
        "evaluation_id": evaluation_id,
        "plan_id": plan_id,
        "employee": {
            "employee_id": employee_id,
            "employee_name": employee_name,
        },
        "overall_score": round(base_score, 1),
        "overall_level": "良好",
        "strengths": [
            "产品基础知识回答较稳定。",
            "接待语气自然，客户沟通压迫感较低。",
            "能够基本完成推荐流程。",
        ],
        "gaps": [
            "高客单产品价值感表达仍偏弱。",
            "异议处理时缺少追问和确认动作。",
            "成交收口动作还不够主动。",
        ],
        "improvement_actions": [
            "优先补强钻石保值、工艺差异和佩戴场景话术。",
            "针对价格异议再做 2 轮高压场景陪练。",
            "下周门店带教时重点抽查成交收口动作。",
        ],
        "review_summary": manager_feedback
        or "当前已具备基础接待能力，下一阶段建议把训练重点转到异议处理和成交推进。",
        "evidence": {
            "learning_summary": learning_summary,
            "practice_summary": practice_summary,
        },
        "evaluation_markdown": "",
        "output_meta": {},
        "workflow_mode": "mock",
    }


@router.get("/overview")
def get_growth_overview(
    employee_id: str = "",
    current_user: Annotated[dict[str, Any], Depends(get_current_user)] = None,
):
    viewer_role = normalize_app_role(str(current_user.get("role") or ""))
    viewer_id = str(current_user.get("user_id") or "")
    target_employee_id = (employee_id or "").strip() or viewer_id
    if target_employee_id != viewer_id and not _is_manager_role(viewer_role):
        raise HTTPException(status_code=403, detail="权限不足，仅管理层可查看其他员工成长计划")
    with get_conn() as conn:
        if viewer_role == "store_manager" and target_employee_id != viewer_id:
            identity = _resolve_employee_identity(conn, target_employee_id)
            _ensure_store_manager_same_store(
                conn,
                current_user,
                target_store_id=identity.get("store_id") or "",
                detail="仅可查看本门店员工成长计划",
            )
        data = _build_growth_overview_payload(
            conn,
            employee_id=target_employee_id,
            viewer_role=viewer_role,
        )
    return success_response(
        data,
        workflow_code="growth_overview",
        mock=False,
        extra_meta={"viewer_role": normalize_app_role(viewer_role)},
    )


@router.post("/tasks/manual-check")
def growth_task_manual_check(
    body: GrowthTaskManualCheckRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    actor_role = normalize_app_role(str(current_user.get("role") or ""))
    if not _is_manager_role(actor_role):
        raise HTTPException(status_code=403, detail="权限不足，仅管理层可手动勾选成长任务")
    plan_id = (body.plan_id or "").strip()
    employee_id = (body.employee_id or "").strip()
    task_code = (body.task_code or "").strip()
    if not plan_id or not employee_id or not task_code:
        raise HTTPException(status_code=400, detail="plan_id、employee_id、task_code 不能为空")

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM growth_plan_records
            WHERE plan_id = ? AND employee_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (plan_id, employee_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应成长计划")
        identity = _resolve_employee_identity(conn, employee_id)
        _ensure_store_manager_same_store(
            conn,
            current_user,
            target_store_id=identity.get("store_id") or "",
            detail="仅可操作本门店员工成长任务",
        )

        plan_meta = _parse_json_dict(row["plan_meta_json"])
        task_blueprint = _normalize_daily_task_blueprint(
            _parse_json_list(plan_meta.get("daily_tasks")),
            _parse_json_dict(row["payload_json"]),
        )
        valid_codes = {str(x.get("task_code") or "").strip() for x in task_blueprint}
        if task_code not in valid_codes:
            raise HTTPException(status_code=400, detail="任务编码不存在或不属于当前成长计划")

        conn.execute(
            """
            INSERT INTO growth_task_manual_records (
                plan_id, employee_id, task_code, status, note, checked_by, checked_role, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                employee_id,
                task_code,
                "complete" if body.checked else "pending",
                (body.note or "").strip(),
                str(current_user.get("user_id") or ""),
                normalize_app_role(actor_role),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        data = _build_growth_overview_payload(
            conn,
            employee_id=employee_id,
            viewer_role=actor_role,
        )

    return success_response(
        data,
        workflow_code="growth_manual_check",
        mock=False,
    )


@router.post("/retraining/reset")
def retraining_reset(
    body: RetrainingResetRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    actor_role = normalize_app_role(str(current_user.get("role") or ""))
    if not _is_manager_role(actor_role):
        raise HTTPException(status_code=403, detail="仅管理员或店长可发起回访重学")

    target_user_id = (body.user_id or "").strip()
    reset_days = int(body.reset_days or 30)
    if reset_days not in {30, 60, 90}:
        raise HTTPException(status_code=400, detail="计划重推周期仅支持 30 / 60 / 90 天")

    with get_conn() as conn:
        identity = _resolve_employee_identity(conn, target_user_id)
        if not identity.get("employee_id"):
            raise HTTPException(status_code=404, detail="目标员工不存在")
        _ensure_store_manager_same_store(
            conn,
            current_user,
            target_store_id=identity.get("store_id") or "",
            detail="只能对本门店员工发起回访重学",
        )
        active_cycle = get_active_cycle(conn, target_user_id)
        if active_cycle:
            void_cycle(conn, str(active_cycle["cycle_id"]))
        plan_id = make_request_id("gp_rt")
        now = datetime.now(timezone.utc).isoformat()
        plan_payload = {
            "source": "retraining",
            "reset_days": reset_days,
            "employee_stage": "senior_retraining",
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
                target_user_id,
                (
                    "# 回访重学计划\n\n"
                    f"- 计划重推周期：{reset_days} 天\n"
                    "- 触发动作：到达回访节点后重新推送成长计划\n"
                    "- 训练安排：重新开启新的两阶段 7 天训练\n"
                    "- 说明：旧周期未完成任务已作废，历史成绩保留。"
                ),
                json_text(plan_payload),
                "growth_retraining",
                now,
                plan_id,
                target_user_id,
                identity.get("employee_name") or "",
                identity.get("position") or "",
                identity.get("store_id") or "",
                identity.get("mentor_name") or "",
                "",
                "回访重学",
                json_text(plan_payload),
                str(current_user.get("user_id") or ""),
            ),
        )
        cycle = create_stage_cycle(
            conn,
            user_id=target_user_id,
            plan_id=plan_id,
            stage_no=1,
            cycle_type="retraining",
            previous_cycle_id=str(active_cycle["cycle_id"]) if active_cycle else "",
            source_reset_days=reset_days,
        )

    return success_response(
        {
            "user_id": target_user_id,
            "reset_days": reset_days,
            "new_plan_id": plan_id,
            "new_cycle_id": cycle.get("cycle_id") or "",
        },
        workflow_code="growth_retraining",
        mock=False,
    )


@router.post("/plan")
def create_growth_plan(
    body: GrowthPlanRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    context = _resolve_growth_plan_context(body, current_user)
    _log.info("growth_plan start employee_id=%s user_id=%s", context["employee_id"], current_user.get("user_id"))
    try:
        result = _execute_growth_plan_context(context)
    except _GrowthPlanExecutionError as exc:
        _log.warning(
            "growth_plan dify failed employee_id=%s reason=%s",
            context["employee_id"],
            exc.call.get("reason", "") if isinstance(exc.call, dict) else "",
        )
        return dify_failure_response(
            workflow_code="growth1",
            route_path="/api/growth/plan",
            call=exc.call if isinstance(exc.call, dict) else None,
        )
    return success_response(
        result["data"],
        workflow_code=str(result["workflow_code"] or "growth1"),
        mock=bool(result["mock"]),
    )


@router.post("/plan/tasks")
def submit_growth_plan_task(
    body: GrowthPlanRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    context = _resolve_growth_plan_context(body, current_user)
    task_id = make_request_id("gptask")
    with get_conn() as conn:
        _create_growth_plan_job(
            conn,
            task_id=task_id,
            employee_id=str(context.get("employee_id") or "").strip(),
            requested_by=str(current_user.get("user_id") or "").strip(),
            requested_role=str(current_user.get("role") or "").strip(),
            request_payload=context,
        )
    background_tasks.add_task(_run_growth_plan_job, task_id)
    return success_response(
        {
            "task_id": task_id,
            "status": "pending",
            "employee_id": str(context.get("employee_id") or "").strip(),
            "message": "任务已提交，正在后台生成成长计划",
        },
        workflow_code="growth1_async",
        message="accepted",
        mock=False,
    )


@router.get("/plan/tasks/{task_id}")
def get_growth_plan_task_status(
    task_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    viewer_id = str(current_user.get("user_id") or "").strip()
    viewer_role = normalize_app_role(str(current_user.get("role") or "").strip())
    with get_conn() as conn:
        row = _load_growth_plan_job_row(conn, task_id)
        if not row:
            raise HTTPException(status_code=404, detail="成长计划任务不存在")
        employee_id = str(row["employee_id"] or "").strip()
        if employee_id != viewer_id and not _is_manager_role(viewer_role):
            raise HTTPException(status_code=403, detail="权限不足，仅管理层可查看其他员工任务")
        if viewer_role == "store_manager" and employee_id != viewer_id:
            identity = _resolve_employee_identity(conn, employee_id)
            _ensure_store_manager_same_store(
                conn,
                current_user,
                target_store_id=identity.get("store_id") or "",
                detail="仅可查看本门店员工任务",
            )
        result_payload = _parse_json_dict(row["result_payload_json"])
    return success_response(
        {
            "task_id": str(row["task_id"] or "").strip(),
            "status": str(row["status"] or "").strip() or "pending",
            "employee_id": employee_id,
            "message": str(row["message"] or "").strip(),
            "error_message": str(row["error_message"] or "").strip(),
            "plan_id": str(row["plan_id"] or "").strip(),
            "result": result_payload.get("data") if isinstance(result_payload.get("data"), dict) else {},
            "workflow_code": str(result_payload.get("workflow_code") or "").strip(),
            "mock": bool(result_payload.get("mock")),
            "completed_at": str(row["completed_at"] or "").strip(),
        },
        workflow_code="growth1_async",
        mock=False,
    )


@router.post("/evaluate")
def evaluate_growth_result(
    body: GrowthEvaluateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    employee_id = (body.employee_id or "").strip() or str(current_user.get("user_id") or "")
    actor_user_id = str(current_user.get("user_id") or "")
    actor_role = str(current_user.get("role") or "")
    is_manager = _is_manager_role(actor_role)
    if employee_id != actor_user_id and not is_manager:
        raise HTTPException(status_code=403, detail="员工只能提交本人的成长评估，仅管理层可代员工提交")
    with get_conn() as conn:
        identity = _resolve_employee_identity(conn, employee_id)
    employee_id = identity.get("employee_id") or actor_user_id or employee_id
    _log.info("growth_evaluate start employee_id=%s user_id=%s", employee_id, current_user.get("user_id"))
    employee_name = identity.get("employee_name") or (body.employee_name or "").strip() or "待评估员工"
    plan_id = (body.plan_id or "").strip() or ""
    evaluation_id = make_request_id("ge")

    score_hint = ""
    if body.score is not None:
        s = round(float(body.score), 1)
        score_hint = f"产品知识{s}；合规表达{s}；销售沟通{s}；客户应答{s}"

    module_code = (body.module_code or "").strip() or (f"plan:{plan_id}" if plan_id else "growth_eval")
    module_name = (body.module_name or "").strip() or "成长计划复盘"
    question_text = (body.question_text or "").strip() or (body.learning_summary or "").strip() or "请总结本阶段学习结果"
    user_answer = (body.user_answer or "").strip() or (body.practice_summary or "").strip()
    standard_answer = (body.standard_answer or "").strip() or (body.manager_feedback or "").strip() or "表达清晰、要点完整、可执行"
    knowledge_tag = (body.knowledge_tag or "").strip() or "综合成长"
    current_profile = (body.current_profile or "").strip() or (body.learning_summary or "").strip()
    current_scores = (body.current_scores or "").strip() or score_hint

    call = run_growth2_workflow(
        user_id=employee_id,
        module_code=module_code,
        module_name=module_name,
        question_text=question_text,
        user_answer=user_answer,
        standard_answer=standard_answer,
        knowledge_tag=knowledge_tag,
        current_profile=current_profile,
        current_scores=current_scores,
    )

    use_dify = bool(call.get("ok"))
    if use_dify:
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        eval_markdown = (wf_data.get("evaluation_markdown") or "").strip()
        if not eval_markdown:
            use_dify = False
            call = {
                "ok": False,
                "reason": "empty_workflow_output",
                "error": "evaluation_markdown_empty",
                "raw": call.get("raw") if isinstance(call, dict) else {},
            }
        else:
            markdown_meta = wf_data.get("markdown_meta") if isinstance(wf_data.get("markdown_meta"), dict) else {}
            output_meta = wf_data.get("output_meta") if isinstance(wf_data.get("output_meta"), dict) else {}
            score_raw = (
                markdown_meta.get("answer_score")
                or output_meta.get("answer_score")
                or (body.score if body.score is not None else None)
            )
            overall_score = _to_float(score_raw)
            weak_dimension = (
                markdown_meta.get("weak_dimension")
                or output_meta.get("weak_dimension")
                or "待评估"
            )
            knowledge_source = (
                markdown_meta.get("knowledge_source")
                or output_meta.get("knowledge_source")
                or "unknown"
            )
            data = {
                "evaluation_id": evaluation_id,
                "plan_id": plan_id,
                "employee": {
                    "employee_id": employee_id,
                    "employee_name": employee_name,
                },
                "overall_score": round(overall_score, 1) if overall_score is not None else None,
                "overall_level": _score_level(overall_score),
                "strengths": [],
                "gaps": [f"重点薄弱维度：{weak_dimension}"] if weak_dimension else [],
                "improvement_actions": [],
                "review_summary": "已生成学习结果评估，请查看下方详细内容。",
                "evidence": {
                    "learning_summary": (body.learning_summary or "").strip(),
                    "practice_summary": (body.practice_summary or "").strip(),
                },
                "evaluation_markdown": eval_markdown,
                "output_meta": output_meta,
                "markdown_meta": markdown_meta,
                "knowledge_source": knowledge_source,
                "workflow_mode": "dify",
                "workflow_reason": "",
            }
    if not use_dify and not (
        app_config.DIFY_STAGE4A_FORCE_MOCK or app_config.DIFY_ALLOW_FALLBACK_TO_MOCK
    ):
        _log.warning("growth_evaluate dify failed employee_id=%s reason=%s", employee_id, call.get("reason", "") if isinstance(call, dict) else "")
        return dify_failure_response(
            workflow_code="growth2",
            route_path="/api/growth/evaluate",
            call=call if isinstance(call, dict) else None,
        )
    if not use_dify:
        _log.info("growth_evaluate using mock employee_id=%s", employee_id)
        data = _build_growth_eval_mock(
            evaluation_id=evaluation_id,
            plan_id=plan_id,
            employee_id=employee_id,
            employee_name=employee_name,
            learning_summary=(body.learning_summary or "").strip(),
            practice_summary=(body.practice_summary or "").strip(),
            manager_feedback=(body.manager_feedback or "").strip(),
            score=float(body.score) if body.score is not None else None,
        )
        data["workflow_reason"] = (call.get("reason") or "fallback_to_mock") if isinstance(call, dict) else "fallback_to_mock"
        data["dify_error"] = (call.get("error") or "") if isinstance(call, dict) else ""

    with get_conn() as conn:
        upsert_employee_profile(
            conn,
            employee_id=employee_id,
            employee_name=employee_name,
            position="",
            store_id="",
            role=str(current_user.get("role") or ""),
            source="growth_evaluate",
        )
        row = conn.execute(
            """
            INSERT INTO learning_eval_records (
                evaluation_id, plan_id, employee_id, employee_name, learning_summary,
                practice_summary, manager_feedback, score, payload_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                plan_id,
                employee_id,
                employee_name,
                (body.learning_summary or "").strip(),
                (body.practice_summary or "").strip(),
                (body.manager_feedback or "").strip(),
                float(body.score) if body.score is not None else None,
                json_text(data),
                str(current_user.get("user_id") or ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        data["db_record_id"] = int(row.lastrowid or 0)
    _log.info("growth_evaluate success employee_id=%s use_dify=%s evaluation_id=%s", employee_id, use_dify, evaluation_id)
    return success_response(
        data,
        workflow_code="growth2" if use_dify else "growth2_mock",
        mock=not use_dify,
    )
