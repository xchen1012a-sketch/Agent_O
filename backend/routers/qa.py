from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from api_response import success_response
from assistant_service import resolve_user_context
from auth import get_current_user
from qa_service import (
    classify_qa_question,
    fetch_qa_citations,
    is_qa_chat_configured,
    qa_history_to_text,
    run_qa1_workflow,
    run_qa_chat_sync,
    sanitize_qa_history,
)
from schemas import QaAskRequest

router = APIRouter(prefix="/api/qa", tags=["qa"])
_log = logging.getLogger("jewelry_qipei.router.qa")


def _redirect_questions() -> list[str]:
    return [
        "最近30天高风险员工有哪些？",
        "各门店培训完成率怎么样？",
        "这7天哪些员工需要重点跟进？",
    ]


def _unsupported_questions() -> list[str]:
    return [
        "钻石4C怎么给顾客解释更容易听懂？",
        "顾客说再考虑一下时，怎么回应更合适？",
        "在岗助手和一句话查询分别适合做什么？",
    ]


def _knowledge_patch_from_question(question: str, bridge: dict[str, str]) -> str:
    text = str(question or "").strip()
    focus_dimension = str((bridge or {}).get("focus_dimension") or "").strip()
    coach_tip = str((bridge or {}).get("coach_tip") or "").strip()
    knowledge_tag = str((bridge or {}).get("knowledge_tag") or "").strip()

    if focus_dimension and focus_dimension in text:
        return f"这题你最该记住的是：先把“{focus_dimension}”讲清，再补一个具体例子。"
    if focus_dimension in {"专业准确", "产品知识"}:
        return "这题你最该记住的是：先说定义和依据，再讲卖点，别只堆术语。"
    if focus_dimension in {"合规表达", "合规风险"}:
        return "这题你最该记住的是：先说边界和依据，不要用绝对化承诺。"
    if focus_dimension in {"需求洞察", "成交推进", "销售沟通"}:
        return "这题你最该记住的是：先接住顾客情绪，再往下讲专业解释。"
    if coach_tip:
        return "下次顾客问到这里，先说：" + coach_tip[:40]
    if knowledge_tag:
        return f"这题你最该记住的是：优先围绕“{knowledge_tag[:16]}”给出清晰依据。"

    lowered = text.lower()
    if "4c" in lowered or "钻石" in text:
        return "这题你最该记住的是：钻石类问题先讲标准含义，再讲怎么向顾客解释。"
    if "证书" in text or "真假" in text or "材质" in text:
        return "这题你最该记住的是：先讲可核验依据，再补充保障和售后。"
    if "价格" in text or "贵" in text or "预算" in text:
        return "这题你最该记住的是：价格类问题先拆价值和差异，再谈预算匹配。"
    if "系统" in text or "页面" in text or "按钮" in text:
        return "这题你最该记住的是：系统问题优先确认入口、步骤和结果。"
    return "这题你最该记住的是：先给判断，再给依据，最后补一个门店里的说法。"


def _plain_qa_payload(
    *,
    answer_text: str,
    citations: list[dict[str, Any]] | None = None,
    related_questions: list[str] | None = None,
    conversation_id: str = "",
    qa_chat_conversation_id: str = "",
    turn_feedback: dict[str, Any] | None = None,
    reply_mode: str = "dify",
) -> dict[str, Any]:
    return {
        "answer_text": str(answer_text or "").strip(),
        "answer_brief": "",
        "answer_reason": "",
        "answer_example": "",
        "coach_question": "",
        "citations": citations if isinstance(citations, list) else [],
        "knowledge_patch": "",
        "related_questions": related_questions if isinstance(related_questions, list) else [],
        "conversation_id": str(conversation_id or "").strip(),
        "qa_chat_conversation_id": str(qa_chat_conversation_id or "").strip(),
        "turn_feedback": turn_feedback if isinstance(turn_feedback, dict) else None,
        "reply_mode": str(reply_mode or "").strip(),
        "answer_subtype": "",
    }


@router.post("/ask")
def qa_ask(
    body: QaAskRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请输入问题")

    category = classify_qa_question(question)
    conversation_id = (body.conversation_id or "").strip()
    qa_chat_conversation_id = (body.qa_chat_conversation_id or "").strip()
    _log.info(
        "qa_ask start user_id=%s category=%s question=%s",
        current_user.get("user_id"),
        category,
        question[:80],
    )

    if category == "data_query":
        return success_response(
            _plain_qa_payload(
                answer_text="这类问题属于业务数据查询，不属于知识问答范围。你可以去“一句话查询”里继续提问，比如输入“最近30天高风险员工有哪些”或“各门店培训完成率怎么样”。",
                citations=[],
                related_questions=_redirect_questions(),
                conversation_id=conversation_id,
                reply_mode="error_fallback",
            ),
            workflow_code="qa1_local_redirect",
            mock=True,
        )

    if category == "other_unsupported":
        return success_response(
            _plain_qa_payload(
                answer_text="这个问题不在当前知识问答范围内，暂时无法给出可靠回答。你可以改问产品知识、销售话术、合规边界或系统使用说明相关问题。",
                citations=[],
                related_questions=_unsupported_questions(),
                conversation_id=conversation_id,
                reply_mode="error_fallback",
            ),
            workflow_code="qa1_local_boundary",
            mock=True,
        )

    context = resolve_user_context(current_user)
    history = sanitize_qa_history(body.history or [])
    history_text = qa_history_to_text(history)
    citations = fetch_qa_citations(question)
    qa_chat_result: dict[str, Any] = {"turn_feedback": None, "qa_chat_conversation_id": ""}

    if is_qa_chat_configured():
        with ThreadPoolExecutor(max_workers=2) as executor:
            qa1_future = executor.submit(
                run_qa1_workflow,
                question=question,
                conversation_history=history_text,
                conversation_id=conversation_id,
                user_id=context["user_id"],
                role=context["role"],
                store_id=context["store_id"],
            )
            qa_chat_future = executor.submit(
                run_qa_chat_sync,
                question=question,
                user_id=context["user_id"],
                conversation_id=qa_chat_conversation_id,
            )
            run_result = qa1_future.result()
            try:
                qa_chat_result = qa_chat_future.result()
            except Exception:
                _log.exception("qa_chat parallel call failed")
    else:
        run_result = run_qa1_workflow(
            question=question,
            conversation_history=history_text,
            conversation_id=conversation_id,
            user_id=context["user_id"],
            role=context["role"],
            store_id=context["store_id"],
        )

    if run_result.get("ok"):
        data = run_result.get("data") if isinstance(run_result.get("data"), dict) else {}
        return success_response(
            _plain_qa_payload(
                answer_text=str(data.get("answer_text") or "").strip(),
                citations=citations,
                related_questions=data.get("related_questions") or [],
                conversation_id=str(data.get("conversation_id") or conversation_id),
                qa_chat_conversation_id=str(qa_chat_result.get("qa_chat_conversation_id") or ""),
                turn_feedback=qa_chat_result.get("turn_feedback"),
                reply_mode="dify",
            ),
            workflow_code="qa1",
            mock=False,
        )

    if citations:
        return success_response(
            _plain_qa_payload(
                answer_text="当前暂时没拿到完整回答，你可以先参考下方命中的知识依据。",
                citations=citations,
                related_questions=[],
                conversation_id=conversation_id,
                reply_mode="kb_fallback",
            ),
            workflow_code="qa1_kb_fallback",
            mock=True,
        )

    return success_response(
        _plain_qa_payload(
            answer_text="当前暂时无法完成知识问答，请稍后再试。你也可以换个更具体的问法，例如补充产品类型、顾客场景或想了解的规则。",
            citations=[],
            related_questions=[],
            conversation_id=conversation_id,
            reply_mode="error_fallback",
        ),
        workflow_code="qa1_error_fallback",
        mock=True,
    )
