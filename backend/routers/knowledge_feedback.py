"""AI 教练建议路由（B1+B2）。

GET  /api/knowledge-feedback/clusters   - 高频问题聚类（店长/管理员）
POST /api/knowledge-feedback/dispatch   - 一键派发到导购（店长/管理员）
GET  /api/knowledge-feedback/my-tasks   - 导购查看自己的派发任务（所有角色）
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from api_response import success_response
from auth import get_current_user, require_roles
from knowledge_feedback_service import (
    DispatchInput,
    cluster_high_frequency_questions,
    dispatch_cluster_to_targets,
    list_dispatched_tasks_for_user,
)

_WC = "knowledge_feedback"

router = APIRouter(prefix="/api/knowledge-feedback", tags=["knowledge-feedback"])


@contextmanager
def get_conn():
    """数据库连接。生产环境走 db_stage3，测试通过 monkey-patch 替换。"""
    from db_stage3 import get_conn as _gc

    with _gc() as conn:
        yield conn


class DispatchRequest(BaseModel):
    cluster_signature: str = ""
    representative_question: str = Field(..., min_length=1)
    primary_tag: str = ""
    top_keywords: list[str] = Field(default_factory=list)
    cluster_count: int = 0
    target_user_ids: list[str] = Field(..., min_length=1)
    target_role: str = ""
    note: str = ""


@router.get("/clusters")
async def get_clusters(
    store_id: str | None = Query(None),
    top_n: int = Query(5, ge=1, le=20),
    current_user: Annotated[dict, Depends(require_roles(["store_manager", "admin"]))] = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        role = (current_user.get("role", "") or "").lower()
        effective_store = store_id
        if not effective_store and role == "store_manager":
            effective_store = current_user.get("store_id", "") or None
        clusters = cluster_high_frequency_questions(
            conn,
            store_id=effective_store or None,
            top_n=top_n,
        )
    return success_response(
        {"clusters": clusters, "store_id": effective_store or ""},
        workflow_code=_WC,
        mock=False,
    )


@router.post("/dispatch")
async def post_dispatch(
    body: DispatchRequest,
    current_user: Annotated[dict, Depends(require_roles(["store_manager", "admin"]))] = None,
) -> dict[str, Any]:
    payload = DispatchInput(
        cluster_signature=body.cluster_signature or body.representative_question,
        representative_question=body.representative_question,
        primary_tag=body.primary_tag,
        top_keywords=body.top_keywords,
        cluster_count=body.cluster_count,
        store_id=current_user.get("store_id", ""),
        dispatched_by_user_id=current_user.get("user_id", ""),
        target_user_ids=body.target_user_ids,
        target_role=body.target_role,
        note=body.note,
    )
    with get_conn() as conn:
        result = dispatch_cluster_to_targets(conn, payload=payload)
    return success_response(result, workflow_code=_WC, mock=False)


@router.get("/my-tasks")
async def get_my_tasks(
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    with get_conn() as conn:
        tasks = list_dispatched_tasks_for_user(
            conn,
            user_id=current_user["user_id"],
        )
    return success_response({"tasks": tasks}, workflow_code=_WC, mock=False)
