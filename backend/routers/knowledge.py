"""知识库管理 API（仅管理员）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from typing_extensions import Annotated
from pydantic import BaseModel, Field

import config as app_config
from api_response import success_response, error_response
from auth import require_roles, get_current_user
from audit import log_audit_from_user
from dify_kb_client import (
    kb_create_dataset,
    kb_create_text_document,
    kb_delete_dataset,
    kb_delete_document,
    kb_get_workflow_dataset_mapping,
    kb_hit_test,
    kb_list_datasets,
    kb_list_documents,
    kb_list_segments,
    kb_upload_document,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(require_roles(["admin"]))])

_WC = "knowledge"


def _audit_knowledge(current_user: dict | None, action: str, target_type: str, target_id: str, target_name: str = "") -> None:
    """Fire-and-forget audit for knowledge mutations."""
    if not current_user:
        return
    try:
        from db_stage3 import get_conn as _get_conn
        with _get_conn() as conn:
            log_audit_from_user(conn, current_user, action=action, target_type=target_type,
                                target_id=target_id, target_name=target_name)
    except Exception:
        pass


def _kb_error(result: dict, message: str, http_status: int = 502) -> dict[str, Any]:
    """Standard error response for knowledge base operations."""
    return error_response(
        workflow_code=_WC,
        message=message,
        data={"error": result.get("error", "")},
        http_status=http_status,
    )


class CreateDatasetRequest(BaseModel):
    name: str = Field(..., min_length=1, description="知识库名称")
    description: str = Field("", description="知识库描述")


class CreateTextDocumentRequest(BaseModel):
    name: str = Field(..., min_length=1, description="文档名称")
    text: str = Field(..., min_length=1, description="文档内容")


class HitTestRequest(BaseModel):
    query: str = Field(..., min_length=1, description="查询文本")
    retrieval_model: dict[str, Any] | None = Field(None, description="检索模型配置")


@router.get("/datasets")
async def list_datasets(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """列出所有知识库。"""
    result = kb_list_datasets(page=page, limit=limit)
    if not result.get("ok"):
        return _kb_error(result, "知识库列表获取失败")
    return success_response(
        {
            "items": result.get("data", []),
            "total": result.get("total", 0),
            "has_more": result.get("has_more", False),
        },
        workflow_code=_WC,
        mock=False,
    )


@router.post("/datasets")
async def create_dataset(body: CreateDatasetRequest, current_user: Annotated[dict, Depends(get_current_user)]) -> dict[str, Any]:
    """创建知识库。"""
    result = kb_create_dataset(body.name, body.description)
    if not result.get("ok"):
        return _kb_error(result, "知识库创建失败")
    _audit_knowledge(current_user, "knowledge_dataset_create", "knowledge_dataset", result.get("dataset", {}).get("id", ""), body.name)
    return success_response(
        {"dataset": result.get("dataset", {})},
        workflow_code=_WC,
        mock=False,
    )


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str, current_user: Annotated[dict, Depends(get_current_user)]) -> dict[str, Any]:
    """删除知识库。"""
    result = kb_delete_dataset(dataset_id)
    if not result.get("ok"):
        return _kb_error(result, "知识库删除失败")
    _audit_knowledge(current_user, "knowledge_dataset_delete", "knowledge_dataset", dataset_id)
    return success_response({}, workflow_code=_WC, mock=False)


@router.post("/datasets/{dataset_id}/documents")
async def upload_document(
    dataset_id: str,
    file: UploadFile = File(...),
    current_user: Annotated[dict, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    """上传文档到指定知识库。"""
    max_bytes = app_config.KB_UPLOAD_MAX_MB * 1024 * 1024
    # Check Content-Length header first to avoid reading oversized files into memory
    content_length = file.headers.get("content-length")
    try:
        if content_length and int(content_length) > max_bytes:
            return _kb_error({"error": ""}, f"文件超过 {app_config.KB_UPLOAD_MAX_MB}MB 限制", http_status=400)
    except ValueError:
        pass
    file_bytes = await file.read()
    if len(file_bytes) > max_bytes:
        return _kb_error({"error": ""}, f"文件超过 {app_config.KB_UPLOAD_MAX_MB}MB 限制", http_status=400)
    if not file_bytes:
        return _kb_error({"error": ""}, "文件为空", http_status=400)

    filename = file.filename or "upload.txt"
    result = kb_upload_document(dataset_id, file_bytes, filename)

    if not result.get("ok"):
        return _kb_error(result, "文档上传失败")

    _audit_knowledge(current_user, "knowledge_document_upload", "knowledge_document",
                     result.get("document", {}).get("id", ""), filename)

    return success_response(
        {"document": result.get("document", {}), "batch": result.get("batch", "")},
        workflow_code=_WC,
        mock=False,
    )


@router.post("/datasets/{dataset_id}/documents/text")
async def create_text_document(
    dataset_id: str,
    body: CreateTextDocumentRequest,
) -> dict[str, Any]:
    """通过文本内容创建文档。"""
    result = kb_create_text_document(dataset_id, body.name, body.text)
    if not result.get("ok"):
        return _kb_error(result, "文本文档创建失败")
    return success_response(
        {"document": result.get("document", {}), "batch": result.get("batch", "")},
        workflow_code=_WC,
        mock=False,
    )


@router.get("/datasets/{dataset_id}/documents")
async def list_documents(
    dataset_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """列出指定知识库下的文档。"""
    result = kb_list_documents(dataset_id, page=page, limit=limit)
    if not result.get("ok"):
        return _kb_error(result, "文档列表获取失败")

    return success_response(
        {
            "items": result.get("data", []),
            "total": result.get("total", 0),
            "has_more": result.get("has_more", False),
        },
        workflow_code=_WC,
        mock=False,
    )


@router.get("/datasets/{dataset_id}/documents/{document_id}/segments")
async def list_segments(
    dataset_id: str,
    document_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """查看文档的分段列表。"""
    result = kb_list_segments(dataset_id, document_id, page=page, limit=limit)
    if not result.get("ok"):
        return _kb_error(result, "分段获取失败")
    return success_response(
        {"items": result.get("data", []), "total": result.get("total", 0)},
        workflow_code=_WC,
        mock=False,
    )


@router.delete("/datasets/{dataset_id}/documents/{document_id}")
async def delete_document(
    dataset_id: str,
    document_id: str,
    current_user: Annotated[dict, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    """删除指定知识库下的文档。"""
    result = kb_delete_document(dataset_id, document_id)
    if not result.get("ok"):
        return _kb_error(result, "文档删除失败")
    _audit_knowledge(current_user, "knowledge_document_delete", "knowledge_document", document_id)
    return success_response({}, workflow_code=_WC, mock=False)


@router.post("/datasets/{dataset_id}/hit-test")
async def hit_test(
    dataset_id: str,
    body: HitTestRequest,
) -> dict[str, Any]:
    """知识库检索测试。"""
    result = kb_hit_test(dataset_id, body.query, body.retrieval_model)
    if not result.get("ok"):
        return _kb_error(result, "检索测试失败")
    return success_response(
        {"query": result.get("query", ""), "results": result.get("results", [])},
        workflow_code=_WC,
        mock=False,
    )


@router.get("/workflow-dataset-mapping")
async def get_workflow_dataset_mapping() -> dict[str, Any]:
    """返回工作流与知识库的关联配置。"""
    return success_response(
        {"mapping": kb_get_workflow_dataset_mapping()},
        workflow_code=_WC,
        mock=False,
    )
