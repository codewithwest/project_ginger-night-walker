"""Deterministic cart-journey final report and goal reconciliation.

Cart missions are not site crawls. Their answer must read as a presentable
step-by-step breakdown of what the browser verified, without SEO/QA crawl
boilerplate.
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
            "All authorised cart steps completed. The journey finished without opening or submitting checkout."
        )
    else:
        lines.append(
            f"{len(completed)} of {len(steps)} cart steps verified before the mission ended."
        )
    lines.append("")
    lines.append("## Checkout")
    lines.append("")
    lines.append("No checkout actions were initiated.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _completed_results(steps: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for step in steps:
        if step.get("status") != "completed":
            continue
        rows.append((
            str(step.get("step_id") or "").casefold(),
            str(step.get("result") or "").casefold(),
            step,
        ))
    return rows


def _proof_step(
    steps: list[dict[str, Any]],
    *,
    step_suffixes: tuple[str, ...],
    result_terms: tuple[str, ...],
) -> dict[str, Any] | None:
    for step_id, result, step in _completed_results(steps):
        if not any(step_id.endswith(suffix) for suffix in step_suffixes):
            continue
        if any(term in result for term in result_terms):
            return step
    return None


def apply_cart_goal_from_plan(
    goal_state: dict[str, Any] | None,
    execution_plan: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Mark free-text cart criteria complete when matching plan steps verified them."""
    if not isinstance(goal_state, dict):
        return goal_state
    steps = _steps(execution_plan)
    if not steps:
        return goal_state
    evidence_map = dict(goal_state.get("evidence_by_criterion") or {})
    completed = set(goal_state.get("completed") or [])

    def mark(description: str, step: dict[str, Any] | None, quote: str | None = None) -> None:
        if description in completed:
            return
        refs = [
            str(ref) for ref in ((step or {}).get("evidence_refs") or []) if str(ref).strip()
        ] if step else []
        evidence_map[description] = {
            "evidence_id": refs[0] if refs else str((step or {}).get("step_id") or "cart-plan"),
            "quote": (quote or str((step or {}).get("result") or (step or {}).get("objective") or ""))[:500],
            "verification": "cart_plan_step",
            "plan_step_id": (step or {}).get("step_id"),
        }
        completed.add(description)

    for criterion in goal_state.get("criteria") or []:
        description = str(criterion)
        needle = description.casefold()
        if description in completed:
            continue
        if "checkout" in needle:
            continue
        if any(term in needle for term in ("navigat", "provided url", "storefront")):
            step = next(
                (
                    item for item in steps
                    if str(item.get("step_id") or "").endswith("qa-navigation")
                    and item.get("status") == "completed"
                ),
                None,
            )
            if step:
                mark(description, step)
                continue
        if any(term in needle for term in ("addition", "add to the cart", "added")) or (
            "identif" in needle and "cart" in needle
        ):
            step = _proof_step(
                steps,
                step_suffixes=("qa-availability", "qa-action", "qa-content", "qa-discover"),
                result_terms=("cart count", "out of stock", "opened the product page", "added"),
            )
            if step:
                mark(description, step)
                continue
        if any(term in needle for term in ("price", "quantity", "presence in the cart", "cart line")):
            step = _proof_step(
                steps,
                step_suffixes=("qa-content",),
                result_terms=("price", "quantity", "out of stock", "cart line"),
            )
            if step:
                mark(description, step)
                continue
        if "remov" in needle:
            step = _proof_step(
                steps,
                step_suffixes=("qa-validate",),
                result_terms=("removed", "empty", "out of stock", "no cart line"),
            )
            if step:
                mark(description, step)
                continue
        if "empty" in needle:
            step = _proof_step(
                steps,
                step_suffixes=("qa-synthesise", "qa-validate"),
                result_terms=("empty", "out of stock"),
            )
            if step:
                mark(description, step)
                continue

    for criterion in goal_state.get("criteria") or []:
        description = str(criterion)
        if "checkout" not in description.casefold() or description in completed:
            continue
        if any(step.get("status") == "completed" for step in steps):
            checkout_opened = any(
                "checkout" in str(step.get("result") or "").casefold()
                and "not" not in str(step.get("result") or "").casefold()
                and "never" not in str(step.get("result") or "").casefold()
                for step in steps
            )
            if not checkout_opened:
                mark(description, None, quote="No checkout actions were initiated.")

    goal_state["evidence_by_criterion"] = evidence_map
    goal_state["completed"] = [
        item for item in goal_state.get("criteria", []) if item in completed
    ]
    goal_state["remaining"] = [
        item for item in goal_state.get("criteria", []) if item not in completed
    ]
    return goal_state


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
