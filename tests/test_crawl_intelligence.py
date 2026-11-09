from nightwalker.crawl_intelligence import (
    classify_page_url,
    rank_crawl_candidates,
    url_identity,
)


def test_url_identity_ignores_tracking_but_preserves_meaningful_query():
    assert url_identity("https://EXAMPLE.com/products/chair?utm_source=x&gclid=123") == (
        "https://example.com/products/chair"
    )
    assert url_identity("https://example.com/search?q=chair") != url_identity(
        "https://example.com/search?q=table"
    )


def test_page_classifier_recognises_common_templates():
    assert classify_page_url("https://example.com/") == "homepage"
    assert classify_page_url("https://example.com/collections/lounge") == "category"
    assert classify_page_url("https://example.com/products/chair") == "detail"
    assert classify_page_url("https://example.com/blog/design-trends") == "editorial"
    assert classify_page_url("https://example.com/contact") == "informational"
    assert classify_page_url("https://example.com/cart") == "utility"


def test_scheduler_prioritises_new_templates_and_keeps_original_fetch_url():
    candidates = [
        {"url": "https://example.com/products/chair?utm_source=mail", "label": "Chair"},
        {"url": "https://example.com/products/table", "label": "Table"},
        {"url": "https://example.com/collections/lounge", "label": "Lounge"},
        {"url": "https://example.com/products/chair?sort=price", "label": "Sorted chair"},
        {"url": "https://example.com/cart", "label": "Cart"},
        {"url": "https://example.com/blog/design", "label": "Design"},
        {"url": "https://example.com/products?page=2", "label": "Page two"},
    ]
    ranked = rank_crawl_candidates(
        candidates,
        ["https://example.com/products/chair"],
        workflow="seo",
        inspected_template_keys={"detail:products"},
    )
    urls = [item["url"] for item in ranked]
    assert "https://example.com/products/chair?utm_source=mail" not in urls
    assert "https://example.com/products/chair?sort=price" not in urls
    assert "https://example.com/cart" not in urls
    assert "https://example.com/collections/lounge" in urls
    assert "https://example.com/products?page=2" in urls
    assert urls.index("https://example.com/collections/lounge") < urls.index(
        "https://example.com/products?page=2"
    )


def test_scheduler_prefers_template_novelty_and_qa_specific_weights():
    candidates = [
        {"url": "https://example.com/blog/news", "label": "News"},
        {"url": "https://example.com/products/item-1", "label": "Item"},
        {"url": "https://example.com/contact", "label": "Contact"},
    ]
    seo = rank_crawl_candidates(candidates, [], workflow="seo")
    qa = rank_crawl_candidates(candidates, [], workflow="qa")
    assert seo[0]["page_type"] == "detail"
    assert qa[0]["page_type"] == "detail"
    assert all(item["template_novelty"] for item in seo)


def test_excluded_variants_are_accounted_for_without_hiding_valid_pages():
    from nightwalker.audit_analysis import crawl_coverage
    from nightwalker.crawl_intelligence import candidate_exclusion_reason

    discovered = [
        "https://example.com/products/chair",
        "https://example.com/products/chair?utm_source=mail",
        "https://example.com/products/chair?sort=price",
        "https://example.com/cart",
        "https://example.com/products/table",
    ]
    excluded = [url for url in discovered if candidate_exclusion_reason(url) is not None]
    coverage = crawl_coverage(
        discovered,
        ["https://example.com/products/chair?utm_source=mail"],
        excluded_urls=excluded,
    )
    assert coverage["discovered_url_count"] == 4
    assert coverage["eligible_url_count"] == 2
    assert coverage["excluded_url_count"] == 2
    assert coverage["known_discovered_urls_remaining"] == 1
    assert coverage["coverage_percent"] == 50.0
