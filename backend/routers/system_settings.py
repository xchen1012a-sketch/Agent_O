from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user, require_roles
import system_settings_service

router = APIRouter(prefix="/api/system-settings", tags=["system-settings"])

_admin_only = require_roles(["admin"])


class DigitalHumanSystemSettingsPatchBody(BaseModel):
    enabled: bool | None = None
    tts_provider: str | None = None


@router.get("/digital-human")
def get_digital_human_settings(
    _current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return {
        "code": 200,
        "message": "success",
        "data": system_settings_service.get_digital_human_system_settings(),
    }


@router.patch("/digital-human", dependencies=[Depends(_admin_only)])
def patch_digital_human_settings(
    body: DigitalHumanSystemSettingsPatchBody,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    if body.enabled is None and body.tts_provider is None:
        raise HTTPException(status_code=400, detail="at least one field is required")
    provider = body.tts_provider
    if provider is not None:
        provider = str(provider).strip().lower()
        if provider not in system_settings_service.VALID_DIGITAL_HUMAN_TTS_PROVIDERS:
            raise HTTPException(status_code=400, detail="invalid tts_provider")
    snapshot = system_settings_service.update_digital_human_system_settings(
        enabled=body.enabled,
        tts_provider=provider,
        current_user=current_user,
    )
    return {
        "code": 200,
        "message": "success",
        "data": snapshot,
    }
