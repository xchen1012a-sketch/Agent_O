from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest

from knowledge_feedback_service import (
    cluster_high_frequency_questions,
    normalize_question,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE assistant_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            store_id TEXT NOT NULL DEFAULT '',
            customer_question TEXT NOT NULL DEFAULT '',
            assistant_reply TEXT NOT NULL DEFAULT '',
            matched_knowledge TEXT NOT NULL DEFAULT '',
            question_type TEXT NOT NULL DEFAULT '',
            knowledge_tag TEXT NOT NULL DEFAULT '',
            risk_level TEXT NOT NULL DEFAULT '',
            weak_dimension TEXT NOT NULL DEFAULT '',
            training_advice TEXT NOT NULL DEFAULT '',
            source_workflow_reply TEXT NOT NULL DEFAULT '',
            source_workflow_analyze TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    question: str,
    knowledge_tag: str = "",
    question_type: str = "",
    risk_level: str = "",
    store_id: str = "STORE_GZ",
    created_at: str = "2026-05-01T10:00:00+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO assistant_records (
            customer_question, knowledge_tag, question_type,
            risk_level, store_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (question, knowledge_tag, question_type, risk_level, store_id, created_at),
    )


def test_normalize_strips_demo_suffix() -> None:
    assert normalize_question("顾客问钻石有没有保值空间？（演示样本 001）") == "顾客问钻石有没有保值空间？"
    assert normalize_question("顾客问钻石有没有保值空间？（演示样本 042）") == "顾客问钻石有没有保值空间？"


def test_normalize_collapses_whitespace_and_trims() -> None:
    assert normalize_question("  顾客　问\t钻石？  ") == "顾客 问 钻石？"


def test_empty_table_returns_empty_list() -> None:
    conn = _make_conn()
    assert cluster_high_frequency_questions(conn) == []


def test_identical_questions_form_one_cluster() -> None:
    conn = _make_conn()
    for _ in range(5):
        _insert(conn, question="顾客问钻石有没有保值空间？", knowledge_tag="钻石话术")

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["count"] == 5
    assert cluster["representative_question"] == "顾客问钻石有没有保值空间？"
    assert cluster["primary_tag"] == "钻石话术"
    assert cluster["rank"] == 1


def test_demo_suffix_variants_cluster_together() -> None:
    conn = _make_conn()
    for idx in range(1, 11):
        _insert(
            conn,
            question=f"顾客问钻石有没有保值空间？（演示样本 {idx:03d}）",
            knowledge_tag="钻石话术",
        )

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 10
    assert clusters[0]["representative_question"] == "顾客问钻石有没有保值空间？"


def test_multiple_topics_produce_multiple_clusters_sorted_by_count() -> None:
    conn = _make_conn()
    for _ in range(7):
        _insert(conn, question="顾客说别家同款便宜 1000。", knowledge_tag="竞品对比")
    for _ in range(3):
        _insert(conn, question="顾客担心戒托容易变形。", knowledge_tag="工艺售后")
    for _ in range(5):
        _insert(conn, question="顾客想送妈妈但不知道款式。", knowledge_tag="送礼推荐")

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    counts = [c["count"] for c in clusters]
    tags = [c["primary_tag"] for c in clusters]
    assert counts == [7, 5, 3]
    assert tags == ["竞品对比", "送礼推荐", "工艺售后"]
    assert [c["rank"] for c in clusters] == [1, 2, 3]


def test_singletons_filtered_by_min_cluster_size() -> None:
    conn = _make_conn()
    for _ in range(3):
        _insert(conn, question="顾客追问能不能保证升值。", knowledge_tag="风险边界")
    _insert(conn, question="顾客提了一个非常罕见的小众问题。", knowledge_tag="其他")

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 3


def test_store_id_filter_restricts_scope() -> None:
    conn = _make_conn()
    for _ in range(4):
        _insert(conn, question="顾客问钻石有没有保值空间？", store_id="STORE_GZ")
    for _ in range(6):
        _insert(conn, question="顾客问钻石有没有保值空间？", store_id="STORE_BJ")

    only_gz = cluster_high_frequency_questions(conn, store_id="STORE_GZ", min_cluster_size=2)
    assert len(only_gz) == 1
    assert only_gz[0]["count"] == 4

    only_bj = cluster_high_frequency_questions(conn, store_id="STORE_BJ", min_cluster_size=2)
    assert only_bj[0]["count"] == 6


def test_since_filter_restricts_time_window() -> None:
    conn = _make_conn()
    old = "2026-01-01T10:00:00+00:00"
    recent = "2026-05-10T10:00:00+00:00"
    for _ in range(5):
        _insert(conn, question="顾客问钻石有没有保值空间？", created_at=old)
    for _ in range(3):
        _insert(conn, question="顾客问钻石有没有保值空间？", created_at=recent)

    since = datetime(2026, 5, 1, tzinfo=timezone.utc)
    clusters = cluster_high_frequency_questions(conn, since=since, min_cluster_size=2)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 3


def test_top_n_limits_returned_clusters() -> None:
    conn = _make_conn()
    questions = [
        ("顾客问钻石有没有保值空间？", "钻石话术"),
        ("顾客说别家同款便宜 1000。", "竞品对比"),
        ("顾客想送妈妈但不知道款式。", "送礼推荐"),
        ("顾客担心戒托容易变形。", "工艺售后"),
        ("顾客追问能不能保证升值。", "风险边界"),
    ]
    for question, tag in questions:
        for _ in range(4):
            _insert(conn, question=question, knowledge_tag=tag)

    clusters = cluster_high_frequency_questions(conn, top_n=3, min_cluster_size=2)
    assert len(clusters) == 3
    assert [c["rank"] for c in clusters] == [1, 2, 3]


def test_cluster_contains_keywords_and_sample_questions() -> None:
    conn = _make_conn()
    for _ in range(4):
        _insert(conn, question="顾客问钻石有没有保值空间？", knowledge_tag="钻石话术")
    _insert(conn, question="顾客问钻石培育钻和天然钻区别？", knowledge_tag="钻石话术")

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    cluster = clusters[0]
    assert "sample_questions" in cluster
    assert isinstance(cluster["sample_questions"], list)
    assert len(cluster["sample_questions"]) >= 1
    assert "top_keywords" in cluster
    assert isinstance(cluster["top_keywords"], list)
    assert all(isinstance(kw, str) and kw for kw in cluster["top_keywords"])


def test_similar_paraphrasings_cluster_together() -> None:
    """近义表达应聚为同一簇：基于字符 bigram 的 TF-IDF 余弦相似度判定。"""
    conn = _make_conn()
    base = "顾客问钻石有没有保值空间？"
    paraphrases = [
        "顾客问钻石有没有保值空间？",
        "顾客问钻石有没有保值空间呢？",
        "顾客问钻石的保值空间大不大？",
    ]
    for q in paraphrases:
        for _ in range(2):
            _insert(conn, question=q, knowledge_tag="钻石话术")

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2, similarity_threshold=0.30)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 6
    assert base in clusters[0]["sample_questions"] or clusters[0]["representative_question"] == base


def test_risk_level_and_question_type_propagated() -> None:
    conn = _make_conn()
    for _ in range(3):
        _insert(
            conn,
            question="顾客追问能不能保证升值。",
            knowledge_tag="风险边界",
            question_type="风险型",
            risk_level="high",
        )

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    cluster = clusters[0]
    assert cluster["primary_question_type"] == "风险型"
    assert cluster["risk_level"] == "high"


def test_skips_empty_questions() -> None:
    conn = _make_conn()
    for _ in range(3):
        _insert(conn, question="", knowledge_tag="未知")
    for _ in range(2):
        _insert(conn, question="   ", knowledge_tag="未知")
    for _ in range(2):
        _insert(conn, question="顾客问钻石有没有保值空间？", knowledge_tag="钻石话术")

    clusters = cluster_high_frequency_questions(conn, min_cluster_size=2)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 2
