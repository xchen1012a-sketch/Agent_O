"""复盘本“已掌握”判定与手动标记服务。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from learning_taxonomy import normalize_to_dimension
from models import AssessmentRecord, AssistantRecord, PracticeEvalRecord, ReviewNotebookMastery

DEFAULT_PRACTICE_CLEAN_STREAK = 2


def _parse_json(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _is_blank_answer(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _is_wrong_objective(question_type: str, user_answer: Any, correct_answer: Any) -> bool:
    qtype = (question_type or "single").lower()
    if qtype in {"single", "judge"}:
        return str(user_answer or "").strip().upper() != str(correct_answer or "").strip().upper()
    expected = sorted(str(x).strip().upper() for x in (correct_answer or []) if str(x).strip())
    actual = (
        sorted(str(x).strip().upper() for x in (user_answer or []) if str(x).strip())
        if isinstance(user_answer, list)
        else []
    )
    return not expected or actual != expected


def _essay_keyword_hit(question: dict[str, Any], user_answer: Any) -> bool:
    keywords = [str(k).strip().lower() for k in (question.get("keywords") or []) if str(k).strip()]
    if not keywords:
        return True
    text = str(user_answer or "").strip().lower()
    if not text:
        return False
    hits = sum(1 for kw in keywords if kw and kw in text)
    return (hits / len(keywords)) >= 0.7


def is_assessment_item_mastered(db: Session, user_id: str, task_id: int, question_id: str) -> bool:
    if not user_id or not task_id or not question_id:
        return False

    passing_record = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.user_id == user_id, AssessmentRecord.task_id == task_id, AssessmentRecord.is_pass == 1)
        .order_by(AssessmentRecord.finished_at.desc(), AssessmentRecord.id.desc())
        .first()
    )
    if not passing_record:
        return False

    answers = _parse_json(passing_record.paper_answer_json, {})
    if not answers or question_id not in answers:
        return False

    from models import AssessmentTask

    task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
    if not task:
        return False

    paper = _parse_json(task.paper_config_json, {})
    questions = paper.get("questions") if isinstance(paper.get("questions"), list) else []
    question = next((q for q in questions if isinstance(q, dict) and str(q.get("id")) == question_id), None)
    if not question:
        return False

    user_answer = answers.get(question_id)
    if _is_blank_answer(user_answer):
        return False

    qtype = str(question.get("type") or "single").lower()
    correct_answer = question.get("answer")
    if qtype in {"single", "judge", "multiple"}:
        return not _is_wrong_objective(qtype, user_answer, correct_answer)
    return _essay_keyword_hit(question, user_answer)


def is_practice_dimension_mastered(
    db: Session,
    user_id: str,
    dimension: str,
    consecutive_n: int = DEFAULT_PRACTICE_CLEAN_STREAK,
) -> bool:
    if not user_id or not dimension or consecutive_n < 1:
        return False

    records = (
        db.query(PracticeEvalRecord)
        .filter(PracticeEvalRecord.user_id == user_id)
        .order_by(PracticeEvalRecord.created_at.desc(), PracticeEvalRecord.id.desc())
        .limit(consecutive_n * 3)
        .all()
    )
    if len(records) < consecutive_n:
        return False

    clean_count = 0
    for record in records:
        record_dim = normalize_to_dimension(record.weak_dimension or "", module_code=record.module_code or "")
        if record_dim != dimension:
            continue
        points = _parse_json(record.problem_points_json, [])
        concrete = [p for p in (points or []) if str(p or "").strip()]
        if not concrete:
            clean_count += 1
        else:
            clean_count = 0
        if clean_count >= consecutive_n:
            return True

    return False


def is_assistant_item_mastered(db: Session, user_id: str, knowledge_tag: str, scene_hint: str = "") -> bool:
    if not user_id or not knowledge_tag:
        return False

    query = db.query(AssistantRecord).filter(AssistantRecord.user_id == user_id, AssistantRecord.knowledge_tag == knowledge_tag)
    if scene_hint:
        query = query.filter(AssistantRecord.scene_hint == scene_hint)

    latest = query.order_by(AssistantRecord.created_at.desc(), AssistantRecord.id.desc()).first()
    if not latest:
        return False

    return str(latest.risk_level or "").strip().lower() != "high"


def check_auto_mastery(
    db: Session,
    user_id: str,
    source: str,
    record_id: int,
    question_id: str = "",
    task_id: int = 0,
    dimension: str = "",
    knowledge_tag: str = "",
    scene_hint: str = "",
) -> bool:
    if source == "assessment" and task_id and question_id:
        return is_assessment_item_mastered(db, user_id, task_id, question_id)
    if source == "practice" and dimension:
        return is_practice_dimension_mastered(db, user_id, dimension)
    if source in {"assistant", "qa"} and knowledge_tag:
        return is_assistant_item_mastered(db, user_id, knowledge_tag, scene_hint)
    return False


def load_manual_mastery_keys(db: Session, user_id: str) -> set[tuple[str, int, str]]:
    if not user_id:
        return set()

    rows = db.query(ReviewNotebookMastery).filter(ReviewNotebookMastery.user_id == user_id).all()
    return {(r.source, r.source_record_id, r.question_id) for r in rows}


def mark_as_mastered(
    db: Session,
    *,
    user_id: str,
    source: str,
    source_record_id: int,
    question_id: str = "",
    dimension: str = "",
    knowledge_tag: str = "",
    title: str = "",
    remark: str = "",
    marked_by: str = "",
) -> dict[str, Any]:
    existing = (
        db.query(ReviewNotebookMastery)
        .filter(
            ReviewNotebookMastery.user_id == user_id,
            ReviewNotebookMastery.source == source,
            ReviewNotebookMastery.source_record_id == source_record_id,
            ReviewNotebookMastery.question_id == question_id,
        )
        .first()
    )
    if existing:
        return {"id": existing.id, "status": "already_mastered"}

    mastery = ReviewNotebookMastery(
        user_id=user_id,
        source=source,
        source_record_id=source_record_id,
        question_id=question_id,
        dimension=dimension,
        knowledge_tag=knowledge_tag,
        title=title,
        remark=remark,
        reason="manual",
        marked_by=marked_by or user_id,
    )
    db.add(mastery)
    db.commit()
    db.refresh(mastery)
    return {"id": mastery.id, "status": "marked"}


def unmark_as_mastered(db: Session, user_id: str, mastery_id: int) -> bool:
    mastery = (
        db.query(ReviewNotebookMastery)
        .filter(ReviewNotebookMastery.id == mastery_id, ReviewNotebookMastery.user_id == user_id)
        .first()
    )
    if not mastery:
        return False
    db.delete(mastery)
    db.commit()
    return True


def get_manual_masteries(db: Session, user_id: str, source: str = "", limit: int = 50) -> list[dict[str, Any]]:
    if not user_id:
        return []

    query = db.query(ReviewNotebookMastery).filter(ReviewNotebookMastery.user_id == user_id)
    if source:
        query = query.filter(ReviewNotebookMastery.source == source)

    rows = query.order_by(ReviewNotebookMastery.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_record_id": r.source_record_id,
            "question_id": r.question_id,
            "dimension": r.dimension,
            "knowledge_tag": r.knowledge_tag,
            "title": r.title,
            "remark": r.remark,
            "reason": r.reason,
            "marked_by": r.marked_by,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
