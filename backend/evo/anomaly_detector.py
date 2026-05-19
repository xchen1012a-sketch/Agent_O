"""Anomaly detection for Hermes Route B Phase 6."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from evo.audit import log_audit
from evo.retriever import _cosine_text
from models import (
    AgentEvoAnomaly,
    AgentEvoEpisode,
    AgentEvoMemoryHit,
    AgentEvoProcedural,
    AgentEvoReflective,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)

_log = logging.getLogger("jewelry_qipei.evo.anomaly_detector")

ACTIVE_SEMANTIC_STATUSES = ("active",)
ACTIVE_MEMORY_STATUSES = ("active", "auto")
NEGATIVE_SIGNALS = ("thumb_down", "correction")
MEMORY_MODELS = {
    "semantic": AgentEvoSemantic,
    "procedural": AgentEvoProcedural,
    "reflective": AgentEvoReflective,
}

_SPACE_RE = re.compile(r"\s+")
_NEGATIVE_MARKERS = (
    "不能",
    "不可以",
    "不要",
    "禁止",
    "避免",
    "不得",
    "不应",
    "不可",
    "严禁",
    "不承诺",
    "do not",
    "don't",
    "cannot",
    "never",
    "avoid",
    "forbidden",
    "must not",
)
_POSITIVE_MARKERS = (
    "可以",
    "能",
    "允许",
    "建议",
    "承诺",
    "保证",
    "保值",
    "升值",
    "稳赚",
    "can",
    "allow",
    "promise",
    "guarantee",
    "guaranteed",
    "appreciation",
    "preserve value",
    "retains value",
)
_HIGH_RISK_MARKERS = (
    "承诺",
    "保证",
    "保值",
    "升值",
    "稳赚",
    "最低价",
    "绝对",
    "promise",
    "guarantee",
    "guaranteed",
    "appreciation",
    "preserve value",
    "retains value",
    "lowest price",
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "").strip().lower())


def _trigger_key(row: AgentEvoSemantic) -> str:
    key = _clean(row.trigger_text)
    if key:
        return key[:80]
    return _clean(row.content)[:80]


def _stance(text: str) -> int:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in _NEGATIVE_MARKERS):
        return -1
    if any(marker in lowered for marker in _POSITIVE_MARKERS):
        return 1
    return 0


def _risk_terms(text: str) -> set[str]:
    lowered = str(text or "").lower()
    return {marker for marker in _HIGH_RISK_MARKERS if marker in lowered}


def _is_conflicting_text(left: str, right: str) -> bool:
    left_stance = _stance(left)
    right_stance = _stance(right)
    if left_stance * right_stance >= 0:
        return False
    shared_risk = _risk_terms(left) & _risk_terms(right)
    return bool(shared_risk) or _cosine_text(left, right) >= 0.25


def _has_pairwise_conflict(rows: list[AgentEvoSemantic]) -> bool:
    for idx, left in enumerate(rows):
        for right in rows[idx + 1 :]:
            if _is_conflicting_text(left.content or "", right.content or ""):
                return True
    return False


def _has_open_anomaly(
    session: Session,
    *,
    anomaly_type: str,
    target_type: str,
    target_id: str,
) -> bool:
    return (
        session.query(AgentEvoAnomaly)
        .filter(AgentEvoAnomaly.anomaly_type == anomaly_type)
        .filter(AgentEvoAnomaly.target_type == target_type)
        .filter(AgentEvoAnomaly.target_id == str(target_id))
        .filter(AgentEvoAnomaly.status == "open")
        .first()
        is not None
    )


def _write_anomaly(
    session: Session,
    *,
    anomaly_type: str,
    target_type: str,
    target_id: str,
    severity: int,
    reason: str,
    evidence: dict[str, Any],
) -> AgentEvoAnomaly | None:
    if _has_open_anomaly(
        session,
        anomaly_type=anomaly_type,
        target_type=target_type,
        target_id=target_id,
    ):
        return None
    row = AgentEvoAnomaly(
        anomaly_type=anomaly_type,
        target_type=target_type,
        target_id=str(target_id)[:64],
        severity=int(severity or 2),
        status="open",
        reason=reason,
        evidence=_json_dumps(evidence),
    )
    session.add(row)
    session.flush()
    log_audit(
        session,
        actor="system",
        action="anomaly_write",
        target_type="anomaly",
        target_id=row.id,
        payload={"anomaly_type": anomaly_type, "target_type": target_type, "target_id": target_id},
    )
    return row


def anomaly_scan_diagnostics(session: Session) -> dict[str, Any]:
    semantic_count = session.query(AgentEvoSemantic).count()
    active_semantic_count = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.status.in_(ACTIVE_SEMANTIC_STATUSES))
        .count()
    )
    memory_hit_count = session.query(AgentEvoMemoryHit).count()
    negative_feedback_episode_count = (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.signal.in_(NEGATIVE_SIGNALS))
        .count()
    )
    active_memory_count = 0
    for model in MEMORY_MODELS.values():
        active_memory_count += (
            session.query(model)
            .filter(model.status.in_(ACTIVE_MEMORY_STATUSES))
            .count()
        )

    reasons: list[str] = []
    if active_semantic_count <= 0:
        reasons.append("no_active_semantic")
    if memory_hit_count <= 0:
        reasons.append("no_memory_hits")
    if negative_feedback_episode_count <= 0:
        reasons.append("no_negative_feedback")
    if active_memory_count <= 0:
        reasons.append("no_active_memory")

    return {
        "semantic_count": semantic_count,
        "active_semantic_count": active_semantic_count,
        "active_memory_count": active_memory_count,
        "memory_hit_count": memory_hit_count,
        "negative_feedback_episode_count": negative_feedback_episode_count,
        "reasons": reasons,
        "status": "ready" if active_semantic_count > 0 or (memory_hit_count > 0 and negative_feedback_episode_count > 0) else "not_ready",
    }


def _enqueue_review(
    session: Session,
    *,
    target_type: str,
    target_id: int,
    reason: str,
    priority: int,
) -> AgentEvoReviewQueue | None:
    existing = (
        session.query(AgentEvoReviewQueue)
        .filter(AgentEvoReviewQueue.target_type == target_type)
        .filter(AgentEvoReviewQueue.target_id == int(target_id))
        .filter(AgentEvoReviewQueue.status == "pending")
        .first()
    )
    if existing is not None:
        return None
    row = AgentEvoReviewQueue(
        target_type=target_type,
        target_id=int(target_id),
        reason=reason,
        priority=int(priority or 0),
        status="pending",
    )
    session.add(row)
    return row


def _mark_semantic_pending(
    session: Session,
    rows: Iterable[AgentEvoSemantic],
    *,
    reason: str,
    priority: int,
) -> int:
    count = 0
    for row in rows:
        if row.status != "pending":
            old_status = row.status
            row.status = "pending"
            session.add(row)
            count += 1
            log_audit(
                session,
                actor="system",
                action="memory_pending_review",
                target_type="semantic",
                target_id=row.id,
                payload={"reason": reason, "old_status": old_status},
            )
        _enqueue_review(
            session,
            target_type="semantic",
            target_id=int(row.id),
            reason=reason,
            priority=priority,
        )
    return count


def scan_semantic_trigger_conflicts(
    session: Session,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
    min_cluster_size: int = 3,
) -> list[AgentEvoAnomaly]:
    current = now or datetime.now(timezone.utc)
    since = current - timedelta(hours=max(1, int(window_hours or 24)))
    rows = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.status.in_(ACTIVE_SEMANTIC_STATUSES))
        .filter(AgentEvoSemantic.created_at >= since)
        .order_by(AgentEvoSemantic.id.asc())
        .all()
    )
    grouped: dict[str, list[AgentEvoSemantic]] = {}
    for row in rows:
        grouped.setdefault(_trigger_key(row), []).append(row)

    anomalies: list[AgentEvoAnomaly] = []
    for trigger, cluster in grouped.items():
        if not trigger or len(cluster) < max(2, int(min_cluster_size or 3)):
            continue
        if not _has_pairwise_conflict(cluster):
            continue
        ids = [int(row.id) for row in cluster]
        _mark_semantic_pending(
            session,
            cluster,
            reason="同一触发语 24 小时内出现相互矛盾的事实记忆，已转入人审。",
            priority=3,
        )
        anomaly = _write_anomaly(
            session,
            anomaly_type="semantic_trigger_conflict",
            target_type="semantic_cluster",
            target_id=",".join(str(item) for item in ids),
            severity=3,
            reason="同一 trigger 下出现多条矛盾 semantic 记忆。",
            evidence={
                "trigger": trigger,
                "memory_ids": ids,
                "contents": [row.content for row in cluster],
                "window_hours": window_hours,
            },
        )
        if anomaly is not None:
            anomalies.append(anomaly)
    return anomalies


def scan_scope_conflicts(
    session: Session,
    *,
    similarity_threshold: float = 0.55,
) -> list[AgentEvoAnomaly]:
    global_rows = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.scope_type == "global")
        .filter(AgentEvoSemantic.status.in_(ACTIVE_SEMANTIC_STATUSES))
        .all()
    )
    scoped_rows = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.scope_type.in_(("store", "user")))
        .filter(AgentEvoSemantic.status.in_(ACTIVE_SEMANTIC_STATUSES))
        .all()
    )
    anomalies: list[AgentEvoAnomaly] = []
    threshold = max(0.0, min(1.0, float(similarity_threshold)))
    for scoped in scoped_rows:
        scoped_text = " ".join(part for part in (scoped.trigger_text, scoped.content) if part)
        for global_row in global_rows:
            global_text = " ".join(part for part in (global_row.trigger_text, global_row.content) if part)
            similar = max(
                _cosine_text(scoped.trigger_text or "", global_row.trigger_text or ""),
                _cosine_text(scoped_text, global_text),
            )
            if similar < threshold or not _is_conflicting_text(scoped.content, global_row.content):
                continue
            _mark_semantic_pending(
                session,
                [scoped],
                reason="低层记忆与 global 规则语义冲突，已转入人审。",
                priority=3,
            )
            target_id = f"semantic:{scoped.id}|global:{global_row.id}"
            anomaly = _write_anomaly(
                session,
                anomaly_type="scope_conflict",
                target_type="semantic",
                target_id=target_id,
                severity=3,
                reason="低层 semantic 与 global semantic 规则冲突。",
                evidence={
                    "semantic_id": int(scoped.id),
                    "global_semantic_id": int(global_row.id),
                    "similarity": round(similar, 4),
                    "semantic_content": scoped.content,
                    "global_content": global_row.content,
                },
            )
            if anomaly is not None:
                anomalies.append(anomaly)
            break
    return anomalies


def _memory_for_hit(session: Session, hit: AgentEvoMemoryHit):
    model = MEMORY_MODELS.get(hit.memory_type)
    if model is None:
        return None
    return session.get(model, int(hit.memory_id))


def scan_negative_feedback_spikes(
    session: Session,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
    min_hits: int = 3,
    min_negative: int = 2,
    negative_ratio_threshold: float = 0.5,
    query_similarity_threshold: float = 0.65,
) -> list[AgentEvoAnomaly]:
    current = now or datetime.now(timezone.utc)
    since = current - timedelta(hours=max(1, int(window_hours or 24)))
    hits = (
        session.query(AgentEvoMemoryHit)
        .filter(AgentEvoMemoryHit.created_at >= since)
        .order_by(AgentEvoMemoryHit.id.asc())
        .all()
    )
    if not hits:
        return []
    feedback_rows = (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.created_at >= since)
        .filter(AgentEvoEpisode.signal.in_(NEGATIVE_SIGNALS))
        .all()
    )
    by_memory: dict[tuple[str, int], list[AgentEvoMemoryHit]] = {}
    for hit in hits:
        by_memory.setdefault((hit.memory_type, int(hit.memory_id)), []).append(hit)

    anomalies: list[AgentEvoAnomaly] = []
    for (memory_type, memory_id), memory_hits in by_memory.items():
        if len(memory_hits) < max(1, int(min_hits or 3)):
            continue
        negatives = 0
        for hit in memory_hits:
            for feedback in feedback_rows:
                if feedback.user_id != hit.user_id or feedback.module != hit.module:
                    continue
                if _cosine_text(feedback.query_text or "", hit.query_text or "") >= query_similarity_threshold:
                    negatives += 1
                    break
        ratio = negatives / max(1, len(memory_hits))
        if negatives < max(1, int(min_negative or 2)) or ratio < float(negative_ratio_threshold):
            continue
        probe = memory_hits[0]
        memory = _memory_for_hit(session, probe)
        if memory is None or getattr(memory, "status", "") not in ACTIVE_MEMORY_STATUSES:
            continue
        old_confidence = float(getattr(memory, "confidence", 0.0) or 0.0)
        memory.confidence = max(0.1, old_confidence - 0.2)
        session.add(memory)
        log_audit(
            session,
            actor="system",
            action="memory_confidence_decay",
            target_type=memory_type,
            target_id=memory_id,
            payload={
                "reason": "negative_feedback_spike",
                "old_confidence": old_confidence,
                "new_confidence": memory.confidence,
                "hit_count": len(memory_hits),
                "negative_count": negatives,
                "negative_ratio": round(ratio, 4),
            },
        )
        anomaly = _write_anomaly(
            session,
            anomaly_type="negative_feedback_spike",
            target_type=memory_type,
            target_id=str(memory_id),
            severity=2,
            reason="记忆命中后的负反馈率突增，已自动降低置信度。",
            evidence={
                "memory_type": memory_type,
                "memory_id": memory_id,
                "hit_count": len(memory_hits),
                "negative_count": negatives,
                "negative_ratio": round(ratio, 4),
                "old_confidence": old_confidence,
                "new_confidence": memory.confidence,
            },
        )
        if anomaly is not None:
            anomalies.append(anomaly)
    return anomalies


def run_anomaly_scan(
    session: Session,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
    min_conflict_cluster_size: int = 3,
) -> list[AgentEvoAnomaly]:
    """Run all Phase 6 anomaly detectors."""
    current = now or datetime.now(timezone.utc)
    anomalies: list[AgentEvoAnomaly] = []
    anomalies.extend(
        scan_semantic_trigger_conflicts(
            session,
            now=current,
            window_hours=window_hours,
            min_cluster_size=min_conflict_cluster_size,
        )
    )
    anomalies.extend(scan_scope_conflicts(session))
    anomalies.extend(scan_negative_feedback_spikes(session, now=current, window_hours=window_hours))
    _log.info("evo anomaly scan finished anomalies=%s", len(anomalies))
    return anomalies
