from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any

import config as app_config
import httpx
from dify_client import run_workflow_blocking
from dify_utils import (
    _extract_data_and_outputs,
    _parse_json_like,
    _pick_value,
    _strip_reasoning_block,
    _to_bool,
    _to_float,
    _to_int,
    _to_list,
    _to_text,
)

_log = logging.getLogger("jewelry_qipei.stage4b")

PRACTICE_END_MARKER = "[PRACTICE_END]"


def _extract_call_fields(call: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract (data, outputs) from a workflow call result's raw field."""
    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    return _extract_data_and_outputs(raw)


def _resolve_workflow_api_key(name: str) -> str:
    # key-only 模式：仅按 API Key 直连，避免 workflow_id 与 key 双维护
    if name == "practice2":
        specific_key = _to_text(app_config.DIFY_PRACTICE2_API_KEY)
    elif name == "practice3":
        specific_key = _to_text(app_config.DIFY_PRACTICE3_API_KEY)
    elif name == "dashboard":
        specific_key = _to_text(app_config.DIFY_DASHBOARD_API_KEY)
    elif name == "query1":
        specific_key = _to_text(app_config.DIFY_QUERY1_API_KEY)
    elif name == "query2":
        specific_key = _to_text(app_config.DIFY_QUERY2_API_KEY)
    elif name == "practice_mentor":
        specific_key = _to_text(app_config.DIFY_PRACTICE_MENTOR_API_KEY)
    elif name == "practice_turn_feedback":
        specific_key = _to_text(app_config.DIFY_PRACTICE_TURN_FEEDBACK_API_KEY)
    else:
        return ""

    return specific_key or _to_text(app_config.DIFY_API_KEY)


def _resolve_workflow_api_base(name: str) -> str:
    if name == "practice_mentor":
        base = _to_text(app_config.DIFY_PRACTICE_MENTOR_API_BASE)
        return (base or _to_text(app_config.DIFY_API_BASE)).rstrip("/")
    return _to_text(app_config.DIFY_API_BASE).rstrip("/")


def _resolve_workflow_timeout(name: str) -> float:
    if name == "practice_mentor":
        return float(app_config.DIFY_PRACTICE_MENTOR_TIMEOUT)
    if name == "practice_turn_feedback":
        return float(app_config.DIFY_PRACTICE_TURN_FEEDBACK_TIMEOUT)
    return float(app_config.DIFY_STAGE4B_TIMEOUT)


def _run_stage4b_chat(
    *,
    chat_name: str,
    user_id: str,
    query: str,
    inputs: dict[str, Any],
    conversation_id: str = "",
) -> dict[str, Any]:
    if app_config.DIFY_STAGE4B_FORCE_MOCK:
        _log.warning("stage4b chat blocked by force_mock chat=%s", chat_name)
        return {"ok": False, "reason": "force_mock", "error": "", "raw": {}}

    api_key = _to_text(app_config.DIFY_PRACTICE1_API_KEY) or _to_text(app_config.DIFY_API_KEY)
    if not api_key:
        _log.error("stage4b chat missing api_key chat=%s", chat_name)
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _to_text(app_config.DIFY_API_BASE).rstrip("/")
    if not base:
        _log.error("stage4b chat missing api_base chat=%s", chat_name)
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}

    url = f"{base}/v1/chat-messages"
    payload: dict[str, Any] = {
        "inputs": {str(k): "" if v is None else str(v) for k, v in (inputs or {}).items()},
        "query": _to_text(query),
        # blocking 模式：Dify 返回完整 JSON，避免 nginx 代理缓冲导致 SSE 事件丢失（仅收到 ping）。
        "response_mode": "blocking",
        "user": _to_text(user_id) or "stage4b-user",
    }
    cid = _to_text(conversation_id)
    if cid:
        payload["conversation_id"] = cid

    timeout_sec = (
        app_config.DIFY_PRACTICE1_TIMEOUT
        if chat_name == "practice1"
        else app_config.DIFY_STAGE4B_TIMEOUT
    )
    t0 = time.perf_counter()
    _log.info(
        "stage4b chat request chat=%s url=%s input_keys=%s conversation_id=%s timeout=%.1fs",
        chat_name,
        url,
        sorted((inputs or {}).keys()),
        cid[:64],
        timeout_sec,
    )
    try:
        timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=15.0, pool=10.0)
        # 使用 HTTPTransport(retries=0, http2=False) 禁用连接池复用和HTTP/2，避免 Dify nginx 代理的 keep-alive 502 兼容性问题
        transport = httpx.HTTPTransport(retries=0, http2=False)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            with client.stream(
                "POST",
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                elapsed = time.perf_counter() - t0
                ctype = _to_text(resp.headers.get("content-type")).lower()
                _log.info(
                    "stage4b chat response headers chat=%s http_status=%s elapsed=%.2fs content_type=%s",
                    chat_name,
                    resp.status_code,
                    elapsed,
                    ctype,
                )

                if resp.status_code >= 400:
                    err_body = ""
                    try:
                        err_body = "".join(resp.iter_text())
                    except Exception:
                        err_body = ""
                    detail = ""
                    try:
                        body = json.loads(err_body or "{}")
                        if isinstance(body, dict):
                            detail = _to_text(body.get("message") or body.get("detail"))
                    except Exception:
                        detail = _to_text(err_body)[:500]
                    _log.warning(
                        "stage4b chat non-200: chat=%s http=%s elapsed=%.2fs detail=%s",
                        chat_name,
                        resp.status_code,
                        elapsed,
                        detail[:300],
                    )
                    return {
                        "ok": False,
                        "reason": "dify_non_200",
                        "error": detail or f"http_{resp.status_code}",
                        "raw": {"http_status": resp.status_code, "body": _to_text(err_body)[:2000]},
                    }

                # Dify 返回 JSON（少数环境）时，走原有 JSON 解析
                if "application/json" in ctype:
                    raw_text = "".join(resp.iter_text())
                    raw = json.loads(raw_text) if raw_text else {}
                else:
                    # Dify 返回 SSE（text/event-stream）时，增量提取 message / workflow_finished
                    answer = ""
                    conversation_id_out = ""
                    message_id_out = ""
                    task_id_out = ""
                    sse_events_tail: deque[dict[str, Any]] = deque(maxlen=8)
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            continue
                        payload_text = _to_text(line[5:])
                        if not payload_text or payload_text == "[DONE]":
                            continue
                        try:
                            event_obj = json.loads(payload_text)
                        except Exception:
                            continue
                        if isinstance(event_obj, dict):
                            sse_events_tail.append(event_obj)

                            conversation_id_out = (
                                _to_text(event_obj.get("conversation_id")) or conversation_id_out
                            )
                            message_id_out = _to_text(event_obj.get("message_id")) or message_id_out
                            task_id_out = _to_text(event_obj.get("task_id")) or task_id_out

                            evt = _to_text(event_obj.get("event")).lower()
                            if evt == "message":
                                answer = _to_text(event_obj.get("answer")) or answer
                            elif evt == "workflow_finished":
                                data_obj = (
                                    event_obj.get("data")
                                    if isinstance(event_obj.get("data"), dict)
                                    else {}
                                )
                                if not answer:
                                    answer = _to_text(data_obj.get("answer"))
                                if not answer and isinstance(data_obj.get("outputs"), dict):
                                    answer = _to_text(data_obj["outputs"].get("answer"))
                                if answer:
                                    break

                    raw = {
                        "event_stream": True,
                        "conversation_id": conversation_id_out,
                        "message_id": message_id_out,
                        "task_id": task_id_out,
                        "answer": answer,
                        "events_tail": list(sse_events_tail),
                    }
    except Exception as e:
        _log.exception("stage4b chat call failed: %s", chat_name)
        return {"ok": False, "reason": "dify_exception", "error": _to_text(e), "raw": {}}

    if not isinstance(raw, dict):
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}

    answer = _strip_reasoning_block(_to_text(raw.get("answer")))
    if not answer:
        return {"ok": False, "reason": "empty_answer", "error": "", "raw": raw}

    return {
        "ok": True,
        "reason": "",
        "error": "",
        "raw": raw,
        "data": {
            "answer": answer,
            "conversation_id": _to_text(raw.get("conversation_id")),
            "message_id": _to_text(raw.get("message_id") or raw.get("id")),
            "task_id": _to_text(raw.get("task_id")),
        },
    }


def _run_stage4b_workflow(
    *,
    workflow_name: str,
    user_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if app_config.DIFY_STAGE4B_FORCE_MOCK:
        _log.warning("stage4b workflow blocked by force_mock workflow=%s", workflow_name)
        return {"ok": False, "reason": "force_mock", "error": "", "raw": {}}

    api_key = _resolve_workflow_api_key(workflow_name)
    if not api_key:
        _log.error("stage4b workflow missing api_key workflow=%s", workflow_name)
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _resolve_workflow_api_base(workflow_name)
    if not base:
        _log.error("stage4b workflow missing api_base workflow=%s", workflow_name)
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}
    timeout_sec = _resolve_workflow_timeout(workflow_name)

    _log.info(
        "stage4b workflow prepare dify call workflow=%s url=%s input_keys=%s timeout=%.1fs",
        workflow_name,
        f"{base}/v1/workflows/run",
        sorted((inputs or {}).keys()),
        timeout_sec,
    )

    try:
        raw = run_workflow_blocking(
            base_url=base,
            api_key=api_key,
            inputs={str(k): "" if v is None else str(v) for k, v in (inputs or {}).items()},
            user=_to_text(user_id) or "stage4b-user",
            workflow_id=None,
            timeout_sec=timeout_sec,
        )
    except Exception as e:
        _log.exception("stage4b workflow call failed: %s", workflow_name)
        return {"ok": False, "reason": "dify_exception", "error": _to_text(e), "raw": {}}

    if not isinstance(raw, dict):
        _log.warning("stage4b workflow invalid response workflow=%s raw_type=%s", workflow_name, type(raw).__name__)
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}

    code = raw.get("code")
    if code != 200:
        _log.warning(
            "stage4b workflow dify failed workflow=%s code=%s message=%s",
            workflow_name,
            code,
            _to_text(raw.get("message")),
        )
        return {
            "ok": False,
            "reason": "dify_non_200",
            "error": _to_text(raw.get("message")),
            "raw": raw,
        }
    _log.info("stage4b workflow dify success workflow=%s", workflow_name)
    return {"ok": True, "reason": "", "error": "", "raw": raw}


def run_practice1_chat(
    *,
    user_id: str,
    query: str,
    module_code: str,
    scenario_type_select: str,
    scenario_type_custom: str,
    difficulty: str,
    trainee_role: str,
    conversation_id: str = "",
) -> dict[str, Any]:
    call = _run_stage4b_chat(
        chat_name="practice1",
        user_id=user_id,
        query=query,
        inputs={
            "user_id": user_id,
            "module_code": module_code,
            "scenario_type_select": scenario_type_select,
            "scenario_type_custom": scenario_type_custom,
            "difficulty": difficulty,
            "trainee_role": trainee_role,
        },
        conversation_id=conversation_id,
    )
    if not call.get("ok"):
        return call

    data = call.get("data") if isinstance(call.get("data"), dict) else {}
    call["data"] = {
        "assistant_reply": _strip_reasoning_block(_to_text(data.get("answer"))),
        "conversation_id": _to_text(data.get("conversation_id")),
        "message_id": _to_text(data.get("message_id")),
        "task_id": _to_text(data.get("task_id")),
        "end_flag": 1 if PRACTICE_END_MARKER in _to_text(data.get("answer")) else 0,
    }
    return call


def run_assistant_chat(
    *,
    user_id: str,
    query: str,
    scene_input: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    """在岗助手 chat 模式调用 Dify chat-messages API，支持对话上下文和 turn_feedback。"""
    api_key = _to_text(app_config.DIFY_ASSISTANT_CHAT_API_KEY) or _to_text(app_config.DIFY_API_KEY)
    if not api_key:
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _to_text(app_config.DIFY_ASSISTANT_CHAT_API_BASE).rstrip("/")
    if not base:
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}

    timeout_sec = float(app_config.DIFY_ASSISTANT_CHAT_TIMEOUT)

    payload: dict[str, Any] = {
        "inputs": {"scene_input": scene_input or "", "user_id": _to_text(user_id) or ""},
        "query": _to_text(query),
        "response_mode": "blocking",
        "user": _to_text(user_id) or "assistant-chat-user",
    }
    cid = _to_text(conversation_id)
    if cid:
        payload["conversation_id"] = cid

    t0 = time.perf_counter()
    url = f"{base}/v1/chat-messages"
    _log.info(
        "assistant chat request url=%s conversation_id=%s timeout=%.1fs",
        url, cid[:64], timeout_sec,
    )
    try:
        timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=15.0, pool=10.0)
        transport = httpx.HTTPTransport(retries=0, http2=False)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            with client.stream(
                "POST", url, json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            ) as resp:
                elapsed = time.perf_counter() - t0
                ctype = _to_text(resp.headers.get("content-type")).lower()
                _log.info(
                    "assistant chat response http=%s elapsed=%.2fs content_type=%s",
                    resp.status_code, elapsed, ctype,
                )
                if resp.status_code >= 400:
                    err_body = ""
                    try:
                        err_body = "".join(resp.iter_text())
                    except Exception:
                        pass
                    detail = ""
                    try:
                        body = json.loads(err_body or "{}")
                        if isinstance(body, dict):
                            detail = _to_text(body.get("message") or body.get("detail"))
                    except Exception:
                        detail = _to_text(err_body)[:500]
                    return {
                        "ok": False, "reason": "dify_non_200",
                        "error": detail or f"http_{resp.status_code}",
                        "raw": {"http_status": resp.status_code, "body": _to_text(err_body)[:2000]},
                    }

                if "application/json" in ctype:
                    raw_text = "".join(resp.iter_text())
                    raw = json.loads(raw_text) if raw_text else {}
                else:
                    answer = ""
                    conversation_id_out = ""
                    message_id_out = ""
                    task_id_out = ""
                    sse_events_tail: deque[dict[str, Any]] = deque(maxlen=8)
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload_text = _to_text(line[5:])
                        if not payload_text or payload_text == "[DONE]":
                            continue
                        try:
                            event_obj = json.loads(payload_text)
                        except Exception:
                            continue
                        if isinstance(event_obj, dict):
                            sse_events_tail.append(event_obj)
                            conversation_id_out = _to_text(event_obj.get("conversation_id")) or conversation_id_out
                            message_id_out = _to_text(event_obj.get("message_id")) or message_id_out
                            task_id_out = _to_text(event_obj.get("task_id")) or task_id_out
                            evt = _to_text(event_obj.get("event")).lower()
                            if evt == "message":
                                answer = _to_text(event_obj.get("answer")) or answer
                            elif evt == "workflow_finished":
                                data_obj = event_obj.get("data") if isinstance(event_obj.get("data"), dict) else {}
                                if not answer:
                                    answer = _to_text(data_obj.get("answer"))
                                if not answer and isinstance(data_obj.get("outputs"), dict):
                                    answer = _to_text(data_obj["outputs"].get("answer"))
                                if answer:
                                    break
                    raw = {
                        "event_stream": True,
                        "conversation_id": conversation_id_out,
                        "message_id": message_id_out,
                        "task_id": task_id_out,
                        "answer": answer,
                        "events_tail": list(sse_events_tail),
                    }
    except Exception as e:
        _log.exception("assistant chat call failed")
        return {"ok": False, "reason": "dify_exception", "error": _to_text(e), "raw": {}}

    if not isinstance(raw, dict):
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}

    answer = _strip_reasoning_block(_to_text(raw.get("answer")))
    if not answer:
        return {"ok": False, "reason": "empty_answer", "error": "", "raw": raw}

    # 从 answer 中解析 ---TF--- 分隔符，提取 turn_feedback JSON
    assistant_reply = answer
    turn_feedback_json = ""
    import re as _re
    tf_marker = "---TF---"
    if tf_marker in answer:
        parts = answer.split(tf_marker, 1)
        assistant_reply = parts[0].strip()
        tf_raw = parts[1].strip() if len(parts) > 1 else ""
        json_match = _re.search(r"\{[\s\S]*\}", tf_raw)
        if json_match:
            turn_feedback_json = json_match.group(0)

    return {
        "ok": True, "reason": "", "error": "",
        "raw": raw,
        "data": {
            "assistant_reply": assistant_reply,
            "conversation_id": _to_text(raw.get("conversation_id")),
            "message_id": _to_text(raw.get("message_id") or raw.get("id")),
            "task_id": _to_text(raw.get("task_id")),
            "turn_feedback_json": turn_feedback_json,
        },
    }


def run_qa_chat(
    *,
    user_id: str,
    query: str,
    conversation_id: str = "",
) -> dict[str, Any]:
    """知识问答 chat 模式调用 Dify chat-messages API，支持 turn_feedback。"""
    api_key = _to_text(app_config.DIFY_QA_CHAT_API_KEY) or _to_text(app_config.DIFY_API_KEY)
    if not api_key:
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _to_text(app_config.DIFY_QA_CHAT_API_BASE).rstrip("/")
    if not base:
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}

    timeout_sec = float(app_config.DIFY_QA_CHAT_TIMEOUT)

    payload: dict[str, Any] = {
        "inputs": {"question": _to_text(query)},
        "query": _to_text(query),
        "response_mode": "blocking",
        "user": _to_text(user_id) or "qa-chat-user",
    }
    cid = _to_text(conversation_id)
    if cid:
        payload["conversation_id"] = cid

    t0 = time.perf_counter()
    url = f"{base}/v1/chat-messages"
    _log.info(
        "qa chat request url=%s conversation_id=%s timeout=%.1fs",
        url, cid[:64], timeout_sec,
    )
    try:
        timeout = httpx.Timeout(connect=10.0, read=timeout_sec, write=15.0, pool=10.0)
        transport = httpx.HTTPTransport(retries=0, http2=False)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            with client.stream(
                "POST", url, json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            ) as resp:
                elapsed = time.perf_counter() - t0
                ctype = _to_text(resp.headers.get("content-type")).lower()
                _log.info(
                    "qa chat response http=%s elapsed=%.2fs content_type=%s",
                    resp.status_code, elapsed, ctype,
                )
                if resp.status_code >= 400:
                    err_body = ""
                    try:
                        err_body = "".join(resp.iter_text())
                    except Exception:
                        pass
                    detail = ""
                    try:
                        body = json.loads(err_body or "{}")
                        if isinstance(body, dict):
                            detail = _to_text(body.get("message") or body.get("detail"))
                    except Exception:
                        detail = _to_text(err_body)[:500]
                    return {
                        "ok": False, "reason": "dify_non_200",
                        "error": detail or f"http_{resp.status_code}",
                        "raw": {"http_status": resp.status_code, "body": _to_text(err_body)[:2000]},
                    }

                if "application/json" in ctype:
                    raw_text = "".join(resp.iter_text())
                    raw = json.loads(raw_text) if raw_text else {}
                else:
                    answer = ""
                    conversation_id_out = ""
                    message_id_out = ""
                    task_id_out = ""
                    sse_events_tail: deque[dict[str, Any]] = deque(maxlen=8)
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload_text = _to_text(line[5:])
                        if not payload_text or payload_text == "[DONE]":
                            continue
                        try:
                            event_obj = json.loads(payload_text)
                        except Exception:
                            continue
                        if isinstance(event_obj, dict):
                            sse_events_tail.append(event_obj)
                            conversation_id_out = _to_text(event_obj.get("conversation_id")) or conversation_id_out
                            message_id_out = _to_text(event_obj.get("message_id")) or message_id_out
                            task_id_out = _to_text(event_obj.get("task_id")) or task_id_out
                            evt = _to_text(event_obj.get("event")).lower()
                            if evt == "message":
                                answer = _to_text(event_obj.get("answer")) or answer
                            elif evt == "workflow_finished":
                                data_obj = event_obj.get("data") if isinstance(event_obj.get("data"), dict) else {}
                                if not answer:
                                    answer = _to_text(data_obj.get("answer"))
                                if not answer and isinstance(data_obj.get("outputs"), dict):
                                    answer = _to_text(data_obj["outputs"].get("answer"))
                                if answer:
                                    break
                    raw = {
                        "event_stream": True,
                        "conversation_id": conversation_id_out,
                        "message_id": message_id_out,
                        "task_id": task_id_out,
                        "answer": answer,
                        "events_tail": list(sse_events_tail),
                    }
    except Exception as e:
        _log.exception("qa chat call failed")
        return {"ok": False, "reason": "dify_exception", "error": _to_text(e), "raw": {}}

    if not isinstance(raw, dict):
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}

    answer = _strip_reasoning_block(_to_text(raw.get("answer")))
    if not answer:
        return {"ok": False, "reason": "empty_answer", "error": "", "raw": raw}

    qa_answer = answer
    turn_feedback_json = ""
    import re as _re
    tf_marker = "---TF---"
    if tf_marker in answer:
        parts = answer.split(tf_marker, 1)
        qa_answer = parts[0].strip()
        tf_raw = parts[1].strip() if len(parts) > 1 else ""
        json_match = _re.search(r"\{[\s\S]*\}", tf_raw)
        if json_match:
            turn_feedback_json = json_match.group(0)

    return {
        "ok": True, "reason": "", "error": "",
        "raw": raw,
        "data": {
            "qa_answer": qa_answer,
            "conversation_id": _to_text(raw.get("conversation_id")),
            "message_id": _to_text(raw.get("message_id") or raw.get("id")),
            "task_id": _to_text(raw.get("task_id")),
            "turn_feedback_json": turn_feedback_json,
        },
    }


def run_practice2_workflow(
    *,
    user_id: str,
    practice_id: str,
    trainee_name: str,
    scenario_type: str,
    target_focus: str,
    dialogue_text: str,
    performance_gap_summary: str = "",
    target_metric: str = "",
    recommended_action: str = "",
) -> dict[str, Any]:
    call = _run_stage4b_workflow(
        workflow_name="practice2",
        user_id=user_id,
        inputs={
            "practice_id": practice_id,
            "user_id": user_id,
            "trainee_name": trainee_name,
            "scenario_type": scenario_type,
            "target_focus": target_focus,
            "dialogue_text": dialogue_text,
            "performance_gap_summary": performance_gap_summary,
            "target_metric": target_metric,
            "recommended_action": recommended_action,
        },
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    evaluation_raw = _pick_value(data, outputs, "evaluation_result")
    evaluation_obj = _parse_json_like(evaluation_raw)

    call["data"] = {
        "evaluation_status": _to_text(_pick_value(data, outputs, "evaluation_status"))
        or _to_text(evaluation_obj.get("evaluation_status"))
        or "unknown",
        "evaluation_result": evaluation_obj,
        "overall_score": _to_float(_pick_value(data, outputs, "overall_score"), 0.0),
        "pass_flag": _to_int(_pick_value(data, outputs, "pass_flag"), 0),
        "level": _to_text(_pick_value(data, outputs, "level"))
        or _to_text(evaluation_obj.get("level")),
        "risk_level": _to_text(_pick_value(data, outputs, "risk_level"))
        or _to_text(evaluation_obj.get("risk_level")),
        "need_insight_score": _to_int(_pick_value(data, outputs, "need_insight_score"), 0),
        "product_accuracy_score": _to_int(_pick_value(data, outputs, "product_accuracy_score"), 0),
        "objection_handling_score": _to_int(
            _pick_value(data, outputs, "objection_handling_score"), 0
        ),
        "closing_push_score": _to_int(_pick_value(data, outputs, "closing_push_score"), 0),
        "expression_natural_score": _to_int(
            _pick_value(data, outputs, "expression_natural_score"), 0
        ),
        "compliance_safety_score": _to_int(
            _pick_value(data, outputs, "compliance_safety_score"), 0
        ),
        "weak_dimension": _to_text(_pick_value(data, outputs, "weak_dimension"))
        or _to_text(evaluation_obj.get("weak_dimension")),
        "sample_status": _to_text(_pick_value(data, outputs, "sample_status")),
        "sample_char_count": _to_int(_pick_value(data, outputs, "sample_char_count"), 0),
        "sample_round_count": _to_int(_pick_value(data, outputs, "sample_round_count"), 0),
        "risk_tags": _to_list(_pick_value(data, outputs, "risk_tags")),
        "highlights": _to_list(_pick_value(data, outputs, "highlights")),
        "problem_points": _to_list(_pick_value(data, outputs, "problem_points")),
        "storage_tags": _to_list(_pick_value(data, outputs, "storage_tags")),
        "improvement_advice": _to_text(_pick_value(data, outputs, "improvement_advice")),
        "concise_feedback": _to_text(_pick_value(data, outputs, "concise_feedback")),
        "followup_training": _to_text(_pick_value(data, outputs, "followup_training")),
        "raw_llm_output": _to_text(_pick_value(data, outputs, "raw_llm_output")),
    }
    return call


def run_practice_mentor_workflow(
    *,
    user_id: str,
    practice_id: str,
    trainee_name: str,
    scenario_type: str,
    dialogue_text: str,
    overall_score: str = "",
    highlights: str = "",
    problem_points: str = "",
    improvement_advice: str = "",
) -> dict[str, Any]:
    call = _run_stage4b_workflow(
        workflow_name="practice_mentor",
        user_id=user_id,
        inputs={
            "practice_id": practice_id,
            "user_id": user_id,
            "trainee_name": trainee_name,
            "scenario_type": scenario_type,
            "dialogue_text": dialogue_text,
            "overall_score": overall_score,
            "highlights": highlights,
            "problem_points": problem_points,
            "improvement_advice": improvement_advice,
        },
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    call["data"] = {
        "mentor_sentence": _to_text(_pick_value(data, outputs, "mentor_sentence")),
    }
    return call


def run_practice_turn_feedback(
    *,
    user_id: str,
    user_message: str,
    assistant_reply: str,
    round_count: int,
    scenario_type: str,
) -> dict[str, Any]:
    call = _run_stage4b_workflow(
        workflow_name="practice_turn_feedback",
        user_id=user_id,
        inputs={
            "user_id": user_id,
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "round_count": str(round_count),
            "scenario_type": scenario_type,
        },
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    turn_feedback_raw = _to_text(_pick_value(data, outputs, "turn_feedback_json"))
    turn_feedback_obj = _parse_json_like(turn_feedback_raw)

    call["data"] = {
        "turn_feedback": turn_feedback_obj if isinstance(turn_feedback_obj, dict) else {},
    }
    return call


def run_practice3_workflow(
    *,
    user_id: str,
    practice_id: str,
    module_code: str,
    module_name: str,
    cycle_id: str,
    stage_no: int,
    scenario_type: str,
    evaluation_result: str,
    final_score: float,
    evaluator_feedback: str,
    risk_level: str,
    risk_tags_json: str,
    weak_dimension: str,
    followup_training: str,
    concise_feedback: str,
    practice_turns: int,
    conversation_text: str,
    current_product_knowledge_score: float,
    current_compliance_score: float,
    current_sales_communication_score: float,
    current_response_score: float,
) -> dict[str, Any]:
    call = _run_stage4b_workflow(
        workflow_name="practice3",
        user_id=user_id,
        inputs={
            "practice_id": practice_id,
            "user_id": user_id,
            "module_code": module_code,
            "module_name": module_name,
            "cycle_id": cycle_id,
            "stage_no": stage_no,
            "scenario_type": scenario_type,
            "scenario_code": scenario_type,
            "evaluation_result": evaluation_result,
            "final_score": final_score,
            "evaluator_feedback": evaluator_feedback,
            "risk_level": risk_level,
            "risk_tag": risk_level,
            "risk_tags_json": risk_tags_json,
            "weak_dimension": weak_dimension,
            "followup_training": followup_training,
            "concise_feedback": concise_feedback,
            "practice_turns": practice_turns,
            "conversation_text": conversation_text,
            "current_product_knowledge_score": current_product_knowledge_score,
            "current_compliance_score": current_compliance_score,
            "current_sales_communication_score": current_sales_communication_score,
            "current_response_score": current_response_score,
        },
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    update_json_raw = _pick_value(data, outputs, "ability_update_json")
    update_json = _parse_json_like(update_json_raw)

    call["data"] = {
        "scenario_name": _to_text(_pick_value(data, outputs, "scenario_name")),
        "product_knowledge_score": _to_float(
            _pick_value(data, outputs, "product_knowledge_score"), 0.0
        ),
        "compliance_score": _to_float(_pick_value(data, outputs, "compliance_score"), 0.0),
        "sales_communication_score": _to_float(
            _pick_value(data, outputs, "sales_communication_score"), 0.0
        ),
        "response_score": _to_float(_pick_value(data, outputs, "response_score"), 0.0),
        "overall_score": _to_float(_pick_value(data, outputs, "overall_score"), 0.0),
        "product_knowledge_delta": _to_int(
            _pick_value(data, outputs, "product_knowledge_delta"), 0
        ),
        "compliance_delta": _to_int(_pick_value(data, outputs, "compliance_delta"), 0),
        "sales_communication_delta": _to_int(
            _pick_value(data, outputs, "sales_communication_delta"), 0
        ),
        "response_delta": _to_int(_pick_value(data, outputs, "response_delta"), 0),
        "risk_level": _to_text(_pick_value(data, outputs, "risk_level")),
        "risk_level_label": _to_text(_pick_value(data, outputs, "risk_level_label")),
        "focus_dimension": _to_text(_pick_value(data, outputs, "focus_dimension")),
        "focus_dimension_label": _to_text(
            _pick_value(data, outputs, "focus_dimension_label")
        ),
        "need_manager_followup": _to_bool(_pick_value(data, outputs, "need_manager_followup")),
        "next_training_direction": _to_text(
            _pick_value(data, outputs, "next_training_direction")
        ),
        "update_summary": _to_text(_pick_value(data, outputs, "update_summary")),
        "manager_tip": _to_text(_pick_value(data, outputs, "manager_tip")),
        "ability_update_json": update_json,
    }
    return call


def run_dashboard_workflow(
    *,
    user_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    call = _run_stage4b_workflow(
        workflow_name="dashboard",
        user_id=user_id,
        inputs=inputs,
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    safe_output_obj = _parse_json_like(_pick_value(data, outputs, "safe_output_json"))

    call["data"] = {
        "workflow_status": _to_text(_pick_value(data, outputs, "workflow_status")) or "unknown",
        "risk_level": _to_text(_pick_value(data, outputs, "risk_level"))
        or _to_text(safe_output_obj.get("risk_level")),
        "risk_score": _to_int(_pick_value(data, outputs, "risk_score"), 0),
        "rule_risk_level": _to_text(_pick_value(data, outputs, "rule_risk_level")),
        "rule_risk_score": _to_int(_pick_value(data, outputs, "rule_risk_score"), 0),
        "risk_reason": _to_text(_pick_value(data, outputs, "risk_reason"))
        or _to_text(safe_output_obj.get("risk_reason")),
        "followup_advice": _to_text(_pick_value(data, outputs, "followup_advice"))
        or _to_text(safe_output_obj.get("followup_advice")),
        "next_action": _to_text(_pick_value(data, outputs, "next_action")),
        "action_window": _to_text(_pick_value(data, outputs, "action_window")),
        "coaching_focus": _to_text(_pick_value(data, outputs, "coaching_focus")),
        "manager_attention_needed": _to_bool(
            _pick_value(data, outputs, "manager_attention_needed")
        ),
        "dashboard_tags": _to_list(_pick_value(data, outputs, "dashboard_tags"))
        or _to_list(safe_output_obj.get("dashboard_tags")),
        "observation_note": _to_text(_pick_value(data, outputs, "observation_note"))
        or _to_text(safe_output_obj.get("observation_note")),
        "panel_summary": _to_text(_pick_value(data, outputs, "panel_summary"))
        or _to_text(safe_output_obj.get("panel_summary")),
        "safe_output_json": safe_output_obj,
    }
    return call


def run_query1_workflow(
    *,
    user_id: str,
    store_id: str,
    query_text: str,
    manager_role: str,
    time_anchor: str,
    data_catalog_summary: str = "",
    knowledge_enabled: bool = False,
) -> dict[str, Any]:
    call = _run_stage4b_workflow(
        workflow_name="query1",
        user_id=user_id,
        inputs={
            "store_id": store_id,
            "query_text": query_text,
            "manager_role": manager_role,
            "time_anchor": time_anchor,
            "data_catalog_summary": data_catalog_summary,
            "knowledge_enabled": "yes" if knowledge_enabled else "no",
        },
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    params_json_raw = _pick_value(data, outputs, "params_json", "final_params_json")
    payload_json_raw = _pick_value(data, outputs, "fastapi_payload_json")
    params_obj = _parse_json_like(params_json_raw)
    payload_obj = _parse_json_like(payload_json_raw)
    sql_params_raw = _pick_value(data, outputs, "sql_params_json", "sql_params")
    sql_params_obj = _parse_json_like(sql_params_raw)
    if not isinstance(sql_params_obj, list):
        sql_params_obj = payload_obj.get("params") if isinstance(payload_obj.get("params"), list) else []

    call["data"] = {
        "workflow_status": _to_text(_pick_value(data, outputs, "workflow_status")),
        "route_type": _to_text(_pick_value(data, outputs, "route_type")),
        "query_type": _to_text(_pick_value(data, outputs, "query_type", "final_query_type"))
        or "unsupported",
        "params_json": params_obj,
        "rewritten_query": _to_text(_pick_value(data, outputs, "rewritten_query")),
        "fastapi_payload_json": payload_obj,
        "summary_text": _to_text(_pick_value(data, outputs, "summary_text")),
        "confidence_level": _to_text(_pick_value(data, outputs, "confidence_level")) or "medium",
        "can_query": _to_int(_pick_value(data, outputs, "can_query"), 0),
        "problem_note": _to_text(_pick_value(data, outputs, "problem_note")),
        "sql_query": _to_text(_pick_value(data, outputs, "sql_query", "sql"))
        or _to_text(payload_obj.get("sql")),
        "sql_params_json": sql_params_obj,
        "raw_output": _to_text(_pick_value(data, outputs, "raw_output")),
    }
    return call


_QUERY2_TYPE_CN: dict[str, str] = {
    "training_unfinished_newcomer": "本月新人培训未完成",
    "training_incomplete_staff": "培训未完成员工",
    "assessment_incomplete_staff": "考试未完成员工",
    "low_compliance_staff": "低合规分员工",
    "practice_decline_staff": "陪练成绩下降员工",
    "recent_high_risk_staff": "最近高风险员工",
    "followup_priority_staff": "重点跟进员工",
    "high_freq_knowledge_tag_staff": "高频提问某类知识点员工",
    "generic_sql": "自定义数据查询",
}

_QUERY2_STATUS_CN: dict[str, str] = {
    "success": "成功",
    "empty": "空结果",
    "error": "异常",
}


def run_query2_workflow(
    *,
    user_id: str,
    store_id: str,
    query_type: str,
    user_query: str,
    params_json: dict[str, Any],
    query_status: str,
    result_count: int,
    result_json: list[dict[str, Any]],
    error_message: str,
) -> dict[str, Any]:
    cn_query_type = _QUERY2_TYPE_CN.get(query_type, query_type)
    cn_query_status = _QUERY2_STATUS_CN.get(query_status, query_status)
    call = _run_stage4b_workflow(
        workflow_name="query2",
        user_id=user_id,
        inputs={
            "user_id": user_id,
            "store_id": store_id,
            "query_type": cn_query_type,
            "user_query": user_query,
            "params_json": json.dumps(params_json or {}, ensure_ascii=False),
            "query_status": cn_query_status,
            "result_count": result_count,
            "result_json": json.dumps(result_json or [], ensure_ascii=False),
            "error_message": error_message,
        },
    )
    if not call.get("ok"):
        return call

    data, outputs = _extract_call_fields(call)
    safe_output_obj = _parse_json_like(_pick_value(data, outputs, "safe_output_json"))

    focus_names = _to_list(_pick_value(data, outputs, "focus_names_json"))
    if not focus_names:
        focus_names = _to_list(safe_output_obj.get("focus_names"))
    display_tags = _to_list(_pick_value(data, outputs, "display_tags_json"))
    if not display_tags:
        display_tags = _to_list(safe_output_obj.get("display_tags"))

    call["data"] = {
        "workflow_status": _to_text(_pick_value(data, outputs, "workflow_status")),
        "query_type": _to_text(_pick_value(data, outputs, "query_type")) or query_type,
        "result_count": _to_int(_pick_value(data, outputs, "result_count"), result_count),
        "summary": _to_text(_pick_value(data, outputs, "summary"))
        or _to_text(safe_output_obj.get("summary")),
        "manager_advice": _to_text(_pick_value(data, outputs, "manager_advice"))
        or _to_text(safe_output_obj.get("manager_advice")),
        "focus_names": focus_names,
        "display_tags": display_tags,
        "user_visible_output": _to_text(_pick_value(data, outputs, "user_visible_output"))
        or _to_text(safe_output_obj.get("user_visible_output")),
        "confidence_level": _to_text(_pick_value(data, outputs, "confidence_level")) or "medium",
        "raw_output": _to_text(_pick_value(data, outputs, "raw_output")),
        "safe_output_json": safe_output_obj,
    }
    return call

