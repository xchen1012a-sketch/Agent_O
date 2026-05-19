"""Evo 后台调度器。

Phase 3 反思循环、Phase 4 procedural 合成、Phase 5 升级提议都会挂到这里。
Phase 1 阶段使用 asyncio loop 而非 APScheduler，避免引入额外依赖。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import config as app_config
from database import SessionLocal
from evo.anomaly_detector import run_anomaly_scan
from evo.eval_runner import run_eval_cases
from evo.procedural_synthesizer import disable_stale_procedural_memories, run_procedural_synthesis
from evo.promoter import run_promotion_scan
from evo.reflector import run_reflection_cycle

_log = logging.getLogger("jewelry_qipei.evo.scheduler")

_TICK_INTERVAL_SECONDS = 60.0
_task: asyncio.Task[None] | None = None
_last_reflective_run_date: str = ""
_last_procedural_run_week: str = ""
_last_promotion_run_date: str = ""
_last_safety_run_date: str = ""


def _should_run_reflection(local_now: datetime) -> bool:
    if not getattr(app_config, "DIFY_EVO_REFLECTIVE_ENABLED", True):
        return False
    hour = int(getattr(app_config, "DIFY_EVO_REFLECTIVE_HOUR", 2) or 2)
    if local_now.hour != hour:
        return False
    return _last_reflective_run_date != local_now.date().isoformat()


def _should_run_procedural(local_now: datetime) -> bool:
    if not getattr(app_config, "DIFY_EVO_PROCEDURAL_ENABLED", True):
        return False
    hour = int(getattr(app_config, "DIFY_EVO_PROCEDURAL_HOUR", 3) or 3)
    weekday = int(getattr(app_config, "DIFY_EVO_PROCEDURAL_WEEKDAY", 6) or 6)
    if local_now.hour != hour or local_now.weekday() != weekday:
        return False
    year, week, _day = local_now.isocalendar()
    return _last_procedural_run_week != f"{year}-W{week:02d}"


def _should_run_promotion(local_now: datetime) -> bool:
    if not getattr(app_config, "DIFY_EVO_PROMOTION_ENABLED", True):
        return False
    hour = int(getattr(app_config, "DIFY_EVO_PROMOTION_HOUR", 4) or 4)
    if local_now.hour != hour:
        return False
    return _last_promotion_run_date != local_now.date().isoformat()


def _should_run_safety(local_now: datetime) -> bool:
    if not getattr(app_config, "DIFY_EVO_SAFETY_ENABLED", True):
        return False
    hour = int(getattr(app_config, "DIFY_EVO_SAFETY_HOUR", 5) or 5)
    if local_now.hour != hour:
        return False
    return _last_safety_run_date != local_now.date().isoformat()


def _run_due_jobs(now: datetime | None = None) -> None:
    global _last_reflective_run_date, _last_procedural_run_week, _last_promotion_run_date, _last_safety_run_date
    current_utc = now or datetime.now(timezone.utc)
    local_now = current_utc.astimezone()
    if _should_run_reflection(local_now):
        with SessionLocal() as session:
            written = run_reflection_cycle(session, now=current_utc)
            session.commit()
        _last_reflective_run_date = local_now.date().isoformat()
        _log.info("evo reflective cycle finished written=%s", len(written))

    if _should_run_procedural(local_now):
        with SessionLocal() as session:
            disabled = disable_stale_procedural_memories(session, now=current_utc)
            written = run_procedural_synthesis(session, now=current_utc)
            session.commit()
        year, week, _day = local_now.isocalendar()
        _last_procedural_run_week = f"{year}-W{week:02d}"
        _log.info("evo procedural cycle finished written=%s disabled=%s", len(written), disabled)

    if _should_run_promotion(local_now):
        with SessionLocal() as session:
            promotions = run_promotion_scan(session, now=current_utc)
            session.commit()
        _last_promotion_run_date = local_now.date().isoformat()
        _log.info("evo promotion cycle finished suggested=%s", len(promotions))

    if _should_run_safety(local_now):
        with SessionLocal() as session:
            runs = run_eval_cases(session, triggered_by="daily_safety", now=current_utc)
            anomalies = run_anomaly_scan(session, now=current_utc)
            session.commit()
        _last_safety_run_date = local_now.date().isoformat()
        failed_count = sum(1 for run in runs if run.status != "passed")
        _log.info(
            "evo safety cycle finished runs=%s failed=%s anomalies=%s",
            len(runs),
            failed_count,
            len(anomalies),
        )


async def _tick_loop() -> None:
    _log.info("evo scheduler started interval=%ss", _TICK_INTERVAL_SECONDS)
    try:
        while True:
            await asyncio.sleep(_TICK_INTERVAL_SECONDS)
            _run_due_jobs()
    except asyncio.CancelledError:
        _log.info("evo scheduler stopped")
        raise
    except Exception:
        _log.exception("evo scheduler crashed")
        raise


def start_scheduler() -> asyncio.Task[None]:
    """挂到 FastAPI lifespan 里调用一次。重复调用是幂等的。"""
    global _task
    if _task is not None and not _task.done():
        return _task
    loop = asyncio.get_event_loop()
    _task = loop.create_task(_tick_loop(), name="evo-scheduler")
    return _task


def stop_scheduler() -> None:
    """关闭时调用。"""
    global _task
    if _task is None:
        return
    _task.cancel()
    _task = None
