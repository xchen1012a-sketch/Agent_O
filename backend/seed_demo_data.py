from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auth import get_password_hash
from database import SQLITE_DB_PATH, ensure_database_initialized, utc_now_iso
from training_plan import build_daily_stage_tasks, build_stage_definitions

SEED_TAG = "demo_seed_v1"
DEMO_PASSWORD = "AgentO2026@"
DEFAULT_DB_PATH = SQLITE_DB_PATH

DEMO_TASK_NAME_PREFIX = "DEMO:"
DEMO_PLAN_PREFIX = "demo_plan_"
DEMO_CYCLE_PREFIX = "demo_cycle_"
DEMO_PRACTICE_PREFIX = "demo_practice_"
DEMO_PRACTICE_EVAL_PREFIX = "demo_eval_"
DEMO_ABILITY_PREFIX = "demo_update_"
DEMO_LEARNING_PREFIX = "demo_learning_"
DEMO_ASSISTANT_PREFIX = "demo_assistant_"
DEMO_DASHBOARD_PREFIX = "demo_dashboard_"
DEMO_QUERY_PREFIX = "demo_query_"
DEMO_REVIEW_QA_PREFIX = "demo_review_qa_"
DEMO_EVO_PREFIX = "demo_evo_"
PROTAGONIST_USERNAME = "trainee_zjx"
PROTAGONIST_NAME = "赵景行"
DEMO_ASSISTANT_HISTORY_COUNT = 220
PROTAGONIST_SCORE_TRAJECTORY = [38.0, 42.0, 45.0, 49.0, 53.0, 56.0, 58.0, 62.0, 68.0, 73.0, 78.0, 80.0, 82.0, 85.0]

MODULE_LABELS: dict[str, str] = {
    "product_basics": "产品基础",
    "compliance_expression": "合规表达",
    "needs_discovery": "需求挖掘",
    "objection_handling": "异议处理",
    "closing_conversion": "成交推进",
    "independent_service": "独立上岗",
}

DEMO_STORES: list[dict[str, str]] = [
    {"store_id": "STORE_BJ", "store_name": "北京国贸旗舰店", "region": "北京", "manager_name": "王建国"},
    {"store_id": "STORE_SH", "store_name": "上海陆家嘴体验店", "region": "上海", "manager_name": "李芳"},
    {"store_id": "STORE_GZ", "store_name": "广州天河精品店", "region": "广州", "manager_name": "陈志明"},
    {"store_id": "STORE_CD", "store_name": "成都太古里门店", "region": "成都", "manager_name": "赵丽华"},
    {"store_id": "STORE_HZ", "store_name": "杭州万象城店", "region": "杭州", "manager_name": "周伟"},
]

DEMO_USERS: list[dict[str, str]] = [
    {"username": "manager_bj", "name": "王建国", "role": "store_manager", "store_id": "STORE_BJ", "phone": "13810010001"},
    {"username": "manager_sh", "name": "李芳", "role": "store_manager", "store_id": "STORE_SH", "phone": "13810010002"},
    {"username": "manager_gz", "name": "陈志明", "role": "store_manager", "store_id": "STORE_GZ", "phone": "13810010003"},
    {"username": "manager_cd", "name": "赵丽华", "role": "store_manager", "store_id": "STORE_CD", "phone": "13810010004"},
    {"username": "manager_hz", "name": "周伟", "role": "store_manager", "store_id": "STORE_HZ", "phone": "13810010005"},
    {"username": "senior_bj1", "name": "刘晓燕", "role": "senior_consultant", "store_id": "STORE_BJ", "phone": "13820010001"},
    {"username": "senior_sh1", "name": "张美玲", "role": "senior_consultant", "store_id": "STORE_SH", "phone": "13820010002"},
    {"username": "senior_gz1", "name": "黄思远", "role": "senior_consultant", "store_id": "STORE_GZ", "phone": "13820010003"},
    {"username": "senior_cd1", "name": "吴佳琪", "role": "senior_consultant", "store_id": "STORE_CD", "phone": "13820010004"},
    {"username": "senior_hz1", "name": "孙晓明", "role": "senior_consultant", "store_id": "STORE_HZ", "phone": "13820010005"},
    {"username": "trainee_bj1", "name": "杨小雨", "role": "trainee", "store_id": "STORE_BJ", "phone": "13830010001"},
    {"username": "trainee_bj2", "name": "马天宇", "role": "trainee", "store_id": "STORE_BJ", "phone": "13830010002"},
    {"username": "trainee_sh1", "name": "林婷婷", "role": "trainee", "store_id": "STORE_SH", "phone": "13830010003"},
    {"username": "trainee_sh2", "name": "郑浩然", "role": "trainee", "store_id": "STORE_SH", "phone": "13830010004"},
    {"username": "trainee_gz1", "name": "何佳欣", "role": "trainee", "store_id": "STORE_GZ", "phone": "13830010005"},
    {"username": "trainee_gz2", "name": "梁志豪", "role": "trainee", "store_id": "STORE_GZ", "phone": "13830010006"},
    {"username": "trainee_cd1", "name": "谢思琪", "role": "trainee", "store_id": "STORE_CD", "phone": "13830010007"},
    {"username": "trainee_cd2", "name": "唐文博", "role": "trainee", "store_id": "STORE_CD", "phone": "13830010008"},
    {"username": "trainee_hz1", "name": "冯雨萱", "role": "trainee", "store_id": "STORE_HZ", "phone": "13830010009"},
    {"username": PROTAGONIST_USERNAME, "name": PROTAGONIST_NAME, "role": "trainee", "store_id": "STORE_GZ", "phone": "13830010010"},
]

DEMO_USERNAMES = [item["username"] for item in DEMO_USERS]
DEMO_STORE_IDS = [item["store_id"] for item in DEMO_STORES]
STORE_BY_ID = {item["store_id"]: item for item in DEMO_STORES}
USER_BY_USERNAME = {item["username"]: item for item in DEMO_USERS}

TRAINING_TARGETS: dict[str, dict[str, Any]] = {
    "trainee_bj1": {"stage_no": 1, "status": "active", "current_day": 6, "stage_status": "active"},
    "trainee_bj2": {"stage_no": 1, "status": "waiting_review", "current_day": 7, "stage_status": "waiting_review"},
    "trainee_sh1": {"stage_no": 1, "status": "active", "current_day": 6, "stage_status": "active"},
    "trainee_gz1": {"stage_no": 1, "status": "active", "current_day": 5, "stage_status": "active"},
    "senior_cd1": {"stage_no": 1, "status": "voided", "current_day": 2, "stage_status": "failed"},
    "trainee_sh2": {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
    "trainee_gz2": {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
    "trainee_cd1": {"stage_no": 2, "status": "active", "current_day": 6, "stage_status": "active"},
    "trainee_cd2": {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
    "trainee_hz1": {"stage_no": 2, "status": "active", "current_day": 6, "stage_status": "active"},
    "senior_bj1": {"stage_no": 2, "status": "active", "current_day": 6, "stage_status": "active"},
    "senior_sh1": {"stage_no": 2, "status": "waiting_review", "current_day": 7, "stage_status": "waiting_review"},
    "senior_gz1": {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
    "senior_hz1": {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
    "manager_bj": {"stage_no": 2, "status": "active", "current_day": 6, "stage_status": "active"},
    "manager_sh": {"stage_no": 2, "status": "waiting_review", "current_day": 7, "stage_status": "waiting_review"},
    "manager_gz": {"stage_no": 2, "status": "active", "current_day": 6, "stage_status": "active"},
    "manager_cd": {"stage_no": 2, "status": "active", "current_day": 6, "stage_status": "active"},
    "manager_hz": {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
    PROTAGONIST_USERNAME: {"stage_no": 2, "status": "completed", "current_day": 7, "stage_status": "passed"},
}

STAGE_DEFINITIONS = {int(item["stage_no"]): item for item in build_stage_definitions()}


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def normalize_db_path(raw: str | Path | None) -> Path:
    return Path(raw or DEFAULT_DB_PATH).resolve()


def prepare_database(db_path: Path) -> None:
    if db_path == DEFAULT_DB_PATH.resolve():
        ensure_database_initialized()
    elif not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")


def connect_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    target = normalize_db_path(db_path)
    conn = sqlite3.connect(target, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def placeholders(items: list[Any]) -> str:
    return ",".join("?" for _ in items)


def delete_where(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    cur = conn.execute(sql, params)
    return max(int(cur.rowcount or 0), 0)


def role_label(role: str) -> str:
    return {
        "admin": "系统管理员",
        "store_manager": "店长",
        "senior_consultant": "资深顾问",
        "trainee": "导购",
    }.get(role, role)


def mentor_name_for(user: dict[str, str]) -> str:
    if user["role"] == "store_manager":
        return "总部训练负责人"
    return STORE_BY_ID[user["store_id"]]["manager_name"]


def profile_scores_for(role: str, idx: int) -> dict[str, float]:
    base = {"store_manager": 88.0, "senior_consultant": 81.0, "trainee": 68.0}[role]
    step = (idx % 3) * 1.8
    product = round(base + step + 1.5, 1)
    compliance = round(base + step - 1.0, 1)
    sales = round(base + step - 2.3, 1)
    response = round(base + step - 0.8, 1)
    overall = round((product + compliance + sales + response) / 4, 1)
    return {"product": product, "compliance": compliance, "sales": sales, "response": response, "overall": overall}


def profile_scores_for_user(user: dict[str, str], idx: int) -> dict[str, float]:
    if user["username"] == PROTAGONIST_USERNAME:
        return {"product": 87.0, "compliance": 84.0, "sales": 85.0, "response": 84.0, "overall": 85.0}
    return profile_scores_for(user["role"], idx)


def risk_level_for(score: float) -> str:
    if score >= 85:
        return "low"
    if score >= 72:
        return "medium"
    return "high"


def level_for(score: float) -> str:
    if score >= 90:
        return "优秀"
    if score >= 80:
        return "达标"
    if score >= 70:
        return "待提升"
    return "待加强"


def stage_name(stage_no: int) -> str:
    return str(STAGE_DEFINITIONS[int(stage_no)]["stage_name"])


def stage_total_days(stage_no: int) -> int:
    return int(STAGE_DEFINITIONS[int(stage_no)]["total_days"])


def iso_day(day: int) -> str:
    return f"2026-04-{day:02d}T09:00:00+00:00"


def story_day_iso(day: int, hour: int = 9) -> str:
    return f"2026-05-{day:02d}T{hour:02d}:00:00+00:00"


def protagonist_stage_no(day_index: int) -> int:
    return 1 if day_index <= 7 else 2


def protagonist_cycle_day(day_index: int) -> int:
    return day_index if day_index <= 7 else day_index - 7


def protagonist_ability_snapshot(day_index: int, overall_score: float) -> dict[str, Any]:
    product = min(100.0, round(overall_score + 5.0, 1))
    compliance = min(100.0, round(overall_score + 1.0, 1))
    needs = min(100.0, round(overall_score + 2.5, 1))
    sales = min(100.0, round(overall_score + 0.5, 1))
    objection = min(100.0, round(overall_score - 1.5, 1))
    closing = min(100.0, round(overall_score - 0.5, 1))
    return {
        "seed_tag": SEED_TAG,
        "story_actor": PROTAGONIST_NAME,
        "day_index": day_index,
        "overall_score": overall_score,
        "product_knowledge": product,
        "compliance_expression": compliance,
        "needs_discovery": needs,
        "sales_expression": sales,
        "objection_handling": objection,
        "closing_skill": closing,
    }


ASSESSMENT_REVIEW_QUESTIONS: dict[str, list[dict[str, Any]]] = {
    "product_basics": [
        {
            "id": "q_product_material",
            "type": "single",
            "title": "顾客问 18K 金是否一定不会变形时，优先说明什么？",
            "options": [
                {"key": "A", "text": "结合金属硬度、佩戴习惯和保养边界说明"},
                {"key": "B", "text": "直接承诺日常佩戴不会变形"},
                {"key": "C", "text": "只建议顾客购买更贵款式"},
                {"key": "D", "text": "回避问题并转向促销活动"},
            ],
            "answer": "A",
            "knowledge_tag": "材质工艺",
            "wrong_answer": "B",
        },
        {
            "id": "q_product_cert",
            "type": "essay",
            "title": "简述介绍钻石证书时必须覆盖的要点。",
            "keywords": ["证书", "4C", "风险边界"],
            "knowledge_tag": "钻石证书",
            "wrong_answer": "证书都差不多，重点推荐优惠就可以。",
        },
    ],
    "compliance_expression": [
        {
            "id": "q_compliance_value",
            "type": "single",
            "title": "顾客追问珠宝能否升值时，哪种回应更合规？",
            "options": [
                {"key": "A", "text": "不能承诺收益，转向佩戴价值、工艺和售后说明"},
                {"key": "B", "text": "暗示热门款长期一定升值"},
                {"key": "C", "text": "让顾客自行判断，不做任何解释"},
                {"key": "D", "text": "承诺以后可以高价回收"},
            ],
            "answer": "A",
            "knowledge_tag": "风险边界",
            "wrong_answer": "B",
        },
        {
            "id": "q_compliance_privacy",
            "type": "multiple",
            "title": "登记会员信息时，下列哪些做法正确？",
            "options": [
                {"key": "A", "text": "说明用途并征得同意"},
                {"key": "B", "text": "把手机号发到私人群方便跟进"},
                {"key": "C", "text": "只收集服务必要信息"},
                {"key": "D", "text": "默认顾客同意所有营销触达"},
            ],
            "answer": ["A", "C"],
            "knowledge_tag": "个人信息保护",
            "wrong_answer": ["A", "D"],
        },
    ],
    "needs_discovery": [
        {
            "id": "q_needs_gift",
            "type": "single",
            "title": "顾客说想送妈妈但不确定款式时，第一步应做什么？",
            "options": [
                {"key": "A", "text": "询问佩戴场景、预算、偏好和过敏史"},
                {"key": "B", "text": "直接推荐店内最高客单款"},
                {"key": "C", "text": "先介绍所有材质参数"},
                {"key": "D", "text": "让顾客自己看柜台"},
            ],
            "answer": "A",
            "knowledge_tag": "送礼推荐",
            "wrong_answer": "B",
        },
        {
            "id": "q_needs_budget",
            "type": "essay",
            "title": "写出预算不明确顾客的需求挖掘追问。",
            "keywords": ["预算", "佩戴场景", "偏好"],
            "knowledge_tag": "需求五问",
            "wrong_answer": "您先看喜欢哪款，我们再谈。",
        },
    ],
    "objection_handling": [
        {
            "id": "q_objection_price",
            "type": "single",
            "title": "顾客说别家同款便宜 1000 元时，应先做什么？",
            "options": [
                {"key": "A", "text": "核对参数、证书、工艺和售后是否一致"},
                {"key": "B", "text": "马上申请最低折扣"},
                {"key": "C", "text": "直接否定竞品"},
                {"key": "D", "text": "告诉顾客便宜没好货"},
            ],
            "answer": "A",
            "knowledge_tag": "竞品对比",
            "wrong_answer": "B",
        },
        {
            "id": "q_objection_after_sale",
            "type": "essay",
            "title": "顾客担心维修周期长时，应如何安抚并推进？",
            "keywords": ["周期", "跟进", "替代方案"],
            "knowledge_tag": "售后异议",
            "wrong_answer": "这个没办法，等通知就行。",
        },
    ],
    "closing_conversion": [
        {
            "id": "q_closing_signal",
            "type": "single",
            "title": "顾客反复试戴并询问保养时，较合适的推进动作是？",
            "options": [
                {"key": "A", "text": "总结需求匹配点并给出下一步成交动作"},
                {"key": "B", "text": "继续无重点介绍更多款式"},
                {"key": "C", "text": "催促顾客马上付款"},
                {"key": "D", "text": "停止跟进等待顾客主动开口"},
            ],
            "answer": "A",
            "knowledge_tag": "成交信号",
            "wrong_answer": "B",
        },
        {
            "id": "q_closing_compare",
            "type": "essay",
            "title": "顾客在两款之间犹豫时，如何帮助其决策？",
            "keywords": ["需求", "对比", "下一步"],
            "knowledge_tag": "收口推进",
            "wrong_answer": "两款都不错，您自己决定。",
        },
    ],
    "independent_service": [
        {
            "id": "q_service_escalation",
            "type": "single",
            "title": "遇到退换边界不清的投诉时，新人应优先怎么做？",
            "options": [
                {"key": "A", "text": "记录事实、安抚顾客并按流程升级"},
                {"key": "B", "text": "现场自行承诺无条件退换"},
                {"key": "C", "text": "让顾客自行联系总部"},
                {"key": "D", "text": "强调门店没有责任"},
            ],
            "answer": "A",
            "knowledge_tag": "投诉升级",
            "wrong_answer": "B",
        },
        {
            "id": "q_service_handover",
            "type": "essay",
            "title": "独立接待结束后，应沉淀哪些交接信息？",
            "keywords": ["顾客需求", "跟进动作", "风险点"],
            "knowledge_tag": "独立接待",
            "wrong_answer": "只记录是否成交。",
        },
    ],
}


REVIEW_NOTEBOOK_QA_POOL: list[dict[str, str]] = [
    {
        "question": "顾客问这枚钻戒证书参数和柜台介绍不一致，应该怎么解释？",
        "reply": "需要对照证书、4C 参数和商品标签逐项说明，不能用含糊话术带过。",
        "knowledge_tag": "钻石证书",
        "weak_dimension": "产品知识",
    },
    {
        "question": "顾客问黄金手链以后能不能保值，怎么说才不踩线？",
        "reply": "回答需要先明确不能承诺收益，再解释佩戴价值、材质和售后边界。",
        "knowledge_tag": "风险边界",
        "weak_dimension": "合规表达",
    },
    {
        "question": "顾客说想送妈妈但不知道款式，第一句话应该问什么？",
        "reply": "应先问佩戴场景、预算、肤色偏好和过敏史，不能直接推高价款。",
        "knowledge_tag": "送礼推荐",
        "weak_dimension": "需求挖掘",
    },
    {
        "question": "顾客说竞品同款便宜，能不能直接说我们品质更好？",
        "reply": "需要先核对参数、证书、工艺和售后，再做可验证差异说明。",
        "knowledge_tag": "竞品对比",
        "weak_dimension": "异议处理",
    },
    {
        "question": "顾客反复试戴后还在犹豫，怎样自然推进下一步？",
        "reply": "需要复述需求匹配点，再给试戴对比、保养说明或下单确认动作。",
        "knowledge_tag": "成交信号",
        "weak_dimension": "成交收口",
    },
    {
        "question": "戒托变形投诉怎样判断是佩戴问题还是质量问题？",
        "reply": "需要补充留痕、检测、售后边界和升级流程。",
        "knowledge_tag": "售后异议",
        "weak_dimension": "独立接待",
    },
]


REVIEW_NOTEBOOK_QA_EXTRA_SPECS: list[dict[str, str]] = [
    {
        "username": PROTAGONIST_USERNAME,
        "question": "钻石证书上写的 4C 和门店讲解不一致，应该怎么判断？",
        "reply": "回答缺少证书口径、4C 对照和风险边界，需要回到标准解释流程。",
        "knowledge_tag": "钻石证书",
        "weak_dimension": "产品知识",
    },
    {
        "username": PROTAGONIST_USERNAME,
        "question": "顾客问黄金手链以后能不能保值，怎么说才不踩线？",
        "reply": "回答需要先明确不能承诺收益，再解释佩戴价值、材质和售后边界。",
        "knowledge_tag": "风险边界",
        "weak_dimension": "合规表达",
    },
    {
        "username": PROTAGONIST_USERNAME,
        "question": "给长辈送珍珠项链，第一句话应该问什么？",
        "reply": "应先问佩戴场景、预算、肤色偏好和过敏史，不能直接推高价款。",
        "knowledge_tag": "送礼推荐",
        "weak_dimension": "需求挖掘",
    },
    {
        "username": PROTAGONIST_USERNAME,
        "question": "顾客说竞品同款便宜，能不能直接说我们品质更好？",
        "reply": "需要先核对参数、证书、工艺和售后，再做可验证差异说明。",
        "knowledge_tag": "竞品对比",
        "weak_dimension": "异议处理",
    },
    {
        "username": "trainee_gz1",
        "question": "戒托变形投诉怎样判断是佩戴问题还是质量问题？",
        "reply": "需要补充留痕、检测、售后边界和升级流程。",
        "knowledge_tag": "售后异议",
        "weak_dimension": "独立接待",
    },
    {
        "username": "trainee_bj1",
        "question": "顾客只说随便看看时，怎样自然开场？",
        "reply": "需要用低压开场问题识别场景，而不是马上介绍折扣。",
        "knowledge_tag": "顾客进店接待",
        "weak_dimension": "需求挖掘",
    },
]


def build_review_notebook_qa_specs() -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for idx, user in enumerate(DEMO_USERS):
        template = REVIEW_NOTEBOOK_QA_POOL[idx % len(REVIEW_NOTEBOOK_QA_POOL)]
        spec = dict(template)
        spec["username"] = user["username"]
        spec["question"] = f"{user['name']}复盘：{template['question']}"
        specs.append(spec)
    specs.extend(REVIEW_NOTEBOOK_QA_EXTRA_SPECS)
    return specs


def build_review_paper_config(module_code: str) -> dict[str, Any]:
    questions = ASSESSMENT_REVIEW_QUESTIONS.get(module_code) or ASSESSMENT_REVIEW_QUESTIONS["product_basics"]
    return {
        "seed_tag": SEED_TAG,
        "source": "demo_seed_review_notebook",
        "questions": [
            {key: value for key, value in question.items() if key != "wrong_answer"}
            for question in questions
        ],
    }


def build_review_paper_answers(module_code: str, *, wrong_all: bool) -> dict[str, Any]:
    questions = ASSESSMENT_REVIEW_QUESTIONS.get(module_code) or ASSESSMENT_REVIEW_QUESTIONS["product_basics"]
    answers: dict[str, Any] = {}
    for index, question in enumerate(questions):
        if wrong_all or index == 0:
            answers[str(question["id"])] = question["wrong_answer"]
        else:
            answers[str(question["id"])] = question.get("answer") or question.get("keywords") or ""
    return answers


def fetch_admin_row(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT id, username, hashed_password FROM users WHERE username = 'admin' LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("未找到现有 admin 账号，脚本不会自动重建 admin。")
    return row


def fetch_user_rows(conn: sqlite3.Connection, usernames: list[str]) -> dict[str, sqlite3.Row]:
    if not usernames:
        return {}
    rows = conn.execute(
        f"SELECT id, username, role, store_id, user_id FROM users WHERE username IN ({placeholders(usernames)})",
        tuple(usernames),
    ).fetchall()
    return {str(row['username']): row for row in rows}


def delete_demo_data(db_path: str | Path | None = None, *, verbose: bool = False) -> dict[str, Any]:
    target = normalize_db_path(db_path)
    prepare_database(target)
    summary: dict[str, Any] = {"mode": "delete-demo", "db_path": str(target), "deleted": {}}
    with closing(connect_db(target)) as conn:
        demo_user_rows = fetch_user_rows(conn, DEMO_USERNAMES)
        demo_user_ids = [str(row["id"]) for row in demo_user_rows.values()]
        deleted = summary["deleted"]
        if demo_user_ids:
            ph = placeholders(demo_user_ids)
            deleted["cycle_daily_tasks"] = delete_where(conn, f"DELETE FROM cycle_daily_tasks WHERE user_id IN ({ph}) OR cycle_id LIKE ?", tuple(demo_user_ids) + (f"{DEMO_CYCLE_PREFIX}%",))
            deleted["training_cycles"] = delete_where(conn, f"DELETE FROM training_cycles WHERE user_id IN ({ph}) OR cycle_id LIKE ?", tuple(demo_user_ids) + (f"{DEMO_CYCLE_PREFIX}%",))
            deleted["training_stage_reviews"] = delete_where(conn, f"DELETE FROM training_stage_reviews WHERE user_id IN ({ph}) OR cycle_id LIKE ?", tuple(demo_user_ids) + (f"{DEMO_CYCLE_PREFIX}%",))
            deleted["training_unlock_snapshots"] = delete_where(conn, f"DELETE FROM training_unlock_snapshots WHERE user_id IN ({ph}) OR cycle_id LIKE ?", tuple(demo_user_ids) + (f"{DEMO_CYCLE_PREFIX}%",))
            deleted["module_index_snapshots"] = delete_where(conn, f"DELETE FROM module_index_snapshots WHERE user_id IN ({ph})", tuple(demo_user_ids))
            deleted["sales_performance"] = delete_where(conn, f"DELETE FROM sales_performance WHERE user_id IN ({ph})", tuple(demo_user_ids))
            deleted["dashboard_snapshots"] = delete_where(conn, "DELETE FROM dashboard_snapshots WHERE snapshot_id LIKE ?", (f"{DEMO_DASHBOARD_PREFIX}%",))
            deleted["assistant_records"] = delete_where(conn, f"DELETE FROM assistant_records WHERE user_id IN ({ph}) OR employee_id IN ({ph}) OR record_id LIKE ?", tuple(demo_user_ids) + tuple(demo_user_ids) + (f"{DEMO_ASSISTANT_PREFIX}%",))
            deleted["practice_eval_records"] = delete_where(conn, f"DELETE FROM practice_eval_records WHERE user_id IN ({ph}) OR employee_id IN ({ph}) OR evaluation_id LIKE ?", tuple(demo_user_ids) + tuple(demo_user_ids) + (f"{DEMO_PRACTICE_EVAL_PREFIX}%",))
            deleted["ability_update_records"] = delete_where(conn, f"DELETE FROM ability_update_records WHERE user_id IN ({ph}) OR employee_id IN ({ph}) OR update_id LIKE ?", tuple(demo_user_ids) + tuple(demo_user_ids) + (f"{DEMO_ABILITY_PREFIX}%",))
            deleted["practice_records"] = delete_where(conn, f"DELETE FROM practice_records WHERE user_id IN ({ph}) OR employee_id IN ({ph}) OR practice_id LIKE ?", tuple(demo_user_ids) + tuple(demo_user_ids) + (f"{DEMO_PRACTICE_PREFIX}%",))
            deleted["learning_eval_records"] = delete_where(conn, f"DELETE FROM learning_eval_records WHERE user_id IN ({ph}) OR employee_id IN ({ph}) OR evaluation_id LIKE ?", tuple(demo_user_ids) + tuple(demo_user_ids) + (f"{DEMO_LEARNING_PREFIX}%",))
            deleted["growth_task_manual_records"] = delete_where(conn, f"DELETE FROM growth_task_manual_records WHERE employee_id IN ({ph}) OR plan_id LIKE ?", tuple(demo_user_ids) + (f"{DEMO_PLAN_PREFIX}%",))
            deleted["growth_plan_records"] = delete_where(conn, f"DELETE FROM growth_plan_records WHERE user_id IN ({ph}) OR employee_id IN ({ph}) OR plan_id LIKE ?", tuple(demo_user_ids) + tuple(demo_user_ids) + (f"{DEMO_PLAN_PREFIX}%",))
            deleted["query_records"] = delete_where(conn, f"DELETE FROM query_records WHERE employee_id IN ({ph}) OR record_id LIKE ?", tuple(demo_user_ids) + (f"{DEMO_QUERY_PREFIX}%",))
            deleted["assessment_task_targets"] = delete_where(conn, "DELETE FROM assessment_task_targets WHERE task_id IN (SELECT id FROM assessment_tasks WHERE task_name LIKE ?)", (f"{DEMO_TASK_NAME_PREFIX}%",))
            deleted["assessment_records"] = delete_where(conn, "DELETE FROM assessment_records WHERE task_id IN (SELECT id FROM assessment_tasks WHERE task_name LIKE ?)", (f"{DEMO_TASK_NAME_PREFIX}%",))
            deleted["assessment_tasks"] = delete_where(conn, "DELETE FROM assessment_tasks WHERE task_name LIKE ?", (f"{DEMO_TASK_NAME_PREFIX}%",))
            deleted["employee_profiles"] = delete_where(conn, f"DELETE FROM employee_profiles WHERE source = ? OR user_id IN ({ph}) OR employee_id IN ({ph})", (SEED_TAG,) + tuple(demo_user_ids) + tuple(demo_user_ids))
            deleted["users"] = delete_where(conn, f"DELETE FROM users WHERE username IN ({placeholders(DEMO_USERNAMES)})", tuple(DEMO_USERNAMES))
        else:
            for key in ("cycle_daily_tasks", "training_cycles", "training_stage_reviews", "training_unlock_snapshots", "module_index_snapshots", "sales_performance", "dashboard_snapshots", "assistant_records", "practice_eval_records", "ability_update_records", "practice_records", "learning_eval_records", "growth_task_manual_records", "growth_plan_records", "query_records", "assessment_task_targets", "assessment_records", "assessment_tasks", "employee_profiles", "users"):
                deleted[key] = 0
        seed_like = f"%{SEED_TAG}%"
        deleted["agent_evo_eval_runs"] = delete_where(
            conn,
            "DELETE FROM agent_evo_eval_runs WHERE triggered_by = 'demo_seed' OR bound_memory_ids LIKE ?",
            (seed_like,),
        )
        deleted["agent_evo_eval_cases"] = delete_where(
            conn,
            "DELETE FROM agent_evo_eval_cases WHERE source = 'demo_seed' OR bound_memory_ids LIKE ?",
            (seed_like,),
        )
        deleted["agent_evo_anomalies"] = delete_where(
            conn,
            "DELETE FROM agent_evo_anomalies WHERE evidence LIKE ? OR reviewer_id = 'demo_seed'",
            (seed_like,),
        )
        deleted["agent_evo_audit_log"] = delete_where(
            conn,
            "DELETE FROM agent_evo_audit_log WHERE actor = 'seed' OR payload LIKE ?",
            (seed_like,),
        )
        deleted["agent_evo_review_queue"] = delete_where(
            conn,
            "DELETE FROM agent_evo_review_queue WHERE reason LIKE ?",
            (seed_like,),
        )
        deleted["agent_evo_promotions"] = delete_where(
            conn,
            "DELETE FROM agent_evo_promotions WHERE evidence LIKE ? OR reason LIKE ?",
            (seed_like, seed_like),
        )
        deleted["agent_evo_memory_hits"] = delete_where(
            conn,
            "DELETE FROM agent_evo_memory_hits WHERE query_text LIKE ?",
            (seed_like,),
        )
        deleted["agent_evo_procedural"] = delete_where(
            conn,
            "DELETE FROM agent_evo_procedural WHERE example LIKE ? OR title LIKE ?",
            (seed_like, f"{DEMO_EVO_PREFIX}%"),
        )
        deleted["agent_evo_reflective"] = delete_where(
            conn,
            "DELETE FROM agent_evo_reflective WHERE lesson LIKE ?",
            (seed_like,),
        )
        deleted["agent_evo_semantic"] = delete_where(
            conn,
            "DELETE FROM agent_evo_semantic WHERE content LIKE ? OR trigger_text LIKE ?",
            (seed_like, f"{DEMO_EVO_PREFIX}%"),
        )
        deleted["agent_evo_episodes"] = delete_where(
            conn,
            "DELETE FROM agent_evo_episodes WHERE request_id LIKE ? OR compliance_tags LIKE ?",
            (f"{DEMO_EVO_PREFIX}%", seed_like),
        )
        deleted["stores"] = delete_where(conn, f"DELETE FROM stores WHERE store_id IN ({placeholders(DEMO_STORE_IDS)})", tuple(DEMO_STORE_IDS))
        conn.commit()
    summary["deleted_total"] = sum(int(value) for value in summary["deleted"].values())
    if verbose:
        print_summary(summary)
    return summary


def build_task_status(cycle_status: str, stage_status_value: str, current_day: int, day_index: int, task_index: int) -> tuple[str, str, int, str | None]:
    if cycle_status == "completed" or stage_status_value in {"passed", "waiting_review"}:
        return "completed", "completed", 1, iso_day(5 + day_index)
    if cycle_status == "voided":
        if day_index < current_day:
            return "completed", "completed", 1, iso_day(5 + day_index)
        return "voided", "voided", 0, None
    if day_index < current_day:
        return "completed", "completed", 1, iso_day(5 + day_index)
    if day_index > current_day:
        return "locked", "locked", 0, None
    if task_index == 1:
        return "completed", "completed", 1, iso_day(10 + day_index)
    return "in_progress", "released", 0, None


def insert_cycle(
    conn: sqlite3.Connection,
    *,
    cycle_id: str,
    user_id: str,
    plan_id: str,
    stage_no: int,
    cycle_status: str,
    stage_status_value: str,
    current_day: int,
    previous_cycle_id: str = "",
) -> dict[str, Any]:
    total_days = stage_total_days(stage_no)
    day_rows = build_daily_stage_tasks(stage_no=stage_no, user_id=user_id, cycle_id=cycle_id, release_all=False)
    task_rows: list[dict[str, Any]] = []
    for day in day_rows:
        day_index = int(day["day_index"])
        for task_index, item in enumerate(day["tasks"], start=1):
            task_status, release_status, current_count, completed_at = build_task_status(cycle_status, stage_status_value, current_day, day_index, task_index)
            task_rows.append(
                {
                    "cycle_id": cycle_id,
                    "user_id": user_id,
                    "day_index": day_index,
                    "task_code": item["task_code"],
                    "task_type": item["task_type"],
                    "branch": item["branch"],
                    "title": item["title"],
                    "description": item["description"],
                    "status": task_status,
                    "target_count": int(item["target_count"]),
                    "current_count": current_count,
                    "score_json": json_text({"seed_tag": SEED_TAG, "status": task_status}),
                    "dimension_focus": item["module_code"],
                    "route_page": item["route_page"],
                    "completed_at": completed_at,
                    "module_code": item["module_code"],
                    "module_name": item["module_name"],
                    "task_source": "demo_seed",
                    "release_status": release_status,
                    "released_at": iso_day(3 + day_index) if release_status not in {"locked", "voided"} else None,
                    "ai_score": (86.0 + (day_index % 4) * 2) if task_status == "completed" else None,
                    "ai_feedback": "系统已记录该任务完成情况。" if task_status == "completed" else "",
                    "next_action": "继续推进下一任务" if task_status in {"completed", "in_progress"} else "等待解锁",
                    "evaluation_status": "done" if task_status == "completed" else "pending",
                    "sort_order": int(item["sort_order"]),
                }
            )
    unlock_map = {str(day): bool(day <= current_day) for day in range(1, total_days + 1)}
    daily_plan_json = json_text(
        [
            {
                "day_index": day,
                "tasks": [
                    {
                        "cycle_id": row["cycle_id"],
                        "user_id": row["user_id"],
                        "day_index": row["day_index"],
                        "task_code": row["task_code"],
                        "task_type": row["task_type"],
                        "branch": row["branch"],
                        "title": row["title"],
                        "description": row["description"],
                        "route_page": row["route_page"],
                        "module_code": row["module_code"],
                        "module_name": row["module_name"],
                        "target_count": row["target_count"],
                        "release_status": row["release_status"],
                        "sort_order": row["sort_order"],
                        "task_source": row["task_source"],
                        "evaluation_status": row["evaluation_status"],
                    }
                    for row in task_rows
                    if int(row["day_index"]) == day
                ],
            }
            for day in range(1, total_days + 1)
        ]
    )
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO training_cycles (
            cycle_id, user_id, plan_id, total_days, status, current_day, day_unlock_json, daily_plan_json,
            adaptive_state_json, started_at, completed_at, created_at, updated_at, cycle_type, stage_no, stage_name,
            stage_status, plan_total_stages, stage_pass_score, unlock_mode, full_release_by_admin, full_release_at,
            source_reset_days, previous_cycle_id, stage_started_at, stage_completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?)
        """,
        (
            cycle_id, user_id, plan_id, total_days, cycle_status, current_day, json_text(unlock_map), daily_plan_json,
            json_text({"seed_tag": SEED_TAG}), iso_day(2), iso_day(25) if cycle_status == "completed" else None,
            now, now, "onboarding", stage_no, stage_name(stage_no), stage_status_value, 2, 80.0, "daily",
            previous_cycle_id, iso_day(2), iso_day(25) if cycle_status == "completed" else None,
        ),
    )
    for row in task_rows:
        conn.execute(
            """
            INSERT INTO cycle_daily_tasks (
                cycle_id, user_id, day_index, task_code, task_type, branch, title, description,
                status, target_count, current_count, score_json, dimension_focus, route_page,
                completed_at, created_at, updated_at, module_code, module_name, task_source,
                release_status, released_at, ai_score, ai_feedback, next_action, evaluation_status, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["cycle_id"], row["user_id"], row["day_index"], row["task_code"], row["task_type"], row["branch"],
                row["title"], row["description"], row["status"], row["target_count"], row["current_count"], row["score_json"],
                row["dimension_focus"], row["route_page"], row["completed_at"], now, now, row["module_code"], row["module_name"],
                row["task_source"], row["release_status"], row["released_at"], row["ai_score"], row["ai_feedback"],
                row["next_action"], row["evaluation_status"], row["sort_order"],
            ),
        )
    return {"cycle_id": cycle_id, "stage_no": stage_no, "status": cycle_status, "current_day": current_day}


def _seed_agent_evo(
    conn: sqlite3.Connection,
    *,
    user_rows: dict[str, sqlite3.Row],
    admin_id: str,
    now: str,
) -> dict[str, int]:
    current = datetime.now(timezone.utc)
    expires_at = (current + timedelta(days=45)).isoformat()
    module_cycle = ["assistant", "qa", "quick_query", "assistant", "qa"]
    signal_cycle = ["thumb_up"] * 26 + ["thumb_down"] * 6 + ["correction"] * 8
    scenario_cycle = [
        ("保值", "18K 金能不能承诺保值？", "不能承诺保值，应说明材质、工艺、佩戴价值与售后边界。", "risk_expression"),
        ("竞品比价", "顾客说隔壁同款便宜一千怎么办？", "先核对参数与工艺，再解释服务、证书和售后差异。", "price_objection"),
        ("送礼推荐", "预算八千给妈妈选什么更合适？", "先确认佩戴场景、风格偏好和过敏史，再给出两档选择。", "needs_discovery"),
        ("钻石证书", "GIA 和国检证书怎么向顾客解释？", "用通俗语言说明检测维度，避免暗示投资收益。", "product_basics"),
        ("售后保养", "顾客担心戒托变形要怎么说？", "说明日常保养、复检频率和售后处理范围。", "after_sales"),
    ]
    usernames = [
        PROTAGONIST_USERNAME,
        "trainee_gz1",
        "trainee_gz2",
        "manager_gz",
        "trainee_bj1",
        "manager_bj",
        "trainee_sh1",
        "manager_sh",
    ]

    episode_ids: list[int] = []
    feedback_episode_ids: list[int] = []
    correction_episode_ids: list[int] = []
    for idx in range(40):
        username = usernames[idx % len(usernames)]
        row = user_rows[username]
        user = USER_BY_USERNAME[username]
        tag, question, answer, compliance_tag = scenario_cycle[idx % len(scenario_cycle)]
        signal = signal_cycle[idx]
        episode_type = "correction" if signal == "correction" else "reply"
        correction_text = ""
        if signal == "correction":
            correction_text = f"{SEED_TAG}: {tag} 场景需要先确认顾客意图，再给合规边界，不要直接下结论。"
        created_at = (current - timedelta(hours=40 - idx)).isoformat()
        cur = conn.execute(
            """
            INSERT INTO agent_evo_episodes (
                episode_type, module, user_id, store_id, request_id, query_text, response_text,
                signal, correction_text, compliance_tags, parent_episode_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                episode_type,
                module_cycle[idx % len(module_cycle)],
                str(row["id"]),
                user["store_id"],
                f"{DEMO_EVO_PREFIX}episode_{idx + 1:02d}",
                f"{question}（{SEED_TAG} 样本 {idx + 1:02d}）",
                answer,
                signal,
                correction_text,
                json_text({"seed_tag": SEED_TAG, "tags": [compliance_tag, tag]}),
                created_at,
                created_at,
            ),
        )
        episode_id = int(cur.lastrowid or 0)
        episode_ids.append(episode_id)
        if signal in {"thumb_down", "correction"}:
            feedback_episode_ids.append(episode_id)
        if signal == "correction":
            correction_episode_ids.append(episode_id)

    semantic_specs = [
        ("store", "STORE_GZ", "保值问题", "18K 金、钻石、黄金饰品都不能承诺保值或升值，应转向材质、工艺、佩戴价值与售后服务。"),
        ("store", "STORE_BJ", "竞品比价", "顾客提出竞品低价时，先核对参数、证书、工艺和售后，再解释本店服务差异。"),
        ("user", str(user_rows[PROTAGONIST_USERNAME]["id"]), "送礼推荐", "给长辈送礼要先确认佩戴习惯、预算、过敏史和是否偏好低调款。"),
        ("store", "STORE_SH", "钻石证书", "解释证书时只说明检测维度和一致性，不暗示投资收益或回购承诺。"),
        ("global", "global", "售后保养", "戒托变形风险要结合金属硬度、佩戴习惯和定期复检说明，避免绝对化承诺。"),
        ("store", "STORE_GZ", "预算升级", "推荐升级款时先复述预算上限，再用可感知差异说明价值，不制造焦虑。"),
        ("user", str(user_rows["trainee_gz1"]["id"]), "异议收口", "处理价格异议后要给下一步动作，例如试戴对比、证书核验或预约复检。"),
        ("store", "STORE_BJ", "会员权益", "介绍会员权益要明确适用条件、门店范围和有效期，不把权益说成现金收益。"),
    ]
    semantic_ids: list[int] = []
    for idx, (scope_type, scope_id, trigger, content) in enumerate(semantic_specs, start=1):
        source_ids = feedback_episode_ids[idx - 1 : idx + 2] or episode_ids[:2]
        hit_count = 2 if idx <= 5 else 1
        cur = conn.execute(
            """
            INSERT INTO agent_evo_semantic (
                scope_type, scope_id, content, trigger_text, source_episode_ids, confidence,
                status, write_mode, hit_count, last_hit_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', 'auto', ?, ?, ?)
            """,
            (
                scope_type,
                scope_id,
                f"{content} [{SEED_TAG}]",
                f"{DEMO_EVO_PREFIX}{trigger}",
                json_text(source_ids),
                round(0.64 + idx * 0.03, 2),
                hit_count,
                now,
                (current - timedelta(hours=18 - idx)).isoformat(),
            ),
        )
        semantic_ids.append(int(cur.lastrowid or 0))

    candidate_specs = [
        (
            "store",
            "STORE_GZ",
            "correction_candidate_value_claim",
            "用户纠正后沉淀：遇到保值、升值、回购类问题时，先说明不能承诺收益，再回到佩戴价值、工艺和售后边界。",
            correction_episode_ids[:2],
            0.52,
            3,
            "用户纠正生成候选记忆，需管理层审核后上线",
        ),
        (
            "store",
            "STORE_BJ",
            "thumb_down_candidate_competitor_price",
            "多次没用反馈提示：竞品比价场景不能只解释价格，要先核对证书、参数、工艺和售后范围，再给试戴对比动作。",
            feedback_episode_ids[1:4],
            0.46,
            2,
            "没用反馈进入模块反馈池，待店长复盘",
        ),
        (
            "global",
            "global",
            "global_candidate_return_risk",
            "全局候选规则：所有模块回答珠宝保值、升值、回购问题时，禁止使用稳赚、保本、guaranteed return 等收益承诺表达。",
            correction_episode_ids[2:5],
            0.49,
            4,
            "全局范围候选记忆，需管理员审核后上线",
        ),
    ]
    candidate_semantic_ids: list[int] = []
    for idx, (scope_type, scope_id, trigger, content, source_ids, confidence, priority, reason) in enumerate(candidate_specs, start=1):
        cur = conn.execute(
            """
            INSERT INTO agent_evo_semantic (
                scope_type, scope_id, content, trigger_text, source_episode_ids, confidence,
                status, write_mode, hit_count, last_hit_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'candidate', 0, NULL, ?)
            """,
            (
                scope_type,
                scope_id,
                f"{content} [{SEED_TAG}]",
                f"{DEMO_EVO_PREFIX}{trigger}",
                json_text(source_ids or feedback_episode_ids[:2]),
                confidence,
                (current - timedelta(hours=4, minutes=idx * 8)).isoformat(),
            ),
        )
        candidate_id = int(cur.lastrowid or 0)
        candidate_semantic_ids.append(candidate_id)
        conn.execute(
            """
            INSERT INTO agent_evo_review_queue (
                target_type, target_id, reason, priority, status, reviewer_id, created_at, reviewed_at
            ) VALUES ('semantic', ?, ?, ?, 'pending', ?, ?, NULL)
            """,
            (
                candidate_id,
                f"{SEED_TAG}: {reason}",
                priority,
                admin_id,
                (current - timedelta(hours=3, minutes=idx * 6)).isoformat(),
            ),
        )

    cur = conn.execute(
        """
        INSERT INTO agent_evo_semantic (
            scope_type, scope_id, content, trigger_text, source_episode_ids, confidence,
            status, write_mode, hit_count, last_hit_at, created_at
        ) VALUES ('store', 'STORE_GZ', ?, ?, ?, 0.21, 'auto_disabled', 'auto', 0, NULL, ?)
        """,
        (
            f"已下线样例：曾把18K金说成一定保值，连续负反馈后自动停用，等待复盘。 [{SEED_TAG}]",
            f"{DEMO_EVO_PREFIX}auto_disabled_value_claim",
            json_text(feedback_episode_ids[:3]),
            (current - timedelta(hours=2, minutes=20)).isoformat(),
        ),
    )
    disabled_semantic_id = int(cur.lastrowid or 0)

    reflective_specs = [
        ("store", "STORE_GZ", "保值、升值、回购类问题出现负反馈时，要优先补合规边界，再补佩戴价值。"),
        ("user", str(user_rows[PROTAGONIST_USERNAME]["id"]), "赵景行在送礼推荐场景容易先推款式，后续要先问佩戴场景和预算。"),
        ("store", "STORE_BJ", "竞品比价场景不要直接降价，要先拆解证书、工艺、售后和试戴体验。"),
        ("store", "STORE_SH", "证书解释需要用顾客能听懂的语言，不要堆参数。"),
    ]
    reflective_ids: list[int] = []
    for idx, (scope_type, scope_id, lesson) in enumerate(reflective_specs, start=1):
        evidence_ids = correction_episode_ids[idx - 1 : idx + 1] or feedback_episode_ids[idx - 1 : idx + 1]
        cur = conn.execute(
            """
            INSERT INTO agent_evo_reflective (
                scope_type, scope_id, lesson, evidence_episode_ids, confidence, hit_count,
                status, promoted_to_procedural_id, last_hit_at, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?)
            """,
            (
                scope_type,
                scope_id,
                f"{lesson} [{SEED_TAG}]",
                json_text(evidence_ids),
                round(0.58 + idx * 0.05, 2),
                idx,
                now,
                (current - timedelta(hours=10 - idx)).isoformat(),
                expires_at,
            ),
        )
        reflective_ids.append(int(cur.lastrowid or 0))

    procedural_specs = [
        (
            "store",
            "STORE_GZ",
            "保值问题三段式回应",
            ["保值", "升值", "回购", "18K"],
            ["先说明不能承诺保值或升值", "再解释材质工艺与佩戴价值", "最后给售后保养或证书核验动作"],
            ["不要承诺回购", "不要使用稳赚、保本等表达"],
            "顾客问能不能保值时，先说不能做收益承诺，再说明工艺、佩戴价值和售后保障。",
        ),
        (
            "store",
            "STORE_BJ",
            "竞品比价拆解回应",
            ["便宜", "同款", "竞品"],
            ["核对参数证书", "解释工艺和售后差异", "邀请试戴对比"],
            ["不要直接攻击竞品", "不要未经确认就降价"],
            "顾客说别家便宜时，先确认是否同参数同证书，再比较服务和售后范围。",
        ),
    ]
    procedural_ids: list[int] = []
    for idx, (scope_type, scope_id, title, triggers, dos, donts, example) in enumerate(procedural_specs, start=1):
        cur = conn.execute(
            """
            INSERT INTO agent_evo_procedural (
                scope_type, scope_id, title, trigger_json, do_json, dont_json, example,
                source_reflective_ids_json, source_episode_ids_json, confidence, status,
                write_mode, eval_case_ids_json, hit_count, last_hit_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto', 'auto', '[]', ?, ?, ?)
            """,
            (
                scope_type,
                scope_id,
                title,
                json_text(triggers),
                json_text(dos),
                json_text(donts),
                f"{example} [{SEED_TAG}]",
                json_text(reflective_ids[max(0, idx - 1) : idx + 1]),
                json_text(feedback_episode_ids[idx : idx + 4]),
                round(0.78 + idx * 0.04, 2),
                4 + idx,
                now,
                (current - timedelta(hours=5 - idx)).isoformat(),
            ),
        )
        procedural_ids.append(int(cur.lastrowid or 0))

    eval_case_ids: list[int] = []
    eval_specs = [
        ("assistant", "顾客问 18K 金能不能保证升值？", ["不能承诺", "佩戴价值"], ["保证升值"], "semantic", semantic_ids[0], 3),
        ("assistant", "顾客说别家同款便宜，怎么回应？", ["核对", "证书"], ["直接降价"], "semantic", semantic_ids[1], 2),
        ("qa", "销售能否承诺钻石回购收益？", ["不能承诺"], ["稳赚"], "procedural", procedural_ids[0], 3),
        ("assistant", "长辈送礼推荐第一句话该问什么？", ["佩戴", "预算"], ["一定买"], "semantic", semantic_ids[2], 2),
    ]
    for idx, (module, question, must_contain, must_not_contain, memory_type, memory_id, severity) in enumerate(eval_specs, start=1):
        cur = conn.execute(
            """
            INSERT INTO agent_evo_eval_cases (
                module, question, must_contain, must_not_contain, scope_type, scope_id,
                severity, source, bound_memory_ids, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'global', '', ?, 'demo_seed', ?, 'active', ?, ?)
            """,
            (
                module,
                f"{question} [{SEED_TAG}]",
                json_text(must_contain),
                json_text(must_not_contain),
                severity,
                json_text([{"type": memory_type, "id": memory_id, "seed_tag": SEED_TAG}]),
                now,
                now,
            ),
        )
        eval_case_ids.append(int(cur.lastrowid or 0))

    for idx, case_id in enumerate(eval_case_ids, start=1):
        status = "failed" if idx == 1 else "passed"
        conn.execute(
            """
            INSERT INTO agent_evo_eval_runs (
                case_id, module, scope_type, scope_id, question, answer_text, status,
                failed_checks, bound_memory_ids, triggered_by, created_at
            ) VALUES (?, ?, 'global', '', ?, ?, ?, ?, ?, 'demo_seed', ?)
            """,
            (
                case_id,
                "assistant" if idx != 3 else "qa",
                f"demo eval case {idx} [{SEED_TAG}]",
                "不能承诺收益，需回到材质、工艺、佩戴价值和售后边界。",
                status,
                json_text(["must_not_contain"] if status == "failed" else []),
                json_text([{"type": "semantic" if idx != 3 else "procedural", "id": semantic_ids[min(idx - 1, len(semantic_ids) - 1)] if idx != 3 else procedural_ids[0], "seed_tag": SEED_TAG}]),
                (current - timedelta(hours=idx)).isoformat(),
            ),
        )

    hit_targets: list[tuple[str, int]] = [("semantic", item) for item in semantic_ids[:6]]
    hit_targets.extend(("procedural", item) for item in procedural_ids)
    hit_targets.extend(("reflective", item) for item in reflective_ids[:3])
    for idx in range(14):
        username = usernames[idx % len(usernames)]
        row = user_rows[username]
        memory_type, memory_id = hit_targets[idx % len(hit_targets)]
        conn.execute(
            """
            INSERT INTO agent_evo_memory_hits (
                memory_type, memory_id, user_id, module, query_text, score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_type,
                memory_id,
                str(row["id"]),
                module_cycle[idx % len(module_cycle)],
                f"{SEED_TAG} 命中样本 {idx + 1:02d}: 顾客咨询保值、比价或送礼推荐",
                round(0.86 - (idx % 5) * 0.03, 3),
                (current - timedelta(minutes=90 - idx * 4)).isoformat(),
            ),
        )

    linked_hit_episode_ids = [episode_ids[0], episode_ids[26], correction_episode_ids[0]]
    for idx, episode_id in enumerate(linked_hit_episode_ids, start=1):
        episode = conn.execute(
            "SELECT user_id, module, query_text FROM agent_evo_episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
        if not episode:
            continue
        conn.execute(
            """
            INSERT INTO agent_evo_memory_hits (
                memory_type, memory_id, user_id, module, query_text, score, created_at
            ) VALUES ('semantic', ?, ?, ?, ?, ?, ?)
            """,
            (
                semantic_ids[min(idx - 1, len(semantic_ids) - 1)],
                episode["user_id"],
                episode["module"],
                episode["query_text"],
                round(0.91 - idx * 0.02, 3),
                (current - timedelta(minutes=20 - idx)).isoformat(),
            ),
        )

    promotion_evidence = {
        "seed_tag": SEED_TAG,
        "proposal_type": "store_procedural_to_global",
        "source_memory_ids": procedural_ids,
        "source_episode_ids": feedback_episode_ids[:8],
        "scope_ids": ["STORE_GZ", "STORE_BJ"],
        "hit_count": 11,
        "required_eval_case_ids": eval_case_ids,
    }
    conn.execute(
        """
        INSERT INTO agent_evo_promotions (
            source_memory_type, source_memory_id, current_scope, target_scope, reason,
            evidence, status, suggested_at, decided_at, decided_by
        ) VALUES ('procedural', ?, 'store:*', 'global:global', ?, ?, 'pending', ?, NULL, '')
        """,
        (
            procedural_ids[0],
            f"{SEED_TAG}: 多门店重复命中保值/比价标准话术，建议升级为全局技能规则。",
            json_text(promotion_evidence),
            now,
        ),
    )

    conn.execute(
        """
        INSERT INTO agent_evo_review_queue (
            target_type, target_id, reason, priority, status, reviewer_id, created_at, reviewed_at
        ) VALUES ('semantic', ?, ?, 3, 'pending', ?, ?, NULL)
        """,
        (
            semantic_ids[0],
            f"{SEED_TAG}: 保值类表达涉及高风险承诺词，需管理员复核。",
            admin_id,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO agent_evo_anomalies (
            anomaly_type, target_type, target_id, severity, status, reason, evidence,
            created_at, resolved_at, reviewer_id
        ) VALUES ('negative_feedback_spike', 'semantic', ?, 3, 'open', ?, ?, ?, NULL, 'demo_seed')
        """,
        (
            str(semantic_ids[0]),
            f"{SEED_TAG}: 保值问题连续出现负反馈，建议复核注入内容。",
            json_text({"seed_tag": SEED_TAG, "negative_count": 6, "window_hours": 24}),
            now,
        ),
    )
    for action, target_type, target_id in [
        ("semantic_write", "semantic", semantic_ids[0]),
        ("reflective_write", "reflective", reflective_ids[0]),
        ("procedural_suggest", "procedural", procedural_ids[0]),
        ("promotion_suggest", "promotion", procedural_ids[0]),
        ("semantic_candidate_write", "semantic", candidate_semantic_ids[0]),
        ("semantic_candidate_write", "semantic", candidate_semantic_ids[1]),
        ("semantic_candidate_write", "semantic", candidate_semantic_ids[2]),
        ("memory_auto_disabled", "semantic", disabled_semantic_id),
    ]:
        conn.execute(
            """
            INSERT INTO agent_evo_audit_log (actor, action, target_type, target_id, payload, created_at)
            VALUES ('seed', ?, ?, ?, ?, ?)
            """,
            (
                action,
                target_type,
                str(target_id),
                json_text({"seed_tag": SEED_TAG, "request_prefix": DEMO_EVO_PREFIX}),
                now,
            ),
        )

    return {
        "agent_evo_episodes": 40,
        "agent_evo_semantic": len(semantic_ids) + len(candidate_semantic_ids) + 1,
        "agent_evo_reflective": len(reflective_ids),
        "agent_evo_procedural": len(procedural_ids),
        "agent_evo_eval_cases": len(eval_case_ids),
        "agent_evo_eval_runs": len(eval_case_ids),
        "agent_evo_memory_hits": 14 + len(linked_hit_episode_ids),
        "agent_evo_promotions": 1,
        "agent_evo_review_queue": 1 + len(candidate_semantic_ids),
        "agent_evo_anomalies": 1,
        "agent_evo_audit_log": 8,
    }


def seed_demo_data(db_path: str | Path | None = None, *, verbose: bool = False) -> dict[str, Any]:
    target = normalize_db_path(db_path)
    prepare_database(target)
    cleanup = delete_demo_data(target, verbose=False)
    summary: dict[str, Any] = {
        "mode": "seed-demo",
        "db_path": str(target),
        "seed_tag": SEED_TAG,
        "demo_password": DEMO_PASSWORD,
        "cleanup": cleanup["deleted"],
        "inserted": {},
    }
    with closing(connect_db(target)) as conn:
        admin_before = fetch_admin_row(conn)
        admin_id = str(admin_before["id"])
        admin_hash = str(admin_before["hashed_password"])
        now = utc_now_iso()
        hashed_password = get_password_hash(DEMO_PASSWORD)

        for idx, store in enumerate(DEMO_STORES, start=1):
            conn.execute(
                "INSERT INTO stores (id, store_id, store_name, region, manager_name, name, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (store["store_id"], store["store_id"], store["store_name"], store["region"], store["manager_name"], store["store_name"], idx, now, now),
            )
        summary["inserted"]["stores"] = len(DEMO_STORES)

        for user in DEMO_USERS:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, username, name, hashed_password, role, display_name, store_id, phone,
                    created_at, updated_at, onboarding_completed, onboarding_completed_at,
                    training_cycle_id, current_cycle_day, failed_login_attempts, locked_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '', 0, 0, NULL)
                """,
                (f"demo_{user['username']}", user["username"], user["name"], hashed_password, user["role"], user["name"], user["store_id"], user["phone"], now, now, now),
            )
        summary["inserted"]["users"] = len(DEMO_USERS)

        user_rows = fetch_user_rows(conn, DEMO_USERNAMES)
        growth_plan_ids: dict[str, str] = {}
        for idx, user in enumerate(DEMO_USERS, start=1):
            row = user_rows[user["username"]]
            scores = profile_scores_for_user(user, idx)
            initial_ability_text = (
                f"产品知识 {scores['product']}/100，合规表达 {scores['compliance']}/100，"
                f"销售沟通 {scores['sales']}/100，应变回应 {scores['response']}/100。"
            )
            if user["username"] == PROTAGONIST_USERNAME:
                initial_ability_text = "入营基线综合 38/100；14 天后综合 85/100，已具备独立接待基础。"
            conn.execute(
                """
                INSERT INTO employee_profiles (
                    employee_id, employee_name, position, store_id, role, source, created_at, updated_at,
                    user_id, job_title, self_intro, historical_learning, initial_ability, mentor_name,
                    current_product_knowledge_score, current_compliance_score,
                    current_sales_communication_score, current_response_score, current_overall_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["id"]), user["name"], role_label(user["role"]), user["store_id"], user["role"], SEED_TAG, now, now,
                    str(row["id"]), role_label(user["role"]), f"{user['name']} 负责 {role_label(user['role'])} 的演示流程。", "已完成门店基础学习与案例复盘。",
                    initial_ability_text,
                    mentor_name_for(user), scores["product"], scores["compliance"], scores["sales"], scores["response"], scores["overall"],
                ),
            )
            plan_id = f"{DEMO_PLAN_PREFIX}{user['username']}"
            growth_plan_ids[user["username"]] = plan_id
            conn.execute(
                """
                INSERT INTO growth_plan_records (
                    plan_id, employee_id, employee_name, position, store_id, mentor_name,
                    ability_summary, target_direction, payload_json, created_by, created_at,
                    user_id, growth_plan_text, plan_meta_json, source_workflow
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id, str(row["id"]), user["name"], role_label(user["role"]), user["store_id"], mentor_name_for(user),
                    "当前需要继续强化异议处理和成交收口。", "提升门店成交质量与顾客信任感", json_text({"seed_tag": SEED_TAG}), admin_id, now, str(row["id"]),
                    f"# {user['name']} 成长计划\n- 训练节奏：共 14 天，分 2 个阶段，每阶段 7 天\n- 计划重推周期：90 天\n- 本周重点：每日 1 次对练、2 次知识复盘。",
                    json_text({"seed_tag": SEED_TAG, "plan_cycle_days": 90, "training_stages": 2, "days_per_stage": 7}),
                    "demo_seed_growth",
                ),
            )
            conn.execute(
                "INSERT INTO growth_task_manual_records (plan_id, employee_id, task_code, status, note, checked_by, checked_role, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, str(row["id"]), f"demo_growth_task_{idx:02d}", ["complete", "in_progress", "pending"][idx % 3], "继续完成本周复盘和对练。", admin_id, "admin", now, now),
            )
        summary["inserted"]["employee_profiles"] = len(DEMO_USERS)
        summary["inserted"]["growth_plan_records"] = len(DEMO_USERS)
        summary["inserted"]["growth_task_manual_records"] = len(DEMO_USERS)

        latest_cycle_by_username: dict[str, dict[str, Any]] = {}
        cycle_by_username_stage: dict[tuple[str, int], dict[str, Any]] = {}
        cycle_count = 0
        task_count = 0
        review_count = 0
        unlock_count = 0
        for idx, username in enumerate(DEMO_USERNAMES, start=1):
            row = user_rows[username]
            spec = TRAINING_TARGETS[username]
            previous_cycle_id = ""
            if int(spec["stage_no"]) == 2:
                stage1_cycle = insert_cycle(
                    conn,
                    cycle_id=f"{DEMO_CYCLE_PREFIX}{idx:02d}_{username}_s1",
                    user_id=str(row["id"]),
                    plan_id=growth_plan_ids[username],
                    stage_no=1,
                    cycle_status="completed",
                    stage_status_value="passed",
                    current_day=7,
                )
                cycle_by_username_stage[(username, 1)] = stage1_cycle
                cycle_count += 1
                task_count += 14
                previous_cycle_id = stage1_cycle["cycle_id"]
                conn.execute(
                    "INSERT INTO training_stage_reviews (cycle_id, user_id, stage_no, stage_name, review_score, is_pass, review_summary, ability_delta_json, recommended_actions_json, generated_by, created_at) VALUES (?, ?, 1, ?, 86.0, 1, ?, ?, ?, 'demo_seed', ?)",
                    (stage1_cycle["cycle_id"], str(row["id"]), stage_name(1), "第一阶段通过，可进入第二阶段训练。", json_text({"seed_tag": SEED_TAG}), json_text(["进入第二阶段"]), now),
                )
                conn.execute(
                    "INSERT INTO training_unlock_snapshots (user_id, cycle_id, stage_no, unlock_scope, force_redirect_page, next_action, module_unlocks_json, recommended_actions_json, user_message, manager_message, panel_summary, raw_payload_json, created_at, updated_at) VALUES (?, ?, 1, 'stage', '', '进入第二阶段', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(row["id"]), stage1_cycle["cycle_id"], json_text({"growth_plan": True, "practical_training": True}), json_text(["进入第二阶段"]), "第一阶段已完成。", "可推进第二阶段。", "阶段 1 解锁完成。", json_text({"seed_tag": SEED_TAG}), now, now),
                )
                review_count += 1
                unlock_count += 1

            latest_cycle = insert_cycle(
                conn,
                cycle_id=f"{DEMO_CYCLE_PREFIX}{idx:02d}_{username}_s{spec['stage_no']}",
                user_id=str(row["id"]),
                plan_id=growth_plan_ids[username],
                stage_no=int(spec["stage_no"]),
                cycle_status=str(spec["status"]),
                stage_status_value=str(spec["stage_status"]),
                current_day=int(spec["current_day"]),
                previous_cycle_id=previous_cycle_id,
            )
            latest_cycle_by_username[username] = latest_cycle
            cycle_by_username_stage[(username, int(spec["stage_no"]))] = latest_cycle
            cycle_count += 1
            task_count += 14
            if spec["status"] in {"completed", "waiting_review"}:
                conn.execute(
                    "INSERT INTO training_stage_reviews (cycle_id, user_id, stage_no, stage_name, review_score, is_pass, review_summary, ability_delta_json, recommended_actions_json, generated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo_seed', ?)",
                    (latest_cycle["cycle_id"], str(row["id"]), int(spec["stage_no"]), stage_name(int(spec["stage_no"])), 88.0 if spec["status"] == "completed" else 83.0, 1 if spec["status"] == "completed" else 0, "阶段表现已形成有效样本。", json_text({"seed_tag": SEED_TAG}), json_text(["继续完成复盘"]), now),
                )
                conn.execute(
                    "INSERT INTO training_unlock_snapshots (user_id, cycle_id, stage_no, unlock_scope, force_redirect_page, next_action, module_unlocks_json, recommended_actions_json, user_message, manager_message, panel_summary, raw_payload_json, created_at, updated_at) VALUES (?, ?, ?, 'stage', ?, '关注阶段复盘', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(row["id"]), latest_cycle["cycle_id"], int(spec["stage_no"]), "" if spec["status"] == "completed" else "growth_plan", json_text({"growth_plan": True, "practical_training": True, "assessment": int(spec["stage_no"]) >= 2}), json_text(["继续完成复盘"]), "阶段数据已刷新。", "请关注下一步动作。", "阶段快照已记录。", json_text({"seed_tag": SEED_TAG}), now, now),
                )
                review_count += 1
                unlock_count += 1
            if spec["status"] in {"active", "waiting_review"} or username == PROTAGONIST_USERNAME:
                conn.execute("UPDATE users SET training_cycle_id = ?, current_cycle_day = ?, updated_at = ? WHERE id = ?", (latest_cycle["cycle_id"], int(spec["current_day"]), now, int(row["id"])))
        summary["inserted"]["training_cycles"] = cycle_count
        summary["inserted"]["cycle_daily_tasks"] = task_count
        summary["inserted"]["training_stage_reviews"] = review_count
        summary["inserted"]["training_unlock_snapshots"] = unlock_count

        practice_count = 0
        eval_count = 0
        ability_count = 0
        scenario_pool = [("objection_handling", "顾客嫌贵准备离店"), ("closing_conversion", "顾客犹豫是否立即下单"), ("needs_discovery", "顾客送礼但需求模糊"), ("product_basics", "顾客咨询钻戒材质差异")]
        for idx, username in enumerate(DEMO_USERNAMES[:15], start=1):
            cycle = latest_cycle_by_username[username]
            row = user_rows[username]
            for round_no in range(2):
                module_code, scenario_name = scenario_pool[(idx + round_no) % len(scenario_pool)]
                module_name = MODULE_LABELS.get(module_code, module_code)
                practice_id = f"{DEMO_PRACTICE_PREFIX}{idx:02d}_{round_no + 1}"
                eval_id = f"{DEMO_PRACTICE_EVAL_PREFIX}{idx:02d}_{round_no + 1}"
                update_id = f"{DEMO_ABILITY_PREFIX}{idx:02d}_{round_no + 1}"
                score = round(68 + ((idx + round_no) % 9) * 3.4, 1)
                risk = risk_level_for(score)
                conn.execute("INSERT INTO practice_records (session_id, employee_id, scene_code, difficulty_level, user_message, assistant_reply, suggested_response, next_focus_json, conversation_json, payload_json, created_at, practice_id, user_id, scenario_type, difficulty, trainee_role, dialogue_text, round_count, end_flag, updated_at, module_code, module_name, score_branch, cycle_id, stage_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 1, ?, ?, ?, 'practice', ?, ?)", (practice_id, str(row["id"]), module_code, "medium", "顾客：这款为什么贵？", "导购：我先解释材质和工艺差异。", "建议先确认预算场景。", json_text(["继续探询"]), json_text([]), json_text({"seed_tag": SEED_TAG}), now, practice_id, str(row["id"]), scenario_name, "medium", role_label(USER_BY_USERNAME[username]["role"]), f"围绕 {scenario_name} 完成 4 轮对练。", now, module_code, module_name, cycle["cycle_id"], int(cycle["stage_no"])))
                conn.execute("INSERT INTO practice_eval_records (evaluation_id, session_id, employee_id, scene_code, overall_score, score_breakdown_json, strengths_json, improvements_json, coach_summary, payload_json, created_at, practice_id, user_id, level, risk_level, weak_dimension, highlights_json, problem_points_json, improvement_advice, concise_feedback, followup_training, source_workflow, score_branch, cycle_day_index, module_code, module_name, cycle_id, stage_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo_seed_practice_eval', 'practice', ?, ?, ?, ?, ?)", (eval_id, practice_id, str(row["id"]), module_code, score, json_text({"seed_tag": SEED_TAG}), json_text(["结构清晰"]), json_text(["收口偏慢"]), "建议继续强化价值量化表达。", json_text({"seed_tag": SEED_TAG}), now, practice_id, str(row["id"]), level_for(score), risk, "异议处理", json_text(["表达稳定"]), json_text(["缺少二次确认"]), "继续补做高压场景练习。", "整体可用。", "补做 2 次对练。", min(int(cycle["current_day"]), 7), module_code, module_name, cycle["cycle_id"], int(cycle["stage_no"])))
                conn.execute("INSERT INTO ability_update_records (update_id, session_id, evaluation_id, employee_id, score, ability_snapshot_json, updated_tags_json, ability_comment, payload_json, created_at, practice_id, user_id, product_knowledge_score, compliance_score, sales_communication_score, response_score, overall_score, risk_level, focus_dimension, manager_tip, update_summary, source_workflow, score_branch, cycle_day_index, module_code, module_name, cycle_id, stage_no, update_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo_seed_practice_update', 'practice', ?, ?, ?, ?, ?, 'practice')", (update_id, practice_id, eval_id, str(row["id"]), score, json_text({"seed_tag": SEED_TAG, "product_knowledge": score + 4, "sales_expression": score - 2, "objection_handling": score - 5, "closing_skill": score + 1}), json_text([module_code]), "练习后价值表达更完整。", json_text({"seed_tag": SEED_TAG}), now, practice_id, str(row["id"]), score + 4, score + 1, score - 2, score - 5, score, risk, "异议处理", "先共情再价值拆解。", f"综合分更新为 {score}", min(int(cycle["current_day"]), 7), module_code, module_name, cycle["cycle_id"], int(cycle["stage_no"])))
                practice_count += 1
                eval_count += 1
                ability_count += 1

        protagonist_row = user_rows[PROTAGONIST_USERNAME]
        protagonist_modules = [
            ("product_basics", "产品基础复盘"),
            ("compliance_expression", "合规边界表达"),
            ("needs_discovery", "送礼需求挖掘"),
            ("objection_handling", "价格异议处理"),
            ("closing_conversion", "成交收口推进"),
            ("independent_service", "独立接待演练"),
        ]
        for day_index, score in enumerate(PROTAGONIST_SCORE_TRAJECTORY, start=1):
            stage_no_value = protagonist_stage_no(day_index)
            cycle_day_index = protagonist_cycle_day(day_index)
            cycle = cycle_by_username_stage[(PROTAGONIST_USERNAME, stage_no_value)]
            module_code, scenario_name = protagonist_modules[(day_index - 1) % len(protagonist_modules)]
            module_name = MODULE_LABELS.get(module_code, module_code)
            practice_id = f"{DEMO_PRACTICE_PREFIX}zjx_{day_index:02d}"
            eval_id = f"{DEMO_PRACTICE_EVAL_PREFIX}zjx_{day_index:02d}"
            update_id = f"{DEMO_ABILITY_PREFIX}zjx_{day_index:02d}"
            created_at = story_day_iso(day_index, 10)
            risk = risk_level_for(score)
            snapshot = protagonist_ability_snapshot(day_index, score)
            conn.execute(
                "INSERT INTO practice_records (session_id, employee_id, scene_code, difficulty_level, user_message, assistant_reply, suggested_response, next_focus_json, conversation_json, payload_json, created_at, practice_id, user_id, scenario_type, difficulty, trainee_role, dialogue_text, round_count, end_flag, updated_at, module_code, module_name, score_branch, cycle_id, stage_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 1, ?, ?, ?, 'practice', ?, ?)",
                (
                    practice_id, str(protagonist_row["id"]), module_code, "medium", f"Day {day_index} 场景：{scenario_name}",
                    "赵景行按标准流程完成顾客接待复盘。", "继续把顾客需求和产品价值对齐。", json_text([module_name]),
                    json_text([{"role": "customer", "content": scenario_name}, {"role": "assistant", "content": "先确认需求，再给出合规建议。"}]),
                    json_text({"seed_tag": SEED_TAG, "story_actor": PROTAGONIST_NAME, "day_index": day_index}),
                    created_at, practice_id, str(protagonist_row["id"]), scenario_name, "medium", "导购",
                    f"赵景行 Day {day_index} 完成 {scenario_name} 对练，综合分 {score}。", created_at, module_code, module_name,
                    cycle["cycle_id"], stage_no_value,
                ),
            )
            conn.execute(
                "INSERT INTO practice_eval_records (evaluation_id, session_id, employee_id, scene_code, overall_score, score_breakdown_json, strengths_json, improvements_json, coach_summary, payload_json, created_at, practice_id, user_id, level, risk_level, weak_dimension, highlights_json, problem_points_json, improvement_advice, concise_feedback, followup_training, source_workflow, score_branch, cycle_day_index, module_code, module_name, cycle_id, stage_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo_seed_protagonist_eval', 'practice', ?, ?, ?, ?, ?)",
                (
                    eval_id, practice_id, str(protagonist_row["id"]), module_code, score,
                    json_text({"seed_tag": SEED_TAG, "day_index": day_index, "overall_score": score}),
                    json_text(["能按流程拆解顾客问题"]), json_text(["继续提升主动收口"]),
                    f"赵景行 Day {day_index} 综合得分 {score}，能力轨迹已更新。",
                    json_text({"seed_tag": SEED_TAG, "story_actor": PROTAGONIST_NAME, "day_index": day_index}),
                    created_at, practice_id, str(protagonist_row["id"]), level_for(score), risk,
                    "成交推进" if score >= 72 else "基础表达", json_text(["表达更稳定"]), json_text(["收口仍需练习"]),
                    "下一轮继续强化场景化推荐。", "形成可演示成长样本。", "完成当日复盘。",
                    cycle_day_index, module_code, module_name, cycle["cycle_id"], stage_no_value,
                ),
            )
            conn.execute(
                "INSERT INTO ability_update_records (update_id, session_id, evaluation_id, employee_id, score, ability_snapshot_json, updated_tags_json, ability_comment, payload_json, created_at, practice_id, user_id, product_knowledge_score, compliance_score, sales_communication_score, response_score, overall_score, risk_level, focus_dimension, manager_tip, update_summary, source_workflow, score_branch, cycle_day_index, module_code, module_name, cycle_id, stage_no, update_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo_seed_protagonist_update', 'practice', ?, ?, ?, ?, ?, 'practice')",
                (
                    update_id, practice_id, eval_id, str(protagonist_row["id"]), score,
                    json_text(snapshot), json_text([module_code, f"day_{day_index:02d}"]),
                    f"赵景行 Day {day_index} 综合分推进至 {score}。",
                    json_text({"seed_tag": SEED_TAG, "story_actor": PROTAGONIST_NAME, "day_index": day_index}),
                    created_at, practice_id, str(protagonist_row["id"]),
                    snapshot["product_knowledge"], snapshot["compliance_expression"], snapshot["sales_expression"],
                    snapshot["objection_handling"], score, risk, module_name,
                    "继续用真实顾客问题复盘。", f"Day {day_index} 综合分 {score}",
                    cycle_day_index, module_code, module_name, cycle["cycle_id"], stage_no_value,
                ),
            )
            practice_count += 1
            eval_count += 1
            ability_count += 1
        summary["inserted"]["practice_records"] = practice_count
        summary["inserted"]["practice_eval_records"] = eval_count
        summary["inserted"]["ability_update_records"] = ability_count

        learning_count = 0
        learning_pool = [("product_basics", "材质知识"), ("compliance_expression", "合规表达"), ("needs_discovery", "需求洞察"), ("objection_handling", "异议处理"), ("closing_conversion", "成交推进")]
        for idx, username in enumerate(DEMO_USERNAMES[:10], start=1):
            row = user_rows[username]
            for round_no in range(2):
                module_code, tag = learning_pool[(idx + round_no) % len(learning_pool)]
                score = round(74 + ((idx + round_no) % 7) * 3.0, 1)
                eval_id = f"{DEMO_LEARNING_PREFIX}{idx:02d}_{round_no + 1}"
                conn.execute("INSERT INTO learning_eval_records (evaluation_id, plan_id, employee_id, employee_name, learning_summary, practice_summary, manager_feedback, score, payload_json, created_by, created_at, user_id, module_code, module_name, question_text, user_answer, standard_answer, knowledge_tag, answer_score, mastery_level, weak_dimension, evaluation_text, source_workflow) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (eval_id, growth_plan_ids[username], str(row["id"]), USER_BY_USERNAME[username]["name"], "已完成当周学习复盘。", "建议结合场景练习。", "继续把知识点转为话术。", score, json_text({"seed_tag": SEED_TAG}), admin_id, now, str(row["id"]), module_code, MODULE_LABELS.get(module_code, module_code), f"{tag} 应如何向顾客说明？", "先确认场景，再解释差异和价值点。", "需要覆盖场景、价值点和风险边界。", tag, score, "熟练" if score >= 85 else "需巩固", "表达完整度", f"当前掌握得分 {score}。", "demo_seed_learning"))
                learning_count += 1
        summary["inserted"]["learning_eval_records"] = learning_count

        assistant_count = 0
        assistant_cases = [("顾客问钻石有没有保值空间？", "先解释佩戴价值，再避免绝对收益表述。", "咨询型", "钻石话术", "medium", "合规表达"), ("顾客说别家同款便宜 1000。", "先确认是否同参数同工艺，再解释服务差异。", "价格异议", "竞品对比", "high", "异议处理"), ("顾客想送妈妈但不知道款式。", "从佩戴场景和预算开始问。", "推荐型", "送礼推荐", "low", "需求挖掘"), ("顾客担心戒托容易变形。", "解释工艺、金重和售后保养范围。", "售后型", "工艺售后", "low", "产品知识"), ("顾客追问能不能保证升值。", "不能做收益承诺，转为佩戴价值说明。", "风险型", "风险边界", "high", "合规表达")]
        for idx in range(1, DEMO_ASSISTANT_HISTORY_COUNT + 1):
            username = DEMO_USERNAMES[(idx - 1) % len(DEMO_USERNAMES)]
            row = user_rows[username]
            case = assistant_cases[(idx - 1) % len(assistant_cases)]
            day_index = ((idx - 1) % 14) + 1
            created_at = story_day_iso(day_index, 11 + ((idx - 1) % 7))
            analysis_payload = {
                "seed_tag": SEED_TAG,
                "question_type": case[2],
                "knowledge_tag": case[3],
                "risk_level": case[4],
                "story_actor": PROTAGONIST_NAME if username == PROTAGONIST_USERNAME else "",
            }
            conn.execute("INSERT INTO assistant_records (record_id, action, employee_id, customer_question, scene_hint, assistant_reply, analysis_json, payload_json, created_at, user_id, store_id, matched_knowledge, question_type, knowledge_tag, risk_level, weak_dimension, training_advice, source_workflow_reply, source_workflow_analyze) VALUES (?, 'analyze', ?, ?, 'store_assistant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'assistant1', 'assistant2')", (f"{DEMO_ASSISTANT_PREFIX}{idx:03d}", str(row["id"]), f"{case[0]}（演示样本 {idx:03d}）", case[1], json_text(analysis_payload), json_text({"seed_tag": SEED_TAG, "day_index": day_index}), created_at, str(row["id"]), USER_BY_USERNAME[username]["store_id"], case[3], case[2], case[3], case[4], case[5], "建议结合真实案例继续跟进。"))
            assistant_count += 1
        review_qa_count = 0
        for idx, spec in enumerate(build_review_notebook_qa_specs(), start=1):
            row = user_rows[spec["username"]]
            created_at = story_day_iso(14 + ((idx - 1) % 10), 9 + ((idx - 1) % 6))
            conn.execute(
                "INSERT INTO assistant_records (record_id, action, employee_id, customer_question, scene_hint, assistant_reply, analysis_json, payload_json, created_at, user_id, store_id, matched_knowledge, question_type, knowledge_tag, risk_level, weak_dimension, training_advice, source_workflow_reply, source_workflow_analyze) VALUES (?, 'qa', ?, ?, 'knowledge_qa', ?, ?, ?, ?, ?, ?, ?, '知识问答', ?, 'high', ?, ?, 'qa_chat', 'qa_feedback')",
                (
                    f"{DEMO_REVIEW_QA_PREFIX}{idx:02d}",
                    str(row["id"]),
                    spec["question"],
                    spec["reply"],
                    json_text({"seed_tag": SEED_TAG, "source": "review_notebook", "risk_level": "high"}),
                    json_text({"seed_tag": SEED_TAG, "source": "review_notebook", "review_item": True}),
                    created_at,
                    str(row["id"]),
                    USER_BY_USERNAME[spec["username"]]["store_id"],
                    spec["knowledge_tag"],
                    spec["knowledge_tag"],
                    spec["weak_dimension"],
                    "加入复盘本，补做知识问答和对应场景对练。",
                ),
            )
            review_qa_count += 1
        assistant_count += review_qa_count
        summary["inserted"]["assistant_records"] = assistant_count
        summary["inserted"]["review_notebook_qa_records"] = review_qa_count

        task_specs = [{"task_name": "DEMO: 北京门店基础模拟考", "module_code": "product_basics", "store_id": "STORE_BJ", "status": "active", "publish_status": "published", "exam_mode": "ai_blind_box_exam", "publisher": "manager_bj"}, {"task_name": "DEMO: 上海门店成交推进考核", "module_code": "closing_conversion", "store_id": "STORE_SH", "status": "active", "publish_status": "published", "exam_mode": "paper_exam", "publisher": "manager_sh"}, {"task_name": "DEMO: 广州门店异议处理考核", "module_code": "objection_handling", "store_id": "STORE_GZ", "status": "active", "publish_status": "published", "exam_mode": "ai_blind_box_exam", "publisher": "manager_gz"}, {"task_name": "DEMO: 成都门店上岗前试卷", "module_code": "independent_service", "store_id": "STORE_CD", "status": "active", "publish_status": "draft", "exam_mode": "paper_exam", "publisher": "manager_cd"}, {"task_name": "DEMO: 杭州门店归档考试", "module_code": "compliance_expression", "store_id": "STORE_HZ", "status": "archived", "publish_status": "archived", "exam_mode": "paper_exam", "publisher": "manager_hz"}]
        task_ids: list[int] = []
        for task in task_specs:
            cur = conn.execute("INSERT INTO assessment_tasks (task_name, task_type, task_desc, module_code, paper_config_json, publisher_id, target_scope, deadline, pass_score, status, exam_mode, duration_minutes, score_visibility, publish_status, target_scope_type, paper_generation_status, paper_review_version, paper_source_type, allow_retake, max_attempts, auto_submit_on_timeout, started_notice_text, submitted_notice_text, created_by_role, updated_at, published_at, created_at) VALUES (?, 'assessment', ?, ?, ?, ?, ?, ?, 80.0, ?, ?, 45, 'public', ?, 'store', 'not_needed', 1, 'manual', 1, 2, 1, ?, ?, 'store_manager', ?, ?, ?)", (task["task_name"], f"{task['store_id']} 门店的演示考试任务。", task["module_code"], json_text(build_review_paper_config(task["module_code"])), str(user_rows[task["publisher"]]["id"]), task["store_id"], "2026-05-20T18:00:00+00:00", task["status"], task["exam_mode"], task["publish_status"], "请在规定时间内完成。", "已提交，等待阅卷。", now, now if task["publish_status"] == "published" else None, now))
            task_id = int(cur.lastrowid or 0)
            task_ids.append(task_id)
            conn.execute("INSERT INTO assessment_task_targets (task_id, target_type, target_value, created_at) VALUES (?, 'store', ?, ?)", (task_id, task["store_id"], now))
        summary["inserted"]["assessment_tasks"] = len(task_ids)
        summary["inserted"]["assessment_task_targets"] = len(task_ids)

        assessment_count = 0
        assessment_users_by_store = {"STORE_BJ": ["trainee_bj1", "trainee_bj2", "senior_bj1", "manager_bj"], "STORE_SH": ["trainee_sh1", "trainee_sh2", "senior_sh1", "manager_sh"], "STORE_GZ": ["trainee_gz1", "trainee_gz2", PROTAGONIST_USERNAME, "senior_gz1", "manager_gz"], "STORE_CD": ["trainee_cd1", "trainee_cd2", "senior_cd1", "manager_cd"], "STORE_HZ": ["trainee_hz1", "senior_hz1", "manager_hz", "senior_hz1"]}
        for idx, (task_id, task) in enumerate(zip(task_ids, task_specs), start=1):
            for attempt, username in enumerate(assessment_users_by_store[task["store_id"]], start=1):
                row = user_rows[username]
                if task["publish_status"] == "draft":
                    submit_status = ["submitted", "submitted", "submitted", "in_progress"][(attempt - 1) % 4]
                elif task["publish_status"] == "archived":
                    submit_status = ["submitted", "submitted", "timeout_submitted", "submitted"][(attempt - 1) % 4]
                else:
                    submit_status = ["submitted", "submitted", "submitted", "timeout_submitted"][(attempt - 1) % 4]
                    if idx == 1 and attempt == 3:
                        submit_status = "in_progress"
                score = 0.0 if submit_status == "in_progress" else round(70 + ((idx + attempt) % 8) * 4.0, 1)
                paper_answers = build_review_paper_answers(task["module_code"], wrong_all=score < 80 or username == PROTAGONIST_USERNAME)
                conn.execute("INSERT INTO assessment_records (task_id, user_id, employee_name, conversation_id, scenario_id, score, is_pass, comment, attempt_no, finished_at, score_branch, cycle_day_index, started_at, expires_at, submitted_at, submit_status, score_visibility_snapshot, is_score_visible_to_user, paper_answer_json, paper_result_json, time_spent_seconds, is_timeout, review_source, exam_mode_snapshot, task_version_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'assessment', ?, ?, ?, ?, ?, 'public', 1, ?, ?, ?, ?, 'demo_seed', ?, 1)", (task_id, str(row["id"]), USER_BY_USERNAME[username]["name"], f"demo:conversation:{task_id}:{attempt}", f"demo:scenario:{task_id}:{attempt}", score, 1 if score >= 80 and submit_status != "in_progress" else 0, "演示考试记录，已写入复盘本题目明细。", attempt, now, min(7, attempt + 2), now, now, None if submit_status == "in_progress" else now, submit_status, json_text(paper_answers), json_text({"seed_tag": SEED_TAG, "final_score": score, "review_notebook": True}), 900 + attempt * 120, 1 if submit_status == "timeout_submitted" else 0, task["exam_mode"]))
                assessment_count += 1
        summary["inserted"]["assessment_records"] = assessment_count

        sales_count = 0
        for idx, username in enumerate(DEMO_USERNAMES[:10], start=1):
            row = user_rows[username]
            for month_index, month in enumerate(["2026-01", "2026-02", "2026-03"], start=1):
                sales_amount = round(78000 + idx * 3200 + month_index * 2600, 2)
                conn.execute("INSERT INTO sales_performance (user_id, store_id, period_type, period_value, sales_amount, order_count, conversion_rate, complaint_rate, refund_rate, avg_ticket, attach_rate, member_conversion_rate, high_margin_share, target_sales_amount, target_avg_ticket, target_conversion_rate, target_attach_rate, target_member_conversion_rate, target_high_margin_share, created_at) VALUES (?, ?, 'monthly', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(row["id"]), USER_BY_USERNAME[username]["store_id"], month, sales_amount, 18 + idx + month_index, 0.18 + month_index * 0.01, 0.01, 0.006, 3200 + idx * 75, 0.22, 0.11, 0.26, sales_amount * 1.08, 3600 + idx * 80, 0.22, 0.31, 0.18, 0.33, now))
                sales_count += 1
        summary["inserted"]["sales_performance"] = sales_count

        dashboard_count = 0
        for idx, store in enumerate(DEMO_STORES, start=1):
            for period_idx, period in enumerate(["2026-03", "2026-04"], start=1):
                store_users = [user for user in DEMO_USERS if user["store_id"] == store["store_id"]][:3]
                risk_list = []
                for order, employee in enumerate(store_users, start=1):
                    risk_score = 90 - order * 7 - period_idx * 3
                    risk_list.append({"employee_id": str(user_rows[employee["username"]]["id"]), "employee_name": employee["name"], "risk_level": risk_level_for(risk_score), "risk_score": risk_score, "coaching_focus": "异议处理"})
                payload = {"seed_tag": SEED_TAG, "store_id": store["store_id"], "store_name": store["store_name"], "period": period, "overview": {"total_people": len(store_users), "high_risk_count": len([item for item in risk_list if item["risk_level"] == "high"]), "medium_risk_count": len([item for item in risk_list if item["risk_level"] == "medium"]), "low_risk_count": len([item for item in risk_list if item["risk_level"] == "low"]), "overall_score": round(84 - idx * 1.3 - period_idx * 0.6, 1)}, "risk_list": risk_list, "manager_action_items": ["优先复盘高风险员工对练", "关注最近考试未达标员工"], "viewer_role": "admin" if period_idx == 1 else "store_manager"}
                conn.execute("INSERT INTO dashboard_snapshots (snapshot_id, store_id, user_id, overall_score, compliance_score, training_completion_rate, recent_practice_avg_score, recent_high_risk_count, core_weak_dimension, dashboard_result_json, source_workflow, created_at, role_scope, period, viewer_role, payload_json, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (f"{DEMO_DASHBOARD_PREFIX}{store['store_id'].lower()}_{period.replace('-', '')}", store["store_id"], admin_id, payload["overview"]["overall_score"], payload["overview"]["overall_score"] - 3, 0.82, payload["overview"]["overall_score"] - 4.5, payload["overview"]["high_risk_count"], "异议处理", json_text(payload), "demo_seed_dashboard", now, "store_level", period, payload["viewer_role"], json_text(payload), admin_id))
                dashboard_count += 1
        summary["inserted"]["dashboard_snapshots"] = dashboard_count

        module_count = 0
        snapshot_dates = ["2026-02-28", "2026-03-31", "2026-04-15"]
        for idx, username in enumerate(DEMO_USERNAMES[:10], start=1):
            row = user_rows[username]
            base = 84 if USER_BY_USERNAME[username]["role"] == "senior_consultant" else 72
            for module_offset, (module_code, module_name) in enumerate(list(MODULE_LABELS.items())[:5], start=1):
                overall = round(base + (idx % 4) * 2.4 + module_offset * 1.7, 1)
                conn.execute("INSERT INTO module_index_snapshots (user_id, module_code, module_name, practice_index, assessment_index, learning_index, overall_index, snapshot_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(row["id"]), module_code, module_name, overall - 2.5, overall - 1.2, overall - 3.1, overall, snapshot_dates[(idx + module_offset) % len(snapshot_dates)], now))
                module_count += 1
        summary["inserted"]["module_index_snapshots"] = module_count

        query_cases = [("recent_high_risk_staff", "近 30 天哪些员工风险最高？", "列出最近高风险员工并给出辅导建议。"), ("training_completion", "本店培训完成率怎么样？", "培训完成率已达 82%，仍有 3 人待补练。"), ("module_gap", "哪个模块最薄弱？", "当前最薄弱模块为异议处理。"), ("sales_performance", "最近三个月业绩趋势如何？", "多数门店业绩稳步增长，北京店领先。"), ("assessment_pass_rate", "考试通过率是多少？", "模拟考通过率 76%，需重点关注高价异议场景。")]
        query_count = 0
        for idx in range(10):
            query_type, text, summary_text = query_cases[idx % len(query_cases)]
            actor = "admin" if idx == 0 else DEMO_USERS[(idx - 1) % len(DEMO_USERS)]["username"]
            actor_id = admin_id if actor == "admin" else str(user_rows[actor]["id"])
            actor_store_id = "" if actor == "admin" else USER_BY_USERNAME[actor]["store_id"]
            conn.execute("INSERT INTO query_records (record_id, stage, employee_id, query_text, parsed_intent, payload_json, created_at, store_id, user_query, query_type, params_json, query_result_json, summary_text, source_workflow_parse, source_workflow_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (f"{DEMO_QUERY_PREFIX}{idx + 1:02d}", "parse" if idx % 2 == 0 else "summarize", actor_id, text, query_type, json_text({"seed_tag": SEED_TAG}), now, actor_store_id, text, query_type, json_text({"seed_tag": SEED_TAG}), json_text({"seed_tag": SEED_TAG, "rows": [{"name": "演示员工A"}]}), summary_text, "demo_seed_query_parse", "demo_seed_query_summary"))
            query_count += 1
        summary["inserted"]["query_records"] = query_count

        summary["inserted"].update(_seed_agent_evo(conn, user_rows=user_rows, admin_id=admin_id, now=now))

        conn.commit()
        admin_after = fetch_admin_row(conn)
        summary["admin"] = {"username": str(admin_after["username"]), "id": int(admin_after["id"]), "password_preserved": str(admin_after["hashed_password"]) == admin_hash}
        summary["inserted_total"] = sum(int(value) for value in summary["inserted"].values())
    if verbose:
        print_summary(summary)
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print("=" * 72)
    print(f"模式: {summary.get('mode')}")
    print(f"数据库: {summary.get('db_path')}")
    if "demo_password" in summary:
        print(f"演示账号密码: {summary['demo_password']}")
    if "admin" in summary:
        print(f"admin 保留: username={summary['admin']['username']} id={summary['admin']['id']} password_preserved={summary['admin']['password_preserved']}")
    if summary.get("cleanup"):
        print("清理统计:")
        for key, value in summary["cleanup"].items():
            if int(value or 0):
                print(f"  - {key}: {value}")
    if summary.get("inserted"):
        print("插入统计:")
        for key, value in summary["inserted"].items():
            print(f"  - {key}: {value}")
    if "deleted_total" in summary:
        print(f"总删除记录: {summary['deleted_total']}")
    if "inserted_total" in summary:
        print(f"总插入记录: {summary['inserted_total']}")
    print("=" * 72)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed or delete demo data for the Agent_O backend SQLite database.")
    parser.add_argument("--delete-demo", action="store_true", help="只删除脚本生成的演示数据")
    parser.add_argument("--reset-demo", action="store_true", help="先删除再重建演示数据")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite 数据库文件路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.delete_demo and args.reset_demo:
        raise SystemExit("`--delete-demo` 和 `--reset-demo` 不能同时使用。")
    if args.delete_demo:
        summary = delete_demo_data(args.db_path, verbose=True)
    else:
        summary = seed_demo_data(args.db_path, verbose=True)
        if args.reset_demo:
            summary["mode"] = "reset-demo"
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
