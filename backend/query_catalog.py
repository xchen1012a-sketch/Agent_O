from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Any

from database import SQLITE_DB_PATH


SENSITIVE_COLUMNS: frozenset[str] = frozenset({
    "hashed_password",
    "password",
    "phone",
    "mobile",
    "token",
    "api_key",
    "secret",
})

_MANUAL_DESCRIPTIONS: dict[str, str] = {
    "users": "系统用户、角色与门店归属",
    "stores": "门店信息、区域与店长",
    "employee_profiles": "员工档案、岗位与能力分",
    "practice_records": "陪练对话记录",
    "practice_eval_records": "陪练评估结果",
    "learning_eval_records": "学习评估记录",
    "assistant_records": "在岗助手使用记录",
    "ability_update_records": "能力更新记录",
    "dashboard_snapshots": "经营看板快照",
    "growth_plan_records": "成长计划记录",
    "growth_plan_jobs": "成长计划生成任务",
    "growth_task_manual_records": "成长任务人工补记",
    "sales_performance": "销售绩效数据",
    "sales_data": "销售流水数据",
    "performance_attribution_reports": "绩效归因分析报告",
    "assessment_tasks": "考核任务",
    "assessment_records": "考核成绩记录",
    "assessment_task_papers": "考核试卷版本",
    "assessment_task_targets": "考核对象",
    "question_bank_questions": "题库题目",
    "kb_documents": "知识库文档索引",
    "kb_dataset_bindings": "知识库数据集绑定",
    "theory_learning_documents": "理论学习资料",
    "audit_logs": "系统审计日志",
    "query_records": "一句话查询历史",
    "training_cycles": "训练周期",
    "cycle_daily_tasks": "周期每日任务",
    "training_stage_reviews": "阶段复盘",
    "training_unlock_snapshots": "训练解锁快照",
    "module_index_snapshots": "模块能力指数快照",
    "mentor_histories": "导师对话历史",
    "exam_papers": "考试试卷内容",
    "exam_results": "考试结果明细",
    "study_progress": "学习进度记录",
    "role_settings": "角色设置",
}


def _visible_columns(columns: list[str]) -> list[str]:
    return [col for col in columns if col not in SENSITIVE_COLUMNS]


def _scope_key(columns: list[str]) -> str:
    colset = set(columns)
    if "store_id" in colset:
        return "store_id"
    if "user_id" in colset:
        return "user_id"
    if "employee_id" in colset:
        return "employee_id"
    return "global"


@lru_cache(maxsize=1)
def get_query_data_catalog() -> list[dict[str, Any]]:
    """Return every application SQLite table with safe, query-facing metadata."""
    if not SQLITE_DB_PATH.exists():
        return []

    catalog: list[dict[str, Any]] = []
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table_name,) in rows:
            raw_columns = [
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                if row and row[1]
            ]
            visible_columns = _visible_columns(raw_columns)
            if not visible_columns:
                continue
            scope_key = _scope_key(raw_columns)
            catalog.append({
                "table_name": str(table_name),
                "description": _MANUAL_DESCRIPTIONS.get(str(table_name), f"系统数据表（{table_name}）"),
                "visible_columns": visible_columns,
                "scope_key": scope_key,
                "has_store_id": scope_key == "store_id",
            })
    finally:
        if conn is not None:
            conn.close()
    return catalog


def query_catalog_prompt_summary(*, limit: int = 80) -> str:
    """Compact catalog text for LLM routing prompts and debug output."""
    lines: list[str] = []
    for item in get_query_data_catalog()[:limit]:
        columns = ", ".join(item.get("visible_columns", [])[:12])
        lines.append(
            f"{item['table_name']}：{item['description']}；字段：{columns}；权限键：{item['scope_key']}"
        )
    return "\n".join(lines)


def query_catalog_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in get_query_data_catalog():
        rows.append({
            "table_name": str(item["table_name"]),
            "description": str(item["description"]),
            "fields": ", ".join(item.get("visible_columns", [])[:12]),
            "scope_key": str(item.get("scope_key") or "global"),
        })
    return rows
