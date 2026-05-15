"""高频问题聚类服务（B1）。

从 ``assistant_records`` 表读取在岗助手历史问答，使用字符 bigram + TF-IDF
做近义聚类，输出 Top-N 高频问题簇，供"店长 AI 教练建议"卡片（B2）消费。

设计要点：
- 不调 LLM，纯 Python 实现，演示稳定、毫秒级响应（数据规模 < 10k 行）。
- 字符 n-gram 避开中文分词器依赖（jieba / sklearn 都不在依赖里）。
- 完全相同的问题走快速分桶；近义表达再走 TF-IDF 余弦相似度兜底。
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

_log = logging.getLogger("jewelry_qipei.knowledge_feedback")

_DEMO_SUFFIX_RE = re.compile(r"[（(]\s*演示样本[^）)]*[）)]\s*$")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_TOP_N = 5
DEFAULT_MIN_CLUSTER_SIZE = 2
DEFAULT_SIMILARITY_THRESHOLD = 0.35
DEFAULT_SAMPLE_LIMIT = 3
DEFAULT_KEYWORD_LIMIT = 5
DEFAULT_FETCH_LIMIT = 2000

# 中文虚词与高频功能词：从 top_keywords 里过滤掉，避免输出"顾客/问/的"等无信息词。
_STOPWORDS: frozenset[str] = frozenset(
    {
        "顾客", "客户", "问", "问下", "问问", "请问", "想问",
        "如果", "怎么", "怎么办", "怎么样", "怎样", "如何",
        "什么", "为什么", "为啥", "哪个", "哪些", "可以", "不能",
        "可以吗", "可不可以", "能不能", "有没有", "是不是", "对吗",
        "我", "你", "他", "她", "我们", "你们", "他们",
        "的", "了", "吗", "呢", "啊", "呀", "吧", "嘛",
        "和", "与", "或", "在", "对", "把", "被", "给", "让",
        "这", "那", "这个", "那个", "这种", "那种", "这样", "那样",
    }
)


def normalize_question(text: str) -> str:
    """归一化问题文本：去除演示后缀、折叠空白。"""
    if not text:
        return ""
    cleaned = _DEMO_SUFFIX_RE.sub("", text).strip()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


@dataclass
class _Record:
    question: str
    normalized: str
    knowledge_tag: str
    question_type: str
    risk_level: str


@dataclass
class _Cluster:
    centroid_vector: dict[str, float]
    centroid_norm: float
    members: list[_Record] = field(default_factory=list)
    question_counter: Counter[str] = field(default_factory=Counter)
    tag_counter: Counter[str] = field(default_factory=Counter)
    type_counter: Counter[str] = field(default_factory=Counter)
    risk_counter: Counter[str] = field(default_factory=Counter)

    @property
    def count(self) -> int:
        return len(self.members)

    def add(self, record: _Record, vector: dict[str, float], norm: float) -> None:
        # 增量更新质心：保持向量为成员均值，避免再做一遍归一化。
        n = self.count
        for token, weight in vector.items():
            self.centroid_vector[token] = (self.centroid_vector.get(token, 0.0) * n + weight) / (n + 1)
        for token in list(self.centroid_vector.keys()):
            if token not in vector:
                self.centroid_vector[token] = self.centroid_vector[token] * n / (n + 1)
        self.centroid_norm = _vector_norm(self.centroid_vector)

        self.members.append(record)
        self.question_counter[record.normalized] += 1
        if record.knowledge_tag:
            self.tag_counter[record.knowledge_tag] += 1
        if record.question_type:
            self.type_counter[record.question_type] += 1
        if record.risk_level:
            self.risk_counter[record.risk_level] += 1


def _char_ngrams(text: str) -> list[str]:
    """字符 unigram + bigram 联合特征。

    短中文文本里单 bigram 信号不够稳，叠加 unigram 让"换序/换字"的近义句也能匹配。
    """
    if not text:
        return []
    tokens: list[str] = list(text)
    if len(text) > 1:
        tokens.extend(text[i : i + 2] for i in range(len(text) - 1))
    return tokens


def _vector_norm(vector: dict[str, float]) -> float:
    return math.sqrt(sum(w * w for w in vector.values()))


def _cosine(a: dict[str, float], a_norm: float, b: dict[str, float], b_norm: float) -> float:
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for token, weight in a.items():
        other = b.get(token)
        if other is not None:
            dot += weight * other
    return dot / (a_norm * b_norm)


def _compute_tfidf(records: list[_Record]) -> list[tuple[dict[str, float], float]]:
    """对每条记录的归一化问题计算字符 bigram TF-IDF 向量。"""
    document_frequency: Counter[str] = Counter()
    term_frequencies: list[Counter[str]] = []
    for record in records:
        tokens = _char_ngrams(record.normalized)
        tf = Counter(tokens)
        term_frequencies.append(tf)
        for token in tf:
            document_frequency[token] += 1

    total_docs = len(records)
    vectors: list[tuple[dict[str, float], float]] = []
    for tf in term_frequencies:
        if not tf:
            vectors.append(({}, 0.0))
            continue
        vector: dict[str, float] = {}
        for token, count in tf.items():
            df = document_frequency[token]
            idf = math.log((1 + total_docs) / (1 + df)) + 1.0
            vector[token] = count * idf
        vectors.append((vector, _vector_norm(vector)))
    return vectors


def _build_record_query(
    *, store_id: str | None, since: datetime | None, limit: int
) -> tuple[str, list[Any]]:
    where: list[str] = ["TRIM(customer_question) != ''"]
    params: list[Any] = []
    if store_id:
        where.append("store_id = ?")
        params.append(store_id)
    if since is not None:
        where.append("created_at >= ?")
        params.append(_to_iso(since))
    sql = (
        "SELECT customer_question, knowledge_tag, question_type, risk_level "
        "FROM assistant_records WHERE " + " AND ".join(where)
        + " ORDER BY id DESC LIMIT ?"
    )
    params.append(int(limit))
    return sql, params


def _fetch_records(
    conn: sqlite3.Connection,
    *,
    store_id: str | None,
    since: datetime | None,
    limit: int,
) -> list[_Record]:
    sql, params = _build_record_query(store_id=store_id, since=since, limit=limit)
    rows: Iterable[Any] = conn.execute(sql, params).fetchall()
    records: list[_Record] = []
    for row in rows:
        raw_question = (row["customer_question"] if isinstance(row, sqlite3.Row) else row[0]) or ""
        normalized = normalize_question(raw_question)
        if not normalized:
            continue
        if isinstance(row, sqlite3.Row):
            tag = (row["knowledge_tag"] or "").strip()
            qtype = (row["question_type"] or "").strip()
            risk = (row["risk_level"] or "").strip()
        else:
            tag = (row[1] or "").strip()
            qtype = (row[2] or "").strip()
            risk = (row[3] or "").strip()
        records.append(
            _Record(
                question=raw_question,
                normalized=normalized,
                knowledge_tag=tag,
                question_type=qtype,
                risk_level=risk,
            )
        )
    return records


def _pick_top_keywords(cluster: _Cluster, *, limit: int) -> list[str]:
    """从质心向量里挑权重最高、长度 >= 2、非停用词的关键词。"""
    candidates = sorted(cluster.centroid_vector.items(), key=lambda kv: kv[1], reverse=True)
    keywords: list[str] = []
    seen: set[str] = set()
    for token, _weight in candidates:
        token = token.strip()
        if len(token) < 2 or token in _STOPWORDS or token in seen:
            continue
        if not any(ch.isalnum() or "一" <= ch <= "鿿" for ch in token):
            continue
        keywords.append(token)
        seen.add(token)
        if len(keywords) >= limit:
            break
    return keywords


def _pick_representative(cluster: _Cluster) -> str:
    """取簇内最常见的归一化问题；若并列，挑长度居中的版本（不要太短也不要太长）。"""
    most_common = cluster.question_counter.most_common()
    if not most_common:
        return cluster.members[0].normalized if cluster.members else ""
    top_count = most_common[0][1]
    tied = [q for q, c in most_common if c == top_count]
    if len(tied) == 1:
        return tied[0]
    tied.sort(key=lambda q: (abs(len(q) - 18), len(q)))
    return tied[0]


def _serialize_cluster(cluster: _Cluster, *, rank: int, sample_limit: int, keyword_limit: int) -> dict[str, Any]:
    primary_tag = cluster.tag_counter.most_common(1)[0][0] if cluster.tag_counter else ""
    primary_type = cluster.type_counter.most_common(1)[0][0] if cluster.type_counter else ""
    primary_risk = cluster.risk_counter.most_common(1)[0][0] if cluster.risk_counter else ""
    sample_questions = [q for q, _ in cluster.question_counter.most_common(sample_limit)]
    return {
        "rank": rank,
        "cluster_id": f"cluster_{rank:03d}",
        "count": cluster.count,
        "representative_question": _pick_representative(cluster),
        "sample_questions": sample_questions,
        "top_keywords": _pick_top_keywords(cluster, limit=keyword_limit),
        "primary_tag": primary_tag,
        "primary_question_type": primary_type,
        "risk_level": primary_risk,
    }


def cluster_high_frequency_questions(
    conn: sqlite3.Connection,
    *,
    store_id: str | None = None,
    since: datetime | None = None,
    top_n: int = DEFAULT_TOP_N,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    keyword_limit: int = DEFAULT_KEYWORD_LIMIT,
) -> list[dict[str, Any]]:
    """聚类在岗助手历史问题，返回按频次倒序的 Top-N 簇。

    Args:
        conn: 已开启 ``row_factory = sqlite3.Row`` 的 SQLite 连接。
        store_id: 仅统计该门店；为空则跨门店。
        since: 仅统计 ``created_at >= since`` 的记录；为空则不限时间。
        top_n: 返回的簇数量上限。
        min_cluster_size: 簇成员数下限，低于该阈值的簇被过滤。
        similarity_threshold: 余弦相似度合并阈值（0~1）。
        fetch_limit: 单次最多读取的历史问答行数。
        sample_limit: 每个簇返回的代表性问题数。
        keyword_limit: 每个簇返回的关键词数。
    """
    records = _fetch_records(conn, store_id=store_id, since=since, limit=fetch_limit)
    if not records:
        return []

    vectors = _compute_tfidf(records)
    clusters: list[_Cluster] = []

    # 先按归一化文本完全相同分桶（O(n)），再用 TF-IDF 余弦合并近义簇。
    bucket_by_text: dict[str, int] = {}
    for record, (vector, norm) in zip(records, vectors):
        bucket_idx = bucket_by_text.get(record.normalized)
        if bucket_idx is not None:
            clusters[bucket_idx].add(record, vector, norm)
            continue

        best_idx = -1
        best_sim = similarity_threshold
        for idx, cluster in enumerate(clusters):
            sim = _cosine(vector, norm, cluster.centroid_vector, cluster.centroid_norm)
            if sim >= best_sim:
                best_sim = sim
                best_idx = idx
        if best_idx >= 0:
            clusters[best_idx].add(record, vector, norm)
            bucket_by_text[record.normalized] = best_idx
        else:
            new_cluster = _Cluster(centroid_vector=dict(vector), centroid_norm=norm)
            new_cluster.add(record, vector, norm)
            clusters.append(new_cluster)
            bucket_by_text[record.normalized] = len(clusters) - 1

    filtered = [c for c in clusters if c.count >= min_cluster_size]
    filtered.sort(key=lambda c: c.count, reverse=True)
    top = filtered[: max(0, top_n)]
    return [
        _serialize_cluster(c, rank=rank, sample_limit=sample_limit, keyword_limit=keyword_limit)
        for rank, c in enumerate(top, start=1)
    ]


# ---------------------------------------------------------------------------
# B2: 派发审计表 + 增删查
# ---------------------------------------------------------------------------


DISPATCH_TABLE = "knowledge_feedback_dispatches"
DISPATCH_STATUS_DISPATCHED = "dispatched"
DISPATCH_STATUS_COMPLETED = "completed"
DISPATCH_STATUS_CANCELLED = "cancelled"
_ACTIVE_DISPATCH_STATUSES = (DISPATCH_STATUS_DISPATCHED, DISPATCH_STATUS_COMPLETED)


@dataclass(frozen=True)
class DispatchInput:
    """店长一键派发的输入载荷。"""

    cluster_signature: str
    representative_question: str
    primary_tag: str
    top_keywords: list[str]
    cluster_count: int
    store_id: str
    dispatched_by_user_id: str
    target_user_ids: list[str]
    target_role: str = ""
    note: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dispatch_table(conn: sqlite3.Connection) -> None:
    """幂等建表。生产路径走 database.py 的 schema 注册，这里给独立连接（如测试）兜底。"""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DISPATCH_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_signature TEXT NOT NULL DEFAULT '',
            representative_question TEXT NOT NULL DEFAULT '',
            primary_tag TEXT NOT NULL DEFAULT '',
            top_keywords_json TEXT NOT NULL DEFAULT '[]',
            cluster_count INTEGER NOT NULL DEFAULT 0,
            store_id TEXT NOT NULL DEFAULT '',
            dispatched_by_user_id TEXT NOT NULL DEFAULT '',
            target_user_id TEXT NOT NULL DEFAULT '',
            target_role TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'dispatched',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_kf_dispatch_target ON {DISPATCH_TABLE}(target_user_id)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_kf_dispatch_store ON {DISPATCH_TABLE}(store_id)"
    )


def _dedupe_targets(target_user_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in target_user_ids or []:
        text = str(raw or "").strip()
        if text and text not in seen:
            deduped.append(text)
            seen.add(text)
    return deduped


def dispatch_cluster_to_targets(
    conn: sqlite3.Connection,
    *,
    payload: DispatchInput,
) -> dict[str, Any]:
    """对每位 target 写一条派发记录，返回汇总。"""
    representative = str(payload.representative_question or "").strip()
    if not representative:
        raise ValueError("representative_question is required")

    targets = _dedupe_targets(payload.target_user_ids)
    if not targets:
        raise ValueError("target_user_ids must contain at least one user")

    signature = (payload.cluster_signature or representative).strip()
    keywords_json = json.dumps(list(payload.top_keywords or []), ensure_ascii=False)
    now = _utc_now_iso()
    dispatch_ids: list[int] = []
    for target in targets:
        cur = conn.execute(
            f"""
            INSERT INTO {DISPATCH_TABLE} (
                cluster_signature, representative_question, primary_tag,
                top_keywords_json, cluster_count, store_id,
                dispatched_by_user_id, target_user_id, target_role,
                status, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signature,
                representative,
                (payload.primary_tag or "").strip(),
                keywords_json,
                int(payload.cluster_count or 0),
                (payload.store_id or "").strip(),
                (payload.dispatched_by_user_id or "").strip(),
                target,
                (payload.target_role or "").strip(),
                DISPATCH_STATUS_DISPATCHED,
                (payload.note or "").strip(),
                now,
                now,
            ),
        )
        dispatch_ids.append(int(cur.lastrowid or 0))

    return {
        "dispatched_count": len(dispatch_ids),
        "dispatch_ids": dispatch_ids,
        "cluster_signature": signature,
        "representative_question": representative,
        "created_at": now,
    }


def _parse_keywords(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _serialize_dispatch_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "cluster_signature": row["cluster_signature"] or "",
        "representative_question": row["representative_question"] or "",
        "primary_tag": row["primary_tag"] or "",
        "top_keywords": _parse_keywords(row["top_keywords_json"]),
        "cluster_count": int(row["cluster_count"] or 0),
        "store_id": row["store_id"] or "",
        "dispatched_by_user_id": row["dispatched_by_user_id"] or "",
        "target_user_id": row["target_user_id"] or "",
        "target_role": row["target_role"] or "",
        "status": row["status"] or "",
        "note": row["note"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def list_dispatched_tasks_for_user(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """导购视角：拉自己被派发到的活跃任务。"""
    if not user_id:
        return []
    placeholders = ",".join("?" for _ in _ACTIVE_DISPATCH_STATUSES)
    rows = conn.execute(
        f"""
        SELECT * FROM {DISPATCH_TABLE}
        WHERE target_user_id = ? AND status IN ({placeholders})
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(user_id), *_ACTIVE_DISPATCH_STATUSES, int(limit)),
    ).fetchall()
    return [_serialize_dispatch_row(row) for row in rows]


def list_recent_dispatches_by_store(
    conn: sqlite3.Connection,
    *,
    store_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """店长视角：看本店最近一次派发去向。"""
    if not store_id:
        return []
    rows = conn.execute(
        f"""
        SELECT * FROM {DISPATCH_TABLE}
        WHERE store_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(store_id), int(limit)),
    ).fetchall()
    return [_serialize_dispatch_row(row) for row in rows]
