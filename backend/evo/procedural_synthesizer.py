"""Procedural skill synthesis for Hermes Route B Phase 4."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import config as app_config
from dify_client import run_workflow_blocking
from dify_utils import _extract_data_and_outputs, _parse_json_like, _pick_value, _to_text
from evo.audit import log_audit
from evo.retriever import _cosine_text
from models import AgentEvoEpisode, AgentEvoProcedural, AgentEvoReflective

_log = logging.getLogger("jewelry_qipei.evo.procedural")

DEFAULT_CONFIDENCE = 0.7
DEFAULT_MIN_CLUSTER_SIZE = 3
DEFAULT_MIN_USERS = 2
DEFAULT_SIMILARITY_THRESHOLD = 0.38
DEFAULT_STALE_DAYS = 30
ACTIVE_REFLECTIVE_STATUSES = ("active",)
ACTIVE_PROCEDURAL_STATUSES = ("active", "auto")


@dataclass(frozen=True)
class ProceduralDraft:
    title: str
    trigger: list[str]
    do: list[str]
    dont: list[str]
    example: str
    confidence: float = DEFAULT_CONFIDENCE


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _json_list(values: Iterable[Any]) -> str:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        out.append(value)
        seen.add(normalized)
    return json.dumps(out, ensure_ascii=False)


def _json_int_list(values: Iterable[Any]) -> str:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            continue
        if ivalue in seen:
            continue
        out.append(ivalue)
        seen.add(ivalue)
    return json.dumps(out, ensure_ascii=False)


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if raw is None:
        return []
    if isinstance(raw, str):
        parsed = _parse_json_like(raw)
        if "_list" in parsed and isinstance(parsed["_list"], list):
            return parsed["_list"]
        if parsed:
            for key in ("items", "trigger", "do", "dont", "list", "values"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return value
            return [parsed]
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def _parse_int_list(raw: Any) -> list[int]:
    out: list[int] = []
    for value in _parse_json_list(raw):
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _active_reflections(session, *, now: datetime) -> list[AgentEvoReflective]:
    return (
        session.query(AgentEvoReflective)
        .filter(AgentEvoReflective.status.in_(ACTIVE_REFLECTIVE_STATUSES))
        .filter(AgentEvoReflective.expires_at > now)
        .filter(AgentEvoReflective.promoted_to_procedural_id.is_(None))
        .order_by(AgentEvoReflective.created_at.asc(), AgentEvoReflective.id.asc())
        .all()
    )


def _cluster_reflections(
    rows: list[AgentEvoReflective],
    *,
    similarity_threshold: float,
) -> list[list[AgentEvoReflective]]:
    clusters: list[list[AgentEvoReflective]] = []
    assigned: set[int] = set()
    for seed in rows:
        if int(seed.id) in assigned:
            continue
        cluster = [seed]
        assigned.add(int(seed.id))
        for candidate in rows:
            if int(candidate.id) in assigned:
                continue
            if _cosine_text(seed.lesson or "", candidate.lesson or "") >= similarity_threshold:
                cluster.append(candidate)
                assigned.add(int(candidate.id))
        clusters.append(cluster)
    return clusters


def _episode_ids_for(cluster: list[AgentEvoReflective]) -> list[int]:
    ids: list[int] = []
    for row in cluster:
        ids.extend(_parse_int_list(row.evidence_episode_ids))
    return ids


def _episode_map(session, episode_ids: list[int]) -> dict[int, AgentEvoEpisode]:
    if not episode_ids:
        return {}
    rows = session.query(AgentEvoEpisode).filter(AgentEvoEpisode.id.in_(episode_ids)).all()
    return {int(row.id): row for row in rows}


def _users_for(cluster: list[AgentEvoReflective], episodes_by_id: dict[int, AgentEvoEpisode]) -> set[str]:
    users: set[str] = set()
    for row in cluster:
        if row.scope_type == "user" and row.scope_id:
            users.add(row.scope_id)
        for eid in _parse_int_list(row.evidence_episode_ids):
            episode = episodes_by_id.get(eid)
            if episode is not None and episode.user_id:
                users.add(episode.user_id)
    return users


def _scope_for(cluster: list[AgentEvoReflective], episodes_by_id: dict[int, AgentEvoEpisode]) -> tuple[str, str]:
    store_ids: list[str] = []
    for row in cluster:
        if row.scope_type == "store" and row.scope_id:
            store_ids.append(row.scope_id)
        for eid in _parse_int_list(row.evidence_episode_ids):
            episode = episodes_by_id.get(eid)
            if episode is not None and episode.store_id:
                store_ids.append(episode.store_id)
    if store_ids:
        return "store", Counter(store_ids).most_common(1)[0][0]

    user_ids = [row.scope_id for row in cluster if row.scope_type == "user" and row.scope_id]
    if user_ids:
        return "user", Counter(user_ids).most_common(1)[0][0]
    return "store", ""


def _summary_for_dify(cluster: list[AgentEvoReflective], episodes_by_id: dict[int, AgentEvoEpisode]) -> str:
    lines: list[str] = []
    for row in cluster:
        evidence = []
        for eid in _parse_int_list(row.evidence_episode_ids):
            episode = episodes_by_id.get(eid)
            if episode is None:
                continue
            evidence.append(
                f"episode_id={episode.id}; user={episode.user_id}; query={_clean_text(episode.query_text, limit=160)}"
            )
        lines.append(
            f"- reflective_id={row.id}; scope={row.scope_type}:{row.scope_id}; "
            f"confidence={float(row.confidence or 0.0):.2f}; lesson={_clean_text(row.lesson, limit=420)}; "
            f"evidence=[{'; '.join(evidence)}]"
        )
    return "\n".join(lines)


def _coerce_str_list(value: Any, *, limit: int = 6) -> list[str]:
    out: list[str] = []
    for item in _parse_json_list(value):
        if isinstance(item, dict):
            item = item.get("text") or item.get("content") or item.get("title") or ""
        text = _clean_text(item, limit=240)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _draft_from_obj(obj: dict[str, Any]) -> ProceduralDraft | None:
    title = _clean_text(obj.get("title") or obj.get("name") or obj.get("skill_title"), limit=120)
    trigger = _coerce_str_list(obj.get("trigger") or obj.get("triggers") or obj.get("trigger_json"))
    do_items = _coerce_str_list(obj.get("do") or obj.get("dos") or obj.get("do_json") or obj.get("steps"))
    dont_items = _coerce_str_list(obj.get("dont") or obj.get("don't") or obj.get("dont_json") or obj.get("avoid"))
    example = _clean_text(obj.get("example") or obj.get("sample") or obj.get("response_example"), limit=1000)
    try:
        confidence = float(obj.get("confidence") or DEFAULT_CONFIDENCE)
    except (TypeError, ValueError):
        confidence = DEFAULT_CONFIDENCE
    if not title or not do_items:
        return None
    return ProceduralDraft(
        title=title,
        trigger=trigger or [title],
        do=do_items,
        dont=dont_items,
        example=example,
        confidence=max(0.1, min(1.0, confidence)),
    )


def _extract_json_candidate(data: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    for key in ("procedural_json", "skill_json", "result_json", "result", "answer", "text"):
        parsed = _parse_json_like(_pick_value(data, outputs, key))
        if "_list" in parsed and parsed["_list"]:
            first = parsed["_list"][0]
            return first if isinstance(first, dict) else {}
        if parsed:
            return parsed
    return {}


def _extract_with_dify(
    cluster: list[AgentEvoReflective],
    episodes_by_id: dict[int, AgentEvoEpisode],
    *,
    scope_type: str,
    scope_id: str,
    now: datetime,
) -> ProceduralDraft | None:
    api_key = _to_text(getattr(app_config, "DIFY_EVO_PROCEDURAL_API_KEY", ""))
    if not api_key:
        return None
    base = _to_text(getattr(app_config, "DIFY_EVO_PROCEDURAL_API_BASE", "")) or _to_text(
        getattr(app_config, "DIFY_API_BASE", "")
    )
    if not base:
        return None
    try:
        timeout = float(getattr(app_config, "DIFY_EVO_PROCEDURAL_TIMEOUT", 30.0) or 30.0)
    except (TypeError, ValueError):
        timeout = 30.0

    raw = run_workflow_blocking(
        base_url=base.rstrip("/"),
        api_key=api_key,
        inputs={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "synthesized_at": now.isoformat(),
            "reflective_lessons": _summary_for_dify(cluster, episodes_by_id),
        },
        user=f"evo-procedural:{scope_id or scope_type}",
        workflow_id=_to_text(getattr(app_config, "DIFY_EVO_PROCEDURAL_WORKFLOW_ID", "")) or None,
        timeout_sec=timeout,
    )
    if not isinstance(raw, dict) or raw.get("code") != 200:
        return None
    data, outputs = _extract_data_and_outputs(raw)
    direct = _draft_from_obj(
        {
            "title": _pick_value(data, outputs, "title", "skill_title"),
            "trigger": _pick_value(data, outputs, "trigger", "triggers", "trigger_json"),
            "do": _pick_value(data, outputs, "do", "dos", "do_json", "steps"),
            "dont": _pick_value(data, outputs, "dont", "dont_json", "avoid"),
            "example": _pick_value(data, outputs, "example", "response_example"),
            "confidence": _pick_value(data, outputs, "confidence"),
        }
    )
    if direct is not None:
        return direct
    obj = _extract_json_candidate(data, outputs)
    return _draft_from_obj(obj) if obj else None


def _fallback_draft(
    cluster: list[AgentEvoReflective],
    episodes_by_id: dict[int, AgentEvoEpisode],
) -> ProceduralDraft | None:
    lessons = [_clean_text(row.lesson, limit=700) for row in cluster if _clean_text(row.lesson)]
    if not lessons:
        return None
    combined = "；".join(lessons)
    title = "通用应对话术"
    if "价格" in combined or "太贵" in combined or "预算" in combined:
        title = "价格异议标准应对话术"
    elif "保值" in combined or "升值" in combined:
        title = "保值问题标准应对话术"
    elif "证书" in combined:
        title = "证书问题标准应对话术"

    trigger: list[str] = []
    for episode in episodes_by_id.values():
        query = _clean_text(episode.query_text, limit=40)
        if query and query not in trigger:
            trigger.append(query)
        if len(trigger) >= 3:
            break
    if title == "价格异议标准应对话术":
        trigger = ["客户嫌价格贵", "价格异议", "预算沟通", *trigger]
    elif not trigger:
        trigger = [title]

    do_items: list[str] = []
    if "预算" in combined:
        do_items.append("先问预算，确认客户可接受的价格区间")
    if "材质" in combined or "工艺" in combined:
        do_items.append("再结合材质、工艺和款式价值说明价格依据")
    if "售后" in combined or "佩戴" in combined:
        do_items.append("补充售后保障和日常佩戴价值")
    if not do_items:
        do_items = lessons[:3]

    dont_items: list[str] = []
    if "打折" in combined or title == "价格异议标准应对话术":
        dont_items.append("不要一开始就直接打折或承诺额外优惠")
    if "保值" in combined or "升值" in combined or "承诺" in combined:
        dont_items.append("不要承诺保值、稳赚或绝对升值")

    example = ""
    if title == "价格异议标准应对话术":
        example = "客户觉得价格高时，先询问预算，再说明材质、工艺、款式价值和售后保障，最后给出匹配预算的选择。"
    confidence_values = [float(row.confidence or 0.0) for row in cluster]
    base = sum(confidence_values) / max(1, len(confidence_values))
    confidence = min(0.9, max(DEFAULT_CONFIDENCE, base + min(0.12, len(cluster) * 0.03)))
    return ProceduralDraft(
        title=title,
        trigger=trigger[:6],
        do=do_items[:6],
        dont=dont_items[:6],
        example=example,
        confidence=confidence,
    )


def _has_existing_procedural(session, reflective_ids: list[int]) -> bool:
    wanted = set(int(item) for item in reflective_ids)
    if not wanted:
        return False
    rows = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .all()
    )
    for row in rows:
        existing = set(_parse_int_list(row.source_reflective_ids_json))
        if existing == wanted:
            return True
    return False


def _write_procedural(
    session,
    *,
    cluster: list[AgentEvoReflective],
    draft: ProceduralDraft,
    scope_type: str,
    scope_id: str,
    source_episode_ids: list[int],
) -> AgentEvoProcedural:
    source_reflective_ids = [int(row.id) for row in cluster]
    row = AgentEvoProcedural(
        scope_type=scope_type,
        scope_id=scope_id,
        title=draft.title,
        trigger_json=_json_list(draft.trigger),
        do_json=_json_list(draft.do),
        dont_json=_json_list(draft.dont),
        example=draft.example,
        source_reflective_ids_json=_json_int_list(source_reflective_ids),
        source_episode_ids_json=_json_int_list(source_episode_ids),
        confidence=max(0.1, min(1.0, draft.confidence)),
        status="auto",
        write_mode="auto",
        eval_case_ids_json="[]",
    )
    session.add(row)
    session.flush()

    for reflective in cluster:
        reflective.promoted_to_procedural_id = int(row.id)
        session.add(reflective)

    log_audit(
        session,
        actor="system",
        action="procedural_write",
        target_type="procedural",
        target_id=row.id,
        payload={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "source_reflective_ids": source_reflective_ids,
            "source_episode_ids": source_episode_ids,
            "status": row.status,
            "write_mode": row.write_mode,
        },
    )
    _log.info("procedural memory written id=%s scope=%s:%s title=%s", row.id, scope_type, scope_id, row.title)
    return row


def run_procedural_synthesis(
    session,
    *,
    now: datetime | None = None,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_users: int = DEFAULT_MIN_USERS,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[AgentEvoProcedural]:
    """Cluster active reflective lessons and write reusable procedural skill drafts."""
    current = now or datetime.now(timezone.utc)
    written: list[AgentEvoProcedural] = []
    reflections = _active_reflections(session, now=current)
    for cluster in _cluster_reflections(reflections, similarity_threshold=similarity_threshold):
        if len(cluster) < max(1, int(min_cluster_size or DEFAULT_MIN_CLUSTER_SIZE)):
            continue
        source_reflective_ids = [int(row.id) for row in cluster]
        if _has_existing_procedural(session, source_reflective_ids):
            continue
        source_episode_ids = _episode_ids_for(cluster)
        episodes_by_id = _episode_map(session, source_episode_ids)
        if len(_users_for(cluster, episodes_by_id)) < max(1, int(min_users or DEFAULT_MIN_USERS)):
            continue
        scope_type, scope_id = _scope_for(cluster, episodes_by_id)
        draft = _extract_with_dify(cluster, episodes_by_id, scope_type=scope_type, scope_id=scope_id, now=current)
        if draft is None:
            draft = _fallback_draft(cluster, episodes_by_id)
        if draft is None:
            continue
        written.append(
            _write_procedural(
                session,
                cluster=cluster,
                draft=draft,
                scope_type=scope_type,
                scope_id=scope_id,
                source_episode_ids=source_episode_ids,
            )
        )
    return written


def disable_stale_procedural_memories(
    session,
    *,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> int:
    """Auto-disable procedural rules that have not been hit in the configured window."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max(1, int(stale_days or DEFAULT_STALE_DAYS)))
    disabled = 0
    rows = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .all()
    )
    for row in rows:
        last_seen = _as_utc(row.last_hit_at) or _as_utc(row.created_at)
        if last_seen is None or last_seen > cutoff:
            continue
        row.status = "auto_disabled"
        session.add(row)
        disabled += 1
        log_audit(
            session,
            actor="system",
            action="procedural_auto_disable_stale",
            target_type="procedural",
            target_id=row.id,
            payload={"last_seen_at": last_seen.isoformat(), "stale_days": stale_days},
        )
    return disabled
