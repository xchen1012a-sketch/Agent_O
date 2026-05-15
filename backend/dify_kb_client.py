"""DIFY 知识库 API 客户端 — dataset / document / segment CRUD + 检索测试。

与 dify_client.py 分离：后者封装 /v1/workflows/run 工作流调用，
本模块直接调用 DIFY Knowledge REST API（GET / POST / DELETE）。
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any

import httpx

import config as app_config

_logger = logging.getLogger("jewelry_qipei.dify_kb")

_DEFAULT_INDEXING_CONFIG = {
    "indexing_technique": "high_quality",
    "doc_form": "text_model",
    "process_rule": {"mode": "automatic"},
}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=app_config.KB_API_TIMEOUT, write=app_config.KB_API_TIMEOUT, pool=5.0)


def _base_url() -> str:
    return app_config.DIFY_KNOWLEDGE_API_BASE


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {app_config.DIFY_KNOWLEDGE_API_KEY}"}


def _extract_error_detail(e: httpx.HTTPStatusError) -> str:
    """Try to extract a human-readable message from DIFY error response JSON."""
    try:
        return e.response.json().get("message", str(e))
    except Exception:
        return str(e)


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared HTTP request helper for DIFY Knowledge API calls.

    Returns ``{ok: True, ...}`` on success or ``{ok: False, error: ..., status: ...}`` on failure.
    """
    url = f"{_base_url()}{path}"
    _logger.info("KB API %s %s", method, url)
    try:
        with httpx.Client(timeout=_timeout(), transport=httpx.HTTPTransport(retries=0)) as c:
            r = c.request(method, url, params=params, json=json, files=files, data=data, headers=_headers())
            r.raise_for_status()
        try:
            body = r.json()
        except Exception:
            body = {}
        return {"ok": True, "_body": body}
    except httpx.HTTPStatusError as e:
        _logger.warning("KB API %s %s failed: %s", method, url, e)
        return {"ok": False, "error": _extract_error_detail(e), "status": e.response.status_code}
    except httpx.RequestError as e:
        _logger.warning("KB API %s %s failed: %s", method, url, e)
        return {"ok": False, "error": str(e)}


def kb_list_datasets(page: int = 1, limit: int = 20) -> dict[str, Any]:
    """GET /v1/datasets — 返回 {ok, data, total, has_more, error}"""
    result = _request("GET", "/v1/datasets", params={"page": page, "limit": limit})
    if not result["ok"]:
        return result
    body = result["_body"]
    return {"ok": True, "data": body.get("data", []), "total": body.get("total", 0), "has_more": body.get("has_more", False)}


def kb_upload_document(
    dataset_id: str,
    file_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    """POST /v1/datasets/{dataset_id}/document/create-by-file (multipart)

    返回 {ok, document, batch, error}
    """
    data_json = _json.dumps(_DEFAULT_INDEXING_CONFIG)

    result = _request(
        "POST",
        f"/v1/datasets/{dataset_id}/document/create-by-file",
        files={"file": (filename, file_bytes)},
        data={"data": data_json},
    )
    if not result["ok"]:
        return result
    body = result["_body"]
    return {"ok": True, "document": body.get("document", {}), "batch": body.get("batch", "")}


def kb_create_text_document(
    dataset_id: str,
    name: str,
    text: str,
) -> dict[str, Any]:
    """POST /v1/datasets/{dataset_id}/document/create-by-text — 粘贴文本创建文档。

    返回 {ok, document, batch, error}
    """
    data_payload = {
        **_DEFAULT_INDEXING_CONFIG,
        "data": text,
        "name": name,
    }
    result = _request(
        "POST",
        f"/v1/datasets/{dataset_id}/document/create-by-text",
        json=data_payload,
    )
    if not result["ok"]:
        return result
    body = result["_body"]
    return {"ok": True, "document": body.get("document", {}), "batch": body.get("batch", "")}


def kb_list_segments(
    dataset_id: str,
    document_id: str,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """GET /v1/datasets/{dataset_id}/documents/{document_id}/segments — 查看文档分段。

    返回 {ok, data, total, error}
    """
    result = _request(
        "GET",
        f"/v1/datasets/{dataset_id}/documents/{document_id}/segments",
        params={"page": page, "limit": limit},
    )
    if not result["ok"]:
        return result
    body = result["_body"]
    return {"ok": True, "data": body.get("data", []), "total": body.get("total", 0)}


def kb_hit_test(
    dataset_id: str,
    query: str,
    retrieval_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /v1/datasets/{dataset_id}/hit-testing — 检索测试。

    返回 {ok, query, results, error}
    """
    payload: dict[str, Any] = {
        "query": query,
        "retrieval_model": retrieval_model or {
            "search_method": "semantic_search",
            "reranking_enable": True,
            "reranking_mode": "weighted_score",
            "weights": {
                "keyword_setting": {"keyword_weight": 0.3},
                "semantic_setting": {"semantic_weight": 0.7},
            },
            "top_k": 5,
            "score_threshold_enabled": True,
            "score_threshold": 0.1,
        },
    }
    result = _request(
        "POST",
        f"/v1/datasets/{dataset_id}/hit-testing",
        json=payload,
    )
    if not result["ok"]:
        return result
    body = result["_body"]
    return {"ok": True, "query": query, "results": body.get("records", [])}


def kb_get_workflow_dataset_mapping() -> dict[str, list[str]]:
    """返回工作流与知识库的关联映射（读取 config 中的环境变量）。"""
    mapping: dict[str, list[str]] = {}
    for agent, env_val in [
        ("agent2_on_duty", app_config.KB_DATASET_IDS_AGENT2),
        ("agent3_practice", app_config.KB_DATASET_IDS_AGENT3),
        ("agent4_other", app_config.KB_DATASET_IDS_AGENT4),
        ("qa_knowledge", app_config.KB_DATASET_IDS_QA),
    ]:
        if env_val:
            ids = [x.strip() for x in env_val.split(",") if x.strip()]
            if ids:
                mapping[agent] = ids
    return mapping


def kb_create_dataset(
    name: str,
    description: str = "",
) -> dict[str, Any]:
    """POST /v1/datasets — 创建知识库。返回 {ok, dataset, error}"""
    result = _request(
        "POST",
        "/v1/datasets",
        json={"name": name, "description": description, "indexing_technique": "high_quality"},
    )
    if not result["ok"]:
        return result
    return {"ok": True, "dataset": result["_body"]}


def kb_delete_dataset(dataset_id: str) -> dict[str, Any]:
    """DELETE /v1/datasets/{dataset_id} — 删除知识库。返回 {ok, error}"""
    return _request("DELETE", f"/v1/datasets/{dataset_id}")


def kb_list_documents(
    dataset_id: str,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """GET /v1/datasets/{dataset_id}/documents — 返回 {ok, data, total, has_more, error}"""
    result = _request(
        "GET",
        f"/v1/datasets/{dataset_id}/documents",
        params={"page": page, "limit": limit},
    )
    if not result["ok"]:
        return result
    body = result["_body"]
    return {"ok": True, "data": body.get("data", []), "total": body.get("total", 0), "has_more": body.get("has_more", False)}


def kb_delete_document(dataset_id: str, document_id: str) -> dict[str, Any]:
    """DELETE /v1/datasets/{dataset_id}/documents/{document_id} — 删除文档。返回 {ok, error}"""
    return _request("DELETE", f"/v1/datasets/{dataset_id}/documents/{document_id}")
