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
import routers.system_settings as system_settings_router
import routers.tts as tts_router
import system_settings_service


class DigitalHumanSystemSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.temp_db = Path(self.tmpdir.name) / "digital_human_settings.db"
        self._create_schema()

        self._patch_db_context(system_settings_service)
        self._patch_db_context(tts_router.digital_human_settings_service)

        self.original_service_key = system_settings_service.app_config.MINIMAX_API_KEY
        self.original_tts_key = tts_router.app_config.MINIMAX_API_KEY
        self.addCleanup(setattr, system_settings_service.app_config, "MINIMAX_API_KEY", self.original_service_key)
        self.addCleanup(setattr, tts_router.app_config, "MINIMAX_API_KEY", self.original_tts_key)

        app = FastAPI()
        app.include_router(system_settings_router.router)
        app.include_router(tts_router.router)

        self.current_user = {"user_id": "1", "role": "admin", "username": "admin", "store_id": "STORE01"}
        app.dependency_overrides[auth.get_current_user] = lambda: self.current_user
        self.client = TestClient(app)

    def _create_schema(self) -> None:
        conn = sqlite3.connect(self.temp_db)
        try:
            conn.execute(
                """
                CREATE TABLE system_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_by_role TEXT NOT NULL DEFAULT ''
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

    def _patch_db_context(self, module) -> None:
        original = module.get_conn
        module.get_conn = self._conn
        self.addCleanup(setattr, module, "get_conn", original)

    def test_admin_can_get_default_settings_when_minimax_configured(self) -> None:
        system_settings_service.app_config.MINIMAX_API_KEY = "minimax-test-key"
        tts_router.app_config.MINIMAX_API_KEY = "minimax-test-key"

        response = self.client.get("/api/system-settings/digital-human")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 200)
        self.assertEqual(
            payload["data"],
            {
                "enabled": True,
                "tts_provider": "minimax",
                "minimax_configured": True,
            },
        )

    def test_admin_can_get_default_settings_when_minimax_missing(self) -> None:
        system_settings_service.app_config.MINIMAX_API_KEY = ""
        tts_router.app_config.MINIMAX_API_KEY = ""

        response = self.client.get("/api/system-settings/digital-human")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["enabled"], True)
        self.assertEqual(payload["data"]["tts_provider"], "browser")
        self.assertEqual(payload["data"]["minimax_configured"], False)

    def test_admin_can_patch_settings(self) -> None:
        system_settings_service.app_config.MINIMAX_API_KEY = "minimax-test-key"
        tts_router.app_config.MINIMAX_API_KEY = "minimax-test-key"

        response = self.client.patch(
            "/api/system-settings/digital-human",
            json={"enabled": False, "tts_provider": "browser"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["enabled"], False)
        self.assertEqual(payload["data"]["tts_provider"], "browser")

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT setting_key, setting_value, updated_by, updated_by_role FROM system_settings ORDER BY setting_key"
            ).fetchall()
        row_map = {row["setting_key"]: dict(row) for row in rows}
        self.assertEqual(row_map["digital_human_enabled"]["setting_value"], "false")
        self.assertEqual(row_map["digital_human_enabled"]["updated_by"], "1")
        self.assertEqual(row_map["digital_human_enabled"]["updated_by_role"], "admin")
        self.assertEqual(row_map["digital_human_tts_provider"]["setting_value"], "browser")

    def test_non_admin_cannot_patch_settings(self) -> None:
        self.current_user = {"user_id": "2", "role": "trainee", "username": "trainee", "store_id": "STORE01"}

        response = self.client.patch(
            "/api/system-settings/digital-human",
            json={"enabled": False},
        )

        self.assertEqual(response.status_code, 403)

    def test_tts_route_rejects_browser_provider(self) -> None:
        system_settings_service.app_config.MINIMAX_API_KEY = "minimax-test-key"
        tts_router.app_config.MINIMAX_API_KEY = "minimax-test-key"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by, updated_by_role) VALUES (?, ?, '', '', '')",
                ("digital_human_enabled", "true"),
            )
            conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by, updated_by_role) VALUES (?, ?, '', '', '')",
                ("digital_human_tts_provider", "browser"),
            )

        response = self.client.post("/api/tts/synthesize", json={"text": "你好"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("browser", response.text)

    def test_tts_route_rejects_when_digital_human_disabled(self) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by, updated_by_role) VALUES (?, ?, '', '', '')",
                ("digital_human_enabled", "false"),
            )
            conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by, updated_by_role) VALUES (?, ?, '', '', '')",
                ("digital_human_tts_provider", "minimax"),
            )

        response = self.client.post("/api/tts/synthesize", json={"text": "你好"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("disabled", response.text.lower())


if __name__ == "__main__":
    unittest.main()
