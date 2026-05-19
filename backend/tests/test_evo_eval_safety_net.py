"""Hermes Route B Phase 6 regression and anomaly safety-net tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
import evo.eval_runner as eval_runner  # noqa: E402
from evo.anomaly_detector import run_anomaly_scan  # noqa: E402
from evo.eval_runner import run_eval_cases  # noqa: E402
from evo.promoter import approve_promotion  # noqa: E402
from evo.retriever import retrieve_semantic_memories  # noqa: E402
from models import (  # noqa: E402
    AgentEvoAnomaly,
    AgentEvoAuditLog,
    AgentEvoEvalCase,
    AgentEvoEvalRun,
    AgentEvoProcedural,
    AgentEvoPromotion,
    AgentEvoReviewQueue,
    AgentEvoSemantic,
)
import auth  # noqa: E402


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class EvoEvalSafetyNetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_eval_safety.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_failed_eval_quarantines_bound_memory_and_records_anomaly(self) -> None:
        with self.Session() as session:
            memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-001",
                content="18K gold can be described as guaranteed appreciation.",
                trigger_text="18K value preservation",
                source_episode_ids="[]",
                confidence=0.8,
                status="active",
                write_mode="auto",
            )
            session.add(memory)
            session.flush()
            case = AgentEvoEvalCase(
                module="assistant",
                question="Can 18K gold preserve value?",
                must_contain="[]",
                must_not_contain=_json(["guaranteed appreciation"]),
                scope_type="user",
                scope_id="trainee-001",
                severity=3,
                source="manual",
                bound_memory_ids=_json([{"type": "semantic", "id": memory.id}]),
                status="active",
            )
            session.add(case)
            session.commit()

            runs = run_eval_cases(
                session,
                answer_provider=lambda _case: "Yes, promise guaranteed appreciation to close the sale.",
                triggered_by="test",
                now=self.now,
            )
            session.commit()

            self.assertEqual(1, len(runs))
            self.assertEqual("failed", runs[0].status)
            self.assertEqual("quarantined", session.get(AgentEvoSemantic, memory.id).status)
            anomaly = session.query(AgentEvoAnomaly).one()
            self.assertEqual("eval_case_failed", anomaly.anomaly_type)
            self.assertEqual(3, anomaly.severity)
            actions = [row.action for row in session.query(AgentEvoAuditLog).all()]
            self.assertIn("memory_quarantine", actions)
            self.assertIn("eval_case_fail", actions)

            hits = retrieve_semantic_memories(
                session,
                user_id="trainee-001",
                store_id="STORE_GZ",
                module="assistant",
                query_text="18K value preservation",
                write_hits=False,
            )
            self.assertEqual([], hits)

    def test_passing_eval_keeps_bound_memory_active(self) -> None:
        with self.Session() as session:
            memory = AgentEvoSemantic(
                scope_type="user",
                scope_id="trainee-002",
                content="Do not promise appreciation; explain material and wearing value.",
                trigger_text="18K value preservation",
                source_episode_ids="[]",
                confidence=0.8,
                status="active",
                write_mode="auto",
            )
            session.add(memory)
            session.flush()
            session.add(
                AgentEvoEvalCase(
                    module="assistant",
                    question="Can 18K gold preserve value?",
                    must_contain=_json(["material"]),
                    must_not_contain=_json(["guaranteed appreciation"]),
                    scope_type="user",
                    scope_id="trainee-002",
                    severity=3,
                    source="manual",
                    bound_memory_ids=_json([{"type": "semantic", "id": memory.id}]),
                    status="active",
                )
            )
            session.commit()

            runs = run_eval_cases(
                session,
                answer_provider=lambda _case: "Do not promise returns; explain material and wearing value.",
                triggered_by="test",
                now=self.now,
            )
            session.commit()

            self.assertEqual("passed", runs[0].status)
            self.assertEqual("active", session.get(AgentEvoSemantic, memory.id).status)
            self.assertEqual(0, session.query(AgentEvoAnomaly).count())

    def test_anomaly_scan_moves_conflicting_semantic_cluster_to_review(self) -> None:
        with self.Session() as session:
            rows = [
                AgentEvoSemantic(
                    scope_type="store",
                    scope_id="STORE_GZ",
                    content="Can promise guaranteed appreciation for 18K gold.",
                    trigger_text="18K value preservation",
                    source_episode_ids="[]",
                    confidence=0.7,
                    status="active",
                    write_mode="auto",
                    created_at=self.now,
                ),
                AgentEvoSemantic(
                    scope_type="store",
                    scope_id="STORE_GZ",
                    content="Do not promise guaranteed appreciation for 18K gold.",
                    trigger_text="18K value preservation",
                    source_episode_ids="[]",
                    confidence=0.7,
                    status="active",
                    write_mode="auto",
                    created_at=self.now,
                ),
                AgentEvoSemantic(
                    scope_type="store",
                    scope_id="STORE_GZ",
                    content="Can guarantee appreciation to close the sale.",
                    trigger_text="18K value preservation",
                    source_episode_ids="[]",
                    confidence=0.7,
                    status="active",
                    write_mode="auto",
                    created_at=self.now,
                ),
            ]
            session.add_all(rows)
            session.commit()

            anomalies = run_anomaly_scan(session, now=self.now, min_conflict_cluster_size=3)
            session.commit()

            self.assertEqual(1, len(anomalies))
            self.assertEqual("semantic_trigger_conflict", anomalies[0].anomaly_type)
            self.assertEqual(3, session.query(AgentEvoReviewQueue).count())
            statuses = [session.get(AgentEvoSemantic, row.id).status for row in rows]
            self.assertEqual(["pending", "pending", "pending"], statuses)

    def test_global_promotion_is_blocked_when_bound_eval_fails(self) -> None:
        original_run_eval_cases = eval_runner.run_eval_cases

        def fake_run_eval_cases(session, **kwargs):
            return original_run_eval_cases(
                session,
                case_ids=kwargs.get("case_ids"),
                answer_provider=lambda _case: "Promise guaranteed appreciation.",
                triggered_by=kwargs.get("triggered_by", "test"),
                now=kwargs.get("now"),
            )

        eval_runner.run_eval_cases = fake_run_eval_cases
        self.addCleanup(setattr, eval_runner, "run_eval_cases", original_run_eval_cases)

        with self.Session() as session:
            case = AgentEvoEvalCase(
                module="assistant",
                question="Can this rule promise appreciation?",
                must_contain="[]",
                must_not_contain=_json(["guaranteed appreciation"]),
                scope_type="global",
                scope_id="",
                severity=3,
                source="manual",
                bound_memory_ids="[]",
                status="active",
            )
            session.add(case)
            session.flush()
            source = AgentEvoProcedural(
                scope_type="store",
                scope_id="STORE_GZ",
                title="Value preservation script",
                trigger_json=_json(["value preservation"]),
                do_json=_json(["explain material value"]),
                dont_json=_json(["do not promise returns"]),
                example="",
                source_reflective_ids_json="[]",
                source_episode_ids_json="[]",
                confidence=0.8,
                status="auto",
                write_mode="auto",
                eval_case_ids_json=_json([case.id]),
                hit_count=4,
            )
            session.add(source)
            session.flush()
            promotion = AgentEvoPromotion(
                source_memory_type="procedural",
                source_memory_id=source.id,
                current_scope="store:*",
                target_scope="global:global",
                reason="test global promotion",
                evidence=_json(
                    {
                        "proposal_type": "store_procedural_to_global",
                        "source_memory_ids": [source.id],
                    }
                ),
                status="pending",
            )
            session.add(promotion)
            session.commit()

            blocked, target = approve_promotion(session, promotion.id, decided_by="manager_gz", now=self.now)
            session.commit()

            self.assertIsNone(target)
            self.assertEqual("blocked", blocked.status)
            self.assertEqual("quarantined", session.get(AgentEvoProcedural, source.id).status)
            self.assertEqual(1, session.query(AgentEvoEvalRun).count())
            self.assertEqual(1, session.query(AgentEvoAnomaly).count())


class EvoEvalSafetyNetRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_eval_safety_router.db"
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

        import routers.evo as evo_router  # noqa: WPS433
        self.evo_router = evo_router

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

    def test_eval_run_endpoint_returns_run_summary(self) -> None:
        original_run_eval_cases = self.evo_router.run_eval_cases

        def fake_run_eval_cases(session, **_kwargs):
            case = session.query(AgentEvoEvalCase).one()
            row = AgentEvoEvalRun(
                case_id=case.id,
                module=case.module,
                scope_type=case.scope_type,
                scope_id=case.scope_id,
                question=case.question,
                answer_text="Do not promise returns; explain material and wearing value.",
                status="passed",
                failed_checks="[]",
                bound_memory_ids="[]",
                triggered_by="test",
            )
            session.add(row)
            session.flush()
            return [row]

        self.evo_router.run_eval_cases = fake_run_eval_cases
        self.addCleanup(setattr, self.evo_router, "run_eval_cases", original_run_eval_cases)

        with self.Session() as session:
            session.add(
                AgentEvoEvalCase(
                    module="assistant",
                    question="Can 18K gold preserve value?",
                    must_contain="[]",
                    must_not_contain=_json(["guaranteed appreciation"]),
                    severity=3,
                    source="manual",
                    status="active",
                )
            )
            session.commit()

        response = self.client.post("/api/evo/eval-runs", json={"case_ids": [], "module": ""})

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(data["run_count"], data["summary"]["run_count"])
        self.assertEqual(1, data["summary"]["run_count"])
        self.assertEqual(1, data["summary"]["failed_count"] + data["summary"]["passed_count"] + data["summary"]["error_count"])
        self.assertEqual(data["failed_count"], data["summary"]["failed_count"] + data["summary"]["error_count"])
        self.assertIn("anomaly_count", data["summary"])

    def test_eval_case_list_exposes_source_and_filters_by_bound_memory(self) -> None:
        with self.Session() as session:
            source_memory = AgentEvoSemantic(
                scope_type="store",
                scope_id="STORE_GZ",
                content="Do not promise guaranteed appreciation.",
                trigger_text="18K value preservation",
                source_episode_ids="[]",
                confidence=0.8,
                status="active",
                write_mode="auto",
            )
            session.add(source_memory)
            session.flush()
            session.add_all(
                [
                    AgentEvoEvalCase(
                        module="assistant",
                        question="Can 18K gold preserve value?",
                        must_contain=_json(["material"]),
                        must_not_contain=_json(["guaranteed appreciation"]),
                        scope_type="global",
                        scope_id="",
                        severity=3,
                        source="baseline",
                        bound_memory_ids=_json([{"type": "semantic", "id": source_memory.id}]),
                        status="active",
                    ),
                    AgentEvoEvalCase(
                        module="qa",
                        question="Can 18K gold preserve value?",
                        must_contain=_json(["material"]),
                        must_not_contain=_json(["guaranteed appreciation"]),
                        scope_type="global",
                        scope_id="",
                        severity=2,
                        source="manual",
                        bound_memory_ids="[]",
                        status="active",
                    ),
                ]
            )
            session.commit()

        response = self.client.get(
            "/api/evo/eval-cases",
            params={"status": "active", "source": "baseline", "bound_memory_type": "semantic", "bound_memory_id": source_memory.id},
        )

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(1, data["eval_cases"][0]["id"] if data["eval_cases"] else 0)
        self.assertEqual("baseline", data["eval_cases"][0]["source"])
        self.assertEqual([{"type": "semantic", "id": source_memory.id}], data["eval_cases"][0]["bound_memory_ids"])

    def test_seed_default_eval_cases_marks_baseline_source(self) -> None:
        response = self.client.post("/api/evo/eval-cases/seed-defaults")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertGreater(data["created_count"], 0)
        self.assertTrue(all(item["source"] == "baseline" for item in data["eval_cases"]))

    def test_empty_anomaly_scan_returns_real_pipeline_diagnostics(self) -> None:
        response = self.client.post("/api/evo/anomalies/scan")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(0, data["created_count"])
        self.assertEqual("not_ready", data["summary"]["status"])
        self.assertEqual(0, data["diagnostics"]["active_semantic_count"])
        self.assertEqual(0, data["diagnostics"]["memory_hit_count"])
        self.assertIn("no_active_semantic", data["diagnostics"]["reasons"])
        self.assertIn("no_memory_hits", data["diagnostics"]["reasons"])


if __name__ == "__main__":
    unittest.main()
