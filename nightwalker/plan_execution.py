"""Persistent mission-plan execution ledger.

The planner proposes steps; the task manager owns transitions. A step is only
completed after the caller supplies concrete evidence references.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


TERMINAL = {"completed", "blocked", "failed", "skipped"}


def initialise_plan(blueprint: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for index, item in enumerate(blueprint.get("subtasks") or [], 1):
        steps.append({
            "step_id": str(item["subtask_id"]),
            "index": index,
            "objective": str(item.get("objective") or ""),
            "capability": str(item.get("capability") or ""),
            "depends_on": list(item.get("depends_on") or []),
            "success_criteria": list(item.get("success_criteria") or []),
            "expected_output": str(item.get("expected_output") or ""),
            "status": "pending",
            "attempts": 0,
            "evidence_refs": [],
            "result": None,
            "updated_at": datetime.now(UTC).isoformat(),
        })
    return {
        "version": int(blueprint.get("version") or 1),
        "status": "pending",
        "steps": steps,
        "history": [],
    }


def _event(plan: dict[str, Any], step: dict[str, Any], status: str, message: str) -> None:
    now = datetime.now(UTC).isoformat()
    step["status"] = status
    step["updated_at"] = now
    plan["status"] = (
        "completed" if plan.get("steps") and all(s["status"] == "completed" for s in plan["steps"])
        else "blocked" if any(s["status"] == "blocked" for s in plan["steps"])
        else "in_progress" if any(s["status"] == "in_progress" for s in plan["steps"])
        else "pending"
    )
    plan.setdefault("history", []).append({
        "at": now, "step_id": step["step_id"], "status": status,
        "message": str(message)[:500],
    })


def start_step(plan: dict[str, Any], step_id: str) -> dict[str, Any]:
    step = next((s for s in plan.get("steps", []) if s["step_id"] == step_id), None)
    if step is None:
        raise ValueError("Unknown planned step")
    if step["status"] in TERMINAL:
        return step
    by_id = {s["step_id"]: s for s in plan["steps"]}
    unmet = [dep for dep in step.get("depends_on", []) if by_id.get(dep, {}).get("status") != "completed"]
    if unmet:
        raise ValueError("Planned step dependencies are not verified: " + ", ".join(unmet))
    if step["status"] != "in_progress":
        step["attempts"] = int(step.get("attempts", 0)) + 1
        _event(plan, step, "in_progress", "Execution started")
    return step


def complete_step(
    plan: dict[str, Any], step_id: str, *, evidence_refs: list[str], result: str,
) -> dict[str, Any]:
    step = next((s for s in plan.get("steps", []) if s["step_id"] == step_id), None)
    if step is None:
        raise ValueError("Unknown planned step")
    if not evidence_refs:
        raise ValueError("A planned step cannot complete without evidence references")
    by_id = {s["step_id"]: s for s in plan["steps"]}
    unmet = [dep for dep in step.get("depends_on", []) if by_id.get(dep, {}).get("status") != "completed"]
    if unmet:
        raise ValueError("Planned step dependencies are not verified: " + ", ".join(unmet))
    if step.get("status") != "in_progress":
        raise ValueError("A planned step must be in progress before it can be completed")
    step["evidence_refs"] = list(dict.fromkeys([*step.get("evidence_refs", []), *evidence_refs]))
    step["result"] = str(result)[:1000]
    _event(plan, step, "completed", "Acceptance check passed with evidence: " + ", ".join(evidence_refs))
    return step


def fail_step(plan: dict[str, Any], step_id: str, message: str, *, blocked: bool = False) -> dict[str, Any]:
    step = next((s for s in plan.get("steps", []) if s["step_id"] == step_id), None)
    if step is None:
        raise ValueError("Unknown planned step")
    _event(plan, step, "blocked" if blocked else "failed", message)
    return step


def ready_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {s["step_id"]: s for s in plan.get("steps", [])}
    return [
        s for s in plan.get("steps", [])
        if s["status"] in {"pending", "ready", "in_progress"}
        and all(by_id.get(dep, {}).get("status") == "completed" for dep in s.get("depends_on", []))
    ]
