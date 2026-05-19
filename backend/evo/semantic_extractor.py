"""Semantic memory extraction for Hermes Route B Phase 2."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import config as app_config
from dify_client import run_workflow_blocking
from dify_utils import _extract_data_and_outputs, _parse_json_like, _pick_value, _to_text
from evo.audit import log_audit
from evo.compliance_tagger import detect_compliance_tags
from models import AgentEvoEpisode, AgentEvoReviewQueue, AgentEvoSemantic

_log = logging.getLogger("jewelry_qipei.evo.semantic_extractor")

DEFAULT_CONFIDENCE = 0.6
NEGATIVE_FEEDBACK_CONFIDENCE = 0.4
MIN_CONTENT_LENGTH = 8

_NEGATION_MARKERS = (
    "不能",
    "不要",
    "避免",
    "不得",
    "不应",
    "不可",
    "不可以",
    "禁止",
    "严禁",
    "别",
    "不能承诺",
    "不要承诺",
)
_POSITIVE_RISK_MARKERS = (
    "可以承诺",
    "能承诺",
    "可承诺",
    "保证",
    "稳赚",
    "一定",
    "绝对",
    "百分百",
    "100%",
)
_LOW_VALUE_CONTENT = frozenset({"好的", "收到", "谢谢", "不准", "有误", "错误", "不对"})


@dataclass(frozen=True)
class SemanticExtraction:
    trigger_text: str
    content: str
    scope_suggestion: str = "user"
    confidence: float = DEFAULT_CONFIDENCE


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _normalize_scope(value: str) -> str:
    scope = (value or "").strip().lower()
    if scope in {"store", "门店"}:
        return "store"
    if scope in {"global", "company", "全局", "公司"}:
        return "global"
    return "user"


def _scope_id_for(scope_type: str, episode: AgentEvoEpisode) -> str:
    if scope_type == "store" and episode.store_id:
        return episode.store_id
    if scope_type == "global":
        return ""
    return episode.user_id


def _json_list(values: list[int]) -> str:
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        ivalue = int(value)
        if ivalue not in seen:
            out.append(ivalue)
            seen.add(ivalue)
    return json.dumps(out, ensure_ascii=False)


def _has_negated_sensitive_phrase(content: str, tags: list[str]) -> bool:
    text = _clean_text(content)
    if not text or not tags:
        return False
    for tag in tags:
        idx = text.find(tag)
        if idx < 0:
            continue
        window = text[max(0, idx - 12) : idx + len(tag) + 12]
        if any(marker in window for marker in _NEGATION_MARKERS):
            return True
    return False


def _needs_human_review(content: str, trigger_text: str, scope_type: str) -> tuple[bool, list[str]]:
    tags = detect_compliance_tags(content, trigger_text)
    if scope_type == "global":
        return True, tags
    if not tags:
        return False, tags
    if _has_negated_sensitive_phrase(content, tags):
        return False, tags
    if any(marker in content for marker in _POSITIVE_RISK_MARKERS):
        return True, tags
    return True, tags


def _fallback_from_correction(
    *,
    parent: AgentEvoEpisode,
    correction: AgentEvoEpisode,
) -> SemanticExtraction | None:
    content = _clean_text(correction.correction_text)
    if len(content) < MIN_CONTENT_LENGTH or content in _LOW_VALUE_CONTENT:
        return None
    trigger = _clean_text(parent.query_text or correction.query_text or content, limit=500)
    scope = "store" if any(token in content for token in ("本店", "门店", "店里", "我们店")) else "user"
    return SemanticExtraction(
        trigger_text=trigger,
        content=content,
        scope_suggestion=scope,
        confidence=DEFAULT_CONFIDENCE,
    )


def _fallback_from_negative_feedback(episode: AgentEvoEpisode) -> SemanticExtraction | None:
    trigger = _clean_text(episode.query_text, limit=500)
    response = _clean_text(episode.response_text, limit=500)
    if not trigger and not response:
        return None
    content = (
        "用户反馈上一回答不准确；后续遇到同类问题时需重新核对依据，"
        f"避免直接复用该回答。原问题：{trigger or response}"
    )
    return SemanticExtraction(
        trigger_text=trigger or response,
        content=content,
        scope_suggestion="user",
        confidence=NEGATIVE_FEEDBACK_CONFIDENCE,
    )


def _extract_json_candidate(data: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    direct = _parse_json_like(_pick_value(data, outputs, "semantic_json", "memory_json", "result_json"))
    if direct:
        return direct
    for key in ("result", "content", "answer", "text"):
        parsed = _parse_json_like(_pick_value(data, outputs, key))
        if parsed:
            return parsed
    return {}


def _extract_with_dify(
    *,
    parent: AgentEvoEpisode,
    correction: AgentEvoEpisode | None,
) -> SemanticExtraction | None:
    api_key = _to_text(getattr(app_config, "DIFY_EVO_SEMANTIC_API_KEY", ""))
    if not api_key:
        return None
    base = _to_text(getattr(app_config, "DIFY_EVO_SEMANTIC_API_BASE", "")) or _to_text(
        getattr(app_config, "DIFY_API_BASE", "")
    )
    if not base:
        return None
    try:
        timeout = float(getattr(app_config, "DIFY_EVO_SEMANTIC_TIMEOUT", 20.0) or 20.0)
    except (TypeError, ValueError):
        timeout = 20.0

    wrong = _clean_text(parent.response_text)
    correct = _clean_text(correction.correction_text if correction else "")
    raw = run_workflow_blocking(
        base_url=base.rstrip("/"),
        api_key=api_key,
        inputs={
            "query_text": _clean_text(parent.query_text),
            "wrong": wrong,
            "correct": correct,
            "feedback_signal": "correction" if correction else "thumb_down",
            "user_id": _clean_text(parent.user_id),
            "store_id": _clean_text(parent.store_id),
        },
        user=_clean_text(parent.user_id) or "evo-semantic",
        workflow_id=_to_text(getattr(app_config, "DIFY_EVO_SEMANTIC_WORKFLOW_ID", "")) or None,
        timeout_sec=timeout,
    )
    if not isinstance(raw, dict) or raw.get("code") != 200:
        return None

    data, outputs = _extract_data_and_outputs(raw)
    obj = _extract_json_candidate(data, outputs)
    content = _clean_text(
        _pick_value(data, outputs, "content", "memory_content", "semantic_content")
        or obj.get("content")
    )
    trigger = _clean_text(
        _pick_value(data, outputs, "trigger", "trigger_text")
        or obj.get("trigger")
        or parent.query_text,
        limit=500,
    )
    scope = _normalize_scope(
        _to_text(
            _pick_value(data, outputs, "scope_suggestion", "scope_type")
            or obj.get("scope_suggestion")
            or obj.get("scope_type")
        )
    )
    try:
        confidence = float(
            _pick_value(data, outputs, "confidence")
            or obj.get("confidence")
            or DEFAULT_CONFIDENCE
        )
    except (TypeError, ValueError):
        confidence = DEFAULT_CONFIDENCE
    if len(content) < MIN_CONTENT_LENGTH or content in _LOW_VALUE_CONTENT:
        return None
    return SemanticExtraction(
        trigger_text=trigger or _clean_text(parent.query_text, limit=500),
        content=content,
        scope_suggestion=scope,
        confidence=max(0.1, min(1.0, confidence)),
    )


def _write_semantic_memory(
    session,
    *,
    parent: AgentEvoEpisode,
    extraction: SemanticExtraction,
    source_episode_ids: list[int],
    actor: str,
    force_review: bool = False,
    review_reason: str = "",
) -> AgentEvoSemantic:
    scope_type = _normalize_scope(extraction.scope_suggestion)
    scope_id = _scope_id_for(scope_type, parent)
    needs_review, tags = _needs_human_review(
        extraction.content,
        extraction.trigger_text,
        scope_type,
    )
    needs_review = bool(force_review or needs_review)
    status = "pending" if needs_review else "active"
    write_mode = "human" if scope_type == "global" else "auto"

    memory = AgentEvoSemantic(
        scope_type=scope_type,
        scope_id=scope_id,
        content=extraction.content,
        trigger_text=extraction.trigger_text,
        source_episode_ids=_json_list(source_episode_ids),
        confidence=max(0.1, min(1.0, extraction.confidence)),
        status=status,
        write_mode=write_mode,
    )
    session.add(memory)
    session.flush()

    log_audit(
        session,
        actor=actor or "system",
        action="semantic_write",
        target_type="semantic",
        target_id=memory.id,
        payload={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "status": status,
            "write_mode": write_mode,
            "source_episode_ids": source_episode_ids,
            "compliance_tags": tags,
        },
    )

    if needs_review:
        reason = "global 记忆需人审" if scope_type == "global" else "命中合规敏感词：" + "、".join(tags)
        reason = (review_reason or "").strip() or reason
        queue = AgentEvoReviewQueue(
            target_type="semantic",
            target_id=int(memory.id),
            reason=reason,
            priority=90 if tags else 70,
            status="pending",
        )
        session.add(queue)
        log_audit(
            session,
            actor=actor or "system",
            action="semantic_review_enqueue",
            target_type="semantic",
            target_id=memory.id,
            payload={"reason": reason, "compliance_tags": tags},
        )

    _log.info(
        "semantic memory written id=%s scope=%s:%s status=%s",
        memory.id,
        scope_type,
        scope_id,
        status,
    )
    return memory


def extract_semantic_from_correction(
    session,
    *,
    correction_episode_id: int,
    actor_user_id: str = "",
) -> AgentEvoSemantic | None:
    correction = session.get(AgentEvoEpisode, int(correction_episode_id))
    if correction is None or correction.episode_type != "correction":
        return None
    parent = session.get(AgentEvoEpisode, int(correction.parent_episode_id or 0))
    if parent is None:
        return None

    extraction = _extract_with_dify(parent=parent, correction=correction)
    if extraction is None:
        extraction = _fallback_from_correction(parent=parent, correction=correction)
    if extraction is None:
        return None

    return _write_semantic_memory(
        session,
        parent=parent,
        extraction=extraction,
        source_episode_ids=[int(parent.id), int(correction.id)],
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        force_review=True,
        review_reason="用户纠正生成候选记忆，需管理层审核后上线",
    )


def extract_semantic_from_negative_feedback(
    session,
    *,
    episode_id: int,
    actor_user_id: str = "",
) -> AgentEvoSemantic | None:
    episode = session.get(AgentEvoEpisode, int(episode_id))
    if episode is None:
        return None
    extraction = _extract_with_dify(parent=episode, correction=None)
    if extraction is None:
        extraction = _fallback_from_negative_feedback(episode)
    if extraction is None:
        return None
    return _write_semantic_memory(
        session,
        parent=episode,
        extraction=extraction,
        source_episode_ids=[int(episode.id)],
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        force_review=True,
        review_reason="用户标记没用/不准，进入模块反馈池待复盘",
    )
