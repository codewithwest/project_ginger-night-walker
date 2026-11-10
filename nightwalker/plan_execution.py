"""Persistent mission-plan execution ledger.

The planner proposes steps; the task manager owns transitions. A step is only
completed after the caller supplies concrete evidence references.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


TERMINAL = {"completed", "blocked", "failed", "skipped"}

# Canonical step IDs the runtime knows how to execute and verify.
# The model-generated blueprint must only use IDs from this set.
_QA_CANONICAL_STEP_IDS = {
    "qa-discover", "qa-availability", "qa-content",
    "qa-navigation", "qa-crawl", "qa-validate", "qa-synthesise",
}
_SEO_CANONICAL_STEP_IDS = {
    "seo-discover", "seo-technical", "seo-page",
    "seo-links", "seo-crawl", "seo-validate", "seo-synthesise",
}
_COMBINED_CANONICAL_STEP_IDS = {
    f"combined-{s}" for s in _QA_CANONICAL_STEP_IDS | _SEO_CANONICAL_STEP_IDS
} | {"combined-audit-crawl"}


def _canonical_step_id(step_id: str) -> str:
    """Collapse a model-generated sub-suffixed step ID to its canonical form.

    The goal-analyst sometimes produces step IDs like ``qa-validate-name``,
    ``qa-validate-image`` or ``seo-validate-links`` when it is asked to treat
    each verification dimension as a separate criterion.  Those IDs pass the
    acyclic-graph validator in ``ExecutionBlueprint`` but break the runtime
    because ``start_step`` / ``complete_step`` only recognise the stable
    canonical IDs.

    This function maps any such extended ID back to the nearest canonical parent
    so that the plan can be executed without crashes.
    """
    all_canonical = (
        _QA_CANONICAL_STEP_IDS
        | _SEO_CANONICAL_STEP_IDS
        | _COMBINED_CANONICAL_STEP_IDS
    )
    if step_id in all_canonical:
        return step_id
    for canonical in sorted(all_canonical, key=len, reverse=True):
        if step_id.startswith(canonical + "-"):
            return canonical
    return step_id


def normalise_blueprint_subtasks(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Rewrite sub-suffixed step IDs to their canonical counterparts.

    When the model returns a blueprint with IDs like ``qa-validate-name`` or
    ``qa-validate-image``, this function:

    1. Builds a remapping table (old-id → canonical-id).
    2. Merges duplicate canonical steps (combining ``depends_on`` and
       ``success_criteria``).
    3. Rewrites all ``depends_on`` lists in every subtask.

    The returned blueprint dict is a new shallow copy; the original is not
    mutated.
    """
    subtasks = list(blueprint.get("subtasks") or [])
    if not subtasks:
        return blueprint

    # Build remapping: original ID → canonical ID
    remap: dict[str, str] = {}
    for item in subtasks:
        original = str(item.get("subtask_id") or "")
        canonical = _canonical_step_id(original)
        if original != canonical:
            remap[original] = canonical

    if not remap:
        return blueprint  # Nothing to fix

    # Merge sub-steps into canonical steps.  Preserve the first occurrence of
    # each canonical ID as the template; additional occurrences are merged in.
    seen: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    for item in subtasks:
        original_id = str(item.get("subtask_id") or "")
        canonical_id = remap.get(original_id, original_id)
        rewritten = {
            **item,
            "subtask_id": canonical_id,
            "depends_on": [remap.get(d, d) for d in (item.get("depends_on") or [])],
            "success_criteria": list(item.get("success_criteria") or []),
        }
        if canonical_id in seen:
            existing = seen[canonical_id]
            # Extend deps and criteria, keeping uniqueness.
            existing_deps = list(existing.get("depends_on") or [])
            for dep in rewritten.get("depends_on", []):
                if dep not in existing_deps and dep != canonical_id:
                    existing_deps.append(dep)
            existing["depends_on"] = existing_deps
            existing_criteria = list(existing.get("success_criteria") or [])
            for crit in rewritten.get("success_criteria", []):
                if crit not in existing_criteria:
                    existing_criteria.append(crit)
            existing["success_criteria"] = existing_criteria
        else:
            seen[canonical_id] = rewritten
            merged.append(rewritten)

    # Rewrite depends_on in all merged steps (a canonical step must not depend
    # on itself after merging).
    for item in merged:
        item["depends_on"] = [
            d for d in item.get("depends_on", [])
            if d != item["subtask_id"]
        ]

    return {**blueprint, "subtasks": merged}


def initialise_plan(blueprint: dict[str, Any]) -> dict[str, Any]:
    blueprint = normalise_blueprint_subtasks(blueprint)
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


def reset_retryable_steps(plan: dict[str, Any]) -> list[str]:
    """Requeue blocked/failed steps only when every dependency is verified."""
    by_id = {step["step_id"]: step for step in plan.get("steps", [])}
    reset = []
    for step in plan.get("steps", []):
        if step.get("status") not in {"blocked", "failed"}:
            continue
        dependencies = step.get("depends_on") or []
        if not all(by_id.get(dep, {}).get("status") == "completed" for dep in dependencies):
            continue
        step["status"] = "pending"
        step["result"] = None
        step["evidence_refs"] = []
        step["updated_at"] = datetime.now(UTC).isoformat()
        reset.append(step["step_id"])
        plan.setdefault("history", []).append({
            "at": step["updated_at"], "step_id": step["step_id"],
            "status": "pending",
            "message": "Retry requeued this step because all dependencies remain verified.",
        })
    if reset:
        plan["status"] = "pending"
    return reset


def ready_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {s["step_id"]: s for s in plan.get("steps", [])}
    return [
        s for s in plan.get("steps", [])
        if s["status"] in {"pending", "ready", "in_progress"}
        and all(by_id.get(dep, {}).get("status") == "completed" for dep in s.get("depends_on", []))
    ]


def ready_steps_for_capture(
    plan: dict[str, Any], active_step_id: str, *, one_step_per_capture: bool = False,
) -> list[dict[str, Any]]:
    """Return steps that may be verified from this browser capture.

    Most read-only extraction steps can share one captured page. A focused
    product-search journey cannot: navigation, query submission and result
    extraction each require a distinct browser action/capture. In that mode,
    never advance a dependent step using evidence captured before that step ran.
    """
    ready = ready_steps(plan)
    if not one_step_per_capture:
        return ready
    return [step for step in ready if step.get("step_id") == active_step_id]
