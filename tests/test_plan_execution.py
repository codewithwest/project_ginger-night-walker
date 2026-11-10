import pytest

from nightwalker.plan_execution import complete_step, fail_step, initialise_decision_plan, initialise_plan, ready_steps, ready_steps_for_capture, reset_retryable_steps, start_step, validate_plan_dependencies


def sample_plan():
    return initialise_plan({
        "version": 1,
        "subtasks": [
            {"subtask_id": "open", "objective": "Open homepage", "capability": "navigate", "depends_on": [], "success_criteria": [], "expected_output": "Page evidence"},
            {"subtask_id": "extract", "objective": "Extract products", "capability": "extract", "depends_on": ["open"], "success_criteria": [], "expected_output": "Product records"},
        ],
    })


def test_plan_steps_execute_in_authored_order_and_require_evidence():
    plan = sample_plan()
    assert [step["step_id"] for step in ready_steps(plan)] == ["open", "extract"]
    start_step(plan, "open")
    with pytest.raises(ValueError, match="evidence references"):
        complete_step(plan, "open", evidence_refs=[], result="Opened")
    complete_step(plan, "open", evidence_refs=["ev-home"], result="Homepage captured")
    assert [step["step_id"] for step in ready_steps(plan)] == ["extract"]
    start_step(plan, "extract")
    complete_step(plan, "extract", evidence_refs=["ev-products"], result="Extracted")
    assert plan["steps"][0]["status"] == "completed"
    assert plan["steps"][1]["status"] == "completed"
    assert validate_plan_dependencies(plan) == []


def test_dependency_validation_is_deferred_until_after_execution():
    plan = sample_plan()
    start_step(plan, "extract")
    complete_step(plan, "extract", evidence_refs=["ev-1"], result="Executed with evidence")
    issues = validate_plan_dependencies(plan)

    assert issues == [{"step_id": "extract", "unmet_dependencies": ["open"]}]
    assert plan["dependency_validation"]["status"] == "failed"
    assert plan["status"] == "blocked"



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


def test_retry_requeues_only_blocked_steps_with_verified_dependencies():
    plan = sample_plan()
    start_step(plan, "open")
    complete_step(plan, "open", evidence_refs=["ev-home"], result="Homepage captured")
    start_step(plan, "extract")
    fail_step(plan, "extract", "Product card not found", blocked=True)

    reset = reset_retryable_steps(plan)

    assert reset == ["extract"]
    assert plan["steps"][0]["status"] == "completed"
    assert plan["steps"][1]["status"] == "pending"
    assert plan["steps"][1]["result"] is None
    assert [step["step_id"] for step in ready_steps(plan)] == ["extract"]


def test_retry_does_not_requeue_step_with_unverified_dependency():
    plan = sample_plan()
    start_step(plan, "open")
    fail_step(plan, "open", "Navigation failed", blocked=True)

    assert reset_retryable_steps(plan) == []
    assert plan["steps"][0]["status"] == "blocked"
    assert plan["steps"][0]["result"] == "Navigation failed"
    assert plan["steps"][1]["status"] == "pending"
    # A later step can still execute; dependency integrity is reported afterwards.
    assert [step["step_id"] for step in ready_steps(plan)] == ["extract"]



def test_execution_plan_preserves_decider_sequence_without_blueprint_reordering():
    decider_steps = [
        {"step_id": "inspect-home", "objective": "Inspect the homepage", "capability": "navigate"},
        {"step_id": "find-category", "objective": "Find the furniture categories", "capability": "interact"},
        {"step_id": "extract-categories", "objective": "Extract the category names", "capability": "extract"},
    ]
    # A separate goal analyst may produce a different task breakdown. It is
    # intentionally not an input to this constructor.
    plan = initialise_decision_plan(decider_steps)

    assert [step["step_id"] for step in plan["steps"]] == [
        "inspect-home", "find-category", "extract-categories",
    ]
    assert [step["objective"] for step in plan["steps"]] == [
        "Inspect the homepage",
        "Find the furniture categories",
        "Extract the category names",
    ]
    assert [step["depends_on"] for step in plan["steps"]] == [
        [], ["inspect-home"], ["find-category"],
    ]
    assert plan["source"] == "decider_plan"
    assert plan["ordering"] == "decider_authored"


def test_decider_execution_plan_rejects_duplicate_step_ids():
    with pytest.raises(ValueError, match="duplicate step ID"):
        initialise_decision_plan([
            {"step_id": "same", "objective": "First", "capability": "navigate"},
            {"step_id": "same", "objective": "Second", "capability": "extract"},
        ])
