from __future__ import annotations

import asyncio
import json
from io import BytesIO
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi import UploadFile
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
import routers.theory_learning as theory_router


class TestTheoryLearningRouter:
    def setup_method(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.temp_db = Path(self.tmpdir.name) / "theory_learning.db"
        self.docs_dir = Path(self.tmpdir.name) / "docs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._create_schema()

        self.original_get_conn = theory_router.get_conn
        self.original_docs_dir = theory_router.THEORY_DOCS_DIR
        theory_router.get_conn = self._conn
        theory_router.THEORY_DOCS_DIR = self.docs_dir

        app = FastAPI()
        app.include_router(theory_router.router)
        self.admin_user = {
            "user_id": "admin_1",
            "role": "admin",
            "username": "admin",
        }
        app.dependency_overrides[auth.get_current_user] = lambda: self.admin_user
        self.client = TestClient(app)

    def teardown_method(self) -> None:
        theory_router.get_conn = self.original_get_conn
        theory_router.THEORY_DOCS_DIR = self.original_docs_dir
        self.tmpdir.cleanup()

    def _create_schema(self) -> None:
        conn = sqlite3.connect(self.temp_db)
        try:
            conn.execute(
                """
                CREATE TABLE theory_learning_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    original_file_name TEXT NOT NULL DEFAULT '',
                    stored_file_name TEXT NOT NULL DEFAULT '',
                    content_type TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    uploaded_by TEXT NOT NULL DEFAULT '',
                    is_published INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
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

    def test_upload_accepts_arbitrary_file_types_and_resolves_mime(self) -> None:
        response = self.client.post(
            "/api/theory-learning/admin/documents",
            data={
                "title": "任意文件测试",
                "category": "测试",
                "is_published": "1",
            },
            files={
                "file": (
                    "sample.docx",
                    b"fake-docx-bytes",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 200, response.text
        document = response.json()["data"]["document"]
        assert document["file_name"] == "sample.docx"
        assert document["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert len(list(self.docs_dir.iterdir())) == 1

    def test_upload_allows_files_without_extension_and_defaults_mime(self) -> None:
        response = self.client.post(
            "/api/theory-learning/admin/documents",
            data={
                "title": "无扩展名测试",
                "category": "测试",
                "is_published": "1",
            },
            files={"file": ("sample", b"plain-bytes", "application/octet-stream")},
        )
        assert response.status_code == 200, response.text
        document = response.json()["data"]["document"]
        assert document["file_name"] == "sample"
        assert document["content_type"] == "application/octet-stream"
        stored_files = list(self.docs_dir.iterdir())
        assert len(stored_files) == 1
        assert "." not in stored_files[0].name

    def test_upload_rejects_empty_file_name(self) -> None:
        response = asyncio.run(
            theory_router.upload_theory_document(
                title="空文件名测试",
                category="测试",
                summary="",
                is_published="1",
                file=UploadFile(file=BytesIO(b"plain-bytes"), filename=""),
                current_user=self.admin_user,
            )
        )
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["code"] == 400
        assert payload["message"] == "文件名不能为空"
