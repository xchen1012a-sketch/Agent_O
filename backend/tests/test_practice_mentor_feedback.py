import routers.practice as practice_router
from routers.practice import PracticeMentorFeedbackRequest


def test_practice_mentor_feedback_uses_dify_result(monkeypatch):
    monkeypatch.setattr(
        practice_router,
        "run_practice_mentor_workflow",
        lambda **kwargs: {
            "ok": True,
            "data": {"mentor_sentence": "你这轮价值拆解已经有感觉了，继续把顾客顾虑问深一点，成交会更顺。"},
        },
    )

    body = PracticeMentorFeedbackRequest(
        session_id="ps_001",
        scene_code="jewelry_recommendation",
        module_code="objection_handling",
        conversation=[{"role": "user", "content": "顾客说有点贵。"}],
        overall_score=82,
        strengths=["价值拆解清晰"],
        improvements=["追问还不够深入"],
        coach_summary="建议继续强化异议处理。",
    )

    result = practice_router.practice_mentor_feedback(
        body,
        {"user_id": "1", "username": "tester", "role": "trainee"},
    )

    assert result["code"] == 200
    assert result["data"]["mentor_sentence"].startswith("你这轮价值拆解已经有感觉了")
    assert result["data"]["fallback_used"] is False
    assert result["meta"]["workflow_code"] == "practice_mentor"
    assert result["meta"]["mock"] is False


def test_practice_mentor_feedback_falls_back_to_coach_summary(monkeypatch):
    monkeypatch.setattr(
        practice_router,
        "run_practice_mentor_workflow",
        lambda **kwargs: {"ok": False, "reason": "missing_api_key", "error": ""},
    )

    body = PracticeMentorFeedbackRequest(
        session_id="ps_002",
        scene_code="objection_handling",
        module_code="objection_handling",
        conversation=[{"role": "user", "content": "顾客说太贵了。"}],
        overall_score=65,
        strengths=["语气还算自然"],
        improvements=["价值拆解不够具体"],
        coach_summary="顾客说太贵时别急着降价，先把价值讲透，再推进下一步。",
    )

    result = practice_router.practice_mentor_feedback(
        body,
        {"user_id": "1", "username": "tester", "role": "trainee"},
    )

    assert result["code"] == 200
    assert result["data"]["mentor_sentence"] == "顾客说太贵时别急着降价，先把价值讲透，再推进下一步。"
    assert result["data"]["fallback_used"] is True
    assert result["meta"]["workflow_code"] == "practice_mentor_fallback"
    assert result["meta"]["mock"] is True
