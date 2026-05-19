"""Reflective self-review loop for Hermes Route B Phase 3."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

import config as app_config
from dify_client import run_workflow_blocking
from dify_utils import _extract_data_and_outputs, _parse_json_like, _pick_value, _to_text
from evo.audit import log_audit
from models import AgentEvoEpisode, AgentEvoReflective

_log = logging.getLogger("jewelry_qipei.evo.reflector")

DEFAULT_CONFIDENCE = 0.5
DEFAULT_TTL_DAYS = 30
DEFAULT_WINDOW_HOURS = 24
MIN_LESSON_LENGTH = 8

LessonGenerator = Callable[[list[AgentEvoEpisode], datetime], Iterable[dict[str, Any]]]


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _json_list(values: Iterable[int]) -> str:
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        ivalue = int(value)
        if ivalue not in seen:
            out.append(ivalue)
            seen.add(ivalue)
    return json.dumps(out, ensure_ascii=False)


def _normalize_scope(value: str) -> str:
    scope = (value or "").strip().lower()
    if scope in {"store", "门店"}:
        return "store"
    return "user"


def _scope_id_for(scope_type: str, episodes: list[AgentEvoEpisode]) -> str:
    first = next((ep for ep in episodes if ep.episode_type != "correction"), episodes[0])
    if scope_type == "store" and first.store_id:
        return first.store_id
    return first.user_id


def _coerce_evidence_ids(raw: Any) -> list[int]:
    if isinstance(raw, str):
        parsed = _parse_json_like(raw)
        if "_list" in parsed:
            raw = parsed["_list"]
        elif parsed:
            raw = parsed.get("evidence_episode_ids") or parsed.get("evidence_ids") or []
        else:
            raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _parse_lesson_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    parsed = _parse_json_like(raw)
    if "_list" in parsed and isinstance(parsed["_list"], list):
        return [item for item in parsed["_list"] if isinstance(item, dict)]
    if parsed:
        items = parsed.get("lessons") or parsed.get("items") or parsed.get("result")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return [parsed]
    return []


def _episode_summary(episodes: list[AgentEvoEpisode]) -> str:
    lines: list[str] = []
    for ep in episodes:
        if ep.episode_type == "correction":
            detail = ep.correction_text
        else:
            detail = ep.response_text
        lines.append(
            f"- episode_id={ep.id}; type={ep.episode_type}; signal={ep.signal}; "
            f"query={_clean_text(ep.query_text, limit=240)}; content={_clean_text(detail, limit=360)}"
        )
    return "\n".join(lines)


def _extract_with_dify(episodes: list[AgentEvoEpisode], now: datetime) -> list[dict[str, Any]]:
    api_key = _to_text(getattr(app_config, "DIFY_EVO_REFLECTIVE_API_KEY", ""))
    if not api_key:
        return []
    base = _to_text(getattr(app_config, "DIFY_EVO_REFLECTIVE_API_BASE", "")) or _to_text(
        getattr(app_config, "DIFY_API_BASE", "")
    )
    if not base:
        return []
    try:
        timeout = float(getattr(app_config, "DIFY_EVO_REFLECTIVE_TIMEOUT", 30.0) or 30.0)
    except (TypeError, ValueError):
        timeout = 30.0

    first = episodes[0]
    raw = run_workflow_blocking(
        base_url=base.rstrip("/"),
        api_key=api_key,
        inputs={
            "user_id": first.user_id,
            "store_id": first.store_id,
            "window_ended_at": now.isoformat(),
            "episodes": _episode_summary(episodes),
        },
        user=_clean_text(first.user_id) or "evo-reflective",
        workflow_id=_to_text(getattr(app_config, "DIFY_EVO_REFLECTIVE_WORKFLOW_ID", "")) or None,
        timeout_sec=timeout,
    )
    if not isinstance(raw, dict) or raw.get("code") != 200:
        return []
    data, outputs = _extract_data_and_outputs(raw)
    for key in ("reflective_json", "lessons_json", "result_json", "result", "lessons", "answer", "text"):
        lessons = _parse_lesson_list(_pick_value(data, outputs, key))
        if lessons:
            return lessons
    return []


def _fallback_lessons(episodes: list[AgentEvoEpisode], _now: datetime) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    corrections = [ep for ep in episodes if ep.episode_type == "correction" and _clean_text(ep.correction_text)]
    for correction in corrections:
        evidence = [int(correction.id)]
        if correction.parent_episode_id:
            evidence.insert(0, int(correction.parent_episode_id))
        lesson = _clean_text(correction.correction_text, limit=700)
        if not lesson:
            continue
        lessons.append(
            {
                "lesson": f"下次遇到同类问题时，优先采用这条纠正经验：{lesson}",
                "evidence_episode_ids": evidence,
                "scope_suggestion": "user",
                "confidence": DEFAULT_CONFIDENCE,
            }
        )

    negative = [
        ep
        for ep in episodes
        if ep.episode_type == "reply" and ep.signal == "thumb_down" and _clean_text(ep.query_text or ep.response_text)
    ]
    for episode in negative:
        lessons.append(
            {
                "lesson": (
                    "下次遇到同类问题时，先重新核对依据，再组织回答；"
                    f"这类回答曾被标记为不准确：{_clean_text(episode.query_text or episode.response_text, limit=420)}"
                ),
                "evidence_episode_ids": [int(episode.id)],
                "scope_suggestion": "user",
                "confidence": 0.4,
            }
        )
    return lessons


def _default_lesson_generator(episodes: list[AgentEvoEpisode], now: datetime) -> list[dict[str, Any]]:
    lessons = _extract_with_dify(episodes, now)
    if lessons:
        return lessons
    return _fallback_lessons(episodes, now)


def _recent_episode_groups(session, *, now: datetime, window_hours: int) -> dict[str, list[AgentEvoEpisode]]:
    since = now - timedelta(hours=max(1, int(window_hours or DEFAULT_WINDOW_HOURS)))
    rows = (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.created_at >= since)
        .filter(AgentEvoEpisode.created_at <= now)
        .filter(AgentEvoEpisode.user_id != "")
        .order_by(AgentEvoEpisode.user_id.asc(), AgentEvoEpisode.created_at.asc(), AgentEvoEpisode.id.asc())
        .all()
    )
    groups: dict[str, list[AgentEvoEpisode]] = defaultdict(list)
    group_ids: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        key = row.user_id
        if row.episode_type == "correction" and row.parent_episode_id:
            parent = session.get(AgentEvoEpisode, int(row.parent_episode_id))
            if parent is not None and parent.user_id:
                key = parent.user_id
                if int(parent.id) not in group_ids[key]:
                    groups[key].append(parent)
                    group_ids[key].add(int(parent.id))
        if int(row.id) not in group_ids[key]:
            groups[key].append(row)
            group_ids[key].add(int(row.id))
    return dict(groups)


def _has_existing_reflection(session, *, evidence_ids: list[int]) -> bool:
    if not evidence_ids:
        return False
    wanted = set(evidence_ids)
    for row in session.query(AgentEvoReflective).filter(AgentEvoReflective.status != "archived").all():
        try:
            existing = set(int(item) for item in json.loads(row.evidence_episode_ids or "[]"))
        except (TypeError, ValueError):
            existing = set()
        if existing == wanted:
            return True
    return False


def _write_reflection(
    session,
    *,
    episodes: list[AgentEvoEpisode],
    item: dict[str, Any],
    now: datetime,
) -> AgentEvoReflective | None:
    valid_episode_ids = {int(ep.id) for ep in episodes}
    evidence_ids = [eid for eid in _coerce_evidence_ids(item.get("evidence_episode_ids") or item.get("evidence_ids")) if eid in valid_episode_ids]
    if not evidence_ids:
        return None
    lesson = _clean_text(item.get("lesson") or item.get("content") or item.get("reflection"))
    if len(lesson) < MIN_LESSON_LENGTH:
        return None
    if _has_existing_reflection(session, evidence_ids=evidence_ids):
        return None

    try:
        confidence = float(item.get("confidence") or DEFAULT_CONFIDENCE)
    except (TypeError, ValueError):
        confidence = DEFAULT_CONFIDENCE
    scope_type = _normalize_scope(_to_text(item.get("scope_suggestion") or item.get("scope_type") or "user"))
    scope_id = _scope_id_for(scope_type, episodes)
    row = AgentEvoReflective(
        scope_type=scope_type,
        scope_id=scope_id,
        lesson=lesson,
        evidence_episode_ids=_json_list(evidence_ids),
        confidence=max(0.1, min(1.0, confidence)),
        status="active",
        expires_at=now + timedelta(days=DEFAULT_TTL_DAYS),
    )
    session.add(row)
    session.flush()
    log_audit(
        session,
        actor="system",
        action="reflective_write",
        target_type="reflective",
        target_id=row.id,
        payload={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "evidence_episode_ids": evidence_ids,
            "expires_at": row.expires_at.isoformat(),
        },
    )
    _log.info("reflective memory written id=%s scope=%s:%s", row.id, scope_type, scope_id)
    return row


def expire_stale_reflections(session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = (
        session.query(AgentEvoReflective)
        .filter(AgentEvoReflective.status == "active")
        .filter(AgentEvoReflective.expires_at <= now)
        .all()
    )
    for row in rows:
        row.status = "archived"
        session.add(row)
        log_audit(
            session,
            actor="system",
            action="reflective_archive_expired",
            target_type="reflective",
            target_id=row.id,
            payload={"expires_at": row.expires_at.isoformat()},
        )
    return len(rows)


def run_reflection_cycle(
    session,
    *,
    now: datetime | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    lesson_generator: LessonGenerator | None = None,
) -> list[AgentEvoReflective]:
    """Review recent episodes by user and write evidence-backed reflective lessons."""
    current = now or datetime.now(timezone.utc)
    generator = lesson_generator or _default_lesson_generator
    written: list[AgentEvoReflective] = []
    expire_stale_reflections(session, now=current)

    for user_id, episodes in _recent_episode_groups(session, now=current, window_hours=window_hours).items():
        if not episodes:
            continue
        try:
            candidates = list(generator(episodes, current))
        except Exception:
            _log.exception("reflective lesson generation failed user_id=%s", user_id)
            continue
        for item in candidates:
            if not isinstance(item, dict):
                continue
            row = _write_reflection(session, episodes=episodes, item=item, now=current)
            if row is not None:
                written.append(row)
    return written
