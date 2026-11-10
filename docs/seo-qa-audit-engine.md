# SEO and QA audit engine

## Crawl behaviour

SEO, QA and combined audit tasks use deterministic same-origin discovery rather than the general-research planner.

- The requested URL is the crawl seed.
- Up to 2,000 rendered anchors per inspected page are considered for discovery, with up to 200 unique same-origin links admitted from that page. Link context (main/article content, navigation, footer or body) contributes to scheduling priority. Same-origin sitemap URLs also enter the bounded candidate queue. SEO/QA candidates are classified and priority-ranked rather than inspected in discovery order.
- `robots.txt` is read before discovered URLs are followed. Disallowed URLs are excluded. If the robots endpoint fails unexpectedly or returns a server error, further discovery is paused conservatively.
- Sitemap indexes are parsed with bounded tag extraction. Up to 5 sitemap endpoints and 20 child sitemap files are inspected once per task origin and cached for the remainder of that task.
- At most 1,000 sitemap page URLs are admitted from a sitemap response.
- The default audit page budget is 200. Set `AUDIT_MAX_PAGES` to a value from 1 to 1,000 to change it.
- Redirected pages are checked against the established origin. External destinations are not added to the audit crawl. Internal-link HEAD/GET checks disable automatic redirects, validate every redirect hop against the origin and URL policy, and classify off-origin redirects separately from HTTP failures.
- URL identity removes fragments, common tracking/session parameters, default ports and trailing-slash variations for comparison while preserving the original URL used for evidence and navigation.
- Filter/sort/facet URLs and utility routes (cart, checkout, account, login, search, API/admin) are excluded from the standard crawl queue; pagination remains eligible at lower priority.
- Candidates are classified into homepage, category, detail, editorial, informational, utility or other page types. The deterministic scheduler scores workflow relevance, template novelty, pagination and path depth, with different weighting for SEO and QA.
- Inspected pages record their page type and URL-derived template key. The scheduler boosts candidates from templates not yet represented in inspected pages.
- The queue retains up to 1,000 eligible candidates independently of the inspection budget, so lower-ranked discovered URLs remain available and can be reported as unvisited rather than silently discarded.
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

## Product-search QA flow

A single-page QA request that explicitly asks to search for a named product uses an observation-driven branch rather than assuming the supplied URL is already a catalogue page:

1. Open the exact supplied URL and inspect the rendered page for a visible, enabled search field.
2. If a search field exists, use it.
3. If no search field exists, inspect rendered same-origin navigation links for an actual product/shop/catalogue destination; do not guess or append a conventional path.
4. Open the best evidenced destination and inspect it for a usable search field.
5. Search only when exactly one usable field is available. Ambiguous or missing controls are reported as blockers, not silently guessed.
6. Capture matching product-card text, image source/load state and displayed price independently. Missing fields remain unverified.

The route taken (requested page, discovered product page and search results page) is recorded in `qa_product_search.route`. The fallback is bounded to one discovered product-page destination; it does not perform a broad site crawl.

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

The audit is bounded by `AUDIT_MAX_PAGES`, per-page link-check limits and sitemap sample limits. Priority scheduling improves the value of inspected pages but does not make a budget-limited crawl exhaustive; deferred and unvisited URLs remain part of the coverage calculation. A report must preserve remaining/unvisited URL counts and untested QA scope; it must not label an incomplete crawl as exhaustive. The crawl success criterion is evidence-gated, so reaching the page budget with remaining discovered URLs produces a blocked/incomplete task rather than a false completed status.


## Priority crawl scheduling

The scheduler is deterministic and does not ask an LLM to choose crawl URLs. It prioritises homepage/category/detail and important informational templates, boosts previously unseen templates, and lowers pagination priority. QA slightly favours detail and informational routes; SEO favours category and editorial coverage. Query variants identified as tracking, sorting or facets are deduplicated or excluded from the default crawl; meaningful query parameters remain part of URL identity. The original URL is retained for navigation and evidence.

The report summary includes inspected template count, candidate page-type counts, pagination count, queued distinct template count and page-budget utilisation. These are scheduling diagnostics, not proof of complete route coverage.


## Product listing and on-page search QA

A request to search a supplied product/listing page and verify a named result is functional QA when the user asks to exercise the site's search or product-discovery journey. A product noun alone does not trigger QA. These requests use the single-page QA boundary unless site-wide coverage is explicitly requested. Product identity is always verified; additional fields (image/load state, displayed price and product URL) are acceptance criteria only when requested. Missing or contradictory values remain unverified rather than inferred. This is product discovery inside a user journey, not standalone shopping or price comparison.

## Product search QA

Single-page QA objectives that explicitly ask to search for a quoted product name perform that search in the browser. The runner resolves a visible, enabled search field and a unique submit control (or presses Enter when no submit control exists), then captures matching product-card text and all available requested product attributes. Each field retains its source card and evidence provenance. Missing or ambiguous controls are recorded as test outcomes; a product is not considered verified merely because the page loaded. Product results are displayed in dedicated structured cards, separately from QA defects. Image settling is bounded by `QA_IMAGE_SETTLE_TIMEOUT_MS` (default 4,000 ms); unresolved lazy or slow images remain pending. Product result settling is bounded by `QA_PRODUCT_RESULT_SETTLE_MS` (default 1,500 ms, capped at 2,500 ms).

## QA scope selection

QA defaults to a single-page usability/functional smoke test for the URL supplied by the user. It does not silently expand into a same-origin crawl or unrelated journey. Checks focus on visible controls, accessible names, links/forms, rendering failures, image load failures, responsive behaviour and browser/runtime errors where captured. A site-wide crawl is enabled only when the objective explicitly asks for site-wide/full-site coverage, all/every page, all URLs, or a site crawl. Page titles, heading hierarchy, metadata, canonical/indexing and structured-data checks belong to SEO; they are not QA defects. If a user explicitly requests both SEO and functional QA, the combined workflow keeps the two workstreams and findings separate.

Single-page QA captures usability and runtime observations within the supplied page boundary. Pending images are not reported as broken. Raw page metadata may remain in captured evidence for context, but it is not promoted to a functional QA finding or a QA summary metric. The report marks crawl coverage as not applicable rather than treating undiscovered links as incomplete or implying a full-site audit. Site-wide QA retains discovered-URL coverage gates.


## Finding validation and recommendation gate

Every detected finding is retained as a candidate and passed through a separate deterministic validation stage before remediation is attached. The validator checks that referenced evidence IDs exist and that a type-specific structured browser observation supports the claim. It records the verdict, evidence references checked, supporting observation references, limitations and a bounded recommendation context containing matching observations and evidence excerpts.

- `confirmed`: a matching structured observation supports the finding; recommendations may be generated.
- `insufficient_evidence`: evidence is missing, unrelated, or the finding type has no dedicated validator; the candidate remains visible without a recommendation.
- The validator currently does not assign a `rejected` verdict; contradictory-evidence checks and reproducibility retries remain future work. A confidence value is not used as a substitute for evidence.
- Recommendation output includes the confirmed root-cause context, action, expected outcome and a verification step. Unconfirmed findings have remediation/recommendation fields removed.
- The full matching observation and evidence excerpt packet is attached to each finding under `validation.recommendation_context`, so later recommendation agents can inspect the underlying data rather than relying on a short claim.
- Set `AUDIT_SKIP_RECOMMENDATIONS=true` to suppress remediation and recommendation generation. Finding validation still runs and remains visible in the report. The default is `false`.

This is an initial conservative validator, not an independent second-browser reproduction for every defect. Types without a dedicated matching rule remain insufficient rather than being promoted based on a model's assertion. The validation summary is persisted in task structured data and task state.
