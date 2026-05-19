"""Hermes 路线 B · Phase 2 · Semantic 抽取 + 检索注入测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import assistant_service  # noqa: E402
import database  # noqa: E402
from evo import apply_correction, record_episode  # noqa: E402
import evo.semantic_extractor as semantic_extractor  # noqa: E402
from evo.retriever import build_memory_block, retrieve_semantic_memories  # noqa: E402
from models import (  # noqa: E402
    AgentEvoAuditLog,
    AgentEvoMemoryHit,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)


class EvoSemanticMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_semantic.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_correction_extracts_active_semantic_memory(self) -> None:
        with self.Session() as session:
            parent = record_episode(
                session,
                module="assistant",
                user_id="trainee-001",
                store_id="STORE_GZ",
                query_text="18K 金能不能保值？",
                response_text="可以承诺 18K 金比较保值。",
            )
            session.commit()

            correction = apply_correction(
                session,
                episode_id=parent.id,
                correction_text="不能承诺 18K 金保值，应改为讲材质工艺和日常佩戴价值。",
                actor_user_id="manager_gz",
            )
            session.commit()

            memories = session.query(AgentEvoSemantic).all()
            self.assertEqual(1, len(memories))
            memory = memories[0]
            self.assertIn(memory.scope_type, ("user", "store", "global"))
            if memory.scope_type == "user":
                self.assertEqual("trainee-001", memory.scope_id)
            elif memory.scope_type == "store":
                self.assertEqual("STORE_GZ", memory.scope_id)
            self.assertIn(memory.status, ("active", "pending"))
            self.assertIn(memory.write_mode, ("auto", "human"))
            self.assertTrue(memory.content)
            self.assertTrue(memory.trigger_text)
            self.assertEqual([parent.id, correction.id], json.loads(memory.source_episode_ids))

            self.assertEqual(
                1 if memory.status == "pending" else 0,
                session.query(AgentEvoReviewQueue).count(),
            )
            actions = [row.action for row in session.query(AgentEvoAuditLog).all()]
            self.assertIn("semantic_write", actions)

    def test_correction_uses_dify_structured_semantic_output(self) -> None:
        original_key = semantic_extractor.app_config.DIFY_EVO_SEMANTIC_API_KEY
        original_base = semantic_extractor.app_config.DIFY_EVO_SEMANTIC_API_BASE
        original_run = semantic_extractor.run_workflow_blocking

        def fake_run_workflow_blocking(**kwargs):
            self.assertEqual("app-evo-semantic-test", kwargs["api_key"])
            self.assertEqual("https://dify.example.com", kwargs["base_url"])
            self.assertEqual(
                {
                    "query_text",
                    "wrong",
                    "correct",
                    "feedback_signal",
                    "user_id",
                    "store_id",
                },
                set(kwargs["inputs"]),
            )
            return {
                "code": 200,
                "data": {
                    "outputs": {
                        "semantic_json": json.dumps(
                            {
                                "trigger": "customer asks about 18K value retention",
                                "content": "Do not promise 18K gold value retention; explain material, craft, and daily wearing value.",
                                "scope_suggestion": "store",
                                "confidence": 0.86,
                            },
                            ensure_ascii=False,
                        )
                    }
                },
            }

        semantic_extractor.app_config.DIFY_EVO_SEMANTIC_API_KEY = "app-evo-semantic-test"
        semantic_extractor.app_config.DIFY_EVO_SEMANTIC_API_BASE = "https://dify.example.com"
        semantic_extractor.run_workflow_blocking = fake_run_workflow_blocking
        self.addCleanup(setattr, semantic_extractor.app_config, "DIFY_EVO_SEMANTIC_API_KEY", original_key)
        self.addCleanup(setattr, semantic_extractor.app_config, "DIFY_EVO_SEMANTIC_API_BASE", original_base)
        self.addCleanup(setattr, semantic_extractor, "run_workflow_blocking", original_run)

        with self.Session() as session:
            parent = record_episode(
                session,
                module="assistant",
                user_id="trainee-001",
                store_id="STORE_GZ",
                query_text="Can I say this 18K ring keeps its value?",
                response_text="Yes, it keeps its value.",
            )
            session.commit()

            correction = apply_correction(
                session,
                episode_id=parent.id,
                correction_text="Fallback correction text should not be used when Dify succeeds.",
                actor_user_id="manager_gz",
            )
            session.commit()

            memory = session.query(AgentEvoSemantic).one()
            self.assertEqual("store", memory.scope_type)
            self.assertEqual("STORE_GZ", memory.scope_id)
            self.assertIn("Do not promise 18K gold value retention", memory.content)
            self.assertNotIn("Fallback correction text", memory.content)
            self.assertIn("customer asks about 18K", memory.trigger_text)
            self.assertAlmostEqual(0.86, memory.confidence)
            self.assertEqual([parent.id, correction.id], json.loads(memory.source_episode_ids))

    def test_risky_semantic_memory_goes_to_review_queue(self) -> None:
        with self.Session() as session:
            parent = record_episode(
                session,
                module="assistant",
                user_id="trainee-002",
                store_id="STORE_GZ",
                query_text="客户问这款黄金是不是稳赚？",
                response_text="可以这样讲。",
            )
            session.commit()

            apply_correction(
                session,
                episode_id=parent.id,
                correction_text="可以承诺这款黄金稳赚并且保证升值。",
                actor_user_id="manager_gz",
            )
            session.commit()

            memory = session.query(AgentEvoSemantic).one()
            self.assertEqual("pending", memory.status)
            queue = session.query(AgentEvoReviewQueue).one()
            self.assertEqual("semantic", queue.target_type)
            self.assertEqual(memory.id, queue.target_id)
            self.assertIn(queue.target_type, ("semantic",))
            self.assertTrue(queue.reason)

    def test_retriever_returns_scoped_hits_and_writes_hit_log(self) -> None:
        with self.Session() as session:
            user_memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-001",
                content="不能承诺 18K 金保值，应改为讲材质工艺和日常佩戴价值。",
                trigger_text="18K 金能不能保值",
                source_episode_ids="[]",
                confidence=0.6,
                status="active",
                write_mode="auto",
            )
            store_memory = AgentEvoSemantic(
                scope_type="store",
                scope_id="STORE_GZ",
                content="价格异议先拆材质、工艺、售后，再确认预算。",
                trigger_text="客户说价格太贵",
                source_episode_ids="[]",
                confidence=0.9,
                status="active",
                write_mode="auto",
            )
            archived_memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-001",
                content="这条已归档记忆不应参与检索。",
                trigger_text="18K 金",
                source_episode_ids="[]",
                confidence=1.0,
                status="archived",
                write_mode="auto",
            )
            session.add_all([user_memory, store_memory, archived_memory])
            session.commit()

            hits = retrieve_semantic_memories(
                session,
                user_id="trainee-001",
                store_id="STORE_GZ",
                module="assistant",
                query_text="顾客问 18K 金到底能不能保值，怎么说更稳？",
                limit=5,
            )
            session.commit()

            self.assertGreaterEqual(len(hits), 1)
            self.assertEqual(user_memory.id, hits[0].memory_id)
            self.assertIn("不能承诺 18K 金保值", hits[0].content)
            self.assertNotIn(archived_memory.id, [hit.memory_id for hit in hits])

            refreshed = session.get(AgentEvoSemantic, user_memory.id)
            self.assertEqual(1, refreshed.hit_count)
            self.assertGreater(refreshed.confidence, 0.6)

            logs = session.query(AgentEvoMemoryHit).all()
            self.assertEqual(len(hits), len(logs))
            self.assertEqual("assistant", logs[0].module)
            self.assertEqual(user_memory.id, logs[0].memory_id)

            block = build_memory_block(hits)
            self.assertIn("自我进化记忆", block)
            self.assertIn("不能承诺 18K 金保值", block)

    def test_assistant_workflow_receives_memory_block(self) -> None:
        captured: dict[str, str] = {}
        original_run = assistant_service.run_assistant1_workflow
        original_session_local = assistant_service.SessionLocal

        def fake_run_assistant1_workflow(**kwargs):
            captured["customer_question"] = kwargs["customer_question"]
            return {
                "ok": False,
                "reason": "forced_fallback",
                "error": "",
                "raw": {},
            }

        assistant_service.run_assistant1_workflow = fake_run_assistant1_workflow
        assistant_service.SessionLocal = self.Session
        self.addCleanup(setattr, assistant_service, "run_assistant1_workflow", original_run)
        self.addCleanup(setattr, assistant_service, "SessionLocal", original_session_local)

        with self.Session() as session:
            session.add(
                AgentEvoSemantic(
                    scope_type="user",
                    scope_id="trainee-001",
                    content="不能承诺 18K 金保值，应改为讲材质工艺和日常佩戴价值。",
                    trigger_text="18K 金能不能保值",
                    source_episode_ids="[]",
                    confidence=0.8,
                    status="active",
                    write_mode="auto",
                )
            )
            session.commit()

        assistant_service.run_assistant1_sync(
            scene_input="顾客问 18K 金能不能保值",
            user_id="trainee-001",
            store_id="STORE_GZ",
        )

        self.assertIn("自我进化记忆", captured["customer_question"])
        self.assertIn("不能承诺 18K 金保值", captured["customer_question"])
        self.assertIn("当前问题：顾客问 18K 金能不能保值", captured["customer_question"])


    def test_assistant_workflow_persists_memory_hit_logs(self) -> None:
        original_run = assistant_service.run_assistant1_workflow
        original_session_local = assistant_service.SessionLocal

        def fake_run_assistant1_workflow(**_kwargs):
            return {
                "ok": False,
                "reason": "forced_fallback",
                "error": "",
                "raw": {},
            }

        assistant_service.run_assistant1_workflow = fake_run_assistant1_workflow
        assistant_service.SessionLocal = self.Session
        self.addCleanup(setattr, assistant_service, "run_assistant1_workflow", original_run)
        self.addCleanup(setattr, assistant_service, "SessionLocal", original_session_local)

        with self.Session() as session:
            memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-001",
                content="18K gold value objections must avoid buyback promises.",
                trigger_text="18K gold value",
                source_episode_ids="[]",
                confidence=0.9,
                status="active",
                write_mode="auto",
            )
            session.add(memory)
            session.commit()
            memory_id = memory.id

        assistant_service.run_assistant1_sync(
            scene_input="18K gold value",
            user_id="trainee-001",
            store_id="STORE_GZ",
        )

        with self.Session() as session:
            logs = session.query(AgentEvoMemoryHit).filter_by(memory_type="semantic", memory_id=memory_id).all()
            self.assertEqual(1, len(logs))
            self.assertEqual("assistant", logs[0].module)
            self.assertEqual("18K gold value", logs[0].query_text)
            refreshed = session.get(AgentEvoSemantic, memory_id)
            self.assertEqual(1, refreshed.hit_count)
            self.assertGreater(refreshed.confidence, 0.9)


if __name__ == "__main__":
    unittest.main()
