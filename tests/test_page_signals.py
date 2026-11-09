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
