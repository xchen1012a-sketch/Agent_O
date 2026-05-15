from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

_log = logging.getLogger("jewelry_qipei.crud")

from models import (
    AbilityUpdateRecord,
    AssistantRecord,
    Store,
    DashboardSnapshot,
    EmployeeProfile,
    GrowthPlanRecord,
    LearningEvalRecord,
    PracticeEvalRecord,
    PracticeRecord,
    QueryRecord,
    User,
)
from schemas import (
    AbilityUpdateRecordCreate,
    AssistantRecordCreate,
    DashboardSnapshotCreate,
    EmployeeProfileUpsert,
    GrowthPlanRecordCreate,
    LearningEvalRecordCreate,
    PracticeEvalRecordCreate,
    PracticeRecordCreate,
    QueryRecordCreate,
    StoreCreate,
    UserCreate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:16]}"


class DataRepository:
    def __init__(self, db: Session):
        self.db = db

    # 1. 创建/查询用户
    def create_user(self, payload: UserCreate) -> User:
        existed = self.get_user_by_user_id(payload.user_id)
        if existed:
            _log.debug("create_user skipped (exists) user_id=%s", payload.user_id)
            return existed

        now = _now()
        username = (payload.username or payload.user_id).strip()
        display_name = (payload.display_name or payload.name or username).strip()
        user = User(
            user_id=payload.user_id.strip(),
            name=(payload.name or display_name).strip(),
            role=(payload.role or "trainee").strip(),
            store_id=(payload.store_id or "").strip(),
            created_at=now,
            updated_at=now,
            username=username,
            hashed_password=(payload.password_hash or "").strip(),
            display_name=display_name,
            phone=(payload.phone or "").strip(),
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        _log.info("create_user created user_id=%s username=%s role=%s", user.user_id, user.username, user.role)
        return user

    def get_user_by_user_id(self, user_id: str) -> User | None:
        result = self.db.query(User).filter(User.user_id == user_id.strip()).first()
        _log.debug("get_user_by_user_id user_id=%s found=%s", user_id, result is not None)
        return result

    # 2. 创建/查询门店
    def create_store(self, payload: StoreCreate) -> Store:
        existed = self.get_store_by_store_id(payload.store_id)
        if existed:
            _log.debug("create_store skipped (exists) store_id=%s", payload.store_id)
            return existed

        now = _now()
        sid = payload.store_id.strip()
        store = Store(
            id=sid,
            store_id=sid,
            store_name=payload.store_name.strip(),
            region=(payload.region or "").strip(),
            manager_name=(payload.manager_name or "").strip(),
            created_at=now,
            updated_at=now,
            name=payload.store_name.strip(),
            sort_order=0,
        )
        self.db.add(store)
        self.db.commit()
        self.db.refresh(store)
        _log.info("create_store created store_id=%s store_name=%s", store.store_id, store.store_name)
        return store

    def get_store_by_store_id(self, store_id: str) -> Store | None:
        result = self.db.query(Store).filter(Store.store_id == store_id.strip()).first()
        _log.debug("get_store_by_store_id store_id=%s found=%s", store_id, result is not None)
        return result

    # 3. 创建/更新员工画像
    def upsert_employee_profile(self, payload: EmployeeProfileUpsert) -> EmployeeProfile:
        uid = payload.user_id.strip()
        profile = self.db.query(EmployeeProfile).filter(EmployeeProfile.user_id == uid).first()
        now = _now()

        if profile is None:
            profile = EmployeeProfile(
                user_id=uid,
                employee_id=uid,
                created_at=now,
                updated_at=now,
            )
            self.db.add(profile)

        profile.job_title = (payload.job_title or "").strip()
        profile.self_intro = (payload.self_intro or "").strip()
        profile.historical_learning = (payload.historical_learning or "").strip()
        profile.initial_ability = (payload.initial_ability or "").strip()
        profile.current_product_knowledge_score = payload.current_product_knowledge_score
        profile.current_compliance_score = payload.current_compliance_score
        profile.current_sales_communication_score = payload.current_sales_communication_score
        profile.current_response_score = payload.current_response_score
        profile.current_overall_score = payload.current_overall_score
        profile.updated_at = now

        # Compatibility fields.
        if payload.job_title:
            profile.position = payload.job_title.strip()
        profile.employee_name = (payload.employee_name or profile.employee_name or "").strip()
        profile.store_id = (payload.store_id or profile.store_id or "").strip()
        profile.role = (payload.role or profile.role or "").strip()
        profile.source = (payload.source or profile.source or "manual").strip()

        self.db.commit()
        self.db.refresh(profile)
        _log.info("upsert_employee_profile user_id=%s store_id=%s", uid, profile.store_id)
        return profile

    # 4. 保存成长计划记录
    def save_growth_plan_record(self, payload: GrowthPlanRecordCreate) -> GrowthPlanRecord:
        now = _now()
        row = GrowthPlanRecord(
            user_id=payload.user_id.strip(),
            growth_plan_text=payload.growth_plan_text,
            plan_meta_json=payload.plan_meta_json,
            source_workflow=(payload.source_workflow or "growth").strip(),
            created_at=now,
            plan_id=_new_id("gp"),
            employee_id=payload.user_id.strip(),
            employee_name="",
            position="",
            store_id="",
            mentor_name="",
            ability_summary="",
            target_direction="",
            payload_json=payload.plan_meta_json,
            created_by=payload.user_id.strip(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_growth_plan_record user_id=%s plan_id=%s", row.user_id, row.plan_id)
        return row

    # 5. 保存学习评估记录
    def save_learning_eval_record(self, payload: LearningEvalRecordCreate) -> LearningEvalRecord:
        now = _now()
        row = LearningEvalRecord(
            user_id=payload.user_id.strip(),
            module_code=(payload.module_code or "").strip(),
            module_name=(payload.module_name or "").strip(),
            question_text=payload.question_text,
            user_answer=payload.user_answer,
            standard_answer=payload.standard_answer,
            knowledge_tag=(payload.knowledge_tag or "").strip(),
            answer_score=payload.answer_score,
            mastery_level=(payload.mastery_level or "").strip(),
            weak_dimension=(payload.weak_dimension or "").strip(),
            evaluation_text=payload.evaluation_text,
            source_workflow=(payload.source_workflow or "growth").strip(),
            created_at=now,
            evaluation_id=_new_id("le"),
            plan_id="",
            employee_id=payload.user_id.strip(),
            employee_name="",
            learning_summary=payload.question_text,
            practice_summary=payload.user_answer,
            manager_feedback=payload.standard_answer,
            score=payload.answer_score,
            payload_json=_json_text(
                {
                    "module_code": payload.module_code,
                    "module_name": payload.module_name,
                    "knowledge_tag": payload.knowledge_tag,
                }
            ),
            created_by=payload.user_id.strip(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_learning_eval_record user_id=%s evaluation_id=%s", row.user_id, row.evaluation_id)
        return row

    # 6. 保存陪练记录
    def save_practice_record(self, payload: PracticeRecordCreate) -> PracticeRecord:
        now = _now()
        row = PracticeRecord(
            practice_id=payload.practice_id.strip(),
            user_id=payload.user_id.strip(),
            scenario_type=(payload.scenario_type or "").strip(),
            difficulty=(payload.difficulty or "").strip(),
            trainee_role=(payload.trainee_role or "").strip(),
            dialogue_text=payload.dialogue_text,
            round_count=payload.round_count,
            end_flag=payload.end_flag,
            created_at=now,
            updated_at=now,
            session_id=payload.practice_id.strip(),
            employee_id=payload.user_id.strip(),
            scene_code=(payload.scenario_type or "").strip(),
            difficulty_level=(payload.difficulty or "").strip(),
            user_message="",
            assistant_reply="",
            suggested_response="",
            next_focus_json="[]",
            conversation_json="[]",
            payload_json=_json_text({"dialogue_text": payload.dialogue_text}),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_practice_record practice_id=%s user_id=%s", row.practice_id, row.user_id)
        return row

    # 7. 保存陪练评估记录
    def save_practice_eval_record(self, payload: PracticeEvalRecordCreate) -> PracticeEvalRecord:
        now = _now()
        row = PracticeEvalRecord(
            practice_id=payload.practice_id.strip(),
            user_id=payload.user_id.strip(),
            overall_score=payload.overall_score,
            level=(payload.level or "").strip(),
            risk_level=(payload.risk_level or "").strip(),
            weak_dimension=(payload.weak_dimension or "").strip(),
            highlights_json=payload.highlights_json,
            problem_points_json=payload.problem_points_json,
            improvement_advice=payload.improvement_advice,
            concise_feedback=payload.concise_feedback,
            followup_training=payload.followup_training,
            source_workflow=(payload.source_workflow or "practice").strip(),
            created_at=now,
            evaluation_id=_new_id("pe"),
            session_id=payload.practice_id.strip(),
            employee_id=payload.user_id.strip(),
            scene_code="",
            score_breakdown_json="{}",
            strengths_json=payload.highlights_json,
            improvements_json=payload.problem_points_json,
            coach_summary=payload.concise_feedback,
            payload_json=_json_text(
                {
                    "improvement_advice": payload.improvement_advice,
                    "followup_training": payload.followup_training,
                }
            ),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_practice_eval_record practice_id=%s user_id=%s score=%s", row.practice_id, row.user_id, row.overall_score)
        return row

    # 8. 保存能力更新记录
    def save_ability_update_record(self, payload: AbilityUpdateRecordCreate) -> AbilityUpdateRecord:
        now = _now()
        row = AbilityUpdateRecord(
            practice_id=payload.practice_id.strip(),
            user_id=payload.user_id.strip(),
            product_knowledge_score=payload.product_knowledge_score,
            compliance_score=payload.compliance_score,
            sales_communication_score=payload.sales_communication_score,
            response_score=payload.response_score,
            overall_score=payload.overall_score,
            risk_level=(payload.risk_level or "").strip(),
            focus_dimension=(payload.focus_dimension or "").strip(),
            manager_tip=payload.manager_tip,
            update_summary=payload.update_summary,
            source_workflow=(payload.source_workflow or "practice").strip(),
            created_at=now,
            update_id=_new_id("au"),
            session_id=payload.practice_id.strip(),
            evaluation_id="",
            employee_id=payload.user_id.strip(),
            score=payload.overall_score,
            ability_snapshot_json=_json_text(
                {
                    "product_knowledge": payload.product_knowledge_score,
                    "compliance": payload.compliance_score,
                    "sales_communication": payload.sales_communication_score,
                    "response": payload.response_score,
                }
            ),
            updated_tags_json="[]",
            ability_comment=payload.update_summary,
            payload_json=_json_text(
                {
                    "risk_level": payload.risk_level,
                    "focus_dimension": payload.focus_dimension,
                    "manager_tip": payload.manager_tip,
                }
            ),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_ability_update_record practice_id=%s user_id=%s overall=%s", row.practice_id, row.user_id, row.overall_score)
        return row

    # 9. 保存在岗助手记录
    def save_assistant_record(self, payload: AssistantRecordCreate) -> AssistantRecord:
        now = _now()
        row = AssistantRecord(
            user_id=payload.user_id.strip(),
            store_id=(payload.store_id or "").strip(),
            customer_question=payload.customer_question,
            assistant_reply=payload.assistant_reply,
            matched_knowledge=payload.matched_knowledge,
            question_type=(payload.question_type or "").strip(),
            knowledge_tag=(payload.knowledge_tag or "").strip(),
            risk_level=(payload.risk_level or "").strip(),
            weak_dimension=(payload.weak_dimension or "").strip(),
            training_advice=payload.training_advice,
            source_workflow_reply=(payload.source_workflow_reply or "").strip(),
            source_workflow_analyze=(payload.source_workflow_analyze or "").strip(),
            created_at=now,
            record_id=_new_id("as"),
            action="save",
            employee_id=payload.user_id.strip(),
            scene_hint="",
            analysis_json="{}",
            payload_json=_json_text(
                {
                    "matched_knowledge": payload.matched_knowledge,
                    "training_advice": payload.training_advice,
                }
            ),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_assistant_record user_id=%s record_id=%s", row.user_id, row.record_id)
        return row

    # 10. 保存看板快照
    def save_dashboard_snapshot(self, payload: DashboardSnapshotCreate) -> DashboardSnapshot:
        now = _now()
        row = DashboardSnapshot(
            store_id=payload.store_id.strip(),
            user_id=(payload.user_id or "").strip(),
            overall_score=payload.overall_score,
            compliance_score=payload.compliance_score,
            training_completion_rate=payload.training_completion_rate,
            recent_practice_avg_score=payload.recent_practice_avg_score,
            recent_high_risk_count=payload.recent_high_risk_count,
            core_weak_dimension=(payload.core_weak_dimension or "").strip(),
            dashboard_result_json=payload.dashboard_result_json,
            source_workflow=(payload.source_workflow or "dashboard").strip(),
            created_at=now,
            snapshot_id=_new_id("ds"),
            role_scope="",
            period="",
            viewer_role="",
            payload_json=payload.dashboard_result_json,
            created_by=(payload.user_id or "").strip(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_dashboard_snapshot store_id=%s snapshot_id=%s", row.store_id, row.snapshot_id)
        return row

    # 11. 保存一句话查询记录
    def save_query_record(self, payload: QueryRecordCreate) -> QueryRecord:
        now = _now()
        row = QueryRecord(
            store_id=(payload.store_id or "").strip(),
            user_query=payload.user_query,
            query_type=(payload.query_type or "").strip(),
            params_json=payload.params_json,
            query_result_json=payload.query_result_json,
            summary_text=payload.summary_text,
            source_workflow_parse=(payload.source_workflow_parse or "").strip(),
            source_workflow_summary=(payload.source_workflow_summary or "").strip(),
            created_at=now,
            record_id=_new_id("qr"),
            stage="save",
            employee_id="",
            query_text=payload.user_query,
            parsed_intent=(payload.query_type or "").strip(),
            payload_json=_json_text(
                {
                    "params_json": payload.params_json,
                    "query_result_json": payload.query_result_json,
                    "summary_text": payload.summary_text,
                }
            ),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        _log.info("save_query_record store_id=%s record_id=%s query_type=%s", row.store_id, row.record_id, row.query_type)
        return row
