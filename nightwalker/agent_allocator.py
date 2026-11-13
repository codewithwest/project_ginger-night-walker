"""Deterministic capability allocator; model plans never choose permissions."""
from __future__ import annotations

from typing import Any

from nightwalker.a2a import A2ABus, A2AMessage, A2AMessageType
from nightwalker.decision_contracts import PlannedStep
from nightwalker.planning_contracts import SuccessCriterion

VERIFIER_AGENT_ID = "night-walker-verifier"

CAPABILITY_AGENTS = {
    "navigate": "night-walker-navigator",
    "interact": "night-walker-interactor",
    "extract": "night-walker-extractor",
    "recover": "night-walker-recovery",
}


class AgentAllocator:
    def __init__(self, bus: A2ABus | None = None) -> None:
        self.bus = bus or A2ABus()

    def assign(
        self,
        task_id: str,
        steps: list[PlannedStep],
        success_criteria: list[SuccessCriterion] | None = None,
    ) -> list[dict[str, Any]]:
        """Assign agents to Decider-authored steps; never create a second plan."""
        criteria_context = [
            {
                "criterion_id": item.criterion_id,
                "description": item.description,
                "acceptance_test": item.acceptance_test,
                "evidence_required": item.evidence_required,
            }
            for item in (success_criteria or [])
        ]
        assignments = []
        for step in steps:
            capability = step.capability
            agent_id = CAPABILITY_AGENTS[capability.value]
            message = self.bus.send(A2AMessage(
                parent_task_id=task_id,
                subtask_id=step.step_id,
                sender_agent_id="night-walker-orchestrator",
                recipient_agent_id=agent_id,
                message_type=A2AMessageType.ASSIGNMENT,
                capability=capability,
                objective=step.objective,
                payload={
                    "success_criteria": criteria_context,
                    "plan_source": "decider_plan",
                },
            ))
            assignments.append({
                "subtask_id": step.step_id,
                "agent_id": agent_id,
                "verifier_agent_id": VERIFIER_AGENT_ID,
                "capability": capability.value,
                "status": "assigned",
                "message_id": message.message_id,
            })
        return assignments
