"""复盘本路由（D2）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String as SAString, cast, or_
from sqlalchemy.orm import Session

from api_response import success_response
from auth import get_current_user, require_roles
from database import get_db
from learning_taxonomy import DIMENSIONS_6
from models import User
from review_notebook_mastery import get_manual_masteries, mark_as_mastered, unmark_as_mastered
from review_notebook_service import DEFAULT_RECORD_LIMIT, DEFAULT_RETURN_LIMIT, SOURCES, aggregate_review_notebook, summary_only

_WC = "review_notebook"

router = APIRouter(prefix="/api/wrong-questions", tags=["review-notebook"])


def _normalize_source(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip().lower()
    return text if text in SOURCES else ""


def _normalize_dimension(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    return text if text in DIMENSIONS_6 else ""


@router.get("/my-list")
async def get_my_review_notebook(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    source: str | None = Query(None, description="筛选来源 assessment|practice|assistant"),
    dimension: str | None = Query(None, description="筛选能力维度"),
    record_limit: int = Query(DEFAULT_RECORD_LIMIT, ge=1, le=200),
    return_limit: int = Query(DEFAULT_RETURN_LIMIT, ge=1, le=500),
) -> dict[str, Any]:
    payload = aggregate_review_notebook(
        db,
        user_id=str(current_user.get("user_id") or ""),
        source=_normalize_source(source),
        dimension=_normalize_dimension(dimension),
        record_limit=record_limit,
        return_limit=return_limit,
    )
    return success_response(payload, workflow_code=_WC, mock=False)


@router.get("/summary")
async def get_my_review_summary(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    record_limit: int = Query(DEFAULT_RECORD_LIMIT, ge=1, le=200),
) -> dict[str, Any]:
    payload = summary_only(db, user_id=str(current_user.get("user_id") or ""), record_limit=record_limit)
    return success_response(payload, workflow_code=_WC, mock=False)


def _resolve_target_user(db: Session, target_user_id: str) -> User | None:
    text = str(target_user_id or "").strip()
    if not text:
        return None
    return db.query(User).filter(or_(User.user_id == text, cast(User.id, SAString) == text)).first()


def _enforce_store_scope(actor: dict, target: User) -> None:
    role = str(actor.get("role") or "").lower()
    if role == "admin":
        return
    if role != "store_manager":
        raise HTTPException(status_code=403, detail="forbidden")
    actor_store = str(actor.get("store_id") or "").strip()
    target_store = str(target.store_id or "").strip()
    if not actor_store or actor_store != target_store:
        raise HTTPException(status_code=403, detail="cross_store_access_denied")


def _parse_required_int(value: Any, error: str, message: str) -> int | dict[str, Any]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return {"success": False, "error": error, "message": message}
    if parsed < 1:
        return {"success": False, "error": error, "message": message}
    return parsed


@router.get("/by-user/{target_user_id}")
async def get_user_review_notebook(
    target_user_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(require_roles(["store_manager", "admin"]))] = None,
    source: str | None = Query(None),
    dimension: str | None = Query(None),
    record_limit: int = Query(DEFAULT_RECORD_LIMIT, ge=1, le=200),
    return_limit: int = Query(DEFAULT_RETURN_LIMIT, ge=1, le=500),
) -> dict[str, Any]:
    target = _resolve_target_user(db, target_user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    _enforce_store_scope(current_user or {}, target)
    payload = aggregate_review_notebook(
        db,
        user_id=str(target.id),
        source=_normalize_source(source),
        dimension=_normalize_dimension(dimension),
        record_limit=record_limit,
        return_limit=return_limit,
    )
    return success_response(payload, workflow_code=_WC, mock=False)


@router.post("/mark-mastered")
async def mark_review_item_mastered(
    body: dict[str, Any],
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    source = str(body.get("source") or "").strip()
    if source not in SOURCES:
        return {"success": False, "error": "invalid_source", "message": f"来源必须是 {SOURCES} 之一"}

    source_record_id = _parse_required_int(
        body.get("source_record_id"),
        "invalid_source_record_id",
        "source_record_id 必须是正整数",
    )
    if isinstance(source_record_id, dict):
        return source_record_id

    remark = str(body.get("remark") or "").strip()
    if len(remark) < 2:
        return {"success": False, "error": "remark_too_short", "message": "留痕文字至少需要 2 个字"}

    user_id = str(current_user.get("user_id") or "")
    result = mark_as_mastered(
        db,
        user_id=user_id,
        source=source,
        source_record_id=source_record_id,
        question_id=str(body.get("question_id") or "").strip(),
        dimension=str(body.get("dimension") or "").strip(),
        knowledge_tag=str(body.get("knowledge_tag") or "").strip(),
        title=str(body.get("title") or "").strip(),
        remark=remark,
        marked_by=user_id,
    )
    return success_response(result, workflow_code=_WC, mock=False)


@router.post("/unmark-mastered")
async def unmark_review_item_mastered(
    body: dict[str, Any],
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    mastery_id = _parse_required_int(body.get("mastery_id"), "invalid_mastery_id", "mastery_id 必须是正整数")
    if isinstance(mastery_id, dict):
        return mastery_id

    user_id = str(current_user.get("user_id") or "")
    ok = unmark_as_mastered(db, user_id, mastery_id)
    if not ok:
        return {"success": False, "error": "not_found", "message": "未找到对应的已掌握记录"}
    return success_response({"status": "unmarked"}, workflow_code=_WC, mock=False)


@router.get("/mastered")
async def get_mastered_items(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    source: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    user_id = str(current_user.get("user_id") or "")
    items = get_manual_masteries(db, user_id, source=_normalize_source(source), limit=limit)
    return success_response({"user_id": user_id, "items": items}, workflow_code=_WC, mock=False)
