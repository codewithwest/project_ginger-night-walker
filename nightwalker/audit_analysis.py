"""Deterministic crawl aggregation and report prioritisation helpers."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any
from nightwalker.crawl_intelligence import url_identity


def normalise_page_url(value: str) -> str:
    """Use crawler URL identity consistently for coverage and duplicate comparisons."""
    return url_identity(value)


def content_fingerprint(text: str) -> str:
    """Stable fingerprint of visible text after whitespace and case normalisation."""
    normalised = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest() if normalised else ""


def analyse_duplicate_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Find exact canonical, title and visible-content duplicates in inspected pages."""
    canonical_groups: dict[str, list[str]] = defaultdict(list)
    title_groups: dict[str, list[str]] = defaultdict(list)
    description_groups: dict[str, list[str]] = defaultdict(list)
    content_groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        url = str(page.get("url") or "")
        canonical = normalise_page_url(str(page.get("canonical_url") or ""))
        title = re.sub(r"\s+", " ", str(page.get("title") or "")).strip().casefold()
        description = re.sub(r"\s+", " ", str(page.get("meta_description") or "")).strip().casefold()
        fingerprint = str(page.get("content_fingerprint") or "")
        if canonical:
            canonical_groups[canonical].append(url)
        if title:
            title_groups[title].append(url)
        if description:
            description_groups[description].append(url)
        if fingerprint:
            content_groups[fingerprint].append(url)

    def groups(source: dict[str, list[str]]) -> list[dict[str, Any]]:
        return [
            {"identity": key, "urls": list(dict.fromkeys(urls)), "count": len(set(urls))}
            for key, urls in source.items() if len(set(urls)) > 1
        ]

    return {
        "pages_analysed": len(pages),
        "duplicate_canonical_groups": groups(canonical_groups),
        "duplicate_title_groups": groups(title_groups),
        "duplicate_meta_description_groups": groups(description_groups),
        "duplicate_content_groups": groups(content_groups),
    }


def crawl_coverage(
    discovered_urls: list[str], visited_urls: list[str],
    excluded_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Report eligible coverage while explicitly accounting for policy-excluded URL variants."""
    discovered = {normalise_page_url(url) for url in discovered_urls if url}
    visited = {normalise_page_url(url) for url in visited_urls if url}
    excluded = {normalise_page_url(url) for url in (excluded_urls or []) if url} - visited
    eligible = discovered - excluded
    covered = eligible & visited
    remaining = eligible - visited
    return {
        "discovered_url_count": len(discovered),
        "eligible_url_count": len(eligible),
        "excluded_url_count": len(excluded),
        "visited_url_count": len(visited & eligible),
        "discovered_urls_visited": len(covered),
        "known_discovered_urls_remaining": len(remaining),
        "coverage_percent": round((len(covered) / len(eligible)) * 100, 1) if eligible else None,
        "coverage_basis": "Eligible URLs discovered from inspected internal links and parsed sitemap entries after explicit utility/filter exclusions; not a claim of exhaustive site coverage.",
        "unvisited_sample": sorted(remaining)[:100],
    }


_PRIORITY = {
    "critical": (100, "P0"),
    "high": (80, "P1"),
    "medium": (55, "P2"),
    "low": (30, "P3"),
    "info": (10, "P4"),
}

_REMEDIATION = {
    "broken_internal_link": "Correct the href, restore the destination, or add an intentional redirect; verify the final destination returns a successful response.",
    "off_origin_internal_redirect": "Review the redirect target. Keep internal navigation on the intended origin, or make the cross-site hand-off explicit and expected.",
    "duplicate_canonical": "Choose one preferred URL and align canonical tags, redirects and internal links with that URL.",
    "duplicate_title": "Write a distinct, descriptive title that reflects each page's unique search intent.",
    "duplicate_meta_description": "Write a distinct summary for each indexable page that accurately describes its content.",
    "duplicate_content": "Consolidate near-identical pages or add genuinely distinct, useful content and clear canonical signals.",
    "qa_responsive_issue": "Inspect the reported viewport and remove the element or layout rule causing horizontal overflow.",
    "qa_navigation_failure": "Repair the destination or redirect so the same-origin link reaches a valid page; confirm the final URL remains within the intended site boundary.",
    "qa_interaction_issue": "Review the disclosure control's event handling and expanded/collapsed state so it responds predictably and can be restored.",
    "qa_browser_error": "Reproduce the browser exception or console error, identify the responsible script/resource and correct the underlying failure.",
    "qa_accessibility_issue": "Provide a programmatic accessible name or label, and ensure hidden content is not keyboard-focusable.",
    "qa_accessibility_warning": "Review semantic landmarks and keyboard navigation, then validate with an automated WCAG testing tool.",
    "seo_issue": "Correct the affected page's technical or on-page SEO signal and verify the rendered page again.",
    "seo_warning": "Review the affected page and make the metadata or document structure clearer and more consistent.",
}

_IMPACT = {
    "broken_internal_link": "Users and crawlers may be unable to reach the linked destination.",
    "off_origin_internal_redirect": "An internal link unexpectedly leaves the audited site, which can break navigation expectations and crawl paths.",
    "duplicate_canonical": "Conflicting canonical signals can fragment indexing and ranking signals.",
    "duplicate_title": "Repeated titles make page purpose less distinct in search results.",
    "duplicate_meta_description": "Repeated snippets reduce the uniqueness and relevance of search-result descriptions.",
    "duplicate_content": "Substantially repeated page content can dilute indexation and make page selection ambiguous.",
    "qa_responsive_issue": "Horizontal overflow can hide content or controls on smaller screens.",
    "qa_navigation_failure": "Broken or off-origin navigation can strand users or move them outside the expected application flow.",
    "qa_interaction_issue": "A non-responsive disclosure can hide content or leave a control in an inconsistent state.",
    "qa_browser_error": "Uncaught browser errors may break page functionality or prevent important interactions from working.",
    "qa_accessibility_issue": "Assistive technology or keyboard users may not be able to identify or operate the affected control.",
}


def prioritise_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add deterministic impact/priority fields while preserving original evidence."""
    output = []
    for finding in findings:
        item = dict(finding)
        severity = str(item.get("severity") or "info").casefold()
        score, priority = _PRIORITY.get(severity, _PRIORITY["info"])
        kind = str(item.get("type") or "")
        if "broken" in kind or "availability" in kind:
            score = min(100, score + 10)
            priority = "P0" if score >= 100 else "P1" if score >= 80 else priority
        item["impact_score"] = score
        item["priority"] = priority
        item.setdefault("impact_rationale", _IMPACT.get(kind, "This issue may affect search visibility, usability or reliability; validate against the affected page and business context."))
        item.setdefault("remediation", _REMEDIATION.get(kind, "Review the evidence, correct the underlying page issue and re-run the affected check."))
        item.setdefault("effort", "low" if kind in {"broken_internal_link", "seo_warning"} else "medium")
        item.setdefault("affected_urls", [])
        output.append(item)
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    return sorted(output, key=lambda item: (order.get(item["priority"], 5), -item["impact_score"], item.get("claim", "")))
