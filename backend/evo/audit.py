"""Evo 自动写入审计日志——所有 record/feedback/correction 都过这里落 1 行。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from models import AgentEvoAuditLog

_log = logging.getLogger("jewelry_qipei.evo.audit")


def log_audit(
    session: Session,
    *,
    actor: str = "system",
    action: str,
    target_type: str,
    target_id: str | int,
    payload: dict[str, Any] | None = None,
) -> AgentEvoAuditLog:
    """写一条审计；仅 add 不 commit，由调用方控制事务。"""
    try:
        encoded = json.dumps(payload or {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        _log.warning("evo audit payload not json-serializable, falling back to str")
        encoded = json.dumps({"raw": str(payload)}, ensure_ascii=False)

    row = AgentEvoAuditLog(
        actor=str(actor or "system"),
        action=str(action or "")[:64],
        target_type=str(target_type or "")[:32],
        target_id=str(target_id or "")[:64],
        payload=encoded,
    )
    session.add(row)
    return row
