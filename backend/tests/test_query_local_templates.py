from __future__ import annotations

import sqlite3
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from local_query_templates import try_local_query_template
from routers import query


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE stores (
            store_id TEXT PRIMARY KEY,
            store_name TEXT,
            region TEXT,
            manager_name TEXT
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            username TEXT,
            display_name TEXT,
            role TEXT,
            store_id TEXT,
            created_at TEXT
        );
        CREATE TABLE employee_profiles (
            id INTEGER PRIMARY KEY,
            employee_id TEXT,
            employee_name TEXT,
            position TEXT,
            role TEXT,
            store_id TEXT,
            created_at TEXT
        );
        CREATE TABLE role_settings (
            role_key TEXT,
            display_name TEXT,
            description TEXT,
            is_enabled INTEGER,
            sort_order INTEGER
        );
        CREATE TABLE query_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT,
            stage TEXT,
            employee_id TEXT,
            query_text TEXT,
            parsed_intent TEXT,
            payload_json TEXT,
            created_at TEXT
        );
        CREATE TABLE cycle_daily_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            user_id TEXT,
            day_index INTEGER,
            task_code TEXT,
            task_type TEXT,
            branch TEXT,
            title TEXT,
            description TEXT,
            status TEXT,
            target_count INTEGER,
            current_count INTEGER,
            created_at TEXT,
            module_name TEXT
        );
        CREATE TABLE assessment_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            user_id TEXT,
            employee_name TEXT,
            score REAL,
            finished_at TEXT,
            submit_status TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO stores (store_id, store_name, region, manager_name) VALUES (?, ?, ?, ?)",
        [
            ("STORE_A", "上海南京东路店", "华东", "王店长"),
            ("STORE_B", "广州天河城店", "华南", "李店长"),
            ("STORE_RISK_FAKE", "风险演示店", "测试", "假店长"),
        ],
    )
    conn.executemany(
        "INSERT INTO users (id, user_id, username, display_name, role, store_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "1", "admin", "管理员", "admin", "STORE_A", "2026-01-01T00:00:00Z"),
            (2, "2", "sm_a", "王店长", "store_manager", "STORE_A", "2026-01-01T00:00:00Z"),
            (3, "3", "alice", "张三", "trainee", "STORE_A", "2026-01-01T00:00:00Z"),
            (4, "4", "bob", "李四", "senior_consultant", "STORE_B", "2026-01-01T00:00:00Z"),
            (5, "5", "fake", "测试员工", "trainee", "STORE_RISK_FAKE", "2026-01-01T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO employee_profiles (employee_id, employee_name, position, role, store_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("2", "王店长", "店长", "store_manager", "STORE_A", "2026-01-01T00:00:00Z"),
            ("3", "张三", "珠宝顾问", "trainee", "STORE_A", "2026-01-01T00:00:00Z"),
            ("4", "李四", "资深顾问", "senior_consultant", "STORE_B", "2026-01-01T00:00:00Z"),
            ("5", "测试员工", "测试", "trainee", "STORE_RISK_FAKE", "2026-01-01T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO role_settings (role_key, display_name, description, is_enabled, sort_order) VALUES (?, ?, ?, ?, ?)",
        [
            ("store_manager", "店长", "门店管理", 1, 1),
            ("trainee", "新人顾问", "新人销售", 1, 2),
            ("disabled", "停用角色", "不可见", 0, 3),
        ],
    )
    conn.executemany(
        """
        INSERT INTO cycle_daily_tasks (
            cycle_id, user_id, day_index, task_code, task_type, branch, title, description,
            status, target_count, current_count, created_at, module_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("C1", "2", 1, "t1", "learning_review", "learning", "产品基础学习", "", "completed", 1, 1, "2026-01-02T00:00:00Z", "产品知识"),
            ("C1", "2", 2, "t2", "practice_chat", "practice", "接待练习", "", "completed", 1, 1, "2026-01-03T00:00:00Z", "接待流程"),
            ("C1", "3", 1, "t3", "learning_review", "learning", "材质学习", "", "completed", 1, 1, "2026-01-02T00:00:00Z", "材质知识"),
            ("C1", "3", 2, "t4", "practice_chat", "practice", "成交练习", "", "in_progress", 1, 0, "2026-01-03T00:00:00Z", "成交推进"),
            ("C1", "3", 3, "t5", "mock_exam", "assessment", "阶段考试", "", "locked", 1, 0, "2026-01-04T00:00:00Z", "阶段考核"),
            ("C2", "4", 1, "t6", "learning_review", "learning", "异议处理学习", "", "completed", 1, 1, "2026-01-02T00:00:00Z", "异议处理"),
            ("C2", "4", 2, "t7", "practice_chat", "practice", "异议处理练习", "", "completed", 1, 1, "2026-01-03T00:00:00Z", "异议处理"),
            ("C2", "4", 3, "t8", "learning_review", "learning", "旧任务", "", "voided", 1, 0, "2026-01-04T00:00:00Z", "历史任务"),
        ],
    )
    conn.executemany(
        "INSERT INTO assessment_records (task_id, user_id, employee_name, score, finished_at, submit_status) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "1", "管理员", 88.0, "2026-01-05T00:00:00Z", "submitted"),
            (1, "2", "王店长", 0.0, None, "in_progress"),
            (1, "4", "李四", 92.0, "2026-01-05T00:00:00Z", "timeout_submitted"),
        ],
    )
    return conn


@contextmanager
def _conn_ctx(conn: sqlite3.Connection):
    yield conn


class LocalQueryTemplateTests(unittest.TestCase):
    def _conn(self) -> sqlite3.Connection:
        conn = _make_conn()
        self.addCleanup(conn.close)
        return conn

    def test_admin_store_count_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "全系统有几家门店？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("store_count", result["query_type"])
        self.assertEqual("local_template", result["route_type"])
        self.assertEqual("success", result["query_status"])
        self.assertEqual([{"store_count": 2}], result["result_rows"])
        self.assertIn("当前系统共有2家门店", result["reply_text"])

    def test_store_manager_employee_count_is_scoped_to_own_store(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "本店有多少员工？",
            store_id="STORE_A",
            allow_global_scope=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([{"employee_count": 3}], result["result_rows"])
        self.assertIn("本店共有3名员工", result["reply_text"])

    def test_role_list_uses_enabled_roles_only(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "有哪些角色？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("role_list", result["query_type"])
        self.assertIn("店长", result["reply_text"])
        self.assertIn("新人顾问", result["reply_text"])
        self.assertNotIn("停用角色", result["reply_text"])

    def test_named_store_employee_list_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "上海南京东路店有哪些员工？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("employee_list", result["query_type"])
        names = [row["employee_name"] for row in result["result_rows"]]
        self.assertCountEqual(["管理员", "王店长", "张三"], names)
        self.assertNotIn("李四", names)

    def test_unknown_named_store_does_not_fall_back_to_global_employee_list(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "上海南京西路店有哪些员工？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("employee_list", result["query_type"])
        self.assertEqual([], result["result_rows"])
        self.assertIn("上海南京西路店", result["reply_text"])

    def test_knowledge_question_does_not_hit_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "黄金是什么？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNone(result)

    def test_training_incomplete_staff_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "哪些员工培训还没完成？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("training_incomplete_staff", result["query_type"])
        names = [row["employee_name"] for row in result["result_rows"]]
        self.assertEqual(["张三"], names)

    def test_training_unfinished_newcomer_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "有哪些新人培训还没完成？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("training_unfinished_newcomer", result["query_type"])
        self.assertEqual(["张三"], [row["employee_name"] for row in result["result_rows"]])

    def test_training_completion_overview_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "本店培训完成率怎么样？",
            store_id="STORE_A",
            allow_global_scope=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("store_training_completion_rank", result["query_type"])
        self.assertIn("培训完成率目前是", result["reply_text"])

    def test_exam_incomplete_staff_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "哪些员工没有完成考试？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("exam_incomplete_staff", result["query_type"])
        self.assertCountEqual(["王店长", "张三"], [row["employee_name"] for row in result["result_rows"]])

    def test_exam_completion_overview_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "考试完成率是多少？",
            store_id="STORE_A",
            allow_global_scope=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("exam_completion_overview", result["query_type"])
        self.assertIn("考试完成率目前是", result["reply_text"])

    def test_task_incomplete_items_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "今天还有哪些任务没完成？",
            store_id="STORE_A",
            allow_global_scope=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("task_incomplete_items", result["query_type"])
        self.assertGreaterEqual(len(result["result_rows"]), 2)
        self.assertIn("任务未完成", result["reply_text"])

    def test_task_completion_overview_hits_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "本店任务完成率怎么样？",
            store_id="STORE_A",
            allow_global_scope=False,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("task_completion_overview", result["query_type"])
        self.assertIn("任务完成率目前是", result["reply_text"])

    def test_training_analysis_question_does_not_hit_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "为什么培训进度低？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNone(result)

    def test_exam_trend_question_does_not_hit_local_template(self) -> None:
        conn = self._conn()

        result = try_local_query_template(
            conn,
            "最近考试趋势怎么样？",
            store_id="STORE_A",
            allow_global_scope=True,
        )

        self.assertIsNone(result)

    def test_query_ask_local_template_prefers_query2_summary(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(query, "run_query1_workflow") as query1, \
            mock.patch.object(
                query,
                "run_query2_workflow",
                return_value={
                    "ok": True,
                    "data": {
                        "workflow_status": "success",
                        "summary": "我看了下，目前系统里一共是2家门店。",
                        "manager_advice": "",
                        "focus_names": [],
                        "display_tags": ["已出结果"],
                        "user_visible_output": "我看了下，目前系统里一共是2家门店。",
                    },
                },
            ) as query2:
            response = query.query_ask(
                query.QueryAskRequest(query_text="全系统有几家门店？"),
                current_user,
            )

        query1.assert_not_called()
        query2.assert_called_once()
        data = response["data"]
        self.assertEqual("local_template", data["route_type"])
        self.assertEqual("system_hit", data["reply_mode"])
        self.assertEqual("success", data["query_status"])
        self.assertEqual("我看了下，目前系统里一共是2家门店。", data["reply_text"])

    def test_query_ask_local_template_falls_back_when_query2_fails(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(query, "run_query1_workflow") as query1, \
            mock.patch.object(
                query,
                "run_query2_workflow",
                return_value={"ok": False, "reason": "dify_non_200"},
            ) as query2:
            response = query.query_ask(
                query.QueryAskRequest(query_text="全系统有几家门店？"),
                current_user,
            )

        query1.assert_not_called()
        query2.assert_called_once()
        data = response["data"]
        self.assertEqual("local_template", data["route_type"])
        self.assertEqual("local_template", data["reply_mode"])
        self.assertIn("当前系统共有2家门店", data["reply_text"])

    def test_query_ask_non_template_still_calls_dify(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(
                query,
                "run_query1_workflow",
                return_value={
                    "ok": True,
                    "data": {
                        "query_type": "unsupported",
                        "can_query": 0,
                        "route_type": "unsupported",
                        "summary_text": "黄金是一种常见贵金属。",
                    },
                },
            ) as query1, \
            mock.patch.object(query, "run_query2_workflow") as query2:
            response = query.query_ask(
                query.QueryAskRequest(query_text="黄金是什么？"),
                current_user,
            )

        query1.assert_called_once()
        query2.assert_not_called()
        data = response["data"]
        self.assertEqual("llm_fallback", data["reply_mode"])
        self.assertIn("黄金是一种常见贵金属", data["reply_text"])

    def test_blocked_query_wins_before_local_template(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(query, "run_query1_workflow") as query1:
            response = query.query_ask(
                query.QueryAskRequest(query_text="全系统有几家门店，顺便导出手机号"),
                current_user,
            )

        query1.assert_not_called()
        data = response["data"]
        self.assertEqual("blocked", data["route_type"])

    def test_query_parse_local_template_returns_fallback_reply(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(query, "run_query1_workflow") as query1:
            response = query.query_parse(
                query.QueryParseRequest(query_text="有哪些角色？"),
                current_user,
            )

        query1.assert_not_called()
        data = response["data"]
        self.assertEqual("local_template", data["route_type"])
        self.assertEqual("system_hit", data["reply_mode"])
        self.assertEqual(1, data["can_query"])
        self.assertIn("当前启用角色包括", data["fallback_reply_text"])

    def test_context_rewrite_does_not_turn_role_list_into_employee_attribute_query(self) -> None:
        history = [
            {
                "role": "user",
                "content": "上海陆家嘴体验店有哪些员工？",
            },
            {
                "role": "assistant",
                "content": "我查到上海陆家嘴体验店有4名员工。",
                "query_context": {
                    "intent": "employee_list",
                    "result_preview": [
                        {"employee_name": "张美玲", "store_name": "上海陆家嘴体验店"},
                        {"employee_name": "李芳", "store_name": "上海陆家嘴体验店"},
                    ],
                },
            },
        ]

        rewritten = query._rewrite_contextual_query("有哪些角色？", history)

        self.assertEqual("有哪些角色？", rewritten)

    def test_query_summarize_local_template_prefers_query2_summary(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}
        body = query.QuerySummarizeRequest(
            query_text="有哪些角色？",
            parsed_intent="role_list",
            query_status="success",
            fallback_reply_text="当前启用角色包括：店长、新人顾问。",
            reply_mode="system_hit",
            result_rows=[{"display_name": "店长"}, {"display_name": "新人顾问"}],
        )

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(
                query,
                "run_query2_workflow",
                return_value={
                    "ok": True,
                    "data": {
                        "workflow_status": "success",
                        "summary": "当前启用的角色主要是店长和新人顾问。",
                        "manager_advice": "",
                        "focus_names": [],
                        "display_tags": ["已出结果"],
                        "user_visible_output": "当前启用的角色主要是店长和新人顾问。",
                    },
                },
            ) as query2:
            response = query.query_summarize(body, current_user)

        query2.assert_called_once()
        data = response["data"]
        self.assertEqual("system_hit", data["reply_mode"])
        self.assertEqual("当前启用的角色主要是店长和新人顾问。", data["reply_text"])

    def test_query_summarize_local_template_falls_back_when_query2_fails(self) -> None:
        conn = self._conn()
        current_user = {"user_id": "1", "role": "admin", "store_id": "STORE_A"}
        body = query.QuerySummarizeRequest(
            query_text="有哪些角色？",
            parsed_intent="role_list",
            query_status="success",
            fallback_reply_text="当前启用角色包括：店长、新人顾问。",
            reply_mode="system_hit",
            result_rows=[{"display_name": "店长"}, {"display_name": "新人顾问"}],
        )

        with mock.patch.object(query, "get_conn", side_effect=lambda: _conn_ctx(conn)), \
            mock.patch.object(
                query,
                "run_query2_workflow",
                return_value={"ok": False, "reason": "dify_non_200"},
            ) as query2:
            response = query.query_summarize(body, current_user)

        query2.assert_called_once()
        data = response["data"]
        self.assertEqual("local_template", data["reply_mode"])
        self.assertEqual("当前启用角色包括：店长、新人顾问。", data["reply_text"])


if __name__ == "__main__":
    unittest.main()
