from __future__ import annotations

import os
from pathlib import Path

LOADED_ENV_FILES: tuple[str, ...] = ()


def _load_dotenv_file() -> None:
    """加载可见范围内的 .env，兼容从 workspace 根目录或 backend 目录启动。"""
    global LOADED_ENV_FILES
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    backend_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / '.env',
        backend_dir / '.env',
        backend_dir.parent / '.env',
    ]
    loaded: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        try:
            rp = str(p.resolve())
        except OSError:
            rp = str(p)
        if rp in seen or not p.is_file():
            continue
        seen.add(rp)
        load_dotenv(p, override=False)
        loaded.append(rp)
    LOADED_ENV_FILES = tuple(loaded)


_load_dotenv_file()


def _strip(s: str | None) -> str:
    return (s or '').strip()


def _mask_secret(value: str) -> str:
    """Mask a secret string for logging: show first 2 or 6 chars, then ***, then last 4."""
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) <= 10:
        return s[:2] + "***"
    return s[:6] + "***" + s[-4:]


def _env_first(*names: str) -> str:
    for name in names:
        value = _strip(os.environ.get(name))
        if value:
            return value
    return ''


def _normalize_dify_api_base(raw: str) -> str:
    """base 仅保留协议、主机、端口，不包含 /v1。"""
    u = _strip(raw)
    if not u:
        return u
    if not u.startswith(('http://', 'https://')):
        u = 'http://' + u
    u = u.rstrip('/')
    if u.endswith('/v1'):
        u = u[: -len('/v1')].rstrip('/')
    return u


def _float_env(name: str, default: float, minimum: float = 10.0) -> float:
    try:
        v = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, v)


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _bool_env(name: str, default: bool = False) -> bool:
    val = _strip(os.environ.get(name, '')).lower()
    if not val:
        return default
    return val in ('1', 'true', 'yes', 'on')


JWT_DEFAULT_DEV_SECRET = 'jewelry-qipei-2026-competition-secret'
JWT_SECRET_KEY = _strip(os.environ.get('JWT_SECRET_KEY')) or JWT_DEFAULT_DEV_SECRET
JWT_EXPIRE_MINUTES = int(os.environ.get('JWT_EXPIRE_MINUTES', '480'))

if JWT_SECRET_KEY == JWT_DEFAULT_DEV_SECRET:
    import logging as _logging
    _logging.getLogger("jewelry_qipei.config").warning(
        "JWT_SECRET_KEY is using the default dev secret — set JWT_SECRET_KEY in .env for production!"
    )

DIFY_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_API_BASE', 'DIFY_BASE_URL', 'DIFY_BASE')
) or 'http://localhost'
DIFY_API_KEY = _env_first('DIFY_API_KEY', 'DIFY_APP_API_KEY', 'DIFY_APP_KEY')
DIFY_AGENT2_API_KEY = _strip(os.environ.get('DIFY_AGENT2_API_KEY'))
DIFY_AGENT3_API_KEY = _strip(os.environ.get('DIFY_AGENT3_API_KEY'))
DIFY_AGENT4_API_KEY = _strip(os.environ.get('DIFY_AGENT4_API_KEY'))
DIFY_KNOWLEDGE_API_KEY = _strip(os.environ.get('DIFY_KNOWLEDGE_API_KEY')) or DIFY_API_KEY

DIFY_AGENT1_WORKFLOW_ID = _strip(os.environ.get('DIFY_AGENT1_WORKFLOW_ID'))
DIFY_INTENT_FALLBACK_WORKFLOW_ID = _strip(
    os.environ.get('DIFY_INTENT_FALLBACK_WORKFLOW_ID')
)
DIFY_INTENT_FALLBACK_TIMEOUT = _float_env('DIFY_INTENT_FALLBACK_TIMEOUT', 60.0, 10.0)
DIFY_UPSTREAM_FALLBACK_WORKFLOW_ID = _strip(
    os.environ.get('DIFY_UPSTREAM_FALLBACK_WORKFLOW_ID')
)
DIFY_WORKFLOW_TIMEOUT = _float_env('DIFY_WORKFLOW_TIMEOUT', 300.0)
_default_exam_timeout = max(DIFY_WORKFLOW_TIMEOUT, 600.0)
DIFY_WORKFLOW_TIMEOUT_EXAM = _float_env(
    'DIFY_WORKFLOW_TIMEOUT_EXAM', _default_exam_timeout
)
DIFY_WORKFLOW_MAX_CONCURRENT = _int_env('DIFY_WORKFLOW_MAX_CONCURRENT', 4, 1, 32)

KB_UPLOAD_MAX_MB = _int_env('KB_UPLOAD_MAX_MB', 15, 1, 50)
KB_DATASET_IDS_AGENT2 = _strip(os.environ.get('KB_DATASET_IDS_AGENT2'))
KB_DATASET_IDS_AGENT3 = _strip(os.environ.get('KB_DATASET_IDS_AGENT3'))
KB_DATASET_IDS_AGENT4 = _strip(os.environ.get('KB_DATASET_IDS_AGENT4'))
DIFY_KNOWLEDGE_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_KNOWLEDGE_API_BASE', 'DIFY_KB_API_BASE')
) or DIFY_API_BASE
KB_API_TIMEOUT = _float_env('KB_API_TIMEOUT', 120.0, 10.0)

_cors = _strip(os.environ.get('CORS_ORIGINS'))
CORS_ORIGINS = [x.strip() for x in _cors.split(',') if x.strip()] if _cors else ['http://127.0.0.1:8000', 'http://localhost:8000']
DATABASE_URL = _strip(os.environ.get('DATABASE_URL'))

APP_USER_ROLES = frozenset(
    {'admin', 'store_manager', 'senior_consultant', 'trainee'}
)

_DISABLE_SEED = _strip(os.environ.get('DISABLE_DEMO_USER_SEED', '1')).lower()
DISABLE_DEMO_USER_SEED = _DISABLE_SEED in ('1', 'true', 'yes')
DEMO_SEED_PASSWORD = _strip(os.environ.get('DEMO_SEED_PASSWORD')) or '123456'
LOGIN_LOCK_ENABLED = _bool_env('LOGIN_LOCK_ENABLED', default=True)
LOGIN_MAX_ATTEMPTS = _int_env('LOGIN_MAX_ATTEMPTS', 5, 1, 100)
LOGIN_LOCK_MINUTES = _int_env('LOGIN_LOCK_MINUTES', 15, 0, 1440)

if DEMO_SEED_PASSWORD == '123456' and not DISABLE_DEMO_USER_SEED:
    import logging as _logging
    _logging.getLogger("jewelry_qipei.config").warning(
        "DEMO_SEED_PASSWORD is using default '123456' — set DEMO_SEED_PASSWORD or disable demo seeding in production!"
    )

UVICORN_HOST = _strip(os.environ.get('UVICORN_HOST')) or '127.0.0.1'
UVICORN_PORT = int(os.environ.get('UVICORN_PORT', '8000'))
DEBUG_MODE = _bool_env('DEBUG')
_uvicorn_log = _strip(os.environ.get('UVICORN_LOG_LEVEL', ''))
UVICORN_LOG_LEVEL = _uvicorn_log or ('debug' if DEBUG_MODE else 'info')

DIFY_STAGE4A_FORCE_MOCK = _bool_env('DIFY_STAGE4A_FORCE_MOCK')
DIFY_STAGE4A_TIMEOUT = _float_env('DIFY_STAGE4A_TIMEOUT', DIFY_WORKFLOW_TIMEOUT)
DIFY_GROWTH1_TIMEOUT = _float_env(
    'DIFY_GROWTH1_TIMEOUT',
    max(DIFY_STAGE4A_TIMEOUT, 180.0),
    1.0,
)
DIFY_GROWTH1_RETRY_ATTEMPTS = _int_env('DIFY_GROWTH1_RETRY_ATTEMPTS', 1, 0, 3)
DIFY_GROWTH1_RETRY_BACKOFF_SEC = _float_env(
    'DIFY_GROWTH1_RETRY_BACKOFF_SEC',
    1.5,
    0.0,
)

DIFY_GROWTH1_WORKFLOW_ID = _strip(os.environ.get('DIFY_GROWTH1_WORKFLOW_ID'))
DIFY_GROWTH2_WORKFLOW_ID = _strip(os.environ.get('DIFY_GROWTH2_WORKFLOW_ID'))
DIFY_ASSISTANT1_WORKFLOW_ID = _strip(os.environ.get('DIFY_ASSISTANT1_WORKFLOW_ID'))
DIFY_ASSISTANT2_WORKFLOW_ID = _strip(os.environ.get('DIFY_ASSISTANT2_WORKFLOW_ID'))
DIFY_GROWTH1_API_KEY = _strip(os.environ.get('DIFY_GROWTH1_API_KEY'))
DIFY_GROWTH2_API_KEY = _strip(os.environ.get('DIFY_GROWTH2_API_KEY'))
DIFY_ASSISTANT1_API_KEY = _strip(os.environ.get('DIFY_ASSISTANT1_API_KEY'))
DIFY_ASSISTANT2_API_KEY = _strip(os.environ.get('DIFY_ASSISTANT2_API_KEY'))
DIFY_QA1_WORKFLOW_ID = _strip(os.environ.get('DIFY_QA1_WORKFLOW_ID'))
DIFY_QA1_API_KEY = _strip(os.environ.get('DIFY_QA1_API_KEY'))
DIFY_ASSISTANT1_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_ASSISTANT1_API_BASE', 'DIFY_ASSISTANT1_API_URL')
) or DIFY_API_BASE
DIFY_ASSISTANT2_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_ASSISTANT2_API_BASE', 'DIFY_ASSISTANT2_API_URL')
) or DIFY_API_BASE
DIFY_QA1_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_QA1_API_BASE', 'DIFY_QA1_API_URL')
) or DIFY_API_BASE
DIFY_PRACTICE_MENTOR_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_PRACTICE_MENTOR_API_BASE', 'DIFY_PRACTICE_MENTOR_API_URL')
) or DIFY_API_BASE
DIFY_WF11_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_WF11_API_BASE', 'DIFY_WF11_API_URL')
) or DIFY_API_BASE
DIFY_WF13_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_WF13_API_BASE', 'DIFY_WF13_API_URL')
) or DIFY_API_BASE
DIFY_WF14_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_WF14_API_BASE', 'DIFY_WF14_API_URL')
) or DIFY_API_BASE
DIFY_WF15_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_WF15_API_BASE', 'DIFY_WF15_API_URL')
) or DIFY_API_BASE

DIFY_STAGE4B_FORCE_MOCK = _bool_env('DIFY_STAGE4B_FORCE_MOCK')
DIFY_STAGE4B_TIMEOUT = _float_env('DIFY_STAGE4B_TIMEOUT', DIFY_WORKFLOW_TIMEOUT)
DIFY_ALLOW_FALLBACK_TO_MOCK = _bool_env('DIFY_ALLOW_FALLBACK_TO_MOCK')
DIFY_PRACTICE1_TIMEOUT = _float_env(
    'DIFY_PRACTICE1_TIMEOUT',
    min(DIFY_STAGE4B_TIMEOUT, 20.0),
    1.0,
)
DIFY_PRACTICE1_ALLOW_FAST_FALLBACK = _bool_env('DIFY_PRACTICE1_ALLOW_FAST_FALLBACK')
DIFY_ASSISTANT1_TIMEOUT = _float_env(
    'DIFY_ASSISTANT1_TIMEOUT',
    min(DIFY_STAGE4A_TIMEOUT, 25.0),
    1.0,
)
DIFY_ASSISTANT2_TIMEOUT = _float_env(
    'DIFY_ASSISTANT2_TIMEOUT',
    DIFY_STAGE4A_TIMEOUT,
    1.0,
)
DIFY_ASSISTANT_CHAT_API_KEY = _strip(os.environ.get('DIFY_ASSISTANT_CHAT_API_KEY'))
DIFY_ASSISTANT_CHAT_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_ASSISTANT_CHAT_API_BASE', 'DIFY_ASSISTANT_CHAT_API_URL')
) or DIFY_API_BASE
DIFY_ASSISTANT_CHAT_TIMEOUT = _float_env(
    'DIFY_ASSISTANT_CHAT_TIMEOUT',
    20.0,
    1.0,
)
DIFY_QA_CHAT_API_KEY = _strip(os.environ.get('DIFY_QA_CHAT_API_KEY'))
DIFY_QA_CHAT_API_BASE = _normalize_dify_api_base(
    _env_first('DIFY_QA_CHAT_API_BASE', 'DIFY_QA_CHAT_API_URL')
) or DIFY_API_BASE
DIFY_QA_CHAT_TIMEOUT = _float_env(
    'DIFY_QA_CHAT_TIMEOUT',
    12.0,
    1.0,
)
DIFY_QA1_TIMEOUT = _float_env(
    'DIFY_QA1_TIMEOUT',
    min(DIFY_STAGE4A_TIMEOUT, 25.0),
    1.0,
)
DIFY_PRACTICE_MENTOR_TIMEOUT = _float_env(
    'DIFY_PRACTICE_MENTOR_TIMEOUT',
    DIFY_STAGE4B_TIMEOUT,
    1.0,
)
DIFY_PRACTICE_TURN_FEEDBACK_TIMEOUT = _float_env(
    'DIFY_PRACTICE_TURN_FEEDBACK_TIMEOUT',
    15.0,
    1.0,
)

DIFY_PRACTICE1_API_KEY = _strip(os.environ.get('DIFY_PRACTICE1_API_KEY'))
DIFY_PRACTICE2_WORKFLOW_ID = _strip(os.environ.get('DIFY_PRACTICE2_WORKFLOW_ID'))
DIFY_PRACTICE3_WORKFLOW_ID = _strip(os.environ.get('DIFY_PRACTICE3_WORKFLOW_ID'))
DIFY_DASHBOARD_WORKFLOW_ID = _strip(os.environ.get('DIFY_DASHBOARD_WORKFLOW_ID'))
DIFY_QUERY1_WORKFLOW_ID = _strip(os.environ.get('DIFY_QUERY1_WORKFLOW_ID'))
DIFY_QUERY2_WORKFLOW_ID = _strip(os.environ.get('DIFY_QUERY2_WORKFLOW_ID'))
DIFY_PRACTICE2_API_KEY = _strip(os.environ.get('DIFY_PRACTICE2_API_KEY'))
DIFY_PRACTICE3_API_KEY = _strip(os.environ.get('DIFY_PRACTICE3_API_KEY'))
DIFY_DASHBOARD_API_KEY = _strip(os.environ.get('DIFY_DASHBOARD_API_KEY'))
DIFY_PRACTICE_MENTOR_API_KEY = _strip(os.environ.get('DIFY_PRACTICE_MENTOR_API_KEY'))
DIFY_PRACTICE_TURN_FEEDBACK_API_KEY = _strip(os.environ.get('DIFY_PRACTICE_TURN_FEEDBACK_API_KEY'))
DIFY_QUERY1_API_KEY = _strip(os.environ.get('DIFY_QUERY1_API_KEY'))
DIFY_QUERY2_API_KEY = _strip(os.environ.get('DIFY_QUERY2_API_KEY'))

# ---------------------------------------------------------------------------
# MiniMax TTS
# ---------------------------------------------------------------------------
MINIMAX_API_KEY = _strip(os.environ.get('MINIMAX_API_KEY'))
MINIMAX_API_BASE = _strip(os.environ.get('MINIMAX_API_BASE')) or 'https://api.minimaxi.com'
MINIMAX_GROUP_ID = _strip(os.environ.get('MINIMAX_GROUP_ID'))
MINIMAX_TTS_MODEL = _strip(os.environ.get('MINIMAX_TTS_MODEL')) or 'speech-2.8-hd'
MINIMAX_TTS_VOICE = _strip(os.environ.get('MINIMAX_TTS_VOICE')) or 'female-chengshu'
DIFY_QA1_API_KEY = DIFY_QA1_API_KEY or _strip(os.environ.get('DIFY_QA1_APP_API_KEY'))
DIFY_WF11_API_KEY = _strip(os.environ.get('DIFY_WF11_API_KEY')) or DIFY_PRACTICE1_API_KEY or DIFY_API_KEY
DIFY_WF13_API_KEY = _strip(os.environ.get('DIFY_WF13_API_KEY')) or DIFY_API_KEY
DIFY_WF14_API_KEY = _strip(os.environ.get('DIFY_WF14_API_KEY')) or DIFY_API_KEY
DIFY_WF15_API_KEY = _strip(os.environ.get('DIFY_WF15_API_KEY')) or DIFY_API_KEY
KB_DATASET_IDS_QA = _strip(os.environ.get('KB_DATASET_IDS_QA'))
KB_DATASET_IDS_QUERY = _strip(os.environ.get('KB_DATASET_IDS_QUERY'))
DIFY_WF11_TIMEOUT = _float_env('DIFY_WF11_TIMEOUT', DIFY_STAGE4B_TIMEOUT, 1.0)
DIFY_WF13_TIMEOUT = _float_env('DIFY_WF13_TIMEOUT', DIFY_WORKFLOW_TIMEOUT, 1.0)
DIFY_WF14_TIMEOUT = _float_env('DIFY_WF14_TIMEOUT', DIFY_WORKFLOW_TIMEOUT, 1.0)
DIFY_WF15_TIMEOUT = _float_env('DIFY_WF15_TIMEOUT', DIFY_WORKFLOW_TIMEOUT, 1.0)

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
LOG_LEVEL = _strip(os.environ.get('LOG_LEVEL', 'INFO')).upper()
LOG_FILE_ENABLED = _bool_env('LOG_FILE_ENABLED', default=True)
LOG_FILE_PATH = _strip(os.environ.get('LOG_FILE_PATH', 'logs/app.log'))
LOG_FILE_MAX_BYTES = _int_env('LOG_FILE_MAX_BYTES', 10485760, 1048576, 1073741824)
LOG_FILE_BACKUP_COUNT = _int_env('LOG_FILE_BACKUP_COUNT', 5, 1, 100)
LOG_JSON_ENABLED = _bool_env('LOG_JSON_ENABLED')
