from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

from api_response import make_request_id
from database import SessionLocal
from db_stage3 import get_conn, json_text, upsert_employee_profile
from dify_client import extract_minimal_text_fields
from dify_stage4a import run_assistant1_workflow, run_assistant2_workflow
from dify_stage4b import run_assistant_chat
from evo.retriever import build_memory_block, inject_memory_block, retrieve_semantic_memories
from schemas import AssistantReplyResponse

_log = logging.getLogger("jewelry_qipei.assistant_service")

_ASSISTANT1_REPLY_ALIAS_MAP = {
    "reply_script": (
        "reply_script",
        "reply_text",
        "content",
        "final_answer",
        "text",
        "answer",
    ),
    "followup_question": (
        "followup_question",
        "next_question",
        "suggested_question",
    ),
}


class AssistantReplyRunResult(TypedDict):
    response: AssistantReplyResponse
    use_dify: bool
    workflow_reason: str
    dify_error: str
    reply_compliance_tag: str
    matched_knowledge: str


_FALLBACK_REPLY_SCRIPT = (
    "可以先认可客户关注点，再从材质工艺、佩戴体验和售后保障三点回应，"
    "最后给出可现场验证的信息，避免只谈价格。"
)
_FALLBACK_FOLLOWUP_QUESTION = "您更在意日常佩戴效果，还是保值与纪念意义？"

_COACH_TIP_BY_KNOWLEDGE = {
    "价格": "先认同顾虑，再拆解材质工艺和售后价值，不要直接降价。",
    "工艺": "先讲工艺和佩戴体验，再补一句可现场验证的信息。",
    "证书": "先把证书和标准讲清，再补充品牌与售后保障。",
    "材质": "先说材质依据和佩戴差异，避免只给笼统结论。",
}


def _normalize_voice_advice(text: str) -> str:
    advice = " ".join(str(text or "").strip().split())
    if not advice:
        return ""

    for sep in ("。", "！", "？", "!", "?", ";", "；"):
        idx = advice.find(sep)
        if idx >= 0:
            advice = advice[: idx + 1]
            break

    advice = advice.strip("，,;；。！？!?\n\r\t ")
    if not advice:
        return ""

    max_len = 36
    if len(advice) > max_len - 1:
        advice = advice[: max_len - 1].rstrip("，,;；。！？!? ")

    if advice and advice[-1] not in "。！？!?":
        advice += "。"
    return advice


def _is_valid_reply_script(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if len(t) < 8:
        return False
    if t.lower() in {"other", "unknown", "n/a"}:
        return False
    if t in {"其他", "无", "暂无"}:
        return False
    return True


_TURN_FEEDBACK_FIELDS = (
    "intent_label",
    "intent_reason",
    "customer_state",
    "mentor_comment",
    "next_action",
    "next_question",
    "voice_advice",
)


def _as_text(val: Any) -> str:
    if isinstance(val, str):
        return val.strip()
    if val is None:
        return ""
    return str(val).strip()


def _parse_json_object(val: Any) -> dict[str, Any]:
    if isinstance(val, dict):
        return val
    text = _as_text(val)
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


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


def _build_assistant_turn_feedback_fallback(
    *,
    scene_input: str,
    reply_script: str,
) -> dict[str, Any]:
    user_text = _as_text(scene_input)
    reply_text = _as_text(reply_script)
    combined = f"{reply_text}\n{user_text}"
    intent_label = "需求确认"
    intent_reason = "顾客仍在确认信息，尚未形成明确购买决策。"
    customer_state = "谨慎"
    mentor_comment = "先别急着堆卖点，先把顾客最卡的顾虑问实。"
    next_action = "缩小顾客当前最在意的一个阻力点，再顺着回应。"
    next_question = "您现在最拿不定的，是预算、款式，还是佩戴场景？"
    voice_advice = "先问实顾客卡点，再顺着回应。"

    if (
        (_contains_any(combined, ("黄金", "足金")) and _contains_any(combined, ("k金", "K金", "18k", "18K")))
        or (_contains_any(combined, ("黄金", "足金")) and _contains_any(combined, ("款式", "年轻", "时尚", "保值", "区别", "怎么选")))
    ):
        intent_label = "材质对比"
        intent_reason = "顾客把纠结点说到材质选择上，核心是在比较黄金与 K 金的款式感和保值预期。"
        customer_state = "权衡"
        mentor_comment = "直接顺着顾客已说出的比较维度回应，把黄金和 K 金在款式感、佩戴场景和保值差异拆开讲清楚。"
        next_action = "先确认顾客更优先看日常佩戴效果，还是更看重材质稳妥和保值，再给出对应推荐。"
        next_question = "如果现在二选一，您会更偏向戴起来更年轻时尚，还是更看重材质更稳、更保值？"
        voice_advice = "顺着材质对比来讲，直接拆黄金和 K 金的款式感、佩戴场景和保值差异。"
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
        mentor_comment = "先稳住信任感，用证书、材质依据和售后承接，不要空口保证。"
        next_action = "拿出可验证依据，按材质、证书、售后顺序回应。"
        next_question = "您最想先确认的是材质依据、证书信息，还是后续售后保障？"
        voice_advice = "先用可验证依据稳住信任，再接顾客顾虑。"
    elif any(token in combined for token in ("送人", "送礼", "对象", "场合", "佩戴场景", "婚礼", "通勤")):
        intent_label = "场景匹配"
        intent_reason = "顾客在确认购买对象或佩戴场景，说明还在判断这款是否真正适配使用需求。"
        customer_state = "斟酌"
        mentor_comment = "直接按送礼对象、佩戴频率和搭配效果给方案，别再堆通用卖点。"
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

    risk_flag = ""
    if any(token in combined for token in ("保证升值", "一定升值", "绝对", "最低价", "假一赔十", "百分百")):
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
        "voice_advice": _normalize_voice_advice(voice_advice),
    }
    if risk_flag:
        feedback["risk_flag"] = risk_flag
    return feedback


def conversation_history_to_text(history: list[dict[str, str]]) -> str:
    """将对话历史列表转换为文本格式供 DIFY 使用"""
    if not history:
        return ""
    lines = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"用户: {content}")
        elif role == "assistant":
            lines.append(f"助手: {content}")
    return "\n".join(lines)


def resolve_user_context(current_user: dict[str, Any] | None) -> dict[str, str]:
    payload = current_user if isinstance(current_user, dict) else {}
    user_id = str(payload.get("user_id") or "").strip() or "mock_user"
    role = str(payload.get("role") or "").strip()
    store_id = str(payload.get("store_id") or "").strip()

    if user_id != "mock_user" and not store_id:
        try:
            with get_conn() as conn:
                row = conn.execute(
                    """
                    SELECT COALESCE(store_id, '') AS store_id
                    FROM users
                    WHERE CAST(id AS TEXT) = ?
                    """,
                    (user_id,),
                ).fetchone()
                if row:
                    store_id = str(row["store_id"] or "").strip()
                if not store_id:
                    row2 = conn.execute(
                        """
                        SELECT COALESCE(store_id, '') AS store_id
                        FROM employee_profiles
                        WHERE employee_id = ?
                        """,
                        (user_id,),
                    ).fetchone()
                    if row2:
                        store_id = str(row2["store_id"] or "").strip()
        except Exception:
            _log.exception("resolve_user_context query store_id failed")

    if not store_id:
        store_id = "STORE01"

    return {
        "user_id": user_id,
        "store_id": store_id,
        "role": role,
    }


def build_local_fallback_reply(scene_input: str) -> AssistantReplyResponse:
    scene = str(scene_input or "").strip()
    prefix = ""
    if scene:
        prefix = "针对您当前这个客户场景，"
    reply_script = f"{prefix}{_FALLBACK_REPLY_SCRIPT}"
    coach_tip = build_coach_tip(
        scene_input=scene_input,
        reply_script=reply_script,
        matched_knowledge="",
        reply_compliance_tag="safe",
    )
    return AssistantReplyResponse(
        reply_script=reply_script,
        followup_question=_FALLBACK_FOLLOWUP_QUESTION,
        coach_tip=coach_tip,
        voice_advice=build_voice_advice(
            coach_tip=coach_tip,
            scene_input=scene_input,
            reply_script=reply_script,
            matched_knowledge="",
            reply_compliance_tag="safe",
        ),
    )


def build_coach_tip(
    *,
    scene_input: str,
    reply_script: str,
    matched_knowledge: str,
    reply_compliance_tag: str,
) -> str:
    scene = str(scene_input or "").strip()
    reply = str(reply_script or "").strip()
    knowledge = str(matched_knowledge or "").strip()
    tag = str(reply_compliance_tag or "").strip().lower()
    haystack = " ".join([scene, reply, knowledge])

    if tag and tag not in {"safe", "normal", "low", "ok"}:
        return "先稳住顾客情绪，再给可核验信息，避免绝对化承诺。"

    for key, tip in _COACH_TIP_BY_KNOWLEDGE.items():
        if key and key in haystack:
            return tip

    if "贵" in haystack or "预算" in haystack or "价格" in haystack:
        return "先认同预算顾虑，再拆价值和使用场景，别急着报优惠。"
    if "送礼" in haystack or "丈母娘" in haystack or "对象" in haystack:
        return "先确认送礼对象和场合，再给寓意与预算都匹配的推荐。"
    if "真假" in haystack or "证书" in haystack:
        return "先讲证书和材质依据，再补充售后承诺，增强信任感。"
    if "犹豫" in haystack or "比较" in haystack:
        return "先帮顾客缩小比较维度，再给一个明确试戴或成交动作。"
    return "先确认顾客最在意的一点，再给一条短而稳的专业回应。"


def build_voice_advice(
    *,
    coach_tip: str,
    scene_input: str,
    reply_script: str,
    matched_knowledge: str,
    reply_compliance_tag: str,
) -> str:
    tip = str(coach_tip or "").strip()
    if not tip:
        tip = build_coach_tip(
            scene_input=scene_input,
            reply_script=reply_script,
            matched_knowledge=matched_knowledge,
            reply_compliance_tag=reply_compliance_tag,
        )
    return _normalize_voice_advice(tip)


def _query_with_semantic_memory(
    *,
    module: str,
    query_text: str,
    user_id: str,
    store_id: str,
    write_hits: bool = True,
) -> tuple[str, str]:
    """Retrieve active evo memory and prepend it to the Dify-facing query."""
    try:
        with SessionLocal() as session:
            hits = retrieve_semantic_memories(
                session,
                user_id=user_id,
                store_id=store_id,
                module=module,
                query_text=query_text,
                write_hits=write_hits,
            )
            session.commit()
        block = build_memory_block(hits)
    except Exception:
        _log.exception("semantic memory retrieval failed module=%s user_id=%s", module, user_id)
        block = ""
    return inject_memory_block(query_text, block), block


def run_assistant1_sync(
    *,
    scene_input: str,
    user_id: str,
    store_id: str,
    conversation_history: str = "",
    write_memory_hits: bool = True,
) -> AssistantReplyRunResult:
    text = (scene_input or "").strip() or "客户对珠宝价值存在疑虑"
    dify_text, memory_block = _query_with_semantic_memory(
        module="assistant",
        query_text=text,
        user_id=user_id,
        store_id=store_id,
        write_hits=write_memory_hits,
    )
    call = run_assistant1_workflow(
        user_id=user_id,
        store_id=store_id,
        customer_question=dify_text,
        product_name="",
        customer_profile="",
        use_scene="store_assistant",
        store_policy="",
        conversation_history=conversation_history,
    )

    wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    minimal = extract_minimal_text_fields(
        raw,
        _ASSISTANT1_REPLY_ALIAS_MAP,
    )

    reply_script = (minimal.get("reply_script") or "").strip() or str(
        wf_data.get("reply_script") or ""
    ).strip()
    followup_question = (minimal.get("followup_question") or "").strip() or str(
        wf_data.get("followup_question") or ""
    ).strip()

    if bool(call.get("ok")) and _is_valid_reply_script(reply_script):
        coach_tip = build_coach_tip(
            scene_input=text,
            reply_script=reply_script,
            matched_knowledge="\n".join(
                item
                for item in [
                    str(wf_data.get("matched_knowledge") or "").strip(),
                    memory_block,
                ]
                if item
            ),
            reply_compliance_tag=str(wf_data.get("reply_compliance_tag") or "safe").strip(),
        )
        matched_knowledge = "\n".join(
            item
            for item in [
                str(wf_data.get("matched_knowledge") or "").strip(),
                memory_block,
            ]
            if item
        )
        reply_compliance_tag = str(wf_data.get("reply_compliance_tag") or "safe").strip() or "safe"
        return {
            "response": AssistantReplyResponse(
                reply_script=reply_script,
                followup_question=followup_question,
                coach_tip=coach_tip,
                voice_advice=build_voice_advice(
                    coach_tip=coach_tip,
                    scene_input=text,
                    reply_script=reply_script,
                    matched_knowledge=matched_knowledge,
                    reply_compliance_tag=reply_compliance_tag,
                ),
            ),
            "use_dify": True,
            "workflow_reason": "",
            "dify_error": "",
            "reply_compliance_tag": reply_compliance_tag,
            "matched_knowledge": matched_knowledge,
        }

    fallback = build_local_fallback_reply(text)
    reason = str(call.get("reason") or "").strip()
    if bool(call.get("ok")) and not reason:
        reason = "invalid_reply_script"
    return {
        "response": fallback,
        "use_dify": False,
        "workflow_reason": reason or "fallback_local_reply",
        "dify_error": str(call.get("error") or "").strip(),
        "reply_compliance_tag": "safe",
        "matched_knowledge": memory_block,
    }


def is_assistant_chat_configured() -> bool:
    """检查 assistant_chat Dify chat 应用是否已配置。"""
    import config as app_config
    return bool(_as_text(app_config.DIFY_ASSISTANT_CHAT_API_KEY))


def run_assistant_chat_sync(
    *,
    scene_input: str,
    user_id: str,
    store_id: str,
    conversation_id: str = "",
) -> AssistantReplyRunResult:
    """在岗助手 chat 模式：调用 Dify chat API，提取 turn_feedback，回退到本地 fallback。"""
    text = (scene_input or "").strip() or "客户对珠宝价值存在疑虑"
    dify_text, memory_block = _query_with_semantic_memory(
        module="assistant",
        query_text=text,
        user_id=user_id,
        store_id=store_id,
    )
    call = run_assistant_chat(
        user_id=user_id,
        query=dify_text,
        scene_input=dify_text,
        conversation_id=conversation_id,
    )

    if not bool(call.get("ok")):
        fallback = build_local_fallback_reply(text)
        return {
            "response": AssistantReplyResponse(
                reply_script=fallback.reply_script,
                followup_question=fallback.followup_question,
                coach_tip=fallback.coach_tip,
                voice_advice=fallback.voice_advice,
                turn_feedback=None,
                conversation_id="",
            ),
            "use_dify": False,
            "workflow_reason": str(call.get("reason") or "").strip() or "chat_failed",
            "dify_error": str(call.get("error") or "").strip(),
            "reply_compliance_tag": "safe",
            "matched_knowledge": memory_block,
        }

    data = call.get("data") if isinstance(call.get("data"), dict) else {}
    reply_script = _as_text(data.get("assistant_reply"))

    if not _is_valid_reply_script(reply_script):
        fallback = build_local_fallback_reply(text)
        return {
            "response": AssistantReplyResponse(
                reply_script=fallback.reply_script,
                followup_question=fallback.followup_question,
                coach_tip=fallback.coach_tip,
                voice_advice=fallback.voice_advice,
                turn_feedback=None,
                conversation_id=_as_text(data.get("conversation_id")),
            ),
            "use_dify": False,
            "workflow_reason": "invalid_reply_script",
            "dify_error": "",
            "reply_compliance_tag": "safe",
            "matched_knowledge": memory_block,
        }

    coach_tip = build_coach_tip(
        scene_input=text,
        reply_script=reply_script,
        matched_knowledge=memory_block,
        reply_compliance_tag="safe",
    )
    old_voice_advice = build_voice_advice(
        coach_tip=coach_tip,
        scene_input=text,
        reply_script=reply_script,
        matched_knowledge=memory_block,
        reply_compliance_tag="safe",
    )

    turn_feedback = _extract_turn_feedback_from_call(call)
    if not turn_feedback:
        turn_feedback = _build_assistant_turn_feedback_fallback(
            scene_input=text,
            reply_script=reply_script,
        )

    tf_voice = _as_text(turn_feedback.get("voice_advice"))
    voice_advice = tf_voice if tf_voice else old_voice_advice

    conv_id = _as_text(data.get("conversation_id"))

    return {
        "response": AssistantReplyResponse(
            reply_script=reply_script,
            followup_question="",
            coach_tip=coach_tip,
            voice_advice=voice_advice,
            turn_feedback=turn_feedback,
            conversation_id=conv_id,
        ),
        "use_dify": True,
        "workflow_reason": "",
        "dify_error": "",
        "reply_compliance_tag": "safe",
        "matched_knowledge": memory_block,
    }


def persist_reply_record(
    *,
    user_id: str,
    store_id: str,
    role: str,
    scene_input: str,
    response: AssistantReplyResponse,
    use_dify: bool,
    workflow_reason: str,
    dify_error: str,
) -> int:
    payload = {
        "reply_script": response.reply_script,
        "followup_question": response.followup_question,
        "coach_tip": response.coach_tip,
        "voice_advice": response.voice_advice,
        "workflow_mode": "dify" if use_dify else "local_fallback",
        "workflow_reason": workflow_reason,
        "dify_error": dify_error,
    }
    with get_conn() as conn:
        upsert_employee_profile(
            conn,
            employee_id=user_id,
            employee_name="",
            position="",
            store_id=store_id,
            role=role,
            source="assistant_reply",
        )
        row = conn.execute(
            """
            INSERT INTO assistant_records (
                record_id, action, employee_id, customer_question, scene_hint,
                assistant_reply, analysis_json, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                make_request_id("ar"),
                "reply",
                user_id,
                scene_input,
                "store_assistant",
                response.reply_script,
                "{}",
                json_text(payload),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return int(row.lastrowid or 0)


def run_assistant2_background(
    *,
    user_id: str,
    store_id: str,
    role: str,
    scene_input: str,
    assistant_reply: str,
    reply_compliance_tag: str,
    matched_knowledge: str,
) -> None:
    """后台异步沉淀调用：失败只记录日志，不影响前台。"""
    try:
        call = run_assistant2_workflow(
            user_id=user_id,
            store_id=store_id,
            customer_question=scene_input,
            assistant_reply=assistant_reply,
            reply_compliance_tag=reply_compliance_tag,
            matched_knowledge=matched_knowledge,
            scene_hint="store_assistant",
            source_module="on_duty_assistant_async",
        )

        ok = bool(call.get("ok"))
        wf_data = call.get("data") if isinstance(call.get("data"), dict) else {}
        payload = {
            "ok": ok,
            "reason": str(call.get("reason") or "").strip(),
            "error": str(call.get("error") or "").strip(),
            "data": wf_data,
        }
        analysis_obj = {
            "customer_intent": str(wf_data.get("customer_intent") or "").strip(),
            "risk_level": str(wf_data.get("risk_level") or "").strip(),
            "record_summary": str(wf_data.get("record_summary") or "").strip(),
            "weak_dimension": str(wf_data.get("weak_dimension") or "").strip(),
        }

        with get_conn() as conn:
            upsert_employee_profile(
                conn,
                employee_id=user_id,
                employee_name="",
                position="",
                store_id=store_id,
                role=role,
                source="assistant_analyze_async",
            )
            conn.execute(
                """
                INSERT INTO assistant_records (
                    record_id, action, employee_id, customer_question, scene_hint,
                    assistant_reply, analysis_json, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_request_id("aa"),
                    "analyze",
                    user_id,
                    scene_input,
                    "store_assistant",
                    assistant_reply,
                    json_text(analysis_obj),
                    json_text(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception:
        _log.exception("assistant2 background task failed")
