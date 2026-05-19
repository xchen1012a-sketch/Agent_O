"""Manual orchestration for the real Evo memory pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from evo.governance import get_governance_data_health
from evo.procedural_synthesizer import disable_stale_procedural_memories, run_procedural_synthesis
from evo.promoter import run_promotion_scan
from evo.reflector import run_reflection_cycle
from models import AgentEvoEpisode


def _feedback_source_count(session: Session) -> int:
    return (
        session.query(AgentEvoEpisode)
        .filter(AgentEvoEpisode.signal.in_(("thumb_down", "correction")))
        .count()
    )


def run_pipeline_advance(
    session: Session,
    *,
    now: datetime | None = None,
    window_hours: int = 24 * 30,
) -> dict[str, Any]:
    """Advance existing feedback through reflection, procedural synthesis, and promotion scan.

    This does not fabricate retrieval hits. Promotion proposals still require real query hits
    written by the retriever during normal assistant/QA usage.
    """
    current = now or datetime.now(timezone.utc)
    feedback_source_count = _feedback_source_count(session)
    reflective_rows = run_reflection_cycle(session, now=current, window_hours=window_hours)
    disabled_count = disable_stale_procedural_memories(session, now=current)
    procedural_rows = run_procedural_synthesis(session, now=current)
    promotion_rows = run_promotion_scan(session, now=current)
    data_health = get_governance_data_health(session)

    reasons: list[str] = []
    if feedback_source_count <= 0:
        reasons.append("no_feedback_for_reflection")
    if data_health["procedural_count"] <= 0:
        reasons.append("no_active_procedural")
    if data_health["memory_hit_count"] <= 0:
        reasons.append("needs_real_query_hits")

    created_count = len(reflective_rows) + len(procedural_rows) + len(promotion_rows)
    status = "advanced" if created_count > 0 else "no_new_writes"
    return {
        "summary": {
            "status": status,
            "created_count": created_count,
            "feedback_source_count": feedback_source_count,
            "reflective_created_count": len(reflective_rows),
            "procedural_created_count": len(procedural_rows),
            "procedural_disabled_count": disabled_count,
            "promotion_created_count": len(promotion_rows),
            "reasons": reasons,
        },
        "created": {
            "reflective_ids": [int(row.id) for row in reflective_rows],
            "procedural_ids": [int(row.id) for row in procedural_rows],
            "promotion_ids": [int(row.id) for row in promotion_rows],
        },
        "data_health": data_health,
    }
