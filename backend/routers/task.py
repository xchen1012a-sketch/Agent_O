from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String as SAString, cast, or_
from sqlalchemy.orm import Session

from api_response import dify_failure_response, error_response, success_response
from .assessment import reconcile_expired_assessment_records
from auth import get_current_user, normalize_app_role
from audit import log_audit_from_user
from database import get_db, utc_now
from dify_assessment import run_wf13_generate_paper
from models import (
    AssessmentRecord,
    AssessmentTask,
    AssessmentTaskPaper,
    AssessmentTaskTarget,
    Store,
    User,
)
from schemas import (
    TaskArchiveReq,
    TaskCreate,
    TaskDeleteReq,
    TaskPaperGenerateReq,
    TaskPaperReviewReq,
    TaskPublishReq,
    TaskRetakeReq,
)

router = APIRouter(prefix="/api/tasks", tags=["Assessment Tasks"])

_WORKFLOW_CODE = "assessment_tasks"
_ALLOWED_MANAGER_ROLES = {"admin", "store_manager"}
_STANDARD_PAPER_DUPLICATE_RETRY_LIMIT = 3


def _audit_task(current_user: dict, action: str, target_type: str, target_id: str, target_name: str = "") -> None:
    """Fire-and-forget audit for task mutations (uses separate sqlite3 conn)."""
    try:
        from db_stage3 import get_conn as _get_conn
        with _get_conn() as conn:
            log_audit_from_user(conn, current_user, action=action, target_type=target_type,
                                target_id=target_id, target_name=target_name)
    except Exception:
        pass


def _current_role(current_user: dict) -> str:
    return normalize_app_role(str(current_user.get("role") or ""))


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


def _resolve_store_id(db: Session, current_user: dict) -> str:
    user = _resolve_user(db, current_user)
    return str(user.store_id or "").strip() if user else ""


def _normalize_paper_config_json(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"paper_config_json 不是合法 JSON：{exc.msg}") from exc
    if not isinstance(parsed, (dict, list)):
        raise ValueError("paper_config_json 仅支持 JSON 对象或数组")
    return json.dumps(parsed, ensure_ascii=False)


def _normalize_exam_mode(exam_mode: str | None, task_type: str | None) -> str:
    mode = str(exam_mode or "").strip()
    if mode in {"ai_blind_box_exam", "paper_exam"}:
        return mode
    raw_type = str(task_type or "").strip().lower()
    if raw_type in {"paper_exam", "mixed_exam"}:
        return "paper_exam"
    return "ai_blind_box_exam"


def _task_type_from_exam_mode(exam_mode: str) -> str:
    return "paper_exam" if exam_mode == "paper_exam" else "blind_box_exam"


def _normalize_module_code(module_code: str | None) -> str:
    return str(module_code or "").strip()


def _normalize_target_values(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _normalize_compare_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _paper_question_signature(paper_config_json: str | None) -> str:
    raw = str(paper_config_json or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _normalize_compare_text(raw)
    if isinstance(parsed, dict):
        questions = parsed.get("questions")
        if isinstance(questions, list) and questions:
            normalized_questions: list[dict[str, Any]] = []
            for item in questions:
                if not isinstance(item, dict):
                    normalized_questions.append({"value": _normalize_compare_text(item)})
                    continue
                normalized_item: dict[str, Any] = {
                    "id": _normalize_compare_text(item.get("id")),
                    "type": _normalize_compare_text(item.get("type")),
                    "title": _normalize_compare_text(item.get("title")),
                    "score": item.get("score"),
                }
                options = item.get("options")
                if isinstance(options, list):
                    normalized_item["options"] = [
                        {
                            "value": _normalize_compare_text(opt.get("value")) if isinstance(opt, dict) else _normalize_compare_text(opt),
                            "label": _normalize_compare_text(opt.get("label")) if isinstance(opt, dict) else "",
                        }
                        for opt in options
                    ]
                keywords = item.get("keywords")
                if isinstance(keywords, list):
                    normalized_item["keywords"] = [_normalize_compare_text(word) for word in keywords]
                normalized_questions.append(normalized_item)
            return json.dumps(normalized_questions, ensure_ascii=False, sort_keys=True)
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    return _normalize_compare_text(parsed)


def _recent_standard_paper_signatures(db: Session, module_code: str, *, limit: int = 6) -> set[str]:
    normalized_code = _normalize_module_code(module_code)
    if not normalized_code:
        return set()
    rows = (
        db.query(AssessmentTask.paper_config_json)
        .filter(
            AssessmentTask.exam_mode == "paper_exam",
            AssessmentTask.module_code == normalized_code,
            AssessmentTask.paper_config_json.isnot(None),
            AssessmentTask.paper_config_json != "",
        )
        .order_by(AssessmentTask.id.desc())
        .limit(max(int(limit), 1))
        .all()
    )
    signatures: set[str] = set()
    for (paper_config_json,) in rows:
        signature = _paper_question_signature(paper_config_json)
        if signature:
            signatures.add(signature)
    return signatures


def _generate_unique_standard_paper(
    *,
    db: Session,
    current_user: dict,
    body: TaskPaperGenerateReq,
) -> dict[str, Any]:
    seen_signatures = _recent_standard_paper_signatures(db, body.module_code)
    latest_call: dict[str, Any] = {}
    for _ in range(_STANDARD_PAPER_DUPLICATE_RETRY_LIMIT):
        latest_call = run_wf13_generate_paper(
            user_id=_current_user_id(current_user),
            task_name=body.task_name,
            task_desc=body.task_desc,
            module_code=body.module_code,
            difficulty=body.difficulty,
            question_count=max(int(body.question_count or 20), 1),
            question_mix=body.question_mix,
            pass_score=float(body.pass_score or 85),
            generation_batch=uuid.uuid4().hex,
        )
        if not latest_call.get("ok"):
            return latest_call
        payload = latest_call.get("data") if isinstance(latest_call.get("data"), dict) else {}
        signature = _paper_question_signature(payload.get("paper_config_json"))
        if not signature or signature not in seen_signatures:
            return latest_call
    return latest_call


def _task_targets(db: Session, task_id: int) -> list[AssessmentTaskTarget]:
    return (
        db.query(AssessmentTaskTarget)
        .filter(AssessmentTaskTarget.task_id == task_id)
        .order_by(AssessmentTaskTarget.id.asc())
        .all()
    )


def _coerce_utc_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _employee_no(row_id: int | str | None) -> str:
    try:
        return f"EMP{int(row_id):05d}"
    except (TypeError, ValueError):
        return str(row_id or "").strip()


def _resolve_target_labels(db: Session, targets: list[AssessmentTaskTarget]) -> tuple[dict[str, str], dict[str, str]]:
    store_ids: list[str] = []
    account_ids: list[str] = []
    for item in targets:
        target_type = str(item.target_type or "").strip().lower()
        target_value = str(item.target_value or "").strip()
        if not target_value:
            continue
        if target_type == "store":
            store_ids.append(target_value)
        elif target_type == "account":
            account_ids.append(target_value)

    store_labels: dict[str, str] = {}
    if store_ids:
        for store in db.query(Store).filter(Store.store_id.in_(store_ids)).all():
            store_id = str(store.store_id or "").strip()
            if not store_id:
                continue
            store_name = str(store.store_name or store.name or store_id).strip() or store_id
            store_labels[store_id] = f"{store_name}({store_id})"

    account_labels: dict[str, str] = {}
    if account_ids:
        users = (
            db.query(User)
            .filter(or_(cast(User.id, SAString).in_(account_ids), User.user_id.in_(account_ids)))
            .all()
        )
        for user in users:
            label = str(user.display_name or user.name or user.username or user.user_id or user.id).strip()
            if not label:
                label = str(user.user_id or user.id or "").strip()
            display = f"{label}({_employee_no(user.id)})"
            account_labels[str(user.id)] = display
            if user.user_id:
                account_labels[str(user.user_id)] = display

    return store_labels, account_labels


def _resolve_user_display_labels(db: Session, identifiers: list[str] | None) -> dict[str, str]:
    values = [str(value or "").strip() for value in identifiers or [] if str(value or "").strip()]
    if not values:
        return {}

    labels: dict[str, str] = {}
    users = (
        db.query(User)
        .filter(or_(cast(User.id, SAString).in_(values), User.user_id.in_(values)))
        .all()
    )
    for user in users:
        label = str(user.display_name or user.name or user.username or user.user_id or user.id).strip()
        if not label:
            label = str(user.user_id or user.id or "").strip()
        labels[str(user.id)] = label
        if user.user_id:
            labels[str(user.user_id)] = label
    return labels


def _resolve_account_target_values(db: Session, values: list[str] | None) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()

    for raw in values or []:
        token = str(raw or "").strip()
        if not token:
            continue

        users = (
            db.query(User)
            .filter(
                or_(
                    cast(User.id, SAString) == token,
                    User.user_id == token,
                    User.username == token,
                    User.display_name == token,
                )
            )
            .all()
        )

        unique_users: list[User] = []
        unique_keys: set[str] = set()
        for user in users:
            key = str(user.user_id or user.id or "").strip()
            if not key or key in unique_keys:
                continue
            unique_keys.add(key)
            unique_users.append(user)

        if not unique_users:
            raise ValueError(f"账号“{token}”不存在，请填写账号姓名或用户名")

        if len(unique_users) > 1:
            options = "、".join(
                f"{str(user.display_name or user.username or user.user_id or user.id).strip()}({str(user.username or user.user_id or user.id).strip()})"
                for user in unique_users[:5]
            )
            raise ValueError(f"账号“{token}”匹配到多个员工，请改用用户名：{options}")

        user = unique_users[0]
        target_value = str(user.user_id or user.id or "").strip()
        if target_value and target_value not in seen:
            seen.add(target_value)
            resolved.append(target_value)

    return resolved


def _target_summary(task: AssessmentTask, targets: list[AssessmentTaskTarget]) -> str:
    if targets:
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in targets:
            grouped[str(item.target_type or "store")].append(str(item.target_value or ""))
        parts = []
        if grouped.get("store"):
            parts.append("门店:" + "、".join(grouped["store"]))
        if grouped.get("account"):
            parts.append("账号:" + "、".join(grouped["account"]))
        return " / ".join(parts)
    return str(task.target_scope or "all")


def _serialize_task(task: AssessmentTask, targets: list[AssessmentTaskTarget], completed_count: int = 0) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_name": task.task_name,
        "task_type": task.task_type,
        "task_desc": task.task_desc or "",
        "module_code": _normalize_module_code(getattr(task, "module_code", "")),
        "paper_config_json": task.paper_config_json or "",
        "publisher_id": task.publisher_id,
        "target_scope": task.target_scope or "",
        "target_scope_type": task.target_scope_type or "store",
        "target_summary": _target_summary(task, targets),
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "pass_score": float(task.pass_score or 0),
        "status": task.status,
        "exam_mode": task.exam_mode or "ai_blind_box_exam",
        "duration_minutes": int(task.duration_minutes or 60),
        "score_visibility": task.score_visibility or "public",
        "publish_status": task.publish_status or "draft",
        "paper_generation_status": task.paper_generation_status or "not_needed",
        "published_at": task.published_at.isoformat() if task.published_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
        "target_count": len(targets),
        "completed_count": completed_count,
        "max_attempts": int(task.max_attempts or 0),
        "allow_retake": bool(task.allow_retake),
    }


def _task_is_archived(task: AssessmentTask | None) -> bool:
    if not task:
        return False
    publish_status = str(task.publish_status or "").strip().lower()
    status = str(task.status or "").strip().lower()
    return publish_status == "archived" or status in {"archived", "closed"}


def _target_summary_readable(db: Session, task: AssessmentTask, targets: list[AssessmentTaskTarget]) -> str:
    if targets:
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in targets:
            target_type = str(item.target_type or "store").strip().lower()
            target_value = str(item.target_value or "").strip()
            if target_value:
                grouped[target_type].append(target_value)

        store_labels, account_labels = _resolve_target_labels(db, targets)
        parts: list[str] = []
        if grouped.get("account"):
            account_items = [account_labels.get(value) or value for value in grouped["account"]]
            parts.append("个人：" + "、".join(account_items))
        if grouped.get("store"):
            store_items = [store_labels.get(value) or value for value in grouped["store"]]
            parts.append("门店：" + "、".join(store_items))
        if parts:
            return " / ".join(parts)

    scope = str(task.target_scope or "").strip()
    if not scope or scope == "all":
        return "全员"
    return scope


def _serialize_task_view(
    db: Session,
    task: AssessmentTask,
    targets: list[AssessmentTaskTarget],
    completed_count: int = 0,
    publisher_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    item = _serialize_task(task, targets, completed_count)
    item["target_summary"] = _target_summary_readable(db, task, targets)
    publisher_id = str(task.publisher_id or "").strip()
    item["publisher_name"] = (publisher_labels or {}).get(publisher_id) or publisher_id
    return item


def _is_task_assigned_to_user(task: AssessmentTask, targets: list[AssessmentTaskTarget], user_aliases: set[str], store_id: str) -> bool:
    if not targets:
        scope = str(task.target_scope or "").strip()
        return scope in {"", "all", store_id}
    for target in targets:
        target_type = str(target.target_type or "store").strip()
        target_value = str(target.target_value or "").strip()
        if target_type == "store" and store_id and target_value == store_id:
            return True
        if target_type == "account" and target_value in user_aliases:
            return True
    return False


def _upsert_targets(
    db: Session,
    *,
    task_id: int,
    target_scope_type: str,
    store_ids: list[str],
    account_ids: list[str],
) -> tuple[str, str]:
    normalized_stores = _normalize_target_values(store_ids)
    normalized_accounts = _resolve_account_target_values(db, _normalize_target_values(account_ids))
    db.query(AssessmentTaskTarget).filter(AssessmentTaskTarget.task_id == task_id).delete()

    created_targets: list[AssessmentTaskTarget] = []
    for value in normalized_stores:
        created_targets.append(AssessmentTaskTarget(task_id=task_id, target_type="store", target_value=value))
    for value in normalized_accounts:
        created_targets.append(AssessmentTaskTarget(task_id=task_id, target_type="account", target_value=value))
    for item in created_targets:
        db.add(item)

    if normalized_stores and normalized_accounts:
        target_type = "mixed"
        target_scope = ",".join(normalized_stores + normalized_accounts)
    elif normalized_accounts:
        target_type = "account"
        target_scope = ",".join(normalized_accounts)
    elif normalized_stores:
        target_type = "store"
        target_scope = ",".join(normalized_stores)
    else:
        target_type = target_scope_type or "store"
        target_scope = "all"
    return target_type, target_scope


def _build_generated_paper(body: TaskPaperGenerateReq) -> dict[str, Any]:
    total = max(1, int(body.question_count or 10))
    mix = body.question_mix or {}
    type_order = ["single", "multiple", "judge", "short"]
    weights = {key: int(mix.get(key) or 0) for key in type_order}
    if sum(weights.values()) <= 0:
        weights = {"single": max(total - 3, 4), "multiple": 2, "judge": 2, "short": 1}
    questions = []
    index = 1
    allocated = 0
    for question_type in type_order:
        desired = weights.get(question_type, 0)
        count = desired
        if allocated + count > total:
            count = max(total - allocated, 0)
        for _ in range(count):
            question = {
                "id": f"q{index}",
                "type": question_type,
                "title": f"{body.task_name} 第 {index} 题",
                "score": 10,
            }
            if question_type in {"single", "multiple", "judge"}:
                options = [
                    {"value": "A", "label": "选项 A"},
                    {"value": "B", "label": "选项 B"},
                    {"value": "C", "label": "选项 C"},
                    {"value": "D", "label": "选项 D"},
                ]
                if question_type == "judge":
                    options = [{"value": "A", "label": "正确"}, {"value": "B", "label": "错误"}]
                    question["answer"] = "A"
                elif question_type == "multiple":
                    question["answer"] = ["A", "C"]
                else:
                    question["answer"] = "A"
                question["options"] = options
            else:
                question["keywords"] = ["要点1", "要点2"]
                question["placeholder"] = "请从产品知识、接待逻辑、合规表达三个方面作答"
            questions.append(question)
            index += 1
            allocated += 1
            if allocated >= total:
                break
        if allocated >= total:
            break
    return {
        "exam_title": body.task_name,
        "instructions": body.task_desc or "请按要求完成全部题目后提交试卷。",
        "questions": questions,
        "answer_key": {str(item["id"]): item.get("answer", item.get("keywords", [])) for item in questions},
        "pass_score": float(body.pass_score or 85),
    }


@router.post("/create")
def create_task(
    body: TaskCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可创建任务", {}, 403, False)

    publisher_id = _current_user_id(current_user)
    if not publisher_id:
        return error_response(_WORKFLOW_CODE, "当前登录用户无效", {}, 401, False)

    exam_mode = _normalize_exam_mode(body.exam_mode, body.task_type)
    try:
        paper_config_json = _normalize_paper_config_json(body.paper_config_json)
    except ValueError as exc:
        return error_response(_WORKFLOW_CODE, str(exc), {}, 400, False)
    module_code = _normalize_module_code(body.module_code)
    if not module_code:
        return error_response(_WORKFLOW_CODE, "考试任务必须绑定 module_code", {}, 400, False)

    task = AssessmentTask(
        task_name=body.task_name,
        task_type=_task_type_from_exam_mode(exam_mode),
        task_desc=body.task_desc or "",
        module_code=module_code,
        paper_config_json=paper_config_json,
        publisher_id=publisher_id,
        target_scope=body.target_scope or "",
        deadline=body.deadline,
        pass_score=body.pass_score,
        status="draft",
        exam_mode=exam_mode,
        duration_minutes=max(int(body.duration_minutes or 60), 1),
        score_visibility=str(body.score_visibility or "public"),
        publish_status="draft",
        target_scope_type=str(body.target_scope_type or "store"),
        paper_generation_status="generated" if paper_config_json and exam_mode == "paper_exam" else ("not_needed" if exam_mode != "paper_exam" else "not_needed"),
        paper_source_type="manual" if paper_config_json else "ai_generated",
        allow_retake=1 if body.allow_retake else 0,
        max_attempts=max(int(body.max_attempts or 1), 1),
        started_notice_text=str(body.started_notice_text or ""),
        submitted_notice_text=str(body.submitted_notice_text or ""),
        created_by_role=_current_role(current_user),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    if body.store_ids or body.account_ids:
        try:
            target_type, target_scope = _upsert_targets(
                db,
                task_id=int(task.id),
                target_scope_type=task.target_scope_type,
                store_ids=body.store_ids,
                account_ids=body.account_ids,
            )
        except ValueError as exc:
            return error_response(_WORKFLOW_CODE, str(exc), {}, 400, False)
        task.target_scope_type = target_type
        task.target_scope = target_scope
        db.add(task)
        db.commit()
        db.refresh(task)

    return success_response(
        {
            "task_id": task.id,
            "publish_status": task.publish_status,
            "exam_mode": task.exam_mode,
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/generate-paper")
def generate_paper(
    body: TaskPaperGenerateReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可生成试卷草稿", {}, 403, False)

    module_code = _normalize_module_code(body.module_code)
    if not module_code:
        return error_response(_WORKFLOW_CODE, "标准考试出卷必须传入 module_code", {}, 400, False)
    call = _generate_unique_standard_paper(
        db=db,
        current_user=current_user,
        body=body,
    )
    if not call.get("ok"):
        return dify_failure_response(
            workflow_code="wf13_standard_paper",
            route_path="/api/tasks/generate-paper",
            call=call,
        )
    return success_response(
        call["data"],
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/review-paper")
def review_paper(
    body: TaskPaperReviewReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可审核试卷", {}, 403, False)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == body.task_id).first()
    if not task:
        return error_response(_WORKFLOW_CODE, "考试任务不存在", {}, 404, False)
    try:
        paper_config_json = _normalize_paper_config_json(body.paper_config_json)
    except ValueError as exc:
        return error_response(_WORKFLOW_CODE, str(exc), {}, 400, False)

    version = max(int(body.paper_version or 1), 1)
    paper = AssessmentTaskPaper(
        task_id=int(task.id),
        paper_version=version,
        paper_status="reviewed",
        source_type="manual",
        paper_config_json=paper_config_json,
        review_comment=str(body.review_comment or ""),
        reviewed_by=_current_user_id(current_user),
    )
    db.add(paper)
    task.paper_config_json = paper_config_json
    task.paper_generation_status = "reviewed"
    task.paper_review_version = version
    task.paper_source_type = "manual"
    db.add(task)
    db.commit()

    return success_response(
        {"saved_version": version, "paper_status": "reviewed"},
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/publish")
def publish_task(
    body: TaskPublishReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可发布任务", {}, 403, False)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == body.task_id).first()
    if not task:
        return error_response(_WORKFLOW_CODE, "考试任务不存在", {}, 404, False)

    try:
        target_type, target_scope = _upsert_targets(
            db,
            task_id=int(task.id),
            target_scope_type=body.target_scope_type,
            store_ids=body.store_ids,
            account_ids=body.account_ids,
        )
    except ValueError as exc:
        return error_response(_WORKFLOW_CODE, str(exc), {}, 400, False)
    task.target_scope_type = target_type
    task.target_scope = target_scope
    task.duration_minutes = max(int(body.duration_minutes or task.duration_minutes or 60), 1)
    task.score_visibility = str(body.score_visibility or task.score_visibility or "public")
    task.publish_status = "published"
    task.status = "active"
    task.published_at = utc_now()
    if body.deadline:
        task.deadline = body.deadline
    if task.exam_mode == "paper_exam" and task.paper_generation_status == "not_needed":
        task.paper_generation_status = "generated" if task.paper_config_json else "not_needed"
    db.add(task)
    db.commit()

    _audit_task(current_user, "exam_publish", "exam_task", str(task.id), task.title if hasattr(task, 'title') else str(task.id))

    return success_response(
        {
            "published_task_id": task.id,
            "target_count": len(body.store_ids or []) + len(body.account_ids or []),
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/archive")
def archive_task(
    body: TaskArchiveReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可归档任务", {}, 403, False)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == body.task_id).first()
    if not task:
        return error_response(_WORKFLOW_CODE, "考试任务不存在", {}, 404, False)
    task.publish_status = "archived"
    task.status = "closed"
    db.add(task)
    db.commit()
    _audit_task(current_user, "exam_archive", "exam_task", str(task.id), task.title if hasattr(task, 'title') else str(task.id))
    return success_response({"task_id": task.id, "publish_status": task.publish_status}, workflow_code=_WORKFLOW_CODE, mock=False)


@router.post("/retake")
def retake_task(
    body: TaskRetakeReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可重新发布重考", {}, 403, False)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == body.task_id).first()
    if not task:
        return error_response(_WORKFLOW_CODE, "考试任务不存在", {}, 404, False)
    if not _task_is_archived(task):
        return error_response(_WORKFLOW_CODE, "仅已归档的历史试卷支持重新发布重考", {}, 400, False)

    deleted_records = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.task_id == int(task.id))
        .delete(synchronize_session=False)
    )
    task.publish_status = "published"
    task.status = "active"
    task.published_at = utc_now()
    db.add(task)
    db.commit()
    _audit_task(current_user, "exam_retake", "exam_task", str(task.id), task.task_name or str(task.id))
    return success_response(
        {
            "task_id": int(task.id),
            "publish_status": task.publish_status,
            "status": task.status,
            "deleted_record_count": int(deleted_records or 0),
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.post("/delete")
def delete_task(
    body: TaskDeleteReq,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可删除历史试卷", {}, 403, False)
    task = db.query(AssessmentTask).filter(AssessmentTask.id == body.task_id).first()
    if not task:
        return error_response(_WORKFLOW_CODE, "考试任务不存在", {}, 404, False)
    if not _task_is_archived(task):
        return error_response(_WORKFLOW_CODE, "请先归档试卷，再执行彻底删除", {}, 400, False)

    task_id = int(task.id)
    task_name = task.task_name or str(task.id)
    deleted_records = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.task_id == task_id)
        .delete(synchronize_session=False)
    )
    deleted_targets = (
        db.query(AssessmentTaskTarget)
        .filter(AssessmentTaskTarget.task_id == task_id)
        .delete(synchronize_session=False)
    )
    deleted_papers = (
        db.query(AssessmentTaskPaper)
        .filter(AssessmentTaskPaper.task_id == task_id)
        .delete(synchronize_session=False)
    )
    db.delete(task)
    db.commit()
    _audit_task(current_user, "exam_delete", "exam_task", str(task_id), task_name)
    return success_response(
        {
            "task_id": task_id,
            "deleted_record_count": int(deleted_records or 0),
            "deleted_target_count": int(deleted_targets or 0),
            "deleted_paper_count": int(deleted_papers or 0),
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )


@router.get("/list")
def list_tasks(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    status: str | None = Query(default=None, description="任务状态筛选"),
):
    if _current_role(current_user) not in _ALLOWED_MANAGER_ROLES:
        return error_response(_WORKFLOW_CODE, "权限不足，仅管理员或店长可查看管理端任务列表", {}, 403, False)

    query = db.query(AssessmentTask)
    if status and status != "all":
        lowered = str(status).strip().lower()
        if lowered in {"draft", "published", "archived"}:
            query = query.filter(AssessmentTask.publish_status == lowered)
        else:
            query = query.filter(AssessmentTask.status == lowered)

    tasks = query.order_by(AssessmentTask.created_at.desc()).all()
    task_ids = [int(task.id) for task in tasks if task.id is not None]
    targets_by_task: dict[int, list[AssessmentTaskTarget]] = defaultdict(list)
    if task_ids:
        for target in db.query(AssessmentTaskTarget).filter(AssessmentTaskTarget.task_id.in_(task_ids)).all():
            targets_by_task[int(target.task_id)].append(target)

    completed_counts: dict[int, int] = defaultdict(int)
    if task_ids:
        records = db.query(AssessmentRecord).filter(AssessmentRecord.task_id.in_(task_ids)).all()
        pass_pairs = {(int(record.task_id), str(record.user_id or "")) for record in records if int(record.is_pass or 0) == 1}
        for task_id, _ in pass_pairs:
            completed_counts[task_id] += 1

    publisher_labels = _resolve_user_display_labels(db, [str(task.publisher_id or "").strip() for task in tasks])
    items = [
        _serialize_task_view(
            db,
            task,
            targets_by_task.get(int(task.id), []),
            completed_counts.get(int(task.id), 0),
            publisher_labels,
        )
        for task in tasks
    ]
    return success_response({"items": items}, workflow_code=_WORKFLOW_CODE, mock=False)


@router.get("/my")
def my_tasks(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
):
    actor_id = _current_user_id(current_user)
    if not actor_id:
        return error_response(_WORKFLOW_CODE, "当前登录用户无效", {}, 401, False)

    user = _resolve_user(db, current_user)
    if not user:
        return error_response(_WORKFLOW_CODE, "当前用户不存在", {}, 404, False)
    store_id = str(user.store_id or "").strip()
    user_aliases = {actor_id, str(user.id or ""), str(user.user_id or "")}

    tasks = (
        db.query(AssessmentTask)
        .filter(AssessmentTask.publish_status == "published")
        .filter(AssessmentTask.status == "active")
        .order_by(AssessmentTask.published_at.desc(), AssessmentTask.created_at.desc())
        .all()
    )
    task_ids = [int(task.id) for task in tasks if task.id is not None]
    targets_by_task: dict[int, list[AssessmentTaskTarget]] = defaultdict(list)
    if task_ids:
        for target in db.query(AssessmentTaskTarget).filter(AssessmentTaskTarget.task_id.in_(task_ids)).all():
            targets_by_task[int(target.task_id)].append(target)

    records = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.user_id.in_(list(user_aliases)))
        .filter(AssessmentRecord.task_id.in_(task_ids or [0]))
        .order_by(AssessmentRecord.id.desc())
        .all()
    )
    reconcile_expired_assessment_records(db, records=records)
    records_by_task: dict[int, list[AssessmentRecord]] = defaultdict(list)
    for record in records:
        records_by_task[int(record.task_id)].append(record)

    grouped = {"todo": [], "retake": [], "completed": []}
    now = utc_now()
    for task in tasks:
        targets = targets_by_task.get(int(task.id), [])
        if not _is_task_assigned_to_user(task, targets, user_aliases, store_id):
            continue
        task_records = records_by_task.get(int(task.id), [])
        passed = any(int(record.is_pass or 0) == 1 for record in task_records)
        attempts_used = len(task_records)
        latest_record = task_records[0] if task_records else None
        latest_status = str(latest_record.submit_status or "") if latest_record else ""
        remaining_seconds = 0
        expires_at = _coerce_utc_dt(latest_record.expires_at) if latest_record else None
        if expires_at and latest_status == "in_progress":
            remaining_seconds = max(int((expires_at - now).total_seconds()), 0)
        item = _serialize_task_view(db, task, targets)
        item.update(
            {
                "attempt_count": attempts_used,
                "max_attempts": int(task.max_attempts or 0),
                "remaining_seconds": remaining_seconds,
                "submit_status": latest_status or "not_started",
                "record_id": int(latest_record.id) if latest_record else None,
                "is_score_visible_to_user": bool(int(latest_record.is_score_visible_to_user or 0)) if latest_record else (task.score_visibility != "hidden"),
            }
        )
        live_in_progress = latest_status == "in_progress" and remaining_seconds > 0
        if passed or (attempts_used >= int(task.max_attempts or 0) and not live_in_progress):
            grouped["completed"].append(item)
        elif attempts_used > 0:
            grouped["retake"].append(item)
        else:
            grouped["todo"].append(item)

    return success_response(
        {
            "items": grouped["todo"] + grouped["retake"],
            "todo": grouped["todo"],
            "retake": grouped["retake"],
            "completed": grouped["completed"],
        },
        workflow_code=_WORKFLOW_CODE,
        mock=False,
    )
