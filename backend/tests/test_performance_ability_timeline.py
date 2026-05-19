from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import Base
from models import User
from routers import performance as performance_router
from routers.performance import _ABILITY_TIMELINE_DIMENSIONS, _build_ability_timeline_items, _list_leaderboard_stores


def test_build_ability_timeline_items_maps_snapshot_dimensions() -> None:
    rows = [
        {
            "id": 1,
            "update_id": "u1",
            "created_at": "2026-05-01T10:00:00",
            "stage_no": 1,
            "cycle_day_index": 1,
            "overall_score": 38,
            "ability_snapshot_json": json.dumps(
                {
                    "product_knowledge": 42,
                    "compliance_expression": 40,
                    "needs_discovery": 35,
                    "sales_expression": 36,
                    "objection_handling": 33,
                    "closing_skill": 39,
                },
                ensure_ascii=False,
            ),
            "module_code": "needs_discovery",
            "module_name": "需求挖掘",
        },
        {
            "id": 2,
            "update_id": "u2",
            "created_at": "2026-05-14T10:00:00",
            "stage_no": 3,
            "cycle_day_index": 14,
            "overall_score": 85,
            "ability_snapshot_json": json.dumps(
                {
                    "product_knowledge": 88,
                    "compliance_expression": 86,
                    "needs_discovery": 83,
                    "sales_expression": 84,
                    "objection_handling": 80,
                    "closing_skill": 87,
                },
                ensure_ascii=False,
            ),
            "module_code": "closing_skill",
            "module_name": "成交收口",
        },
    ]

    items = _build_ability_timeline_items(rows)

    assert [dim["key"] for dim in _ABILITY_TIMELINE_DIMENSIONS] == [
        "product_knowledge",
        "compliance_expression",
        "needs_discovery",
        "sales_expression",
        "objection_handling",
        "closing_skill",
    ]
    assert [dim["label"] for dim in _ABILITY_TIMELINE_DIMENSIONS] == [
        "产品知识",
        "合规表达",
        "需求挖掘",
        "销售沟通",
        "异议处理",
        "成交收口",
    ]
    assert len(items) == 2
    assert items[0]["label"] == "S1 · Day 1"
    assert items[0]["overall_score"] == 38.0
    assert items[0]["values"]["needs_discovery"] == 35.0
    assert items[1]["label"] == "S3 · Day 14"
    assert items[1]["overall_score"] == 85.0
    assert items[1]["values"]["closing_skill"] == 87.0


def test_build_ability_timeline_items_falls_back_to_record_scores() -> None:
    items = _build_ability_timeline_items(
        [
            {
                "id": 3,
                "update_id": "u3",
                "created_at": "2026-05-02T10:00:00",
                "stage_no": 1,
                "cycle_day_index": 2,
                "overall_score": 70,
                "score": 68,
                "product_knowledge_score": 82,
                "compliance_score": 76,
                "sales_communication_score": 65,
                "response_score": 61,
                "ability_snapshot_json": "{}",
            }
        ]
    )

    assert items[0]["values"] == {
        "product_knowledge": 82.0,
        "compliance_expression": 76.0,
        "needs_discovery": 70.0,
        "sales_expression": 65.0,
        "objection_handling": 61.0,
        "closing_skill": 70.0,
    }


def test_get_ability_timeline_endpoint_returns_selected_user_payload(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        user = User(
            user_id="u-001",
            username="tester",
            hashed_password="hashed",
            name="测试员工",
            display_name="赵景行",
            store_id="store-001",
            role="trainee",
        )
        db.add(user)
        db.commit()

        class FakeCursor:
            def fetchall(self):
                return [
                    {
                        "id": 1,
                        "update_id": "u1",
                        "created_at": "2026-05-01T10:00:00",
                        "stage_no": 1,
                        "cycle_day_index": 1,
                        "overall_score": 38,
                        "ability_snapshot_json": json.dumps(
                            {
                                "product_knowledge": 42,
                                "compliance_expression": 40,
                                "needs_discovery": 35,
                                "sales_expression": 36,
                                "objection_handling": 33,
                                "closing_skill": 39,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]

        class FakeConn:
            def execute(self, sql, params):
                assert "ability_update_records" in sql
                assert "u-001" in params or str(user.id) in params
                return FakeCursor()

        @contextmanager
        def fake_get_conn():
            yield FakeConn()

        monkeypatch.setattr(performance_router, "get_conn", fake_get_conn)

        response = performance_router.get_ability_timeline(
            db=db,
            current_user={"user_id": "u-001", "role": "trainee"},
            user_id="",
        )

        assert response["code"] == 200
        assert response["data"]["selected_user_name"] == "赵景行"
        assert response["data"]["items"][0]["overall_score"] == 38.0
        assert response["data"]["dimensions"] == _ABILITY_TIMELINE_DIMENSIONS
    finally:
        db.close()
        engine.dispose()


def test_list_leaderboard_stores_skips_legacy_null_primary_key_rows() -> None:
    class FakeQuery:
        def order_by(self, *_args):
            return self

        def all(self):
            return [None]

    class FakeDb:
        def query(self, *_args):
            return FakeQuery()

    assert _list_leaderboard_stores(FakeDb()) == []
