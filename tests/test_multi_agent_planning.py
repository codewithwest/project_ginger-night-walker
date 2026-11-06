import pytest
from pydantic import ValidationError

from nightwalker.a2a import A2ABus, A2AMessage, A2AMessageType
from nightwalker.agent_allocator import AgentAllocator
from nightwalker.planning_contracts import ExecutionBlueprint


def sample_blueprint():
    return ExecutionBlueprint.model_validate({
        "version": 1,
        "original_objective": "Research three competitors and compare their pricing",
        "interpretation": "Find three competitors and compare publicly listed prices.",
        "success_criteria": [
            {"criterion_id": "competitors", "description": "Identify three relevant competitors",
             "evidence_required": True, "acceptance_test": "Three distinct competitor sources are captured."},
            {"criterion_id": "pricing", "description": "Compare their public pricing",
             "evidence_required": True, "acceptance_test": "Each competitor has a price or an explicit unavailable marker."}
        ],
        "subtasks": [
            {"subtask_id": "discover", "objective": "Discover competitor sources", "capability": "navigate",
             "required_tools": ["browser_navigate"], "success_criteria": ["competitors"],
             "depends_on": [], "parallelizable": False, "expected_output": "Discovered competitor URLs"},
            {"subtask_id": "extract", "objective": "Extract competitor prices", "capability": "extract",
             "required_tools": ["browser_snapshot", "browser_extract"], "success_criteria": ["competitors", "pricing"],
             "depends_on": ["discover"], "parallelizable": True, "expected_output": "Evidence-linked pricing records"}
        ]
    })


def test_blueprint_preserves_goal_and_models_criteria_and_tools():
    blueprint = sample_blueprint()
    assert blueprint.original_objective.startswith("Research three")
    assert len(blueprint.success_criteria) == 2
    assert blueprint.subtasks[1].required_tools == ["browser_snapshot", "browser_extract"]


def test_blueprint_rejects_unknown_dependency_and_cycle():
    payload = sample_blueprint().model_dump(mode="json")
    payload["subtasks"][0]["depends_on"] = ["missing"]
    with pytest.raises(ValidationError):
        ExecutionBlueprint.model_validate(payload)

    payload = sample_blueprint().model_dump(mode="json")
    payload["subtasks"][0]["depends_on"] = ["extract"]
    with pytest.raises(ValidationError):
        ExecutionBlueprint.model_validate(payload)


def test_allocator_assigns_by_capability_and_emits_a2a_assignment():
    bus = A2ABus()
    assignments = AgentAllocator(bus).assign("task-1", sample_blueprint())
    assert assignments[0]["agent_id"] == "night-walker-navigator"
    assert assignments[1]["agent_id"] == "night-walker-extractor"
    messages = bus.history("task-1")
    assert len(messages) == 2
    assert messages[0]["message_type"] == "assignment"
    assert messages[1]["payload"]["required_tools"] == ["browser_snapshot", "browser_extract"]


def test_a2a_messages_are_task_scoped_and_restorable():
    bus = A2ABus()
    message = A2AMessage(
        parent_task_id="task-1", subtask_id="extract",
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
