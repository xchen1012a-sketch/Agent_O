from assistant_service import build_coach_tip
from routers.qa import _knowledge_patch_from_question
from routers.tts import _cache_path


def test_build_coach_tip_prefers_price_guidance():
    tip = build_coach_tip(
        scene_input="顾客觉得太贵了",
        reply_script="可以先认可顾虑，再拆解材质工艺和售后保障。",
        matched_knowledge="价格异议",
        reply_compliance_tag="safe",
    )
    assert "先认同" in tip
    assert "降价" in tip or "优惠" in tip


def test_knowledge_patch_uses_focus_dimension_first():
    patch = _knowledge_patch_from_question(
        "我想补一下销售沟通应该怎么说",
        {"focus_dimension": "销售沟通", "coach_tip": "", "knowledge_tag": ""},
    )
    assert "销售沟通" in patch


def test_tts_cache_path_isolated_by_model_and_voice():
    path_a = _cache_path(text="你好", emotion="calm", model="speech-2.8-hd", voice_id="voice-a")
    path_b = _cache_path(text="你好", emotion="calm", model="speech-02-hd", voice_id="voice-a")
    path_c = _cache_path(text="你好", emotion="calm", model="speech-2.8-hd", voice_id="voice-b")
    assert path_a != path_b
    assert path_a != path_c
