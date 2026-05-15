from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from routers.agents import build_agent_topology_payload, get_agent_topology
from workflow_registry import AGENT_TOPOLOGY_WORKFLOW_CODES, DIFY_WORKFLOW_REGISTRY


class AgentTopologyTests(unittest.TestCase):
    def test_locked_agent_mapping_has_five_agents_and_fourteen_core_workflows(self) -> None:
        payload = build_agent_topology_payload({"user_id": "1", "role": "admin"})

        self.assertEqual([item["id"] for item in payload["agents"]], [
            "tutor",
            "practice",
            "examiner",
            "service",
            "analyst",
        ])
        self.assertEqual(payload["summary"]["agent_count"], 5)
        self.assertEqual(payload["summary"]["workflow_count"], 14)

        by_role = {
            item["id"]: [workflow["code"] for workflow in item["workflows"]]
            for item in payload["agents"]
        }
        self.assertEqual(by_role, {key: list(value) for key, value in AGENT_TOPOLOGY_WORKFLOW_CODES.items()})

    def test_registry_records_include_agent_role(self) -> None:
        missing = [
            item["code"]
            for item in DIFY_WORKFLOW_REGISTRY
            if not str(item.get("agent_role") or "").strip()
        ]
        self.assertEqual(missing, [])

    def test_route_response_shape(self) -> None:
        response = get_agent_topology(current_user={"user_id": "2", "role": "trainee"})

        self.assertEqual(response["code"], 200)
        self.assertEqual(response["meta"]["workflow_code"], "agent_topology")
        self.assertEqual(len(response["data"]["agents"]), 5)
        self.assertGreaterEqual(len(response["data"]["links"]), 6)
        self.assertEqual(response["data"]["summary"]["viewer_role"], "trainee")


if __name__ == "__main__":
    unittest.main()
