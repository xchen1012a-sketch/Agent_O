from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import config as app_config
from dify_client import run_workflow_blocking
from dify_utils import (
    _extract_data_and_outputs,
    _parse_json_like,
    _pick_value,
    _to_bool,
    _to_int,
    _to_text,
)

_log = logging.getLogger("jewelry_qipei.stage4a")


def _split_points(v: Any) -> list[str]:
    if isinstance(v, list):
        out = [_to_text(x) for x in v]
        return [x for x in out if x]
    s = _to_text(v)
    if not s:
        return []
    parts = re.split(r"[;\n,，；、]+", s)
    out = [_to_text(x) for x in parts]
    return [x for x in out if x]



def _extract_markdown_meta(markdown: str) -> dict[str, str]:
    text = _to_text(markdown)
    if not text.startswith("<!--"):
        return {}
    end = text.find("-->")
    if end <= 4:
        return {}
    body = text[4:end].strip()
    if not body:
        return {}
    out: dict[str, str] = {}
    for item in body.split(";"):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        key = _to_text(k)
        val = _to_text(v)
        if key:
            out[key] = val
    return out


def _resolve_workflow_api_key(name: str) -> str:
    # key-only 模式：仅按 API Key 直连，避免 workflow_id 与 key 双维护
    if name == "growth1":
        specific_key = _to_text(app_config.DIFY_GROWTH1_API_KEY)
    elif name == "growth2":
        specific_key = _to_text(app_config.DIFY_GROWTH2_API_KEY)
    elif name == "assistant1":
        specific_key = _to_text(app_config.DIFY_ASSISTANT1_API_KEY)
    elif name == "assistant2":
        specific_key = _to_text(app_config.DIFY_ASSISTANT2_API_KEY)
    else:
        return ""

    return specific_key or _to_text(app_config.DIFY_API_KEY)


def _resolve_workflow_api_base(name: str) -> str:
    if name == "assistant1":
        base = _to_text(app_config.DIFY_ASSISTANT1_API_BASE)
    elif name == "assistant2":
        base = _to_text(app_config.DIFY_ASSISTANT2_API_BASE)
    else:
        base = _to_text(app_config.DIFY_API_BASE)
    return base.rstrip("/")


def _resolve_workflow_timeout(name: str) -> float:
    if name == "growth1":
        return float(app_config.DIFY_GROWTH1_TIMEOUT)
    if name == "assistant1":
        return float(app_config.DIFY_ASSISTANT1_TIMEOUT)
    if name == "assistant2":
        return float(app_config.DIFY_ASSISTANT2_TIMEOUT)
    return float(app_config.DIFY_STAGE4A_TIMEOUT)


def _resolve_workflow_retry_attempts(name: str) -> int:
    if name == "growth1":
        return int(app_config.DIFY_GROWTH1_RETRY_ATTEMPTS)
    return 0


def _resolve_workflow_retry_backoff(name: str) -> float:
    if name == "growth1":
        return float(app_config.DIFY_GROWTH1_RETRY_BACKOFF_SEC)
    return 0.0


def _should_retry_stage4a_failure(raw: dict[str, Any]) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("message") != "dify_http_error":
        return False
    data = raw.get("data")
    if not isinstance(data, dict):
        return False
    http_status = _to_int(data.get("http_status"), 0)
    if http_status == 504:
        return True
    if http_status == 0 and _to_text(data.get("reason_hint")) == "upstream_connection_closed_after_long_blocking_wait":
        return True
    return False


def _run_stage4a_workflow(
    *,
    workflow_name: str,
    user_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if app_config.DIFY_STAGE4A_FORCE_MOCK:
        _log.warning("stage4a blocked by force_mock workflow=%s", workflow_name)
        return {"ok": False, "reason": "force_mock", "error": "", "raw": {}}

    api_key = _resolve_workflow_api_key(workflow_name)
    if not api_key:
        _log.error("stage4a missing api_key workflow=%s", workflow_name)
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _resolve_workflow_api_base(workflow_name)
    if not base:
        _log.error("stage4a missing api_base workflow=%s", workflow_name)
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}
    timeout_sec = _resolve_workflow_timeout(workflow_name)
    retry_attempts = max(0, _resolve_workflow_retry_attempts(workflow_name))
    retry_backoff_sec = max(0.0, _resolve_workflow_retry_backoff(workflow_name))

    _log.info(
        "stage4a prepare dify call workflow=%s base=%s timeout=%.1fs retries=%s input_keys=%s",
        workflow_name,
        f"{base}/v1/workflows/run",
        timeout_sec,
        retry_attempts,
        sorted((inputs or {}).keys()),
    )

    raw: Any = {}
    last_error = ""
    total_attempts = retry_attempts + 1
    for attempt in range(1, total_attempts + 1):
        try:
            raw = run_workflow_blocking(
                base_url=base,
                api_key=api_key,
                inputs={str(k): "" if v is None else str(v) for k, v in (inputs or {}).items()},
                user=_to_text(user_id) or "stage4a-user",
                workflow_id=None,
                timeout_sec=timeout_sec,
            )
        except Exception as e:
            _log.exception("stage4a workflow call failed: %s", workflow_name)
            return {"ok": False, "reason": "dify_exception", "error": _to_text(e), "raw": {}}

        if isinstance(raw, dict) and raw.get("code") == 200:
            break

        if attempt < total_attempts and isinstance(raw, dict) and _should_retry_stage4a_failure(raw):
            raw_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            last_error = _to_text(raw.get("message")) or _to_text(raw_data.get("detail"))
            _log.warning(
                "stage4a retrying workflow=%s next_attempt=%s/%s http_status=%s reason_hint=%s backoff=%.1fs",
                workflow_name,
                attempt + 1,
                total_attempts,
                _to_int(raw_data.get("http_status"), 0),
                _to_text(raw_data.get("reason_hint")),
                retry_backoff_sec,
            )
            time.sleep(retry_backoff_sec)
            continue
        break

    if not isinstance(raw, dict):
        _log.warning("stage4a invalid response workflow=%s raw_type=%s", workflow_name, type(raw).__name__)
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}

    code = raw.get("code")
    if code != 200:
        _log.warning(
            "stage4a dify failed workflow=%s code=%s message=%s",
            workflow_name,
            code,
            _to_text(raw.get("message")),
        )
        return {
            "ok": False,
            "reason": "dify_non_200",
            "error": _to_text(raw.get("message")) or last_error,
            "raw": raw,
        }
    _log.info("stage4a dify success workflow=%s", workflow_name)
    return {"ok": True, "reason": "", "error": "", "raw": raw}


def run_growth1_workflow(
    *,
    user_id: str,
    job_title: str,
    self_intro: str,
    historical_learning: str,
    initial_ability: str,
    employee_stage: str = "",
    plan_cycle: str = "",
    current_performance: str = "",
    target_performance: str = "",
    gap_metrics: str = "",
    performance_context: str = "",
) -> dict[str, Any]:
    call = _run_stage4a_workflow(
        workflow_name="growth1",
        user_id=user_id,
        inputs={
            "user_id": user_id,
            "job_title": job_title,
            "self_intro": self_intro,
            "historical_learning": historical_learning,
            "initial_ability": initial_ability,
            "employee_stage": employee_stage,
            "plan_cycle": plan_cycle,
            "current_performance": current_performance,
            "target_performance": target_performance,
            "gap_metrics": gap_metrics,
            "performance_context": performance_context,
        },
    )
    if not call.get("ok"):
        return call

    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    data, outputs = _extract_data_and_outputs(raw)
    markdown = _to_text(
        _pick_value(
            data,
            outputs,
            "growth_plan_markdown",
            "final_markdown",
            "content",
        )
    )
    meta_raw = _pick_value(data, outputs, "plan_meta")
    plan_meta = _parse_json_like(meta_raw)
    normalized_profile = _to_text(_pick_value(data, outputs, "normalized_profile", "profile_card"))
    markdown_meta = _extract_markdown_meta(markdown)

    call["data"] = {
        "growth_plan_markdown": markdown,
        "plan_meta": plan_meta,
        "normalized_profile": normalized_profile,
        "markdown_meta": markdown_meta,
    }
    return call


def run_growth2_workflow(
    *,
    user_id: str,
    module_code: str,
    module_name: str,
    question_text: str,
    user_answer: str,
    standard_answer: str,
    knowledge_tag: str,
    current_profile: str,
    current_scores: str,
) -> dict[str, Any]:
    call = _run_stage4a_workflow(
        workflow_name="growth2",
        user_id=user_id,
        inputs={
            "user_id": user_id,
            "module_code": module_code,
            "module_name": module_name,
            "question_text": question_text,
            "user_answer": user_answer,
            "standard_answer": standard_answer,
            "knowledge_tag": knowledge_tag,
            "current_profile": current_profile,
            "current_scores": current_scores,
        },
    )
    if not call.get("ok"):
        return call

    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    data, outputs = _extract_data_and_outputs(raw)
    markdown = _to_text(
        _pick_value(
            data,
            outputs,
            "evaluation_markdown",
            "final_markdown",
            "content",
        )
    )
    output_meta = _parse_json_like(_pick_value(data, outputs, "output_meta"))
    markdown_meta = _extract_markdown_meta(markdown)

    call["data"] = {
        "evaluation_markdown": markdown,
        "output_meta": output_meta,
        "markdown_meta": markdown_meta,
    }
    return call


def run_assistant1_workflow(
    *,
    user_id: str,
    customer_question: str,
    store_id: str = "",
    product_name: str = "",
    customer_profile: str = "",
    use_scene: str = "",
    store_policy: str = "",
    conversation_history: str = "",
) -> dict[str, Any]:
    call = _run_stage4a_workflow(
        workflow_name="assistant1",
        user_id=user_id,
        inputs={
            "scene_input": customer_question,
            "user_id": user_id,
            "store_id": store_id,
            "customer_question": customer_question,
            "product_name": product_name,
            "customer_profile": customer_profile,
            "use_scene": use_scene,
            "store_policy": store_policy,
            "conversation_history": conversation_history,
        },
    )
    if not call.get("ok"):
        return call

    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    data, outputs = _extract_data_and_outputs(raw)

    reply_script = _to_text(_pick_value(data, outputs, "reply_script", "content"))
    risk_level = _to_text(_pick_value(data, outputs, "risk_level")).lower() or "medium"
    refuse_flag = _to_text(_pick_value(data, outputs, "refuse_flag")).lower() or "no"
    knowledge_points = _to_text(_pick_value(data, outputs, "knowledge_points"))
    followup_question = _to_text(_pick_value(data, outputs, "followup_question"))

    reply_compliance_tag = "safe"
    if refuse_flag == "yes" or risk_level == "high":
        reply_compliance_tag = "high_risk"
    elif risk_level == "medium":
        reply_compliance_tag = "caution"

    call["data"] = {
        "reply_script": reply_script,
        "compliance_tip": _to_text(_pick_value(data, outputs, "compliance_tip")),
        "risk_level": risk_level,
        "question_type": _to_text(_pick_value(data, outputs, "question_type")),
        "scene_tag": _to_text(_pick_value(data, outputs, "scene_tag")),
        "knowledge_points": knowledge_points,
        "followup_question": followup_question,
        "confidence_level": _to_text(_pick_value(data, outputs, "confidence_level")).lower()
        or "medium",
        "answer_mode": _to_text(_pick_value(data, outputs, "answer_mode")).lower() or "fallback",
        "refuse_flag": refuse_flag,
        "knowledge_hit": _to_text(_pick_value(data, outputs, "knowledge_hit")).lower() or "no",
        "hit_strategy": _to_text(_pick_value(data, outputs, "hit_strategy")).lower() or "fallback",
        "problem_note": _to_text(_pick_value(data, outputs, "problem_note")),
        "raw_output": _to_text(_pick_value(data, outputs, "raw_output")),
        # 兼容前端旧字段
        "reply_text": reply_script,
        "key_points": _split_points(knowledge_points),
        "suggested_questions": [followup_question] if followup_question else [],
        # 给在岗助手分析节点映射字段
        "reply_compliance_tag": reply_compliance_tag,
        "matched_knowledge": knowledge_points,
    }
    return call


def run_assistant2_workflow(
    *,
    user_id: str,
    store_id: str,
    customer_question: str,
    assistant_reply: str,
    reply_compliance_tag: str,
    matched_knowledge: str,
    scene_hint: str,
    source_module: str,
) -> dict[str, Any]:
    call = _run_stage4a_workflow(
        workflow_name="assistant2",
        user_id=user_id,
        inputs={
            "scene_input": customer_question,
            "user_id": user_id,
            "store_id": store_id,
            "customer_question": customer_question,
            "assistant_reply": assistant_reply,
            "reply_compliance_tag": reply_compliance_tag,
            "matched_knowledge": matched_knowledge,
            "scene_hint": scene_hint,
            "source_module": source_module,
        },
    )
    if not call.get("ok"):
        return call

    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    data, outputs = _extract_data_and_outputs(raw)

    risk_points = _split_points(_pick_value(data, outputs, "risk_points"))
    internal_tags = _split_points(_pick_value(data, outputs, "internal_tags"))
    followup_topic = _to_text(_pick_value(data, outputs, "followup_training_topic"))
    followup_advice = _to_text(_pick_value(data, outputs, "followup_training_advice"))
    recommended_action = _to_text(_pick_value(data, outputs, "recommended_action"))

    training_suggestions = [x for x in [followup_topic, followup_advice, recommended_action] if x]
    if not training_suggestions:
        training_suggestions = ["建议继续补学并做场景化陪练"]

    call["data"] = {
        "question_type": _to_text(_pick_value(data, outputs, "question_type")),
        "customer_intent": _to_text(_pick_value(data, outputs, "customer_intent")),
        "knowledge_tag": _to_text(_pick_value(data, outputs, "knowledge_tag")),
        "knowledge_subtag": _to_text(_pick_value(data, outputs, "knowledge_subtag")),
        "scene_stage": _to_text(_pick_value(data, outputs, "scene_stage")),
        "objection_strength": _to_text(_pick_value(data, outputs, "objection_strength")).lower()
        or "low",
        "risk_level": _to_text(_pick_value(data, outputs, "risk_level")).lower() or "low",
        "compliance_tag": _to_text(_pick_value(data, outputs, "compliance_tag")) or "safe",
        "risk_points": risk_points,
        "weak_dimension": _to_text(_pick_value(data, outputs, "weak_dimension")),
        "weak_severity": _to_text(_pick_value(data, outputs, "weak_severity")).lower() or "low",
        "weak_reason": _to_text(_pick_value(data, outputs, "weak_reason")),
        "followup_training_topic": followup_topic,
        "followup_training_advice": followup_advice,
        "recommended_action": recommended_action,
        "internal_tags": internal_tags,
        "manager_attention_needed": _to_bool(
            _pick_value(data, outputs, "manager_attention_needed")
        ),
        "training_priority_score": _to_int(
            _pick_value(data, outputs, "training_priority_score"), 0
        ),
        "ability_update_hint": _to_text(_pick_value(data, outputs, "ability_update_hint")),
        "record_summary": _to_text(_pick_value(data, outputs, "record_summary")),
        "analysis_json": _to_text(_pick_value(data, outputs, "analysis_json")),
        # 兼容前端旧字段
        "focus_points": risk_points or internal_tags,
        "training_suggestions": training_suggestions,
        "knowledge_gap": _to_text(_pick_value(data, outputs, "weak_reason")),
    }
    return call
