from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from routers import qa as qa_router
from schemas import QaAskRequest


class QaPlainReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_resolve_user_context = qa_router.resolve_user_context
        self.original_fetch_qa_citations = qa_router.fetch_qa_citations
        self.original_run_qa1_workflow = qa_router.run_qa1_workflow
        self.original_is_qa_chat_configured = qa_router.is_qa_chat_configured
        self.original_run_qa_chat_sync = qa_router.run_qa_chat_sync

    def tearDown(self) -> None:
        qa_router.resolve_user_context = self.original_resolve_user_context
        qa_router.fetch_qa_citations = self.original_fetch_qa_citations
        qa_router.run_qa1_workflow = self.original_run_qa1_workflow
        qa_router.is_qa_chat_configured = self.original_is_qa_chat_configured
        qa_router.run_qa_chat_sync = self.original_run_qa_chat_sync

    def test_success_branch_returns_plain_answer_text_and_related_questions(self) -> None:
        qa_router.resolve_user_context = lambda current_user: {
            "user_id": "1",
            "role": "admin",
            "store_id": "STORE01",
        }
        qa_router.fetch_qa_citations = lambda question: [
            {"document_name": "doc", "snippet": "snippet"}
        ]
        qa_router.is_qa_chat_configured = lambda: True
        qa_router.run_qa1_workflow = lambda **kwargs: {
            "ok": True,
            "data": {
                "answer_text": "\u4f60\u597d\uff0c\u6211\u53ef\u4ee5\u5e2e\u4f60\u89e3\u7b54\u4ea7\u54c1\u77e5\u8bc6\u95ee\u9898\u3002",
                "answer_brief": "\u5148\u7ed9\u7ed3\u8bba\uff1a\u8fd9\u91cc\u5148\u5224\u65ad\u3002",
                "answer_reason": "\u56e0\u4e3a\u8981\u5148\u5224\u65ad\u3002",
                "answer_example": "\u53ef\u4ee5\u8fd9\u6837\u8bf4\uff1a\u5148\u5224\u65ad\u518d\u89e3\u91ca\u3002",
                "coach_question": "\u8981\u4e0d\u8981\u7ee7\u7eed\u95ee\uff1f",
                "related_questions": ["\u767d\u94f6\u548c925\u94f6\u6709\u4ec0\u4e48\u533a\u522b\uff1f"],
                "conversation_id": "conv-1",
            },
        }
        qa_router.run_qa_chat_sync = lambda **kwargs: {
            "turn_feedback": {"voice_advice": "\u5148\u8bb0\u4f4f75%\u8fd9\u4e2a\u6570\u5b57"},
            "qa_chat_conversation_id": "qa-chat-1",
        }

        response = qa_router.qa_ask(
            QaAskRequest(question="\u4f60\u597d", history=[], qa_chat_conversation_id="qa-prev"),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertEqual(
            "\u4f60\u597d\uff0c\u6211\u53ef\u4ee5\u5e2e\u4f60\u89e3\u7b54\u4ea7\u54c1\u77e5\u8bc6\u95ee\u9898\u3002",
            data["answer_text"],
        )
        self.assertEqual("", data["answer_brief"])
        self.assertEqual("", data["answer_reason"])
        self.assertEqual("", data["answer_example"])
        self.assertEqual("", data["coach_question"])
        self.assertEqual("", data["knowledge_patch"])
        self.assertEqual("", data["answer_subtype"])
        self.assertEqual(["\u767d\u94f6\u548c925\u94f6\u6709\u4ec0\u4e48\u533a\u522b\uff1f"], data["related_questions"])
        self.assertEqual("conv-1", data["conversation_id"])
        self.assertEqual("qa-chat-1", data["qa_chat_conversation_id"])
        self.assertEqual(
            {"voice_advice": "\u5148\u8bb0\u4f4f75%\u8fd9\u4e2a\u6570\u5b57"},
            data["turn_feedback"],
        )

    def test_success_branch_without_qa_chat_keeps_turn_feedback_empty(self) -> None:
        qa_router.resolve_user_context = lambda current_user: {
            "user_id": "1",
            "role": "admin",
            "store_id": "STORE01",
        }
        qa_router.fetch_qa_citations = lambda question: []
        qa_router.is_qa_chat_configured = lambda: False
        qa_router.run_qa1_workflow = lambda **kwargs: {
            "ok": True,
            "data": {
                "answer_text": "\u6807\u51c6\u77e5\u8bc6\u95ee\u7b54",
                "related_questions": [],
                "conversation_id": "conv-only-qa1",
            },
        }

        response = qa_router.qa_ask(
            QaAskRequest(question="\u4f60\u597d", history=[]),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertEqual("conv-only-qa1", data["conversation_id"])
        self.assertEqual("", data["qa_chat_conversation_id"])
        self.assertIsNone(data["turn_feedback"])

    def test_success_branch_with_invalid_qa_chat_feedback_falls_back_cleanly(self) -> None:
        qa_router.resolve_user_context = lambda current_user: {
            "user_id": "1",
            "role": "admin",
            "store_id": "STORE01",
        }
        qa_router.fetch_qa_citations = lambda question: []
        qa_router.is_qa_chat_configured = lambda: True
        qa_router.run_qa1_workflow = lambda **kwargs: {
            "ok": True,
            "data": {
                "answer_text": "\u6807\u51c6\u77e5\u8bc6\u95ee\u7b54",
                "related_questions": [],
                "conversation_id": "conv-valid-answer",
            },
        }
        qa_router.run_qa_chat_sync = lambda **kwargs: {
            "turn_feedback": None,
            "qa_chat_conversation_id": "qa-chat-bad-json",
        }

        response = qa_router.qa_ask(
            QaAskRequest(question="\u4f60\u597d", history=[]),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertEqual("qa-chat-bad-json", data["qa_chat_conversation_id"])
        self.assertIsNone(data["turn_feedback"])

    def test_fallback_branch_does_not_return_qa_chat_feedback(self) -> None:
        qa_router.resolve_user_context = lambda current_user: {
            "user_id": "1",
            "role": "admin",
            "store_id": "STORE01",
        }
        qa_router.fetch_qa_citations = lambda question: [{"document_name": "doc", "snippet": "snippet"}]
        qa_router.is_qa_chat_configured = lambda: True
        qa_router.run_qa1_workflow = lambda **kwargs: {
            "ok": False,
            "reason": "dify_non_200",
            "error": "upstream failed",
            "raw": {},
        }
        qa_router.run_qa_chat_sync = lambda **kwargs: {
            "turn_feedback": {"voice_advice": "\u4e0d\u5e94\u8be5\u900f\u51fa"},
            "qa_chat_conversation_id": "qa-chat-hidden",
        }

        response = qa_router.qa_ask(
            QaAskRequest(question="\u4f60\u597d", history=[]),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertIsNone(data["turn_feedback"])
        self.assertEqual("", data["qa_chat_conversation_id"])

    def test_data_query_fallback_uses_plain_text_without_teacher_fields(self) -> None:
        response = qa_router.qa_ask(
            QaAskRequest(question="\u6700\u8fd130\u5929\u9ad8\u98ce\u9669\u5458\u5de5\u6709\u54ea\u4e9b", history=[]),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertIn("\u4e1a\u52a1\u6570\u636e\u67e5\u8be2", data["answer_text"])
        self.assertEqual("", data["answer_brief"])
        self.assertEqual("", data["answer_reason"])
        self.assertEqual("", data["answer_example"])
        self.assertEqual("", data["coach_question"])
        self.assertEqual("", data["knowledge_patch"])
        self.assertGreater(len(data["related_questions"]), 0)

    def test_unsupported_fallback_uses_plain_text_without_teacher_fields(self) -> None:
        response = qa_router.qa_ask(
            QaAskRequest(question="\u5199\u4e00\u9996\u8bd7", history=[]),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(200, response["code"])
        data = response["data"]
        self.assertIn("\u8fd9\u4e2a\u95ee\u9898\u4e0d\u5728\u5f53\u524d\u77e5\u8bc6\u95ee\u7b54\u8303\u56f4\u5185", data["answer_text"])
        self.assertEqual("", data["answer_brief"])
        self.assertEqual("", data["answer_reason"])
        self.assertEqual("", data["answer_example"])
        self.assertEqual("", data["coach_question"])
        self.assertEqual("", data["knowledge_patch"])
        self.assertGreater(len(data["related_questions"]), 0)


if __name__ == "__main__":
    unittest.main()
