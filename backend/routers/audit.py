"""Audit logs query API (admin-only)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from api_response import success_response
from audit import query_audit_logs, VALID_ACTIONS, VALID_TARGET_TYPES
from auth import require_roles
from db_stage3 import get_conn
from fastapi import Request

router = APIRouter(prefix="/api/admin/audit-logs", tags=["audit"], dependencies=[Depends(require_roles(["admin"]))])


@router.get("")
def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_keyword: str = Query(""),
    action: str = Query(""),
    target_type: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
):
    with get_conn() as conn:
        result = query_audit_logs(
            conn,
            page=page,
            page_size=page_size,
            user_keyword=user_keyword,
            action=action,
            target_type=target_type,
            date_from=date_from,
            date_to=date_to,
        )
    return success_response(workflow_code="audit", data=result)


@router.get("/filters")
def get_audit_filter_options():
    """Return available action and target_type options for the filter UI."""
    return success_response(workflow_code="audit", data={
        "actions": sorted(VALID_ACTIONS),
        "target_types": sorted(VALID_TARGET_TYPES),
    })
