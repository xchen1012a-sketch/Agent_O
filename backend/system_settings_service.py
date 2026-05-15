from __future__ import annotations

from typing import Any

import config as app_config
from database import utc_now_iso
from db_stage3 import get_conn

DIGITAL_HUMAN_ENABLED_KEY = "digital_human_enabled"
DIGITAL_HUMAN_TTS_PROVIDER_KEY = "digital_human_tts_provider"
VALID_DIGITAL_HUMAN_TTS_PROVIDERS = frozenset({"minimax", "browser"})


def minimax_configured() -> bool:
    return bool(str(app_config.MINIMAX_API_KEY or "").strip())


def default_digital_human_tts_provider() -> str:
    return "minimax" if minimax_configured() else "browser"


def _normalize_bool(value: Any, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_provider(value: Any, default: str | None = None) -> str:
    provider = str(value or "").strip().lower()
    fallback = default or default_digital_human_tts_provider()
    if provider in VALID_DIGITAL_HUMAN_TTS_PROVIDERS:
        return provider
    return fallback


def _snapshot_from_rows(rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": _normalize_bool(rows.get(DIGITAL_HUMAN_ENABLED_KEY), default=True),
        "tts_provider": _normalize_provider(rows.get(DIGITAL_HUMAN_TTS_PROVIDER_KEY)),
        "minimax_configured": minimax_configured(),
    }


def _upsert_setting(conn, *, key: str, value: str, current_user: dict[str, Any] | None) -> None:
    actor = current_user or {}
    conn.execute(
        """
        INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by, updated_by_role)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = excluded.setting_value,
            updated_at = excluded.updated_at,
            updated_by = excluded.updated_by,
            updated_by_role = excluded.updated_by_role
        """,
        (
            key,
            value,
            utc_now_iso(),
            str(actor.get("user_id") or "").strip(),
            str(actor.get("role") or "").strip(),
        ),
    )


def get_digital_human_system_settings() -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT setting_key, setting_value
            FROM system_settings
            WHERE setting_key IN (?, ?)
            """,
            (DIGITAL_HUMAN_ENABLED_KEY, DIGITAL_HUMAN_TTS_PROVIDER_KEY),
        ).fetchall()
        row_map = {str(row["setting_key"]): row["setting_value"] for row in rows}
        snapshot = _snapshot_from_rows(row_map)

        if DIGITAL_HUMAN_ENABLED_KEY not in row_map:
            _upsert_setting(conn, key=DIGITAL_HUMAN_ENABLED_KEY, value="true", current_user=None)
        if DIGITAL_HUMAN_TTS_PROVIDER_KEY not in row_map:
            _upsert_setting(
                conn,
                key=DIGITAL_HUMAN_TTS_PROVIDER_KEY,
                value=str(snapshot["tts_provider"]),
                current_user=None,
            )
        return snapshot


def update_digital_human_system_settings(
    *,
    enabled: bool | None = None,
    tts_provider: str | None = None,
    current_user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = get_digital_human_system_settings()
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    next_provider = current["tts_provider"] if tts_provider is None else _normalize_provider(tts_provider)

    with get_conn() as conn:
        _upsert_setting(
            conn,
            key=DIGITAL_HUMAN_ENABLED_KEY,
            value="true" if next_enabled else "false",
            current_user=current_user,
        )
        _upsert_setting(
            conn,
            key=DIGITAL_HUMAN_TTS_PROVIDER_KEY,
            value=next_provider,
            current_user=current_user,
        )
    return get_digital_human_system_settings()
