from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from nightwalker.contracts import Capability


class DecisionStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"


class PlannedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(min_length=1, max_length=40)
    capability: Capability
    objective: str = Field(min_length=1, max_length=500)


class DecisionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: DecisionStatus
    summary: str = Field(min_length=1, max_length=500)
    site_name: str | None = Field(default=None, max_length=200)
    target_url: HttpUrl | None = None
    clarification_question: str | None = Field(default=None, max_length=500)
    steps: list[PlannedStep] = Field(default_factory=list, max_length=8)

    @classmethod
    def validate_plan(cls, value: DecisionPlan) -> DecisionPlan:
        if value.status == DecisionStatus.READY:
            if not value.steps:
                raise ValueError("A ready plan must contain at least one step")
            if not value.target_url:
                raise ValueError("A ready plan must include a verified-by-user or inferred target URL")
        if value.status == DecisionStatus.NEEDS_CLARIFICATION and not value.clarification_question:
            raise ValueError("A clarification plan must include a question")
        if value.status != DecisionStatus.READY and value.steps:
            raise ValueError("Only ready plans may contain execution steps")
        return value
