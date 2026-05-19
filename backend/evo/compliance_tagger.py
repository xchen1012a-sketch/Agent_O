"""合规敏感词检测——Phase 1 用最小硬编码集，珠宝行业典型违禁/高风险表达。

TODO(Phase 2): 接入正式禁用词清单（来源待 PM 提供），并允许后台 system_settings 维护。
"""

from __future__ import annotations

import re

# 命中即标记为高风险，触发后续 review_queue（在 Phase 2 才真正生效；
# Phase 1 仅作为元数据落库，便于演示"系统看见了风险"）。
_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "保值",
    "升值",
    "投资",
    "100%",
    "百分百",
    "绝对",
    "保证",
    "稳赚",
    "国家认证",
    "包退",
)

_SENSITIVE_PATTERN = re.compile("|".join(re.escape(kw) for kw in _SENSITIVE_KEYWORDS))


def detect_compliance_tags(*texts: str) -> list[str]:
    """对任意条目文本扫描敏感词，返回命中的 tag 列表（去重，保持声明顺序）。"""
    seen: set[str] = set()
    hits: list[str] = []
    blob = " ".join(t for t in texts if t)
    if not blob:
        return hits
    for match in _SENSITIVE_PATTERN.findall(blob):
        if match not in seen:
            seen.add(match)
            hits.append(match)
    return hits
