"""Small, closed output schemas; the model never chooses scope or executable tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect_window: Literal["current", "after_fixed_until"] = "current"
    impact_kind: Literal["neutral", "risk", "opportunity"] = "neutral"
    text: str = Field(min_length=1, max_length=600)
    section: Literal["relevance", "consequences", "actions"]
    evidence_ids: list[str] = Field(max_length=4)
    feature_ids: list[str] = Field(min_length=1, max_length=3)
    conditions: list[str] = Field(min_length=1, max_length=5)
    alternatives: list[str] = Field(min_length=1, max_length=3)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposals: list[Proposal] = Field(max_length=6)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, le=5)
    status: Literal["conditional", "unsupported", "conflicting"]
    reason: str = Field(min_length=1, max_length=200)


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict] = Field(max_length=6)
