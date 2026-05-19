"""Cross-scope promotion proposals for Hermes Route B Phase 5."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from evo.audit import log_audit
from evo.retriever import _cosine_text
from models import (
    AgentEvoEvalCase,
    AgentEvoEpisode,
    AgentEvoMemoryHit,
    AgentEvoProcedural,
    AgentEvoPromotion,
    AgentEvoSemantic,
    User,
)

_log = logging.getLogger("jewelry_qipei.evo.promoter")

ACTIVE_PROCEDURAL_STATUSES = ("active", "auto")
ACTIVE_SEMANTIC_STATUSES = ("active",)
OPEN_PROMOTION_STATUSES = ("pending", "approved")
DEFAULT_MIN_USER_SCOPES = 3
DEFAULT_MIN_STORE_SCOPES = 2
DEFAULT_MIN_TOTAL_HITS = 3
DEFAULT_PROCEDURAL_SIMILARITY = 0.72
DEFAULT_SEMANTIC_SIMILARITY = 0.85


def _clean_text(value: Any, *, limit: int = 2000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_list(values: Iterable[Any]) -> str:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        marker = str(value).strip()
        if not marker or marker in seen:
            continue
        out.append(value)
        seen.add(marker)
    return _json_dumps(out)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw or "").strip() or "null")
    except (TypeError, ValueError):
        return None


def _parse_list(raw: Any) -> list[Any]:
    parsed = _parse_json(raw)
    if isinstance(parsed, list):
        return parsed
    if parsed is None:
        return []
    return [parsed]


def _parse_int_list(raw: Any) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in _parse_list(raw):
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item in seen:
            continue
        out.append(item)
        seen.add(item)
    return out


def _scope_key(scope_type: str, scope_id: str) -> str:
    return f"{str(scope_type or '').strip()}:{str(scope_id or '').strip()}"


def _split_scope(scope: str) -> tuple[str, str]:
    left, sep, right = str(scope or "").partition(":")
    if not sep:
        return left.strip(), ""
    return left.strip(), right.strip()


def _procedural_text(row: AgentEvoProcedural) -> str:
    return " ".join(
        part
        for part in (
            row.title,
            _clean_text(row.trigger_json),
            _clean_text(row.do_json),
            _clean_text(row.dont_json),
            _clean_text(row.example),
        )
        if part
    )


def _semantic_text(row: AgentEvoSemantic) -> str:
    return " ".join(part for part in (row.trigger_text, row.content) if part)


def _cluster_rows(rows: list[Any], *, threshold: float, text_getter) -> list[list[Any]]:
    clusters: list[list[Any]] = []
    assigned: set[int] = set()
    for seed in rows:
        seed_id = int(seed.id)
        if seed_id in assigned:
            continue
        cluster = [seed]
        assigned.add(seed_id)
        seed_text = text_getter(seed)
        for candidate in rows:
            candidate_id = int(candidate.id)
            if candidate_id in assigned:
                continue
            if _cosine_text(seed_text, text_getter(candidate)) >= threshold:
                cluster.append(candidate)
                assigned.add(candidate_id)
        clusters.append(cluster)
    return clusters


def _episode_ids_for(rows: Iterable[AgentEvoProcedural]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        for episode_id in _parse_int_list(row.source_episode_ids_json):
            if episode_id in seen:
                continue
            ids.append(episode_id)
            seen.add(episode_id)
    return ids


def _store_id_for_user_procedural(session: Session, row: AgentEvoProcedural) -> str:
    episode_ids = _parse_int_list(row.source_episode_ids_json)
    if episode_ids:
        episodes = session.query(AgentEvoEpisode).filter(AgentEvoEpisode.id.in_(episode_ids)).all()
        stores = [episode.store_id for episode in episodes if episode.store_id]
        if stores:
            return Counter(stores).most_common(1)[0][0]

    user = (
        session.query(User)
        .filter((User.user_id == row.scope_id) | (User.username == row.scope_id))
        .first()
    )
    return str(user.store_id or "").strip() if user is not None else ""


def _promotion_source_ids(promotion: AgentEvoPromotion) -> set[int]:
    evidence = _parse_json(promotion.evidence)
    if not isinstance(evidence, dict):
        return set()
    return set(_parse_int_list(evidence.get("source_memory_ids")))


def _has_existing_promotion(
    session: Session,
    *,
    source_memory_type: str,
    source_memory_ids: list[int],
    current_scope: str,
    target_scope: str,
) -> bool:
    wanted = set(int(item) for item in source_memory_ids)
    rows = (
        session.query(AgentEvoPromotion)
        .filter(AgentEvoPromotion.source_memory_type == source_memory_type)
        .filter(AgentEvoPromotion.current_scope == current_scope)
        .filter(AgentEvoPromotion.target_scope == target_scope)
        .filter(AgentEvoPromotion.status.in_(OPEN_PROMOTION_STATUSES))
        .all()
    )
    return any(_promotion_source_ids(row) == wanted for row in rows)


def _bound_case_matches_source(row: AgentEvoEvalCase, *, memory_type: str, source_ids: set[int]) -> bool:
    refs = _parse_list(row.bound_memory_ids)
    if not refs:
        return True
    for ref in refs:
        if isinstance(ref, dict):
            ref_type = str(ref.get("type") or ref.get("memory_type") or "").strip()
            raw_id = ref.get("id") or ref.get("memory_id")
        else:
            ref_type = ""
            raw_id = ref
        try:
            ref_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if ref_id in source_ids and (not ref_type or ref_type == memory_type):
            return True
    return False


def _eval_case_preview(row: AgentEvoEvalCase) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "module": row.module,
        "source": row.source,
        "severity": int(row.severity or 0),
        "question": row.question,
        "bound_memory_ids": _parse_list(row.bound_memory_ids),
    }


def _required_eval_case_previews(
    session: Session,
    *,
    memory_type: str,
    source_memory_ids: Iterable[int],
) -> list[dict[str, Any]]:
    source_ids = {int(item) for item in source_memory_ids if int(item or 0) > 0}
    rows = (
        session.query(AgentEvoEvalCase)
        .filter(AgentEvoEvalCase.status == "active")
        .order_by(AgentEvoEvalCase.id.asc())
        .all()
    )
    matched = [
        row
        for row in rows
        if not source_ids or _bound_case_matches_source(row, memory_type=memory_type, source_ids=source_ids)
    ]
    source_rank = {"baseline": 0, "promotion_preflight": 1, "manual": 2, "document_import": 3}
    matched.sort(key=lambda row: (source_rank.get(str(row.source or ""), 9), int(row.id)))
    return [_eval_case_preview(row) for row in matched[:20]]


def _preview_status(*, scope_count: int, min_scope_count: int, hit_count: int, min_total_hits: int) -> str:
    if scope_count >= min_scope_count and hit_count >= min_total_hits:
        return "ready"
    return "needs_more_data"


def _procedural_candidate_preview(
    *,
    proposal_type: str,
    source_ids: list[int],
    current_scope: str,
    target_scope: str,
    scope_ids: list[str],
    hit_count: int,
    min_scope_count: int,
    min_total_hits: int,
    titles: list[str],
    required_eval_cases: list[dict[str, Any]] | None = None,
    existing_promotion: bool = False,
) -> dict[str, Any]:
    return {
        "proposal_type": proposal_type,
        "source_memory_type": "procedural",
        "source_memory_ids": source_ids,
        "current_scope": current_scope,
        "target_scope": target_scope,
        "scope_ids": scope_ids,
        "scope_count": len(scope_ids),
        "hit_count": int(hit_count or 0),
        "min_scope_count": int(min_scope_count or 0),
        "min_total_hits": int(min_total_hits or 0),
        "missing_scope_count": max(0, int(min_scope_count or 0) - len(scope_ids)),
        "missing_hit_count": max(0, int(min_total_hits or 0) - int(hit_count or 0)),
        "status": _preview_status(
            scope_count=len(scope_ids),
            min_scope_count=min_scope_count,
            hit_count=hit_count,
            min_total_hits=min_total_hits,
        ),
        "titles": titles,
        "required_eval_cases": required_eval_cases or [],
        "existing_promotion": existing_promotion,
    }


def _user_procedural_candidate_previews(
    session: Session,
    *,
    min_user_scopes: int,
    min_total_hits: int,
    procedural_similarity_threshold: float,
) -> list[dict[str, Any]]:
    rows = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.scope_type == "user")
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .order_by(AgentEvoProcedural.id.asc())
        .all()
    )
    by_store: dict[str, list[AgentEvoProcedural]] = {}
    for row in rows:
        store_id = _store_id_for_user_procedural(session, row)
        if store_id:
            by_store.setdefault(store_id, []).append(row)

    previews: list[dict[str, Any]] = []
    for store_id, store_rows in by_store.items():
        for cluster in _cluster_rows(
            store_rows,
            threshold=procedural_similarity_threshold,
            text_getter=_procedural_text,
        ):
            scope_ids = sorted({row.scope_id for row in cluster if row.scope_id})
            hit_count = sum(int(row.hit_count or 0) for row in cluster)
            source_ids = [int(row.id) for row in sorted(cluster, key=lambda item: int(item.id))]
            previews.append(
                _procedural_candidate_preview(
                    proposal_type="user_procedural_to_store",
                    source_ids=source_ids,
                    current_scope="user:*",
                    target_scope=f"store:{store_id}",
                    scope_ids=scope_ids,
                    hit_count=hit_count,
                    min_scope_count=min_user_scopes,
                    min_total_hits=min_total_hits,
                    titles=[row.title for row in cluster],
                    existing_promotion=_has_existing_promotion(
                        session,
                        source_memory_type="procedural",
                        source_memory_ids=source_ids,
                        current_scope="user:*",
                        target_scope=f"store:{store_id}",
                    ),
                )
            )
    return previews


def _store_procedural_candidate_previews(
    session: Session,
    *,
    min_store_scopes: int,
    min_total_hits: int,
    procedural_similarity_threshold: float,
) -> list[dict[str, Any]]:
    rows = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.scope_type == "store")
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .order_by(AgentEvoProcedural.id.asc())
        .all()
    )
    previews: list[dict[str, Any]] = []
    for cluster in _cluster_rows(rows, threshold=procedural_similarity_threshold, text_getter=_procedural_text):
        scope_ids = sorted({row.scope_id for row in cluster if row.scope_id})
        hit_count = sum(int(row.hit_count or 0) for row in cluster)
        source_ids = [int(row.id) for row in sorted(cluster, key=lambda item: int(item.id))]
        previews.append(
            _procedural_candidate_preview(
                proposal_type="store_procedural_to_global",
                source_ids=source_ids,
                current_scope="store:*",
                target_scope="global:global",
                scope_ids=scope_ids,
                hit_count=hit_count,
                min_scope_count=min_store_scopes,
                min_total_hits=min_total_hits,
                titles=[row.title for row in cluster],
                required_eval_cases=_required_eval_case_previews(
                    session,
                    memory_type="procedural",
                    source_memory_ids=source_ids,
                ),
                existing_promotion=_has_existing_promotion(
                    session,
                    source_memory_type="procedural",
                    source_memory_ids=source_ids,
                    current_scope="store:*",
                    target_scope="global:global",
                ),
            )
        )
    return previews


def _candidate_previews(
    session: Session,
    *,
    min_user_scopes: int,
    min_store_scopes: int,
    min_total_hits: int,
    procedural_similarity_threshold: float,
) -> list[dict[str, Any]]:
    previews = []
    previews.extend(
        _user_procedural_candidate_previews(
            session,
            min_user_scopes=min_user_scopes,
            min_total_hits=min_total_hits,
            procedural_similarity_threshold=procedural_similarity_threshold,
        )
    )
    previews.extend(
        _store_procedural_candidate_previews(
            session,
            min_store_scopes=min_store_scopes,
            min_total_hits=min_total_hits,
            procedural_similarity_threshold=procedural_similarity_threshold,
        )
    )
    previews.sort(
        key=lambda item: (
            0 if item.get("status") == "ready" else 1,
            int(item.get("missing_scope_count") or 0) + int(item.get("missing_hit_count") or 0),
            str(item.get("proposal_type") or ""),
        )
    )
    return previews[:10]


def promotion_scan_diagnostics(session: Session) -> dict[str, Any]:
    procedural_count = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .count()
    )
    hit_procedural_count = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .count()
    )
    user_hit_procedural_count = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.scope_type == "user")
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .count()
    )
    store_hit_procedural_count = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.scope_type == "store")
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .count()
    )
    active_semantic_count = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.status.in_(ACTIVE_SEMANTIC_STATUSES))
        .count()
    )
    memory_hit_count = session.query(AgentEvoMemoryHit).count()
    open_promotion_count = (
        session.query(AgentEvoPromotion)
        .filter(AgentEvoPromotion.status.in_(OPEN_PROMOTION_STATUSES))
        .count()
    )

    reasons: list[str] = []
    if procedural_count <= 0:
        reasons.append("no_active_procedural")
    elif hit_procedural_count <= 0:
        reasons.append("no_hit_procedural")
    if memory_hit_count <= 0:
        reasons.append("no_memory_hits")
    if active_semantic_count < 2 and hit_procedural_count <= 0:
        reasons.append("not_enough_semantic_for_merge")

    return {
        "procedural_count": procedural_count,
        "hit_procedural_count": hit_procedural_count,
        "user_hit_procedural_count": user_hit_procedural_count,
        "store_hit_procedural_count": store_hit_procedural_count,
        "active_semantic_count": active_semantic_count,
        "memory_hit_count": memory_hit_count,
        "open_promotion_count": open_promotion_count,
        "candidate_preview": _candidate_previews(
            session,
            min_user_scopes=DEFAULT_MIN_USER_SCOPES,
            min_store_scopes=DEFAULT_MIN_STORE_SCOPES,
            min_total_hits=DEFAULT_MIN_TOTAL_HITS,
            procedural_similarity_threshold=DEFAULT_PROCEDURAL_SIMILARITY,
        ),
        "reasons": reasons,
        "status": "ready" if not reasons else "not_ready",
    }


def _write_promotion(
    session: Session,
    *,
    source_memory_type: str,
    source_memory_ids: list[int],
    current_scope: str,
    target_scope: str,
    reason: str,
    evidence: dict[str, Any],
    now: datetime,
) -> AgentEvoPromotion | None:
    if not source_memory_ids:
        return None
    if _has_existing_promotion(
        session,
        source_memory_type=source_memory_type,
        source_memory_ids=source_memory_ids,
        current_scope=current_scope,
        target_scope=target_scope,
    ):
        return None

    row = AgentEvoPromotion(
        source_memory_type=source_memory_type,
        source_memory_id=int(source_memory_ids[0]),
        current_scope=current_scope,
        target_scope=target_scope,
        reason=reason,
        evidence=_json_dumps(evidence),
        status="pending",
        suggested_at=now,
    )
    session.add(row)
    session.flush()
    log_audit(
        session,
        actor="system",
        action="promotion_suggest",
        target_type="promotion",
        target_id=row.id,
        payload={
            "source_memory_type": source_memory_type,
            "source_memory_ids": source_memory_ids,
            "current_scope": current_scope,
            "target_scope": target_scope,
            "proposal_type": evidence.get("proposal_type"),
        },
    )
    _log.info("promotion suggested id=%s type=%s target=%s", row.id, source_memory_type, target_scope)
    return row


def _user_procedural_candidates(
    session: Session,
    *,
    now: datetime,
    min_user_scopes: int,
    min_total_hits: int,
    procedural_similarity_threshold: float,
) -> list[AgentEvoPromotion]:
    rows = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.scope_type == "user")
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .order_by(AgentEvoProcedural.id.asc())
        .all()
    )
    by_store: dict[str, list[AgentEvoProcedural]] = {}
    for row in rows:
        store_id = _store_id_for_user_procedural(session, row)
        if not store_id:
            continue
        by_store.setdefault(store_id, []).append(row)

    created: list[AgentEvoPromotion] = []
    for store_id, store_rows in by_store.items():
        for cluster in _cluster_rows(
            store_rows,
            threshold=procedural_similarity_threshold,
            text_getter=_procedural_text,
        ):
            scope_ids = sorted({row.scope_id for row in cluster if row.scope_id})
            hit_count = sum(int(row.hit_count or 0) for row in cluster)
            if len(scope_ids) < min_user_scopes or hit_count < min_total_hits:
                continue
            source_ids = [int(row.id) for row in sorted(cluster, key=lambda item: int(item.id))]
            evidence = {
                "proposal_type": "user_procedural_to_store",
                "source_memory_ids": source_ids,
                "source_episode_ids": _episode_ids_for(cluster),
                "scope_ids": scope_ids,
                "hit_count": hit_count,
                "avg_confidence": round(sum(float(row.confidence or 0.0) for row in cluster) / len(cluster), 4),
                "similarity_threshold": procedural_similarity_threshold,
                "titles": [row.title for row in cluster],
            }
            row = _write_promotion(
                session,
                source_memory_type="procedural",
                source_memory_ids=source_ids,
                current_scope="user:*",
                target_scope=f"store:{store_id}",
                reason="多个员工的相似技能规则已被命中，建议升级为门店规则。",
                evidence=evidence,
                now=now,
            )
            if row is not None:
                created.append(row)
    return created


def _store_procedural_candidates(
    session: Session,
    *,
    now: datetime,
    min_store_scopes: int,
    min_total_hits: int,
    procedural_similarity_threshold: float,
) -> list[AgentEvoPromotion]:
    rows = (
        session.query(AgentEvoProcedural)
        .filter(AgentEvoProcedural.scope_type == "store")
        .filter(AgentEvoProcedural.status.in_(ACTIVE_PROCEDURAL_STATUSES))
        .filter(AgentEvoProcedural.hit_count > 0)
        .order_by(AgentEvoProcedural.id.asc())
        .all()
    )
    created: list[AgentEvoPromotion] = []
    for cluster in _cluster_rows(rows, threshold=procedural_similarity_threshold, text_getter=_procedural_text):
        scope_ids = sorted({row.scope_id for row in cluster if row.scope_id})
        hit_count = sum(int(row.hit_count or 0) for row in cluster)
        if len(scope_ids) < min_store_scopes or hit_count < min_total_hits:
            continue
        source_ids = [int(row.id) for row in sorted(cluster, key=lambda item: int(item.id))]
        required_eval_cases = _required_eval_case_previews(
            session,
            memory_type="procedural",
            source_memory_ids=source_ids,
        )
        evidence = {
            "proposal_type": "store_procedural_to_global",
            "source_memory_ids": source_ids,
            "source_episode_ids": _episode_ids_for(cluster),
            "scope_ids": scope_ids,
            "hit_count": hit_count,
            "avg_confidence": round(sum(float(row.confidence or 0.0) for row in cluster) / len(cluster), 4),
            "similarity_threshold": procedural_similarity_threshold,
            "titles": [row.title for row in cluster],
            "requires_eval_cases": True,
            "required_eval_case_ids": [int(item["id"]) for item in required_eval_cases],
            "required_eval_cases": required_eval_cases,
        }
        row = _write_promotion(
            session,
            source_memory_type="procedural",
            source_memory_ids=source_ids,
            current_scope="store:*",
            target_scope="global:global",
            reason="多个门店复用了相似技能规则，建议升级为全局规则；批准前需绑定回归测试。",
            evidence=evidence,
            now=now,
        )
        if row is not None:
            created.append(row)
    return created


def _semantic_merge_candidates(
    session: Session,
    *,
    now: datetime,
    semantic_similarity_threshold: float,
) -> list[AgentEvoPromotion]:
    rows = (
        session.query(AgentEvoSemantic)
        .filter(AgentEvoSemantic.status.in_(ACTIVE_SEMANTIC_STATUSES))
        .order_by(AgentEvoSemantic.scope_type.asc(), AgentEvoSemantic.scope_id.asc(), AgentEvoSemantic.id.asc())
        .all()
    )
    by_scope: dict[str, list[AgentEvoSemantic]] = {}
    for row in rows:
        by_scope.setdefault(_scope_key(row.scope_type, row.scope_id), []).append(row)

    created: list[AgentEvoPromotion] = []
    for scope, scoped_rows in by_scope.items():
        if len(scoped_rows) < 2:
            continue
        for cluster in _cluster_rows(
            scoped_rows,
            threshold=semantic_similarity_threshold,
            text_getter=_semantic_text,
        ):
            if len(cluster) < 2:
                continue
            source_ids = [int(row.id) for row in sorted(cluster, key=lambda item: int(item.id))]
            evidence = {
                "proposal_type": "semantic_merge",
                "source_memory_ids": source_ids,
                "scope": scope,
                "similarity_threshold": semantic_similarity_threshold,
                "contents": [row.content for row in cluster],
                "triggers": [row.trigger_text for row in cluster],
            }
            row = _write_promotion(
                session,
                source_memory_type="semantic_merge",
                source_memory_ids=source_ids,
                current_scope=scope,
                target_scope=scope,
                reason="同一作用域存在高度相似的事实记忆，建议合并去重。",
                evidence=evidence,
                now=now,
            )
            if row is not None:
                created.append(row)
    return created


def run_promotion_scan(
    session: Session,
    *,
    now: datetime | None = None,
    min_user_scopes: int = DEFAULT_MIN_USER_SCOPES,
    min_store_scopes: int = DEFAULT_MIN_STORE_SCOPES,
    min_total_hits: int = DEFAULT_MIN_TOTAL_HITS,
    procedural_similarity_threshold: float = DEFAULT_PROCEDURAL_SIMILARITY,
    semantic_similarity_threshold: float = DEFAULT_SEMANTIC_SIMILARITY,
) -> list[AgentEvoPromotion]:
    """Scan active memories and enqueue human-reviewed cross-scope promotion proposals."""
    current = now or datetime.now(timezone.utc)
    min_users = max(1, int(min_user_scopes or DEFAULT_MIN_USER_SCOPES))
    min_stores = max(1, int(min_store_scopes or DEFAULT_MIN_STORE_SCOPES))
    min_hits = max(0, int(min_total_hits or DEFAULT_MIN_TOTAL_HITS))
    procedural_threshold = max(0.0, min(1.0, float(procedural_similarity_threshold)))
    semantic_threshold = max(0.0, min(1.0, float(semantic_similarity_threshold)))

    created: list[AgentEvoPromotion] = []
    created.extend(
        _user_procedural_candidates(
            session,
            now=current,
            min_user_scopes=min_users,
            min_total_hits=min_hits,
            procedural_similarity_threshold=procedural_threshold,
        )
    )
    created.extend(
        _store_procedural_candidates(
            session,
            now=current,
            min_store_scopes=min_stores,
            min_total_hits=min_hits,
            procedural_similarity_threshold=procedural_threshold,
        )
    )
    created.extend(
        _semantic_merge_candidates(
            session,
            now=current,
            semantic_similarity_threshold=semantic_threshold,
        )
    )
    return created


def _merge_json_lists(rows: Iterable[AgentEvoProcedural], field_name: str) -> str:
    values: list[Any] = []
    for row in rows:
        values.extend(_parse_list(getattr(row, field_name)))
    return _json_list(values)


def _merge_int_json_lists(rows: Iterable[AgentEvoProcedural], field_name: str) -> str:
    values: list[int] = []
    seen: set[int] = set()
    for row in rows:
        for value in _parse_int_list(getattr(row, field_name)):
            if value in seen:
                continue
            values.append(value)
            seen.add(value)
    return _json_dumps(values)


def _load_procedural_sources(session: Session, evidence: dict[str, Any]) -> list[AgentEvoProcedural]:
    ids = _parse_int_list(evidence.get("source_memory_ids"))
    if not ids:
        return []
    rows = session.query(AgentEvoProcedural).filter(AgentEvoProcedural.id.in_(ids)).all()
    by_id = {int(row.id): row for row in rows}
    return [by_id[item] for item in ids if item in by_id]


def _write_promoted_procedural(
    session: Session,
    *,
    promotion: AgentEvoPromotion,
    source_rows: list[AgentEvoProcedural],
    now: datetime,
) -> AgentEvoProcedural | None:
    if not source_rows:
        return None
    target_scope_type, target_scope_id = _split_scope(promotion.target_scope)
    if target_scope_type not in {"store", "global"}:
        return None

    primary = source_rows[0]
    confidence = sum(float(row.confidence or 0.0) for row in source_rows) / len(source_rows)
    target = AgentEvoProcedural(
        scope_type=target_scope_type,
        scope_id=target_scope_id,
        title=primary.title,
        trigger_json=_merge_json_lists(source_rows, "trigger_json"),
        do_json=_merge_json_lists(source_rows, "do_json"),
        dont_json=_merge_json_lists(source_rows, "dont_json"),
        example=primary.example,
        source_reflective_ids_json=_merge_int_json_lists(source_rows, "source_reflective_ids_json"),
        source_episode_ids_json=_merge_int_json_lists(source_rows, "source_episode_ids_json"),
        confidence=max(0.1, min(1.0, confidence)),
        status="active",
        write_mode="human",
        eval_case_ids_json=_merge_int_json_lists(source_rows, "eval_case_ids_json"),
        hit_count=0,
        created_at=now,
    )
    session.add(target)
    session.flush()
    log_audit(
        session,
        actor=f"user:{promotion.decided_by}" if promotion.decided_by else "system",
        action="procedural_promote",
        target_type="procedural",
        target_id=target.id,
        payload={
            "promotion_id": promotion.id,
            "target_scope": promotion.target_scope,
            "source_memory_ids": [int(row.id) for row in source_rows],
            "write_mode": target.write_mode,
            "status": target.status,
        },
    )
    return target


def _eval_case_ids_for_promotion(evidence: dict[str, Any], source_rows: list[AgentEvoProcedural]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for key in ("eval_case_ids", "required_eval_case_ids"):
        for item in _parse_int_list(evidence.get(key)):
            if item in seen:
                continue
            ids.append(item)
            seen.add(item)
    for row in source_rows:
        for item in _parse_int_list(row.eval_case_ids_json):
            if item in seen:
                continue
            ids.append(item)
            seen.add(item)
    return ids


def _run_global_promotion_preflight(
    session: Session,
    *,
    promotion: AgentEvoPromotion,
    source_rows: list[AgentEvoProcedural],
    evidence: dict[str, Any],
    now: datetime,
) -> tuple[bool, list[int]]:
    eval_case_ids = _eval_case_ids_for_promotion(evidence, source_rows)
    if not eval_case_ids:
        _update_evidence(
            promotion,
            {
                "global_preflight": {
                    "status": "blocked",
                    "reason": "missing_eval_cases",
                    "checked_at": now.isoformat(),
                }
            },
        )
        log_audit(
            session,
            actor=f"user:{promotion.decided_by}" if promotion.decided_by else "system",
            action="promotion_block",
            target_type="promotion",
            target_id=promotion.id,
            payload={"reason": "missing_eval_cases", "target_scope": promotion.target_scope},
        )
        return False, []

    from evo.eval_runner import run_eval_cases

    runs = run_eval_cases(
        session,
        case_ids=eval_case_ids,
        triggered_by=f"promotion:{promotion.id}",
        now=now,
    )
    failed_runs = [run for run in runs if run.status != "passed"]
    missing_runs = len(runs) != len(eval_case_ids)
    _update_evidence(
        promotion,
        {
            "global_preflight": {
                "status": "failed" if failed_runs or missing_runs else "passed",
                "checked_at": now.isoformat(),
                "eval_case_ids": eval_case_ids,
                "run_ids": [int(run.id) for run in runs],
                "failed_run_ids": [int(run.id) for run in failed_runs],
                "missing_run_count": max(0, len(eval_case_ids) - len(runs)),
            }
        },
    )
    if failed_runs or missing_runs:
        log_audit(
            session,
            actor=f"user:{promotion.decided_by}" if promotion.decided_by else "system",
            action="promotion_block",
            target_type="promotion",
            target_id=promotion.id,
            payload={
                "reason": "eval_preflight_failed" if failed_runs else "eval_case_missing_or_inactive",
                "target_scope": promotion.target_scope,
                "failed_run_ids": [int(run.id) for run in failed_runs],
                "expected_case_ids": eval_case_ids,
                "actual_run_ids": [int(run.id) for run in runs],
            },
        )
        return False, [int(run.id) for run in failed_runs]
    return True, [int(run.id) for run in runs]


def _update_evidence(promotion: AgentEvoPromotion, updates: dict[str, Any]) -> None:
    evidence = _parse_json(promotion.evidence)
    if not isinstance(evidence, dict):
        evidence = {}
    evidence.update(updates)
    promotion.evidence = _json_dumps(evidence)


def approve_promotion(
    session: Session,
    promotion_id: int,
    *,
    decided_by: str,
    now: datetime | None = None,
) -> tuple[AgentEvoPromotion, AgentEvoProcedural | None]:
    """Approve a pending proposal. Procedural cross-scope proposals write a target rule."""
    promotion = session.get(AgentEvoPromotion, int(promotion_id))
    if promotion is None:
        raise LookupError("promotion not found")
    if promotion.status != "pending":
        raise ValueError("promotion is not pending")

    current = now or datetime.now(timezone.utc)
    actor = str(decided_by or "").strip()

    target: AgentEvoProcedural | None = None
    evidence = _parse_json(promotion.evidence)
    proposal_type = evidence.get("proposal_type") if isinstance(evidence, dict) else ""
    source_rows: list[AgentEvoProcedural] = []
    if promotion.source_memory_type == "procedural" and proposal_type in {
        "user_procedural_to_store",
        "store_procedural_to_global",
    }:
        source_rows = _load_procedural_sources(session, evidence if isinstance(evidence, dict) else {})
        target_scope_type, _target_scope_id = _split_scope(promotion.target_scope)
        if target_scope_type == "global":
            promotion.decided_by = actor
            ok, _run_ids = _run_global_promotion_preflight(
                session,
                promotion=promotion,
                source_rows=source_rows,
                evidence=evidence if isinstance(evidence, dict) else {},
                now=current,
            )
            if not ok:
                promotion.status = "blocked"
                promotion.decided_at = current
                session.add(promotion)
                return promotion, None

    promotion.status = "approved"
    promotion.decided_at = current
    promotion.decided_by = actor

    if promotion.source_memory_type == "procedural" and proposal_type in {
        "user_procedural_to_store",
        "store_procedural_to_global",
    }:
        target = _write_promoted_procedural(session, promotion=promotion, source_rows=source_rows, now=current)
        if target is not None:
            _update_evidence(promotion, {"target_memory_type": "procedural", "target_memory_id": int(target.id)})

    session.add(promotion)
    log_audit(
        session,
        actor=f"user:{decided_by}" if decided_by else "system",
        action="promotion_approve",
        target_type="promotion",
        target_id=promotion.id,
        payload={
            "source_memory_type": promotion.source_memory_type,
            "source_memory_id": promotion.source_memory_id,
            "current_scope": promotion.current_scope,
            "target_scope": promotion.target_scope,
            "target_memory_id": int(target.id) if target is not None else None,
        },
    )
    return promotion, target


def reject_promotion(
    session: Session,
    promotion_id: int,
    *,
    decided_by: str,
    now: datetime | None = None,
) -> AgentEvoPromotion:
    """Reject a pending promotion proposal without mutating source memories."""
    promotion = session.get(AgentEvoPromotion, int(promotion_id))
    if promotion is None:
        raise LookupError("promotion not found")
    if promotion.status != "pending":
        raise ValueError("promotion is not pending")

    promotion.status = "rejected"
    promotion.decided_at = now or datetime.now(timezone.utc)
    promotion.decided_by = str(decided_by or "").strip()
    session.add(promotion)
    log_audit(
        session,
        actor=f"user:{decided_by}" if decided_by else "system",
        action="promotion_reject",
        target_type="promotion",
        target_id=promotion.id,
        payload={
            "source_memory_type": promotion.source_memory_type,
            "source_memory_id": promotion.source_memory_id,
            "current_scope": promotion.current_scope,
            "target_scope": promotion.target_scope,
        },
    )
    return promotion


def list_promotions(
    session: Session,
    *,
    status: str | None = "pending",
    limit: int = 50,
) -> list[AgentEvoPromotion]:
    query = session.query(AgentEvoPromotion)
    normalized_status = str(status or "").strip()
    if normalized_status:
        query = query.filter(AgentEvoPromotion.status == normalized_status)
    return (
        query.order_by(AgentEvoPromotion.suggested_at.desc(), AgentEvoPromotion.id.desc())
        .limit(max(1, min(200, int(limit or 50))))
        .all()
    )
