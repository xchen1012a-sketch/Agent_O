"""Audit log: structured records of who did what in the system."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from database import utc_now_iso

_log = logging.getLogger("jewelry_qipei.audit")

# ── Table DDL ──

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL DEFAULT '',
    user_name   TEXT    NOT NULL DEFAULT '',
    user_role   TEXT    NOT NULL DEFAULT '',
    action      TEXT    NOT NULL,
    target_type TEXT    NOT NULL DEFAULT '',
    target_id   TEXT    NOT NULL DEFAULT '',
    target_name TEXT    NOT NULL DEFAULT '',
    detail      TEXT    NOT NULL DEFAULT '',
    ip          TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
"""

_AUDIT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_target_type ON audit_logs(target_type)",
]


def ensure_audit_logs_table(conn: sqlite3.Connection) -> None:
    conn.execute(_AUDIT_DDL)
    for idx_sql in _AUDIT_INDEXES:
        conn.execute(idx_sql)


# ── Write helper ──

def log_audit(
    conn: sqlite3.Connection,
    *,
    user_id: str = "",
    user_name: str = "",
    user_role: str = "",
    action: str,
    target_type: str = "",
    target_id: str = "",
    target_name: str = "",
    detail: dict[str, Any] | str | None = None,
    ip: str = "",
) -> None:
    """Insert one audit log row.  Safe to call inside an existing transaction."""
    detail_text = (
        json.dumps(detail, ensure_ascii=False)
        if isinstance(detail, dict)
        else (detail or "")
    )
    try:
        conn.execute(
            """
            INSERT INTO audit_logs
                (user_id, user_name, user_role, action,
                 target_type, target_id, target_name, detail,
                 ip, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(user_id),
                user_name,
                user_role,
                action,
                target_type,
                str(target_id),
                target_name,
                detail_text,
                ip,
                utc_now_iso(),
            ),
        )
    except Exception:
        _log.exception("audit write failed action=%s target=%s/%s", action, target_type, target_id)


def log_audit_from_user(
    conn: sqlite3.Connection,
    current_user: dict[str, Any],
    *,
    action: str,
    target_type: str = "",
    target_id: str = "",
    target_name: str = "",
    detail: dict[str, Any] | str | None = None,
    ip: str = "",
) -> None:
    """Convenience wrapper that extracts user info from a JWT user dict."""
    log_audit(
        conn,
        user_id=str(current_user.get("user_id", "")),
        user_name=current_user.get("display_name") or current_user.get("username", ""),
        user_role=current_user.get("role", ""),
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        detail=detail,
        ip=ip,
    )


# ── Query helpers ──

VALID_ACTIONS = {
    "login", "logout",
    "user_create", "user_update", "user_delete",
    "store_create", "store_update", "store_delete",
    "role_setting_create", "role_setting_update", "role_setting_delete",
    "exam_create", "exam_publish", "exam_archive", "exam_delete", "exam_retake",
    "knowledge_dataset_create", "knowledge_dataset_delete",
    "knowledge_document_upload", "knowledge_document_delete",
    "theory_document_upload", "theory_document_publish", "theory_document_delete",
    "env_upload",
}

VALID_TARGET_TYPES = {
    "user", "store", "role_setting",
    "exam_task", "exam_paper",
    "knowledge_dataset", "knowledge_document",
    "theory_document",
    "env",
}


def query_audit_logs(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = 20,
    user_keyword: str = "",
    action: str = "",
    target_type: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    """Paginated audit log query with filters."""
    where_clauses: list[str] = []
    params: list[Any] = []

    if user_keyword:
        like = f"%{user_keyword}%"
        where_clauses.append("(user_name LIKE ? OR user_id LIKE ?)")
        params.extend([like, like])

    if action:
        where_clauses.append("action = ?")
        params.append(action)

    if target_type:
        where_clauses.append("target_type = ?")
        params.append(target_type)

    if date_from:
        where_clauses.append("created_at >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("created_at <= ?")
        params.append(date_to + "T23:59:59")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM audit_logs {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT id, user_id, user_name, user_role, action,
               target_type, target_id, target_name, detail, ip, created_at
        FROM audit_logs {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()

    items = [dict(r) for r in rows]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }
