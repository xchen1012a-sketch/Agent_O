from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
import routers.personnel as personnel_router


class EmployeeJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.temp_db = Path(self.tmpdir.name) / "journey.db"
        self._create_schema()

        original_get_conn = personnel_router.get_conn
        personnel_router.get_conn = self._conn
        self.addCleanup(setattr, personnel_router, "get_conn", original_get_conn)

        self.current_user = {
            "user_id": "manager-1",
            "role": "store_manager",
            "username": "manager_gz",
            "store_id": "STORE_GZ",
        }
        self.app = FastAPI()
        self.app.include_router(personnel_router.router)
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.current_user
        self.client = TestClient(self.app)
        self._seed_rows()

    def _create_schema(self) -> None:
        conn = sqlite3.connect(self.temp_db)
        try:
            conn.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    store_id TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE stores (
                    store_id TEXT PRIMARY KEY,
                    store_name TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE employee_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    employee_name TEXT NOT NULL DEFAULT '',
                    position TEXT NOT NULL DEFAULT '',
                    job_title TEXT NOT NULL DEFAULT '',
                    store_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    mentor_name TEXT NOT NULL DEFAULT '',
                    initial_ability TEXT NOT NULL DEFAULT '',
                    current_overall_score REAL
                );
                CREATE TABLE growth_plan_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL DEFAULT '',
                    employee_id TEXT NOT NULL DEFAULT '',
                    employee_name TEXT NOT NULL DEFAULT '',
                    position TEXT NOT NULL DEFAULT '',
                    store_id TEXT NOT NULL DEFAULT '',
                    mentor_name TEXT NOT NULL DEFAULT '',
                    ability_summary TEXT NOT NULL DEFAULT '',
                    target_direction TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    user_id TEXT NOT NULL DEFAULT '',
                    growth_plan_text TEXT NOT NULL DEFAULT '',
                    plan_meta_json TEXT NOT NULL DEFAULT '{}',
                    source_workflow TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE training_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    plan_id TEXT NOT NULL DEFAULT '',
                    total_days INTEGER NOT NULL DEFAULT 7,
                    status TEXT NOT NULL DEFAULT '',
                    current_day INTEGER NOT NULL DEFAULT 1,
                    stage_no INTEGER NOT NULL DEFAULT 1,
                    stage_name TEXT NOT NULL DEFAULT '',
                    stage_status TEXT NOT NULL DEFAULT '',
                    stage_started_at TEXT,
                    stage_completed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE cycle_daily_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    day_index INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    module_code TEXT NOT NULL DEFAULT '',
                    module_name TEXT NOT NULL DEFAULT '',
                    ai_score REAL,
                    ai_feedback TEXT NOT NULL DEFAULT '',
                    completed_at TEXT
                );
                CREATE TABLE practice_eval_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL DEFAULT '',
                    practice_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    employee_id TEXT NOT NULL DEFAULT '',
                    overall_score REAL,
                    risk_level TEXT NOT NULL DEFAULT '',
                    weak_dimension TEXT NOT NULL DEFAULT '',
                    coach_summary TEXT NOT NULL DEFAULT '',
                    improvement_advice TEXT NOT NULL DEFAULT '',
                    cycle_day_index INTEGER,
                    module_code TEXT NOT NULL DEFAULT '',
                    module_name TEXT NOT NULL DEFAULT '',
                    cycle_id TEXT NOT NULL DEFAULT '',
                    stage_no INTEGER,
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE ability_update_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id TEXT NOT NULL DEFAULT '',
                    practice_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    employee_id TEXT NOT NULL DEFAULT '',
                    score REAL,
                    overall_score REAL,
                    risk_level TEXT NOT NULL DEFAULT '',
                    focus_dimension TEXT NOT NULL DEFAULT '',
                    update_summary TEXT NOT NULL DEFAULT '',
                    ability_comment TEXT NOT NULL DEFAULT '',
                    ability_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    cycle_day_index INTEGER,
                    module_code TEXT NOT NULL DEFAULT '',
                    module_name TEXT NOT NULL DEFAULT '',
                    cycle_id TEXT NOT NULL DEFAULT '',
                    stage_no INTEGER,
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE learning_eval_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    employee_id TEXT NOT NULL DEFAULT '',
                    score REAL,
                    learning_summary TEXT NOT NULL DEFAULT '',
                    manager_feedback TEXT NOT NULL DEFAULT '',
                    module_code TEXT NOT NULL DEFAULT '',
                    module_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE assessment_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    employee_name TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    is_pass INTEGER NOT NULL DEFAULT 0,
                    comment TEXT NOT NULL DEFAULT '',
                    cycle_day_index INTEGER,
                    submit_status TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.temp_db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _seed_rows(self) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO stores (store_id, store_name, name, region) VALUES ('STORE_GZ', '广州天河精品店', '广州天河精品店', '广州')"
            )
            conn.execute(
                """
                INSERT INTO users (id, user_id, username, name, display_name, role, store_id, created_at)
                VALUES (1, 'manager-1', 'manager_gz', '陈志明', '陈志明', 'store_manager', 'STORE_GZ', '2026-05-01T08:00:00+00:00'),
                       (2, 'demo_trainee_zjx', 'trainee_zjx', '赵景行', '赵景行', 'trainee', 'STORE_GZ', '2026-05-01T08:00:00+00:00'),
                       (3, 'other-user', 'other_user', '外部门店', '外部门店', 'trainee', 'STORE_SH', '2026-05-01T08:00:00+00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO employee_profiles (
                    employee_id, user_id, employee_name, position, job_title, store_id, role,
                    mentor_name, initial_ability, current_overall_score
                ) VALUES ('2', '2', '赵景行', '导购', '导购', 'STORE_GZ', 'trainee', '陈志明', '入营基线综合 38/100。', 85)
                """
            )
            conn.execute(
                """
                INSERT INTO growth_plan_records (
                    plan_id, employee_id, employee_name, position, store_id, mentor_name,
                    ability_summary, target_direction, user_id, growth_plan_text, plan_meta_json,
                    source_workflow, created_at
                ) VALUES (
                    'plan-zjx', '2', '赵景行', '导购', 'STORE_GZ', '陈志明',
                    '异议处理和成交收口偏弱。', '14 天补强至独立上岗',
                    '2', '# 赵景行成长计划', '{"plan_cycle_days": 90}',
                    'demo_seed_growth', '2026-05-01T09:00:00+00:00'
                )
                """
            )
            for stage_no in (1, 2):
                conn.execute(
                    """
                    INSERT INTO training_cycles (
                        cycle_id, user_id, plan_id, total_days, status, current_day,
                        stage_no, stage_name, stage_status, stage_started_at, stage_completed_at, created_at
                    ) VALUES (?, '2', 'plan-zjx', 7, 'completed', 7, ?, ?, 'passed', ?, ?, ?)
                    """,
                    (
                        f"cycle-s{stage_no}",
                        stage_no,
                        "基础认知" if stage_no == 1 else "独立上岗",
                        f"2026-05-{1 + (stage_no - 1) * 7:02d}T09:00:00+00:00",
                        f"2026-05-{stage_no * 7:02d}T18:00:00+00:00",
                        f"2026-05-{1 + (stage_no - 1) * 7:02d}T09:00:00+00:00",
                    ),
                )
            scores = [38, 42, 45, 49, 53, 58, 61, 65, 69, 73, 78, 80, 82, 85]
            for day_index, score in enumerate(scores, start=1):
                stage_no = 1 if day_index <= 7 else 2
                cycle_day = day_index if day_index <= 7 else day_index - 7
                cycle_id = f"cycle-s{stage_no}"
                module_code = "objection_handling" if day_index in (7, 11) else "product_basics"
                module_name = "异议处理" if module_code == "objection_handling" else "产品基础"
                created_at = f"2026-05-{day_index:02d}T10:00:00+00:00"
                snapshot = {
                    "overall_score": score,
                    "product_knowledge": score + 4,
                    "compliance_expression": score + 2,
                    "needs_discovery": score + 1,
                    "sales_expression": score,
                    "objection_handling": score - 2,
                    "closing_skill": score - 1,
                }
                conn.execute(
                    """
                    INSERT INTO cycle_daily_tasks (
                        cycle_id, user_id, day_index, title, description, status,
                        module_code, module_name, ai_score, ai_feedback, completed_at
                    ) VALUES (?, '2', ?, ?, '当日训练任务', 'completed', ?, ?, ?, '系统已完成记录', ?)
                    """,
                    (cycle_id, cycle_day, f"Day {day_index} 任务", module_code, module_name, score, created_at),
                )
                conn.execute(
                    """
                    INSERT INTO ability_update_records (
                        update_id, practice_id, user_id, employee_id, score, overall_score,
                        risk_level, focus_dimension, update_summary, ability_comment,
                        ability_snapshot_json, cycle_day_index, module_code, module_name,
                        cycle_id, stage_no, created_at
                    ) VALUES (?, ?, '2', '2', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"update-{day_index:02d}",
                        f"practice-{day_index:02d}",
                        score,
                        score,
                        "high" if score < 60 else ("medium" if score < 85 else "low"),
                        module_name,
                        f"Day {day_index} 综合分 {score}",
                        f"赵景行 Day {day_index} 综合分推进至 {score}。",
                        json.dumps(snapshot, ensure_ascii=False),
                        cycle_day,
                        module_code,
                        module_name,
                        cycle_id,
                        stage_no,
                        created_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO practice_eval_records (
                        evaluation_id, practice_id, user_id, employee_id, overall_score,
                        risk_level, weak_dimension, coach_summary, improvement_advice,
                        cycle_day_index, module_code, module_name, cycle_id, stage_no, created_at
                    ) VALUES (?, ?, '2', '2', ?, ?, ?, ?, '继续强化场景化推荐。', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"eval-{day_index:02d}",
                        f"practice-{day_index:02d}",
                        score,
                        "high" if score < 60 else ("medium" if score < 85 else "low"),
                        "成交推进" if score >= 72 else "基础表达",
                        f"Day {day_index} 陪练得分 {score}。",
                        cycle_day,
                        module_code,
                        module_name,
                        cycle_id,
                        stage_no,
                        created_at,
                    ),
                )
            conn.execute(
                """
                INSERT INTO learning_eval_records (
                    evaluation_id, user_id, employee_id, score, learning_summary,
                    manager_feedback, module_code, module_name, created_at
                ) VALUES ('learn-08', '2', '2', 66, '导师 Agent 生成补强计划。', '重点补强价格异议。', 'objection_handling', '异议处理', '2026-05-08T11:00:00+00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO assessment_records (
                    user_id, employee_name, score, is_pass, comment, cycle_day_index,
                    submit_status, finished_at
                ) VALUES ('2', '赵景行', 58, 0, '阶段评估未通过。', 7, 'submitted', '2026-05-07T15:00:00+00:00'),
                         ('2', '赵景行', 82, 1, '模拟考核通过。', 6, 'submitted', '2026-05-13T15:00:00+00:00')
                """
            )

    def test_build_employee_journey_payload_returns_story_nodes(self) -> None:
        with self._conn() as conn:
            payload = personnel_router.build_employee_journey_payload(conn, "2", self.current_user)

        self.assertEqual(payload["employee"]["name"], "赵景行")
        self.assertEqual(payload["summary"]["total_days"], 14)
        self.assertEqual(payload["summary"]["start_score"], 38.0)
        self.assertEqual(payload["summary"]["current_score"], 85.0)
        self.assertEqual(payload["summary"]["score_delta"], 47.0)
        self.assertGreaterEqual(payload["summary"]["high_risk_count"], 1)
        self.assertEqual(len(payload["nodes"]), 14)
        self.assertEqual(payload["nodes"][0]["day_index"], 1)
        self.assertEqual(payload["nodes"][0]["score"], 38.0)
        self.assertEqual(payload["nodes"][6]["risk_level"], "high")
        self.assertTrue(payload["nodes"][6]["key_event"])
        self.assertTrue(payload["nodes"][7]["key_event"])
        self.assertTrue(payload["nodes"][10]["key_event"])
        self.assertTrue(payload["nodes"][-1]["passed"])
        self.assertEqual(len(payload["nodes"][-1]["ability_values"]), 6)

    def test_endpoint_allows_same_store_manager(self) -> None:
        response = self.client.get("/api/employee/2/journey")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["employee"]["name"], "赵景行")
        self.assertEqual(len(body["data"]["nodes"]), 14)

    def test_endpoint_rejects_out_of_scope_manager(self) -> None:
        response = self.client.get("/api/employee/3/journey")

        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
