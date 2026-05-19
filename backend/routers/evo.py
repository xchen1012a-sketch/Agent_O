"""Evo HTTP 接口——Phase 1 仅暴露 episode 反馈/纠正两个写入端点。"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api_response import success_response
from auth import get_current_user, is_management_role, normalize_app_role
from database import get_db
from evo import (
    EpisodeCorrectionRequest,
    EpisodeFeedbackRequest,
    apply_correction,
    apply_feedback,
    anomaly_scan_diagnostics,
    approve_promotion,
    list_promotions,
    promotion_scan_diagnostics,
    reject_promotion,
    run_anomaly_scan,
    run_eval_cases,
    run_pipeline_advance,
    run_promotion_scan,
    seed_default_eval_cases,
)
from evo.governance import (
    decide_review_queue_item,
    get_governance_overview,
    get_memory_detail,
    get_memory_row,
    list_review_queue,
    list_feedback_events,
    list_memories,
    rollback_memory,
    update_memory,
)
from models import AgentEvoAnomaly, AgentEvoEvalCase, AgentEvoEvalRun, AgentEvoPromotion, AgentEvoReviewQueue

router = APIRouter(prefix="/api/evo", tags=["evo"])
_log = logging.getLogger("jewelry_qipei.router.evo")


def _user_id(current_user: dict[str, Any] | None) -> str:
    return str((current_user or {}).get("user_id") or "").strip()


def _require_manager(current_user: dict[str, Any]) -> None:
    if not is_management_role(str((current_user or {}).get("role") or "")):
        raise HTTPException(status_code=403, detail="权限不足：仅管理员或店长可管理升级建议")


def _memory_scope_allowlist(current_user: dict[str, Any], scope_type: str) -> set[str] | None:
    if normalize_app_role(str((current_user or {}).get("role") or "")) != "store_manager":
        return None
    allowed = {"user", "store"}
    if scope_type == "global":
        raise HTTPException(status_code=403, detail="store managers can only view user/store scoped memories")
    return allowed


def _parse_evidence(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


_PREFLIGHT_LABELS = {
    "not_run": "未运行",
    "passed": "通过",
    "blocked": "阻断",
    "missing_cases": "缺用例",
}


def _promotion_preflight_runs(session: Session | None, row: AgentEvoPromotion, evidence: dict[str, Any]) -> list[AgentEvoEvalRun]:
    if session is None or not row.id:
        return []
    rows_by_id: dict[int, AgentEvoEvalRun] = {}
    for run in (
        session.query(AgentEvoEvalRun)
        .filter(AgentEvoEvalRun.triggered_by == f"promotion:{row.id}")
        .order_by(AgentEvoEvalRun.created_at.desc(), AgentEvoEvalRun.id.desc())
        .all()
    ):
        rows_by_id[int(run.id)] = run

    preflight = evidence.get("global_preflight") if isinstance(evidence, dict) else {}
    raw_run_ids = preflight.get("run_ids") if isinstance(preflight, dict) else []
    run_ids: list[int] = []
    if isinstance(raw_run_ids, list):
        for item in raw_run_ids:
            try:
                run_ids.append(int(item))
            except (TypeError, ValueError):
                continue
    if run_ids:
        for run in session.query(AgentEvoEvalRun).filter(AgentEvoEvalRun.id.in_(run_ids)).all():
            rows_by_id[int(run.id)] = run
    return sorted(rows_by_id.values(), key=lambda item: (item.created_at or datetime.min, item.id), reverse=True)


def _promotion_preflight_payload(
    session: Session | None,
    row: AgentEvoPromotion,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    runs = _promotion_preflight_runs(session, row, evidence)
    preflight = evidence.get("global_preflight") if isinstance(evidence, dict) else {}
    reason = str(preflight.get("reason") or "").strip() if isinstance(preflight, dict) else ""
    preflight_status = str(preflight.get("status") or "").strip() if isinstance(preflight, dict) else ""

    if reason == "missing_eval_cases":
        status = "missing_cases"
    elif runs:
        status = "blocked" if any(run.status != "passed" for run in runs) else "passed"
    elif preflight_status in {"blocked", "failed"}:
        status = "blocked"
    elif preflight_status == "passed":
        status = "passed"
    else:
        status = "not_run"

    failed_runs = [run for run in runs if run.status != "passed"]
    return {
        "status": status,
        "label": _PREFLIGHT_LABELS[status],
        "run_count": len(runs),
        "failed_count": len(failed_runs),
        "run_ids": [int(run.id) for run in runs],
        "failed_run_ids": [int(run.id) for run in failed_runs],
        "checked_at": preflight.get("checked_at") if isinstance(preflight, dict) else None,
        "reason": reason,
    }


def _promotion_payload(row: AgentEvoPromotion, session: Session | None = None) -> dict[str, Any]:
    evidence = _parse_evidence(row.evidence)
    return {
        "id": row.id,
        "source_memory_type": row.source_memory_type,
        "source_memory_id": row.source_memory_id,
        "current_scope": row.current_scope,
        "target_scope": row.target_scope,
        "reason": row.reason,
        "evidence": evidence,
        "preflight": _promotion_preflight_payload(session, row, evidence),
        "status": row.status,
        "suggested_at": _dt(row.suggested_at),
        "decided_at": _dt(row.decided_at),
        "decided_by": row.decided_by,
    }


class PromotionDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$", description="approve 或 reject")


class EvalCaseCreateRequest(BaseModel):
    module: str = Field("assistant", pattern="^(assistant|qa)$")
    question: str = Field(..., min_length=1, max_length=4000)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    scope_type: str = Field("global", pattern="^(user|store|global)$")
    scope_id: str = Field("", max_length=64)
    severity: int = Field(2, ge=1, le=3)
    source: str = Field("manual", pattern="^(baseline|manual|document_import|promotion_preflight)$")
    bound_memory_ids: list[Any] = Field(default_factory=list)


class EvalRunRequest(BaseModel):
    case_ids: list[int] = Field(default_factory=list)
    module: str = Field("", pattern="^(|assistant|qa)$")


class MemoryUpdateRequest(BaseModel):
    status: str | None = Field(None, pattern="^(active|pending|archived|auto|auto_disabled|quarantined)$")
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class MemoryRollbackRequest(BaseModel):
    reason: str = Field("", max_length=1000)


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")


class PipelineAdvanceRequest(BaseModel):
    window_hours: int = Field(720, ge=1, le=8760)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(raw: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def _eval_case_payload(row: AgentEvoEvalCase) -> dict[str, Any]:
    return {
        "id": row.id,
        "module": row.module,
        "question": row.question,
        "must_contain": _json_loads(row.must_contain, []),
        "must_not_contain": _json_loads(row.must_not_contain, []),
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "severity": row.severity,
        "source": row.source,
        "bound_memory_ids": _json_loads(row.bound_memory_ids, []),
        "status": row.status,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _eval_case_bound_ref_matches(row: AgentEvoEvalCase, memory_type: str, memory_id: int) -> bool:
    target_type = str(memory_type or "").strip()
    if not target_type:
        return True
    refs = _json_loads(row.bound_memory_ids, [])
    if not isinstance(refs, list):
        return False
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
        if ref_id == int(memory_id) and (not ref_type or ref_type == target_type):
            return True
    return False


def _eval_run_payload(row: AgentEvoEvalRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "case_id": row.case_id,
        "module": row.module,
        "scope_type": row.scope_type,
        "scope_id": row.scope_id,
        "question": row.question,
        "answer_text": row.answer_text,
        "status": row.status,
        "failed_checks": _json_loads(row.failed_checks, []),
        "bound_memory_ids": _json_loads(row.bound_memory_ids, []),
        "triggered_by": row.triggered_by,
        "created_at": _dt(row.created_at),
    }


def _anomaly_payload(row: AgentEvoAnomaly) -> dict[str, Any]:
    return {
        "id": row.id,
        "anomaly_type": row.anomaly_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "severity": row.severity,
        "status": row.status,
        "reason": row.reason,
        "evidence": _json_loads(row.evidence, {}),
        "created_at": _dt(row.created_at),
        "resolved_at": _dt(row.resolved_at),
        "reviewer_id": row.reviewer_id,
    }


def _promotion_scan_summary(created_count: int, diagnostics: dict[str, Any]) -> dict[str, Any]:
    reasons = list(diagnostics.get("reasons") or [])
    if created_count > 0:
        status = "created"
    elif diagnostics.get("status") == "ready":
        status = "no_candidate"
        reasons = reasons or ["threshold_not_met"]
    else:
        status = "not_ready"
    return {
        "status": status,
        "created_count": int(created_count or 0),
        "reasons": reasons,
    }


def _eval_run_summary(rows: list[AgentEvoEvalRun], *, anomaly_count: int) -> dict[str, Any]:
    passed_count = sum(1 for row in rows if row.status == "passed")
    failed_count = sum(1 for row in rows if row.status == "failed")
    error_count = sum(1 for row in rows if row.status == "error")
    return {
        "run_count": len(rows),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "error_count": error_count,
        "anomaly_count": int(anomaly_count or 0),
    }


def _anomaly_scan_summary(created_count: int, diagnostics: dict[str, Any]) -> dict[str, Any]:
    reasons = list(diagnostics.get("reasons") or [])
    if created_count > 0:
        status = "created"
    elif diagnostics.get("status") == "ready":
        status = "clear"
    else:
        status = "not_ready"
    return {
        "status": status,
        "created_count": int(created_count or 0),
        "reasons": reasons,
    }


@router.get("/governance/overview")
def governance_overview(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    allowed_scope_types = _memory_scope_allowlist(current_user, "")
    return success_response(
        get_governance_overview(db, allowed_scope_types=allowed_scope_types),
        workflow_code="evo_governance_overview",
        mock=False,
    )


@router.get("/memories")
def memory_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    memory_type: str = "all",
    scope_type: str = "",
    status: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
    _require_manager(current_user)
    if memory_type not in {"", "all", "semantic", "reflective", "procedural"}:
        raise HTTPException(status_code=400, detail="unsupported memory_type")
    if scope_type not in {"", "user", "store", "global"}:
        raise HTTPException(status_code=400, detail="unsupported scope_type")
    allowed_scope_types = _memory_scope_allowlist(current_user, scope_type)
    data = list_memories(
        db,
        memory_type=memory_type,
        scope_type=scope_type,
        allowed_scope_types=allowed_scope_types,
        status=status,
        query_text=q,
        limit=limit,
        offset=offset,
    )
    return success_response(data, workflow_code="evo_memory_list", mock=False)


@router.get("/feedback-events")
def feedback_event_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    signal: str = "all",
    module: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
    _require_manager(current_user)
    if signal not in {"", "all", "thumb_up", "thumb_down", "correction"}:
        raise HTTPException(status_code=400, detail="unsupported signal")
    if module not in {"", "assistant", "qa", "quick_query"}:
        raise HTTPException(status_code=400, detail="unsupported module")
    data = list_feedback_events(
        db,
        signal=signal,
        module=module,
        query_text=q,
        limit=limit,
        offset=offset,
    )
    return success_response(data, workflow_code="evo_feedback_event_list", mock=False)


@router.get("/memories/{memory_type}/{memory_id}")
def memory_detail(
    memory_type: str,
    memory_id: int,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    try:
        data = get_memory_detail(db, memory_type=memory_type, memory_id=memory_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success_response(data, workflow_code="evo_memory_detail", mock=False)


@router.patch("/memories/{memory_type}/{memory_id}")
def memory_update(
    memory_type: str,
    memory_id: int,
    body: MemoryUpdateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    actor = _user_id(current_user) or str(current_user.get("username") or "manager")
    try:
        data = update_memory(
            db,
            memory_type=memory_type,
            memory_id=memory_id,
            actor=f"user:{actor}",
            status=body.status,
            confidence=body.confidence,
        )
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success_response(data, workflow_code="evo_memory_update", mock=False)


@router.get("/review-queue")
def review_queue_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str = "pending",
    limit: int = 50,
    offset: int = 0,
):
    _require_manager(current_user)
    if status not in {"", "all", "pending", "approved", "rejected"}:
        raise HTTPException(status_code=400, detail="unsupported status")
    allowed_scope_types = _memory_scope_allowlist(current_user, "")
    data = list_review_queue(
        db,
        status=status,
        allowed_scope_types=allowed_scope_types,
        limit=limit,
        offset=offset,
    )
    return success_response(data, workflow_code="evo_review_queue_list", mock=False)


@router.post("/review-queue/{review_id}/decision")
def review_queue_decision(
    review_id: int,
    body: ReviewDecisionRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    review = db.get(AgentEvoReviewQueue, int(review_id))
    if review is None:
        raise HTTPException(status_code=404, detail=f"review item not found: {review_id}")
    if normalize_app_role(str((current_user or {}).get("role") or "")) == "store_manager":
        try:
            target = get_memory_row(db, review.target_type, int(review.target_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if str(getattr(target, "scope_type", "") or "") == "global":
            raise HTTPException(status_code=403, detail="store managers cannot review global memories")
    try:
        data = decide_review_queue_item(
            db,
            review_id=review_id,
            decision=body.decision,
            actor=_user_id(current_user),
        )
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success_response(data, workflow_code="evo_review_queue_decision", mock=False)


@router.post("/memories/{memory_type}/{memory_id}/rollback")
def memory_rollback(
    memory_type: str,
    memory_id: int,
    body: MemoryRollbackRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    actor = _user_id(current_user) or str(current_user.get("username") or "manager")
    try:
        data = rollback_memory(
            db,
            memory_type=memory_type,
            memory_id=memory_id,
            actor=f"user:{actor}",
            reason=body.reason,
        )
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success_response(data, workflow_code="evo_memory_rollback", mock=False)


@router.post("/pipeline/advance")
def pipeline_advance(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: PipelineAdvanceRequest | None = None,
):
    _require_manager(current_user)
    payload = body or PipelineAdvanceRequest()
    data = run_pipeline_advance(db, window_hours=payload.window_hours)
    db.commit()
    return success_response(data, workflow_code="evo_pipeline_advance", mock=False)


@router.post("/episodes/{episode_id}/feedback")
def episode_feedback(
    episode_id: int,
    body: EpisodeFeedbackRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        record = apply_feedback(
            db,
            episode_id=episode_id,
            signal=body.signal,
            actor_user_id=_user_id(current_user),
        )
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success_response(record.model_dump(), workflow_code="evo_feedback", mock=False)


@router.post("/episodes/{episode_id}/correction")
def episode_correction(
    episode_id: int,
    body: EpisodeCorrectionRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        record = apply_correction(
            db,
            episode_id=episode_id,
            correction_text=body.correction_text,
            actor_user_id=_user_id(current_user),
        )
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success_response(record.model_dump(), workflow_code="evo_correction", mock=False)


@router.get("/promotions")
def promotion_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str = "pending",
    limit: int = 50,
):
    _require_manager(current_user)
    rows = list_promotions(db, status=status, limit=limit)
    return success_response(
        {"promotions": [_promotion_payload(row, db) for row in rows]},
        workflow_code="evo_promotions",
        mock=False,
    )


@router.post("/promotions/scan")
def promotion_scan(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    diagnostics = promotion_scan_diagnostics(db)
    rows = run_promotion_scan(db)
    db.commit()
    created_count = len(rows)
    return success_response(
        {
            "created_count": created_count,
            "promotions": [_promotion_payload(row, db) for row in rows],
            "diagnostics": diagnostics,
            "summary": _promotion_scan_summary(created_count, diagnostics),
        },
        workflow_code="evo_promotion_scan",
        mock=False,
    )


@router.post("/promotions/{promotion_id}/decision")
def promotion_decision(
    promotion_id: int,
    body: PromotionDecisionRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    actor = _user_id(current_user) or str(current_user.get("username") or "")
    try:
        if body.decision == "approve":
            row, target = approve_promotion(db, promotion_id, decided_by=actor)
        else:
            row = reject_promotion(db, promotion_id, decided_by=actor)
            target = None
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = {"promotion": _promotion_payload(row, db)}
    if target is not None:
        data["target"] = {"memory_type": "procedural", "memory_id": target.id}
    return success_response(data, workflow_code="evo_promotion_decision", mock=False)


@router.get("/eval-cases")
def eval_case_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str = "active",
    module: str = "",
    severity: int = 0,
    source: str = "",
    bound_memory_type: str = "",
    bound_memory_id: int = 0,
    limit: int = 100,
):
    _require_manager(current_user)
    query = db.query(AgentEvoEvalCase)
    normalized_status = str(status or "").strip()
    if normalized_status:
        query = query.filter(AgentEvoEvalCase.status == normalized_status)
    normalized_module = str(module or "").strip()
    if normalized_module:
        if normalized_module not in {"assistant", "qa"}:
            raise HTTPException(status_code=400, detail="unsupported module")
        query = query.filter(AgentEvoEvalCase.module == normalized_module)
    normalized_source = str(source or "").strip()
    if normalized_source:
        if normalized_source not in {"baseline", "manual", "document_import", "promotion_preflight"}:
            raise HTTPException(status_code=400, detail="unsupported source")
        query = query.filter(AgentEvoEvalCase.source == normalized_source)
    normalized_severity = int(severity or 0)
    if normalized_severity:
        if normalized_severity not in {1, 2, 3}:
            raise HTTPException(status_code=400, detail="unsupported severity")
        query = query.filter(AgentEvoEvalCase.severity == normalized_severity)
    bounded_rows = query.order_by(AgentEvoEvalCase.id.asc()).all()
    normalized_bound_type = str(bound_memory_type or "").strip()
    normalized_bound_id = int(bound_memory_id or 0)
    if normalized_bound_type or normalized_bound_id:
        if normalized_bound_type not in {"semantic", "reflective", "procedural"} or normalized_bound_id <= 0:
            raise HTTPException(status_code=400, detail="unsupported bound memory filter")
        bounded_rows = [
            row
            for row in bounded_rows
            if _eval_case_bound_ref_matches(row, normalized_bound_type, normalized_bound_id)
        ]
    max_limit = max(1, min(500, int(limit or 100)))
    rows = bounded_rows[:max_limit]
    return success_response(
        {"eval_cases": [_eval_case_payload(row) for row in rows]},
        workflow_code="evo_eval_cases",
        mock=False,
    )


@router.post("/eval-cases")
def eval_case_create(
    body: EvalCaseCreateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    row = AgentEvoEvalCase(
        module=body.module,
        question=body.question.strip(),
        must_contain=_json_dumps(body.must_contain),
        must_not_contain=_json_dumps(body.must_not_contain),
        scope_type=body.scope_type,
        scope_id=(body.scope_id or "").strip(),
        severity=body.severity,
        source=body.source,
        bound_memory_ids=_json_dumps(body.bound_memory_ids),
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return success_response({"eval_case": _eval_case_payload(row)}, workflow_code="evo_eval_case_create", mock=False)


@router.post("/eval-cases/seed-defaults")
def eval_case_seed_defaults(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    rows = seed_default_eval_cases(db)
    db.commit()
    return success_response(
        {"created_count": len(rows), "eval_cases": [_eval_case_payload(row) for row in rows]},
        workflow_code="evo_eval_case_seed_defaults",
        mock=False,
    )


@router.post("/eval-runs")
def eval_run_create(
    body: EvalRunRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    actor = _user_id(current_user) or str(current_user.get("username") or "manager")
    before_anomaly_count = db.query(AgentEvoAnomaly).count()
    rows = run_eval_cases(
        db,
        case_ids=body.case_ids or None,
        module=(body.module or None),
        triggered_by=f"user:{actor}",
    )
    anomaly_count = max(0, db.query(AgentEvoAnomaly).count() - before_anomaly_count)
    db.commit()
    summary = _eval_run_summary(rows, anomaly_count=anomaly_count)
    return success_response(
        {
            "run_count": len(rows),
            "failed_count": sum(1 for row in rows if row.status != "passed"),
            "summary": summary,
            "eval_runs": [_eval_run_payload(row) for row in rows],
        },
        workflow_code="evo_eval_run",
        mock=False,
    )


@router.get("/eval-runs")
def eval_run_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str = "",
    limit: int = 100,
):
    _require_manager(current_user)
    query = db.query(AgentEvoEvalRun)
    normalized_status = str(status or "").strip()
    if normalized_status:
        query = query.filter(AgentEvoEvalRun.status == normalized_status)
    rows = (
        query.order_by(AgentEvoEvalRun.created_at.desc(), AgentEvoEvalRun.id.desc())
        .limit(max(1, min(500, int(limit or 100))))
        .all()
    )
    return success_response(
        {"eval_runs": [_eval_run_payload(row) for row in rows]},
        workflow_code="evo_eval_runs",
        mock=False,
    )


@router.post("/anomalies/scan")
def anomaly_scan(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_manager(current_user)
    diagnostics = anomaly_scan_diagnostics(db)
    rows = run_anomaly_scan(db)
    db.commit()
    created_count = len(rows)
    return success_response(
        {
            "created_count": created_count,
            "anomalies": [_anomaly_payload(row) for row in rows],
            "diagnostics": diagnostics,
            "summary": _anomaly_scan_summary(created_count, diagnostics),
        },
        workflow_code="evo_anomaly_scan",
        mock=False,
    )


@router.get("/anomalies")
def anomaly_list(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str = "open",
    limit: int = 100,
):
    _require_manager(current_user)
    query = db.query(AgentEvoAnomaly)
    normalized_status = str(status or "").strip()
    if normalized_status:
        query = query.filter(AgentEvoAnomaly.status == normalized_status)
    rows = (
        query.order_by(AgentEvoAnomaly.created_at.desc(), AgentEvoAnomaly.id.desc())
        .limit(max(1, min(500, int(limit or 100))))
        .all()
    )
    return success_response(
        {"anomalies": [_anomaly_payload(row) for row in rows]},
        workflow_code="evo_anomalies",
        mock=False,
    )
