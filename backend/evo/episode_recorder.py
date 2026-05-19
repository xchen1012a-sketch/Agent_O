"""Episode 采集服务——assistant/qa/practice/quick_query 回答返回前埋点。

Phase 1 默认动作：
- 写入 episode 自动落库（episodic 永不审）
- 命中合规敏感词只是打 tag，不进 review_queue（review_queue 在 Phase 2 接入 semantic 写入）
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from evo.audit import log_audit
from evo.compliance_tagger import detect_compliance_tags
from evo.schemas import EpisodeRecord
from evo.semantic_extractor import (
    extract_semantic_from_correction,
    extract_semantic_from_negative_feedback,
)
from models import (
    AgentEvoEpisode,
    AgentEvoMemoryHit,
    AgentEvoProcedural,
    AgentEvoReflective,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)

_log = logging.getLogger("jewelry_qipei.evo.episode_recorder")

_VALID_MODULES = {"assistant", "qa", "practice", "quick_query"}
_MEMORY_MODELS = {
    "semantic": AgentEvoSemantic,
    "procedural": AgentEvoProcedural,
    "reflective": AgentEvoReflective,
}
_POSITIVE_CONFIDENCE_BUMP = 0.04
_NEGATIVE_CONFIDENCE_DECAY = 0.12
_AUTO_REVIEW_NEGATIVE_COUNT = 3


def _normalize_module(module: str) -> str:
    m = (module or "").strip().lower()
    if m not in _VALID_MODULES:
        raise ValueError(f"unsupported evo module: {module!r}")
    return m


def _to_record(episode: AgentEvoEpisode) -> EpisodeRecord:
    try:
        tags = json.loads(episode.compliance_tags or "[]")
        if not isinstance(tags, list):
            tags = []
    except (TypeError, ValueError):
        tags = []
    return EpisodeRecord(
        id=int(episode.id),
        episode_type=episode.episode_type,  # type: ignore[arg-type]
        module=episode.module,  # type: ignore[arg-type]
        signal=episode.signal,  # type: ignore[arg-type]
        compliance_tags=[str(t) for t in tags],
        parent_episode_id=episode.parent_episode_id,
    )


def _linked_memory_hits(session: Session, episode: AgentEvoEpisode) -> list[AgentEvoMemoryHit]:
    query = str(episode.query_text or "").strip()
    if not query:
        return []
    return (
        session.query(AgentEvoMemoryHit)
        .filter(AgentEvoMemoryHit.user_id == str(episode.user_id or ""))
        .filter(AgentEvoMemoryHit.module == str(episode.module or ""))
        .filter(AgentEvoMemoryHit.query_text == query)
        .order_by(AgentEvoMemoryHit.created_at.desc(), AgentEvoMemoryHit.id.desc())
        .limit(20)
        .all()
    )


def _ensure_review_queue(
    session: Session,
    *,
    target_type: str,
    target_id: int,
    reason: str,
    priority: int = 70,
) -> None:
    existing = (
        session.query(AgentEvoReviewQueue)
        .filter(AgentEvoReviewQueue.target_type == target_type)
        .filter(AgentEvoReviewQueue.target_id == int(target_id))
        .filter(AgentEvoReviewQueue.status == "pending")
        .first()
    )
    if existing is not None:
        return
    session.add(
        AgentEvoReviewQueue(
            target_type=target_type,
            target_id=int(target_id),
            reason=reason,
            priority=int(priority or 0),
            status="pending",
        )
    )


def _negative_feedback_count(session: Session, episode: AgentEvoEpisode) -> int:
    query = str(episode.query_text or "").strip()
    if not query:
        return 0
    return (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.user_id == str(episode.user_id or ""))
        .filter(AgentEvoEpisode.module == str(episode.module or ""))
        .filter(AgentEvoEpisode.query_text == query)
        .filter(AgentEvoEpisode.signal.in_(("thumb_down", "correction")))
        .count()
    )


def _apply_feedback_to_linked_memories(
    session: Session,
    *,
    episode: AgentEvoEpisode,
    signal: str,
    actor: str,
) -> None:
    hits = _linked_memory_hits(session, episode)
    if not hits:
        return
    seen: set[tuple[str, int]] = set()
    negative_count = _negative_feedback_count(session, episode)
    for hit in hits:
        memory_type = str(hit.memory_type or "").strip()
        memory_id = int(hit.memory_id or 0)
        key = (memory_type, memory_id)
        if key in seen:
            continue
        seen.add(key)
        model = _MEMORY_MODELS.get(memory_type)
        if model is None or memory_id <= 0:
            continue
        memory = session.get(model, memory_id)
        if memory is None:
            continue
        old_confidence = float(getattr(memory, "confidence", 0.0) or 0.0)
        old_status = str(getattr(memory, "status", "") or "")
        if signal == "thumb_up":
            new_confidence = min(1.0, old_confidence + _POSITIVE_CONFIDENCE_BUMP)
            action = "memory_feedback_positive"
        else:
            new_confidence = max(0.0, old_confidence - _NEGATIVE_CONFIDENCE_DECAY)
            action = "memory_feedback_negative"
            if new_confidence <= 0.2:
                setattr(memory, "status", "auto_disabled")
                _ensure_review_queue(
                    session,
                    target_type=memory_type,
                    target_id=memory_id,
                    reason="命中后负反馈导致置信度过低，自动下线待复盘",
                    priority=85,
                )
            elif negative_count >= _AUTO_REVIEW_NEGATIVE_COUNT and old_status in {"active", "auto"}:
                setattr(memory, "status", "pending")
                _ensure_review_queue(
                    session,
                    target_type=memory_type,
                    target_id=memory_id,
                    reason="同类问题连续负反馈达到阈值，转入待审",
                    priority=80,
                )
        setattr(memory, "confidence", new_confidence)
        session.add(memory)
        log_audit(
            session,
            actor=actor,
            action=action,
            target_type=memory_type,
            target_id=memory_id,
            payload={
                "episode_id": int(episode.id),
                "signal": signal,
                "confidence": {"from": old_confidence, "to": new_confidence},
                "status": {"from": old_status, "to": str(getattr(memory, "status", "") or "")},
                "hit_id": int(hit.id),
            },
        )


def record_episode(
    session: Session,
    *,
    module: str,
    user_id: str,
    store_id: str = "",
    request_id: str = "",
    query_text: str = "",
    response_text: str = "",
    episode_type: str = "reply",
) -> EpisodeRecord:
    """记录一次 Agent 回答（默认 episode_type=reply）。调用方控制 commit。"""
    norm_module = _normalize_module(module)
    tags = detect_compliance_tags(query_text, response_text)

    episode = AgentEvoEpisode(
        episode_type=str(episode_type or "reply")[:32],
        module=norm_module,
        user_id=str(user_id or "")[:64],
        store_id=str(store_id or "")[:64],
        request_id=str(request_id or "")[:64],
        query_text=str(query_text or ""),
        response_text=str(response_text or ""),
        signal="none",
        compliance_tags=json.dumps(tags, ensure_ascii=False),
    )
    session.add(episode)
    session.flush()  # 取到 id 用于审计

    log_audit(
        session,
        actor=f"user:{user_id}" if user_id else "system",
        action="episode_write",
        target_type="episode",
        target_id=episode.id,
        payload={
            "module": norm_module,
            "episode_type": episode_type,
            "compliance_tags": tags,
            "request_id": request_id,
        },
    )
    _log.info(
        "evo episode recorded id=%s module=%s user=%s tags=%s",
        episode.id,
        norm_module,
        user_id,
        tags,
    )
    return _to_record(episode)


def apply_feedback(
    session: Session,
    *,
    episode_id: int,
    signal: str,
    actor_user_id: str = "",
) -> EpisodeRecord:
    """更新 episode.signal=thumb_up/thumb_down。"""
    if signal not in ("thumb_up", "thumb_down"):
        raise ValueError(f"unsupported signal: {signal!r}")

    episode = session.get(AgentEvoEpisode, int(episode_id))
    if episode is None:
        raise LookupError(f"episode not found: {episode_id}")

    episode.signal = signal
    session.add(episode)
    actor = f"user:{actor_user_id}" if actor_user_id else "system"
    log_audit(
        session,
        actor=actor,
        action="episode_feedback",
        target_type="episode",
        target_id=episode.id,
        payload={"signal": signal},
    )
    _apply_feedback_to_linked_memories(
        session,
        episode=episode,
        signal=signal,
        actor=actor,
    )
    if signal == "thumb_down":
        try:
            extract_semantic_from_negative_feedback(
                session,
                episode_id=int(episode.id),
                actor_user_id=actor_user_id,
            )
        except Exception:
            _log.exception("semantic extraction from negative feedback failed episode_id=%s", episode.id)
    _log.info("evo episode feedback id=%s signal=%s", episode.id, signal)
    return _to_record(episode)


def apply_correction(
    session: Session,
    *,
    episode_id: int,
    correction_text: str,
    actor_user_id: str = "",
) -> EpisodeRecord:
    """写入一条 episode_type=correction 的子记录，指向原 episode。"""
    parent = session.get(AgentEvoEpisode, int(episode_id))
    if parent is None:
        raise LookupError(f"episode not found: {episode_id}")

    text = (correction_text or "").strip()
    if not text:
        raise ValueError("correction_text required")

    tags = detect_compliance_tags(text)
    correction = AgentEvoEpisode(
        episode_type="correction",
        module=parent.module,
        user_id=str(actor_user_id or parent.user_id)[:64],
        store_id=parent.store_id,
        request_id=parent.request_id,
        query_text=parent.query_text,
        response_text=parent.response_text,
        signal="correction",
        correction_text=text,
        compliance_tags=json.dumps(tags, ensure_ascii=False),
        parent_episode_id=int(parent.id),
    )
    session.add(correction)

    # 同步把父 episode 的 signal 标成 correction，便于后续按 signal 反查
    parent.signal = "correction"
    session.add(parent)
    session.flush()
    actor = f"user:{actor_user_id}" if actor_user_id else "system"
    _apply_feedback_to_linked_memories(
        session,
        episode=parent,
        signal="correction",
        actor=actor,
    )

    log_audit(
        session,
        actor=actor,
        action="episode_correction",
        target_type="episode",
        target_id=correction.id,
        payload={
            "parent_episode_id": int(parent.id),
            "compliance_tags": tags,
        },
    )
    try:
        extract_semantic_from_correction(
            session,
            correction_episode_id=int(correction.id),
            actor_user_id=actor_user_id,
        )
    except Exception:
        _log.exception("semantic extraction from correction failed correction_id=%s", correction.id)
    _log.info(
        "evo episode correction id=%s parent=%s module=%s",
        correction.id,
        parent.id,
        parent.module,
    )
    return _to_record(correction)
