"""Goal-analysis contracts. The Decider owns all executable steps and dependencies."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

class PlannedSubtask(BaseModel):
    """Legacy workflow-plan shape retained for compatibility outside goal analysis."""
    model_config = ConfigDict(extra="forbid")
    subtask_id: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=1000)
    capability: str = Field(min_length=1, max_length=40)
    required_tools: list[str] = Field(default_factory=list, max_length=12)
    success_criteria: list[str] = Field(default_factory=list, max_length=12)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    expected_output: str = Field(default="", max_length=1000)


class SuccessCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    evidence_required: bool = True
    acceptance_test: str = Field(min_length=1, max_length=500)


class ExecutionBlueprint(BaseModel):
    """Goal-analysis envelope. Executable steps belong to the Decider, not here."""
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    original_objective: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(min_length=1, max_length=1000)
    success_criteria: list[SuccessCriterion] = Field(min_length=1, max_length=12)
    completion_policy: str = Field(default="all_criteria_verified", max_length=100)
    research_queries: list[str] = Field(default_factory=list, max_length=8)


class WorkflowPlanTemplate(BaseModel):
    """Deterministic executable template for specialised QA journeys.

    Used by cart/product-search coercion and legacy migration. This is not a
    second Decider plan; it only supplies canonical step IDs and capabilities.
    """
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    original_objective: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(min_length=1, max_length=1000)
    success_criteria: list[SuccessCriterion] = Field(min_length=1, max_length=20)
    subtasks: list[PlannedSubtask] = Field(default_factory=list, max_length=20)
    completion_policy: str = Field(default="all_criteria_verified", max_length=100)
    research_queries: list[str] = Field(default_factory=list, max_length=8)

