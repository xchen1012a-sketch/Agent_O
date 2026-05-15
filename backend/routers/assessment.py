from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import String as SAString, cast, or_
from sqlalchemy.orm import Session

from api_response import dify_failure_response, error_response, success_response
from auth import get_current_user
import config as app_config
from database import get_db, utc_now
from dify_assessment import run_wf14_grade_paper
from models import AssessmentRecord, AssessmentTask, AssessmentTaskTarget, User
from schemas import (
    AssessmentChatReq,
    AssessmentFinishReq,
    AssessmentStartReq,
    AssessmentSubmitPaperReq,
)

router = APIRouter(prefix="/api/assessment", tags=["Assessment"])

_WORKFLOW_CODE = "assessment"


def _current_user_id(current_user: dict) -> str:
    return str(current_user.get("user_id") or "").strip()


def _resolve_user(db: Session, current_user: dict) -> User | None:
    actor_id = _current_user_id(current_user)
    if not actor_id:
        return None
    return (
        db.query(User)
        .filter(or_(cast(User.id, SAString) == actor_id, User.user_id == actor_id))
        .first()
    )


def _user_aliases(user: User | None, current_user: dict) -> set[str]:
    aliases = {_current_user_id(current_user)}
    if user:
        aliases.add(str(user.id or ""))
        aliases.add(str(user.user_id or ""))
    return {item for item in aliases if item}


def _task_targets(db: Session, task_id: int) -> list[AssessmentTaskTarget]:
    return db.query(AssessmentTaskTarget).filter(AssessmentTaskTarget.task_id == task_id).all()


def _is_task_assigned(task: AssessmentTask, targets: list[AssessmentTaskTarget], aliases: set[str], store_id: str) -> bool:
    if not targets:
        scope = str(task.target_scope or "").strip()
        return scope in {"", "all", store_id}
    for target in targets:
        target_type = str(target.target_type or "store").strip()
        target_value = str(target.target_value or "").strip()
        if target_type == "store" and target_value == store_id:
            return True
        if target_type == "account" and target_value in aliases:
            return True
    return False


def _dify_chat_url() -> str:
    base = (
        app_config.DIFY_WF11_API_BASE
        or app_config.DIFY_API_BASE
        or ""
    ).strip().rstrip("/")
    if not base:
        base = "https://api.dify.ai"
    return f"{base}/chat-messages" if base.endswith("/v1") else f"{base}/v1/chat-messages"


def _wf11_api_key() -> str:
    return str(getattr(app_config, "DIFY_WF11_API_KEY", "") or "").strip()


def _wf11_timeout() -> float:
    try:
        return float(getattr(app_config, "DIFY_WF11_TIMEOUT", 120.0) or 120.0)
    except (TypeError, ValueError):
        return 120.0


def _parse_paper_config(task: AssessmentTask) -> dict[str, Any]:
    raw = str(task.paper_config_json or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_utc_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_timed_out(record: AssessmentRecord) -> bool:
    expires_at = _coerce_utc_dt(record.expires_at)
    return bool(expires_at and utc_now() >= expires_at and record.submit_status == "in_progress")


def _is_retake_allowed(task: AssessmentTask) -> bool:
    return bool(int(task.allow_retake or 0))


def _is_auto_submit_enabled(task: AssessmentTask) -> bool:
    return bool(int(task.auto_submit_on_timeout or 0))


def _compute_time_spent_seconds(record: AssessmentRecord) -> int:
    started_at = _coerce_utc_dt(record.started_at)
    if not started_at:
        return 0
    end_at = _coerce_utc_dt(record.submitted_at) or _coerce_utc_dt(record.finished_at) or utc_now()
    return max(int((end_at - started_at).total_seconds()), 0)


def _finalize_record(
    record: AssessmentRecord,
    *,
    score: float,
    is_pass: int,
    comment: str,
    paper_answer_json: str | None = None,
    paper_result_json: str | None = None,
    submit_status: str = "submitted",
    review_source: str = "ai_auto",
    is_timeout: bool = False,
) -> None:
    now = utc_now()
    record.score = float(score or 0)
    record.is_pass = int(is_pass or 0)
    record.comment = str(comment or "")
    record.finished_at = now
    record.submitted_at = now
    record.submit_status = submit_status
    record.is_timeout = 1 if is_timeout else 0
    record.review_source = review_source
    record.time_spent_seconds = _compute_time_spent_seconds(record)
    if paper_answer_json is not None:
        record.paper_answer_json = paper_answer_json
    if paper_result_json is not None:
        record.paper_result_json = paper_result_json


def _handle_timeout_submission(
    record: AssessmentRecord,
    task: AssessmentTask,
    *,
    score: float,
    is_pass: int,
    comment: str,
    review_source: str = "ai_auto",
) -> tuple[bool, bool, Any]:
    if not _is_timed_out(record):
        return False, False, None
    if not _is_auto_submit_enabled(task):
        return (
            True,
            False,
            error_response(
                workflow_code=_WORKFLOW_CODE,
                message="考试已超时，当前任务未启用自动交卷，请联系管理员处理。",
                data={"record_id": record.id},
                http_status=400,
                mock=False,
            ),
        )

    _finalize_record(
        record,
        score=score,
        is_pass=is_pass,
        comment=comment,
        submit_status="timeout_submitted",
        review_source=review_source,
        is_timeout=True,
    )
    return (
        True,
        True,
        error_response(
            workflow_code=_WORKFLOW_CODE,
            message="考试已超时，系统已自动交卷。",
            data={"record_id": record.id},
            http_status=400,
            mock=False,
        ),
    )


def _assessment_history_visible(record: AssessmentRecord) -> bool:
    status = str(record.submit_status or "").strip().lower()
    if status in {"submitted", "finished", "timeout_submitted"}:
        return True
    if status == "in_progress":
        return False
    return _coerce_utc_dt(record.submitted_at) is not None


def _normalize_assessment_coach_text(text: str, fallback: str) -> str:
    value = " ".join(str(text or "").strip().split())
    value = value or str(fallback or "").strip()
    if not value:
        return ""
    for sep in ("。", "！", "？", "!", "?", ";", "；"):
        idx = value.find(sep)
        if idx >= 0:
            value = value[: idx + 1]
            break
    if len(value) > 24:
        value = value[:24].rstrip("，,；;。！？!? ")
    if value and value[-1] not in "。！？!?":
        value += "。"
    return value


def build_assessment_coach_hint(
    *,
    user_message: str,
    examiner_reply: str,
    is_finished: bool = False,
    score: float = 0.0,
    is_pass: int = 0,
    conversation_started: bool = False,
) -> dict[str, Any]:
    user_text = str(user_message or "").strip()
    reply_text = str(examiner_reply or "").strip()
    haystack = f"{user_text} {reply_text}"

    if is_finished:
        hint = "先复盘亮点，再把收口动作讲更稳。" if int(is_pass or 0) == 1 else "先复盘卡点，再补顾虑承接和收口。"
        if "收口" in haystack:
            hint = "先复盘卡点，再补收口动作。"
        elif "顾虑" in haystack or "异议" in haystack:
            hint = "先复盘顾虑承接，再补价值表达。"
        return {
            "phase": "result_debrief",
            "intent_label": "考后复盘",
            "hint_text": _normalize_assessment_coach_text(hint, "先复盘亮点，再讲稳下一轮。"),
            "pose": "celebrate" if int(is_pass or 0) == 1 else "encourage",
            "urgency": "normal",
            "should_speak": True,
        }

    if len(user_text) <= 6 or user_text in {"嗯", "好的", "再看看", "考虑下", "不知道"}:
        return {
            "phase": "stuck",
            "intent_label": "补充展开",
            "hint_text": _normalize_assessment_coach_text("先补顾虑，再讲价值依据。", "先补顾虑，再讲价值依据。"),
            "pose": "think",
            "urgency": "normal",
            "should_speak": True,
        }

    if any(word in haystack for word in ("贵", "价格", "优惠", "折扣", "预算")):
        return {
            "phase": "objection",
            "intent_label": "价值拆解",
            "hint_text": _normalize_assessment_coach_text("先接住顾虑，再讲价值依据。", "先接住顾虑，再讲价值依据。"),
            "pose": "think",
            "urgency": "normal",
            "should_speak": False,
        }

    if any(word in haystack for word in ("送礼", "对象", "场合", "需求", "预算范围")):
        return {
            "phase": "opening" if not conversation_started else "needs_discovery",
            "intent_label": "需求确认",
            "hint_text": _normalize_assessment_coach_text("先问对象、场景和预算范围。", "先问对象、场景和预算范围。"),
            "pose": "agree",
            "urgency": "normal",
            "should_speak": False,
        }

    if any(word in haystack for word in ("证书", "保真", "真假", "材质", "工艺", "售后")):
        return {
            "phase": "evidence",
            "intent_label": "证据建立",
            "hint_text": _normalize_assessment_coach_text("先讲证据依据，再补售后保障。", "先讲证据依据，再补售后保障。"),
            "pose": "agree",
            "urgency": "normal",
            "should_speak": False,
        }

    if any(word in haystack for word in ("再看看", "考虑", "对比", "回头", "下次")):
        return {
            "phase": "closing",
            "intent_label": "收口推进",
            "hint_text": _normalize_assessment_coach_text("先问卡点，再给下一步动作。", "先问卡点，再给下一步动作。"),
            "pose": "encourage",
            "urgency": "normal",
            "should_speak": False,
        }

    return {
        "phase": "opening" if not conversation_started else "after_examiner_reply",
        "intent_label": "开场引导" if not conversation_started else "表达结构",
        "hint_text": _normalize_assessment_coach_text("先定主线，再顺着讲理由。", "先定主线，再顺着讲理由。"),
        "pose": "agree" if not conversation_started else "think",
        "urgency": "normal",
        "should_speak": False,
    }


def _score_paper_exam(task: AssessmentTask, answers: dict[str, Any]) -> dict[str, Any]:
    paper = _parse_paper_config(task)
    questions = paper.get("questions") if isinstance(paper.get("questions"), list) else []
    total = 0.0
    gained = 0.0
    strengths: list[str] = []
    risks: list[str] = []

    for question in questions:
        question_id = str(question.get("id") or "")
        question_type = str(question.get("type") or "single")
        score = float(question.get("score") or 0)
        total += score
        user_answer = answers.get(question_id)
        if question_type in {"single", "judge"}:
            if str(user_answer or "").strip().upper() == str(question.get("answer") or "").strip().upper():
                gained += score
                strengths.append(str(question.get("title") or question_id))
            else:
                risks.append(str(question.get("title") or question_id))
        elif question_type == "multiple":
            expected = sorted([str(item).strip().upper() for item in (question.get("answer") or []) if str(item).strip()])
            actual = sorted([str(item).strip().upper() for item in (user_answer or []) if str(item).strip()]) if isinstance(user_answer, list) else []
            if expected and actual == expected:
                gained += score
                strengths.append(str(question.get("title") or question_id))
            else:
                risks.append(str(question.get("title") or question_id))
        else:
            text = str(user_answer or "").strip()
            keywords = [str(item).strip().lower() for item in (question.get("keywords") or []) if str(item).strip()]
            if not keywords:
                ratio = 1.0 if text else 0.0
            else:
                hits = sum(1 for keyword in keywords if keyword and keyword in text.lower())
                ratio = min(hits / len(keywords), 1.0)
            gained += score * ratio
            if ratio >= 0.7:
                strengths.append(str(question.get("title") or question_id))
            else:
                risks.append(str(question.get("title") or question_id))

    final_score = round((gained / total) * 100, 2) if total > 0 else 0.0
    is_pass = 1 if final_score >= float(task.pass_score or 85) else 0
    comment = "本次试卷考试已完成。"
    if is_pass:
        comment = "本次试卷考试通过，作答结构较完整。"
    else:
        comment = "本次试卷考试未达标，建议重点加强标准化表达与完整接待思路。"
    if strengths:
        comment += " 表现较好的部分：" + "、".join(strengths[:2]) + "。"
    if risks:
        comment += " 需重点复盘：" + "、".join(risks[:2]) + "。"
    return {
        "score": final_score,
        "is_pass": is_pass,
        "comment": comment,
        "grading_detail": {"strengths": strengths[:3], "risks": risks[:3]},
    }


def _local_paper_fallback_payload(task: AssessmentTask, answers: dict[str, Any], call: dict[str, Any] | None) -> dict[str, Any]:
    result = _score_paper_exam(task, answers)
    reason = str((call or {}).get("reason") or "local_fallback")
    error = str((call or {}).get("error") or "").strip()
    note = "AI阅卷暂时不可用，已按标准答案完成本地判分。"
    comment = str(result.get("comment") or "").strip()
    if comment:
        comment = f"{comment} {note}"
    else:
        comment = note
    paper_result = {
        "grading_status": "local_fallback",
        "summary_comment": comment,
        "total_score": float(result.get("score") or 0),
        "pass_score": float(task.pass_score or 85),
        "is_pass": int(result.get("is_pass") or 0),
        "grading_detail": result.get("grading_detail") or {},
        "fallback_reason": reason,
    }
    if error:
        paper_result["fallback_error"] = error[:500]
    return {
        "score": float(result.get("score") or 0),
        "is_pass": int(result.get("is_pass") or 0),
        "comment": comment,
        "paper_result_json": json.dumps(paper_result, ensure_ascii=False),
        "review_source": "local_fallback",
    }


def reconcile_expired_assessment_records(
    db: Session,
    *,
    records: list[AssessmentRecord] | None = None,
) -> int:
    target_records = list(records or [])
    if not target_records:
        return 0
    task_ids = [int(item.task_id) for item in target_records if item.task_id is not None]
    task_map = {
        item.id: item
        for item in db.query(AssessmentTask).filter(AssessmentTask.id.in_(task_ids or [0])).all()
    }
    updated = 0
    for record in target_records:
        if not _is_timed_out(record):
            continue
        task = task_map.get(record.task_id)
        comment = "考试已超时，系统已自动归档本次记录。"
        score = float(record.score or 0)
        is_pass = int(record.is_pass or 0)
        paper_result_json = None
        review_source = "system_timeout_reconcile"
        if task and str(task.exam_mode or "") == "paper_exam":
            try:
                answers = json.loads(str(record.paper_answer_json or "{}"))
            except json.JSONDecodeError:
                answers = {}
            if isinstance(answers, dict) and answers:
                local_result = _score_paper_exam(task, answers)
                score = float(local_result.get("score") or 0)
                is_pass = int(local_result.get("is_pass") or 0)
                comment = str(local_result.get("comment") or comment)
                paper_result_json = json.dumps(
                    {
                        "grading_status": "timeout_reconciled",
                        "summary_comment": comment,
                        "total_score": score,
                        "pass_score": float(task.pass_score or 85),
                        "is_pass": is_pass,
                        "grading_detail": local_result.get("grading_detail") or {},
                    },
                    ensure_ascii=False,
                )
                review_source = "timeout_local_reconcile"
            else:
                comment = "考试已超时，系统按未作答处理并归档本次试卷记录。"
        _finalize_record(
            record,
            score=score,
            is_pass=is_pass,
            comment=comment,
            paper_result_json=paper_result_json,
            submit_status="timeout_submitted",
            review_source=review_source,
            is_timeout=True,
        )
        db.add(record)
        updated += 1
    if updated:
        db.commit()
    return updated


@router.post("/start")
def start_assessment(
    body: AssessmentStartReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    task = (
        db.query(AssessmentTask)
        .filter(AssessmentTask.id == body.task_id, AssessmentTask.publish_status == "published", AssessmentTask.status == "active")
        .first()
    )
    if not task:
        return error_response(workflow_code=_WORKFLOW_CODE, message="考核任务不存在或未发布", data={}, http_status=404, mock=False)

    user = _resolve_user(db, current_user)
    if not user:
        return error_response(workflow_code=_WORKFLOW_CODE, message="当前登录用户无效", data={}, http_status=401, mock=False)
    aliases = _user_aliases(user, current_user)
    targets = _task_targets(db, int(task.id))
    if not _is_task_assigned(task, targets, aliases, str(user.store_id or "").strip()):
        return error_response(workflow_code=_WORKFLOW_CODE, message="当前用户不在该考试发布范围内", data={}, http_status=403, mock=False)

    history_records = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.task_id == task.id, AssessmentRecord.user_id.in_(list(aliases)))
        .order_by(AssessmentRecord.id.desc())
        .all()
    )
    reconcile_expired_assessment_records(db, records=history_records)
    latest_record = history_records[0] if history_records else None
    latest_expires_at = _coerce_utc_dt(latest_record.expires_at) if latest_record else None
    if latest_record and latest_record.submit_status == "in_progress" and latest_expires_at and latest_expires_at > utc_now():
        return success_response(
            {
                "record_id": latest_record.id,
                "attempt_no": latest_record.attempt_no,
                "duration_minutes": int(task.duration_minutes or 60),
                "expires_at": latest_expires_at.isoformat() if latest_expires_at else None,
                "score_visibility": task.score_visibility,
                "exam_mode": task.exam_mode,
                "paper_config_json": task.paper_config_json if task.exam_mode == "paper_exam" else None,
            },
            workflow_code=_WORKFLOW_CODE,
            mock=False,
        )

    if any(int(item.is_pass or 0) == 1 for item in history_records):
        return error_response(workflow_code=_WORKFLOW_CODE, message="您已通过该考核，无需重复作答", data={}, http_status=400, mock=False)
    if len(history_records) >= int(task.max_attempts or 1):
        return error_response(workflow_code=_WORKFLOW_CODE, message="已超过最大作答次数", data={}, http_status=400, mock=False)

    if history_records and not _is_retake_allowed(task):
        return error_response(workflow_code=_WORKFLOW_CODE, message="当前考试不允许重考或补考", data={}, http_status=400, mock=False)

    start_at = utc_now()
    expires_at = start_at + timedelta(minutes=max(int(task.duration_minutes or 60), 1))
    attempt_no = len(history_records) + 1
    new_record = AssessmentRecord(
        task_id=task.id,
        user_id=_current_user_id(current_user),
        employee_name=str(user.display_name or user.name or user.username or "").strip(),
        attempt_no=attempt_no,
        finished_at=None,
        score_branch=body.score_branch,
        cycle_day_index=body.cycle_day_index,
        started_at=start_at,
        expires_at=expires_at,
        submit_status="in_progress",
        score_visibility_snapshot=task.score_visibility or "public",
        is_score_visible_to_user=0 if str(task.score_visibility or "public") == "hidden" else 1,
        exam_mode_snapshot=task.exam_mode or "ai_blind_box_exam",
        task_version_snapshot=max(int(task.paper_review_version or 0), 1),
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    message = ""
    if attempt_no > 1:
        message = f"本次为第 {attempt_no} 次补考"

    return success_response(
        {
            "record_id": new_record.id,
            "attempt_no": attempt_no,
            "message": message,
            "duration_minutes": int(task.duration_minutes or 60),
            "expires_at": expires_at.isoformat(),
            "score_visibility": task.score_visibility,
            "exam_mode": task.exam_mode,
            "paper_config_json": task.paper_config_json if task.exam_mode == "paper_exam" else None,
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/chat")
def assessment_chat(
    body: AssessmentChatReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == body.record_id).first()
    if not record:
        return error_response(workflow_code=_WORKFLOW_CODE, message="考试记录不存在", data={}, http_status=404, mock=False)
    if str(record.user_id or "") != _current_user_id(current_user):
        return error_response(workflow_code=_WORKFLOW_CODE, message="无权访问该考试记录", data={}, http_status=403, mock=False)

    task = db.query(AssessmentTask).filter(AssessmentTask.id == record.task_id).first()
    if not task:
        return error_response(workflow_code=_WORKFLOW_CODE, message="关联的考核任务不存在", data={}, http_status=404, mock=False)
    if task.exam_mode != "ai_blind_box_exam":
        return error_response(workflow_code=_WORKFLOW_CODE, message="当前考试不是 AI 盲盒考试", data={}, http_status=400, mock=False)

    if _is_timed_out(record):
        _finalize_record(record, score=float(record.score or 0), is_pass=int(record.is_pass or 0), comment="考试超时，系统已自动交卷。", submit_status="timeout_submitted", is_timeout=True)
        db.add(record)
        db.commit()
        return error_response(workflow_code=_WORKFLOW_CODE, message="考试已超时，系统已自动交卷", data={"record_id": record.id}, http_status=400, mock=False)

    timed_out, auto_submitted, timeout_response = _handle_timeout_submission(
        record,
        task,
        score=float(record.score or 0),
        is_pass=int(record.is_pass or 0),
        comment="考试超时，系统已自动交卷。",
    )
    if timed_out:
        if auto_submitted:
            db.add(record)
            db.commit()
        return timeout_response

    api_key = _wf11_api_key()
    if not api_key:
        return error_response(workflow_code=_WORKFLOW_CODE, message="DIFY_WF11_API_KEY 未配置", data={}, http_status=500, mock=False)

    current_conv_id = (record.conversation_id or body.conversation_id or "").strip()
    conversation_started = bool(current_conv_id)
    inputs = {}
    if not current_conv_id:
        inputs = {
            "task_name": task.task_name,
            "customer_persona": "一位要求很高、问题很多且带有明显压迫感的顾客",
        }
    payload = {
        "inputs": inputs,
        "query": body.message,
        "user": _current_user_id(current_user),
        "response_mode": "blocking",
        "conversation_id": current_conv_id,
    }

    try:
        resp = httpx.post(
            _dify_chat_url(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=_wf11_timeout(),
        )
    except httpx.HTTPError as exc:
        return error_response(workflow_code=_WORKFLOW_CODE, message=f"Dify 请求失败：{exc}", data={}, http_status=500, mock=False)
    if resp.status_code != 200:
        return error_response(workflow_code=_WORKFLOW_CODE, message="Dify 调用失败", data={"status_code": resp.status_code, "detail": resp.text[:500]}, http_status=500, mock=False)

    try:
        result = resp.json()
    except ValueError:
        return error_response(workflow_code=_WORKFLOW_CODE, message="Dify 返回了非 JSON 响应", data={}, http_status=500, mock=False)

    answer = str(result.get("answer") or "").strip()
    new_conv_id = str(result.get("conversation_id") or current_conv_id or "").strip()
    record.conversation_id = new_conv_id

    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError:
        coach = build_assessment_coach_hint(
            user_message=body.message,
            examiner_reply=answer,
            conversation_started=conversation_started,
        )
        db.add(record)
        db.commit()
        return success_response(
            {"reply": answer, "conversation_id": new_conv_id, "is_finished": False, "coach": coach},
            workflow_code=_WORKFLOW_CODE,
            mock=False,
        )

    if isinstance(parsed, dict) and parsed.get("is_finished") is True:
        score = float(parsed.get("score") or 0)
        is_pass = int(parsed.get("is_pass", parsed.get("pass_flag", 0)) or 0)
        reason = str(parsed.get("reason") or parsed.get("comment") or parsed.get("examiner_comment") or parsed.get("message") or "考试已结束")
        coach = build_assessment_coach_hint(
            user_message=body.message,
            examiner_reply=reason,
            is_finished=True,
            score=score,
            is_pass=is_pass,
            conversation_started=conversation_started,
        )
        _finalize_record(record, score=score, is_pass=is_pass, comment=reason)
        db.add(record)
        db.commit()
        return success_response(
            {"reply": reason, "conversation_id": new_conv_id, "is_finished": True, "score": score, "is_pass": is_pass, "coach": coach},
            workflow_code=_WORKFLOW_CODE,
            mock=False,
        )

    coach = build_assessment_coach_hint(
        user_message=body.message,
        examiner_reply=answer,
        conversation_started=conversation_started,
    )
    db.add(record)
    db.commit()
    return success_response(
        {"reply": answer, "conversation_id": new_conv_id, "is_finished": False, "coach": coach},
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/submit-paper")
def submit_paper(
    body: AssessmentSubmitPaperReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == body.record_id).first()
    if not record:
        return error_response(workflow_code=_WORKFLOW_CODE, message="考试记录不存在", data={}, http_status=404, mock=False)
    if str(record.user_id or "") != _current_user_id(current_user):
        return error_response(workflow_code=_WORKFLOW_CODE, message="无权提交该试卷", data={}, http_status=403, mock=False)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == record.task_id).first()
    if not task:
        return error_response(workflow_code=_WORKFLOW_CODE, message="关联的考试任务不存在", data={}, http_status=404, mock=False)
    if task.exam_mode != "paper_exam":
        return error_response(workflow_code=_WORKFLOW_CODE, message="当前考试不是标准试卷考试", data={}, http_status=400, mock=False)
    if _is_timed_out(record):
        _finalize_record(record, score=float(record.score or 0), is_pass=int(record.is_pass or 0), comment="考试超时，系统已自动交卷。", submit_status="timeout_submitted", is_timeout=True)
        db.add(record)
        db.commit()
        return error_response(workflow_code=_WORKFLOW_CODE, message="考试已超时，系统已自动交卷", data={"record_id": record.id}, http_status=400, mock=False)

    timed_out, auto_submitted, timeout_response = _handle_timeout_submission(
        record,
        task,
        score=float(record.score or 0),
        is_pass=int(record.is_pass or 0),
        comment="考试超时，系统已自动交卷。",
    )
    if timed_out:
        if auto_submitted:
            db.add(record)
            db.commit()
        return timeout_response

    answers = body.answers if isinstance(body.answers, dict) else {}
    call = run_wf14_grade_paper(
        user_id=_current_user_id(current_user),
        record_id=int(record.id),
        task_id=int(task.id),
        exam_title=str(_parse_paper_config(task).get("exam_title") or task.task_name or "标准试卷考试"),
        paper_config_json=task.paper_config_json or "{}",
        answers=answers,
        pass_score=float(task.pass_score or 85),
        module_code=str(getattr(task, "module_code", "") or ""),
    )
    if not call.get("ok"):
        result = _local_paper_fallback_payload(task, answers, call)
        paper_answer_json = json.dumps(answers, ensure_ascii=False)
        _finalize_record(
            record,
            score=float(result["score"]),
            is_pass=int(result["is_pass"]),
            comment=str(result["comment"]),
            paper_answer_json=paper_answer_json,
            paper_result_json=str(result["paper_result_json"]),
            submit_status="submitted",
            review_source=str(result["review_source"]),
        )
        db.add(record)
        db.commit()
        return success_response(
            {
                "record_id": record.id,
                "score": result["score"] if record.is_score_visible_to_user else None,
                "is_pass": result["is_pass"],
                "comment": result["comment"],
            },
            workflow_code=_WORKFLOW_CODE,
            mock=False,
        )
    result = call["data"]
    paper_answer_json = json.dumps(answers, ensure_ascii=False)
    paper_result_json = str(result.get("paper_result_json") or "{}")
    _finalize_record(
        record,
        score=float(result["score"]),
        is_pass=int(result["is_pass"]),
        comment=str(result["comment"]),
        paper_answer_json=paper_answer_json,
        paper_result_json=paper_result_json,
        submit_status="submitted",
        review_source=str(result.get("review_source") or "wf14_auto"),
    )
    db.add(record)
    db.commit()
    return success_response(
        {
            "record_id": record.id,
            "score": result["score"] if record.is_score_visible_to_user else None,
            "is_pass": result["is_pass"],
            "comment": result["comment"],
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/finish")
def finish_assessment(
    body: AssessmentFinishReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == body.record_id).first()
    if not record:
        return error_response(workflow_code=_WORKFLOW_CODE, message="考试记录不存在", data={}, http_status=404, mock=False)
    if str(record.user_id or "") != _current_user_id(current_user):
        return error_response(workflow_code=_WORKFLOW_CODE, message="无权提交该考试记录", data={}, http_status=403, mock=False)

    # Non-admin users cannot override score/is_pass — use server-side values only
    actor_role = str(current_user.get("role") or "").strip().lower()
    is_admin = actor_role in ("admin", "store_manager")
    final_score = body.score if is_admin else float(record.score or 0)
    final_is_pass = body.is_pass if is_admin else int(record.is_pass or 0)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == record.task_id).first()
    if task:
        timed_out, auto_submitted, timeout_response = _handle_timeout_submission(
            record,
            task,
            score=float(final_score or 0),
            is_pass=final_is_pass,
            comment=body.comment or "考试超时，系统已自动交卷。",
            review_source="manual_override",
        )
        if timed_out:
            if auto_submitted:
                db.add(record)
                db.commit()
            return timeout_response

    if _is_timed_out(record):
        _finalize_record(
            record,
            score=float(final_score or 0),
            is_pass=final_is_pass,
            comment=body.comment or "考试超时，系统已自动交卷。",
            submit_status="timeout_submitted",
            review_source="manual_override",
            is_timeout=True,
        )
    else:
        _finalize_record(
            record,
            score=final_score,
            is_pass=final_is_pass,
            comment=body.comment or "",
            submit_status="submitted",
            review_source="manual_override",
        )
    db.add(record)
    db.commit()
    return success_response(
        {
            "message": "交卷成功",
            "score": float(record.score or 0),
            "is_pass": int(record.is_pass or 0),
            "comment": record.comment or "",
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/history")
def assessment_history(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    user = _resolve_user(db, current_user)
    if not user:
        return error_response(workflow_code=_WORKFLOW_CODE, message="当前登录用户无效", data={}, http_status=401, mock=False)
    aliases = _user_aliases(user, current_user)

    records = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.user_id.in_(list(aliases)))
        .order_by(AssessmentRecord.id.desc())
        .all()
    )
    reconcile_expired_assessment_records(db, records=records)
    visible_records = [item for item in records if _assessment_history_visible(item)]
    task_ids = [int(item.task_id) for item in visible_records if item.task_id is not None]
    task_map = {item.id: item for item in db.query(AssessmentTask).filter(AssessmentTask.id.in_(task_ids or [0])).all()}

    items = []
    for item in visible_records:
        task = task_map.get(item.task_id)
        visible = bool(int(item.is_score_visible_to_user or 0))
        items.append(
            {
                "record_id": item.id,
                "task_id": item.task_id,
                "task_name": task.task_name if task else "未命名考试",
                "task_type": task.task_type if task else "assessment",
                "exam_mode": task.exam_mode if task else item.exam_mode_snapshot,
                "attempt_no": item.attempt_no,
                "score": float(item.score or 0) if visible else None,
                "is_pass": int(item.is_pass or 0),
                "comment": item.comment or "",
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                "status": item.submit_status or ("finished" if item.finished_at else "in_progress"),
                "submit_status": item.submit_status or "not_started",
                "score_visibility_snapshot": item.score_visibility_snapshot or "public",
                "is_score_visible_to_user": visible,
                "time_spent_seconds": int(item.time_spent_seconds or 0),
                "is_timeout": bool(int(item.is_timeout or 0)),
            }
        )

    return success_response({"items": items}, workflow_code=_WORKFLOW_CODE, mock=False)
