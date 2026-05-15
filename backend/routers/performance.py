from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String as SAString, cast, or_
from sqlalchemy.orm import Session

from api_response import error_response, success_response
from auth import get_current_user, normalize_app_role
from database import get_db
from db_stage3 import get_conn
from models import (
    AbilityUpdateRecord,
    AssessmentRecord,
    EmployeeProfile,
    LearningEvalRecord,
    PracticeEvalRecord,
    SalesPerformance,
    Store,
    User,
)
from training_cycle_service import get_active_cycle, refresh_module_snapshots
from training_plan import calculate_training_summary

router = APIRouter(prefix="/api/performance", tags=["Performance"])

_WORKFLOW_CODE = "performance"
_COEFFICIENT_WORKFLOW_CODE = "training_coefficient"

# 训练系数权重：知识考核 50% + 实战陪练 30% + 成长学习 20%
_COEFFICIENT_WEIGHTS = {
    "knowledge": 0.5,
    "practice": 0.3,
    "learning": 0.2,
}
_COEFFICIENT_BASELINE_SCORE = 60.0
_COEFFICIENT_BASELINE_VALUE = 0.8
_COEFFICIENT_MIN = 0.8
_COEFFICIENT_MAX = 1.2

_ABILITY_TIMELINE_DIMENSIONS: list[dict[str, str]] = [
    {"key": "product_knowledge", "label": "产品知识"},
    {"key": "compliance_expression", "label": "合规表达"},
    {"key": "needs_discovery", "label": "需求挖掘"},
    {"key": "sales_expression", "label": "销售沟通"},
    {"key": "objection_handling", "label": "异议处理"},
    {"key": "closing_skill", "label": "成交收口"},
]

_ABILITY_SNAPSHOT_ALIASES: dict[str, tuple[str, ...]] = {
    "product_knowledge": ("product_knowledge", "product_knowledge_score", "product"),
    "compliance_expression": ("compliance_expression", "compliance_score", "compliance"),
    "needs_discovery": ("needs_discovery", "needs_discovery_score", "customer_needs"),
    "sales_expression": ("sales_expression", "sales_communication_score", "sales_communication"),
    "objection_handling": ("objection_handling", "response_score", "objection_response"),
    "closing_skill": ("closing_skill", "closing_score", "closing"),
}

_RATE_METRICS = {
    "conversion_rate",
    "attach_rate",
    "member_conversion_rate",
    "high_margin_share",
}

_PERFORMANCE_TARGET_BASELINES: dict[str, dict[str, float]] = {
    "trainee": {
        "sales_amount": 60000.0,
        "avg_ticket": 4200.0,
        "conversion_rate": 0.18,
        "attach_rate": 0.25,
        "member_conversion_rate": 0.22,
        "high_margin_share": 0.18,
    },
    "senior_consultant": {
        "sales_amount": 100000.0,
        "avg_ticket": 6800.0,
        "conversion_rate": 0.24,
        "attach_rate": 0.35,
        "member_conversion_rate": 0.30,
        "high_margin_share": 0.24,
    },
    "store_manager": {
        "sales_amount": 120000.0,
        "avg_ticket": 7600.0,
        "conversion_rate": 0.26,
        "attach_rate": 0.38,
        "member_conversion_rate": 0.32,
        "high_margin_share": 0.28,
    },
    "default": {
        "sales_amount": 70000.0,
        "avg_ticket": 4800.0,
        "conversion_rate": 0.20,
        "attach_rate": 0.28,
        "member_conversion_rate": 0.24,
        "high_margin_share": 0.20,
    },
}

_PERFORMANCE_METRIC_CONFIG: dict[str, dict[str, str]] = {
    "sales_amount": {
        "label": "销售额",
        "unit": "元",
        "ability_gap": "成交推进与目标拆解",
        "training_topic": "成交推进与大单收口",
        "recommended_scene": "顾客犹豫不决不下单",
        "scene_code": "jewelry_recommendation",
        "action_title": "围绕销售额差距安排成交推进复盘",
    },
    "avg_ticket": {
        "label": "客单价",
        "unit": "元",
        "ability_gap": "高客单搭配与价值塑造",
        "training_topic": "高客单搭配与价值塑造",
        "recommended_scene": "顾客嫌贵",
        "scene_code": "objection_handling",
        "action_title": "围绕客单价差距补强高价值推荐",
    },
    "conversion_rate": {
        "label": "转化率",
        "unit": "%",
        "ability_gap": "需求洞察与异议处理",
        "training_topic": "需求追问与异议处理",
        "recommended_scene": "顾客犹豫不决不下单",
        "scene_code": "jewelry_recommendation",
        "action_title": "围绕转化率差距做需求挖掘与异议处理",
    },
    "attach_rate": {
        "label": "连带率",
        "unit": "%",
        "ability_gap": "组合推荐与连带销售",
        "training_topic": "连带推荐与组合搭配",
        "recommended_scene": "顾客嫌贵",
        "scene_code": "objection_handling",
        "action_title": "围绕连带率差距强化组合推荐",
    },
    "member_conversion_rate": {
        "label": "会员转化率",
        "unit": "%",
        "ability_gap": "会员邀约与权益讲解",
        "training_topic": "会员邀约与权益表达",
        "recommended_scene": "顾客犹豫不决不下单",
        "scene_code": "jewelry_recommendation",
        "action_title": "围绕会员转化率差距补强会员邀约",
    },
    "high_margin_share": {
        "label": "高毛利品类占比",
        "unit": "%",
        "ability_gap": "高价值品类推荐",
        "training_topic": "高毛利品类推荐",
        "recommended_scene": "顾客嫌贵",
        "scene_code": "objection_handling",
        "action_title": "围绕高毛利品类占比补强价值表达",
    },
}

_COMPETITION_AWARD_RULES: dict[int, dict[str, Any]] = {
    1: {"tier": "top1", "label": "Top 1", "badge": "冠军奖金位"},
    2: {"tier": "top2", "label": "Top 2", "badge": "亚军奖金位"},
    3: {"tier": "top3", "label": "Top 3", "badge": "季军奖金位"},
}



def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _avg(values: list[float]) -> float:
    cleaned = [float(v) for v in values if v is not None]
    if not cleaned:
        return 0.0
    return round(sum(cleaned) / len(cleaned), 2)


def _resolve_user(db: Session, raw_user_id: str) -> User | None:
    actor_id = _as_text(raw_user_id)
    if not actor_id:
        return None
    return (
        db.query(User)
        .filter(or_(cast(User.id, SAString) == actor_id, User.user_id == actor_id))
        .first()
    )


def _resolve_store_id(db: Session, raw_user_id: str) -> str:
    user = _resolve_user(db, raw_user_id)
    return _as_text(user.store_id if user else "")


def _canonical_user_ref(user: User | None, raw_user_id: str) -> str:
    if user and user.id is not None:
        return str(user.id)
    return _as_text(user.user_id if user else raw_user_id)


def _same_user_identity(actor: User | None, target: User | None, actor_raw_id: str, target_raw_id: str) -> bool:
    actor_aliases = {
        _as_text(actor_raw_id),
        _as_text(actor.id if actor else ""),
        _as_text(actor.user_id if actor else ""),
    }
    target_aliases = {
        _as_text(target_raw_id),
        _as_text(target.id if target else ""),
        _as_text(target.user_id if target else ""),
    }
    actor_aliases.discard("")
    target_aliases.discard("")
    return bool(actor_aliases & target_aliases)


def _assert_training_coefficient_access(
    db: Session,
    current_user: dict[str, Any],
    target_user_id: str,
) -> str:
    viewer_role = normalize_app_role(_as_text(current_user.get("role")))
    viewer_user_id = _as_text(current_user.get("user_id"))
    target_user = _resolve_user(db, target_user_id)
    if target_user is None:
        return target_user_id
    if viewer_role == "admin":
        return _canonical_user_ref(target_user, target_user_id)
    viewer_user = _resolve_user(db, viewer_user_id)
    if _same_user_identity(viewer_user, target_user, viewer_user_id, target_user_id):
        return _canonical_user_ref(target_user, target_user_id)
    if viewer_role == "store_manager":
        viewer_store_id = _as_text(viewer_user.store_id if viewer_user else "")
        target_store_id = _as_text(target_user.store_id)
        if viewer_store_id and target_store_id and viewer_store_id == target_store_id:
            return _canonical_user_ref(target_user, target_user_id)
        raise HTTPException(status_code=403, detail="店长仅可查看本门店员工培训系数")
    raise HTTPException(status_code=403, detail="普通员工仅可查看本人的培训系数")


def _assert_module_index_access(
    db: Session,
    current_user: dict[str, Any],
    target_user_id: str,
) -> str:
    viewer_role = normalize_app_role(_as_text(current_user.get("role")))
    viewer_user_id = _as_text(current_user.get("user_id"))
    target_user = _resolve_user(db, target_user_id)
    if target_user is None:
        if viewer_role == "admin":
            return target_user_id
        if _as_text(target_user_id) == viewer_user_id:
            return target_user_id
        if viewer_role == "store_manager":
            raise HTTPException(status_code=403, detail="店长仅可查看本人或本门店员工能力图谱")
        raise HTTPException(status_code=403, detail="普通员工仅可查看本人的能力图谱")
    if viewer_role == "admin":
        return _canonical_user_ref(target_user, target_user_id)
    viewer_user = _resolve_user(db, viewer_user_id)
    if _same_user_identity(viewer_user, target_user, viewer_user_id, target_user_id):
        return _canonical_user_ref(target_user, target_user_id)
    if viewer_role == "store_manager":
        viewer_store_id = _as_text(viewer_user.store_id if viewer_user else "")
        target_store_id = _as_text(target_user.store_id)
        if viewer_store_id and target_store_id and viewer_store_id == target_store_id:
            return _canonical_user_ref(target_user, target_user_id)
        raise HTTPException(status_code=403, detail="店长仅可查看本人或本门店员工能力图谱")
    raise HTTPException(status_code=403, detail="普通员工仅可查看本人的能力图谱")


def _user_aliases(db: Session, raw_user_id: str) -> list[str]:
    aliases: list[str] = []
    user = _resolve_user(db, raw_user_id)
    profile = None
    if user:
        profile = (
            db.query(EmployeeProfile)
            .filter(
                or_(
                    EmployeeProfile.user_id == str(user.id),
                    EmployeeProfile.user_id == _as_text(user.user_id),
                    EmployeeProfile.employee_id == str(user.id),
                    EmployeeProfile.employee_id == _as_text(user.user_id),
                )
            )
            .order_by(EmployeeProfile.id.desc())
            .first()
        )
    for item in [
        raw_user_id,
        str(user.id) if user else "",
        user.user_id if user else "",
        profile.user_id if profile else "",
        profile.employee_id if profile else "",
    ]:
        text = _as_text(item)
        if text and text not in aliases:
            aliases.append(text)
    return aliases


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return default
        return row[key]
    except Exception:
        return default


def _score_or_none(*values: Any) -> float | None:
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


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = _as_text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _snapshot_score(snapshot: dict[str, Any], key: str) -> float | None:
    for alias in _ABILITY_SNAPSHOT_ALIASES.get(key, (key,)):
        score = _score_or_none(snapshot.get(alias))
        if score is not None:
            return score
    return None


def _timeline_label(row: Any, fallback_index: int) -> str:
    stage_no = _int_or_none(_row_value(row, "stage_no"))
    day_index = _int_or_none(_row_value(row, "cycle_day_index"))
    if stage_no is not None and day_index is not None:
        return f"S{stage_no} · Day {day_index}"
    if day_index is not None:
        return f"Day {day_index}"
    created_at = _as_text(_row_value(row, "created_at"))
    if created_at:
        return created_at[:10]
    return f"记录 {fallback_index + 1}"


def _build_ability_timeline_items(rows: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        snapshot = _json_object(_row_value(row, "ability_snapshot_json"))
        explicit_overall = _score_or_none(
            _row_value(row, "overall_score"),
            _row_value(row, "score"),
            snapshot.get("overall_score"),
            snapshot.get("overall"),
        )
        values: dict[str, float | None] = {}
        for dim in _ABILITY_TIMELINE_DIMENSIONS:
            key = dim["key"]
            row_score = None
            if key == "product_knowledge":
                row_score = _row_value(row, "product_knowledge_score")
            elif key == "compliance_expression":
                row_score = _row_value(row, "compliance_score")
            elif key == "sales_expression":
                row_score = _row_value(row, "sales_communication_score")
            elif key == "objection_handling":
                row_score = _row_value(row, "response_score")
            values[key] = _score_or_none(_snapshot_score(snapshot, key), row_score)

        known_scores = [score for score in values.values() if score is not None]
        fallback_score = explicit_overall if explicit_overall is not None else _avg(known_scores)
        complete_values = {
            key: _score_or_none(value, fallback_score, 0.0) or 0.0
            for key, value in values.items()
        }
        overall_score = explicit_overall if explicit_overall is not None else _avg(list(complete_values.values()))
        items.append(
            {
                "id": _row_value(row, "id"),
                "update_id": _as_text(_row_value(row, "update_id")),
                "label": _timeline_label(row, index),
                "created_at": _as_text(_row_value(row, "created_at")),
                "stage_no": _int_or_none(_row_value(row, "stage_no")) or 0,
                "day_index": _int_or_none(_row_value(row, "cycle_day_index")) or 0,
                "overall_score": _score_or_none(overall_score, 0.0) or 0.0,
                "values": complete_values,
                "module_code": _as_text(_row_value(row, "module_code")),
                "module_name": _as_text(_row_value(row, "module_name")),
                "summary": _as_text(_row_value(row, "update_summary") or _row_value(row, "ability_comment")),
            }
        )
    return items


def _collect_ability_timeline_items(conn, user_aliases: list[str], limit: int = 36) -> list[dict[str, Any]]:
    aliases = [_as_text(alias) for alias in user_aliases if _as_text(alias)]
    if not aliases:
        return []
    placeholders = ",".join("?" for _ in aliases)
    rows = conn.execute(
        f"""
        SELECT *
        FROM (
          SELECT id, update_id, created_at, stage_no, cycle_day_index,
                 overall_score, score, product_knowledge_score, compliance_score,
                 sales_communication_score, response_score, ability_snapshot_json,
                 module_code, module_name, update_summary, ability_comment
          FROM ability_update_records
          WHERE user_id IN ({placeholders}) OR employee_id IN ({placeholders})
          ORDER BY datetime(created_at) DESC, id DESC
          LIMIT ?
        )
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        tuple(aliases) + tuple(aliases) + (max(2, min(120, int(limit or 36))),),
    ).fetchall()
    return _build_ability_timeline_items(list(rows))


def _or_conditions(column, aliases: list[str]):
    return or_(*[column == alias for alias in aliases])


def _trend_label(score_series: list[float]) -> str:
    if len(score_series) < 2:
        return "stable"
    recent = _avg(score_series[:3])
    previous = _avg(score_series[3:6]) if len(score_series) > 3 else _avg(score_series[1:])
    delta = recent - previous
    if delta >= 5:
        return "up"
    if delta <= -5:
        return "down"
    return "stable"


def _display_name(user: User | None, profile: EmployeeProfile | None, fallback_user_id: str) -> str:
    return (
        _as_text(user.display_name if user else "")
        or _as_text(profile.employee_name if profile else "")
        or f"员工{_as_text(fallback_user_id)}"
    )


def _canonical_user_id(user: User | None, fallback_user_id: str) -> str:
    return (
        _as_text(str(user.id) if user else "")
        or _as_text(user.user_id if user else "")
        or _as_text(fallback_user_id)
    )



def _normalize_metric_value(metric_key: str, value: Any) -> float:
    try:
        num = float(value)
    except Exception:
        return 0.0
    if metric_key in _RATE_METRICS:
        if num > 1.0:
            num = num / 100.0
        return max(0.0, min(1.0, num))
    return max(0.0, num)


def _metric_display_value(metric_key: str, value: Any) -> float:
    num = _normalize_metric_value(metric_key, value)
    if metric_key in _RATE_METRICS:
        return round(num * 100.0, 1)
    return round(num, 1)


def _metric_display_text(metric_key: str, value: Any) -> str:
    conf = _PERFORMANCE_METRIC_CONFIG.get(metric_key) or {}
    unit = conf.get("unit", "")
    shown = _metric_display_value(metric_key, value)
    if unit == "%":
        return f"{shown:.1f}%"
    if unit == "元":
        return f"{shown:.0f}元"
    return f"{shown:.1f}{unit}"


def _target_baselines(role: str) -> dict[str, float]:
    normalized = _as_text(role).lower()
    return dict(_PERFORMANCE_TARGET_BASELINES.get(normalized) or _PERFORMANCE_TARGET_BASELINES["default"])


def _stretch_target(metric_key: str, current_value: float, explicit_target: float, baseline_target: float) -> float:
    if explicit_target > 0:
        return explicit_target
    if metric_key == "sales_amount":
        return max(baseline_target, current_value * 1.15 if current_value > 0 else 0.0)
    if metric_key == "avg_ticket":
        return max(baseline_target, current_value * 1.10 if current_value > 0 else 0.0)
    if metric_key == "conversion_rate":
        return min(1.0, max(baseline_target, current_value + 0.03 if current_value > 0 else 0.0))
    if metric_key == "attach_rate":
        return min(1.0, max(baseline_target, current_value + 0.05 if current_value > 0 else 0.0))
    if metric_key == "member_conversion_rate":
        return min(1.0, max(baseline_target, current_value + 0.04 if current_value > 0 else 0.0))
    if metric_key == "high_margin_share":
        return min(1.0, max(baseline_target, current_value + 0.04 if current_value > 0 else 0.0))
    return max(baseline_target, explicit_target, current_value)


def _employee_stage(snapshot: dict[str, Any], has_sales_data: bool) -> tuple[str, int]:
    role = _as_text(snapshot.get("role")).lower()
    learning_count = int(snapshot.get("total_learning_count") or 0)
    practice_count = int(snapshot.get("total_practice_count") or 0)
    assessment_count = int(snapshot.get("total_assessment_count") or 0)
    training_volume = learning_count + practice_count + assessment_count
    if role == "store_manager":
        return ("管理带教", 90)
    if training_volume <= 6 or (role == "trainee" and not has_sales_data):
        return ("新人巩固", 60)
    if role == "senior_consultant" or training_volume >= 20:
        return ("成熟员工", 90)
    return ("在岗提升", 90)


def _build_performance_linkage(
    snapshot: dict[str, Any] | None,
    sales_summary: dict[str, Any],
    sales_items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not snapshot:
        return {
            "has_sales_data": False,
            "employee_stage": "待识别",
            "plan_cycle_days": 90,
            "plan_cycle_label": "90天",
            "current_metrics": {},
            "target_metrics": {},
            "priority_gaps": [],
            "primary_gap": {},
            "recommended_training_actions": [],
            "growth_direction": "提升综合销售能力",
            "performance_summary": "当前缺少训练快照，暂无法生成业绩差距画像。",
            "current_performance_text": "",
            "target_performance_text": "",
            "gap_metrics_text": "",
            "performance_context_card": "",
            "incentive_text": "待训练与业绩数据齐备后，再启用业绩驱动成长计划。",
        }

    employee_stage, plan_cycle_days = _employee_stage(snapshot, bool(sales_items))
    if not sales_items:
        return {
            "has_sales_data": False,
            "employee_stage": employee_stage,
            "plan_cycle_days": plan_cycle_days,
            "plan_cycle_label": f"{plan_cycle_days}天",
            "current_metrics": {},
            "target_metrics": {},
            "priority_gaps": [],
            "primary_gap": {},
            "recommended_training_actions": [],
            "growth_direction": "先接入真实业绩，再按差距驱动训练",
            "performance_summary": "当前尚未接入真实业绩数据，暂不生成业绩差距画像。",
            "current_performance_text": "",
            "target_performance_text": "",
            "gap_metrics_text": "",
            "performance_context_card": "【业绩现状】\n当前尚未接入真实业绩数据\n\n【建议】\n先补齐业绩数据，再生成差距驱动成长计划",
            "incentive_text": "建议先接入员工真实业绩数据，再把训练结果与绩效观察挂钩。",
        }

    latest = sales_items[0] if sales_items else {}
    baselines = _target_baselines(snapshot.get("role"))

    metric_fields = [
        "sales_amount",
        "avg_ticket",
        "conversion_rate",
        "attach_rate",
        "member_conversion_rate",
        "high_margin_share",
    ]
    current_metrics: dict[str, dict[str, Any]] = {}
    target_metrics: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []

    for metric_key in metric_fields:
        conf = _PERFORMANCE_METRIC_CONFIG.get(metric_key) or {}
        current_value = _normalize_metric_value(metric_key, latest.get(metric_key) if latest else 0.0)
        explicit_target = _normalize_metric_value(metric_key, latest.get(f"target_{metric_key}") if latest else 0.0)
        baseline_target = _normalize_metric_value(metric_key, baselines.get(metric_key))
        target_value = _stretch_target(metric_key, current_value, explicit_target, baseline_target)

        current_metrics[metric_key] = {
            "label": conf.get("label", metric_key),
            "value": round(current_value, 4),
            "display_value": _metric_display_value(metric_key, current_value),
            "display_text": _metric_display_text(metric_key, current_value),
            "unit": conf.get("unit", ""),
        }
        target_metrics[metric_key] = {
            "label": conf.get("label", metric_key),
            "value": round(target_value, 4),
            "display_value": _metric_display_value(metric_key, target_value),
            "display_text": _metric_display_text(metric_key, target_value),
            "unit": conf.get("unit", ""),
        }

        gap_value = max(0.0, target_value - current_value)
        if gap_value <= 0:
            continue

        denominator = target_value if target_value > 0 else 1.0
        gap_ratio = round(gap_value / denominator, 4)
        gaps.append(
            {
                "metric_key": metric_key,
                "metric_label": conf.get("label", metric_key),
                "current_value": round(current_value, 4),
                "current_display": _metric_display_text(metric_key, current_value),
                "target_value": round(target_value, 4),
                "target_display": _metric_display_text(metric_key, target_value),
                "gap_value": round(gap_value, 4),
                "gap_display": _metric_display_text(metric_key, gap_value),
                "gap_ratio": gap_ratio,
                "ability_gap": conf.get("ability_gap", "综合销售能力"),
                "training_topic": conf.get("training_topic", "针对性训练"),
                "recommended_scene": conf.get("recommended_scene", "顾客犹豫不决不下单"),
                "scene_code": conf.get("scene_code", "jewelry_recommendation"),
                "action_title": conf.get("action_title", "安排专项复盘"),
            }
        )

    gaps.sort(key=lambda item: (-float(item.get("gap_ratio") or 0.0), -float(item.get("gap_value") or 0.0)))
    priority_gaps = gaps[:3]
    primary_gap = priority_gaps[0] if priority_gaps else {}

    if priority_gaps:
        summary_items = [f"{item['metric_label']}距目标差{item['gap_display']}" for item in priority_gaps]
        performance_summary = "；".join(summary_items) + "。"
        growth_direction = f"优先提升{primary_gap.get('ability_gap') or '综合销售能力'}"
        gap_metrics_text = "；".join(
            [
                f"{item['metric_label']}当前{item['current_display']}，目标{item['target_display']}，建议训练{item['training_topic']}"
                for item in priority_gaps
            ]
        )
        recommended_training_actions = [
            {
                "metric_key": item["metric_key"],
                "metric_label": item["metric_label"],
                "action_title": item["action_title"],
                "training_topic": item["training_topic"],
                "recommended_scene": item["recommended_scene"],
                "scene_code": item["scene_code"],
            }
            for item in priority_gaps
        ]
        incentive_text = (
            f"建议把“{primary_gap.get('metric_label') or '核心指标'}”差距完成情况纳入月度绩效观察，"
            f"优先跟踪{primary_gap.get('training_topic') or '专项训练'}后的业绩变化。"
        )
    else:
        performance_summary = "当前主要业绩指标已接近目标，可进入下一轮精细化提升。"
        growth_direction = "保持训练节奏并做精细化提升"
        gap_metrics_text = "当前主要业绩指标已接近目标，建议继续围绕高客单推荐、会员转化和复盘稳定性做精细化训练。"
        recommended_training_actions = [
            {
                "metric_key": "",
                "metric_label": "稳定提升",
                "action_title": "保持月度复盘与季度重生成",
                "training_topic": "高价值推荐与复盘稳定性",
                "recommended_scene": "顾客犹豫不决不下单",
                "scene_code": "jewelry_recommendation",
            }
        ]
        incentive_text = "建议保留月度训练加分和季度成长计划重生机制，防止老员工停留在旧计划。"

    current_performance_text = "；".join(
        [
            f"{item['label']}{item['display_text']}"
            for item in current_metrics.values()
            if float(item.get("value") or 0.0) > 0
        ]
    )
    target_performance_text = "；".join(
        [
            f"{item['label']}{item['display_text']}"
            for item in target_metrics.values()
            if float(item.get("value") or 0.0) > 0
        ]
    )
    performance_context_card = "\n".join(
        [
            "【员工阶段】",
            employee_stage,
            "",
            "【当前业绩】",
            current_performance_text or "暂无真实业绩数据",
            "",
            "【目标业绩】",
            target_performance_text or "待设定目标",
            "",
            "【优先差距】",
            gap_metrics_text or "当前暂无显著差距，进入精细化提升阶段",
            "",
            "【推荐训练动作】",
            "；".join([item.get("action_title") or "" for item in recommended_training_actions if item.get("action_title")]),
        ]
    ).strip()

    return {
        "has_sales_data": bool(sales_items),
        "employee_stage": employee_stage,
        "plan_cycle_days": plan_cycle_days,
        "plan_cycle_label": f"{plan_cycle_days}天",
        "current_metrics": current_metrics,
        "target_metrics": target_metrics,
        "priority_gaps": priority_gaps,
        "primary_gap": primary_gap,
        "recommended_training_actions": recommended_training_actions,
        "growth_direction": growth_direction,
        "performance_summary": performance_summary,
        "current_performance_text": current_performance_text,
        "target_performance_text": target_performance_text,
        "gap_metrics_text": gap_metrics_text,
        "performance_context_card": performance_context_card,
        "incentive_text": incentive_text,
    }


def _build_performance_snapshot(db: Session, raw_user_id: str) -> dict[str, Any] | None:
    aliases = _user_aliases(db, raw_user_id)
    if not aliases:
        return None

    user = _resolve_user(db, raw_user_id)
    profile = (
        db.query(EmployeeProfile)
        .filter(
            or_(
                _or_conditions(EmployeeProfile.user_id, aliases),
                _or_conditions(EmployeeProfile.employee_id, aliases),
            )
        )
        .order_by(EmployeeProfile.id.desc())
        .first()
    )

    learning_rows = (
        db.query(LearningEvalRecord)
        .filter(or_(_or_conditions(LearningEvalRecord.user_id, aliases), _or_conditions(LearningEvalRecord.employee_id, aliases)))
        .order_by(LearningEvalRecord.created_at.desc(), LearningEvalRecord.id.desc())
        .all()
    )
    practice_rows = (
        db.query(PracticeEvalRecord)
        .filter(or_(_or_conditions(PracticeEvalRecord.user_id, aliases), _or_conditions(PracticeEvalRecord.employee_id, aliases)))
        .order_by(PracticeEvalRecord.created_at.desc(), PracticeEvalRecord.id.desc())
        .all()
    )
    assessment_rows = (
        db.query(AssessmentRecord)
        .filter(_or_conditions(AssessmentRecord.user_id, aliases))
        .order_by(AssessmentRecord.finished_at.desc(), AssessmentRecord.id.desc())
        .all()
    )
    ability_row = (
        db.query(AbilityUpdateRecord)
        .filter(or_(_or_conditions(AbilityUpdateRecord.user_id, aliases), _or_conditions(AbilityUpdateRecord.employee_id, aliases)))
        .order_by(AbilityUpdateRecord.created_at.desc(), AbilityUpdateRecord.id.desc())
        .first()
    )

    learning_scores = [
        float((row.answer_score if row.answer_score is not None else row.score) or 0.0)
        for row in learning_rows
        if (row.answer_score is not None or row.score is not None)
    ]
    practice_scores = [float(row.overall_score or 0.0) for row in practice_rows if row.overall_score is not None]
    assessment_scores = [float(row.score or 0.0) for row in assessment_rows if row.score is not None and float(row.score or 0.0) > 0]

    learning_avg = _avg(learning_scores)
    practice_avg = _avg(practice_scores)
    assessment_avg = _avg(assessment_scores)
    assessment_total = len(assessment_rows)
    assessment_passed = len([row for row in assessment_rows if int(row.is_pass or 0) == 1])
    assessment_pass_rate = round(assessment_passed * 100.0 / assessment_total, 2) if assessment_total else 0.0

    high_risk_count = len([row for row in practice_rows if _as_text(row.risk_level).lower() == "high"])
    high_risk_count += len([row for row in assessment_rows if int(row.is_pass or 0) == 0 and float(row.score or 0.0) > 0])

    ability_score = float(ability_row.overall_score or 0.0) if ability_row and ability_row.overall_score is not None else 0.0
    non_zero_scores = [score for score in [learning_avg, practice_avg, assessment_avg, ability_score] if score > 0]
    growth_index = round(sum(non_zero_scores) / len(non_zero_scores), 2) if non_zero_scores else 0.0

    score_series = (assessment_scores[:3] + practice_scores[:3]) + (assessment_scores[3:6] + practice_scores[3:6])
    trend = _trend_label(score_series)
    trend_label = {"up": "持续上升", "down": "近期回落", "stable": "平稳推进"}.get(trend, "平稳推进")

    latest_focus_dimension = _as_text(ability_row.focus_dimension if ability_row else "") or _as_text(profile.position if profile else "")
    latest_manager_tip = _as_text(ability_row.manager_tip if ability_row else "") or _as_text(profile.job_title if profile else "")

    completed_learning = len([row for row in learning_rows if float((row.answer_score if row.answer_score is not None else row.score) or 0.0) >= 60])
    learning_completion_rate = round(completed_learning * 100.0 / len(learning_rows), 2) if learning_rows else 0.0

    recommended_actions: list[str] = []
    if assessment_pass_rate < 80 and assessment_total:
        recommended_actions.append("优先安排正式考核复盘，补齐临场抗压短板。")
    if practice_avg and practice_avg < 75:
        recommended_actions.append("增加同场景高压陪练，提升真实对抗稳定性。")
    if high_risk_count > 0:
        recommended_actions.append("关注高风险回合，做逐轮复盘和话术修正。")
    if not recommended_actions:
        recommended_actions.append("保持当前训练节奏，沉淀优秀答题样本做团队分享。")

    summary_text = (
        f"{_display_name(user, profile, raw_user_id)}当前成长指数{growth_index:.1f}分，"
        f"正式考核通过率{assessment_pass_rate:.1f}%，趋势为{trend_label}。"
    )

    return {
        "user_id": _canonical_user_id(user, raw_user_id),
        "employee_name": _display_name(user, profile, raw_user_id),
        "role": _as_text(user.role if user else "") or _as_text(profile.role if profile else "") or "trainee",
        "store_id": _as_text(user.store_id if user else "") or _as_text(profile.store_id if profile else ""),
        "period_label": "当前训练周期",
        "learning_avg_score": learning_avg,
        "learning_completion_rate": learning_completion_rate,
        "practice_avg_score": practice_avg,
        "assessment_avg_score": assessment_avg,
        "assessment_pass_rate": assessment_pass_rate,
        "growth_index": growth_index,
        "high_risk_count": high_risk_count,
        "growth_trend": trend,
        "growth_trend_label": trend_label,
        "total_learning_count": len(learning_rows),
        "total_practice_count": len(practice_rows),
        "total_assessment_count": assessment_total,
        "latest_focus_dimension": latest_focus_dimension or "综合稳定性",
        "latest_manager_tip": latest_manager_tip or "建议持续观察训练波动并沉淀优秀样本。",
        "summary_text": summary_text,
        "recommended_actions": recommended_actions,
    }


def _latest_relevant_cycle(conn, user_id: str) -> dict[str, Any] | None:
    cycle = get_active_cycle(conn, user_id)
    if cycle:
        return cycle
    row = conn.execute(
        """
        SELECT *
        FROM training_cycles
        WHERE user_id = ? AND status != 'voided'
        ORDER BY COALESCE(updated_at, created_at, '') DESC, id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def _task_completion_snapshot(conn, user_id: str) -> dict[str, Any]:
    cycle = _latest_relevant_cycle(conn, user_id)
    if not cycle:
        return {
            "cycle_id": "",
            "cycle_status": "",
            "stage_no": 0,
            "stage_name": "",
            "completed_count": 0,
            "total_count": 0,
            "rate": 0.0,
        }
    counts = conn.execute(
        """
        SELECT COUNT(*) AS total_count,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count
        FROM cycle_daily_tasks
        WHERE cycle_id = ?
        """,
        (cycle["cycle_id"],),
    ).fetchone()
    total_count = int((counts["total_count"] if counts else 0) or 0)
    completed_count = int((counts["completed_count"] if counts else 0) or 0)
    rate = round((completed_count / total_count) * 100, 2) if total_count else 0.0
    return {
        "cycle_id": _as_text(cycle.get("cycle_id")),
        "cycle_status": _as_text(cycle.get("status")),
        "stage_no": int(cycle.get("stage_no") or 0),
        "stage_name": _as_text(cycle.get("stage_name")),
        "completed_count": completed_count,
        "total_count": total_count,
        "rate": rate,
    }


def _competition_summary_for_user(
    conn,
    *,
    user_id: str,
    include_module_indexes: bool = False,
) -> dict[str, Any]:
    task_completion = _task_completion_snapshot(conn, user_id)
    module_indexes = refresh_module_snapshots(conn, user_id=user_id, persist=include_module_indexes)
    learning_rows = conn.execute(
        """
        SELECT COALESCE(answer_score, score, 0) AS score
        FROM learning_eval_records
        WHERE user_id = ? OR employee_id = ?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user_id, user_id),
    ).fetchall()
    practice_rows = conn.execute(
        """
        SELECT COALESCE(overall_score, 0) AS score
        FROM practice_eval_records
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user_id,),
    ).fetchall()
    assessment_rows = conn.execute(
        """
        SELECT COALESCE(score, 0) AS score, COALESCE(is_pass, 0) AS is_pass
        FROM assessment_records
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user_id,),
    ).fetchall()
    latest_stage_review = conn.execute(
        """
        SELECT COALESCE(review_score, 0) AS review_score
        FROM training_stage_reviews
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    summary = calculate_training_summary(
        learning_scores=[float(row["score"] or 0) for row in learning_rows if float(row["score"] or 0) > 0],
        practice_scores=[float(row["score"] or 0) for row in practice_rows if float(row["score"] or 0) > 0],
        assessment_scores=[float(row["score"] or 0) for row in assessment_rows if float(row["score"] or 0) > 0],
        latest_stage_review_score=float((latest_stage_review["review_score"] if latest_stage_review else 0) or 0),
        task_completion_rate=float(task_completion["rate"]),
        monthly_scores=[],
    )
    latest_assessment_score = float(assessment_rows[0]["score"] or 0) if assessment_rows else 0.0
    competition_total_score = float(summary.get("competition_total_score") or 0.0)
    quarterly_scores = [round(competition_total_score, 2)] if competition_total_score > 0 else []
    quarterly_average = round(sum(quarterly_scores) / len(quarterly_scores), 2) if quarterly_scores else 0.0

    eligibility_reasons: list[str] = []
    if float(task_completion["rate"]) < 100:
        eligibility_reasons.append("任务完成率未达100%")
    if competition_total_score < 80:
        eligibility_reasons.append("竞赛总分低于80分")
    is_eligible = not eligibility_reasons

    return {
        **summary,
        "latest_assessment_score": round(latest_assessment_score, 2),
        "task_completion_rate": round(float(task_completion["rate"]), 2),
        "task_completion": {
            "completed_count": int(task_completion["completed_count"]),
            "total_count": int(task_completion["total_count"]),
            "rate": round(float(task_completion["rate"]), 2),
        },
        "cycle": {
            "cycle_id": task_completion["cycle_id"],
            "status": task_completion["cycle_status"],
            "stage_no": task_completion["stage_no"],
            "stage_name": task_completion["stage_name"],
        },
        "eligibility": {
            "is_eligible": is_eligible,
            "task_completion_passed": float(task_completion["rate"]) >= 100,
            "score_passed": competition_total_score >= 80,
            "no_absence": True,
            "no_cheat": True,
            "reasons": eligibility_reasons or ["满足当前培训奖金资格条件"],
        },
        "quarterly_summary": {
            "monthly_scores": quarterly_scores,
            "average_score": quarterly_average,
            "award_status": "eligible" if is_eligible else "pending",
            "months_count": len(quarterly_scores),
        },
        "module_indexes": module_indexes,
    }


def _store_leaderboard(db: Session, store_id: str) -> list[dict[str, Any]]:
    sid = _as_text(store_id)
    if not sid:
        return []
    users = db.query(User).filter(User.store_id == sid).order_by(User.id.asc()).all()
    items: list[dict[str, Any]] = []
    with get_conn() as conn:
        for user in users:
            canonical_user_id = _canonical_user_id(user, str(user.id))
            snapshot = _build_performance_snapshot(db, canonical_user_id)
            if not snapshot:
                continue
            competition = _competition_summary_for_user(conn, user_id=canonical_user_id, include_module_indexes=False)
            items.append({**snapshot, **competition})
    items.sort(
        key=lambda item: (
            -float(item.get("competition_total_score") or 0.0),
            -float(item.get("assessment_avg_score") or 0.0),
            -float(item.get("task_completion_rate") or 0.0),
            int(item.get("high_risk_count") or 0),
        )
    )
    ranked: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        award_meta = _COMPETITION_AWARD_RULES.get(idx) or {}
        ranked.append(
            {
                "rank": idx,
                "user_id": item["user_id"],
                "employee_name": item["employee_name"],
                "role": item["role"],
                "growth_index": item["growth_index"],
                "training_coefficient": item.get("training_coefficient", 0.0),
                "competition_total_score": item.get("competition_total_score", 0.0),
                "competition_status": item.get("competition_status", "pending"),
                "task_completion_rate": item.get("task_completion_rate", 0.0),
                "latest_assessment_score": item.get("latest_assessment_score", 0.0),
                "quarterly_average_score": item.get("quarterly_summary", {}).get("average_score", 0.0),
                "award_tier": award_meta.get("tier", ""),
                "award_label": award_meta.get("label", ""),
                "award_badge": award_meta.get("badge", ""),
                "assessment_avg_score": item["assessment_avg_score"],
                "practice_avg_score": item["practice_avg_score"],
                "learning_avg_score": item["learning_avg_score"],
                "assessment_pass_rate": item["assessment_pass_rate"],
                "growth_trend_label": item["growth_trend_label"],
                "high_risk_count": item["high_risk_count"],
            }
        )
    return ranked


def _store_has_training_users(db: Session, store_id: str) -> bool:
    sid = _as_text(store_id)
    if not sid:
        return False
    return db.query(User.id).filter(User.store_id == sid).first() is not None


def _store_display_name(db: Session, store_id: str) -> str:
    sid = _as_text(store_id)
    if not sid:
        return ""
    store = db.query(Store).filter(Store.store_id == sid).first()
    if not store:
        return sid
    return _as_text(store.store_name) or _as_text(store.name) or sid


def _list_leaderboard_stores(db: Session) -> list[dict[str, str]]:
    stores: list[dict[str, str]] = []
    for row in db.query(Store).order_by(Store.sort_order.asc(), Store.store_id.asc()).all():
        sid = _as_text(row.store_id)
        if not sid:
            continue
        if not _store_has_training_users(db, sid):
            continue
        stores.append(
            {
                "store_id": sid,
                "store_name": _as_text(row.store_name) or _as_text(row.name) or sid,
            }
        )
    return stores


def _all_store_leaderboards(db: Session) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    for store in _list_leaderboard_stores(db):
        sid = store["store_id"]
        stores.append(
            {
                "store_id": sid,
                "store_name": store["store_name"],
                "items": _store_leaderboard(db, sid)[:20],
            }
        )
    return stores


def _mask_leaderboard_name(name: str) -> str:
    return "门店同事"


def _sanitize_leaderboard_items_for_viewer(
    items: list[dict[str, Any]],
    *,
    viewer_role: str,
    viewer_user_id: str,
) -> list[dict[str, Any]]:
    normalized_role = normalize_app_role(_as_text(viewer_role))
    viewer_id = _as_text(viewer_user_id)
    if normalized_role in {"admin", "store_manager"}:
        return list(items)

    sanitized: list[dict[str, Any]] = []
    for item in items:
        current = dict(item or {})
        is_self = _as_text(current.get("user_id")) == viewer_id
        if is_self:
            current["is_self"] = True
            current["privacy_masked"] = False
            sanitized.append(current)
            continue

        sanitized.append(
            {
                "rank": current.get("rank", 0),
                "user_id": "",
                "employee_name": _mask_leaderboard_name(_as_text(current.get("employee_name"))),
                "role": "",
                "growth_index": 0.0,
                "training_coefficient": current.get("training_coefficient", 0.0),
                "competition_total_score": current.get("competition_total_score", 0.0),
                "competition_status": current.get("competition_status", "pending"),
                "task_completion_rate": None,
                "latest_assessment_score": current.get("latest_assessment_score", 0.0),
                "quarterly_average_score": current.get("quarterly_average_score", 0.0),
                "award_tier": "",
                "award_label": "",
                "award_badge": "",
                "assessment_avg_score": current.get("assessment_avg_score", 0.0),
                "practice_avg_score": None,
                "learning_avg_score": None,
                "assessment_pass_rate": None,
                "growth_trend_label": "",
                "high_risk_count": None,
                "is_self": False,
                "privacy_masked": True,
            }
        )
    return sanitized


def _store_average_summary(conn, db: Session, store_id: str) -> dict[str, Any]:
    """汇总指定门店所有员工的均值摘要，供管理员按门店查看。"""
    sid = _as_text(store_id)
    if not sid:
        return {}
    users = db.query(User).filter(User.store_id == sid).order_by(User.id.asc()).all()
    if not users:
        return {}
    totals = {
        "training_coefficient": 0.0,
        "assessment_avg_score": 0.0,
        "latest_assessment_score": 0.0,
        "task_completion_rate": 0.0,
        "competition_total_score": 0.0,
    }
    eligible_count = 0
    task_pass_count = 0
    score_pass_count = 0
    count = 0
    for user in users:
        uid = _canonical_user_id(user, str(user.id))
        s = _competition_summary_for_user(conn, user_id=uid, include_module_indexes=False)
        if not s:
            continue
        count += 1
        for k in totals:
            totals[k] += float(s.get(k) or 0.0)
        elig = s.get("eligibility") or {}
        if elig.get("is_eligible"):
            eligible_count += 1
        if elig.get("task_completion_passed"):
            task_pass_count += 1
        if elig.get("score_passed"):
            score_pass_count += 1
    if count == 0:
        return {}
    avg = {k: round(v / count, 2) for k, v in totals.items()}
    return {
        **avg,
        "competition_status": "active" if count > 0 else "pending",
        "task_completion": {
            "completed_count": 0,
            "total_count": 0,
            "rate": avg["task_completion_rate"],
        },
        "eligibility": {
            "is_eligible": eligible_count == count,
            "task_completion_passed": task_pass_count == count,
            "score_passed": score_pass_count == count,
            "no_absence": True,
            "no_cheat": True,
            "reasons": ["门店汇总：{0}/{1} 人满足资格".format(eligible_count, count)],
        },
        "quarterly_summary": {
            "average_score": avg["competition_total_score"],
            "award_status": "eligible" if eligible_count == count else "pending",
            "months_count": 0,
        },
        "_is_store_aggregate": True,
        "_store_employee_count": count,
    }


def _serialize_sales_performance_rows(rows: list[SalesPerformance]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "user_id": _as_text(row.user_id),
                "store_id": _as_text(row.store_id),
                "period_type": _as_text(row.period_type),
                "period_value": _as_text(row.period_value),
                "sales_amount": float(row.sales_amount or 0.0),
                "order_count": int(row.order_count or 0),
                "conversion_rate": float(row.conversion_rate or 0.0),
                "complaint_rate": float(row.complaint_rate or 0.0),
                "refund_rate": float(row.refund_rate or 0.0),
                "avg_ticket": float(row.avg_ticket or 0.0),
                "attach_rate": float(row.attach_rate or 0.0),
                "member_conversion_rate": float(row.member_conversion_rate or 0.0),
                "high_margin_share": float(row.high_margin_share or 0.0),
                "target_sales_amount": float(row.target_sales_amount or 0.0),
                "target_avg_ticket": float(row.target_avg_ticket or 0.0),
                "target_conversion_rate": float(row.target_conversion_rate or 0.0),
                "target_attach_rate": float(row.target_attach_rate or 0.0),
                "target_member_conversion_rate": float(row.target_member_conversion_rate or 0.0),
                "target_high_margin_share": float(row.target_high_margin_share or 0.0),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return items


def _query_sales_performance_rows(db: Session, aliases: list[str]) -> list[SalesPerformance]:
    if not aliases:
        return []
    return (
        db.query(SalesPerformance)
        .filter(_or_conditions(SalesPerformance.user_id, aliases))
        .order_by(SalesPerformance.created_at.desc(), SalesPerformance.id.desc())
        .all()
    )


def _sales_period_label(row: dict[str, Any]) -> str:
    parts: list[str] = []
    period_type = _as_text(row.get("period_type"))
    period_value = _as_text(row.get("period_value"))
    if period_type:
        parts.append(period_type)
    if period_value:
        parts.append(period_value)
    return " / ".join(parts) or "真实业绩周期"


def _build_sales_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "record_count": 0,
            "has_sales_data": False,
            "latest_period_label": "",
            "latest_sales_amount": 0.0,
            "latest_order_count": 0,
            "latest_conversion_rate": 0.0,
            "latest_complaint_rate": 0.0,
            "latest_refund_rate": 0.0,
            "latest_avg_ticket": 0.0,
            "latest_attach_rate": 0.0,
            "latest_member_conversion_rate": 0.0,
            "latest_high_margin_share": 0.0,
            "target_sales_amount": 0.0,
            "target_avg_ticket": 0.0,
            "target_conversion_rate": 0.0,
            "target_attach_rate": 0.0,
            "target_member_conversion_rate": 0.0,
            "target_high_margin_share": 0.0,
        }

    latest = items[0]
    return {
        "record_count": len(items),
        "has_sales_data": True,
        "latest_period_label": _sales_period_label(latest),
        "latest_sales_amount": float(latest.get("sales_amount") or 0.0),
        "latest_order_count": int(latest.get("order_count") or 0),
        "latest_conversion_rate": float(latest.get("conversion_rate") or 0.0),
        "latest_complaint_rate": float(latest.get("complaint_rate") or 0.0),
        "latest_refund_rate": float(latest.get("refund_rate") or 0.0),
        "latest_avg_ticket": float(latest.get("avg_ticket") or 0.0),
        "latest_attach_rate": float(latest.get("attach_rate") or 0.0),
        "latest_member_conversion_rate": float(latest.get("member_conversion_rate") or 0.0),
        "latest_high_margin_share": float(latest.get("high_margin_share") or 0.0),
        "target_sales_amount": float(latest.get("target_sales_amount") or 0.0),
        "target_avg_ticket": float(latest.get("target_avg_ticket") or 0.0),
        "target_conversion_rate": float(latest.get("target_conversion_rate") or 0.0),
        "target_attach_rate": float(latest.get("target_attach_rate") or 0.0),
        "target_member_conversion_rate": float(latest.get("target_member_conversion_rate") or 0.0),
        "target_high_margin_share": float(latest.get("target_high_margin_share") or 0.0),
    }


def build_employee_performance_bundle(db: Session, raw_user_id: str) -> dict[str, Any]:
    aliases = _user_aliases(db, raw_user_id)
    if not aliases and _as_text(raw_user_id):
        aliases = [_as_text(raw_user_id)]
    snapshot = _build_performance_snapshot(db, raw_user_id)
    rows = _query_sales_performance_rows(db, aliases)
    items = _serialize_sales_performance_rows(rows)
    sales_summary = _build_sales_summary(items)
    performance_linkage = _build_performance_linkage(snapshot, sales_summary, items)
    return {
        "aliases": aliases,
        "training_snapshot": snapshot,
        "sales_records": items,
        "sales_summary": sales_summary,
        "performance_linkage": performance_linkage,
    }


@router.get("/leaderboard")
def get_performance_leaderboard(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    store_id: str = Query("", description="门店编号，管理员可选；其他角色自动使用所属门店"),
):
    user_role = normalize_app_role(str(current_user.get("role") or ""))
    actor_user_id = _as_text(current_user.get("user_id"))
    resolved_sid = _as_text(store_id)
    if user_role == "admin" and not resolved_sid:
        stores = _all_store_leaderboards(db)
        return success_response(
            {
                "view_mode": "all_stores",
                "store_id": "",
                "store_name": "",
                "items": [],
                "stores": stores,
            },
            workflow_code=_WORKFLOW_CODE,
            mock=False,
        )
    if user_role != "admin":
        resolved_sid = _resolve_store_id(db, _as_text(current_user.get("user_id")))
    items = _store_leaderboard(db, resolved_sid)
    items = _sanitize_leaderboard_items_for_viewer(
        items,
        viewer_role=user_role,
        viewer_user_id=actor_user_id,
    )
    return success_response(
        {
            "view_mode": "single_store",
            "store_id": resolved_sid,
            "store_name": _store_display_name(db, resolved_sid),
            "items": items[:20],
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/module-index")
def get_module_index(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    user_id: str = Query("", description="要查看的员工 ID；普通员工仅限本人，店长限本门店，管理员不限"),
):
    actor_user_id = _as_text(current_user.get("user_id"))
    if not actor_user_id:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="当前登录用户无效",
            data={},
            http_status=401,
            mock=False,
        )
    target_user_id = _as_text(user_id) or actor_user_id
    try:
        effective_user_id = _assert_module_index_access(db, current_user, target_user_id)
    except HTTPException as exc:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message=str(exc.detail),
            data={"user_id": target_user_id},
            http_status=int(exc.status_code),
            mock=False,
        )
    target_user = _resolve_user(db, target_user_id) or _resolve_user(db, effective_user_id)
    target_profile = None
    if target_user:
        target_profile = (
            db.query(EmployeeProfile)
            .filter(
                or_(
                    EmployeeProfile.user_id == str(target_user.id),
                    EmployeeProfile.user_id == _as_text(target_user.user_id),
                    EmployeeProfile.employee_id == str(target_user.id),
                    EmployeeProfile.employee_id == _as_text(target_user.user_id),
                )
            )
            .order_by(EmployeeProfile.id.desc())
            .first()
        )
    aliases = _user_aliases(db, effective_user_id)
    with get_conn() as conn:
        items = refresh_module_snapshots(conn, user_id=effective_user_id, persist=True, user_aliases=aliases)
    selected_store_id = _as_text(target_user.store_id if target_user else "") or _as_text(target_profile.store_id if target_profile else "")
    return success_response(
        {
            "items": items,
            "viewer_role": normalize_app_role(_as_text(current_user.get("role"))),
            "selected_user_id": _as_text(str(target_user.id) if target_user and target_user.id is not None else target_user_id),
            "selected_user_name": _display_name(target_user, target_profile, target_user_id),
            "selected_store_id": selected_store_id,
            "selected_store_name": _store_display_name(db, selected_store_id) if selected_store_id else "",
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/ability-timeline")
def get_ability_timeline(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    user_id: str = Query("", description="要查看的员工 ID；普通员工仅限本人，店长限本门店，管理员不限"),
):
    actor_user_id = _as_text(current_user.get("user_id"))
    if not actor_user_id:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="当前登录用户无效",
            data={},
            http_status=401,
            mock=False,
        )
    target_user_id = _as_text(user_id) or actor_user_id
    try:
        effective_user_id = _assert_module_index_access(db, current_user, target_user_id)
    except HTTPException as exc:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message=str(exc.detail),
            data={"user_id": target_user_id},
            http_status=int(exc.status_code),
            mock=False,
        )

    target_user = _resolve_user(db, target_user_id) or _resolve_user(db, effective_user_id)
    target_profile = None
    if target_user:
        target_profile = (
            db.query(EmployeeProfile)
            .filter(
                or_(
                    EmployeeProfile.user_id == str(target_user.id),
                    EmployeeProfile.user_id == _as_text(target_user.user_id),
                    EmployeeProfile.employee_id == str(target_user.id),
                    EmployeeProfile.employee_id == _as_text(target_user.user_id),
                )
            )
            .order_by(EmployeeProfile.id.desc())
            .first()
        )
    aliases = _user_aliases(db, effective_user_id)
    with get_conn() as conn:
        items = _collect_ability_timeline_items(conn, aliases)
    selected_store_id = _as_text(target_user.store_id if target_user else "") or _as_text(target_profile.store_id if target_profile else "")
    return success_response(
        {
            "items": items,
            "dimensions": _ABILITY_TIMELINE_DIMENSIONS,
            "viewer_role": normalize_app_role(_as_text(current_user.get("role"))),
            "selected_user_id": _as_text(str(target_user.id) if target_user and target_user.id is not None else target_user_id),
            "selected_user_name": _display_name(target_user, target_profile, target_user_id),
            "selected_store_id": selected_store_id,
            "selected_store_name": _store_display_name(db, selected_store_id) if selected_store_id else "",
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/training-summary")
def get_training_summary(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    store_id: str = Query("", description="门店编号，管理员可传；非管理员忽略此参数"),
):
    user_id = _as_text(current_user.get("user_id"))
    if not user_id:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="当前登录用户无效",
            data={},
            http_status=401,
            mock=False,
        )
    user_role = str(current_user.get("role") or "")
    resolved_sid = _as_text(store_id)
    if user_role == "admin" and resolved_sid:
        # 管理员查看指定门店时，汇总该门店所有员工的均值
        with get_conn() as conn:
            summary = _store_average_summary(conn, db, resolved_sid)
    else:
        with get_conn() as conn:
            summary = _competition_summary_for_user(conn, user_id=user_id, include_module_indexes=True)
    return success_response(
        summary,
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


def _coefficient_grade(coefficient: float) -> str:
    """将数值系数映射到 A+/A/B/C/D 档位，用于前端徽章展示。"""
    if coefficient >= 1.15:
        return "A+"
    if coefficient >= 1.05:
        return "A"
    if coefficient >= 0.95:
        return "B"
    if coefficient >= 0.85:
        return "C"
    return "D"


def _compute_training_coefficient(snapshot: dict[str, Any]) -> dict[str, Any]:
    """根据训练快照映射培训系数。

    公式：composite = Σ(score_i × normalized_weight_i)，其中只对"有数据"的维度
    重新分配权重，避免缺维度时被清零；再线性映射到 [0.8, 1.2]。
    60 分 → 0.80（基准），每多 1 分 +0.01，封顶 1.20。
    """
    knowledge = float(snapshot.get("assessment_avg_score") or 0.0)
    practice = float(snapshot.get("practice_avg_score") or 0.0)
    learning = float(snapshot.get("learning_avg_score") or 0.0)

    components: list[tuple[str, float, float]] = []
    if knowledge > 0:
        components.append(("knowledge", knowledge, _COEFFICIENT_WEIGHTS["knowledge"]))
    if practice > 0:
        components.append(("practice", practice, _COEFFICIENT_WEIGHTS["practice"]))
    if learning > 0:
        components.append(("learning", learning, _COEFFICIENT_WEIGHTS["learning"]))

    if not components:
        return {
            "knowledge_score": 0.0,
            "practice_score": 0.0,
            "learning_score": 0.0,
            "composite_score": 0.0,
            "coefficient": 1.0,
            "grade": "N/A",
            "reason": "暂无训练数据，系数按基准 1.00 计",
            "has_data": False,
            "weights": dict(_COEFFICIENT_WEIGHTS),
            "formula": "coefficient = clamp(0.8 + (composite - 60) × 0.01, 0.8, 1.2)",
        }

    total_weight = sum(weight for _, _, weight in components)
    composite_raw = sum(score * (weight / total_weight) for _, score, weight in components)
    coefficient_raw = _COEFFICIENT_BASELINE_VALUE + (composite_raw - _COEFFICIENT_BASELINE_SCORE) * 0.01
    coefficient = max(_COEFFICIENT_MIN, min(_COEFFICIENT_MAX, coefficient_raw))

    composite = round(composite_raw, 1)
    coefficient = round(coefficient, 2)

    reason_parts: list[str] = []
    if knowledge > 0:
        reason_parts.append(f"知识考核 {knowledge:.1f}×{_COEFFICIENT_WEIGHTS['knowledge']}")
    if practice > 0:
        reason_parts.append(f"实战陪练 {practice:.1f}×{_COEFFICIENT_WEIGHTS['practice']}")
    if learning > 0:
        reason_parts.append(f"成长学习 {learning:.1f}×{_COEFFICIENT_WEIGHTS['learning']}")
    reason = " + ".join(reason_parts) + f" → 综合 {composite:.1f} → 系数 {coefficient:.2f}"

    return {
        "knowledge_score": round(knowledge, 1),
        "practice_score": round(practice, 1),
        "learning_score": round(learning, 1),
        "composite_score": composite,
        "coefficient": coefficient,
        "grade": _coefficient_grade(coefficient),
        "reason": reason,
        "has_data": True,
        "weights": dict(_COEFFICIENT_WEIGHTS),
        "formula": "coefficient = clamp(0.8 + (composite - 60) × 0.01, 0.8, 1.2)",
    }


@router.get("/training-coefficient")
def get_training_coefficient(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    user_id: str | None = None,
    period: str | None = None,
):
    """导出训练系数：把训练成绩映射为 0.8–1.2 的绩效挂钩系数。

    缺省 user_id 时返回当前登录员工本人；period 缺省取当前月（YYYY-MM）。
    本接口只读不落库，纯计算复用 `_build_performance_snapshot`。
    """
    target_user_id = _as_text(user_id) or _as_text(current_user.get("user_id"))
    if not target_user_id:
        return error_response(
            workflow_code=_COEFFICIENT_WORKFLOW_CODE,
            message="缺少 user_id 参数且当前登录身份不可识别",
            data={},
            http_status=400,
            mock=False,
        )
    try:
        target_user_id = _assert_training_coefficient_access(db, current_user, target_user_id)
    except HTTPException as exc:
        return error_response(
            workflow_code=_COEFFICIENT_WORKFLOW_CODE,
            message=str(exc.detail),
            data={"user_id": target_user_id},
            http_status=int(exc.status_code),
            mock=False,
        )

    snapshot = _build_performance_snapshot(db, target_user_id)
    if not snapshot:
        return error_response(
            workflow_code=_COEFFICIENT_WORKFLOW_CODE,
            message="未找到该员工的训练数据",
            data={"user_id": target_user_id},
            http_status=404,
            mock=False,
        )

    period_label = _as_text(period) or datetime.now().strftime("%Y-%m")
    coefficient_data = _compute_training_coefficient(snapshot)

    return success_response(
        {
            "user_id": snapshot.get("user_id"),
            "employee_name": snapshot.get("employee_name"),
            "store_id": snapshot.get("store_id"),
            "role": snapshot.get("role"),
            "period": period_label,
            **coefficient_data,
            "source_snapshot": {
                "assessment_avg_score": snapshot.get("assessment_avg_score"),
                "practice_avg_score": snapshot.get("practice_avg_score"),
                "learning_avg_score": snapshot.get("learning_avg_score"),
                "assessment_pass_rate": snapshot.get("assessment_pass_rate"),
                "total_assessment_count": snapshot.get("total_assessment_count"),
                "total_practice_count": snapshot.get("total_practice_count"),
                "total_learning_count": snapshot.get("total_learning_count"),
                "growth_index": snapshot.get("growth_index"),
                "growth_trend_label": snapshot.get("growth_trend_label"),
            },
        },
        workflow_code=_COEFFICIENT_WORKFLOW_CODE,
        mock=False,
    )
