from __future__ import annotations

import json
import logging
from typing import Any

import config as app_config
from dify_client import run_workflow_blocking
from dify_utils import (
    _extract_data_and_outputs,
    _parse_json_like,
    _pick_value,
    _to_float,
    _to_int,
    _to_text,
)

_log = logging.getLogger("jewelry_qipei.dify_assessment")
_PAPER_STEERING_MARKER = "出卷要求：这是一次全新的标准试卷发布"


def _resolve_workflow_api_key(workflow_name: str) -> str:
    if workflow_name == "wf13":
        return _to_text(app_config.DIFY_WF13_API_KEY) or _to_text(app_config.DIFY_API_KEY)
    if workflow_name == "wf14":
        return _to_text(app_config.DIFY_WF14_API_KEY) or _to_text(app_config.DIFY_API_KEY)
    return ""


def _resolve_workflow_api_base(workflow_name: str) -> str:
    if workflow_name == "wf13":
        base = _to_text(app_config.DIFY_WF13_API_BASE)
    elif workflow_name == "wf14":
        base = _to_text(app_config.DIFY_WF14_API_BASE)
    else:
        base = ""
    return (base or _to_text(app_config.DIFY_API_BASE)).rstrip("/")


def _resolve_workflow_timeout(workflow_name: str) -> float:
    if workflow_name == "wf13":
        return float(app_config.DIFY_WF13_TIMEOUT)
    if workflow_name == "wf14":
        return float(app_config.DIFY_WF14_TIMEOUT)
    return float(app_config.DIFY_WORKFLOW_TIMEOUT)


def _run_assessment_workflow(
    *,
    workflow_name: str,
    user_id: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    api_key = _resolve_workflow_api_key(workflow_name)
    if not api_key:
        _log.error("assessment workflow missing api_key workflow=%s", workflow_name)
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _resolve_workflow_api_base(workflow_name)
    if not base:
        _log.error("assessment workflow missing api_base workflow=%s", workflow_name)
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}

    try:
        raw = run_workflow_blocking(
            base_url=base,
            api_key=api_key,
            inputs={str(k): "" if v is None else str(v) for k, v in (inputs or {}).items()},
            user=_to_text(user_id) or "assessment-user",
            workflow_id=None,
            timeout_sec=_resolve_workflow_timeout(workflow_name),
        )
    except Exception as exc:
        _log.exception("assessment workflow call failed workflow=%s", workflow_name)
        return {"ok": False, "reason": "dify_exception", "error": _to_text(exc), "raw": {}}

    if not isinstance(raw, dict):
        return {"ok": False, "reason": "invalid_response", "error": "", "raw": {}}
    if raw.get("code") != 200:
        return {
            "ok": False,
            "reason": "dify_non_200",
            "error": _to_text(raw.get("message")),
            "raw": raw,
        }
    return {"ok": True, "reason": "", "error": "", "raw": raw}


def _compose_paper_generation_desc(task_desc: str, generation_batch: str) -> str:
    base_desc = _to_text(task_desc)
    batch = _to_text(generation_batch)
    if not batch:
        return base_desc
    steering = (
        f"{_PAPER_STEERING_MARKER}。"
        "请重新基于知识库检索并生成一套新的题目集合，避免与最近一次已发布标准试卷重复。"
        "仅输出最终试卷 JSON，不要在试卷内容中回显本条要求。"
        f"批次标识：{batch}"
    )
    if not base_desc:
        return steering
    return f"{base_desc}\n\n{steering}"


def _sanitize_generated_text(value: str, generation_batch: str) -> str:
    text = _to_text(value)
    if not text:
        return text
    batch = _to_text(generation_batch)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        if _PAPER_STEERING_MARKER in line:
            continue
        if batch and batch in line and "批次标识" in line:
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    if batch:
        cleaned = cleaned.replace(batch, "").strip()
    return cleaned


def _sanitize_generated_payload(value: Any, generation_batch: str) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_generated_payload(item, generation_batch) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_generated_payload(item, generation_batch) for item in value]
    if isinstance(value, str):
        return _sanitize_generated_text(value, generation_batch)
    return value


def run_wf13_generate_paper(
    *,
    user_id: str,
    task_name: str,
    task_desc: str,
    module_code: str,
    difficulty: str,
    question_count: int,
    question_mix: dict[str, Any] | None,
    pass_score: float,
    generation_batch: str = "",
) -> dict[str, Any]:
    call = _run_assessment_workflow(
        workflow_name="wf13",
        user_id=user_id,
        inputs={
            "task_name": task_name,
            "task_desc": _compose_paper_generation_desc(task_desc, generation_batch),
            "module_code": module_code,
            "difficulty": difficulty,
            "question_count": question_count,
            "question_mix": json.dumps(question_mix or {}, ensure_ascii=False),
            "pass_score": pass_score,
        },
    )
    if not call.get("ok"):
        return call

    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    data, outputs = _extract_data_and_outputs(raw)
    paper_config = _parse_json_like(_pick_value(data, outputs, "paper_config_json"))
    if not paper_config:
        paper_config = _parse_json_like(_pick_value(data, outputs, "raw_output"))
    if not paper_config:
        return {
            "ok": False,
            "reason": "empty_workflow_output",
            "error": "paper_config_json missing",
            "raw": raw,
        }
    paper_config = _sanitize_generated_payload(paper_config, generation_batch)

    exam_title = _to_text(_pick_value(data, outputs, "exam_title")) or _to_text(paper_config.get("exam_title")) or task_name
    instructions = _to_text(_pick_value(data, outputs, "instructions")) or _to_text(paper_config.get("instructions")) or task_desc
    pass_score_final = _to_float(_pick_value(data, outputs, "pass_score_final", "pass_score"), pass_score)
    generation_status = _to_text(_pick_value(data, outputs, "generation_status", "workflow_status")) or "generated"
    paper_version = max(_to_int(_pick_value(data, outputs, "paper_version"), 1), 1)
    exam_title = _sanitize_generated_text(exam_title, generation_batch) or task_name
    instructions = _sanitize_generated_text(instructions, generation_batch) or task_desc

    paper_config.setdefault("exam_title", exam_title)
    paper_config.setdefault("instructions", instructions)
    paper_config.setdefault("pass_score", pass_score_final)
    if module_code and not _to_text(paper_config.get("module_code")):
        paper_config["module_code"] = module_code

    call["data"] = {
        "paper_version": paper_version,
        "paper_config_json": json.dumps(paper_config, ensure_ascii=False),
        "generation_status": generation_status,
        "exam_title": exam_title,
        "instructions": instructions,
        "pass_score_final": round(pass_score_final, 2),
    }
    return call


def _coerce_is_pass(raw_value: Any, *, score: float, pass_score: float) -> int:
    text = _to_text(raw_value).strip().lower()
    if text in {"1", "true", "yes", "pass", "passed"}:
        return 1
    if text in {"0", "false", "no", "fail", "failed"}:
        return 0
    return 1 if score >= pass_score else 0


def run_wf14_grade_paper(
    *,
    user_id: str,
    record_id: int,
    task_id: int,
    exam_title: str,
    paper_config_json: str,
    answers: dict[str, Any],
    pass_score: float,
    module_code: str,
) -> dict[str, Any]:
    call = _run_assessment_workflow(
        workflow_name="wf14",
        user_id=user_id,
        inputs={
            "record_id": record_id,
            "task_id": task_id,
            "exam_title": exam_title,
            "module_code": module_code,
            "paper_config_json": paper_config_json,
            "answers_json": json.dumps(answers or {}, ensure_ascii=False),
            "pass_score": pass_score,
        },
    )
    if not call.get("ok"):
        return call

    raw = call.get("raw") if isinstance(call.get("raw"), dict) else {}
    data, outputs = _extract_data_and_outputs(raw)
    score = _to_float(_pick_value(data, outputs, "total_score", "score"), 0.0)
    grading_detail = _parse_json_like(_pick_value(data, outputs, "grading_detail_json"))
    weak_points = _parse_json_like(_pick_value(data, outputs, "weak_points_json"))
    recommended_actions = _parse_json_like(_pick_value(data, outputs, "recommended_actions_json"))
    comment = _to_text(_pick_value(data, outputs, "summary_comment", "comment")) or "标准试卷 AI 阅卷已完成"
    grading_status = _to_text(_pick_value(data, outputs, "grading_status", "workflow_status")) or "graded"
    is_pass = _coerce_is_pass(_pick_value(data, outputs, "is_pass"), score=score, pass_score=pass_score)

    paper_result = _parse_json_like(_pick_value(data, outputs, "paper_result_json"))
    if not paper_result:
        paper_result = {
            "grading_status": grading_status,
            "summary_comment": comment,
            "total_score": round(score, 2),
            "pass_score": round(pass_score, 2),
            "is_pass": is_pass,
        }
    if grading_detail:
        paper_result["grading_detail"] = grading_detail
    if weak_points:
        paper_result["weak_points"] = weak_points
    if recommended_actions:
        paper_result["recommended_actions"] = recommended_actions

    call["data"] = {
        "score": round(score, 2),
        "is_pass": is_pass,
        "comment": comment,
        "paper_result_json": json.dumps(paper_result, ensure_ascii=False),
        "grading_detail": grading_detail,
        "review_source": "wf14_auto",
    }
    return call
