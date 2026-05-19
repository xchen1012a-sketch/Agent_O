"""Hermes 路线 B · Phase 3 · Reflective 自反思循环测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
from evo import apply_correction, record_episode  # noqa: E402
from evo.reflector import run_reflection_cycle  # noqa: E402
from evo.retriever import build_memory_block, retrieve_semantic_memories  # noqa: E402
from models import (  # noqa: E402
    AgentEvoAuditLog,
    AgentEvoMemoryHit,
    AgentEvoReflective,
    AgentEvoSemantic,
)


class EvoReflectiveLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_reflective.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self.now = datetime.now(timezone.utc) + timedelta(seconds=1)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_reflection_cycle_writes_lesson_for_original_user_with_evidence(self) -> None:
        with self.Session() as session:
            parent = record_episode(
                session,
                module="assistant",
                user_id="trainee-001",
                store_id="STORE_GZ",
                query_text="客户嫌价格贵怎么办？",
                response_text="可以直接说今天能打折。",
            )
            correction = apply_correction(
                session,
                episode_id=parent.id,
                correction_text="价格异议要先问预算，再讲材质工艺和售后价值。",
                actor_user_id="manager_gz",
            )
            session.commit()

            rows = run_reflection_cycle(session, now=self.now, window_hours=24)
            session.commit()

            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertIn(row.scope_type, ("user", "store", "global"))
            self.assertTrue(row.scope_id)
            self.assertEqual("active", row.status)
            self.assertTrue(row.lesson)
            self.assertEqual([parent.id, correction.id], json.loads(row.evidence_episode_ids))
            self.assertEqual(self.now + timedelta(days=30), row.expires_at)

            audits = session.query(AgentEvoAuditLog).filter_by(action="reflective_write").all()
            self.assertEqual(1, len(audits))
            self.assertEqual(str(row.id), audits[0].target_id)

    def test_reflection_cycle_discards_lessons_without_evidence(self) -> None:
        def fake_generator(*_args, **_kwargs):
            return [
                {
                    "lesson": "没有证据链的反思不应写入。",
                    "evidence_episode_ids": [],
                    "scope_suggestion": "user",
                }
            ]

        with self.Session() as session:
            record_episode(
                session,
                module="assistant",
                user_id="trainee-002",
                query_text="顾客问证书怎么看",
                response_text="看证书编号。",
            )
            session.commit()

            rows = run_reflection_cycle(
                session,
                now=self.now,
                window_hours=24,
                lesson_generator=fake_generator,
            )
            session.commit()

            self.assertEqual([], rows)
            self.assertEqual(0, session.query(AgentEvoReflective).count())

    def test_retriever_includes_reflective_hits_but_keeps_them_lower_weight(self) -> None:
        with self.Session() as session:
            semantic = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-003",
                content="价格异议先确认预算，再讲材质工艺和售后价值。",
                trigger_text="客户嫌价格贵怎么办",
                source_episode_ids="[]",
                confidence=0.8,
                status="active",
                write_mode="auto",
            )
            reflective = AgentEvoReflective(
                scope_type="user",
                scope_id="trainee-003",
                lesson="处理价格异议时，下一次要先问预算，再讲材质工艺和售后价值。",
                evidence_episode_ids="[101, 102]",
                confidence=0.8,
                status="active",
                expires_at=self.now + timedelta(days=30),
            )
            session.add_all([semantic, reflective])
            session.commit()

            hits = retrieve_semantic_memories(
                session,
                user_id="trainee-003",
                store_id="STORE_GZ",
                module="assistant",
                query_text="客户嫌价格贵怎么办，要怎么回应？",
                limit=5,
            )
            session.commit()

            self.assertGreaterEqual(len(hits), 2)
            self.assertEqual("semantic", hits[0].memory_type)
            self.assertIn("reflective", [hit.memory_type for hit in hits])
            reflective_hit = next(hit for hit in hits if hit.memory_type == "reflective")
            self.assertLess(reflective_hit.score, hits[0].score)

            refreshed = session.get(AgentEvoReflective, reflective.id)
            self.assertEqual(1, refreshed.hit_count)
            logs = session.query(AgentEvoMemoryHit).filter_by(memory_type="reflective").all()
            self.assertEqual(1, len(logs))

            block = build_memory_block(hits)
            self.assertIn("自反思经验", block)
            self.assertIn("先问预算", block)


if __name__ == "__main__":
    unittest.main()
