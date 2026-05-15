"""系统日志查看 API（仅管理员）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from auth import get_current_user, require_roles

router = APIRouter(prefix="/api/logs", tags=["logs"])

_log = logging.getLogger("jewelry_qipei.router.logs")

_admin_only = require_roles(["admin"])

_LOG_ROOT = Path(__file__).resolve().parent.parent


@router.get("", dependencies=[Depends(_admin_only)])
def get_logs(
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    lines: int = Query(200, ge=1, le=1000, description="返回最后 N 行"),
) -> dict:
    """读取 app.log 最后 N 行。"""
    import config as app_config

    log_path = Path(app_config.LOG_FILE_PATH)
    if not log_path.is_absolute():
        log_path = _LOG_ROOT / log_path

    if not log_path.exists():
        return {
            "code": 200,
            "message": "success",
            "data": {"lines": [], "total_lines": 0, "file_path": str(log_path)},
        }

    try:
        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)
        tail = all_lines[-lines:] if total > lines else all_lines
        return {
            "code": 200,
            "message": "success",
            "data": {
                "lines": tail,
                "total_lines": total,
                "file_path": str(log_path),
            },
        }
    except Exception as exc:
        _log.exception("failed to read log file %s", log_path)
        return {
            "code": 500,
            "message": f"读取日志失败: {exc}",
            "data": {"lines": [], "total_lines": 0, "file_path": str(log_path)},
        }
