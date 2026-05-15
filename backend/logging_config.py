"""集中式日志配置模块 —— 请求链路追踪、文件轮转、结构化JSON输出。

用法（main.py 中一次性调用）::

    from logging_config import initialize_logging
    initialize_logging()

然后在中间件中::

    from logging_config import set_request_id, clear_request_id, get_request_id

    async def middleware(request, call_next):
        rid = set_request_id()
        try:
            response = await call_next(request)
        finally:
            clear_request_id()
        return response
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Request-ID 上下文
# ---------------------------------------------------------------------------
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jewelry_qipei_request_id", default="-"
)


def get_request_id() -> str:
    """返回当前请求的 request_id（无请求上下文时为 "-"）。"""
    return _request_id_ctx.get("-")


def set_request_id(rid: str | None = None) -> str:
    """生成（或使用给定值）request_id 并存入 ContextVar。"""
    rid = rid or uuid.uuid4().hex[:12]
    _request_id_ctx.set(rid)
    return rid


def clear_request_id() -> None:
    """请求结束时清除 request_id，防止泄漏。"""
    _request_id_ctx.set("-")


# ---------------------------------------------------------------------------
# LogRecord 过滤器 —— 自动注入 request_id
# ---------------------------------------------------------------------------
class RequestIdFilter(logging.Filter):
    """将当前 contextvars 中的 request_id 注入到 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get("-")  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# 格式化器
# ---------------------------------------------------------------------------
_HUMAN_FMT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ANSI 颜色码（Windows Terminal / PowerShell 7 均支持）
_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[2m",       # dim/grey
    logging.INFO: "",               # 默认
    logging.WARNING: "\033[33m",    # yellow
    logging.ERROR: "\033[31m",      # red
    logging.CRITICAL: "\033[1;31m", # bold red
}
_ANSI_RESET = "\033[0m"


class HumanFormatter(logging.Formatter):
    """带 ANSI 颜色的人类可读格式化器，非 TTY 自动禁用颜色。"""

    def __init__(self, fmt: str | None = None, datefmt: str | None = None,
                 use_color: bool | None = None):
        super().__init__(fmt or _HUMAN_FMT, datefmt or _DATE_FMT)
        if use_color is None:
            try:
                self._use_color = sys.stdout.isatty()
            except AttributeError:
                self._use_color = False
        else:
            self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if not self._use_color:
            return msg
        color = _LEVEL_COLORS.get(record.levelno, "")
        if color:
            return f"{color}{msg}{_ANSI_RESET}"
        return msg


class JsonFormatter(logging.Formatter):
    """结构化 JSON 格式化器，用于机器解析。"""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, _DATE_FMT),
            "level": record.levelname,
            "request_id": getattr(record, "request_id", "-"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------
_INITIALIZED = False


def initialize_logging() -> None:
    """一次性配置 `jewelry_qipei` logger 层级。幂等：重复调用无副作用。"""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    # 延迟导入避免循环依赖（config.py 会在 import 链上被其他模块加载）
    import config as app_config

    level = getattr(logging, app_config.LOG_LEVEL, logging.INFO)

    root = logging.getLogger("jewelry_qipei")
    root.setLevel(logging.DEBUG)          # handler 各自控制实际阈值
    root.propagate = False                # 防止 uvicorn root handler 双重输出
    root.addFilter(RequestIdFilter())

    # --- 控制台 handler（始终启用） ---
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(HumanFormatter())
    console.addFilter(RequestIdFilter())
    root.addHandler(console)

    # --- 文件 handler（可配） ---
    file_handler: logging.handlers.RotatingFileHandler | None = None
    if app_config.LOG_FILE_ENABLED:
        fpath = Path(app_config.LOG_FILE_PATH)
        if not fpath.is_absolute():
            fpath = Path(__file__).resolve().parent / fpath
        fpath.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            str(fpath),
            maxBytes=app_config.LOG_FILE_MAX_BYTES,
            backupCount=app_config.LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(HumanFormatter(use_color=False))
        file_handler.addFilter(RequestIdFilter())
        root.addHandler(file_handler)

    # --- JSON 文件 handler（可选） ---
    if app_config.LOG_JSON_ENABLED:
        json_path: Path
        if file_handler is not None:
            json_path = Path(file_handler.baseFilename).with_suffix(".json.log")
        else:
            jp = app_config.LOG_FILE_PATH
            json_path = Path(jp).with_suffix(".json.log") if jp else Path("logs/app.json.log")
        if not json_path.is_absolute():
            json_path = Path(__file__).resolve().parent / json_path
        json_path.parent.mkdir(parents=True, exist_ok=True)

        json_handler = logging.handlers.RotatingFileHandler(
            str(json_path),
            maxBytes=app_config.LOG_FILE_MAX_BYTES,
            backupCount=app_config.LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        json_handler.setLevel(level)
        json_handler.setFormatter(JsonFormatter())
        json_handler.addFilter(RequestIdFilter())
        root.addHandler(json_handler)

    # --- uvicorn 日志级别对齐 ---
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).setLevel(level)

    root.info(
        "logging initialized level=%s file=%s json=%s",
        app_config.LOG_LEVEL,
        app_config.LOG_FILE_ENABLED,
        app_config.LOG_JSON_ENABLED,
    )
