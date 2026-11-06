import pytest
from pydantic import ValidationError

from nightwalker.decision_contracts import (
    DecisionPlan,
    DecisionStatus,
    PlannedStep,
    normalise_decision_plan,
)


def ready_plan(**overrides):
    values = {
        "status": DecisionStatus.READY,
        "summary": "Inspect the product catalogue",
        "site_name": "Acme",
        "target_url": "https://example.com",
        "steps": [
            PlannedStep(
                step_id="step-1",
                capability="navigate",
                objective="Locate the product catalogue",
            )
        ],
    }
    values.update(overrides)
    return DecisionPlan(**values)


def test_ready_plan_requires_steps_and_destination():
    plan = ready_plan()
    assert DecisionPlan.validate_plan(plan) == plan


def test_clarification_plan_requires_question_and_no_steps():
    plan = DecisionPlan(
        status=DecisionStatus.NEEDS_CLARIFICATION,
        summary="The target site is ambiguous",
        clarification_question="Which Acme website do you mean?",
    )
    assert DecisionPlan.validate_plan(plan) == plan


def test_ready_plan_without_steps_is_rejected():
    plan = ready_plan(steps=[])
    with pytest.raises(ValueError, match="at least one step"):
        DecisionPlan.validate_plan(plan)


def test_ready_plan_without_url_is_rejected():
    plan = ready_plan(target_url=None)
    with pytest.raises(ValueError, match="target URL"):
        DecisionPlan.validate_plan(plan)


def test_clarification_plan_without_question_gets_safe_fallback():
    plan = DecisionPlan(status="needs_clarification", summary="Need more detail")

    validated = DecisionPlan.validate_plan(plan)

    assert validated.status == DecisionStatus.NEEDS_CLARIFICATION
    assert validated.clarification_question
    assert "essential detail" in validated.clarification_question


def test_unknown_capability_is_rejected():
    with pytest.raises(ValidationError):
        PlannedStep(step_id="step-1", capability="admin", objective="Do anything")



def test_normalise_repairs_string_url_and_dict_steps_before_serialization():
    malformed = DecisionPlan.model_construct(
        status=DecisionStatus.READY,
        summary="Search the public web",
        site_name="Web search",
        target_url="https://www.bing.com/search?q=ginger",
        clarification_question=None,
        steps=[
            {
                "step_id": "open",
                "capability": "navigate",
                "objective": "Open a discovered public source.",
            },
            {
                "step_id": "capture",
                "capability": "extract",
                "objective": "Capture relevant source details.",
            },
        ],
    )

    repaired = normalise_decision_plan(malformed)
    payload = repaired.model_dump(mode="json")

    assert str(repaired.target_url).startswith("https://www.bing.com/")
    assert all(isinstance(step, PlannedStep) for step in repaired.steps)
    assert payload["steps"][0]["step_id"] == "open"
