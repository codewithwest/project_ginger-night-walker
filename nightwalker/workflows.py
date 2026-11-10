"""First-phase product workflow routing and deterministic plans.

SEO and QA are first-class workflows. Unknown/general research is deliberately
not used as a fallback in this product phase.
"""
from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlsplit

from nightwalker.contracts import Capability
from nightwalker.planning_contracts import ExecutionBlueprint, PlannedSubtask, SuccessCriterion


class WorkflowType(StrEnum):
    SEO = "seo"
    QA = "qa"
    SEO_QA = "seo_qa"
    UNSUPPORTED = "unsupported"


_SEO_TERMS = {
    "seo", "search engine optimisation", "search engine optimization",
    "technical seo", "on-page seo", "on page seo", "indexability",
    "crawlability", "search visibility", "meta description", "structured data",
    "robots.txt", "sitemap.xml", "core web vitals",
}
_QA_TERMS = {
    "qa", "quality assurance", "test website", "test web app", "test web application",
    "website testing", "web app testing", "functional testing", "regression testing",
    "broken links", "user journey", "usability testing", "accessibility testing",
    "responsive testing", "smoke test", "smoke testing", "broken images",
    "console error", "console errors", "browser errors", "page title", "h1 heading", "h1 headings",
    "bug", "bugs", "defects",
    "product listing", "product listings", "product search", "search results",
    "verify the product appears", "verify that the product appears",
    "add to cart", "add it to cart", "remove from cart", "remove it from the cart",
    "shopping cart", "basket", "cart is empty", "cart is empty", "quantity",
    "do not checkout", "without checking out", "cart journey", "cart flow",
    "check the product appears", "check that the product appears",
    "confirm the product appears", "confirm that the product appears",
}


def classify_workflow(objective: str) -> WorkflowType:
    """Classify the requested outcome, not the nouns/entities in the objective."""
    value = " ".join(objective.casefold().split())
    seo = any(term in value for term in _SEO_TERMS)
    qa = any(term in value for term in _QA_TERMS)
    # Product-page verification is QA when the requested outcome is to test
    # that a site search/listing renders expected UI content. The product noun
    # alone is deliberately not sufficient to classify a task as QA.
    product_result_verification = (
        any(verb in value for verb in ("verify", "check", "confirm", "test"))
        and any(term in value for term in ("product appears", "product is shown", "product is displayed", "search for"))
        and bool(re.search(r"\bhttps?://|\bwww\.", value))
    )
    qa = qa or product_result_verification
    if seo and qa:
        return WorkflowType.SEO_QA
    if seo:
        return WorkflowType.SEO
    if qa:
        return WorkflowType.QA
    return WorkflowType.UNSUPPORTED


def target_url_from_objective(objective: str) -> str | None:
    match = re.search(
        r"(?i)\bhttps?://[^\s<>\"']+|\bwww\.[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s<>\"']*)?|\b[a-z0-9-]+(?:\.[a-z]{2,})(?:\.[a-z]{2,})?(?:/[^\s<>\"']*)?\b",
        objective,
    )
    if not match:
        return None
    value = match.group(0).rstrip(".,;:!?)]}")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if not hostname or "." not in hostname:
        return None
    # Reject purely numeric hostnames (e.g. version numbers like "1.0", "3.14")
    tld = hostname.rsplit(".", 1)[-1]
    if not tld or tld.isdigit() or len(tld) < 2:
        return None
    return value



def qa_requires_site_crawl(objective: str) -> bool:
    """Only expand QA beyond the supplied URL when the user asks for site-wide coverage."""
    value = " ".join(objective.casefold().split())
    explicit_site_wide = (
        r"\b(site[- ]wide|full[- ]site|full website|entire site|entire website|whole site|whole website|all pages|every page|all urls|all site pages|crawl (?:the )?site|site crawl|website[- ]wide|across the (?:whole )?site)\b"
    )
    return bool(re.search(explicit_site_wide, value))


def qa_scope_for_objective(objective: str) -> str:
    return "site_wide" if qa_requires_site_crawl(objective) else "single_page"

def extract_product_search_query(objective: str) -> str | None:
    """Extract a product search query, excluding ecommerce cart workflows."""
    value = " ".join(objective.casefold().split())
    if any(term in value for term in ("add to cart", "add it to the cart", "remove from cart", "remove it from the cart", "shopping cart", "cart is empty", "do not checkout", "without checking out")):
        return None
    m = re.search(
        r"(?i)search\s+for\s+[“”\"'\u2018\u2019]([^“”\"'\u2018\u2019]{1,100})[“”\"'\u2018\u2019]",
        objective,
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(?i)\b(?:submit|run|perform|execute)\s+(?:the\s+)?search(?:\s+query)?\s+(?:for\s+)?"
        r"[“”\"'\u2018\u2019]?([^“”\"'\u2018\u2019\n.!?]{1,100})",
        objective,
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(?i)(?:verify|check|confirm|test)\s+(?:the\s+)?(?:presence|existence|display|appearance)\s+of\s+"
        r"[“”\"'\u2018\u2019]([^“”\"'\u2018\u2019]{1,100})[“”\"'\u2018\u2019]",
        objective,
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(?i)(?:verify|check|confirm|test)\s+(?:that\s+)?[“”\"'\u2018\u2019]([^“”\"'\u2018\u2019]{1,100})[“”\"'\u2018\u2019]"
        r"\s+(?:product\s+)?(?:appears|is\s+shown|is\s+displayed|is\s+listed|is\s+available)",
        objective,
    )
    if m:
        return m.group(1).strip()
    for m in re.finditer(
        r"[“”\"'\u2018\u2019]([^“”\"'\u2018\u2019]{2,80})[“”\"'\u2018\u2019]",
        objective,
    ):
        candidate = m.group(1).strip()
        if len(candidate.split()) <= 8 and "://" not in candidate:
            return candidate
    return None


def build_product_search_blueprint(objective: str) -> ExecutionBlueprint:
    """Build a deterministic, correctly authorised plan for exact product-search QA."""
    query = extract_product_search_query(objective)
    if not query:
        raise ValueError("A quoted product-search query is required for this workflow")

    criteria = [
        SuccessCriterion(
            criterion_id="sc-navigation",
            description="The supplied website loads and its search interface or actual catalogue route is identified.",
            acceptance_test="Capture the supplied site's successful navigation and identify a rendered search control or a grounded same-origin catalogue route.",
        ),
        SuccessCriterion(
            criterion_id="sc-search-submission",
            description=f"The exact search query {query!r} is submitted to the website.",
            acceptance_test=f"Capture the search control containing {query!r} and confirm the search action and resulting navigation/outcome.",
        ),
        SuccessCriterion(
            criterion_id="sc-product-name",
            description=f"A result with a product name matching {query!r} is identified.",
            acceptance_test="Extract the actual product title from a matching result; incidental page or card text is insufficient.",
        ),
        SuccessCriterion(
            criterion_id="sc-product-image",
            description="The matching product has an image in the search results.",
            acceptance_test="Capture a non-empty image source associated with the matched product result.",
        ),
        SuccessCriterion(
            criterion_id="sc-product-price",
            description="The matching product has a displayed price.",
            acceptance_test="Capture the numerical price associated with the matched product result.",
        ),
    ]
    subtasks = [
        PlannedSubtask(
            subtask_id="qa-navigation",
            objective="Navigate to the supplied website and identify its rendered search interface; if absent, discover the actual same-origin catalogue route from site navigation.",
            capability=Capability.NAVIGATE,
            success_criteria=["sc-navigation"],
            expected_output="Captured target page and grounded search-interface or catalogue-route evidence.",
        ),
        PlannedSubtask(
            subtask_id="qa-availability",
            objective=f'Search for "{query}" using the rendered search control, discover the actual same-origin catalogue route if necessary, submit the query and capture the search outcome.',
            capability=Capability.INTERACT,
            depends_on=["qa-navigation"],
            success_criteria=["sc-search-submission"],
            expected_output="Evidence that the exact query was entered and the site's search action was triggered, or a precise recorded failure outcome.",
        ),
        PlannedSubtask(
            subtask_id="qa-content",
            objective=f'Extract the actual matching product name, image source and displayed price from the search results for "{query}".',
            capability=Capability.EXTRACT,
            depends_on=["qa-availability"],
            success_criteria=["sc-product-name", "sc-product-image", "sc-product-price"],
            expected_output="Product title, associated image source and displayed price, each independently captured.",
        ),
        PlannedSubtask(
            subtask_id="qa-validate",
            objective=f'Validate the extracted product title, image source and price against the search results for "{query}" and the captured browser evidence.',
            capability=Capability.EXTRACT,
            depends_on=["qa-content"],
            success_criteria=["sc-product-name", "sc-product-image", "sc-product-price"],
            expected_output="Evidence-linked validation verdict for each requested product field.",
        ),
        PlannedSubtask(
            subtask_id="qa-synthesise",
            objective="Summarize the search submission and each independently verified product field; clearly distinguish confirmed, failed and unverified checks.",
            capability=Capability.EXTRACT,
            depends_on=["qa-validate"],
            success_criteria=[
                "sc-navigation", "sc-search-submission", "sc-product-name",
                "sc-product-image", "sc-product-price",
            ],
            expected_output="Concise evidence-constrained QA report with no unsupported product claims.",
        ),
    ]
    return ExecutionBlueprint(
        original_objective=objective,
        interpretation=f"Search the supplied website for {query!r} and verify the matching product name, image and price.",
        success_criteria=criteria,
        subtasks=subtasks,
        completion_policy="all_criteria_verified",
        research_queries=[],
    )


def _qa_content_objective(objective: str) -> str:
    """Build the qa-content step objective, injecting the product name when present."""
    product = extract_product_search_query(objective)
    if product:
        return (
            f'Inspect the requested page for a search control. If absent, discover a same-origin '
            f'product/shop page from rendered navigation, open it, and inspect again. '
            f'Search for "{product}" when a usable control is found; capture matching product name, image and price.'
        )
    return (
        "Inspect the requested page for a search control. If absent, discover a same-origin "
        "product/shop page from rendered navigation, open it, and inspect again. "
        "Search for the requested product only when a usable control is found; capture matching product name, image and price."
    )


def build_workflow_blueprint(objective: str, workflow: WorkflowType) -> ExecutionBlueprint:
    if workflow == WorkflowType.SEO:
        criteria = [
            ("seo-scope", "The target website and inspected URL scope are recorded.", "At least one target URL and its captured source are recorded."),
            ("seo-technical", "Technical crawl and indexability signals are inspected.", "Evidence records observed HTTP outcome, canonical/robots directives when present, and sitemap/robots discovery outcome."),
            ("seo-page", "Page metadata, headings, visible content and image alternatives are inspected.", "Captured page evidence supports each reported title, description, canonical, heading, language, viewport and image-alt observation."),
            ("seo-links", "Internal link structure and broken-link candidates are assessed.", "Observed internal links are recorded and any failed destination is evidenced."),
            ("seo-coverage", "Discovered same-origin URLs are visited or explicitly accounted for.", "The report records discovered, visited, robots-excluded and budget-excluded URL counts; incomplete coverage remains blocked."),
            ("seo-validation", "Every candidate SEO finding is validated against captured structured evidence before recommendations.", "Each candidate has a persisted confirmed or insufficient-evidence verdict and linked validation context."),
            ("seo-report", "Only confirmed SEO findings receive remediation recommendations.", "Recommendations reference confirmed findings and include verification steps."),
        ]
        tasks = [
            ("seo-discover", "Discover the target website and establish the crawl boundary.", Capability.NAVIGATE, ["seo-scope"]),
            ("seo-technical", "Inspect technical SEO signals on the target and discovered pages.", Capability.EXTRACT, ["seo-technical"]),
            ("seo-page", "Inspect page-level metadata, headings, content and image signals.", Capability.EXTRACT, ["seo-page"]),
            ("seo-links", "Inspect internal links and verify selected same-site destinations.", Capability.INTERACT, ["seo-links"]),
            ("seo-crawl", "Continue the deterministic same-origin crawl until discovered URLs or the page budget are exhausted.", Capability.NAVIGATE, ["seo-coverage"]),
            ("seo-validate", "Validate every candidate finding against its linked browser, DOM, network and crawl evidence.", Capability.EXTRACT, ["seo-validation"]),
            ("seo-synthesise", "Prepare recommendations only for confirmed SEO findings.", Capability.EXTRACT, ["seo-report"]),
        ]
    elif workflow == WorkflowType.QA:
        if qa_requires_site_crawl(objective):
            criteria = [
                ("qa-scope", "The target application and site-wide tested URL scope are recorded.", "The report records the seed URL and discovered/visited URL counts."),
                ("qa-availability", "Target pages load and HTTP/navigation failures are recorded.", "Each tested page has a captured navigation outcome."),
                ("qa-navigation", "Internal navigation destinations are inspected for broken-link candidates.", "Each reported broken destination has an observed failed navigation."),
                ("qa-content", "Page structure and visible content are checked for observable defects.", "Evidence supports each reported page-level defect."),
                ("qa-coverage", "Discovered same-origin QA URLs are visited or explicitly accounted for.", "The report records discovered and visited URL counts; remaining URLs keep coverage incomplete."),
                ("qa-validation", "Every candidate QA defect is validated against captured browser evidence before recommendations.", "Each candidate has a persisted confirmed or insufficient-evidence verdict and linked validation context."),
                ("qa-report", "Only confirmed QA defects receive remediation recommendations.", "Recommendations reference confirmed defects and include reproducible verification steps."),
            ]
            tasks = [
                ("qa-discover", "Discover the target application pages and establish site-wide test scope.", Capability.NAVIGATE, ["qa-scope"]),
                ("qa-availability", "Check page availability and record navigation failures.", Capability.EXTRACT, ["qa-availability"]),
                ("qa-navigation", "Inspect internal navigation and verify selected destinations.", Capability.INTERACT, ["qa-navigation"]),
                ("qa-content", "Inspect page structure and visible content for observable defects.", Capability.EXTRACT, ["qa-content"]),
                ("qa-crawl", "Continue the deterministic same-origin QA crawl until discovered URLs or the page budget are exhausted.", Capability.NAVIGATE, ["qa-coverage"]),
                ("qa-validate", "Validate each candidate QA defect against the linked DOM, image, network and runtime observations.", Capability.EXTRACT, ["qa-validation"]),
                ("qa-synthesise", "Produce a QA report with confirmed defects and tested/untested boundaries.", Capability.EXTRACT, ["qa-report"]),
            ]
        else:
            criteria = [
                ("qa-scope", "The requested page and single-page test boundary are recorded.", "Evidence identifies the supplied target URL; unrelated same-origin pages are not required."),
                ("qa-availability", "The requested page loads and its HTTP/navigation outcome is recorded.", "The target page has a captured navigation outcome."),
                ("qa-page-smoke", "The requested page title, H1 headings, image load status and browser console errors are inspected.", "Evidence records the document title, all H1 texts/count, each image load outcome and captured console/page errors."),
                ("qa-validation", "Every candidate page-level defect is validated against captured browser evidence before recommendations.", "Each candidate has a persisted confirmed or insufficient-evidence verdict and linked validation context."),
                ("qa-report", "Only confirmed page-level defects receive recommendations.", "Recommendations reference confirmed defects and include reproducible verification steps."),
            ]
            tasks = [
                ("qa-discover", "Open the supplied page and establish a single-page QA boundary.", Capability.NAVIGATE, ["qa-scope"]),
                ("qa-availability", "Record the requested page's HTTP and navigation outcome.", Capability.EXTRACT, ["qa-availability"]),
                ("qa-content", _qa_content_objective(objective), Capability.EXTRACT, ["qa-page-smoke"]),
                ("qa-validate", "Validate each candidate page defect against the captured page, image and runtime evidence.", Capability.EXTRACT, ["qa-validation"]),
                ("qa-synthesise", "Report only confirmed defects and recommendations for the requested page.", Capability.EXTRACT, ["qa-report"]),
            ]
    elif workflow == WorkflowType.SEO_QA:
        criteria = []
        tasks = []
    else:
        raise ValueError("A dedicated SEO or QA workflow is required")

    if workflow == WorkflowType.SEO_QA:
        seo = build_workflow_blueprint(objective, WorkflowType.SEO)
        qa = build_workflow_blueprint(objective + " site-wide QA crawl", WorkflowType.QA)
        criteria = [item.model_dump() for item in seo.success_criteria + qa.success_criteria]
        seo_tasks = [
            item for item in seo.subtasks
            if item.subtask_id not in {"seo-crawl", "seo-synthesise"}
        ]
        qa_tasks = [
            item for item in qa.subtasks
            if item.subtask_id not in {"qa-crawl", "qa-synthesise"}
        ]
        seo_synthesis = next(item for item in seo.subtasks if item.subtask_id == "seo-synthesise")
        qa_synthesis = next(item for item in qa.subtasks if item.subtask_id == "qa-synthesise")
        tasks = [
            item.model_dump()
            for item in [*seo_tasks, *qa_tasks, seo_synthesis, qa_synthesis]
        ]
        # Prefix IDs to keep both workstreams independent and the graph unambiguous.
        for item in criteria:
            item["criterion_id"] = "combined-" + item["criterion_id"]
        for item in tasks:
            item["subtask_id"] = "combined-" + item["subtask_id"]
            item["success_criteria"] = ["combined-" + value for value in item["success_criteria"]]
            item["depends_on"] = ["combined-" + value for value in item["depends_on"]]
        # One long-running crawl step authorises every subsequent same-origin page.
        tasks.insert(-2, {
            "subtask_id": "combined-audit-crawl",
            "objective": "Continue the deterministic same-origin crawl for both workstreams until discovered URLs or the page budget are exhausted.",
            "capability": Capability.NAVIGATE,
            "success_criteria": ["combined-seo-coverage", "combined-qa-coverage"],
            "depends_on": [],
        })
        criteria = criteria[:20]
        tasks = tasks[:20]

    return ExecutionBlueprint(
        original_objective=objective,
        interpretation={
            WorkflowType.SEO: "Website SEO audit",
            WorkflowType.QA: ("Site-wide website/application QA audit" if qa_requires_site_crawl(objective) else "Single-page QA smoke test"),
            WorkflowType.SEO_QA: "Combined SEO and QA audit with separate workstreams",
        }[workflow],
        success_criteria=[SuccessCriterion(
            criterion_id=item[0] if isinstance(item, tuple) else item["criterion_id"],
            description=item[1] if isinstance(item, tuple) else item["description"],
            evidence_required=True,
            acceptance_test=item[2] if isinstance(item, tuple) else item["acceptance_test"],
        ) for item in criteria],
        subtasks=[PlannedSubtask(
            subtask_id=item[0] if isinstance(item, tuple) else item["subtask_id"],
            objective=item[1] if isinstance(item, tuple) else item["objective"],
            capability=item[2] if isinstance(item, tuple) else item["capability"],
            required_tools=["browser_navigate", "browser_snapshot", "browser_extract"],
            success_criteria=item[3] if isinstance(item, tuple) else item["success_criteria"],
            depends_on=[],
            expected_output="Evidence-linked workflow observations; unsupported checks are explicitly untested.",
        ) for item in tasks],
        completion_policy="all_criteria_verified",
        research_queries=[],
    )
