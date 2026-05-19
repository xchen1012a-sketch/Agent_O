"""复盘本服务（D2）。

聚合三类数据源里学员的"错/弱项"，按能力维度归一化输出，给前端复盘视图用：

1. ``assessment_records``  — 客观题答错（对比 ``assessment_tasks.paper_config_json`` 标准答案）
2. ``practice_eval_records`` — 智能陪练 AI 评出的弱维度 / problem_points
3. ``assistant_records`` — 在岗助手识别为 ``risk_level=high`` 的顾客提问

设计要点：
- 只读，无 schema 变更。
- 每条 item 统一形态（见 ``ReviewItem``），便于前端跨源同一渲染。
- 维度归一化全部走 ``learning_taxonomy``，避免自由文本污染聚合 summary。
- 聚合层产出 ``by_dimension / by_module / by_source / recurring_top``，供 mini 雷达和置顶区使用。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from learning_taxonomy import (
    DIMENSION_ORDER,
    OTHER_DIMENSION,
    dimension_label,
    module_label,
    normalize_module,
    normalize_to_dimension,
)
from models import AssessmentRecord, AssessmentTask, AssistantRecord, PracticeEvalRecord
from review_notebook_mastery import check_auto_mastery, load_manual_mastery_keys

_log = logging.getLogger("jewelry_qipei.review_notebook")

DEFAULT_RECORD_LIMIT = 50
DEFAULT_RETURN_LIMIT = 100
RECURRING_THRESHOLD = 2
RECURRING_TOP_N = 5

SOURCES = ("assessment", "practice", "assistant", "qa")
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

QA_SCENE_HINT = "knowledge_qa"


@dataclass(frozen=True)
class SuggestedAction:
    type: str
    route: str
    module_code: str = ""
    label: str = ""


@dataclass
class ReviewItem:
    source: str
    dimension: str
    dimension_label: str
    module_code: str
    module_label: str
    title: str
    evidence: str
    severity: str
    occurred_at: str
    record_id: int
    suggested_action: SuggestedAction
    question_id: str = ""
    user_answer: Any = None
    correct_answer: Any = None
    knowledge_tag: str = ""
    stage_no: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "dimension": self.dimension,
            "dimension_label": self.dimension_label,
            "module_code": self.module_code,
            "module_label": self.module_label,
            "title": self.title,
            "evidence": self.evidence,
            "severity": self.severity,
            "occurred_at": self.occurred_at,
            "record_id": self.record_id,
            "suggested_action": asdict(self.suggested_action),
            "question_id": self.question_id,
            "user_answer": self.user_answer,
            "correct_answer": self.correct_answer,
            "knowledge_tag": self.knowledge_tag,
            "stage_no": self.stage_no,
        }


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _parse_json(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _truncate(text: str, limit: int = 240) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def _severity_from_risk(risk: str) -> str:
    text = str(risk or "").strip().lower()
    if text in {"high", "高", "h", "critical", "严重"}:
        return SEVERITY_HIGH
    if text in {"medium", "med", "中", "m", "warning"}:
        return SEVERITY_MEDIUM
    if text in {"low", "低", "l"}:
        return SEVERITY_LOW
    return ""


def _severity_from_score(score: Any, *, pass_score: float = 80.0) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ""
    if value < 60:
        return SEVERITY_HIGH
    if value < pass_score:
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


def _build_review_item(
    *,
    source: str,
    title: str,
    evidence: str,
    record_id: int,
    occurred_at: str,
    knowledge_tag: str = "",
    module_code: str = "",
    severity: str = SEVERITY_MEDIUM,
    dimension_hints: Iterable[str] = (),
    question_id: str = "",
    user_answer: Any = None,
    correct_answer: Any = None,
    stage_no: int | None = None,
    action: SuggestedAction | None = None,
) -> ReviewItem:
    dimension = normalize_to_dimension(
        *list(dimension_hints), knowledge_tag, module_label(module_code), module_code=module_code
    )
    normalized_module = normalize_module(module_code)
    return ReviewItem(
        source=source,
        dimension=dimension,
        dimension_label=dimension_label(dimension),
        module_code=normalized_module,
        module_label=module_label(normalized_module),
        title=title or "未命名",
        evidence=_truncate(evidence),
        severity=severity or SEVERITY_MEDIUM,
        occurred_at=occurred_at,
        record_id=record_id,
        suggested_action=action or _default_action(source, normalized_module),
        question_id=question_id,
        user_answer=user_answer,
        correct_answer=correct_answer,
        knowledge_tag=knowledge_tag,
        stage_no=stage_no,
    )


def _default_action(source: str, module_code: str) -> SuggestedAction:
    if source == "practice":
        return SuggestedAction(type="practice", route="practical_training", module_code=module_code, label="去陪练")
    if source == "assistant":
        return SuggestedAction(type="knowledge_qa", route="knowledge_qa", module_code=module_code, label="去查知识库")
    if source == "qa":
        return SuggestedAction(type="knowledge_qa", route="knowledge_qa", module_code=module_code, label="重新提问")
    return SuggestedAction(type="assessment", route="assessment", module_code=module_code, label="去查题")


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


def _collect_assessment_wrongs(db: Session, *, user_id: str, record_limit: int) -> list[ReviewItem]:
    records = (
        db.query(AssessmentRecord)
        .filter(AssessmentRecord.user_id == user_id)
        .order_by(AssessmentRecord.finished_at.desc(), AssessmentRecord.id.desc())
        .limit(record_limit)
        .all()
    )
    if not records:
        return []

    task_ids = {int(r.task_id) for r in records if r.task_id}
    task_map: dict[int, AssessmentTask] = {}
    if task_ids:
        for task in db.query(AssessmentTask).filter(AssessmentTask.id.in_(task_ids)).all():
            task_map[int(task.id)] = task

    seen: set[tuple[int, str]] = set()
    items: list[ReviewItem] = []
    for record in records:
        task = task_map.get(int(record.task_id or 0))
        if task is None:
            continue
        submit_status = str(record.submit_status or "").strip().lower()
        if submit_status not in {"submitted", "graded", "auto_submitted"}:
            continue
        paper = _parse_json(task.paper_config_json, {})
        questions = paper.get("questions") if isinstance(paper.get("questions"), list) else []
        if not questions:
            continue
        answers = _parse_json(record.paper_answer_json, {}) or {}
        if not answers:
            continue
        finished_iso = _to_iso(record.finished_at)
        task_module = str(task.module_code or "").strip()
        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("id") or "").strip()
            if not qid:
                continue
            qtype = str(question.get("type") or "single").lower()
            if qid not in answers:
                continue
            user_answer = answers.get(qid)
            if _is_blank_answer(user_answer):
                continue
            correct_answer = question.get("answer")
            if qtype in {"single", "judge", "multiple"}:
                if not _is_wrong_objective(qtype, user_answer, correct_answer):
                    continue
            else:
                if _essay_keyword_hit(question, user_answer):
                    continue
                correct_answer = question.get("keywords") or correct_answer
            key = (int(task.id), qid)
            if key in seen:
                continue
            seen.add(key)
            knowledge_tag = str(question.get("knowledge_tag") or question.get("tag") or "").strip()
            severity = _severity_from_score(record.score, pass_score=float(task.pass_score or 80))
            items.append(
                _build_review_item(
                    source="assessment",
                    title=str(question.get("title") or qid),
                    evidence=str(task.task_name or ""),
                    record_id=int(record.id),
                    occurred_at=finished_iso,
                    knowledge_tag=knowledge_tag,
                    module_code=task_module,
                    severity=severity,
                    question_id=qid,
                    user_answer=user_answer,
                    correct_answer=correct_answer,
                )
            )
    return items


def _collect_practice_weaknesses(db: Session, *, user_id: str, record_limit: int) -> list[ReviewItem]:
    records = (
        db.query(PracticeEvalRecord)
        .filter(PracticeEvalRecord.user_id == user_id)
        .order_by(PracticeEvalRecord.created_at.desc(), PracticeEvalRecord.id.desc())
        .limit(record_limit)
        .all()
    )
    items: list[ReviewItem] = []
    for record in records:
        points = _parse_json(record.problem_points_json, []) or []
        if not isinstance(points, list):
            continue
        concrete_points = [str(p).strip() for p in points if str(p or "").strip()]
        if not concrete_points:
            continue

        weak = str(record.weak_dimension or "").strip()
        risk_severity = _severity_from_risk(record.risk_level)
        score_severity = _severity_from_score(record.overall_score)
        severity = risk_severity or score_severity or SEVERITY_MEDIUM
        occurred_iso = _to_iso(record.created_at)
        module_code = str(record.module_code or "").strip()
        coach = str(record.coach_summary or "").strip()
        advice = str(record.improvement_advice or "").strip()
        evidence_base = coach or advice

        for point_text in concrete_points:
            items.append(
                _build_review_item(
                    source="practice",
                    title=point_text if len(point_text) <= 40 else f"{point_text[:40]}…",
                    evidence=evidence_base or weak or point_text,
                    record_id=int(record.id),
                    occurred_at=occurred_iso,
                    module_code=module_code,
                    severity=severity,
                    dimension_hints=(weak, point_text),
                    stage_no=record.stage_no,
                )
            )
    return items


def _collect_assistant_risks(db: Session, *, user_id: str, record_limit: int) -> list[ReviewItem]:
    records = (
        db.query(AssistantRecord)
        .filter(AssistantRecord.user_id == user_id)
        .filter(AssistantRecord.scene_hint != QA_SCENE_HINT)
        .order_by(AssistantRecord.created_at.desc(), AssistantRecord.id.desc())
        .limit(record_limit * 4)
        .all()
    )
    items: list[ReviewItem] = []
    count = 0
    for record in records:
        severity = _severity_from_risk(record.risk_level)
        if severity != SEVERITY_HIGH:
            continue
        question = str(record.customer_question or "").strip()
        if not question:
            continue
        knowledge_tag = str(record.knowledge_tag or "").strip()
        weak = str(record.weak_dimension or "").strip()
        advice = str(record.training_advice or "").strip()
        items.append(
            _build_review_item(
                source="assistant",
                title=question if len(question) <= 60 else f"{question[:60]}…",
                evidence=advice or knowledge_tag or question,
                record_id=int(record.id),
                occurred_at=_to_iso(record.created_at),
                knowledge_tag=knowledge_tag,
                severity=SEVERITY_HIGH,
                dimension_hints=(weak, knowledge_tag),
            )
        )
        count += 1
        if count >= record_limit:
            break
    return items


def _collect_qa_gaps(db: Session, *, user_id: str, record_limit: int) -> list[ReviewItem]:
    records = (
        db.query(AssistantRecord)
        .filter(AssistantRecord.user_id == user_id)
        .filter(AssistantRecord.scene_hint == QA_SCENE_HINT)
        .order_by(AssistantRecord.created_at.desc(), AssistantRecord.id.desc())
        .limit(record_limit)
        .all()
    )
    items: list[ReviewItem] = []
    for record in records:
        question = str(record.customer_question or "").strip()
        if not question:
            continue
        weak = str(record.weak_dimension or "").strip()
        knowledge_tag = str(record.knowledge_tag or "").strip()
        reply = str(record.assistant_reply or "").strip()
        items.append(
            _build_review_item(
                source="qa",
                title=question if len(question) <= 60 else f"{question[:60]}…",
                evidence=reply or weak or "知识问答未给出完整回答",
                record_id=int(record.id),
                occurred_at=_to_iso(record.created_at),
                knowledge_tag=knowledge_tag,
                severity=SEVERITY_HIGH,
                dimension_hints=(knowledge_tag, weak),
            )
        )
    return items


def _filter_items(items: list[ReviewItem], *, source: str = "", dimension: str = "") -> list[ReviewItem]:
    if not source and not dimension:
        return items
    out: list[ReviewItem] = []
    for item in items:
        if source and item.source != source:
            continue
        if dimension and item.dimension != dimension:
            continue
        out.append(item)
    return out


def _sort_items(items: list[ReviewItem]) -> list[ReviewItem]:
    severity_order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2, "": 3}
    return sorted(items, key=lambda it: (severity_order.get(it.severity, 9), -1 * _epoch(it.occurred_at)))


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _summarize(items: list[ReviewItem]) -> dict[str, Any]:
    by_dim: Counter[str] = Counter()
    by_dim_high: Counter[str] = Counter()
    by_module: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    for item in items:
        by_dim[item.dimension] += 1
        if item.severity == SEVERITY_HIGH:
            by_dim_high[item.dimension] += 1
        if item.module_code:
            by_module[item.module_code] += 1
        by_source[item.source] += 1

    by_dimension = []
    for key in DIMENSION_ORDER + (OTHER_DIMENSION,):
        if by_dim[key] == 0:
            continue
        by_dimension.append({"dimension": key, "label": dimension_label(key), "count": by_dim[key], "severity_high": by_dim_high[key]})

    by_modules = [{"module_code": code, "label": module_label(code), "count": cnt} for code, cnt in by_module.most_common()]
    source_summary = {src: by_source.get(src, 0) for src in SOURCES}

    recurring_top: list[dict[str, Any]] = []
    for dim, count in by_dim.most_common():
        if dim == OTHER_DIMENSION or count < RECURRING_THRESHOLD:
            continue
        recurring_top.append({"dimension": dim, "label": dimension_label(dim), "count": count, "severity_high": by_dim_high[dim]})
        if len(recurring_top) >= RECURRING_TOP_N:
            break

    return {
        "total": len(items),
        "by_dimension": by_dimension,
        "by_module": by_modules,
        "by_source": source_summary,
        "recurring_top": recurring_top,
    }


def aggregate_review_notebook(
    db: Session,
    *,
    user_id: str,
    source: str = "",
    dimension: str = "",
    record_limit: int = DEFAULT_RECORD_LIMIT,
    return_limit: int = DEFAULT_RETURN_LIMIT,
) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"user_id": "", "items": [], "summary": _summarize([])}

    all_items: list[ReviewItem] = []
    all_items.extend(_collect_assessment_wrongs(db, user_id=user_id, record_limit=record_limit))
    all_items.extend(_collect_practice_weaknesses(db, user_id=user_id, record_limit=record_limit))
    all_items.extend(_collect_assistant_risks(db, user_id=user_id, record_limit=record_limit))
    all_items.extend(_collect_qa_gaps(db, user_id=user_id, record_limit=record_limit))

    manual_keys = load_manual_mastery_keys(db, user_id)
    mastered_items: list[ReviewItem] = []
    pending_items: list[ReviewItem] = []
    for item in all_items:
        if (item.source, item.record_id, item.question_id) in manual_keys:
            mastered_items.append(item)
            continue
        task_id = 0
        if item.source == "assessment":
            record = db.query(AssessmentRecord).filter(AssessmentRecord.id == item.record_id).first()
            task_id = int(record.task_id) if record else 0
        if check_auto_mastery(
            db,
            user_id,
            source=item.source,
            record_id=item.record_id,
            question_id=item.question_id,
            task_id=task_id,
            dimension=item.dimension,
            knowledge_tag=item.knowledge_tag,
            scene_hint=QA_SCENE_HINT if item.source == "qa" else "",
        ):
            mastered_items.append(item)
            continue
        pending_items.append(item)

    summary = _summarize(pending_items)
    summary["mastered_count"] = len(mastered_items)

    filtered = _filter_items(pending_items, source=source, dimension=dimension)
    filtered = _sort_items(filtered)[: max(int(return_limit), 0)]

    return {"user_id": user_id, "items": [item.to_dict() for item in filtered], "summary": summary}


def summary_only(db: Session, *, user_id: str, record_limit: int = DEFAULT_RECORD_LIMIT) -> dict[str, Any]:
    payload = aggregate_review_notebook(db, user_id=user_id, record_limit=record_limit, return_limit=0)
    return {"user_id": payload["user_id"], "summary": payload["summary"]}
