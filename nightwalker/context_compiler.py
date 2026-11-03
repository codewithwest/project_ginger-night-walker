from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nightwalker.contracts import AgentTask


@dataclass(frozen=True)
class CompiledContext:
    task_id: str
    capability: str
    objective: str
    input_data: dict[str, Any]
    requirements: dict[str, Any]
    permissions: frozenset[str]


class ContextCompiler:
    INPUT_ALLOWLIST = {
        "navigate": {"url", "destination"},
        "interact": {"target_selector", "action", "value"},
        "extract": {"dom_snapshot", "url"},
        "recover": {"failure_code", "error_summary", "current_url"},
    }
    REQUIREMENT_ALLOWLIST = {
        "navigate": {"wait_until", "timeout_ms"},
        "interact": {"expected_result"},
        "extract": {"fields", "minimum_results", "output_schema"},
        "recover": {"recovery_goal", "max_attempts"},
    }

    def compile(self, task: AgentTask) -> CompiledContext:
        capability = task.capability.value
        allowed_inputs = self.INPUT_ALLOWLIST[capability]
        allowed_requirements = self.REQUIREMENT_ALLOWLIST[capability]
        return CompiledContext(
            task_id=task.task_id,
            capability=capability,
            objective=task.objective,
            input_data={k: v for k, v in task.input_data.items() if k in allowed_inputs},
            requirements={k: v for k, v in task.requirements.items() if k in allowed_requirements},
            permissions=frozenset(task.permissions),
        )
