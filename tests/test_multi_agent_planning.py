import pytest
from pydantic import ValidationError

from nightwalker.a2a import A2ABus, A2AMessage, A2AMessageType
from nightwalker.agent_allocator import AgentAllocator
from nightwalker.decision_contracts import PlannedStep
from nightwalker.planning_contracts import ExecutionBlueprint


def sample_blueprint():
    return ExecutionBlueprint.model_validate({
        "version": 1,
        "original_objective": "Research three competitors and compare their pricing",
        "interpretation": "Find three competitors and compare publicly listed prices.",
        "research_queries": ["top furniture retailers South Africa", "Weylandts competitors furniture prices South Africa"],
        "success_criteria": [
            {"criterion_id": "competitors", "description": "Identify three relevant competitors",
             "evidence_required": True, "acceptance_test": "Three distinct competitor sources are captured."},
            {"criterion_id": "pricing", "description": "Compare their public pricing",
             "evidence_required": True, "acceptance_test": "Each competitor has a price or an explicit unavailable marker."}
        ],
    })


def test_goal_analysis_contains_criteria_but_no_execution_plan():
    blueprint = sample_blueprint()
    assert blueprint.original_objective.startswith("Research three")
    assert len(blueprint.success_criteria) == 2
    assert blueprint.research_queries[0] == "top furniture retailers South Africa"
    assert "subtasks" not in blueprint.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ExecutionBlueprint.model_validate({
            **blueprint.model_dump(mode="json"),
            "subtasks": [{"subtask_id": "invented-second-plan"}],
        })


def test_allocator_assigns_only_decider_steps_and_emits_a2a_assignments():
    bus = A2ABus()
    blueprint = sample_blueprint()
    steps = [
        PlannedStep(step_id="decider-discover", objective="Discover competitor sources", capability="navigate"),
        PlannedStep(step_id="decider-prices", objective="Extract competitor prices", capability="extract"),
    ]
    assignments = AgentAllocator(bus).assign("task-1", steps, blueprint.success_criteria)
    assert [item["subtask_id"] for item in assignments] == ["decider-discover", "decider-prices"]
    assert assignments[0]["agent_id"] == "night-walker-navigator"
    assert assignments[1]["agent_id"] == "night-walker-extractor"
    assert assignments[0]["verifier_agent_id"] == "night-walker-verifier"
    messages = bus.history("task-1")
    assert len(messages) == 2
    assert messages[0]["message_type"] == "assignment"
    assert messages[1]["payload"]["plan_source"] == "decider_plan"
    assert messages[1]["payload"]["success_criteria"][1]["criterion_id"] == "pricing"


def test_a2a_messages_are_task_scoped_and_restorable():
    bus = A2ABus()
    message = A2AMessage(
        parent_task_id="task-1", subtask_id="decider-prices",
        sender_agent_id="night-walker-navigator",
        recipient_agent_id="night-walker-extractor",
        message_type=A2AMessageType.ASSISTANCE_REQUEST,
        capability="extract", objective="Please extract the price from this captured page.",
        evidence_refs=["ev-123"],
    )
    bus.send(message)
    restored = A2ABus()
    restored.restore("task-1", bus.history("task-1"))
    assert restored.history("task-1")[0]["evidence_refs"] == ["ev-123"]
