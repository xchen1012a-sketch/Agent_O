from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from workflow_registry import (
    AGENT_ROLE_DEFINITIONS,
    AGENT_TOPOLOGY_WORKFLOW_CODES,
    DIFY_WORKFLOW_REGISTRY,
)

_MAX_HISTORY = 24
_MAX_SUBSCRIBER_QUEUE = 32

_history: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_lock = threading.Lock()
_event_seq = 0

_VISIBLE_WORKFLOW_CODES = {
    code
    for codes in AGENT_TOPOLOGY_WORKFLOW_CODES.values()
    for code in codes
}
_AGENT_DEFINITION_BY_ID = {
    str(item.get("id") or "").strip(): item
    for item in AGENT_ROLE_DEFINITIONS
}

_KNOWLEDGE_SOURCE_BY_ROLE = {
    "tutor": "成长计划库",
    "practice": "销售话术库",
    "examiner": "考核题库",
    "service": "珠宝知识库",
    "analyst": "经营数据看板",
    "evolution": "自我进化记忆库",
}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workflow_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    code = _as_text(item.get("code"))
    call_type = _as_text(item.get("call_type"))
    return (
        0 if code in _VISIBLE_WORKFLOW_CODES else 1,
        0 if call_type == "workflow" else 1,
        code,
    )


def _route_workflow_map() -> dict[str, dict[str, Any]]:
    by_route: dict[str, dict[str, Any]] = {}
    for item in DIFY_WORKFLOW_REGISTRY:
        route_path = _as_text(item.get("route_path"))
        if not route_path:
            continue
        current = by_route.get(route_path)
        if current is None or _workflow_priority(item) < _workflow_priority(current):
            by_route[route_path] = item
    return by_route


_WORKFLOW_BY_ROUTE = _route_workflow_map()


def get_agent_workflow_for_route(route_path: str) -> dict[str, Any] | None:
    return _WORKFLOW_BY_ROUTE.get(_as_text(route_path))


def _next_event_id() -> str:
    global _event_seq
    with _lock:
        _event_seq += 1
        return f"agent-activity-{_event_seq}"


def build_agent_activity_event(
    *,
    method: str,
    route_path: str,
    status_code: int,
    request_id: str,
    elapsed_seconds: float,
) -> dict[str, Any] | None:
    workflow = get_agent_workflow_for_route(route_path)
    if workflow is None:
        return None

    agent_role = _as_text(workflow.get("agent_role"))
    definition = _AGENT_DEFINITION_BY_ID.get(agent_role, {})
    elapsed_ms = max(0, int(round(float(elapsed_seconds or 0) * 1000)))
    ok = 200 <= int(status_code or 0) < 400
    return {
        "id": _next_event_id(),
        "type": "agent_call",
        "agent_role": agent_role,
        "agent_label": _as_text(definition.get("label")) or agent_role,
        "agent_name": _as_text(definition.get("agent_name")) or agent_role,
        "agent_color": _as_text(definition.get("color")) or "#2563EB",
        "workflow_code": _as_text(workflow.get("code")),
        "workflow_label": _as_text(workflow.get("label")),
        "route_path": _as_text(route_path),
        "method": _as_text(method).upper(),
        "call_type": _as_text(workflow.get("call_type")) or "workflow",
        "knowledge_source": _KNOWLEDGE_SOURCE_BY_ROLE.get(agent_role, "业务知识库"),
        "status_code": int(status_code or 0),
        "ok": ok,
        "elapsed_ms": elapsed_ms,
        "elapsed_label": f"{elapsed_ms / 1000:.1f}s",
        "request_id": _as_text(request_id),
        "created_at": _now_iso(),
    }


def publish_agent_activity(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not event:
        return None
    payload = dict(event)
    with _lock:
        _history.append(payload)
        subscribers = list(_subscribers)

    for queue in subscribers:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass
    return payload


def publish_agent_activity_from_request(
    *,
    method: str,
    route_path: str,
    status_code: int,
    request_id: str,
    elapsed_seconds: float,
) -> dict[str, Any] | None:
    event = build_agent_activity_event(
        method=method,
        route_path=route_path,
        status_code=status_code,
        request_id=request_id,
        elapsed_seconds=elapsed_seconds,
    )
    return publish_agent_activity(event)


def subscribe_agent_activity() -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_SUBSCRIBER_QUEUE)
    with _lock:
        _subscribers.add(queue)
    return queue


def unsubscribe_agent_activity(queue: asyncio.Queue[dict[str, Any]]) -> None:
    with _lock:
        _subscribers.discard(queue)


def recent_agent_activity(limit: int = 8) -> list[dict[str, Any]]:
    count = max(0, min(int(limit or 0), _MAX_HISTORY))
    with _lock:
        items = list(_history)[-count:]
    return items


def clear_agent_activity_for_tests() -> None:
    global _event_seq
    with _lock:
        _history.clear()
        _event_seq = 0
