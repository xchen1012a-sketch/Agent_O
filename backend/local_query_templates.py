"""Deterministic local templates for low-risk one-sentence queries."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable


_FAKE_STORE_PREFIXES = ("STORE_RISK_", "STORE_LINK_", "STORE_TEST_", "STORE_PERM_")
_COUNT_TOKENS = ("几", "多少", "数量", "总数", "共有", "一共", "总共")
_LIST_TOKENS = ("哪些", "有哪些", "都有谁", "名单", "列表")
_GLOBAL_TOKENS = ("全系统", "系统", "所有", "全部", "各门店", "每家门店")
_OWN_STORE_TOKENS = ("本店", "店里", "店内", "当前门店", "我店")
_OPEN_ANALYSIS_TOKENS = (
    "风险",
    "高风险",
    "业绩",
    "销售",
    "趋势",
    "归因",
    "模块",
    "知识点",
    "黄金",
    "钻石",
    "顾客",
    "客户",
    "话术",
)


@dataclass(frozen=True)
class LocalQueryTemplate:
    template_id: str
    query_type: str
    matcher: Callable[[str], bool]
    runner: Callable[[sqlite3.Connection, str, str, bool], dict[str, Any]]


def _t(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _normalize_query(text: str) -> str:
    normalized = _t(text)
    replacements = (
        ("店铺", "门店"),
        ("分店", "门店"),
        ("店面", "门店"),
        ("人员", "员工"),
        ("店员", "员工"),
        ("用户", "员工"),
        ("账号", "员工"),
        ("负责人", "店长"),
        ("门店店长", "店长"),
    )
    for src, dst in replacements:
        normalized = normalized.replace(src, dst)
    normalized = re.sub(r"[\s？?。！!，,、]+", "", normalized)
    return normalized


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _has_count_intent(text: str) -> bool:
    return _contains_any(text, _COUNT_TOKENS)


def _has_list_intent(text: str) -> bool:
    return _contains_any(text, _LIST_TOKENS)


def _is_open_analysis_query(text: str) -> bool:
    return _contains_any(text, _OPEN_ANALYSIS_TOKENS)


def _is_completion_analysis_query(text: str) -> bool:
    return any(token in text for token in ("为什么", "原因", "趋势", "薄弱", "风险", "建议", "怎么改", "如何改进"))


def _has_incomplete_intent(text: str) -> bool:
    return any(token in text for token in ("未完成", "没完成", "没有完成", "还没完成", "未做完", "没做完", "没参加", "没有参加", "未参加", "未开始", "尚未开始"))


def _has_completion_overview_intent(text: str) -> bool:
    return any(token in text for token in ("完成率", "完成情况", "进度", "完成得怎么样", "完成怎么样"))


def _is_training_query(text: str) -> bool:
    return any(token in text for token in ("培训", "训练"))


def _is_exam_query(text: str) -> bool:
    return any(token in text for token in ("考试", "考核"))


def _is_task_query(text: str) -> bool:
    return "任务" in text


def _has_global_hint(text: str) -> bool:
    return _contains_any(text, _GLOBAL_TOKENS)


def _has_own_store_hint(text: str) -> bool:
    return _contains_any(text, _OWN_STORE_TOKENS)


def _store_name_aliases(name: str) -> list[str]:
    text = _t(name)
    if not text:
        return []
    aliases = [text]
    compact = text.replace("珠宝", "").replace("华璟", "").replace("店", "").strip()
    if compact and compact not in aliases:
        aliases.append(compact)
    return aliases


def _matched_store_ids(conn: sqlite3.Connection, query_text: str) -> list[str]:
    matched: list[str] = []
    rows = conn.execute("SELECT store_id, store_name FROM stores").fetchall()
    for row in rows:
        store_id = _t(row["store_id"] if isinstance(row, sqlite3.Row) else row[0])
        if not store_id or any(store_id.startswith(prefix) for prefix in _FAKE_STORE_PREFIXES):
            continue
        store_name = _t(row["store_name"] if isinstance(row, sqlite3.Row) else row[1])
        aliases = _store_name_aliases(store_name)
        if any(alias and alias in query_text for alias in aliases):
            matched.append(store_id)
    return matched


def _extract_explicit_store_phrase(query_text: str) -> str:
    q = _t(query_text)
    if not q or any(token in q for token in _OWN_STORE_TOKENS):
        return ""
    generic_tokens = ("全系统", "系统", "门店", "员工", "店长", "角色", "多少", "几家", "几个", "本店", "全部", "所有", "各")
    patterns = (
        r"^(?:查一下|看看|帮我查一下|帮我看下)?([\u4e00-\u9fff]{2,20}(?:旗舰店|精品店|体验店|门店|店))(?:有|的|员工|店长|多少|几|都|还|目前|现在)",
        r"([\u4e00-\u9fff]{2,20}(?:旗舰店|精品店|体验店|门店|店))(?:有哪些员工|有多少员工|店长是谁|是谁|有哪些人)",
    )
    for pattern in patterns:
        matches = re.findall(pattern, q)
        if not matches:
            continue
        candidate = max(matches, key=len)
        if candidate and not any(token in candidate for token in generic_tokens):
            return candidate
    return ""


def _store_filters(alias: str = "") -> list[str]:
    prefix = f"{alias}." if alias else ""
    return [f"{prefix}store_id NOT LIKE ?" for _ in _FAKE_STORE_PREFIXES]


def _store_filter_params() -> list[str]:
    return [f"{prefix}%" for prefix in _FAKE_STORE_PREFIXES]


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [desc[0] for desc in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _single_count(conn: sqlite3.Connection, sql: str, params: list[Any], key: str) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    if isinstance(row, sqlite3.Row):
        return _i(row[key] if key in row.keys() else row[0])
    return _i(row[0])


def _scope_is_global(query_text: str, allow_global_scope: bool) -> bool:
    return allow_global_scope and not _has_own_store_hint(query_text)


def _match_total_store_and_employee_count(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    if not (_has_count_intent(q) and "门店" in q and "员工" in q):
        return False
    return any(token in q for token in (
        "门店和员工",
        "门店及员工",
        "门店以及员工",
        "门店、员工",
        "门店员工总数",
        "门店数量和员工",
        "店和员工",
    )) or bool(re.search(r"(?:多少|几).*门店.*(?:多少|几).*员工", q))


def _match_store_count(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    return _has_count_intent(q) and "门店" in q and "员工" not in q and "店长" not in q


def _match_employee_count(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    if "各门店" in q or "每家门店" in q:
        return False
    return _has_count_intent(q) and "员工" in q and "店长" not in q and not _match_total_store_and_employee_count(q)


def _match_employee_list(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    if _is_training_query(q) or _is_exam_query(q) or _is_task_query(q):
        return False
    return "员工" in q and _has_list_intent(q)


def _match_store_manager(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    if "店长" not in q:
        return False
    return _has_list_intent(q) or "谁是店长" in q or "店长是谁" in q or "各门店店长" in q


def _match_role_list(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    return "角色" in q and (_has_list_intent(q) or "有什么角色" in q or "哪些角色" in q)


def _match_training_incomplete_staff(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    return _is_training_query(q) and _has_incomplete_intent(q) and "新人" not in q and "新员工" not in q


def _match_training_unfinished_newcomer(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    return _is_training_query(q) and _has_incomplete_intent(q) and any(token in q for token in ("新人", "新员工"))


def _match_training_completion_overview(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    return _is_training_query(q) and _has_completion_overview_intent(q)


def _match_exam_incomplete_staff(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    if "通过率" in q or "及格率" in q:
        return False
    return _is_exam_query(q) and _has_incomplete_intent(q)


def _match_exam_completion_overview(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    if "通过率" in q or "及格率" in q:
        return False
    return _is_exam_query(q) and _has_completion_overview_intent(q)


def _match_task_incomplete_staff(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    return _is_task_query(q) and "员工" in q and _has_incomplete_intent(q)


def _match_task_incomplete_items(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    return _is_task_query(q) and _has_incomplete_intent(q)


def _match_task_completion_overview(q: str) -> bool:
    if _is_open_analysis_query(q) or _is_completion_analysis_query(q):
        return False
    return _is_task_query(q) and _has_completion_overview_intent(q)


def _match_person_role(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    return bool(_extract_person_name(q)) and any(token in q for token in ("什么岗位", "什么职位", "什么角色", "干嘛"))


def _match_person_store(q: str) -> bool:
    if _is_open_analysis_query(q):
        return False
    return bool(_extract_person_name(q)) and any(token in q for token in ("哪个门店", "哪家门店", "在哪个门店", "在哪家门店"))


_NOT_PERSON_NAMES = frozenset({
    "什么",
    "哪个",
    "哪家",
    "员工",
    "门店",
    "角色",
    "岗位",
    "职位",
    "店长",
})


def _extract_person_name(q: str) -> str:
    patterns = (
        r"([\u4e00-\u9fff]{2,4})(?:是(?:什么|哪个)?(?:岗位|职位|角色)|干嘛)",
        r"([\u4e00-\u9fff]{2,4})(?:属于|在)(?:哪个|哪家)?门店",
        r"(?:查一下|看看)?([\u4e00-\u9fff]{2,4})的(?:岗位|职位|角色|门店)",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            name = match.group(1)
            if name not in _NOT_PERSON_NAMES:
                return name
    return ""


def _store_scope_clause(
    *,
    conn: sqlite3.Connection,
    table_alias: str,
    query_text: str,
    store_id: str,
    allow_global_scope: bool,
) -> tuple[list[str], list[Any], bool, str]:
    conditions = _store_filters(table_alias)
    params: list[Any] = _store_filter_params()
    is_global = _scope_is_global(query_text, allow_global_scope)
    named_store_ids = _matched_store_ids(conn, query_text)
    explicit_store_phrase = _extract_explicit_store_phrase(query_text)
    prefix = f"{table_alias}." if table_alias else ""
    if named_store_ids:
        conditions.append(f"{prefix}store_id IN ({', '.join(['?'] * len(named_store_ids))})")
        params.extend(named_store_ids)
        is_global = False
    elif explicit_store_phrase:
        return conditions, params, False, explicit_store_phrase
    elif store_id and not is_global:
        conditions.append(f"{prefix}store_id = ?")
        params.append(store_id)
    return conditions, params, is_global, ""


def _unknown_store_result(template_id: str, query_type: str, store_phrase: str) -> dict[str, Any]:
    return {
        **_result(
            template_id=template_id,
            query_type=query_type,
            result_rows=[],
            reply_text=f"当前系统里暂时没有匹配到“{store_phrase}”这家门店。",
            focus_names=[],
        ),
        "skip_query2": True,
    }


def _run_store_count(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    conditions, params, is_global, unresolved_store = _store_scope_clause(
        conn=conn,
        table_alias="",
        query_text=q,
        store_id=store_id,
        allow_global_scope=allow_global_scope,
    )
    if unresolved_store:
        return _unknown_store_result("store_count", "store_count", unresolved_store)
    count = _single_count(
        conn,
        f"SELECT COUNT(*) AS store_count FROM stores WHERE {' AND '.join(conditions)}",
        params,
        "store_count",
    )
    rows = [{"store_count": count}]
    scope = "当前系统" if is_global else "本店"
    return _result(
        template_id="store_count",
        query_type="store_count",
        result_rows=rows,
        reply_text=f"{scope}共有{count}家门店。",
        focus_names=[],
    )


def _run_employee_count(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    conditions, params, is_global, unresolved_store = _store_scope_clause(
        conn=conn,
        table_alias="u",
        query_text=q,
        store_id=store_id,
        allow_global_scope=allow_global_scope,
    )
    if unresolved_store:
        return _unknown_store_result("employee_count", "employee_count", unresolved_store)
    count = _single_count(
        conn,
        f"SELECT COUNT(*) AS employee_count FROM users u WHERE {' AND '.join(conditions)}",
        params,
        "employee_count",
    )
    rows = [{"employee_count": count}]
    scope = "当前系统" if is_global else "本店"
    return _result(
        template_id="employee_count",
        query_type="employee_count",
        result_rows=rows,
        reply_text=f"{scope}共有{count}名员工。",
        focus_names=[],
    )


def _run_total_store_and_employee_count(
    conn: sqlite3.Connection,
    q: str,
    store_id: str,
    allow_global_scope: bool,
) -> dict[str, Any]:
    store_result = _run_store_count(conn, q, store_id, allow_global_scope)
    employee_result = _run_employee_count(conn, q, store_id, allow_global_scope)
    store_count = _i(store_result["result_rows"][0].get("store_count"))
    employee_count = _i(employee_result["result_rows"][0].get("employee_count"))
    rows = [
        {"metric": "store_count", "total_count": store_count},
        {"metric": "employee_count", "total_count": employee_count},
    ]
    scope = "当前系统" if _scope_is_global(q, allow_global_scope) else "本店"
    return _result(
        template_id="store_employee_count",
        query_type="store_employee_count",
        result_rows=rows,
        reply_text=f"{scope}共有{store_count}家门店、{employee_count}名员工。",
        focus_names=[],
    )


def _run_employee_list(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    conditions, params, is_global, unresolved_store = _store_scope_clause(
        conn=conn,
        table_alias="u",
        query_text=q,
        store_id=store_id,
        allow_global_scope=allow_global_scope,
    )
    if unresolved_store:
        return _unknown_store_result("employee_list", "employee_list", unresolved_store)
    rows = _rows(
        conn.execute(
            "SELECT u.display_name AS employee_name, "
            "COALESCE(p.position, u.role) AS position, "
            "s.store_name "
            "FROM users u "
            "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
            "LEFT JOIN stores s ON u.store_id = s.store_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY u.store_id ASC, u.display_name ASC "
            "LIMIT 100",
            params,
        )
    )
    names = [_t(row.get("employee_name")) for row in rows if _t(row.get("employee_name"))]
    scope = "当前系统" if is_global else "本店"
    if names:
        sample = "、".join(names[:8])
        tail = f"等{len(names)}名员工" if len(names) > 8 else f"{len(names)}名员工"
        reply = f"{scope}共有{tail}：{sample}。"
    else:
        reply = f"{scope}暂时没有查到员工。"
    return _result(
        template_id="employee_list",
        query_type="employee_list",
        result_rows=rows,
        reply_text=reply,
        focus_names=names[:8],
    )


def _run_store_manager(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    conditions, params, is_global, unresolved_store = _store_scope_clause(
        conn=conn,
        table_alias="",
        query_text=q,
        store_id=store_id,
        allow_global_scope=allow_global_scope,
    )
    if unresolved_store:
        return _unknown_store_result("store_manager", "store_manager", unresolved_store)
    conditions.extend(["manager_name IS NOT NULL", "manager_name <> ''"])
    rows = _rows(
        conn.execute(
            "SELECT store_name, manager_name "
            "FROM stores "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY store_id ASC "
            "LIMIT 100",
            params,
        )
    )
    names = [_t(row.get("manager_name")) for row in rows if _t(row.get("manager_name"))]
    if not rows:
        reply = "当前范围内暂时没有查到店长信息。"
    elif is_global:
        pairs = "；".join(
            f"{_t(row.get('store_name'))}：{_t(row.get('manager_name'))}"
            for row in rows[:8]
        )
        reply = f"当前系统查到{len(rows)}家门店的店长信息：{pairs}。"
    else:
        row = rows[0]
        reply = f"{_t(row.get('store_name')) or '本店'}的店长是{_t(row.get('manager_name'))}。"
    return _result(
        template_id="store_manager",
        query_type="store_manager",
        result_rows=rows,
        reply_text=reply,
        focus_names=names[:8],
    )


def _run_role_list(conn: sqlite3.Connection, _q: str, _store_id: str, _allow_global_scope: bool) -> dict[str, Any]:
    rows = _rows(
        conn.execute(
            "SELECT display_name, description "
            "FROM role_settings "
            "WHERE COALESCE(is_enabled, 1) = 1 "
            "ORDER BY sort_order ASC, display_name ASC "
            "LIMIT 100"
        )
    )
    names = [_t(row.get("display_name")) for row in rows if _t(row.get("display_name"))]
    if names:
        reply = f"当前启用角色包括：{'、'.join(names)}。"
    else:
        reply = "当前暂时没有查到启用角色。"
    return _result(
        template_id="role_list",
        query_type="role_list",
        result_rows=rows,
        reply_text=reply,
        focus_names=names[:8],
    )


def _employees_in_scope(
    conn: sqlite3.Connection,
    q: str,
    store_id: str,
    allow_global_scope: bool,
) -> tuple[list[dict[str, Any]], bool, str]:
    conditions, params, is_global, unresolved_store = _store_scope_clause(
        conn=conn,
        table_alias="u",
        query_text=q,
        store_id=store_id,
        allow_global_scope=allow_global_scope,
    )
    if unresolved_store:
        return [], False, unresolved_store
    rows = _rows(
        conn.execute(
            "SELECT CAST(u.id AS TEXT) AS employee_id, "
            "u.display_name AS employee_name, "
            "COALESCE(p.position, u.role) AS position, "
            "u.role, u.store_id, s.store_name "
            "FROM users u "
            "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
            "LEFT JOIN stores s ON u.store_id = s.store_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY u.store_id ASC, u.display_name ASC",
            params,
        )
    )
    return rows, is_global, ""


def _training_completion_rows(
    conn: sqlite3.Connection,
    q: str,
    store_id: str,
    allow_global_scope: bool,
    *,
    newcomer_only: bool = False,
) -> tuple[list[dict[str, Any]], bool, str]:
    employees, is_global, unresolved_store = _employees_in_scope(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return [], False, unresolved_store
    by_employee = {str(item["employee_id"]): dict(item) for item in employees}
    if newcomer_only:
        by_employee = {
            employee_id: item
            for employee_id, item in by_employee.items()
            if _t(item.get("role")).lower() in {"trainee", "newbie"}
        }
    if not by_employee:
        return [], is_global, ""
    ids = list(by_employee.keys())
    placeholders = ", ".join(["?"] * len(ids))
    task_rows = conn.execute(
        f"""
        SELECT user_id,
               COUNT(*) AS total,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS done
        FROM cycle_daily_tasks
        WHERE status <> 'voided' AND user_id IN ({placeholders})
        GROUP BY user_id
        """,
        ids,
    ).fetchall()
    task_map = {str(row["user_id"]): row for row in task_rows}
    rows: list[dict[str, Any]] = []
    for employee_id, item in by_employee.items():
        row = task_map.get(employee_id)
        total = _i(row["total"]) if row else 0
        done = _i(row["done"]) if row else 0
        completion_rate = round(done / total * 100, 1) if total else 0.0
        rows.append({
            "employee_id": employee_id,
            "employee_name": _t(item.get("employee_name")),
            "position": _t(item.get("position")),
            "store_id": _t(item.get("store_id")),
            "store_name": _t(item.get("store_name")),
            "training_completed": done,
            "training_required": total,
            "completion_rate": completion_rate,
        })
    return rows, is_global, ""


def _exam_completion_rows(
    conn: sqlite3.Connection,
    q: str,
    store_id: str,
    allow_global_scope: bool,
) -> tuple[list[dict[str, Any]], bool, str]:
    employees, is_global, unresolved_store = _employees_in_scope(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return [], False, unresolved_store
    by_employee = {str(item["employee_id"]): dict(item) for item in employees}
    if not by_employee:
        return [], is_global, ""
    ids = list(by_employee.keys())
    placeholders = ", ".join(["?"] * len(ids))
    exam_rows = conn.execute(
        f"""
        SELECT user_id,
               MAX(CASE
                   WHEN COALESCE(submit_status, '') IN ('submitted', 'timeout_submitted')
                        OR finished_at IS NOT NULL
                   THEN 1 ELSE 0 END) AS completed,
               MAX(CASE WHEN COALESCE(submit_status, '') = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
               COUNT(*) AS record_count
        FROM assessment_records
        WHERE user_id IN ({placeholders})
        GROUP BY user_id
        """,
        ids,
    ).fetchall()
    exam_map = {str(row["user_id"]): row for row in exam_rows}
    rows: list[dict[str, Any]] = []
    for employee_id, item in by_employee.items():
        row = exam_map.get(employee_id)
        completed = _i(row["completed"]) if row else 0
        in_progress = _i(row["in_progress"]) if row else 0
        rows.append({
            "employee_id": employee_id,
            "employee_name": _t(item.get("employee_name")),
            "position": _t(item.get("position")),
            "store_id": _t(item.get("store_id")),
            "store_name": _t(item.get("store_name")),
            "exam_completed": 1 if completed else 0,
            "exam_status": "completed" if completed else ("in_progress" if in_progress else "pending"),
            "exam_record_count": _i(row["record_count"]) if row else 0,
        })
    return rows, is_global, ""


def _task_status_rows(
    conn: sqlite3.Connection,
    q: str,
    store_id: str,
    allow_global_scope: bool,
) -> tuple[list[dict[str, Any]], bool, str]:
    employees, is_global, unresolved_store = _employees_in_scope(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return [], False, unresolved_store
    by_employee = {str(item["employee_id"]): dict(item) for item in employees}
    if not by_employee:
        return [], is_global, ""
    ids = list(by_employee.keys())
    placeholders = ", ".join(["?"] * len(ids))
    task_rows = _rows(
        conn.execute(
            f"""
            SELECT user_id, title, branch, status, task_type, module_name
            FROM cycle_daily_tasks
            WHERE status <> 'voided' AND user_id IN ({placeholders})
            ORDER BY user_id ASC, id ASC
            """,
            ids,
        )
    )
    rows: list[dict[str, Any]] = []
    for task in task_rows:
        employee = by_employee.get(_t(task.get("user_id")))
        if not employee:
            continue
        rows.append({
            "employee_id": _t(employee.get("employee_id")),
            "employee_name": _t(employee.get("employee_name")),
            "position": _t(employee.get("position")),
            "store_id": _t(employee.get("store_id")),
            "store_name": _t(employee.get("store_name")),
            "task_title": _t(task.get("title")),
            "task_status": _t(task.get("status")),
            "task_branch": _t(task.get("branch")),
            "task_type": _t(task.get("task_type")),
            "module_name": _t(task.get("module_name")),
        })
    return rows, is_global, ""


def _run_training_incomplete_staff(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, _is_global, unresolved_store = _training_completion_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("training_incomplete_staff", "training_incomplete_staff", unresolved_store)
    rows = [row for row in rows if _i(row.get("training_required")) > 0 and _i(row.get("training_completed")) < _i(row.get("training_required"))]
    rows.sort(key=lambda item: (_i(item.get("training_completed")), _t(item.get("employee_name"))))
    focus_names = [_t(row.get("employee_name")) for row in rows[:8]]
    if rows:
        preview = "、".join(focus_names[:8])
        reply = f"当前有{len(rows)}名员工培训还没完成，优先关注：{preview}。"
    else:
        reply = "当前范围内员工培训都已完成。"
    return _result(
        template_id="training_incomplete_staff",
        query_type="training_incomplete_staff",
        result_rows=rows[:20],
        reply_text=reply,
        focus_names=focus_names,
    )


def _run_training_unfinished_newcomer(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, _is_global, unresolved_store = _training_completion_rows(conn, q, store_id, allow_global_scope, newcomer_only=True)
    if unresolved_store:
        return _unknown_store_result("training_unfinished_newcomer", "training_unfinished_newcomer", unresolved_store)
    rows = [row for row in rows if _i(row.get("training_required")) > 0 and _i(row.get("training_completed")) < _i(row.get("training_required"))]
    rows.sort(key=lambda item: (_i(item.get("training_completed")), _t(item.get("employee_name"))))
    focus_names = [_t(row.get("employee_name")) for row in rows[:8]]
    if rows:
        reply = f"当前有{len(rows)}名新人培训还没完成，优先关注：{'、'.join(focus_names[:8])}。"
    else:
        reply = "当前范围内新员工培训都已完成。"
    return _result(
        template_id="training_unfinished_newcomer",
        query_type="training_unfinished_newcomer",
        result_rows=rows[:20],
        reply_text=reply,
        focus_names=focus_names,
    )


def _run_training_completion_overview(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, is_global, unresolved_store = _training_completion_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("store_training_completion_rank", "store_training_completion_rank", unresolved_store)
    filtered = [row for row in rows if _i(row.get("training_required")) > 0]
    total_required = sum(_i(row.get("training_required")) for row in filtered)
    total_done = sum(_i(row.get("training_completed")) for row in filtered)
    completion_rate = round(total_done / total_required * 100, 1) if total_required else 0.0
    scope_label = "当前系统" if is_global else "本店"
    overview_rows = [{
        "training_completed": total_done,
        "training_required": total_required,
        "training_completion_rate": completion_rate,
        "employee_count": len(filtered),
    }]
    reply = f"{scope_label}培训完成率目前是{completion_rate:.1f}%，已完成{total_done}/{total_required}项。"
    return _result(
        template_id="store_training_completion_rank",
        query_type="store_training_completion_rank",
        result_rows=overview_rows,
        reply_text=reply,
        focus_names=[],
    )


def _run_exam_incomplete_staff(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, _is_global, unresolved_store = _exam_completion_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("exam_incomplete_staff", "exam_incomplete_staff", unresolved_store)
    rows = [row for row in rows if _i(row.get("exam_completed")) == 0]
    focus_names = [_t(row.get("employee_name")) for row in rows[:8]]
    if rows:
        reply = f"当前有{len(rows)}名员工考试还没完成，优先关注：{'、'.join(focus_names[:8])}。"
    else:
        reply = "当前范围内员工考试都已完成。"
    return _result(
        template_id="exam_incomplete_staff",
        query_type="exam_incomplete_staff",
        result_rows=rows[:20],
        reply_text=reply,
        focus_names=focus_names,
    )


def _run_exam_completion_overview(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, is_global, unresolved_store = _exam_completion_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("exam_completion_overview", "exam_completion_overview", unresolved_store)
    total = len(rows)
    completed = len([row for row in rows if _i(row.get("exam_completed")) == 1])
    completion_rate = round(completed / total * 100, 1) if total else 0.0
    scope_label = "当前系统" if is_global else "本店"
    overview_rows = [{
        "exam_completed_count": completed,
        "exam_required_count": total,
        "exam_completion_rate": completion_rate,
    }]
    reply = f"{scope_label}考试完成率目前是{completion_rate:.1f}%，已完成{completed}/{total}人。"
    return _result(
        template_id="exam_completion_overview",
        query_type="exam_completion_overview",
        result_rows=overview_rows,
        reply_text=reply,
        focus_names=[],
    )


def _run_task_incomplete_items(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, _is_global, unresolved_store = _task_status_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("task_incomplete_items", "task_incomplete_items", unresolved_store)
    rows = [row for row in rows if _t(row.get("task_status")) in {"locked", "in_progress"}]
    rows.sort(key=lambda item: (_t(item.get("employee_name")), _t(item.get("task_title"))))
    focus_titles = [_t(row.get("task_title")) for row in rows[:8] if _t(row.get("task_title"))]
    if rows:
        reply = f"当前还有{len(rows)}项任务未完成，优先看：{'、'.join(focus_titles[:8])}。"
    else:
        reply = "当前范围内任务都已完成。"
    return _result(
        template_id="task_incomplete_items",
        query_type="task_incomplete_items",
        result_rows=rows[:20],
        reply_text=reply,
        focus_names=focus_titles,
    )


def _run_task_incomplete_staff(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, _is_global, unresolved_store = _task_status_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("task_incomplete_staff", "task_incomplete_staff", unresolved_store)
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        if _t(row.get("task_status")) not in {"locked", "in_progress"}:
            continue
        employee_id = _t(row.get("employee_id"))
        bucket = counts.setdefault(employee_id, {
            "employee_id": employee_id,
            "employee_name": _t(row.get("employee_name")),
            "position": _t(row.get("position")),
            "store_id": _t(row.get("store_id")),
            "store_name": _t(row.get("store_name")),
            "task_incomplete_count": 0,
        })
        bucket["task_incomplete_count"] += 1
    out = list(counts.values())
    out.sort(key=lambda item: (-_i(item.get("task_incomplete_count")), _t(item.get("employee_name"))))
    focus_names = [_t(row.get("employee_name")) for row in out[:8]]
    if out:
        reply = f"当前有{len(out)}名员工还有任务没完成，优先关注：{'、'.join(focus_names[:8])}。"
    else:
        reply = "当前范围内员工任务都已完成。"
    return _result(
        template_id="task_incomplete_staff",
        query_type="task_incomplete_staff",
        result_rows=out[:20],
        reply_text=reply,
        focus_names=focus_names,
    )


def _run_task_completion_overview(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    rows, is_global, unresolved_store = _task_status_rows(conn, q, store_id, allow_global_scope)
    if unresolved_store:
        return _unknown_store_result("task_completion_overview", "task_completion_overview", unresolved_store)
    total = len(rows)
    completed = len([row for row in rows if _t(row.get("task_status")) == "completed"])
    completion_rate = round(completed / total * 100, 1) if total else 0.0
    scope_label = "当前系统" if is_global else "本店"
    overview_rows = [{
        "task_completed_count": completed,
        "task_required_count": total,
        "task_completion_rate": completion_rate,
    }]
    reply = f"{scope_label}任务完成率目前是{completion_rate:.1f}%，已完成{completed}/{total}项。"
    return _result(
        template_id="task_completion_overview",
        query_type="task_completion_overview",
        result_rows=overview_rows,
        reply_text=reply,
        focus_names=[],
    )


def _run_person_role(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    name = _extract_person_name(q)
    rows = _person_rows(conn, name, store_id, allow_global_scope)
    focus = [_t(row.get("employee_name")) for row in rows if _t(row.get("employee_name"))]
    if not rows:
        reply = f"当前范围内暂时没有查到{name}的岗位信息。"
    else:
        parts = [
            f"{_t(row.get('employee_name'))}是{_t(row.get('position')) or '未设置岗位'}"
            for row in rows[:5]
        ]
        reply = "；".join(parts) + "。"
    return _result(
        template_id="person_role",
        query_type="person_role",
        result_rows=rows,
        reply_text=reply,
        focus_names=focus[:8],
    )


def _run_person_store(conn: sqlite3.Connection, q: str, store_id: str, allow_global_scope: bool) -> dict[str, Any]:
    name = _extract_person_name(q)
    rows = _person_rows(conn, name, store_id, allow_global_scope)
    focus = [_t(row.get("employee_name")) for row in rows if _t(row.get("employee_name"))]
    if not rows:
        reply = f"当前范围内暂时没有查到{name}的门店归属。"
    else:
        parts = [
            f"{_t(row.get('employee_name'))}属于{_t(row.get('store_name')) or '未设置门店'}"
            for row in rows[:5]
        ]
        reply = "；".join(parts) + "。"
    return _result(
        template_id="person_store",
        query_type="person_store",
        result_rows=rows,
        reply_text=reply,
        focus_names=focus[:8],
    )


def _person_rows(conn: sqlite3.Connection, name: str, store_id: str, allow_global_scope: bool) -> list[dict[str, Any]]:
    conditions = _store_filters("u")
    params: list[Any] = _store_filter_params()
    if store_id and not allow_global_scope:
        conditions.append("u.store_id = ?")
        params.append(store_id)
    conditions.append("(u.display_name LIKE ? OR u.username LIKE ?)")
    params.extend([f"%{name}%", f"%{name}%"])
    return _rows(
        conn.execute(
            "SELECT u.display_name AS employee_name, "
            "COALESCE(p.position, u.role) AS position, "
            "s.store_name "
            "FROM users u "
            "LEFT JOIN employee_profiles p ON p.employee_id = CAST(u.id AS TEXT) "
            "LEFT JOIN stores s ON u.store_id = s.store_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY u.display_name ASC "
            "LIMIT 20",
            params,
        )
    )


def _result(
    *,
    template_id: str,
    query_type: str,
    result_rows: list[dict[str, Any]],
    reply_text: str,
    focus_names: list[str],
) -> dict[str, Any]:
    query_status = "success" if result_rows else "empty"
    return {
        "template_id": template_id,
        "query_type": query_type,
        "params_json": {},
        "rewritten_query": "",
        "confidence_level": "high",
        "can_query": 1,
        "route_type": "local_template",
        "scope_status": "in_scope",
        "query_status": query_status,
        "result_rows": result_rows,
        "reply_text": reply_text,
        "summary": reply_text.split("\n\n", 1)[0],
        "fallback_reply_text": reply_text,
        "reply_mode": "local_template",
        "render_source": "local_template",
        "display_tags": ["本地固定查询"],
        "focus_names": focus_names,
        "problem_note": "",
    }


_TEMPLATES: tuple[LocalQueryTemplate, ...] = (
    LocalQueryTemplate("store_employee_count", "store_employee_count", _match_total_store_and_employee_count, _run_total_store_and_employee_count),
    LocalQueryTemplate("store_count", "store_count", _match_store_count, _run_store_count),
    LocalQueryTemplate("employee_count", "employee_count", _match_employee_count, _run_employee_count),
    LocalQueryTemplate("employee_list", "employee_list", _match_employee_list, _run_employee_list),
    LocalQueryTemplate("store_manager", "store_manager", _match_store_manager, _run_store_manager),
    LocalQueryTemplate("role_list", "role_list", _match_role_list, _run_role_list),
    LocalQueryTemplate("training_unfinished_newcomer", "training_unfinished_newcomer", _match_training_unfinished_newcomer, _run_training_unfinished_newcomer),
    LocalQueryTemplate("training_incomplete_staff", "training_incomplete_staff", _match_training_incomplete_staff, _run_training_incomplete_staff),
    LocalQueryTemplate("store_training_completion_rank", "store_training_completion_rank", _match_training_completion_overview, _run_training_completion_overview),
    LocalQueryTemplate("exam_incomplete_staff", "exam_incomplete_staff", _match_exam_incomplete_staff, _run_exam_incomplete_staff),
    LocalQueryTemplate("exam_completion_overview", "exam_completion_overview", _match_exam_completion_overview, _run_exam_completion_overview),
    LocalQueryTemplate("task_incomplete_staff", "task_incomplete_staff", _match_task_incomplete_staff, _run_task_incomplete_staff),
    LocalQueryTemplate("task_incomplete_items", "task_incomplete_items", _match_task_incomplete_items, _run_task_incomplete_items),
    LocalQueryTemplate("task_completion_overview", "task_completion_overview", _match_task_completion_overview, _run_task_completion_overview),
    LocalQueryTemplate("person_role", "person_role", _match_person_role, _run_person_role),
    LocalQueryTemplate("person_store", "person_store", _match_person_store, _run_person_store),
)


def try_local_query_template(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    store_id: str,
    allow_global_scope: bool,
) -> dict[str, Any] | None:
    """Return a deterministic local result for fixed whitelist questions."""
    normalized = _normalize_query(query_text)
    if not normalized:
        return None
    for template in _TEMPLATES:
        if not template.matcher(normalized):
            continue
        result = template.runner(conn, normalized, _t(store_id), allow_global_scope)
        result["template_id"] = template.template_id
        result["query_type"] = template.query_type
        result["rewritten_query"] = query_text
        return result
    return None
