from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
import routers.practice as practice_router


def _json(value):
    return json.dumps(value, ensure_ascii=False)


class TestPracticeTurnFeedback:
    def setup_method(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.temp_db = Path(self.tmpdir.name) / "practice_turn_feedback.db"
        self._create_schema()

        self.original_get_conn = practice_router.get_conn
        self.original_run_practice1_chat = practice_router.run_practice1_chat
        self.original_upsert_employee_profile = practice_router.upsert_employee_profile
        practice_router.get_conn = self._conn
        practice_router.upsert_employee_profile = lambda *args, **kwargs: None

        app = FastAPI()
        app.include_router(practice_router.router)
        self.current_user = {"user_id": "1", "role": "trainee", "username": "tester", "store_id": "STORE01"}
        app.dependency_overrides[auth.get_current_user] = lambda: self.current_user
        self.client = TestClient(app)

    def teardown_method(self) -> None:
        practice_router.get_conn = self.original_get_conn
        practice_router.run_practice1_chat = self.original_run_practice1_chat
        practice_router.upsert_employee_profile = self.original_upsert_employee_profile
        self.tmpdir.cleanup()

    def _create_schema(self) -> None:
        conn = sqlite3.connect(self.temp_db)
        try:
            conn.execute(
                """
                CREATE TABLE practice_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    practice_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    scenario_type TEXT NOT NULL DEFAULT '',
                    difficulty TEXT NOT NULL DEFAULT '',
                    trainee_role TEXT NOT NULL DEFAULT '',
                    dialogue_text TEXT NOT NULL DEFAULT '',
                    round_count INTEGER NOT NULL DEFAULT 0,
                    end_flag INTEGER NOT NULL DEFAULT 0,
                    module_code TEXT NOT NULL DEFAULT '',
                    module_name TEXT NOT NULL DEFAULT '',
                    score_branch TEXT NOT NULL DEFAULT 'practice',
                    cycle_id TEXT NOT NULL DEFAULT '',
                    stage_no INTEGER,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    employee_id TEXT NOT NULL DEFAULT '',
                    scene_code TEXT NOT NULL DEFAULT '',
                    difficulty_level TEXT NOT NULL DEFAULT '',
                    user_message TEXT NOT NULL DEFAULT '',
                    assistant_reply TEXT NOT NULL DEFAULT '',
                    suggested_response TEXT NOT NULL DEFAULT '',
                    next_focus_json TEXT NOT NULL DEFAULT '[]',
                    conversation_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}'
                )
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

    def test_chat_uses_dify_turn_feedback_and_persists_it(self) -> None:
        expected_feedback = {
            "intent_label": "价格试探",
            "intent_reason": "顾客连续追问为什么贵，核心在试探价值空间。",
            "customer_state": "谨慎",
            "mentor_comment": "这轮先别急着降价，先把价值拆清楚。",
            "next_action": "先确认顾客更在意预算还是佩戴场景。",
            "next_question": "您更担心超预算，还是担心戴着不值这个价？",
            "voice_advice": "先别降价，先确认顾客在意预算还是价值感。",
            "risk_flag": "避免直接承诺保值或最低价。",
        }

        def fake_run_practice1_chat(**kwargs):
            return {
                "ok": True,
                "raw": {
                    "answer": "我就是想知道这款为什么比别家贵。",
                    "conversation_id": "conv_turn_1",
                    "metadata": {"turn_feedback": expected_feedback},
                },
                "data": {
                    "assistant_reply": "我就是想知道这款为什么比别家贵。",
                    "conversation_id": "conv_turn_1",
                },
            }

        practice_router.run_practice1_chat = fake_run_practice1_chat

        response = self.client.post(
            "/api/practice/chat",
            json={
                "session_id": "ps_turn_feedback_1",
                "scene_code": "objection_handling",
                "module_code": "objection_handling",
                "difficulty_level": "standard",
                "user_message": "这款为什么这么贵？",
                "conversation_id": "",
                "action": "send",
            },
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["data"]["turn_feedback"] == expected_feedback
        assert payload["data"]["conversation"][-1]["turn_feedback"] == expected_feedback

        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload_json, conversation_json FROM practice_records WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                ("ps_turn_feedback_1",),
            ).fetchone()

        stored_payload = json.loads(row["payload_json"])
        stored_conversation = json.loads(row["conversation_json"])
        assert stored_payload["turn_feedback"] == expected_feedback
        assert stored_conversation[-1]["turn_feedback"] == expected_feedback

    def test_chat_builds_fallback_turn_feedback_when_dify_missing_fields(self) -> None:
        def fake_run_practice1_chat(**kwargs):
            return {
                "ok": True,
                "raw": {
                    "answer": "我再看看吧，现在还是觉得价格有点虚高。",
                    "conversation_id": "conv_turn_2",
                },
                "data": {
                    "assistant_reply": "我再看看吧，现在还是觉得价格有点虚高。",
                    "conversation_id": "conv_turn_2",
                },
            }

        practice_router.run_practice1_chat = fake_run_practice1_chat

        response = self.client.post(
            "/api/practice/chat",
            json={
                "session_id": "ps_turn_feedback_2",
                "scene_code": "objection_handling",
                "module_code": "objection_handling",
                "difficulty_level": "standard",
                "user_message": "老师说这款保证升值，你再便宜点我就考虑。",
                "conversation_id": "",
                "action": "send",
            },
        )

        payload = response.json()
        turn_feedback = payload["data"]["turn_feedback"]

        assert response.status_code == 200
        for field in (
            "intent_label",
            "intent_reason",
            "customer_state",
            "mentor_comment",
            "next_action",
            "next_question",
            "voice_advice",
        ):
            assert isinstance(turn_feedback[field], str)
            assert turn_feedback[field].strip()
        assert "risk_flag" in turn_feedback
        assert payload["data"]["risk_hit_count"] == 1

    def test_chat_builds_material_compare_feedback_from_customer_reply(self) -> None:
        assistant_reply = "我都说了是黄金和K金的区别啊，K金款式年轻一点，但黄金不是更保值吗？这两种到底怎么选呢？"

        def fake_run_practice1_chat(**kwargs):
            return {
                "ok": True,
                "raw": {
                    "answer": assistant_reply,
                    "conversation_id": "conv_turn_2b",
                },
                "data": {
                    "assistant_reply": assistant_reply,
                    "conversation_id": "conv_turn_2b",
                },
            }

        practice_router.run_practice1_chat = fake_run_practice1_chat

        response = self.client.post(
            "/api/practice/chat",
            json={
                "session_id": "ps_turn_feedback_2b",
                "scene_code": "jewelry_recommendation",
                "module_code": "jewelry_recommendation",
                "difficulty_level": "standard",
                "user_message": "您更想先看哪部分？",
                "conversation_id": "",
                "action": "send",
            },
        )

        payload = response.json()
        turn_feedback = payload["data"]["turn_feedback"]

        assert response.status_code == 200
        assert turn_feedback["intent_label"] == "材质对比"
        assert "黄金" in turn_feedback["mentor_comment"]
        assert "K 金" in turn_feedback["mentor_comment"]
        assert "保值" in turn_feedback["voice_advice"]
        assert "款式" in turn_feedback["voice_advice"]
        assert turn_feedback["voice_advice"] != "先问实顾客卡点，再顺着回应。"

    def test_resume_keeps_turn_feedback_history(self) -> None:
        expected_feedback = {
            "intent_label": "犹豫观望",
            "intent_reason": "顾客没有拒绝，只是在拖延决策。",
            "customer_state": "观望",
            "mentor_comment": "先把顾客卡住的点问实，不要急着连发卖点。",
            "next_action": "追问顾客最担心的决策成本。",
            "next_question": "您现在主要还没想定的是预算、款式，还是送礼场景？",
            "voice_advice": "先问实顾客卡点，再推进下一步。",
        }

        def fake_run_practice1_chat(**kwargs):
            return {
                "ok": True,
                "raw": {
                    "answer": "我再想想，今天先不急着决定。",
                    "conversation_id": "conv_turn_3",
                    "metadata": {"turn_feedback": expected_feedback},
                },
                "data": {
                    "assistant_reply": "我再想想，今天先不急着决定。",
                    "conversation_id": "conv_turn_3",
                },
            }

        practice_router.run_practice1_chat = fake_run_practice1_chat

        first = self.client.post(
            "/api/practice/chat",
            json={
                "session_id": "ps_turn_feedback_3",
                "scene_code": "jewelry_recommendation",
                "module_code": "closing_conversion",
                "difficulty_level": "advanced",
                "user_message": "您如果今天拿不定，我可以先帮您梳理两个方案。",
                "conversation_id": "",
                "action": "send",
            },
        )
        first_payload = first.json()
        assert first.status_code == 200

        resumed = self.client.post(
            "/api/practice/chat",
            json={
                "session_id": "ps_turn_feedback_3",
                "scene_code": "jewelry_recommendation",
                "module_code": "closing_conversion",
                "difficulty_level": "advanced",
                "user_message": "",
                "conversation_id": "",
                "action": "resume",
            },
        )

        resumed_payload = resumed.json()
        assert resumed.status_code == 200
        assert resumed_payload["data"]["assistant_reply"] == "我再想想，今天先不急着决定。"
        assert resumed_payload["data"]["conversation"][-1]["turn_feedback"] == expected_feedback
        assert resumed_payload["data"]["conversation"] == first_payload["data"]["conversation"]
