"""Hermes Route B pipeline orchestration tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth  # noqa: E402
import database  # noqa: E402
from evo import apply_correction, record_episode  # noqa: E402
from models import AgentEvoMemoryHit, AgentEvoProcedural, AgentEvoReflective  # noqa: E402


class EvoPipelineRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_pipeline.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

        import routers.evo as evo_router  # noqa: WPS433

        def _override_get_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        self.app = FastAPI()
        self.app.include_router(evo_router.router)
        self.app.dependency_overrides[auth.get_current_user] = lambda: {
            "user_id": "manager_gz",
            "role": "store_manager",
            "username": "manager_gz",
            "store_id": "STORE_GZ",
        }
        self.app.dependency_overrides[database.get_db] = _override_get_db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_correction(self, session, *, user_id: str) -> None:
        parent = record_episode(
            session,
            module="assistant",
            user_id=user_id,
            store_id="STORE_GZ",
            query_text="customer says the ring is too expensive",
            response_text="offer a discount immediately",
        )
        apply_correction(
            session,
            episode_id=parent.id,
            correction_text="For a price objection, ask the budget first, then explain material, craft, and service value.",
            actor_user_id="manager_gz",
        )

    def test_pipeline_advance_uses_existing_feedback_without_faking_hits(self) -> None:
        with self.Session() as session:
            self._seed_correction(session, user_id="trainee-001")
            self._seed_correction(session, user_id="trainee-002")
            self._seed_correction(session, user_id="trainee-003")
            session.commit()

        response = self.client.post("/api/evo/pipeline/advance")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(3, data["summary"]["reflective_created_count"])
        self.assertEqual(1, data["summary"]["procedural_created_count"])
        self.assertEqual(0, data["summary"]["promotion_created_count"])
        self.assertEqual(1, data["data_health"]["procedural_count"])
        self.assertEqual(0, data["data_health"]["memory_hit_count"])
        self.assertIn("needs_real_query_hits", data["summary"]["reasons"])

        with self.Session() as session:
            self.assertEqual(3, session.query(AgentEvoReflective).count())
            self.assertEqual(1, session.query(AgentEvoProcedural).count())
            self.assertEqual(0, session.query(AgentEvoMemoryHit).count())

    def test_pipeline_advance_explains_empty_feedback_source(self) -> None:
        response = self.client.post("/api/evo/pipeline/advance")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual("no_new_writes", data["summary"]["status"])
        self.assertEqual(0, data["summary"]["created_count"])
        self.assertIn("no_feedback_for_reflection", data["summary"]["reasons"])


if __name__ == "__main__":
    unittest.main()
