"""能力维度 / 业务模块 / 知识标签的受控词表与归一化（复盘本 D2）。

集中放映射表，避免散落在各 service。所有"自由文本 → 受控枚举"的转换都过这里。

- ``DIMENSIONS_6`` 取自 ``routers/personnel.py::_JOURNEY_DIMENSIONS``（成长之旅雷达 SoT）
- ``MODULES_9`` 取自 ``training_plan.MODULE_NAME_MAP``
- ``KNOWLEDGE_TAG_TO_DIMENSION``：关键词兜底匹配，归一化失败落 ``OTHER_DIMENSION``
"""

from __future__ import annotations

from typing import Iterable

OTHER_DIMENSION = "other"

DIMENSIONS_6: dict[str, str] = {
    "product_knowledge": "产品知识",
    "compliance_expression": "合规表达",
    "needs_discovery": "需求挖掘",
    "sales_expression": "销售沟通",
    "objection_handling": "异议处理",
    "closing_skill": "成交收口",
}

DIMENSION_ORDER: tuple[str, ...] = (
    "product_knowledge",
    "compliance_expression",
    "needs_discovery",
    "sales_expression",
    "objection_handling",
    "closing_skill",
)

MODULES_9: dict[str, str] = {
    "product_basics": "产品基础",
    "compliance_expression": "合规表达",
    "customer_opening": "接待开场",
    "needs_discovery": "需求挖掘",
    "objection_handling": "异议处理",
    "combo_recommendation": "搭配推荐",
    "closing_conversion": "成交推进",
    "independent_service": "独立上岗",
    "final_review": "综合复盘",
}

MODULE_TO_DIMENSION: dict[str, str] = {
    "product_basics": "product_knowledge",
    "compliance_expression": "compliance_expression",
    "customer_opening": "sales_expression",
    "needs_discovery": "needs_discovery",
    "objection_handling": "objection_handling",
    "combo_recommendation": "sales_expression",
    "closing_conversion": "closing_skill",
    "independent_service": "sales_expression",
    "final_review": "closing_skill",
}

KNOWLEDGE_TAG_TO_DIMENSION: list[tuple[str, str]] = [
    ("产品", "product_knowledge"),
    ("钻石", "product_knowledge"),
    ("翡翠", "product_knowledge"),
    ("黄金", "product_knowledge"),
    ("铂金", "product_knowledge"),
    ("4C", "product_knowledge"),
    ("4c", "product_knowledge"),
    ("材质", "product_knowledge"),
    ("工艺", "product_knowledge"),
    ("GIA", "product_knowledge"),
    ("认证", "product_knowledge"),
    ("珠宝", "product_knowledge"),
    ("合规", "compliance_expression"),
    ("承诺", "compliance_expression"),
    ("法规", "compliance_expression"),
    ("广告", "compliance_expression"),
    ("虚假", "compliance_expression"),
    ("保证", "compliance_expression"),
    ("需求", "needs_discovery"),
    ("预算", "needs_discovery"),
    ("场景", "needs_discovery"),
    ("送礼", "needs_discovery"),
    ("挖掘", "needs_discovery"),
    ("洞察", "needs_discovery"),
    ("异议", "objection_handling"),
    ("价格", "objection_handling"),
    ("嫌贵", "objection_handling"),
    ("质疑", "objection_handling"),
    ("对比", "objection_handling"),
    ("顾虑", "objection_handling"),
    ("成交", "closing_skill"),
    ("收口", "closing_skill"),
    ("试戴", "closing_skill"),
    ("连带", "closing_skill"),
    ("转化", "closing_skill"),
    ("逼单", "closing_skill"),
    ("接待", "sales_expression"),
    ("开场", "sales_expression"),
    ("迎宾", "sales_expression"),
    ("破冰", "sales_expression"),
    ("沟通", "sales_expression"),
    ("表达", "sales_expression"),
    ("话术", "sales_expression"),
]


def is_known_dimension(value: str) -> bool:
    return value in DIMENSIONS_6


def is_known_module(value: str) -> bool:
    return value in MODULES_9


def dimension_label(dimension: str) -> str:
    return DIMENSIONS_6.get(dimension, "其他") if dimension else "其他"


def module_label(module_code: str) -> str:
    return MODULES_9.get(module_code, module_code or "")


def normalize_to_dimension(
    *raw_values: str,
    module_code: str = "",
) -> str:
    cleaned = [str(v or "").strip() for v in raw_values if str(v or "").strip()]

    for value in cleaned:
        if value in DIMENSIONS_6:
            return value

    code = str(module_code or "").strip()
    if code in MODULE_TO_DIMENSION:
        return MODULE_TO_DIMENSION[code]

    for value in cleaned:
        for keyword, dimension in KNOWLEDGE_TAG_TO_DIMENSION:
            if keyword and keyword in value:
                return dimension

    return OTHER_DIMENSION


def normalize_module(*raw_values: str) -> str:
    for value in raw_values:
        text = str(value or "").strip()
        if text in MODULES_9:
            return text
    return ""


def iter_dimensions() -> Iterable[tuple[str, str]]:
    for key in DIMENSION_ORDER:
        yield key, DIMENSIONS_6[key]
