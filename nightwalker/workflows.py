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
    # destination (for example, "add 'Dark Grey' to the cart"). Exact phrase
    # matching missed these common natural-language cart requests.
    has_add_action = bool(re.search(
        r"\badd\b.{0,120}\bto\s+(?:the\s+)?(?:shopping\s+)?cart\b",
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


def build_cart_journey_blueprint(objective: str) -> ExecutionBlueprint:
    """Create a sequential, explicitly interactive plan for an authorised cart test."""
    if not is_ecommerce_cart_workflow(objective):
        raise ValueError("An explicit ecommerce cart journey is required")
    criteria = [
        SuccessCriterion(
            criterion_id="sc-navigation",
            description="The requested storefront is reached.",
            acceptance_test="Capture the target storefront URL and a matching page title or visible site identity.",
        ),
        SuccessCriterion(
            criterion_id="sc-product-discovery",
            description="The requested product and its add-to-cart control are identified.",
            acceptance_test="Capture the exact matching product card, its visible name and an enabled Add to Cart control within that card.",
        ),
        SuccessCriterion(
            criterion_id="sc-product-addition",
            description="The requested product is added to the cart.",
            acceptance_test="After an authorised click, capture the product in the cart or a cart count/success state attributable to that product.",
        ),
        SuccessCriterion(
            criterion_id="sc-cart-verification",
            description="The cart's product, displayed price and quantity are verified.",
            acceptance_test="Capture the cart line item with exact product name, displayed price and quantity; do not infer missing values.",
        ),
        SuccessCriterion(
            criterion_id="sc-cart-removal",
            description="The requested product is removed from the cart.",
            acceptance_test="After an authorised remove action, capture that the product line item is absent.",
        ),
        SuccessCriterion(
            criterion_id="sc-empty-cart-confirmation",
            description="The cart is confirmed empty without checkout.",
            acceptance_test="Capture an explicit empty-cart state or zero-item cart count after removal; checkout must not be opened or submitted.",
        ),
    ]
    tasks = [
        PlannedSubtask(
            subtask_id="qa-navigation",
            objective="Navigate to the exact storefront URL and capture its identity.",
            capability=Capability.NAVIGATE,
            required_tools=["browser_navigate", "browser_snapshot"],
            success_criteria=["sc-navigation"],
            depends_on=[],
            expected_output="Target URL, page title and navigation evidence.",
        ),
        PlannedSubtask(
            subtask_id="qa-discover",
            objective="Find the exact requested product card and identify its enabled Add to Cart control. Do not click it in this step.",
            capability=Capability.INTERACT,
            required_tools=["browser_snapshot", "browser_interact"],
            success_criteria=["sc-product-discovery"],
            depends_on=["qa-navigation"],
            expected_output="Product-card identity, price/image observations and grounded add-control locator.",
        ),
        PlannedSubtask(
            subtask_id="qa-availability",
            objective="Click Add to Cart for the verified matching product only; do not checkout.",
            capability=Capability.INTERACT,
            required_tools=["browser_interact", "browser_snapshot"],
            success_criteria=["sc-product-addition"],
            depends_on=["qa-discover"],
            expected_output="Observed cart mutation and evidence that the requested item was added.",
        ),
        PlannedSubtask(
            subtask_id="qa-content",
            objective="Read the cart line item and capture the exact product name, displayed price and quantity.",
            capability=Capability.EXTRACT,
            required_tools=["browser_snapshot", "browser_extract"],
            success_criteria=["sc-cart-verification"],
            depends_on=["qa-availability"],
            expected_output="Exact cart line-item name, price, quantity and evidence source.",
        ),
        PlannedSubtask(
            subtask_id="qa-validate",
            objective="Remove only the requested product from the cart using its grounded remove control.",
            capability=Capability.INTERACT,
            required_tools=["browser_interact", "browser_snapshot"],
            success_criteria=["sc-cart-removal"],
            depends_on=["qa-content"],
            expected_output="Observed removal and evidence that the product line item is absent.",
        ),
        PlannedSubtask(
            subtask_id="qa-synthesise",
            objective="Confirm the cart is empty and report any issues. Never open or submit checkout.",
            capability=Capability.EXTRACT,
            required_tools=["browser_snapshot", "browser_extract"],
            success_criteria=["sc-empty-cart-confirmation"],
            depends_on=["qa-validate"],
            expected_output="Explicit empty-cart evidence and a concise account of verified/unverified steps.",
        ),
    ]
    return ExecutionBlueprint(
        original_objective=objective,
        interpretation="Sequential ecommerce cart QA journey with explicitly authorised add/remove actions and no checkout.",
        success_criteria=criteria,
        subtasks=tasks,
        completion_policy="all_criteria_verified",
        research_queries=[],
    )


def build_product_search_blueprint(objective: str) -> ExecutionBlueprint:
    """Build a deterministic, correctly authorised plan for exact product-search QA."""
    query = extract_product_search_query(objective)
    if not query:
        raise ValueError("A quoted product-search query is required for this workflow")

    # Product identity is always required. Other fields are required only when
    # the user asks for them; the browser may still capture additional visible
    # attributes, but unrequested fields never block task completion.
    requested_fields = {"name"}
    field_patterns = {
        "name": r"\b(name|title)\b",
        "image": r"\b(image|images|photo|photos|picture|pictures)\b",
        "price": r"\b(price|prices|cost|costs|pricing)\b",
        "product_url": r"\b(product\s+(?:url|link|page)|url|link)\b",
    }
    explicit_field_request = any(
        re.search(pattern, objective, re.IGNORECASE)
        for pattern in field_patterns.values()
    )
    for field, pattern in field_patterns.items():
        if re.search(pattern, objective, re.IGNORECASE):
            requested_fields.add(field)
    if not explicit_field_request:
        requested_fields.update({"image", "price"})

    field_criteria = {
        "name": SuccessCriterion(
            criterion_id="sc-product-name",
            description=f"A result with a product name matching {query!r} is identified.",
            acceptance_test="Extract the actual product title from a matching result; incidental page or card text is insufficient.",
        ),
        "image": SuccessCriterion(
            criterion_id="sc-product-image",
            description="The matching product image source and load state are captured.",
            acceptance_test="Capture the image source associated with the matched product and distinguish present, loaded and unconfirmed states.",
        ),
        "price": SuccessCriterion(
            criterion_id="sc-product-price",
            description="The matching product's displayed price is captured.",
            acceptance_test="Capture the visible price associated with the matched product result; do not infer missing prices.",
        ),
        "product_url": SuccessCriterion(
            criterion_id="sc-product-url",
            description="The matching product's destination URL is captured.",
            acceptance_test="Capture the product link from the matched result and retain its exact destination.",
        ),
    }
    requested_criteria = [field_criteria[field] for field in ("name", "image", "price", "product_url") if field in requested_fields]
    product_criterion_ids = [item.criterion_id for item in requested_criteria]
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
        *requested_criteria,
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
            objective=f'Extract only the requested product fields ({", ".join(sorted(requested_fields))}) from the matching search result for "{query}". Preserve exact values and provenance.',
            capability=Capability.EXTRACT,
            depends_on=["qa-availability"],
            success_criteria=product_criterion_ids,
            expected_output="Each requested product field captured independently with its source and exact value; missing fields remain explicitly unverified.",
        ),
        PlannedSubtask(
            subtask_id="qa-validate",
            objective=f'Validate each requested product field ({", ".join(sorted(requested_fields))}) against the matching result card and captured browser evidence for "{query}".',
            capability=Capability.EXTRACT,
            depends_on=["qa-content"],
            success_criteria=product_criterion_ids,
            expected_output="Evidence-linked validation verdict for every requested product field.",
        ),
        PlannedSubtask(
            subtask_id="qa-synthesise",
            objective="Summarize the search submission and each independently verified product field; clearly distinguish confirmed, failed and unverified checks.",
            capability=Capability.EXTRACT,
            depends_on=["qa-validate"],
            success_criteria=["sc-navigation", "sc-search-submission", *product_criterion_ids],
            expected_output="Concise evidence-constrained QA report with no unsupported product claims.",
        ),
    ]
    return ExecutionBlueprint(
        original_objective=objective,
        interpretation=f"Search the supplied website for {query!r} and extract the requested product fields: {', '.join(sorted(requested_fields))}.",
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
