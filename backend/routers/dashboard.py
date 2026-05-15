from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import config as app_config
from api_response import make_request_id, success_response
from auth import get_current_user, normalize_app_role
from database import SessionLocal
from db_stage3 import get_conn, json_text
from dify_stage4b import run_dashboard_workflow
from routers.performance import (
    _list_leaderboard_stores,
    _store_average_summary,
    _store_display_name,
    _store_leaderboard,
    build_employee_performance_bundle,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_log = logging.getLogger("jewelry_qipei.router.dashboard")

_ROLE_POSITION_LABELS = {
    "admin": "管理员",
    "store_manager": "店长",
    "leader": "店长",
    "senior_consultant": "资深顾问",
    "trainee": "导购",
    "newbie": "导购",
}

_ABILITY_FOCUS_LABELS = {
    "product_knowledge": "产品知识",
    "compliance": "合规表达",
    "sales_communication": "销售沟通",
    "response": "应变回应",
}


class DashboardRiskRequest(BaseModel):
    store_id: str = Field("", description="门店 ID")
    employee_id: str = Field("", description="员工 ID")
    role_scope: str = Field("", description="岗位范围")
    period: str = Field("", description="统计周期（与 date_from/date_to 二选一）")
    date_from: str = Field("", description="分析起始日期 YYYY-MM-DD")
    date_to: str = Field("", description="分析结束日期 YYYY-MM-DD")


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _iso_text(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return _as_text(v)


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return default


def _employee_no(employee_id: Any) -> str:
    eid = _as_text(employee_id)
    if eid.isdigit():
        return f"EMP{int(eid):05d}"
    return eid


def _looks_placeholder_text(value: Any) -> bool:
    text = _as_text(value)
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"?", "??", "-", "--", "unknown", "null", "none", "n/a", "na"}:
        return True
    if "?" in text or "？" in text:
        stripped = re.sub(r"[?？\s]+", "", text)
        if not stripped:
            return True
    return False


def _role_position_label(role: Any) -> str:
    normalized = normalize_app_role(_as_text(role))
    return _ROLE_POSITION_LABELS.get(normalized, _as_text(role))


def _normalize_employee_name(value: Any, *, fallback: str) -> str:
    text = _as_text(value)
    if _looks_placeholder_text(text):
        return fallback
    return text or fallback


def _normalize_position_label(value: Any, *, fallback: str = "") -> str:
    text = _as_text(value)
    if _looks_placeholder_text(text):
        return fallback
    mapped = _role_position_label(text)
    return mapped or fallback


def _resolve_user_store_id(conn, raw_user_id: str) -> str:
    actor_id = _as_text(raw_user_id)
    if not actor_id:
        return ""
    row = conn.execute(
        """
        SELECT COALESCE(store_id, '') AS store_id
        FROM users
        WHERE CAST(id AS TEXT) = ? OR COALESCE(user_id, '') = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (actor_id, actor_id),
    ).fetchone()
    return _as_text(row["store_id"]) if row else ""


def _restrict_dashboard_scope(
    conn,
    current_user: dict[str, Any],
    requested_store_id: str,
) -> tuple[str, str]:
    role = normalize_app_role(_as_text(current_user.get("role")))
    if role not in {"admin", "store_manager"}:
        raise HTTPException(status_code=403, detail="权限不足：仅管理员或店长可访问风险看板")
    if role == "admin":
        return role, _as_text(requested_store_id)
    actor_store_id = _resolve_user_store_id(conn, _as_text(current_user.get("user_id")))
    if not actor_store_id:
        raise HTTPException(status_code=403, detail="店长未绑定门店，无法访问风险看板")
    if _as_text(requested_store_id) and _as_text(requested_store_id) != actor_store_id:
        raise HTTPException(status_code=403, detail="店长仅可查看本门店风险看板")
    return role, actor_store_id


def _safe_json_loads(v: Any) -> dict[str, Any]:
    text = _as_text(v)
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _parse_ymd(s: Any) -> str | None:
    t = _as_text(s)
    if len(t) < 10:
        return None
    try:
        datetime.strptime(t[:10], "%Y-%m-%d")
        return t[:10]
    except Exception:
        return None


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_english_text(value: Any) -> bool:
    text = _as_text(value)
    if not text or _has_cjk(text):
        return False
    return bool(re.search(r"[A-Za-z]", text))


def _normalize_ascii_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _as_text(value).lower()).strip()


def _normalize_risk_level_key(value: Any) -> str:
    text = _as_text(value)
    key = _normalize_ascii_key(text)
    if "high" in key or "高" in text:
        return "high"
    if "medium" in key or "中" in text:
        return "medium"
    if "low" in key or "低" in text:
        return "low"
    if "unknown" in key or "未知" in text:
        return "unknown"
    return key or text or "medium"


def _risk_level_cn(value: Any) -> str:
    return {
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
        "unknown": "待判断",
    }.get(_normalize_risk_level_key(value), "中风险")


def _normalize_coaching_focus(value: Any) -> str:
    text = _as_text(value)
    if not text:
        return ""
    if not _looks_english_text(text):
        return text
    key = _normalize_ascii_key(text)
    literal_map = {
        "product knowledge": "产品知识",
        "closing skill": "成交收口",
        "sales communication": "销售沟通",
        "sales expression": "销售沟通",
        "objection handling": "异议处理",
        "response handling": "应变回应",
        "data completion": "数据补齐",
        "missing data": "数据补齐",
        "input shortage": "数据补齐",
        "compliance": "合规表达",
    }
    if key in literal_map:
        return literal_map[key]
    if "product" in key and "knowledge" in key:
        return "产品知识"
    if "closing" in key:
        return "成交收口"
    if "objection" in key:
        return "异议处理"
    if "sales" in key and ("communication" in key or "expression" in key):
        return "销售沟通"
    if "response" in key:
        return "应变回应"
    if "compliance" in key:
        return "合规表达"
    if "data" in key or "input" in key:
        return "数据补齐"
    return "重点能力提升"


def _default_action_window(risk_level: str) -> str:
    level = _normalize_risk_level_key(risk_level)
    if level == "high":
        return "3天内"
    if level == "low":
        return "7天内"
    return "本周内"


def _normalize_action_window(value: Any, *, risk_level: str) -> str:
    text = _as_text(value)
    if not text:
        return _default_action_window(risk_level)
    if not _looks_english_text(text):
        return text
    key = _normalize_ascii_key(text)
    hour_match = re.search(r"(\d+)\s+hour", key)
    if hour_match:
        return f"{hour_match.group(1)}小时内"
    day_match = re.search(r"(\d+)\s+day", key)
    if day_match:
        return f"{day_match.group(1)}天内"
    week_match = re.search(r"(\d+)\s+week", key)
    if week_match:
        return f"{week_match.group(1)}周内"
    if "today" in key or "same day" in key:
        return "当天"
    if "this week" in key:
        return "本周内"
    if "immediately" in key or "urgent" in key or "asap" in key:
        return "尽快"
    return _default_action_window(risk_level)


def _default_risk_reason(*, risk_level: str, coaching_focus: str) -> str:
    focus = coaching_focus or "重点能力"
    if _normalize_risk_level_key(risk_level) == "unknown":
        return "当前数据不足，建议先补齐看板分析所需信息。"
    return f"近期表现存在波动，辅导重点集中在{focus}。"


def _normalize_risk_reason(value: Any, *, risk_level: str, coaching_focus: str) -> str:
    text = _as_text(value)
    if not text:
        return _default_risk_reason(risk_level=risk_level, coaching_focus=coaching_focus)
    if not _looks_english_text(text):
        return text
    key = _normalize_ascii_key(text)
    focus = coaching_focus or "重点能力"
    if "fluctuat" in key or "volatile" in key:
        return f"近期表现波动较大，辅导重点集中在{focus}。"
    if "closing" in key and ("drop" in key or "declin" in key):
        return f"近期成交表现下滑，辅导重点集中在{focus}。"
    if "lack" in key and "data" in key:
        return "当前数据不足，建议先补齐看板分析所需信息。"
    return _default_risk_reason(risk_level=risk_level, coaching_focus=focus)


def _default_follow_up_action(*, risk_level: str, coaching_focus: str) -> str:
    focus = coaching_focus or "重点能力"
    level = _normalize_risk_level_key(risk_level)
    if level == "high":
        return f"尽快围绕{focus}安排复盘与补训，并跟进后续实操表现。"
    if level == "low":
        return f"保持对{focus}的日常跟进，观察后续表现。"
    return f"围绕{focus}安排针对性复盘，并在下一轮训练中复查。"


def _normalize_follow_up_action(value: Any, *, risk_level: str, coaching_focus: str) -> str:
    text = _as_text(value)
    if not text:
        return _default_follow_up_action(risk_level=risk_level, coaching_focus=coaching_focus)
    if not _looks_english_text(text):
        return text
    key = _normalize_ascii_key(text)
    if "arrange" in key and "review" in key:
        return "安排一对一复盘"
    if "replay" in key and "conversation" in key:
        return "回看近期对话并复盘"
    if "follow up" in key and "immediately" in key:
        return "立即跟进并安排复盘"
    return _default_follow_up_action(risk_level=risk_level, coaching_focus=coaching_focus)


def _default_next_action(*, coaching_focus: str) -> str:
    focus = coaching_focus or "专项能力"
    if focus == "数据补齐":
        return "补齐关键数据"
    return f"安排{focus}专项复盘"


def _normalize_next_action(value: Any, *, coaching_focus: str) -> str:
    text = _as_text(value)
    if not text:
        return _default_next_action(coaching_focus=coaching_focus)
    if not _looks_english_text(text):
        return text
    key = _normalize_ascii_key(text)
    if "manager" in key and "shadow" in key:
        return "安排店长陪访"
    if "data" in key:
        return "补齐关键数据"
    return _default_next_action(coaching_focus=coaching_focus)


def _default_panel_summary(*, risk_level: str, coaching_focus: str) -> str:
    level = _normalize_risk_level_key(risk_level)
    focus = coaching_focus or "重点能力"
    if level == "high":
        return f"高风险，需尽快围绕{focus}启动跟进。"
    if level == "low":
        return f"低风险，继续跟进{focus}表现。"
    if level == "unknown":
        return "当前数据待补充，暂不输出明确风险判断。"
    return f"中风险，建议围绕{focus}安排复盘。"


def _normalize_panel_summary(value: Any, *, risk_level: str, coaching_focus: str) -> str:
    text = _as_text(value)
    if not text:
        return _default_panel_summary(risk_level=risk_level, coaching_focus=coaching_focus)
    if not _looks_english_text(text):
        return text
    return _default_panel_summary(risk_level=risk_level, coaching_focus=coaching_focus)


def _normalize_dashboard_tags(values: Any, *, risk_level: str, coaching_focus: str) -> list[str]:
    tags = list(values or [])
    normalized: list[str] = []
    focus = coaching_focus or ""
    tag_map = {
        "high risk": "高风险",
        "medium risk": "中风险",
        "low risk": "低风险",
        "product knowledge": "产品知识",
        "closing skill": "成交收口",
        "sales communication": "销售沟通",
        "sales expression": "销售沟通",
        "objection handling": "异议处理",
        "dashboard recommendation": "看板建议",
        "downward trend": "趋势下降",
        "trend down": "趋势下降",
        "recent risk record": "近期有风险记录",
        "missing data": "待补数据",
        "input shortage": "输入不足",
    }
    for tag in tags:
        text = _as_text(tag)
        if not text:
            continue
        if not _looks_english_text(text):
            normalized.append(text)
            continue
        key = _normalize_ascii_key(text)
        mapped = tag_map.get(key)
        if not mapped and ("product" in key and "knowledge" in key):
            mapped = "产品知识"
        elif not mapped and "closing" in key:
            mapped = "成交收口"
        elif not mapped and "objection" in key:
            mapped = "异议处理"
        elif not mapped and "sales" in key:
            mapped = "销售沟通"
        if mapped:
            normalized.append(mapped)
    level_tag = _risk_level_cn(risk_level)
    if level_tag not in normalized:
        normalized.insert(0, level_tag)
    if focus and focus not in normalized and focus != "重点能力提升":
        normalized.append(focus)
    deduped: list[str] = []
    for tag in normalized:
        if tag and tag not in deduped:
            deduped.append(tag)
    return deduped[:4]


def _normalize_dashboard_risk_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item or {})
    employee_id = _as_text(out.get("employee_id"))
    out["employee_name"] = _normalize_employee_name(
        out.get("employee_name"),
        fallback=f"员工{employee_id}" if employee_id else "待确认员工",
    )
    out["position"] = _normalize_position_label(
        out.get("position"),
        fallback="导购",
    )
    risk_level = _normalize_risk_level_key(out.get("risk_level"))
    coaching_focus = _normalize_coaching_focus(out.get("coaching_focus"))
    out["risk_level"] = risk_level or "medium"
    out["coaching_focus"] = coaching_focus or "重点能力提升"
    out["risk_reason"] = _normalize_risk_reason(
        out.get("risk_reason"),
        risk_level=out["risk_level"],
        coaching_focus=out["coaching_focus"],
    )
    out["follow_up_action"] = _normalize_follow_up_action(
        out.get("follow_up_action") or out.get("followup_advice"),
        risk_level=out["risk_level"],
        coaching_focus=out["coaching_focus"],
    )
    out["next_action"] = _normalize_next_action(
        out.get("next_action"),
        coaching_focus=out["coaching_focus"],
    )
    out["action_window"] = _normalize_action_window(
        out.get("action_window"),
        risk_level=out["risk_level"],
    )
    out["dashboard_tags"] = _normalize_dashboard_tags(
        out.get("dashboard_tags"),
        risk_level=out["risk_level"],
        coaching_focus=out["coaching_focus"],
    )
    out["panel_summary"] = _normalize_panel_summary(
        out.get("panel_summary"),
        risk_level=out["risk_level"],
        coaching_focus=out["coaching_focus"],
    )
    return out


def _build_manager_action_items(risk_list: list[dict[str, Any]]) -> list[str]:
    manager_actions: list[str] = []
    for item in risk_list[:6]:
        if item.get("risk_level") == "high":
            manager_actions.append(
                f"优先跟进 {item['employee_name']}：{_as_text(item.get('follow_up_action'))}"
            )
    if not manager_actions and risk_list:
        manager_actions.append("本周保持门店例行复盘，持续观察中风险员工变化。")
    manager_actions.append("看板统计已基于数据库聚合，建议每周固定复盘一次。")
    return manager_actions[:4]


def _normalize_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized = dict(payload)
    raw_risk_list = payload.get("risk_list") if isinstance(payload.get("risk_list"), list) else []
    risk_list: list[dict[str, Any]] = []
    seen_employee_ids: set[str] = set()
    for item in raw_risk_list:
        if not isinstance(item, dict):
            continue
        normalized_item = _normalize_dashboard_risk_item(item)
        employee_id = _as_text(normalized_item.get("employee_id"))
        if employee_id and employee_id in seen_employee_ids:
            continue
        if employee_id:
            seen_employee_ids.add(employee_id)
        risk_list.append(normalized_item)
    if risk_list:
        normalized["risk_list"] = risk_list
        normalized["overview"] = {
            "total_people": len(risk_list),
            "high_risk_count": len([item for item in risk_list if item.get("risk_level") == "high"]),
            "medium_risk_count": len([item for item in risk_list if item.get("risk_level") == "medium"]),
            "low_risk_count": len([item for item in risk_list if item.get("risk_level") == "low"]),
        }
        normalized["manager_action_items"] = _build_manager_action_items(risk_list)
    if _as_text(normalized.get("core_weak_dimension")):
        normalized["core_weak_dimension"] = _normalize_coaching_focus(normalized.get("core_weak_dimension"))
    return normalized


def _parse_date_range_bounds(date_from: str, date_to: str) -> tuple[datetime, datetime, datetime, datetime] | None:
    """返回 (range_start UTC, range_end_exclusive, prev_start, prev_end_exclusive)。"""
    df_s = _parse_ymd(date_from)
    dt_s = _parse_ymd(date_to)
    if not df_s or not dt_s:
        return None
    try:
        d1 = date.fromisoformat(df_s)
        d2 = date.fromisoformat(dt_s)
    except Exception:
        return None
    if d1 > d2:
        return None
    span_days = (d2 - d1).days + 1
    if span_days > 731:
        return None
    start_excl = datetime(d1.year, d1.month, d1.day, tzinfo=timezone.utc)
    end_excl = datetime(d2.year, d2.month, d2.day, tzinfo=timezone.utc) + timedelta(days=1)
    span = end_excl - start_excl
    prev_start = start_excl - span
    prev_end_excl = start_excl
    return (start_excl, end_excl, prev_start, prev_end_excl)


def _period_days(period: str) -> int:
    p = _as_text(period).lower()
    if p in {"365d", "1y", "12m"}:
        return 365
    if p in {"180d", "6m"}:
        return 180
    if p in {"90d", "3m"}:
        return 90
    if p in {"30d", "1m"}:
        return 30
    if p in {"14d", "2w"}:
        return 14
    return 7


def _level_by_score(score: float) -> str:
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 60:
        return "合格"
    return "待提升"


def _load_store_employees(conn, store_id: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    aliases: dict[str, str] = {}
    sid = _as_text(store_id)

    try:
        if sid:
            rows = conn.execute(
                """
                SELECT
                    CAST(id AS TEXT) AS employee_id,
                    COALESCE(NULLIF(TRIM(user_id), ''), CAST(id AS TEXT)) AS user_key,
                    display_name,
                    role
                FROM users
                WHERE store_id = ?
                """,
                (sid,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    CAST(id AS TEXT) AS employee_id,
                    COALESCE(NULLIF(TRIM(user_id), ''), CAST(id AS TEXT)) AS user_key,
                    display_name,
                    role
                FROM users
                """
            ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        eid = _as_text(r["employee_id"])
        if not eid:
            continue
        role_label = _role_position_label(r["role"]) or "导购"
        out[eid] = {
            "employee_name": _normalize_employee_name(r["display_name"], fallback=f"员工{eid}"),
            "position": role_label,
            "employee_no": _employee_no(eid),
        }
        aliases[eid] = eid
        user_key = _as_text(r["user_key"])
        if user_key:
            aliases[user_key] = eid

    try:
        if sid:
            prows = conn.execute(
                """
                SELECT employee_id, user_id, employee_name, position
                FROM employee_profiles
                WHERE store_id = ?
                """,
                (sid,),
            ).fetchall()
        else:
            prows = conn.execute(
                "SELECT employee_id, user_id, employee_name, position FROM employee_profiles"
            ).fetchall()
    except Exception:
        prows = []
    for r in prows:
        profile_employee_id = _as_text(r["employee_id"])
        profile_user_id = _as_text(r["user_id"])
        eid = aliases.get(profile_employee_id) or aliases.get(profile_user_id)
        if not eid:
            continue
        row = out.get(eid)
        if row is None:
            continue
        row["employee_name"] = _normalize_employee_name(
            r["employee_name"],
            fallback=_as_text(row.get("employee_name")) or f"员工{eid}",
        )
        row["position"] = _normalize_position_label(
            r["position"],
            fallback=_as_text(row.get("position")) or "导购",
        )
        if not _as_text(row.get("employee_no")):
            row["employee_no"] = _employee_no(eid)

    return out


def _collect_employee_metrics(
    conn,
    *,
    employee_id: str,
    period_days: int,
    range_bounds: tuple[datetime, datetime, datetime, datetime] | None = None,
) -> dict[str, Any]:
    if range_bounds:
        start_excl, end_excl, prev_start, prev_end_excl = range_bounds
        recent_since = start_excl.isoformat()
        recent_until = end_excl.isoformat()
        prev_since = prev_start.isoformat()
        prev_until = prev_end_excl.isoformat()
        bounded = True
    else:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(1, period_days))
        since_prev = now - timedelta(days=max(1, period_days) * 2)
        recent_since = since.isoformat()
        recent_until = None
        prev_since = since_prev.isoformat()
        prev_until = since.isoformat()
        bounded = False

    learning_completed = 0
    learning_total = 0
    latest_learning_score: float | None = None
    try:
        if bounded:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM learning_eval_records
                WHERE employee_id = ? AND created_at >= ? AND created_at < ?
                """,
                (employee_id, recent_since, recent_until),
            ).fetchone()
            row_plan = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM growth_plan_records
                WHERE employee_id = ? AND created_at >= ? AND created_at < ?
                """,
                (employee_id, recent_since, recent_until),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM learning_eval_records
                WHERE employee_id = ? AND created_at >= ?
                """,
                (employee_id, recent_since),
            ).fetchone()
            row_plan = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM growth_plan_records
                WHERE employee_id = ? AND created_at >= ?
                """,
                (employee_id, recent_since),
            ).fetchone()
        learning_completed = int(row["c"] or 0) if row else 0
        learning_total = int(row_plan["c"] or 0) if row_plan else 0
        if learning_total < learning_completed:
            learning_total = learning_completed
        row_latest_learning = conn.execute(
            """
            SELECT score
            FROM learning_eval_records
            WHERE employee_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (employee_id,),
        ).fetchone()
        if row_latest_learning and row_latest_learning["score"] is not None:
            latest_learning_score = _to_float(row_latest_learning["score"], 0.0)
    except Exception:
        pass

    training_completion_rate = (
        round(learning_completed * 100.0 / learning_total, 2) if learning_total > 0 else -1.0
    )

    recent_scores: list[float] = []
    prev_scores: list[float] = []
    latest_practice_feedback = ""
    weak_dimension = ""
    latest_practice_level = ""
    try:
        if bounded:
            rows_recent = conn.execute(
                """
                SELECT overall_score, coach_summary, payload_json
                FROM practice_eval_records
                WHERE employee_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY id DESC
                """,
                (employee_id, recent_since, recent_until),
            ).fetchall()
            rows_prev = conn.execute(
                """
                SELECT overall_score
                FROM practice_eval_records
                WHERE employee_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY id DESC
                """,
                (employee_id, prev_since, prev_until),
            ).fetchall()
        else:
            rows_recent = conn.execute(
                """
                SELECT overall_score, coach_summary, payload_json
                FROM practice_eval_records
                WHERE employee_id = ? AND created_at >= ?
                ORDER BY id DESC
                """,
                (employee_id, recent_since),
            ).fetchall()
            rows_prev = conn.execute(
                """
                SELECT overall_score
                FROM practice_eval_records
                WHERE employee_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY id DESC
                """,
                (employee_id, prev_since, prev_until),
            ).fetchall()
        for r in rows_recent:
            recent_scores.append(_to_float(r["overall_score"], 0.0))
            if not latest_practice_feedback:
                latest_practice_feedback = _as_text(r["coach_summary"])
            if not weak_dimension:
                payload = _safe_json_loads(r["payload_json"])
                weak_dimension = _as_text(payload.get("weak_dimension"))
        for r in rows_prev:
            prev_scores.append(_to_float(r["overall_score"], 0.0))
    except Exception:
        pass

    recent_practice_avg_score = round(sum(recent_scores) / len(recent_scores), 2) if recent_scores else -1.0
    prev_avg = round(sum(prev_scores) / len(prev_scores), 2) if prev_scores else None
    if prev_avg is None or recent_practice_avg_score < 0:
        recent_practice_trend = "flat"
    else:
        delta = recent_practice_avg_score - prev_avg
        if delta > 3:
            recent_practice_trend = "up"
        elif delta < -3:
            recent_practice_trend = "down"
        else:
            recent_practice_trend = "flat"

    high_risk_count = len([x for x in recent_scores if x < 70])
    if recent_practice_avg_score >= 0:
        compliance_pass_rate = round(
            len([x for x in recent_scores if x >= 75]) * 100.0 / max(1, len(recent_scores)),
            2,
        )
        overall_score = recent_practice_avg_score
    else:
        compliance_pass_rate = -1.0
        overall_score = -1.0
    if overall_score >= 0:
        latest_practice_level = _level_by_score(overall_score)

    ability_snapshot: dict[str, Any] = {}
    try:
        row_ability = conn.execute(
            """
            SELECT ability_snapshot_json
            FROM ability_update_records
            WHERE employee_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (employee_id,),
        ).fetchone()
        if row_ability:
            ability_snapshot = _safe_json_loads(row_ability["ability_snapshot_json"])
    except Exception:
        pass

    product_knowledge_score = _to_float(ability_snapshot.get("product_knowledge"), -1.0)
    compliance_score = _to_float(ability_snapshot.get("closing_skill"), -1.0)
    sales_communication_score = _to_float(ability_snapshot.get("sales_expression"), -1.0)
    response_score = _to_float(ability_snapshot.get("objection_handling"), -1.0)

    recent_assistant_tags: list[str] = []
    try:
        if bounded:
            rows_assistant = conn.execute(
                """
                SELECT analysis_json
                FROM assistant_records
                WHERE employee_id = ? AND created_at >= ? AND created_at < ? AND action = 'analyze'
                ORDER BY id DESC
                LIMIT 20
                """,
                (employee_id, recent_since, recent_until),
            ).fetchall()
        else:
            rows_assistant = conn.execute(
                """
                SELECT analysis_json
                FROM assistant_records
                WHERE employee_id = ? AND created_at >= ? AND action = 'analyze'
                ORDER BY id DESC
                LIMIT 20
                """,
                (employee_id, recent_since),
            ).fetchall()
        for r in rows_assistant:
            obj = _safe_json_loads(r["analysis_json"])
            t = _as_text(obj.get("knowledge_tag")) or _as_text(obj.get("question_type"))
            if t:
                recent_assistant_tags.append(t)
    except Exception:
        pass
    recent_assistant_tags = recent_assistant_tags[:5]

    latest_learning_status = ""
    if latest_learning_score is not None:
        latest_learning_status = _level_by_score(latest_learning_score)

    if not weak_dimension:
        candidates = [
            ("产品知识", product_knowledge_score),
            ("销售沟通", sales_communication_score),
            ("应变回应", response_score),
            ("成交收口", compliance_score),
        ]
        candidates = [x for x in candidates if x[1] >= 0]
        if candidates:
            weak_dimension = min(candidates, key=lambda x: x[1])[0]

    return {
        "training_completion_rate": training_completion_rate,
        "compliance_pass_rate": compliance_pass_rate,
        "overall_score": overall_score,
        "product_knowledge_score": product_knowledge_score,
        "compliance_score": compliance_score,
        "sales_communication_score": sales_communication_score,
        "response_score": response_score,
        "recent_learning_completed": learning_completed,
        "recent_learning_total": learning_total,
        "recent_practice_avg_score": recent_practice_avg_score,
        "recent_practice_trend": recent_practice_trend,
        "recent_high_risk_count": high_risk_count,
        "recent_assistant_question_tags": "、".join(recent_assistant_tags),
        "core_weak_dimension": weak_dimension,
        "manager_focus_hint": "",
        "latest_learning_status": latest_learning_status,
        "latest_practice_level": latest_practice_level,
        "latest_practice_feedback": latest_practice_feedback,
    }


def _local_dashboard_fallback(metrics: dict[str, Any]) -> dict[str, Any]:
    score = _to_float(metrics.get("overall_score"), -1.0)
    trend = _as_text(metrics.get("recent_practice_trend"))
    high_risk_count = _to_int(metrics.get("recent_high_risk_count"), 0)
    weak = _as_text(metrics.get("core_weak_dimension")) or "综合稳定性"

    risk_score = 30
    if score >= 0:
        if score < 60:
            risk_score += 45
        elif score < 70:
            risk_score += 30
        elif score < 80:
            risk_score += 18
        else:
            risk_score += 8
    if trend == "down":
        risk_score += 15
    if high_risk_count > 0:
        risk_score += min(20, high_risk_count * 6)
    risk_score = max(0, min(100, risk_score))

    if risk_score >= 70:
        risk_level = "high"
    elif risk_score >= 45:
        risk_level = "medium"
    else:
        risk_level = "low"

    risk_reason = f"近期综合表现{_level_by_score(score) if score >= 0 else '数据不足'}，薄弱项集中在{weak}。"
    followup = "建议本周安排1次针对性复盘 + 1次同场景复练，并观察下一轮变化。"
    next_action = "安排复盘并跟进下轮评估"
    _risk_cn = {"high": "高", "medium": "中", "low": "低"}.get(risk_level, risk_level)
    panel_summary = f"{_risk_cn}风险，关注{weak}并持续跟进。"

    return {
        "workflow_status": "fallback",
        "risk_level": risk_level,
        "risk_score": risk_score,
        "risk_reason": risk_reason,
        "followup_advice": followup,
        "next_action": next_action,
        "action_window": "7天内",
        "coaching_focus": weak,
        "manager_attention_needed": risk_level in {"high", "medium"},
        "dashboard_tags": [f"{_risk_cn}风险", weak, "本地规则"],
        "observation_note": "当前为规则兜底结果。",
        "panel_summary": panel_summary,
        "safe_output_json": {},
    }


@router.post("/risk")
def dashboard_risk(
    body: DashboardRiskRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    requested_store_id = (body.store_id or "").strip()
    _log.info(
        "risk start user_id=%s store_id=%s period=%s",
        str(current_user.get("user_id") or ""),
        requested_store_id or "(all)",
        (body.period or "").strip() or "",
    )
    range_bounds = _parse_date_range_bounds(body.date_from, body.date_to)
    if range_bounds:
        df_s = _parse_ymd(body.date_from)
        dt_s = _parse_ymd(body.date_to)
        period = f"{df_s}~{dt_s}"
        period_days = max(1, (range_bounds[1] - range_bounds[0]).days)
    else:
        period = (body.period or "").strip() or "7d"
        period_days = _period_days(period)
        range_bounds = None
    viewer_role = normalize_app_role(str(current_user.get("role") or ""))
    viewer_user_id = str(current_user.get("user_id") or "")
    selected_employee_id = (body.employee_id or "").strip()

    risk_list: list[dict[str, Any]] = []
    dify_success_count = 0
    dify_failure_count = 0
    with get_conn() as conn:
        viewer_role, store_id = _restrict_dashboard_scope(conn, current_user, requested_store_id)
        employees = _load_store_employees(conn, store_id)
        # fallback to self if the store has no seed data yet
        if not employees and viewer_user_id:
            employees[viewer_user_id] = {
                "employee_name": "",
                "position": "",
                "employee_no": _employee_no(viewer_user_id),
            }
        if selected_employee_id:
            if selected_employee_id in employees:
                employees = {selected_employee_id: employees[selected_employee_id]}
            else:
                employees = {}

        employee_jobs: list[dict[str, Any]] = []
        for eid, info in employees.items():
            metrics = _collect_employee_metrics(
                conn,
                employee_id=eid,
                period_days=period_days,
                range_bounds=range_bounds,
            )
            employee_jobs.append({"eid": eid, "info": info, "metrics": metrics})

        # End read transaction before slow outbound workflow calls.
        conn.commit()

        def _eval_employee(job: dict[str, Any]) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
            eid = _as_text(job.get("eid"))
            info = job.get("info") if isinstance(job.get("info"), dict) else {}
            metrics = job.get("metrics") if isinstance(job.get("metrics"), dict) else {}
            performance_linkage: dict[str, Any] = {}
            try:
                with SessionLocal() as db:
                    perf_bundle = build_employee_performance_bundle(db, eid)
                    performance_linkage = (
                        perf_bundle.get("performance_linkage")
                        if isinstance(perf_bundle.get("performance_linkage"), dict)
                        else {}
                    )
            except Exception:
                performance_linkage = {}
            primary_gap = performance_linkage.get("primary_gap") if isinstance(performance_linkage.get("primary_gap"), dict) else {}
            current_metrics = performance_linkage.get("current_metrics") if isinstance(performance_linkage.get("current_metrics"), dict) else {}
            target_metrics = performance_linkage.get("target_metrics") if isinstance(performance_linkage.get("target_metrics"), dict) else {}
            workflow_inputs = {
                "store_id": store_id,
                "user_id": eid,
                "employee_name": _as_text(info.get("employee_name")),
                "job_title": _as_text(info.get("position")),
                **metrics,
                "current_sales_amount": _to_float(((current_metrics.get("sales_amount") or {}).get("value")), 0.0),
                "target_sales_amount": _to_float(((target_metrics.get("sales_amount") or {}).get("value")), 0.0),
                "current_avg_ticket": _to_float(((current_metrics.get("avg_ticket") or {}).get("value")), 0.0),
                "target_avg_ticket": _to_float(((target_metrics.get("avg_ticket") or {}).get("value")), 0.0),
                "current_conversion_rate": _to_float(((current_metrics.get("conversion_rate") or {}).get("value")), 0.0),
                "target_conversion_rate": _to_float(((target_metrics.get("conversion_rate") or {}).get("value")), 0.0),
                "current_attach_rate": _to_float(((current_metrics.get("attach_rate") or {}).get("value")), 0.0),
                "target_attach_rate": _to_float(((target_metrics.get("attach_rate") or {}).get("value")), 0.0),
                "current_member_conversion_rate": _to_float(((current_metrics.get("member_conversion_rate") or {}).get("value")), 0.0),
                "target_member_conversion_rate": _to_float(((target_metrics.get("member_conversion_rate") or {}).get("value")), 0.0),
                "performance_gap_summary": _as_text(performance_linkage.get("gap_metrics_text")),
                "performance_summary": _as_text(performance_linkage.get("performance_summary")),
                "primary_gap_metric": _as_text(primary_gap.get("metric_label")),
                "primary_gap_action": _as_text(primary_gap.get("action_title")),
            }
            call = run_dashboard_workflow(user_id=viewer_user_id or eid, inputs=workflow_inputs)
            use_dify = bool(call.get("ok"))
            if use_dify:
                wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
                if not _as_text(wf_data.get("risk_level")):
                    use_dify = False
                    call = {
                        "ok": False,
                        "reason": "empty_workflow_output",
                        "error": "risk_level_empty",
                        "raw": call.get("raw") if isinstance(call, dict) else {},
                    }
                else:
                    out = dict(wf_data)
            if not use_dify:
                out = _local_dashboard_fallback(metrics)

            risk_item = {
                "employee_id": eid,
                "employee_name": _as_text(info.get("employee_name")) or f"员工{eid}",
                "employee_no": _as_text(info.get("employee_no")) or _employee_no(eid),
                "position": _as_text(info.get("position")) or "珠宝顾问",
                "risk_level": _as_text(out.get("risk_level")) or "medium",
                "risk_reason": _as_text(out.get("risk_reason")) or "暂无风险说明",
                "follow_up_action": _as_text(out.get("followup_advice"))
                or _as_text(out.get("next_action"))
                or "建议安排复盘跟进",
                "next_action": _as_text(out.get("next_action")),
                "action_window": _as_text(out.get("action_window")),
                "coaching_focus": _as_text(out.get("coaching_focus")),
                "risk_score": _to_int(out.get("risk_score"), 0),
                "manager_attention_needed": bool(out.get("manager_attention_needed")),
                "dashboard_tags": list(out.get("dashboard_tags") or []),
                "panel_summary": _as_text(out.get("panel_summary")),
            }
            return _normalize_dashboard_risk_item(risk_item), use_dify, None

        worker_count = max(1, min(len(employee_jobs) or 1, app_config.DIFY_WORKFLOW_MAX_CONCURRENT))
        if len(employee_jobs) <= 1:
            eval_results = [_eval_employee(job) for job in employee_jobs]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                eval_results = list(pool.map(_eval_employee, employee_jobs))

        for risk_item, use_dify, fail_call in eval_results:
            if fail_call is not None:
                _log.warning("risk dify failed reason=%s", fail_call.get("reason") if isinstance(fail_call, dict) else "")
                dify_failure_count += 1
            if use_dify:
                dify_success_count += 1
            risk_list.append(risk_item)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        risk_list.sort(
            key=lambda x: (
                severity_order.get(_as_text(x.get("risk_level")), 9),
                -_to_int(x.get("risk_score"), 0),
            )
        )

        high_count = len([x for x in risk_list if x.get("risk_level") == "high"])
        medium_count = len([x for x in risk_list if x.get("risk_level") == "medium"])
        low_count = len([x for x in risk_list if x.get("risk_level") == "low"])

        # Calculate aggregate metrics for snapshot
        valid_scores = [_to_int(x.get("risk_score"), 0) for x in risk_list if x.get("risk_score", 0) > 0]
        overall_avg = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None

        # Find most common weak dimension (coaching_focus)
        weak_dims = [_as_text(x.get("coaching_focus")) for x in risk_list if x.get("coaching_focus")]
        core_weak = max(set(weak_dims), key=weak_dims.count) if weak_dims else ""

        manager_actions = _build_manager_action_items(risk_list)

        data = {
            "risk_id": make_request_id("dr"),
            "store_id": store_id,
            "period": period,
            "selected_employee_id": selected_employee_id,
            "overview": {
                "total_people": len(risk_list),
                "high_risk_count": high_count,
                "medium_risk_count": medium_count,
                "low_risk_count": low_count,
            },
            "risk_list": risk_list,
            "manager_action_items": manager_actions,
            "viewer_role": viewer_role,
            "workflow_stats": {
                "dify_success_count": dify_success_count,
                "fallback_count": max(0, len(risk_list) - dify_success_count),
                "dify_failure_count": dify_failure_count,
            },
        }

        row = conn.execute(
            """
            INSERT INTO dashboard_snapshots (
                snapshot_id, store_id, user_id, overall_score, recent_high_risk_count,
                core_weak_dimension, dashboard_result_json, source_workflow,
                role_scope, period, viewer_role, payload_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["risk_id"],
                store_id,
                viewer_user_id,
                overall_avg,
                high_count,
                core_weak,
                json_text(data),
                "dashboard",
                (body.role_scope or "").strip(),
                period,
                viewer_role,
                json_text(data),
                viewer_user_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        data["db_record_id"] = int(row.lastrowid or 0)

    use_mock = dify_success_count == 0
    if use_mock:
        _log.info("risk using mock user_id=%s", str(current_user.get("user_id") or ""))
    _log.info("risk success user_id=%s total=%d high=%d medium=%d low=%d",
              str(current_user.get("user_id") or ""),
              len(risk_list), high_count, medium_count, low_count)
    return success_response(
        data,
        workflow_code="dashboard" if not use_mock else "dashboard_mock",
        mock=use_mock,
    )


@router.get("/snapshots")
def dashboard_snapshots_list(
    store_id: str = "",
    limit: int = 20,
    offset: int = 0,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)] = None,
):
    """获取门店的历史看板快照列表"""
    _log.info("snapshots_list start user_id=%s store_id=%s limit=%d offset=%d",
              str((current_user or {}).get("user_id") or ""),
              store_id, limit, offset)
    viewer_role = normalize_app_role(str(current_user.get("role") or "")) if current_user else ""

    with get_conn() as conn:
        viewer_role, scoped_store_id = _restrict_dashboard_scope(conn, current_user or {}, store_id)
        store_id = scoped_store_id if viewer_role == "store_manager" else (scoped_store_id or store_id)
        if store_id:
            rows = conn.execute(
                """
                SELECT id, snapshot_id, store_id, period, viewer_role, created_at,
                       payload_json
                FROM dashboard_snapshots
                WHERE store_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (store_id, limit, offset),
            ).fetchall()
            total = conn.execute(
                """
                SELECT COUNT(*) FROM dashboard_snapshots
                WHERE store_id = ?
                """,
                (store_id,),
            ).fetchone()[0] or 0
        else:
            rows = conn.execute(
                """
                SELECT id, snapshot_id, store_id, period, viewer_role, created_at,
                       payload_json
                FROM dashboard_snapshots
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            total = conn.execute(
                """
                SELECT COUNT(*) FROM dashboard_snapshots
                """
            ).fetchone()[0] or 0

        items = []
        for r in rows:
            payload = _safe_json_loads(r["payload_json"])
            overview = payload.get("overview", {}) if isinstance(payload, dict) else {}
            items.append({
                "id": r["id"],
                "snapshot_id": r["snapshot_id"],
                "store_id": r["store_id"],
                "period": r["period"],
                "viewer_role": r["viewer_role"],
                "created_at": r["created_at"],
                "overview": overview,
            })

        _log.info("snapshots_list success user_id=%s total=%d returned=%d",
                  str((current_user or {}).get("user_id") or ""),
                  total, len(items))
        return success_response(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": items,
            },
            workflow_code="dashboard_snapshot",
            mock=False,
        )


@router.get("/snapshots/{snapshot_id}")
def dashboard_snapshots_get(
    snapshot_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)] = None,
):
    """获取单个看板快照详情"""
    _log.info("snapshots_get start user_id=%s snapshot_id=%s",
              str((current_user or {}).get("user_id") or ""),
              snapshot_id)
    viewer_role = normalize_app_role(str(current_user.get("role") or "")) if current_user else ""
    viewer_user_id = str(current_user.get("user_id") or "") if current_user else ""

    with get_conn() as conn:
        _, actor_store_id = _restrict_dashboard_scope(conn, current_user or {}, "")
        row = conn.execute(
            """
            SELECT id, snapshot_id, store_id, user_id, overall_score, compliance_score,
                   training_completion_rate, recent_practice_avg_score, recent_high_risk_count,
                   core_weak_dimension, dashboard_result_json, source_workflow,
                   role_scope, period, viewer_role, payload_json, created_by, created_at
            FROM dashboard_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()

        if not row:
            _log.warning("snapshots_get not_found snapshot_id=%s", snapshot_id)
            raise HTTPException(status_code=404, detail="快照不存在")
        if viewer_role == "store_manager" and actor_store_id and _as_text(row["store_id"]) != actor_store_id:
            raise HTTPException(status_code=403, detail="店长仅可查看本门店风险看板快照")

        payload_obj = _normalize_dashboard_payload(_safe_json_loads(row["payload_json"]))
        result_obj = _normalize_dashboard_payload(_safe_json_loads(row["dashboard_result_json"]))
        core_weak_dimension = _normalize_coaching_focus(row["core_weak_dimension"])
        if not result_obj and payload_obj:
            result_obj = dict(payload_obj)
        if not core_weak_dimension and payload_obj:
            core_weak_dimension = _as_text(payload_obj.get("core_weak_dimension"))

        _log.info("snapshots_get success user_id=%s snapshot_id=%s",
                  str((current_user or {}).get("user_id") or ""),
                  snapshot_id)
        return success_response(
            {
                "id": row["id"],
                "snapshot_id": row["snapshot_id"],
                "store_id": row["store_id"],
                "user_id": row["user_id"],
                "overall_score": row["overall_score"],
                "compliance_score": row["compliance_score"],
                "training_completion_rate": row["training_completion_rate"],
                "recent_practice_avg_score": row["recent_practice_avg_score"],
                "recent_high_risk_count": row["recent_high_risk_count"],
                "core_weak_dimension": core_weak_dimension,
                "dashboard_result_json": json_text(result_obj) if result_obj else row["dashboard_result_json"],
                "source_workflow": row["source_workflow"],
                "role_scope": row["role_scope"],
                "period": row["period"],
                "viewer_role": row["viewer_role"],
                "payload_json": json_text(payload_obj) if payload_obj else row["payload_json"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            },
            workflow_code="dashboard_snapshot",
            mock=False,
        )


# ---------------------------------------------------------------------------
# Home Stats — 首页工作台数据看板
# ---------------------------------------------------------------------------

_DAY_NAMES = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def _collect_home_stats_payload(sid: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    month_ago = (now - timedelta(days=30)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    with get_conn() as conn:
        if sid:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE store_id = ?", (sid,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        total_employees = _to_int(row["c"], 0) if row else 0

        active_sql = (
            "SELECT DISTINCT employee_id FROM practice_records "
            "WHERE created_at >= ? AND employee_id != '' "
            "AND employee_id IN (SELECT CAST(id AS TEXT) FROM users) "
            "UNION "
            "SELECT DISTINCT employee_id FROM assistant_records "
            "WHERE created_at >= ? AND employee_id != '' "
            "AND employee_id IN (SELECT CAST(id AS TEXT) FROM users) "
            "UNION "
            "SELECT DISTINCT employee_id FROM learning_eval_records "
            "WHERE created_at >= ? AND employee_id != '' "
            "AND employee_id IN (SELECT CAST(id AS TEXT) FROM users) "
            "UNION "
            "SELECT DISTINCT employee_id FROM growth_plan_records "
            "WHERE created_at >= ? AND employee_id != '' "
            "AND employee_id IN (SELECT CAST(id AS TEXT) FROM users)"
        )
        params_active = [month_ago, month_ago, month_ago, month_ago]
        if sid:
            active_sql = (
                "SELECT DISTINCT pr.employee_id FROM practice_records pr "
                "JOIN users u ON pr.employee_id = CAST(u.id AS TEXT) "
                "WHERE pr.created_at >= ? AND pr.employee_id != '' AND u.store_id = ? "
                "UNION "
                "SELECT DISTINCT ar.employee_id FROM assistant_records ar "
                "JOIN users u1 ON ar.employee_id = CAST(u1.id AS TEXT) "
                "WHERE ar.created_at >= ? AND ar.employee_id != '' AND u1.store_id = ? "
                "UNION "
                "SELECT DISTINCT le.employee_id FROM learning_eval_records le "
                "JOIN users u2 ON le.employee_id = CAST(u2.id AS TEXT) "
                "WHERE le.created_at >= ? AND le.employee_id != '' AND u2.store_id = ? "
                "UNION "
                "SELECT DISTINCT gp.employee_id FROM growth_plan_records gp "
                "JOIN users u3 ON gp.employee_id = CAST(u3.id AS TEXT) "
                "WHERE gp.created_at >= ? AND gp.employee_id != '' AND u3.store_id = ? "
            )
            params_active = [month_ago, sid, month_ago, sid, month_ago, sid, month_ago, sid]
        try:
            active_rows = conn.execute(active_sql, params_active).fetchall()
            monthly_active = len(active_rows)
        except Exception:
            monthly_active = 0

        try:
            if sid:
                practice_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM practice_records pr "
                    "JOIN users u ON pr.employee_id = CAST(u.id AS TEXT) "
                    "WHERE pr.created_at >= ? AND u.store_id = ?",
                    (month_ago, sid),
                ).fetchone()
            else:
                practice_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM practice_records WHERE created_at >= ?",
                    (month_ago,),
                ).fetchone()
            monthly_practice_count = _to_int(practice_row["c"], 0) if practice_row else 0
        except Exception:
            monthly_practice_count = 0

        try:
            if sid:
                assistant_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM assistant_records WHERE created_at >= ? AND store_id = ?",
                    (month_ago, sid),
                ).fetchone()
            else:
                assistant_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM assistant_records WHERE created_at >= ?",
                    (month_ago,),
                ).fetchone()
            monthly_assistant_count = _to_int(assistant_row["c"], 0) if assistant_row else 0
        except Exception:
            monthly_assistant_count = 0

        ability_distribution = {"product_knowledge": 0, "compliance": 0, "sales_communication": 0, "response": 0}
        try:
            if sid:
                snap_rows = conn.execute(
                    "SELECT a.ability_snapshot_json FROM ability_update_records a "
                    "INNER JOIN ("
                    "  SELECT employee_id, MAX(id) AS max_id FROM ability_update_records "
                    "  WHERE employee_id IN (SELECT CAST(id AS TEXT) FROM users WHERE store_id = ?) "
                    "  GROUP BY employee_id"
                    ") latest ON a.id = latest.max_id",
                    (sid,),
                ).fetchall()
            else:
                snap_rows = conn.execute(
                    "SELECT a.ability_snapshot_json FROM ability_update_records a "
                    "INNER JOIN ("
                    "  SELECT employee_id, MAX(id) AS max_id FROM ability_update_records "
                    "  GROUP BY employee_id"
                    ") latest ON a.id = latest.max_id",
                ).fetchall()

            pk_scores, comp_scores, sc_scores, resp_scores = [], [], [], []
            for r in snap_rows:
                snap = _safe_json_loads(r["ability_snapshot_json"])
                pk = _to_float(snap.get("product_knowledge"), -1)
                comp = _to_float(snap.get("closing_skill"), -1)
                sc = _to_float(snap.get("sales_expression"), -1)
                resp = _to_float(snap.get("objection_handling"), -1)
                if pk >= 0:
                    pk_scores.append(pk)
                if comp >= 0:
                    comp_scores.append(comp)
                if sc >= 0:
                    sc_scores.append(sc)
                if resp >= 0:
                    resp_scores.append(resp)

            if pk_scores:
                ability_distribution = {
                    "product_knowledge": round(sum(pk_scores) / len(pk_scores), 1),
                    "compliance": round(sum(comp_scores) / len(comp_scores), 1) if comp_scores else 0,
                    "sales_communication": round(sum(sc_scores) / len(sc_scores), 1) if sc_scores else 0,
                    "response": round(sum(resp_scores) / len(resp_scores), 1) if resp_scores else 0,
                }
        except Exception:
            pass

        weekly_trend = []
        try:
            trend_sql = (
                "SELECT CAST(strftime('%w', created_at) AS INTEGER) AS dow, COUNT(*) AS c "
                "FROM ("
                "  SELECT created_at FROM practice_records WHERE created_at >= ? "
                "  UNION ALL "
                "  SELECT created_at FROM assistant_records WHERE created_at >= ? "
                "  UNION ALL "
                "  SELECT created_at FROM learning_eval_records WHERE created_at >= ? "
                "  UNION ALL "
                "  SELECT created_at FROM growth_plan_records WHERE created_at >= ? "
                ") sub "
                "GROUP BY dow ORDER BY dow"
            )
            trend_params = [week_ago, week_ago, week_ago, week_ago]
            if sid:
                trend_sql = (
                    "SELECT CAST(strftime('%w', sub.created_at) AS INTEGER) AS dow, COUNT(*) AS c "
                    "FROM ("
                    "  SELECT pr.created_at FROM practice_records pr "
                    "  JOIN users u ON pr.employee_id = CAST(u.id AS TEXT) "
                    "  WHERE pr.created_at >= ? AND u.store_id = ? "
                    "  UNION ALL "
                    "  SELECT created_at FROM assistant_records WHERE created_at >= ? AND store_id = ? "
                    "  UNION ALL "
                    "  SELECT le.created_at FROM learning_eval_records le "
                    "  JOIN users u2 ON le.employee_id = CAST(u2.id AS TEXT) "
                    "  WHERE le.created_at >= ? AND u2.store_id = ? "
                    "  UNION ALL "
                    "  SELECT gp.created_at FROM growth_plan_records gp "
                    "  JOIN users u3 ON gp.employee_id = CAST(u3.id AS TEXT) "
                    "  WHERE gp.created_at >= ? AND u3.store_id = ? "
                    ") sub "
                    "GROUP BY dow ORDER BY dow"
                )
                trend_params = [week_ago, sid, week_ago, sid, week_ago, sid, week_ago, sid]
            trend_rows = conn.execute(trend_sql, trend_params).fetchall()
            dow_map: dict[int, int] = {}
            for tr in trend_rows:
                dow_map[_to_int(tr["dow"], 0)] = _to_int(tr["c"], 0)
            for d in [1, 2, 3, 4, 5, 6, 0]:
                weekly_trend.append({"day": _DAY_NAMES[d], "count": dow_map.get(d, 0)})
        except Exception:
            for d in [1, 2, 3, 4, 5, 6, 0]:
                weekly_trend.append({"day": _DAY_NAMES[d], "count": 0})

    return {
        "store_filter": sid or "all",
        "metrics": {
            "total_employees": total_employees,
            "monthly_active": monthly_active,
            "monthly_practice_count": monthly_practice_count,
            "monthly_assistant_count": monthly_assistant_count,
        },
        "ability_distribution": ability_distribution,
        "weekly_trend": weekly_trend,
    }


def _ability_focus_summary(ability_distribution: dict[str, Any]) -> dict[str, Any]:
    strongest_key = ""
    strongest_val = -1.0
    for key, label in _ABILITY_FOCUS_LABELS.items():
        score = _to_float(ability_distribution.get(key), -1.0)
        if score > strongest_val:
            strongest_key = key
            strongest_val = score
    if strongest_val < 0:
        return {"key": "", "label": "暂无", "score": 0.0}
    return {
        "key": strongest_key,
        "label": _ABILITY_FOCUS_LABELS.get(strongest_key, strongest_key or "暂无"),
        "score": round(strongest_val, 1),
    }


def _aggregate_store_training_summaries(conn, db) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for store in _list_leaderboard_stores(db):
        sid = _as_text(store.get("store_id"))
        if not sid:
            continue
        summary = _store_average_summary(conn, db, sid)
        employee_count = _to_int(summary.get("_store_employee_count"), 0)
        if employee_count <= 0:
            continue
        items.append(
            {
                "store_id": sid,
                "store_name": _as_text(store.get("store_name")) or sid,
                "employee_count": employee_count,
                "summary": summary,
            }
        )
    return items


def _build_system_training_summary(store_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    total_people = sum(_to_int(item.get("employee_count"), 0) for item in store_summaries)
    if total_people <= 0:
        return {
            "training_coefficient": 0.0,
            "assessment_avg_score": 0.0,
            "latest_assessment_score": 0.0,
            "task_completion_rate": 0.0,
            "competition_total_score": 0.0,
            "task_completion": {"completed_count": 0, "total_count": 0, "rate": 0.0},
            "eligibility": {
                "is_eligible": False,
                "task_completion_passed": False,
                "score_passed": False,
                "no_absence": True,
                "no_cheat": True,
                "reasons": ["暂无系统训练数据"],
            },
            "_is_store_aggregate": True,
            "_store_employee_count": 0,
        }

    weighted_keys = [
        "training_coefficient",
        "assessment_avg_score",
        "latest_assessment_score",
        "task_completion_rate",
        "competition_total_score",
    ]
    weighted_totals = {key: 0.0 for key in weighted_keys}
    eligible_all = True
    task_pass_all = True
    score_pass_all = True

    for item in store_summaries:
        count = _to_int(item.get("employee_count"), 0)
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        if count <= 0:
            continue
        for key in weighted_keys:
            weighted_totals[key] += _to_float(summary.get(key), 0.0) * count
        eligibility = summary.get("eligibility") if isinstance(summary.get("eligibility"), dict) else {}
        eligible_all = eligible_all and bool(eligibility.get("is_eligible"))
        task_pass_all = task_pass_all and bool(eligibility.get("task_completion_passed"))
        score_pass_all = score_pass_all and bool(eligibility.get("score_passed"))

    averages = {key: round(weighted_totals[key] / total_people, 2) for key in weighted_keys}
    return {
        **averages,
        "task_completion": {
            "completed_count": 0,
            "total_count": 0,
            "rate": averages["task_completion_rate"],
        },
        "eligibility": {
            "is_eligible": eligible_all,
            "task_completion_passed": task_pass_all,
            "score_passed": score_pass_all,
            "no_absence": True,
            "no_cheat": True,
            "reasons": [
                f"全系统汇总：{len(store_summaries)} 家门店，{total_people} 人参与训练汇总"
            ],
        },
        "_is_store_aggregate": True,
        "_store_employee_count": total_people,
    }


def _latest_snapshot_rows(conn, store_id: str):
    sid = _as_text(store_id)
    if sid:
        return conn.execute(
            """
            SELECT id, store_id, payload_json, dashboard_result_json, core_weak_dimension, created_at
            FROM dashboard_snapshots
            WHERE store_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (sid,),
        ).fetchall()
    return conn.execute(
        """
        SELECT s.id, s.store_id, s.payload_json, s.dashboard_result_json, s.core_weak_dimension, s.created_at
        FROM dashboard_snapshots s
        INNER JOIN (
            SELECT COALESCE(NULLIF(store_id, ''), '__all__') AS grouped_store_id, MAX(id) AS max_id
            FROM dashboard_snapshots
            GROUP BY grouped_store_id
        ) latest ON s.id = latest.max_id
        ORDER BY s.id DESC
        """
    ).fetchall()


def _snapshot_payload_from_row(row) -> dict[str, Any]:
    payload = _safe_json_loads(row["payload_json"]) if row else {}
    if not payload and row:
        payload = _safe_json_loads(row["dashboard_result_json"])
    return _normalize_dashboard_payload(payload) if payload else {}


def _build_home_bi_risk_snapshot(conn, db, store_id: str) -> dict[str, Any]:
    sid = _as_text(store_id)
    rows = _latest_snapshot_rows(conn, sid)
    if not rows:
        return {
            "has_snapshot": False,
            "snapshot_time": "",
            "scope_label": (_store_display_name(db, sid) or sid) if sid else "全系统（全部门店）",
            "high_risk_count": 0,
            "core_weak_dimension": "",
            "manager_action_items": [],
        }

    if sid:
        row = rows[0]
        payload = _snapshot_payload_from_row(row)
        overview = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
        return {
            "has_snapshot": True,
            "snapshot_time": _iso_text(row["created_at"]),
            "scope_label": _store_display_name(db, sid) or sid,
            "high_risk_count": _to_int(overview.get("high_risk_count"), 0),
            "core_weak_dimension": _normalize_coaching_focus(row["core_weak_dimension"]),
            "manager_action_items": list(payload.get("manager_action_items") or [])[:3],
        }

    # Global mode: prefer latest global snapshot for high_risk_count,
    # only sum per-store snapshots as fallback
    valid_stores = {r["id"] for r in conn.execute("SELECT id FROM stores").fetchall()}
    high_risk_total: int | None = None
    latest_created_at = None
    weak_counter: dict[str, int] = {}
    action_items: list[str] = []
    snapshot_store_count = 0
    for row in rows:
        payload = _snapshot_payload_from_row(row)
        if not payload:
            continue
        row_store = _as_text(row["store_id"])
        overview = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
        # Use the latest global snapshot (empty store_id) as authoritative high_risk_count
        if not row_store:
            if high_risk_total is None:
                high_risk_total = _to_int(overview.get("high_risk_count"), 0)
            continue
        # Skip snapshots from stores that no longer exist
        if row_store not in valid_stores:
            continue
        snapshot_store_count += 1
        if high_risk_total is None:
            high_risk_total = (_to_int(overview.get("high_risk_count"), 0) or 0)
        weak = _normalize_coaching_focus(row["core_weak_dimension"])
        if weak:
            weak_counter[weak] = weak_counter.get(weak, 0) + 1
        for item in list(payload.get("manager_action_items") or []):
            text = _as_text(item)
            if text and text not in action_items:
                action_items.append(text)
        created_at = row["created_at"]
        if created_at and (latest_created_at is None or created_at > latest_created_at):
            latest_created_at = created_at

    core_weak = max(weak_counter.items(), key=lambda item: item[1])[0] if weak_counter else ""
    return {
        "has_snapshot": snapshot_store_count > 0,
        "snapshot_time": _iso_text(latest_created_at),
        "scope_label": f"全系统（{snapshot_store_count} 家门店快照）" if snapshot_store_count else "全系统（全部门店）",
        "high_risk_count": high_risk_total if high_risk_total is not None else 0,
        "core_weak_dimension": core_weak,
        "manager_action_items": action_items[:3],
    }


@router.get("/home-bi")
def dashboard_home_bi(
    store_id: str = "",
    current_user: Annotated[dict[str, Any], Depends(get_current_user)] = None,
):
    with get_conn() as conn:
        viewer_role, sid = _restrict_dashboard_scope(conn, current_user or {}, store_id)

    stats_payload = _collect_home_stats_payload(sid)
    ability_focus = _ability_focus_summary(
        stats_payload.get("ability_distribution") if isinstance(stats_payload.get("ability_distribution"), dict) else {}
    )

    with SessionLocal() as db:
        with get_conn() as conn:
            if sid:
                summary = _store_average_summary(conn, db, sid)
                leaderboard_items = _store_leaderboard(db, sid)[:3]
                leaderboard = {
                    "mode": "employees",
                    "title": "门店训练排行",
                    "items": [
                        {
                            "rank": _to_int(item.get("rank"), 0),
                            "label": _as_text(item.get("employee_name")) or "未命名员工",
                            "meta": _role_position_label(item.get("role")),
                            "value": round(_to_float(item.get("competition_total_score"), 0.0), 1),
                        }
                        for item in leaderboard_items
                    ],
                }
                scope_name = _store_display_name(db, sid) or sid
            else:
                store_summaries = _aggregate_store_training_summaries(conn, db)
                summary = _build_system_training_summary(store_summaries)
                top_stores = sorted(
                    store_summaries,
                    key=lambda item: (
                        -_to_float((item.get("summary") or {}).get("competition_total_score"), 0.0),
                        -_to_float((item.get("summary") or {}).get("task_completion_rate"), 0.0),
                    ),
                )[:3]
                leaderboard = {
                    "mode": "stores",
                    "title": "门店训练排行",
                    "items": [
                        {
                            "rank": idx + 1,
                            "label": _as_text(item.get("store_name")) or _as_text(item.get("store_id")) or "未命名门店",
                            "meta": f"{_to_int(item.get('employee_count'), 0)} 人",
                            "value": round(_to_float((item.get("summary") or {}).get("competition_total_score"), 0.0), 1),
                        }
                        for idx, item in enumerate(top_stores)
                    ],
                }
                scope_name = "全系统（全部门店）"

            risk_snapshot = _build_home_bi_risk_snapshot(conn, db, sid)

    competition_total_score = round(_to_float(summary.get("competition_total_score"), 0.0), 1)
    task_completion_rate = round(_to_float(summary.get("task_completion_rate"), 0.0), 1)
    high_risk_count = _to_int(risk_snapshot.get("high_risk_count"), 0)
    story_parts = [
        f"当前竞赛总分 {competition_total_score:.1f}",
        f"任务完成率 {task_completion_rate:.1f}%",
        (f"高风险 {high_risk_count} 人需优先跟进" if high_risk_count > 0 else "当前无高风险预警"),
    ]

    return success_response(
        {
            "scope": {
                "store_id": sid or "",
                "store_name": scope_name,
                "viewer_role": viewer_role,
                "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
            },
            "story": "，".join(story_parts),
            "headline": {
                "competition_total_score": competition_total_score,
                "task_completion_rate": task_completion_rate,
                "high_risk_count": high_risk_count,
            },
            "evidence": {
                "total_employees": _to_int((stats_payload.get("metrics") or {}).get("total_employees"), 0),
                "monthly_active": _to_int((stats_payload.get("metrics") or {}).get("monthly_active"), 0),
                "monthly_practice_count": _to_int((stats_payload.get("metrics") or {}).get("monthly_practice_count"), 0),
                "monthly_assistant_count": _to_int((stats_payload.get("metrics") or {}).get("monthly_assistant_count"), 0),
                "training_coefficient": round(_to_float(summary.get("training_coefficient"), 0.0), 2),
                "assessment_avg_score": round(_to_float(summary.get("assessment_avg_score"), 0.0), 1),
            },
            "trend": {
                "weekly_trend": list(stats_payload.get("weekly_trend") or []),
                "ability_focus": ability_focus,
            },
            "leaderboard": leaderboard,
            "risk_snapshot": risk_snapshot,
        },
        workflow_code="home_bi",
        mock=False,
    )


@router.get("/home-stats")
def dashboard_home_stats(
    store_id: str = "",
    current_user: Annotated[dict[str, Any], Depends(get_current_user)] = None,
):
    """首页工作台统计数据：指标卡片 + 能力分布 + 周学习趋势"""
    with get_conn() as conn:
        _, sid = _restrict_dashboard_scope(conn, current_user or {}, store_id)
    return success_response(_collect_home_stats_payload(sid), workflow_code="home_stats", mock=False)
