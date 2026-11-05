# ADR 0001: Separate browsing, research and memory

- **Status:** Accepted
- **Date:** 2026-11-05
- **Decision owners:** Ginger Night Walker project

## Context

Night Walker is intended to support general-purpose browsing and research. A browser observation, a research conclusion and a durable memory item have different provenance, trust and lifecycle requirements. Conflating them would make a visited page appear verified and would allow transient or adversarial content to become persistent knowledge without review.

## Decision

Keep three explicit layers:

1. **Browsing:** records what the browser actually visited, requested and captured.
2. **Research:** evaluates evidence, creates traceable findings, compares sources and communicates uncertainty.
3. **Memory:** decides what Ginger should retain, trust, expire and reuse.

Night Walker returns a structured report and may propose candidate memory. It does not directly write to Ginger's persistent memory.

## Consequences

Positive:
- provenance is inspectable;
- task-specific extraction does not dictate the platform's report shape;
- memory retention can apply Ginger-wide trust and privacy rules;
- untrusted page content remains separate from system policy.

Costs:
- report contracts need source/evidence references;
- cross-source synthesis is a distinct capability to implement;
- memory integration needs an explicit approval contract;
- UI must distinguish observed, reported, derived and inferred claims.

## Rejected alternatives

- **Browser capture equals truth:** rejected because pages can be inaccurate, stale or adversarial.
- **Every report becomes memory:** rejected because transient task output should not automatically persist.
- **Product-first result schema:** rejected because product lookup is only one use case among many.
