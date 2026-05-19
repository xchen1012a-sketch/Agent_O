from __future__ import annotations

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utc_now


class RoleSetting(Base):
    """人员管理：员工角色展示名、说明及店长是否可分配（与 users.role 编码对应）。"""

    __tablename__ = "role_settings"

    role_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_system: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assignable_by_manager: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_by_role: Mapped[str] = mapped_column(String(50), nullable=False, default="")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="trainee")
    store_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Compatibility fields for existing auth/login flow.
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class Store(Base):
    __tablename__ = "stores"

    # Keep string PK for compatibility with the current seed data.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    store_name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    manager_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Compatibility fields.
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EmployeeProfile(Base):
    __tablename__ = "employee_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    job_title: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    self_intro: Mapped[str] = mapped_column(Text, nullable=False, default="")
    historical_learning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    initial_ability: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_product_knowledge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_compliance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_sales_communication_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_response_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Compatibility fields.
    employee_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    employee_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    position: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    store_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mentor_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")


class GrowthPlanRecord(Base):
    __tablename__ = "growth_plan_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    growth_plan_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plan_meta_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_workflow: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    plan_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    position: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    store_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mentor_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    ability_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_direction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class GrowthPlanJob(Base):
    __tablename__ = "growth_plan_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    requested_role: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    request_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GrowthTaskManualRecord(Base):
    __tablename__ = "growth_task_manual_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    task_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checked_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    checked_role: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LearningEvalRecord(Base):
    __tablename__ = "learning_eval_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    question_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    standard_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    knowledge_tag: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    answer_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mastery_level: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    weak_dimension: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    evaluation_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_workflow: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    evaluation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    learning_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    practice_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manager_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class PracticeRecord(Base):
    __tablename__ = "practice_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    practice_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    trainee_role: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    dialogue_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    round_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_flag: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    score_branch: Mapped[str] = mapped_column(String(32), nullable=False, default="practice")
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stage_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Compatibility fields.
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    scene_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    difficulty_level: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    user_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assistant_reply: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_focus_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    conversation_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class PracticeEvalRecord(Base):
    __tablename__ = "practice_eval_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    practice_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    level: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    weak_dimension: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    highlights_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    problem_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    improvement_advice: Mapped[str] = mapped_column(Text, nullable=False, default="")
    concise_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    followup_training: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_workflow: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stage_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    evaluation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    scene_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    score_breakdown_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    strengths_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    improvements_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    coach_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class AbilityUpdateRecord(Base):
    __tablename__ = "ability_update_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    practice_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    product_knowledge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    compliance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales_communication_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    focus_dimension: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    manager_tip: Mapped[str] = mapped_column(Text, nullable=False, default="")
    update_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_workflow: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stage_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    update_source: Mapped[str] = mapped_column(String(32), nullable=False, default="practice")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    update_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    evaluation_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ability_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    ability_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class AssistantRecord(Base):
    __tablename__ = "assistant_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    customer_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assistant_reply: Mapped[str] = mapped_column(Text, nullable=False, default="")
    matched_knowledge: Mapped[str] = mapped_column(Text, nullable=False, default="")
    question_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    knowledge_tag: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    weak_dimension: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    training_advice: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_workflow_reply: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_workflow_analyze: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    record_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    scene_hint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    analysis_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ReviewNotebookMastery(Base):
    __tablename__ = "review_notebook_masteries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    dimension: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    knowledge_tag: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    marked_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DashboardSnapshot(Base):
    __tablename__ = "dashboard_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    compliance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_practice_avg_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_high_risk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    core_weak_dimension: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    dashboard_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_workflow: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    snapshot_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    period: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    viewer_role: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class QueryRecord(Base):
    __tablename__ = "query_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    user_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    query_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    query_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_workflow_parse: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_workflow_summary: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Compatibility fields.
    record_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    query_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_intent: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class AssessmentTask(Base):
    __tablename__ = "assessment_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default="")
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    task_desc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    paper_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    publisher_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    target_scope: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    deadline: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pass_score: Mapped[float] = mapped_column(Float, nullable=False, default=85.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    exam_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="ai_blind_box_exam")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    score_visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    publish_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    target_scope_type: Mapped[str] = mapped_column(String(32), nullable=False, default="store")
    paper_generation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_needed")
    paper_review_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paper_source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    allow_retake: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    auto_submit_on_timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_notice_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_notice_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_role: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    published_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssessmentRecord(Base):
    __tablename__ = "assessment_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=0)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    employee_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    scenario_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_pass: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    finished_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    score_branch: Mapped[str] = mapped_column(String(32), nullable=False, default="assessment")
    cycle_day_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    score_visibility_snapshot: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    is_score_visible_to_user: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    paper_answer_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    paper_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    time_spent_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_source: Mapped[str] = mapped_column(String(32), nullable=False, default="ai_auto")
    exam_mode_snapshot: Mapped[str] = mapped_column(String(32), nullable=False, default="ai_blind_box_exam")
    task_version_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SalesPerformance(Base):
    __tablename__ = "sales_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    period_type: Mapped[str] = mapped_column(String(32), nullable=False, default="monthly")
    period_value: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    sales_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    complaint_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    refund_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_ticket: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attach_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    member_conversion_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    high_margin_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    target_sales_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    target_avg_ticket: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    target_conversion_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    target_attach_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    target_member_conversion_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    target_high_margin_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PerformanceAttributionReport(Base):
    __tablename__ = "performance_attribution_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    store_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    analysis_window: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TrainingCycle(Base):
    __tablename__ = "training_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    total_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    current_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    day_unlock_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    daily_plan_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    adaptive_state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[str] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[str] = mapped_column(String(64), nullable=True)
    cycle_type: Mapped[str] = mapped_column(String(32), nullable=False, default="onboarding")
    stage_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False, default="基础认知")
    stage_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    plan_total_stages: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    stage_pass_score: Mapped[float] = mapped_column(Float, nullable=False, default=80.0)
    unlock_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="daily")
    full_release_by_admin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    full_release_at: Mapped[str] = mapped_column(String(64), nullable=True)
    source_reset_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_cycle_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stage_started_at: Mapped[str] = mapped_column(String(64), nullable=True)
    stage_completed_at: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class CycleDailyTask(Base):
    __tablename__ = "cycle_daily_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    day_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_code: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    branch: Mapped[str] = mapped_column(String(32), nullable=False, default="practice")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="locked")
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    dimension_focus: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    route_page: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    completed_at: Mapped[str] = mapped_column(String(64), nullable=True)
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    task_source: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    release_status: Mapped[str] = mapped_column(String(32), nullable=False, default="locked")
    released_at: Mapped[str] = mapped_column(String(64), nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evaluation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AssessmentTaskTarget(Base):
    __tablename__ = "assessment_task_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, default="store")
    target_value: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssessmentTaskPaper(Base):
    __tablename__ = "assessment_task_papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    paper_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    paper_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ai_generated")
    paper_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    review_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class TrainingStageReview(Base):
    __tablename__ = "training_stage_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    stage_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    review_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_pass: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ability_delta_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    recommended_actions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TrainingUnlockSnapshot(Base):
    __tablename__ = "training_unlock_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    stage_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unlock_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    force_redirect_page: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    next_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module_unlocks_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    recommended_actions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    user_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manager_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    panel_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ModuleIndexSnapshot(Base):
    __tablename__ = "module_index_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    module_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    practice_index: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assessment_index: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    learning_index: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overall_index: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    snapshot_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


# ── Hermes self-evolution (Route B) · memory/evolution tables ──────────────


class AgentEvoEpisode(Base):
    """Raw reply, feedback, and correction event stream for the evo subsystem."""

    __tablename__ = "agent_evo_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_type: Mapped[str] = mapped_column(String(32), nullable=False, default="reply")
    module: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="assistant")
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    store_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    query_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    signal: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    correction_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compliance_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    parent_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AgentEvoSemantic(Base):
    """Fact-like semantic memory extracted from feedback and corrections."""

    __tablename__ = "agent_evo_semantic"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_episode_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    write_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentEvoReflective(Base):
    """Short-lived reflective lessons used by the agent before formal promotion."""

    __tablename__ = "agent_evo_reflective"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    lesson: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_episode_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    promoted_to_procedural_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_hit_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentEvoProcedural(Base):
    """Reusable procedural skill rules synthesized from repeated reflective lessons."""

    __tablename__ = "agent_evo_procedural"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="store")
    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    trigger_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    do_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    dont_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    example: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_reflective_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_episode_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    write_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    eval_case_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentEvoPromotion(Base):
    """Cross-scope promotion proposal queue."""

    __tablename__ = "agent_evo_promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_memory_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="")
    source_memory_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=0)
    current_scope: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default="")
    target_scope: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="pending")
    suggested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class AgentEvoReviewQueue(Base):
    """Human review queue for sensitive or cross-scope memory writes."""

    __tablename__ = "agent_evo_review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="semantic")
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="pending")
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEvoAuditLog(Base):
    """Audit stream for automated evo writes, promotion, rollback, and safety actions."""

    __tablename__ = "agent_evo_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentEvoMemoryHit(Base):
    """Retrieval hit log for semantic, reflective, and procedural memory injection."""

    __tablename__ = "agent_evo_memory_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, default="semantic")
    memory_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=0)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    query_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentEvoEvalCase(Base):
    """Safety-net regression case bound to agent memory."""

    __tablename__ = "agent_evo_eval_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="assistant")
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    must_contain: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    must_not_contain: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="global")
    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    severity: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    bound_memory_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="active")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AgentEvoEvalRun(Base):
    """Safety-net regression run record."""

    __tablename__ = "agent_evo_eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=0)
    module: Mapped[str] = mapped_column(String(32), nullable=False, default="assistant")
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="global")
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="passed")
    failed_checks: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    bound_memory_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentEvoAnomaly(Base):
    """Evo anomaly alert raised by the safety net."""

    __tablename__ = "agent_evo_anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anomaly_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    severity: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="open")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
