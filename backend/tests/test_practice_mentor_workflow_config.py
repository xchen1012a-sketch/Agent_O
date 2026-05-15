import dify_stage4b


def test_practice_mentor_workflow_uses_dedicated_base_and_timeout(monkeypatch):
    captured = {}

    def fake_run_workflow_blocking(**kwargs):
        captured.update(kwargs)
        return {"code": 200, "data": {"outputs": {}}}

    monkeypatch.setattr(dify_stage4b.app_config, "DIFY_STAGE4B_FORCE_MOCK", False)
    monkeypatch.setattr(dify_stage4b.app_config, "DIFY_API_KEY", "")
    monkeypatch.setattr(dify_stage4b.app_config, "DIFY_API_BASE", "https://global.example.com")
    monkeypatch.setattr(dify_stage4b.app_config, "DIFY_STAGE4B_TIMEOUT", 120.0)
    monkeypatch.setattr(dify_stage4b.app_config, "DIFY_PRACTICE_MENTOR_API_KEY", "app-mentor-test")
    monkeypatch.setattr(
        dify_stage4b.app_config,
        "DIFY_PRACTICE_MENTOR_API_BASE",
        "https://mentor.example.com/",
    )
    monkeypatch.setattr(dify_stage4b.app_config, "DIFY_PRACTICE_MENTOR_TIMEOUT", 45.0)
    monkeypatch.setattr(dify_stage4b, "run_workflow_blocking", fake_run_workflow_blocking)

    result = dify_stage4b._run_stage4b_workflow(
        workflow_name="practice_mentor",
        user_id="user-1",
        inputs={"practice_id": "ps_001", "overall_score": 88},
    )

    assert result["ok"] is True
    assert captured["base_url"] == "https://mentor.example.com"
    assert captured["api_key"] == "app-mentor-test"
    assert captured["timeout_sec"] == 45.0
