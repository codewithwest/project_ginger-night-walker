from nightwalker.audit_analysis import (
    analyse_duplicate_pages,
    content_fingerprint,
    crawl_coverage,
    normalise_page_url,
    prioritise_findings,
)


def test_normalise_page_url_removes_fragments_and_tracking_parameters():
    assert normalise_page_url("https://EXAMPLE.com/a?utm_source=x&b=2#section") == "https://example.com/a?b=2"


def test_content_fingerprint_ignores_case_and_whitespace():
    assert content_fingerprint("Hello   WORLD\npage") == content_fingerprint("hello world page")


def test_duplicate_analysis_reports_canonical_title_and_content_groups():
    pages = [
        {"url": "https://example.com/a", "canonical_url": "https://example.com/shared", "title": "Same", "content_fingerprint": "abc"},
        {"url": "https://example.com/b", "canonical_url": "https://example.com/shared", "title": "Same", "content_fingerprint": "abc"},
        {"url": "https://example.com/c", "canonical_url": "https://example.com/c", "title": "Other", "content_fingerprint": "xyz"},
    ]
    result = analyse_duplicate_pages(pages)
    assert result["pages_analysed"] == 3
    assert result["duplicate_canonical_groups"][0]["urls"] == ["https://example.com/a", "https://example.com/b"]
    assert result["duplicate_title_groups"][0]["count"] == 2
    assert result["duplicate_content_groups"][0]["count"] == 2


def test_crawl_coverage_uses_known_discovered_urls_not_whole_site_claims():
    result = crawl_coverage(
        ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        ["https://example.com/a", "https://example.com/b"],
    )
    assert result["discovered_url_count"] == 3
    assert result["discovered_urls_visited"] == 2
    assert result["known_discovered_urls_remaining"] == 1
    assert result["coverage_percent"] == 66.7
    assert "not a claim" in result["coverage_basis"]


def test_findings_are_prioritised_without_losing_evidence_fields():
    result = prioritise_findings([
        {"claim": "Minor heading issue", "severity": "low", "evidence_ids": ["ev-1"]},
        {"claim": "Broken internal link", "severity": "high", "type": "broken_internal_link", "evidence_ids": ["ev-2"]},
    ])
    assert result[0]["priority"] == "P0"
    assert result[0]["evidence_ids"] == ["ev-2"]
    assert result[1]["priority"] == "P3"
