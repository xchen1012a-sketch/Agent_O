"""Shared helpers for Dify integration — used by dify_client, dify_stage4a, dify_stage4b."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

# ------------------------------------------------------------------------------------------
# Type conversion helpers
# ------------------------------------------------------------------------------------------


def _to_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)


def _to_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _to_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _to_list(val: Any) -> list[Any]:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


# ------------------------------------------------------------------------------------------
# JSON parsing helpers
# ------------------------------------------------------------------------------------------


def _parse_json_like(raw: Any) -> dict[str, Any]:
    """
    Parse a value that might be a JSON string, a JSON object, or neither.
    Falls back through: raw dict -> raw string JSON -> match-anything JSON fragment.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {"_list": parsed}
            except json.JSONDecodeError:
                pass
        # Fall back: scan for first {...} block
        m = re.search(r"\{[^{}]*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {}


def _safe_json_loads(v: Any) -> dict[str, Any]:
    """Parse JSON, return {} on failure."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return {}


# ------------------------------------------------------------------------------------------
# Text cleanup helpers
# ------------------------------------------------------------------------------------------

_REASONING_BLOCK_RE = re.compile(
    r"<(?:think|thinking|reasoning)>[\s\S]*?</(?:think|thinking|reasoning)>",
    re.IGNORECASE,
)
_REASONING_LINE_RE = re.compile(
    r"^\s*(?:"
    r"思考(?:过程|如下)?|分析(?:如下)?|推理(?:过程|如下)?|内部思考|解题思路|思路|策略|计划|判断依据|回应策略|输出说明|说明|备注"
    r"|reasoning|analysis|thought(?: process)?|internal(?: reasoning| thought)?|plan|strategy|note|notes"
    r")\s*[:：-]",
    re.IGNORECASE,
)
_CUSTOMER_SPEAKER_RE = re.compile(
    r"(?:^|\n)\s*(?:顾客|客户|客人|买家|消费者|Customer|Client)\s*[:：]",
    re.IGNORECASE,
)


def _strip_reasoning_block(s: str) -> str:
    """
    Remove chain-of-thought / reasoning blocks that Dify sometimes prepends.
    Handles simple markdown/tag wrappers and common reasoning prefixes, then keeps only the
    customer utterance when a speaker marker is present.
    """
    cleaned = _to_text(s)
    cleaned = _REASONING_BLOCK_RE.sub("", cleaned)
    cleaned = re.sub(r"\*\*[^*]*?\*\*", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()
    if not cleaned:
        return ""

    speaker_match = _CUSTOMER_SPEAKER_RE.search(cleaned)
    if speaker_match:
        cleaned = cleaned[speaker_match.start():].strip()
        cleaned = re.sub(r"^(?:顾客|客户|客人|买家|消费者|Customer|Client)\s*[:：]\s*", "", cleaned, count=1, flags=re.IGNORECASE)
        return cleaned.strip()

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    kept_lines = [line for line in lines if not _REASONING_LINE_RE.match(line)]
    if kept_lines:
        return "\n".join(kept_lines).strip()
    return cleaned


# ------------------------------------------------------------------------------------------
# Dict extraction helpers
# ------------------------------------------------------------------------------------------


def _pick_value(data: dict[str, Any], outputs: dict[str, Any], *keys: str) -> Any:
    """Pick the first non-empty value for any of the given keys from data or outputs."""
    _empty = (None, "")
    for key in keys:
        val = data.get(key)
        if val not in _empty:
            return val
        val = outputs.get(key)
        if val not in _empty:
            return val
    return ""


def _extract_data_and_outputs(result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract data and outputs dicts from a Dify response."""
    data = result.get("data") if isinstance(result, dict) else {}
    if not isinstance(data, dict):
        data = {}
    outputs = data.get("raw_outputs") if isinstance(data, dict) else {}
    if not isinstance(outputs, dict):
        outputs = {}
    return data, outputs


# ------------------------------------------------------------------------------------------
# HTTP client factory
# ------------------------------------------------------------------------------------------


def make_dify_client(timeout_sec: float) -> httpx.Client:
    """Create an httpx client configured to avoid Dify nginx keep-alive 502 issues."""
    t = httpx.Timeout(connect=10.0, read=timeout_sec, write=15.0, pool=10.0)
    tr = httpx.HTTPTransport(retries=0, http2=False)
    return httpx.Client(timeout=t, transport=tr)
