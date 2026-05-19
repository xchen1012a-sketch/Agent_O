"""Hermes 路线 B · Phase 1 · Episodic 自动采集闭环测试。"""

from __future__ import annotations

import json
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
from evo import apply_correction, apply_feedback, record_episode  # noqa: E402
from evo.compliance_tagger import detect_compliance_tags  # noqa: E402
from models import (  # noqa: E402
    AgentEvoAuditLog,
    AgentEvoEpisode,
    AgentEvoMemoryHit,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)


class EvoEpisodeRecorderTests(unittest.TestCase):
    """直接对 service 层调用——验证落库 + 审计 + 合规标签。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_record_episode_persists_row_and_audit(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="trainee-001",
                store_id="STORE_GZ",
                request_id="req-1",
                query_text="顾客问 18K 金能不能保值",
                response_text="可以从材质工艺和售后角度讲。",
            )
            session.commit()

            self.assertGreater(record.id, 0)
            self.assertEqual(record.signal, "none")
            self.assertEqual(record.compliance_tags, ["保值"])

            episode = session.get(AgentEvoEpisode, record.id)
            self.assertIsNotNone(episode)
            self.assertEqual(episode.module, "assistant")
            self.assertEqual(episode.user_id, "trainee-001")
            self.assertEqual(json.loads(episode.compliance_tags), ["保值"])

            audits = session.query(AgentEvoAuditLog).all()
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0].action, "episode_write")
            self.assertEqual(audits[0].target_id, str(record.id))

    def test_record_episode_accepts_quick_query_module(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="quick_query",
                user_id="manager-001",
                store_id="STORE_A",
                query_text="全系统有几家门店？",
                response_text="目前系统里一共是2家门店。",
            )
            session.commit()

            self.assertGreater(record.id, 0)
            self.assertEqual(record.module, "quick_query")
            episode = session.get(AgentEvoEpisode, record.id)
            self.assertIsNotNone(episode)
            self.assertEqual(episode.module, "quick_query")

    def test_apply_feedback_updates_signal(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="trainee-002",
                query_text="问什么",
                response_text="答什么",
            )
            session.commit()

            updated = apply_feedback(
                session,
                episode_id=record.id,
                signal="thumb_down",
                actor_user_id="trainee-002",
            )
            session.commit()

            self.assertEqual(updated.signal, "thumb_down")
            episode = session.get(AgentEvoEpisode, record.id)
            self.assertEqual(episode.signal, "thumb_down")
            actions = [a.action for a in session.query(AgentEvoAuditLog).all()]
            self.assertIn("episode_feedback", actions)

    def test_thumb_up_increases_confidence_for_linked_memory_hits(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="trainee-002",
                query_text="18K gold value",
                response_text="Do not promise value retention.",
            )
            memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-002",
                content="Do not promise 18K gold value retention.",
                trigger_text="18K gold value",
                source_episode_ids="[]",
                confidence=0.5,
                status="active",
                write_mode="auto",
            )
            session.add(memory)
            session.flush()
            session.add(
                AgentEvoMemoryHit(
                    memory_type="semantic",
                    memory_id=memory.id,
                    user_id="trainee-002",
                    module="assistant",
                    query_text="18K gold value",
                    score=0.9,
                )
            )
            session.commit()

            apply_feedback(session, episode_id=record.id, signal="thumb_up", actor_user_id="trainee-002")
            session.commit()

            refreshed = session.get(AgentEvoSemantic, memory.id)
            self.assertGreater(refreshed.confidence, 0.5)
            actions = [a.action for a in session.query(AgentEvoAuditLog).all()]
            self.assertIn("memory_feedback_positive", actions)

    def test_thumb_down_decreases_confidence_and_creates_pending_review_candidate(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="trainee-002",
                query_text="18K gold value",
                response_text="It always keeps value.",
            )
            memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-002",
                content="Bad memory that should lose confidence.",
                trigger_text="18K gold value",
                source_episode_ids="[]",
                confidence=0.5,
                status="active",
                write_mode="auto",
            )
            session.add(memory)
            session.flush()
            session.add(
                AgentEvoMemoryHit(
                    memory_type="semantic",
                    memory_id=memory.id,
                    user_id="trainee-002",
                    module="assistant",
                    query_text="18K gold value",
                    score=0.9,
                )
            )
            session.commit()

            apply_feedback(session, episode_id=record.id, signal="thumb_down", actor_user_id="trainee-002")
            session.commit()

            refreshed = session.get(AgentEvoSemantic, memory.id)
            self.assertLess(refreshed.confidence, 0.5)
            candidates = session.query(AgentEvoSemantic).filter(AgentEvoSemantic.id != memory.id).all()
            self.assertEqual(1, len(candidates))
            self.assertEqual("pending", candidates[0].status)
            self.assertEqual(1, session.query(AgentEvoReviewQueue).filter_by(target_id=candidates[0].id).count())
            actions = [a.action for a in session.query(AgentEvoAuditLog).all()]
            self.assertIn("memory_feedback_negative", actions)

    def test_apply_correction_writes_child_episode(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="trainee-003",
                query_text="18K 金能不能保值",
                response_text="可以保值。",
            )
            session.commit()

            correction = apply_correction(
                session,
                episode_id=record.id,
                correction_text="不能承诺保值，建议讲材质工艺。",
                actor_user_id="manager_gz",
            )
            session.commit()

            self.assertEqual(correction.episode_type, "correction")
            self.assertEqual(correction.parent_episode_id, record.id)
            parent = session.get(AgentEvoEpisode, record.id)
            self.assertEqual(parent.signal, "correction")
            child = session.get(AgentEvoEpisode, correction.id)
            self.assertEqual(child.correction_text, "不能承诺保值，建议讲材质工艺。")
            self.assertEqual(json.loads(child.compliance_tags), ["保值"])
            memory = session.query(AgentEvoSemantic).one()
            self.assertEqual("pending", memory.status)
            self.assertEqual(1, session.query(AgentEvoReviewQueue).filter_by(target_id=memory.id).count())

    def test_invalid_signal_raises(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="u",
                query_text="q",
                response_text="r",
            )
            session.commit()
            with self.assertRaises(ValueError):
                apply_feedback(session, episode_id=record.id, signal="meh")

    def test_apply_correction_requires_text(self) -> None:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="u",
                query_text="q",
                response_text="r",
            )
            session.commit()
            with self.assertRaises(ValueError):
                apply_correction(session, episode_id=record.id, correction_text="   ")

    def test_compliance_tagger_detects_known_keywords(self) -> None:
        self.assertEqual(detect_compliance_tags("这块表保值升值都行"), ["保值", "升值"])
        self.assertEqual(detect_compliance_tags("好看的设计"), [])


class EvoRouterTests(unittest.TestCase):
    """通过 FastAPI TestClient 验证 evo 路由的反馈/纠正接口。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_router.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

        import routers.evo as evo_router  # noqa: WPS433 - delayed import after path setup

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

    def _seed_episode(self) -> int:
        with self.Session() as session:
            record = record_episode(
                session,
                module="assistant",
                user_id="trainee-001",
                query_text="q",
                response_text="r",
            )
            session.commit()
            return record.id

    def test_feedback_endpoint_marks_thumb_down(self) -> None:
        episode_id = self._seed_episode()
        resp = self.client.post(
            f"/api/evo/episodes/{episode_id}/feedback",
            json={"signal": "thumb_down"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["code"], 200)
        self.assertEqual(payload["data"]["signal"], "thumb_down")

    def test_correction_endpoint_creates_child(self) -> None:
        episode_id = self._seed_episode()
        resp = self.client.post(
            f"/api/evo/episodes/{episode_id}/correction",
            json={"correction_text": "应说明材质而非承诺保值。"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["data"]["episode_type"], "correction")
        self.assertEqual(payload["data"]["parent_episode_id"], episode_id)

    def test_feedback_404_when_missing(self) -> None:
        resp = self.client.post(
            "/api/evo/episodes/99999/feedback",
            json={"signal": "thumb_up"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_correction_requires_text(self) -> None:
        episode_id = self._seed_episode()
        resp = self.client.post(
            f"/api/evo/episodes/{episode_id}/correction",
            json={"correction_text": ""},
        )
        self.assertEqual(resp.status_code, 422)  # pydantic min_length=1


if __name__ == "__main__":
    unittest.main()
