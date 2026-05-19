"""Hermes 式自我进化（路线 B）· Agent 记忆/反思子系统。

Phase 1 范围：数据基座 + Episodic 自动采集。
Phase 2 范围：Semantic 抽取 + 检索注入。
Phase 3 范围：Reflective 自反思循环。
Phase 4 范围：Procedural 技能沉淀。
"""

from evo.episode_recorder import (
    apply_correction,
    apply_feedback,
    record_episode,
)
from evo.retriever import (
    MemoryHit,
    build_memory_block,
    inject_memory_block,
    retrieve_semantic_memories,
)
from evo.reflector import (
    expire_stale_reflections,
    run_reflection_cycle,
)
from evo.procedural_synthesizer import (
    disable_stale_procedural_memories,
    run_procedural_synthesis,
)
from evo.promoter import (
    approve_promotion,
    list_promotions,
    promotion_scan_diagnostics,
    reject_promotion,
    run_promotion_scan,
)
from evo.pipeline import (
    run_pipeline_advance,
)
from evo.anomaly_detector import (
    anomaly_scan_diagnostics,
    run_anomaly_scan,
)
from evo.eval_runner import (
    bound_memory_refs_for_case,
    quarantine_memories,
    run_eval_cases,
    seed_default_eval_cases,
)
from evo.schemas import (
    EpisodeCorrectionRequest,
    EpisodeFeedbackRequest,
    EpisodeRecord,
)
from evo.semantic_extractor import (
    extract_semantic_from_correction,
    extract_semantic_from_negative_feedback,
)

__all__ = [
    "EpisodeCorrectionRequest",
    "EpisodeFeedbackRequest",
    "EpisodeRecord",
    "MemoryHit",
    "apply_correction",
    "apply_feedback",
    "approve_promotion",
    "anomaly_scan_diagnostics",
    "build_memory_block",
    "disable_stale_procedural_memories",
    "extract_semantic_from_correction",
    "extract_semantic_from_negative_feedback",
    "expire_stale_reflections",
    "inject_memory_block",
    "list_promotions",
    "promotion_scan_diagnostics",
    "record_episode",
    "reject_promotion",
    "bound_memory_refs_for_case",
    "quarantine_memories",
    "run_anomaly_scan",
    "run_eval_cases",
    "run_pipeline_advance",
    "run_reflection_cycle",
    "run_procedural_synthesis",
    "run_promotion_scan",
    "seed_default_eval_cases",
    "retrieve_semantic_memories",
]
