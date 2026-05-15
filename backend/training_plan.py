from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any


MODULE_NAME_MAP = {
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


@dataclass(frozen=True)
class TaskTemplate:
    task_code: str
    task_type: str
    title: str
    description: str
    branch: str
    route_page: str
    module_code: str
    target_count: int = 1

    @property
    def module_name(self) -> str:
        return MODULE_NAME_MAP.get(self.module_code, self.module_code)


STAGE_TEMPLATES: dict[int, list[list[TaskTemplate]]] = {
    1: [
        [
            TaskTemplate("s1d1_learning", "learning_review", "产品基础学习", "回顾产品基础知识与卖点表达", "learning", "growth_plan", "product_basics"),
            TaskTemplate("s1d1_practice", "practice_chat", "基础接待练习", "完成一次基础接待与开场对话练习", "practice", "practical_training", "customer_opening"),
        ],
        [
            TaskTemplate("s1d2_quiz", "knowledge_quiz", "材质知识测验", "检验材质与产品基础知识掌握程度", "learning", "growth_plan", "product_basics"),
            TaskTemplate("s1d2_practice", "practice_chat", "标准表达练习", "完成一次合规表达练习", "practice", "practical_training", "compliance_expression"),
        ],
        [
            TaskTemplate("s1d3_learning", "learning_review", "合规表达学习", "学习合规表达与风控边界", "learning", "growth_plan", "compliance_expression"),
            TaskTemplate("s1d3_practice", "practice_chat", "顾客开场练习", "完成一次顾客开场场景训练", "practice", "practical_training", "customer_opening"),
        ],
        [
            TaskTemplate("s1d4_exam", "mock_exam", "阶段小测", "围绕产品与合规进行阶段小测", "assessment", "assessment", "product_basics"),
            TaskTemplate("s1d4_practice", "practice_chat", "薄弱点补练", "围绕当前最低分维度进行补练", "practice", "practical_training", "compliance_expression"),
        ],
        [
            TaskTemplate("s1d5_practice", "practice_chat", "门店场景练习", "模拟门店接待与推荐场景", "practice", "practical_training", "customer_opening"),
            TaskTemplate("s1d5_learning", "learning_review", "学习回顾", "复盘阶段 1 已完成任务", "learning", "growth_plan", "final_review"),
        ],
        [
            TaskTemplate("s1d6_exam", "mock_exam", "基础模拟考", "执行一次基础综合模拟考", "assessment", "assessment", "product_basics"),
            TaskTemplate("s1d6_practice", "practice_chat", "补练任务", "围绕基础认知薄弱项补练", "practice", "practical_training", "customer_opening"),
        ],
        [
            TaskTemplate("s1d7_summary", "stage_review_prep", "阶段总结", "完成阶段总结并准备阶段评估", "learning", "growth_plan", "final_review"),
            TaskTemplate("s1d7_review", "mock_exam", "阶段评估", "完成阶段 1 评估", "assessment", "assessment", "final_review"),
        ],
    ],
    2: [
        [
            TaskTemplate("s2d1_learning", "learning_review", "需求挖掘学习", "学习需求识别与追问方法", "learning", "growth_plan", "needs_discovery"),
            TaskTemplate("s2d1_practice", "practice_chat", "追问练习", "完成一次需求挖掘场景对话", "practice", "practical_training", "needs_discovery"),
        ],
        [
            TaskTemplate("s2d2_learning", "learning_review", "异议处理学习", "学习价格与顾虑异议处理框架", "learning", "growth_plan", "objection_handling"),
            TaskTemplate("s2d2_practice", "practice_chat", "嫌贵场景练习", "完成一次嫌贵/犹豫场景练习", "practice", "practical_training", "objection_handling"),
        ],
        [
            TaskTemplate("s2d3_learning", "learning_review", "搭配推荐学习", "学习搭配推荐与连带销售逻辑", "learning", "growth_plan", "combo_recommendation"),
            TaskTemplate("s2d3_practice", "practice_chat", "组合推荐练习", "完成一次组合推荐对话", "practice", "practical_training", "combo_recommendation"),
        ],
        [
            TaskTemplate("s2d4_learning", "learning_review", "成交推进训练", "学习临门一脚与成交推进结构", "learning", "growth_plan", "closing_conversion"),
            TaskTemplate("s2d4_exam", "mock_exam", "转化小测", "执行一次转化能力小测", "assessment", "assessment", "closing_conversion"),
        ],
        [
            TaskTemplate("s2d5_practice", "practice_chat", "高压沟通练习", "处理高压顾客与复杂追问", "practice", "practical_training", "objection_handling"),
            TaskTemplate("s2d5_learning", "learning_review", "综合复盘", "复盘销售转化链路的关键节点", "learning", "growth_plan", "final_review"),
        ],
        [
            TaskTemplate("s2d6_exam", "mock_exam", "终局模拟考", "执行一次上岗前综合模拟考", "assessment", "assessment", "independent_service"),
            TaskTemplate("s2d6_practice", "practice_chat", "薄弱项补练", "针对终局模拟考薄弱项补练", "practice", "practical_training", "independent_service"),
        ],
        [
            TaskTemplate("s2d7_summary", "stage_review_prep", "阶段总结", "完成阶段 2 总结与上岗准备", "learning", "growth_plan", "final_review"),
            TaskTemplate("s2d7_review", "mock_exam", "上岗判定", "完成上岗判定评估", "assessment", "assessment", "independent_service"),
        ],
    ],
}


def _avg(values: list[float | int]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return 0.0
    return round(mean(cleaned), 2)


def _weighted_average(weighted_values: list[tuple[float, float]]) -> float:
    valid = [(value, weight) for value, weight in weighted_values if value > 0 and weight > 0]
    if not valid:
        return 0.0
    total_weight = sum(weight for _, weight in valid)
    return round(sum(value * weight for value, weight in valid) / total_weight, 2)


def build_stage_definitions() -> list[dict[str, Any]]:
    return [
        {
            "stage_no": 1,
            "stage_name": "基础认知",
            "total_days": 7,
            "pass_score": 80,
            "unlock_modules": ["growth_plan", "training_path"],
        },
        {
            "stage_no": 2,
            "stage_name": "销售转化与上岗",
            "total_days": 7,
            "pass_score": 80,
            "unlock_modules": ["practical_training", "assessment", "dashboard"],
        },
    ]


def build_daily_stage_tasks(stage_no: int, user_id: str, cycle_id: str, *, release_all: bool = False) -> list[dict[str, Any]]:
    stage_templates = STAGE_TEMPLATES.get(stage_no) or STAGE_TEMPLATES[1]
    day_rows: list[dict[str, Any]] = []
    for day_index, task_templates in enumerate(stage_templates, start=1):
        release_status = "released" if release_all or day_index == 1 else "locked"
        tasks = []
        for order, template in enumerate(task_templates, start=1):
            tasks.append(
                {
                    "cycle_id": cycle_id,
                    "user_id": user_id,
                    "day_index": day_index,
                    "task_code": template.task_code,
                    "task_type": template.task_type,
                    "branch": template.branch,
                    "title": template.title,
                    "description": template.description,
                    "route_page": template.route_page,
                    "module_code": template.module_code,
                    "module_name": template.module_name,
                    "target_count": template.target_count,
                    "release_status": release_status,
                    "sort_order": order,
                    "task_source": "system",
                    "evaluation_status": "pending",
                }
            )
        day_rows.append({"day_index": day_index, "tasks": tasks})
    return day_rows


def build_unlock_matrix(*, stage_no: int, stage_passed: bool, onboarding_completed: bool, is_retraining: bool) -> dict[str, Any]:
    modules = {
        "onboarding": True,
        "growth_plan": True,
        "practical_training": is_retraining or (onboarding_completed and stage_no >= 1 and stage_passed),
        "on_duty_assistant": is_retraining or (onboarding_completed and stage_no >= 1 and stage_passed),
        "assessment": is_retraining or (onboarding_completed and stage_no >= 2 and stage_passed),
        "dashboard": is_retraining or (onboarding_completed and stage_no >= 2 and stage_passed),
        "quick_query": is_retraining or (onboarding_completed and stage_no >= 2 and stage_passed),
    }
    if not onboarding_completed:
        force_redirect = "growth_plan"
    elif stage_no <= 1 and not stage_passed and not is_retraining:
        force_redirect = "growth_plan"
    else:
        force_redirect = ""
    return {
        "modules": modules,
        "onboarding_locked": not onboarding_completed,
        "force_redirect_page": force_redirect,
    }


def calculate_module_indexes(module_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for item in module_records:
        learning_index = _avg(item.get("learning_scores") or [])
        practice_index = _avg(item.get("practice_scores") or [])
        assessment_index = _avg(item.get("assessment_scores") or [])
        overall_index = _weighted_average(
            [
                (learning_index, 0.2),
                (practice_index, 0.4),
                (assessment_index, 0.4),
            ]
        )
        items.append(
            {
                "module_code": item.get("module_code") or "",
                "module_name": item.get("module_name") or MODULE_NAME_MAP.get(item.get("module_code") or "", ""),
                "learning_index": learning_index,
                "practice_index": practice_index,
                "assessment_index": assessment_index,
                "overall_index": overall_index,
            }
        )
    return items


def calculate_training_summary(
    *,
    learning_scores: list[float | int],
    practice_scores: list[float | int],
    assessment_scores: list[float | int],
    latest_stage_review_score: float | int | None,
    task_completion_rate: float | int,
    monthly_scores: list[float | int] | None = None,
) -> dict[str, Any]:
    learning_avg = _avg(learning_scores)
    practice_avg = _avg(practice_scores)
    assessment_avg = _avg(assessment_scores)
    stage_review = round(float(latest_stage_review_score or 0), 2)
    training_coefficient = _weighted_average(
        [
            (learning_avg, 0.2),
            (practice_avg, 0.3),
            (assessment_avg, 0.3),
            (stage_review, 0.2),
        ]
    )
    assessment_score = assessment_avg
    completion_rate = round(float(task_completion_rate or 0), 2)
    competition_total_score = round((training_coefficient * 0.6) + (assessment_score * 0.3) + (completion_rate * 0.1), 2)
    monthly_values = [float(value) for value in (monthly_scores or []) if value is not None]
    quarterly_average = round(mean(monthly_values), 2) if monthly_values else 0.0
    eligible = completion_rate >= 100 and competition_total_score >= 80
    return {
        "learning_avg_score": learning_avg,
        "practice_avg_score": practice_avg,
        "assessment_avg_score": assessment_score,
        "latest_stage_review_score": stage_review,
        "task_completion_rate": completion_rate,
        "training_coefficient": training_coefficient,
        "competition_total_score": competition_total_score,
        "competition_status": "eligible" if eligible else "pending",
        "monthly_scores": monthly_values,
        "quarterly_average_score": quarterly_average,
    }


# Override garbled legacy literals with normalized labels used by current UI/API.
MODULE_NAME_MAP = {
    "product_basics": "产品基础",
    "compliance_expression": "合规表达",
    "customer_opening": "接待开场",
    "needs_discovery": "需求洞察",
    "objection_handling": "异议处理",
    "combo_recommendation": "搭配推荐",
    "closing_conversion": "成交推进",
    "independent_service": "独立上岗",
    "final_review": "综合复盘",
}

STAGE_TEMPLATES = {
    1: [
        [
            TaskTemplate("s1d1_learning", "learning_review", "产品基础学习", "回顾产品基础知识与卖点表达。", "learning", "growth_plan", "product_basics"),
            TaskTemplate("s1d1_practice", "practice_chat", "基础接待练习", "完成一次基础接待与开场对话练习。", "practice", "practical_training", "customer_opening"),
        ],
        [
            TaskTemplate("s1d2_quiz", "knowledge_quiz", "材质知识测验", "检验材质与产品基础知识掌握程度。", "learning", "growth_plan", "product_basics"),
            TaskTemplate("s1d2_practice", "practice_chat", "标准表达练习", "完成一次合规表达练习。", "practice", "practical_training", "compliance_expression"),
        ],
        [
            TaskTemplate("s1d3_learning", "learning_review", "合规表达学习", "学习合规表达与风险边界。", "learning", "growth_plan", "compliance_expression"),
            TaskTemplate("s1d3_practice", "practice_chat", "顾客开场练习", "完成一次顾客开场场景训练。", "practice", "practical_training", "customer_opening"),
        ],
        [
            TaskTemplate("s1d4_exam", "mock_exam", "阶段小测", "围绕产品与合规进行阶段小测。", "assessment", "assessment", "product_basics"),
            TaskTemplate("s1d4_practice", "practice_chat", "薄弱点补练", "围绕当前最低分维度进行补练。", "practice", "practical_training", "compliance_expression"),
        ],
        [
            TaskTemplate("s1d5_practice", "practice_chat", "门店场景练习", "模拟门店接待与推荐场景。", "practice", "practical_training", "customer_opening"),
            TaskTemplate("s1d5_learning", "learning_review", "学习回顾", "复盘阶段 1 已完成任务。", "learning", "growth_plan", "final_review"),
        ],
        [
            TaskTemplate("s1d6_exam", "mock_exam", "基础模拟考", "执行一次基础综合模拟考。", "assessment", "assessment", "product_basics"),
            TaskTemplate("s1d6_practice", "practice_chat", "补练任务", "围绕基础认知薄弱项进行补练。", "practice", "practical_training", "customer_opening"),
        ],
        [
            TaskTemplate("s1d7_summary", "stage_review_prep", "阶段总结", "完成阶段总结并准备阶段评估。", "learning", "growth_plan", "final_review"),
            TaskTemplate("s1d7_review", "mock_exam", "阶段评估", "完成阶段 1 评估。", "assessment", "assessment", "final_review"),
        ],
    ],
    2: [
        [
            TaskTemplate("s2d1_learning", "learning_review", "需求洞察学习", "学习需求识别与追问方法。", "learning", "growth_plan", "needs_discovery"),
            TaskTemplate("s2d1_practice", "practice_chat", "追问练习", "完成一次需求洞察场景对话。", "practice", "practical_training", "needs_discovery"),
        ],
        [
            TaskTemplate("s2d2_learning", "learning_review", "异议处理学习", "学习价格与顾虑异议的处理框架。", "learning", "growth_plan", "objection_handling"),
            TaskTemplate("s2d2_practice", "practice_chat", "嫌贵场景练习", "完成一次价格异议场景练习。", "practice", "practical_training", "objection_handling"),
        ],
        [
            TaskTemplate("s2d3_learning", "learning_review", "搭配推荐学习", "学习搭配推荐与连带销售逻辑。", "learning", "growth_plan", "combo_recommendation"),
            TaskTemplate("s2d3_practice", "practice_chat", "组合推荐练习", "完成一次组合推荐对话。", "practice", "practical_training", "combo_recommendation"),
        ],
        [
            TaskTemplate("s2d4_learning", "learning_review", "成交推进训练", "学习临门一脚与成交推进结构。", "learning", "growth_plan", "closing_conversion"),
            TaskTemplate("s2d4_exam", "mock_exam", "转化小测", "执行一次转化能力小测。", "assessment", "assessment", "closing_conversion"),
        ],
        [
            TaskTemplate("s2d5_practice", "practice_chat", "高压沟通练习", "处理高压顾客与复杂追问。", "practice", "practical_training", "objection_handling"),
            TaskTemplate("s2d5_learning", "learning_review", "综合复盘", "复盘销售转化链路的关键节点。", "learning", "growth_plan", "final_review"),
        ],
        [
            TaskTemplate("s2d6_exam", "mock_exam", "终局模拟考", "执行一次上岗前综合模拟考。", "assessment", "assessment", "independent_service"),
            TaskTemplate("s2d6_practice", "practice_chat", "薄弱项补练", "针对终局模拟考的薄弱项补练。", "practice", "practical_training", "independent_service"),
        ],
        [
            TaskTemplate("s2d7_summary", "stage_review_prep", "阶段总结", "完成阶段 2 总结与上岗准备。", "learning", "growth_plan", "final_review"),
            TaskTemplate("s2d7_review", "mock_exam", "上岗判定", "完成上岗判定评估。", "assessment", "assessment", "independent_service"),
        ],
    ],
}


def build_stage_definitions() -> list[dict[str, Any]]:
    return [
        {
            "stage_no": 1,
            "stage_name": "基础认知",
            "total_days": 7,
            "pass_score": 80,
            "unlock_modules": ["growth_plan"],
        },
        {
            "stage_no": 2,
            "stage_name": "销售转化与上岗",
            "total_days": 7,
            "pass_score": 80,
            "unlock_modules": ["practical_training", "assessment", "dashboard"],
        },
    ]
