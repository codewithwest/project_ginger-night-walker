"""Deterministic evaluation primitives for Ginger mission traces.

This module deliberately evaluates recorded outcomes; it does not plan missions,
change acceptance criteria, or grant execution authority.
"""
from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any


_FAILURE_SIGNALS = {
    "comprehension": (
        "misunderstood", "wrong control", "wrong page", "incorrect target",
        "stale element", "no page node", "ambiguous element", "not found",
    ),
    "specification": (
        "acceptance criteria", "objective unclear", "missing criterion",
        "success condition", "scope mismatch",
    ),
    "generalization": (
        "layout changed", "unsupported layout", "unseen pattern",
        "site-specific", "different website",
    ),
    "tool_execution": (
        "tool error", "mcp error", "timeout", "session slot",
        "browser error", "execution failed",
    ),
    "orchestration": (
        "budget exhausted", "cancelled", "queue", "scheduler",
        "dependency", "runtime",
    ),
}


def classify_failure(events: list[dict[str, Any]]) -> str:
    """Classify the strongest observable failure signal in a trace."""
    text_parts: list[str] = []
    for event in events:
        details = event.get("details") if isinstance(event, dict) else {}
        if isinstance(details, dict):
            text_parts.extend(str(details.get(key, "")) for key in (
                "reason", "error", "message", "failure", "status",
            ))
        text_parts.append(str(event.get("phase", "")))
    haystack = " ".join(text_parts).casefold()
    for category, signals in _FAILURE_SIGNALS.items():
        if any(signal in haystack for signal in signals):
            return category
    return "unclassified"


def evaluate_mission(record: dict[str, Any]) -> dict[str, Any]:
    """Calculate evidence-based mission metrics from a normalized run record.

    Expected shape:
      status: terminal mission status
      criteria: [{status: verified|failed|pending, ...}]
      actions: [{intended_state_change: bool, observed_state_change: bool}]
      recoveries: [{attempted: bool, recovered: bool}]
      trace: append-only run trace events
      duration_ms: elapsed mission duration
    """
    criteria = record.get("criteria") or []
    actions = record.get("actions") or []
    recoveries = record.get("recoveries") or []
    trace = record.get("trace") or []
    verified = sum(1 for item in criteria if item.get("status") == "verified")
    failed = sum(1 for item in criteria if item.get("status") == "failed")
    total = len(criteria)
    completed = record.get("status") == "completed"
    unsupported_completion = completed and (total == 0 or verified != total)
    attempted_recoveries = [item for item in recoveries if item.get("attempted")]
    effective_actions = [
        item for item in actions if item.get("intended_state_change")
    ]
    effective_count = sum(
        1 for item in effective_actions if item.get("observed_state_change")
    )
    return {
        "task_id": str(record.get("task_id") or ""),
        "mission_completed": completed and not unsupported_completion,
        "false_completion": unsupported_completion,
        "criteria_verified": verified,
        "criteria_total": total,
        "objective_completion_rate": verified / total if total else 0.0,
        "action_effectiveness_rate": (
            effective_count / len(effective_actions) if effective_actions else None
        ),
        "recovery_rate": (
            sum(1 for item in attempted_recoveries if item.get("recovered"))
            / len(attempted_recoveries) if attempted_recoveries else None
        ),
        "failure_class": classify_failure(trace) if not completed or failed else None,
        "duration_ms": max(0, int(record.get("duration_ms") or 0)),
    }


def aggregate_evaluations(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate normalized mission records without allowing model claims as proof."""
    results = [evaluate_mission(record) for record in records]
    count = len(results)
    completed = sum(1 for item in results if item["mission_completed"])
    false_completions = sum(1 for item in results if item["false_completion"])
    criteria_total = sum(item["criteria_total"] for item in results)
    criteria_verified = sum(item["criteria_verified"] for item in results)
    action_rates = [item["action_effectiveness_rate"] for item in results if item["action_effectiveness_rate"] is not None]
    recovery_rates = [item["recovery_rate"] for item in results if item["recovery_rate"] is not None]
    failures = Counter(item["failure_class"] for item in results if item["failure_class"])
    return {
        "mission_count": count,
        "mission_completion_rate": completed / count if count else 0.0,
        "false_completion_rate": false_completions / count if count else 0.0,
        "objective_completion_rate": criteria_verified / criteria_total if criteria_total else 0.0,
        "mean_action_effectiveness_rate": mean(action_rates) if action_rates else None,
        "mean_recovery_rate": mean(recovery_rates) if recovery_rates else None,
        "mean_duration_ms": mean(item["duration_ms"] for item in results) if results else 0.0,
        "failure_classes": dict(failures),
        "missions": results,
    }


def evaluate_task_record(
    record: dict[str, Any], trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive a conservative evaluation view from a persisted Ginger task snapshot."""
    goal = record.get("goal_state") or {}
    criteria = goal.get("criteria") or []
    completed = set(goal.get("completed") or [])
    normalized_criteria = [
        {"criterion_id": str(index), "status": "verified" if item in completed else "pending"}
        for index, item in enumerate(criteria)
    ]
    events = trace if trace is not None else record.get("execution_trace") or []
    actions: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    for event in events:
        details = event.get("details") if isinstance(event, dict) else {}
        if not isinstance(details, dict):
            continue
        if "intended_state_change" in details and "observed_state_change" in details:
            actions.append({
                "intended_state_change": bool(details["intended_state_change"]),
                "observed_state_change": bool(details["observed_state_change"]),
            })
        if details.get("recovery_attempted") is True:
            recoveries.append({
                "attempted": True,
                "recovered": details.get("recovery_succeeded") is True,
            })
    timing = record.get("timing") or {}
    return evaluate_mission({
        "task_id": record.get("task_id"),
        "status": record.get("status"),
        "criteria": normalized_criteria,
        "actions": actions,
        "recoveries": recoveries,
        "trace": events,
        "duration_ms": timing.get("elapsed_ms", 0),
    })
