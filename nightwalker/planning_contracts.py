"""Goal-analysis contracts. The Decider owns all executable steps and dependencies."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

class SuccessCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    evidence_required: bool = True
    acceptance_test: str = Field(min_length=1, max_length=500)


class ExecutionBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    original_objective: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(min_length=1, max_length=1000)
    success_criteria: list[SuccessCriterion] = Field(min_length=1, max_length=12)
    completion_policy: str = Field(default="all_criteria_verified", max_length=100)
    research_queries: list[str] = Field(default_factory=list, max_length=8)

