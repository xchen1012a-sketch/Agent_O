"""JWT 鉴权 & RBAC 角色权限控制模块。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

_log = logging.getLogger("jewelry_qipei.auth")

try:
    import config as app_config
    from config import JWT_DEFAULT_DEV_SECRET
except ImportError:
    import secrets as _secrets
    app_config = None
    JWT_DEFAULT_DEV_SECRET = _secrets.token_hex(32)

ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def _secret_key() -> str:
    if app_config is None:
        return JWT_DEFAULT_DEV_SECRET
    return str(getattr(app_config, "JWT_SECRET_KEY", "") or JWT_DEFAULT_DEV_SECRET)


def _access_token_expire_minutes() -> int:
    if app_config is None:
        return 480
    raw_value = getattr(app_config, "JWT_EXPIRE_MINUTES", 480)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 480


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=_access_token_expire_minutes())
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, _secret_key(), algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        _log.warning("token expired")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 已过期")
    except jwt.InvalidTokenError:
        _log.warning("invalid token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效 token")


def decode_token_optional(token: str) -> dict | None:
    """解析 JWT；过期或无效时返回 None，不抛 HTTP 异常（用于会话探测等）。"""
    try:
        return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


_LEGACY_TO_CANONICAL_ROLE = {
    "newbie": "trainee",
    "senior": "senior_consultant",
    "leader": "store_manager",
}
MANAGEMENT_ROLES = frozenset({"admin", "store_manager"})
ORDINARY_EMPLOYEE_ROLES = frozenset({"senior_consultant", "trainee"})


def normalize_app_role(role: str | None) -> str:
    """将历史 users.role 枚举归一为 MVP 四角色（与 config.APP_USER_ROLES 一致）。"""
    r = (role or "").strip().lower()
    return _LEGACY_TO_CANONICAL_ROLE.get(r, r)


def is_admin_role(role: str | None) -> bool:
    return normalize_app_role(role) == "admin"


def is_management_role(role: str | None) -> bool:
    return normalize_app_role(role) in MANAGEMENT_ROLES


def is_ordinary_employee_role(role: str | None) -> bool:
    return normalize_app_role(role) in ORDINARY_EMPLOYEE_ROLES


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    if creds is None:
        _log.warning("auth failed: no credentials provided")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未提供认证凭据")
    payload = _decode_token(creds.credentials)
    user_id: str | None = payload.get("user_id")
    role: str | None = payload.get("role")
    if not user_id or not role:
        _log.warning("auth failed: incomplete payload user_id=%s role=%s", bool(user_id), bool(role))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 载荷不完整")
    _log.debug("auth success user_id=%s role=%s", user_id, role)
    return {
        "user_id": user_id,
        "role": role,
        "username": payload.get("username", ""),
        "store_id": payload.get("store_id", ""),
    }


async def get_optional_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict | None:
    """无 Bearer、过期或无效 token 时返回 None，不返回 HTTP 401。"""
    if creds is None:
        return None
    payload = decode_token_optional(creds.credentials)
    if not payload:
        return None
    user_id: str | None = payload.get("user_id")
    role: str | None = payload.get("role")
    if not user_id or not role:
        return None
    return {
        "user_id": str(user_id),
        "role": role,
        "username": payload.get("username", ""),
        "store_id": payload.get("store_id", ""),
    }


def require_roles(allowed: list[str]):
    """返回一个 FastAPI 依赖，校验当前用户角色是否在 allowed 列表中。"""

    allowed_norm = {normalize_app_role(x) for x in allowed}

    async def _checker(
        current_user: Annotated[dict, Depends(get_current_user)],
    ) -> dict:
        if normalize_app_role(current_user["role"]) not in allowed_norm:
            _log.warning(
                "access denied user_id=%s role=%s required=%s",
                current_user.get("user_id"), current_user["role"], allowed_norm,
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"权限不足：当前角色 {current_user['role']} 无法访问此资源",
            )
        return current_user

    return _checker
