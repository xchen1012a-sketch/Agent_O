"""Evo memory retrieval and prompt-block injection for Hermes Route B."""

from __future__ import annotations

import math
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from evo.audit import log_audit
from models import AgentEvoMemoryHit, AgentEvoProcedural, AgentEvoReflective, AgentEvoSemantic

ACTIVE_MEMORY_STATUSES = ("active",)
ACTIVE_REFLECTIVE_STATUSES = ("active",)
ACTIVE_PROCEDURAL_STATUSES = ("active", "auto")
DEFAULT_LIMIT = 5
MIN_SCORE = 0.08
PROCEDURAL_SCORE_WEIGHT = 0.82
REFLECTIVE_SCORE_WEIGHT = 0.62
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MemoryHit:
    memory_id: int
    memory_type: str
    scope_type: str
    scope_id: str
    content: str
    trigger_text: str
    confidence: float
    score: float


def _clean(text: str) -> str:
    return _SPACE_RE.sub("", str(text or "").strip().lower())


def _char_ngrams(text: str) -> list[str]:
    cleaned = _clean(text)
    if not cleaned:
        return []
    tokens = list(cleaned)
    tokens.extend(cleaned[i : i + 2] for i in range(len(cleaned) - 1))
    return tokens


def _vector(tokens: Iterable[str]) -> tuple[dict[str, float], float]:
    counts = Counter(tokens)
    if not counts:
        return {}, 0.0
    vector = {token: float(count) for token, count in counts.items()}
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    return vector, norm


def _cosine_text(a: str, b: str) -> float:
    avec, anorm = _vector(_char_ngrams(a))
    bvec, bnorm = _vector(_char_ngrams(b))
    if not avec or not bvec or anorm == 0.0 or bnorm == 0.0:
        return 0.0
    if len(avec) > len(bvec):
        avec, bvec = bvec, avec
        anorm, bnorm = bnorm, anorm
    dot = sum(weight * bvec.get(token, 0.0) for token, weight in avec.items())
    return dot / (anorm * bnorm)


def _scope_weight(scope_type: str) -> float:
    if scope_type == "user":
        return 1.35
    if scope_type == "store":
        return 1.1
    if scope_type == "global":
        return 0.95
    return 1.0


def _time_decay(memory) -> float:
    created = memory.created_at
    if created is None:
        return 1.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400.0)
    # Semantic facts are long-lived. Keep decay gentle, just enough to break ties.
    return max(0.75, 1.0 - age_days * 0.002)


def _trigger_bonus(query: str, trigger: str) -> float:
    q = _clean(query)
    t = _clean(trigger)
    if not q or not t:
        return 1.0
    if t in q or q in t:
        return 1.25
    shared = set(_char_ngrams(q)) & set(_char_ngrams(t))
    return 1.1 if len(shared) >= 4 else 1.0


def _candidate_query(session: Session, *, user_id: str, store_id: str):
    return (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.status.in_(ACTIVE_MEMORY_STATUSES))
        .filter(
            or_(
                (AgentEvoSemantic.scope_type == "user") & (AgentEvoSemantic.scope_id == str(user_id or "")),
                (AgentEvoSemantic.scope_type == "store") & (AgentEvoSemantic.scope_id == str(store_id or "")),
                AgentEvoSemantic.scope_type == "global",
            )
        )
    )


def _reflective_candidate_query(session: Session, *, user_id: str, store_id: str):
    now = datetime.now(timezone.utc)
    return (
        session.query(AgentEvoReflective)
        .filter(AgentEvoReflective.status.in_(ACTIVE_REFLECTIVE_STATUSES))
        .filter(AgentEvoReflective.expires_at > now)
        .filter(
            or_(
                (AgentEvoReflective.scope_type == "user") & (AgentEvoReflective.scope_id == str(user_id or "")),
                (AgentEvoReflective.scope_type == "store") & (AgentEvoReflective.scope_id == str(store_id or "")),
            )
        )
    )


def _procedural_candidate_query(session: Session, *, user_id: str, store_id: str):
    return (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(
            or_(
                (AgentEvoProcedural.scope_type == "user") & (AgentEvoProcedural.scope_id == str(user_id or "")),
                (AgentEvoProcedural.scope_type == "store") & (AgentEvoProcedural.scope_id == str(store_id or "")),
                AgentEvoProcedural.scope_type == "global",
            )
        )
    )


def _json_text(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(parsed, list):
        return "；".join(str(item) for item in parsed if str(item or "").strip())
    if isinstance(parsed, dict):
        return "；".join(str(value) for value in parsed.values() if str(value or "").strip())
    return str(parsed or "")


def _score_semantic_memory(query_text: str, memory: AgentEvoSemantic) -> float:
    similarity = max(
        _cosine_text(query_text, memory.trigger_text or ""),
        _cosine_text(query_text, memory.content or "") * 0.85,
    )
    if similarity <= 0:
        return 0.0
    return (
        similarity
        * _scope_weight(memory.scope_type)
        * max(0.1, float(memory.confidence or 0.0))
        * _time_decay(memory)
        * _trigger_bonus(query_text, memory.trigger_text or "")
    )


def _score_procedural_memory(query_text: str, memory: AgentEvoProcedural) -> float:
    trigger_text = "；".join(part for part in (memory.title, _json_text(memory.trigger_json)) if part)
    body_text = "；".join(
        part
        for part in (
            memory.title,
            _json_text(memory.do_json),
            _json_text(memory.dont_json),
            memory.example,
        )
        if part
    )
    similarity = max(
        _cosine_text(query_text, trigger_text),
        _cosine_text(query_text, body_text) * 0.9,
    )
    if similarity <= 0:
        return 0.0
    return (
        similarity
        * _scope_weight(memory.scope_type)
        * max(0.1, float(memory.confidence or 0.0))
        * _time_decay(memory)
        * _trigger_bonus(query_text, trigger_text)
        * PROCEDURAL_SCORE_WEIGHT
    )


def _score_reflective_memory(query_text: str, memory: AgentEvoReflective) -> float:
    similarity = _cosine_text(query_text, memory.lesson or "")
    if similarity <= 0:
        return 0.0
    return (
        similarity
        * _scope_weight(memory.scope_type)
        * max(0.1, float(memory.confidence or 0.0))
        * _time_decay(memory)
        * REFLECTIVE_SCORE_WEIGHT
    )


def _procedural_content(memory: AgentEvoProcedural) -> str:
    parts = [memory.title]
    do_text = _json_text(memory.do_json)
    dont_text = _json_text(memory.dont_json)
    if do_text:
        parts.append(f"建议：{do_text}")
    if dont_text:
        parts.append(f"避免：{dont_text}")
    if memory.example:
        parts.append(f"示例：{memory.example}")
    return "；".join(part for part in parts if part)


def _to_hit(memory: AgentEvoSemantic, score: float) -> MemoryHit:
    return MemoryHit(
        memory_id=int(memory.id),
        memory_type="semantic",
        scope_type=memory.scope_type,
        scope_id=memory.scope_id,
        content=memory.content,
        trigger_text=memory.trigger_text,
        confidence=float(memory.confidence or 0.0),
        score=round(float(score), 6),
    )


def _procedural_to_hit(memory: AgentEvoProcedural, score: float) -> MemoryHit:
    return MemoryHit(
        memory_id=int(memory.id),
        memory_type="procedural",
        scope_type=memory.scope_type,
        scope_id=memory.scope_id,
        content=_procedural_content(memory),
        trigger_text=_json_text(memory.trigger_json) or memory.title,
        confidence=float(memory.confidence or 0.0),
        score=round(float(score), 6),
    )


def _reflective_to_hit(memory: AgentEvoReflective, score: float) -> MemoryHit:
    return MemoryHit(
        memory_id=int(memory.id),
        memory_type="reflective",
        scope_type=memory.scope_type,
        scope_id=memory.scope_id,
        content=memory.lesson,
        trigger_text="自反思经验",
        confidence=float(memory.confidence or 0.0),
        score=round(float(score), 6),
    )


def retrieve_semantic_memories(
    session: Session,
    *,
    user_id: str,
    store_id: str = "",
    module: str,
    query_text: str,
    limit: int = DEFAULT_LIMIT,
    write_hits: bool = True,
) -> list[MemoryHit]:
    query = str(query_text or "").strip()
    if not query:
        return []

    scored: list[tuple[float, str, AgentEvoSemantic | AgentEvoProcedural | AgentEvoReflective]] = []
    for memory in _candidate_query(session, user_id=user_id, store_id=store_id).all():
        score = _score_semantic_memory(query, memory)
        if score >= MIN_SCORE:
            scored.append((score, "semantic", memory))
    for memory in _procedural_candidate_query(session, user_id=user_id, store_id=store_id).all():
        score = _score_procedural_memory(query, memory)
        if score >= MIN_SCORE:
            scored.append((score, "procedural", memory))
    for memory in _reflective_candidate_query(session, user_id=user_id, store_id=store_id).all():
        score = _score_reflective_memory(query, memory)
        if score >= MIN_SCORE:
            scored.append((score, "reflective", memory))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[MemoryHit] = []
    for score, memory_type, memory in scored[: max(0, int(limit or DEFAULT_LIMIT))]:
        if memory_type == "semantic":
            hits.append(_to_hit(memory, score))
        elif memory_type == "procedural":
            hits.append(_procedural_to_hit(memory, score))
        else:
            hits.append(_reflective_to_hit(memory, score))

    if write_hits and hits:
        now = datetime.now(timezone.utc)
        for hit in hits:
            if hit.memory_type == "reflective":
                model = AgentEvoReflective
                bump = 0.03
            elif hit.memory_type == "procedural":
                model = AgentEvoProcedural
                bump = 0.04
            else:
                model = AgentEvoSemantic
                bump = 0.05
            memory = session.get(model, hit.memory_id)
            if memory is None:
                continue
            memory.hit_count = int(memory.hit_count or 0) + 1
            memory.last_hit_at = now
            memory.confidence = min(1.0, float(memory.confidence or 0.0) + bump)
            session.add(memory)
            session.add(
                AgentEvoMemoryHit(
                    memory_type=hit.memory_type,
                    memory_id=hit.memory_id,
                    user_id=str(user_id or ""),
                    module=str(module or "")[:32],
                    query_text=query,
                    score=hit.score,
                )
            )
        log_audit(
            session,
            actor=f"user:{user_id}" if user_id else "system",
            action="memory_retrieve",
            target_type="memory",
            target_id=",".join(f"{hit.memory_type}:{hit.memory_id}" for hit in hits),
            payload={
                "module": module,
                "query_text": query[:500],
                "hit_ids": [{"type": hit.memory_type, "id": hit.memory_id} for hit in hits],
                "scores": [hit.score for hit in hits],
            },
        )

    return hits


def build_memory_block(hits: list[MemoryHit], *, max_chars: int = 1600) -> str:
    if not hits:
        return ""
    lines = [
        "【自我进化记忆】",
        "以下是系统从过往纠正/反馈/自反思中沉淀的记忆，只作为回答约束与补充依据；若与当前问题无关，请忽略。",
    ]
    for idx, hit in enumerate(hits, start=1):
        scope_label = {"user": "个人", "store": "门店", "global": "全局"}.get(hit.scope_type, hit.scope_type)
        type_label = {
            "semantic": "事实记忆",
            "procedural": "技能规则",
            "reflective": "自反思经验",
        }.get(hit.memory_type, hit.memory_type)
        prefix = f"触发：{hit.trigger_text or '同类问题'}；" if hit.memory_type in {"semantic", "procedural"} else ""
        lines.append(f"{idx}. [{scope_label}｜{type_label}｜置信度 {hit.confidence:.2f}] {prefix}记忆：{hit.content}")
    block = "\n".join(lines).strip()
    if len(block) > max_chars:
        block = block[: max_chars - 1].rstrip() + "…"
    return block


def inject_memory_block(query_text: str, memory_block: str) -> str:
    text = str(query_text or "").strip()
    block = str(memory_block or "").strip()
    if not block:
        return text
    return f"{block}\n\n当前问题：{text}"
