from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import config as app_config
from api_response import dify_failure_response, make_request_id, success_response
from auth import get_current_user
from database import SessionLocal
from db_stage3 import get_conn, json_text, upsert_employee_profile
from dify_stage4b import run_practice1_chat, run_practice2_workflow, run_practice3_workflow, run_practice_mentor_workflow, run_practice_turn_feedback
from routers.performance import build_employee_performance_bundle

router = APIRouter(prefix="/api/practice", tags=["practice"])

_log = logging.getLogger("jewelry_qipei.router.practice")


class PracticeChatRequest(BaseModel):
    session_id: str = Field("", description="陪练会话 ID")
    scene_code: str = Field("", description="场景编码")
    module_code: str = Field("", description="训练模块编码")
    difficulty_level: str = Field("", description="难度等级")
    user_message: str = Field("", description="员工输入")
    conversation_id: str = Field("", description="Dify conversation_id")
    action: str = Field("send", description="send/end/resume")
    trainee_role: str = Field("", description="学员角色")
    force_new_session: bool = Field(False, description="是否强制新会话")
    score_branch: str = Field("practice", description="分数分支：practice / assessment")
    cycle_day_index: int | None = Field(None, description="训练周期天数索引")

class PracticeEvaluateRequest(BaseModel):
    session_id: str = Field("", description="陪练会话 ID")
    scene_code: str = Field("", description="场景编码")
    module_code: str = Field("", description="训练模块编码")
    conversation: list[dict[str, Any]] = Field(default_factory=list, description="会话记录")
    score_branch: str = Field("practice", description="分数分支：practice / assessment")
    cycle_day_index: int | None = Field(None, description="训练周期天数索引")


class PracticeAbilityUpdateRequest(BaseModel):
    session_id: str = Field("", description="陪练会话 ID")
    employee_id: str = Field("", description="员工 ID")
    evaluation_id: str = Field("", description="评分结果 ID")
    module_code: str = Field("", description="训练模块编码")
    score: float | None = Field(None, description="本轮评分")
    score_branch: str = Field("practice", description="分数分支：practice / assessment")
    cycle_day_index: int | None = Field(None, description="训练周期天数索引")


class PracticeMentorFeedbackRequest(BaseModel):
    session_id: str = Field("", description="陪练会话 ID")
    scene_code: str = Field("", description="场景编码")
    module_code: str = Field("", description="训练模块编码")
    conversation: list[dict[str, Any]] = Field(default_factory=list, description="会话记录")
    overall_score: float | None = Field(None, description="本轮评分")
    strengths: list[str] = Field(default_factory=list, description="亮点")
    improvements: list[str] = Field(default_factory=list, description="改进点")
    coach_summary: str = Field("", description="教练总结")


def _scene_code_to_practice2_type(scene_code: str) -> str:
    s = (scene_code or "").strip().lower()
    if s == "objection_handling":
        return "顾客嫌贵"
    if s == "jewelry_recommendation":
        return "顾客犹豫不决不下单"
    if s == "after_sales":
        return "顾客质疑材质真假"
    return "其他"


def _scene_code_to_practice3_code(scene_code: str) -> str:
    s = (scene_code or "").strip().lower()
    if s == "objection_handling":
        return "顾客嫌贵"
    if s == "jewelry_recommendation":
        return "顾客犹豫不决不下单"
    if s == "after_sales":
        return "顾客质疑材质真假"
    return "自定义场景"


def _scene_code_to_practice1_type(scene_code: str) -> str:
    s = (scene_code or "").strip().lower()
    if s == "objection_handling":
        return "顾客嫌贵"
    if s == "jewelry_recommendation":
        return "顾客犹豫不决不下单"
    if s == "after_sales":
        return "顾客质疑材质真假"
    return "顾客嫌贵"


def _map_weak_dimension_for_practice3(weak_dimension: str) -> str:
    s = (weak_dimension or "").strip()
    if not s:
        return "自动判断"
    if s in {"自动判断", "产品知识", "合规表达", "销售沟通", "应变回应"}:
        return s
    if s in {"异议处理", "回应不清", "应变不足"}:
        return "应变回应"
    if s in {"需求洞察", "成交推进", "开场建联"}:
        return "销售沟通"
    if s in {"专业准确", "产品准确", "产品表述错误"}:
        return "产品知识"
    if s in {"合规风险", "过度承诺", "逼单风险"}:
        return "合规表达"
    return "自动判断"


def _difficulty_to_practice1(difficulty_level: str) -> str:
    s = (difficulty_level or "").strip().lower()
    if s in {"advanced", "hard", "high"}:
        return "进阶"
    if s in {"pressure", "expert"}:
        return "高压"
    return "标准"


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_json_loads(s: str) -> dict[str, Any]:
    text = _as_text(s)
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _safe_json_loads_list(s: str) -> list[dict[str, Any]]:
    text = _as_text(s)
    if not text:
        return []
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
    except Exception:
        pass
    return []


def _is_end_command(message: str) -> bool:
    m = _as_text(message)
    if not m:
        return False
    hits = ("结束陪练", "结束", "停止", "结束训练", "结束本轮")
    return any(x in m for x in hits)


def _strip_end_flag(text: str) -> tuple[str, int]:
    raw = _as_text(text)
    if "[PRACTICE_END]" not in raw:
        return raw, 0
    cleaned = raw.replace("[PRACTICE_END]", "").strip()
    return cleaned, 1


_TURN_FEEDBACK_FIELDS = (
    "intent_label",
    "intent_reason",
    "customer_state",
    "mentor_comment",
    "next_action",
    "next_question",
    "voice_advice",
)


def _normalize_turn_feedback(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in _TURN_FEEDBACK_FIELDS:
        text = _as_text(source.get(key))
        if text:
            out[key] = text
    risk_flag = _as_text(source.get("risk_flag"))
    if risk_flag:
        out["risk_flag"] = risk_flag
    return out


def _parse_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = _as_text(raw)
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _find_turn_feedback_candidate(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        direct = _normalize_turn_feedback(raw)
        if direct:
            return direct
        for key in ("turn_feedback", "feedback", "metadata", "data", "outputs", "raw_outputs", "result"):
            nested = raw.get(key)
            nested_obj = _parse_json_object(nested)
            if not nested_obj and isinstance(nested, dict):
                nested_obj = nested
            if not nested_obj:
                continue
            if key == "turn_feedback":
                direct_nested = _normalize_turn_feedback(nested_obj)
                if direct_nested:
                    return direct_nested
            candidate = _find_turn_feedback_candidate(nested_obj)
            if candidate:
                return candidate
        for key in ("turn_feedback_json", "feedback_json"):
            candidate = _normalize_turn_feedback(_parse_json_object(raw.get(key)))
            if candidate:
                return candidate
    else:
        candidate = _normalize_turn_feedback(_parse_json_object(raw))
        if candidate:
            return candidate
    return {}


def _extract_turn_feedback_from_call(call: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {}
    data = call.get("data") if isinstance(call.get("data"), dict) else {}
    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    for source in (data, raw):
        candidate = _find_turn_feedback_candidate(source)
        if candidate:
            return candidate
    return {}


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    haystack = _as_text(text)
    return any(token and token in haystack for token in tokens)


def _build_turn_feedback_fallback(
    *,
    scene_code: str,
    user_message: str,
    assistant_reply: str,
    risk_hit_this_turn: int = 0,
) -> dict[str, Any]:
    assistant_text = _as_text(assistant_reply)
    user_text = _as_text(user_message)
    analysis_text = assistant_text or f"{user_text}\n{assistant_text}"
    combined = f"{assistant_text}\n{user_text}"
    intent_label = "需求确认"
    intent_reason = "顾客仍在确认信息，尚未形成明确购买决策。"
    customer_state = "谨慎"
    mentor_comment = "先别急着堆卖点，先把顾客这轮最卡的顾虑问实。"
    next_action = "缩小顾客当前最在意的一个阻力点，再顺着回应。"
    next_question = "您现在最拿不定的，是预算、款式，还是佩戴场景？"
    voice_advice = "先问实顾客卡点，再顺着回应。"

    if (
        (_contains_any(analysis_text, ("黄金", "足金")) and _contains_any(analysis_text, ("k金", "K金", "18k", "18K")))
        or (_contains_any(analysis_text, ("黄金", "足金")) and _contains_any(analysis_text, ("款式", "年轻", "时尚", "保值", "区别", "怎么选")))
    ):
        intent_label = "材质对比"
        intent_reason = "顾客已经把纠结点说到材质选择上，核心是在比较黄金与 K 金的款式感、佩戴体验和保值预期。"
        customer_state = "权衡"
        mentor_comment = "这轮别再泛问卡点，直接顺着顾客已说出的比较维度回应，把黄金和 K 金在款式感、佩戴场景和保值差异拆开讲清楚。"
        next_action = "先确认顾客更优先看日常佩戴效果，还是更看重材质稳妥和保值，再给出对应推荐。"
        next_question = "如果现在二选一，您会更偏向戴起来更年轻时尚，还是更看重材质更稳、更保值？"
        voice_advice = "顺着顾客这轮的材质对比来讲，直接拆黄金和 K 金的款式感、佩戴场景和保值差异。"
    elif any(token in combined for token in ("贵", "价格", "便宜", "优惠", "虚高", "折扣")):
        intent_label = "价格试探"
        intent_reason = "顾客持续围绕价格和是否值得发问，本质是在评估价值感。"
        customer_state = "谨慎"
        mentor_comment = "别急着让价，先把价格背后的材质、工艺和佩戴价值拆清楚。"
        next_action = "先确认顾客更在意预算压力还是价值感不足。"
        next_question = "您现在更在意超预算，还是觉得这款的价值感还不够明确？"
        voice_advice = "先别降价，先确认顾客是在意预算还是价值感。"
    elif any(token in combined for token in ("真假", "材质", "证书", "足金", "真金", "鉴定")):
        intent_label = "真假确认"
        intent_reason = "顾客反复确认材质和证书，核心在验证信任与安全感。"
        customer_state = "警惕"
        mentor_comment = "这轮先稳住信任感，用证书、材质依据和售后承接，不要空口保证。"
        next_action = "拿出可验证依据，按材质、证书、售后顺序回应。"
        next_question = "您最想先确认的是材质依据、证书信息，还是后续售后保障？"
        voice_advice = "先用可验证依据稳住信任，再接顾客顾虑。"
    elif any(token in analysis_text for token in ("送人", "送礼", "对象", "场合", "佩戴场景", "婚礼", "通勤")):
        intent_label = "场景匹配"
        intent_reason = "顾客在确认购买对象或佩戴场景，说明还在判断这款是否真正适配使用需求。"
        customer_state = "斟酌"
        mentor_comment = "顾客已经把判断标准转到使用场景上了，别再堆通用卖点，直接按送礼对象、佩戴频率和搭配效果给方案。"
        next_action = "先锁定主要使用场景，再把对应款式和佩戴效果收窄到一两种选择。"
        next_question = "这款您更倾向自己日常佩戴，还是送人用？主要会出现在什么场合？"
        voice_advice = "顺着场景做推荐，先锁定对象和佩戴场合，再给对应方案。"
    elif any(token in combined for token in ("再看看", "考虑", "想想", "不急", "先不", "回头")):
        intent_label = "犹豫观望"
        intent_reason = "顾客没有直接拒绝，而是在延后决策，说明仍有未说透的顾虑。"
        customer_state = "观望"
        mentor_comment = "不要连续补充卖点，先把顾客迟迟不决的真实卡点问出来。"
        next_action = "追问顾客此刻最难下决心的原因。"
        next_question = "您现在主要还没想定的是预算、款式，还是送礼场景？"
        voice_advice = "先问出顾客迟疑的真实卡点，再继续推进。"
    elif (scene_code or "").strip().lower() == "after_sales":
        intent_label = "售后确认"
        intent_reason = "顾客更关注后续保障和是否靠谱，需要稳定预期。"
        customer_state = "谨慎"
        mentor_comment = "先给清晰边界和售后依据，让顾客知道出问题怎么处理。"
        next_action = "按保障范围、处理流程、门店承接顺序说明。"
        next_question = "您最担心的是售后流程、保养方式，还是后续维修成本？"
        voice_advice = "先把售后边界和处理流程讲清楚。"

    risk_flag = ""
    if risk_hit_this_turn == 1 or any(token in combined for token in ("保证升值", "一定升值", "绝对", "最低价", "假一赔十", "百分百")):
        risk_flag = "注意合规表达：避免承诺保值升值、绝对化表述或最低价保证。"
    elif any(token in combined for token in ("今天必须", "最后一件", "再不买就没了", "马上定")):
        risk_flag = "注意节奏：避免制造稀缺焦虑或明显逼单。"

    feedback = {
        "intent_label": intent_label,
        "intent_reason": intent_reason,
        "customer_state": customer_state,
        "mentor_comment": mentor_comment,
        "next_action": next_action,
        "next_question": next_question,
        "voice_advice": voice_advice,
    }
    if risk_flag:
        feedback["risk_flag"] = risk_flag
    return feedback


def _ensure_turn_feedback(
    *,
    scene_code: str,
    user_message: str,
    assistant_reply: str,
    raw_feedback: Any,
    risk_hit_this_turn: int = 0,
) -> dict[str, Any]:
    fallback = _build_turn_feedback_fallback(
        scene_code=scene_code,
        user_message=user_message,
        assistant_reply=assistant_reply,
        risk_hit_this_turn=risk_hit_this_turn,
    )
    candidate = _normalize_turn_feedback(raw_feedback)
    if not candidate:
        return fallback
    merged = dict(fallback)
    merged.update(candidate)
    if not _as_text(merged.get("risk_flag")) and _as_text(candidate.get("risk_flag")):
        merged["risk_flag"] = _as_text(candidate.get("risk_flag"))
    if not _as_text(merged.get("risk_flag")) and "risk_flag" in fallback:
        merged["risk_flag"] = _as_text(fallback.get("risk_flag"))
    if not _as_text(merged.get("risk_flag")):
        merged.pop("risk_flag", None)
    return merged


def _latest_turn_feedback_from_conversation(conversation: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(conversation or []):
        if not isinstance(item, dict):
            continue
        if _as_text(item.get("role")).lower() != "assistant":
            continue
        feedback = _normalize_turn_feedback(item.get("turn_feedback"))
        if feedback:
            return feedback
    return {}


def _mock_practice1_reply(
    *,
    scenario_text: str,
    round_count: int,
    end_requested: bool,
) -> str:
    if end_requested:
        return "好的，这轮就先到这里。你刚才的回应比上一轮更自然一些，建议下一轮再加强追问力度。\n[PRACTICE_END]"
    if round_count <= 1:
        return f"你的说法我听到了，但在“{scenario_text}”这个点上我还是有点拿不准，你能不能再说具体一点？"
    if round_count >= 5:
        return "行，你这次解释比刚开始清楚多了，我可以再看看款式和活动细节。\n[PRACTICE_END]"
    return "我理解你的意思，但我还是会担心价格和后续保障，你再帮我对比下更合适的方案。"


def _load_latest_chat_state(conn, session_id: str, employee_id: str) -> dict[str, Any]:
    sid = _as_text(session_id)
    eid = _as_text(employee_id)
    if not sid or not eid:
        return {}
    row = conn.execute(
        """
        SELECT id, payload_json, conversation_json, scene_code, module_code, difficulty_level
        FROM practice_records
        WHERE session_id = ? AND employee_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (sid, eid),
    ).fetchone()
    if not row:
        return {}
    payload = _safe_json_loads(_as_text(row["payload_json"]))
    session_state = payload.get("session_state") if isinstance(payload.get("session_state"), dict) else {}
    if not isinstance(session_state, dict):
        session_state = {}
    conversation = payload.get("conversation")
    if not isinstance(conversation, list):
        conversation = _safe_json_loads_list(_as_text(row["conversation_json"]))
    return {
        "db_record_id": int(row["id"] or 0),
        "session_state": session_state,
        "conversation": [x for x in (conversation or []) if isinstance(x, dict)],
        "scene_code": _as_text(row["scene_code"]),
        "module_code": _as_text(row["module_code"]),
        "difficulty_level": _as_text(row["difficulty_level"]),
    }


def _build_dialogue_text(conversation: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in conversation or []:
        if not isinstance(item, dict):
            continue
        role = _as_text(item.get("role")).lower()
        content = _as_text(item.get("content") or item.get("text"))
        if not content:
            continue
        if role == "user":
            lines.append(f"学员：{content}")
        elif role == "assistant":
            lines.append(f"系统：{content}")
        else:
            lines.append(content)
    return "\n".join(lines).strip()


def _normalize_text_list(items: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        text = _as_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _join_text_lines(items: list[Any]) -> str:
    return "\n".join(_normalize_text_list(items))


def _level_by_score(score: float) -> str:
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 60:
        return "合格"
    return "待加强"


def _build_practice_mentor_fallback(
    *,
    overall_score: float,
    strengths: list[str],
    improvements: list[str],
    coach_summary: str,
) -> str:
    summary = _as_text(coach_summary)
    if summary:
        return summary

    top_strength = strengths[0] if strengths else ""
    top_improvement = improvements[0] if improvements else ""
    if overall_score >= 80:
        if top_strength:
            return f"这轮你在“{top_strength}”上已经有明显感觉，继续保持这个节奏，下一轮把顾客顾虑追问得再深一点，会更稳。"
        return "这轮整体表现不错，继续保持现在的表达节奏，下一轮把顾客顾虑再追问深一点，成交推进会更自然。"
    if top_improvement:
        return f"这轮先别急着求快，重点把“{top_improvement}”补扎实，先练到回应自然、价值说透，下一轮就会明显更顺。"
    if top_strength:
        return f"这轮已经有“{top_strength}”这个基础，接下来把顾客顾虑回应得更具体一点，你会越来越稳。"
    return "这轮先稳住节奏，围绕顾客顾虑多做追问和价值拆解，下一轮会比这次更顺。"


def _mock_evaluate_data(
    *,
    evaluation_id: str,
    session_id: str,
    scene_code: str,
    employee_id: str,
) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation_id,
        "session_id": session_id,
        "scene_code": scene_code,
        "employee_id": employee_id,
        "overall_score": 84.0,
        "score_breakdown": {
            "opening": 82,
            "probing": 78,
            "recommendation": 86,
            "closing": 74,
        },
        "strengths": ["产品卖点表达清晰", "语气自然不过度施压"],
        "improvements": ["异议处理追问不足", "成交收口动作偏弱"],
        "coach_summary": "本轮表现达到合格以上，建议下一轮重点训练异议处理与成交收口。",
        "mentor_sentence": "你这轮异议处理还有提升空间，顾客说'太贵了'的时候别急着降价，先试着拆解价值——好东西贵一点，顾客反而更放心。",
        "workflow_mode": "mock",
    }


def _mock_ability_data(
    *,
    update_id: str,
    session_id: str,
    evaluation_id: str,
    employee_id: str,
    score: float,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "session_id": session_id,
        "evaluation_id": evaluation_id,
        "employee_id": employee_id,
        "ability_snapshot": {
            "product_knowledge": 82,
            "sales_expression": 85,
            "objection_handling": 76,
            "closing_skill": 74,
        },
        "updated_tags": ["产品讲解较稳定", "异议处理待加强", "成交收口待加强"],
        "ability_comment": f"基于本轮评分 {round(score, 1)} 分，建议继续强化异议处理与成交收口。",
        "workflow_mode": "mock",
    }


def _load_eval_payload(conn, evaluation_id: str) -> dict[str, Any]:
    eid = _as_text(evaluation_id)
    if not eid:
        return {}
    row = conn.execute(
        "SELECT payload_json FROM practice_eval_records WHERE evaluation_id = ?",
        (eid,),
    ).fetchone()
    if not row:
        return {}
    return _safe_json_loads(row["payload_json"])


def _load_latest_ability_snapshot(conn, employee_id: str) -> dict[str, float]:
    eid = _as_text(employee_id)
    if not eid:
        return {}
    row = conn.execute(
        """
        SELECT ability_snapshot_json
        FROM ability_update_records
        WHERE employee_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (eid,),
    ).fetchone()
    if not row:
        return {}
    obj = _safe_json_loads(row["ability_snapshot_json"])
    out: dict[str, float] = {}
    for k in ("product_knowledge", "sales_expression", "objection_handling", "closing_skill"):
        if k in obj:
            out[k] = _to_float(obj.get(k), 0.0)
    return out


def _normalize_percent_score(value: Any) -> float:
    score = _to_float(value, 0.0)
    if 0 < score <= 5:
        score *= 20.0
    elif 0 < score <= 10:
        score *= 10.0
    return round(max(0.0, min(100.0, score)), 1)


def _load_performance_linkage(employee_id: str) -> dict[str, Any]:
    target_id = _as_text(employee_id)
    if not target_id:
        return {}
    try:
        with SessionLocal() as db:
            bundle = build_employee_performance_bundle(db, target_id)
            linkage = bundle.get("performance_linkage")
            return linkage if isinstance(linkage, dict) else {}
    except Exception:
        return {}


@router.post("/chat")
def practice_chat(
    body: PracticeChatRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    employee_id = str(current_user.get("user_id") or "")
    scene_code = _as_text(body.scene_code) or "jewelry_recommendation"
    module_code = _as_text(body.module_code)
    difficulty_level = _as_text(body.difficulty_level) or "standard"
    session_id = _as_text(body.session_id) or make_request_id("ps")
    action = _as_text(body.action).lower() or "send"
    if action not in {"send", "end", "resume"}:
        action = "send"
    trainee_role = _as_text(body.trainee_role) or "珠宝机构导购"
    input_message = _as_text(body.user_message)
    end_requested = action == "end" or _is_end_command(input_message)
    if end_requested and not input_message:
        input_message = "结束陪练"
    _log.info("practice_chat start user_id=%s session_id=%s scene=%s action=%s",
              employee_id, session_id, scene_code, action)

    dify_error = ""
    recovered = False
    allow_mock_fallback = bool(
        app_config.DIFY_STAGE4B_FORCE_MOCK
        or app_config.DIFY_ALLOW_FALLBACK_TO_MOCK
        or app_config.DIFY_PRACTICE1_ALLOW_FAST_FALLBACK
    )

    latest_state: dict[str, Any] = {}
    with get_conn() as conn:
        upsert_employee_profile(
            conn,
            employee_id=employee_id,
            employee_name="",
            position="",
            store_id="",
            role=str(current_user.get("role") or ""),
            source="practice_chat",
        )
        if not bool(body.force_new_session):
            latest_state = _load_latest_chat_state(conn, session_id, employee_id)

    latest_session_state = (
        latest_state.get("session_state") if isinstance(latest_state.get("session_state"), dict) else {}
    )
    state_scene_code = _as_text(latest_session_state.get("scene_code")) or _as_text(
        latest_state.get("scene_code")
    )
    state_module_code = _as_text(latest_session_state.get("module_code")) or _as_text(
        latest_state.get("module_code")
    )
    state_difficulty_level = _as_text(latest_session_state.get("difficulty_level")) or _as_text(
        latest_state.get("difficulty_level")
    )
    current_scene_code = scene_code or state_scene_code or "jewelry_recommendation"
    current_module_code = module_code or state_module_code
    current_difficulty_level = difficulty_level or state_difficulty_level or "standard"
    conversation = latest_state.get("conversation") if isinstance(latest_state.get("conversation"), list) else []
    if not isinstance(conversation, list):
        conversation = []
    state_round_count = _to_int(latest_session_state.get("round_count"), 0)
    state_end_flag = _to_int(latest_session_state.get("end_flag"), 0)
    state_risk_hit_count = _to_int(latest_session_state.get("risk_hit_count"), 0)
    state_stage = _as_text(latest_session_state.get("stage")) or "init"
    state_last_reply = _as_text(latest_session_state.get("last_customer_reply"))
    state_conversation_id = _as_text(latest_session_state.get("conversation_id"))
    request_conversation_id = _as_text(body.conversation_id)
    current_conversation_id = request_conversation_id or state_conversation_id
    recovered = bool(latest_state) and not request_conversation_id and bool(state_conversation_id)

    if action == "resume":
        latest_turn_feedback = _latest_turn_feedback_from_conversation(conversation)
        data = {
            "session_id": session_id,
            "scene_code": current_scene_code,
            "module_code": current_module_code,
            "difficulty_level": current_difficulty_level,
            "employee_id": employee_id,
            "assistant_reply": state_last_reply,
            "suggested_response": "",
            "next_focus": [],
            "conversation": conversation,
            "conversation_id": current_conversation_id,
            "round_count": state_round_count,
            "stage": state_stage,
            "end_flag": state_end_flag,
            "risk_hit_count": state_risk_hit_count,
            "recovered": recovered,
            "workflow_mode": _as_text(latest_session_state.get("workflow_mode")) or "mock",
            "workflow_reason": _as_text(latest_session_state.get("workflow_reason")),
            "dify_error": "",
            "db_record_id": int(latest_state.get("db_record_id") or 0),
            "auto_chain_hint": bool(state_end_flag == 1),
        }
        if latest_turn_feedback:
            data["turn_feedback"] = latest_turn_feedback
        _log.info("practice_chat resume success user_id=%s session_id=%s",
                  employee_id, session_id)
        return success_response(
            data,
            workflow_code="practice1_resume",
            mock=data["workflow_mode"] != "dify",
        )

    if state_end_flag == 1 and not bool(body.force_new_session) and not end_requested:
        latest_turn_feedback = _latest_turn_feedback_from_conversation(conversation)
        data = {
            "session_id": session_id,
            "scene_code": current_scene_code,
            "module_code": current_module_code,
            "difficulty_level": current_difficulty_level,
            "employee_id": employee_id,
            "assistant_reply": "本场陪练已结束，请点击“开始新会话”或切换场景再继续。",
            "suggested_response": "",
            "next_focus": ["结束后可直接生成评分并更新能力档案。"],
            "conversation": conversation,
            "conversation_id": current_conversation_id,
            "round_count": state_round_count,
            "stage": "finished",
            "end_flag": 1,
            "risk_hit_count": state_risk_hit_count,
            "recovered": recovered,
            "workflow_mode": _as_text(latest_session_state.get("workflow_mode")) or "mock",
            "workflow_reason": _as_text(latest_session_state.get("workflow_reason")),
            "dify_error": "",
            "db_record_id": int(latest_state.get("db_record_id") or 0),
            "auto_chain_hint": True,
        }
        if latest_turn_feedback:
            data["turn_feedback"] = latest_turn_feedback
        _log.info("practice_chat finished success user_id=%s session_id=%s",
                  employee_id, session_id)
        return success_response(
            data,
            workflow_code="practice1_finished",
            mock=data["workflow_mode"] != "dify",
        )

    user_message = input_message or ("开始陪练" if state_round_count <= 0 else "继续")
    next_round_count = state_round_count + 1

    call = run_practice1_chat(
        user_id=employee_id,
        query=user_message,
        module_code=current_module_code,
        scenario_type_select=_scene_code_to_practice1_type(current_scene_code),
        scenario_type_custom="",
        difficulty=_difficulty_to_practice1(current_difficulty_level),
        trainee_role=trainee_role,
        conversation_id=current_conversation_id,
    )
    use_dify = bool(call.get("ok"))
    workflow_mode = "dify" if use_dify else "mock"
    workflow_reason = ""
    if use_dify:
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        reply_raw = _as_text(wf_data.get("assistant_reply"))
        next_conversation_id = _as_text(wf_data.get("conversation_id")) or current_conversation_id
        workflow_reason = ""
        if not reply_raw:
            use_dify = False
            call = {
                "ok": False,
                "reason": "empty_answer",
                "error": "assistant_reply_empty",
                "raw": call.get("raw") if isinstance(call, dict) else {},
            }
    else:
        if not allow_mock_fallback:
            _log.warning("practice_chat dify failed reason=%s",
                         _as_text(call.get("reason")) if isinstance(call, dict) else "unknown")
            return dify_failure_response(
                workflow_code="practice1",
                route_path="/api/practice/chat",
                call=call if isinstance(call, dict) else None,
            )
        _log.info("practice_chat using mock user_id=%s session_id=%s",
                  employee_id, session_id)
        reply_raw = _mock_practice1_reply(
            scenario_text=_scene_code_to_practice1_type(current_scene_code),
            round_count=next_round_count,
            end_requested=end_requested,
        )
        next_conversation_id = current_conversation_id
        workflow_reason = _as_text(call.get("reason")) or "fallback_to_mock"
        dify_error = _as_text(call.get("error"))

    assistant_reply, reply_has_end_flag = _strip_end_flag(reply_raw)
    next_end_flag = 1 if (end_requested or reply_has_end_flag == 1) else 0
    next_stage = "finished" if next_end_flag == 1 else ("opening" if next_round_count <= 1 else "in_progress")

    risk_keywords = (
        "保证升值",
        "一定升值",
        "绝对",
        "全国最低价",
        "最低价",
        "假一赔十",
        "百分百",
    )
    risk_hit_this_turn = 1 if any(k in user_message for k in risk_keywords) else 0
    next_risk_hit_count = state_risk_hit_count + risk_hit_this_turn

    # Keep chat deterministic: prefer feedback embedded by the main Dify chat.
    # If it is missing, the local fallback below is used instead of blocking chat
    # on a secondary workflow.
    raw_feedback = _extract_turn_feedback_from_call(call)
    turn_feedback = _ensure_turn_feedback(
        scene_code=current_scene_code,
        user_message=user_message,
        assistant_reply=assistant_reply,
        raw_feedback=raw_feedback,
        risk_hit_this_turn=risk_hit_this_turn,
    )

    next_conversation = list(conversation)
    next_conversation.append(
        {
            "role": "user",
            "content": user_message,
            "round": next_round_count,
        }
    )
    next_conversation.append(
        {
            "role": "assistant",
            "content": assistant_reply,
            "round": next_round_count,
            "turn_feedback": turn_feedback,
        }
    )

    next_focus: list[str] = []
    if next_end_flag == 1:
        next_focus = ["本场已结束，可自动进入实战2评分与实战3能力更新。"]
    elif next_round_count <= 2:
        next_focus = ["继续围绕顾客异议追问，避免一次性输出过多信息。"]
    else:
        next_focus = ["推进到成交收口或明确下一步动作。"]

    session_state = {
        "conversation_id": next_conversation_id,
        "scene_code": current_scene_code,
        "module_code": current_module_code,
        "difficulty_level": current_difficulty_level,
        "stage": next_stage,
        "round_count": next_round_count,
        "end_flag": next_end_flag,
        "risk_hit_count": next_risk_hit_count,
        "last_customer_reply": assistant_reply,
        "workflow_mode": workflow_mode,
        "workflow_reason": workflow_reason,
    }
    data = {
        "session_id": session_id,
        "scene_code": current_scene_code,
        "module_code": current_module_code,
        "difficulty_level": current_difficulty_level,
        "employee_id": employee_id,
        "assistant_reply": assistant_reply,
        "suggested_response": "",
        "next_focus": next_focus,
        "conversation": next_conversation,
        "conversation_id": next_conversation_id,
        "round_count": next_round_count,
        "stage": next_stage,
        "end_flag": next_end_flag,
        "risk_hit_count": next_risk_hit_count,
        "recovered": recovered,
        "workflow_mode": workflow_mode,
        "workflow_reason": workflow_reason,
        "dify_error": dify_error,
        "auto_chain_hint": bool(next_end_flag == 1),
        "session_state": session_state,
        "turn_feedback": turn_feedback,
    }
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO practice_records (
                practice_id, user_id, session_id, employee_id, scene_code, module_code,
                difficulty_level, user_message, assistant_reply, suggested_response,
                next_focus_json, conversation_json, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                employee_id,
                session_id,
                employee_id,
                current_scene_code,
                current_module_code,
                current_difficulty_level,
                user_message,
                data["assistant_reply"],
                data["suggested_response"],
                json_text(data["next_focus"]),
                json_text(data["conversation"]),
                json_text(data),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        # Write score_branch and cycle_day_index to practice_eval_records when evaluate is called
        data["score_branch"] = body.score_branch
        data["cycle_day_index"] = body.cycle_day_index
        data["db_record_id"] = int(row.lastrowid or 0)
    _log.info("practice_chat success user_id=%s session_id=%s use_dify=%s round=%s",
              employee_id, session_id, data.get("workflow_mode"), next_round_count)
    return success_response(
        data,
        workflow_code="practice1" if data.get("workflow_mode") == "dify" else "practice1_mock",
        mock=data.get("workflow_mode") != "dify",
    )


@router.post("/evaluate")
def practice_evaluate(
    body: PracticeEvaluateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    session_id = (body.session_id or "").strip() or make_request_id("ps")
    scene_code = (body.scene_code or "").strip() or "jewelry_recommendation"
    employee_id = str(current_user.get("user_id") or "")
    evaluation_id = make_request_id("pe")
    module_code = _as_text(body.module_code)

    if not module_code:
        with get_conn() as conn:
            latest_chat_state = _load_latest_chat_state(conn, session_id, employee_id)
        module_code = _as_text(latest_chat_state.get("module_code"))

    _log.info("practice_evaluate start user_id=%s session_id=%s scene=%s",
              employee_id, session_id, scene_code)

    dialogue_text = _build_dialogue_text(body.conversation)
    if not dialogue_text:
        dialogue_text = "学员：顾客担心价格偏高。\n系统：先确认预算范围，再做价值拆解。"

    performance_linkage = _load_performance_linkage(employee_id)
    primary_gap = performance_linkage.get("primary_gap") if isinstance(performance_linkage.get("primary_gap"), dict) else {}
    target_focus = (
        _as_text(primary_gap.get("training_topic"))
        or "异议处理与成交收口"
    )
    target_metric = _as_text(primary_gap.get("metric_label")) or "核心指标"
    performance_gap_summary = _as_text(performance_linkage.get("gap_metrics_text"))
    recommended_action = _as_text(primary_gap.get("action_title")) or target_focus

    call = run_practice2_workflow(
        user_id=employee_id,
        practice_id=session_id,
        trainee_name=str(current_user.get("username") or ""),
        scenario_type=_scene_code_to_practice2_type(scene_code),
        target_focus=target_focus,
        dialogue_text=dialogue_text,
        performance_gap_summary=performance_gap_summary,
        target_metric=target_metric,
        recommended_action=recommended_action,
    )

    use_dify = bool(call.get("ok"))
    if use_dify:
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        eval_obj = wf_data.get("evaluation_result") if isinstance(wf_data.get("evaluation_result"), dict) else {}
        overall_score = round(_to_float(wf_data.get("overall_score"), 0.0), 1)
        if overall_score <= 0:
            overall_score = round(_to_float(eval_obj.get("overall_score"), 0.0), 1)
        score_breakdown = {
            "opening": _to_int(wf_data.get("need_insight_score"), 0),
            "probing": _to_int(wf_data.get("objection_handling_score"), 0),
            "recommendation": _to_int(wf_data.get("product_accuracy_score"), 0),
            "closing": _to_int(wf_data.get("closing_push_score"), 0),
        }
        if all(v <= 0 for v in score_breakdown.values()) and isinstance(
            eval_obj.get("dimension_scores"), dict
        ):
            dims = eval_obj.get("dimension_scores") if isinstance(eval_obj.get("dimension_scores"), dict) else {}
            score_breakdown = {
                "opening": _to_int(dims.get("需求洞察"), 0),
                "probing": _to_int(dims.get("异议处理"), 0),
                "recommendation": _to_int(dims.get("专业准确"), 0),
                "closing": _to_int(dims.get("成交推进"), 0),
            }
            if overall_score <= 0 and any(v > 0 for v in score_breakdown.values()):
                overall_score = round(sum(score_breakdown.values()) / 4.0, 1)
        strengths = list(wf_data.get("highlights") or [])
        improvements = list(wf_data.get("problem_points") or [])
        if _as_text(wf_data.get("improvement_advice")):
            improvements.append(_as_text(wf_data.get("improvement_advice")))
        coach_summary = (
            _as_text(wf_data.get("concise_feedback"))
            or _as_text(wf_data.get("followup_training"))
            or f"本轮评分 {overall_score} 分，建议继续强化薄弱维度。"
        )
        data = {
            "evaluation_id": evaluation_id,
            "session_id": session_id,
            "scene_code": scene_code,
            "module_code": module_code,
            "employee_id": employee_id,
            "overall_score": overall_score,
            "score_breakdown": score_breakdown,
            "strengths": strengths,
            "improvements": improvements,
            "coach_summary": coach_summary,
            "evaluation_status": _as_text(wf_data.get("evaluation_status")) or "success",
            "level": _as_text(wf_data.get("level")) or _level_by_score(overall_score),
            "risk_level": _as_text(wf_data.get("risk_level")) or "medium",
            "weak_dimension": _as_text(wf_data.get("weak_dimension")),
            "target_metric": target_metric,
            "target_focus": target_focus,
            "performance_gap_summary": performance_gap_summary,
            "workflow_mode": "dify",
            "workflow_reason": "",
            "mentor_sentence": _as_text(wf_data.get("mentor_sentence")),
        }

    if not use_dify and not (
        app_config.DIFY_STAGE4B_FORCE_MOCK or app_config.DIFY_ALLOW_FALLBACK_TO_MOCK
    ):
        _log.warning("practice_evaluate dify failed reason=%s",
                     _as_text(call.get("reason")) if isinstance(call, dict) else "unknown")
        return dify_failure_response(
            workflow_code="practice2",
            route_path="/api/practice/evaluate",
            call=call if isinstance(call, dict) else None,
        )
    if not use_dify:
        _log.info("practice_evaluate using mock user_id=%s session_id=%s",
                  employee_id, session_id)
        data = _mock_evaluate_data(
            evaluation_id=evaluation_id,
            session_id=session_id,
            scene_code=scene_code,
            employee_id=employee_id,
        )
        data["target_metric"] = target_metric
        data["target_focus"] = target_focus
        data["performance_gap_summary"] = performance_gap_summary
        data["module_code"] = module_code
        data["workflow_reason"] = (
            call.get("reason") if isinstance(call, dict) and call.get("reason") else "fallback_to_mock"
        )
        data["dify_error"] = _as_text(call.get("error")) if isinstance(call, dict) else ""

    # Resolve current training cycle_id and stage_no for this user
    current_cycle_id = ""
    current_stage_no = 0
    with get_conn() as _c:
        _row = _c.execute(
            "SELECT training_cycle_id FROM users WHERE CAST(id AS TEXT) = ? OR user_id = ?",
            (employee_id, employee_id),
        ).fetchone()
        if _row and _row["training_cycle_id"]:
            current_cycle_id = str(_row["training_cycle_id"])
            _cyc = _c.execute(
                "SELECT stage_no FROM training_cycles WHERE cycle_id = ?",
                (current_cycle_id,),
            ).fetchone()
            if _cyc:
                current_stage_no = int(_cyc["stage_no"] or 0)

    with get_conn() as conn:
        upsert_employee_profile(
            conn,
            employee_id=employee_id,
            employee_name="",
            position="",
            store_id="",
            role=str(current_user.get("role") or ""),
            source="practice_evaluate",
        )
        row = conn.execute(
            """
            INSERT INTO practice_eval_records (
                practice_id, user_id, evaluation_id, session_id, employee_id, scene_code,
                module_code, overall_score, score_breakdown_json, strengths_json,
                improvements_json, coach_summary, payload_json, created_at, score_branch, cycle_day_index,
                cycle_id, stage_no
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                employee_id,
                data["evaluation_id"],
                session_id,
                employee_id,
                scene_code,
                module_code,
                float(data.get("overall_score") or 0.0),
                json_text(data.get("score_breakdown") or {}),
                json_text(data.get("strengths") or []),
                json_text(data.get("improvements") or []),
                _as_text(data.get("coach_summary")),
                json_text({**data, "dialogue_text": dialogue_text}),
                datetime.now(timezone.utc).isoformat(),
                body.score_branch,
                body.cycle_day_index,
                current_cycle_id,
                current_stage_no,
            ),
        )
        data["db_record_id"] = int(row.lastrowid or 0)
    _log.info("practice_evaluate success user_id=%s session_id=%s use_dify=%s score=%s",
              employee_id, session_id, data.get("workflow_mode"), data.get("overall_score"))
    return success_response(
        data,
        workflow_code="practice2" if use_dify else "practice2_mock",
        mock=not use_dify,
    )


@router.post("/mentor-feedback")
def practice_mentor_feedback(
    body: PracticeMentorFeedbackRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    session_id = _as_text(body.session_id) or make_request_id("ps")
    scene_code = _as_text(body.scene_code) or "jewelry_recommendation"
    module_code = _as_text(body.module_code)
    employee_id = str(current_user.get("user_id") or "")
    trainee_name = _as_text(current_user.get("username"))
    overall_score = round(_to_float(body.overall_score, 0.0), 1)
    strengths = _normalize_text_list(body.strengths)
    improvements = _normalize_text_list(body.improvements)
    coach_summary = _as_text(body.coach_summary)

    dialogue_text = _build_dialogue_text(body.conversation)
    if not dialogue_text:
        dialogue_text = "学员：顾客担心价格偏高。\n系统：先确认预算范围，再做价值拆解。"

    fallback_sentence = _build_practice_mentor_fallback(
        overall_score=overall_score,
        strengths=strengths,
        improvements=improvements,
        coach_summary=coach_summary,
    )

    _log.info(
        "practice_mentor_feedback start user_id=%s session_id=%s scene=%s score=%s",
        employee_id,
        session_id,
        scene_code,
        overall_score,
    )

    call = run_practice_mentor_workflow(
        user_id=employee_id,
        practice_id=session_id,
        trainee_name=trainee_name,
        scenario_type=_scene_code_to_practice2_type(scene_code),
        dialogue_text=dialogue_text,
        overall_score=str(overall_score or ""),
        highlights=_join_text_lines(strengths),
        problem_points=_join_text_lines(improvements),
        improvement_advice=coach_summary or (improvements[0] if improvements else ""),
    )

    use_dify = bool(call.get("ok"))
    if use_dify:
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        mentor_sentence = _as_text(wf_data.get("mentor_sentence"))
        if mentor_sentence:
            data = {
                "session_id": session_id,
                "scene_code": scene_code,
                "module_code": module_code,
                "employee_id": employee_id,
                "overall_score": overall_score,
                "mentor_sentence": mentor_sentence,
                "coach_summary": coach_summary,
                "workflow_mode": "dify",
                "workflow_reason": "",
                "fallback_used": False,
            }
            _log.info(
                "practice_mentor_feedback success user_id=%s session_id=%s use_dify=%s",
                employee_id,
                session_id,
                True,
            )
            return success_response(
                data,
                workflow_code="practice_mentor",
                mock=False,
                extra_meta={"fallback_used": False},
            )
        use_dify = False
        call = {
            "ok": False,
            "reason": "empty_workflow_output",
            "error": "mentor_sentence_empty",
            "raw": call.get("raw") if isinstance(call, dict) else {},
        }

    data = {
        "session_id": session_id,
        "scene_code": scene_code,
        "module_code": module_code,
        "employee_id": employee_id,
        "overall_score": overall_score,
        "mentor_sentence": fallback_sentence,
        "coach_summary": coach_summary,
        "workflow_mode": "mock",
        "workflow_reason": (
            call.get("reason") if isinstance(call, dict) and call.get("reason") else "coach_summary_fallback"
        ),
        "fallback_used": True,
        "dify_error": _as_text(call.get("error")) if isinstance(call, dict) else "",
    }
    _log.info(
        "practice_mentor_feedback fallback user_id=%s session_id=%s reason=%s",
        employee_id,
        session_id,
        data["workflow_reason"],
    )
    return success_response(
        data,
        workflow_code="practice_mentor_fallback",
        mock=True,
        extra_meta={"fallback_used": True},
    )


@router.post("/update-ability")
def practice_update_ability(
    body: PracticeAbilityUpdateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    jwt_user_id = str(current_user.get("user_id") or "")
    actor_role = str(current_user.get("role") or "").strip().lower()
    is_manager = actor_role in ("admin", "store_manager")
    body_employee_id = (body.employee_id or "").strip()
    if body_employee_id and body_employee_id != jwt_user_id and not is_manager:
        raise HTTPException(status_code=403, detail="只能更新自己的能力档案")
    employee_id = body_employee_id or jwt_user_id
    session_id = (body.session_id or "").strip() or ""
    evaluation_id = (body.evaluation_id or "").strip() or ""
    update_id = make_request_id("pa")

    _log.info("practice_update_ability start user_id=%s session_id=%s evaluation_id=%s",
              employee_id, session_id, evaluation_id)

    with get_conn() as conn:
        eval_payload = _load_eval_payload(conn, evaluation_id)
        latest_snapshot = _load_latest_ability_snapshot(conn, employee_id)
        latest_chat_state = _load_latest_chat_state(conn, session_id, employee_id) if session_id else {}

    module_code = _as_text(body.module_code) or _as_text(eval_payload.get("module_code")) or _as_text(
        latest_chat_state.get("module_code")
    )

    scene_code = _as_text(eval_payload.get("scene_code")) or "jewelry_recommendation"
    overall_score = _to_float(body.score, -1.0)
    if overall_score < 0:
        overall_score = _to_float(eval_payload.get("overall_score"), 84.0)

    score_breakdown = eval_payload.get("score_breakdown")
    if not isinstance(score_breakdown, dict):
        score_breakdown = {}

    conversation_text = _as_text(eval_payload.get("dialogue_text"))
    evaluator_feedback = _as_text(eval_payload.get("coach_summary")) or "建议继续提升应变表达与成交推进。"
    weak_dimension = _map_weak_dimension_for_practice3(
        _as_text(eval_payload.get("weak_dimension"))
    )
    risk_level = _as_text(eval_payload.get("risk_level")).lower()
    risk_tags_json = json_text(eval_payload.get("risk_tags") or [])
    followup_training = _as_text(eval_payload.get("followup_training"))
    concise_feedback = _as_text(eval_payload.get("concise_feedback"))
    scenario_type = _scene_code_to_practice3_code(scene_code)
    current_cycle_id = _as_text(eval_payload.get("cycle_id"))
    current_stage_no = _to_int(eval_payload.get("stage_no"), 0)
    evaluation_result_json = json.dumps(eval_payload or {}, ensure_ascii=False)

    call = run_practice3_workflow(
        user_id=employee_id,
        practice_id=session_id,
        module_code=module_code,
        module_name="",
        cycle_id=current_cycle_id,
        stage_no=current_stage_no,
        scenario_type=scenario_type,
        evaluation_result=evaluation_result_json,
        final_score=overall_score,
        evaluator_feedback=evaluator_feedback,
        risk_level=risk_level,
        risk_tags_json=risk_tags_json,
        weak_dimension=weak_dimension,
        followup_training=followup_training,
        concise_feedback=concise_feedback,
        practice_turns=_to_int(eval_payload.get("sample_round_count"), 0),
        conversation_text=conversation_text,
        current_product_knowledge_score=_to_float(
            latest_snapshot.get("product_knowledge"),
            _to_float(score_breakdown.get("recommendation"), 60.0),
        ),
        current_compliance_score=_to_float(
            latest_snapshot.get("closing_skill"),
            _to_float(score_breakdown.get("closing"), 60.0),
        ),
        current_sales_communication_score=_to_float(
            latest_snapshot.get("sales_expression"),
            _to_float(score_breakdown.get("opening"), 60.0),
        ),
        current_response_score=_to_float(
            latest_snapshot.get("objection_handling"),
            _to_float(score_breakdown.get("probing"), 60.0),
        ),
    )

    use_dify = bool(call.get("ok"))
    if use_dify:
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        ability_snapshot = {
            "product_knowledge": _normalize_percent_score(wf_data.get("product_knowledge_score")),
            "sales_expression": _normalize_percent_score(wf_data.get("sales_communication_score")),
            "objection_handling": _normalize_percent_score(wf_data.get("response_score")),
            "closing_skill": _normalize_percent_score(wf_data.get("compliance_score")),
        }
        updated_tags = [
            t
            for t in [
                _as_text(wf_data.get("risk_level_label")),
                _as_text(wf_data.get("focus_dimension_label")),
                _as_text(wf_data.get("scenario_name")),
            ]
            if t
        ]
        ability_comment = (
            _as_text(wf_data.get("update_summary"))
            or _as_text(wf_data.get("next_training_direction"))
            or f"本轮能力已更新，综合评分 {_normalize_percent_score(wf_data.get('overall_score') or overall_score)} 分。"
        )
        if _as_text(wf_data.get("manager_tip")):
            ability_comment = ability_comment + " " + _as_text(wf_data.get("manager_tip"))
        data = {
            "update_id": update_id,
            "session_id": session_id,
            "evaluation_id": evaluation_id,
            "employee_id": employee_id,
            "module_code": module_code,
            "ability_snapshot": ability_snapshot,
            "updated_tags": updated_tags,
            "ability_comment": ability_comment,
            "workflow_mode": "dify",
            "workflow_reason": "",
            "risk_level": _as_text(wf_data.get("risk_level")),
            "focus_dimension": _as_text(wf_data.get("focus_dimension")),
            "next_training_direction": _as_text(wf_data.get("next_training_direction")),
        }
        has_structured_update = any(
            [
                bool(wf_data.get("ability_update_json")),
                bool(data["updated_tags"]),
                bool(_as_text(wf_data.get("update_summary"))),
                bool(_as_text(wf_data.get("manager_tip"))),
                bool(_as_text(wf_data.get("focus_dimension"))),
                bool(_as_text(wf_data.get("risk_level"))),
            ]
        )
        if not any(ability_snapshot.values()) and not has_structured_update:
            use_dify = False
            call = {
                "ok": False,
                "reason": "empty_workflow_output",
                "error": "ability_snapshot_empty",
                "raw": call.get("raw") if isinstance(call, dict) else {},
            }

    if not use_dify and not (
        app_config.DIFY_STAGE4B_FORCE_MOCK or app_config.DIFY_ALLOW_FALLBACK_TO_MOCK
    ):
        _log.warning("practice_update_ability dify failed reason=%s",
                     _as_text(call.get("reason")) if isinstance(call, dict) else "unknown")
        return dify_failure_response(
            workflow_code="practice3",
            route_path="/api/practice/update-ability",
            call=call if isinstance(call, dict) else None,
        )
    if not use_dify:
        _log.info("practice_update_ability using mock user_id=%s employee_id=%s",
                  employee_id, employee_id)
        data = _mock_ability_data(
            update_id=update_id,
            session_id=session_id,
            evaluation_id=evaluation_id,
            employee_id=employee_id,
            score=overall_score,
        )
        data["module_code"] = module_code
        data["workflow_reason"] = (
            call.get("reason") if isinstance(call, dict) and call.get("reason") else "fallback_to_mock"
        )
        data["dify_error"] = _as_text(call.get("error")) if isinstance(call, dict) else ""

    with get_conn() as conn:
        upsert_employee_profile(
            conn,
            employee_id=employee_id,
            employee_name="",
            position="",
            store_id="",
            role=str(current_user.get("role") or ""),
            source="practice_update_ability",
        )
        row = conn.execute(
            """
            INSERT INTO ability_update_records (
                practice_id, user_id, update_id, session_id, evaluation_id, employee_id,
                module_code, score, overall_score, ability_snapshot_json, updated_tags_json,
                ability_comment, payload_json, created_at, score_branch, cycle_day_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                employee_id,
                data["update_id"],
                data["session_id"],
                data["evaluation_id"],
                employee_id,
                module_code,
                float(overall_score),
                float(overall_score),
                json_text(data.get("ability_snapshot") or {}),
                json_text(data.get("updated_tags") or []),
                _as_text(data.get("ability_comment")),
                json_text(data),
                datetime.now(timezone.utc).isoformat(),
                body.score_branch,
                body.cycle_day_index,
            ),
        )
        data["db_record_id"] = int(row.lastrowid or 0)
    _log.info("practice_update_ability success user_id=%s employee_id=%s use_dify=%s score=%s",
              employee_id, employee_id, data.get("workflow_mode"), overall_score)
    return success_response(
        data,
        workflow_code="practice3" if use_dify else "practice3_mock",
        mock=not use_dify,
    )
