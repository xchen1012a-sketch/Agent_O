from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from shutil import copy2

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import seed_demo_data


class SeedDemoDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
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
        self.assertEqual(seed_summary["inserted"]["assessment_tasks"], 5)
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


if __name__ == "__main__":
    unittest.main()
