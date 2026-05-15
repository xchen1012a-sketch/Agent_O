"""调用 Dify Workflow Run（Agent1），解析 outputs / api_response。"""
from __future__ import annotations

import json
import logging
import time
import httpx
from typing import Any

from config import _mask_secret

_logger = logging.getLogger("jewelry_qipei.dify")

# 子流返回非数字 code（如 UNKNOWN_INTENT）或解析异常时的默认用户可见文案
_FALLBACK_CHAT_REPLY = (
    "抱歉，我有点没理解您的意思，请详细描述您的珠宝培训问题。"
)
# 供 main 等模块兜底响应复用（与上文同文）
FALLBACK_AGENT_CHAT_REPLY = _FALLBACK_CHAT_REPLY

# 上游模型/知识检索失败经 sanitize 后的统一用户可见文案（与 apply_upstream_error_fallback_workflow 联动）
UPSTREAM_UNAVAILABLE_REPLY = (
    "抱歉，当前智能回答服务暂时不可用（上游模型或知识检索返回异常）。请稍后再试、换种问法，或联系管理员检查 Dify 子流与模型 API 配置。"
)


def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    inputs = payload.get("inputs")
    safe_inputs = inputs if isinstance(inputs, dict) else {}
    return {
        "response_mode": payload.get("response_mode"),
        "user": str(payload.get("user") or "")[:64],
        "conversation_id": str(payload.get("conversation_id") or "")[:64],
        "workflow_id": str(payload.get("workflow_id") or "")[:64],
        "input_keys": sorted(safe_inputs.keys()),
    }


def _summarize_response_body(payload: Any) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = repr(payload)
    return text[:800]


def extract_workflow_conversation_id(dify_raw: Any) -> str:
    """
    从 Dify workflows/run 原始 JSON 中解析 conversation_id（不同版本字段位置可能不同）。
    """
    if not isinstance(dify_raw, dict):
        return ""
    v = dify_raw.get("conversation_id")
    if isinstance(v, str) and v.strip():
        return v.strip()
    d = dify_raw.get("data")
    if isinstance(d, dict):
        v2 = d.get("conversation_id")
        if isinstance(v2, str) and v2.strip():
            return v2.strip()
        for sub_key in ("workflow_run", "workflow_run_result", "task", "workflow"):
            sub = d.get(sub_key)
            if isinstance(sub, dict):
                v3 = sub.get("conversation_id")
                if isinstance(v3, str) and v3.strip():
                    return v3.strip()
    return ""


def _attach_conversation_id_to_result(
    out: dict[str, Any], dify_raw: dict[str, Any]
) -> None:
    cid = extract_workflow_conversation_id(dify_raw)
    if not cid:
        return
    data = out.get("data")
    if not isinstance(data, dict):
        return
    data["dify_conversation_id"] = cid
    if not (
        isinstance(data.get("conversation_id"), str)
        and str(data.get("conversation_id") or "").strip()
    ):
        data["conversation_id"] = cid


def _try_parse_int_code(raw: Any) -> int | None:
    """将子流契约中的 code 解析为整数；非数字字符串（如 UNKNOWN_INTENT）返回 None。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit() or (s.startswith("-") and len(s) > 1 and s[1:].isdigit()):
            try:
                return int(s)
            except ValueError:
                return None
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sanitize_leaked_transport_error_in_content(data: dict[str, Any]) -> None:
    """子流契约 code=200 时，部分节点仍会把上游英文错误写进 data.content，需与 message 分支一致地替换。"""
    if not isinstance(data, dict):
        return
    c = data.get("content")
    if not isinstance(c, str) or not c.strip():
        return
    if not _looks_like_upstream_transport_error(c):
        return
    data["raw_leaked_upstream_text"] = c[:2000]
    data["content"] = UPSTREAM_UNAVAILABLE_REPLY
    data["upstream_error_fallback_eligible"] = True




def _build_upstream_transport_failure(
    data: dict[str, Any], *, raw_message: str = "", raw_code: int | None = None
) -> dict[str, Any]:
    payload = dict(data or {})
    payload.setdefault("content", UPSTREAM_UNAVAILABLE_REPLY)
    payload["upstream_error_fallback_eligible"] = True
    msg = str(raw_message or "").strip()
    if msg:
        payload.setdefault("detail", msg[:2000])
        payload.setdefault("raw_upstream_message", msg[:2000])
    if raw_code is not None:
        payload.setdefault("raw_upstream_code", raw_code)
    return {"code": 500, "message": "dify_request_failed", "data": payload}

def _looks_like_upstream_transport_error(msg: str) -> bool:
    """
    子流 HTTP/模型节点失败时，message 常为英文技术文案（如 axios/OpenAI 插件返回的 status code）。
    这类不应作为学员可见正文透出；业务侧中文错误一般保留。
    """
    m = (msg or "").strip()
    if not m:
        return True
    if any("\u4e00" <= c <= "\u9fff" for c in m):
        return False
    low = m.lower()
    needles = (
        "status code",
        "request failed",
        "failed to invoke",
        "maximum retries",
        "max retries",
        "reached maximum retries",
        "httperror",
        "timeout",
        "timed out",
        "connection refused",
        "econnrefused",
        "name or service not known",
        "temporary failure in name resolution",
        "nodename nor servname provided",
        "could not resolve host",
        "no such host",
        "host.docker.internal",
        "rate limit",
        "invalid api",
        "internal server error",
    )
    return any(n in low for n in needles)


def _extract_inner_user_message(inner: dict[str, Any]) -> str:
    """子流常用 message；部分画布使用 msg。"""
    for key in ("message", "msg"):
        v = inner.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    data = inner.get("data")
    if isinstance(data, dict):
        c = data.get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ""


def extract_outputs(resp: dict[str, Any]) -> dict[str, Any]:
    if not resp:
        return {}
    data = resp.get("data")
    if isinstance(data, dict):
        outs = data.get("outputs")
        if isinstance(outs, dict):
            return outs
    outs = resp.get("outputs")
    if isinstance(outs, dict):
        return outs
    return {}


def _pick_first_nonempty_text(
    data: dict[str, Any], outputs: dict[str, Any], aliases: tuple[str, ...]
) -> str:
    for key in aliases:
        if key in data:
            value = data.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        if key in outputs:
            value = outputs.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def extract_minimal_text_fields(
    normalized_result: dict[str, Any],
    alias_map: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    """
    从 run_workflow_blocking 的归一化结果中提取最小字段集合（在岗助手等仅取展示字段，避免把整段 Dify 原文透给前端）。
    优先 data 中的业务字段，其次 raw_outputs 中同名字段。
    """
    data = normalized_result.get("data") if isinstance(normalized_result, dict) else {}
    if not isinstance(data, dict):
        data = {}
    outputs = data.get("raw_outputs")
    if not isinstance(outputs, dict):
        outputs = {}
    picked: dict[str, str] = {}
    for target_key, aliases in alias_map.items():
        picked[target_key] = _pick_first_nonempty_text(data, outputs, aliases)
    return picked


def _unwrap_to_contract_inner(obj: Any) -> dict[str, Any] | None:
    """
    HTTP 子流节点有时返回 Dify workflows/run 的完整 JSON（含 task_id、data.outputs），
    真正契约在 data.outputs.api_response 字符串里；剥壳直到出现 {code, data}。
    """
    if not isinstance(obj, dict):
        return None
    if "code" in obj:
        return obj
    d = obj.get("data")
    if not isinstance(d, dict):
        return None
    outs = d.get("outputs")
    if not isinstance(outs, dict):
        return None
    ar = outs.get("api_response")
    if isinstance(ar, str):
        try:
            return _unwrap_to_contract_inner(json.loads(ar))
        except json.JSONDecodeError:
            return None
    if isinstance(ar, dict):
        return _unwrap_to_contract_inner(ar)
    return None


def _enrich_exam_content_from_contract_data(data: dict[str, Any]) -> None:
    """
    考核子流出卷契约：data.content 与 data.paper_content 常为同一套试卷 JSON。
    若 content 缺失 questions（含被截断、空串），则用 paper_content / exam_paper 补全，供前端 safeParseExamData。
    """
    if not isinstance(data, dict):
        return
    c = data.get("content")
    if isinstance(c, str) and '"questions"' in c:
        return
    for key in ("paper_content", "exam_paper"):
        pc = data.get(key)
        if isinstance(pc, str) and pc.strip() and '"questions"' in pc:
            data["content"] = pc.strip()
            return


def _extract_full_contract_text_from_outputs(outputs: dict[str, Any]) -> str | None:
    """
    子流结束节点常同时输出 api_response（完整）与 final_answer（可能被画布截断，如 [:4000]）。
    回退路径须优先解析完整 JSON 字段，否则前端只能拿到半截 JSON，无法渲染试卷。
    """
    if not isinstance(outputs, dict):
        return None
    for key in ("api_response", "result", "text"):
        raw = outputs.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        s = raw.strip()
        if not s.startswith("{"):
            continue
        try:
            o = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict) or o.get("code") != 200:
            continue
        inner = o.get("data")
        if not isinstance(inner, dict):
            continue
        for pk in ("paper_content", "content"):
            v = inner.get(pk)
            if isinstance(v, str) and v.strip() and '"questions"' in v:
                return v.strip()
    return None


def parse_nested_api_response(outputs: dict[str, Any]) -> dict[str, Any] | None:
    """从 Agent1 结束节点 outputs 解析子流契约；支持 api_response / result 及 workflows/run 外壳。"""
    for key in ("api_response", "result", "text"):
        raw = outputs.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            inner = raw
        elif isinstance(raw, str):
            try:
                inner = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            continue
        contract = _unwrap_to_contract_inner(inner)
        if contract is not None:
            inner = contract
        if isinstance(inner, dict) and "code" in inner:
            return inner
    return None


def _normalize_agent1_result_impl(dify_resp: dict[str, Any]) -> dict[str, Any]:
    """
    将 Dify workflows/run 响应转为前端可用的统一结构：
    { "code", "message", "data": { "content", "raw_outputs", ... } }
    """
    if not isinstance(dify_resp, dict):
        return {
            "code": 500,
            "message": "dify_invalid_response",
            "data": {
                "content": "【Dify 响应异常】返回体不是 JSON 对象",
                "detail": "response_not_dict",
                "raw_outputs": {},
            },
        }

    # 工作流失败时 Dify 常返回 status=failed、outputs={}，正文在 data.error（模型欠费、子流异常等）
    wr = dify_resp.get("data")
    if isinstance(wr, dict):
        st = str(wr.get("status") or "").lower()
        if st == "failed":
            err = str(wr.get("error") or "").strip() or "工作流执行失败（无详细说明）"
            return {
                "code": 500,
                "message": "dify_workflow_failed",
                "data": {
                    "content": "【Dify 工作流失败】" + err,
                    "detail": err[:2000],
                    "workflow_status": st,
                    "raw_outputs": wr.get("outputs") if isinstance(wr.get("outputs"), dict) else {},
                },
            }

    outputs = extract_outputs(dify_resp)
    inner = parse_nested_api_response(outputs)
    if inner and isinstance(inner, dict) and "code" in inner:
        data = inner.get("data")
        if not isinstance(data, dict):
            data = {}
        data = dict(data)
        data.setdefault("raw_outputs", outputs)

        parsed = _try_parse_int_code(inner.get("code"))
        if parsed is None:
            # 子流返回字符串 code（如 UNKNOWN_INTENT），勿映射为 HTTP 业务码 500
            raw_sym = str(inner.get("code") or "").strip().upper()
            if raw_sym in ("UNKNOWN_INTENT", "UNKNOWN"):
                # 与 DSL 中「无法识别意图」结束节点区分：默认先给占位；若配置了 DIFY_INTENT_FALLBACK_WORKFLOW_ID，main 会再调一条 LLM 工作流覆盖
                data["content"] = _FALLBACK_CHAT_REPLY
                data["intent_unknown"] = True
            else:
                user_line = _extract_inner_user_message(inner)
                data.setdefault("content", user_line or _FALLBACK_CHAT_REPLY)
            return {"code": 200, "message": "success", "data": data}

        msg = _extract_inner_user_message(inner) or "success"
        if parsed != 200 and _looks_like_upstream_transport_error(msg):
            return _build_upstream_transport_failure(
                data,
                raw_message=msg,
                raw_code=parsed,
            )
        if parsed == 200:
            _sanitize_leaked_transport_error_in_content(data)
            if data.get("upstream_error_fallback_eligible"):
                return _build_upstream_transport_failure(
                    data,
                    raw_message=str(data.get("raw_leaked_upstream_text") or ""),
                )
            _enrich_exam_content_from_contract_data(data)
            return {"code": 200, "message": "success", "data": data}
        return {"code": parsed, "message": msg, "data": data}

    full_text = _extract_full_contract_text_from_outputs(outputs)
    text = full_text
    if text is None:
        text = outputs.get("final_answer") or outputs.get("text") or outputs.get("answer")
    if text is None and outputs:
        for _k, v in outputs.items():
            if isinstance(v, str) and v.strip():
                text = v
                break
    content = str(text) if text is not None else ""
    data_obj: dict[str, Any] = {"content": content, "raw_outputs": outputs}
    _sanitize_leaked_transport_error_in_content(data_obj)
    if data_obj.get("upstream_error_fallback_eligible"):
        return _build_upstream_transport_failure(
            data_obj,
            raw_message=str(data_obj.get("raw_leaked_upstream_text") or ""),
        )
    return {
        "code": 200,
        "message": "success",
        "data": data_obj,
    }


def apply_intent_fallback_workflow(
    first_result: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    fallback_workflow_id: str,
    raw_user_query: str,
    user_id: str,
    dify_user_role: str,
    conversation_id: str,
    timeout_sec: float,
) -> dict[str, Any]:
    """
    Agent1 已判定 UNKNOWN_INTENT 时，可选再调用一条仅含「自然对话/引导」的工作流，用模型生成不生硬的回复。
    开始节点建议变量：user_query, user_id, user_role, conversation_id, fallback_mode（固定 intent_unknown）。
    """
    data = first_result.get("data")
    if not isinstance(data, dict) or not data.get("intent_unknown"):
        return first_result
    wid = (fallback_workflow_id or "").strip()
    if not wid:
        data.pop("intent_unknown", None)
        return first_result
    fb_inputs: dict[str, str] = {
        "user_query": (raw_user_query or "").strip(),
        "user_id": (user_id or "").strip(),
        "user_role": (dify_user_role or "").strip(),
        "conversation_id": (conversation_id or "").strip(),
        "fallback_mode": "intent_unknown",
    }
    try:
        out = run_workflow_blocking(
            base_url=base_url,
            api_key=api_key,
            inputs=fb_inputs,
            user=(user_id or "").strip(),
            conversation_id=(conversation_id or "").strip(),
            workflow_id=wid,
            timeout_sec=timeout_sec,
        )
    except Exception:
        _logger.exception("intent_fallback workflow failed")
        data.pop("intent_unknown", None)
        return first_result
    if isinstance(out.get("data"), dict) and out["data"].get("dify_conversation_id"):
        first_result.setdefault("data", {})["dify_conversation_id"] = out["data"][
            "dify_conversation_id"
        ]
        if not (
            isinstance(first_result["data"].get("conversation_id"), str)
            and str(first_result["data"].get("conversation_id") or "").strip()
        ):
            first_result["data"]["conversation_id"] = out["data"]["dify_conversation_id"]
    od = out.get("data") if isinstance(out.get("data"), dict) else {}
    text = od.get("content") if isinstance(od, dict) else None
    if isinstance(text, str) and text.strip():
        data["content"] = text.strip()
    data.pop("intent_unknown", None)
    return first_result


def apply_upstream_error_fallback_workflow(
    first_result: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    fallback_workflow_id: str,
    raw_user_query: str,
    user_id: str,
    dify_user_role: str,
    conversation_id: str,
    timeout_sec: float,
) -> dict[str, Any]:
    """
    当 Agent1/子流将正文替换为「上游模型或知识检索异常」类统一文案时，可选再跑一条仅 LLM 的工作流，
    在不依赖失败子流的前提下生成自然回复。开始节点建议变量与意图兜底一致，另传 fallback_mode=upstream_error。
    """
    data = first_result.get("data")
    if not isinstance(data, dict) or not data.get("upstream_error_fallback_eligible"):
        return first_result
    wid = (fallback_workflow_id or "").strip()
    if not wid:
        data.pop("upstream_error_fallback_eligible", None)
        return first_result
    fb_inputs: dict[str, str] = {
        "user_query": (raw_user_query or "").strip(),
        "user_id": (user_id or "").strip(),
        "user_role": (dify_user_role or "").strip(),
        "conversation_id": (conversation_id or "").strip(),
        "fallback_mode": "upstream_error",
    }
    try:
        out = run_workflow_blocking(
            base_url=base_url,
            api_key=api_key,
            inputs=fb_inputs,
            user=(user_id or "").strip(),
            conversation_id=(conversation_id or "").strip(),
            workflow_id=wid,
            timeout_sec=timeout_sec,
        )
    except Exception:
        _logger.exception("upstream_error fallback workflow failed")
        data.pop("upstream_error_fallback_eligible", None)
        return first_result
    if isinstance(out.get("data"), dict) and out["data"].get("dify_conversation_id"):
        first_result.setdefault("data", {})["dify_conversation_id"] = out["data"][
            "dify_conversation_id"
        ]
        if not (
            isinstance(first_result["data"].get("conversation_id"), str)
            and str(first_result["data"].get("conversation_id") or "").strip()
        ):
            first_result["data"]["conversation_id"] = out["data"]["dify_conversation_id"]
    od = out.get("data") if isinstance(out.get("data"), dict) else {}
    if od.get("upstream_error_fallback_eligible"):
        _logger.warning("upstream_error fallback workflow still flagged upstream failure; keeping sanitized message")
        data.pop("upstream_error_fallback_eligible", None)
        return first_result
    text = od.get("content") if isinstance(od, dict) else None
    if isinstance(text, str) and text.strip():
        data["content"] = text.strip()
        first_result["code"] = 200
        first_result["message"] = "success"
        data.pop("detail", None)
        data.pop("raw_upstream_code", None)
        data.pop("raw_upstream_message", None)
    data.pop("upstream_error_fallback_eligible", None)
    return first_result


def normalize_agent1_result(dify_resp: dict[str, Any]) -> dict[str, Any]:
    """包装：解析异常时返回明确失败，避免把异常伪装成成功。"""
    try:
        return _normalize_agent1_result_impl(dify_resp)
    except Exception as exc:
        _logger.exception("normalize_agent1_result failed")
        exc_msg = str(exc)[:300]
        return {
            "code": 500,
            "message": "dify_normalize_failed",
            "data": {
                "content": "【Dify 响应解析失败】" + exc_msg,
                "detail": str(exc)[:1000],
                "normalize_error": exc_msg,
                "raw_outputs": {},
            },
        }


def run_workflow_blocking(
    *,
    base_url: str,
    api_key: str,
    inputs: dict[str, str],
    user: str,
    conversation_id: str = "",
    workflow_id: str | None = None,
    timeout_sec: float = 300.0,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/v1/workflows/run"
    inputs_str = {str(k): "" if v is None else str(v) for k, v in inputs.items()}
    body: dict[str, Any] = {
        "inputs": inputs_str,
        "response_mode": "blocking",
        "user": user,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id
    if workflow_id:
        body["workflow_id"] = workflow_id
    t0 = time.perf_counter()
    wf = workflow_id or "(app_default)"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    _logger.info(
        "dify workflows/run request url=%s workflow_id=%s timeout_sec=%.1f api_key=%s payload=%s",
        url,
        wf,
        timeout_sec,
        _mask_secret(api_key),
        _summarize_payload(body),
    )
    # 连接阶段单独限时，避免长时间卡在 TCP；读超时沿用业务配置的 timeout_sec（在岗 1 等可设较短）
    # 使用 HTTPTransport(retries=0) 禁用连接池复用，避免 Dify nginx 代理的 keep-alive 502 兼容性问题
    _timeout = httpx.Timeout(connect=5.0, read=timeout_sec, write=min(60.0, timeout_sec), pool=5.0)
    _transport = httpx.HTTPTransport(retries=0, http2=False)
    try:
        with httpx.Client(timeout=_timeout, transport=_transport) as client:
            resp = client.post(url, json=body, headers=headers)
        elapsed = time.perf_counter() - t0
        text = resp.text
        try:
            data = resp.json()
        except json.JSONDecodeError:
            data = {"detail": text[:2000]}
        _logger.info(
            "dify workflows/run response workflow_id=%s http_status=%s elapsed=%.2fs body=%s",
            wf,
            resp.status_code,
            elapsed,
            _summarize_response_body(data),
        )
        if resp.status_code >= 400:
            return {
                "code": 500,
                "message": "dify_http_error",
                "data": {
                    "http_status": resp.status_code,
                    "elapsed_sec": round(elapsed, 3),
                    "target_url": url,
                    "dify": data,
                },
            }
        out = normalize_agent1_result(data if isinstance(data, dict) else {})
        _attach_conversation_id_to_result(out, data if isinstance(data, dict) else {})
        inner_code = out.get("code", 200)
        if inner_code != 200:
            _logger.warning(
                "dify workflows/run business_failure workflow_id=%s result_code=%s elapsed=%.2fs detail=%s",
                wf,
                inner_code,
                elapsed,
                _summarize_response_body(out),
            )
        else:
            _logger.info(
                "dify ok in %.2fs workflow_id=%s result_code=%s",
                elapsed,
                wf,
                inner_code,
            )
        return out
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - t0
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        err_body: Any = {}
        status_code = response.status_code if response is not None else 0
        error_text = str(exc).strip() or repr(exc)
        error_type = type(exc).__name__
        reason_hint = ""
        if isinstance(exc, httpx.RemoteProtocolError) and elapsed >= 55:
            reason_hint = "upstream_connection_closed_after_long_blocking_wait"
        if response is not None:
            try:
                err_body = response.json()
            except Exception:
                err_body = {"detail": (response.text or "")[:2000]}
        _logger.warning(
            "dify http error workflow_id=%s http_status=%s elapsed=%.2fs error=%s body=%s",
            wf,
            status_code,
            elapsed,
            repr(exc),
            str(err_body)[:500],
        )
        return {
            "code": 500,
            "message": "dify_http_error",
            "data": {
                "http_status": status_code,
                "elapsed_sec": round(elapsed, 3),
                "target_url": url,
                "detail": error_text[:1000],
                "error_type": error_type,
                "reason_hint": reason_hint,
                "dify": err_body,
            },
        }
    except (TimeoutError, OSError) as e:
        elapsed = time.perf_counter() - t0
        _logger.warning(
            "dify request failed workflow_id=%s elapsed=%.2fs error=%s",
            wf,
            elapsed,
            repr(e),
        )
        return {
            "code": 500,
            "message": "dify_request_failed",
            "data": {
                "detail": str(e)[:1000],
                "elapsed_sec": round(elapsed, 3),
                "target_url": url,
            },
        }


def fetch_dify_conversation_messages(
    *,
    base_url: str,
    api_key: str,
    conversation_id: str,
    user: str,
    limit: int = 100,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    """
    调用 Dify GET /v1/messages，拉取某会话下的历史问答。
    返回 { "ok": True, "items": [ {"role","text"}, ... ] } 或 { "ok": False, "error": str, ... }。
    """
    base = base_url.rstrip("/")
    cid = (conversation_id or "").strip()
    uid = (user or "").strip()
    if not cid:
        return {"ok": False, "error": "empty_conversation_id"}
    try:
        params = {
            "conversation_id": cid,
            "user": uid,
            "limit": str(max(1, min(limit, 100))),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.get(f"{base}/v1/messages", params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as e:
        err_body: Any
        try:
            err_body = e.response.json()
        except Exception:
            err_body = {"detail": (e.response.text or "")[:2000]}
        _logger.warning(
            "dify GET /v1/messages HTTPError %s: %s",
            e.response.status_code,
            str(err_body)[:400],
        )
        return {
            "ok": False,
            "error": "dify_http_error",
            "http_status": e.response.status_code,
            "detail": err_body,
        }
    except httpx.RequestError as e:
        _logger.warning("dify GET /v1/messages failed: %s", repr(e))
        return {"ok": False, "error": "dify_request_failed", "detail": str(e)[:800]}
    except Exception as e:
        _logger.warning("dify GET /v1/messages unexpected failed: %s", repr(e))
        return {"ok": False, "error": "dify_unexpected_failed", "detail": str(e)[:800]}

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    # Dify 默认较新在前，画布展示改为时间正序
    rows = list(reversed(rows))
    items: list[dict[str, str]] = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        qtext = m.get("query")
        if isinstance(qtext, str) and qtext.strip():
            items.append({"role": "user", "text": qtext.strip()})
        atext = m.get("answer")
        if isinstance(atext, str) and atext.strip():
            items.append({"role": "assistant", "text": atext.strip()})
    return {"ok": True, "items": items}
