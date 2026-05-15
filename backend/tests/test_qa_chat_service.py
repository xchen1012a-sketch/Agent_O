from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import qa_service


class QaChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        from dify_stage4b import run_qa_chat as real_run_qa_chat

        self.real_run_qa_chat = real_run_qa_chat

    def tearDown(self) -> None:
        import dify_stage4b

        dify_stage4b.run_qa_chat = self.real_run_qa_chat

    def test_run_qa_chat_sync_parses_turn_feedback_json(self) -> None:
        import dify_stage4b

        dify_stage4b.run_qa_chat = lambda **kwargs: {
            "ok": True,
            "data": {
                "turn_feedback_json": '{"voice_advice":"\u8bb0\u4f4f75%\u9ec4\u91d1"}',
                "conversation_id": "qa-chat-1",
            },
        }

        result = qa_service.run_qa_chat_sync(question="\u4f60\u597d", user_id="1")

        self.assertEqual("qa-chat-1", result["qa_chat_conversation_id"])
        self.assertEqual({"voice_advice": "\u8bb0\u4f4f75%\u9ec4\u91d1"}, result["turn_feedback"])

    def test_run_qa_chat_sync_returns_empty_feedback_on_invalid_json(self) -> None:
        import dify_stage4b

        dify_stage4b.run_qa_chat = lambda **kwargs: {
            "ok": True,
            "data": {
                "turn_feedback_json": '{"voice_advice":',
                "conversation_id": "qa-chat-2",
            },
        }

        result = qa_service.run_qa_chat_sync(question="\u4f60\u597d", user_id="1")

        self.assertEqual("qa-chat-2", result["qa_chat_conversation_id"])
        self.assertIsNone(result["turn_feedback"])


if __name__ == "__main__":
    unittest.main()
