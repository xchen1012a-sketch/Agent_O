from __future__ import annotations

import importlib
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

import config as app_config
from agent_activity import publish_agent_activity_from_request
from config import _mask_secret
from logging_config import initialize_logging, set_request_id, clear_request_id, get_request_id
from auth import (
    create_access_token,
    get_current_user,
    get_optional_user,
    get_password_hash,
    require_roles,
    verify_password,
)
from database import SQLITE_DB_PATH, SessionLocal, ensure_database_initialized, utc_now_iso
from db_stage3 import ensure_stage3_tables
from evo.scheduler import start_scheduler, stop_scheduler
from routers import BUSINESS_ROUTERS, task
from workflow_registry import DIFY_WORKFLOW_REGISTRY

_log = logging.getLogger("jewelry_qipei")
DB_PATH = SQLITE_DB_PATH
ENV_PATH = Path(__file__).resolve().parent / ".env"
# 与 backend 同级的 frontend 目录（静态页与 SPA 资源）
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _employee_no(row_id: Any) -> str:
    try:
        return f"EMP{int(row_id):05d}"
    except (TypeError, ValueError):
        return str(row_id or "").strip()

REQUIRED_DIFY_ENV_KEYS = (
    "DIFY_API_BASE",
    *(item["api_key_env"] for item in DIFY_WORKFLOW_REGISTRY),
)
_ALLOWED_UPLOAD_ENV_PREFIXES = ("DIFY_", "JWT_SECRET_KEY", "JWT_EXPIRE_MINUTES")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _configured_key_map() -> dict[str, str]:
    return {
        item["api_key_env"]: _mask_secret(getattr(app_config, item["api_key_env"], ""))
        for item in DIFY_WORKFLOW_REGISTRY
    }


def _missing_required_dify_keys() -> list[str]:
    return [
        item["api_key_env"]
        for item in DIFY_WORKFLOW_REGISTRY
        if not str(getattr(app_config, item["api_key_env"], "") or "").strip()
    ]


def _workflow_health_snapshot() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in DIFY_WORKFLOW_REGISTRY:
        env_name = item["api_key_env"]
        raw_value = str(getattr(app_config, env_name, "") or "").strip()
        items.append(
            {
                "workflow_code": item["code"],
                "label": item["label"],
                "route_path": item["route_path"],
                "call_type": item["call_type"],
                "agent_role": item.get("agent_role", ""),
                "api_key_env": env_name,
                "configured": bool(raw_value),
                "masked_api_key": _mask_secret(raw_value),
            }
        )
    return items


def _log_dify_config_summary() -> None:
    missing_required = _missing_required_dify_keys()
    _log.info(
        "startup dify_config env_files=%s base=%s stage4a_force_mock=%s stage4b_force_mock=%s allow_fallback_to_mock=%s",
        list(getattr(app_config, "LOADED_ENV_FILES", ())),
        app_config.DIFY_API_BASE,
        app_config.DIFY_STAGE4A_FORCE_MOCK,
        app_config.DIFY_STAGE4B_FORCE_MOCK,
        app_config.DIFY_ALLOW_FALLBACK_TO_MOCK,
    )
    _log.info("startup dify_keys %s missing_required=%s", _configured_key_map(), missing_required)


def _log_tts_config_summary() -> None:
    configured = bool(str(app_config.MINIMAX_API_KEY or "").strip())
    base_url = str(app_config.MINIMAX_API_BASE or "").strip() or "https://api.minimaxi.com"
    group_configured = bool(str(app_config.MINIMAX_GROUP_ID or "").strip())
    model = str(app_config.MINIMAX_TTS_MODEL or "").strip() or "speech-2.8-hd"
    voice_id = str(app_config.MINIMAX_TTS_VOICE or "").strip() or "female-chengshu"
    _log.info(
        "startup tts_config minimax_configured=%s base_url=%s group_configured=%s model=%s voice_id=%s edge_fallback=%s",
        configured,
        base_url,
        group_configured,
        model,
        voice_id,
        True,
    )
    if not configured:
        _log.warning("startup tts_config missing MINIMAX_API_KEY, Edge TTS will be used as fallback")


class LoginRequest(BaseModel):
    username: str = Field("", description="用户名")
    password: str = Field("", description="密码")


class UpdateMeRequest(BaseModel):
    display_name: str | None = Field(None, description="显示名")
    phone: str | None = Field(None, description="手机号")
    old_password: str | None = Field(None, description="旧密码")
    password: str | None = Field(None, description="新密码")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _parse_env_text(env_text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in (env_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = (k or "").strip()
        if not key:
            continue
        val = (v or "").strip()
        if len(val) >= 2 and (
            (val.startswith('"') and val.endswith('"'))
            or (val.startswith("'") and val.endswith("'"))
        ):
            val = val[1:-1]
        pairs[key] = val
    return pairs


def _is_allowed_uploaded_env_key(key: str) -> bool:
    upper_key = str(key or "").strip().upper()
    return any(upper_key.startswith(prefix) for prefix in _ALLOWED_UPLOAD_ENV_PREFIXES)


def _sanitize_uploaded_env(env_pairs: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    sanitized: dict[str, str] = {}
    rejected: list[str] = []
    for raw_key, value in env_pairs.items():
        key = str(raw_key or "").strip().upper()
        if not key or not _ENV_KEY_RE.match(key):
            rejected.append(str(raw_key or ""))
            continue
        if _is_allowed_uploaded_env_key(key):
            sanitized[key] = value
        else:
            rejected.append(key)
    return sanitized, rejected


def _serialize_env_pairs(env_pairs: dict[str, str]) -> str:
    if not env_pairs:
        return ""
    return "".join(f"{key}={value}\n" for key, value in env_pairs.items())


def _sync_runtime_env(env_pairs: dict[str, str]) -> None:
    managed_keys = [key for key in list(os.environ.keys()) if _is_allowed_uploaded_env_key(key)]
    for key in managed_keys:
        os.environ.pop(key, None)
    for key, value in env_pairs.items():
        os.environ[key] = value
    importlib.reload(app_config)


def _ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL UNIQUE,
            hashed_password TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            store_id TEXT NOT NULL DEFAULT 'STORE01',
            phone TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            first_login_at TEXT,
            onboarding_completed INTEGER NOT NULL DEFAULT 0,
            onboarding_completed_at TEXT,
            training_cycle_id TEXT NOT NULL DEFAULT '',
            current_cycle_day INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_store_id ON users(store_id)")


def _seed_demo_users(conn: sqlite3.Connection) -> None:
    if app_config.DISABLE_DEMO_USER_SEED:
        return

    demo_password_hash = get_password_hash(app_config.DEMO_SEED_PASSWORD)
    now = utc_now_iso()
    rows = [
        ("admin", "admin", "系统管理员", demo_password_hash, "admin", "系统管理员", "HQ", "", now, now),
        ("manager", "manager", "门店店长", demo_password_hash, "store_manager", "门店店长", "STORE01", "", now, now),
        ("senior", "senior", "资深顾问", demo_password_hash, "senior_consultant", "资深顾问", "STORE01", "", now, now),
        ("trainee", "trainee", "导购", demo_password_hash, "trainee", "导购", "STORE01", "", now, now),
    ]
    inserted_count = 0
    for row in rows:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ? OR user_id = ? LIMIT 1",
            (row[1], row[0]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO users (user_id, username, name, hashed_password, role, display_name, store_id, phone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        inserted_count += 1
    _log.info("seeded missing demo users count=%s", inserted_count)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_database_initialized()
    with get_conn() as conn:
        ensure_stage3_tables(conn)
        _ensure_users_table(conn)
        _seed_demo_users(conn)
    _log_dify_config_summary()
    _log_tts_config_summary()
    _log.info(
        "frontend_dir=%s static_mount=%s",
        FRONTEND_DIR,
        FRONTEND_DIR.is_dir(),
    )
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    title="珠宝企业培训智能体系统（19工作流版）",
    version="2.0.0",
    lifespan=lifespan,
)

initialize_logging()

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Simple in-memory rate limiter ──────────────────────────────────────────
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_GET = 120  # requests per window per IP+method+path
_RATE_LIMIT_MAX_MUTATION = 30  # requests per window per IP+method+path
_RATE_LIMIT_EXEMPT_PATHS = {
    "/",
    "/favicon.ico",
    "/frontend",
    "/frontend/",
    "/health",
    "/health/db",
    "/health/dify",
}
_rate_limit_store: dict[str, tuple[float, int]] = {}


def _normalize_rate_limit_path(path: str) -> str:
    normalized = (path or "").strip() or "/"
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized or "/"


def _rate_limit_policy(method: str, path: str) -> tuple[str, int] | None:
    normalized_method = (method or "GET").upper()
    normalized_path = _normalize_rate_limit_path(path)
    if normalized_method == "OPTIONS":
        return None
    if normalized_path in _RATE_LIMIT_EXEMPT_PATHS or normalized_path.startswith("/frontend/"):
        return None
    if not normalized_path.startswith("/api/"):
        return None
    limit = _RATE_LIMIT_MAX_GET if normalized_method in {"GET", "HEAD"} else _RATE_LIMIT_MAX_MUTATION
    return normalized_path, limit


def _rate_limit_bucket_key(client_ip: str, method: str, path: str) -> tuple[str, int] | None:
    policy = _rate_limit_policy(method, path)
    if policy is None:
        return None
    normalized_path, limit = policy
    bucket_key = f"{client_ip}:{method.upper()}:{normalized_path}"
    return bucket_key, limit


def _prune_rate_limit_store(
    now: float,
    *,
    store: dict[str, tuple[float, int]] | None = None,
) -> dict[str, tuple[float, int]]:
    target = _rate_limit_store if store is None else store
    cutoff = now - _RATE_LIMIT_WINDOW
    expired_keys = [key for key, value in target.items() if value[0] < cutoff]
    for key in expired_keys:
        target.pop(key, None)
    return target


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    bucket = _rate_limit_bucket_key(client_ip, request.method, request.url.path)
    if bucket is None:
        return await call_next(request)
    bucket_key, bucket_limit = bucket
    now = time.time()
    window_start, count = _rate_limit_store.get(bucket_key, (now, 0))
    if now - window_start > _RATE_LIMIT_WINDOW:
        window_start = now
        count = 0
    count += 1
    _rate_limit_store[bucket_key] = (window_start, count)
    if count > bucket_limit:
        retry_after = max(int(_RATE_LIMIT_WINDOW - (now - window_start)), 0)
        response = JSONResponse(
            status_code=429,
            content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": {}},
        )
        response.headers["Retry-After"] = str(retry_after)
        return response
    # Periodically clean up old entries
    if len(_rate_limit_store) > 10000:
        _prune_rate_limit_store(now)
    return await call_next(request)

for router in BUSINESS_ROUTERS:
    app.include_router(router)


@app.middleware("http")
async def log_http_request(request, call_next):
    rid = set_request_id()
    started = time.perf_counter()
    _log.info("http request start method=%s path=%s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        elapsed = time.perf_counter() - started
        _log.exception(
            "http request failed method=%s path=%s request_id=%s elapsed=%.2fs",
            request.method,
            request.url.path,
            rid,
            elapsed,
        )
        publish_agent_activity_from_request(
            method=request.method,
            route_path=request.url.path,
            status_code=500,
            request_id=rid,
            elapsed_seconds=elapsed,
        )
        clear_request_id()
        raise
    elapsed = time.perf_counter() - started
    _log.info(
        "http request finish method=%s path=%s status=%s request_id=%s elapsed=%.2fs",
        request.method,
        request.url.path,
        response.status_code,
        rid,
        elapsed,
    )
    publish_agent_activity_from_request(
        method=request.method,
        route_path=request.url.path,
        status_code=response.status_code,
        request_id=rid,
        elapsed_seconds=elapsed,
    )
    clear_request_id()
    # 确保 JSON / HTML 响应头声明 charset=utf-8，避免 Mac/Windows 乱码
    ct = response.headers.get("content-type", "")
    if "json" in ct and "charset" not in ct:
        response.headers["content-type"] = ct.rstrip() + "; charset=utf-8"
    elif "text/html" in ct and "charset" not in ct:
        response.headers["content-type"] = ct.rstrip() + "; charset=utf-8"
    elif "text/css" in ct and "charset" not in ct:
        response.headers["content-type"] = ct.rstrip() + "; charset=utf-8"
    elif "javascript" in ct and "charset" not in ct:
        response.headers["content-type"] = ct.rstrip() + "; charset=utf-8"
    # /frontend/ 静态资源依赖版本号做长期缓存；HTML shell 每次重校验，避免部署后用户继续使用旧入口页。
    if request.method == "GET" and response.status_code in (200, 304):
        p = (request.url.path or "").lower()
        if p.startswith("/frontend/") or p in ("/frontend", "/frontend/"):
            is_shell = p.endswith(".html") or p.rstrip("/") == "/frontend"
            is_static_asset = any(
                p.endswith(ext)
                for ext in (".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".ttf")
            )
            if is_static_asset:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                if "Pragma" in response.headers:
                    del response.headers["Pragma"]
            elif is_shell:
                response.headers["Cache-Control"] = "no-cache"
                if "Pragma" in response.headers:
                    del response.headers["Pragma"]
    return response


@app.get("/", include_in_schema=False)
def root():
    """重定向到 /frontend/，确保相对路径的 CSS/JS 资源能被 StaticFiles 正确加载。"""
    return RedirectResponse(url="/frontend/")


@app.post("/", include_in_schema=False)
def root_post():
    """POST / 重定向到 /frontend/（303 促使浏览器以 GET 重新请求）。"""
    return RedirectResponse(url="/frontend/", status_code=303)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/one-sentence-query", include_in_schema=False)
@app.get("/one-sentence-query.html", include_in_schema=False)
def one_sentence_query_redirect():
    """旧独立页已移除，统一走 /frontend/ SPA。"""
    return RedirectResponse(url="/frontend/#quick_query")


@app.get("/store-talent-dashboard", include_in_schema=False)
@app.get("/store-talent-dashboard.html", include_in_schema=False)
def store_talent_dashboard_redirect():
    return RedirectResponse(url="/frontend/#talent_dashboard")


@app.get("/talent-dashboard", include_in_schema=False)
def talent_dashboard_shortcut():
    return RedirectResponse(url="/frontend/#talent_dashboard")


@app.get("/on-job-assistant", include_in_schema=False)
@app.get("/on-job-assistant.html", include_in_schema=False)
def on_job_assistant_redirect():
    return RedirectResponse(url="/frontend/#on_duty_assistant")


@app.get("/personnel-management", include_in_schema=False)
@app.get("/personnel-management.html", include_in_schema=False)
def personnel_management_redirect():
    return RedirectResponse(url="/frontend/#personnel_manage")


@app.get("/index", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index_page_redirect():
    """工作台仅通过 /frontend/ 静态挂载提供，根路径直链重定向。"""
    return RedirectResponse(url="/frontend/")


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return {
            "code": 200,
            "message": "ok",
            "data": {
                "db": "sqlite",
                "db_path": str(DB_PATH),
            },
        }
    except sqlite3.Error as e:
        _log.exception("health check failed")
        return {
            "code": 500,
            "message": "db_error",
            "data": {
                "db": "sqlite",
                "error": str(e),
            },
        }


@app.get("/health/db")
def health_db() -> dict[str, Any]:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {"code": 200, "message": "ok", "data": {"db": "sqlite", "orm": "sqlalchemy"}}
    except Exception as e:
        _log.exception("health_db check failed")
        return {
            "code": 500,
            "message": "db_error",
            "data": {"db": "sqlite", "orm": "sqlalchemy", "error": str(e)},
        }


@app.get("/health/dify")
def health_dify() -> dict[str, Any]:
    workflow_health = _workflow_health_snapshot()
    missing_required = [
        item["api_key_env"]
        for item in workflow_health
        if not bool(item["configured"])
    ]
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "env_files": list(getattr(app_config, "LOADED_ENV_FILES", ())),
            "dify_api_base": app_config.DIFY_API_BASE,
            "stage4a_force_mock": app_config.DIFY_STAGE4A_FORCE_MOCK,
            "stage4b_force_mock": app_config.DIFY_STAGE4B_FORCE_MOCK,
            "allow_fallback_to_mock": app_config.DIFY_ALLOW_FALLBACK_TO_MOCK,
            "configured_keys": _configured_key_map(),
            "workflow_registry": workflow_health,
            "registered_workflow_count": len(DIFY_WORKFLOW_REGISTRY),
            "configured_workflow_count": sum(1 for item in workflow_health if bool(item["configured"])),
            "required_keys_count": len(REQUIRED_DIFY_ENV_KEYS),
            "missing_required_keys": missing_required,
        },
    }


MAX_LOGIN_ATTEMPTS = int(getattr(app_config, "LOGIN_MAX_ATTEMPTS", 5) or 5)
LOCK_MINUTES = int(getattr(app_config, "LOGIN_LOCK_MINUTES", 15) or 15)
LOGIN_LOCK_ENABLED = bool(getattr(app_config, "LOGIN_LOCK_ENABLED", True))

@app.post("/api/login")
def login(body: LoginRequest):
    username = (body.username or "").strip()
    password = (body.password or "").strip()
    if not username or not password:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "用户名和密码不能为空", "data": {}},
        )

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                u.id,
                u.username,
                u.hashed_password,
                u.role,
                u.display_name,
                COALESCE(u.store_id, '') AS store_id,
                COALESCE(NULLIF(TRIM(s.store_name), ''), NULLIF(TRIM(s.name), ''), COALESCE(u.store_id, '')) AS store_name,
                u.first_login_at,
                COALESCE(u.onboarding_completed, 0) AS onboarding_completed,
                COALESCE(u.training_cycle_id, '') AS training_cycle_id,
                COALESCE(u.current_cycle_day, 0) AS current_cycle_day,
                COALESCE(u.failed_login_attempts, 0) AS failed_login_attempts,
                u.locked_until
            FROM users u
            LEFT JOIN stores s
              ON LOWER(TRIM(COALESCE(s.store_id, ''))) = LOWER(TRIM(COALESCE(u.store_id, '')))
            WHERE u.username = ?
            """,
            (username,),
        ).fetchone()

        # ── Account lockout check ──
        if LOGIN_LOCK_ENABLED and row and row["locked_until"]:
            try:
                locked_ts = datetime.fromisoformat(row["locked_until"]).timestamp()
                if time.time() < locked_ts:
                    remaining = int((locked_ts - time.time()) / 60) + 1
                    _log.warning("login blocked (locked) username=%s", username)
                    return JSONResponse(
                        status_code=403,
                        content={"code": 403, "message": f"账号已锁定，请 {remaining} 分钟后重试", "data": {}},
                    )
                else:
                    # Lock expired — reset counter
                    conn.execute(
                        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
                        (row["id"],),
                    )
                    row = dict(row)
                    row["failed_login_attempts"] = 0
                    row["locked_until"] = None
            except (ValueError, OSError):
                pass

    user_found = row is not None
    verify_ok = bool(row) and verify_password(password, row["hashed_password"])
    _log.info("login attempt username=%s found=%s verified=%s", username, user_found, verify_ok)

    if not row or not verify_ok:
        # ── Increment failed attempts ──
        if row and LOGIN_LOCK_ENABLED:
            attempts = int(row["failed_login_attempts"] or 0) + 1
            if attempts >= MAX_LOGIN_ATTEMPTS:
                locked_until = datetime.fromtimestamp(
                    time.time() + LOCK_MINUTES * 60, tz=timezone.utc
                ).isoformat()
                _log.warning("account locked username=%s attempts=%s", username, attempts)
                with get_conn() as conn2:
                    conn2.execute(
                        "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
                        (attempts, locked_until, row["id"]),
                    )
                return JSONResponse(
                    status_code=403,
                    content={"code": 403, "message": f"连续登录失败 {MAX_LOGIN_ATTEMPTS} 次，账号已锁定 {LOCK_MINUTES} 分钟", "data": {}},
                )
            else:
                with get_conn() as conn2:
                    conn2.execute(
                        "UPDATE users SET failed_login_attempts = ? WHERE id = ?",
                        (attempts, row["id"]),
                    )
                remaining = MAX_LOGIN_ATTEMPTS - attempts
                return JSONResponse(
                    status_code=401,
                    content={"code": 401, "message": f"用户名或密码错误，还剩 {remaining} 次机会", "data": {}},
                )
        return JSONResponse(
            status_code=401,
            content={"code": 401, "message": "用户名或密码错误", "data": {}},
        )

    # ── Login success — reset failed attempts ──
    with get_conn() as conn_reset:
        conn_reset.execute(
            "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
            (row["id"],),
        )

    token = create_access_token(
        {
            "user_id": str(row["id"]),
            "username": row["username"],
            "role": row["role"],
            "store_id": str(row["store_id"] or "").strip(),
        }
    )

    # First-login detection: stamp first_login_at if never set.
    first_login = row["first_login_at"] is None
    if first_login:
        try:
            with get_conn() as conn2:
                conn2.execute(
                    "UPDATE users SET first_login_at = ? WHERE id = ? AND first_login_at IS NULL",
                    (utc_now_iso(), row["id"]),
                )
        except Exception:
            pass

    # Audit log: login
    try:
        from audit import log_audit
        with get_conn() as conn_audit:
            log_audit(
                conn_audit,
                user_id=str(row["id"]),
                user_name=row["display_name"] or row["username"],
                user_role=row["role"],
                action="login",
                target_type="user",
                target_id=str(row["id"]),
                target_name=row["display_name"] or row["username"],
            )
    except Exception:
        pass

    return {
        "code": 200,
        "message": "success",
        "data": {
            "access_token": token,
            "token": token,
            "user_id": str(row["id"]),
            "employee_no": _employee_no(row["id"]),
            "username": row["username"],
            "role": row["role"],
            "display_name": row["display_name"] or row["username"],
            "store_id": row["store_id"] or "",
            "store_name": row["store_name"] or row["store_id"] or "",
            "first_login": first_login,
            "onboarding_completed": bool(row["onboarding_completed"]),
            "training_cycle_id": row["training_cycle_id"] or "",
            "current_cycle_day": row["current_cycle_day"] or 0,
        },
    }


@app.get("/api/me")
def get_me(current_user: Annotated[dict[str, Any] | None, Depends(get_optional_user)]):
    if not current_user:
        return {"code": 401, "message": "未登录", "data": {}}
    uid = str(current_user.get("user_id") or "").strip()
    if not uid:
        return {"code": 401, "message": "未登录", "data": {}}

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                   u.id,
                   u.username,
                   u.role,
                   u.display_name,
                   u.created_at,
                   COALESCE(u.store_id, '') AS store_id,
                   COALESCE(NULLIF(TRIM(s.store_name), ''), NULLIF(TRIM(s.name), ''), COALESCE(u.store_id, '')) AS store_name,
                   COALESCE(u.phone, '') AS phone,
                   u.first_login_at,
                   COALESCE(u.onboarding_completed, 0) AS onboarding_completed,
                   COALESCE(u.training_cycle_id, '') AS training_cycle_id,
                   COALESCE(u.current_cycle_day, 0) AS current_cycle_day
            FROM users u
            LEFT JOIN stores s
              ON LOWER(TRIM(COALESCE(s.store_id, ''))) = LOWER(TRIM(COALESCE(u.store_id, '')))
            WHERE CAST(u.id AS TEXT) = ?
            """,
            (uid,),
        ).fetchone()

    if not row:
        return {"code": 404, "message": "用户不存在", "data": {}}

    data = dict(row)
    data["display_name"] = data.get("display_name") or data.get("username") or ""
    data["employee_no"] = _employee_no(data.get("id"))
    return {"code": 200, "message": "success", "data": data}


@app.patch("/api/me")
def patch_me(
    body: UpdateMeRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    uid = str(current_user.get("user_id") or "").strip()
    if not uid:
        return {"code": 401, "message": "未登录", "data": {}}

    updates: list[str] = []
    params: list[Any] = []

    if body.display_name is not None:
        updates.append("display_name = ?")
        params.append((body.display_name or "").strip())

    if body.phone is not None:
        phone = (body.phone or "").strip()
        if len(phone) > 32:
            return {"code": 400, "message": "手机号过长", "data": {}}
        updates.append("phone = ?")
        params.append(phone)

    change_password = body.password is not None and (body.password or "").strip() != ""
    if change_password:
        old_pwd = (body.old_password or "").strip()
        new_pwd = (body.password or "").strip()
        if not old_pwd:
            return {"code": 400, "message": "请先输入旧密码", "data": {}}
        if len(new_pwd) < 6:
            return {"code": 400, "message": "新密码至少 6 位", "data": {}}

        with get_conn() as conn:
            row = conn.execute(
                "SELECT id, hashed_password FROM users WHERE CAST(id AS TEXT) = ?",
                (uid,),
            ).fetchone()
            if not row:
                return {"code": 404, "message": "用户不存在", "data": {}}
            if not verify_password(old_pwd, row["hashed_password"]):
                return {"code": 400, "message": "旧密码错误", "data": {}}

        updates.append("hashed_password = ?")
        params.append(get_password_hash(new_pwd))

    if not updates:
        return {"code": 200, "message": "success", "data": {"user_id": uid, "employee_no": _employee_no(uid)}}

    updates.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(uid)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE CAST(id AS TEXT) = ?",
            tuple(params),
        )
        # Sync display_name to employee_profiles so growth plan sees the change
        if body.display_name is not None:
            new_name = (body.display_name or "").strip()
            conn.execute(
                "UPDATE employee_profiles SET employee_name = ?, updated_at = ? WHERE user_id = ? OR employee_id = ?",
                (new_name, utc_now_iso(), uid, uid),
            )

    return {"code": 200, "message": "success", "data": {"user_id": uid, "employee_no": _employee_no(uid)}}


@app.post(
    "/api/admin/env/upload",
    dependencies=[Depends(require_roles(["admin"]))],
)
async def upload_env_file(
    file: UploadFile = File(...),
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    filename = (file.filename or "").strip().lower()
    if filename and ".env" not in filename:
        return {"code": 400, "message": "仅支持上传 .env 文件", "data": {}}

    raw = await file.read()
    if not raw:
        return {"code": 400, "message": "上传文件为空", "data": {}}
    if len(raw) > 512 * 1024:
        return {"code": 400, "message": "文件过大（最大 512KB）", "data": {}}

    try:
        env_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"code": 400, "message": "请上传 UTF-8 编码的 .env 文件", "data": {}}

    env_pairs = _parse_env_text(env_text)
    if not env_pairs:
        return {"code": 400, "message": ".env 无有效键值对", "data": {}}

    sanitized_pairs, rejected_keys = _sanitize_uploaded_env(env_pairs)
    if not sanitized_pairs:
        return {"code": 400, "message": ".env 无有效可用配置项", "data": {"rejected_keys": rejected_keys}}

    missing_required = [k for k in REQUIRED_DIFY_ENV_KEYS if not sanitized_pairs.get(k)]
    if missing_required:
        return {
            "code": 400,
            "message": "缺少必要的 Dify API Key 配置项",
            "data": {"missing_required_keys": missing_required},
        }

    ENV_PATH.write_text(_serialize_env_pairs(sanitized_pairs), encoding="utf-8")
    _sync_runtime_env(sanitized_pairs)

    # Audit log: env upload
    if current_user:
        try:
            from audit import log_audit_from_user
            with get_conn() as conn_audit:
                log_audit_from_user(conn_audit, current_user, action="env_upload", target_type="env",
                                    target_id="env", target_name=".env",
                                    detail={"updated_keys": sorted(sanitized_pairs)})
        except Exception:
            pass

    return {
        "code": 200,
        "message": "success",
        "data": {
            "env_path": str(ENV_PATH),
            "updated_keys": sorted(sanitized_pairs),
            "rejected_keys": rejected_keys,
            "required_keys_count": len(REQUIRED_DIFY_ENV_KEYS),
            "missing_required_keys": [],
        },
    }


@app.exception_handler(Exception)
async def on_unhandled_exception(_request, exc: Exception):
    _log.exception(
        "unhandled exception request_id=%s path=%s",
        get_request_id(),
        getattr(_request, "url", ""),
    )
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "internal_server_error", "data": {"error": "服务器内部错误，请稍后重试"}},
    )


if FRONTEND_DIR.is_dir():
    @app.get("/frontend", include_in_schema=False)
    def serve_frontend_index():
        return FileResponse(
            os.path.join(FRONTEND_DIR, "index.html"),
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
            },
        )

if FRONTEND_DIR.is_dir():
    try:
        app.mount(
            "/frontend/",
            StaticFiles(directory=str(FRONTEND_DIR), html=True),
            name="frontend",
        )
        _log.info("static frontend mounted: /frontend/ -> %s", FRONTEND_DIR)
    except OSError as e:
        _log.exception("static frontend mount failed: %s", e)
else:
    _log.warning("frontend directory not found, /frontend/ mount skipped: %s", FRONTEND_DIR)


def _is_port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def _kill_port_owner(host: str, port: int) -> bool:
    """On Windows, kill the process holding *port* via netstat+taskkill."""
    import subprocess
    try:
        out = subprocess.check_output(
            f'netstat -ano | findstr "{host}:{port}" | findstr "LISTENING"',
            shell=True, text=True, timeout=5,
        ).strip()
        if not out:
            return False
        pid = int(out.strip().split()[-1])
        subprocess.check_call(f'taskkill /F /PID {pid}', shell=True, timeout=5)
        _log.info("killed stale process pid=%s on %s:%s", pid, host, port)
        return True
    except Exception as exc:
        _log.warning("failed to kill port owner: %s", exc)
        return False


if __name__ == "__main__":
    import uvicorn

    host = app_config.UVICORN_HOST
    port = app_config.UVICORN_PORT

    if _is_port_in_use(host, port):
        _log.warning("port %s:%s already in use, attempting to reclaim", host, port)
        _kill_port_owner(host, port)
        import time
        time.sleep(1)
        if _is_port_in_use(host, port):
            _log.error("port %s:%s still in use after reclaim attempt, aborting", host, port)
            raise SystemExit(1)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=app_config.UVICORN_LOG_LEVEL,
    )
