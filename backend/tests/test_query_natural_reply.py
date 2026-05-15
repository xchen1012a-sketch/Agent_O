from __future__ import annotations

import sys
import unittest
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
from routers import query


def _make_query_endpoint_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            store_id TEXT
        );
        CREATE TABLE employee_profiles (
            id INTEGER PRIMARY KEY,
            employee_id TEXT,
            store_id TEXT
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
        INSERT INTO users (id, store_id) VALUES (40, 'STORE_A');
        """
    )
    return conn


@contextmanager
def _query_conn_ctx(conn: sqlite3.Connection):
    yield conn


class QueryNaturalReplyTests(unittest.TestCase):
    def _employee_rows(self, count: int = 20) -> list[dict[str, object]]:
        return [
            {
                "employee_name": f"员工{i:02d}",
                "store_name": "南京东路店",
                "position": "珠宝顾问",
                "high_risk_count": i,
            }
            for i in range(1, count + 1)
        ]

    def test_employee_reply_lists_returned_names_with_second_paragraph(self) -> None:
        rows = self._employee_rows()

        reply = query._employee_natural_reply("有哪些高风险员工？", rows)

        self.assertIn("共查到20位员工", reply)
        self.assertIn("员工01", reply)
        self.assertIn("员工20", reply)
        paragraphs = [part for part in reply.split("\n\n") if part.strip()]
        self.assertGreaterEqual(len(paragraphs), 2)
        self.assertIn("具体看", paragraphs[1])

    def test_employee_structured_sections_use_natural_text_not_field_blocks(self) -> None:
        rows = self._employee_rows()

        sections = query._build_structured_sections("有哪些高风险员工？", rows, "success")
        flattened = query._structured_sections_to_text(sections)

        self.assertIn("员工20", flattened)
        self.assertNotIn("结果明细", flattened)
        self.assertNotIn("展开", flattened)
        self.assertNotIn("补充说明", flattened)
        self.assertTrue(all("fields" not in section for section in sections))
        self.assertGreaterEqual(len(sections), 2)

    def test_generic_structured_sections_do_not_reference_expandable_details(self) -> None:
        rows = [
            {"store_name": f"门店{i:02d}", "completion_rate": 90 - i, "record_count": i}
            for i in range(1, 8)
        ]

        sections = query._build_structured_sections("哪些门店培训完成率低？", rows, "success")
        flattened = query._structured_sections_to_text(sections)

        self.assertNotIn("数据明细", flattened)
        self.assertNotIn("结果明细", flattened)
        self.assertNotIn("可继续展开查看", flattened)
        self.assertNotIn("补充说明", flattened)
        self.assertTrue(all("fields" not in section for section in sections))

    def test_workflow_reply_payload_prefers_query2_natural_output(self) -> None:
        rows = self._employee_rows(8)
        natural_reply = "最近需要重点关注的员工主要集中在高风险名单里。\\n\\n先看代表性对象：员工01、员工02、员工03。"

        with mock.patch.object(
            query,
            "run_query2_workflow",
            return_value={
                "ok": True,
                "data": {
                    "workflow_status": "success",
                    "summary": "最近需要重点关注的员工主要集中在高风险名单里。",
                    "manager_advice": "建议先跟进前三位员工。",
                    "focus_names": ["员工01", "员工02"],
                    "display_tags": ["已出结果"],
                    "user_visible_output": natural_reply,
                },
            },
        ):
            payload, error = query._workflow_reply_payload(
                user_id="U1001",
                store_id="STORE01",
                query_text="有哪些高风险员工？",
                query_type="recent_high_risk_staff",
                params_json={},
                query_status="success",
                result_rows=rows,
                error_message="",
            )

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        self.assertEqual(natural_reply, payload["reply_text"])
        self.assertEqual(natural_reply, payload["user_visible_output"])
        self.assertEqual(
            natural_reply,
            query._structured_sections_to_text(payload["structured_sections"]),
        )

    def test_workflow_reply_payload_falls_back_when_query2_output_empty(self) -> None:
        rows = self._employee_rows(8)

        with mock.patch.object(
            query,
            "run_query2_workflow",
            return_value={
                "ok": True,
                "data": {
                    "workflow_status": "success",
                    "summary": "",
                    "manager_advice": "",
                    "focus_names": [],
                    "display_tags": [],
                    "user_visible_output": "",
                },
            },
        ):
            payload, error = query._workflow_reply_payload(
                user_id="U1001",
                store_id="STORE01",
                query_text="有哪些高风险员工？",
                query_type="recent_high_risk_staff",
                params_json={},
                query_status="success",
                result_rows=rows,
                error_message="",
            )

        self.assertIsNone(payload)
        self.assertEqual("empty_workflow_output", error["reason"])

    def test_local_parse_treats_training_incomplete_as_all_staff(self) -> None:
        result = query._local_parse("有谁没有参加培训")

        self.assertEqual("training_incomplete_staff", result["query_type"])
        self.assertEqual("all_staff", result["params_json"]["target_group"])

    def test_normalize_supported_query_type_preserves_newcomer_scope(self) -> None:
        qtype, params = query._normalize_supported_query_type(
            "本月哪些新人培训没完成",
            "training_unfinished_newcomer",
            {"target_group": "newcomer", "time_range": "this_month"},
        )

        self.assertEqual("training_unfinished_newcomer", qtype)
        self.assertEqual("newcomer", params["target_group"])

    def test_normalize_supported_query_type_coerces_broad_training_query(self) -> None:
        qtype, params = query._normalize_supported_query_type(
            "一共有多少人没有参加培训",
            "training_unfinished_newcomer",
            {"target_group": "newcomer", "time_range": "this_month"},
        )

        self.assertEqual("training_incomplete_staff", qtype)
        self.assertEqual("all_staff", params["target_group"])

    def test_training_progress_metric_snippet_uses_completed_vs_required(self) -> None:
        snippet = query._single_row_metric_snippet(
            {
                "training_completed": 1,
                "training_required": 7,
                "completion_rate": 14.3,
            }
        )

        self.assertEqual("当前进度 1/7", snippet)

    def test_generic_sql_without_sql_is_not_treated_as_template_hit(self) -> None:
        self.assertFalse(
            query._is_executable_template_hit(
                route_type="template_hit",
                query_type="generic_sql",
                sql_raw="",
            )
        )

    def test_generic_sql_with_sql_is_treated_as_template_hit(self) -> None:
        self.assertTrue(
            query._is_executable_template_hit(
                route_type="template_hit",
                query_type="generic_sql",
                sql_raw="SELECT COUNT(*) AS store_count FROM stores",
            )
        )


    def test_count_star_rows_answer_with_metric_value_not_row_count(self) -> None:
        payload = query._answer_payload_from_data(
            "\u5168\u7cfb\u7edf\u6709\u51e0\u4e2a\u95e8\u5e97",
            [{"COUNT(*)": 5}],
            "success",
        )

        self.assertIn("\u5f53\u524d\u7cfb\u7edf\u5171\u67095\u5bb6\u95e8\u5e97", payload["reply_text"])
        self.assertNotIn("1 \u6761\u76f8\u5173\u7ed3\u679c", payload["reply_text"])

    def test_total_employee_alias_answers_with_user_count(self) -> None:
        payload = query._answer_payload_from_data(
            "\u7cfb\u7edf\u91cc\u4e00\u5171\u6709\u591a\u5c11\u7528\u6237",
            [{"total_employees": 3}],
            "success",
        )

        self.assertIn("\u5f53\u524d\u7cfb\u7edf\u5171\u67093\u4e2a\u7528\u6237", payload["reply_text"])
        self.assertNotIn("1 \u6761\u76f8\u5173\u7ed3\u679c", payload["reply_text"])

    def test_workflow_sql_result_reads_sql_from_fastapi_payload(self) -> None:
        sql, params, data = query._workflow_sql_result(
            {
                "data": {
                    "query_type": "generic_sql",
                    "fastapi_payload_json": {
                        "sql": "SELECT COUNT(*) FROM employee_profiles",
                        "sql_params_json": ["STORE_GZ"],
                    },
                }
            }
        )

        self.assertEqual("SELECT COUNT(*) FROM employee_profiles", sql)
        self.assertEqual(["STORE_GZ"], params)
        self.assertEqual("generic_sql", data["query_type"])

    def test_query_summarize_unsupported_returns_local_reply_not_502(self) -> None:
        conn = _make_query_endpoint_conn()
        original_get_conn = query.get_conn
        query.get_conn = lambda: _query_conn_ctx(conn)
        app = FastAPI()
        app.include_router(query.router)
        app.dependency_overrides[auth.get_current_user] = lambda: {
            "user_id": "40",
            "role": "store_manager",
            "username": "manager",
            "store_id": "STORE_A",
        }
        try:
            client = TestClient(app)
            response = client.post(
                "/api/query/summarize",
                json={
                    "query_text": "帮我预测明年金价",
                    "parsed_intent": "unsupported",
                    "query_status": "error",
                },
            )
        finally:
            query.get_conn = original_get_conn
            conn.close()

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(200, payload["code"])
        self.assertIn("没有命中", payload["data"]["reply_text"])
        self.assertEqual("local_fallback", payload["data"]["reply_mode"])


    def test_query_ask_does_not_return_database_record_ids(self) -> None:
        conn = _make_query_endpoint_conn()
        original_get_conn = query.get_conn
        query.get_conn = lambda: _query_conn_ctx(conn)
        app = FastAPI()
        app.include_router(query.router)
        app.dependency_overrides[auth.get_current_user] = lambda: {
            "user_id": "40",
            "role": "store_manager",
            "username": "manager",
            "store_id": "STORE_A",
        }
        local_result = {
            "query_type": "generic_sql",
            "params_json": {},
            "result_rows": [
                {
                    "id": 99,
                    "store_id": "STORE_A",
                    "employee_id": "40",
                    "store_name": "广州天河精品店",
                    "sales_amount": 111400.0,
                }
            ],
            "query_status": "success",
            "reply_text": "广州天河精品店销售额最高。",
            "summary": "广州天河精品店销售额最高。",
            "display_tags": ["本地固定查询"],
            "focus_names": ["广州天河精品店"],
            "skip_query2": True,
            "template_id": "sales_amount_check",
        }
        try:
            client = TestClient(app)
            with mock.patch.object(query, "try_local_query_template", return_value=local_result):
                response = client.post(
                    "/api/query/ask",
                    json={"query_text": "销售额最高的门店"},
                )
        finally:
            query.get_conn = original_get_conn
            conn.close()

        self.assertEqual(200, response.status_code)
        data = response.json()["data"]
        self.assertNotIn("db_record_id", data)
        self.assertNotIn("id", data["result_rows"][0])
        self.assertNotIn("store_id", data["result_rows"][0])
        self.assertNotIn("employee_id", data["result_rows"][0])


if __name__ == "__main__":
    unittest.main()
