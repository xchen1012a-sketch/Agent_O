from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends

from api_response import success_response
from assistant_service import (
    conversation_history_to_text,
    is_assistant_chat_configured,
    persist_reply_record,
    resolve_user_context,
    run_assistant1_sync,
    run_assistant2_background,
    run_assistant_chat_sync,
)
from auth import get_current_user
from schemas import AssistantReplyRequest

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
_log = logging.getLogger("jewelry_qipei.router.assistant")


@router.post("/reply")
def assistant_reply(
    body: AssistantReplyRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    user_id = (current_user or {}).get("user_id", "anonymous")
    _log.info("assistant_reply start user_id=%s", user_id)
    scene_input = (body.scene_input or "").strip() or "客户对珠宝价值存在疑虑"
    context = resolve_user_context(current_user)
    history_text = conversation_history_to_text(body.history or [])
    conversation_id = (body.conversation_id or "").strip()

    # 优先使用 chat 模式（支持 turn_feedback 和对话上下文），未配置时回退到 workflow 模式
    if is_assistant_chat_configured():
        run_result = run_assistant_chat_sync(
            scene_input=scene_input,
            user_id=context["user_id"],
            store_id=context["store_id"],
            conversation_id=conversation_id,
        )
    else:
        run_result = run_assistant1_sync(
            scene_input=scene_input,
            user_id=context["user_id"],
            store_id=context["store_id"],
            conversation_history=history_text,
        )
    reply_obj = run_result["response"]

    persist_reply_record(
        user_id=context["user_id"],
        store_id=context["store_id"],
        role=context["role"],
        scene_input=scene_input,
        response=reply_obj,
        use_dify=run_result["use_dify"],
        workflow_reason=run_result["workflow_reason"],
        dify_error=run_result["dify_error"],
    )

    background_tasks.add_task(
        run_assistant2_background,
        user_id=context["user_id"],
        store_id=context["store_id"],
        role=context["role"],
        scene_input=scene_input,
        assistant_reply=reply_obj.reply_script,
        reply_compliance_tag=run_result["reply_compliance_tag"],
        matched_knowledge=run_result["matched_knowledge"],
    )

    _log.info("assistant_reply success user_id=%s use_dify=%s chat_mode=%s", user_id, run_result["use_dify"], is_assistant_chat_configured())

    # 前台仅返回结构化话术字段，不透出 Dify 原始 JSON、内部 workflow 原因或错误详情
    resp_data = {
        "reply_script": reply_obj.reply_script,
        "followup_question": reply_obj.followup_question or "",
        "coach_tip": reply_obj.coach_tip or "",
        "voice_advice": reply_obj.voice_advice or "",
    }
    if reply_obj.turn_feedback:
        resp_data["turn_feedback"] = reply_obj.turn_feedback
    if reply_obj.conversation_id:
        resp_data["conversation_id"] = reply_obj.conversation_id
    return success_response(
        resp_data,
        workflow_code="assistant_chat" if is_assistant_chat_configured() else ("assistant1" if run_result["use_dify"] else "assistant1_local_fallback"),
        mock=not run_result["use_dify"],
    )
