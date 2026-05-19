"""Hermes 路线 B · Phase 4 · Procedural 技能沉淀测试。"""

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
from evo.procedural_synthesizer import (  # noqa: E402
    disable_stale_procedural_memories,
    run_procedural_synthesis,
)
from evo.retriever import build_memory_block, retrieve_semantic_memories  # noqa: E402
from models import (  # noqa: E402
    AgentEvoAuditLog,
    AgentEvoEpisode,
    AgentEvoMemoryHit,
    AgentEvoProcedural,
    AgentEvoReflective,
    AgentEvoSemantic,
)


class EvoProceduralSynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_procedural.db"
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

    def _add_reflective_case(
        self,
        session,
        *,
        user_id: str,
        store_id: str = "STORE_GZ",
        lesson: str = "处理价格异议时，要先问预算，再讲材质工艺和售后价值。",
    ) -> AgentEvoReflective:
        episode = AgentEvoEpisode(
            episode_type="reply",
            module="assistant",
            user_id=user_id,
            store_id=store_id,
            query_text="客户嫌价格贵怎么办？",
            response_text="先问预算，再讲材质工艺和售后价值。",
            signal="thumb_up",
            compliance_tags="[]",
        )
        session.add(episode)
        session.flush()
        row = AgentEvoReflective(
            scope_type="user",
            scope_id=user_id,
            lesson=lesson,
            evidence_episode_ids=json.dumps([episode.id], ensure_ascii=False),
            confidence=0.72,
            status="active",
            expires_at=self.now + timedelta(days=30),
        )
        session.add(row)
        session.flush()
        return row

    def test_synthesizer_promotes_cross_user_reflective_cluster_to_procedural(self) -> None:
        with self.Session() as session:
            r1 = self._add_reflective_case(session, user_id="trainee-001")
            r2 = self._add_reflective_case(session, user_id="trainee-002")
            r3 = self._add_reflective_case(
                session,
                user_id="trainee-003",
                lesson="遇到价格太贵的异议，要先确认客户预算，再补充材质工艺和售后保障。",
            )
            session.commit()

            rows = run_procedural_synthesis(session, now=self.now)
            session.commit()

            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("store", row.scope_type)
            self.assertEqual("STORE_GZ", row.scope_id)
            self.assertEqual("auto", row.status)
            self.assertEqual("auto", row.write_mode)
            self.assertTrue(row.title)
            self.assertTrue(row.do_json)
            self.assertEqual([r1.id, r2.id, r3.id], json.loads(row.source_reflective_ids_json))

            refreshed = [session.get(AgentEvoReflective, item.id) for item in (r1, r2, r3)]
            self.assertEqual([row.id, row.id, row.id], [item.promoted_to_procedural_id for item in refreshed])
            audits = session.query(AgentEvoAuditLog).filter_by(action="procedural_write").all()
            self.assertEqual(1, len(audits))
            self.assertEqual(str(row.id), audits[0].target_id)

    def test_synthesizer_requires_cross_user_cluster(self) -> None:
        with self.Session() as session:
            for _idx in range(3):
                self._add_reflective_case(session, user_id="trainee-001")
            session.commit()

            rows = run_procedural_synthesis(session, now=self.now)
            session.commit()

            self.assertEqual([], rows)
            self.assertEqual(0, session.query(AgentEvoProcedural).count())

    def test_retriever_includes_procedural_between_semantic_and_reflective(self) -> None:
        with self.Session() as session:
            semantic = AgentEvoSemantic(
                scope_type="store",
                scope_id="STORE_GZ",
                content="价格异议先确认预算，再讲材质工艺和售后价值。",
                trigger_text="客户嫌价格贵怎么办",
                source_episode_ids="[]",
                confidence=0.9,
                status="active",
                write_mode="auto",
            )
            procedural = AgentEvoProcedural(
                scope_type="store",
                scope_id="STORE_GZ",
                title="价格异议标准应对话术",
                trigger_json=json.dumps(["客户嫌价格贵", "价格异议"], ensure_ascii=False),
                do_json=json.dumps(["先问预算", "讲材质工艺", "补充售后价值"], ensure_ascii=False),
                dont_json=json.dumps(["不要直接打折", "不要承诺保值"], ensure_ascii=False),
                example="客户说太贵时，先问预算，再说明材质、工艺和售后价值。",
                source_reflective_ids_json="[1, 2, 3]",
                source_episode_ids_json="[11, 12, 13]",
                confidence=0.85,
                status="auto",
                write_mode="auto",
                eval_case_ids_json="[]",
            )
            reflective = AgentEvoReflective(
                scope_type="user",
                scope_id="trainee-004",
                lesson="处理价格异议时，下一次要先问预算，再讲材质工艺和售后价值。",
                evidence_episode_ids="[101, 102]",
                confidence=0.85,
                status="active",
                expires_at=self.now + timedelta(days=30),
            )
            session.add_all([semantic, procedural, reflective])
            session.commit()

            hits = retrieve_semantic_memories(
                session,
                user_id="trainee-004",
                store_id="STORE_GZ",
                module="assistant",
                query_text="客户嫌价格贵怎么办，要怎么回应？",
                limit=5,
            )
            session.commit()

            types = [hit.memory_type for hit in hits]
            self.assertEqual("semantic", types[0])
            self.assertIn("procedural", types)
            self.assertIn("reflective", types)
            procedural_hit = next(hit for hit in hits if hit.memory_type == "procedural")
            reflective_hit = next(hit for hit in hits if hit.memory_type == "reflective")
            self.assertGreater(procedural_hit.score, reflective_hit.score)

            refreshed = session.get(AgentEvoProcedural, procedural.id)
            self.assertEqual(1, refreshed.hit_count)
            self.assertIsNotNone(refreshed.last_hit_at)
            logs = session.query(AgentEvoMemoryHit).filter_by(memory_type="procedural").all()
            self.assertEqual(1, len(logs))

            block = build_memory_block(hits)
            self.assertIn("技能规则", block)
            self.assertIn("先问预算", block)

    def test_disable_stale_procedural_memories_archives_unhit_rules(self) -> None:
        with self.Session() as session:
            stale = AgentEvoProcedural(
                scope_type="store",
                scope_id="STORE_GZ",
                title="旧的价格异议话术",
                trigger_json="[\"价格异议\"]",
                do_json="[\"先问预算\"]",
                dont_json="[]",
                example="",
                source_reflective_ids_json="[]",
                source_episode_ids_json="[]",
                confidence=0.7,
                status="auto",
                write_mode="auto",
                eval_case_ids_json="[]",
                created_at=self.now - timedelta(days=31),
            )
            fresh = AgentEvoProcedural(
                scope_type="store",
                scope_id="STORE_GZ",
                title="新的价格异议话术",
                trigger_json="[\"价格异议\"]",
                do_json="[\"先问预算\"]",
                dont_json="[]",
                example="",
                source_reflective_ids_json="[]",
                source_episode_ids_json="[]",
                confidence=0.7,
                status="auto",
                write_mode="auto",
                eval_case_ids_json="[]",
                created_at=self.now - timedelta(days=5),
            )
            session.add_all([stale, fresh])
            session.commit()

            disabled = disable_stale_procedural_memories(session, now=self.now, stale_days=30)
            session.commit()

            self.assertEqual(1, disabled)
            self.assertEqual("auto_disabled", session.get(AgentEvoProcedural, stale.id).status)
            self.assertEqual("auto", session.get(AgentEvoProcedural, fresh.id).status)


if __name__ == "__main__":
    unittest.main()
