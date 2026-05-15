from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
import knowledge_feedback_service as service
import routers.knowledge_feedback as kf_router


class KnowledgeFeedbackRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.temp_db = Path(self.tmpdir.name) / "kf.db"
        self._create_schema()

        original_get_conn = kf_router.get_conn
        kf_router.get_conn = self._conn
        self.addCleanup(setattr, kf_router, "get_conn", original_get_conn)

        self.app = FastAPI()
        self.app.include_router(kf_router.router)
        self.manager_user = {
            "user_id": "manager_gz",
            "role": "store_manager",
            "username": "manager_gz",
            "store_id": "STORE_GZ",
        }
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.manager_user
        self.client = TestClient(self.app)

    def _create_schema(self) -> None:
        conn = sqlite3.connect(self.temp_db)
        try:
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
            service.ensure_dispatch_table(conn)
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.temp_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _seed_assistant_records(self) -> None:
        with self._conn() as conn:
            for _ in range(7):
                conn.execute(
                    """
                    INSERT INTO assistant_records (
                        customer_question, knowledge_tag, question_type,
                        risk_level, store_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "顾客说别家同款便宜 1000。",
                        "竞品对比",
                        "价格异议",
                        "high",
                        "STORE_GZ",
                        "2026-05-10T10:00:00+00:00",
                    ),
                )
            for _ in range(3):
                conn.execute(
                    """
                    INSERT INTO assistant_records (
                        customer_question, knowledge_tag, question_type,
                        risk_level, store_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "顾客担心戒托容易变形。",
                        "工艺售后",
                        "售后型",
                        "low",
                        "STORE_GZ",
                        "2026-05-10T10:00:00+00:00",
                    ),
                )

    # ---- GET /clusters ----

    def test_clusters_returns_top_n_from_b1_service(self) -> None:
        self._seed_assistant_records()
        response = self.client.get(
            "/api/knowledge-feedback/clusters", params={"store_id": "STORE_GZ", "top_n": 3}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 200
        data = body["data"]
        assert "clusters" in data
        assert len(data["clusters"]) == 2
        assert data["clusters"][0]["count"] == 7
        assert data["clusters"][0]["primary_tag"] == "竞品对比"

    def test_clusters_requires_store_manager_or_admin(self) -> None:
        self.app.dependency_overrides[auth.get_current_user] = lambda: {
            "user_id": "trainee_zjx",
            "role": "trainee",
            "username": "trainee_zjx",
            "store_id": "STORE_GZ",
        }
        response = self.client.get("/api/knowledge-feedback/clusters", params={"store_id": "STORE_GZ"})
        assert response.status_code == 403

    def test_clusters_defaults_store_id_to_current_user_store(self) -> None:
        self._seed_assistant_records()
        response = self.client.get("/api/knowledge-feedback/clusters")
        assert response.status_code == 200
        clusters = response.json()["data"]["clusters"]
        assert len(clusters) >= 1
        assert response.json()["data"]["store_id"] == "STORE_GZ"

    # ---- POST /dispatch ----

    def test_dispatch_writes_audit_rows_and_returns_count(self) -> None:
        payload = {
            "cluster_signature": "顾客说别家同款便宜 1000。",
            "representative_question": "顾客说别家同款便宜 1000。",
            "primary_tag": "竞品对比",
            "top_keywords": ["竞品", "便宜", "同款"],
            "cluster_count": 7,
            "target_user_ids": ["trainee_zjx", "trainee_lhua"],
            "note": "本周优先复盘。",
        }
        response = self.client.post("/api/knowledge-feedback/dispatch", json=payload)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["dispatched_count"] == 2
        assert len(data["dispatch_ids"]) == 2

        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_feedback_dispatches"
            ).fetchone()["c"]
        assert count == 2

    def test_dispatch_rejected_for_trainee_role(self) -> None:
        self.app.dependency_overrides[auth.get_current_user] = lambda: {
            "user_id": "trainee_zjx",
            "role": "trainee",
            "username": "trainee_zjx",
            "store_id": "STORE_GZ",
        }
        response = self.client.post(
            "/api/knowledge-feedback/dispatch",
            json={
                "representative_question": "顾客问钻石有没有保值空间？",
                "target_user_ids": ["trainee_lhua"],
            },
        )
        assert response.status_code == 403

    def test_dispatch_validates_targets_required(self) -> None:
        response = self.client.post(
            "/api/knowledge-feedback/dispatch",
            json={
                "representative_question": "顾客问钻石有没有保值空间？",
                "target_user_ids": [],
            },
        )
        assert response.status_code in (400, 422)

    # ---- GET /my-tasks ----

    def test_my_tasks_returns_dispatched_tasks_for_current_user(self) -> None:
        payload = {
            "representative_question": "顾客问钻石有没有保值空间？",
            "cluster_signature": "顾客问钻石有没有保值空间？",
            "primary_tag": "钻石话术",
            "top_keywords": ["保值", "钻石"],
            "cluster_count": 12,
            "target_user_ids": ["trainee_zjx"],
            "note": "重点复盘。",
        }
        self.client.post("/api/knowledge-feedback/dispatch", json=payload)

        self.app.dependency_overrides[auth.get_current_user] = lambda: {
            "user_id": "trainee_zjx",
            "role": "trainee",
            "username": "trainee_zjx",
            "store_id": "STORE_GZ",
        }
        response = self.client.get("/api/knowledge-feedback/my-tasks")
        assert response.status_code == 200
        tasks = response.json()["data"]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["representative_question"] == "顾客问钻石有没有保值空间？"
        assert tasks[0]["top_keywords"] == ["保值", "钻石"]


if __name__ == "__main__":
    unittest.main()
