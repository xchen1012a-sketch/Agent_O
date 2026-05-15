"""TTS proxy endpoint for MiniMax only, gated by global digital-human settings."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import AsyncIterator

import config as app_config
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
import system_settings_service as digital_human_settings_service

router = APIRouter(prefix="/api/tts", tags=["tts"])

_log = logging.getLogger("jewelry_qipei.tts")

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "tts"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(*, text: str, emotion: str, model: str, voice_id: str) -> Path:
    cache_key = "|".join([model.strip(), voice_id.strip(), emotion.strip(), text.strip()])
    digest = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.mp3"


def minimax_tts_config_snapshot() -> dict[str, str | bool]:
    return {
        "configured": bool(str(app_config.MINIMAX_API_KEY or "").strip()),
        "base_url": str(app_config.MINIMAX_API_BASE or "").strip() or "https://api.minimaxi.com",
        "group_configured": bool(str(app_config.MINIMAX_GROUP_ID or "").strip()),
        "model": str(app_config.MINIMAX_TTS_MODEL or "").strip() or "speech-2.8-hd",
        "voice_id": str(app_config.MINIMAX_TTS_VOICE or "").strip() or "female-chengshu",
    }


def _candidate_minimax_urls() -> list[str]:
    configured = str(app_config.MINIMAX_API_BASE or "").strip().rstrip("/")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    for base in ("https://api.minimaxi.com", "https://api.minimax.io"):
        if base.rstrip("/") not in candidates:
            candidates.append(base.rstrip("/"))

    group_id = str(app_config.MINIMAX_GROUP_ID or "").strip()
    urls: list[str] = []
    for base in candidates:
        url = base + "/v1/t2a_v2"
        if group_id:
            url = url + "?GroupId=" + group_id
        urls.append(url)
    return urls


def _synthesize_minimax(text: str, model: str, voice_id: str, emotion: str = "") -> bytes | None:
    api_key = str(app_config.MINIMAX_API_KEY or "").strip()
    if not api_key:
        _log.warning("MiniMax TTS skipped: missing_api_key")
        return None

    payload: dict[str, object] = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    if emotion:
        payload["voice_setting"]["emotion"] = emotion

    for url in _candidate_minimax_urls():
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except Exception:
            _log.exception(
                "MiniMax TTS call failed: reason=network_or_parse_error url=%s model=%s voice=%s",
                url,
                model,
                voice_id,
            )
            continue

        if response.status_code != 200:
            reason = "http_error"
            if response.status_code in (401, 403):
                reason = "permission_denied"
            elif response.status_code == 429:
                reason = "rate_limited"
            _log.warning(
                "MiniMax TTS failed: reason=%s url=%s status=%s model=%s voice=%s body=%s",
                reason,
                url,
                response.status_code,
                model,
                voice_id,
                response.text[:300],
            )
            continue

        try:
            data = response.json()
        except ValueError:
            _log.warning("MiniMax TTS failed: reason=invalid_json url=%s model=%s voice=%s", url, model, voice_id)
            continue

        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") != 0:
            _log.warning(
                "MiniMax TTS failed: reason=api_error url=%s model=%s voice=%s code=%s msg=%s",
                url,
                model,
                voice_id,
                base_resp.get("status_code"),
                str(base_resp.get("status_msg") or "")[:300],
            )
            continue

        audio_hex = str((data.get("data") or {}).get("audio") or "").strip()
        if not audio_hex:
            _log.warning("MiniMax TTS failed: reason=empty_audio url=%s model=%s voice=%s", url, model, voice_id)
            continue

        try:
            audio = bytes.fromhex(audio_hex)
        except ValueError:
            _log.warning(
                "MiniMax TTS failed: reason=invalid_audio_payload url=%s model=%s voice=%s",
                url,
                model,
                voice_id,
            )
            continue

        extra = data.get("extra_info") or {}
        _log.info(
            "MiniMax TTS OK: url=%s model=%s voice=%s audio=%d bytes length=%dms",
            url,
            model,
            voice_id,
            len(audio),
            int(extra.get("audio_length") or 0),
        )
        return audio

    return None


async def _stream_minimax_audio(
    text: str, model: str, voice_id: str, emotion: str = ""
) -> AsyncIterator[bytes]:
    """Call MiniMax with stream=True and yield raw MP3 audio chunks."""
    api_key = str(app_config.MINIMAX_API_KEY or "").strip()
    if not api_key:
        _log.warning("MiniMax streaming TTS skipped: missing_api_key")
        return

    payload: dict[str, object] = {
        "model": model,
        "text": text,
        "stream": True,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    if emotion:
        payload["voice_setting"]["emotion"] = emotion

    for url in _candidate_minimax_urls():
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status_code != 200:
                        _log.warning(
                            "MiniMax streaming TTS failed: status=%s url=%s",
                            response.status_code,
                            url,
                        )
                        continue

                    chunk_count = 0
                    total_bytes = 0
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if not data_str:
                            continue
                        try:
                            data = json.loads(data_str)
                        except ValueError:
                            continue

                        base_resp = data.get("base_resp") or {}
                        if base_resp.get("status_code") not in (0, None):
                            _log.warning(
                                "MiniMax streaming chunk error: code=%s msg=%s",
                                base_resp.get("status_code"),
                                str(base_resp.get("status_msg", ""))[:200],
                            )
                            continue

                        audio_hex = str(
                            (data.get("data") or {}).get("audio") or ""
                        ).strip()
                        if not audio_hex:
                            continue

                        try:
                            audio_bytes = bytes.fromhex(audio_hex)
                        except ValueError:
                            continue

                        chunk_count += 1
                        total_bytes += len(audio_bytes)
                        yield audio_bytes

                    _log.info(
                        "MiniMax streaming TTS OK: url=%s model=%s voice=%s chunks=%d bytes=%d",
                        url,
                        model,
                        voice_id,
                        chunk_count,
                        total_bytes,
                    )
                    return

        except Exception:
            _log.exception(
                "MiniMax streaming TTS call failed: url=%s model=%s voice=%s",
                url,
                model,
                voice_id,
            )
            continue

    _log.error("MiniMax streaming TTS: all candidate URLs failed")


@router.post("/synthesize")
async def synthesize(request: Request) -> Response:
    settings = digital_human_settings_service.get_digital_human_system_settings()
    if not bool(settings.get("enabled")):
        return Response(
            content=b'{"error":"digital human is disabled"}',
            status_code=403,
            media_type="application/json",
        )
    if str(settings.get("tts_provider") or "").strip().lower() == "browser":
        _log.warning("TTS endpoint called while browser provider is selected; client should handle speech synthesis")
        return Response(
            content=b'{"error":"browser tts provider is selected"}',
            status_code=409,
            media_type="application/json",
        )
    body = await request.json()
    text = str(body.get("text", "")).strip()
    emotion = str(body.get("emotion", "")).strip()
    model = str(body.get("model", "")).strip() or str(app_config.MINIMAX_TTS_MODEL or "").strip() or "speech-2.8-hd"
    voice_id = str(body.get("voice_id", "")).strip() or str(app_config.MINIMAX_TTS_VOICE or "").strip() or "female-chengshu"

    if not text:
        return Response(
            content=b'{"error":"text is required"}',
            status_code=400,
            media_type="application/json",
        )

    cached = _cache_path(text=text, emotion=emotion, model=model, voice_id=voice_id)
    if cached.exists():
        return Response(content=cached.read_bytes(), media_type="audio/mpeg")

    audio = _synthesize_minimax(text, model, voice_id, emotion)
    if audio is None:
        return Response(
            content=b'{"error":"TTS synthesis failed"}',
            status_code=502,
            media_type="application/json",
        )

    try:
        cached.write_bytes(audio)
    except OSError:
        _log.warning("TTS cache write failed path=%s", cached)

    return Response(content=audio, media_type="audio/mpeg")


@router.post("/synthesize/stream")
async def synthesize_stream(request: Request) -> Response:
    """Streaming TTS: calls MiniMax with stream=True and returns audio chunks progressively."""
    settings = digital_human_settings_service.get_digital_human_system_settings()
    if not bool(settings.get("enabled")):
        return Response(
            content=b'{"error":"digital human is disabled"}',
            status_code=403,
            media_type="application/json",
        )
    if str(settings.get("tts_provider") or "").strip().lower() == "browser":
        return Response(
            content=b'{"error":"browser tts provider is selected"}',
            status_code=409,
            media_type="application/json",
        )
    body = await request.json()
    text = str(body.get("text", "")).strip()
    emotion = str(body.get("emotion", "")).strip()
    model = str(body.get("model", "")).strip() or str(app_config.MINIMAX_TTS_MODEL or "").strip() or "speech-2.8-hd"
    voice_id = str(body.get("voice_id", "")).strip() or str(app_config.MINIMAX_TTS_VOICE or "").strip() or "female-chengshu"

    if not text:
        return Response(
            content=b'{"error":"text is required"}',
            status_code=400,
            media_type="application/json",
        )

    # Cache hit: return full file from non-streaming endpoint (faster for cached content)
    cached = _cache_path(text=text, emotion=emotion, model=model, voice_id=voice_id)
    if cached.exists():
        return Response(content=cached.read_bytes(), media_type="audio/mpeg")

    return StreamingResponse(
        _stream_minimax_audio(text, model, voice_id, emotion),
        media_type="audio/mpeg",
    )
