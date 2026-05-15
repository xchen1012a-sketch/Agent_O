from __future__ import annotations

import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import Base, utc_now
from models import AssessmentRecord, AssessmentTask, User
from routers import assessment as assessment_router
from routers import task as task_router
from schemas import AssessmentChatReq, AssessmentSubmitPaperReq


class AssessmentExamCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.current_user = {"user_id": "u-001", "role": "trainee"}

        user = User(
            user_id="u-001",
            username="tester",
            hashed_password="hashed",
            name="Tester",
            display_name="Tester",
            store_id="store-001",
            role="trainee",
        )
        self.db.add(user)
        self.db.commit()

    def _create_task(self, **overrides) -> AssessmentTask:
        task = AssessmentTask(
            task_name=overrides.pop("task_name", "标准试卷"),
            task_type=overrides.pop("task_type", "assessment"),
            task_desc=overrides.pop("task_desc", "desc"),
            module_code=overrides.pop("module_code", "module-a"),
            paper_config_json=overrides.pop(
                "paper_config_json",
                json.dumps(
                    {
                        "exam_title": "标准试卷",
                        "questions": [
                            {
                                "id": "q1",
                                "type": "single",
                                "title": "标准答案",
                                "score": 10,
                                "answer": "A",
                                "options": [{"value": "A", "label": "A"}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
            publisher_id=overrides.pop("publisher_id", "admin"),
            target_scope=overrides.pop("target_scope", "all"),
            deadline=overrides.pop("deadline", utc_now() + timedelta(days=1)),
            pass_score=overrides.pop("pass_score", 85.0),
            status=overrides.pop("status", "active"),
            exam_mode=overrides.pop("exam_mode", "paper_exam"),
            duration_minutes=overrides.pop("duration_minutes", 60),
            score_visibility=overrides.pop("score_visibility", "public"),
            publish_status=overrides.pop("publish_status", "published"),
            allow_retake=overrides.pop("allow_retake", 1),
            max_attempts=overrides.pop("max_attempts", 1),
            auto_submit_on_timeout=overrides.pop("auto_submit_on_timeout", 1),
            **overrides,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def _create_blind_box_task(self, **overrides) -> AssessmentTask:
        return self._create_task(
            task_name=overrides.pop("task_name", "AI盲盒考核"),
            task_desc=overrides.pop("task_desc", "高压顾客随机对话考核"),
            exam_mode=overrides.pop("exam_mode", "ai_blind_box_exam"),
            paper_config_json=overrides.pop("paper_config_json", "{}"),
            **overrides,
        )

    def _create_record(self, task_id: int, **overrides) -> AssessmentRecord:
        record = AssessmentRecord(
            task_id=task_id,
            user_id=overrides.pop("user_id", "u-001"),
            employee_name=overrides.pop("employee_name", "Tester"),
            attempt_no=overrides.pop("attempt_no", 1),
            score=overrides.pop("score", 0.0),
            is_pass=overrides.pop("is_pass", 0),
            comment=overrides.pop("comment", ""),
            started_at=overrides.pop("started_at", utc_now() - timedelta(minutes=5)),
            expires_at=overrides.pop("expires_at", utc_now() + timedelta(minutes=30)),
            submit_status=overrides.pop("submit_status", "in_progress"),
            is_score_visible_to_user=overrides.pop("is_score_visible_to_user", 1),
            score_visibility_snapshot=overrides.pop("score_visibility_snapshot", "public"),
            exam_mode_snapshot=overrides.pop("exam_mode_snapshot", "paper_exam"),
            task_version_snapshot=overrides.pop("task_version_snapshot", 1),
            **overrides,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def test_my_tasks_keeps_live_in_progress_exam_in_pending_groups(self) -> None:
        task = self._create_task(max_attempts=1)
        self._create_record(task.id, submit_status="in_progress", expires_at=utc_now() + timedelta(minutes=20))

        response = task_router.my_tasks(db=self.db, current_user=self.current_user)

        self.assertEqual(response["code"], 200)
        self.assertEqual(len(response["data"]["completed"]), 0)
        self.assertEqual(len(response["data"]["retake"]), 1)
        self.assertEqual(response["data"]["retake"][0]["submit_status"], "in_progress")
        self.assertGreater(response["data"]["retake"][0]["remaining_seconds"], 0)

    def test_expired_max_attempt_record_moves_to_history(self) -> None:
        task = self._create_task(max_attempts=1)
        record = self._create_record(
            task.id,
            submit_status="in_progress",
            expires_at=utc_now() - timedelta(minutes=1),
            started_at=utc_now() - timedelta(minutes=61),
        )

        task_response = task_router.my_tasks(db=self.db, current_user=self.current_user)
        history_response = assessment_router.assessment_history(db=self.db, current_user=self.current_user)

        self.db.refresh(record)
        self.assertEqual(record.submit_status, "timeout_submitted")
        self.assertEqual(len(task_response["data"]["completed"]), 1)
        self.assertEqual(task_response["data"]["completed"][0]["submit_status"], "timeout_submitted")
        self.assertEqual(len(history_response["data"]["items"]), 1)
        self.assertEqual(history_response["data"]["items"][0]["record_id"], record.id)
        self.assertEqual(history_response["data"]["items"][0]["submit_status"], "timeout_submitted")

    def test_submit_paper_falls_back_to_local_grading_when_wf14_fails(self) -> None:
        task = self._create_task()
        record = self._create_record(task.id, submit_status="in_progress")
        original_runner = assessment_router.run_wf14_grade_paper
        assessment_router.run_wf14_grade_paper = lambda **kwargs: {
            "ok": False,
            "reason": "dify_exception",
            "error": "workflow down",
            "raw": {},
        }
        self.addCleanup(setattr, assessment_router, "run_wf14_grade_paper", original_runner)

        response = assessment_router.submit_paper(
            body=AssessmentSubmitPaperReq(record_id=record.id, answers={"q1": "A"}),
            db=self.db,
            current_user=self.current_user,
        )

        self.db.refresh(record)
        self.assertEqual(response["code"], 200)
        self.assertEqual(response["data"]["record_id"], record.id)
        self.assertEqual(response["data"]["score"], 100.0)
        self.assertEqual(record.submit_status, "submitted")
        self.assertEqual(record.review_source, "local_fallback")
        self.assertIn("本地判分", record.comment)


    def test_assessment_chat_returns_normal_coach_payload(self) -> None:
        task = self._create_blind_box_task()
        record = self._create_record(task.id, exam_mode_snapshot="ai_blind_box_exam", submit_status="in_progress")

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "answer": "请你说明为什么值得购买。",
                    "conversation_id": "conv-001",
                }

        original_api_key = assessment_router._wf11_api_key
        original_httpx_post = assessment_router.httpx.post
        assessment_router._wf11_api_key = lambda: "test-key"
        assessment_router.httpx.post = lambda *args, **kwargs: _FakeResponse()
        self.addCleanup(setattr, assessment_router, "_wf11_api_key", original_api_key)
        self.addCleanup(setattr, assessment_router.httpx, "post", original_httpx_post)

        response = assessment_router.assessment_chat(
            body=AssessmentChatReq(record_id=record.id, message="我会先了解顾客需求，再说明材质工艺价值"),
            db=self.db,
            current_user=self.current_user,
        )

        self.assertEqual(response["code"], 200)
        self.assertEqual(response["data"]["conversation_id"], "conv-001")
        coach = response["data"]["coach"]
        self.assertEqual(coach["phase"], "opening")
        self.assertIn(coach["intent_label"], {"需求确认", "价值拆解", "开场引导"})
        self.assertTrue(coach["hint_text"])
        self.assertFalse(coach["should_speak"])

    def test_assessment_chat_returns_stuck_coach_payload_for_short_reply(self) -> None:
        task = self._create_blind_box_task()
        record = self._create_record(task.id, exam_mode_snapshot="ai_blind_box_exam", submit_status="in_progress")

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "answer": "顾客继续追问，请补充你的推荐理由。",
                    "conversation_id": "conv-002",
                }

        original_api_key = assessment_router._wf11_api_key
        original_httpx_post = assessment_router.httpx.post
        assessment_router._wf11_api_key = lambda: "test-key"
        assessment_router.httpx.post = lambda *args, **kwargs: _FakeResponse()
        self.addCleanup(setattr, assessment_router, "_wf11_api_key", original_api_key)
        self.addCleanup(setattr, assessment_router.httpx, "post", original_httpx_post)

        response = assessment_router.assessment_chat(
            body=AssessmentChatReq(record_id=record.id, message="嗯"),
            db=self.db,
            current_user=self.current_user,
        )

        self.assertEqual(response["code"], 200)
        coach = response["data"]["coach"]
        self.assertEqual(coach["phase"], "stuck")
        self.assertEqual(coach["urgency"], "normal")
        self.assertTrue(coach["should_speak"])
        self.assertTrue(coach["hint_text"])

    def test_assessment_chat_returns_result_debrief_coach_when_finished(self) -> None:
        task = self._create_blind_box_task()
        record = self._create_record(task.id, exam_mode_snapshot="ai_blind_box_exam", submit_status="in_progress")

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "answer": json.dumps(
                        {
                            "is_finished": True,
                            "score": 88,
                            "is_pass": 1,
                            "reason": "你能接住顾客顾虑，但收口还可以更明确。",
                        },
                        ensure_ascii=False,
                    ),
                    "conversation_id": "conv-003",
                }

        original_api_key = assessment_router._wf11_api_key
        original_httpx_post = assessment_router.httpx.post
        assessment_router._wf11_api_key = lambda: "test-key"
        assessment_router.httpx.post = lambda *args, **kwargs: _FakeResponse()
        self.addCleanup(setattr, assessment_router, "_wf11_api_key", original_api_key)
        self.addCleanup(setattr, assessment_router.httpx, "post", original_httpx_post)

        response = assessment_router.assessment_chat(
            body=AssessmentChatReq(record_id=record.id, message="我会先认同顾虑，再说明证据和售后保障"),
            db=self.db,
            current_user=self.current_user,
        )

        self.assertEqual(response["code"], 200)
        self.assertTrue(response["data"]["is_finished"])
        coach = response["data"]["coach"]
        self.assertEqual(coach["phase"], "result_debrief")
        self.assertEqual(coach["urgency"], "normal")
        self.assertTrue(coach["should_speak"])
        self.assertIn("复盘", coach["intent_label"])


if __name__ == "__main__":
    unittest.main()
