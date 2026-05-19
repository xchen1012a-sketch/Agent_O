from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from routers.agents import build_agent_topology_payload, get_agent_topology
from workflow_registry import AGENT_TOPOLOGY_WORKFLOW_CODES, DIFY_WORKFLOW_REGISTRY


class AgentTopologyTests(unittest.TestCase):
    def test_locked_agent_mapping_has_six_agents_and_registered_workflow_total(self) -> None:
        payload = build_agent_topology_payload({"user_id": "1", "role": "admin"})

        self.assertEqual([item["id"] for item in payload["agents"]], [
            "tutor",
            "practice",
            "examiner",
            "service",
            "analyst",
            "evolution",
        ])
        self.assertEqual(payload["summary"]["agent_count"], 6)
        self.assertEqual(payload["summary"]["workflow_count"], 17)
        self.assertEqual(payload["summary"]["registered_workflow_count"], 22)
        self.assertEqual(payload["summary"]["hidden_workflow_count"], 5)

        by_role = {
            item["id"]: [workflow["code"] for workflow in item["workflows"]]
            for item in payload["agents"]
        }
        self.assertEqual(by_role, {key: list(value) for key, value in AGENT_TOPOLOGY_WORKFLOW_CODES.items()})

    def test_registry_includes_practice_turn_feedback(self) -> None:
        codes = [item["code"] for item in DIFY_WORKFLOW_REGISTRY]
        self.assertIn("practice_turn_feedback", codes)
        self.assertEqual(len(codes), 22)

    def test_self_evolution_workflows_are_visible_in_topology(self) -> None:
        payload = build_agent_topology_payload({"user_id": "1", "role": "admin"})
        by_role = {
            item["id"]: [workflow["code"] for workflow in item["workflows"]]
            for item in payload["agents"]
        }

        self.assertEqual(by_role["evolution"], ["evo_semantic", "evo_reflective", "evo_procedural"])
        self.assertTrue(any(link["source"] == "analyst" and link["target"] == "evolution" for link in payload["links"]))
        self.assertTrue(any(link["source"] == "evolution" and link["target"] == "user_input" for link in payload["links"]))

    def test_registry_records_include_agent_role(self) -> None:
        missing = [
            item["code"]
            for item in DIFY_WORKFLOW_REGISTRY
            if not str(item.get("agent_role") or "").strip()
        ]
        self.assertEqual(missing, [])

    def test_route_response_shape_for_management_user(self) -> None:
        response = get_agent_topology(current_user={"user_id": "2", "role": "store_manager"})

        self.assertEqual(response["code"], 200)
        self.assertEqual(response["meta"]["workflow_code"], "agent_topology")
        self.assertEqual(len(response["data"]["agents"]), 6)
        self.assertGreaterEqual(len(response["data"]["links"]), 6)
        self.assertEqual(response["data"]["summary"]["viewer_role"], "store_manager")

    def test_route_rejects_ordinary_employee(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            get_agent_topology(current_user={"user_id": "2", "role": "trainee"})

        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
