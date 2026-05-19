"""Governance queries and actions for Hermes Route B observability."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from evo.audit import log_audit
from models import (
    AgentEvoAnomaly,
    AgentEvoAuditLog,
    AgentEvoEpisode,
    AgentEvoEvalCase,
    AgentEvoEvalRun,
    AgentEvoMemoryHit,
    AgentEvoProcedural,
    AgentEvoPromotion,
    AgentEvoReflective,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)

MEMORY_MODELS = {
    "semantic": AgentEvoSemantic,
    "reflective": AgentEvoReflective,
    "procedural": AgentEvoProcedural,
}

ALL_MEMORY_TYPES = tuple(MEMORY_MODELS.keys())
ALLOWED_MEMORY_STATUSES = {
    "active",
    "pending",
    "archived",
    "auto",
    "auto_disabled",
    "quarantined",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def _json_list(raw: str | None) -> list[Any]:
    parsed = _json_loads(raw, [])
    return parsed if isinstance(parsed, list) else []


def _json_dict(raw: str | None) -> dict[str, Any]:
    parsed = _json_loads(raw, {})
    return parsed if isinstance(parsed, dict) else {}


def _episode_payload(row: AgentEvoEpisode | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "episode_type": row.episode_type,
        "module": row.module,
        "user_id": row.user_id,
        "store_id": row.store_id,
        "request_id": row.request_id,
        "query_text": row.query_text,
        "response_text": row.response_text,
        "signal": row.signal,
        "correction_text": row.correction_text,
        "compliance_tags": _json_list(row.compliance_tags),
        "parent_episode_id": row.parent_episode_id,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _has_ref(raw: str | None, ref_id: int) -> bool:
    return int(ref_id) in {
        int(item)
        for item in _json_list(raw)
        if str(item).strip().lstrip("-").isdigit()
    }


def _bound_refs(raw: str | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _json_list(raw):
        if isinstance(item, dict):
            refs.append(item)
        elif isinstance(item, int):
            refs.append({"type": "semantic", "id": item})
    return refs


def _bound_ref_matches(raw: str | None, memory_type: str, memory_id: int) -> bool:
    for item in _bound_refs(raw):
        item_type = str(item.get("type") or item.get("memory_type") or "semantic").strip()
        try:
            item_id = int(item.get("id") or item.get("memory_id"))
        except (TypeError, ValueError):
            continue
        if item_type == memory_type and item_id == int(memory_id):
            return True
    return False


def _promotion_mentions(row: AgentEvoPromotion, memory_type: str, memory_id: int) -> bool:
    if row.source_memory_type == memory_type and int(row.source_memory_id or 0) == int(memory_id):
        return True
    evidence = _json_dict(row.evidence)
    source_ids = evidence.get("source_memory_ids")
    if isinstance(source_ids, list) and int(memory_id) in {
        int(item) for item in source_ids if str(item).strip().lstrip("-").isdigit()
    }:
        return row.source_memory_type in {memory_type, "semantic_merge", "procedural"}
    typed_refs = evidence.get("source_memories")
    if isinstance(typed_refs, list):
        for item in typed_refs:
            if not isinstance(item, dict):
                continue
            try:
                item_id = int(item.get("id") or item.get("memory_id"))
            except (TypeError, ValueError):
                continue
            item_type = str(item.get("type") or item.get("memory_type") or "").strip()
            if item_type == memory_type and item_id == int(memory_id):
                return True
    return False


def _clamp_limit(limit: int | None, default: int = 50, maximum: int = 500) -> int:
    try:
        value = int(limit or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _clamp_offset(offset: int | None) -> int:
    try:
        value = int(offset or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)


def _created_at(row: Any) -> datetime:
    value = getattr(row, "created_at", None)
    if isinstance(value, datetime):
        return value
    return datetime.min.replace(tzinfo=timezone.utc)


def _memory_title(memory_type: str, row: Any) -> str:
    if memory_type == "procedural":
        return str(row.title or "").strip()
    if memory_type == "reflective":
        lesson = str(row.lesson or "").strip()
        return lesson[:80]
    content = str(row.content or "").strip()
    return content[:80]


def _memory_body(memory_type: str, row: Any) -> str:
    if memory_type == "procedural":
        parts = [
            str(row.title or "").strip(),
            " / ".join(str(x) for x in _json_list(row.do_json) if str(x).strip()),
            " / ".join(str(x) for x in _json_list(row.dont_json) if str(x).strip()),
            str(row.example or "").strip(),
        ]
        return " | ".join(part for part in parts if part)
    if memory_type == "reflective":
        return str(row.lesson or "").strip()
    return str(row.content or "").strip()


def _memory_sources(memory_type: str, row: Any) -> list[int]:
    if memory_type == "procedural":
        return [
            int(item)
            for item in _json_list(row.source_episode_ids_json)
            if str(item).strip().lstrip("-").isdigit()
        ]
    if memory_type == "reflective":
        return [
            int(item)
            for item in _json_list(row.evidence_episode_ids)
            if str(item).strip().lstrip("-").isdigit()
        ]
    return [
        int(item)
        for item in _json_list(row.source_episode_ids)
        if str(item).strip().lstrip("-").isdigit()
    ]


def _memory_scope(row: Any) -> dict[str, str]:
    return {
        "scope_type": str(getattr(row, "scope_type", "") or ""),
        "scope_id": str(getattr(row, "scope_id", "") or ""),
    }


def _readiness(status: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "reasons": reasons,
    }


def get_governance_data_health(session: Session) -> dict[str, Any]:
    episode_count = session.query(AgentEvoEpisode).count()
    semantic_count = session.query(AgentEvoSemantic).count()
    active_semantic_count = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.status == "active")
        .count()
    )
    reflective_count = session.query(AgentEvoReflective).count()
    active_reflective_count = (
        session.query(AgentEvoReflective)
        .filter(AgentEvoReflective.status == "active")
        .count()
    )
    procedural_count = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(("active", "auto")))
        .count()
    )
    hit_procedural_count = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(("active", "auto")))
        .filter(AgentEvoProcedural.hit_count > 0)
        .count()
    )
    memory_hit_count = session.query(AgentEvoMemoryHit).count()
    promotion_count = session.query(AgentEvoPromotion).count()
    pending_promotion_count = (
        session.query(AgentEvoPromotion)
        .filter(AgentEvoPromotion.status == "pending")
        .count()
    )
    review_queue_count = session.query(AgentEvoReviewQueue).count()
    pending_review_count = (
        session.query(AgentEvoReviewQueue)
        .filter(AgentEvoReviewQueue.status == "pending")
        .count()
    )
    eval_case_count = session.query(AgentEvoEvalCase).count()
    active_eval_case_count = (
        session.query(AgentEvoEvalCase)
        .filter(AgentEvoEvalCase.status == "active")
        .count()
    )
    eval_run_count = session.query(AgentEvoEvalRun).count()
    failed_eval_run_count = (
        session.query(AgentEvoEvalRun)
        .filter(AgentEvoEvalRun.status != "passed")
        .count()
    )
    anomaly_count = session.query(AgentEvoAnomaly).count()
    open_anomaly_count = (
        session.query(AgentEvoAnomaly)
        .filter(AgentEvoAnomaly.status == "open")
        .count()
    )

    promotion_reasons: list[str] = []
    if procedural_count <= 0:
        promotion_reasons.append("no_active_procedural")
    elif hit_procedural_count <= 0:
        promotion_reasons.append("no_hit_procedural")
    if memory_hit_count <= 0:
        promotion_reasons.append("no_memory_hits")
    if active_semantic_count < 2 and hit_procedural_count <= 0:
        promotion_reasons.append("not_enough_semantic_for_merge")

    safety_reasons: list[str] = []
    if active_eval_case_count <= 0:
        safety_reasons.append("no_eval_cases")
    if eval_run_count <= 0 and active_eval_case_count > 0:
        safety_reasons.append("no_eval_runs")

    return {
        "episode_count": episode_count,
        "semantic_count": semantic_count,
        "active_semantic_count": active_semantic_count,
        "reflective_count": reflective_count,
        "active_reflective_count": active_reflective_count,
        "procedural_count": procedural_count,
        "hit_procedural_count": hit_procedural_count,
        "memory_hit_count": memory_hit_count,
        "promotion_count": promotion_count,
        "pending_promotion_count": pending_promotion_count,
        "review_queue_count": review_queue_count,
        "pending_review_count": pending_review_count,
        "eval_case_count": eval_case_count,
        "active_eval_case_count": active_eval_case_count,
        "eval_run_count": eval_run_count,
        "failed_eval_run_count": failed_eval_run_count,
        "anomaly_count": anomaly_count,
        "open_anomaly_count": open_anomaly_count,
        "promotion_readiness": _readiness(
            "ready" if not promotion_reasons else "not_ready",
            promotion_reasons,
        ),
        "safety_readiness": _readiness(
            "ready" if not safety_reasons else "not_ready",
            safety_reasons,
        ),
    }


def _memory_payload(memory_type: str, row: Any) -> dict[str, Any]:
    payload = {
        "id": int(row.id),
        "memory_type": memory_type,
        "title": _memory_title(memory_type, row),
        "content": _memory_body(memory_type, row),
        "status": str(getattr(row, "status", "") or ""),
        "confidence": float(getattr(row, "confidence", 0.0) or 0.0),
        "hit_count": int(getattr(row, "hit_count", 0) or 0),
        "last_hit_at": _dt(getattr(row, "last_hit_at", None)),
        "created_at": _dt(getattr(row, "created_at", None)),
        "source_episode_ids": _memory_sources(memory_type, row),
        "source_episode_count": len(_memory_sources(memory_type, row)),
    }
    payload.update(_memory_scope(row))
    if memory_type == "semantic":
        payload.update(
            {
                "trigger_text": row.trigger_text,
                "write_mode": row.write_mode,
            }
        )
    elif memory_type == "reflective":
        payload.update(
            {
                "trigger_text": "",
                "expires_at": _dt(row.expires_at),
                "promoted_to_procedural_id": row.promoted_to_procedural_id,
            }
        )
    else:
        payload.update(
            {
                "trigger_text": " / ".join(str(x) for x in _json_list(row.trigger_json) if str(x).strip()),
                "write_mode": row.write_mode,
                "trigger": _json_list(row.trigger_json),
                "do": _json_list(row.do_json),
                "dont": _json_list(row.dont_json),
                "example": row.example,
                "source_reflective_ids": _json_list(row.source_reflective_ids_json),
                "eval_case_ids": _json_list(row.eval_case_ids_json),
            }
        )
    return payload


def get_governance_overview(
    session: Session,
    *,
    now: datetime | None = None,
    allowed_scope_types: Iterable[str] | None = None,
) -> dict[str, Any]:
    current = now or _now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)

    today_auto_writes = (
        session.query(AgentEvoEpisode).filter(AgentEvoEpisode.created_at >= start).count()
        + session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.created_at >= start, AgentEvoSemantic.write_mode == "auto")
        .count()
        + session.query(AgentEvoReflective).filter(AgentEvoReflective.created_at >= start).count()
        + session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.created_at >= start, AgentEvoProcedural.write_mode == "auto")
        .count()
    )

    memory_totals: dict[str, dict[str, int]] = {}
    for memory_type, model in MEMORY_MODELS.items():
        rows = session.query(model.status).all()
        by_status: dict[str, int] = defaultdict(int)
        for (status,) in rows:
            by_status[str(status or "")] += 1
        memory_totals[memory_type] = {
            "total": sum(by_status.values()),
            "active": by_status.get("active", 0) + (by_status.get("auto", 0) if memory_type == "procedural" else 0),
            "pending": by_status.get("pending", 0),
            "archived": by_status.get("archived", 0),
            "auto_disabled": by_status.get("auto_disabled", 0),
            "quarantined": by_status.get("quarantined", 0),
        }

    recent_audits = (
        session.query(AgentEvoAuditLog)
        .order_by(AgentEvoAuditLog.created_at.desc(), AgentEvoAuditLog.id.desc())
        .limit(8)
        .all()
    )
    recent_anomalies = (
        session.query(AgentEvoAnomaly)
        .filter(AgentEvoAnomaly.status == "open")
        .order_by(AgentEvoAnomaly.created_at.desc(), AgentEvoAnomaly.id.desc())
        .limit(6)
        .all()
    )
    pending_reviews_result = list_review_queue(
        session,
        status="pending",
        allowed_scope_types=allowed_scope_types,
        limit=6,
        offset=0,
    )
    pending_reviews = pending_reviews_result["items"]

    return {
        "today_auto_writes": today_auto_writes,
        "today_memory_hits": session.query(AgentEvoMemoryHit)
        .filter(AgentEvoMemoryHit.created_at >= start)
        .count(),
        "pending_review_count": int(pending_reviews_result["total"]),
        "open_anomaly_count": session.query(AgentEvoAnomaly)
        .filter(AgentEvoAnomaly.status == "open")
        .count(),
        "pending_promotion_count": session.query(AgentEvoPromotion)
        .filter(AgentEvoPromotion.status == "pending")
        .count(),
        "today_eval_failed_count": session.query(AgentEvoEvalRun)
        .filter(AgentEvoEvalRun.created_at >= start, AgentEvoEvalRun.status != "passed")
        .count(),
        "memory_totals": memory_totals,
        "data_health": get_governance_data_health(session),
        "recent_audits": [
            {
                "id": row.id,
                "actor": row.actor,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "payload": _json_dict(row.payload),
                "created_at": _dt(row.created_at),
            }
            for row in recent_audits
        ],
        "recent_anomalies": [
            {
                "id": row.id,
                "anomaly_type": row.anomaly_type,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "severity": row.severity,
                "reason": row.reason,
                "created_at": _dt(row.created_at),
            }
            for row in recent_anomalies
        ],
        "pending_reviews": [
            {
                "id": row["id"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "reason": row["reason"],
                "priority": row["priority"],
                "created_at": row["created_at"],
            }
            for row in pending_reviews
        ],
    }


def list_memories(
    session: Session,
    *,
    memory_type: str = "all",
    scope_type: str = "",
    allowed_scope_types: Iterable[str] | None = None,
    status: str = "",
    query_text: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    requested_types = ALL_MEMORY_TYPES if memory_type in {"", "all"} else (memory_type,)
    scope_allowlist = {
        str(item or "").strip()
        for item in (allowed_scope_types or [])
        if str(item or "").strip()
    }
    rows: list[dict[str, Any]] = []
    q = str(query_text or "").strip().lower()
    for item_type in requested_types:
        model = MEMORY_MODELS.get(item_type)
        if model is None:
            continue
        query = session.query(model)
        if scope_type:
            query = query.filter(model.scope_type == scope_type)
        elif scope_allowlist:
            query = query.filter(model.scope_type.in_(scope_allowlist))
        if status:
            query = query.filter(model.status == status)
        for row in query.all():
            payload = _memory_payload(item_type, row)
            if q:
                haystack = " ".join(
                    str(payload.get(key) or "")
                    for key in ("title", "content", "trigger_text", "scope_id", "status")
                ).lower()
                if q not in haystack:
                    continue
            rows.append(payload)
    rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    total = len(rows)
    start = _clamp_offset(offset)
    stop = start + _clamp_limit(limit)
    return {
        "total": total,
        "items": rows[start:stop],
        "limit": stop - start,
        "offset": start,
    }


def _memory_refs_by_source_episode(session: Session) -> dict[int, list[dict[str, Any]]]:
    refs_by_episode: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for memory_type, model in MEMORY_MODELS.items():
        for row in session.query(model).all():
            payload = _memory_payload(memory_type, row)
            ref = {
                "memory_type": memory_type,
                "id": payload["id"],
                "title": payload["title"],
                "status": payload["status"],
                "scope_type": payload["scope_type"],
                "scope_id": payload["scope_id"],
            }
            for episode_id in payload.get("source_episode_ids") or []:
                refs_by_episode[int(episode_id)].append(ref)
    return refs_by_episode


def _feedback_event_payload(
    parent: AgentEvoEpisode,
    correction: AgentEvoEpisode | None,
    refs_by_episode: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    episode_ids = [int(parent.id)]
    if correction is not None:
        episode_ids.append(int(correction.id))
    linked: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for episode_id in episode_ids:
        for ref in refs_by_episode.get(int(episode_id), []):
            key = (str(ref.get("memory_type") or ""), int(ref.get("id") or 0))
            if key in seen:
                continue
            seen.add(key)
            linked.append(ref)
    effective_signal = "correction" if correction is not None else str(parent.signal or "none")
    return {
        "id": int(parent.id),
        "episode": _episode_payload(parent),
        "correction": _episode_payload(correction),
        "module": parent.module,
        "user_id": parent.user_id,
        "store_id": parent.store_id,
        "query_text": parent.query_text,
        "response_text": parent.response_text,
        "signal": effective_signal,
        "feedback_text": correction.correction_text if correction is not None else "",
        "linked_memories": linked,
        "linked_memory_count": len(linked),
        "created_at": _dt(correction.created_at if correction is not None else parent.created_at),
    }


def list_feedback_events(
    session: Session,
    *,
    signal: str = "all",
    module: str = "",
    query_text: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    rows = (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.signal != "none")
        .order_by(AgentEvoEpisode.created_at.desc(), AgentEvoEpisode.id.desc())
        .all()
    )
    corrections_by_parent: dict[int, AgentEvoEpisode] = {}
    orphan_corrections: list[AgentEvoEpisode] = []
    for row in rows:
        if row.episode_type != "correction":
            continue
        parent_id = int(row.parent_episode_id or 0)
        if parent_id:
            current = corrections_by_parent.get(parent_id)
            if current is None or int(row.id) > int(current.id):
                corrections_by_parent[parent_id] = row
        else:
            orphan_corrections.append(row)

    q = str(query_text or "").strip().lower()
    wanted_signal = str(signal or "all").strip()
    wanted_module = str(module or "").strip()
    refs_by_episode = _memory_refs_by_source_episode(session)
    events: list[dict[str, Any]] = []
    parent_ids = {int(row.id) for row in rows if row.episode_type != "correction"}

    candidates = [row for row in rows if row.episode_type != "correction"]
    candidates.extend(row for row in orphan_corrections if int(row.id) not in parent_ids)
    for parent in candidates:
        correction = corrections_by_parent.get(int(parent.id))
        event = _feedback_event_payload(parent, correction, refs_by_episode)
        if wanted_signal not in {"", "all"} and event["signal"] != wanted_signal:
            continue
        if wanted_module and event["module"] != wanted_module:
            continue
        if q:
            haystack = " ".join(
                [
                    str(event.get("query_text") or ""),
                    str(event.get("response_text") or ""),
                    str(event.get("feedback_text") or ""),
                    str((event.get("correction") or {}).get("correction_text") or ""),
                ]
            ).lower()
            if q not in haystack:
                continue
        events.append(event)

    events.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    total = len(events)
    start = _clamp_offset(offset)
    stop = start + _clamp_limit(limit)
    return {
        "total": total,
        "items": events[start:stop],
        "limit": stop - start,
        "offset": start,
    }


def get_memory_row(session: Session, memory_type: str, memory_id: int) -> Any:
    model = MEMORY_MODELS.get(memory_type)
    if model is None:
        raise LookupError(f"unsupported memory_type: {memory_type}")
    row = session.get(model, int(memory_id))
    if row is None:
        raise LookupError(f"memory not found: {memory_type}:{memory_id}")
    return row


def _review_payload(session: Session, row: AgentEvoReviewQueue) -> dict[str, Any]:
    memory: dict[str, Any] | None = None
    if row.target_type in MEMORY_MODELS:
        target = session.get(MEMORY_MODELS[row.target_type], int(row.target_id or 0))
        if target is not None:
            memory = _memory_payload(row.target_type, target)
    return {
        "id": int(row.id),
        "target_type": row.target_type,
        "target_id": int(row.target_id or 0),
        "reason": row.reason,
        "priority": int(row.priority or 0),
        "status": row.status,
        "reviewer_id": row.reviewer_id,
        "created_at": _dt(row.created_at),
        "reviewed_at": _dt(row.reviewed_at),
        "memory": memory,
    }


def list_review_queue(
    session: Session,
    *,
    status: str = "pending",
    allowed_scope_types: set[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    rows = (
        session.query(AgentEvoReviewQueue)
        .order_by(AgentEvoReviewQueue.priority.desc(), AgentEvoReviewQueue.created_at.desc(), AgentEvoReviewQueue.id.desc())
        .all()
    )
    wanted_status = str(status or "pending").strip()
    items: list[dict[str, Any]] = []
    for row in rows:
        if wanted_status not in {"", "all"} and row.status != wanted_status:
            continue
        payload = _review_payload(session, row)
        memory = payload.get("memory")
        if allowed_scope_types is not None and memory is not None:
            if memory.get("scope_type") not in allowed_scope_types:
                continue
        items.append(payload)
    total = len(items)
    start = _clamp_offset(offset)
    stop = start + _clamp_limit(limit)
    return {"total": total, "items": items[start:stop], "limit": stop - start, "offset": start}


def decide_review_queue_item(
    session: Session,
    *,
    review_id: int,
    decision: str,
    actor: str,
) -> dict[str, Any]:
    row = session.get(AgentEvoReviewQueue, int(review_id))
    if row is None:
        raise LookupError(f"review item not found: {review_id}")
    normalized = str(decision or "").strip()
    if normalized not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    memory_payload: dict[str, Any] | None = None
    target = None
    if row.target_type in MEMORY_MODELS:
        target = session.get(MEMORY_MODELS[row.target_type], int(row.target_id or 0))
        if target is None:
            raise LookupError(f"review target not found: {row.target_type}:{row.target_id}")
        old_status = str(getattr(target, "status", "") or "")
        setattr(target, "status", "active" if normalized == "approve" else "archived")
        session.add(target)
        memory_payload = _memory_payload(row.target_type, target)
    row.status = "approved" if normalized == "approve" else "rejected"
    row.reviewer_id = actor
    row.reviewed_at = _now()
    session.add(row)
    log_audit(
        session,
        actor=actor,
        action="review_approved" if normalized == "approve" else "review_rejected",
        target_type=row.target_type,
        target_id=row.target_id,
        payload={
            "review_id": int(row.id),
            "decision": normalized,
            "memory_status": {
                "from": old_status if target is not None else "",
                "to": getattr(target, "status", "") if target is not None else "",
            },
        },
    )
    return {"review": _review_payload(session, row), "memory": memory_payload}


def _source_episode_payloads(session: Session, episode_ids: Iterable[int]) -> list[dict[str, Any]]:
    ids = [int(item) for item in episode_ids if str(item).strip().lstrip("-").isdigit()]
    if not ids:
        return []
    rows = (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.id.in_(ids))
        .order_by(AgentEvoEpisode.created_at.asc(), AgentEvoEpisode.id.asc())
        .all()
    )
    return [_episode_payload(row) for row in rows if row is not None]


def _hit_payload(session: Session, memory_type: str, memory_id: int) -> dict[str, Any]:
    rows = (
        session.query(AgentEvoMemoryHit)
        .filter(
            AgentEvoMemoryHit.memory_type == memory_type,
            AgentEvoMemoryHit.memory_id == int(memory_id),
        )
        .order_by(AgentEvoMemoryHit.created_at.desc(), AgentEvoMemoryHit.id.desc())
        .limit(60)
        .all()
    )
    daily: dict[str, dict[str, float | int]] = defaultdict(lambda: {"count": 0, "score_sum": 0.0})
    for row in rows:
        day = (row.created_at or _now()).date().isoformat()
        daily[day]["count"] = int(daily[day]["count"]) + 1
        daily[day]["score_sum"] = float(daily[day]["score_sum"]) + float(row.score or 0.0)
    return {
        "recent_hits": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "module": row.module,
                "query_text": row.query_text,
                "score": row.score,
                "created_at": _dt(row.created_at),
            }
            for row in rows
        ],
        "daily": [
            {
                "date": day,
                "count": int(values["count"]),
                "avg_score": round(float(values["score_sum"]) / max(1, int(values["count"])), 6),
            }
            for day, values in sorted(daily.items())
        ],
    }


def _derived_path(session: Session, memory_type: str, memory_id: int) -> dict[str, Any]:
    procedural_children: list[dict[str, Any]] = []
    if memory_type == "reflective":
        reflective = session.get(AgentEvoReflective, int(memory_id))
        promoted_to_id = int(reflective.promoted_to_procedural_id or 0) if reflective is not None else 0
        for row in session.query(AgentEvoProcedural).all():
            if (promoted_to_id and int(row.id) == promoted_to_id) or _has_ref(row.source_reflective_ids_json, int(memory_id)):
                procedural_children.append(_memory_payload("procedural", row))

    promotions = [
        {
            "id": row.id,
            "source_memory_type": row.source_memory_type,
            "source_memory_id": row.source_memory_id,
            "current_scope": row.current_scope,
            "target_scope": row.target_scope,
            "reason": row.reason,
            "status": row.status,
            "suggested_at": _dt(row.suggested_at),
            "decided_at": _dt(row.decided_at),
            "evidence": _json_dict(row.evidence),
        }
        for row in session.query(AgentEvoPromotion).all()
        if _promotion_mentions(row, memory_type, int(memory_id))
    ]

    eval_cases = [
        {
            "id": row.id,
            "module": row.module,
            "question": row.question,
            "severity": row.severity,
            "status": row.status,
        }
        for row in session.query(AgentEvoEvalCase).all()
        if _bound_ref_matches(row.bound_memory_ids, memory_type, int(memory_id))
    ]

    anomalies = [
        {
            "id": row.id,
            "anomaly_type": row.anomaly_type,
            "severity": row.severity,
            "status": row.status,
            "reason": row.reason,
            "created_at": _dt(row.created_at),
        }
        for row in session.query(AgentEvoAnomaly)
        .filter(AgentEvoAnomaly.target_type == memory_type, AgentEvoAnomaly.target_id == str(memory_id))
        .order_by(AgentEvoAnomaly.created_at.desc())
        .all()
    ]

    review_queue = [
        {
            "id": row.id,
            "reason": row.reason,
            "priority": row.priority,
            "status": row.status,
            "created_at": _dt(row.created_at),
        }
        for row in session.query(AgentEvoReviewQueue)
        .filter(AgentEvoReviewQueue.target_type == memory_type, AgentEvoReviewQueue.target_id == int(memory_id))
        .order_by(AgentEvoReviewQueue.created_at.desc())
        .all()
    ]

    return {
        "procedural_children": procedural_children,
        "promotions": promotions,
        "eval_cases": eval_cases,
        "anomalies": anomalies,
        "review_queue": review_queue,
    }


def get_memory_detail(session: Session, *, memory_type: str, memory_id: int) -> dict[str, Any]:
    row = get_memory_row(session, memory_type, memory_id)
    memory = _memory_payload(memory_type, row)
    source_episode_ids = memory.get("source_episode_ids") or []
    hit_payload = _hit_payload(session, memory_type, memory_id)
    return {
        "memory": memory,
        "source_episodes": _source_episode_payloads(session, source_episode_ids),
        "hit_history": hit_payload,
        "derived_path": _derived_path(session, memory_type, memory_id),
    }


def update_memory(
    session: Session,
    *,
    memory_type: str,
    memory_id: int,
    actor: str,
    status: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    row = get_memory_row(session, memory_type, memory_id)
    changes: dict[str, Any] = {}
    if status is not None:
        normalized_status = str(status or "").strip()
        if normalized_status not in ALLOWED_MEMORY_STATUSES:
            raise ValueError(f"unsupported status: {normalized_status}")
        old = str(getattr(row, "status", "") or "")
        if old != normalized_status:
            setattr(row, "status", normalized_status)
            changes["status"] = {"from": old, "to": normalized_status}
    if confidence is not None:
        value = float(confidence)
        if value < 0.0 or value > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        old_value = float(getattr(row, "confidence", 0.0) or 0.0)
        if old_value != value:
            setattr(row, "confidence", value)
            changes["confidence"] = {"from": old_value, "to": value}
    if not changes:
        return {"memory": _memory_payload(memory_type, row), "changes": {}}
    session.add(row)
    log_audit(
        session,
        actor=actor,
        action="memory_update",
        target_type=memory_type,
        target_id=memory_id,
        payload={"changes": changes},
    )
    return {"memory": _memory_payload(memory_type, row), "changes": changes}


def _archive_memory_row(
    session: Session,
    *,
    memory_type: str,
    row: Any,
    archived: list[dict[str, Any]],
) -> None:
    if str(getattr(row, "status", "") or "") == "archived":
        return
    setattr(row, "status", "archived")
    session.add(row)
    archived.append({"memory_type": memory_type, "id": int(row.id)})


def rollback_memory(
    session: Session,
    *,
    memory_type: str,
    memory_id: int,
    actor: str,
    reason: str = "",
) -> dict[str, Any]:
    row = get_memory_row(session, memory_type, memory_id)
    archived: list[dict[str, Any]] = []
    _archive_memory_row(session, memory_type=memory_type, row=row, archived=archived)

    if memory_type == "reflective":
        for child in session.query(AgentEvoProcedural).all():
            if _has_ref(child.source_reflective_ids_json, int(memory_id)):
                _archive_memory_row(session, memory_type="procedural", row=child, archived=archived)
    if memory_type == "procedural":
        for child in session.query(AgentEvoProcedural).all():
            refs = _json_list(child.source_reflective_ids_json)
            if child.id != int(memory_id) and int(memory_id) in {
                int(item) for item in refs if str(item).strip().lstrip("-").isdigit()
            }:
                _archive_memory_row(session, memory_type="procedural", row=child, archived=archived)

    rejected_promotions: list[int] = []
    current_time = _now()
    for promotion in session.query(AgentEvoPromotion).all():
        if not _promotion_mentions(promotion, memory_type, int(memory_id)):
            continue
        if promotion.status == "pending":
            promotion.status = "rejected"
            promotion.decided_at = current_time
            promotion.decided_by = actor
            session.add(promotion)
            rejected_promotions.append(int(promotion.id))

    log_audit(
        session,
        actor=actor,
        action="memory_rollback",
        target_type=memory_type,
        target_id=memory_id,
        payload={
            "reason": str(reason or "").strip(),
            "archived": archived,
            "rejected_promotions": rejected_promotions,
        },
    )
    return {
        "memory": _memory_payload(memory_type, row),
        "archived": archived,
        "rejected_promotions": rejected_promotions,
    }
