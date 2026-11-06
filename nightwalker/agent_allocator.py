"""Deterministic capability allocator; model plans never choose permissions."""
from __future__ import annotations

from nightwalker.a2a import A2ABus, A2AMessage, A2AMessageType
from nightwalker.planning_contracts import ExecutionBlueprint
from typing import Any


CAPABILITY_AGENTS = {
    "navigate": "night-walker-navigator",
    "interact": "night-walker-interactor",
    "extract": "night-walker-extractor",
    "recover": "night-walker-recovery",
}


class AgentAllocator:
    def __init__(self, bus: A2ABus | None = None) -> None:
        self.bus = bus or A2ABus()

    def assign(self, task_id: str, blueprint: ExecutionBlueprint) -> list[dict[str, Any]]:
        assignments = []
        for subtask in blueprint.subtasks:
            agent_id = CAPABILITY_AGENTS[subtask.capability.value]
            message = self.bus.send(A2AMessage(
                parent_task_id=task_id,
                subtask_id=subtask.subtask_id,
                sender_agent_id="night-walker-orchestrator",
                recipient_agent_id=agent_id,
                message_type=A2AMessageType.ASSIGNMENT,
                capability=subtask.capability,
                objective=subtask.objective,
                payload={
                    "required_tools": subtask.required_tools,
                    "success_criteria": subtask.success_criteria,
                    "depends_on": subtask.depends_on,
                    "parallelizable": subtask.parallelizable,
                    "expected_output": subtask.expected_output,
                },
            ))
            assignments.append({
                "subtask_id": subtask.subtask_id,
                "agent_id": agent_id,
                "capability": subtask.capability.value,
                "status": "assigned",
                "message_id": message.message_id,
            })
        return assignments
