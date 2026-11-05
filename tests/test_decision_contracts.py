import pytest
from pydantic import ValidationError

from nightwalker.decision_contracts import (
    DecisionPlan,
    DecisionStatus,
    PlannedStep,
)


def test_ready_plan_requires_steps():
    plan = DecisionPlan(
        status=DecisionStatus.READY,
        summary="Inspect the product catalogue",
        site_name="Acme",
        steps=[
            PlannedStep(
                step_id="step-1",
                capability="navigate",
                objective="Locate the product catalogue",
            )
        ],
    )
    assert DecisionPlan.validate_plan(plan) == plan


def test_clarification_plan_requires_question_and_no_steps():
    plan = DecisionPlan(
        status=DecisionStatus.NEEDS_CLARIFICATION,
        summary="The target site is ambiguous",
        clarification_question="Which Acme website do you mean?",
    )
    assert DecisionPlan.validate_plan(plan) == plan


def test_ready_plan_without_steps_is_rejected():
    plan = DecisionPlan(status="ready", summary="No actionable steps")
    with pytest.raises(ValueError, match="at least one step"):
        DecisionPlan.validate_plan(plan)


def test_clarification_plan_without_question_is_rejected():
    plan = DecisionPlan(status="needs_clarification", summary="Need more detail")
    with pytest.raises(ValueError, match="include a question"):
        DecisionPlan.validate_plan(plan)


def test_unknown_capability_is_rejected():
    with pytest.raises(ValidationError):
        PlannedStep(step_id="step-1", capability="admin", objective="Do anything")
