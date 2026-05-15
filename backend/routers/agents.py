from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

import config as app_config
from agent_activity import (
    recent_agent_activity,
    subscribe_agent_activity,
    unsubscribe_agent_activity,
)
from api_response import success_response
from auth import decode_token_optional, get_current_user, get_optional_user, normalize_app_role
from workflow_registry import (
    AGENT_ROLE_DEFINITIONS,
    AGENT_TOPOLOGY_EDGES,
    AGENT_TOPOLOGY_WORKFLOW_CODES,
    DIFY_WORKFLOW_REGISTRY,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])

_WORKFLOW_CODE = "agent_topology"
_LOG_TAIL_BYTES = 240_000
_SSE_HEARTBEAT_SECONDS = 15.0


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _configured(env_name: str) -> bool:
    return bool(_as_text(getattr(app_config, env_name, "")))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_log_path() -> Path:
    raw_path = Path(_as_text(getattr(app_config, "LOG_FILE_PATH", "")) or "logs/app.log")
    if raw_path.is_absolute():
        return raw_path
    return Path(__file__).resolve().parents[1] / raw_path


def _count_today_route_hits(route_paths: set[str]) -> dict[str, int]:
    counts = {path: 0 for path in route_paths if path}
    if not counts:
        return counts
    log_path = _resolve_log_path()
    if not log_path.exists() or not log_path.is_file():
        return counts

    today_prefix = datetime.now().strftime("%Y-%m-%d")
    try:
        with log_path.open("rb") as fh:
            try:
                fh.seek(max(log_path.stat().st_size - _LOG_TAIL_BYTES, 0))
            except OSError:
                fh.seek(0)
            text = fh.read().decode("utf-8", errors="ignore")
    except OSError:
        return counts

    for line in text.splitlines():
        if not line.startswith(today_prefix) or "request finish" not in line:
            continue
        for route_path in counts:
            if f"path={route_path}" in line:
                counts[route_path] += 1
    return counts


def _workflow_item(raw: dict[str, Any], route_counts: dict[str, int]) -> dict[str, Any]:
    route_path = _as_text(raw.get("route_path"))
    env_name = _as_text(raw.get("api_key_env"))
    return {
        "code": _as_text(raw.get("code")),
        "label": _as_text(raw.get("label")),
        "route_path": route_path,
        "call_type": _as_text(raw.get("call_type")),
        "api_key_env": env_name,
        "agent_role": _as_text(raw.get("agent_role")),
        "configured": _configured(env_name),
        "today_call_count": int(route_counts.get(route_path, 0)),
    }


def build_agent_topology_payload(current_user: dict[str, Any] | None = None) -> dict[str, Any]:
    registry_by_code = {
        _as_text(item.get("code")): item
        for item in DIFY_WORKFLOW_REGISTRY
        if _as_text(item.get("code"))
    }
    visible_codes = {
        code
        for codes in AGENT_TOPOLOGY_WORKFLOW_CODES.values()
        for code in codes
    }
    route_counts = _count_today_route_hits({
        _as_text(item.get("route_path"))
        for code, item in registry_by_code.items()
        if code in visible_codes
    })

    agents: list[dict[str, Any]] = []
    call_count_by_role: dict[str, int] = {}
    configured_total = 0
    workflow_total = 0

    for definition in AGENT_ROLE_DEFINITIONS:
        role_id = _as_text(definition.get("id"))
        workflows = [
            _workflow_item(registry_by_code[code], route_counts)
            for code in AGENT_TOPOLOGY_WORKFLOW_CODES.get(role_id, ())
            if code in registry_by_code
        ]
        today_call_count = sum(int(item["today_call_count"]) for item in workflows)
        configured_count = sum(1 for item in workflows if bool(item["configured"]))
        workflow_count = len(workflows)
        configured_total += configured_count
        workflow_total += workflow_count
        call_count_by_role[role_id] = today_call_count
        agents.append(
            {
                "id": role_id,
                "label": _as_text(definition.get("label")),
                "agent_name": _as_text(definition.get("agent_name")),
                "headline": _as_text(definition.get("headline")),
                "responsibility": _as_text(definition.get("responsibility")),
                "color": _as_text(definition.get("color")),
                "workflow_count": workflow_count,
                "configured_workflow_count": configured_count,
                "today_call_count": today_call_count,
                "chart_weight": max(today_call_count, workflow_count * 2, 2),
                "workflows": workflows,
            }
        )

    links: list[dict[str, Any]] = []
    for edge in AGENT_TOPOLOGY_EDGES:
        source = _as_text(edge.get("source"))
        target = _as_text(edge.get("target"))
        source_count = call_count_by_role.get(source, 0)
        target_count = call_count_by_role.get(target, 0)
        activity_weight = round((source_count + target_count) / 2)
        links.append(
            {
                "source": source,
                "target": target,
                "label": _as_text(edge.get("label")),
                "value": max(int(edge.get("base_weight") or 1), activity_weight),
            }
        )

    hidden_count = len([code for code in registry_by_code if code not in visible_codes])
    user = current_user or {}
    return {
        "entry": {
            "id": "user_input",
            "label": "用户输入",
            "agent_name": "用户输入",
            "headline": "训练任务、顾客问题、经营查询统一进入编排层",
            "color": "#334155",
        },
        "agents": agents,
        "links": links,
        "summary": {
            "agent_count": len(agents),
            "workflow_count": workflow_total,
            "registered_workflow_count": len(registry_by_code),
            "hidden_workflow_count": hidden_count,
            "configured_workflow_count": configured_total,
            "today_call_count": sum(call_count_by_role.values()),
            "viewer_role": normalize_app_role(_as_text(user.get("role"))),
            "viewer_user_id": _as_text(user.get("user_id")),
            "updated_at": _now_iso(),
        },
    }


@router.get("/topology")
def get_agent_topology(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    return success_response(
        build_agent_topology_payload(current_user),
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


def _user_from_token_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    user_id = _as_text(payload.get("user_id"))
    role = _as_text(payload.get("role"))
    if not user_id or not role:
        return None
    return {
        "user_id": user_id,
        "role": role,
        "username": _as_text(payload.get("username")),
        "store_id": _as_text(payload.get("store_id")),
    }


async def _get_activity_stream_user(
    current_user: Annotated[dict[str, Any] | None, Depends(get_optional_user)],
    token: str = Query(default=""),
) -> dict[str, Any]:
    if current_user:
        return current_user
    user = _user_from_token_payload(decode_token_optional(_as_text(token)))
    if user:
        return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未提供认证凭据")


def _sse(event_name: str, data: dict[str, Any]) -> str:
    return "event: " + event_name + "\n" + "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


async def _activity_event_stream(
    request: Request,
    current_user: dict[str, Any],
) -> AsyncIterator[str]:
    queue = subscribe_agent_activity()
    try:
        yield _sse(
            "ready",
            {
                "type": "ready",
                "viewer_role": normalize_app_role(_as_text(current_user.get("role"))),
                "viewer_user_id": _as_text(current_user.get("user_id")),
                "created_at": _now_iso(),
            },
        )
        for event in recent_agent_activity(limit=6):
            yield _sse("agent_call", event)

        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield _sse("heartbeat", {"type": "heartbeat", "created_at": _now_iso()})
                continue
            yield _sse("agent_call", event)
    finally:
        unsubscribe_agent_activity(queue)


@router.get("/activity-stream")
async def get_agent_activity_stream(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(_get_activity_stream_user)],
):
    return StreamingResponse(
        _activity_event_stream(request, current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
