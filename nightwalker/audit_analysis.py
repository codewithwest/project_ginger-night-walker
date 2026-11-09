"""Deterministic crawl aggregation and report prioritisation helpers."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalise_page_url(value: str) -> str:
    """Remove fragments and common tracking parameters for page identity."""
    try:
        parts = urlsplit(str(value).strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return str(value).strip()
        tracking = {"gclid", "fbclid", "msclkid"}
        query = [
            (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in tracking
        ]
        return urlunsplit((
            parts.scheme.casefold(), parts.netloc.casefold(),
            parts.path or "/", urlencode(sorted(query)), "",
        ))
    except (TypeError, ValueError):
        return str(value).strip()


def content_fingerprint(text: str) -> str:
    """Stable fingerprint of visible text after whitespace and case normalisation."""
    normalised = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest() if normalised else ""


def analyse_duplicate_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Find exact canonical, title and visible-content duplicates in inspected pages."""
    canonical_groups: dict[str, list[str]] = defaultdict(list)
    title_groups: dict[str, list[str]] = defaultdict(list)
    content_groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        url = str(page.get("url") or "")
        canonical = normalise_page_url(str(page.get("canonical_url") or ""))
        title = re.sub(r"\s+", " ", str(page.get("title") or "")).strip().casefold()
        fingerprint = str(page.get("content_fingerprint") or "")
        if canonical:
            canonical_groups[canonical].append(url)
        if title:
            title_groups[title].append(url)
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
        "duplicate_content_groups": groups(content_groups),
    }


def crawl_coverage(discovered_urls: list[str], visited_urls: list[str]) -> dict[str, Any]:
    """Report coverage against the known discovered URL set; never imply whole-web coverage."""
    discovered = {normalise_page_url(url) for url in discovered_urls if url}
    visited = {normalise_page_url(url) for url in visited_urls if url}
    covered = discovered & visited
    return {
        "discovered_url_count": len(discovered),
        "visited_url_count": len(visited),
        "discovered_urls_visited": len(covered),
        "known_discovered_urls_remaining": len(discovered - visited),
        "coverage_percent": round((len(covered) / len(discovered)) * 100, 1) if discovered else None,
        "coverage_basis": "URLs discovered from inspected internal links and parsed sitemap entries; not a claim of exhaustive site coverage.",
        "unvisited_sample": sorted(discovered - visited)[:100],
    }


_PRIORITY = {
    "critical": (100, "P0"),
    "high": (80, "P1"),
    "medium": (55, "P2"),
    "low": (30, "P3"),
    "info": (10, "P4"),
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
        item.setdefault("effort", "medium")
        item.setdefault("affected_urls", [])
        output.append(item)
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    return sorted(output, key=lambda item: (order.get(item["priority"], 5), -item["impact_score"], item.get("claim", "")))
