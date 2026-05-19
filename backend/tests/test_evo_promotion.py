"""Hermes Route B Phase 5 cross-scope promotion tests."""

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

import auth  # noqa: E402
import database  # noqa: E402
from evo.promoter import approve_promotion, run_promotion_scan  # noqa: E402
from models import (  # noqa: E402
    AgentEvoAuditLog,
    AgentEvoEpisode,
    AgentEvoEvalCase,
    AgentEvoEvalRun,
    AgentEvoProcedural,
    AgentEvoPromotion,
    AgentEvoSemantic,
)


def _json_list(values: list[str | int]) -> str:
    return json.dumps(values, ensure_ascii=False)


class EvoPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_promotion.db"
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

    def _seed_procedural(
        self,
        session,
        *,
        scope_type: str,
        scope_id: str,
        store_id: str,
        title: str = "price objection budget first",
        hit_count: int = 2,
    ) -> AgentEvoProcedural:
        episode = AgentEvoEpisode(
            episode_type="reply",
            module="assistant",
            user_id=scope_id if scope_type == "user" else f"{scope_id}-user",
            store_id=store_id,
            query_text="customer says the ring is too expensive",
            response_text="ask budget first, then explain material and service value",
            signal="thumb_up",
            compliance_tags="[]",
        )
        session.add(episode)
        session.flush()
        row = AgentEvoProcedural(
            scope_type=scope_type,
            scope_id=scope_id,
            title=title,
            trigger_json=_json_list(["too expensive", "price objection", "budget"]),
            do_json=_json_list(["ask budget first", "explain material craft and service"]),
            dont_json=_json_list(["do not discount before understanding concern"]),
            example="When the customer says it is expensive, ask budget before discounting.",
            source_reflective_ids_json="[]",
            source_episode_ids_json=_json_list([episode.id]),
            confidence=0.78,
            status="auto",
            write_mode="auto",
            eval_case_ids_json="[]",
            hit_count=hit_count,
            last_hit_at=self.now,
        )
        session.add(row)
        session.flush()
        return row

    def test_scan_suggests_user_procedural_to_store_promotion_without_duplicates(self) -> None:
        with self.Session() as session:
            first = self._seed_procedural(session, scope_type="user", scope_id="trainee-001", store_id="STORE_GZ")
            second = self._seed_procedural(session, scope_type="user", scope_id="trainee-002", store_id="STORE_GZ")
            session.commit()

            created = run_promotion_scan(
                session,
                now=self.now,
                min_user_scopes=2,
                min_store_scopes=2,
                min_total_hits=2,
            )
            session.commit()

            self.assertEqual(1, len(created))
            promotion = created[0]
            self.assertEqual("procedural", promotion.source_memory_type)
            self.assertEqual(first.id, promotion.source_memory_id)
            self.assertEqual("user:*", promotion.current_scope)
            self.assertEqual("store:STORE_GZ", promotion.target_scope)
            self.assertEqual("pending", promotion.status)

            evidence = json.loads(promotion.evidence)
            self.assertEqual("user_procedural_to_store", evidence["proposal_type"])
            self.assertEqual([first.id, second.id], evidence["source_memory_ids"])
            self.assertEqual(["trainee-001", "trainee-002"], evidence["scope_ids"])
            self.assertEqual(4, evidence["hit_count"])

            duplicate = run_promotion_scan(
                session,
                now=self.now,
                min_user_scopes=2,
                min_store_scopes=2,
                min_total_hits=2,
            )
            session.commit()

            self.assertEqual([], duplicate)
            self.assertEqual(1, session.query(AgentEvoPromotion).count())
            audits = session.query(AgentEvoAuditLog).filter_by(action="promotion_suggest").all()
            self.assertEqual(1, len(audits))

    def test_scan_suggests_store_procedural_to_global_promotion(self) -> None:
        with self.Session() as session:
            first = self._seed_procedural(session, scope_type="store", scope_id="STORE_GZ", store_id="STORE_GZ")
            second = self._seed_procedural(session, scope_type="store", scope_id="STORE_SZ", store_id="STORE_SZ")
            session.commit()

            created = run_promotion_scan(
                session,
                now=self.now,
                min_user_scopes=2,
                min_store_scopes=2,
                min_total_hits=2,
            )
            session.commit()

            self.assertEqual(1, len(created))
            promotion = created[0]
            self.assertEqual(first.id, promotion.source_memory_id)
            self.assertEqual("store:*", promotion.current_scope)
            self.assertEqual("global:global", promotion.target_scope)
            evidence = json.loads(promotion.evidence)
            self.assertEqual("store_procedural_to_global", evidence["proposal_type"])
            self.assertEqual([first.id, second.id], evidence["source_memory_ids"])
            self.assertEqual(["STORE_GZ", "STORE_SZ"], evidence["scope_ids"])

    def test_scan_suggests_same_scope_semantic_merge(self) -> None:
        with self.Session() as session:
            first = AgentEvoSemantic(
                scope_type="store",
                scope_id="STORE_GZ",
                content="18K gold should be described by material and wearing value, not guaranteed appreciation.",
                trigger_text="can 18K gold preserve value",
                source_episode_ids="[]",
                confidence=0.68,
                status="active",
                write_mode="auto",
            )
            second = AgentEvoSemantic(
                scope_type="store",
                scope_id="STORE_GZ",
                content="18K gold is explained through material, craft and daily wearing value, never as guaranteed appreciation.",
                trigger_text="18K value preservation",
                source_episode_ids="[]",
                confidence=0.72,
                status="active",
                write_mode="auto",
            )
            session.add_all([first, second])
            session.commit()

            created = run_promotion_scan(
                session,
                now=self.now,
                min_user_scopes=2,
                min_store_scopes=2,
                min_total_hits=2,
                semantic_similarity_threshold=0.55,
            )
            session.commit()

            self.assertEqual(1, len(created))
            promotion = created[0]
            self.assertEqual("semantic_merge", promotion.source_memory_type)
            self.assertEqual(first.id, promotion.source_memory_id)
            self.assertEqual("store:STORE_GZ", promotion.current_scope)
            self.assertEqual("store:STORE_GZ", promotion.target_scope)
            evidence = json.loads(promotion.evidence)
            self.assertEqual("semantic_merge", evidence["proposal_type"])
            self.assertEqual([first.id, second.id], evidence["source_memory_ids"])

    def test_approval_writes_target_scope_procedural_rule(self) -> None:
        with self.Session() as session:
            first = self._seed_procedural(session, scope_type="user", scope_id="trainee-001", store_id="STORE_GZ")
            second = self._seed_procedural(session, scope_type="user", scope_id="trainee-002", store_id="STORE_GZ")
            session.commit()

            promotion = run_promotion_scan(
                session,
                now=self.now,
                min_user_scopes=2,
                min_store_scopes=2,
                min_total_hits=2,
            )[0]
            session.commit()

            approved, target = approve_promotion(session, promotion.id, decided_by="manager_gz", now=self.now)
            session.commit()

            self.assertEqual("approved", approved.status)
            self.assertEqual("manager_gz", approved.decided_by)
            self.assertIsNotNone(target)
            self.assertEqual("store", target.scope_type)
            self.assertEqual("STORE_GZ", target.scope_id)
            self.assertEqual("active", target.status)
            self.assertEqual("human", target.write_mode)
            self.assertEqual([first.id, second.id], json.loads(approved.evidence)["source_memory_ids"])
            self.assertEqual(2, len(json.loads(target.source_episode_ids_json)))
            actions = [row.action for row in session.query(AgentEvoAuditLog).all()]
            self.assertIn("procedural_promote", actions)
            self.assertIn("promotion_approve", actions)


class EvoPromotionRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "evo_promotion_router.db"
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

    def _seed_procedural(self, session, **kwargs) -> AgentEvoProcedural:
        return EvoPromotionTests._seed_procedural(self, session, **kwargs)

    def test_promotion_list_endpoint_returns_pending_items(self) -> None:
        with self.Session() as session:
            session.add(
                AgentEvoPromotion(
                    source_memory_type="procedural",
                    source_memory_id=1,
                    current_scope="user:*",
                    target_scope="store:STORE_GZ",
                    reason="test promotion",
                    evidence=json.dumps({"proposal_type": "user_procedural_to_store"}, ensure_ascii=False),
                    status="pending",
                )
            )
            session.commit()

        response = self.client.get("/api/evo/promotions")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(200, payload["code"])
        self.assertEqual(1, len(payload["data"]["promotions"]))
        self.assertEqual("store:STORE_GZ", payload["data"]["promotions"][0]["target_scope"])
        self.assertEqual("not_run", payload["data"]["promotions"][0]["preflight"]["status"])

    def test_promotion_list_exposes_preflight_badge_states(self) -> None:
        with self.Session() as session:
            not_run = AgentEvoPromotion(
                source_memory_type="procedural",
                source_memory_id=1,
                current_scope="store:*",
                target_scope="global:global",
                reason="not run",
                evidence=json.dumps({"proposal_type": "store_procedural_to_global"}, ensure_ascii=False),
                status="pending",
            )
            passed = AgentEvoPromotion(
                source_memory_type="procedural",
                source_memory_id=2,
                current_scope="store:*",
                target_scope="global:global",
                reason="passed",
                evidence=json.dumps({"proposal_type": "store_procedural_to_global"}, ensure_ascii=False),
                status="pending",
            )
            blocked = AgentEvoPromotion(
                source_memory_type="procedural",
                source_memory_id=3,
                current_scope="store:*",
                target_scope="global:global",
                reason="blocked",
                evidence=json.dumps({"proposal_type": "store_procedural_to_global"}, ensure_ascii=False),
                status="blocked",
            )
            missing = AgentEvoPromotion(
                source_memory_type="procedural",
                source_memory_id=4,
                current_scope="store:*",
                target_scope="global:global",
                reason="missing cases",
                evidence=json.dumps(
                    {
                        "proposal_type": "store_procedural_to_global",
                        "global_preflight": {"status": "blocked", "reason": "missing_eval_cases"},
                    },
                    ensure_ascii=False,
                ),
                status="blocked",
            )
            session.add_all([not_run, passed, blocked, missing])
            session.flush()
            session.add_all(
                [
                    AgentEvoEvalRun(
                        case_id=11,
                        module="assistant",
                        question="passed case",
                        status="passed",
                        failed_checks=_json_list([]),
                        bound_memory_ids=_json_list([]),
                        triggered_by=f"promotion:{passed.id}",
                        created_at=self.now,
                    ),
                    AgentEvoEvalRun(
                        case_id=12,
                        module="assistant",
                        question="failed case",
                        status="failed",
                        failed_checks=_json_list(["must_not_contain"]),
                        bound_memory_ids=_json_list([]),
                        triggered_by=f"promotion:{blocked.id}",
                        created_at=self.now,
                    ),
                ]
            )
            session.commit()

        response = self.client.get("/api/evo/promotions?status=&limit=20")
        self.assertEqual(200, response.status_code)
        promotions = {row["reason"]: row["preflight"] for row in response.json()["data"]["promotions"]}
        self.assertEqual("not_run", promotions["not run"]["status"])
        self.assertEqual("passed", promotions["passed"]["status"])
        self.assertEqual(1, promotions["passed"]["run_count"])
        self.assertEqual("blocked", promotions["blocked"]["status"])
        self.assertEqual(1, promotions["blocked"]["failed_count"])
        self.assertEqual("missing_cases", promotions["missing cases"]["status"])
        self.assertEqual("缺用例", promotions["missing cases"]["label"])

    def test_empty_promotion_scan_returns_real_pipeline_diagnostics(self) -> None:
        response = self.client.post("/api/evo/promotions/scan")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertEqual(0, data["created_count"])
        self.assertEqual("not_ready", data["summary"]["status"])
        self.assertEqual(0, data["diagnostics"]["procedural_count"])
        self.assertEqual(0, data["diagnostics"]["memory_hit_count"])
        self.assertIn("no_active_procedural", data["diagnostics"]["reasons"])

    def test_promotion_scan_exposes_candidate_preview_when_real_data_is_ready(self) -> None:
        with self.Session() as session:
            first = self._seed_procedural(session, scope_type="store", scope_id="STORE_GZ", store_id="STORE_GZ", hit_count=3)
            second = self._seed_procedural(session, scope_type="store", scope_id="STORE_SZ", store_id="STORE_SZ", hit_count=3)
            case = AgentEvoEvalCase(
                module="assistant",
                question="Can this rule promise appreciation?",
                must_contain=_json_list([]),
                must_not_contain=_json_list(["guaranteed appreciation"]),
                scope_type="global",
                scope_id="",
                severity=3,
                source="baseline",
                bound_memory_ids="[]",
                status="active",
            )
            session.add(case)
            session.commit()

        response = self.client.post("/api/evo/promotions/scan")

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        preview = data["diagnostics"].get("candidate_preview") or []
        self.assertGreaterEqual(len(preview), 1)
        first_preview = preview[0]
        self.assertIn("source_memory_ids", first_preview)
        self.assertIn(first.id, first_preview["source_memory_ids"])
        self.assertEqual("store_procedural_to_global", first_preview["proposal_type"])
        self.assertEqual("baseline", first_preview["required_eval_cases"][0]["source"])


if __name__ == "__main__":
    unittest.main()
