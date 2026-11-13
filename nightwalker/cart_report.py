"""Cart QA report formatting from the Decider-authored execution plan.

This module formats the plan as recorded. It does not create steps, infer
completion, or reconcile criteria from step names.
"""
from __future__ import annotations

from typing import Any


def _steps(execution_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(execution_plan, dict):
        return []
    return [step for step in execution_plan.get("steps", []) if isinstance(step, dict)]


def build_cart_journey_report(
    objective: str,
    execution_plan: dict[str, Any] | None,
    *,
    out_of_stock: bool = False,
) -> str:
    """Build a markdown mission report from verified plan-step results."""
    steps = _steps(execution_plan)
    lines = [
        "# Cart journey report",
        "",
        f"**Objective:** {objective.strip() or 'Cart mission'}",
        "",
        "## Step-by-step findings",
        "",
    ]
    if not steps:
        lines.append("No authorised cart plan steps were recorded.")
        lines.append("")
    for index, step in enumerate(steps, start=1):
        step_id = str(step.get("step_id") or f"step-{index}")
        objective_text = str(step.get("objective") or step_id).strip()
        status = str(step.get("status") or "pending").replace("_", " ")
        result = str(step.get("result") or "").strip()
        evidence = [
            str(ref) for ref in (step.get("evidence_refs") or []) if str(ref).strip()
        ]
        lines.append(f"### {index}. {objective_text}")
        lines.append("")
        lines.append(f"- **Step:** `{step_id}`")
        lines.append(f"- **Status:** {status}")
        if result:
            lines.append(f"- **Finding:** {result}")
        else:
            lines.append("- **Finding:** No verified finding was recorded for this step.")
        if evidence:
            lines.append(f"- **Evidence:** {', '.join(evidence[:8])}")
        lines.append("")

    completed = [step for step in steps if step.get("status") == "completed"]
    blocked = [
        step for step in steps
        if step.get("status") in {"blocked", "failed"}
    ]
    lines.append("## Outcome")
    lines.append("")
    if out_of_stock:
        lines.append(
            "The product page reported an out-of-stock / unavailable state. "
            "Add, cart-line verification and removal were treated as stock outcomes, not browser failures."
        )
    elif blocked:
        lines.append(
            f"{len(completed)} of {len(steps)} cart steps verified. "
            "Blocked or failed steps are listed above with their findings."
        )
    elif steps and len(completed) == len(steps):
        lines.append(
            "All authorised steps in the requested shopping journey were verified."
        )
    else:
        lines.append(
            f"{len(completed)} of {len(steps)} cart steps verified before the mission ended."
        )
    lines.append("")
    lines.append("## Checkout")
    lines.append("")
    lines.append(
        "The report reflects the checkout-page navigation and verification steps, if requested. "
        "No order is placed and no payment is submitted unless that is explicitly authorised."
    )
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def cart_limitations_from_plan(
    execution_plan: dict[str, Any] | None,
    goal_state: dict[str, Any] | None = None,
) -> list[str]:
    """Cart-specific limitations only; never crawl-coverage text."""
    limitations: list[str] = []
    for step in _steps(execution_plan):
        if step.get("status") in {"blocked", "failed"}:
            label = str(step.get("step_id") or "step")
            detail = str(step.get("result") or step.get("error") or "not verified")[:300]
            limitations.append(f"{label}: {detail}")
    remaining = list((goal_state or {}).get("remaining") or [])
    if remaining:
        limitations.append(
            "Success criteria still lacking directly quoted evidence: " + "; ".join(remaining)
        )
    return limitations
