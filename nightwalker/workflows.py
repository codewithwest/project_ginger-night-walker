"""First-phase SEO/QA workflow routing and scope classification.

SEO and QA are first-class workflows. Unknown/general research is deliberately
not used as a fallback in this product phase.
"""
from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlsplit

from nightwalker.contracts import Capability
from nightwalker.planning_contracts import (
    ExecutionBlueprint,
    PlannedSubtask,
    SuccessCriterion,
    WorkflowPlanTemplate,
)


class WorkflowType(StrEnum):
    SEO = "seo"
    QA = "qa"
    SEO_QA = "seo_qa"
    UNSUPPORTED = "unsupported"


_SEO_TERMS = {
    "seo", "search engine optimisation", "search engine optimization",
    "technical seo", "on-page seo", "on page seo", "indexability",
    "crawlability", "search visibility", "meta description", "structured data",
    "robots.txt", "sitemap.xml", "core web vitals", "page title", "title tag",
    "meta title", "h1 heading", "h1 headings", "heading structure", "alt text",
    "image alt text", "canonical url", "indexing",
}
_QA_TERMS = {
    # Explicit QA/audit language and concrete test activities only. Generic
    # nouns such as "bugs", "products", "basket", "quantity" or "search results"
    # must never turn an ordinary question into an audit.
    "qa", "quality assurance", "website qa", "web app qa", "web application qa",
    "qa audit", "qa testing", "qa test", "website testing", "web app testing",
    "web application testing", "test website", "test the website",
    "test web app", "test the web app", "test web application",
    "functional testing", "regression testing", "broken links",
    "user journey testing", "test the user journey", "usability testing",
    "accessibility testing", "responsive testing", "smoke test", "smoke testing",
    "broken images", "console error", "console errors", "browser errors",
    "find bugs", "identify bugs", "test for bugs", "find defects",
    "add to cart", "add it to cart", "to the cart", "remove from cart",
    "remove it from the cart", "shopping cart", "cart is empty",
    "cart journey", "cart flow", "do not checkout", "without checking out",
}


def classify_workflow(objective: str) -> WorkflowType:
    """Classify explicit requested work; incidental nouns are not workflow intent."""
    value = " ".join(objective.casefold().split())
    padded_value = f" {value} "
    seo = any(term in value for term in _SEO_TERMS)
    qa = any(
        (term in padded_value if term in {"qa", "smoke test"} else term in value)
        for term in _QA_TERMS
    )
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
    if any(term in value for term in ("add to cart", "add it to the cart", "to the cart", "remove from cart", "remove it from the cart", "shopping cart", "cart is empty", "do not checkout", "without checking out")):
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



def is_ecommerce_cart_workflow(objective: str) -> bool:
    """Identify an explicitly requested cart-state journey, not ordinary product research."""
    value = " ".join(objective.casefold().split())
    # Allow the product name or a short description between the action and
    # destination (for example, "add '<product>' to the cart"). Exact phrase
    # matching missed these common natural-language cart requests.
    has_add_action = bool(re.search(
        r"\b(?:add|put|place)\b.{0,120}\b(?:to|into)\s+(?:(?:the|my|our)\s+)?(?:shopping\s+)?cart\b",
        value,
    ))
    has_remove_action = bool(re.search(
        r"\bremove\b.{0,100}\b(?:from|out\s+of)\s+(?:the\s+)?(?:shopping\s+)?cart\b",
        value,
    ))
    has_remove_item_action = bool(re.search(
        r"\bremove\s+(?:it|the\s+(?:item|product))\b",
        value,
    )) and bool(re.search(r"\bcart\b", value))
    has_cart_action = has_add_action or has_remove_action or has_remove_item_action
    has_cart_verification = any(term in value for term in (
        "cart is empty", "empty cart", "verify its price", "verify the price",
        "quantity", "do not checkout", "without checking out",
    ))
    return has_cart_action and has_cart_verification


def objective_requests_search(objective: str) -> str | None:
    """Return the query when this step must type it into a search box and submit it."""
    query = extract_product_search_query(objective)
    if not query:
        return None
    value = " ".join(objective.casefold().split())
    markers = (
        "search for", "search query", "submit the search", "submit the query",
        "type the query", "enter the query", "rendered search", "use the search",
    )
    if any(marker in value for marker in markers):
        return query
    return None


def step_objective_requires_interaction(objective: str) -> bool:
    """True when a step objective needs page interaction, not navigate-only capture."""
    value = " ".join(objective.casefold().split())
    markers = (
        "search for", "search query", "submit the query", "submit the search",
        "enter the query", "type the query", "use the search", "rendered search",
        "select a product", "select the product", "identify one exact",
        "find the exact", "open its product", "product detail", "add to cart",
        "click add", "remove", "increase quantity", "decrease quantity",
        "change quantity", "update quantity", "set quantity", "checkout",
        "configure", "variant", "colour", "color", "size",
        "finish", "collection location", "store location",
    )
    return any(marker in value for marker in markers)


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


def build_workflow_blueprint(objective: str, workflow: WorkflowType) -> WorkflowPlanTemplate:
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
                ("qa-page-smoke", "The requested page's usability signals and runtime behaviour are inspected.", "Evidence records visible interactive controls, accessible names, navigation links, forms, image load failures and captured console/page errors; SEO metadata and heading rules are excluded."),
                ("qa-validation", "Every candidate page-level defect is validated against captured browser evidence before recommendations.", "Each candidate has a persisted confirmed or insufficient-evidence verdict and linked validation context."),
                ("qa-report", "Only confirmed page-level defects receive recommendations.", "Recommendations reference confirmed defects and include reproducible verification steps."),
            ]
            tasks = [
                ("qa-discover", "Open the supplied page and establish a single-page QA boundary.", Capability.NAVIGATE, ["qa-scope"]),
                ("qa-availability", "Record the requested page's HTTP and navigation outcome.", Capability.EXTRACT, ["qa-availability"]),
                ("qa-content", "Inspect visible interactive controls, links, forms, image load failures and runtime behaviour for usability issues. Do not audit SEO metadata, heading hierarchy or indexing.", Capability.EXTRACT, ["qa-page-smoke"]),
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
            "capability": Capability.NAVIGATE.value,
            "success_criteria": ["combined-seo-coverage", "combined-qa-coverage"],
            "depends_on": [],
        })
        criteria = criteria[:20]
        tasks = tasks[:20]

    def _task_capability(raw) -> str:
        if isinstance(raw, tuple):
            return _capability_value(raw[2])
        return _capability_value(raw.get("capability"))

    return WorkflowPlanTemplate(
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
            capability=_task_capability(item),
            required_tools=["browser_navigate", "browser_snapshot", "browser_extract"],
            success_criteria=item[3] if isinstance(item, tuple) else item["success_criteria"],
            depends_on=[],
            expected_output="Evidence-linked workflow observations; unsupported checks are explicitly untested.",
        ) for item in tasks],
        completion_policy="all_criteria_verified",
        research_queries=[],
    )
