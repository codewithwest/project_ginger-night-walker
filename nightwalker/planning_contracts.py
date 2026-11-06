"""Goal-first execution blueprint contracts.

The model proposes a plan; Python validates dependencies and assigns capabilities.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nightwalker.contracts import Capability


class SuccessCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    evidence_required: bool = True
    acceptance_test: str = Field(min_length=1, max_length=500)


class PlannedSubtask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtask_id: str = Field(min_length=1, max_length=40)
    objective: str = Field(min_length=1, max_length=500)
    capability: Capability
    required_tools: list[str] = Field(default_factory=list, max_length=12)
    success_criteria: list[str] = Field(default_factory=list, max_length=8)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    parallelizable: bool = False
    expected_output: str = Field(min_length=1, max_length=500)


class ExecutionBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    original_objective: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(min_length=1, max_length=1000)
    success_criteria: list[SuccessCriterion] = Field(min_length=1, max_length=12)
    subtasks: list[PlannedSubtask] = Field(min_length=1, max_length=20)
    completion_policy: str = Field(default="all_criteria_verified", max_length=100)
    research_queries: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_graph(self):
        criterion_ids = {item.criterion_id for item in self.success_criteria}
        subtask_ids = {item.subtask_id for item in self.subtasks}
        if len(subtask_ids) != len(self.subtasks):
            raise ValueError("Subtask IDs must be unique")
        if len(criterion_ids) != len(self.success_criteria):
            raise ValueError("Success criterion IDs must be unique")
        for task in self.subtasks:
            unknown_criteria = set(task.success_criteria) - criterion_ids
            unknown_dependencies = set(task.depends_on) - subtask_ids
            if unknown_criteria:
                raise ValueError(f"Unknown success criteria: {sorted(unknown_criteria)}")
            if unknown_dependencies:
                raise ValueError(f"Unknown subtask dependencies: {sorted(unknown_dependencies)}")
            if task.subtask_id in task.depends_on:
                raise ValueError("A subtask cannot depend on itself")
        # Validate acyclic dependencies before the plan is accepted.
        graph = {task.subtask_id: set(task.depends_on) for task in self.subtasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str):
            if node in visiting:
                raise ValueError("Subtask dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        return self
