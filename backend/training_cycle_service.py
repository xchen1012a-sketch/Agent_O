from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from db_stage3 import json_text, now_iso
from training_plan import (
    MODULE_NAME_MAP,
    build_daily_stage_tasks,
    build_stage_definitions,
    calculate_module_indexes,
    calculate_training_summary,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_text() -> str:
    return utcnow().strftime("%Y-%m-%d")


def _normalize_aliases(user_id: str, user_aliases: list[str] | None = None) -> list[str]:
    aliases: list[str] = []
    for item in list(user_aliases or []) + [user_id]:
        text = str(item or "").strip()
        if text and text not in aliases:
            aliases.append(text)
    return aliases


def _alias_match_clause(columns: list[str], aliases: list[str]) -> tuple[str, list[str]]:
    terms: list[str] = []
    params: list[str] = []
    for column in columns:
        for alias in aliases:
            terms.append(f"{column} = ?")
            params.append(alias)
    if not terms:
        return "1 = 0", []
    return "(" + " OR ".join(terms) + ")", params


def _stage_definition(stage_no: int) -> dict[str, Any]:
    for item in build_stage_definitions():
        if int(item["stage_no"]) == int(stage_no):
            return item
    return build_stage_definitions()[0]


def get_active_cycle(conn, user_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM training_cycles WHERE user_id = ? AND status IN ('active', 'waiting_review') ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def get_cycle(conn, cycle_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    if user_id:
        row = conn.execute("SELECT * FROM training_cycles WHERE cycle_id = ? AND user_id = ?", (cycle_id, user_id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM training_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
    return dict(row) if row else None


def create_stage_cycle(
    conn,
    *,
    user_id: str,
    plan_id: str,
    stage_no: int,
    cycle_type: str = "onboarding",
    previous_cycle_id: str = "",
    source_reset_days: int | None = None,
    release_all: bool = False,
) -> dict[str, Any]:
    stage = _stage_definition(stage_no)
    cycle_id = f"tc_{uuid.uuid4().hex[:12]}"
    now = now_iso()
    # "全部发布" means all days are published in advance, but unlocks still advance
    # one day at a time after the previous day is completed.
    day_unlock = {str(day): bool(day == 1) for day in range(1, int(stage["total_days"]) + 1)}
    conn.execute(
        """
        INSERT INTO training_cycles (
            cycle_id, user_id, plan_id, total_days, status, current_day,
            day_unlock_json, daily_plan_json, adaptive_state_json, started_at, completed_at,
            cycle_type, stage_no, stage_name, stage_status, plan_total_stages, stage_pass_score,
            unlock_mode, full_release_by_admin, full_release_at, source_reset_days, previous_cycle_id,
            stage_started_at, stage_completed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cycle_id,
            user_id,
            plan_id,
            int(stage["total_days"]),
            "active",
            1,
            json_text(day_unlock),
            json_text(build_daily_stage_tasks(stage_no=stage_no, user_id=user_id, cycle_id=cycle_id, release_all=release_all)),
            json_text({}),
            now,
            None,
            cycle_type,
            int(stage_no),
            str(stage["stage_name"]),
            "active",
            len(build_stage_definitions()),
            float(stage["pass_score"]),
            "daily",
            1 if release_all else 0,
            now if release_all else None,
            source_reset_days,
            previous_cycle_id,
            now,
            None,
            now,
            now,
        ),
    )
    seed_stage_tasks(conn, cycle_id=cycle_id, user_id=user_id, stage_no=stage_no, release_all=release_all)
    conn.execute(
        "UPDATE users SET training_cycle_id = ?, current_cycle_day = 1, updated_at = ? WHERE CAST(id AS TEXT) = ? OR user_id = ?",
        (cycle_id, now, user_id, user_id),
    )
    return get_cycle(conn, cycle_id) or {}


def seed_stage_tasks(conn, *, cycle_id: str, user_id: str, stage_no: int, release_all: bool = False) -> None:
    conn.execute("DELETE FROM cycle_daily_tasks WHERE cycle_id = ?", (cycle_id,))
    now = now_iso()
    for day_item in build_daily_stage_tasks(stage_no=stage_no, user_id=user_id, cycle_id=cycle_id, release_all=release_all):
        for task in day_item["tasks"]:
            is_unlocked = int(task["day_index"]) == 1
            conn.execute(
                """
                INSERT INTO cycle_daily_tasks (
                    cycle_id, user_id, day_index, task_code, task_type, branch, title, description,
                    status, target_count, current_count, score_json, dimension_focus, route_page, completed_at,
                    module_code, module_name, task_source, release_status, released_at, ai_score, ai_feedback,
                    next_action, evaluation_status, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    user_id,
                    int(task["day_index"]),
                    task["task_code"],
                    task["task_type"],
                    task["branch"],
                    task["title"],
                    task["description"],
                    "released" if is_unlocked else "locked",
                    int(task["target_count"]),
                    0,
                    "{}",
                    task["module_code"],
                    task["route_page"],
                    None,
                    task["module_code"],
                    task["module_name"],
                    task["task_source"],
                    task["release_status"],
                    now if task["release_status"] == "released" else None,
                    None,
                    "",
                    "",
                    task["evaluation_status"],
                    int(task["sort_order"]),
                    now,
                    now,
                ),
            )


def release_current_stage_days(conn, cycle_id: str) -> dict[str, Any]:
    cycle = get_cycle(conn, cycle_id)
    if not cycle:
        return {}
    now = now_iso()
    unlock_map = json.loads(cycle.get("day_unlock_json") or "{}")
    conn.execute(
        """
        UPDATE training_cycles
        SET full_release_by_admin = 1, full_release_at = ?, day_unlock_json = ?, updated_at = ?
        WHERE cycle_id = ?
        """,
        (
            now,
            json_text(unlock_map),
            now,
            cycle_id,
        ),
    )
    conn.execute(
        """
        UPDATE cycle_daily_tasks
        SET release_status = CASE WHEN release_status = 'locked' THEN 'released' ELSE release_status END,
            released_at = COALESCE(released_at, ?),
            updated_at = ?
        WHERE cycle_id = ?
        """,
        (now, now, cycle_id),
    )
    return get_cycle(conn, cycle_id) or {}


def void_cycle(conn, cycle_id: str) -> None:
    now = now_iso()
    cycle = get_cycle(conn, cycle_id)
    conn.execute(
        "UPDATE training_cycles SET status = 'voided', stage_status = 'archived', updated_at = ? WHERE cycle_id = ?",
        (now, cycle_id),
    )
    conn.execute(
        """
        UPDATE cycle_daily_tasks
        SET status = 'voided', release_status = 'voided', updated_at = ?
        WHERE cycle_id = ? AND status != 'completed'
        """,
        (now, cycle_id),
    )
    if cycle:
        conn.execute(
            """
            UPDATE users
            SET training_cycle_id = CASE WHEN training_cycle_id = ? THEN '' ELSE training_cycle_id END,
                current_cycle_day = CASE WHEN training_cycle_id = ? THEN 0 ELSE current_cycle_day END,
                updated_at = ?
            WHERE CAST(id AS TEXT) = ? OR user_id = ?
            """,
            (cycle_id, cycle_id, now, str(cycle["user_id"] or ""), str(cycle["user_id"] or "")),
        )


def stage_review_payload(conn, *, cycle_id: str, user_id: str) -> dict[str, Any]:
    cycle = get_cycle(conn, cycle_id, user_id)
    if not cycle:
        return {}
    practice_rows = conn.execute(
        """
        SELECT COALESCE(overall_score, 0) AS score
        FROM practice_eval_records
        WHERE user_id = ? AND cycle_id = ? AND stage_no = ?
        ORDER BY id DESC
        """,
        (user_id, cycle_id, int(cycle["stage_no"] or 1)),
    ).fetchall()
    learning_rows = conn.execute(
        """
        SELECT COALESCE(answer_score, score, 0) AS score
        FROM learning_eval_records
        WHERE user_id = ? OR employee_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (user_id, user_id),
    ).fetchall()
    assessment_rows = conn.execute(
        """
        SELECT COALESCE(score, 0) AS score
        FROM assessment_records
        WHERE user_id = ? AND cycle_day_index IS NOT NULL
        ORDER BY id DESC
        LIMIT 20
        """,
        (user_id,),
    ).fetchall()
    learning_scores = [float(row["score"] or 0) for row in learning_rows if float(row["score"] or 0) > 0][:7]
    practice_scores = [float(row["score"] or 0) for row in practice_rows if float(row["score"] or 0) > 0]
    assessment_scores = [float(row["score"] or 0) for row in assessment_rows if float(row["score"] or 0) > 0]
    task_rows = conn.execute(
        "SELECT COUNT(*) AS total_count, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count FROM cycle_daily_tasks WHERE cycle_id = ?",
        (cycle_id,),
    ).fetchone()
    total_count = int((task_rows["total_count"] if task_rows else 0) or 0)
    completed_count = int((task_rows["completed_count"] if task_rows else 0) or 0)
    completion_rate = round((completed_count / total_count) * 100, 2) if total_count else 0.0
    summary = calculate_training_summary(
        learning_scores=learning_scores,
        practice_scores=practice_scores,
        assessment_scores=assessment_scores,
        latest_stage_review_score=max(practice_scores[-1] if practice_scores else 0, assessment_scores[-1] if assessment_scores else 0),
        task_completion_rate=completion_rate,
        monthly_scores=[],
    )
    review_score = round((summary["training_coefficient"] * 0.7) + (completion_rate * 0.3), 2)
    is_pass = 1 if review_score >= float(cycle.get("stage_pass_score") or 80) else 0
    weak_modules = []
    module_indexes = refresh_module_snapshots(conn, user_id=user_id, persist=False)
    for module in sorted(module_indexes, key=lambda item: float(item.get("overall_index") or 0))[:3]:
        if float(module.get("overall_index") or 0) < 80:
            weak_modules.append(str(module.get("module_name") or module.get("module_code") or ""))
    review_summary = "阶段评估通过，可进入下一阶段。" if is_pass else "阶段评估未通过，需先完成补训任务。"
    if weak_modules:
        review_summary += " 当前优先补强：" + "、".join(weak_modules[:3]) + "。"
    ability_delta = {
        "product_knowledge_score": summary["learning_avg_score"],
        "sales_communication_score": summary["practice_avg_score"],
        "response_score": summary["assessment_avg_score"],
    }
    recommended_actions = [f"补训模块：{name}" for name in weak_modules[:3]] or ["保持当前节奏，继续推进下一阶段训练"]
    return {
        "cycle": cycle,
        "review_score": review_score,
        "is_pass": is_pass,
        "review_summary": review_summary,
        "ability_delta_json": json_text(ability_delta),
        "recommended_actions_json": json_text(recommended_actions),
        "next_stage_unlocked": bool(is_pass and int(cycle.get("stage_no") or 1) < len(build_stage_definitions())),
        "module_indexes": module_indexes,
    }


def refresh_module_snapshots(conn, *, user_id: str, persist: bool = True, user_aliases: list[str] | None = None) -> list[dict[str, Any]]:
    aliases = _normalize_aliases(user_id, user_aliases)
    learning_user_clause, learning_user_params = _alias_match_clause(["user_id", "employee_id"], aliases)
    practice_user_clause, practice_user_params = _alias_match_clause(["user_id", "employee_id"], aliases)
    assessment_user_clause, assessment_user_params = _alias_match_clause(["r.user_id"], aliases)
    module_records = []
    for module_code, module_name in MODULE_NAME_MAP.items():
        learning_rows = conn.execute(
            f"""
            SELECT COALESCE(answer_score, score, 0) AS score
            FROM learning_eval_records
            WHERE {learning_user_clause} AND module_code = ?
            ORDER BY id DESC
            """,
            tuple(learning_user_params + [module_code]),
        ).fetchall()
        practice_rows = conn.execute(
            f"""
            SELECT COALESCE(overall_score, 0) AS score
            FROM practice_eval_records
            WHERE {practice_user_clause} AND module_code = ?
            ORDER BY id DESC
            """,
            tuple(practice_user_params + [module_code]),
        ).fetchall()
        assessment_rows = conn.execute(
            f"""
            SELECT COALESCE(r.score, 0) AS score
            FROM assessment_records r
            JOIN assessment_tasks t ON t.id = r.task_id
            WHERE {assessment_user_clause}
              AND (
                COALESCE(t.module_code, '') = ?
                OR (
                    COALESCE(t.module_code, '') = ''
                    AND (t.task_desc LIKE ? OR t.task_name LIKE ?)
                )
              )
            ORDER BY r.id DESC
            """,
            tuple(assessment_user_params + [module_code, f"%{module_name}%", f"%{module_name}%"]),
        ).fetchall()
        module_records.append(
            {
                "module_code": module_code,
                "module_name": module_name,
                "learning_scores": [float(row["score"] or 0) for row in learning_rows if float(row["score"] or 0) > 0],
                "practice_scores": [float(row["score"] or 0) for row in practice_rows if float(row["score"] or 0) > 0],
                "assessment_scores": [float(row["score"] or 0) for row in assessment_rows if float(row["score"] or 0) > 0],
            }
        )
    indexes = calculate_module_indexes(module_records)
    if persist:
        conn.execute("DELETE FROM module_index_snapshots WHERE user_id = ? AND snapshot_date = ?", (user_id, _today_text()))
        now = now_iso()
        for item in indexes:
            conn.execute(
                """
                INSERT INTO module_index_snapshots (
                    user_id, module_code, module_name, practice_index, assessment_index,
                    learning_index, overall_index, snapshot_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    item["module_code"],
                    item["module_name"],
                    float(item["practice_index"]),
                    float(item["assessment_index"]),
                    float(item["learning_index"]),
                    float(item["overall_index"]),
                    _today_text(),
                    now,
                ),
            )
    return indexes
