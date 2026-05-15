from __future__ import annotations

import json
import logging
import re
from typing import Any

import config as app_config
from dify_client import extract_workflow_conversation_id, run_workflow_blocking
from dify_kb_client import kb_hit_test
from dify_utils import _extract_data_and_outputs, _pick_value, _to_text

_log = logging.getLogger("jewelry_qipei.qa_service")

_DATA_QUERY_PATTERNS = [
    r"排行|排名|榜单",
    r"高风险|低风险|风险员工|风险名单|风险看板",
    r"完成率|通过率|均分|趋势|近7天|近30天|最近\d+天",
    r"哪个门店|哪些门店|门店.*最好|门店.*最差",
    r"哪些员工|哪个员工|谁表现|谁最好|谁最差|员工表现",
    r"培训绩效|经营数据|业绩|数据分析|统计",
]
_UNSUPPORTED_PATTERNS = [
    r"写一首诗|写首诗|写诗",
    r"天气|股价|股票|彩票",
    r"奖金分配|工资核算|提成方案",
    r"真假鉴定|帮我鉴定|直接判断真假",
]
_ANSWER_ALIAS_KEYS = (
    "answer_text",
    "final_answer",
    "answer",
    "reply_text",
    "content",
    "text",
    "draft_answer",
)

_BRIEF_ALIAS_KEYS = ("answer_brief", "brief", "summary", "conclusion")
_REASON_ALIAS_KEYS = ("answer_reason", "reason", "explanation", "basis")
_EXAMPLE_ALIAS_KEYS = ("answer_example", "example", "script_example", "sales_example")
_COACH_QUESTION_ALIAS_KEYS = ("coach_question", "followup_question", "next_question", "suggested_followup")


def classify_qa_question(question: str) -> str:
    text = _to_text(question).strip()
    if not text:
        return "other_unsupported"
    for pattern in _DATA_QUERY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return "data_query"
    for pattern in _UNSUPPORTED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return "other_unsupported"
    return "knowledge"


def classify_qa_subtype(question: str) -> str:
    text = _to_text(question).strip().lower()
    if not text:
        return "general_knowledge"
    if any(token in text for token in ("系统", "页面", "按钮", "怎么操作", "怎么看", "哪里", "流程", "功能", "使用")):
        return "system_usage"
    if any(token in text for token in ("钻石", "4c", "材质", "工艺", "证书", "镶嵌", "k金", "铂金", "产品", "卖点")):
        return "product_knowledge"
    if any(token in text for token in ("合规", "风险", "承诺", "回购", "保值", "能不能说", "违规", "边界", "真假鉴定")):
        return "compliance_boundary"
    if any(token in text for token in ("顾客", "太贵", "异议", "怎么接", "怎么回", "话术", "推荐", "成交", "试戴", "预算")):
        return "sales_script"
    return "general_knowledge"


def _split_sentences(text: str) -> list[str]:
    raw = _to_text(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[。！？；;])\s*", raw)
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return cleaned or [raw]


def _fallback_teacher_parts(question: str, subtype: str) -> dict[str, str]:
    if subtype == "product_knowledge":
        return {
            "answer_brief": "先给结论：这类产品问题要先讲清定义或标准，再补卖点。",
            "answer_reason": "因为顾客先要确认你说得准不准，再决定要不要继续听你推荐。",
            "answer_example": "门店里可以这样说：先把核心标准讲明白，再补一句佩戴体验或工艺价值。",
            "coach_question": "你想继续追问这类产品的定义、标准，还是顾客更容易听懂的说法？",
        }
    if subtype == "compliance_boundary":
        return {
            "answer_brief": "先给结论：合规类问题先说边界，再给可替代表达。",
            "answer_reason": "因为顾客现场最怕听到过度承诺，而门店表达最容易在这里越界。",
            "answer_example": "门店里可以这样说：先说明依据和保障范围，不要直接说绝对结果或保值承诺。",
            "coach_question": "你要继续追问这句话为什么不合规，还是想看更稳妥的替代表达？",
        }
    if subtype == "sales_script":
        return {
            "answer_brief": "先给结论：销售话术要先接住顾客情绪，再拆价值，不要直接反驳。",
            "answer_reason": "因为顾客提出异议时，先被理解才会继续听你的专业解释。",
            "answer_example": "门店里可以这样说：先认同顾虑，再补一句材质、工艺或售后的价值点。",
            "coach_question": "你想继续追问这类异议怎么开场，还是怎么自然收口？",
        }
    if subtype == "system_usage":
        return {
            "answer_brief": "先给结论：系统问题优先看入口、步骤和常见误区。",
            "answer_reason": "因为这类问题不是背概念，而是要马上知道先点哪里、再做什么。",
            "answer_example": "你可以把它理解成三步：先找到页面入口，再完成关键操作，最后确认结果有没有保存。",
            "coach_question": "你要继续追问具体入口位置，还是想看完整操作步骤？",
        }
    return {
        "answer_brief": "先给结论：这题先抓住最关键的定义或判断，再往下展开。",
        "answer_reason": "因为先说结论，顾客和学员都会更容易跟上后面的解释。",
        "answer_example": "你可以把回答分成三步：先判断，再解释，最后补一个门店里的说法。",
        "coach_question": "你想继续追问概念本身，还是想看门店里更自然的表达？",
    }


def build_teacher_answer_parts(question: str, answer_text: str, knowledge_patch: str = "") -> dict[str, str]:
    subtype = classify_qa_subtype(question)
    fallback = _fallback_teacher_parts(question, subtype)
    sentences = _split_sentences(answer_text)

    brief = sentences[0] if sentences else fallback["answer_brief"]
    reason = ""
    example = ""

    if len(sentences) >= 2:
        reason = sentences[1]
    if len(sentences) >= 3:
        example = " ".join(sentences[2:]).strip()

    if not reason:
        if subtype == "compliance_boundary":
            reason = "这里的重点不是能不能说得动人，而是不能越过门店表达边界。"
        elif subtype == "sales_script":
            reason = "这里的重点不是立刻说服，而是先把顾客情绪接住，再把价值讲顺。"
        elif subtype == "product_knowledge":
            reason = "这里的重点不是堆术语，而是先讲定义和依据，再讲顾客听得懂的价值。"
        elif subtype == "system_usage":
            reason = "这里的重点不是概念复述，而是让你知道下一步该点哪里。"
        else:
            reason = fallback["answer_reason"]

    if not example:
        if subtype == "compliance_boundary":
            example = "下次顾客问到这里，先说明依据和保障范围，再给稳妥表达，不要说绝对话。"
        elif subtype == "sales_script":
            example = "你在门店里可以这样说：先认同顾客顾虑，再补一句专业价值点。"
        elif subtype == "product_knowledge":
            example = "你在门店里可以这样说：先把定义讲清，再补一句佩戴体验或工艺卖点。"
        elif subtype == "system_usage":
            example = "实际操作时，先找入口，再做关键步骤，最后核对结果是否保存成功。"
        else:
            example = fallback["answer_example"]

    coach_question = fallback["coach_question"]

    return {
        "subtype": subtype,
        "answer_brief": brief.strip(),
        "answer_reason": reason.strip(),
        "answer_example": example.strip(),
        "coach_question": coach_question.strip(),
    }


def sanitize_qa_history(history: list[dict[str, Any]] | None, *, max_items: int = 10) -> list[dict[str, str]]:
    items = history if isinstance(history, list) else []
    cleaned: list[dict[str, str]] = []
    for raw in items[-max_items:]:
        if not isinstance(raw, dict):
            continue
        role = _to_text(raw.get("role")).strip().lower()
        content = _to_text(raw.get("content")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content[:500]})
    return cleaned


def qa_history_to_text(history: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for item in sanitize_qa_history(history):
        prefix = "用户" if item["role"] == "user" else "助手"
        lines.append(f"{prefix}：{item['content']}")
    return "\n".join(lines)


def _maybe_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    text = _to_text(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def normalize_related_questions(raw: Any, *, limit: int = 3) -> list[str]:
    candidates: list[Any]
    if isinstance(raw, list):
        candidates = raw
    else:
        parsed = _maybe_json_list(raw)
        if parsed:
            candidates = parsed
        else:
            text = _to_text(raw).strip()
            if not text:
                candidates = []
            else:
                candidates = re.split(r"[\n；;]+", text)
    items: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = _to_text(candidate).strip().lstrip("-").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value[:120])
        if len(items) >= limit:
            break
    return items


def _pick_nested_text(record: dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        node: Any = record
        ok = True
        for key in path:
            if not isinstance(node, dict) or key not in node:
                ok = False
                break
            node = node.get(key)
        if ok:
            text = _to_text(node).strip()
            if text:
                return text
    return ""


def normalize_citations(results: Any, *, dataset_id: str = "", limit: int = 3) -> list[dict[str, Any]]:
    records = results if isinstance(results, list) else []
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        doc_name = _pick_nested_text(
            item,
            ("segment", "document", "name"),
            ("document", "name"),
            ("doc_metadata", "name"),
            ("metadata", "document_name"),
            ("title",),
            ("document_name",),
            ("name",),
        )
        snippet = _pick_nested_text(
            item,
            ("segment", "content"),
            ("segment", "text"),
            ("content",),
            ("text",),
            ("snippet",),
        )
        score_raw = item.get("score")
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        dsid = (
            _pick_nested_text(item, ("dataset", "id"), ("dataset_id",))
            or dataset_id
        )
        if not doc_name and not snippet:
            continue
        key = (doc_name, snippet[:60])
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "dataset_id": dsid,
                "document_name": doc_name or "命中文档",
                "snippet": snippet[:280],
                "score": round(score, 4),
            }
        )
        if len(citations) >= limit:
            break
    return citations


def qa_dataset_id() -> str:
    raw = _to_text(getattr(app_config, "KB_DATASET_IDS_QA", "")).strip()
    if not raw:
        return ""
    return raw.split(",")[0].strip()


def fetch_qa_citations(question: str) -> list[dict[str, Any]]:
    dataset_id = qa_dataset_id()
    if not dataset_id:
        return []
    result = kb_hit_test(dataset_id, _to_text(question).strip())
    if not result.get("ok"):
        _log.warning("qa kb_hit_test failed dataset_id=%s error=%s", dataset_id, result.get("error"))
        return []
    return normalize_citations(result.get("results"), dataset_id=dataset_id, limit=3)


def _resolve_qa_api_key() -> str:
    return _to_text(getattr(app_config, "DIFY_QA1_API_KEY", "")) or _to_text(getattr(app_config, "DIFY_API_KEY", ""))


def _resolve_qa_api_base() -> str:
    return _to_text(getattr(app_config, "DIFY_QA1_API_BASE", "")) or _to_text(getattr(app_config, "DIFY_API_BASE", ""))


def _resolve_qa_timeout() -> float:
    try:
        return float(getattr(app_config, "DIFY_QA1_TIMEOUT", 30.0) or 30.0)
    except (TypeError, ValueError):
        return 30.0


def is_qa_chat_configured() -> bool:
    return bool(_to_text(getattr(app_config, "DIFY_QA_CHAT_API_KEY", "")))


def run_qa_chat_sync(
    *,
    question: str,
    user_id: str,
    conversation_id: str = "",
) -> dict[str, Any]:
    """调用 qa_chat，返回 turn_feedback 和 qa_chat conversation_id。"""
    from dify_stage4b import run_qa_chat

    call = run_qa_chat(
        user_id=user_id,
        query=question,
        conversation_id=conversation_id,
    )
    if not call.get("ok"):
        return {"turn_feedback": None, "qa_chat_conversation_id": ""}

    data = call.get("data") if isinstance(call.get("data"), dict) else {}
    tf_raw = _to_text(data.get("turn_feedback_json"))
    turn_feedback = None
    if tf_raw:
        try:
            parsed = json.loads(tf_raw)
            if isinstance(parsed, dict):
                turn_feedback = parsed
        except Exception:
            turn_feedback = None

    return {
        "turn_feedback": turn_feedback,
        "qa_chat_conversation_id": _to_text(data.get("conversation_id")),
    }


def run_qa1_workflow(
    *,
    question: str,
    conversation_history: str = "",
    conversation_id: str = "",
    user_id: str = "",
    role: str = "",
    store_id: str = "",
) -> dict[str, Any]:
    api_key = _resolve_qa_api_key()
    if not api_key:
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}
    base = _resolve_qa_api_base().rstrip("/")
    if not base:
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}

    workflow_id = _to_text(getattr(app_config, "DIFY_QA1_WORKFLOW_ID", "")).strip() or None
    raw = run_workflow_blocking(
        base_url=base,
        api_key=api_key,
        inputs={
            "question": _to_text(question).strip(),
            "conversation_history": _to_text(conversation_history).strip(),
            "user_id": _to_text(user_id).strip(),
            "role": _to_text(role).strip(),
            "store_id": _to_text(store_id).strip(),
        },
        user=_to_text(user_id).strip() or "qa-user",
        conversation_id=_to_text(conversation_id).strip(),
        workflow_id=workflow_id,
        timeout_sec=_resolve_qa_timeout(),
    )
    if not isinstance(raw, dict):
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}
    if raw.get("code") != 200:
        return {
            "ok": False,
            "reason": "dify_non_200",
            "error": _to_text(raw.get("message")),
            "raw": raw,
        }

    data, outputs = _extract_data_and_outputs(raw)
    answer_text = _to_text(_pick_value(data, outputs, *_ANSWER_ALIAS_KEYS)).strip()
    related_questions = normalize_related_questions(
        _pick_value(data, outputs, "related_questions", "suggested_questions", "next_questions")
    )
    answer_brief = _to_text(_pick_value(data, outputs, *_BRIEF_ALIAS_KEYS)).strip()
    answer_reason = _to_text(_pick_value(data, outputs, *_REASON_ALIAS_KEYS)).strip()
    answer_example = _to_text(_pick_value(data, outputs, *_EXAMPLE_ALIAS_KEYS)).strip()
    coach_question = _to_text(_pick_value(data, outputs, *_COACH_QUESTION_ALIAS_KEYS)).strip()
    answer_mode = _to_text(_pick_value(data, outputs, "answer_mode", "mode")).strip()
    matched_topics = _to_text(_pick_value(data, outputs, "matched_topics", "topics")).strip()
    risk_tag = _to_text(_pick_value(data, outputs, "risk_tag", "safety_tag")).strip()
    confidence_level = _to_text(_pick_value(data, outputs, "confidence_level", "confidence")).strip()
    refuse_reason = _to_text(_pick_value(data, outputs, "refuse_reason", "reason")).strip()
    wf_conversation_id = extract_workflow_conversation_id(raw)

    if not answer_text:
        return {
            "ok": False,
            "reason": "empty_answer",
            "error": "",
            "raw": raw,
        }

    return {
        "ok": True,
        "reason": "",
        "error": "",
        "raw": raw,
        "data": {
            "answer_text": answer_text,
            "answer_brief": answer_brief,
            "answer_reason": answer_reason,
            "answer_example": answer_example,
            "coach_question": coach_question,
            "related_questions": related_questions,
            "answer_mode": answer_mode,
            "matched_topics": matched_topics,
            "risk_tag": risk_tag,
            "confidence_level": confidence_level,
            "refuse_reason": refuse_reason,
            "conversation_id": wf_conversation_id,
        },
    }
