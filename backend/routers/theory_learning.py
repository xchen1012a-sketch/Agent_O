from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import config as app_config
from api_response import error_response, success_response
from auth import get_current_user, get_optional_user, normalize_app_role, require_roles
from audit import log_audit_from_user
from db_stage3 import get_conn, now_iso
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from typing_extensions import Annotated

router = APIRouter(prefix="/api/theory-learning", tags=["theory-learning"])

_WORKFLOW_CODE = "theory_learning"
THEORY_DOCS_DIR = Path(__file__).resolve().parent.parent / "theory_learning_docs"
_ALLOWED_EXTENSIONS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
}


def _is_admin(current_user: dict[str, Any]) -> bool:
    return normalize_app_role(str(current_user.get("role") or "")) == "admin"


def _parse_publish_flag(raw_value: str) -> bool:
    return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on"}


def _document_storage_path(stored_file_name: str) -> Path:
    return THEORY_DOCS_DIR / stored_file_name


def _serialize_document(row: Any) -> dict[str, Any]:
    document_id = int(row["id"])
    return {
        "id": document_id,
        "title": row["title"],
        "category": row["category"],
        "summary": row["summary"],
        "file_name": row["original_file_name"],
        "content_type": row["content_type"],
        "file_size": int(row["file_size"] or 0),
        "uploaded_by": row["uploaded_by"],
        "is_published": bool(row["is_published"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "file_url": f"/api/theory-learning/documents/{document_id}/file",
    }


def _build_list_query(*, include_unpublished: bool, q: str, category: str) -> tuple[str, list[Any]]:
    where_parts = ["1=1"]
    params: list[Any] = []
    if not include_unpublished:
        where_parts.append("is_published = 1")
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        where_parts.append("(title LIKE ? OR category LIKE ?)")
        params.extend([like, like])
    category_value = (category or "").strip()
    if category_value:
        where_parts.append("category = ?")
        params.append(category_value)
    where_sql = " AND ".join(where_parts)
    return (
        f"""
        SELECT *
        FROM theory_learning_documents
        WHERE {where_sql}
        ORDER BY created_at DESC, id DESC
        """,
        params,
    )


def _get_document_row(*, document_id: int, current_user: dict[str, Any]):
    include_unpublished = _is_admin(current_user)
    with get_conn() as conn:
        if include_unpublished:
            row = conn.execute(
                "SELECT * FROM theory_learning_documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM theory_learning_documents WHERE id = ? AND is_published = 1",
                (document_id,),
            ).fetchone()
    return row


@router.post("/admin/documents")
async def upload_theory_document(
    title: str = Form(...),
    category: str = Form(""),
    summary: str = Form(""),
    is_published: str = Form("0"),
    file: UploadFile = File(...),
    current_user: Annotated[dict[str, Any], Depends(require_roles(["admin"]))] = None,
) -> dict[str, Any]:
    title_value = (title or "").strip()
    category_value = (category or "").strip()
    summary_value = (summary or "").strip()
    filename = (file.filename or "").strip()
    suffix = Path(filename).suffix.lower()
    if not title_value:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="标题不能为空",
            data={},
            http_status=400,
        )
    if not category_value:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="分类不能为空",
            data={},
            http_status=400,
        )
    if suffix not in _ALLOWED_EXTENSIONS:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="仅支持 PDF、Markdown、TXT 文件",
            data={},
            http_status=400,
        )

    max_bytes = app_config.KB_UPLOAD_MAX_MB * 1024 * 1024
    content_length = file.headers.get("content-length")
    try:
        if content_length and int(content_length) > max_bytes:
            return error_response(
                workflow_code=_WORKFLOW_CODE,
                message=f"文件超过 {app_config.KB_UPLOAD_MAX_MB}MB 限制",
                data={},
                http_status=400,
            )
    except ValueError:
        pass

    file_bytes = await file.read()
    if not file_bytes:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="文件为空",
            data={},
            http_status=400,
        )
    if len(file_bytes) > max_bytes:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message=f"文件超过 {app_config.KB_UPLOAD_MAX_MB}MB 限制",
            data={},
            http_status=400,
        )

    THEORY_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stored_file_name = f"{uuid4().hex}{suffix}"
    stored_path = _document_storage_path(stored_file_name)
    stored_path.write_bytes(file_bytes)

    content_type = _ALLOWED_EXTENSIONS[suffix]
    now = now_iso()
    actor_id = str((current_user or {}).get("user_id") or "")
    published_flag = 1 if _parse_publish_flag(is_published) else 0

    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO theory_learning_documents (
                title,
                category,
                summary,
                original_file_name,
                stored_file_name,
                content_type,
                file_size,
                uploaded_by,
                is_published,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title_value,
                category_value,
                summary_value,
                filename,
                stored_file_name,
                content_type,
                len(file_bytes),
                actor_id,
                published_flag,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM theory_learning_documents WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        if current_user:
            log_audit_from_user(conn, current_user, action="theory_document_upload", target_type="theory_document",
                                target_id=str(cursor.lastrowid), target_name=title_value)

    return success_response(
        {"document": _serialize_document(row)},
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.patch("/admin/documents/{document_id}/publish")
def publish_document(
    document_id: int,
    current_user: Annotated[dict[str, Any], Depends(require_roles(["admin"]))] = None,
) -> dict[str, Any]:
    now = now_iso()
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE theory_learning_documents SET is_published = 1, updated_at = ? WHERE id = ?",
            (now, document_id),
        )
        if cursor.rowcount == 0:
            return error_response(workflow_code=_WORKFLOW_CODE, message="文档不存在", data={}, http_status=404)
        row = conn.execute(
            "SELECT * FROM theory_learning_documents WHERE id = ?", (document_id,)
        ).fetchone()
        if current_user:
            log_audit_from_user(conn, current_user, action="theory_document_publish", target_type="theory_document",
                                target_id=str(document_id), target_name=row["title"] if row else "")
    return success_response({"document": _serialize_document(row)}, workflow_code=_WORKFLOW_CODE, mock=False)


@router.patch("/admin/documents/{document_id}/unpublish")
def unpublish_document(
    document_id: int,
    current_user: Annotated[dict[str, Any], Depends(require_roles(["admin"]))] = None,
) -> dict[str, Any]:
    now = now_iso()
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE theory_learning_documents SET is_published = 0, updated_at = ? WHERE id = ?",
            (now, document_id),
        )
        if cursor.rowcount == 0:
            return error_response(workflow_code=_WORKFLOW_CODE, message="文档不存在", data={}, http_status=404)
        row = conn.execute(
            "SELECT * FROM theory_learning_documents WHERE id = ?", (document_id,)
        ).fetchone()
    return success_response({"document": _serialize_document(row)}, workflow_code=_WORKFLOW_CODE, mock=False)


@router.delete("/admin/documents/{document_id}")
def delete_document(
    document_id: int,
    current_user: Annotated[dict[str, Any], Depends(require_roles(["admin"]))] = None,
) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM theory_learning_documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None:
            return error_response(workflow_code=_WORKFLOW_CODE, message="文档不存在", data={}, http_status=404)
        stored = str(row["stored_file_name"] or "")
        conn.execute("DELETE FROM theory_learning_documents WHERE id = ?", (document_id,))
        if current_user:
            log_audit_from_user(conn, current_user, action="theory_document_delete", target_type="theory_document",
                                target_id=str(document_id), target_name=row["title"] if row else "")
    if stored:
        path = _document_storage_path(stored)
        if path.is_file():
            path.unlink(missing_ok=True)
    return success_response({}, workflow_code=_WORKFLOW_CODE, mock=False)


@router.get("/admin/documents")
def list_admin_documents(
    q: str = "",
    category: str = "",
    _current_user: Annotated[dict[str, Any], Depends(require_roles(["admin"]))] = None,
) -> dict[str, Any]:
    sql, params = _build_list_query(include_unpublished=True, q=q, category=category)
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    items = [_serialize_document(row) for row in rows]
    return success_response(
        {"items": items, "total": len(items)},
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/documents")
def list_published_documents(
    q: str = "",
    category: str = "",
    current_user: Annotated[dict[str, Any], Depends(get_current_user)] = None,
) -> dict[str, Any]:
    sql, params = _build_list_query(
        include_unpublished=_is_admin(current_user or {}),
        q=q,
        category=category,
    )
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    items = [_serialize_document(row) for row in rows]
    return success_response(
        {"items": items, "total": len(items)},
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/documents/{document_id}")
def get_document_detail(
    document_id: int,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    row = _get_document_row(document_id=document_id, current_user=current_user)
    if row is None:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="文档不存在",
            data={},
            http_status=404,
        )
    return success_response(
        {"document": _serialize_document(row)},
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: int,
    token: str = "",
    current_user: Annotated[dict[str, Any] | None, Depends(get_optional_user)] = None,
):
    if current_user is None and token:
        from auth import decode_token_optional
        current_user = decode_token_optional(token)
    if current_user is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"code": 401, "message": "请先登录后查看文档", "data": {}})
    row = _get_document_row(document_id=document_id, current_user=current_user)
    if row is None:
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="文档不存在",
            data={},
            http_status=404,
        )

    file_path = _document_storage_path(str(row["stored_file_name"]))
    if not file_path.is_file():
        return error_response(
            workflow_code=_WORKFLOW_CODE,
            message="文档文件不存在",
            data={},
            http_status=404,
        )

    response = FileResponse(
        path=file_path,
        media_type=str(row["content_type"] or "application/octet-stream"),
    )
    response.headers["Content-Disposition"] = "inline"
    return response
