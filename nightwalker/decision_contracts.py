from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from nightwalker.contracts import Capability


class DecisionStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"


class PlannedStep(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    step_id: str = Field(min_length=1, max_length=40)
    capability: Capability
    objective: str = Field(min_length=1, max_length=500)


class DecisionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
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
                raise ValueError(
                    "A ready plan must include a verified-by-user or inferred target URL"
                )
        if (
            value.status == DecisionStatus.NEEDS_CLARIFICATION
            and (
                not value.clarification_question
                or not value.clarification_question.strip()
            )
        ):
            # Some local models emit the clarification status but omit its question.
            # Keep the task recoverable and let the operator supply the missing detail.
                value.clarification_question = (
                    "What essential detail should I use to narrow the task?"
                )
        if value.status != DecisionStatus.READY and value.steps:
            raise ValueError("Only ready plans may contain execution steps")
        return value



def normalise_decision_plan(value: DecisionPlan | dict) -> DecisionPlan:
    """Rebuild a plan from plain field values before validation or serialization.

    Older snapshots and some model/tool adapters can leave nested Pydantic fields
    as dictionaries or URLs as strings. Never call model_dump on that malformed
    instance: extract raw fields and let Pydantic construct fresh typed objects.
    """
    if isinstance(value, dict):
        raw = value
    else:
        raw = {
            "status": getattr(value, "status", None),
            "summary": getattr(value, "summary", ""),
            "site_name": getattr(value, "site_name", None),
            "target_url": getattr(value, "target_url", None),
            "clarification_question": getattr(value, "clarification_question", None),
            "steps": getattr(value, "steps", []) or [],
        }

    steps = []
    for step in raw.get("steps") or []:
        if isinstance(step, PlannedStep):
            steps.append({
                "step_id": step.step_id,
                "capability": step.capability,
                "objective": step.objective,
            })
        else:
            steps.append(step)

    target_url = raw.get("target_url")
    return DecisionPlan.model_validate({
        "status": raw.get("status"),
        "summary": raw.get("summary", ""),
        "site_name": raw.get("site_name"),
        "target_url": str(target_url) if target_url is not None else None,
        "clarification_question": raw.get("clarification_question"),
        "steps": steps,
    })
