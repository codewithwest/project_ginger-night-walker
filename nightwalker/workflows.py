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
}


def classify_workflow(objective: str) -> WorkflowType:
    """Classify the requested outcome, not the nouns/entities in the objective."""
    value = " ".join(objective.casefold().split())
    seo = any(term in value for term in _SEO_TERMS)
    qa = any(term in value for term in _QA_TERMS)
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

def build_workflow_blueprint(objective: str, workflow: WorkflowType) -> ExecutionBlueprint:
    if workflow == WorkflowType.SEO:
        criteria = [
            ("seo-scope", "The target website and inspected URL scope are recorded.", "At least one target URL and its captured source are recorded."),
            ("seo-technical", "Technical crawl and indexability signals are inspected.", "Evidence records observed HTTP outcome, canonical/robots directives when present, and sitemap/robots discovery outcome."),
            ("seo-page", "Page metadata, headings, visible content and image alternatives are inspected.", "Captured page evidence supports each reported title, description, canonical, heading, language, viewport and image-alt observation."),
            ("seo-links", "Internal link structure and broken-link candidates are assessed.", "Observed internal links are recorded and any failed destination is evidenced."),
            ("seo-coverage", "Discovered same-origin URLs are visited or explicitly accounted for.", "The report records discovered, visited, robots-excluded and budget-excluded URL counts; incomplete coverage remains blocked."),
            ("seo-report", "SEO findings are prioritised with affected URLs and actionable remediation.", "Every reported issue has evidence, affected URL, impact rationale and a recommended fix."),
        ]
        tasks = [
            ("seo-discover", "Discover the target website and establish the crawl boundary.", Capability.NAVIGATE, ["seo-scope"]),
            ("seo-technical", "Inspect technical SEO signals on the target and discovered pages.", Capability.EXTRACT, ["seo-technical"]),
            ("seo-page", "Inspect page-level metadata, headings, content and image signals.", Capability.EXTRACT, ["seo-page"]),
            ("seo-links", "Inspect internal links and verify selected same-site destinations.", Capability.INTERACT, ["seo-links"]),
            ("seo-crawl", "Continue the deterministic same-origin crawl until discovered URLs or the page budget are exhausted.", Capability.NAVIGATE, ["seo-coverage"]),
            ("seo-synthesise", "Prioritise evidence-backed SEO issues and remediation.", Capability.EXTRACT, ["seo-report"]),
        ]
    elif workflow == WorkflowType.QA:
        if qa_requires_site_crawl(objective):
            criteria = [
                ("qa-scope", "The target application and site-wide tested URL scope are recorded.", "The report records the seed URL and discovered/visited URL counts."),
                ("qa-availability", "Target pages load and HTTP/navigation failures are recorded.", "Each tested page has a captured navigation outcome."),
                ("qa-navigation", "Internal navigation destinations are inspected for broken-link candidates.", "Each reported broken destination has an observed failed navigation."),
                ("qa-content", "Page structure and visible content are checked for observable defects.", "Evidence supports each reported page-level defect."),
                ("qa-coverage", "Discovered same-origin QA URLs are visited or explicitly accounted for.", "The report records discovered and visited URL counts; remaining URLs keep coverage incomplete."),
                ("qa-report", "QA observations are reported with reproducible URLs and honest test status.", "Each defect includes observed behaviour, URL and evidence; unexecuted interactions are marked untested."),
            ]
            tasks = [
                ("qa-discover", "Discover the target application pages and establish site-wide test scope.", Capability.NAVIGATE, ["qa-scope"]),
                ("qa-availability", "Check page availability and record navigation failures.", Capability.EXTRACT, ["qa-availability"]),
                ("qa-navigation", "Inspect internal navigation and verify selected destinations.", Capability.INTERACT, ["qa-navigation"]),
                ("qa-content", "Inspect page structure and visible content for observable defects.", Capability.EXTRACT, ["qa-content"]),
                ("qa-crawl", "Continue the deterministic same-origin QA crawl until discovered URLs or the page budget are exhausted.", Capability.NAVIGATE, ["qa-coverage"]),
                ("qa-synthesise", "Produce a QA report with evidence and tested/untested boundaries.", Capability.EXTRACT, ["qa-report"]),
            ]
        else:
            criteria = [
                ("qa-scope", "The requested page and single-page test boundary are recorded.", "Evidence identifies the supplied target URL; unrelated same-origin pages are not required."),
                ("qa-availability", "The requested page loads and its HTTP/navigation outcome is recorded.", "The target page has a captured navigation outcome."),
                ("qa-page-smoke", "The requested page title, H1 headings, image load status and browser console errors are inspected.", "Evidence records the document title, all H1 texts/count, each image load outcome and captured console/page errors."),
                ("qa-report", "Confirmed page-level defects are reported with reproducible evidence and recommendations.", "Each defect includes the target URL, observed result, evidence and recommendation; unverified checks are marked untested."),
            ]
            tasks = [
                ("qa-discover", "Open the supplied page and establish a single-page QA boundary.", Capability.NAVIGATE, ["qa-scope"]),
                ("qa-availability", "Record the requested page's HTTP and navigation outcome.", Capability.EXTRACT, ["qa-availability"]),
                ("qa-content", "Inspect the document title, H1 headings, broken images and browser console/page errors on this page only.", Capability.EXTRACT, ["qa-page-smoke"]),
                ("qa-synthesise", "Report confirmed defects and recommendations for the requested page only.", Capability.EXTRACT, ["qa-report"]),
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
        criteria = criteria[:12]
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
