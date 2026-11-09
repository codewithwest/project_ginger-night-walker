# Execution control layer

The initial Night Walker beta keeps SEO and QA execution read-only and deterministic. This layer adds reusable controls without granting model output direct browser authority.

## Pipeline

1. **Observe:** capture the current task step and browser observations.
2. **Decide:** select a tool name from the allowlist for the active workflow.
3. **Resolve:** validate the selected tool and resolve any element only from observed accessibility refs. A low-confidence or ambiguous match is not actionable.
4. **Act:** the existing browser runtime remains the only executor and continues to enforce URL/origin policy.
5. **Verify:** Python checks the browser outcome and requires a matching persisted evidence reference before accepting completion.
6. **Evaluate:** later model interpretation may explain verified results, but cannot override failed or unknown verification.

Tool selection and argument filling are separate structured inference calls. The argument call receives only the schema for the selected registered tool. The current registry contains public navigation, same-origin relevant-link navigation and bounded page capture; no form submission, checkout, account mutation or other state-changing tools are registered.

## State and diagnostics

Prompt working memory is bounded and compiled independently. It does not replace or truncate durable task snapshots, checkpoints, scratchpad history or immutable evidence.

Each task has an append-only JSONL trace under EVIDENCE_DIR/traces. Sensitive-key values and sensitive URL query parameters are redacted before writing. Trace events are operational diagnostics, not source evidence. The API exposes the most recent events through GET /tasks/{task_id}/trace?limit=100. Trace files older than 14 days are eligible for cleanup at service startup.

## Limits

The two-stage proposal method is available to the orchestrator but does not bypass the existing mission-plan authorisation gate. SEO/QA audit runs continue to use their deterministic workflow plans; proposals are not automatically executed. Accessibility resolution is conservative and may decline to act when labels are vague or repeated. The verifier currently checks browser completion plus the matching persisted evidence ID; richer per-action assertions can be added as specific interactive QA workflows are introduced.


## Job timing and bottleneck analysis

Every task records its accepted timestamp, completion timestamp, total wall-clock elapsed milliseconds and accumulated stage durations. Queue time is separated from active execution. Active stages are measured with a monotonic clock and accumulated at stage boundaries; persisted totals survive task recovery and clarification/retry cycles. The API includes timing in both task snapshots and task-list summaries.

The UI shows elapsed/total time in the mission list and report header, plus a live stage breakdown for queue, planning, browser work, analysis and report synthesis. The current active stage is displayed while the job runs. Wall-clock total includes time waiting between resumptions; stage durations represent measured work intervals. Model inference currently contributes to the enclosing stage rather than being split into a separate model-only bucket.
