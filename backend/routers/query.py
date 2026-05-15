from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import config as app_config
from api_response import dify_failure_response, make_request_id, success_response
from auth import get_current_user, normalize_app_role
from db_stage3 import get_conn, get_readonly_conn, json_text
from dify_stage4b import run_query1_workflow, run_query2_workflow
from local_query_templates import try_local_query_template
from query_catalog import query_catalog_prompt_summary, query_catalog_rows

router = APIRouter(prefix='/api/query', tags=['query'])

_log = logging.getLogger("jewelry_qipei.router.query")

# Fake auto-generated store IDs that should be excluded from all queries
_FAKE_STORE_PREFIXES = ('STORE_RISK_', 'STORE_LINK_', 'STORE_TEST_', 'STORE_PERM_')


def _is_real_store(store_id: str) -> bool:
    s = _t(store_id)
    return bool(s) and not any(s.startswith(p) for p in _FAKE_STORE_PREFIXES)

SUPPORTED_QUERY_TYPES = {
    'store_training_completion_rank',
    'training_unfinished_newcomer',
    'training_incomplete_staff',
    'low_compliance_staff',
    'practice_decline_staff',
    'recent_high_risk_staff',
    'followup_priority_staff',
    'high_freq_knowledge_tag_staff',
    'generic_sql',
    'store_count',
    'employee_count',
    'store_employee_count',
    'employee_list',
    'store_manager',
    'role_list',
    'person_role',
    'person_store',
    'exam_incomplete_staff',
    'exam_completion_overview',
    'task_incomplete_staff',
    'task_incomplete_items',
    'task_completion_overview',
}

LOCAL_TEMPLATE_QUERY_TYPES = {
    'store_count',
    'employee_count',
    'store_employee_count',
    'employee_list',
    'store_manager',
    'role_list',
    'person_role',
    'person_store',
    'exam_incomplete_staff',
    'exam_completion_overview',
    'task_incomplete_staff',
    'task_incomplete_items',
    'task_completion_overview',
}

QUERY_METRICS = {
    'store_training_completion_rank': ['training_completion_rate'],
    'training_unfinished_newcomer': ['training_completion_rate'],
    'training_incomplete_staff': ['training_completion_rate'],
    'low_compliance_staff': ['compliance_score'],
    'practice_decline_staff': ['practice_score_trend'],
    'recent_high_risk_staff': ['high_risk_count'],
    'followup_priority_staff': ['followup_priority_score'],
    'high_freq_knowledge_tag_staff': ['knowledge_tag_question_count'],
    'generic_sql': ['generic'],
    'store_count': ['store_count'],
    'employee_count': ['employee_count'],
    'store_employee_count': ['store_count', 'employee_count'],
    'employee_list': ['employee_count'],
    'store_manager': ['store_manager'],
    'role_list': ['role'],
    'person_role': ['role'],
    'person_store': ['store'],
    'exam_incomplete_staff': ['exam_completion_rate'],
    'exam_completion_overview': ['exam_completion_rate'],
    'task_incomplete_staff': ['task_completion_rate'],
    'task_incomplete_items': ['task_completion_rate'],
    'task_completion_overview': ['task_completion_rate'],
    'unsupported': [],
}

QUERY_DIMS = {
    'store_training_completion_rank': ['store'],
    'training_unfinished_newcomer': ['store', 'employee'],
    'training_incomplete_staff': ['store', 'employee'],
    'low_compliance_staff': ['store', 'employee'],
    'practice_decline_staff': ['store', 'employee'],
    'recent_high_risk_staff': ['store', 'employee'],
    'followup_priority_staff': ['store', 'employee'],
    'high_freq_knowledge_tag_staff': ['store', 'employee', 'knowledge_tag'],
    'generic_sql': ['store'],
    'store_count': ['store'],
    'employee_count': ['store', 'employee'],
    'store_employee_count': ['store', 'employee'],
    'employee_list': ['store', 'employee'],
    'store_manager': ['store'],
    'role_list': ['role'],
    'person_role': ['employee'],
    'person_store': ['store', 'employee'],
    'exam_incomplete_staff': ['store', 'employee'],
    'exam_completion_overview': ['store'],
    'task_incomplete_staff': ['store', 'employee'],
    'task_incomplete_items': ['store', 'employee', 'task'],
    'task_completion_overview': ['store'],
    'unsupported': [],
}
QUERY_ALLOWED_ROLES = {'admin', 'store_manager'}
STORE_MANAGER_BLOCKED_TABLES = {'audit_logs'}

_TRAINING_INCOMPLETE_TOKENS = (
    '未完成', '没完成', '没有完成', '尚未完成',
    '没参加', '没有参加', '未参加', '尚未参加',
    '还没完成', '未学完', '没学完', '未做完',
    '未开始', '尚未开始',
)
_NEWCOMER_TOKENS = ('新人', '新员工', '新入职')


def _query_scope_from_user(current_user: dict[str, Any]) -> tuple[str, bool]:
    role = normalize_app_role(_t(current_user.get('role')))
    if role not in QUERY_ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='权限不足：仅管理员或店长可使用一句话查询',
        )
    return role, role == 'admin'


def _query_scope_mode(role: str | None) -> str:
    normalized = normalize_app_role(role)
    if normalized == 'admin':
        return 'global'
    return 'store'


def _query_scope_token(scope_mode: str, store_id: str, employee_id: str) -> str:
    mode = _t(scope_mode).lower()
    if mode == 'global':
        return 'global'
    if mode == 'store':
        return f'store:{_t(store_id)}'
    if _t(employee_id):
        return f'employee:{_t(employee_id)}'
    return 'self'


def _query_allowed_employee_ids(scope_mode: str, current_user: dict[str, Any]) -> set[str] | None:
    return None


class QueryParseRequest(BaseModel):
    query_text: str = Field('', description='自然语言问题')
    history: list[dict[str, Any]] = Field(default_factory=list, description='对话上下文历史')
    conversation_id: str = Field('', description='前端当前会话ID')


class QuerySummarizeRequest(BaseModel):
    query_text: str = Field('', description='自然语言问题')
    parsed_intent: str = Field('', description='解析出的意图')
    result_rows: list[dict[str, Any]] = Field(default_factory=list, description='查询结果')
    params_json: dict[str, Any] = Field(default_factory=dict, description='结构化参数')
    query_status: str = Field('', description='查询状态(success/empty/error)')
    store_id: str = Field('', description='门店ID')
    error_message: str = Field('', description='异常信息')
    fallback_reply_text: str = Field('', description='未命中系统时的自然语言兜底回复')
    reply_mode: str = Field('', description='回复模式(system_hit/llm_fallback/local_fallback/blocked)')
    history: list[dict[str, Any]] = Field(default_factory=list, description='对话上下文历史')
    conversation_id: str = Field('', description='前端当前会话ID')


class QueryAskRequest(BaseModel):
    query_text: str = Field('', description='自然语言问题')
    history: list[dict[str, Any]] = Field(default_factory=list, description='对话上下文历史')
    conversation_id: str = Field('', description='前端当前会话ID')


def _t(v: Any) -> str:
    return '' if v is None else str(v).strip()


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return d


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return d


def _j(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    s = _t(v)
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _days(key: str) -> int:
    k = _t(key).lower()
    if k == 'recent_7d':
        return 7
    if k == 'recent_90d':
        return 90
    if k == 'this_month':
        return 32
    return 30


def _since(key: str) -> str:
    now = datetime.now(timezone.utc)
    if _t(key).lower() == 'this_month':
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    return (now - timedelta(days=_days(key))).isoformat()


def _store_id(conn, user_id: str) -> str:
    uid = _t(user_id)
    try:
        r = conn.execute('SELECT store_id FROM employee_profiles WHERE employee_id=? ORDER BY id DESC LIMIT 1', (uid,)).fetchone()
        if r and _t(r['store_id']):
            return _t(r['store_id'])
    except Exception:
        pass
    try:
        r = conn.execute('SELECT store_id FROM users WHERE CAST(id AS TEXT)=? LIMIT 1', (uid,)).fetchone()
        if r and _t(r['store_id']):
            return _t(r['store_id'])
    except Exception:
        pass
    return ''


def _summary_store_id(
    requested_store_id: str,
    current_store_id: str,
    result_rows: list[dict[str, Any]],
    *,
    allow_global_scope: bool,
) -> str:
    sid = _t(requested_store_id) or _t(current_store_id)
    if sid:
        return sid
    row_store_ids = {
        _t(row.get('store_id'))
        for row in (result_rows or [])
        if isinstance(row, dict) and _t(row.get('store_id'))
    }
    if allow_global_scope and len(row_store_ids) != 1:
        return 'ALL_STORES'
    for row in result_rows or []:
        if not isinstance(row, dict):
            continue
        row_store_id = _t(row.get('store_id'))
        if row_store_id:
            return row_store_id
    if allow_global_scope:
        return 'ALL_STORES'
    return ''


def _query_context_obj(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    ctx = message.get('query_context')
    if isinstance(ctx, dict):
        return ctx
    parse_result = message.get('parseResult') or message.get('parse_result')
    summarize_result = message.get('summarizeResult') or message.get('summarize_result')
    ctx = {}
    if isinstance(parse_result, dict):
        ctx.update({
            'intent': parse_result.get('intent') or parse_result.get('parsed_intent'),
            'filters': parse_result.get('filters') if isinstance(parse_result.get('filters'), dict) else {},
            'params_json': parse_result.get('params_json') if isinstance(parse_result.get('params_json'), dict) else {},
            'result_count': parse_result.get('result_count'),
            'result_preview': parse_result.get('result_rows') if isinstance(parse_result.get('result_rows'), list) else [],
        })
    if isinstance(summarize_result, dict):
        ctx['reply_text'] = summarize_result.get('reply_text') or summarize_result.get('user_visible_output') or summarize_result.get('summary_text') or summarize_result.get('summary')
        if summarize_result.get('focus_names'):
            ctx['focus_names'] = summarize_result.get('focus_names')
    return ctx


def _context_names_from_rows(rows: Any) -> tuple[list[str], list[str]]:
    names: list[str] = []
    stores: list[str] = []
    if not isinstance(rows, list):
        return names, stores
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        name = _t(row.get('employee_name') or row.get('display_name') or row.get('user_name') or row.get('username'))
        if name and name not in names:
            names.append(name)
        store = _t(row.get('store_name') or row.get('store_id'))
        if store and store not in stores:
            stores.append(store)
    return names, stores


def _last_query_context(history: list[dict[str, Any]]) -> dict[str, Any]:
    recent = [msg for msg in (history or [])[-12:] if isinstance(msg, dict)]
    last_assistant_idx = -1
    for idx in range(len(recent) - 1, -1, -1):
        if _t(recent[idx].get('role')).lower() == 'assistant':
            last_assistant_idx = idx
            break
    if last_assistant_idx < 0:
        return {
            'last_user': '',
            'last_assistant': '',
            'intent': '',
            'filters': {},
            'params_json': {},
            'result_count': 0,
            'names': [],
            'stores': [],
        }
    assistant_msg = recent[last_assistant_idx]
    last_assistant = _t(assistant_msg.get('content'))
    merged = _query_context_obj(assistant_msg)
    last_user = ''
    for idx in range(last_assistant_idx - 1, -1, -1):
        msg = recent[idx]
        if _t(msg.get('role')).lower() == 'user' and _t(msg.get('content')):
            last_user = _t(msg.get('content'))
            break
    names, stores = _context_names_from_rows(merged.get('result_preview') or merged.get('result_rows'))
    focus_names = merged.get('focus_names') if isinstance(merged.get('focus_names'), list) else []
    for name in focus_names:
        text = _t(name)
        if text and text not in names:
            names.append(text)
    filters = merged.get('filters') if isinstance(merged.get('filters'), dict) else {}
    params = merged.get('params_json') if isinstance(merged.get('params_json'), dict) else {}
    return {
        'last_user': last_user,
        'last_assistant': last_assistant or _t(merged.get('reply_text')),
        'intent': _t(merged.get('intent') or merged.get('parsed_intent')),
        'filters': filters,
        'params_json': params,
        'result_count': _i(merged.get('result_count'), len(names)),
        'names': names,
        'stores': stores,
    }


def _build_query_context_summary(history: list[dict[str, Any]]) -> str:
    ctx = _last_query_context(history or [])
    lines: list[str] = []
    if ctx['last_user']:
        lines.append(f"上一轮问题：{ctx['last_user'][:120]}")
    if ctx['intent']:
        lines.append(f"上一轮意图：{ctx['intent']}")
    filters = ctx['filters'] or {}
    params = ctx['params_json'] or {}
    time_range = _t(filters.get('time_range') or params.get('time_range'))
    store_id = _t(filters.get('store_id') or params.get('store_id'))
    if time_range or store_id:
        parts = []
        if store_id:
            parts.append(f"门店={store_id}")
        if time_range:
            parts.append(f"时间={time_range}")
        lines.append("上一轮筛选：" + "，".join(parts))
    if ctx['names']:
        lines.append(f"上一轮结果对象：{'、'.join(ctx['names'][:6])}")
    if ctx['stores']:
        lines.append(f"上一轮涉及门店：{'、'.join(ctx['stores'][:4])}")
    if ctx['last_assistant']:
        lines.append(f"上一轮回答摘要：{ctx['last_assistant'][:160]}")
    return "\n".join(lines)


def _query_requests_employee_attribute(query_text: str) -> bool:
    q = _t(query_text)
    if not q:
        return False
    return any(token in q for token in ('干嘛', '做什么', '岗位', '职位', '角色', '负责什么', '是啥'))


def _query_requests_role_catalog(query_text: str) -> bool:
    q = _t(query_text)
    if not q or '角色' not in q:
        return False
    return any(token in q for token in ('有哪些角色', '什么角色', '角色有哪些', '角色列表', '系统角色'))


def _query_requests_store_attribute(query_text: str) -> bool:
    q = _t(query_text)
    if not q:
        return False
    return any(token in q for token in ('哪个店', '哪家店', '哪个门店', '哪家门店', '在哪个店', '在哪家店'))


def _query_requests_count(query_text: str) -> bool:
    q = _t(query_text)
    if not q:
        return False
    return any(token in q for token in ('几个', '多少', '总共', '一共', '共有', '总数'))


def _rewrite_contextual_query(query_text: str, history: list[dict[str, Any]]) -> str:
    q = _t(query_text)
    if not q:
        return q
    ctx = _last_query_context(history or [])
    has_context = bool(ctx.get('names') or ctx.get('stores') or ctx.get('intent'))
    if not has_context:
        return q
    pronoun_followup = any(token in q for token in ('他们', '她们', '这些人', '这几个人', '这些员工', '这批人', '上述员工', '这几个'))
    store_followup = any(token in q for token in ('那家店', '这个门店', '该门店'))
    modifier_followup = any(token in q for token in ('那', '改成', '换成', '再看', '最近7天', '近7天', '最近30天', '本月'))
    employee_attr_followup = (
        _query_requests_employee_attribute(q)
        and not _query_requests_role_catalog(q)
        and bool(ctx.get('names'))
    )
    store_attr_followup = _query_requests_store_attribute(q) and bool(ctx.get('names'))
    if employee_attr_followup:
        names = '、'.join(ctx.get('names', [])[:6])
        return f'{names}分别是什么岗位或角色'
    if store_attr_followup:
        names = '、'.join(ctx.get('names', [])[:6])
        return f'{names}分别属于哪家门店'
    if not (pronoun_followup or store_followup or modifier_followup):
        return q
    parts = [q, '（承接上一轮']
    if ctx.get('intent'):
        parts.append(f"查询类型：{ctx['intent']}")
    if ctx.get('names'):
        parts.append(f"对象：{'、'.join(ctx['names'][:6])}")
    elif ctx.get('stores'):
        parts.append(f"门店：{'、'.join(ctx['stores'][:4])}")
    filters = ctx.get('filters') or {}
    params = ctx.get('params_json') or {}
    time_range = _t(filters.get('time_range') or params.get('time_range'))
    if time_range:
        parts.append(f"上一轮时间范围：{time_range}")
    return '；'.join(parts) + '）'


def _query_history_prompt(history: list[dict[str, Any]], query_text: str) -> tuple[str, str]:
    rewritten_query = _rewrite_contextual_query(query_text, history)
    context_summary = _build_query_context_summary(history)
    if not context_summary:
        return rewritten_query, rewritten_query
    prompt = (
        "【上下文摘要】\n"
        f"{context_summary}\n"
        "【当前问题】\n"
        f"{query_text}\n"
        "【请先把当前问题改写成完整独立查询，再识别意图】\n"
        f"{rewritten_query}"
    )
    return rewritten_query, prompt


def _employees(conn, store_id: str, *, include_all: bool = False) -> dict[str, dict[str, str]]:
    """Build employee map for a store (or all stores if include_all)."""
    out: dict[str, dict[str, str]] = {}
    sid = _t(store_id)
    q = 'SELECT CAST(id AS TEXT) AS employee_id, display_name, role, store_id FROM users'
    q_params: list[str] = []
    if not include_all and sid:
        q += ' WHERE store_id=?'
        q_params.append(sid)
    try:
        rows = conn.execute(q, q_params).fetchall()
    except Exception:
        rows = []
    for r in rows:
        eid = _t(r['employee_id'])
        if not eid:
            continue
        emp_sid = _t(r['store_id']) if 'store_id' in r.keys() else ''
        if not _is_real_store(emp_sid):
            continue
        out[eid] = {'employee_name': _t(r['display_name']) or f'员工{eid}', 'position': _t(r['role']) or '珠宝顾问', 'role': _t(r['role']), 'store_id': emp_sid}
    user_ids_seen = set(out.keys())

    try:
        rows = conn.execute('SELECT employee_id, employee_name, position, store_id FROM employee_profiles').fetchall()
    except Exception:
        rows = []
    for r in rows:
        eid = _t(r['employee_id'])
        if not eid:
            continue
        if eid not in user_ids_seen:
            continue
        emp_sid = _t(r['store_id']) if 'store_id' in r.keys() else ''
        if not _is_real_store(emp_sid):
            continue
        if not include_all and sid and emp_sid != sid:
            continue
        rec = out.setdefault(eid, {'employee_name': '', 'position': '', 'role': '', 'store_id': emp_sid})
        if _t(r['employee_name']):
            rec['employee_name'] = _t(r['employee_name'])
        if _t(r['position']):
            rec['position'] = _t(r['position'])
    return out


def _practice_scores(conn, emap: dict[str, dict[str, str]], since: str, *, store_scope: str = '') -> list[dict[str, Any]]:
    """Get practice eval scores for employees in emap, with real employee names."""
    ids = set(emap.keys())
    if not ids:
        return []
    snames = _store_name_map()
    try:
        rows = conn.execute('SELECT employee_id, overall_score, created_at FROM practice_eval_records WHERE overall_score > 0 AND created_at>=?', (since,)).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        eid = _t(r['employee_id'])
        if eid not in ids:
            continue
        info = emap[eid]
        sid = info.get('store_id', store_scope)
        out.append({
            'employee_id': eid,
            'employee_name': info['employee_name'],
            'position': info['position'],
            'store_id': sid,
            'store_name': snames.get(sid, sid),
            'overall_score': _f(r['overall_score']),
            'created_at': _t(r['created_at']),
        })
    return out


def _training_completion(conn, emap: dict[str, dict[str, str]], since: str) -> list[dict[str, Any]]:
    """Compute per-employee training completion from cycle_daily_tasks."""
    ids = set(emap.keys())
    if not ids:
        return []
    snames = _store_name_map()
    try:
        rows = conn.execute(
            'SELECT user_id, COUNT(*) AS total, SUM(CASE WHEN status="completed" THEN 1 ELSE 0 END) AS done FROM cycle_daily_tasks WHERE created_at>=? GROUP BY user_id',
            (since,),
        ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        uid = _t(r['user_id'])
        if uid not in ids:
            continue
        total = _i(r['total'])
        done = _i(r['done'])
        rate = round(done / total * 100, 1) if total else 0
        info = emap[uid]
        sid = info.get('store_id', '')
        out.append({
            'employee_id': uid,
            'employee_name': info['employee_name'],
            'position': info['position'],
            'store_id': sid,
            'store_name': snames.get(sid, sid),
            'training_completed': done,
            'training_required': total,
            'completion_rate': rate,
        })
    return out


def _query_rows(
    conn,
    store_id: str,
    qtype: str,
    params: dict[str, Any],
    *,
    allow_global_scope: bool = False,
    allowed_employee_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    snames = _store_name_map()
    include_all = allow_global_scope
    emap = _employees(conn, store_id, include_all=include_all)
    if allowed_employee_ids is not None:
        emap = {eid: info for eid, info in emap.items() if eid in allowed_employee_ids}
    ids = set(emap.keys())
    if not ids:
        return []
    since = _since(params.get('time_range', 'recent_30d'))

    if qtype == 'store_training_completion_rank':
        completion_rows = _training_completion(conn, emap, since)
        by_store: dict[str, dict[str, Any]] = {}
        for row in completion_rows:
            sid = _t(row.get('store_id'))
            if not sid or not _is_real_store(sid):
                continue
            bucket = by_store.setdefault(
                sid,
                {
                    'store_id': sid,
                    'store_name': snames.get(sid, sid),
                    'employee_count': 0,
                    'training_completed': 0,
                    'training_required': 0,
                },
            )
            bucket['employee_count'] += 1
            bucket['training_completed'] += _i(row.get('training_completed'), 0)
            bucket['training_required'] += _i(row.get('training_required'), 0)
        out: list[dict[str, Any]] = []
        for item in by_store.values():
            required = _i(item.get('training_required'), 0)
            completed = _i(item.get('training_completed'), 0)
            item['completion_rate'] = round(completed / required * 100, 1) if required > 0 else 0.0
            item['record_count'] = item['employee_count']
            out.append(item)
        reverse = _t(params.get('sort_order')).lower() in {'desc', 'highest', 'high'}
        return sorted(out, key=lambda x: (_f(x.get('completion_rate')), _t(x.get('store_name'))), reverse=reverse)[:20]

    if qtype == 'training_unfinished_newcomer':
        # Use real data from cycle_daily_tasks instead of learning_eval_records (which has NULL data)
        tc = _training_completion(conn, emap, since)
        # Filter to newcomers only
        out = []
        for r in tc:
            eid = r['employee_id']
            role = _t(emap.get(eid, {}).get('role')).lower()
            if role not in {'', 'trainee', 'newbie'}:
                continue
            if r['completion_rate'] < 100:
                out.append(r)
        return sorted(out, key=lambda x: x['completion_rate'])[:20]

    if qtype == 'training_incomplete_staff':
        tc = _training_completion(conn, emap, since)
        out = [row for row in tc if _f(row.get('completion_rate'), 100.0) < 100.0]
        return sorted(
            out,
            key=lambda x: (
                _f(x.get('completion_rate'), 100.0),
                _i(x.get('training_completed'), 0),
                _t(x.get('employee_name')),
            ),
        )[:20]

    # For score-based queries, use real practice eval data
    if qtype == 'low_compliance_staff':
        thr = _i(params.get('threshold_value'), 60) or 60
        scores = _practice_scores(conn, emap, since, store_scope=store_id)
        smap: dict[str, list[float]] = {}
        for r in scores:
            smap.setdefault(r['employee_id'], []).append(r['overall_score'])
        out = []
        for eid, vals in smap.items():
            if not vals:
                continue
            avg = round(sum(vals) / len(vals), 2)
            if avg < thr:
                info = emap[eid]
                sid = info.get('store_id', store_id)
                out.append({'employee_id': eid, 'employee_name': info['employee_name'], 'position': info['position'], 'store_id': sid, 'store_name': snames.get(sid, sid), 'compliance_score': avg, 'threshold_value': thr, 'sample_count': len(vals)})
        return sorted(out, key=lambda x: x['compliance_score'])[:20]

    if qtype == 'practice_decline_staff':
        d = max(7, _days(params.get('time_range', 'recent_30d')))
        recent_since = datetime.now(timezone.utc) - timedelta(days=d)
        prev_since = recent_since - timedelta(days=d)
        try:
            rows2 = conn.execute('SELECT employee_id, overall_score, created_at FROM practice_eval_records WHERE overall_score > 0 AND created_at>=?', (prev_since.isoformat(),)).fetchall()
        except Exception:
            rows2 = []
        rmap: dict[str, list[float]] = {}
        pmap: dict[str, list[float]] = {}
        for r in rows2:
            eid = _t(r['employee_id'])
            if eid not in ids:
                continue
            if _t(r['created_at']) >= recent_since.isoformat():
                rmap.setdefault(eid, []).append(_f(r['overall_score'], 0.0))
            else:
                pmap.setdefault(eid, []).append(_f(r['overall_score'], 0.0))
        out = []
        for eid in ids:
            rv, pv = rmap.get(eid) or [], pmap.get(eid) or []
            if not rv or not pv:
                continue
            ravg, pavg = round(sum(rv) / len(rv), 2), round(sum(pv) / len(pv), 2)
            if ravg < pavg:
                info = emap[eid]
                sid = info.get('store_id', store_id)
                out.append({'employee_id': eid, 'employee_name': info['employee_name'], 'position': info['position'], 'store_id': sid, 'store_name': snames.get(sid, sid), 'recent_avg_score': ravg, 'previous_avg_score': pavg, 'score_delta': round(ravg - pavg, 2), 'trend_direction': 'down'})
        return sorted(out, key=lambda x: x['score_delta'])[:20]

    if qtype == 'recent_high_risk_staff':
        scores = _practice_scores(conn, emap, since, store_scope=store_id)
        hmap: dict[str, int] = {}
        amap: dict[str, list[float]] = {}
        for r in scores:
            eid = r['employee_id']
            sc = r['overall_score']
            amap.setdefault(eid, []).append(sc)
            if sc < 70:
                hmap[eid] = hmap.get(eid, 0) + 1
        out = []
        for eid, c in hmap.items():
            info = emap[eid]
            vals = amap.get(eid) or []
            sid = info.get('store_id', store_id)
            out.append({'employee_id': eid, 'employee_name': info['employee_name'], 'position': info['position'], 'store_id': sid, 'store_name': snames.get(sid, sid), 'high_risk_count': c, 'recent_avg_score': round(sum(vals) / len(vals), 2) if vals else -1.0})
        return sorted(out, key=lambda x: (-_i(x['high_risk_count']), x['recent_avg_score']))[:20]

    if qtype == 'followup_priority_staff':
        scores = _practice_scores(conn, emap, since, store_scope=store_id)
        eid_scores: dict[str, list[float]] = {}
        for r in scores:
            eid_scores.setdefault(r['employee_id'], []).append(r['overall_score'])
        out = []
        for eid, vals in eid_scores.items():
            if not vals:
                continue
            avg = round(sum(vals) / len(vals), 2)
            risk_n = len([x for x in vals if x < 70])
            priority = (45 if avg < 70 else 0) + min(30, risk_n * 10)
            if priority >= 35:
                info = emap[eid]
                sid = info.get('store_id', store_id)
                out.append({'employee_id': eid, 'employee_name': info['employee_name'], 'position': info['position'], 'store_id': sid, 'store_name': snames.get(sid, sid), 'priority_score': min(100, priority), 'recent_avg_score': avg, 'reason': '综合分偏低/高风险次数较多'})
        return sorted(out, key=lambda x: -_i(x['priority_score']))[:20]

    if qtype == 'high_freq_knowledge_tag_staff':
        tag = _t(params.get('knowledge_tag'))
        try:
            arows = conn.execute("SELECT employee_id, customer_question, question_type FROM assistant_records WHERE created_at>=?", (since,)).fetchall()
        except Exception:
            arows = []
        cnt: dict[str, int] = {}
        htag: dict[str, str] = {}
        for r in arows:
            eid = _t(r['employee_id'])
            if eid not in ids:
                continue
            # Use customer_question (has data) instead of knowledge_tag (all NULL)
            t = _t(r['question_type']) or _t(r['customer_question'])
            if not t:
                continue
            if tag and tag not in t:
                continue
            cnt[eid] = cnt.get(eid, 0) + 1
            if t and eid not in htag:
                htag[eid] = t[:40]
        out = []
        for eid, c in cnt.items():
            info = emap[eid]
            sid = info.get('store_id', store_id)
            out.append({'employee_id': eid, 'employee_name': info['employee_name'], 'position': info['position'], 'store_id': sid, 'store_name': snames.get(sid, sid), 'knowledge_tag': tag or htag.get(eid, '客户咨询'), 'question_count': c})
        return sorted(out, key=lambda x: -_i(x['question_count']))[:20]

    return []


def _local_parse(text: str) -> dict[str, Any]:
    q = _t(text)
    params = {'time_range': 'recent_30d', 'threshold_op': 'unspecified', 'threshold_value': 0, 'trend_direction': 'unspecified', 'risk_level': 'unspecified', 'knowledge_tag': '', 'target_group': 'all_staff'}
    if '这个月' in q or '本月' in q:
        params['time_range'] = 'this_month'
    qt = 'unsupported'
    if '新人' in q and ('未完成' in q or '没完成' in q):
        qt = 'training_unfinished_newcomer'; params['target_group'] = 'newcomer'
    elif _is_training_incomplete_query(q):
        qt = 'training_incomplete_staff'; params['target_group'] = 'all_staff'
    elif '门店' in q and ('培训' in q or '训练' in q) and ('完成率' in q or '完成度' in q):
        qt = 'store_training_completion_rank'
        params['sort_order'] = 'desc' if any(x in q for x in ('最高', '最好', '最优')) else 'asc'
    elif '合规' in q and ('低于' in q or '小于' in q):
        qt = 'low_compliance_staff'; params['threshold_op'] = 'lt'; params['threshold_value'] = _i(re.search(r'(?:低于|小于)\s*(\d{1,3})', q).group(1) if re.search(r'(?:低于|小于)\s*(\d{1,3})', q) else 60, 60)
    elif ('陪练' in q or '成绩' in q) and ('下降' in q or '下滑' in q):
        qt = 'practice_decline_staff'; params['trend_direction'] = 'down'
    elif '高风险' in q:
        qt = 'recent_high_risk_staff'; params['risk_level'] = 'high'
    elif '跟进' in q and ('重点' in q or '优先' in q):
        qt = 'followup_priority_staff'
    elif ('高频' in q or '频繁' in q) and ('知识点' in q or '提问' in q):
        qt = 'high_freq_knowledge_tag_staff'
    blocked = any(x in q.lower() for x in ['sql', '身份证', '手机号导出', '数据库账号'])
    if blocked:
        return {'query_type': 'unsupported', 'params_json': params, 'rewritten_query': q, 'confidence_level': 'high', 'can_query': 0, 'route_type': 'blocked', 'scope_status': 'blocked', 'problem_note': '命中安全拦截'}
    can_query = 1 if qt in SUPPORTED_QUERY_TYPES else 0
    return {'query_type': qt, 'params_json': params, 'rewritten_query': q, 'confidence_level': 'medium', 'can_query': can_query, 'route_type': 'template_hit' if can_query else 'out_of_scope', 'scope_status': 'in_scope' if can_query else 'out_of_scope', 'problem_note': '' if can_query else '未命中支持模板'}


def _is_training_incomplete_query(query_text: str) -> bool:
    q = _t(query_text)
    if not q or '培训' not in q:
        return False
    if '完成率' in q or '完成情况' in q or '门店培训完成率' in q:
        return False
    return any(token in q for token in _TRAINING_INCOMPLETE_TOKENS)


def _has_newcomer_scope(query_text: str) -> bool:
    q = _t(query_text)
    return any(token in q for token in _NEWCOMER_TOKENS)


def _normalize_supported_query_type(
    query_text: str,
    query_type: str,
    params_json: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    q = _t(query_text)
    params = dict(params_json or {})
    qtype = _t(query_type) or 'unsupported'
    if _is_training_incomplete_query(q) and not _has_newcomer_scope(q):
        params['target_group'] = 'all_staff'
        if _t(params.get('time_range')) in {'', 'unspecified'}:
            params['time_range'] = 'recent_30d'
        return 'training_incomplete_staff', params
    return qtype, params


def _clean_query_reply_text(text: Any) -> str:
    clean = re.sub(r"\s+", " ", _t(text)).strip()
    if not clean:
        return ''
    clean = re.sub(r"\s*([，。！？；：,!?;:])\s*", r"\1", clean)
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", clean) if part.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = re.sub(r"[。！？!?；;\s]+$", "", part)
        if key and key in seen:
            continue
        deduped.append(part)
        if key:
            seen.add(key)
    return ''.join(deduped).strip() or clean


def _pick_query_reply_text(
    *,
    user_visible_output: Any = '',
    summary_text: Any = '',
    manager_advice: Any = '',
    fallback_text: Any = '',
) -> str:
    for candidate in (user_visible_output, summary_text, manager_advice, fallback_text):
        clean = _clean_query_reply_text(candidate)
        if clean:
            return clean
    return ''


_QUERY2_INVALID_REPLY_MARKERS = (
    '当前查询结果输入无效',
    '未进入总结生成',
    '已完成查询结果总结。',
)


def _is_valid_query2_reply_text(
    text: Any,
    *,
    workflow_status: Any = '',
) -> bool:
    clean = _clean_query_reply_text(text)
    status = _t(workflow_status).lower()
    if not clean:
        return False
    if status and status not in {'success', 'empty', 'error'}:
        return False
    low = clean.lower()
    if 'incorrect model credentials' in low:
        return False
    return not any(marker in clean for marker in _QUERY2_INVALID_REPLY_MARKERS)


def _focus_names_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    keys = ('employee_name', 'display_name', 'store_name', 'knowledge_tag', 'module_name')
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        for key in keys:
            name = _t(row.get(key))
            if name and name not in names:
                names.append(name)
                break
    return names


def _fmt_val(v: Any, key: str = '') -> str:
    """Format a value for natural language output."""
    if v is None or v == '':
        return '--'
    s = str(v)
    # Score fields: show 1 decimal
    if any(k in key for k in ('score', 'rate', 'completion')):
        try:
            return f'{float(v):.1f}'
        except (ValueError, TypeError):
            pass
    # Timestamp: compact
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', s)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    return s


def _store_name_map() -> dict[str, str]:
    """Build store_id → store_name mapping (real stores only)."""
    out: dict[str, str] = {}
    try:
        with get_conn() as conn:
            rows = conn.execute('SELECT store_id, store_name FROM stores').fetchall()
            for r in rows:
                sid = _t(r['store_id'])
                if sid and _is_real_store(sid):
                    out[sid] = _t(r['store_name']) or sid
    except Exception:
        pass
    return out


def _enrich_rows_with_names(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add store_name and employee display_name to result rows. Filter fake stores."""
    snames = _store_name_map()
    enriched: list[dict[str, Any]] = []
    for r in rows:
        sid = _t(r.get('store_id', ''))
        if sid and not _is_real_store(sid):
            continue
        new = dict(r)
        if sid and sid in snames:
            new['store_name'] = snames[sid]
        # Resolve employee name from users table if missing
        eid = _t(r.get('employee_id', ''))
        if eid and not _t(new.get('employee_name')):
            try:
                with get_conn() as conn:
                    u = conn.execute('SELECT display_name FROM users WHERE CAST(id AS TEXT)=?', (eid,)).fetchone()
                    if u and _t(u['display_name']):
                        new['employee_name'] = _t(u['display_name'])
            except Exception:
                pass
        enriched.append(new)
    return enriched


_HIDDEN_RESULT_FIELDS: frozenset[str] = frozenset({
    # Internal IDs
    'user_id', 'store_id', 'employee_id', 'practice_id', 'task_id',
    'record_id', 'conversation_id', 'session_id', 'cycle_id', 'exam_id',
    'paper_id', 'plan_id', 'scenario_id', 'publisher_id',
    # System/internal fields not useful for end users
    'payload_json', 'raw_payload', 'raw_output', 'score_json',
    'dimension_focus', 'route_page', 'branch',
    'day_unlock_json', 'daily_plan_json', 'adaptive_state_json',
    'score_branch', 'cycle_day_index', 'update_source',
    'paper_answer_json', 'paper_result_json', 'paper_config_json',
    'dashboard_result_json', 'plan_meta_json', 'highlights_json',
    'problem_points_json', 'summary_json', 'report_markdown', 'tags_json',
    'answer_json', 'result_json', 'grading_detail', 'radar_data',
    'source_workflow', 'source_workflow_parse', 'source_workflow_summary',
    'source_workflow_analyze', 'source_workflow_reply',
    'dialogue_text', 'user_query', 'question_text', 'user_answer',
    'standard_answer', 'evaluation_text',
    'previous_cycle_id', 'task_version_snapshot', 'score_visibility_snapshot',
    'exam_mode_snapshot', 'review_source',
    'full_release_by_admin', 'sort_order', 'target_count', 'current_count',
})


def _is_hidden_result_field(key: Any) -> bool:
    name = _t(key)
    if not name:
        return False
    if name in _HIDDEN_RESULT_FIELDS:
        return True
    if name.lower() == 'id' or name.lower().endswith('_id'):
        return True
    return False


def _sanitize_result_rows_for_response(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        clean_row = {
            key: value
            for key, value in row.items()
            if not _is_hidden_result_field(key)
        }
        sanitized.append(clean_row)
    return sanitized


def _reply_paragraphs(*parts: str) -> str:
    paragraphs: list[str] = []
    for part in parts:
        clean = _clean_query_reply_text(part)
        if clean and clean not in paragraphs:
            paragraphs.append(clean)
    return "\n\n".join(paragraphs)


def _unique_focus_names(rows: list[dict[str, Any]], keys: tuple[str, ...], limit: int = 8) -> list[str]:
    names: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = _t(row.get(key))
            if value and value not in names:
                names.append(value)
                break
        if len(names) >= limit:
            break
    return names


def _natural_list_text(names: list[str], unit: str, *, max_items: int = 3) -> str:
    clean_names = [name for name in names if _t(name)]
    if not clean_names:
        return ''
    if len(clean_names) <= max_items:
        return '、'.join(clean_names)
    return f"{'、'.join(clean_names[:max_items])}等{len(clean_names)}{unit}"


def _normalize_count_key(key: Any) -> str:
    raw = _t(key).lower()
    if not raw:
        return ''
    compact = re.sub(r'\s+', '', raw)
    if re.fullmatch(r'count\([^)]*\)', compact):
        return 'total_count'
    canonical = re.sub(r'[^a-z0-9_]+', '', compact)
    if canonical in {'count', 'countall', 'total', 'total_count', 'totalcount'}:
        return 'total_count'
    if canonical in {'store_count', 'stores_count', 'total_store_count', 'total_stores', 'store_total'}:
        return 'store_count'
    if canonical in {
        'employee_count',
        'employees_count',
        'staff_count',
        'user_count',
        'users_count',
        'total_employee_count',
        'total_employees',
        'total_staff',
        'total_user_count',
        'total_users',
        'employee_total',
        'user_total',
    }:
        return 'employee_count'
    if canonical in {'question_count', 'questions_count', 'total_questions'}:
        return 'question_count'
    if canonical in {'record_count', 'records_count', 'total_records'}:
        return 'record_count'
    if canonical.startswith('total_') and canonical.endswith('_count'):
        return 'total_count'
    return ''


def _count_value_from_row(row: dict[str, Any]) -> tuple[str, int] | None:
    preferred_keys = (
        'total_count',
        'store_count',
        'employee_count',
        'question_count',
        'record_count',
    )
    for key in preferred_keys:
        if key in row and _t(row.get(key)) != '':
            return key, _i(row.get(key), 0)
    for key, value in row.items():
        normalized_key = _normalize_count_key(key)
        if normalized_key and _t(value) != '':
            return normalized_key, _i(value, 0)
    return None


def _count_query_subject(qtext: str) -> tuple[str, str]:
    text = _t(qtext)
    if any(token in text for token in ('系统', '全系统', '系统里面', '所有门店', '全部门店')):
        if any(token in text for token in ('用户', '账号', '账户')):
            return '当前系统共有', '个用户'
        if any(token in text for token in ('员工', '人员', '店员', '导购')):
            return '当前系统共有', '个员工'
        if any(token in text for token in ('门店', '店铺', '分店', '店面')):
            return '当前系统共有', '家门店'
        return '当前系统查到', '条结果'
    if any(token in text for token in ('用户', '账号', '账户')):
        return '当前条件下共有', '个用户'
    if any(token in text for token in ('员工', '人员', '店员', '导购')):
        prefix = '当前门店共有' if any(token in text for token in ('门店', '店里', '店内')) else '当前条件下共有'
        return prefix, '个员工'
    if any(token in text for token in ('门店', '店铺', '分店', '店面')):
        return '当前条件下共有', '家门店'
    if any(token in text for token in ('知识点', '标签')):
        return '当前条件下共有', '个知识点'
    return '当前条件下查到', '条结果'


def _count_value_sentence(qtext: str, row: dict[str, Any]) -> str:
    count_value = _count_value_from_row(row)
    if not count_value:
        return ''
    _numeric_key, value = count_value
    prefix, unit = _count_query_subject(qtext)
    return f"{prefix}{value}{unit}。"


def _query_time_label(qtext: str) -> str:
    q = _t(qtext)
    if not q:
        return ''
    mapping = (
        ('近7天', '近7天'),
        ('最近7天', '近7天'),
        ('最近30天', '近30天'),
        ('近30天', '近30天'),
        ('最近90天', '近90天'),
        ('近90天', '近90天'),
        ('本月', '本月'),
        ('这个月', '本月'),
        ('今天', '今天'),
        ('今日', '今天'),
    )
    for token, label in mapping:
        if token in q:
            return label
    return ''


def _query_role_label(qtext: str) -> str:
    q = _t(qtext)
    if not q:
        return ''
    for token in ('店长', '管理员', '导购', '资深顾问', '新人', '新员工'):
        if token in q:
            return token
    return ''


def _row_store_name(row: dict[str, Any]) -> str:
    return _t(row.get('store_name') or row.get('store_id'))


def _row_employee_name(row: dict[str, Any]) -> str:
    return _t(row.get('employee_name') or row.get('display_name') or row.get('user_name') or row.get('username'))


def _row_position_name(row: dict[str, Any]) -> str:
    return _t(row.get('position') or row.get('role'))


def _total_count_from_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        count_value = _count_value_from_row(row)
        if count_value:
            key, value = count_value
            if value > 0 or key in {'total_count', 'store_count', 'employee_count'}:
                return value
    return len(rows)


def _query_scope_phrase(qtext: str, rows: list[dict[str, Any]]) -> str:
    q = _t(qtext)
    store_names = _unique_focus_names(rows, ('store_name',), limit=3)
    time_label = _query_time_label(q)
    role_label = _query_role_label(q)
    if any(token in q for token in ('系统', '全系统', '所有门店', '全部门店')):
        scope = '当前系统'
    elif len(store_names) == 1:
        scope = f'当前门店（{store_names[0]}）'
    elif any(token in q for token in ('本店', '门店', '店里', '店内')):
        scope = '当前门店范围'
    else:
        scope = '当前条件下'
    extras = [item for item in (time_label, role_label) if item]
    if extras:
        scope = f"{scope}{'，'.join(extras)}"
    return scope


def _employee_detail_sentence(row: dict[str, Any], qtext: str, *, include_metric: bool = True) -> str:
    name = _row_employee_name(row)
    if not name:
        return ''
    parts: list[str] = []
    position = _row_position_name(row)
    store = _row_store_name(row)
    if position:
        parts.append(f'岗位是{position}')
    if store:
        parts.append(f'所属门店是{store}')
    if include_metric:
        metric = _single_row_metric_snippet(row)
        if metric:
            parts.append(metric)
    if not parts:
        return name
    return f"{name}，" + '，'.join(parts)


def _employee_metric_overview(rows: list[dict[str, Any]]) -> str:
    metric_specs = (
        ('high_risk_count', '高风险次数', '次'),
        ('question_count', '提问次数', '次'),
        ('completion_rate', '完成率', '%'),
        ('training_completion_rate', '完成率', '%'),
        ('compliance_score', '合规得分', '分'),
        ('overall_score', '综合得分', '分'),
        ('recent_avg_score', '近期均分', '分'),
        ('priority_score', '优先级', '分'),
    )
    for key, label, unit in metric_specs:
        values: list[float] = []
        for row in rows:
            if not isinstance(row, dict) or _t(row.get(key)) == '':
                continue
            values.append(_f(row.get(key)))
        if not values:
            continue
        low = min(values)
        high = max(values)
        if unit == '%':
            low_text = f"{low:.0f}%"
            high_text = f"{high:.0f}%"
        elif all(float(v).is_integer() for v in values):
            low_text = str(int(round(low)))
            high_text = str(int(round(high)))
        else:
            low_text = f"{low:.1f}"
            high_text = f"{high:.1f}"
        if low == high:
            return f'{label}大多在 {low_text}{unit if unit != "%" else ""}'
        return f'{label}集中在 {low_text} 到 {high_text}{unit if unit != "%" else ""} 之间'
    return ''


def _employee_natural_reply(
    qtext: str,
    rows: list[dict[str, Any]],
    *,
    max_items: int = 5,
    max_names: int = 20,
) -> str:
    employee_rows = [row for row in rows if isinstance(row, dict) and _row_employee_name(row)]
    if not employee_rows:
        return ''
    total = _total_count_from_rows(employee_rows)
    count = total or len(employee_rows)
    scope = _query_scope_phrase(qtext, employee_rows)
    names: list[str] = []
    for row in employee_rows:
        name = _row_employee_name(row)
        if name and name not in names:
            names.append(name)
        if len(names) >= max_names:
            break
    if names:
        name_text = '、'.join(names)
        if count > len(names):
            name_text = f"{name_text}等{count}位员工"
        lead = f"{scope}共查到{count}位员工：{name_text}。"
    else:
        lead = f"{scope}共查到{count}位员工。"

    detail_items: list[str] = []
    for row in employee_rows[:max_items]:
        detail = _employee_detail_sentence(row, qtext)
        if detail:
            detail_items.append(detail)
    if not detail_items:
        return lead
    detail_text = '具体看，' + '；'.join(detail_items[:max_items]) + '。'
    if len(employee_rows) > max_items:
        overview = _employee_metric_overview(employee_rows)
        detail_text += f"整体上，{overview}。" if overview else '其余员工可结合下方结果继续查看。'
    return _reply_paragraphs(lead, detail_text)


def _single_row_metric_snippet(row: dict[str, Any]) -> str:
    if _t(row.get('training_completed')) != '' and _t(row.get('training_required')) != '':
        completed = _i(row.get('training_completed'), 0)
        required = _i(row.get('training_required'), 0)
        return f'当前进度 {completed}/{required}'
    metrics = [
        ('high_risk_count', lambda value: f'最近高风险 {value} 次'),
        ('question_count', lambda value: f'相关提问 {value} 次'),
        ('completion_rate', lambda value: f'完成率 {_f(value):.0f}%'),
        ('training_completion_rate', lambda value: f'完成率 {_f(value):.0f}%'),
        ('compliance_score', lambda value: f'合规得分 {_f(value):.1f} 分'),
        ('overall_score', lambda value: f'综合得分 {_f(value):.1f} 分'),
        ('recent_avg_score', lambda value: f'近期均分 {_f(value):.1f} 分'),
        ('priority_score', lambda value: f'优先级 {_f(value):.0f} 分'),
    ]
    for key, formatter in metrics:
        if key in row and _t(row.get(key)) != '':
            return formatter(row.get(key))
    return ''


def _single_row_attribute_snippet(row: dict[str, Any], qtext: str) -> str:
    if _query_requests_employee_attribute(qtext):
        role = _t(row.get('position') or row.get('role'))
        if role:
            return f'岗位是{role}'
    if _query_requests_store_attribute(qtext):
        store = _t(row.get('store_name'))
        if store:
            return f'在{store}'
    return ''


def _multi_row_attribute_sentence(rows: list[dict[str, Any]], qtext: str) -> str:
    if not rows or not (_query_requests_employee_attribute(qtext) or _query_requests_store_attribute(qtext)):
        return ''
    parts: list[str] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        name = _t(row.get('employee_name') or row.get('display_name'))
        if not name:
            continue
        detail_parts: list[str] = []
        role = _t(row.get('position') or row.get('role'))
        store = _t(row.get('store_name'))
        if role:
            detail_parts.append(f'岗位是{role}')
        if store:
            detail_parts.append(f'所属门店是{store}')
        if detail_parts:
            parts.append(f"{name}，" + '，'.join(detail_parts))
    if not parts:
        return ''
    return '，'.join(parts) + '。'


def _success_followup_paragraph(qtext: str, rows: list[dict[str, Any]], advice: str = '') -> str:
    if advice:
        return advice
    if _query_requests_count(qtext) or _query_requests_employee_attribute(qtext) or _query_requests_store_attribute(qtext):
        return ''
    if len(rows or []) <= 3 and any(token in _t(qtext) for token in ('有哪些', '哪几个', '哪几位', '哪些人', '哪些员工')):
        return ''
    return '如果你想继续缩小范围，我可以再按时间、门店或人员继续细化。'


def _build_data_driven_summary(qtext: str, rows: list[dict[str, Any]]) -> str:
    """Generate a natural first-paragraph summary from query result rows."""
    if not rows:
        return ''
    n = len(rows)
    first = rows[0] if rows else {}
    keys = list(first.keys())

    # ── Determine what kind of data we have ──
    has_store = 'store_id' in keys or 'store_name' in keys
    has_employee = 'employee_name' in keys or 'display_name' in keys
    has_score = any('score' in k.lower() for k in keys)
    has_rate = any('rate' in k.lower() or 'completion' in k.lower() for k in keys)
    is_count = len(keys) <= 2 and any(
        k in keys for k in ('total_count', 'employee_count', 'store_count', 'record_count')
    )

    # Store name resolution
    snames = _store_name_map()

    def _store(sid: str) -> str:
        return snames.get(_t(sid), _t(sid))

    def _store_from_row(row: dict) -> str:
        """Get store name from row (prefer store_name field), fallback to lookup."""
        sn = _t(row.get('store_name'))
        if sn:
            return sn
        return _store(_t(row.get('store_id', '')))

    def _emp(row: dict) -> str:
        return _t(row.get('employee_name') or row.get('display_name') or '未知')

    # ── Count queries ──
    count_sentence = _count_value_sentence(qtext, first) if n == 1 else ''
    if is_count and count_sentence:
        return count_sentence

    employee_names = _unique_focus_names(rows, ('employee_name', 'display_name'))
    if employee_names:
        attr_sentence = _multi_row_attribute_sentence(rows, qtext)
        if attr_sentence:
            return attr_sentence
        list_text = _natural_list_text(employee_names, '位员工')
        if len(employee_names) == 1:
            metric_snippet = _single_row_attribute_snippet(first, qtext) or _single_row_metric_snippet(first)
            if '高风险' in qtext:
                return f'最近需要重点关注的员工是{employee_names[0]}。' + (f'他目前{metric_snippet}。' if metric_snippet else '')
            if any(token in qtext for token in ('未完成', '没完成', '没参加', '没有参加', '尚未参加', '未开始', '尚未开始')):
                return f'目前还没完成培训的员工是{employee_names[0]}。' + (f'他目前{metric_snippet}。' if metric_snippet else '')
            if any(token in qtext for token in ('下降', '下滑')):
                return f'最近表现下滑的员工是{employee_names[0]}。' + (f'他目前{metric_snippet}。' if metric_snippet else '')
            if '跟进' in qtext and any(token in qtext for token in ('重点', '优先')):
                return f'当前建议优先跟进的员工是{employee_names[0]}。' + (f'他目前{metric_snippet}。' if metric_snippet else '')
            return f'我查到的相关员工是{employee_names[0]}。' + (f'他目前{metric_snippet}。' if metric_snippet else '')
        if '高风险' in qtext:
            return f'最近需要重点关注的员工有{list_text}。'
        if any(token in qtext for token in ('未完成', '没完成', '没参加', '没有参加', '尚未参加', '未开始', '尚未开始')):
            return f'目前还没完成培训的员工有{list_text}。'
        if any(token in qtext for token in ('下降', '下滑')):
            return f'最近表现下滑的员工有{list_text}。'
        if '跟进' in qtext and any(token in qtext for token in ('重点', '优先')):
            return f'当前建议优先跟进的员工有{list_text}。'
        if any(token in qtext for token in ('知识点', '提问')):
            return f'最近高频提问相关知识点的员工有{list_text}。'
        return f'我查到的相关员工有{list_text}。'

    store_names = _unique_focus_names(rows, ('store_name',))
    if store_names:
        list_text = _natural_list_text(store_names, '家门店')
        if any(token in qtext for token in ('完成率', '培训')):
            return f'当前与培训完成情况相关的门店有{list_text}。'
        return f'当前相关门店有{list_text}。'

    if count_sentence:
        return count_sentence

    focus_names = _focus_names_from_rows(rows)
    if focus_names:
        list_text = _natural_list_text(focus_names, '个对象')
        return f'按当前条件，我查到的重点对象有{list_text}。'
    return f'按当前条件，我查到 {n} 条相关结果。'


def _fmt_col_label(key: str) -> str:
    """Human-readable column label."""
    _LABELS = {
        'employee_id': '工号',
        'employee_name': '员工姓名',
        'display_name': '员工姓名',
        'user_id': '工号',
        'user_name': '用户名',
        'username': '用户名',
        'store_id': '所属门店',
        'store_name': '门店名称',
        'position': '岗位',
        'role': '角色',
        'action': '操作',
        'target_type': '操作对象',
        'status': '状态',
        'risk_level': '风险等级',
        'description': '描述',
        'fields': '字段',
        'title': '标题',
        'task_name': '任务名称',
        'module_name': '培训模块',
        'knowledge_tag': '知识分类',
        'overall_score': '综合得分',
        'compliance_score': '合规得分',
        'practice_score': '陪练得分',
        'training_completion_rate': '培训完成率',
        'completion_rate': '完成率',
        'avg_score': '平均得分',
        'score': '得分',
        'priority_score': '优先级',
        'recent_avg_score': '近期均分',
        'previous_avg_score': '前期均分',
        'score_delta': '分数变化',
        'question_count': '提问次数',
        'record_count': '记录数',
        'total_count': '总数',
        'store_count': '门店数',
        'employee_count': '员工数',
        'metric': '指标',
        'total_tasks': '任务总数',
        'completed_tasks': '已完成任务',
        'sample_count': '采样次数',
        'threshold_value': '阈值',
        'trend_direction': '趋势',
        'cycle_type': '周期类型',
        'stage_no': '阶段',
        'stage_name': '阶段名称',
        'stage_status': '阶段状态',
        'plan_total_stages': '总阶段数',
        'stage_pass_score': '及格分',
        'unlock_mode': '解锁模式',
        'day_index': '天数',
        'module_code': '模块代码',
        'module_name': '模块名称',
        'task_source': '任务来源',
        'release_status': '发布状态',
        'ai_score': 'AI 评分',
        'ai_feedback': 'AI 反馈',
        'evaluation_status': '评估状态',
        'exam_mode': '考试模式',
        'submit_time': '提交时间',
        'grading_result': '评分结果',
        'result_type': '结果类型',
        'content_summary': '内容摘要',
        'complete_time': '完成时间',
        'created_at': '创建时间',
        'updated_at': '更新时间',
    }
    return _LABELS.get(key, key)


def _row_title(row: dict[str, Any]) -> str:
    for key in ("employee_name", "display_name", "store_name", "title", "task_name", "module_name", "user_name", "username", "table_name"):
        val = _t(row.get(key))
        if val:
            return val
    return "记录"


def _format_row_highlight(row: dict[str, Any]) -> str:
    title = _row_title(row)
    preferred = [
        "store_name", "role", "position", "action", "target_type",
        "score", "overall_score", "compliance_score", "training_completion_rate",
        "completion_rate", "risk_level", "status", "created_at", "updated_at",
        "description", "fields",
    ]
    parts: list[str] = []
    for key in preferred:
        if key not in row:
            continue
        val = _fmt_val(row.get(key), key)
        if val == "--" or val == title:
            continue
        parts.append(f"{_fmt_col_label(key)}：{val}")
        if len(parts) >= 3:
            break
    if not parts:
        for key, val_raw in row.items():
            if _is_hidden_result_field(key):
                continue
            val = _fmt_val(val_raw, key)
            if val == "--" or val == title:
                continue
            parts.append(f"{_fmt_col_label(key)}：{val}")
            if len(parts) >= 3:
                break
    return f"{title}｜{'；'.join(parts)}" if parts else title


def _row_field_items(row: dict[str, Any], *, max_fields: int = 5) -> list[dict[str, str]]:
    preferred = [
        "employee_name",
        "display_name",
        "store_name",
        "position",
        "role",
        "training_completed",
        "training_required",
        "completion_rate",
        "training_completion_rate",
        "compliance_score",
        "overall_score",
        "recent_avg_score",
        "previous_avg_score",
        "score_delta",
        "high_risk_count",
        "priority_score",
        "question_count",
        "knowledge_tag",
        "action",
        "target_type",
        "status",
        "created_at",
        "updated_at",
        "description",
        "fields",
    ]
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in preferred + list(row.keys()):
        if key not in row:
            continue
        if _is_hidden_result_field(key):
            continue
        label = _fmt_col_label(key)
        if label in seen:
            continue
        value = _fmt_val(row.get(key), key)
        if value == "--":
            continue
        fields.append({"label": label, "value": value})
        seen.add(label)
        if len(fields) >= max_fields:
            break
    return fields


def _employee_row_fields(row: dict[str, Any]) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    position = _row_position_name(row)
    store = _row_store_name(row)
    metric = _single_row_metric_snippet(row)
    if position:
        fields.append({"label": "岗位", "value": position})
    if store:
        fields.append({"label": "所属门店", "value": store})
    if metric:
        fields.append({"label": "当前状态", "value": metric})
    return fields


def _reply_text_to_structured_sections(reply_text: str) -> list[dict[str, Any]]:
    raw = _t(reply_text).replace("\r\n", "\n").strip()
    if not raw:
        return []
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", raw) if block.strip()]
    if not blocks:
        clean = _clean_query_reply_text(raw)
        return [{"text": clean}] if clean else []
    sections: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        text = "\n".join(lines).strip()
        if text:
            sections.append({"text": text})
    return sections


def _structured_sections_to_text(sections: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for section in sections:
        title = _clean_query_reply_text(section.get("title"))
        text = _clean_query_reply_text(section.get("text"))
        fields = section.get("fields") if isinstance(section.get("fields"), list) else []
        items = [str(item).strip() for item in (section.get("items") or []) if str(item).strip()]
        lines: list[str] = []
        if title:
            lines.append(title)
        if text:
            lines.append(text)
        for field in fields:
            if not isinstance(field, dict):
                continue
            label = _clean_query_reply_text(field.get("label"))
            value = _clean_query_reply_text(field.get("value"))
            if label and value:
                lines.append(f"{label}：{value}")
            elif value:
                lines.append(value)
        lines.extend(items)
        block = "\n".join(lines).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


_COUNT_RESULT_KEYS = ('total_count', 'employee_count', 'store_count', 'record_count', 'metric')


def _count_result_sentence(qtext: str, rows: list[dict[str, Any]]) -> str:
    """Generate a natural language sentence for count-only query results."""
    if not rows or len(rows) > 3:
        return ''
    first = rows[0] if isinstance(rows[0], dict) else {}
    keys = list(first.keys())
    # Check if this is a count-style result
    count_key = ''
    for k in _COUNT_RESULT_KEYS:
        if k in keys:
            count_key = k
            break
    if not count_key:
        return ''
    # Single metric result (e.g. [{employee_count: 20}] or [{metric: store_count, total_count: 5}])
    if len(rows) == 1 and 'metric' in keys:
        metric = _t(first.get('metric'))
        count = _i(first.get('total_count'))
        _METRIC_LABELS = {
            'store_count': '家门店', 'employee_count': '个员工',
            'record_count': '条记录',
        }
        unit = _METRIC_LABELS.get(metric, '条结果')
        return f"当前系统共有{count}{unit}。"
    if len(rows) == 1:
        prefix, unit = _count_query_subject(qtext)
        return f"{prefix}{_i(first.get(count_key))}{unit}。"
    # Multiple count rows (e.g. UNION ALL)
    parts = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric = _t(row.get('metric'))
        count = _i(row.get('total_count') or row.get('employee_count') or row.get('store_count'))
        _METRIC_LABELS = {'store_count': '家门店', 'employee_count': '个员工'}
        unit = _METRIC_LABELS.get(metric, '条结果')
        if metric and count:
            parts.append(f"{count}{unit}")
    if parts:
        return f"当前系统共有{'、'.join(parts)}。"
    return ''


def _build_structured_sections(
    qtext: str,
    rows: list[dict[str, Any]],
    status: str,
    detail: str = "",
    advice: str = "",
) -> list[dict[str, Any]]:
    n = len(rows or [])
    sections: list[dict[str, Any]] = []
    if status == "success" and n > 0:
        reply = _build_natural_result_reply(qtext, rows, status, detail, advice)
        return _reply_text_to_structured_sections(reply)

    if status == "empty":
        sections.append({"title": "查询结果", "text": "当前条件下没有找到匹配记录。"})
        sections.append({"title": "建议", "text": advice or "可以放宽时间、门店、人员或指标条件后再查一次。"})
        return sections

    detail_text = detail or "这次查询没有拿到稳定结果。"
    sections.append({"title": "查询状态", "text": detail_text})
    sections.append({"title": "建议", "text": advice or "可以换成更具体的查询对象、时间范围或指标后重试。"})
    return sections


def _build_natural_result_reply(
    qtext: str,
    rows: list[dict[str, Any]],
    status: str,
    detail: str = "",
    advice: str = "",
) -> str:
    n = len(rows or [])
    if status == "success" and n > 0:
        employee_reply = _employee_natural_reply(qtext, rows)
        if employee_reply:
            return employee_reply
        return _reply_paragraphs(
            _build_data_driven_summary(qtext, rows),
            _success_followup_paragraph(qtext, rows, advice),
        )
    if status == "empty":
        return _reply_paragraphs(
            "我查了一下，当前条件下暂时没有找到匹配结果。",
            advice or "你可以换个时间范围、门店或指标再问一次。",
        )
    if detail:
        return _reply_paragraphs(
            f"这次查询没有顺利完成，原因是：{detail}。",
            advice or "你可以换个更具体的问法再试。",
        )
    return _reply_paragraphs(
        "这次查询没有拿到稳定结果。",
        advice or "你可以换个更具体的问法再试。",
    )


def _build_text_reply_payload(
    *,
    query_text: str,
    reply_text: str,
    reply_mode: str,
    display_tags: list[str] | None = None,
    manager_advice: str = '',
) -> dict[str, Any]:
    clean_reply = _t(reply_text).strip()
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n+", clean_reply) if paragraph.strip()]
    summary_text = _clean_query_reply_text(paragraphs[0] if paragraphs else clean_reply)
    advice_text = _clean_query_reply_text(manager_advice)
    sections = _reply_text_to_structured_sections(clean_reply)
    tags = [tag for tag in (display_tags or []) if _t(tag)]
    return {
        'summary': summary_text,
        'manager_advice': advice_text,
        'focus_names': [],
        'display_tags': tags,
        'structured_sections': sections,
        'user_visible_output': clean_reply,
        'reply_text': clean_reply,
        'key_findings': [summary_text or clean_reply, f'问题：{query_text[:40]}' if query_text else (summary_text or clean_reply)],
        'suggested_actions': [advice_text] if advice_text else [],
        'reply_mode': _t(reply_mode) or 'local_fallback',
    }


def _fallback_summary(qtype: str, qtext: str, rows: list[dict[str, Any]], status: str, detail: str = '') -> dict[str, Any]:
    n = len(rows)
    detail_text = _t(detail)
    focus_names = _focus_names_from_rows(rows)
    reply_mode = 'system_hit' if status in {'success', 'empty'} and qtype != 'unsupported' else 'local_fallback'
    if status == 'success' and n > 0:
        advice, tags = '', ['已出结果']
    elif ('安全' in detail_text) or ('拦截' in detail_text):
        reply_mode = 'blocked'
        advice, tags = '避免导出敏感信息或直接操作数据库。', ['安全拦截', '请改问法']
    elif qtype == 'unsupported':
        advice, tags = '换成系统支持的问题类型后，我可以继续帮你查。', ['超出范围', '建议改问']
    elif n == 0:
        advice, tags = '你可以改时间范围、指标或门店范围后再查一次。', ['空结果']
    else:
        advice, tags = '建议检查筛选条件，或缩小问题范围后重试。', ['查询异常', '待排查']
    if ('安全' in detail_text) or ('拦截' in detail_text):
        reply_text = _reply_paragraphs(
            '这个问题触发了安全限制，我先不直接返回结果。',
            '你可以换成门店培训、陪练、合规或风险相关的问题再试。',
        )
    elif qtype == 'unsupported':
        reply_text = _reply_paragraphs(
            '这个问题没有命中一句话查询当前支持的系统能力。',
            advice or '你可以改问培训进度、合规、陪练、风险、重点跟进或知识点相关的问题。',
        )
    else:
        reply_text = _build_natural_result_reply(qtext, rows, status, detail_text, advice)
    paragraphs = [paragraph for paragraph in reply_text.split("\n\n") if paragraph.strip()]
    summary = paragraphs[0] if paragraphs else ''
    if status == 'success' and n > 0:
        structured_sections = _build_structured_sections(qtext, rows, status, detail_text, advice)
    else:
        structured_sections = _reply_text_to_structured_sections(reply_text)
    return {
        'summary': _clean_query_reply_text(summary),
        'manager_advice': _clean_query_reply_text(advice),
        'focus_names': focus_names,
        'display_tags': tags,
        'structured_sections': structured_sections,
        'user_visible_output': reply_text,
        'reply_text': reply_text,
        'key_findings': [reply_text, f'问题：{qtext[:40]}' if qtext else reply_text],
        'suggested_actions': [_clean_query_reply_text(advice)],
        'reply_mode': reply_mode,
    }


def _query_fallback_reply_text(query_text: str, core: dict[str, Any]) -> str:
    route_type = _t(core.get('route_type')).lower()
    scope_status = _t(core.get('scope_status')).lower()
    candidate = _pick_query_reply_text(
        user_visible_output=core.get('summary_text'),
        summary_text=core.get('raw_output'),
        fallback_text=core.get('problem_note'),
    )
    if candidate and 'incorrect model credentials' not in candidate.lower():
        return _reply_paragraphs(candidate)
    if route_type == 'blocked' or scope_status == 'blocked':
        return _reply_paragraphs(
            '这个问题触发了安全限制，我先不直接返回结果。',
            '你可以换成门店培训、陪练、合规或风险相关的问题再试。',
        )
    if _t(core.get('query_type')) == 'unsupported':
        return _reply_paragraphs(
            '这个问题没有命中系统里的结构化查询，我先不给你不可靠的数据结论。',
            '如果你要查系统数据，建议改成“谁、哪家店、近多少天、什么指标”的问法。',
        )
    if query_text:
        return _reply_paragraphs(
            '当前没有命中系统可直接查询的数据口径，我先不拼接结果。',
            f'你可以把“{query_text[:24]}”改成更具体的对象、时间范围和指标后再查。',
        )
    return _reply_paragraphs(
        '当前没有命中系统可直接查询的数据口径。',
        '建议换成更具体的对象、时间范围和指标后再试。',
    )


def _has_store_dimension(sql: str) -> bool:
    upper = sql.upper()
    tables = re.findall(r"\bFROM\s+(\w+)", upper)
    tables += re.findall(r"\bJOIN\s+(\w+)", upper)
    if not tables:
        return False
    try:
        from sql_generator import get_table_schema_hints

        schema_hints = get_table_schema_hints()
        for tbl in tables:
            info = schema_hints.get(tbl.lower())
            if isinstance(info, dict) and bool(info.get('has_store_id')):
                return True
    except Exception:
        return False
    return False


def _is_store_scoped_sql(sql: str, store_id: str, params: list[str] | None = None) -> bool:
    if not _has_store_dimension(sql):
        return False

    sid = _t(store_id)
    if not sid:
        return False

    # Case 1: literal equality, e.g. store_id = 'STORE01'
    escaped_sid = re.escape(sid)
    if re.search(rf"\bstore_id\b\s*=\s*['\"]{escaped_sid}['\"]", sql, re.IGNORECASE):
        return True

    # Case 2: positional placeholder equality, e.g. store_id = ?
    eq_placeholder_matches = list(re.finditer(r"\bstore_id\b\s*=\s*\?", sql, re.IGNORECASE))
    if not eq_placeholder_matches:
        return False

    bound_params = [_t(p) for p in (params or [])]
    for m in eq_placeholder_matches:
        # SQLite positional params bind in left-to-right '?' order.
        param_index = sql[:m.end()].count("?") - 1
        if 0 <= param_index < len(bound_params) and bound_params[param_index] == sid:
            return True

    return False



def _should_try_generic_fallback(core: dict[str, Any]) -> bool:
    route_type = _t(core.get('route_type')).lower()
    scope_status = _t(core.get('scope_status')).lower()
    if route_type == 'blocked' or scope_status == 'blocked':
        return False
    qtype = _t(core.get('query_type'))
    can_query = _i(core.get('can_query'), 0)
    if can_query == 1 and qtype in SUPPORTED_QUERY_TYPES and qtype != 'unsupported':
        return False
    return True


def _execute_generic_sql(sql: str, store_id: str, params: list[str] | None = None, *, allow_global_scope: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Validate and execute a DIFY-generated or locally-generated SQL safely.

    Returns (rows, error_message). rows is empty list on failure.
    """
    from sql_safety import ensure_limit, sanitize_rows, validate_sql

    ok, result = validate_sql(sql)
    if not ok:
        _log.warning("generic_sql validation failed: %s sql=%s", result, sql[:200])
        return [], result

    safe_sql = ensure_limit(result)

    if not allow_global_scope and not _is_store_scoped_sql(safe_sql, store_id, params):
        _log.warning("generic_sql rejected by RBAC: store scope missing sql=%s", safe_sql[:200])
        return [], '店长仅可查询本店数据，请缩小查询范围'

    try:
        with get_readonly_conn() as conn:
            cursor = conn.execute(safe_sql, params or [])
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            raw_rows = cursor.fetchall()
            rows = [dict(zip(columns, row)) for row in raw_rows]
    except Exception as e:
        _log.warning("generic_sql execution failed: %s sql=%s", e, safe_sql[:200])
        return [], _t(e)

    rows = sanitize_rows(rows)

    # Filter out fake auto-generated stores (STORE_RISK_*, STORE_LINK_*, etc.)
    rows = [r for r in rows if _is_real_store(_t(r.get('store_id', ''))) or 'store_id' not in r]

    # Python-side store_id filter as backup for tables with store_id
    if not allow_global_scope and store_id and any("store_id" in (r.keys() if isinstance(r, dict) else []) for r in rows[:1]):
        rows = [r for r in rows if str(r.get("store_id", "")) == store_id or "store_id" not in r]

    # Enrich rows with store_name and employee_name
    rows = _enrich_rows_with_names(rows)

    _log.info("generic_sql success rows=%d sql=%s", len(rows), safe_sql[:200])
    return rows, ""


def _is_unified_blocked_query(query_text: str) -> bool:
    q = _t(query_text)
    if not q:
        return False
    low = q.lower()
    blocked_tokens = (
        '忽略规则', '绕过限制', '直接执行', 'drop ', 'delete ', 'insert ', 'update ',
        '删除用户', '删除数据', '清空数据', '数据库账号', '身份证', '手机号导出',
        '导出手机号', 'hashed_password', 'api_key', 'secret',
    )
    if any(token in low for token in blocked_tokens):
        return True
    return bool(re.search(r'\b(drop|delete|insert|update|alter|attach|pragma)\b', low))


def _is_query_catalog_question(query_text: str) -> bool:
    q = re.sub(r'\s+', '', _t(query_text))
    if not q:
        return False
    return ('系统' in q or '数据' in q) and any(
        token in q
        for token in ('有哪些数据', '所有数据', '全部数据', '数据目录', '能查什么', '可以查什么')
    )


def _has_data_query_intent(query_text: str) -> bool:
    q = _t(query_text)
    if _is_query_catalog_question(q):
        return True
    return any(token in q for token in (
        '员工', '门店', '店长', '培训', '训练', '陪练', '考核', '考试', '业绩', '销售',
        '看板', '记录', '日志', '排名', '完成率', '风险', '得分', '分数', '多少',
        '几个', '哪些', '名单', '查询历史', '知识库文档', '理论学习文档',
    ))


def _has_knowledge_query_intent(query_text: str) -> bool:
    q = _t(query_text)
    compact = re.sub(r'\s+', '', q).lower()
    if not compact:
        return False
    if '知识库文档' in q and any(token in q for token in ('哪些', '多少', '列表', '状态', '上传')):
        return False
    return any(token in compact for token in (
        '怎么解释', '怎么介绍', '怎么回答', '话术', '顾客', '客户', '钻石', '4c',
        '材质', '保养', '售后', '产品知识', '合规边界', '知识库里',
    ))


def _query_dataset_ids() -> list[str]:
    raw = _t(getattr(app_config, 'KB_DATASET_IDS_QUERY', '')) or _t(getattr(app_config, 'KB_DATASET_IDS_QA', ''))
    return [item.strip() for item in raw.split(',') if item.strip()]


def _answer_from_catalog(rows: list[dict[str, Any]]) -> str:
    names = [_t(row.get('table_name')) for row in rows if isinstance(row, dict) and _t(row.get('table_name'))]
    if not names:
        return '当前系统暂时没有可展示的数据目录。'
    sample = '、'.join(names[:8])
    tail = f'等 {len(names)} 类数据' if len(names) > 8 else f'{len(names)} 类数据'
    return f'当前系统可查 {tail}，包括 {sample}。我会按你的角色权限自动收窄范围，管理员看全系统，店长只看本店相关数据。'


def _answer_payload_from_data(query_text: str, rows: list[dict[str, Any]], status_text: str, detail: str = '') -> dict[str, Any]:
    if rows and isinstance(rows[0], dict) and 'table_name' in rows[0]:
        answer_text = _answer_from_catalog(rows)
        payload = _build_text_reply_payload(
            query_text=query_text,
            reply_text=answer_text,
            reply_mode='system_hit',
            display_tags=['已出结果'],
        )
        payload['reply_text'] = answer_text
        payload['user_visible_output'] = answer_text
        payload['summary'] = _clean_query_reply_text(answer_text)
        return payload
    return _fallback_summary('generic_sql', query_text, rows, status_text, detail)


def _should_try_local_sql_fastpath(query_text: str) -> bool:
    return _has_data_query_intent(query_text) and not _has_knowledge_query_intent(query_text)


def _is_allowed_fastpath_explanation(explanation: str) -> bool:
    """Check if a local SQL fastpath explanation is allowed to execute."""
    if explanation in _LOCAL_SQL_FASTPATH_EXPLANATIONS:
        return True
    # General generate_sql path: "查询 <description> (<table>)"
    if explanation.startswith("查询 ") and "(" in explanation:
        return True
    return False


def _run_local_sql_fastpath(
    query_text: str,
    store_id: str,
    *,
    allow_global_scope: bool,
) -> tuple[list[dict[str, Any]], str, str, list[str], str]:
    from sql_generator import generate_sql

    sql, params, explanation = generate_sql(
        query_text,
        store_id,
        allow_global_scope=allow_global_scope,
    )
    if not sql:
        return [], _t(explanation) or '未能生成有效 SQL', '', [], ''
    if not _is_allowed_fastpath_explanation(_t(explanation)):
        return [], _t(explanation) or '未命中本地兜底白名单', '', [], ''
    rows, error_message = _execute_generic_sql(
        sql,
        store_id,
        params,
        allow_global_scope=allow_global_scope,
    )
    return rows, error_message, sql, params, explanation


_LOCAL_SQL_FASTPATH_EXPLANATIONS = {
    '系统数据目录',
    '按门店统计培训完成率（来自训练周期任务数据）',
    '门店和员工总数',
    '员工数量与明细',
    '查询 系统用户（用户名、显示名、角色/职位、所属门店） (users)',
    '门店店长',
    '门店员工数',
    '门店角色成员',
    '门店员工名单',
    '人员岗位',
    '员工门店归属',
    '角色成员',
    '角色列表',
}


def _can_use_query2_workflow(query_type: str, *, include_generic_sql: bool = False) -> bool:
    qtype = _t(query_type)
    return qtype in SUPPORTED_QUERY_TYPES and (include_generic_sql or qtype != 'generic_sql')


def _workflow_reply_payload(
    *,
    user_id: str,
    store_id: str,
    query_text: str,
    query_type: str,
    params_json: dict[str, Any],
    query_status: str,
    result_rows: list[dict[str, Any]],
    error_message: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not _can_use_query2_workflow(query_type, include_generic_sql=True):
        return None, {
            'ok': False,
            'reason': 'unsupported_query_type',
            'error': _t(query_type) or 'unsupported',
            'raw': {},
        }
    # Defense-in-depth: sanitize again even if callers already did
    safe_rows = _sanitize_result_rows_for_response(result_rows)
    call = run_query2_workflow(
        user_id=user_id or 'query-user',
        store_id=store_id,
        query_type=query_type or 'generic_sql',
        user_query=query_text,
        params_json=params_json or {},
        query_status=query_status,
        result_count=len(safe_rows or []),
        result_json=safe_rows or [],
        error_message=error_message,
    )
    if not (isinstance(call, dict) and call.get('ok')):
        return None, call if isinstance(call, dict) else {
            'ok': False,
            'reason': 'dify_exception',
            'error': 'invalid_query2_call',
            'raw': {},
        }
    wf = call.get('data') if isinstance(call.get('data'), dict) else {}
    workflow_status = _t(wf.get('workflow_status')).lower()
    reply_text = _pick_query_reply_text(
        user_visible_output=wf.get('user_visible_output'),
        summary_text=wf.get('summary'),
    )
    if not _is_valid_query2_reply_text(reply_text, workflow_status=workflow_status):
        return None, {
            'ok': False,
            'reason': 'empty_workflow_output',
            'error': 'summary_empty',
            'raw': call.get('raw') if isinstance(call, dict) else {},
        }
    payload = _build_text_reply_payload(
        query_text=query_text,
        reply_text=reply_text,
        reply_mode='system_hit',
        display_tags=list(wf.get('display_tags') or []),
        manager_advice=_t(wf.get('manager_advice')),
    )
    payload['focus_names'] = list(wf.get('focus_names') or [])
    payload['display_tags'] = list(wf.get('display_tags') or [])
    payload['summary'] = _clean_query_reply_text(_t(wf.get('summary')) or payload.get('summary'))
    payload['render_source'] = 'query2'
    return payload, None


_FOLLOW_UP_MAP: dict[str, list[str]] = {
    'training_unfinished_newcomer': [
        '查看这些员工的陪练成绩趋势',
        '按门店分布查看培训完成率',
    ],
    'training_incomplete_staff': [
        '查看这些员工的陪练成绩趋势',
        '按门店分布查看培训完成率',
    ],
    'assessment_incomplete_staff': [
        '查看这些员工的考核通过率',
        '哪些员工需要补考',
    ],
    'low_compliance_staff': [
        '查看合规分的门店对比',
        '最近高风险员工有哪些',
    ],
    'practice_decline_staff': [
        '查看他们的薄弱维度分析',
        '需要重点跟进的员工',
    ],
    'recent_high_risk_staff': [
        '需要重点跟进的员工',
        '查看他们的薄弱维度分析',
    ],
    'followup_priority_staff': [
        '查看这些员工最近的陪练成绩',
        '查看他们的薄弱维度分析',
    ],
    'high_freq_knowledge_tag_staff': [
        '哪些知识点是全系统的薄弱环节',
        '按门店分布查看知识掌握情况',
    ],
}

_EMPTY_FOLLOW_UPS = [
    '换个时间范围试试',
    '换个更具体的问法',
]


def _generate_follow_up_questions(
    query_type: str,
    query_text: str,
    result_rows: list[dict[str, Any]],
    query_status: str,
    *,
    allow_global_scope: bool = False,
) -> list[str]:
    """Rule-based follow-up question suggestions (no extra LLM call)."""
    qt = _t(query_type)
    qs = _t(query_status).lower()

    if qs in ('error', 'blocked'):
        return ['换个更具体的问法再试一次']

    if qs == 'empty' or not result_rows:
        suggestions = list(_EMPTY_FOLLOW_UPS)
        if allow_global_scope:
            suggestions.append('查看全部门店的数据')
        return suggestions[:3]

    # Known query type
    if qt in _FOLLOW_UP_MAP:
        return list(_FOLLOW_UP_MAP[qt][:3])

    # generic_sql with employee rows — offer drill-down
    if qt == 'generic_sql':
        suggestions: list[str] = []
        has_names = any(_t(r.get('employee_name')) for r in result_rows[:5])
        has_stores = any(_t(r.get('store_name')) for r in result_rows[:5])
        if has_names:
            suggestions.append('按门店分组查看')
        if has_stores:
            suggestions.append('查看这些员工的培训记录')
        if not suggestions:
            suggestions.append('换个维度查看')
        suggestions.append('查看更长时间范围的趋势')
        return suggestions[:3]

    return ['换个维度查看', '查看更长时间范围的趋势']



    fb = _answer_payload_from_data(query_text, rows, status_text, detail)
    text = _t(fb.get('reply_text')) or _t(fb.get('summary')) or ''
    return text or ('没有找到匹配的数据。' if status_text == 'empty' else '这次数据查询没有得到稳定结果。')


def _sql_references_tables(sql: str, tables: set[str]) -> bool:
    refs = re.findall(r'\bFROM\s+(\w+)', sql, re.IGNORECASE)
    refs += re.findall(r'\bJOIN\s+(\w+)', sql, re.IGNORECASE)
    return bool({_t(ref).lower() for ref in refs} & {table.lower() for table in tables})


def _is_sql_security_error(detail: str) -> bool:
    low = _t(detail).lower()
    return any(token in low for token in (
        'only select',
        'blocked keyword',
        'semicolons',
        'system table',
        'not in whitelist',
        'subqueries',
        'blocked column',
        '店长仅可查询本店',
    ))


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = _t(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _workflow_sql_result(call: dict[str, Any]) -> tuple[str, list[Any], dict[str, Any]]:
    data = call.get('data') if isinstance(call.get('data'), dict) else {}
    payload = data.get('fastapi_payload_json') if isinstance(data.get('fastapi_payload_json'), dict) else {}
    sql = _t(data.get('sql_query') or data.get('sql') or payload.get('sql') or payload.get('sql_query'))
    params = _json_list(data.get('sql_params_json') or data.get('sql_params') or payload.get('params') or payload.get('sql_params_json'))
    return sql, params, data


def _is_executable_template_hit(route_type: str, query_type: str, sql_raw: str = '') -> bool:
    if _t(route_type).lower() != 'template_hit':
        return False
    qtype = _t(query_type)
    if qtype not in SUPPORTED_QUERY_TYPES:
        return False
    if qtype == 'generic_sql':
        return bool(_t(sql_raw))
    return True


def _public_unsupported_answer(query_text: str) -> str:
    q = _t(query_text)
    if q in {'你好', '您好', 'hi', 'hello', '嗨'}:
        return '你好，我可以帮你查询系统里的门店、员工、培训、考核、陪练、业绩等业务数据。你可以直接问“有几个店铺、有几个员工”这类问题。'
    return '这个问题暂时没有生成可执行的数据查询。你可以换成更具体的业务数据问题，比如门店数量、员工数量、培训完成情况、考核结果或销售业绩。'


def _current_store_id(conn, current_user: dict[str, Any]) -> str:
    employee_id = str(current_user.get('user_id') or '')
    return _store_id(conn, employee_id) or _t(current_user.get('store_id'))


def _local_template_answer_payload(query_text: str, local_result: dict[str, Any]) -> dict[str, Any]:
    payload = _build_text_reply_payload(
        query_text=query_text,
        reply_text=_t(local_result.get('reply_text')),
        reply_mode='local_template',
        display_tags=list(local_result.get('display_tags') or ['本地固定查询']),
    )
    payload['focus_names'] = list(local_result.get('focus_names') or [])
    payload['display_tags'] = list(local_result.get('display_tags') or ['本地固定查询'])
    payload['summary'] = _clean_query_reply_text(_t(local_result.get('summary')) or payload.get('summary'))
    payload['render_source'] = 'local_template'
    return payload


def _local_template_result_count(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    if len(rows) == 1:
        return _total_count_from_rows(rows)
    return len(rows)


def _insert_query_record(
    *,
    record_id: str,
    stage: str,
    employee_id: str,
    query_text: str,
    parsed_intent: str,
    payload: dict[str, Any],
) -> int:
    try:
        with get_conn() as conn:
            row = conn.execute(
                """
                INSERT INTO query_records (
                    record_id, stage, employee_id, query_text, parsed_intent, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    stage,
                    employee_id,
                    query_text,
                    parsed_intent,
                    json_text(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(row.lastrowid or 0)
    except Exception as exc:
        _log.debug("query local_template record insert failed: %s", exc)
    return 0


@router.post('/ask')
def query_ask(
    body: QueryAskRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    role, allow_global_scope = _query_scope_from_user(current_user)
    scope_mode = _query_scope_mode(role)
    raw_query_text = _t(body.query_text)
    if not raw_query_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='请输入查询')

    conversation_id = _t(body.conversation_id)
    ask_id = make_request_id('qaq')
    employee_id = str(current_user.get('user_id') or '')
    rewritten_query, enriched_query = _query_history_prompt(body.history or [], raw_query_text)
    query_text = rewritten_query or raw_query_text

    with get_conn() as conn:
        store_id = _current_store_id(conn, current_user)
    scope_token = _query_scope_token(scope_mode, store_id, employee_id)

    base_data = {
        'ask_id': ask_id,
        'employee_id': employee_id,
        'query_text': raw_query_text,
        'normalized_query': query_text,
        'result_rows': [],
        'citations': [],
        'sources': [],
        'scope': scope_token,
        'manager_role': role,
        'confidence_level': 'medium',
        'conversation_id': conversation_id,
    }

    if _is_unified_blocked_query(enriched_query):
        data = {
            **base_data,
            'answer_text': '这个请求涉及敏感操作或不安全指令，不能执行。我只能做权限内的只读查询。',
            'route_type': 'blocked',
            'query_status': 'error',
            'problem_note': '命中安全拦截',
        }
        return success_response(data, workflow_code='query_ask', mock=True)

    local_result: dict[str, Any] | None = None
    try:
        with get_conn() as conn:
            local_result = try_local_query_template(
                conn,
                query_text,
                store_id=store_id,
                allow_global_scope=allow_global_scope,
            )
    except Exception as exc:
        _log.warning("query_ask local_template failed, falling back to dify: %s", exc)
        local_result = None

    if local_result:
        result_rows = _sanitize_result_rows_for_response(
            local_result.get('result_rows') if isinstance(local_result.get('result_rows'), list) else []
        )
        query_status = _t(local_result.get('query_status')) or ('success' if result_rows else 'empty')
        if local_result.get('skip_query2'):
            answer_payload = _local_template_answer_payload(query_text, local_result)
            workflow_code = 'query_local_template'
            is_mock = True
        else:
            query2_payload, query2_error = _workflow_reply_payload(
                user_id=employee_id,
                store_id=store_id,
                query_text=query_text,
                query_type=_t(local_result.get('query_type')),
                params_json=local_result.get('params_json') if isinstance(local_result.get('params_json'), dict) else {},
                query_status=query_status,
                result_rows=result_rows,
                error_message='',
            )
            if query2_payload:
                answer_payload = query2_payload
                workflow_code = 'query2'
                is_mock = False
            else:
                _log.warning(
                    "query_ask local_template query2 failed, fallback to local reply user_id=%s reason=%s",
                    employee_id,
                    query2_error.get('reason') if isinstance(query2_error, dict) else 'query2_error',
                )
                answer_payload = _local_template_answer_payload(query_text, local_result)
                workflow_code = 'query_local_template'
                is_mock = True
        answer_text = _t(answer_payload.get('reply_text')) or _t(answer_payload.get('summary'))
        data = {
            **base_data,
            'answer_text': answer_text,
            'reply_text': answer_text,
            'summary_text': _t(answer_payload.get('summary')) or answer_text,
            'user_visible_output': _t(answer_payload.get('user_visible_output')) or answer_text,
            'structured_sections': answer_payload.get('structured_sections') if isinstance(answer_payload.get('structured_sections'), list) else [],
            'focus_names': answer_payload.get('focus_names') if isinstance(answer_payload.get('focus_names'), list) else [],
            'display_tags': answer_payload.get('display_tags') if isinstance(answer_payload.get('display_tags'), list) else [],
            'reply_mode': _t(answer_payload.get('reply_mode')) or ('local_template' if local_result.get('skip_query2') else 'system_hit'),
            'route_type': 'local_template',
            'query_status': query_status,
            'result_count': _local_template_result_count(result_rows),
            'result_rows': result_rows,
            'citations': [],
            'sources': [],
            'store_id': store_id,
            'problem_note': '',
            'template_id': _t(local_result.get('template_id')),
            'intent': _t(local_result.get('query_type')),
            'params_json': local_result.get('params_json') if isinstance(local_result.get('params_json'), dict) else {},
            'follow_up_questions': _generate_follow_up_questions(
                query_type=_t(local_result.get('query_type')),
                query_text=query_text,
                result_rows=result_rows,
                query_status=query_status,
                allow_global_scope=allow_global_scope,
            ),
        }
        _insert_query_record(
            record_id=ask_id,
            stage='ask',
            employee_id=employee_id,
            query_text=raw_query_text,
            parsed_intent='local_template',
            payload=data,
        )
        return success_response(data, workflow_code=workflow_code, mock=is_mock)

    result_rows: list[dict[str, Any]] = []
    data_error = ''
    sql_used = ''
    route_type = 'unsupported'
    query_status = 'error'
    sql_raw = ''
    sql_params: list[Any] = []
    wf_data: dict[str, Any] = {}
    query1_ok = False

    call = run_query1_workflow(
        user_id=employee_id or 'query-user',
        store_id=store_id,
        query_text=enriched_query,
        manager_role=role,
        time_anchor=datetime.now(timezone.utc).date().isoformat(),
        data_catalog_summary=query_catalog_prompt_summary(limit=80),
        knowledge_enabled=False,
    )

    if isinstance(call, dict) and call.get('ok'):
        query1_ok = True
        sql_raw, sql_params, wf_data = _workflow_sql_result(call)
        normalized_qtype, normalized_params = _normalize_supported_query_type(
            query_text,
            _t(wf_data.get('query_type')),
            wf_data.get('params_json') if isinstance(wf_data.get('params_json'), dict) else {},
        )
        if normalized_qtype != _t(wf_data.get('query_type')):
            wf_data['query_type'] = normalized_qtype
            wf_data['params_json'] = normalized_params
            sql_raw = ''
            sql_params = []
        base_data['normalized_query'] = _t(wf_data.get('rewritten_query')) or query_text
        base_data['confidence_level'] = _t(wf_data.get('confidence_level')) or 'medium'
        dify_route = _t(wf_data.get('route_type')).lower()
        dify_qtype = _t(wf_data.get('query_type'))
        dify_can_query = _i(wf_data.get('can_query'), 0)
        is_template_hit = _is_executable_template_hit(dify_route, dify_qtype, sql_raw)
        if dify_can_query != 1 and not is_template_hit:
            route_type = 'unsupported'
            query_status = 'error'
            data_error = _t(wf_data.get('problem_note')) or '查询工作流没有生成可执行 SQL'
        elif not sql_raw and not is_template_hit:
            route_type = 'unsupported'
            query_status = 'error'
            data_error = '查询工作流没有生成可执行 SQL'
        else:
            route_type = 'template_hit'
            query_status = 'success'
    else:
        data_error = _t(call.get('reason') if isinstance(call, dict) else '') or 'query1_failed'

    template_qtype = ''
    if sql_raw:
        sql_used = sql_raw
        if not allow_global_scope and _sql_references_tables(sql_raw, STORE_MANAGER_BLOCKED_TABLES):
            result_rows, data_error = [], '店长仅可查询本店业务数据，不能查看系统审计日志'
        else:
            result_rows, data_error = _execute_generic_sql(
                sql_raw,
                store_id,
                [str(p) for p in sql_params],
                allow_global_scope=allow_global_scope,
            )
        if data_error and _is_sql_security_error(data_error):
            route_type = 'blocked'
            query_status = 'error'
        elif data_error:
            route_type = 'unsupported'
            query_status = 'error'
        else:
            route_type = 'data_sql'
            query_status = 'success' if result_rows else 'empty'
    elif route_type != 'unsupported' and _t(wf_data.get('query_type')) in SUPPORTED_QUERY_TYPES:
        template_qtype = _t(wf_data.get('query_type'))
        params = wf_data.get('params_json') if isinstance(wf_data.get('params_json'), dict) else {}
        try:
            with get_conn() as conn:
                result_rows = _query_rows(
                    conn,
                    store_id=store_id,
                    qtype=template_qtype,
                    params=params,
                    allow_global_scope=allow_global_scope,
                )
            route_type = 'data_sql'
            query_status = 'success' if result_rows else 'empty'
        except Exception as e:
            _log.warning("query_ask template_hit _query_rows failed: %s", e)
            result_rows = []
            route_type = 'unsupported'
            query_status = 'error'
            data_error = '模板查询执行失败'

    fallback_query_text = _t(base_data.get('normalized_query')) or query_text or raw_query_text
    if route_type == 'unsupported' and _should_try_local_sql_fastpath(fallback_query_text):
        fallback_rows, fallback_error, fallback_sql, fallback_params, fallback_explanation = _run_local_sql_fastpath(
            fallback_query_text,
            store_id,
            allow_global_scope=allow_global_scope,
        )
        if fallback_sql:
            sql_used = fallback_sql
            sql_params = list(fallback_params or [])
            result_rows = _sanitize_result_rows_for_response(fallback_rows)
            data_error = _t(fallback_error)
            wf_data = {
                **wf_data,
                'query_type': 'generic_sql',
                'params_json': {},
                'rewritten_query': fallback_query_text,
                'confidence_level': _t(wf_data.get('confidence_level')) or 'medium',
                'route_type': 'local_sql_fallback',
                'problem_note': data_error,
                'local_sql_explanation': fallback_explanation,
            }
            if data_error and _is_sql_security_error(data_error):
                route_type = 'blocked'
                query_status = 'error'
            elif data_error:
                route_type = 'unsupported'
                query_status = 'error'
            else:
                route_type = 'local_sql_fallback'
                query_status = 'success' if result_rows else 'empty'

    result_rows = _sanitize_result_rows_for_response(result_rows)

    if route_type == 'blocked':
        answer_payload = _build_text_reply_payload(
            query_text=query_text,
            reply_text=data_error or '店长仅可查询本店数据，请缩小查询范围。',
            reply_mode='blocked',
            display_tags=['安全拦截'],
        )
        answer_payload['render_source'] = 'blocked'
    elif route_type in {'data_sql', 'local_sql_fallback'}:
        query2_payload, query2_error = _workflow_reply_payload(
            user_id=employee_id,
            store_id=store_id,
            query_text=query_text,
            query_type=_t(wf_data.get('query_type')) or template_qtype or 'generic_sql',
            params_json=wf_data.get('params_json') if isinstance(wf_data.get('params_json'), dict) else {},
            query_status=query_status,
            result_rows=result_rows,
            error_message=data_error,
        )
        if query2_payload:
            answer_payload = query2_payload
        else:
            _log.warning(
                "query_ask query2 failed, fallback to local summary user_id=%s reason=%s",
                employee_id,
                query2_error.get('reason') if isinstance(query2_error, dict) else 'query2_error',
            )
            answer_payload = _answer_payload_from_data(query_text, result_rows, query_status, data_error)
            answer_payload['render_source'] = 'local_sql_fallback' if route_type == 'local_sql_fallback' else 'local_fallback'
    else:
        dify_summary = ''
        if query1_ok:
            dify_summary = _clean_query_reply_text(
                _t(wf_data.get('summary_text')) or _t(wf_data.get('raw_output'))
            )
        if not dify_summary:
            return dify_failure_response(
                workflow_code='query1',
                route_path='/api/query/ask',
                call={'ok': False, 'reason': 'empty_workflow_output', 'error': 'summary_empty', 'raw': call.get('raw') if isinstance(call, dict) else {}},
            )
        answer_payload = _build_text_reply_payload(
            query_text=raw_query_text,
            reply_text=dify_summary,
            reply_mode='llm_fallback',
            display_tags=['LLM兜底'],
        )
        answer_payload['render_source'] = 'query1'
    render_source = _t(answer_payload.get('render_source')) or 'query1'
    _log.info(
        "query_ask render_source=%s route_type=%s query_status=%s reply_mode=%s",
        render_source,
        route_type,
        query_status,
        _t(answer_payload.get('reply_mode')) or '',
    )
    answer_text = _t(answer_payload.get('reply_text')) or _t(answer_payload.get('summary'))
    has_employee_rows = any(isinstance(row, dict) and _row_employee_name(row) for row in result_rows)
    result_count = (
        _total_count_from_rows(result_rows)
        if route_type in {'data_sql', 'local_sql_fallback'} and result_rows
        else len(result_rows)
    )

    data = {
        **base_data,
        'answer_text': answer_text,
        'reply_text': answer_text,
        'summary_text': _t(answer_payload.get('summary')) or answer_text,
        'user_visible_output': _t(answer_payload.get('user_visible_output')) or answer_text,
        'structured_sections': answer_payload.get('structured_sections') if isinstance(answer_payload.get('structured_sections'), list) else [],
        'focus_names': answer_payload.get('focus_names') if isinstance(answer_payload.get('focus_names'), list) else [],
        'display_tags': answer_payload.get('display_tags') if isinstance(answer_payload.get('display_tags'), list) else [],
        'reply_mode': _t(answer_payload.get('reply_mode')) or route_type,
        'route_type': route_type,
        'query_status': query_status,
        'result_count': result_count,
        'result_rows': result_rows,
        'citations': [],
        'sources': [],
        'store_id': store_id,
        'problem_note': data_error,
        'follow_up_questions': _generate_follow_up_questions(
            query_type=route_type,
            query_text=query_text,
            result_rows=result_rows,
            query_status=query_status,
            allow_global_scope=allow_global_scope,
        ),
    }

    try:
        with get_conn() as conn:
            row = conn.execute(
                """
                INSERT INTO query_records (
                    record_id, stage, employee_id, query_text, parsed_intent, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ask_id,
                    'ask',
                    employee_id,
                    raw_query_text,
                    route_type,
                    json_text(data),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception as exc:
        _log.debug("query_ask record insert failed: %s", exc)

    return success_response(data, workflow_code='query_ask', mock=False)


@router.post('/parse')
def query_parse(
    body: QueryParseRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    role, allow_global_scope = _query_scope_from_user(current_user)
    scope_mode = _query_scope_mode(role)
    query_text = (body.query_text or '').strip() or '本周各门店陪练完成率最低的三位员工是谁？'
    conversation_id = _t(body.conversation_id)
    employee_id = str(current_user.get('user_id') or '')
    parse_id = make_request_id('qp')

    history = body.history or []
    rewritten_followup_query, enriched_text = _query_history_prompt(history, query_text)
    context_summary = _build_query_context_summary(history)

    _log.info("query_parse start user_id=%s role=%s query_text=%s", employee_id, role, query_text[:80])

    with get_conn() as conn:
        store_id = _current_store_id(conn, current_user)
    allowed_employee_ids = _query_allowed_employee_ids(scope_mode, current_user)
    scope_token = _query_scope_token(scope_mode, store_id, employee_id)

    if _is_unified_blocked_query(enriched_text):
        data = {
            'parse_id': parse_id,
            'employee_id': employee_id,
            'query_text': query_text,
            'intent': 'unsupported',
            'metrics': [],
            'dimensions': [],
            'filters': {'time_range': 'unspecified', 'store_id': store_id, 'top_n': 20},
            'normalized_query': rewritten_followup_query or query_text,
            'context_summary': context_summary,
            'conversation_id': conversation_id,
            'scope_status': 'blocked',
            'route_type': 'blocked',
            'confidence_level': 'high',
            'can_query': 0,
            'problem_note': '命中安全拦截',
            'params_json': {},
            'fastapi_payload_json': {},
            'query_status': 'error',
            'result_count': 0,
            'result_rows': [],
            'fallback_reply_text': '这个请求涉及敏感操作或不安全指令，不能执行。我只能做权限内的只读查询。',
            'reply_mode': 'blocked',
            'store_id': store_id,
            'scope': scope_token,
            'manager_role': role,
        }
        _insert_query_record(
            record_id=parse_id,
            stage='parse',
            employee_id=employee_id,
            query_text=query_text,
            parsed_intent='blocked',
            payload=data,
        )
        return success_response(data, workflow_code='query_local_template', mock=True)

    local_result: dict[str, Any] | None = None
    try:
        with get_conn() as conn:
            local_result = try_local_query_template(
                conn,
                rewritten_followup_query or query_text,
                store_id=store_id,
                allow_global_scope=allow_global_scope,
            )
    except Exception as exc:
        _log.warning("query_parse local_template failed, falling back to dify: %s", exc)
        local_result = None

    if local_result:
        qtype = _t(local_result.get('query_type'))
        rows = _sanitize_result_rows_for_response(
            local_result.get('result_rows') if isinstance(local_result.get('result_rows'), list) else []
        )
        query_status = _t(local_result.get('query_status')) or ('success' if rows else 'empty')
        filters = {
            'time_range': 'unspecified',
            'store_id': store_id,
            'top_n': 100 if qtype in {'employee_list', 'role_list'} else 20,
        }
        data = {
            'parse_id': parse_id,
            'employee_id': employee_id,
            'query_text': query_text,
            'intent': qtype,
            'metrics': QUERY_METRICS.get(qtype, []),
            'dimensions': QUERY_DIMS.get(qtype, []),
            'filters': filters,
            'normalized_query': _t(local_result.get('rewritten_query')) or rewritten_followup_query or query_text,
            'context_summary': context_summary,
            'conversation_id': conversation_id,
            'scope_status': 'in_scope',
            'route_type': 'local_template',
            'confidence_level': 'high',
            'can_query': 1,
            'problem_note': '',
            'params_json': local_result.get('params_json') if isinstance(local_result.get('params_json'), dict) else {},
            'fastapi_payload_json': {},
            'query_status': query_status,
            'result_count': _local_template_result_count(rows),
            'result_rows': rows,
            'fallback_reply_text': _t(local_result.get('fallback_reply_text') or local_result.get('reply_text')),
            'reply_mode': 'local_fallback' if local_result.get('skip_query2') else 'system_hit',
            'store_id': store_id,
            'scope': scope_token,
            'manager_role': role,
            'template_id': _t(local_result.get('template_id')),
        }
        _insert_query_record(
            record_id=parse_id,
            stage='parse',
            employee_id=employee_id,
            query_text=query_text,
            parsed_intent=qtype,
            payload=data,
        )
        return success_response(data, workflow_code='query_local_template', mock=True)

    call = run_query1_workflow(
        user_id=employee_id or 'query-user',
        store_id=store_id,
        query_text=enriched_text,
        manager_role=role,
        time_anchor=datetime.now(timezone.utc).date().isoformat(),
        data_catalog_summary=query_catalog_prompt_summary(limit=80),
        knowledge_enabled=bool(_query_dataset_ids()),
    )
    if not (isinstance(call, dict) and call.get('ok')):
        _log.warning("query_parse dify failed reason=%s", call.get('reason') if isinstance(call, dict) else 'unknown')
        return dify_failure_response(
            workflow_code='query1',
            route_path='/api/query/parse',
            call=call if isinstance(call, dict) else None,
        )

    use_dify = True
    wf = call.get('data') if isinstance(call.get('data'), dict) else {}
    sql_raw, sql_params, _wf_data = _workflow_sql_result(call)
    raw_can_query = _i(wf.get('can_query'), 0)
    if _t(wf.get('query_type')) == 'generic_sql' and sql_raw:
        raw_can_query = 1
    core = {
        'query_type': _t(wf.get('query_type')) or 'unsupported',
        'params_json': wf.get('params_json') if isinstance(wf.get('params_json'), dict) else {},
        'rewritten_query': _t(wf.get('rewritten_query')) or query_text,
        'confidence_level': _t(wf.get('confidence_level')) or 'medium',
        'can_query': raw_can_query,
        'route_type': _t(wf.get('route_type')) or 'unsupported',
        'scope_status': 'in_scope' if raw_can_query == 1 else ('blocked' if _t(wf.get('route_type')) == 'blocked' else 'out_of_scope'),
        'problem_note': _t(wf.get('problem_note')),
        'summary_text': _t(wf.get('summary_text')),
        'raw_output': _t(wf.get('raw_output')),
        'sql_query': sql_raw,
        'sql_params': sql_params,
        'fastapi_payload_json': wf.get('fastapi_payload_json') if isinstance(wf.get('fastapi_payload_json'), dict) else {},
    }
    normalized_qtype, normalized_params = _normalize_supported_query_type(
        query_text,
        _t(core.get('query_type')),
        core.get('params_json') if isinstance(core.get('params_json'), dict) else {},
    )
    if normalized_qtype != _t(core.get('query_type')):
        core['query_type'] = normalized_qtype
        core['params_json'] = normalized_params
        core['sql_query'] = ''
        core['sql_params'] = []
        payload = core.get('fastapi_payload_json') if isinstance(core.get('fastapi_payload_json'), dict) else {}
        if isinstance(payload, dict):
            payload['query_type'] = normalized_qtype
            payload['query_params'] = normalized_params
            payload.pop('sql', None)
            payload.pop('sql_params_json', None)
            core['fastapi_payload_json'] = payload

    with get_conn() as conn:
        qtype = _t(core.get('query_type'))
        params = core.get('params_json') if isinstance(core.get('params_json'), dict) else {}
        can_query = _i(core.get('can_query'), 0)
        rows: list[dict[str, Any]] = []
        err = ''
        if can_query == 1 and qtype == 'generic_sql':
            # ── Generic SQL execution path ──
            sql_raw = _t(core.get('sql_query'))
            sql_params = core.get('sql_params') if isinstance(core.get('sql_params'), list) else None
            if sql_raw:
                rows, err = _execute_generic_sql(
                    sql_raw,
                    store_id,
                    sql_params,
                    allow_global_scope=allow_global_scope,
                )
                if err:
                    can_query = 0
            else:
                can_query = 0
                err = '未能生成有效SQL'
        elif can_query == 1 and qtype in SUPPORTED_QUERY_TYPES:
            try:
                rows = _query_rows(
                    conn,
                    store_id=store_id,
                    qtype=qtype,
                    params=params,
                    allow_global_scope=allow_global_scope,
                    allowed_employee_ids=allowed_employee_ids,
                )
            except Exception as e:
                rows = []
                err = _t(e)
                can_query = 0

        rows = _sanitize_result_rows_for_response(rows)

        fallback_reply_text = ''
        reply_mode = 'system_hit'
        if can_query != 1:
            fallback_reply_text = _clean_query_reply_text(_t(core.get('summary_text')) or _t(core.get('raw_output')))
            if _t(core.get('scope_status')).lower() == 'blocked' or _t(core.get('route_type')).lower() == 'blocked':
                reply_mode = 'blocked'
            elif fallback_reply_text:
                reply_mode = 'llm_fallback'

        if err:
            qstatus = 'error'
        elif can_query == 1 and rows:
            qstatus = 'success'
        elif can_query == 1:
            qstatus = 'empty'
        else:
            qstatus = 'error'

        filters = {
            'time_range': _t(params.get('time_range')) or 'unspecified',
            'store_id': store_id,
            'top_n': 20,
        }
        data = {
            'parse_id': parse_id,
            'employee_id': employee_id,
            'query_text': query_text,
            'intent': qtype if qtype in SUPPORTED_QUERY_TYPES else 'unsupported',
            'metrics': QUERY_METRICS.get(qtype, []),
            'dimensions': QUERY_DIMS.get(qtype, []),
            'filters': filters,
            'normalized_query': _t(core.get('rewritten_query')) or query_text,
            'context_summary': context_summary,
            'conversation_id': conversation_id,
            'scope_status': _t(core.get('scope_status')) or 'out_of_scope',
            'route_type': _t(core.get('route_type')) or 'unsupported',
            'confidence_level': _t(core.get('confidence_level')) or 'medium',
            'can_query': 1 if can_query == 1 else 0,
            'problem_note': _t(core.get('problem_note')) or err,
            'params_json': params,
            'fastapi_payload_json': core.get('fastapi_payload_json') if isinstance(core.get('fastapi_payload_json'), dict) else {},
            'query_status': qstatus,
            'result_count': len(rows),
            'result_rows': rows,
            'fallback_reply_text': fallback_reply_text,
            'reply_mode': reply_mode,
            'store_id': store_id,
            'scope': scope_token,
            'manager_role': role,
        }
        row = conn.execute(
            """
            INSERT INTO query_records (
                record_id, stage, employee_id, query_text, parsed_intent, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data['parse_id'],
                'parse',
                data['employee_id'],
                query_text,
                data['intent'],
                json_text(data),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    _log.info("query_parse success user_id=%s role=%s use_dify=%s", employee_id, role, use_dify)
    return success_response(
        data,
        workflow_code='query1' if use_dify else 'query1_mock',
        mock=not use_dify,
    )


@router.post('/summarize')
def query_summarize(
    body: QuerySummarizeRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
):
    role, allow_global_scope = _query_scope_from_user(current_user)
    scope_mode = _query_scope_mode(role)
    query_text = _t(body.query_text)
    parsed_intent = _t(body.parsed_intent) or 'unsupported'
    result_rows = _sanitize_result_rows_for_response(body.result_rows or [])
    params_json = body.params_json or {}
    query_status = _t(body.query_status)
    fallback_reply_text = _t(body.fallback_reply_text)
    requested_reply_mode = _t(body.reply_mode)
    conversation_id = _t(body.conversation_id)
    employee_id = str(current_user.get('user_id') or '')
    summary_id = make_request_id('qs')
    _log.info("query_summarize start user_id=%s role=%s parsed_intent=%s query_status=%s", employee_id, role, parsed_intent, query_status)

    with get_conn() as conn:
        current_store_id = _store_id(conn, employee_id)
    scope_token = _query_scope_token(scope_mode, current_store_id, employee_id)
    store_id = _summary_store_id(
        _t(body.store_id),
        current_store_id,
        result_rows,
        allow_global_scope=allow_global_scope,
    )

    if not query_status:
        query_status = 'success' if result_rows else 'empty'
    if query_status not in {'success', 'empty', 'error'}:
        query_status = 'success' if result_rows else 'empty'

    local_reply_modes = {'llm_fallback', 'local_fallback'}
    if requested_reply_mode in local_reply_modes and fallback_reply_text:
        use_dify = False
        if requested_reply_mode == 'llm_fallback':
            tags = ['LLM兜底']
        else:
            tags = ['自然回复兜底']
        text_payload = _build_text_reply_payload(
            query_text=query_text,
            reply_text=fallback_reply_text,
            reply_mode=requested_reply_mode,
            display_tags=tags,
        )
        summary_text = text_payload['summary']
        manager_advice = text_payload['manager_advice']
        focus_names = text_payload['focus_names']
        display_tags = text_payload['display_tags']
        structured_sections = text_payload['structured_sections']
        user_visible_output = text_payload['user_visible_output']
        reply_text = text_payload['reply_text']
        reply_mode = text_payload['reply_mode']
        call = None
    else:
        reply_mode = 'system_hit'

    sum_history = body.history or []
    _rewritten_summary_query, enriched_query = _query_history_prompt(sum_history, query_text)
    context_summary = _build_query_context_summary(sum_history)
    call = None
    if not (requested_reply_mode in local_reply_modes and fallback_reply_text):
        use_dify = _can_use_query2_workflow(parsed_intent, include_generic_sql=True)
        if use_dify:
            call = run_query2_workflow(
                user_id=employee_id or 'query-user',
                store_id=store_id,
                query_type=parsed_intent,
                user_query=enriched_query,
                params_json=params_json,
                query_status=query_status,
                result_count=len(result_rows),
                result_json=result_rows,
                error_message=_t(body.error_message),
            )
            use_dify = bool(call.get('ok'))
        else:
            call = {
                'ok': False,
                'reason': 'unsupported_query_type',
                'error': parsed_intent or 'unsupported',
                'raw': {},
            }
            use_dify = False

    if use_dify and isinstance(call, dict):
        wf = call.get('data') if isinstance(call.get('data'), dict) else {}
        workflow_status = _t(wf.get('workflow_status')).lower()
        summary_text = _clean_query_reply_text(wf.get('summary'))
        manager_advice = _clean_query_reply_text(wf.get('manager_advice'))
        focus_names = list(wf.get('focus_names') or [])
        display_tags = list(wf.get('display_tags') or [])
        user_visible_output = _pick_query_reply_text(
            user_visible_output=wf.get('user_visible_output'),
            summary_text=summary_text,
        )
        reply_text = user_visible_output
        if workflow_status and workflow_status not in {'success', 'empty', 'error'}:
            use_dify = False
            call = {
                'ok': False,
                'reason': f'workflow_status_{workflow_status}',
                'error': workflow_status,
                'raw': call.get('raw') if isinstance(call, dict) else {},
            }
        elif not _is_valid_query2_reply_text(reply_text, workflow_status=workflow_status):
            use_dify = False
            call = {
                'ok': False,
                'reason': 'empty_workflow_output',
                'error': 'summary_empty',
                'raw': call.get('raw') if isinstance(call, dict) else {},
            }
    local_template_can_fallback = bool(
        fallback_reply_text and parsed_intent in LOCAL_TEMPLATE_QUERY_TYPES
    )
    if not use_dify and not (requested_reply_mode in local_reply_modes and fallback_reply_text):
        if local_template_can_fallback:
            text_payload = _build_text_reply_payload(
                query_text=query_text,
                reply_text=fallback_reply_text,
                reply_mode='local_template',
                display_tags=['本地固定查询'],
            )
            summary_text = text_payload['summary']
            manager_advice = text_payload['manager_advice']
            focus_names = text_payload['focus_names']
            display_tags = text_payload['display_tags']
            structured_sections = text_payload['structured_sections']
            user_visible_output = text_payload['user_visible_output']
            reply_text = text_payload['reply_text']
            reply_mode = text_payload['reply_mode']
        elif isinstance(call, dict) and call.get('reason') == 'unsupported_query_type':
            text_payload = _fallback_summary(
                'unsupported',
                query_text,
                result_rows,
                query_status or 'error',
                _t(body.error_message) or _t(call.get('error')),
            )
            summary_text = text_payload['summary']
            manager_advice = text_payload['manager_advice']
            focus_names = text_payload['focus_names']
            display_tags = text_payload['display_tags']
            structured_sections = text_payload['structured_sections']
            user_visible_output = text_payload['user_visible_output']
            reply_text = text_payload['reply_text']
            reply_mode = text_payload['reply_mode']
        else:
            _log.warning(
                "query_summarize dify failed user_id=%s reason=%s",
                employee_id,
                call.get('reason') if isinstance(call, dict) else 'dify_exception',
            )
            return dify_failure_response(
                workflow_code='query2',
                route_path='/api/query/summarize',
                call=call if isinstance(call, dict) else None,
            )
    else:
        if requested_reply_mode in local_reply_modes and fallback_reply_text:
            pass
        else:
            reply_text = _pick_query_reply_text(
                user_visible_output=user_visible_output,
                summary_text=summary_text,
            )
            structured_sections = _reply_text_to_structured_sections(reply_text)
            if not reply_text:
                structured_sections = _build_structured_sections(
                    query_text,
                    result_rows,
                    query_status,
                    _t(body.error_message),
                    manager_advice,
                )
                reply_text = _structured_sections_to_text(structured_sections)
                user_visible_output = reply_text
                summary_text = _clean_query_reply_text(reply_text.split("\n\n", 1)[0] if reply_text else '')

    if reply_text and not user_visible_output:
        user_visible_output = reply_text
    if reply_text and not structured_sections:
        structured_sections = _reply_text_to_structured_sections(reply_text)
    if reply_text and not summary_text:
        summary_text = _clean_query_reply_text(reply_text.split("\n\n", 1)[0])
    if reply_text and query_status == 'success' and not focus_names:
        focus_names = _focus_names_from_rows(result_rows)

    with get_conn() as conn:
        key_findings = [reply_text or summary_text]
        if focus_names:
            key_findings.append(f"重点人员：{'、'.join(focus_names[:3])}")
        suggested_actions = [manager_advice] if manager_advice else []

        data = {
            'summary_id': summary_id,
            'employee_id': employee_id,
            'query_text': query_text,
            'parsed_intent': parsed_intent,
            'summary_text': summary_text,
            'reply_text': reply_text,
            'structured_sections': structured_sections,
            'context_summary': context_summary,
            'key_findings': key_findings,
            'suggested_actions': suggested_actions,
            'focus_names': focus_names,
            'display_tags': display_tags,
            'user_visible_output': user_visible_output,
            'query_status': query_status,
            'result_count': len(result_rows),
            'reply_mode': reply_mode,
            'store_id': store_id,
            'scope': scope_token,
            'manager_role': role,
            'conversation_id': conversation_id,
        }
        row = conn.execute(
            """
            INSERT INTO query_records (
                record_id, stage, employee_id, query_text, parsed_intent, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data['summary_id'],
                'summarize',
                data['employee_id'],
                data['query_text'],
                data['parsed_intent'],
                json_text({**data, 'result_rows': result_rows, 'params_json': params_json}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    _log.info("query_summarize success user_id=%s role=%s use_dify=%s", employee_id, role, use_dify)
    return success_response(
        data,
        workflow_code='query2' if use_dify else 'query2_mock',
        mock=not use_dify,
    )
