from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from nightwalker.contracts import AgentTask
from nightwalker.working_memory import WorkingMemory


@dataclass(frozen=True)
class CompiledContext:
    task_id: str
    capability: str
    objective: str
    input_data: dict[str, Any]
    requirements: dict[str, Any]
    permissions: frozenset[str]


class ContextCompiler:
    def __init__(self) -> None:
        self.working_memory = WorkingMemory(max_chars=6000, max_items=40)

    INPUT_ALLOWLIST: ClassVar[dict[str, set[str]]] = {
        "navigate": {"url", "destination"},
        "interact": {"target_selector", "action", "value"},
        "extract": {"dom_snapshot", "url"},
        "recover": {"failure_code", "error_summary", "current_url"},
    }
    REQUIREMENT_ALLOWLIST: ClassVar[dict[str, set[str]]] = {
        "navigate": {"wait_until", "timeout_ms"},
        "interact": {"expected_result"},
        "extract": {"fields", "minimum_results", "output_schema"},
        "recover": {"recovery_goal", "max_attempts"},
    }

    def compile(self, task: AgentTask) -> CompiledContext:
        capability = task.capability.value
        allowed_inputs = self.INPUT_ALLOWLIST[capability]
        allowed_requirements = self.REQUIREMENT_ALLOWLIST[capability]
        input_data = self.working_memory.compact_value(
            {k: v for k, v in task.input_data.items() if k in allowed_inputs}
        )
        requirements = self.working_memory.compact_value(
            {k: v for k, v in task.requirements.items() if k in allowed_requirements}
        )
        if task.target_url and capability in {"navigate", "extract"}:
            input_data["url"] = str(task.target_url)
        return CompiledContext(
            task_id=task.task_id,
            capability=capability,
            objective=task.objective,
            input_data=input_data,
            requirements=requirements,
            # Permissions are deliberately absent until a trusted broker issues task-scoped grants.
            permissions=frozenset(),
        )
