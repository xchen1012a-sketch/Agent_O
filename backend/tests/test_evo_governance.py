"""Hermes Route B Phase 7 governance API tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from models import (  # noqa: E402
    AgentEvoAnomaly,
    AgentEvoAuditLog,
    AgentEvoEpisode,
    AgentEvoEvalCase,
    AgentEvoEvalRun,
    AgentEvoMemoryHit,
    AgentEvoProcedural,
    AgentEvoPromotion,
    AgentEvoReflective,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class EvoGovernanceRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_governance.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self.now = datetime.now(timezone.utc)

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

    def _seed_memory_graph(self) -> dict[str, int]:
        with self.Session() as session:
            episode = AgentEvoEpisode(
                episode_type="reply",
                module="assistant",
                user_id="trainee-001",
                store_id="STORE_GZ",
                query_text="18K 金能不能保值？",
                response_text="不能承诺保值，要讲材质、工艺和佩戴价值。",
                signal="thumb_up",
                compliance_tags="[]",
                created_at=self.now,
            )
            session.add(episode)
            session.flush()

            semantic = AgentEvoSemantic(
                scope_type="store",
                scope_id="STORE_GZ",
                content="18K 金不能承诺保值，应从材质、工艺与佩戴价值解释。",
                trigger_text="18K 金能不能保值",
                source_episode_ids=_json([episode.id]),
                confidence=0.7,
                status="active",
                write_mode="auto",
                hit_count=2,
                last_hit_at=self.now,
                created_at=self.now,
            )
            reflective = AgentEvoReflective(
                scope_type="user",
                scope_id="trainee-001",
                lesson="遇到保值问题时，先规避承诺，再解释长期佩戴价值。",
                evidence_episode_ids=_json([episode.id]),
                confidence=0.62,
                hit_count=1,
                status="active",
                created_at=self.now,
                expires_at=self.now + timedelta(days=30),
            )
            session.add_all([semantic, reflective])
            session.flush()

            procedural = AgentEvoProcedural(
                scope_type="store",
                scope_id="STORE_GZ",
                title="保值问题标准应答",
                trigger_json=_json(["保值", "升值", "18K 金"]),
                do_json=_json(["先规避承诺", "解释材质工艺和佩戴价值"]),
                dont_json=_json(["不要承诺回购或升值"]),
                example="这类问题不能承诺保值，可以说明材质、工艺和售后保障。",
                source_reflective_ids_json=_json([reflective.id]),
                source_episode_ids_json=_json([episode.id]),
                confidence=0.82,
                status="auto",
                write_mode="auto",
                eval_case_ids_json="[]",
                hit_count=3,
                created_at=self.now,
            )
            session.add(procedural)
            session.flush()

            session.add_all(
                [
                    AgentEvoMemoryHit(
                        memory_type="semantic",
                        memory_id=semantic.id,
                        user_id="trainee-001",
                        module="assistant",
                        query_text="顾客问 18K 保值怎么说",
                        score=0.91,
                        created_at=self.now,
                    ),
                    AgentEvoMemoryHit(
                        memory_type="procedural",
                        memory_id=procedural.id,
                        user_id="trainee-002",
                        module="assistant",
                        query_text="保值问题",
                        score=0.88,
                        created_at=self.now,
                    ),
                    AgentEvoReviewQueue(
                        target_type="semantic",
                        target_id=semantic.id,
                        reason="命中高风险词：保值",
                        priority=3,
                        status="pending",
                        created_at=self.now,
                    ),
                    AgentEvoPromotion(
                        source_memory_type="procedural",
                        source_memory_id=procedural.id,
                        current_scope="store:STORE_GZ",
                        target_scope="global:global",
                        reason="多门店复用",
                        evidence=_json({"source_memory_ids": [procedural.id]}),
                        status="pending",
                        suggested_at=self.now,
                    ),
                    AgentEvoEvalCase(
                        module="assistant",
                        question="18K 金是否保值？",
                        must_contain=_json(["不能承诺"]),
                        must_not_contain=_json(["保证升值"]),
                        bound_memory_ids=_json([{"type": "semantic", "id": semantic.id}]),
                        severity=3,
                        status="active",
                        created_at=self.now,
                    ),
                    AgentEvoEvalRun(
                        case_id=1,
                        module="assistant",
                        question="18K 金是否保值？",
                        answer_text="不能承诺保值。",
                        status="failed",
                        failed_checks=_json(["must_not_contain"]),
                        bound_memory_ids=_json([{"type": "semantic", "id": semantic.id}]),
                        created_at=self.now,
                    ),
                    AgentEvoAnomaly(
                        anomaly_type="negative_feedback_spike",
                        target_type="semantic",
                        target_id=str(semantic.id),
                        severity=3,
                        status="open",
                        reason="命中后负反馈突增",
                        evidence=_json({"negative_rate": 0.4}),
                        created_at=self.now,
                    ),
                    AgentEvoAuditLog(
                        actor="system",
                        action="semantic_write",
                        target_type="semantic",
                        target_id=str(semantic.id),
                        payload=_json({"source_episode_ids": [episode.id]}),
                        created_at=self.now,
                    ),
                ]
            )
            session.commit()
            return {
                "episode": episode.id,
                "semantic": semantic.id,
                "reflective": reflective.id,
                "procedural": procedural.id,
            }

    def test_governance_overview_returns_phase7_counts(self) -> None:
        self._seed_memory_graph()

        response = self.client.get("/api/evo/governance/overview")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertGreaterEqual(data["today_auto_writes"], 3)
        self.assertEqual(2, data["today_memory_hits"])
        self.assertEqual(1, data["pending_review_count"])
        self.assertEqual(1, data["open_anomaly_count"])
        self.assertEqual(1, data["pending_promotion_count"])
        self.assertEqual(1, data["today_eval_failed_count"])
        self.assertEqual(1, data["memory_totals"]["semantic"]["active"])
        self.assertEqual(1, data["memory_totals"]["procedural"]["active"])
        self.assertEqual("negative_feedback_spike", data["recent_anomalies"][0]["anomaly_type"])
        self.assertEqual(1, data["data_health"]["procedural_count"])
        self.assertEqual(2, data["data_health"]["memory_hit_count"])
        self.assertEqual("ready", data["data_health"]["promotion_readiness"]["status"])
        self.assertEqual("ready", data["data_health"]["safety_readiness"]["status"])

    def test_governance_overview_hides_global_pending_reviews_for_store_manager(self) -> None:
        ids = self._seed_memory_graph()
        with self.Session() as session:
            global_memory = AgentEvoSemantic(
                scope_type="global",
                scope_id="global",
                content="Global compliance memory",
                trigger_text="global compliance",
                source_episode_ids="[]",
                confidence=0.95,
                status="pending",
                write_mode="human",
                created_at=self.now,
            )
            session.add(global_memory)
            session.flush()
            session.add(
                AgentEvoReviewQueue(
                    target_type="semantic",
                    target_id=global_memory.id,
                    reason="global review item",
                    priority=5,
                    status="pending",
                    created_at=self.now,
                )
            )
            session.commit()

        response = self.client.get("/api/evo/governance/overview")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        pending_reviews = data.get("pending_reviews") or []
        self.assertEqual(1, data["pending_review_count"])
        self.assertEqual(1, len(pending_reviews))
        self.assertEqual(ids["semantic"], pending_reviews[0]["target_id"])
        self.assertTrue(all(item["memory"]["scope_type"] != "global" for item in pending_reviews if item.get("memory")))

    def test_governance_overview_explains_empty_governance_pipeline(self) -> None:
        response = self.client.get("/api/evo/governance/overview")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(0, data["data_health"]["procedural_count"])
        self.assertEqual(0, data["data_health"]["memory_hit_count"])
        self.assertEqual("not_ready", data["data_health"]["promotion_readiness"]["status"])
        self.assertIn("no_active_procedural", data["data_health"]["promotion_readiness"]["reasons"])
        self.assertEqual("not_ready", data["data_health"]["safety_readiness"]["status"])
        self.assertIn("no_eval_cases", data["data_health"]["safety_readiness"]["reasons"])

    def test_memory_list_filters_by_type_scope_and_query(self) -> None:
        ids = self._seed_memory_graph()

        response = self.client.get(
            "/api/evo/memories",
            params={"memory_type": "semantic", "scope_type": "store", "q": "18K"},
        )

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(1, data["total"])
        self.assertEqual(ids["semantic"], data["items"][0]["id"])
        self.assertEqual("semantic", data["items"][0]["memory_type"])

    def test_store_manager_memory_list_excludes_global_scope(self) -> None:
        self._seed_memory_graph()
        with self.Session() as session:
            session.add(
                AgentEvoSemantic(
                    scope_type="global",
                    scope_id="global",
                    content="Global compliance memory",
                    trigger_text="global compliance",
                    source_episode_ids="[]",
                    confidence=0.9,
                    status="active",
                    write_mode="auto",
                    created_at=self.now,
                )
            )
            session.commit()

        response = self.client.get("/api/evo/memories", params={"memory_type": "semantic"})

        self.assertEqual(200, response.status_code)
        items = response.json()["data"]["items"]
        self.assertGreaterEqual(len(items), 1)
        self.assertNotIn("global", {item["scope_type"] for item in items})

    def test_store_manager_memory_list_rejects_global_scope_filter(self) -> None:
        self._seed_memory_graph()

        response = self.client.get(
            "/api/evo/memories",
            params={"memory_type": "semantic", "scope_type": "global"},
        )

        self.assertEqual(403, response.status_code)

    def test_feedback_event_list_pairs_correction_and_linked_memory(self) -> None:
        ids = self._seed_memory_graph()
        with self.Session() as session:
            parent = session.get(AgentEvoEpisode, ids["episode"])
            parent.signal = "correction"
            correction = AgentEvoEpisode(
                episode_type="correction",
                module=parent.module,
                user_id=parent.user_id,
                store_id=parent.store_id,
                query_text=parent.query_text,
                response_text=parent.response_text,
                signal="correction",
                correction_text="Do not promise resale value; explain craft and wearing value.",
                parent_episode_id=parent.id,
                created_at=self.now + timedelta(minutes=1),
            )
            session.add(correction)
            session.flush()
            memory = session.get(AgentEvoSemantic, ids["semantic"])
            memory.source_episode_ids = _json([parent.id, correction.id])
            session.commit()

        response = self.client.get("/api/evo/feedback-events", params={"signal": "correction"})

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(1, data["total"])
        item = data["items"][0]
        self.assertEqual(ids["episode"], item["episode"]["id"])
        self.assertEqual("correction", item["signal"])
        self.assertEqual("Do not promise resale value; explain craft and wearing value.", item["feedback_text"])
        self.assertEqual(ids["episode"], item["correction"]["parent_episode_id"])
        self.assertGreaterEqual(item["linked_memory_count"], 1)
        self.assertIn(
            ("semantic", ids["semantic"]),
            {(ref["memory_type"], ref["id"]) for ref in item["linked_memories"]},
        )

    def test_memory_detail_returns_sources_hits_and_derived_path(self) -> None:
        ids = self._seed_memory_graph()

        response = self.client.get(f"/api/evo/memories/semantic/{ids['semantic']}")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(ids["semantic"], data["memory"]["id"])
        self.assertEqual([ids["episode"]], data["memory"]["source_episode_ids"])
        self.assertEqual(1, len(data["source_episodes"]))
        self.assertEqual("thumb_up", data["source_episodes"][0]["signal"])
        self.assertIn("response_text", data["source_episodes"][0])
        self.assertIn("parent_episode_id", data["source_episodes"][0])
        self.assertEqual(1, len(data["hit_history"]["recent_hits"]))
        self.assertEqual(1, len(data["hit_history"]["daily"]))
        self.assertEqual(1, len(data["derived_path"]["eval_cases"]))
        self.assertEqual(1, len(data["derived_path"]["anomalies"]))
        self.assertEqual(1, len(data["derived_path"]["review_queue"]))

    def test_reflective_memory_detail_returns_procedural_children(self) -> None:
        ids = self._seed_memory_graph()

        response = self.client.get(f"/api/evo/memories/reflective/{ids['reflective']}")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(ids["reflective"], data["memory"]["id"])
        self.assertIn(
            ids["procedural"],
            [item["id"] for item in data["derived_path"]["procedural_children"]],
        )

    def test_memory_update_changes_confidence_and_writes_audit(self) -> None:
        ids = self._seed_memory_graph()

        response = self.client.patch(
            f"/api/evo/memories/semantic/{ids['semantic']}",
            json={"confidence": 0.45, "status": "auto_disabled"},
        )

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual("auto_disabled", data["memory"]["status"])
        self.assertEqual(0.45, data["memory"]["confidence"])

        with self.Session() as session:
            memory = session.get(AgentEvoSemantic, ids["semantic"])
            self.assertEqual("auto_disabled", memory.status)
            self.assertEqual(0.45, memory.confidence)
            audit = session.query(AgentEvoAuditLog).filter_by(action="memory_update").one()
            self.assertEqual("semantic", audit.target_type)

    def test_review_queue_list_and_approve_online_memory(self) -> None:
        ids = self._seed_memory_graph()
        with self.Session() as session:
            memory = session.get(AgentEvoSemantic, ids["semantic"])
            memory.status = "pending"
            queue = session.query(AgentEvoReviewQueue).filter_by(target_type="semantic", target_id=ids["semantic"]).one()
            queue.status = "pending"
            session.commit()
            queue_id = queue.id

        list_response = self.client.get("/api/evo/review-queue")
        self.assertEqual(200, list_response.status_code)
        items = list_response.json()["data"]["items"]
        self.assertEqual(1, len(items))
        self.assertEqual(ids["semantic"], items[0]["target_id"])
        self.assertEqual("pending", items[0]["memory"]["status"])

        approve_response = self.client.post(
            f"/api/evo/review-queue/{queue_id}/decision",
            json={"decision": "approve"},
        )
        self.assertEqual(200, approve_response.status_code)
        data = approve_response.json()["data"]
        self.assertEqual("approved", data["review"]["status"])
        self.assertEqual("active", data["memory"]["status"])

        with self.Session() as session:
            self.assertEqual("active", session.get(AgentEvoSemantic, ids["semantic"]).status)
            self.assertEqual("approved", session.get(AgentEvoReviewQueue, queue_id).status)
            audit = session.query(AgentEvoAuditLog).filter_by(action="review_approved").one()
            self.assertEqual("semantic", audit.target_type)

    def test_review_queue_reject_archives_candidate_memory(self) -> None:
        ids = self._seed_memory_graph()
        with self.Session() as session:
            memory = session.get(AgentEvoSemantic, ids["semantic"])
            memory.status = "pending"
            queue = session.query(AgentEvoReviewQueue).filter_by(target_type="semantic", target_id=ids["semantic"]).one()
            queue.status = "pending"
            session.commit()
            queue_id = queue.id

        response = self.client.post(
            f"/api/evo/review-queue/{queue_id}/decision",
            json={"decision": "reject"},
        )
        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual("rejected", data["review"]["status"])
        self.assertEqual("archived", data["memory"]["status"])

        with self.Session() as session:
            self.assertEqual("archived", session.get(AgentEvoSemantic, ids["semantic"]).status)
            self.assertEqual("rejected", session.get(AgentEvoReviewQueue, queue_id).status)

    def test_rollback_archives_memory_children_and_pending_promotions(self) -> None:
        ids = self._seed_memory_graph()

        response = self.client.post(
            f"/api/evo/memories/reflective/{ids['reflective']}/rollback",
            json={"reason": "反思证据不足"},
        )

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        archived_refs = {(item["memory_type"], item["id"]) for item in data["archived"]}
        self.assertIn(("reflective", ids["reflective"]), archived_refs)
        self.assertIn(("procedural", ids["procedural"]), archived_refs)

        with self.Session() as session:
            reflective = session.get(AgentEvoReflective, ids["reflective"])
            procedural = session.get(AgentEvoProcedural, ids["procedural"])
            promotion = session.query(AgentEvoPromotion).one()
            self.assertEqual("archived", reflective.status)
            self.assertEqual("archived", procedural.status)
            self.assertEqual("rejected", promotion.status)
            audit = session.query(AgentEvoAuditLog).filter_by(action="memory_rollback").one()
            self.assertEqual("reflective", audit.target_type)


if __name__ == "__main__":
    unittest.main()
