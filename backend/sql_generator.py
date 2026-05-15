"""Local SQL generation from natural language (keyword-based fallback)."""
from __future__ import annotations

import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

_log = logging.getLogger("jewelry_qipei.sql_generator")

# ── Table descriptions + columns for keyword matching ───────────────────
TABLE_SCHEMA_HINTS: dict[str, dict[str, Any]] = {
    "users": {
        "keywords": [
            "职位", "职务", "岗位", "用户", "账号", "谁是", "在职",
            "店长", "顾问", "新员工", "管理员", "资深", "新人",
            "多少人", "几个人", "人数", "人员名单", "花名册",
            "属于哪个门店", "在哪个门店", "门店归属",
        ],
        "description": "系统用户（用户名、显示名、角色/职位、所属门店）",
        "columns": [
            "user_id", "username", "display_name", "role",
            "store_id", "created_at",
        ],
        "has_store_id": True,
    },
    "employee_profiles": {
        "keywords": [
            "员工", "人员", "档案", "能力",
            "产品知识", "合规", "销售沟通", "应变", "综合分",
            "能力分", "员工档案", "档案分",
        ],
        "description": "员工档案（姓名、岗位、门店、能力分）",
        "columns": [
            "employee_id", "employee_name", "position", "store_id", "role",
            "current_product_knowledge_score", "current_compliance_score",
            "current_sales_communication_score", "current_response_score",
            "current_overall_score",
        ],
        "has_store_id": True,
    },
    "practice_eval_records": {
        "keywords": ["陪练", "评估", "评分", "成绩", "练习", "陪练成绩", "陪练评估", "得分", "综合得分"],
        "description": "陪练评估记录（得分、等级、风险、弱项）",
        "columns": [
            "employee_id", "practice_id", "overall_score", "level",
            "risk_level", "weak_dimension", "improvement_advice",
            "concise_feedback", "created_at",
        ],
        "has_store_id": False,
    },
    "practice_records": {
        "keywords": ["对话", "陪练对话", "场景", "难度", "回合", "对话记录"],
        "description": "陪练对话记录（场景、难度、对话文本）",
        "columns": [
            "practice_id", "user_id", "scenario_type", "difficulty",
            "round_count", "end_flag", "created_at",
        ],
        "has_store_id": False,
    },
    "learning_eval_records": {
        "keywords": [
            "学习", "培训", "课程", "知识点", "掌握",
            "学习评估", "培训记录", "学习记录",
        ],
        "description": "学习评估记录（培训模块、知识点、得分）",
        "columns": [
            "employee_id", "module_name", "knowledge_tag", "answer_score",
            "mastery_level", "weak_dimension", "created_at",
        ],
        "has_store_id": False,
    },
    "assistant_records": {
        "keywords": [
            "在岗助手", "助手", "客户问题", "提问", "咨询",
            "助手记录", "在岗", "客户咨询",
        ],
        "description": "在岗助手使用记录（客户问题、知识标签）",
        "columns": [
            "user_id", "store_id", "customer_question", "question_type",
            "knowledge_tag", "risk_level", "weak_dimension", "created_at",
        ],
        "has_store_id": True,
    },
    "ability_update_records": {
        "keywords": ["能力更新", "能力变化", "能力分变化", "能力提升", "能力下降"],
        "description": "能力更新记录（各项能力分变化）",
        "columns": [
            "user_id", "practice_id", "product_knowledge_score",
            "compliance_score", "sales_communication_score",
            "response_score", "overall_score", "risk_level",
            "focus_dimension", "created_at",
        ],
        "has_store_id": False,
    },
    "dashboard_snapshots": {
        "keywords": [
            "仪表盘", "看板", "概览", "总览", "大盘",
            "培训完成率", "整体情况",
        ],
        "description": "仪表盘快照（综合分、合规分、培训完成率）",
        "columns": [
            "store_id", "user_id", "overall_score", "compliance_score",
            "training_completion_rate", "recent_practice_avg_score",
            "recent_high_risk_count", "core_weak_dimension", "created_at",
        ],
        "has_store_id": True,
    },
    "stores": {
        "keywords": [
            "门店", "店铺", "分店", "区域", "哪些店", "几家店",
            "几个店", "有几个店", "有几家店", "有几个门店", "门店数量", "店数量",
            "店长", "负责人", "门店负责人", "门店店长",
        ],
        "description": "门店信息（名称、区域、店长）",
        "columns": ["store_id", "store_name", "region", "manager_name"],
        "has_store_id": False,
    },
    "growth_plan_records": {
        "keywords": ["成长计划", "发展计划", "培养", "成长", "学习计划"],
        "description": "成长计划记录（能力总结、目标方向）",
        "columns": [
            "employee_id", "employee_name", "store_id",
            "ability_summary", "target_direction", "created_at",
        ],
        "has_store_id": True,
    },
    "role_settings": {
        "keywords": [
            "角色", "角色设置", "角色列表", "哪些角色",
            "职位", "职务", "岗位",
            "有什么职位", "有哪些职位", "职位列表",
        ],
        "description": "角色设置（角色名、说明）",
        "columns": ["role_key", "display_name", "description", "is_enabled"],
        "has_store_id": False,
    },
    "query_records": {
        "keywords": ["查询记录", "查询历史", "历史查询"],
        "description": "查询历史记录",
        "columns": [
            "store_id", "query_text", "query_type", "summary_text", "created_at",
        ],
        "has_store_id": True,
    },
    # ── newly added for full-system coverage ──
    "sales_performance": {
        "keywords": [
            "销售额", "业绩", "销售", "成交", "客单价", "转化率",
            "退货率", "投诉率", "连带率", "会员转化", "高毛利",
            "目标", "指标", "KPI", "完成率", "销售数据",
        ],
        "description": "销售业绩数据（销售额、订单数、转化率、客单价等）",
        "columns": [
            "user_id", "store_id", "period_type", "period_value",
            "sales_amount", "order_count", "conversion_rate",
            "complaint_rate", "refund_rate", "avg_ticket",
            "attach_rate", "member_conversion_rate", "high_margin_share",
            "target_sales_amount", "target_avg_ticket",
            "target_conversion_rate", "target_attach_rate",
            "target_member_conversion_rate", "target_high_margin_share",
            "created_at",
        ],
        "has_store_id": True,
    },
    "performance_attribution_reports": {
        "keywords": [
            "绩效归因", "归因分析", "绩效分析", "业绩分析报告",
            "绩效报告", "归因报告", "分析报告",
        ],
        "description": "绩效归因分析报告（分析窗口、摘要、标签）",
        "columns": [
            "user_id", "store_id", "analysis_window",
            "summary_json", "report_markdown", "tags_json", "created_at",
        ],
        "has_store_id": True,
    },
    "assessment_tasks": {
        "keywords": [
            "考试", "考核任务", "试卷", "考题", "测试任务",
            "盲盒考核", "考核安排", "考试安排",
        ],
        "description": "考核任务定义（任务名、类型、范围、及格分）",
        "columns": [
            "task_name", "task_type", "task_desc", "publisher_id",
            "target_scope", "deadline", "pass_score", "status", "created_at",
        ],
        "has_store_id": False,
    },
    "assessment_records": {
        "keywords": [
            "考核成绩", "考试成绩", "考核记录", "考试记录",
            "通过", "不及格", "及格", "考了多少",
        ],
        "description": "考核/考试成绩记录（分数、是否通过、评语）",
        "columns": [
            "task_id", "user_id", "employee_name", "conversation_id",
            "scenario_id", "score", "is_pass", "comment",
            "attempt_no", "finished_at",
        ],
        "has_store_id": False,
    },
    "training_cycles": {
        "keywords": [
            "训练周期", "培训周期", "成长周期", "周期进度", "当前阶段",
            "阶段完成", "阶段状态", "周期计划", "周期列表", "周期阶段",
            "训练阶段", "培训阶段", "周期及格分", "解锁模式",
        ],
        "description": "训练周期（阶段名、状态、解锁模式、及格分）",
        "columns": [
            "cycle_id", "user_id", "cycle_type", "stage_no", "stage_name",
            "stage_status", "plan_total_stages", "stage_pass_score",
            "unlock_mode", "stage_started_at", "stage_completed_at",
            "created_at",
        ],
        "has_store_id": False,
    },
    "cycle_daily_tasks": {
        "keywords": [
            "每日任务", "训练任务", "培训任务", "周期任务",
            "任务进度", "未完成任务", "任务完成状态", "今日任务",
            "训练每日任务", "培训每日任务", "任务评估",
        ],
        "description": "周期每日任务（模块、状态、得分、反馈）",
        "columns": [
            "cycle_id", "user_id", "day_index", "module_code", "module_name",
            "task_source", "release_status", "status", "ai_score",
            "ai_feedback", "evaluation_status", "created_at",
        ],
        "has_store_id": False,
    },
    "exam_results": {
        "keywords": [
            "考试结果", "考试明细", "考试评分", "测评结果",
            "考试得分", "试卷得分", "阅卷结果",
        ],
        "description": "考试结果（得分、评级、提交时间）",
        "columns": [
            "user_id", "exam_id", "exam_mode", "submit_time",
            "grading_result", "created_at",
        ],
        "has_store_id": False,
    },
    "study_progress": {
        "keywords": [
            "学习进度", "学习完成", "课程完成", "学习记录",
            "学习状态", "进度完成率", "学习情况",
        ],
        "description": "学习进度记录（类型、摘要、完成时间）",
        "columns": [
            "user_id", "result_type", "content_summary",
            "complete_time", "created_at",
        ],
        "has_store_id": False,
    },
    "growth_task_manual_records": {
        "keywords": [
            "补记", "手动完成", "手动标记", "管理层补记",
            "任务补记", "人工确认",
        ],
        "description": "成长计划任务人工补记记录",
        "columns": [
            "plan_id", "employee_id", "task_code", "status",
            "note", "checked_by", "checked_role", "created_at", "updated_at",
        ],
        "has_store_id": False,
    },
}

BLOCKED_RESULT_COLUMNS: frozenset[str] = frozenset({
    "hashed_password",
    "phone",
})

RUNTIME_TABLE_ALIASES: dict[str, list[str]] = {
    "audit_logs": ["审计日志", "审查记录", "操作日志", "系统日志", "日志"],
    "kb_documents": ["知识库文档", "知识库", "知识文档", "文档库", "知识资料"],
    "kb_dataset_bindings": ["知识库绑定", "知识库数据集", "数据集绑定", "知识库配置"],
    "theory_learning_documents": ["理论学习文档", "学习资料", "课程资料", "资料文档", "理论资料"],
    "question_bank_questions": ["题库", "题目", "试题", "题库题目", "问题库"],
    "assessment_task_papers": ["考试试卷", "考核试卷", "试卷版本", "考试卷"],
    "assessment_task_targets": ["考试对象", "考核对象", "任务对象", "考试范围"],
    "growth_plan_jobs": ["成长计划任务", "成长计划生成", "生成任务", "计划任务"],
    "mentor_histories": ["导师对话", "导师历史", "辅导历史", "对话历史"],
    "training_stage_reviews": ["阶段复盘", "阶段评审", "训练复盘", "培训复盘"],
    "training_unlock_snapshots": ["解锁快照", "训练解锁", "培训解锁", "模块解锁"],
    "training_cycles": ["训练周期", "培训周期", "成长周期", "周期计划"],
    "cycle_daily_tasks": ["每日任务", "训练任务", "培训任务", "周期任务", "任务进度"],
    "module_index_snapshots": ["模块指数", "能力指数", "模块快照", "指数快照"],
    "exam_papers": ["考试内容", "生成试卷", "考试题", "试卷内容"],
    "exam_results": ["考试结果", "考试评分", "测评结果", "考试明细"],
    "study_progress": ["学习进度", "学习完成", "学习历史", "学习记录"],
    "sales_data": ["销售明细", "销售数据", "销售流水", "销售记录", "成交明细"],
    "performance_attribution_reports": ["绩效归因", "业绩归因", "归因报告", "绩效报告"],
}


def _default_db_path() -> Path:
    try:
        from database import SQLITE_DB_PATH

        return Path(SQLITE_DB_PATH)
    except Exception:
        return Path(__file__).with_name("jewelry_qipei.db")


def _safe_sql_literal(value: Any) -> str:
    return str(value).replace("'", "''")


def _runtime_keywords(table_name: str, columns: list[str]) -> list[str]:
    words: list[str] = []
    words.extend(RUNTIME_TABLE_ALIASES.get(table_name, []))
    words.append(table_name)
    words.append(table_name.replace("_", ""))
    for part in table_name.split("_"):
        if len(part) >= 3:
            words.append(part)
    for col in columns:
        if col in BLOCKED_RESULT_COLUMNS:
            continue
        words.append(col)
        words.append(col.replace("_", ""))
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        w = str(word or "").strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


@lru_cache(maxsize=1)
def get_table_schema_hints() -> dict[str, dict[str, Any]]:
    """Return static hints plus runtime SQLite tables so query can cover the whole app."""
    hints: dict[str, dict[str, Any]] = {
        name: {
            **info,
            "keywords": list(info.get("keywords", [])),
            "columns": list(info.get("columns", [])),
        }
        for name, info in TABLE_SCHEMA_HINTS.items()
    }
    db_path = _default_db_path()
    if not db_path.exists():
        return hints

    try:
        conn = sqlite3.connect(db_path)
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table_name,) in table_rows:
            cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            safe_cols = [c for c in cols if c not in BLOCKED_RESULT_COLUMNS]
            if not safe_cols:
                continue
            existing = hints.get(table_name, {})
            keywords = list(existing.get("keywords", []))
            keywords.extend(_runtime_keywords(table_name, safe_cols))
            deduped_keywords = list(dict.fromkeys([kw for kw in keywords if kw]))
            hints[table_name] = {
                "keywords": deduped_keywords,
                "description": existing.get("description") or f"系统数据表（{table_name}）",
                "columns": safe_cols[:24],
                "has_store_id": bool(existing.get("has_store_id", "store_id" in safe_cols)),
            }
    except Exception as exc:
        _log.debug("load runtime schema hints failed: %s", exc)
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return hints


def _store_name_aliases(name: str) -> list[str]:
    text = str(name or "").strip()
    if not text:
        return []
    aliases = [text]
    if "·" in text:
        aliases.append(text.split("·", 1)[1].strip())
    compact = text.replace("珠宝", "").replace("华璟", "").replace("·", "").strip()
    if compact and compact not in aliases:
        aliases.append(compact)
    return list(dict.fromkeys([alias for alias in aliases if alias]))


@lru_cache(maxsize=1)
def get_runtime_entity_refs() -> dict[str, Any]:
    refs: dict[str, Any] = {"stores": [], "users": []}
    db_path = _default_db_path()
    if not db_path.exists():
        return refs
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT store_id, store_name FROM stores ORDER BY store_id").fetchall():
            store_id = str(row["store_id"] or "").strip()
            store_name = str(row["store_name"] or "").strip()
            if not store_id or not store_name:
                continue
            refs["stores"].append({
                "store_id": store_id,
                "store_name": store_name,
                "aliases": _store_name_aliases(store_name),
            })
        for row in conn.execute("SELECT username, display_name, store_id FROM users ORDER BY id").fetchall():
            username = str(row["username"] or "").strip()
            display_name = str(row["display_name"] or "").strip()
            user_store_id = str(row["store_id"] or "").strip()
            aliases = [alias for alias in [username, display_name] if alias]
            if not aliases:
                continue
            refs["users"].append({
                "username": username,
                "display_name": display_name,
                "store_id": user_store_id,
                "aliases": list(dict.fromkeys(aliases)),
            })
    except Exception as exc:
        _log.debug("load runtime entity refs failed: %s", exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    fallback_stores = [
        {
            "store_id": "HJ-SZ-001",
            "store_name": "深圳万象城店",
            "aliases": ["深圳万象城店", "万象城店", "深圳万象城"],
        },
        {
            "store_id": "HJ-GZ-001",
            "store_name": "广州天河城店",
            "aliases": ["广州天河城店", "天河城店", "广州天河城"],
        },
    ]
    known_store_ids = {str(item.get("store_id") or "") for item in refs["stores"] if isinstance(item, dict)}
    for item in fallback_stores:
        if item["store_id"] not in known_store_ids:
            refs["stores"].append(item)
    fallback_users = [
        {
            "username": "SM001",
            "display_name": "周雅雯",
            "store_id": "HJ-SZ-001",
            "aliases": ["SM001", "周雅雯"],
        },
    ]
    known_user_keys = {
        (str(item.get("username") or ""), str(item.get("display_name") or ""))
        for item in refs["users"]
        if isinstance(item, dict)
    }
    for item in fallback_users:
        key = (item["username"], item["display_name"])
        if key not in known_user_keys:
            refs["users"].append(item)
    return refs


def _match_store_ids(query_text: str) -> list[str]:
    q = _normalize_query_text(query_text)
    matched: list[str] = []
    for item in get_runtime_entity_refs().get("stores", []):
        aliases = item.get("aliases") or []
        if any(_normalize_query_text(alias) in q for alias in aliases if alias):
            store_id = str(item.get("store_id") or "").strip()
            if store_id and store_id not in matched:
                matched.append(store_id)
    return matched


def _match_user_aliases(query_text: str) -> list[str]:
    q = _normalize_query_text(query_text)
    matched: list[str] = []
    for item in get_runtime_entity_refs().get("users", []):
        aliases = item.get("aliases") or []
        for alias in aliases:
            normalized_alias = _normalize_query_text(alias)
            if normalized_alias and normalized_alias in q:
                matched.append(str(alias).strip())
                break
    return list(dict.fromkeys([name for name in matched if name]))


def _is_system_catalog_query(q: str) -> bool:
    compact = re.sub(r"\s+", "", q)
    if not compact:
        return False
    catalog_words = ("有哪些数据", "所有数据", "全部数据", "数据目录", "系统数据", "能查什么", "可以查什么")
    return ("系统" in compact or "数据" in compact) and any(word in compact for word in catalog_words)


def _system_catalog_sql() -> str:
    rows: list[str] = []
    for table_name, info in get_table_schema_hints().items():
        if table_name.startswith("_"):
            continue
        cols = ", ".join(list(info.get("columns") or [])[:12])
        rows.append(
            "SELECT "
            f"'{_safe_sql_literal(table_name)}' AS table_name, "
            f"'{_safe_sql_literal(info.get('description') or table_name)}' AS description, "
            f"'{_safe_sql_literal(cols)}' AS fields"
        )
    if not rows:
        return "SELECT '暂无' AS table_name, '未发现可查询数据表' AS description, '' AS fields"
    return " UNION ALL ".join(rows[:100])


def _days_ago(text: str) -> str | None:
    """Parse time range keywords into ISO date string."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    low = text.lower()
    if "今天" in low or "今日" in low:
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if "近7天" in low or "最近7天" in low or "最近一周" in low or "近一周" in low:
        return (now - _dt.timedelta(days=7)).isoformat()
    if "近30天" in low or "最近30天" in low or "最近一个月" in low or "近一个月" in low:
        return (now - _dt.timedelta(days=30)).isoformat()
    if "近90天" in low or "最近90天" in low or "近三个月" in low or "最近三个月" in low:
        return (now - _dt.timedelta(days=90)).isoformat()
    if "本月" in low or "这个月" in low:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    # Default: 30 days
    return (now - _dt.timedelta(days=30)).isoformat()


_NORMALIZE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("本店", "门店"),
    ("店铺", "门店"),
    ("分店", "门店"),
    ("负责人", "店长"),
    ("门店负责人", "门店店长"),
    ("店有几个", "有几个门店"),
    ("几个店", "几个门店"),
    ("几家店", "几家门店"),
    ("门店总数", "门店数量"),
    ("店总数", "门店数量"),
    ("有什么", "有哪些"),
    ("有哪些数据", "数据有哪些"),
)

_SEMANTIC_TABLE_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"(几|多少).*(店|门店)|(店|门店).*(几|多少)"), ("stores",)),
    (re.compile(r"(店长|负责人).*(谁|哪些|哪几位|都有谁)|(谁|哪些).*(店长|负责人)"), ("stores", "users", "employee_profiles")),
    (re.compile(r"(门店).*(多少).*(员工|人员)|(员工|人员).*(属于|在哪个).*(门店)"), ("users", "stores", "employee_profiles")),
    (re.compile(r"(职位|岗位|角色|职务|角色列表)"), ("role_settings", "users")),
    (re.compile(r"(用户|账号|花名册|人员名单|员工名单)"), ("users", "employee_profiles")),
    (re.compile(r"(员工档案|能力分|能力得分|综合分)"), ("employee_profiles",)),
    (re.compile(r"(陪练|练习|实战)"), ("practice_eval_records", "practice_records")),
    (re.compile(r"(学习|培训|课程|知识点掌握)"), ("learning_eval_records",)),
    (re.compile(r"(在岗助手|客户问题|咨询记录|提问记录)"), ("assistant_records",)),
    (re.compile(r"(能力更新|能力变化|能力提升|能力下降)"), ("ability_update_records",)),
    (re.compile(r"(仪表盘|看板|总览|大盘)"), ("dashboard_snapshots",)),
    (re.compile(r"(成长计划|发展计划|培养计划)"), ("growth_plan_records",)),
    (re.compile(r"(查询记录|查询历史|历史查询)"), ("query_records",)),
    (re.compile(r"(销售|业绩|KPI|指标|完成率|客单价|转化率)"), ("sales_performance",)),
    (re.compile(r"(销售明细|销售流水|销售记录|成交明细)"), ("sales_data",)),
    (re.compile(r"(归因分析|绩效归因|分析报告|绩效报告)"), ("performance_attribution_reports",)),
    (re.compile(r"(考试安排|考核任务|试卷|考题|盲盒考核)"), ("assessment_tasks",)),
    (re.compile(r"(考试成绩|考核成绩|考核记录|考试记录|通过率)"), ("assessment_records",)),
    (re.compile(r"(训练周期|培训周期|周期进度|当前阶段|阶段状态|训练阶段|培训阶段)"), ("training_cycles",)),
    (re.compile(r"(每日任务|训练任务|培训任务|任务进度|未完成.*任务|任务.*完成|任务评估)"), ("cycle_daily_tasks",)),
    (re.compile(r"(考试结果|考试明细|考试评分|测评结果|试卷得分|阅卷)"), ("exam_results",)),
    (re.compile(r"(学习进度|学习完成|课程完成|学习状态|学习情况)"), ("study_progress",)),
    (re.compile(r"(手动补记|人工补记|管理层补记|人工确认)"), ("growth_task_manual_records",)),
)


def _normalize_query_text(text: str) -> str:
    normalized = text.strip()
    for src, dst in _NORMALIZE_REPLACEMENTS:
        normalized = normalized.replace(src, dst)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _query_variants(text: str) -> list[str]:
    base = text.strip()
    if not base:
        return []
    variants: list[str] = [base]
    normalized = _normalize_query_text(base)
    if normalized and normalized not in variants:
        variants.append(normalized)
    return variants


def _detect_tables(text: str) -> list[str]:
    """Match natural language keywords to table names."""
    variants = _query_variants(text)
    if not variants:
        return []

    schema_hints = get_table_schema_hints()
    scored: dict[str, int] = {}
    for variant in variants:
        for tbl, info in schema_hints.items():
            for kw in info["keywords"]:
                if kw and kw in variant:
                    # Longer keyword matches are more specific → higher score
                    bonus = max(3, len(kw))
                    scored[tbl] = scored.get(tbl, 0) + bonus

        # Bonus: when query mentions "门店", prefer tables with store_id
        if "门店" in variant:
            for tbl, info in schema_hints.items():
                if info.get("has_store_id"):
                    scored[tbl] = scored.get(tbl, 0) + 2

        for pattern, tables in _SEMANTIC_TABLE_RULES:
            if pattern.search(variant):
                for tbl in tables:
                    scored[tbl] = scored.get(tbl, 0) + 2

    # Sort by match count descending so the best-matching table comes first
    matched = sorted(scored, key=lambda t: -scored[t])

    # Heuristic: prefer practice_eval_records over employee_profiles for score queries
    # (employee_profiles.current_*_score is often NULL; practice_eval_records has real data)
    if {"employee_profiles", "practice_eval_records"}.issubset(set(matched)):
        q_lower = text.lower()
        if any(w in q_lower for w in ["得分", "分数", "评分", "成绩", "分低", "分高"]):
            # Move practice_eval_records before employee_profiles
            new_matched = []
            pushed = False
            for t in matched:
                if t == "employee_profiles" and not pushed:
                    new_matched.append("practice_eval_records")
                    pushed = True
                    continue
                if t == "practice_eval_records" and pushed:
                    new_matched.append("employee_profiles")
                    continue
                new_matched.append(t)
            if not pushed:
                new_matched = matched
            matched = new_matched

    # If nothing matched, try broad fallback
    if not matched:
        normalized_text = variants[-1]
        if any(w in normalized_text for w in ["多少", "数量", "几条", "几人", "列表", "有哪些", "所有", "全部"]):
            matched.append("users")
        elif any(w in normalized_text for w in ["销售", "业绩", "卖了", "成交"]):
            matched.append("sales_performance")
        elif any(w in normalized_text for w in ["考试", "考核", "测验"]):
            matched.append("assessment_records")
        elif any(w in normalized_text for w in ["陪练", "练习"]):
            matched.append("practice_eval_records")
        elif any(w in normalized_text for w in ["培训", "学习", "课程"]):
            matched.append("learning_eval_records")
    return matched


def _training_completion_sql(
    q: str, store_id: str, allow_global_scope: bool
) -> tuple[str | None, list[str]]:
    """Generate SQL to compute real training completion rate from cycle_daily_tasks."""
    conditions: list[str] = []
    params: list[str] = []

    # Filter fake stores
    conditions.append("u.store_id NOT LIKE 'STORE_RISK_%'")
    conditions.append("u.store_id NOT LIKE 'STORE_LINK_%'")
    conditions.append("u.store_id NOT LIKE 'STORE_PERM_%'")

    # Scope
    if not allow_global_scope and store_id:
        conditions.append("u.store_id = ?")
        params.append(store_id)

    # Time range
    since = _days_ago(q)
    if since:
        conditions.append("c.created_at >= ?")
        params.append(since)

    where = " AND ".join(conditions)
    order_dir = "ASC" if any(w in q for w in ["最低", "最差", "不好", "最低的", "最差的"]) else "DESC"
    top_match = re.search(r"(?:前|top)\s*(\d{1,2})", q, re.IGNORECASE)
    limit = int(top_match.group(1)) if top_match else 20

    sql = (
        f"SELECT u.store_id, s.store_name, "
        f"COUNT(*) AS total_tasks, "
        f"SUM(CASE WHEN c.status='completed' THEN 1 ELSE 0 END) AS completed_tasks, "
        f"ROUND(SUM(CASE WHEN c.status='completed' THEN 1.0 ELSE 0.0 END)*100.0/MAX(1,COUNT(*)), 1) AS training_completion_rate, "
        f"COUNT(DISTINCT c.user_id) AS employee_count "
        f"FROM cycle_daily_tasks c "
        f"JOIN users u ON CAST(u.id AS TEXT) = CAST(c.user_id AS TEXT) "
        f"LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        f"GROUP BY u.store_id "
        f"ORDER BY training_completion_rate {order_dir} "
        f"LIMIT {limit}"
    )
    return sql, params


def _fake_store_filters(alias: str) -> list[str]:
    prefix = f"{alias}." if alias else ""
    return [
        f"{prefix}store_id NOT LIKE 'STORE_RISK_%'",
        f"{prefix}store_id NOT LIKE 'STORE_LINK_%'",
        f"{prefix}store_id NOT LIKE 'STORE_PERM_%'",
        f"{prefix}store_id <> 'STORE01'",
    ]


def _is_store_manager_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    if "店长" not in normalized:
        return False
    return any(token in normalized for token in ("谁", "都有谁", "哪位", "哪些", "每家门店", "各门店", "门店店长"))


def _store_manager_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    normalized = _normalize_query_text(q)
    conditions = ["manager_name IS NOT NULL", "manager_name <> ''"]
    conditions.extend(_fake_store_filters(""))
    params: list[str] = []
    wants_all = allow_global_scope and any(token in normalized for token in ("每家门店", "各门店", "都有谁", "哪些"))
    matched_store_ids = _match_store_ids(q)
    if matched_store_ids:
        conditions.append("store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif store_id and not wants_all:
        conditions.append("store_id = ?")
        params.append(store_id)
    where = " AND ".join(conditions)
    sql = (
        "SELECT store_name, manager_name "
        "FROM stores "
        f"WHERE {where} "
        "ORDER BY store_id ASC "
        "LIMIT 100"
    )
    return sql, params, "门店店长"


def _is_employee_detail_count_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    asks_count = any(token in normalized for token in ("几个", "多少", "人数", "员工数", "员工数量", "总共", "一共", "共有"))
    asks_employee = any(token in normalized for token in ("员工", "人员", "店员", "导购", "管理员", "店长", "资深顾问", "新人"))
    if not asks_count or not asks_employee:
        return False
    if _is_total_store_and_employee_count_query(q) or _is_store_employee_count_query(q):
        return False
    if any(token in normalized for token in ("哪些", "名单", "花名册", "属于哪个门店", "属于哪家门店", "在哪个门店", "在哪家门店")):
        return False
    return True


def _employee_detail_count_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    normalized = _normalize_query_text(q)
    conditions = list(_fake_store_filters("u"))
    params: list[str] = []
    matched_store_ids = _match_store_ids(q)
    role_keys = _matched_role_keys(q)
    is_global_query = allow_global_scope and any(token in normalized for token in ("系统", "全系统", "所有", "全部"))

    if matched_store_ids:
        conditions.append("u.store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif store_id and not allow_global_scope:
        conditions.append("u.store_id = ?")
        params.append(store_id)
    elif store_id and not is_global_query and any(token in normalized for token in ("本店", "门店", "店里", "店内")):
        conditions.append("u.store_id = ?")
        params.append(store_id)

    if role_keys:
        conditions.append("u.role IN (" + ", ".join(["?"] * len(role_keys)) + ")")
        params.extend(role_keys)

    where = " AND ".join(conditions)
    sql = (
        "SELECT u.display_name AS employee_name, "
        "COALESCE(p.position, u.role) AS position, "
        "s.store_name, "
        "COUNT(*) OVER() AS total_count "
        "FROM users u "
        "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "ORDER BY u.store_id ASC, u.display_name ASC "
        "LIMIT 100"
    )
    return sql, params, "员工数量与明细"


def _is_store_employee_count_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return "门店" in normalized and any(token in normalized for token in ("多少员工", "多少人", "员工数", "人数")) and "员工属于哪个门店" not in normalized


def _is_total_store_and_employee_count_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    asks_store_count = any(token in normalized for token in (
        "几个店铺", "几家店铺", "几个店", "几家店", "几个门店", "几家门店",
        "多少店铺", "多少门店", "门店数量", "店铺数量",
    ))
    asks_employee_count = any(token in normalized for token in (
        "几个员工", "几位员工", "多少员工", "多少人", "员工数量", "员工数", "人数",
    ))
    return asks_store_count and asks_employee_count


def _total_store_and_employee_count_sql(store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    if allow_global_scope:
        store_where = " AND ".join(_fake_store_filters(""))
        user_where = " AND ".join(_fake_store_filters("u"))
        sql = (
            "SELECT 'store_count' AS metric, COUNT(*) AS total_count FROM stores "
            f"WHERE {store_where} "
            "UNION ALL "
            "SELECT 'employee_count' AS metric, COUNT(*) AS total_count FROM users u "
            f"WHERE {user_where}"
        )
        return sql, [], "门店和员工总数"
    if store_id:
        sql = (
            "SELECT 'store_count' AS metric, 1 AS total_count "
            "UNION ALL "
            "SELECT 'employee_count' AS metric, COUNT(*) AS total_count FROM users u "
            "WHERE u.store_id = ? "
            "AND u.store_id NOT LIKE 'STORE_RISK_%' "
            "AND u.store_id NOT LIKE 'STORE_LINK_%' "
            "AND u.store_id NOT LIKE 'STORE_PERM_%' "
            "AND u.store_id <> 'STORE01'"
        )
        return sql, [store_id], "门店和员工总数"
    return None, [], ""


def _store_employee_count_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    conditions = list(_fake_store_filters("u"))
    params: list[str] = []
    wants_all = allow_global_scope and any(token in _normalize_query_text(q) for token in ("每家门店", "各门店", "所有门店", "全部门店"))
    matched_store_ids = _match_store_ids(q)
    if matched_store_ids:
        conditions.append("u.store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif store_id and not wants_all:
        conditions.append("u.store_id = ?")
        params.append(store_id)
    where = " AND ".join(conditions)
    sql = (
        "SELECT s.store_name, COUNT(*) AS employee_count "
        "FROM users u "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "GROUP BY u.store_id, s.store_name "
        "ORDER BY employee_count DESC, u.store_id ASC "
        "LIMIT 100"
    )
    return sql, params, "门店员工数"


def _is_employee_store_mapping_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return any(token in normalized for token in ("属于哪个门店", "属于哪家门店", "在哪个门店", "在哪家门店", "员工门店归属"))


_ROLE_QUERY_MAP: dict[str, str] = {
    "店长": "store_manager",
    "管理员": "admin",
    "资深顾问": "senior_consultant",
    "资深": "senior_consultant",
    "导购": "trainee",
    "新人": "trainee",
    "新员工": "trainee",
}


def _matched_role_keys(q: str) -> list[str]:
    normalized = _normalize_query_text(q)
    out: list[str] = []
    for label, role_key in _ROLE_QUERY_MAP.items():
        if label in normalized and role_key not in out:
            out.append(role_key)
    return out


def _is_store_employee_list_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return (
        ("门店" in normalized or "店" in normalized or bool(_match_store_ids(q)))
        and any(token in normalized for token in ("有哪些员工", "员工有哪些", "员工名单", "有哪些人", "人员名单"))
    )


def _store_employee_list_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    conditions = list(_fake_store_filters("u"))
    params: list[str] = []
    matched_store_ids = _match_store_ids(q)
    if matched_store_ids:
        conditions.append("u.store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif store_id and not allow_global_scope:
        conditions.append("u.store_id = ?")
        params.append(store_id)
    where = " AND ".join(conditions)
    sql = (
        "SELECT u.display_name AS employee_name, "
        "COALESCE(p.position, u.role) AS position, "
        "s.store_name "
        "FROM users u "
        "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "ORDER BY u.store_id ASC, u.display_name ASC "
        "LIMIT 100"
    )
    return sql, params, "门店员工名单"


def _is_store_role_member_list_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return (
        ("门店" in normalized or "店" in normalized or bool(_match_store_ids(q)))
        and bool(_matched_role_keys(q))
        and any(token in normalized for token in ("有哪些", "都有谁", "名单"))
    )


def _store_role_member_list_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    conditions = list(_fake_store_filters("u"))
    params: list[str] = []
    matched_store_ids = _match_store_ids(q)
    role_keys = _matched_role_keys(q)
    if matched_store_ids:
        conditions.append("u.store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif store_id and not allow_global_scope:
        conditions.append("u.store_id = ?")
        params.append(store_id)
    if role_keys:
        conditions.append("u.role IN (" + ", ".join(["?"] * len(role_keys)) + ")")
        params.extend(role_keys)
    where = " AND ".join(conditions)
    sql = (
        "SELECT u.display_name AS employee_name, "
        "COALESCE(p.position, u.role) AS position, "
        "s.store_name "
        "FROM users u "
        "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "ORDER BY u.store_id ASC, u.display_name ASC "
        "LIMIT 100"
    )
    return sql, params, "门店角色成员"


def _is_person_role_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return bool(_match_user_aliases(q)) and any(token in normalized for token in ("什么岗位", "什么职位", "什么角色", "是什么岗位", "是什么职位", "是什么角色", "干嘛"))


def _person_role_sql(q: str) -> tuple[str, list[str], str]:
    matched_user_aliases = _match_user_aliases(q)
    params: list[str] = []
    conditions = []
    if matched_user_aliases:
        conditions.append(
            "("
            + " OR ".join(["u.display_name = ?" for _ in matched_user_aliases])
            + " OR "
            + " OR ".join(["u.username = ?" for _ in matched_user_aliases])
            + ")"
        )
        params.extend(matched_user_aliases)
        params.extend(matched_user_aliases)
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        "SELECT u.display_name AS employee_name, "
        "COALESCE(p.position, u.role) AS position, "
        "s.store_name "
        "FROM users u "
        "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "ORDER BY u.display_name ASC "
        "LIMIT 20"
    )
    return sql, params, "人员岗位"


def _is_role_member_list_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return bool(_matched_role_keys(q)) and any(token in normalized for token in ("有哪些", "都有谁", "名单"))


def _role_member_list_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    role_keys = _matched_role_keys(q)
    conditions = []
    params: list[str] = []
    if role_keys:
        conditions.append("u.role IN (" + ", ".join(["?"] * len(role_keys)) + ")")
        params.extend(role_keys)
    if store_id and not allow_global_scope:
        conditions.extend(_fake_store_filters("u"))
        conditions.append("u.store_id = ?")
        params.append(store_id)
    else:
        conditions.extend(_fake_store_filters("u"))
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        "SELECT u.display_name AS employee_name, "
        "COALESCE(p.position, u.role) AS position, "
        "s.store_name "
        "FROM users u "
        "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "ORDER BY u.display_name ASC "
        "LIMIT 100"
    )
    return sql, params, "角色成员"


def _employee_store_mapping_sql(q: str, store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    conditions = list(_fake_store_filters("u"))
    params: list[str] = []
    matched_store_ids = _match_store_ids(q)
    matched_user_aliases = _match_user_aliases(q)
    if matched_store_ids:
        conditions.append("u.store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif store_id and not allow_global_scope:
        conditions.append("u.store_id = ?")
        params.append(store_id)
    if matched_user_aliases:
        conditions.append("(" + " OR ".join(["u.display_name = ?" for _ in matched_user_aliases]) + " OR " + " OR ".join(["u.username = ?" for _ in matched_user_aliases]) + ")")
        params.extend(matched_user_aliases)
        params.extend(matched_user_aliases)
    where = " AND ".join(conditions)
    sql = (
        "SELECT u.display_name AS employee_name, "
        "COALESCE(p.position, u.role) AS position, "
        "s.store_name "
        "FROM users u "
        "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
        "LEFT JOIN stores s ON u.store_id = s.store_id "
        f"WHERE {where} "
        "ORDER BY u.store_id ASC, u.display_name ASC "
        "LIMIT 100"
    )
    return sql, params, "员工门店归属"


def _is_role_list_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return "角色" in normalized and any(token in normalized for token in ("有哪些", "有什么", "列表", "都有什么", "系统里"))


def _role_list_sql() -> tuple[str, list[str], str]:
    sql = (
        "SELECT display_name, description "
        "FROM role_settings "
        "WHERE COALESCE(is_enabled, 1) = 1 "
        "ORDER BY sort_order ASC, display_name ASC "
        "LIMIT 100"
    )
    return sql, [], "角色列表"


def _is_store_count_only_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return any(token in normalized for token in (
        "几个门店", "几家门店", "几个店", "几家店",
        "门店数量", "店铺数量", "多少门店", "多少店铺",
    )) and not any(token in normalized for token in ("员工", "人员", "几人", "多少人"))


def _store_count_only_sql(store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    if allow_global_scope:
        filters = " AND ".join(_fake_store_filters(""))
        return f"SELECT COUNT(*) AS store_count FROM stores WHERE {filters}", [], "门店和员工总数"
    return "SELECT COUNT(*) AS store_count FROM stores WHERE store_id = ?", [store_id], "门店和员工总数"


def _is_employee_count_only_query(q: str) -> bool:
    normalized = _normalize_query_text(q)
    return any(token in normalized for token in (
        "几个员工", "几位员工", "多少员工", "多少人",
        "员工数量", "员工数", "人数",
    )) and not any(token in normalized for token in ("门店", "店铺", "店"))


def _employee_count_only_sql(store_id: str, allow_global_scope: bool) -> tuple[str, list[str], str]:
    if allow_global_scope:
        filters = " AND ".join(_fake_store_filters("u"))
        return f"SELECT COUNT(*) AS employee_count FROM users u WHERE {filters}", [], "门店和员工总数"
    if store_id:
        return (
            "SELECT COUNT(*) AS employee_count FROM users u "
            "WHERE u.store_id = ? "
            "AND u.store_id NOT LIKE 'STORE_RISK_%' "
            "AND u.store_id NOT LIKE 'STORE_LINK_%' "
            "AND u.store_id NOT LIKE 'STORE_PERM_%'",
            [store_id],
            "门店和员工总数",
        )
    return None, [], ""


def _specialized_system_sql(
    q: str,
    store_id: str,
    allow_global_scope: bool,
) -> tuple[str | None, list[str], str]:
    if _is_total_store_and_employee_count_query(q):
        return _total_store_and_employee_count_sql(store_id, allow_global_scope)
    if _is_store_count_only_query(q):
        return _store_count_only_sql(store_id, allow_global_scope)
    if _is_employee_count_only_query(q):
        return _employee_count_only_sql(store_id, allow_global_scope)
    if _is_employee_detail_count_query(q):
        return _employee_detail_count_sql(q, store_id, allow_global_scope)
    if _is_store_manager_query(q):
        return _store_manager_sql(q, store_id, allow_global_scope)
    if _is_store_employee_count_query(q):
        return _store_employee_count_sql(q, store_id, allow_global_scope)
    if _is_store_role_member_list_query(q):
        return _store_role_member_list_sql(q, store_id, allow_global_scope)
    if _is_store_employee_list_query(q):
        return _store_employee_list_sql(q, store_id, allow_global_scope)
    if _is_person_role_query(q):
        return _person_role_sql(q)
    if _is_employee_store_mapping_query(q):
        return _employee_store_mapping_sql(q, store_id, allow_global_scope)
    if _is_role_member_list_query(q):
        return _role_member_list_sql(q, store_id, allow_global_scope)
    if _is_role_list_query(q):
        return _role_list_sql()
    return None, [], ""


def generate_sql(
    query_text: str,
    store_id: str,
    *,
    allow_global_scope: bool = False,
) -> tuple[str | None, list[str], str]:
    """Generate a SQL SELECT from natural language.

    Returns (sql, params, explanation) on success, or (None, [], reason) on failure.
    sql uses '?' placeholders; params are passed separately for parameterized execution.
    """
    q = query_text.strip()
    if not q:
        return None, [], "查询内容为空"

    if _is_system_catalog_query(q):
        return _system_catalog_sql(), [], "系统数据目录"

    # ── Specialized: training completion from real task data ──
    if '培训完成率' in q or ('培训' in q and '完成' in q and '门店' in q):
        sql, params = _training_completion_sql(q, store_id, allow_global_scope)
        if sql:
            return sql, params, "按门店统计培训完成率（来自训练周期任务数据）"

    sql, params, explanation = _specialized_system_sql(q, store_id, allow_global_scope)
    if sql:
        return sql, params, explanation

    tables = _detect_tables(q)
    if not tables:
        return None, [], "无法识别查询目标数据表"

    # Use the first matched table as the primary table
    tbl = tables[0]
    schema_hints = get_table_schema_hints()
    info = schema_hints[tbl]
    cols = info["columns"]
    use_table_alias = bool(
        not allow_global_scope
        and store_id
        and not info.get("has_store_id")
        and ("user_id" in cols or "employee_id" in cols)
    )

    def cref(col: str) -> str:
        return f"t.{col}" if use_table_alias else col

    if use_table_alias and "user_id" in cols:
        table_expr = (
            f"{tbl} t JOIN users scope_u ON "
            f"(scope_u.user_id = t.user_id OR CAST(scope_u.id AS TEXT) = CAST(t.user_id AS TEXT))"
        )
        scope_store_ref = "scope_u.store_id"
    elif use_table_alias and "employee_id" in cols:
        table_expr = (
            f"{tbl} t JOIN employee_profiles scope_ep ON "
            f"(scope_ep.employee_id = t.employee_id OR scope_ep.user_id = t.employee_id)"
        )
        scope_store_ref = "scope_ep.store_id"
    else:
        table_expr = tbl
        scope_store_ref = "store_id"

    # Determine which columns to select (exclude internal id columns)
    selectable = [c for c in cols[:10] if c.lower() != "id"]
    if not selectable:
        selectable = cols[:10]
    select_cols = ", ".join([f"{cref(c)} AS {c}" for c in selectable])  # max 10 columns
    select_clause = f"SELECT {select_cols}"

    # Build WHERE conditions
    conditions: list[str] = []
    params: list[str] = []
    matched_store_ids = _match_store_ids(q)
    matched_user_aliases = _match_user_aliases(q)

    # Store scoping — global scope only allowed for admin caller
    is_global_hint = any(w in q for w in ["全部", "所有", "系统", "总共", "一共"])
    is_global_query = bool(is_global_hint and allow_global_scope)

    # ── Store-level aggregate queries ──
    # When query mentions "门店" + a metric and the table has store_id,
    # generate GROUP BY store_id with AVG of relevant metric columns.
    _STORE_AGG_METRICS = [
        "培训完成率", "完成率", "training_completion_rate",
        "综合分", "合规分", "平均分",
    ]
    is_store_agg = (
        "门店" in q
        and info.get("has_store_id")
        and any(m in q for m in _STORE_AGG_METRICS)
    )
    if is_store_agg:
        # Store-level aggregate: admin gets all stores, store_manager gets scoped
        if not allow_global_scope and store_id:
            conditions.append("store_id = ?")
            params.append(store_id)
        # Always exclude fake auto-generated stores
        conditions.append("store_id NOT LIKE 'STORE_RISK_%'")
        conditions.append("store_id NOT LIKE 'STORE_LINK_%'")
        conditions.append("store_id NOT LIKE 'STORE_PERM_%'")
    elif matched_store_ids and info.get("has_store_id"):
        conditions.append("store_id IN (" + ", ".join(["?"] * len(matched_store_ids)) + ")")
        params.extend(matched_store_ids)
    elif info.get("has_store_id") and store_id and not is_global_query:
        conditions.append("store_id = ?")
        params.append(store_id)
    elif info.get("has_store_id") and is_global_query:
        # Admin global query: still exclude fake stores
        conditions.append("store_id NOT LIKE 'STORE_RISK_%'")
        conditions.append("store_id NOT LIKE 'STORE_LINK_%'")
        conditions.append("store_id NOT LIKE 'STORE_PERM_%'")
    elif use_table_alias and store_id:
        conditions.append(f"{scope_store_ref} = ?")
        params.append(store_id)
        conditions.append(f"{scope_store_ref} NOT LIKE 'STORE_RISK_%'")
        conditions.append(f"{scope_store_ref} NOT LIKE 'STORE_LINK_%'")
        conditions.append(f"{scope_store_ref} NOT LIKE 'STORE_PERM_%'")

    # Time range — skip for reference/config tables where time filtering is not meaningful
    _SKIP_TIME_TABLES = {"users", "stores", "role_settings"}
    has_explicit_time = any(w in q for w in [
        "今天", "今日", "近7天", "最近", "近一周", "本月", "这个月",
        "近30天", "近90天", "近三个月", "近一个月",
    ])
    if "created_at" in cols and tbl not in _SKIP_TIME_TABLES:
        since = _days_ago(q)
        if since:
            conditions.append(f"{cref('created_at')} >= ?")
            params.append(since)
    elif "created_at" in cols and has_explicit_time:
        # User explicitly asked for a time range even on a reference table
        since = _days_ago(q)
        if since:
            conditions.append(f"{cref('created_at')} >= ?")
            params.append(since)

    # Score thresholds — match the most relevant score column based on query context
    def _pick_score_col(score_cols: list[str], query: str) -> str | None:
        """Pick the most contextually relevant score column from the query."""
        if not score_cols:
            return None
        # Map query keywords to column name substrings for matching
        _METRIC_HINTS = [
            ("合规", ["compliance"]),
            ("产品知识", ["product_knowledge"]),
            ("销售沟通", ["sales_communication"]),
            ("应变", ["response"]),
            ("综合", ["overall"]),
            ("陪练", ["practice", "overall"]),
            ("培训", ["training"]),
            ("完成率", ["completion", "rate"]),
        ]
        for keyword, substrs in _METRIC_HINTS:
            if keyword in query:
                for sc in score_cols:
                    for sub in substrs:
                        if sub in sc.lower():
                            return sc
        return score_cols[0]

    _score_cols = [c for c in cols if "score" in c.lower()]
    score_match = re.search(r"(?:低于|小于|不满|不到)\s*(\d{1,3})[分]?", q)
    if score_match:
        threshold = score_match.group(1)
        picked = _pick_score_col(_score_cols, q)
        if picked:
            conditions.append(f"{cref(picked)} < ?")
            params.append(threshold)

    score_above = re.search(r"(?:高于|大于|超过|以上)\s*(\d{1,3})[分]?", q)
    if score_above:
        threshold = score_above.group(1)
        picked = _pick_score_col(_score_cols, q)
        if picked:
            conditions.append(f"{cref(picked)} > ?")
            params.append(threshold)

    # Role/position matching (for users and employee_profiles tables)
    _ROLE_MAP: dict[str, str] = {
        "店长": "store_manager",
        "管理员": "admin",
        "资深顾问": "senior_consultant",
        "资深": "senior_consultant",
        "新员工": "trainee",
        "新人": "trainee",
        "实习": "trainee",
    }
    for label, role_key in _ROLE_MAP.items():
        if label in q:
            if "role" in cols:
                conditions.append(f"{cref('role')} = ?")
                params.append(role_key)
                break

    # Name-based search: extract actual person names from query
    _NOT_NAMES = frozenset({
        "什么", "哪个", "哪些", "多少", "怎么", "为什么", "所有", "全部",
        "系统", "这个", "那个", "职位", "岗位", "角色", "店长", "顾问",
        "管理员", "新员工", "新人", "资深", "门店", "助手", "陪练",
        "能力", "培训", "学习", "评估", "成绩", "在职", "账号", "用户",
    })
    name_val: str | None = None
    # Pattern 1: "叫X" / "名叫X"
    m = re.search(r"(?:叫|名叫|叫做)\s*([\u4e00-\u9fff]{2,4})", q)
    if m:
        name_val = m.group(1)
    # Pattern 2: "X是什么职位" / "X是哪个角色"
    if not name_val:
        m = re.search(r"([\u4e00-\u9fff]{2,4})是(?:什么|哪个)(?:职位|岗位|角色|职务)", q)
        if m:
            name_val = m.group(1)
    # Pattern 3: "X的职位" / "X在哪个门店"
    if not name_val:
        m = re.search(r"([\u4e00-\u9fff]{2,4})(?:的职位|的岗位|的角色|在哪)", q)
        if m:
            name_val = m.group(1)
    # Exclude common non-name words
    if name_val and name_val in _NOT_NAMES:
        name_val = None
    if name_val:
        if "display_name" in cols:
            conditions.append(f"{cref('display_name')} LIKE ?")
            params.append(f"%{name_val}%")
        elif "employee_name" in cols:
            conditions.append(f"{cref('employee_name')} LIKE ?")
            params.append(f"%{name_val}%")
    elif matched_user_aliases:
        if "display_name" in cols and "username" in cols:
            conditions.append(
                "("
                + " OR ".join([f"{cref('display_name')} = ?" for _ in matched_user_aliases])
                + " OR "
                + " OR ".join([f"{cref('username')} = ?" for _ in matched_user_aliases])
                + ")"
            )
            params.extend(matched_user_aliases)
            params.extend(matched_user_aliases)
        elif "display_name" in cols:
            conditions.append("(" + " OR ".join([f"{cref('display_name')} = ?" for _ in matched_user_aliases]) + ")")
            params.extend(matched_user_aliases)
        elif "employee_name" in cols:
            conditions.append("(" + " OR ".join([f"{cref('employee_name')} = ?" for _ in matched_user_aliases]) + ")")
            params.extend(matched_user_aliases)

    # Risk level
    for rl in ["高", "中", "低"]:
        if f"风险{rl}" in q or f"{rl}风险" in q:
            if "risk_level" in cols:
                conditions.append(f"{cref('risk_level')} LIKE ?")
                params.append(f"%{rl}%")

    # Level
    for lv in ["优秀", "良好", "合格", "不合格", "待提升"]:
        if lv in q:
            if "level" in cols:
                conditions.append(f"{cref('level')} LIKE ?")
                params.append(f"%{lv}%")
            if "mastery_level" in cols:
                conditions.append(f"{cref('mastery_level')} LIKE ?")
                params.append(f"%{lv}%")

    # Assessment: pass/fail filter
    if "is_pass" in cols:
        if "通过" in q or "及格" in q:
            conditions.append(f"{cref('is_pass')} = ?")
            params.append("1")
        elif "不及格" in q or "未通过" in q or "没通过" in q:
            conditions.append(f"{cref('is_pass')} = ?")
            params.append("0")

    # Assessment task status filter
    if "status" in cols and tbl == "assessment_tasks":
        if "进行中" in q or "活跃" in q:
            conditions.append(f"{cref('status')} = ?")
            params.append("active")
        elif "已结束" in q or "已关闭" in q:
            conditions.append(f"{cref('status')} = ?")
            params.append("closed")

    # Sales: amount threshold (e.g. "销售额超过10万")
    amount_match = re.search(r"(?:销售额|业绩)(?:超过|大于|高于|以上)\s*(\d+)", q)
    if amount_match and "sales_amount" in cols:
        val = float(amount_match.group(1))
        # Auto-scale: if user says "10万" interpret as 100000
        if "万" in q:
            val *= 10000
        conditions.append(f"{cref('sales_amount')} >= ?")
        params.append(str(int(val)))

    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)

    # Detect aggregate intent (counting)
    is_count = any(w in q for w in ["多少", "数量", "几个", "几家", "几人", "几位", "人数", "总共", "一共", "统计"])
    # Detect top-N intent
    top_match = re.search(r"(?:前|top)\s*(\d{1,2})", q, re.IGNORECASE)
    limit = int(top_match.group(1)) if top_match else 100

    if is_count:
        sql = f"SELECT COUNT(*) AS total_count FROM {table_expr}{where_clause}"
    elif is_store_agg:
        # ── Store-level aggregate: GROUP BY store_id ──
        agg_cols: list[str] = ["store_id"]
        score_cols = [c for c in cols if "score" in c.lower() or "rate" in c.lower()]
        for sc in score_cols[:5]:
            agg_cols.append(f"AVG(CASE WHEN {sc} IS NOT NULL THEN {sc} END) AS {sc}")
        agg_cols.append("COUNT(*) AS record_count")
        select_clause = ", ".join(agg_cols)
        order_dir = "ASC" if any(w in q for w in ["最低", "最差", "不好", "最低的", "最差的"]) else "DESC"
        order_metric = score_cols[0] if score_cols else "store_id"
        sql = f"SELECT {select_clause} FROM {table_expr}{where_clause} GROUP BY store_id ORDER BY {order_metric} {order_dir} LIMIT {limit}"
    else:
        order_col = cref("created_at") if "created_at" in cols else cref(cols[0])
        sql = f"{select_clause} FROM {table_expr}{where_clause} ORDER BY {order_col} DESC LIMIT {limit}"

    _log.info("sql_generator produced sql=%s params=%s table=%s query=%s",
              sql[:200], params, tbl, q[:80])

    explanation = f"查询 {info['description']} ({tbl})"
    return sql, params, explanation
