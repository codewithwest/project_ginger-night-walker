import pytest

from nightwalker.plan_execution import complete_step, initialise_plan, ready_steps, ready_steps_for_capture, start_step


def sample_plan():
    return initialise_plan({
        "version": 1,
        "subtasks": [
            {"subtask_id": "open", "objective": "Open homepage", "capability": "navigate", "depends_on": [], "success_criteria": [], "expected_output": "Page evidence"},
            {"subtask_id": "extract", "objective": "Extract products", "capability": "extract", "depends_on": ["open"], "success_criteria": [], "expected_output": "Product records"},
        ],
    })


def test_plan_steps_follow_dependencies_and_require_evidence():
    plan = sample_plan()
    assert [step["step_id"] for step in ready_steps(plan)] == ["open"]
    start_step(plan, "open")
    with pytest.raises(ValueError, match="evidence references"):
        complete_step(plan, "open", evidence_refs=[], result="Opened")
    complete_step(plan, "open", evidence_refs=["ev-home"], result="Homepage captured")
    assert [step["step_id"] for step in ready_steps(plan)] == ["extract"]
    start_step(plan, "extract")
    assert plan["steps"][0]["status"] == "completed"
    assert plan["steps"][1]["status"] == "in_progress"


def test_plan_cannot_complete_dependent_step_before_parent():
    plan = sample_plan()
    with pytest.raises(ValueError, match="dependencies"):
        complete_step(plan, "extract", evidence_refs=["ev-1"], result="Extracted")



def test_product_search_capture_does_not_advance_dependent_step_from_stale_page():
    plan = sample_plan()
    start_step(plan, "open")
    complete_step(plan, "open", evidence_refs=["ev-home"], result="Homepage captured")

    # The current browser capture belongs to "open". Although "extract" is
    # dependency-ready now, it must wait for its own browser execution/capture.
    assert [step["step_id"] for step in ready_steps(plan)] == ["extract"]
    assert ready_steps_for_capture(
        plan, "open", one_step_per_capture=True,
    ) == []
    assert [step["step_id"] for step in ready_steps_for_capture(
        plan, "extract", one_step_per_capture=True,
    )] == ["extract"]
