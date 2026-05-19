from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from shutil import copy2

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from review_notebook_service import aggregate_review_notebook
import seed_demo_data


class SeedDemoDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmpdir.cleanup)
        self.temp_db = Path(self.tmpdir.name) / "jewelry_qipei_test.db"
        copy2(BACKEND_DIR / "jewelry_qipei.db", self.temp_db)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.temp_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def test_seed_and_delete_demo_data(self) -> None:
        with self._conn() as conn:
            admin_before = conn.execute(
                "SELECT id, username, hashed_password FROM users WHERE username = 'admin' LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(admin_before)

        seed_summary = seed_demo_data.seed_demo_data(self.temp_db)
        self.assertEqual(seed_summary["inserted"]["stores"], 5)
        self.assertEqual(seed_summary["inserted"]["users"], len(seed_demo_data.DEMO_USERS))
        self.assertEqual(seed_summary["inserted"]["employee_profiles"], len(seed_demo_data.DEMO_USERS))
        self.assertGreaterEqual(seed_summary["inserted"]["growth_plan_records"], 15)
        self.assertGreaterEqual(seed_summary["inserted"]["training_cycles"], 15)
        self.assertGreaterEqual(seed_summary["inserted"]["cycle_daily_tasks"], 80)
        self.assertGreaterEqual(seed_summary["inserted"]["practice_records"], 30)
        self.assertGreaterEqual(seed_summary["inserted"]["assistant_records"], 200)
        self.assertEqual(
            seed_summary["inserted"]["review_notebook_qa_records"],
            len(seed_demo_data.build_review_notebook_qa_specs()),
        )
        self.assertEqual(seed_summary["inserted"]["assessment_tasks"], 5)
        self.assertGreaterEqual(seed_summary["inserted"]["agent_evo_episodes"], 30)
        self.assertGreaterEqual(seed_summary["inserted"]["agent_evo_semantic"], 5)
        self.assertGreaterEqual(seed_summary["inserted"]["agent_evo_reflective"], 3)
        self.assertGreaterEqual(seed_summary["inserted"]["agent_evo_procedural"], 1)
        self.assertGreaterEqual(seed_summary["inserted"]["agent_evo_memory_hits"], 10)
        self.assertGreaterEqual(seed_summary["inserted"]["agent_evo_promotions"], 1)
        self.assertTrue(seed_summary["admin"]["password_preserved"])

        with self._conn() as conn:
            demo_users = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE username IN ({})".format(
                    ",".join("?" for _ in seed_demo_data.DEMO_USERNAMES)
                ),
                tuple(seed_demo_data.DEMO_USERNAMES),
            ).fetchone()["c"]
            self.assertEqual(demo_users, len(seed_demo_data.DEMO_USERS))

            practice_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM practice_records WHERE practice_id LIKE ?",
                (f"{seed_demo_data.DEMO_PRACTICE_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(practice_rows, 30)

            dashboard_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM dashboard_snapshots WHERE snapshot_id LIKE ?",
                (f"{seed_demo_data.DEMO_DASHBOARD_PREFIX}%",),
            ).fetchone()["c"]
            self.assertEqual(dashboard_rows, 10)

            completed_task_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM cycle_daily_tasks WHERE cycle_id LIKE ? AND status = 'completed'",
                (f"{seed_demo_data.DEMO_CYCLE_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(completed_task_rows, 60)

            submitted_exam_rows = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM assessment_records ar
                JOIN assessment_tasks t ON ar.task_id = t.id
                WHERE t.task_name LIKE ? AND ar.submit_status IN ('submitted', 'timeout_submitted')
                """,
                ('DEMO:%',),
            ).fetchone()["c"]
            self.assertGreaterEqual(submitted_exam_rows, 15)

            protagonist = conn.execute(
                "SELECT id, username, name FROM users WHERE username = ? LIMIT 1",
                (seed_demo_data.PROTAGONIST_USERNAME,),
            ).fetchone()
            self.assertIsNotNone(protagonist)
            self.assertEqual(protagonist["name"], "赵景行")

            protagonist_id = str(protagonist["id"])
            protagonist_cycles = conn.execute(
                """
                SELECT stage_no, status, current_day
                FROM training_cycles
                WHERE user_id = ?
                ORDER BY stage_no ASC
                """,
                (protagonist_id,),
            ).fetchall()
            self.assertEqual([row["stage_no"] for row in protagonist_cycles], [1, 2])
            self.assertTrue(all(row["status"] == "completed" for row in protagonist_cycles))
            self.assertTrue(all(row["current_day"] == 7 for row in protagonist_cycles))

            protagonist_task_days = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM (
                    SELECT tc.stage_no, cdt.day_index
                    FROM cycle_daily_tasks cdt
                    JOIN training_cycles tc ON tc.cycle_id = cdt.cycle_id
                    WHERE cdt.user_id = ? AND cdt.status = 'completed'
                    GROUP BY tc.stage_no, cdt.day_index
                )
                """,
                (protagonist_id,),
            ).fetchone()["c"]
            self.assertEqual(protagonist_task_days, 14)

            protagonist_ability = conn.execute(
                """
                SELECT stage_no, cycle_day_index, overall_score, ability_snapshot_json
                FROM ability_update_records
                WHERE employee_id = ? AND update_id LIKE ?
                ORDER BY created_at ASC, id ASC
                """,
                (protagonist_id, f"{seed_demo_data.DEMO_ABILITY_PREFIX}zjx_%"),
            ).fetchall()
            self.assertEqual(len(protagonist_ability), 14)
            self.assertEqual(float(protagonist_ability[0]["overall_score"]), 38.0)
            self.assertEqual(float(protagonist_ability[-1]["overall_score"]), 85.0)
            self.assertEqual(
                {(row["stage_no"], row["cycle_day_index"]) for row in protagonist_ability},
                {(1, day) for day in range(1, 8)} | {(2, day) for day in range(1, 8)},
            )

            assistant_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM assistant_records WHERE record_id LIKE ?",
                (f"{seed_demo_data.DEMO_ASSISTANT_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(assistant_rows, 200)

            review_qa_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM assistant_records WHERE record_id LIKE ? AND scene_hint = 'knowledge_qa'",
                (f"{seed_demo_data.DEMO_REVIEW_QA_PREFIX}%",),
            ).fetchone()["c"]
            self.assertEqual(review_qa_rows, len(seed_demo_data.build_review_notebook_qa_specs()))

            review_paper_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM assessment_tasks WHERE task_name LIKE ? AND paper_config_json LIKE ?",
                ("DEMO:%", "%\"questions\"%"),
            ).fetchone()["c"]
            self.assertEqual(review_paper_rows, 5)

            protagonist_assessments = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM assessment_records ar
                JOIN assessment_tasks t ON ar.task_id = t.id
                WHERE ar.user_id = ? AND t.task_name LIKE ? AND ar.paper_answer_json LIKE ?
                """,
                (protagonist_id, "DEMO:%", "%q_objection_price%"),
            ).fetchone()["c"]
            self.assertGreaterEqual(protagonist_assessments, 1)

            demo_evo_episodes = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_episodes WHERE request_id LIKE ?",
                (f"{seed_demo_data.DEMO_EVO_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(demo_evo_episodes, 30)

            demo_evo_thumb_down = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_episodes WHERE request_id LIKE ? AND signal = 'thumb_down'",
                (f"{seed_demo_data.DEMO_EVO_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(demo_evo_thumb_down, 5)

            demo_evo_corrections = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_episodes WHERE request_id LIKE ? AND signal = 'correction'",
                (f"{seed_demo_data.DEMO_EVO_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(demo_evo_corrections, 5)

            active_semantic = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_semantic WHERE status = 'active' AND content LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(active_semantic, 5)

            active_reflective = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_reflective WHERE status = 'active' AND lesson LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(active_reflective, 3)

            active_procedural = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_procedural WHERE status IN ('active', 'auto') AND example LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(active_procedural, 1)

            pending_promotions = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_promotions WHERE status = 'pending' AND evidence LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(pending_promotions, 1)

            memory_hits = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_memory_hits WHERE query_text LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(memory_hits, 10)

            pending_candidates = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_semantic WHERE status = 'pending' AND write_mode = 'candidate' AND content LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(pending_candidates, 3)

            disabled_memories = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_semantic WHERE status IN ('archived', 'auto_disabled', 'quarantined') AND content LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(disabled_memories, 1)

            pending_reviews = conn.execute(
                "SELECT COUNT(*) AS c FROM agent_evo_review_queue WHERE status = 'pending' AND reason LIKE ?",
                (f"%{seed_demo_data.SEED_TAG}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(pending_reviews, 3)

            linked_feedback_hits = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM agent_evo_memory_hits h
                JOIN agent_evo_episodes e
                  ON e.user_id = h.user_id
                 AND e.module = h.module
                 AND e.query_text = h.query_text
                WHERE e.request_id LIKE ?
                  AND e.signal IN ('thumb_up', 'thumb_down', 'correction')
                """,
                (f"{seed_demo_data.DEMO_EVO_PREFIX}%",),
            ).fetchone()["c"]
            self.assertGreaterEqual(linked_feedback_hits, 3)

        engine = create_engine(f"sqlite:///{self.temp_db}", connect_args={"check_same_thread": False}, poolclass=NullPool)
        try:
            Session = sessionmaker(bind=engine)
            with Session() as db:
                payload = aggregate_review_notebook(
                    db,
                    user_id=protagonist_id,
                    record_limit=100,
                    return_limit=200,
                )
            by_source = payload["summary"]["by_source"]
            self.assertGreaterEqual(by_source["assessment"], 1)
            self.assertGreaterEqual(by_source["practice"], 1)
            self.assertGreaterEqual(by_source["assistant"], 1)
            self.assertGreaterEqual(by_source["qa"], 1)

            for username in seed_demo_data.DEMO_USERNAMES:
                target_id = db.execute(
                    text("SELECT id FROM users WHERE username = :username"),
                    {"username": username},
                ).scalar_one()
                data = aggregate_review_notebook(db, user_id=str(target_id), record_limit=100, return_limit=200)
                self.assertGreater(data["summary"]["total"], 0, username)
                self.assertGreaterEqual(data["summary"]["by_source"]["qa"], 1, username)
        finally:
            engine.dispose()

        delete_summary = seed_demo_data.delete_demo_data(self.temp_db)
        self.assertGreater(delete_summary["deleted_total"], 0)

        with self._conn() as conn:
            admin_after = conn.execute(
                "SELECT id, username, hashed_password FROM users WHERE username = 'admin' LIMIT 1"
            ).fetchone()
            self.assertEqual(admin_after["id"], admin_before["id"])
            self.assertEqual(admin_after["hashed_password"], admin_before["hashed_password"])

            demo_users_after = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE username IN ({})".format(
                    ",".join("?" for _ in seed_demo_data.DEMO_USERNAMES)
                ),
                tuple(seed_demo_data.DEMO_USERNAMES),
            ).fetchone()["c"]
            self.assertEqual(demo_users_after, 0)

            demo_stores_after = conn.execute(
                "SELECT COUNT(*) AS c FROM stores WHERE store_id IN ({})".format(
                    ",".join("?" for _ in seed_demo_data.DEMO_STORE_IDS)
                ),
                tuple(seed_demo_data.DEMO_STORE_IDS),
            ).fetchone()["c"]
            self.assertEqual(demo_stores_after, 0)

            demo_practice_after = conn.execute(
                "SELECT COUNT(*) AS c FROM practice_records WHERE practice_id LIKE ?",
                (f"{seed_demo_data.DEMO_PRACTICE_PREFIX}%",),
            ).fetchone()["c"]
            self.assertEqual(demo_practice_after, 0)

            demo_assistant_after = conn.execute(
                "SELECT COUNT(*) AS c FROM assistant_records WHERE record_id LIKE ?",
                (f"{seed_demo_data.DEMO_ASSISTANT_PREFIX}%",),
            ).fetchone()["c"]
            self.assertEqual(demo_assistant_after, 0)

            demo_review_qa_after = conn.execute(
                "SELECT COUNT(*) AS c FROM assistant_records WHERE record_id LIKE ?",
                (f"{seed_demo_data.DEMO_REVIEW_QA_PREFIX}%",),
            ).fetchone()["c"]
            self.assertEqual(demo_review_qa_after, 0)

            demo_evo_after = {
                "agent_evo_episodes": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_episodes WHERE request_id LIKE ?",
                    (f"{seed_demo_data.DEMO_EVO_PREFIX}%",),
                ).fetchone()["c"],
                "agent_evo_semantic": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_semantic WHERE content LIKE ?",
                    (f"%{seed_demo_data.SEED_TAG}%",),
                ).fetchone()["c"],
                "agent_evo_reflective": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_reflective WHERE lesson LIKE ?",
                    (f"%{seed_demo_data.SEED_TAG}%",),
                ).fetchone()["c"],
                "agent_evo_procedural": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_procedural WHERE example LIKE ?",
                    (f"%{seed_demo_data.SEED_TAG}%",),
                ).fetchone()["c"],
                "agent_evo_memory_hits": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_memory_hits WHERE query_text LIKE ?",
                    (f"%{seed_demo_data.SEED_TAG}%",),
                ).fetchone()["c"],
                "agent_evo_promotions": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_promotions WHERE evidence LIKE ?",
                    (f"%{seed_demo_data.SEED_TAG}%",),
                ).fetchone()["c"],
                "agent_evo_eval_cases": conn.execute(
                    "SELECT COUNT(*) AS c FROM agent_evo_eval_cases WHERE bound_memory_ids LIKE ?",
                    (f"%{seed_demo_data.SEED_TAG}%",),
                ).fetchone()["c"],
            }
            self.assertEqual(demo_evo_after, {key: 0 for key in demo_evo_after})


if __name__ == "__main__":
    unittest.main()
