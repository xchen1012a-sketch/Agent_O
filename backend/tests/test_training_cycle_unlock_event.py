from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from routers.training_cycle import _build_stage_unlock_event


def test_build_stage_unlock_event_for_next_stage() -> None:
    event = _build_stage_unlock_event(
        cycle={"stage_no": 1, "stage_name": "基础认知"},
        payload={"is_pass": 1, "review_score": 86.5},
        next_cycle_id="tc_next_001",
        wf15_result={"next_route": "training_path"},
    )

    assert event == {
        "type": "stage_unlocked",
        "stage": 2,
        "name": "销售转化与上岗",
        "passed_stage": 1,
        "passed_stage_name": "基础认知",
        "review_score": 86.5,
        "next_cycle_id": "tc_next_001",
        "next_route": "training_path",
    }


def test_build_stage_unlock_event_skips_failed_review() -> None:
    assert (
        _build_stage_unlock_event(
            cycle={"stage_no": 1, "stage_name": "基础认知"},
            payload={"is_pass": 0, "review_score": 72.0},
            next_cycle_id="",
            wf15_result={},
        )
        is None
    )

