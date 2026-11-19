# Ginger evaluation

Ginger already records redacted, task-isolated JSONL execution traces and has an independent evidence verifier. The evaluation layer builds on those foundations; it must not become another planner or rewrite a Decider-authored mission.

## Evaluation boundaries

- The Decider owns the executable plan.
- The goal/acceptance-criteria layer defines what must be true.
- The executor proposes and performs permitted browser actions.
- The independent verifier checks immutable evidence.
- The evaluator measures recorded execution outcomes. It cannot execute tools, grant authority, alter criteria, or turn an unverified result into success.

## Metrics

`nightwalker.evaluation` provides deterministic functions for normalized mission records:

- `mission_completion_rate`: missions whose terminal status is completed and whose acceptance criteria are all verified.
- `false_completion_rate`: completed missions with missing, pending, or failed criteria. A completed status without criteria is counted as unsupported.
- `objective_completion_rate`: verified criteria divided by all criteria.
- `action_effectiveness_rate`: intended state-changing actions that produced their observed state change.
- `recovery_rate`: attempted recoveries that reached their intended recovery.
- `mean_duration_ms`: average recorded mission duration.
- `failure_classes`: counts grouped into comprehension, specification, generalization, tool execution, orchestration, or unclassified.

A metric is `null` when its denominator is absent; do not interpret missing measurements as 100% success.

## Normalized evaluation record

The evaluation functions accept records shaped like this. This is a data contract, not a prescribed mission workflow:

```json
{
  "task_id": "opaque-run-id",
  "status": "completed",
  "duration_ms": 12500,
  "criteria": [{"criterion_id": "criterion-from-goal-state", "status": "verified"}],
  "actions": [{"intended_state_change": true, "observed_state_change": true}],
  "recoveries": [{"attempted": true, "recovered": true}],
  "trace": [{"phase": "verify", "details": {"reason": "Persisted evidence supports the criterion"}}]
}
```

Populate this contract from persisted task state, verification decisions, browser outcomes and the existing redacted run trace. Do not infer state changes from model prose or a tool's transport-level success response.

## Dataset and benchmark policy

1. Convert reviewed real missions into reusable cases. Preserve the user's objective, site/task diversity and expected observable outcomes; remove credentials, personal data, session identifiers and unnecessary page data.
2. Keep the original mission text and expected outcomes separate from the executor prompt. The evaluator must not leak expected answers into execution.
3. Include successful, failed, ambiguous, interrupted and recovery runs.
4. Split cases by website/task family where possible. Keep a held-out set to detect overfitting to familiar sites.
5. Run the same cases against each model/prompt/runtime version. Record model identity, prompt/version, runtime commit, browser backend, latency and cost.
6. Compare aggregate metrics and per-case regressions. Never promote a model based on one successful demonstration or speed alone.
7. Require human review for ambiguous outcomes and consequential actions.

## Failure analysis

Use the trace to identify the earliest point where the intended outcome diverged:

- Comprehension: incorrect interpretation of page state, target or control.
- Specification: missing, contradictory or untestable success condition.
- Generalization: a strategy does not transfer to a different site/layout/task.
- Tool execution: the browser/MCP tool fails to perform the requested operation.
- Orchestration: scheduling, dependency, budget, persistence or lifecycle issue.

Classification is a triage aid, not a root-cause oracle. Preserve the raw trace and review the evidence before changing prompts or code.

## Current implementation boundary

The deterministic metric functions and unit tests are implemented. Automatic conversion of all persisted task records into normalized benchmark records, a benchmark runner, model-comparison dashboard and curated cross-site dataset remain follow-up work. Do not report those as implemented until they exist.
