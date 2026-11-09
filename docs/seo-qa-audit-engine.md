# SEO and QA audit engine

## Crawl behaviour

SEO, QA and combined audit tasks use deterministic same-origin discovery rather than the general-research planner.

- The requested URL is the crawl seed.
- Same-origin HTML links and same-origin sitemap URLs are added to a breadth-first candidate queue.
- `robots.txt` is read before discovered URLs are followed. Disallowed URLs are excluded. If the robots endpoint fails unexpectedly or returns a server error, further discovery is paused conservatively.
- Sitemap indexes are parsed with bounded tag extraction. Up to 5 sitemap endpoints and 20 child sitemap files are inspected once per task origin and cached for the remainder of that task.
- At most 1,000 sitemap page URLs are admitted from a sitemap response.
- The default audit page budget is 200. Set `AUDIT_MAX_PAGES` to a value from 1 to 1,000 to change it.
- Redirected pages are checked against the established origin. External destinations are not added to the audit crawl. Internal-link HEAD/GET checks disable automatic redirects, validate every redirect hop against the origin and URL policy, and classify off-origin redirects separately from HTTP failures.
- URL and content fingerprints are used to report canonical, title, meta-description and exact visible-text duplicate groups.

Coverage is reported against URLs discovered from inspected internal links and parsed sitemap samples. The crawl acceptance step remains incomplete if discovered URLs remain unvisited, the sitemap sample cap is exceeded, or robots policy cannot be read reliably; the task is then blocked rather than marked complete. This is not a guarantee that every URL on a site was found. Orphaned pages, JavaScript-only routes, unlinked content and URLs beyond the sitemap sample/page budget may remain undiscovered.

## SEO observations

The current deterministic checks include:

- HTTP response and redirect outcome for navigated pages
- `robots.txt` outcome and crawl rules
- sitemap discovery, sitemap-index expansion and sampled URL coverage
- title and meta-description presence and heuristic length warnings
- canonical presence and cross-origin canonical warning
- robots noindex directive
- document language and viewport metadata
- H1 count and heading-level skips
- image alt-attribute omissions and HTTP `X-Robots-Tag` noindex directives
- JSON-LD block JSON syntax
- internal-link destination status checks (HEAD, with GET fallback only when HEAD is explicitly unsupported); redirects are followed manually, with policy validation at each hop and no automatic off-origin follow
- duplicate canonical, title, meta-description and exact visible-text groups

Findings receive a deterministic priority (P0-P4), impact score, impact rationale, effort estimate, affected URLs and remediation guidance. The operator UI surfaces these fields alongside an audit overview with crawl, duplicate, link, responsive and accessibility metrics. These are triage heuristics, not search-engine ranking predictions.

Core Web Vitals, rendered field performance, full structured-data schema validation, exhaustive crawl of every route and search-engine index coverage are not claimed.

## QA observations

The read-only QA smoke checks include:

- same-origin anchor destination checks and a small sample of actual browser GET navigations
- HTTP/navigation failures and redirects
- responsive overflow measurements at 375px, 768px and 1280px viewport widths
- visible control, form and duplicate-ID observations
- accessible-name heuristics for controls, links and buttons
- focusable elements inside `aria-hidden` content
- broken `aria-labelledby` references, generic link names, nested interactive controls and positive `tabindex` values
- table header presence, new-tab link `rel=noopener`, document title/language and landmark structure
- visible image alt omissions and heading-level skips
- a six-step keyboard Tab focus sample
- reversible local disclosure-toggle checks (ARIA-expanded buttons and native details summaries)
- browser console errors, uncaught page exceptions and failed network requests

No forms are submitted and no state-changing controls are activated. SPA click handlers, authentication journeys, transactions, exhaustive keyboard paths, visual regression, screen-reader output, contrast conformance and automated WCAG certification are not claimed. Use an approved test account and explicit test authorization before expanding into stateful journeys.

## Execution control and observability

The browser execution layer separates tool selection, argument validation, element resolution, execution and verification. Tool proposals are restricted to the SEO/QA read-only registry; the second model call receives only the selected tool's argument schema. The proposal itself never grants permission to execute. Element resolution uses observed accessibility names and refs, and ambiguous matches are left unresolved. Browser completion is accepted only when the runtime returns a matching persisted evidence reference. Redacted per-task JSONL traces record observe, decide, resolve, act, verify and evaluate phases; trace records are operational diagnostics and are not treated as audit evidence. Prompt working memory is bounded separately from durable task snapshots, scratchpad history and evidence.

## Evidence and completion

A finding is only emitted from captured page or destination observations. Failed or unsupported requests remain distinguishable from confirmed HTTP failures. Crawl coverage, duplicate groups, link-check totals, QA navigation samples and priority counts are included in the structured report summary.

The audit is bounded by `AUDIT_MAX_PAGES`, per-page link-check limits and sitemap sample limits. A report must preserve remaining/unvisited URL counts and untested QA scope; it must not label an incomplete crawl as exhaustive. The crawl success criterion is evidence-gated, so reaching the page budget with remaining discovered URLs produces a blocked/incomplete task rather than a false completed status.
