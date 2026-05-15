from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agent_activity import (  # noqa: E402
    build_agent_activity_event,
    clear_agent_activity_for_tests,
    publish_agent_activity_from_request,
    recent_agent_activity,
    subscribe_agent_activity,
    unsubscribe_agent_activity,
)
from auth import create_access_token  # noqa: E402
from routers.agents import _get_activity_stream_user, _sse  # noqa: E402


class AgentActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_agent_activity_for_tests()

    def test_request_event_maps_registered_route_to_agent_workflow(self) -> None:
        event = build_agent_activity_event(
            method="POST",
            route_path="/api/practice/chat",
            status_code=200,
            request_id="req-1",
            elapsed_seconds=1.236,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["type"], "agent_call")
        self.assertEqual(event["agent_role"], "practice")
        self.assertEqual(event["agent_name"], "陪练 Agent")
        self.assertEqual(event["workflow_code"], "practice1")
        self.assertEqual(event["elapsed_label"], "1.2s")
        self.assertEqual(event["knowledge_source"], "销售话术库")
        self.assertTrue(event["ok"])

    def test_unregistered_route_does_not_publish_activity(self) -> None:
        event = publish_agent_activity_from_request(
            method="GET",
            route_path="/api/agents/topology",
            status_code=200,
            request_id="req-2",
            elapsed_seconds=0.01,
        )

        self.assertIsNone(event)
        self.assertEqual(recent_agent_activity(), [])

    def test_publish_delivers_event_to_subscribers_and_history(self) -> None:
        queue = subscribe_agent_activity()
        try:
            event = publish_agent_activity_from_request(
                method="POST",
                route_path="/api/query/parse",
                status_code=201,
                request_id="req-3",
                elapsed_seconds=0.455,
            )

            self.assertIsNotNone(event)
            delivered = queue.get_nowait()
            self.assertEqual(delivered["agent_role"], "analyst")
            self.assertEqual(delivered["workflow_code"], "query1")
            self.assertEqual(recent_agent_activity(1)[0]["request_id"], "req-3")
        finally:
            unsubscribe_agent_activity(queue)

    def test_activity_stream_accepts_query_token_for_eventsource(self) -> None:
        token = create_access_token({"user_id": "u1", "role": "trainee", "username": "demo"})

        user = asyncio.run(_get_activity_stream_user(current_user=None, token=token))

        self.assertEqual(user["user_id"], "u1")
        self.assertEqual(user["role"], "trainee")

    def test_sse_message_uses_named_event_and_json_data(self) -> None:
        message = _sse("agent_call", {"agent_name": "陪练 Agent", "workflow_code": "practice1"})

        self.assertIn("event: agent_call\n", message)
        self.assertIn('"agent_name": "陪练 Agent"', message)
        self.assertTrue(message.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
