"""Deterministic URL triage and page-priority scheduling for SEO/QA crawls."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_TRACKING_KEYS = {"gclid", "fbclid", "msclkid", "dclid", "yclid", "_ga", "_gl"}
_FACET_KEYS = {
    "sort", "sort_by", "order", "orderby", "filter", "facet", "facets",
    "min_price", "max_price", "price_min", "price_max", "color", "colour",
    "size", "brand", "availability", "view", "layout",
}
_UTILITY_SEGMENTS = {
    "cart", "checkout", "account", "login", "logout", "register", "search",
    "wishlist", "compare", "feed", "feeds", "api", "wp-admin",
}
_DETAIL_SEGMENTS = {
    "product", "products", "item", "items", "p", "sku", "service", "services",
    "solution", "solutions", "case-study", "case-studies",
}
_CATEGORY_SEGMENTS = {
    "category", "categories", "collections", "collection", "shop", "department",
    "departments", "topics", "tag", "tags",
}
_EDITORIAL_SEGMENTS = {"blog", "news", "articles", "article", "journal", "guides", "insights"}
_INFO_SEGMENTS = {"about", "contact", "faq", "faqs", "help", "support", "delivery", "returns", "shipping"}


def url_identity(value: str) -> str:
    """Comparison identity; retain meaningful query parameters and original fetch URL."""
    try:
        parts = urlsplit(str(value).strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return str(value).strip().split("#", 1)[0]
        host = parts.hostname.casefold()
        port = parts.port
        if (parts.scheme.casefold(), port) in {("http", 80), ("https", 443)}:
            port = None
        netloc = host if port is None else f"{host}:{port}"
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/":
            path = path.rstrip("/")
        query = []
        for key, val in parse_qsl(parts.query, keep_blank_values=True):
            folded = key.casefold()
            if folded.startswith("utm_") or folded in _TRACKING_KEYS or folded in {"session", "sessionid", "sid", "phpsessid"}:
                continue
            query.append((key, val))
        return urlunsplit((parts.scheme.casefold(), netloc, path, "&".join(
            f"{key}={val}" for key, val in sorted(query)
        ), ""))
    except (TypeError, ValueError):
        return str(value).strip().split("#", 1)[0]


def classify_page_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        segments = [segment.casefold() for segment in parts.path.split("/") if segment]
        if not segments:
            return "homepage"
        if any(segment in _UTILITY_SEGMENTS for segment in segments):
            return "utility"
        if any(segment in _EDITORIAL_SEGMENTS for segment in segments):
            return "editorial"
        if any(segment in _INFO_SEGMENTS for segment in segments):
            return "informational"
        if any(segment in _DETAIL_SEGMENTS for segment in segments):
            return "detail"
        if any(segment in _CATEGORY_SEGMENTS for segment in segments):
            return "category"
        if re.search(r"/(products?|items?)/[^/]+", parts.path, re.I):
            return "detail"
        if len(segments) == 1:
            return "category"
        return "other"
    except (TypeError, ValueError):
        return "other"


def _template_key(value: str, page_type: str) -> str:
    try:
        parts = urlsplit(value)
        segments = [segment for segment in parts.path.split("/") if segment]
        if page_type == "homepage":
            return "homepage"
        if page_type == "utility":
            return "utility:" + (segments[0].casefold() if segments else "")
        if page_type in {"detail", "category", "editorial", "informational"}:
            return page_type + ":" + (segments[0].casefold() if segments else "")
        return page_type + ":" + str(max(1, min(5, len(segments))))
    except (TypeError, ValueError):
        return page_type


def template_key_for_url(value: str) -> str:
    page_type = classify_page_url(value)
    return _template_key(value, page_type)


def rank_crawl_candidates(
    candidates: list[dict[str, Any]],
    visited_urls: list[str],
    *,
    workflow: str,
    inspected_template_keys: set[str] | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Deduplicate and rank candidate URLs without discarding their original fetch URLs."""
    visited = {url_identity(url) for url in visited_urls}
    templates = inspected_template_keys or set()
    selected: dict[str, dict[str, Any]] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        identity = url_identity(url)
        if not identity or identity in visited or identity in selected:
            continue
        try:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                continue
            params = {key.casefold() for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
        except ValueError:
            continue
        page_type = classify_page_url(url)
        if page_type == "utility":
            continue
        facet = any(
            key in _FACET_KEYS or key.startswith(("filter_", "filter[", "attribute_"))
            for key in params
        )
        if facet:
            continue
        pagination = any(key in {"page", "paged", "p"} for key in params) or bool(
            re.search(r"/(?:page|paged)/\d+/?$", parts.path, re.I)
        )
        template = _template_key(url, page_type)
        novel = template not in templates
        if page_type == "homepage":
            score = 120
        elif page_type == "category":
            score = 100
        elif page_type == "detail":
            score = 88
        elif page_type == "informational":
            score = 84 if workflow in {"seo", "seo_qa"} else 75
        elif page_type == "editorial":
            score = 72 if workflow in {"seo", "seo_qa"} else 68
        else:
            score = 60
        if workflow == "qa":
            score += {"detail": 4, "informational": 2, "editorial": -4}.get(page_type, 0)
        if novel:
            score += 18
        if pagination:
            score -= 35
        depth = len([segment for segment in parts.path.split("/") if segment])
        score -= min(depth, 8)
        item = dict(raw)
        item.update({
            "url": url,
            "crawl_identity": identity,
            "page_type": page_type,
            "template_key": template,
            "template_novelty": novel,
            "pagination": pagination,
            "crawl_score": score,
            "crawl_disposition": "deferred_low_priority" if pagination else "eligible",
        })
        selected[identity] = item
    ranked = sorted(
        selected.values(),
        key=lambda item: (-int(item["crawl_score"]), str(item["url"])),
    )
    return ranked[:max(1, min(1000, int(limit)))]


def crawl_candidate_metrics(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(item.get("page_type") or "unknown") for item in candidates if isinstance(item, dict))
    return {
        "queued_candidate_count": len(candidates),
        "candidate_page_types": dict(counts),
        "low_priority_pagination_count": sum(bool(item.get("pagination")) for item in candidates if isinstance(item, dict)),
        "distinct_candidate_templates": len({
            str(item.get("template_key")) for item in candidates
            if isinstance(item, dict) and item.get("template_key")
        }),
    }
