from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Generator

import config as app_config
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_log = logging.getLogger("jewelry_qipei.database")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "jewelry_qipei.db"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_database_url() -> str:
    configured = (app_config.DATABASE_URL or "").strip()
    if configured:
        return configured
    return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


DATABASE_URL = _build_database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")
SQLITE_DB_PATH = DEFAULT_SQLITE_PATH
if IS_SQLITE and DATABASE_URL.startswith("sqlite:///"):
    raw = DATABASE_URL.replace("sqlite:///", "", 1)
    SQLITE_DB_PATH = Path(raw)

connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_SCHEMA_LOCK = Lock()
_SCHEMA_READY = False


def _validate_identifier(name: str) -> str:
    """Validate a SQL identifier (table/column name) is safe for DDL interpolation."""
    import re as _re
    if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def _ensure_sqlite_columns(conn: sqlite3.Connection) -> None:
    required_columns: dict[str, dict[str, str]] = {
        "users": {
            "user_id": "TEXT",
            "name": "TEXT",
            "updated_at": "TEXT",
            "first_login_at": "TEXT",
            "onboarding_completed": "INTEGER",
            "onboarding_completed_at": "TEXT",
            "training_cycle_id": "TEXT",
            "current_cycle_day": "INTEGER",
            "failed_login_attempts": "INTEGER DEFAULT 0",
            "locked_until": "TEXT",
        },
        "stores": {
            "store_id": "TEXT",
            "store_name": "TEXT",
            "region": "TEXT",
            "manager_name": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
        "employee_profiles": {
            "user_id": "TEXT",
            "job_title": "TEXT",
            "self_intro": "TEXT",
            "historical_learning": "TEXT",
            "initial_ability": "TEXT",
            "mentor_name": "TEXT",
            "current_product_knowledge_score": "REAL",
            "current_compliance_score": "REAL",
            "current_sales_communication_score": "REAL",
            "current_response_score": "REAL",
            "current_overall_score": "REAL",
        },
        "growth_plan_records": {
            "user_id": "TEXT",
            "growth_plan_text": "TEXT",
            "plan_meta_json": "TEXT",
            "source_workflow": "TEXT",
        },
        "learning_eval_records": {
            "user_id": "TEXT",
            "module_code": "TEXT",
            "module_name": "TEXT",
            "question_text": "TEXT",
            "user_answer": "TEXT",
            "standard_answer": "TEXT",
            "knowledge_tag": "TEXT",
            "answer_score": "REAL",
            "mastery_level": "TEXT",
            "weak_dimension": "TEXT",
            "evaluation_text": "TEXT",
            "source_workflow": "TEXT",
        },
        "practice_records": {
            "practice_id": "TEXT",
            "user_id": "TEXT",
            "scenario_type": "TEXT",
            "difficulty": "TEXT",
            "trainee_role": "TEXT",
            "dialogue_text": "TEXT",
            "round_count": "INTEGER",
            "end_flag": "INTEGER",
            "module_code": "TEXT",
            "module_name": "TEXT",
            "score_branch": "TEXT",
            "cycle_id": "TEXT",
            "stage_no": "INTEGER",
            "updated_at": "TEXT",
        },
        "practice_eval_records": {
            "practice_id": "TEXT",
            "user_id": "TEXT",
            "level": "TEXT",
            "risk_level": "TEXT",
            "weak_dimension": "TEXT",
            "highlights_json": "TEXT",
            "problem_points_json": "TEXT",
            "improvement_advice": "TEXT",
            "concise_feedback": "TEXT",
            "followup_training": "TEXT",
            "source_workflow": "TEXT",
            "score_branch": "TEXT",
            "cycle_day_index": "INTEGER",
            "module_code": "TEXT",
            "module_name": "TEXT",
            "cycle_id": "TEXT",
            "stage_no": "INTEGER",
        },
        "ability_update_records": {
            "practice_id": "TEXT",
            "user_id": "TEXT",
            "product_knowledge_score": "REAL",
            "compliance_score": "REAL",
            "sales_communication_score": "REAL",
            "response_score": "REAL",
            "overall_score": "REAL",
            "risk_level": "TEXT",
            "focus_dimension": "TEXT",
            "manager_tip": "TEXT",
            "update_summary": "TEXT",
            "source_workflow": "TEXT",
            "score_branch": "TEXT",
            "cycle_day_index": "INTEGER",
            "module_code": "TEXT",
            "module_name": "TEXT",
            "cycle_id": "TEXT",
            "stage_no": "INTEGER",
            "update_source": "TEXT",
        },
        "assistant_records": {
            "user_id": "TEXT",
            "store_id": "TEXT",
            "matched_knowledge": "TEXT",
            "question_type": "TEXT",
            "knowledge_tag": "TEXT",
            "risk_level": "TEXT",
            "weak_dimension": "TEXT",
            "training_advice": "TEXT",
            "source_workflow_reply": "TEXT",
            "source_workflow_analyze": "TEXT",
        },
        "dashboard_snapshots": {
            "user_id": "TEXT",
            "overall_score": "REAL",
            "compliance_score": "REAL",
            "training_completion_rate": "REAL",
            "recent_practice_avg_score": "REAL",
            "recent_high_risk_count": "INTEGER",
            "core_weak_dimension": "TEXT",
            "dashboard_result_json": "TEXT",
            "source_workflow": "TEXT",
        },
        "sales_performance": {
            "attach_rate": "REAL",
            "member_conversion_rate": "REAL",
            "high_margin_share": "REAL",
            "target_sales_amount": "REAL",
            "target_avg_ticket": "REAL",
            "target_conversion_rate": "REAL",
            "target_attach_rate": "REAL",
            "target_member_conversion_rate": "REAL",
            "target_high_margin_share": "REAL",
        },
        "query_records": {
            "store_id": "TEXT",
            "user_query": "TEXT",
            "query_type": "TEXT",
            "params_json": "TEXT",
            "query_result_json": "TEXT",
            "summary_text": "TEXT",
            "source_workflow_parse": "TEXT",
            "source_workflow_summary": "TEXT",
        },
        "role_settings": {
            "is_enabled": "INTEGER",
        },
        "assessment_tasks": {
            "module_code": "TEXT",
            "paper_config_json": "TEXT",
            "exam_mode": "TEXT",
            "duration_minutes": "INTEGER",
            "score_visibility": "TEXT",
            "publish_status": "TEXT",
            "target_scope_type": "TEXT",
            "paper_generation_status": "TEXT",
            "paper_review_version": "INTEGER",
            "paper_source_type": "TEXT",
            "allow_retake": "INTEGER",
            "max_attempts": "INTEGER",
            "auto_submit_on_timeout": "INTEGER",
            "started_notice_text": "TEXT",
            "submitted_notice_text": "TEXT",
            "created_by_role": "TEXT",
            "updated_at": "TEXT",
            "published_at": "TEXT",
        },
        "assessment_records": {
            "score_branch": "TEXT",
            "cycle_day_index": "INTEGER",
            "started_at": "TEXT",
            "expires_at": "TEXT",
            "submitted_at": "TEXT",
            "submit_status": "TEXT",
            "score_visibility_snapshot": "TEXT",
            "is_score_visible_to_user": "INTEGER",
            "paper_answer_json": "TEXT",
            "paper_result_json": "TEXT",
            "time_spent_seconds": "INTEGER",
            "is_timeout": "INTEGER",
            "review_source": "TEXT",
            "exam_mode_snapshot": "TEXT",
            "task_version_snapshot": "INTEGER",
        },
        "training_cycles": {
            "cycle_type": "TEXT",
            "stage_no": "INTEGER",
            "stage_name": "TEXT",
            "stage_status": "TEXT",
            "plan_total_stages": "INTEGER",
            "stage_pass_score": "REAL",
            "unlock_mode": "TEXT",
            "full_release_by_admin": "INTEGER",
            "full_release_at": "TEXT",
            "source_reset_days": "INTEGER",
            "previous_cycle_id": "TEXT",
            "stage_started_at": "TEXT",
            "stage_completed_at": "TEXT",
        },
        "cycle_daily_tasks": {
            "module_code": "TEXT",
            "module_name": "TEXT",
            "task_source": "TEXT",
            "release_status": "TEXT",
            "released_at": "TEXT",
            "ai_score": "REAL",
            "ai_feedback": "TEXT",
            "next_action": "TEXT",
            "evaluation_status": "TEXT",
            "sort_order": "INTEGER",
        },
    }

    for table_name, columns in required_columns.items():
        _validate_identifier(table_name)
        # Single PRAGMA call per table, then set lookup — ~11 round-trips instead of ~143
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({_validate_identifier(table_name)})")}
        for column_name, column_ddl in columns.items():
            _validate_identifier(column_name)
            if column_name not in existing:
                _log.info("migration: ALTER TABLE %s ADD COLUMN %s %s", table_name, column_name, column_ddl)
                conn.execute(f"ALTER TABLE {_validate_identifier(table_name)} ADD COLUMN {_validate_identifier(column_name)} {column_ddl}")


def _ensure_index(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    index_name: str,
    column_name: str,
    unique: bool = False,
) -> None:
    for n in (table_name, index_name, column_name):
        _validate_identifier(n)
    unique_sql = "UNIQUE " if unique else ""
    conn.execute(
        f"CREATE {unique_sql}INDEX IF NOT EXISTS {_validate_identifier(index_name)} ON {_validate_identifier(table_name)}({_validate_identifier(column_name)})"
    )


def _backfill_sqlite_data(conn: sqlite3.Connection) -> None:
    updates = [
        """
        UPDATE users
        SET user_id = COALESCE(NULLIF(user_id, ''), CAST(id AS TEXT), username)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE users
        SET name = COALESCE(NULLIF(name, ''), NULLIF(display_name, ''), NULLIF(username, ''), user_id)
        WHERE name IS NULL OR name = ''
        """,
        """
        UPDATE users
        SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, CURRENT_TIMESTAMP)
        WHERE updated_at IS NULL OR updated_at = ''
        """,
        """
        UPDATE stores
        SET id = COALESCE(NULLIF(id, ''), NULLIF(store_id, ''), CAST(rowid AS TEXT))
        WHERE id IS NULL OR id = ''
        """,
        """
        UPDATE stores
        SET store_id = COALESCE(NULLIF(store_id, ''), CAST(id AS TEXT))
        WHERE store_id IS NULL OR store_id = ''
        """,
        """
        UPDATE stores
        SET store_name = COALESCE(NULLIF(store_name, ''), NULLIF(name, ''), store_id)
        WHERE store_name IS NULL OR store_name = ''
        """,
        """
        UPDATE stores
        SET created_at = COALESCE(NULLIF(created_at, ''), NULLIF(updated_at, ''), CURRENT_TIMESTAMP)
        WHERE created_at IS NULL OR created_at = ''
        """,
        """
        UPDATE stores
        SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, CURRENT_TIMESTAMP)
        WHERE updated_at IS NULL OR updated_at = ''
        """,
        """
        UPDATE employee_profiles
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE employee_profiles
        SET job_title = COALESCE(NULLIF(job_title, ''), position)
        WHERE job_title IS NULL OR job_title = ''
        """,
        """
        UPDATE growth_plan_records
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE growth_plan_records
        SET growth_plan_text = COALESCE(NULLIF(growth_plan_text, ''), payload_json)
        WHERE growth_plan_text IS NULL OR growth_plan_text = ''
        """,
        """
        UPDATE growth_plan_records
        SET plan_meta_json = COALESCE(NULLIF(plan_meta_json, ''), payload_json, '{}')
        WHERE plan_meta_json IS NULL OR plan_meta_json = ''
        """,
        """
        UPDATE growth_plan_records
        SET source_workflow = COALESCE(NULLIF(source_workflow, ''), 'growth_plan')
        WHERE source_workflow IS NULL OR source_workflow = ''
        """,
        """
        UPDATE learning_eval_records
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE learning_eval_records
        SET evaluation_text = COALESCE(NULLIF(evaluation_text, ''), payload_json)
        WHERE evaluation_text IS NULL OR evaluation_text = ''
        """,
        """
        UPDATE learning_eval_records
        SET source_workflow = COALESCE(NULLIF(source_workflow, ''), 'growth_evaluate')
        WHERE source_workflow IS NULL OR source_workflow = ''
        """,
        """
        UPDATE practice_records
        SET practice_id = COALESCE(NULLIF(practice_id, ''), session_id)
        WHERE practice_id IS NULL OR practice_id = ''
        """,
        """
        UPDATE practice_records
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE practice_records
        SET scenario_type = COALESCE(NULLIF(scenario_type, ''), scene_code)
        WHERE scenario_type IS NULL OR scenario_type = ''
        """,
        """
        UPDATE practice_records
        SET difficulty = COALESCE(NULLIF(difficulty, ''), difficulty_level)
        WHERE difficulty IS NULL OR difficulty = ''
        """,
        """
        UPDATE practice_records
        SET dialogue_text = COALESCE(NULLIF(dialogue_text, ''), conversation_json, payload_json)
        WHERE dialogue_text IS NULL OR dialogue_text = ''
        """,
        """
        UPDATE practice_records
        SET round_count = COALESCE(round_count, 0)
        WHERE round_count IS NULL
        """,
        """
        UPDATE practice_records
        SET end_flag = COALESCE(end_flag, 0)
        WHERE end_flag IS NULL
        """,
        """
        UPDATE practice_records
        SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, CURRENT_TIMESTAMP)
        WHERE updated_at IS NULL OR updated_at = ''
        """,
        """
        UPDATE practice_eval_records
        SET practice_id = COALESCE(NULLIF(practice_id, ''), session_id)
        WHERE practice_id IS NULL OR practice_id = ''
        """,
        """
        UPDATE practice_eval_records
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE practice_eval_records
        SET highlights_json = COALESCE(NULLIF(highlights_json, ''), strengths_json, '[]')
        WHERE highlights_json IS NULL OR highlights_json = ''
        """,
        """
        UPDATE practice_eval_records
        SET problem_points_json = COALESCE(NULLIF(problem_points_json, ''), improvements_json, '[]')
        WHERE problem_points_json IS NULL OR problem_points_json = ''
        """,
        """
        UPDATE practice_eval_records
        SET concise_feedback = COALESCE(NULLIF(concise_feedback, ''), coach_summary)
        WHERE concise_feedback IS NULL OR concise_feedback = ''
        """,
        """
        UPDATE practice_eval_records
        SET source_workflow = COALESCE(NULLIF(source_workflow, ''), 'practice_evaluate')
        WHERE source_workflow IS NULL OR source_workflow = ''
        """,
        """
        UPDATE ability_update_records
        SET practice_id = COALESCE(NULLIF(practice_id, ''), session_id)
        WHERE practice_id IS NULL OR practice_id = ''
        """,
        """
        UPDATE ability_update_records
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE ability_update_records
        SET overall_score = COALESCE(overall_score, score)
        WHERE overall_score IS NULL
        """,
        """
        UPDATE ability_update_records
        SET source_workflow = COALESCE(NULLIF(source_workflow, ''), 'practice_update_ability')
        WHERE source_workflow IS NULL OR source_workflow = ''
        """,
        """
        UPDATE practice_records
        SET score_branch = COALESCE(NULLIF(score_branch, ''), 'practice')
        WHERE score_branch IS NULL OR score_branch = ''
        """,
        """
        UPDATE ability_update_records
        SET update_source = COALESCE(NULLIF(update_source, ''), 'practice')
        WHERE update_source IS NULL OR update_source = ''
        """,
        """
        UPDATE assistant_records
        SET user_id = COALESCE(NULLIF(user_id, ''), employee_id)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE assistant_records
        SET source_workflow_reply = COALESCE(NULLIF(source_workflow_reply, ''), CASE WHEN action = 'reply' THEN 'assistant1' ELSE source_workflow_reply END)
        WHERE source_workflow_reply IS NULL OR source_workflow_reply = ''
        """,
        """
        UPDATE assistant_records
        SET source_workflow_analyze = COALESCE(NULLIF(source_workflow_analyze, ''), CASE WHEN action = 'analyze' THEN 'assistant2' ELSE source_workflow_analyze END)
        WHERE source_workflow_analyze IS NULL OR source_workflow_analyze = ''
        """,
        """
        UPDATE dashboard_snapshots
        SET user_id = COALESCE(NULLIF(user_id, ''), created_by)
        WHERE user_id IS NULL OR user_id = ''
        """,
        """
        UPDATE dashboard_snapshots
        SET dashboard_result_json = COALESCE(NULLIF(dashboard_result_json, ''), payload_json)
        WHERE dashboard_result_json IS NULL OR dashboard_result_json = ''
        """,
        """
        UPDATE dashboard_snapshots
        SET source_workflow = COALESCE(NULLIF(source_workflow, ''), 'dashboard')
        WHERE source_workflow IS NULL OR source_workflow = ''
        """,
        """
        UPDATE query_records
        SET user_query = COALESCE(NULLIF(user_query, ''), query_text)
        WHERE user_query IS NULL OR user_query = ''
        """,
        """
        UPDATE query_records
        SET query_type = COALESCE(NULLIF(query_type, ''), parsed_intent)
        WHERE query_type IS NULL OR query_type = ''
        """,
        """
        UPDATE query_records
        SET query_result_json = COALESCE(NULLIF(query_result_json, ''), payload_json)
        WHERE query_result_json IS NULL OR query_result_json = ''
        """,
        """
        UPDATE query_records
        SET source_workflow_parse = COALESCE(NULLIF(source_workflow_parse, ''), CASE WHEN stage = 'parse' THEN 'query1' ELSE source_workflow_parse END)
        WHERE source_workflow_parse IS NULL OR source_workflow_parse = ''
        """,
        """
        UPDATE query_records
        SET source_workflow_summary = COALESCE(NULLIF(source_workflow_summary, ''), CASE WHEN stage = 'summarize' THEN 'query2' ELSE source_workflow_summary END)
        WHERE source_workflow_summary IS NULL OR source_workflow_summary = ''
        """,
        """
        UPDATE assessment_tasks
        SET module_code = COALESCE(NULLIF(module_code, ''), ''),
            exam_mode = COALESCE(NULLIF(exam_mode, ''), CASE WHEN task_type = 'paper_exam' THEN 'paper_exam' ELSE 'ai_blind_box_exam' END)
        WHERE module_code IS NULL
           OR exam_mode IS NULL OR exam_mode = ''
        """,
        """
        UPDATE assessment_tasks
        SET duration_minutes = COALESCE(duration_minutes, 60)
        WHERE duration_minutes IS NULL
        """,
        """
        UPDATE assessment_tasks
        SET score_visibility = COALESCE(NULLIF(score_visibility, ''), 'public'),
            publish_status = COALESCE(NULLIF(publish_status, ''), CASE WHEN status = 'active' THEN 'published' ELSE 'draft' END),
            target_scope_type = COALESCE(NULLIF(target_scope_type, ''), 'store'),
            paper_generation_status = COALESCE(NULLIF(paper_generation_status, ''), CASE WHEN task_type = 'paper_exam' THEN 'generated' ELSE 'not_needed' END),
            paper_review_version = COALESCE(paper_review_version, 0),
            paper_source_type = COALESCE(NULLIF(paper_source_type, ''), 'manual'),
            allow_retake = COALESCE(allow_retake, 1),
            max_attempts = COALESCE(max_attempts, 3),
            auto_submit_on_timeout = COALESCE(auto_submit_on_timeout, 1),
            created_by_role = COALESCE(NULLIF(created_by_role, ''), 'admin'),
            updated_at = COALESCE(NULLIF(updated_at, ''), created_at, CURRENT_TIMESTAMP)
        WHERE score_visibility IS NULL OR score_visibility = ''
           OR publish_status IS NULL OR publish_status = ''
           OR target_scope_type IS NULL OR target_scope_type = ''
           OR paper_generation_status IS NULL OR paper_generation_status = ''
           OR paper_review_version IS NULL
           OR paper_source_type IS NULL OR paper_source_type = ''
           OR allow_retake IS NULL
           OR max_attempts IS NULL
           OR auto_submit_on_timeout IS NULL
           OR created_by_role IS NULL OR created_by_role = ''
           OR updated_at IS NULL OR updated_at = ''
        """,
        """
        UPDATE assessment_records
        SET submit_status = COALESCE(NULLIF(submit_status, ''), CASE WHEN finished_at IS NULL THEN 'in_progress' ELSE 'submitted' END),
            score_visibility_snapshot = COALESCE(NULLIF(score_visibility_snapshot, ''), 'public'),
            is_score_visible_to_user = COALESCE(is_score_visible_to_user, 1),
            paper_answer_json = COALESCE(NULLIF(paper_answer_json, ''), '{}'),
            paper_result_json = COALESCE(NULLIF(paper_result_json, ''), '{}'),
            time_spent_seconds = COALESCE(time_spent_seconds, 0),
            is_timeout = COALESCE(is_timeout, 0),
            review_source = COALESCE(NULLIF(review_source, ''), 'ai_auto'),
            exam_mode_snapshot = COALESCE(NULLIF(exam_mode_snapshot, ''), 'ai_blind_box_exam'),
            task_version_snapshot = COALESCE(task_version_snapshot, 1)
        WHERE submit_status IS NULL OR submit_status = ''
           OR score_visibility_snapshot IS NULL OR score_visibility_snapshot = ''
           OR is_score_visible_to_user IS NULL
           OR paper_answer_json IS NULL OR paper_answer_json = ''
           OR paper_result_json IS NULL OR paper_result_json = ''
           OR time_spent_seconds IS NULL
           OR is_timeout IS NULL
           OR review_source IS NULL OR review_source = ''
           OR exam_mode_snapshot IS NULL OR exam_mode_snapshot = ''
           OR task_version_snapshot IS NULL
        """,
        """
        UPDATE training_cycles
        SET cycle_type = COALESCE(NULLIF(cycle_type, ''), 'onboarding'),
            stage_no = COALESCE(stage_no, 1),
            stage_name = COALESCE(NULLIF(stage_name, ''), '基础认知'),
            stage_status = COALESCE(NULLIF(stage_status, ''), CASE WHEN status = 'completed' THEN 'passed' ELSE 'active' END),
            plan_total_stages = COALESCE(plan_total_stages, 2),
            stage_pass_score = COALESCE(stage_pass_score, 80),
            unlock_mode = COALESCE(NULLIF(unlock_mode, ''), 'daily'),
            full_release_by_admin = COALESCE(full_release_by_admin, 0),
            previous_cycle_id = COALESCE(NULLIF(previous_cycle_id, ''), '')
        WHERE cycle_type IS NULL OR cycle_type = ''
           OR stage_no IS NULL
           OR stage_name IS NULL OR stage_name = ''
           OR stage_status IS NULL OR stage_status = ''
           OR plan_total_stages IS NULL
           OR stage_pass_score IS NULL
           OR unlock_mode IS NULL OR unlock_mode = ''
           OR full_release_by_admin IS NULL
           OR previous_cycle_id IS NULL
        """,
        """
        UPDATE cycle_daily_tasks
        SET task_source = COALESCE(NULLIF(task_source, ''), 'system'),
            release_status = COALESCE(NULLIF(release_status, ''), CASE WHEN status = 'locked' THEN 'locked' WHEN status = 'completed' THEN 'completed' ELSE 'released' END),
            ai_feedback = COALESCE(NULLIF(ai_feedback, ''), ''),
            next_action = COALESCE(NULLIF(next_action, ''), ''),
            evaluation_status = COALESCE(NULLIF(evaluation_status, ''), 'pending'),
            sort_order = COALESCE(sort_order, day_index)
        WHERE task_source IS NULL OR task_source = ''
           OR release_status IS NULL OR release_status = ''
           OR ai_feedback IS NULL
           OR next_action IS NULL
           OR evaluation_status IS NULL OR evaluation_status = ''
           OR sort_order IS NULL
        """,
    ]

    for sql in updates:
        conn.execute(sql)

    _seed_role_settings_sqlite(conn)


def _seed_role_settings_sqlite(conn: sqlite3.Connection) -> None:
    """初始化员工角色配置（INSERT OR IGNORE，与 personnel 模块 /api/role-settings 一致）。"""
    q = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='role_settings'"
    ).fetchone()
    if not q:
        return
    # Add is_enabled column if missing (migration for existing DBs)
    try:
        conn.execute("ALTER TABLE role_settings ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    now = datetime.now(timezone.utc).isoformat()
    seed_rows: list[tuple] = [
        ("admin", "管理员", "全域配置与人员管理；可创建各角色账号（含管理员）、维护门店主数据。", 10, 1, 0, 1),
        ("store_manager", "店长", "管理本门店人员；可创建导购、资深顾问等。", 20, 1, 1, 1),
        ("senior_consultant", "资深顾问", "业务骨干与带教角色。", 30, 1, 1, 1),
        ("trainee", "导购", "一线学习与陪练；新建员工时的默认角色。", 40, 1, 1, 1),
    ]
    for row in seed_rows:
        # row = (role_key, display_name, description, sort_order, is_system, assignable_by_manager, is_enabled)
        conn.execute(
            """
            INSERT OR IGNORE INTO role_settings (
                role_key, display_name, description, sort_order,
                is_system, assignable_by_manager, is_enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*row, now, now),
        )


def _ensure_sqlite_indexes(conn: sqlite3.Connection) -> None:
    _ensure_index(conn, table_name="users", index_name="idx_users_user_id", column_name="user_id", unique=True)
    _ensure_index(conn, table_name="stores", index_name="idx_stores_store_id", column_name="store_id", unique=True)
    _ensure_index(
        conn,
        table_name="employee_profiles",
        index_name="idx_employee_profiles_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="growth_plan_records",
        index_name="idx_growth_plan_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="learning_eval_records",
        index_name="idx_learning_eval_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="practice_records",
        index_name="idx_practice_records_practice_id",
        column_name="practice_id",
    )
    _ensure_index(
        conn,
        table_name="practice_records",
        index_name="idx_practice_records_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="practice_eval_records",
        index_name="idx_practice_eval_practice_id",
        column_name="practice_id",
    )
    _ensure_index(
        conn,
        table_name="practice_eval_records",
        index_name="idx_practice_eval_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="ability_update_records",
        index_name="idx_ability_update_practice_id",
        column_name="practice_id",
    )
    _ensure_index(
        conn,
        table_name="ability_update_records",
        index_name="idx_ability_update_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="assessment_tasks",
        index_name="idx_assessment_tasks_publish_status",
        column_name="publish_status",
    )
    _ensure_index(
        conn,
        table_name="assessment_records",
        index_name="idx_assessment_records_user_task",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="training_cycles",
        index_name="idx_training_cycles_user_stage",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="cycle_daily_tasks",
        index_name="idx_cycle_daily_tasks_cycle_day",
        column_name="cycle_id",
    )
    _ensure_index(
        conn,
        table_name="assessment_task_targets",
        index_name="idx_assessment_task_targets_task_id",
        column_name="task_id",
    )
    _ensure_index(
        conn,
        table_name="assessment_task_papers",
        index_name="idx_assessment_task_papers_task_id",
        column_name="task_id",
    )
    _ensure_index(
        conn,
        table_name="training_stage_reviews",
        index_name="idx_training_stage_reviews_cycle_id",
        column_name="cycle_id",
    )
    _ensure_index(
        conn,
        table_name="training_unlock_snapshots",
        index_name="idx_training_unlock_snapshots_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="training_unlock_snapshots",
        index_name="idx_training_unlock_snapshots_cycle_id",
        column_name="cycle_id",
    )
    _ensure_index(
        conn,
        table_name="module_index_snapshots",
        index_name="idx_module_index_snapshots_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="assistant_records",
        index_name="idx_assistant_records_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="review_notebook_masteries",
        index_name="idx_rn_masteries_user_id",
        column_name="user_id",
    )
    _ensure_index(
        conn,
        table_name="dashboard_snapshots",
        index_name="idx_dashboard_snapshots_store_id_v2",
        column_name="store_id",
    )
    _ensure_index(
        conn,
        table_name="query_records",
        index_name="idx_query_records_store_id",
        column_name="store_id",
    )


def ensure_database_initialized(sqlite_conn: sqlite3.Connection | None = None) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

        _log.info("database schema initialization started")

        # Import models lazily to avoid import cycle.
        import models  # noqa: F401

        Base.metadata.create_all(bind=engine)

        if IS_SQLITE:
            close_after = False
            conn = sqlite_conn
            if conn is None:
                SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30.0)
                conn.execute("PRAGMA busy_timeout = 30000")
                conn.execute("PRAGMA journal_mode = WAL")
                close_after = True
            try:
                _log.info("ensuring sqlite columns")
                _ensure_sqlite_columns(conn)
                _log.info("backfilling sqlite data")
                _backfill_sqlite_data(conn)
                _log.info("ensuring sqlite indexes")
                _ensure_sqlite_indexes(conn)
                # Lazy import to avoid module-load cycles.
                from knowledge_feedback_service import ensure_dispatch_table
                ensure_dispatch_table(conn)
                conn.commit()
            finally:
                if close_after and conn is not None:
                    conn.close()

        _SCHEMA_READY = True
        _log.info("database schema initialization complete")


def verify_database_connection() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        _log.debug("database connection verified")
        return True
    except Exception as e:
        _log.error("database connection failed: %s", e)
        return False
