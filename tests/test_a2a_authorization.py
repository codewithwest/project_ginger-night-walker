import pytest

from nightwalker.a2a import A2AMessage, A2AMessageType
from nightwalker.task_manager import TaskManager


class StubOrchestrator:
    ollama = object()


def test_task_manager_rejects_unallocated_a2a_agent():
    manager = TaskManager(StubOrchestrator())
    manager.tasks["task-x"] = {
        "task_id": "task-x",
        "agent_assignments": [{"subtask_id": "s1", "agent_id": "night-walker-extractor", "capability": "extract"}],
        "logs": [], "progress_events": [], "status": "running",
    }
    message = A2AMessage(
        parent_task_id="task-x", subtask_id="s1",
        sender_agent_id="unassigned-agent", recipient_agent_id="night-walker-extractor",
        message_type=A2AMessageType.ASSISTANCE_REQUEST, capability="extract",
        objective="Please help extract a value",
    )
    with pytest.raises(ValueError, match="allocated agents"):
        manager.send_a2a_message("task-x", message)


def test_task_manager_rejects_a2a_capability_escalation():
    manager = TaskManager(StubOrchestrator())
    manager.tasks["task-x"] = {
        "task_id": "task-x",
        "agent_assignments": [
            {"subtask_id": "s1", "agent_id": "night-walker-navigator", "capability": "navigate"},
            {"subtask_id": "s2", "agent_id": "night-walker-extractor", "capability": "extract"},
        ],
        "logs": [], "progress_events": [], "status": "running",
    }
    message = A2AMessage(
        parent_task_id="task-x", subtask_id="s1",
        sender_agent_id="night-walker-navigator", recipient_agent_id="night-walker-extractor",
        message_type=A2AMessageType.ASSISTANCE_REQUEST, capability="navigate",
        objective="Please navigate to another site",
    )
    with pytest.raises(ValueError, match="capability"):
        manager.send_a2a_message("task-x", message)
