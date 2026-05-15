"""SQL safety validation and sanitization for generic SQL queries."""
from __future__ import annotations

import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

_log = logging.getLogger("jewelry_qipei.sql_safety")

# ── Whitelist: tables DIFY-generated SQL may reference ──────────────────
ALLOWED_TABLES: frozenset[str] = frozenset({
    "employee_profiles",
    "practice_eval_records",
    "learning_eval_records",
    "assistant_records",
    "practice_records",
    "ability_update_records",
    "dashboard_snapshots",
    "stores",
    "growth_plan_records",
    "role_settings",
    "query_records",
    "users",
    # ── newly added for full-system coverage ──
    "sales_performance",
    "performance_attribution_reports",
    "assessment_tasks",
    "assessment_records",
    "growth_task_manual_records",
    "cycle_daily_tasks",
    "training_cycles",
    "module_index_snapshots",
    "exam_results",
    "exam_papers",
    "study_progress",
})


@lru_cache(maxsize=1)
def _runtime_allowed_tables() -> frozenset[str]:
    """Allow every application table present in SQLite while still blocking sqlite_*."""
    tables = set(ALLOWED_TABLES)
    try:
        from database import SQLITE_DB_PATH

        db_path = Path(SQLITE_DB_PATH)
    except Exception:
        db_path = Path(__file__).with_name("jewelry_qipei.db")
    if not db_path.exists():
        return frozenset(tables)
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (name,) in rows:
            if name and not str(name).lower().startswith("sqlite_"):
                tables.add(str(name))
    except Exception as exc:
        _log.debug("load runtime allowed tables failed: %s", exc)
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return frozenset(tables)

# ── Blacklist: columns to strip from any result ─────────────────────────
BLOCKED_COLUMNS: frozenset[str] = frozenset({
    "hashed_password",
    "phone",
})

# ── Dangerous SQL keywords (standalone word match) ──────────────────────
_BLOCKED_KW: frozenset[str] = frozenset({
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER",
    "ATTACH", "CREATE", "EXECUTE", "VACUUM",
    "REINDEX", "REPLACE", "PRAGMA",
})

_MAX_SQL_LEN = 4000
_MAX_ROWS = 100


def validate_sql(sql: str) -> tuple[bool, str]:
    """Return (True, sql) if safe, or (False, reason) if rejected."""
    s = sql.strip()
    if not s:
        return False, "SQL is empty"

    if len(s) > _MAX_SQL_LEN:
        return False, f"SQL exceeds {_MAX_SQL_LEN} characters"

    upper = s.upper()

    # Must start with SELECT
    if not re.match(r"^\s*SELECT\s", upper):
        return False, "Only SELECT statements are allowed"

    # Block dangerous keywords (standalone word)
    for kw in _BLOCKED_KW:
        if re.search(rf"\b{kw}\b", upper):
            return False, f"Blocked keyword: {kw}"

    # Block semicolons (single statement only)
    if ";" in s:
        return False, "Semicolons are not allowed (single statement only)"

    # Block sqlite_ system tables anywhere
    if "sqlite_" in upper.replace(" ", ""):
        return False, "System table access is not allowed"

    # Block sensitive columns at SQL level, not just in returned rows.
    for col in BLOCKED_COLUMNS:
        if re.search(rf"\b{re.escape(col)}\b", s, re.IGNORECASE):
            return False, f"Blocked column: {col}"

    # Extract table names from FROM / JOIN clauses
    tables = re.findall(r"\bFROM\s+(\w+)", upper)
    tables += re.findall(r"\bJOIN\s+(\w+)", upper)
    for tbl in tables:
        if tbl.startswith("SQLITE_"):
            return False, f"System table access is not allowed: {tbl}"
        allowed_tables = _runtime_allowed_tables()
        if tbl.lower() not in allowed_tables:
            # Also check lowercase version
            if tbl not in allowed_tables and tbl.lower() not in {t.lower() for t in allowed_tables}:
                return False, f"Table not in whitelist: {tbl}"

    # Block subqueries — nested SELECT is not needed for the query feature
    if re.search(r"\(\s*SELECT\b", upper):
        return False, "Subqueries are not allowed"

    # Must have LIMIT or we will add one
    if "LIMIT" not in upper:
        # We'll add LIMIT later — not a rejection
        pass

    return True, s


def sanitize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip blocked columns and cap row count."""
    cleaned: list[dict[str, Any]] = []
    for row in rows[:_MAX_ROWS]:
        cleaned.append({k: v for k, v in row.items() if k not in BLOCKED_COLUMNS})
    return cleaned


def ensure_limit(sql: str) -> str:
    """Append LIMIT if not present."""
    if "LIMIT" not in sql.upper():
        return sql.rstrip("; ").rstrip() + " LIMIT 100"
    return sql
