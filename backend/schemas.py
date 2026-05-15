from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMBaseSchema(BaseModel):
    model_config = {"from_attributes": True}


class UserCreate(ORMBaseSchema):
    user_id: str
    name: str = ""
    role: str = "trainee"
    store_id: str = ""
    username: str = ""
    password_hash: str = ""
    display_name: str = ""
    phone: str = ""


class UserRead(ORMBaseSchema):
    id: int
    user_id: str
    name: str
    role: str
    store_id: str
    created_at: datetime
    updated_at: datetime


class StoreCreate(ORMBaseSchema):
    store_id: str
    store_name: str
    region: str = ""
    manager_name: str = ""


class StoreRead(ORMBaseSchema):
    id: str
    store_id: str
    store_name: str
    region: str
    manager_name: str
    created_at: datetime
    updated_at: datetime


class EmployeeProfileUpsert(ORMBaseSchema):
    user_id: str
    job_title: str = ""
    self_intro: str = ""
    historical_learning: str = ""
    initial_ability: str = ""
    current_product_knowledge_score: float | None = None
    current_compliance_score: float | None = None
    current_sales_communication_score: float | None = None
    current_response_score: float | None = None
    current_overall_score: float | None = None
    employee_name: str = ""
    store_id: str = ""
    role: str = ""
    source: str = ""


class GrowthPlanRecordCreate(ORMBaseSchema):
    user_id: str
    growth_plan_text: str = ""
    plan_meta_json: str = "{}"
    source_workflow: str = "growth"


class LearningEvalRecordCreate(ORMBaseSchema):
    user_id: str
    module_code: str = ""
    module_name: str = ""
    question_text: str = ""
    user_answer: str = ""
    standard_answer: str = ""
    knowledge_tag: str = ""
    answer_score: float | None = None
    mastery_level: str = ""
    weak_dimension: str = ""
    evaluation_text: str = ""
    source_workflow: str = "growth"


class PracticeRecordCreate(ORMBaseSchema):
    practice_id: str
    user_id: str
    scenario_type: str = ""
    difficulty: str = ""
    trainee_role: str = ""
    dialogue_text: str = ""
    round_count: int = 0
    end_flag: int = 0


class PracticeEvalRecordCreate(ORMBaseSchema):
    practice_id: str
    user_id: str
    overall_score: float | None = None
    level: str = ""
    risk_level: str = ""
    weak_dimension: str = ""
    highlights_json: str = "[]"
    problem_points_json: str = "[]"
    improvement_advice: str = ""
    concise_feedback: str = ""
    followup_training: str = ""
    source_workflow: str = "practice"


class AbilityUpdateRecordCreate(ORMBaseSchema):
    practice_id: str
    user_id: str
    product_knowledge_score: float | None = None
    compliance_score: float | None = None
    sales_communication_score: float | None = None
    response_score: float | None = None
    overall_score: float | None = None
    risk_level: str = ""
    focus_dimension: str = ""
    manager_tip: str = ""
    update_summary: str = ""
    source_workflow: str = "practice"


class AssistantRecordCreate(ORMBaseSchema):
    user_id: str
    store_id: str = ""
    customer_question: str = ""
    assistant_reply: str = ""
    matched_knowledge: str = ""
    question_type: str = ""
    knowledge_tag: str = ""
    risk_level: str = ""
    weak_dimension: str = ""
    training_advice: str = ""
    source_workflow_reply: str = ""
    source_workflow_analyze: str = ""


class DashboardSnapshotCreate(ORMBaseSchema):
    store_id: str
    user_id: str = ""
    overall_score: float | None = None
    compliance_score: float | None = None
    training_completion_rate: float | None = None
    recent_practice_avg_score: float | None = None
    recent_high_risk_count: int | None = None
    core_weak_dimension: str = ""
    dashboard_result_json: str = "{}"
    source_workflow: str = "dashboard"


class DashboardSnapshotRead(ORMBaseSchema):
    """Response schema for retrieving a single dashboard snapshot."""
    id: int
    snapshot_id: str
    store_id: str
    user_id: str
    overall_score: float | None = None
    compliance_score: float | None = None
    training_completion_rate: float | None = None
    recent_practice_avg_score: float | None = None
    recent_high_risk_count: int | None = None
    core_weak_dimension: str = ""
    dashboard_result_json: str = "{}"
    source_workflow: str = "dashboard"
    created_at: datetime
    role_scope: str = ""
    period: str = ""
    viewer_role: str = ""
    payload_json: str = "{}"
    created_by: str = ""


class DashboardSnapshotSummary(ORMBaseSchema):
    """Summary schema for dashboard snapshot list."""
    id: int
    snapshot_id: str
    store_id: str
    period: str = ""
    created_at: datetime
    overview: dict[str, Any] = Field(default_factory=dict)
    viewer_role: str = ""


class QueryRecordCreate(ORMBaseSchema):
    store_id: str = ""
    user_query: str = ""
    query_type: str = ""
    params_json: str = "{}"
    query_result_json: str = "{}"
    summary_text: str = ""
    source_workflow_parse: str = ""
    source_workflow_summary: str = ""


class QueryResult(ORMBaseSchema):
    count: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)


class AssistantReplyRequest(ORMBaseSchema):
    scene_input: str = Field("", description="在岗助手场景输入")
    history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="对话历史 [{role: 'user'|'assistant', content: str}]",
    )
    conversation_id: str = Field("", description="Dify conversation_id")


class AssistantReplyResponse(ORMBaseSchema):
    reply_script: str = Field("", description="回复话术")
    followup_question: str = Field("", description="追问问题（可空）")
    coach_tip: str = Field("", description="成交教练提示（可空）")
    voice_advice: str = Field("", description="语音播报建议（可空）")
    turn_feedback: dict[str, Any] | None = Field(None, description="逐轮反馈字段")
    conversation_id: str = Field("", description="Dify conversation_id")


class QaAskRequest(ORMBaseSchema):
    question: str = Field("", min_length=1, max_length=500, description="知识问答问题")
    history: list[dict[str, str]] = Field(
        default_factory=list,
        description="对话历史 [{role: 'user'|'assistant', content: str}]",
    )
    conversation_id: str = Field("", description="前端会话ID，可空")
    qa_chat_conversation_id: str = Field("", description="知识问答数字人 chat conversation_id，可空")


class TaskCreate(ORMBaseSchema):
    task_name: str
    task_type: str = "assessment"
    exam_mode: str = "ai_blind_box_exam"
    module_code: str = ""
    target_scope: str = ""
    target_scope_type: str = "store"
    store_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    deadline: datetime
    task_desc: str | None = None
    pass_score: float = 85.0
    paper_config_json: str | None = None
    duration_minutes: int = 60
    score_visibility: str = "public"
    allow_retake: bool = True
    max_attempts: int = 3
    started_notice_text: str | None = None
    submitted_notice_text: str | None = None


class TaskResponse(ORMBaseSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_name: str
    task_type: str
    task_desc: str | None = None
    module_code: str = ""
    paper_config_json: str | None = None
    publisher_id: str
    target_scope: str
    deadline: datetime
    pass_score: float
    status: str
    exam_mode: str
    duration_minutes: int
    score_visibility: str
    publish_status: str
    paper_generation_status: str
    published_at: datetime | None = None
    target_scope_type: str
    created_at: datetime


class TaskPaperGenerateReq(ORMBaseSchema):
    task_name: str
    task_desc: str = ""
    module_code: str = ""
    difficulty: str = "standard"
    question_count: int = 20
    question_mix: dict[str, Any] = Field(default_factory=dict)
    pass_score: float = 85.0


class TaskPaperReviewReq(ORMBaseSchema):
    task_id: int
    paper_version: int
    paper_config_json: str
    review_comment: str = ""


class TaskPublishReq(ORMBaseSchema):
    task_id: int
    target_scope_type: str = "store"
    store_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    duration_minutes: int = 60
    score_visibility: str = "public"
    deadline: datetime | None = None


class TaskArchiveReq(ORMBaseSchema):
    task_id: int


class TaskDeleteReq(ORMBaseSchema):
    task_id: int


class TaskRetakeReq(ORMBaseSchema):
    task_id: int


class AssessmentStartReq(ORMBaseSchema):
    task_id: int
    score_branch: str = "assessment"
    cycle_day_index: int | None = None


class AssessmentChatReq(ORMBaseSchema):
    record_id: int
    message: str
    conversation_id: str | None = None
    score_branch: str = "assessment"
    cycle_day_index: int | None = None


class AssessmentFinishReq(ORMBaseSchema):
    record_id: int
    score: float
    is_pass: int
    comment: str | None = None


class AssessmentSubmitPaperReq(ORMBaseSchema):
    record_id: int
    answers: dict[str, Any] = Field(default_factory=dict)
