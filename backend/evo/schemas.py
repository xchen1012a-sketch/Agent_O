"""Evo Pydantic 模型——HTTP 入参/出参形状。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EpisodeSignal = Literal["thumb_up", "thumb_down", "correction", "none"]
EpisodeType = Literal["reply", "correction", "feedback"]
EpisodeModule = Literal["assistant", "qa", "practice", "quick_query"]


class EpisodeFeedbackRequest(BaseModel):
    signal: Literal["thumb_up", "thumb_down"] = Field(..., description="反馈信号")


class EpisodeCorrectionRequest(BaseModel):
    correction_text: str = Field(..., min_length=1, max_length=4000, description="用户给出的纠正答案")


class EpisodeRecord(BaseModel):
    """对外暴露的简化 episode 视图，供 API 返回。"""

    id: int
    episode_type: EpisodeType
    module: EpisodeModule
    signal: EpisodeSignal
    compliance_tags: list[str] = Field(default_factory=list)
    parent_episode_id: int | None = None
