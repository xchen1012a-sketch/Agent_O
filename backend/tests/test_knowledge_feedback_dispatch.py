from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest

from knowledge_feedback_service import (
    DispatchInput,
    ensure_dispatch_table,
    dispatch_cluster_to_targets,
    list_dispatched_tasks_for_user,
    list_recent_dispatches_by_store,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dispatch_table(conn)
    return conn


def _make_input(**overrides) -> DispatchInput:
    defaults = dict(
        cluster_signature="顾客问钻石有没有保值空间？",
        representative_question="顾客问钻石有没有保值空间？",
        primary_tag="钻石话术",
        top_keywords=["保值", "钻石", "空间"],
        cluster_count=47,
        store_id="STORE_GZ",
        dispatched_by_user_id="manager_gz",
        target_user_ids=["trainee_zjx"],
        target_role="trainee",
        note="本周高频问题，请重点陪练。",
    )
    defaults.update(overrides)
    return DispatchInput(**defaults)


def test_ensure_dispatch_table_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dispatch_table(conn)
    ensure_dispatch_table(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_feedback_dispatches'"
    ).fetchall()
    assert len(rows) == 1


def test_dispatch_writes_one_row_per_target() -> None:
    conn = _make_conn()
    result = dispatch_cluster_to_targets(
        conn,
        payload=_make_input(target_user_ids=["trainee_zjx", "trainee_lhua", "trainee_wbei"]),
    )
    assert result["dispatched_count"] == 3
    rows = conn.execute(
        "SELECT target_user_id, representative_question FROM knowledge_feedback_dispatches ORDER BY target_user_id"
    ).fetchall()
    assert [row["target_user_id"] for row in rows] == ["trainee_lhua", "trainee_wbei", "trainee_zjx"]
    assert all(row["representative_question"] == "顾客问钻石有没有保值空间？" for row in rows)


def test_dispatch_rejects_empty_targets() -> None:
    conn = _make_conn()
    with pytest.raises(ValueError, match="target_user_ids"):
        dispatch_cluster_to_targets(conn, payload=_make_input(target_user_ids=[]))


def test_dispatch_rejects_empty_representative_question() -> None:
    conn = _make_conn()
    with pytest.raises(ValueError, match="representative_question"):
        dispatch_cluster_to_targets(
            conn,
            payload=_make_input(representative_question="   "),
        )


def test_dispatch_dedupes_target_user_ids() -> None:
    conn = _make_conn()
    result = dispatch_cluster_to_targets(
        conn,
        payload=_make_input(target_user_ids=["trainee_zjx", "trainee_zjx", "  trainee_zjx  "]),
    )
    assert result["dispatched_count"] == 1
    rows = conn.execute("SELECT target_user_id FROM knowledge_feedback_dispatches").fetchall()
    assert [row["target_user_id"] for row in rows] == ["trainee_zjx"]


def test_dispatch_returned_payload_includes_ids_and_signature() -> None:
    conn = _make_conn()
    result = dispatch_cluster_to_targets(conn, payload=_make_input())
    assert "dispatch_ids" in result
    assert len(result["dispatch_ids"]) == 1
    assert result["cluster_signature"] == "顾客问钻石有没有保值空间？"


def test_list_my_tasks_returns_only_for_user() -> None:
    conn = _make_conn()
    dispatch_cluster_to_targets(
        conn,
        payload=_make_input(
            target_user_ids=["trainee_zjx", "trainee_lhua"],
            representative_question="顾客问钻石有没有保值空间？",
        ),
    )
    dispatch_cluster_to_targets(
        conn,
        payload=_make_input(
            target_user_ids=["trainee_zjx"],
            representative_question="顾客担心戒托容易变形。",
            cluster_signature="顾客担心戒托容易变形。",
        ),
    )

    zjx_tasks = list_dispatched_tasks_for_user(conn, user_id="trainee_zjx")
    assert len(zjx_tasks) == 2
    questions = {item["representative_question"] for item in zjx_tasks}
    assert questions == {"顾客问钻石有没有保值空间？", "顾客担心戒托容易变形。"}

    lhua_tasks = list_dispatched_tasks_for_user(conn, user_id="trainee_lhua")
    assert len(lhua_tasks) == 1
    assert lhua_tasks[0]["representative_question"] == "顾客问钻石有没有保值空间？"


def test_list_my_tasks_includes_top_keywords_as_list() -> None:
    conn = _make_conn()
    dispatch_cluster_to_targets(conn, payload=_make_input(top_keywords=["保值", "钻石"]))
    tasks = list_dispatched_tasks_for_user(conn, user_id="trainee_zjx")
    assert tasks[0]["top_keywords"] == ["保值", "钻石"]


def test_list_my_tasks_skips_cancelled_status() -> None:
    conn = _make_conn()
    dispatch_cluster_to_targets(conn, payload=_make_input())
    conn.execute(
        "UPDATE knowledge_feedback_dispatches SET status = 'cancelled' WHERE target_user_id = ?",
        ("trainee_zjx",),
    )
    assert list_dispatched_tasks_for_user(conn, user_id="trainee_zjx") == []


def test_recent_dispatches_filter_by_store_and_window() -> None:
    conn = _make_conn()
    dispatch_cluster_to_targets(conn, payload=_make_input(store_id="STORE_GZ"))
    dispatch_cluster_to_targets(conn, payload=_make_input(store_id="STORE_BJ", target_user_ids=["trainee_lhua"]))

    only_gz = list_recent_dispatches_by_store(conn, store_id="STORE_GZ")
    assert len(only_gz) == 1
    assert only_gz[0]["store_id"] == "STORE_GZ"

    only_bj = list_recent_dispatches_by_store(conn, store_id="STORE_BJ")
    assert len(only_bj) == 1
    assert only_bj[0]["store_id"] == "STORE_BJ"


def test_default_status_is_dispatched() -> None:
    conn = _make_conn()
    dispatch_cluster_to_targets(conn, payload=_make_input())
    row = conn.execute(
        "SELECT status FROM knowledge_feedback_dispatches WHERE target_user_id = ?",
        ("trainee_zjx",),
    ).fetchone()
    assert row["status"] == "dispatched"
