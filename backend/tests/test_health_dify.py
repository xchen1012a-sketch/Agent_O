from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main as backend_main


class HealthDifyTests(unittest.TestCase):
    def test_health_dify_lists_qa_chat_workflow(self) -> None:
        payload = backend_main.health_dify()
        registry = payload["data"]["workflow_registry"]
        codes = {item["workflow_code"] for item in registry}

        self.assertIn("qa1", codes)
        self.assertIn("qa_chat", codes)


if __name__ == "__main__":
    unittest.main()
