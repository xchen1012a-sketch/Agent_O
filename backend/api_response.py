from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse

from database import utc_now_iso

_log = logging.getLogger("jewelry_qipei.api_response")


def now_iso() -> str:
    return utc_now_iso()


def make_request_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def success_response(
    data: dict[str, Any],
    *,
    workflow_code: str,
    message: str = 'success',
    mock: bool = True,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {
        'mock': mock,
        'source': 'mock' if mock else 'dify',
        'workflow_code': workflow_code,
        'generated_at': now_iso(),
    }
    if extra_meta:
        meta.update(extra_meta)
    return {
        'code': 200,
        'message': message,
        'data': data,
        'meta': meta,
    }


def error_response(
    *,
    workflow_code: str,
    message: str,
    data: dict[str, Any],
    http_status: int = 502,
    mock: bool = False,
    extra_meta: dict[str, Any] | None = None,
) -> JSONResponse:
    meta = {
        'mock': mock,
        'source': 'mock' if mock else 'dify',
        'workflow_code': workflow_code,
        'generated_at': now_iso(),
    }
    if extra_meta:
        meta.update(extra_meta)
    return JSONResponse(
        status_code=http_status,
        content={
            'code': http_status,
            'message': message,
            'data': data,
            'meta': meta,
        },
    )


def _summarize_raw_failure(raw: Any) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return '', ''
    detail = ''
    error_code = ''
    if isinstance(raw.get('data'), dict):
        detail = str(raw['data'].get('detail') or raw['data'].get('content') or '').strip()
        error_code = str(raw['data'].get('http_status') or raw['data'].get('raw_upstream_code') or '').strip()
    if not detail:
        detail = str(raw.get('message') or '').strip()
    return detail[:1000], error_code[:64]


_FAILURE_LAYER_MAP: dict[str, str] = {
    'force_mock': 'mock_layer',
    'missing_api_key': 'config_layer',
    'missing_api_base': 'config_layer',
    'invalid_response': 'dify_response_layer',
    'empty_answer': 'dify_response_layer',
    'empty_workflow_output': 'dify_response_layer',
    'dify_exception': 'dify_client_layer',
    'dify_non_200': 'dify_response_layer',
}


def _classify_failure_layer(reason: str) -> str:
    return _FAILURE_LAYER_MAP.get((reason or '').strip(), 'dify_client_layer')


_HUMANIZED_REASON: dict[str, str] = {
    'force_mock': '当前接口被配置为强制 Mock',
    'missing_api_key': 'Dify API Key 未配置',
    'missing_api_base': 'Dify Base URL 未配置',
    'empty_answer': 'Dify 已响应，但返回内容为空',
    'invalid_response': 'Dify 响应结构异常',
    'dify_non_200': 'Dify 已收到请求，但工作流执行失败',
    'dify_exception': 'Dify HTTP 请求阶段异常',
}


def _humanize_dify_message(reason: str, error: str, detail: str) -> str:
    reason = (reason or '').strip()
    low = f'{(error or "").strip()} {(detail or "").strip()}'.lower()
    if 'incorrect model credentials' in low:
        return 'Dify 已收到请求，但工作流模型凭证错误'
    if 'unauthorized' in low or 'invalid api key' in low:
        return 'Dify 鉴权失败，请检查应用 API Key'
    if 'server disconnected without sending a response' in low:
        return 'Dify 阻塞调用等待过久，连接被上游或中间层提前断开'
    if 'timeout' in low:
        return 'Dify 请求超时'
    return _HUMANIZED_REASON.get(reason, 'Dify 调用失败')


def dify_failure_response(
    *,
    workflow_code: str,
    route_path: str,
    call: dict[str, Any] | None,
    http_status: int = 502,
) -> JSONResponse:
    payload = call if isinstance(call, dict) else {}
    reason = str(payload.get('reason') or 'dify_exception').strip()
    error = str(payload.get('error') or '').strip()
    raw = payload.get('raw')
    detail, error_code = _summarize_raw_failure(raw)
    message = _humanize_dify_message(reason, error, detail)
    dify_called = reason not in {'force_mock', 'missing_api_key', 'missing_api_base'}
    data = {
        'route_path': route_path,
        'workflow_reason': reason,
        'dify_error': error,
        'failure_layer': _classify_failure_layer(reason),
        'dify_called': dify_called,
        'detail': detail or error or reason,
        'error_code': error_code,
    }
    if isinstance(raw, dict):
        data['raw'] = raw
    _log.warning(
        "dify failure workflow=%s route=%s reason=%s layer=%s",
        workflow_code, route_path, reason, data['failure_layer'],
    )
    return error_response(
        workflow_code=workflow_code,
        message=message,
        data=data,
        http_status=http_status,
        mock=False,
        extra_meta={
            'failure_layer': data['failure_layer'],
            'dify_called': dify_called,
        },
    )


def deprecated_response(
    *,
    path: str,
    replacement: str,
    method: str,
) -> dict[str, Any]:
    return {
        'code': 410,
        'message': '该旧业务接口已停用，请改用新模块接口',
        'data': {
            'path': path,
            'method': method.upper(),
            'replacement': replacement,
            'status': 'deprecated',
        },
        'meta': {
            'mock': False,
            'source': 'dify',
            'workflow_code': 'deprecated_api',
            'generated_at': now_iso(),
        },
    }
