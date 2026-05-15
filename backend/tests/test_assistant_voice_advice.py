from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import BackgroundTasks

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import assistant_service
from routers import assistant as assistant_router
from schemas import AssistantReplyRequest, AssistantReplyResponse


class AssistantVoiceAdviceTests(unittest.TestCase):
    def test_build_voice_advice_prefers_existing_coach_tip(self) -> None:
        advice = assistant_service.build_voice_advice(
            coach_tip="先认同预算顾虑，再拆价值和使用场景，别急着报优惠。",
            scene_input="顾客觉得这款太贵了",
            reply_script="这款贵主要因为材质和工艺更好。",
            matched_knowledge="价格异议",
            reply_compliance_tag="safe",
        )

        self.assertEqual(advice, "先认同预算顾虑，再拆价值和使用场景，别急着报优惠。")
        self.assertNotIn("材质和工艺更好", advice)

    def test_build_voice_advice_falls_back_to_generated_tip_when_coach_tip_empty(self) -> None:
        advice = assistant_service.build_voice_advice(
            coach_tip="",
            scene_input="顾客在真假和证书上反复确认",
            reply_script="可以先介绍证书和材质依据，再说明售后。",
            matched_knowledge="证书说明",
            reply_compliance_tag="safe",
        )

        self.assertTrue(advice)
        self.assertIn("证书", advice)
        self.assertNotIn("可以先介绍证书和材质依据", advice)

    def test_route_returns_voice_advice_field(self) -> None:
        original_run = assistant_router.run_assistant1_sync
        original_is_chat_configured = assistant_router.is_assistant_chat_configured
        original_persist = assistant_router.persist_reply_record

        assistant_router.run_assistant1_sync = lambda **kwargs: {
            "response": AssistantReplyResponse(
                reply_script="完整回复话术",
                followup_question="顾客更在意预算还是款式？",
                coach_tip="先认同预算顾虑，再拆价值和使用场景，别急着报优惠。",
                voice_advice="先认同预算顾虑，再拆价值和使用场景，别急着报优惠。",
            ),
            "use_dify": True,
            "workflow_reason": "",
            "dify_error": "",
            "reply_compliance_tag": "safe",
            "matched_knowledge": "价格异议",
        }
        assistant_router.is_assistant_chat_configured = lambda: False
        assistant_router.persist_reply_record = lambda **kwargs: 1

        self.addCleanup(setattr, assistant_router, "run_assistant1_sync", original_run)
        self.addCleanup(setattr, assistant_router, "is_assistant_chat_configured", original_is_chat_configured)
        self.addCleanup(setattr, assistant_router, "persist_reply_record", original_persist)

        response = assistant_router.assistant_reply(
            AssistantReplyRequest(scene_input="顾客说太贵了", history=[]),
            BackgroundTasks(),
            current_user={"user_id": "1", "role": "admin", "store_id": "STORE01"},
        )

        self.assertEqual(response["code"], 200)
        self.assertEqual(
            response["data"]["voice_advice"],
            "先认同预算顾虑，再拆价值和使用场景，别急着报优惠。",
        )
        self.assertEqual(response["data"]["reply_script"], "完整回复话术")
        self.assertEqual(response["data"]["followup_question"], "顾客更在意预算还是款式？")


if __name__ == "__main__":
    unittest.main()
