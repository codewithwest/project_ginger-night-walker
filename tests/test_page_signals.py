from nightwalker.page_signals import page_signal_findings


def test_seo_signals_report_missing_metadata_and_indexing_risks():
    signals = {
        "seo": {
            "title": None,
            "title_length": 0,
            "meta_description": None,
            "h1_count": 0,
            "images_missing_alt": 2,
            "canonical_url": "https://other.example/",
            "canonical_is_same_origin": False,
            "noindex": True,
            "json_ld_blocks": [{"valid_json": False}],
            "heading_levels": [1, 3],
        }
    }
    findings = page_signal_findings(signals, "seo")
    claims = [item["claim"] for item in findings]
    assert "Page title is missing." in claims
    assert "Meta description is missing." in claims
    assert "Canonical URL points to a different origin." in claims
    assert "A noindex directive is present." in claims
    assert "At least one JSON-LD block contains invalid JSON." in claims
    assert "Heading levels skip one or more ranks; review document outline." in claims


def test_qa_findings_are_not_emitted_for_seo_only_workflow():
    signals = {"qa": {"controls_without_accessible_name": 2, "duplicate_ids": ["main"]}}
    assert page_signal_findings(signals, "seo") == []


def test_combined_workflow_includes_seo_and_qa_observations():
    signals = {
        "seo": {"title": None, "title_length": 0},
        "qa": {"controls_without_accessible_name": 1, "duplicate_ids": ["main"]},
    }
    findings = page_signal_findings(signals, "seo_qa")
    assert any(item["type"] == "seo_issue" for item in findings)
    assert any(item["type"] == "qa_accessibility_warning" for item in findings)
    assert any(item["type"] == "qa_dom_issue" for item in findings)


def test_seo_signals_report_missing_canonical_language_and_viewport():
    findings = page_signal_findings({
        "seo": {
            "title": "Example",
            "title_length": 7,
            "meta_description": "A useful description for this example page that has enough detail.",
            "canonical_url": None,
            "language": None,
            "viewport": None,
            "h1_count": 1,
            "images_missing_alt": 0,
            "heading_levels": [1, 2],
            "json_ld_blocks": [],
        },
        "qa": {},
    }, "seo")
    claims = [item["claim"] for item in findings]
    assert "Canonical link element is missing." in claims
    assert "Document language attribute is missing." in claims
    assert "Viewport meta tag is missing." in claims


def test_qa_page_signals_detect_accessibility_and_duplicate_id_heuristics():
    findings = page_signal_findings({
        "seo": {},
        "qa": {
            "controls_without_accessible_name": 2,
            "duplicate_ids": ["menu"],
        },
    }, "qa")
    assert any("visible control(s)" in item["claim"] for item in findings)
    assert any("Duplicate HTML IDs" in item["claim"] for item in findings)


def test_off_origin_redirect_is_not_counted_as_a_broken_http_destination():
    from nightwalker.audit_analysis import prioritise_findings

    finding = {
        "claim": "Internal link redirects outside the audited origin",
        "type": "off_origin_internal_redirect",
        "severity": "medium",
        "affected_urls": ["https://example.com/account"],
    }
    result = prioritise_findings([finding])
    assert result[0]["priority"] == "P2"
    assert "redirect target" in result[0]["remediation"].lower()


def test_internal_link_redirects_are_followed_only_within_origin():
    import asyncio
    from nightwalker.page_signals import collect_internal_link_checks

    class Locator:
        async def evaluate_all(self, _script):
            return ["https://example.com/old"]

    class Response:
        def __init__(self, status, headers=None, url=""):
            self.status = status
            self.headers = headers or {}
            self.url = url

    class Request:
        def __init__(self):
            self.calls = []

        async def head(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/old"):
                return Response(301, {"location": "/new"}, url)
            return Response(200, {}, url)

        async def get(self, url, **kwargs):
            raise AssertionError("GET fallback should not be needed")

    class Policy:
        def __init__(self):
            self.validated = []

        def validate_url(self, url, allow_private_networks):
            self.validated.append(url)
            assert allow_private_networks is False
            assert url.startswith("https://example.com/")

    class Page:
        url = "https://example.com/"
        locator = lambda self, _selector: Locator()

        def __init__(self):
            self.request = Request()

    page, policy = Page(), Policy()
    result = asyncio.run(collect_internal_link_checks(page, policy=policy))
    assert result["checked_count"] == 1
    assert result["checks"][0]["ok"] is True
    assert result["checks"][0]["redirect_hops"] == 1
    assert [call[0] for call in page.request.calls] == [
        "https://example.com/old", "https://example.com/new"
    ]
    assert all(call[1]["max_redirects"] == 0 for call in page.request.calls)
    assert policy.validated == ["https://example.com/old", "https://example.com/new"]


def test_internal_link_off_origin_redirect_is_not_followed():
    import asyncio
    from nightwalker.page_signals import collect_internal_link_checks

    class Locator:
        async def evaluate_all(self, _script):
            return ["https://example.com/account"]

    class Response:
        status = 302
        headers = {"location": "https://attacker.example/landing"}

    class Request:
        calls = []

        async def head(self, url, **kwargs):
            self.calls.append(url)
            return Response()

    class Policy:
        def validate_url(self, url, allow_private_networks):
            assert "attacker.example" not in url

    class Page:
        url = "https://example.com/"
        request = Request()

        def locator(self, _selector):
            return Locator()

    result = asyncio.run(collect_internal_link_checks(Page(), policy=Policy()))
    assert result["off_origin_redirect_count"] == 1
    assert result["broken_count"] == 0
    assert result["checks"][0]["ok"] is False
    assert result["checks"][0]["off_origin_redirect"] is True
    assert Page.request.calls == ["https://example.com/account"]


def test_qa_extended_findings_cover_responsive_and_deeper_semantic_failures():
    from nightwalker.page_signals import qa_extended_findings

    findings = qa_extended_findings({
        "responsive_viewports": [{
            "viewport": "mobile", "viewport_width": 375, "horizontal_overflow": True,
        }],
        "semantic_accessibility": {
            "visible_form_controls_without_label": 1,
            "broken_aria_labelledby_references": 2,
            "generic_link_names": 1,
            "nested_interactive_controls": 1,
            "positive_tabindex_count": 2,
            "tables_without_header_cells": 1,
            "target_blank_without_noopener": 1,
            "multiple_main_landmarks": True,
            "document_title_missing": True,
            "document_language_missing": True,
            "landmark_counts": {"main": 2},
        },
    }, "https://example.com/contact")
    types = [item["type"] for item in findings]
    claims = " ".join(item["claim"] for item in findings)
    assert "qa_responsive_issue" in types
    assert types.count("qa_accessibility_issue") >= 3
    assert "aria-labelledby" in claims
    assert "positive tabindex" in claims
    assert "nested interactive" in claims
    assert all(item["affected_urls"] == ["https://example.com/contact"] for item in findings)
