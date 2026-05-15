"""人员管理：门店选项、用户列表、创建与更新（管理员 / 店长）。"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import get_current_user, get_password_hash, normalize_app_role, require_roles
from audit import log_audit_from_user
from db_stage3 import get_conn

router = APIRouter(prefix="/api", tags=["personnel"])

_log = logging.getLogger("jewelry_qipei.router.personnel")

_manage_roles = require_roles(["admin", "store_manager"])
_admin_only = require_roles(["admin"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
_STORE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_ROLE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_MANAGER_ROLE_KEYS = {"store_manager", "leader"}
_ROLE_POSITION_LABELS = {
    "admin": "管理员",
    "store_manager": "店长",
    "leader": "店长",
    "senior_consultant": "资深顾问",
    "trainee": "导购",
}
_JOURNEY_DIMENSIONS = [
    {"key": "product_knowledge", "label": "产品知识"},
    {"key": "compliance_expression", "label": "合规表达"},
    {"key": "needs_discovery", "label": "需求挖掘"},
    {"key": "sales_expression", "label": "销售沟通"},
    {"key": "objection_handling", "label": "异议处理"},
    {"key": "closing_skill", "label": "成交收口"},
]
_JOURNEY_SNAPSHOT_ALIASES = {
    "product_knowledge": ("product_knowledge", "product_knowledge_score", "product"),
    "compliance_expression": ("compliance_expression", "compliance_score", "compliance"),
    "needs_discovery": ("needs_discovery", "needs_discovery_score", "customer_needs"),
    "sales_expression": ("sales_expression", "sales_communication_score", "sales_communication"),
    "objection_handling": ("objection_handling", "response_score", "objection_response"),
    "closing_skill": ("closing_skill", "closing_score", "closing"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _position_for_role(role: str) -> str:
    normalized = normalize_app_role(role)
    return _ROLE_POSITION_LABELS.get(normalized, normalized or "导购")


def _employee_no(row_id: int) -> str:
    return f"EMP{int(row_id):05d}"


def _store_scope_for_actor(conn: sqlite3.Connection, actor_id: str, actor_role: str) -> str | None:
    """admin -> None（全量）；店长 -> 本人门店 id。"""
    r = normalize_app_role(actor_role)
    if r == "admin":
        return None
    row = conn.execute(
        """
        SELECT COALESCE(store_id, '') AS s
        FROM users
        WHERE CAST(id AS TEXT) = ? OR user_id = ? OR username = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (actor_id, actor_id, actor_id),
    ).fetchone()
    return (row["s"] or "").strip() if row else ""


def _assert_target_in_scope(
    conn: sqlite3.Connection,
    scope: str | None,
    target_id: str,
) -> None:
    if scope is None:
        return
    row = conn.execute(
        "SELECT COALESCE(store_id, '') AS s FROM users WHERE CAST(id AS TEXT) = ?",
        (target_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if (row["s"] or "").strip() != scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作其他门店人员")


def _store_exists(conn: sqlite3.Connection, store_id: str) -> bool:
    sid = (store_id or "").strip()
    if not sid:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM stores
        WHERE LOWER(TRIM(COALESCE(store_id, ''))) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (sid,),
    ).fetchone()
    return row is not None


def _find_store_manager_conflict(
    conn: sqlite3.Connection,
    *,
    store_id: str,
    exclude_user_id: str | None = None,
) -> sqlite3.Row | None:
    sid = (store_id or "").strip()
    if not sid:
        return None
    params: list[Any] = [sid]
    sql = """
        SELECT
            id,
            username,
            COALESCE(NULLIF(TRIM(display_name), ''), NULLIF(TRIM(name), ''), username) AS manager_name
        FROM users
        WHERE LOWER(TRIM(COALESCE(store_id, ''))) = LOWER(TRIM(?))
          AND LOWER(TRIM(COALESCE(role, ''))) IN ('store_manager', 'leader')
    """
    if exclude_user_id is not None and str(exclude_user_id).strip():
        sql += " AND CAST(id AS TEXT) <> ?"
        params.append(str(exclude_user_id).strip())
    sql += " ORDER BY id ASC LIMIT 1"
    return conn.execute(sql, tuple(params)).fetchone()


def _sync_store_manager_cache(conn: sqlite3.Connection, store_id: str) -> None:
    sid = (store_id or "").strip()
    if not sid:
        return
    row = _find_store_manager_conflict(conn, store_id=sid)
    manager_name = (row["manager_name"] or "").strip() if row else ""
    conn.execute(
        """
        UPDATE stores
        SET manager_name = ?, updated_at = ?
        WHERE LOWER(TRIM(COALESCE(store_id, ''))) = LOWER(TRIM(?))
        """,
        (manager_name, _now_iso(), sid),
    )


def _sync_store_manager_cache_many(conn: sqlite3.Connection, store_ids: list[str]) -> None:
    seen: set[str] = set()
    for raw in store_ids:
        sid = (raw or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        _sync_store_manager_cache(conn, sid)


def _upsert_personnel_employee_profile(
    conn: sqlite3.Connection,
    *,
    user_key: str,
    display_name: str,
    role: str,
    store_id: str,
    mentor_name: str | None,
) -> None:
    now = _now_iso()
    position = _position_for_role(role)
    mentor_value = None if mentor_name is None else (mentor_name or "").strip()
    conn.execute(
        """
        INSERT INTO employee_profiles (
            employee_id,
            user_id,
            employee_name,
            position,
            job_title,
            store_id,
            role,
            source,
            mentor_name,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, ''), ?, ?)
        ON CONFLICT(employee_id) DO UPDATE SET
            user_id = excluded.user_id,
            employee_name = CASE
                WHEN excluded.employee_name != '' THEN excluded.employee_name
                ELSE employee_profiles.employee_name
            END,
            position = CASE
                WHEN excluded.position != '' THEN excluded.position
                ELSE employee_profiles.position
            END,
            job_title = CASE
                WHEN excluded.job_title != '' THEN excluded.job_title
                ELSE employee_profiles.job_title
            END,
            store_id = CASE
                WHEN excluded.store_id != '' THEN excluded.store_id
                ELSE employee_profiles.store_id
            END,
            role = CASE
                WHEN excluded.role != '' THEN excluded.role
                ELSE employee_profiles.role
            END,
            source = excluded.source,
            mentor_name = CASE
                WHEN ? IS NOT NULL THEN excluded.mentor_name
                ELSE employee_profiles.mentor_name
            END,
            updated_at = excluded.updated_at
        """,
        (
            user_key,
            user_key,
            display_name,
            position,
            position,
            store_id,
            role,
            "personnel",
            mentor_value,
            now,
            now,
            mentor_value,
        ),
    )


def _default_assignable_roles(actor_role: str) -> set[str]:
    if normalize_app_role(actor_role) == "admin":
        return {"admin", "store_manager", "senior_consultant", "trainee"}
    return {"store_manager", "senior_consultant", "trainee"}


def _assignable_role_keys(conn: sqlite3.Connection, actor_role: str) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT role_key, assignable_by_manager
            FROM role_settings
            ORDER BY sort_order ASC, role_key ASC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        return _default_assignable_roles(actor_role)
    ar = normalize_app_role(actor_role)
    keys: set[str] = set()
    for r in rows:
        k = normalize_app_role((r["role_key"] or "").strip())
        if not k:
            continue
        if ar == "admin":
            keys.add(k)
        elif int(r["assignable_by_manager"] or 0) == 1:
            keys.add(k)
    return keys if keys else _default_assignable_roles(actor_role)


def _journey_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _journey_float(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, dict):
            value = value.get("score", value.get("value"))
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if score != score:
            continue
        return round(max(0.0, min(100.0, score)), 1)
    return None


def _journey_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _journey_row(row: Any, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        return row[key]
    except Exception:
        return default


def _journey_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = _journey_text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _journey_lookup_candidates(raw: str) -> list[str]:
    text = _journey_text(raw)
    candidates: list[str] = []
    if text:
        candidates.append(text)
        upper = text.upper()
        if upper.startswith("EMP") and upper[3:].isdigit():
            candidates.append(str(int(upper[3:])))
    return list(dict.fromkeys(candidates))


def _resolve_journey_user(conn: sqlite3.Connection, raw_employee_id: str) -> sqlite3.Row | None:
    for candidate in _journey_lookup_candidates(raw_employee_id):
        row = conn.execute(
            """
            SELECT
                u.id,
                COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT)) AS user_id,
                COALESCE(u.username, '') AS username,
                COALESCE(NULLIF(TRIM(u.display_name), ''), NULLIF(TRIM(u.name), ''), u.username, '') AS display_name,
                COALESCE(u.name, '') AS name,
                COALESCE(u.role, '') AS role,
                COALESCE(u.store_id, '') AS store_id,
                COALESCE(u.phone, '') AS phone,
                COALESCE(u.created_at, '') AS created_at
            FROM users u
            WHERE CAST(u.id AS TEXT) = ?
               OR COALESCE(u.user_id, '') = ?
               OR COALESCE(u.username, '') = ?
            ORDER BY u.id ASC
            LIMIT 1
            """,
            (candidate, candidate, candidate),
        ).fetchone()
        if row:
            return row
    return None


def _resolve_journey_profile(
    conn: sqlite3.Connection,
    raw_employee_id: str,
    user_row: sqlite3.Row | None = None,
) -> sqlite3.Row | None:
    candidates = _journey_lookup_candidates(raw_employee_id)
    if user_row:
        for value in (user_row["id"], user_row["user_id"], user_row["username"]):
            text = _journey_text(value)
            if text and text not in candidates:
                candidates.append(text)
    if not candidates:
        return None
    placeholders = ",".join("?" for _ in candidates)
    return conn.execute(
        f"""
        SELECT *
        FROM employee_profiles
        WHERE employee_id IN ({placeholders})
           OR user_id IN ({placeholders})
           OR employee_name = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        tuple(candidates) + tuple(candidates) + (_journey_text(raw_employee_id),),
    ).fetchone()


def _journey_aliases(user_row: sqlite3.Row | None, profile_row: sqlite3.Row | None, raw_employee_id: str) -> list[str]:
    aliases: list[str] = []
    for value in (
        raw_employee_id,
        _journey_row(user_row, "id"),
        _journey_row(user_row, "user_id"),
        _journey_row(user_row, "username"),
        _journey_row(profile_row, "employee_id"),
        _journey_row(profile_row, "user_id"),
    ):
        text = _journey_text(value)
        if text and text not in aliases:
            aliases.append(text)
    return aliases


def _same_journey_identity(conn: sqlite3.Connection, current_user: dict[str, Any], target_aliases: list[str]) -> bool:
    actor_aliases = [
        _journey_text(current_user.get("user_id")),
        _journey_text(current_user.get("username")),
    ]
    actor_row = _resolve_journey_user(conn, actor_aliases[0] or actor_aliases[1])
    if actor_row:
        actor_aliases.extend([_journey_text(actor_row["id"]), _journey_text(actor_row["user_id"]), _journey_text(actor_row["username"])])
    actor_set = {item for item in actor_aliases if item}
    return bool(actor_set & {item for item in target_aliases if item})


def _assert_journey_access(
    conn: sqlite3.Connection,
    current_user: dict[str, Any],
    *,
    target_store_id: str,
    target_aliases: list[str],
) -> None:
    role = normalize_app_role(str(current_user.get("role") or ""))
    if role == "admin":
        return
    if _same_journey_identity(conn, current_user, target_aliases):
        return
    if role == "store_manager":
        actor_id = _journey_text(current_user.get("user_id")) or _journey_text(current_user.get("username"))
        scope = _store_scope_for_actor(conn, actor_id, role) or _journey_text(current_user.get("store_id"))
        if scope and target_store_id and scope == target_store_id:
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "店长仅可查看本门店员工成长之旅")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "普通员工仅可查看本人成长之旅")


def _journey_store_name(conn: sqlite3.Connection, store_id: str) -> str:
    sid = _journey_text(store_id)
    if not sid:
        return ""
    row = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(store_name), ''), NULLIF(TRIM(name), ''), store_id) AS store_name
        FROM stores
        WHERE LOWER(TRIM(COALESCE(store_id, ''))) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (sid,),
    ).fetchone()
    return _journey_text(row["store_name"] if row else sid)


def _journey_plan(conn: sqlite3.Connection, aliases: list[str]) -> dict[str, Any]:
    if not aliases:
        return {}
    placeholders = ",".join("?" for _ in aliases)
    row = conn.execute(
        f"""
        SELECT *
        FROM growth_plan_records
        WHERE employee_id IN ({placeholders}) OR user_id IN ({placeholders})
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
        """,
        tuple(aliases) + tuple(aliases),
    ).fetchone()
    if not row:
        return {}
    return {
        "plan_id": _journey_text(row["plan_id"]),
        "ability_summary": _journey_text(row["ability_summary"]),
        "target_direction": _journey_text(row["target_direction"]),
        "growth_plan_text": _journey_text(row["growth_plan_text"]),
        "plan_meta": _journey_json_object(row["plan_meta_json"]),
        "source_workflow": _journey_text(row["source_workflow"]),
        "created_at": _journey_text(row["created_at"]),
    }


def _journey_rows_by_aliases(
    conn: sqlite3.Connection,
    sql_template: str,
    aliases: list[str],
    extra_params: tuple[Any, ...] = (),
) -> list[sqlite3.Row]:
    if not aliases:
        return []
    placeholders = ",".join("?" for _ in aliases)
    sql = sql_template.format(placeholders=placeholders)
    return list(conn.execute(sql, tuple(aliases) + tuple(aliases) + extra_params).fetchall())


def _journey_global_day(row: Any, fallback_index: int = 0) -> int:
    stage_no = _journey_int(_journey_row(row, "stage_no"))
    if stage_no is None:
        stage_no = _journey_int(_journey_row(row, "cycle_stage_no"))
    day_index = _journey_int(_journey_row(row, "cycle_day_index"))
    if day_index is None:
        day_index = _journey_int(_journey_row(row, "day_index"))
    if stage_no and day_index:
        return max(1, (stage_no - 1) * 7 + day_index)

    created_at = _journey_text(_journey_row(row, "created_at") or _journey_row(row, "finished_at"))
    if created_at.startswith("2026-05-"):
        try:
            day = int(created_at[8:10])
        except ValueError:
            day = 0
        if 1 <= day <= 14:
            return day

    if day_index:
        return max(1, day_index)
    return max(1, fallback_index + 1)


def _snapshot_score(snapshot: dict[str, Any], key: str) -> float | None:
    for alias in _JOURNEY_SNAPSHOT_ALIASES.get(key, (key,)):
        score = _journey_float(snapshot.get(alias))
        if score is not None:
            return score
    return None


def _ability_values(row: Any | None, score: float | None) -> dict[str, float]:
    snapshot = _journey_json_object(_journey_row(row, "ability_snapshot_json"))
    result: dict[str, float] = {}
    fallback = _journey_float(score, snapshot.get("overall_score"), snapshot.get("overall"), 0.0) or 0.0
    for dim in _JOURNEY_DIMENSIONS:
        result[dim["key"]] = _journey_float(_snapshot_score(snapshot, dim["key"]), fallback, 0.0) or 0.0
    return result


def _risk_from_score(score: float | None, explicit: str = "") -> str:
    text = _journey_text(explicit).lower()
    if text in {"high", "medium", "low"}:
        return text
    if score is None:
        return ""
    if score < 60:
        return "high"
    if score < 85:
        return "medium"
    return "low"


def _serialize_task(row: Any) -> dict[str, Any]:
    return {
        "id": _journey_row(row, "id"),
        "cycle_id": _journey_text(_journey_row(row, "cycle_id")),
        "day_index": _journey_int(_journey_row(row, "day_index")) or 0,
        "title": _journey_text(_journey_row(row, "title")),
        "description": _journey_text(_journey_row(row, "description")),
        "status": _journey_text(_journey_row(row, "status")),
        "module_code": _journey_text(_journey_row(row, "module_code")),
        "module_name": _journey_text(_journey_row(row, "module_name")),
        "ai_score": _journey_float(_journey_row(row, "ai_score")),
        "ai_feedback": _journey_text(_journey_row(row, "ai_feedback")),
        "completed_at": _journey_text(_journey_row(row, "completed_at")),
    }


def _serialize_practice(row: Any | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "evaluation_id": _journey_text(_journey_row(row, "evaluation_id")),
        "practice_id": _journey_text(_journey_row(row, "practice_id")),
        "score": _journey_float(_journey_row(row, "overall_score")),
        "risk_level": _journey_text(_journey_row(row, "risk_level")),
        "weak_dimension": _journey_text(_journey_row(row, "weak_dimension")),
        "coach_summary": _journey_text(_journey_row(row, "coach_summary")),
        "improvement_advice": _journey_text(_journey_row(row, "improvement_advice")),
        "module_code": _journey_text(_journey_row(row, "module_code")),
        "module_name": _journey_text(_journey_row(row, "module_name")),
        "created_at": _journey_text(_journey_row(row, "created_at")),
    }


def _serialize_learning(row: Any | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "evaluation_id": _journey_text(_journey_row(row, "evaluation_id")),
        "score": _journey_float(_journey_row(row, "score")),
        "learning_summary": _journey_text(_journey_row(row, "learning_summary")),
        "manager_feedback": _journey_text(_journey_row(row, "manager_feedback")),
        "module_code": _journey_text(_journey_row(row, "module_code")),
        "module_name": _journey_text(_journey_row(row, "module_name")),
        "created_at": _journey_text(_journey_row(row, "created_at")),
    }


def _serialize_assessment(row: Any | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "record_id": _journey_row(row, "id"),
        "score": _journey_float(_journey_row(row, "score")),
        "is_pass": bool(int(_journey_row(row, "is_pass", 0) or 0)),
        "comment": _journey_text(_journey_row(row, "comment")),
        "submit_status": _journey_text(_journey_row(row, "submit_status")),
        "finished_at": _journey_text(_journey_row(row, "finished_at")),
    }


def _journey_title(day: int, score: float | None, assessment: dict[str, Any] | None, learning: dict[str, Any] | None, module_name: str) -> str:
    if day == 1:
        return "入职建档"
    if day == 3:
        return "完成产品基础学习"
    if day == 7:
        if assessment and not assessment.get("is_pass"):
            return "阶段评估未通过"
        return "阶段评估"
    if day == 8:
        return "导师 Agent 介入" if learning else "补强计划启动"
    if day == 11:
        return "嫌贵话术陪练突破"
    if day == 13:
        return "模拟考核通过" if assessment and assessment.get("is_pass") else "模拟考核"
    if day == 14:
        return "阶段晋级"
    if module_name:
        return f"{module_name}训练"
    return f"Day {day} 训练记录"


def _journey_subtitle(day: int, score: float | None, risk_level: str, passed: bool) -> str:
    if day == 1:
        return "建立员工档案与能力基线"
    if day == 7 and risk_level == "high":
        return "风险红灯触发补强"
    if day == 8:
        return "生成补强计划"
    if day == 11:
        return "关键话术跃迁"
    if day == 13:
        return "模拟考核状态回稳"
    if day == 14 and passed:
        return "通过上岗"
    if score is not None:
        return f"综合 {score:g} 分"
    return "训练进度记录"


def _first_text(*values: Any) -> str:
    for value in values:
        text = _journey_text(value)
        if text:
            return text
    return ""


def build_employee_journey_payload(
    conn: sqlite3.Connection,
    employee_id: str,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    user_row = _resolve_journey_user(conn, employee_id)
    profile_row = _resolve_journey_profile(conn, employee_id, user_row)
    if not user_row and profile_row:
        user_row = _resolve_journey_user(conn, _journey_row(profile_row, "employee_id") or _journey_row(profile_row, "user_id"))
    if not user_row and not profile_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "员工不存在")

    aliases = _journey_aliases(user_row, profile_row, employee_id)
    store_id = _first_text(_journey_row(user_row, "store_id"), _journey_row(profile_row, "store_id"))
    _assert_journey_access(conn, current_user, target_store_id=store_id, target_aliases=aliases)

    store_name = _journey_store_name(conn, store_id)
    plan = _journey_plan(conn, aliases)

    if aliases:
        placeholders = ",".join("?" for _ in aliases)
        cycles = list(conn.execute(
            f"""
        SELECT *
        FROM training_cycles
        WHERE user_id IN ({placeholders}) OR plan_id IN (
            SELECT plan_id FROM growth_plan_records
            WHERE employee_id IN ({placeholders}) OR user_id IN ({placeholders})
        )
        ORDER BY stage_no ASC, datetime(created_at) ASC, id ASC
        """,
            tuple(aliases) + tuple(aliases) + tuple(aliases),
        ).fetchall())
    else:
        cycles = []

    cycle_ids = [_journey_text(row["cycle_id"]) for row in cycles if _journey_text(row["cycle_id"])]
    tasks_by_day: dict[int, list[dict[str, Any]]] = {}
    if aliases:
        placeholders = ",".join("?" for _ in aliases)
        cycle_filter = ""
        params: list[Any] = list(aliases)
        if cycle_ids:
            cycle_placeholders = ",".join("?" for _ in cycle_ids)
            cycle_filter = f" OR cdt.cycle_id IN ({cycle_placeholders})"
            params.extend(cycle_ids)
        task_rows = conn.execute(
            f"""
            SELECT cdt.*, COALESCE(tc.stage_no, 0) AS cycle_stage_no
            FROM cycle_daily_tasks cdt
            LEFT JOIN training_cycles tc ON tc.cycle_id = cdt.cycle_id
            WHERE cdt.user_id IN ({placeholders}){cycle_filter}
            ORDER BY COALESCE(tc.stage_no, 0), cdt.day_index, cdt.id
            """,
            tuple(params),
        ).fetchall()
        for row in task_rows:
            tasks_by_day.setdefault(_journey_global_day(row), []).append(_serialize_task(row))

    ability_rows = _journey_rows_by_aliases(
        conn,
        """
        SELECT id, update_id, practice_id, user_id, employee_id, score, overall_score, risk_level,
               focus_dimension, update_summary, ability_comment, ability_snapshot_json,
               cycle_day_index, module_code, module_name, cycle_id, stage_no, created_at
        FROM ability_update_records
        WHERE user_id IN ({placeholders}) OR employee_id IN ({placeholders})
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        aliases,
    )
    ability_by_day: dict[int, sqlite3.Row] = {}
    for index, row in enumerate(ability_rows):
        ability_by_day[_journey_global_day(row, index)] = row

    practice_rows = _journey_rows_by_aliases(
        conn,
        """
        SELECT id, evaluation_id, practice_id, user_id, employee_id, overall_score, risk_level,
               weak_dimension, coach_summary, improvement_advice, cycle_day_index,
               module_code, module_name, cycle_id, stage_no, created_at
        FROM practice_eval_records
        WHERE user_id IN ({placeholders}) OR employee_id IN ({placeholders})
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        aliases,
    )
    practice_by_day: dict[int, sqlite3.Row] = {}
    for index, row in enumerate(practice_rows):
        practice_by_day[_journey_global_day(row, index)] = row

    learning_rows = _journey_rows_by_aliases(
        conn,
        """
        SELECT id, evaluation_id, user_id, employee_id, score, learning_summary, manager_feedback,
               module_code, module_name, created_at
        FROM learning_eval_records
        WHERE user_id IN ({placeholders}) OR employee_id IN ({placeholders})
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        aliases,
    )
    learning_by_day: dict[int, sqlite3.Row] = {}
    for index, row in enumerate(learning_rows):
        learning_by_day[_journey_global_day(row, index)] = row

    assessment_rows = conn.execute(
        f"""
        SELECT id, user_id, employee_name, score, is_pass, comment, cycle_day_index,
               submit_status, finished_at, finished_at AS created_at
        FROM assessment_records
        WHERE user_id IN ({",".join("?" for _ in aliases) if aliases else "''"})
        ORDER BY datetime(finished_at) ASC, id ASC
        """,
        tuple(aliases),
    ).fetchall() if aliases else []
    assessment_by_day: dict[int, sqlite3.Row] = {}
    for index, row in enumerate(assessment_rows):
        assessment_by_day[_journey_global_day(row, index)] = row

    available_days = set(tasks_by_day) | set(ability_by_day) | set(practice_by_day) | set(learning_by_day) | set(assessment_by_day)
    if cycles:
        available_days.update(range(1, min(14, sum(max(1, int(_journey_row(c, "total_days", 7) or 7)) for c in cycles)) + 1))
    total_days = max(available_days) if available_days else 0
    if total_days:
        total_days = max(14, total_days)

    nodes: list[dict[str, Any]] = []
    previous_score: float | None = None
    for day in range(1, total_days + 1):
        ability = ability_by_day.get(day)
        practice_row = practice_by_day.get(day)
        learning_row = learning_by_day.get(day)
        assessment_row = assessment_by_day.get(day)
        practice = _serialize_practice(practice_row)
        learning = _serialize_learning(learning_row)
        assessment = _serialize_assessment(assessment_row)
        score = _journey_float(
            _journey_row(ability, "overall_score"),
            _journey_row(ability, "score"),
            practice["score"] if practice else None,
            learning["score"] if learning else None,
            assessment["score"] if assessment else None,
            previous_score,
        )
        if score is not None:
            previous_score = score
        module_name = _first_text(
            _journey_row(ability, "module_name"),
            practice["module_name"] if practice else "",
            learning["module_name"] if learning else "",
            tasks_by_day.get(day, [{}])[0].get("module_name") if tasks_by_day.get(day) else "",
        )
        risk_level = _risk_from_score(score, _first_text(_journey_row(ability, "risk_level"), practice["risk_level"] if practice else ""))
        passed = bool((assessment and assessment.get("is_pass")) or (day == total_days and score is not None and score >= 80))
        if assessment and not assessment.get("is_pass"):
            risk_level = "high"
        daily_tasks = tasks_by_day.get(day, [])
        summary = _first_text(
            _journey_row(ability, "update_summary"),
            _journey_row(ability, "ability_comment"),
            learning["learning_summary"] if learning else "",
            assessment["comment"] if assessment else "",
            practice["coach_summary"] if practice else "",
            daily_tasks[0].get("description") if daily_tasks else "",
        )
        key_event = day in {1, 7, 8, 11, 13, total_days} or risk_level == "high" or passed
        node = {
            "day_index": day,
            "label": f"Day {day}",
            "title": _journey_title(day, score, assessment, learning, module_name),
            "subtitle": _journey_subtitle(day, score, risk_level, passed),
            "stage_no": _journey_int(_journey_row(ability, "stage_no")) or (2 if day > 7 else 1),
            "cycle_day_index": ((day - 1) % 7) + 1,
            "score": score,
            "risk_level": risk_level,
            "module_name": module_name,
            "summary": summary,
            "key_event": key_event,
            "passed": passed,
            "score_delta": round(score - (nodes[-1]["score"] if nodes and nodes[-1].get("score") is not None and score is not None else score), 1) if score is not None else 0.0,
            "ability_values": _ability_values(ability, score),
            "details": {
                "tasks": daily_tasks,
                "practice": practice,
                "learning": learning,
                "assessment": assessment,
            },
        }
        nodes.append(node)

    score_series = [float(node["score"]) for node in nodes if node.get("score") is not None]
    start_score = score_series[0] if score_series else None
    current_score = score_series[-1] if score_series else None
    return {
        "employee": {
            "id": _journey_text(_journey_row(user_row, "id") or _journey_row(profile_row, "employee_id")),
            "user_id": _journey_text(_journey_row(user_row, "user_id") or _journey_row(profile_row, "user_id")),
            "username": _journey_text(_journey_row(user_row, "username")),
            "name": _first_text(_journey_row(user_row, "display_name"), _journey_row(profile_row, "employee_name"), _journey_row(user_row, "name")),
            "role": normalize_app_role(_first_text(_journey_row(user_row, "role"), _journey_row(profile_row, "role"))),
            "role_label": _position_for_role(_first_text(_journey_row(user_row, "role"), _journey_row(profile_row, "role"))),
            "position": _first_text(_journey_row(profile_row, "position"), _journey_row(profile_row, "job_title"), _position_for_role(_journey_row(user_row, "role"))),
            "store_id": store_id,
            "store_name": store_name,
            "mentor_name": _journey_text(_journey_row(profile_row, "mentor_name") or _journey_row(user_row, "mentor_name")),
            "phone": _journey_text(_journey_row(user_row, "phone")),
            "created_at": _journey_text(_journey_row(user_row, "created_at")),
            "initial_ability": _journey_text(_journey_row(profile_row, "initial_ability")),
        },
        "plan": plan,
        "summary": {
            "total_days": total_days,
            "start_score": start_score,
            "current_score": current_score,
            "score_delta": round((current_score or 0.0) - (start_score or 0.0), 1) if score_series else 0.0,
            "high_risk_count": sum(1 for node in nodes if node.get("risk_level") == "high"),
            "key_event_count": sum(1 for node in nodes if node.get("key_event")),
            "passed": bool(nodes[-1]["passed"]) if nodes else False,
        },
        "dimensions": _JOURNEY_DIMENSIONS,
        "nodes": nodes,
        "viewer_role": normalize_app_role(str(current_user.get("role") or "")),
    }


class UserCreateBody(BaseModel):
    username: str = Field("", description="登录账号")
    password: str = Field("", description="初始密码")
    display_name: str = Field("", description="显示名")
    role: str = Field("trainee", description="角色")
    store_id: str = Field("", description="门店")
    phone: str = Field("", description="手机")
    mentor_name: str = Field("", description="带教人")


class UserUpdateBody(BaseModel):
    display_name: str | None = None
    role: str | None = None
    store_id: str | None = None
    phone: str | None = None
    password: str | None = None
    mentor_name: str | None = None


class UserDeleteByIdBody(BaseModel):
    """与路径型删除共用逻辑；用于避免部分网关把 /users/{id}/delete 改写为 /users/{id} 导致 POST 405。"""

    user_id: str = Field(..., min_length=1, description="users 表主键 id 的字符串形式")


class StoreCreateBody(BaseModel):
    store_id: str = Field("", description="门店编号")
    store_name: str = Field("", description="门店名称")
    region: str = Field("", description="区域")


class StoreUpdateBody(BaseModel):
    store_name: str = Field("", description="门店名称")
    region: str = Field("", description="区域")
    manager_name: str = Field("", description="店长姓名")


class RoleSettingCreateBody(BaseModel):
    role_key: str = Field("", description="角色标识，小写+下划线")
    display_name: str = Field("", description="展示名称")
    description: str = Field("", description="说明")
    sort_order: int = Field(0, description="排序，越小越靠前")
    assignable_by_manager: int = Field(1, ge=0, le=1, description="店长是否可在创建/编辑时分配")


class RoleSettingUpdateBody(BaseModel):
    display_name: str | None = None
    description: str | None = None
    sort_order: int | None = None
    assignable_by_manager: int | None = Field(None, ge=0, le=1)
    is_enabled: int | None = Field(None, ge=0, le=1)


@router.get("/role-settings", dependencies=[Depends(_manage_roles)])
def list_role_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
    for_assign: int = Query(0, description="1=仅返回当前账号可分配的角色"),
) -> dict[str, Any]:
    _log.debug("list_role_settings user_id=%s for_assign=%s", current_user.get("user_id"), for_assign)
    actor_role = str(current_user.get("role") or "")
    with get_conn() as conn:
        try:
            rows = conn.execute(
                """
                SELECT
                    rs.role_key,
                    rs.display_name,
                    rs.description,
                    rs.sort_order,
                    rs.is_system,
                    rs.assignable_by_manager,
                    COALESCE(rs.is_enabled, 1) AS is_enabled,
                    (
                        SELECT COUNT(*) FROM users u
                        WHERE LOWER(TRIM(COALESCE(u.role, ''))) = LOWER(TRIM(rs.role_key))
                    ) AS user_count
                FROM role_settings rs
                ORDER BY rs.sort_order ASC, rs.role_key ASC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    items: list[dict[str, Any]] = []
    for r in rows:
        rk = (r["role_key"] or "").strip()
        items.append(
            {
                "role_key": rk,
                "display_name": (r["display_name"] or "").strip(),
                "description": (r["description"] or "").strip(),
                "sort_order": int(r["sort_order"] or 0),
                "is_system": int(r["is_system"] or 0),
                "assignable_by_manager": int(r["assignable_by_manager"] or 0),
                "is_enabled": int(r["is_enabled"] or 1),
                "user_count": int(r["user_count"] or 0),
            }
        )

    if for_assign:
        items = [x for x in items if x["is_enabled"] == 1]
        ar = normalize_app_role(actor_role)
        if ar != "admin":
            items = [x for x in items if x["assignable_by_manager"] == 1]

    return {"code": 200, "message": "success", "data": {"items": items}}


@router.post("/role-settings", dependencies=[Depends(_admin_only)])
def create_role_setting(
    body: RoleSettingCreateBody,
) -> Any:
    _log.info("create_role_setting start role_key=%s", body.role_key)
    rk = (body.role_key or "").strip().lower()
    dn = (body.display_name or "").strip()
    desc = (body.description or "").strip()
    if not rk or not _ROLE_KEY_RE.match(rk):
        _log.warning("create_role_setting validation failed reason=invalid_role_key role_key=%s", body.role_key)
        return JSONResponse(
            status_code=400,
            content={
                "code": 400,
                "message": "角色标识须为 2～48 位：小写字母开头，仅含小写字母、数字、下划线",
                "data": {},
            },
        )
    if not dn or len(dn) > 128:
        _log.warning("create_role_setting validation failed reason=invalid_display_name")
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "展示名称须为 1～128 字", "data": {}},
        )
    now = _now_iso()
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO role_settings (
                    role_key, display_name, description, sort_order,
                    is_system, assignable_by_manager, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    rk,
                    dn,
                    desc,
                    int(body.sort_order),
                    int(body.assignable_by_manager),
                    now,
                    now,
                ),
            )
    except sqlite3.IntegrityError:
        _log.warning("create_role_setting validation failed reason=duplicate_role_key role_key=%s", rk)
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "该角色标识已存在", "data": {}},
        )
    _log.info("create_role_setting success role_key=%s", rk)
    return {"code": 200, "message": "success", "data": {"role_key": rk}}


@router.patch("/role-settings/{role_key}", dependencies=[Depends(_admin_only)])
def update_role_setting(role_key: str, body: RoleSettingUpdateBody) -> Any:
    _log.info("update_role_setting start role_key=%s", role_key)
    rk = (role_key or "").strip().lower()
    if not rk:
        _log.warning("update_role_setting validation failed reason=empty_role_key")
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "无效角色", "data": {}},
        )
    sets: list[str] = []
    params: list[Any] = []
    if body.display_name is not None:
        dn = (body.display_name or "").strip()
        if not dn or len(dn) > 128:
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "展示名称须为 1～128 字", "data": {}},
            )
        sets.append("display_name = ?")
        params.append(dn)
    if body.description is not None:
        sets.append("description = ?")
        params.append((body.description or "").strip())
    if body.sort_order is not None:
        sets.append("sort_order = ?")
        params.append(int(body.sort_order))
    if body.assignable_by_manager is not None:
        sets.append("assignable_by_manager = ?")
        params.append(int(body.assignable_by_manager))
    if body.is_enabled is not None:
        sets.append("is_enabled = ?")
        params.append(int(body.is_enabled))
    if not sets:
        return {"code": 200, "message": "success", "data": {"role_key": rk}}

    sets.append("updated_at = ?")
    params.append(_now_iso())
    params.append(rk)

    with get_conn() as conn:
        cur = conn.execute(
            "SELECT role_key FROM role_settings WHERE LOWER(TRIM(role_key)) = ? LIMIT 1",
            (rk,),
        ).fetchone()
        if not cur:
            _log.warning("update_role_setting validation failed reason=role_not_found role_key=%s", rk)
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "角色不存在", "data": {}},
            )
        conn.execute(
            f"UPDATE role_settings SET {', '.join(sets)} WHERE LOWER(TRIM(role_key)) = ?",
            tuple(params),
        )
    _log.info("update_role_setting success role_key=%s", rk)
    return {"code": 200, "message": "success", "data": {"role_key": rk}}


@router.delete("/role-settings/{role_key}", dependencies=[Depends(_admin_only)])
def delete_role_setting(role_key: str) -> Any:
    _log.info("delete_role_setting start role_key=%s", role_key)
    rk = (role_key or "").strip().lower()
    if not rk:
        _log.warning("delete_role_setting validation failed reason=empty_role_key")
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "无效角色", "data": {}},
        )
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_system FROM role_settings WHERE LOWER(TRIM(role_key)) = ? LIMIT 1",
            (rk,),
        ).fetchone()
        if not row:
            _log.warning("delete_role_setting validation failed reason=role_not_found role_key=%s", rk)
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "角色不存在", "data": {}},
            )
        if int(row["is_system"] or 0) == 1:
            _log.warning("delete_role_setting validation failed reason=system_role role_key=%s", rk)
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "系统内置角色不可删除", "data": {}},
            )
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE LOWER(TRIM(COALESCE(role,''))) = ?",
            (rk,),
        ).fetchone()
        if n and int(n["c"] or 0) > 0:
            _log.warning("delete_role_setting validation failed reason=role_in_use role_key=%s user_count=%s", rk, n["c"])
            return JSONResponse(
                status_code=400,
                content={
                    "code": 400,
                    "message": "仍有员工使用该角色，无法删除",
                    "data": {},
                },
            )
        conn.execute(
            "DELETE FROM role_settings WHERE LOWER(TRIM(role_key)) = ?",
            (rk,),
        )
    _log.info("delete_role_setting success role_key=%s", rk)
    return {"code": 200, "message": "success", "data": {"role_key": rk}}


@router.get("/stores", dependencies=[Depends(_manage_roles)])
def list_store_options(
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """返回完整门店列表，包含区域和店长信息。"""
    _log.debug("list_store_options user_id=%s", current_user.get("user_id"))
    uid = str(current_user.get("user_id") or "").strip()
    role = str(current_user.get("role") or "")
    items: list[dict[str, Any]] = []
    with get_conn() as conn:
        scope = _store_scope_for_actor(conn, uid, role)
        try:
            rows = conn.execute(
                """
                SELECT store_id,
                       COALESCE(
                           NULLIF(TRIM(store_name), ''),
                           NULLIF(TRIM(name), ''),
                           store_id
                       ) AS store_name,
                       COALESCE(region, '') AS region,
                       COALESCE(manager_name, '') AS manager_name,
                       COALESCE(created_at, '') AS created_at
                FROM stores
                ORDER BY store_id
                """
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        for r in rows:
            sid = (r["store_id"] or "").strip()
            if not sid:
                continue
            if scope is not None and sid != scope:
                continue

            emp_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE COALESCE(store_id, '') = ?",
                (sid,),
            ).fetchone()
            emp_count = int(emp_row["cnt"] or 0) if emp_row else 0
            if scope is None and emp_count <= 0:
                continue

            manager_rows = conn.execute(
                """
                SELECT
                    id,
                    username,
                    COALESCE(NULLIF(TRIM(display_name), ''), NULLIF(TRIM(name), ''), username) AS manager_name
                FROM users
                WHERE COALESCE(store_id, '') = ?
                  AND LOWER(TRIM(COALESCE(role, ''))) IN ('store_manager', 'leader')
                ORDER BY id ASC
                """,
                (sid,),
            ).fetchall()
            manager_count = len(manager_rows)
            manager_name = (manager_rows[0]["manager_name"] or "").strip() if manager_count > 0 else ""

            items.append(
                {
                    "store_id": sid,
                    "store_name": (r["store_name"] or sid).strip(),
                    "region": (r["region"] or "").strip(),
                    "manager_name": manager_name,
                    "created_at": (r["created_at"] or "").strip(),
                    "employee_count": emp_count,
                    "manager_count": manager_count,
                    "manager_conflict": manager_count > 1,
                }
            )

        if not items:
            rows2 = conn.execute(
                """
                SELECT DISTINCT store_id
                FROM users
                WHERE COALESCE(store_id, '') != ''
                ORDER BY store_id
                """
            ).fetchall()
            for r in rows2:
                sid = (r["store_id"] or "").strip()
                if not sid:
                    continue
                if scope is not None and sid != scope:
                    continue
                emp_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM users WHERE COALESCE(store_id, '') = ?",
                    (sid,),
                ).fetchone()
                emp_count = int(emp_row["cnt"] or 0) if emp_row else 0
                if scope is None and emp_count <= 0:
                    continue
                manager_rows = conn.execute(
                    """
                    SELECT
                        id,
                        username,
                        COALESCE(NULLIF(TRIM(display_name), ''), NULLIF(TRIM(name), ''), username) AS manager_name
                    FROM users
                    WHERE COALESCE(store_id, '') = ?
                      AND LOWER(TRIM(COALESCE(role, ''))) IN ('store_manager', 'leader')
                    ORDER BY id ASC
                    """,
                    (sid,),
                ).fetchall()
                manager_count = len(manager_rows)
                manager_name = (manager_rows[0]["manager_name"] or "").strip() if manager_count > 0 else ""
                items.append(
                    {
                        "store_id": sid,
                        "store_name": sid,
                        "region": "",
                        "manager_name": manager_name,
                        "created_at": "",
                        "employee_count": emp_count,
                        "manager_count": manager_count,
                        "manager_conflict": manager_count > 1,
                    }
                )

    return {"code": 200, "message": "success", "data": {"items": items}}


@router.post("/stores", dependencies=[Depends(_admin_only)])
def create_store_ep(
    body: StoreCreateBody,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.info("create_store start store_id=%s store_name=%s", body.store_id, body.store_name)
    store_id = (body.store_id or "").strip()
    store_name = (body.store_name or "").strip()
    region = (body.region or "").strip()
    if not store_id or not _STORE_ID_RE.match(store_id):
        _log.warning("create_store validation failed reason=invalid_store_id store_id=%s", body.store_id)
        return JSONResponse(
            status_code=400,
            content={
                "code": 400,
                "message": "门店编号须为 1～64 位字母、数字、下划线或短横线",
                "data": {},
            },
        )
    if not store_name or len(store_name) > 128:
        _log.warning("create_store validation failed reason=invalid_store_name store_name=%s", store_name)
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "门店名称须为 1～128 字", "data": {}},
        )
    if len(region) > 64:
        _log.warning("create_store validation failed reason=region_too_long store_id=%s", store_id)
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "区域名称最多 64 字", "data": {}},
        )
    now = _now_iso()
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO stores (
                    id, store_id, store_name, name, region, manager_name,
                    sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, '', 0, ?, ?)
                """,
                (store_id, store_id, store_name, store_name, region, now, now),
            )
            _sync_store_manager_cache(conn, store_id)
            log_audit_from_user(conn, current_user, action="store_create", target_type="store",
                                target_id=store_id, target_name=store_name)
    except sqlite3.IntegrityError:
        _log.warning("create_store validation failed reason=duplicate_store_id store_id=%s", store_id)
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "门店编号已存在", "data": {}},
        )
    _log.info("create_store success store_id=%s", store_id)
    return {"code": 200, "message": "success", "data": {"store_id": store_id}}


@router.patch("/stores/{store_id}", dependencies=[Depends(_manage_roles)])
def update_store_ep(
    store_id: str,
    body: StoreUpdateBody,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.info("update_store start store_id=%s user_id=%s", store_id, current_user.get("user_id"))
    sid = (store_id or "").strip()
    if not sid:
        _log.warning("update_store validation failed reason=empty_store_id user_id=%s", current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "无效门店", "data": {}},
        )
    store_name = (body.store_name or "").strip()
    if not store_name or len(store_name) > 128:
        _log.warning("update_store validation failed reason=invalid_store_name store_id=%s user_id=%s", sid, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "门店名称须为 1～128 字", "data": {}},
        )
    region = (body.region or "").strip()
    if len(region) > 64:
        _log.warning("update_store validation failed reason=region_too_long store_id=%s user_id=%s", sid, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "区域名称最多 64 字", "data": {}},
        )
    actor_id = str(current_user.get("user_id") or "").strip()
    actor_role = str(current_user.get("role") or "")
    now = _now_iso()
    with get_conn() as conn:
        scope = _store_scope_for_actor(conn, actor_id, actor_role)
        if scope is not None and sid != scope:
            _log.warning("update_store validation failed reason=store_out_of_scope store_id=%s user_id=%s", sid, current_user.get("user_id"))
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "无权修改其他门店", "data": {}},
            )
        cur = conn.execute(
            "SELECT store_id FROM stores WHERE store_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
        if not cur:
            _log.warning("update_store validation failed reason=store_not_found store_id=%s user_id=%s", sid, current_user.get("user_id"))
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "门店不存在", "data": {}},
            )
        conn.execute(
            """
            UPDATE stores
            SET store_name = ?, name = ?, region = ?, updated_at = ?
            WHERE store_id = ?
            """,
            (store_name, store_name, region, now, sid),
        )
        _sync_store_manager_cache(conn, sid)
        log_audit_from_user(conn, current_user, action="store_update", target_type="store",
                            target_id=sid, target_name=store_name)
    _log.info("update_store success store_id=%s user_id=%s", sid, current_user.get("user_id"))
    return {"code": 200, "message": "success", "data": {"store_id": sid}}


@router.delete("/stores/{store_id}", dependencies=[Depends(_admin_only)])
def delete_store_ep(
    store_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.info("delete_store start store_id=%s", store_id)
    sid = (store_id or "").strip()
    if not sid:
        _log.warning("delete_store validation failed reason=empty_store_id")
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "无效门店", "data": {}},
        )
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT store_id FROM stores WHERE store_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
        if not cur:
            _log.warning("delete_store validation failed reason=store_not_found store_id=%s", sid)
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "门店不存在", "data": {}},
            )
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE COALESCE(store_id,'') = ?",
            (sid,),
        ).fetchone()
        cnt = int(n["c"] or 0) if n else 0
        if cnt > 0:
            _log.warning("delete_store validation failed reason=store_has_users store_id=%s user_count=%d", sid, cnt)
            return JSONResponse(
                status_code=400,
                content={
                    "code": 400,
                    "message": f"该门店下仍有 {cnt} 名用户，无法删除",
                    "data": {},
                },
            )
        conn.execute("DELETE FROM stores WHERE store_id = ?", (sid,))
        log_audit_from_user(conn, current_user, action="store_delete", target_type="store",
                            target_id=sid)
    _log.info("delete_store success store_id=%s", sid)
    return {"code": 200, "message": "success", "data": {"store_id": sid}}


@router.get("/users", dependencies=[Depends(_manage_roles)])
def list_users(
    current_user: Annotated[dict, Depends(get_current_user)],
    q: str = Query(""),
) -> dict[str, Any]:
    _log.debug("list_users user_id=%s q=%s", current_user.get("user_id"), q)
    uid = str(current_user.get("user_id") or "").strip()
    role = str(current_user.get("role") or "")
    needle = (q or "").strip()
    like = f"%{needle}%" if needle else None

    with get_conn() as conn:
        scope = _store_scope_for_actor(conn, uid, role)
        params: list[Any] = []
        where_parts: list[str] = ["1=1"]
        if scope is not None:
            where_parts.append("COALESCE(u.store_id,'') = ?")
            params.append(scope)
        if like is not None:
            where_parts.append(
                "(u.username LIKE ? OR COALESCE(u.display_name,'') LIKE ? OR CAST(u.id AS TEXT) LIKE ?)"
            )
            params.extend([like, like, like])

        where_sql = " AND ".join(where_parts)
        sql = f"""
            SELECT u.id, u.username, u.role, u.display_name,
                   COALESCE(u.store_id,'') AS store_id,
                   COALESCE(
                       NULLIF(TRIM(s.store_name), ''),
                       NULLIF(TRIM(s.name), '')
                   ) AS store_label,
                   (
                       SELECT COALESCE(ep.mentor_name, '')
                       FROM employee_profiles ep
                       WHERE ep.employee_id = COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT))
                          OR ep.user_id = COALESCE(NULLIF(TRIM(u.user_id), ''), CAST(u.id AS TEXT))
                       ORDER BY ep.id DESC
                       LIMIT 1
                   ) AS mentor_name,
                   COALESCE(u.phone,'') AS phone,
                   u.created_at
            FROM users u
            LEFT JOIN stores s
              ON LOWER(TRIM(COALESCE(s.store_id,''))) = LOWER(TRIM(COALESCE(u.store_id,'')))
            WHERE {where_sql}
            ORDER BY u.id ASC
        """
        rows = conn.execute(sql, tuple(params)).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        rid = int(row["id"])
        sid = (row["store_id"] or "").strip()
        slab = (row["store_label"] or "").strip()
        out.append(
            {
                "id": str(rid),
                "username": row["username"],
                "role": row["role"],
                "display_name": row["display_name"] or row["username"],
                "store_id": sid,
                "store_label": slab,
                "mentor_name": (row["mentor_name"] or "").strip(),
                "phone": row["phone"] or "",
                "employee_no": _employee_no(rid),
                "created_at": row["created_at"],
            }
        )
    return {"code": 200, "message": "success", "data": {"items": out}}


@router.get("/employee/{employee_id}/journey")
def get_employee_journey(
    employee_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    """聚合员工 14 天成长之旅：计划、任务、陪练、学习、考试与能力轨迹。"""
    eid = (employee_id or "").strip()
    if not eid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "员工 ID 不能为空")
    if eid.lower() in {"self", "me", "current"}:
        eid = str(current_user.get("user_id") or current_user.get("username") or "").strip()
        if not eid:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "当前登录用户无效")
    _log.debug("get_employee_journey employee_id=%s viewer=%s", eid, current_user.get("user_id"))
    with get_conn() as conn:
        payload = build_employee_journey_payload(conn, eid, current_user)
    return {"code": 200, "message": "success", "data": payload}


@router.post("/users", dependencies=[Depends(_manage_roles)])
def create_user_ep(
    body: UserCreateBody,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.info("create_user start username=%s user_id=%s", body.username, current_user.get("user_id"))
    username = (body.username or "").strip()
    password = (body.password or "").strip()
    display_name = (body.display_name or "").strip()
    role_raw = normalize_app_role(body.role or "trainee")
    store_id = (body.store_id or "").strip()
    phone = (body.phone or "").strip()
    mentor_name = (body.mentor_name or "").strip()

    actor_role = str(current_user.get("role") or "")
    actor_id = str(current_user.get("user_id") or "").strip()

    if not _USERNAME_RE.match(username):
        _log.warning("create_user validation failed reason=invalid_username username=%s user_id=%s", username, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={
                "code": 400,
                "message": "用户名须为 3～32 位字母、数字、下划线或短横线",
                "data": {},
            },
        )
    if len(password) < 8:
        _log.warning("create_user validation failed reason=password_too_short username=%s user_id=%s", username, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "密码至少 8 位", "data": {}},
        )
    if not display_name:
        _log.warning("create_user validation failed reason=empty_display_name username=%s user_id=%s", username, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "请填写员工姓名", "data": {}},
        )
    if not role_raw:
        _log.warning("create_user validation failed reason=empty_role username=%s user_id=%s", username, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "请选择员工角色", "data": {}},
        )
    if not store_id:
        _log.warning("create_user validation failed reason=empty_store_id username=%s user_id=%s", username, current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "请选择归属门店", "data": {}},
        )
    if len(mentor_name) > 100:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "带教人不能超过 100 字", "data": {}},
        )

    with get_conn() as conn:
        allowed = _assignable_role_keys(conn, actor_role)
        if role_raw not in allowed:
            _log.warning("create_user validation failed reason=role_not_assignable role=%s user_id=%s", role_raw, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "无权创建该角色", "data": {}},
            )
        scope = _store_scope_for_actor(conn, actor_id, actor_role)
        if scope is not None:
            store_id = scope
        if not _store_exists(conn, store_id):
            _log.warning(
                "create_user validation failed reason=store_not_found username=%s store_id=%s user_id=%s",
                username,
                store_id,
                current_user.get("user_id"),
            )
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "归属门店不存在，请先在门店管理中创建", "data": {}},
            )

        if role_raw in _MANAGER_ROLE_KEYS:
            conflict = _find_store_manager_conflict(conn, store_id=store_id)
            if conflict:
                _log.warning(
                    "create_user validation failed reason=store_manager_conflict username=%s store_id=%s manager_id=%s user_id=%s",
                    username,
                    store_id,
                    conflict["id"],
                    current_user.get("user_id"),
                )
                manager_name = (conflict["manager_name"] or conflict["username"] or "").strip()
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": 400,
                        "message": f"该门店已有店长「{manager_name or conflict['id']}」，每个门店仅可设置 1 位店长",
                        "data": {},
                    },
                )

        existed = conn.execute(
            "SELECT id FROM users WHERE username = ? LIMIT 1",
            (username,),
        ).fetchone()
        if existed:
            _log.warning("create_user validation failed reason=username_exists username=%s user_id=%s", username, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "用户名已存在", "data": {}},
            )

        now = _now_iso()
        hp = get_password_hash(password)
        conn.execute(
            """
            INSERT INTO users (
                username, hashed_password, role, display_name, store_id, phone, created_at,
                user_id, name, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                hp,
                role_raw,
                display_name,
                store_id,
                phone,
                now,
                username,
                display_name,
                now,
            ),
        )
        new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        new_id = int(new_id)
        _upsert_personnel_employee_profile(
            conn,
            user_key=username,
            display_name=display_name,
            role=role_raw,
            store_id=store_id,
            mentor_name=mentor_name,
        )
        _sync_store_manager_cache(conn, store_id)
        log_audit_from_user(conn, current_user, action="user_create", target_type="user",
                            target_id=str(new_id), target_name=display_name or username)

    _log.info("create_user success username=%s new_id=%s user_id=%s", username, new_id, current_user.get("user_id"))
    return {
        "code": 200,
        "message": "success",
        "data": {
            "id": str(new_id),
            "employee_no": _employee_no(new_id),
        },
    }


@router.patch("/users/{user_id}", dependencies=[Depends(_manage_roles)])
def update_user_ep(
    user_id: str,
    body: UserUpdateBody,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.info("update_user start user_id=%s actor_id=%s", user_id, current_user.get("user_id"))
    target = (user_id or "").strip()
    if not target:
        _log.warning("update_user validation failed reason=empty_user_id actor_id=%s", current_user.get("user_id"))
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "无效用户", "data": {}},
        )

    actor_id = str(current_user.get("user_id") or "").strip()
    actor_role = str(current_user.get("role") or "")

    updates: list[str] = []
    params: list[Any] = []
    requested_role: str | None = None
    requested_store_id: str | None = None
    requested_mentor_name: str | None = None

    if body.display_name is not None:
        dn = (body.display_name or "").strip()
        if not dn:
            _log.warning("update_user validation failed reason=empty_display_name target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "员工姓名不能为空", "data": {}},
            )
        updates.append("display_name = ?")
        params.append(dn)
        updates.append("name = ?")
        params.append(dn)

    if body.phone is not None:
        phone = (body.phone or "").strip()
        if len(phone) > 32:
            _log.warning("update_user validation failed reason=phone_too_long target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "手机号过长", "data": {}},
            )
        updates.append("phone = ?")
        params.append(phone)

    if body.mentor_name is not None:
        requested_mentor_name = (body.mentor_name or "").strip()
        if len(requested_mentor_name) > 100:
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "带教人不能超过 100 字", "data": {}},
            )

    if body.role is not None:
        requested_role = normalize_app_role(body.role)
        if not requested_role:
            _log.warning("update_user validation failed reason=empty_role target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "员工角色不能为空", "data": {}},
            )
        updates.append("role = ?")
        params.append(requested_role)

    if body.store_id is not None:
        requested_store_id = (body.store_id or "").strip()
        if not requested_store_id:
            _log.warning("update_user validation failed reason=empty_store_id target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "门店不能为空", "data": {}},
            )
        if normalize_app_role(actor_role) != "admin":
            _log.warning("update_user validation failed reason=non_admin_change_store target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "仅管理员可调整门店", "data": {}},
            )
        updates.append("store_id = ?")
        params.append(requested_store_id)

    change_password = body.password is not None and (body.password or "").strip() != ""
    if change_password:
        new_pwd = (body.password or "").strip()
        if len(new_pwd) < 6:
            _log.warning("update_user validation failed reason=password_too_short target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "新密码至少 6 位", "data": {}},
            )
        updates.append("hashed_password = ?")
        params.append(get_password_hash(new_pwd))

    if not updates and requested_mentor_name is None:
        return {"code": 200, "message": "success", "data": {"user_id": target}}

    updates.append("updated_at = ?")
    params.append(_now_iso())

    with get_conn() as conn:
        scope = _store_scope_for_actor(conn, actor_id, actor_role)
        try:
            _assert_target_in_scope(conn, scope, target)
        except HTTPException as e:
            _log.warning("update_user validation failed reason=scope_error target=%s actor_id=%s detail=%s", target, current_user.get("user_id"), e.detail)
            return JSONResponse(
                status_code=int(e.status_code),
                content={
                    "code": int(e.status_code),
                    "message": str(e.detail),
                    "data": {},
                },
            )

        cur = conn.execute(
            """
            SELECT
                id,
                COALESCE(user_id, '') AS user_id,
                COALESCE(username, '') AS username,
                COALESCE(display_name, '') AS display_name,
                COALESCE(name, '') AS name,
                role,
                COALESCE(store_id, '') AS store_id,
                COALESCE(updated_at, '') AS updated_at
            FROM users
            WHERE CAST(id AS TEXT) = ?
            """,
            (target,),
        ).fetchone()
        if not cur:
            _log.warning("update_user validation failed reason=user_not_found target=%s actor_id=%s", target, current_user.get("user_id"))
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "用户不存在", "data": {}},
            )

        if requested_store_id is not None and not _store_exists(conn, requested_store_id):
            _log.warning(
                "update_user validation failed reason=store_not_found target=%s store_id=%s actor_id=%s",
                target,
                requested_store_id,
                current_user.get("user_id"),
            )
            return JSONResponse(
                status_code=400,
                content={"code": 400, "message": "归属门店不存在，请先在门店管理中创建", "data": {}},
            )

        if requested_role is not None:
            allowed = _assignable_role_keys(conn, actor_role)
            if requested_role not in allowed:
                _log.warning("update_user validation failed reason=role_not_assignable target=%s role=%s actor_id=%s", target, requested_role, current_user.get("user_id"))
                return JSONResponse(
                    status_code=400,
                    content={"code": 400, "message": "无权设置为该角色", "data": {}},
                )

        current_store_id = (cur["store_id"] or "").strip()
        current_role = normalize_app_role(cur["role"] or "")
        effective_store_id = requested_store_id if requested_store_id is not None else current_store_id
        effective_role = requested_role if requested_role is not None else current_role
        effective_display_name = (
            (body.display_name or "").strip()
            if body.display_name is not None
            else str(cur["display_name"] or cur["name"] or cur["username"] or "").strip()
        )

        if (requested_role is not None or requested_store_id is not None) and effective_role in _MANAGER_ROLE_KEYS:
            if not effective_store_id:
                _log.warning("update_user validation failed reason=manager_without_store target=%s actor_id=%s", target, current_user.get("user_id"))
                return JSONResponse(
                    status_code=400,
                    content={"code": 400, "message": "店长必须归属到有效门店", "data": {}},
                )
            conflict = _find_store_manager_conflict(
                conn,
                store_id=effective_store_id,
                exclude_user_id=target,
            )
            if conflict:
                _log.warning(
                    "update_user validation failed reason=store_manager_conflict target=%s store_id=%s manager_id=%s actor_id=%s",
                    target,
                    effective_store_id,
                    conflict["id"],
                    current_user.get("user_id"),
                )
                manager_name = (conflict["manager_name"] or conflict["username"] or "").strip()
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": 400,
                        "message": f"该门店已有店长「{manager_name or conflict['id']}」，每个门店仅可设置 1 位店长",
                        "data": {},
                    },
                )

        params.append(target)
        conn.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE CAST(id AS TEXT) = ?",
            tuple(params),
        )
        _upsert_personnel_employee_profile(
            conn,
            user_key=str(cur["user_id"] or target).strip() or target,
            display_name=effective_display_name,
            role=effective_role,
            store_id=effective_store_id,
            mentor_name=requested_mentor_name,
        )
        _sync_store_manager_cache_many(conn, [current_store_id, effective_store_id])
        target_name = (
            (cur["display_name"] or "").strip()
            or (cur["name"] or "").strip()
            or (cur["username"] or "").strip()
            or target
        )
        log_audit_from_user(
            conn,
            current_user,
            action="user_update",
            target_type="user",
            target_id=target,
            target_name=target_name,
            detail={"fields": [u.split(" = ")[0].strip() for u in updates]},
        )

    _log.info("update_user success user_id=%s actor_id=%s", target, current_user.get("user_id"))
    return {"code": 200, "message": "success", "data": {"user_id": target}}


def _delete_user_by_id(actor_id: str, user_id: str, current_user: dict | None = None) -> Any:
    """删除用户：供 DELETE 与 POST 共用（部分代理/网关不允许 DELETE，使用 POST 备用路径）。"""
    _log.info("_delete_user_by_id start target=%s actor_id=%s", user_id, actor_id)
    target = (user_id or "").strip()
    if not target:
        _log.warning("_delete_user_by_id validation failed reason=empty_user_id actor_id=%s", actor_id)
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "无效用户", "data": {}},
        )
    aid = (actor_id or "").strip()
    if aid == target:
        _log.warning("_delete_user_by_id validation failed reason=self_delete actor_id=%s", actor_id)
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "不能删除自己的账号", "data": {}},
        )
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, username, COALESCE(store_id, '') AS store_id FROM users WHERE CAST(id AS TEXT) = ?",
            (target,),
        ).fetchone()
        if not cur:
            _log.warning("_delete_user_by_id validation failed reason=user_not_found target=%s actor_id=%s", target, actor_id)
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "用户不存在", "data": {}},
            )
        username = cur["username"]
        conn.execute(
            "DELETE FROM users WHERE CAST(id AS TEXT) = ?",
            (target,),
        )
        _sync_store_manager_cache(conn, (cur["store_id"] or "").strip())
        if current_user:
            log_audit_from_user(conn, current_user, action="user_delete", target_type="user",
                                target_id=target, target_name=username)
    _log.info("_delete_user_by_id success target=%s username=%s actor_id=%s", target, username, actor_id)
    return {"code": 200, "message": "success", "data": {"user_id": target, "username": username}}


@router.delete("/users/{user_id}", dependencies=[Depends(_admin_only)])
def delete_user_ep(
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.debug("delete_user_ep user_id=%s actor_id=%s", user_id, current_user.get("user_id"))
    return _delete_user_by_id(str(current_user.get("user_id") or "").strip(), user_id, current_user)


@router.post("/users/{user_id}/delete", dependencies=[Depends(_admin_only)])
def delete_user_post_ep(
    user_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.debug("delete_user_post_ep user_id=%s actor_id=%s", user_id, current_user.get("user_id"))
    return _delete_user_by_id(str(current_user.get("user_id") or "").strip(), user_id, current_user)


@router.post("/user-delete", dependencies=[Depends(_admin_only)])
def delete_user_json_ep(
    body: UserDeleteByIdBody,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> Any:
    _log.debug("delete_user_json_ep target=%s actor_id=%s", body.user_id, current_user.get("user_id"))
    return _delete_user_by_id(str(current_user.get("user_id") or "").strip(), body.user_id, current_user)
