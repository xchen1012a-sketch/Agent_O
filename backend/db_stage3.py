from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from database import SQLITE_DB_PATH, ensure_database_initialized, utc_now_iso


def now_iso() -> str:
    return utc_now_iso()


def json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def ensure_stage3_tables(conn: sqlite3.Connection | None = None) -> None:
    ensure_database_initialized(conn)


def _configure_conn(conn: sqlite3.Connection, *, readonly: bool = False) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30.0)
    _configure_conn(conn, readonly=False)
    ensure_stage3_tables(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_readonly_conn() -> Iterator[sqlite3.Connection]:
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{SQLITE_DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    _configure_conn(conn, readonly=True)
    ensure_stage3_tables()
    try:
        yield conn
    finally:
        conn.close()


def _pick_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return default


def _pick_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_employee_profile(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    employee_name: str = "",
    position: str = "",
    store_id: str = "",
    role: str = "",
    source: str = "manual",
    mentor_name: str = "",
    self_intro: str = "",
    historical_learning: str = "",
    initial_ability: str = "",
    current_product_knowledge_score: float | None = None,
    current_compliance_score: float | None = None,
    current_sales_communication_score: float | None = None,
    current_response_score: float | None = None,
    current_overall_score: float | None = None,
) -> sqlite3.Row | None:
    ensure_stage3_tables(conn)

    user_id = _pick_text(employee_id)
    if not user_id:
        return None

    existing = conn.execute(
        """
        SELECT *
        FROM employee_profiles
        WHERE employee_id = ? OR user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, user_id),
    ).fetchone()

    now = now_iso()
    incoming_name = _pick_text(employee_name)
    incoming_position = _pick_text(position)
    incoming_store_id = _pick_text(store_id)
    incoming_role = _pick_text(role)
    incoming_source = _pick_text(source, default="manual")
    incoming_mentor_name = _pick_text(mentor_name)
    incoming_self_intro = _pick_text(self_intro)
    incoming_historical_learning = _pick_text(historical_learning)
    incoming_initial_ability = _pick_text(initial_ability)
    incoming_job_title = incoming_position

    if existing:
        row_id = int(existing["id"])
        conn.execute(
            """
            UPDATE employee_profiles
            SET user_id = ?,
                employee_id = ?,
                employee_name = ?,
                position = ?,
                job_title = ?,
                store_id = ?,
                role = ?,
                source = ?,
                mentor_name = ?,
                self_intro = ?,
                historical_learning = ?,
                initial_ability = ?,
                current_product_knowledge_score = COALESCE(?, current_product_knowledge_score),
                current_compliance_score = COALESCE(?, current_compliance_score),
                current_sales_communication_score = COALESCE(?, current_sales_communication_score),
                current_response_score = COALESCE(?, current_response_score),
                current_overall_score = COALESCE(?, current_overall_score),
                updated_at = ?
            WHERE id = ?
            """,
            (
                user_id,
                user_id,
                _pick_text(incoming_name, existing["employee_name"]),
                _pick_text(incoming_position, existing["position"]),
                _pick_text(incoming_job_title, existing["job_title"]),
                _pick_text(incoming_store_id, existing["store_id"]),
                _pick_text(incoming_role, existing["role"]),
                _pick_text(incoming_source, existing["source"], default="manual"),
                _pick_text(incoming_mentor_name, existing["mentor_name"]),
                _pick_text(incoming_self_intro, existing["self_intro"]),
                _pick_text(incoming_historical_learning, existing["historical_learning"]),
                _pick_text(incoming_initial_ability, existing["initial_ability"]),
                _pick_float(current_product_knowledge_score),
                _pick_float(current_compliance_score),
                _pick_float(current_sales_communication_score),
                _pick_float(current_response_score),
                _pick_float(current_overall_score),
                now,
                row_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO employee_profiles (
                user_id, employee_id, employee_name, position, job_title, store_id, role, source,
                mentor_name, self_intro, historical_learning, initial_ability,
                current_product_knowledge_score, current_compliance_score,
                current_sales_communication_score, current_response_score, current_overall_score,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user_id,
                incoming_name,
                incoming_position,
                incoming_job_title,
                incoming_store_id,
                incoming_role,
                incoming_source,
                incoming_mentor_name,
                incoming_self_intro,
                incoming_historical_learning,
                incoming_initial_ability,
                _pick_float(current_product_knowledge_score),
                _pick_float(current_compliance_score),
                _pick_float(current_sales_communication_score),
                _pick_float(current_response_score),
                _pick_float(current_overall_score),
                now,
                now,
            ),
        )

    return conn.execute(
        """
        SELECT *
        FROM employee_profiles
        WHERE employee_id = ? OR user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, user_id),
    ).fetchone()


__all__ = [
    "ensure_stage3_tables",
    "get_conn",
    "get_readonly_conn",
    "json_text",
    "now_iso",
    "upsert_employee_profile",
]
