# Night Walker product scope

**Phase:** Initial product focus  
**In-scope capabilities:** SEO auditing and website/application QA  
**Deferred:** General-purpose and deep research

## Product purpose

Night Walker is a website quality-analysis tool. In its initial release it helps an operator understand and improve a website through two deliberately scoped workflows:

1. SEO audit: inspect a website's discoverability, crawlability, indexability, technical configuration, page-level optimisation, content structure and search presentation.
2. QA testing: exercise a website's observable user journeys and report reproducible functional, navigation, rendering, accessibility and usability defects.

These are separate task types with their own plans, checks, evidence requirements, scoring and report sections. They share the browser runtime, URL policy, task lifecycle, durable checkpoints, evidence store, progress UI and report infrastructure.

## Intent routing

Task intake must classify the requested outcome, not the nouns appearing in the prompt.

- Route to SEO when the requested outcome concerns search visibility, crawlability, indexability, rankings, metadata, structured data, internal linking, content optimisation, sitemap/robots directives or Core Web Vitals/performance signals.
- Route to QA when the requested outcome concerns whether a website or application works: journeys, forms, navigation, links, controls, responsive behaviour, browser errors, visual/rendering defects, accessibility checks or regression scenarios.
- If a request clearly asks for both, create a combined task with distinct SEO and QA workstreams and independent completion criteria.
- Ask one focused clarification only when the target website or an essential test scope cannot be inferred safely.
- Do not route a task to a shopping/product workflow merely because it mentions a product, price, brand or named entity. Product extraction is not a first-class product capability in this phase.
- Do not launch general web research as a fallback when SEO/QA discovery fails. Recover within the selected workflow and report the blocker if recovery is exhausted.

## SEO audit workflow

### Initial coverage

- Site discovery and crawl boundary definition.
- robots.txt and sitemap discovery/inspection.
- HTTP status, redirects, canonical URLs, indexability directives and crawlable internal links.
- Page titles, meta descriptions, heading hierarchy, language and viewport metadata.
- Duplicate/missing/oversized metadata and thin or repeated page content signals.
- Internal linking, orphan candidates within the discovered crawl, broken internal links.
- Structured data presence and parse/validation signals where observable.
- Image alt text, dimensions, formats and lazy-loading signals where observable.
- Mobile viewport and responsive rendering checks.
- Performance indicators and Core Web Vitals only when measured by a suitable supported measurement source; do not infer lab metrics from page text.
- Findings prioritised by impact, affected URLs, evidence, recommended fix and effort.

### SEO evidence rules

- Every issue identifies the affected URL(s), what was observed, why it matters, and a concrete remediation.
- Distinguish directly observed defects from heuristic warnings.
- Record crawl depth, page sample/coverage, blocked paths and inaccessible pages.
- Never claim a complete site crawl unless the crawl boundary and completion conditions prove it.
- Do not fabricate traffic, rankings, search volume, backlink data or Core Web Vitals.

## QA workflow

### Initial coverage

- Site availability, navigation and internal-link integrity.
- Key user journeys selected from the request or discovered public navigation.
- Interactive controls and forms only within explicitly authorised, safe test boundaries.
- Responsive layout checks at defined viewport sizes.
- Browser console/page errors and failed network requests where supported by the browser runtime.
- Accessibility smoke checks: labels, names, keyboard reachability, focus visibility, heading landmarks and image alternatives where observable.
- Reproducible defect records with URL, steps, expected result, actual result, severity, evidence and environment.
- Regression re-runs for previously recorded scenarios.

### QA safety and evidence rules

- Default to read-only exploration.
- Never submit real transactions, purchases, account changes, messages, uploads or destructive actions.
- Form testing must use clearly designated test data and explicit authorisation; otherwise inspect structure and validation affordances without submission.
- A control that cannot be safely exercised is reported as untested, not passed.
- A test passes only when its assertion was actually observed. Planned or assigned work is not a pass.
- Preserve reproduction steps and browser evidence for each reported defect.

## Shared execution model

1. Resolve the task type and target scope.
2. Build a workflow-specific plan with measurable checks and acceptance criteria.
3. Execute bounded browser inspections using the central BrowserRuntime and PolicyEngine.
4. Capture immutable, URL- and timestamp-linked evidence.
5. Validate findings and completion criteria deterministically where possible.
6. Produce a workflow-specific report and an honest coverage statement.
7. Persist checkpoints and recover interrupted work without changing the original objective.

Agents may help plan, inspect and interpret evidence, but they cannot grant themselves browser permissions or mark checks passed without evidence.

## Report contracts

### SEO report

- Executive summary and overall coverage.
- Crawl scope and crawl statistics.
- Prioritised issues (severity, impact, effort, affected URLs).
- Technical SEO, indexability, metadata, content, links, structured data, images, mobile and performance sections.
- Evidence references, limitations and remediation roadmap.

### QA report

- Test scope, environment and coverage.
- Scenarios and assertions with pass/fail/blocked/not-tested states.
- Defects with severity, reproduction steps, expected/actual behaviour and evidence.
- Browser/runtime errors and responsive/accessibility observations.
- Limitations and recommended regression tests.

### Shared report invariants

- No unsupported pass, issue, score or completion claim.
- Findings link to captured evidence and source URLs.
- Partial execution is labelled partial.
- Zero relevant evidence cannot produce a completed audit.
- Scores, if introduced, must have a documented denominator and must not conceal missing coverage.

## Current implementation status

The current code path is a partial implementation of the initial scope, not a completed audit product:

- **SEO — implemented foundation, still partial:** same-origin crawling with a configurable page budget, robots.txt and bounded sitemap-index discovery, evidence-gated discovered-URL coverage, page-level metadata/indexability observations, internal-link destination checks, duplicate canonical/title/meta-description/visible-text groups, evidence-linked findings and deterministic prioritisation. Exhaustive site discovery, field/lab Core Web Vitals, full Schema.org validation and image performance analysis are not implemented.
- **QA — implemented read-only smoke layer, still partial:** same-origin GET navigation samples, reversible local disclosure toggles, browser console/page/network diagnostics, link status checks, 375/768/1280px overflow measurements, DOM/accessible-name heuristics and a short keyboard focus sample. Stateful journeys, form validation execution, SPA interaction coverage, visual regression, exhaustive keyboard testing and automated WCAG conformance are not implemented.
- **Combined workflow — routed and planned:** both workstreams are represented in the execution blueprint; end-to-end combined completion still requires live validation.
- **Reporting — improved foundation:** evidence-linked findings include P0-P4 priorities, impact rationale, effort and remediation defaults. The report includes discovered/sitemap coverage and duplicate/link/QA summary metrics; these metrics are bounded by crawl and sampling limits.
- **Validation — not yet verified end to end:** unit tests have been added for deterministic analysis and page-signal helpers, but tests/build have not been executed in the current implementation session and no live Night Walker run against a public website has been verified.

See [SEO/QA audit engine](seo-qa-audit-engine.md) for exact bounds and safety behaviour. Do not describe the current checks as a full WCAG audit or an exhaustive crawl.

## Explicitly deferred

- General-purpose web research and deep research.
- Broad fact-finding unrelated to SEO or website QA.
- Shopping/product discovery, price comparison and catalogue extraction as standalone workflows.
- Autonomous account creation, authentication bypass, transactions or destructive testing.
- Search ranking, traffic, backlink and field performance claims without an authorised data integration.

## Delivery order

1. Establish SEO and QA task types, typed plans, report contracts and UI selection.
2. Build reliable SEO site discovery/crawl and deterministic technical/on-page checks.
3. Build QA scenario planning, safe browser assertions and reproducible defect capture.
4. Add combined SEO+QA orchestration after each workflow works independently.
5. Validate against representative real websites and adversarial/edge cases before expanding scope.

General/deep research remains a later product phase and must not be used as a catch-all fallback for the initial workflows.
