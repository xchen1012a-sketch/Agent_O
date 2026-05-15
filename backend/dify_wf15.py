"""WF15 自动晋级解锁 — 调用 Dify 工作流生成权限解锁包与提示文案。"""
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

_log = logging.getLogger("jewelry_qipei.dify_wf15")


def _resolve_api_key() -> str:
    return _to_text(app_config.DIFY_WF15_API_KEY) or _to_text(app_config.DIFY_API_KEY)


def _resolve_api_base() -> str:
    base = _to_text(app_config.DIFY_WF15_API_BASE)
    return (base or _to_text(app_config.DIFY_API_BASE)).rstrip("/")


def _resolve_timeout() -> float:
    return float(app_config.DIFY_WF15_TIMEOUT)


def run_wf15_unlock(
    *,
    user_id: str,
    cycle_id: str,
    cycle_type: str = "onboarding",
    stage_no: int,
    stage_name: str = "",
    review_score: float = 0.0,
    is_pass: int = 0,
    stage_status: str = "active",
    has_growth_plan: int = 1,
    passed_stage1: int = 0,
    passed_stage2: int = 0,
    next_cycle_id: str = "",
    retry_task_count: int = 0,
    trigger_source: str = "review_stage",
    unlock_matrix_version: str = "v1",
    current_unlocks_json: str = "{}",
) -> dict[str, Any]:
    """
    调用 WF15 自动晋级解锁工作流。
    返回 { ok: bool, reason: str, error: str, raw: dict, data: dict }
    """
    api_key = _resolve_api_key()
    if not api_key:
        _log.error("WF15 workflow missing api_key")
        return {"ok": False, "reason": "missing_api_key", "error": "", "raw": {}}

    base = _resolve_api_base()
    if not base:
        _log.error("WF15 workflow missing api_base")
        return {"ok": False, "reason": "missing_api_base", "error": "", "raw": {}}

    inputs = {
        "user_id": user_id,
        "cycle_id": cycle_id,
        "cycle_type": cycle_type,
        "stage_no": str(stage_no),
        "stage_name": stage_name,
        "review_score": str(review_score),
        "is_pass": str(is_pass),
        "stage_status": stage_status,
        "has_growth_plan": str(has_growth_plan),
        "passed_stage1": str(passed_stage1),
        "passed_stage2": str(passed_stage2),
        "next_cycle_id": next_cycle_id,
        "retry_task_count": str(retry_task_count),
        "trigger_source": trigger_source,
        "unlock_matrix_version": unlock_matrix_version,
        "current_unlocks_json": current_unlocks_json,
    }

    _log.info(
        "WF15 unlock prepare dify call user=%s cycle=%s stage=%s is_pass=%s trigger=%s",
        user_id, cycle_id, stage_no, is_pass, trigger_source,
    )

    try:
        raw = run_workflow_blocking(
            base_url=base,
            api_key=api_key,
            inputs=inputs,
            user=_to_text(user_id) or "wf15-user",
            workflow_id=None,
            timeout_sec=_resolve_timeout(),
        )
    except Exception as exc:
        _log.exception("WF15 workflow call failed")
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

    _log.info("WF15 unlock dify success user=%s cycle=%s", user_id, cycle_id)

    data, outputs = _extract_data_and_outputs(raw)

    workflow_status = _to_text(_pick_value(data, outputs, "workflow_status")) or "success"
    unlock_scope = _to_text(_pick_value(data, outputs, "unlock_scope")) or ""
    module_unlocks_json = _to_text(_pick_value(data, outputs, "module_unlocks_json")) or "{}"
    next_route = _to_text(_pick_value(data, outputs, "next_route")) or "/frontend/#/growth-plan"
    next_action = _to_text(_pick_value(data, outputs, "next_action")) or ""
    user_message = _to_text(_pick_value(data, outputs, "user_message")) or ""
    manager_message = _to_text(_pick_value(data, outputs, "manager_message")) or ""
    panel_summary = _to_text(_pick_value(data, outputs, "panel_summary")) or ""
    recommended_actions_json = _to_text(_pick_value(data, outputs, "recommended_actions_json")) or "[]"
    unlock_diff_json = _to_text(_pick_value(data, outputs, "unlock_diff_json")) or "{}"
    should_notify_user = _to_int(_pick_value(data, outputs, "should_notify_user"), 0)
    should_notify_manager = _to_int(_pick_value(data, outputs, "should_notify_manager"), 0)
    invalid_reason = _to_text(_pick_value(data, outputs, "invalid_reason")) or ""
    safe_output_json = _to_text(_pick_value(data, outputs, "safe_output_json")) or "{}"

    module_unlocks = _parse_json_like(module_unlocks_json)
    recommended_actions = _parse_json_like(recommended_actions_json)
    unlock_diff = _parse_json_like(unlock_diff_json)

    return {
        "ok": True,
        "reason": "",
        "error": "",
        "raw": raw,
        "data": {
            "workflow_status": workflow_status,
            "unlock_scope": unlock_scope,
            "module_unlocks": module_unlocks,
            "module_unlocks_json": json.dumps(module_unlocks, ensure_ascii=False),
            "next_route": next_route,
            "next_action": next_action,
            "user_message": user_message,
            "manager_message": manager_message,
            "panel_summary": panel_summary,
            "recommended_actions": recommended_actions,
            "recommended_actions_json": json.dumps(recommended_actions, ensure_ascii=False),
            "unlock_diff": unlock_diff,
            "unlock_diff_json": json.dumps(unlock_diff, ensure_ascii=False),
            "should_notify_user": should_notify_user,
            "should_notify_manager": should_notify_manager,
            "invalid_reason": invalid_reason,
            "safe_output_json": safe_output_json,
        },
    }
