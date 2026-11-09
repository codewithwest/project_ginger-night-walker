# Documentation

This documentation is the engineering source of truth for Ginger Night Walker.

## Product workflows

- [Product scope](product-scope.md) — initial SEO/QA focus and deferred general research.
- [SEO/QA audit engine](seo-qa-audit-engine.md) — crawl behavior, checks, report priorities and verification boundaries.

## Standards and architecture

- [System architecture](architecture.md) — responsibilities, component boundaries, lifecycle and diagrams.
- [Research report standard](research-report-standard.md) — canonical output contract, evidence semantics, source and finding requirements.
- [Security and trust model](security-and-trust.md) — threat model, browser boundaries, untrusted content and deployment requirements.
- [Development and operations](development-and-operations.md) — setup, configuration, quality gates, testing and troubleshooting.
- [Decision record 0001](decisions/0001-separate-browsing-research-and-memory.md) — why browsing, research and memory are separate.

## Documentation conventions

- **Implemented** means present in the current code path and covered by tests or direct inspection.
- **Partial** means some supporting code exists, but the complete capability is not implemented.
- **Planned** means an architectural direction, not a current feature.
- Diagrams describe the intended boundaries; accompanying status notes identify gaps between design and implementation.
- Security statements describe the current development baseline, not a certification or guarantee.

When code and documentation disagree, open an issue and update both. Do not silently document an aspiration as a working feature.
