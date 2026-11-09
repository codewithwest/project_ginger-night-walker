from nightwalker.audit_analysis import prioritise_findings
from nightwalker.finding_validation import validate_findings


def test_finding_without_evidence_is_not_confirmed():
    findings, summary = validate_findings(
        [{"finding_id": "f-1", "claim": "Rs 1200", "type": "product_price"}],
        [],
        {"products": [{"name": "Item", "price": "Rs 1200"}]},
    )
    assert findings[0]["validation"]["verdict"] == "insufficient_evidence"
    assert summary["insufficient_evidence"] == 1
    prioritised = prioritise_findings(findings)
    assert "remediation" not in prioritised[0]
    assert prioritised[0]["recommendation_status"] == "blocked_until_confirmed"


def test_broken_image_requires_matching_structured_load_failure():
    evidence = [{"evidence_id": "ev-1", "url": "https://example.com", "captured_text": "image state"}]
    structured = {"qa_page_smoke": [{
        "source_id": "s-1", "evidence_ids": ["ev-1"],
        "images": [{"src": "https://example.com/broken.png", "load_status": "broken"}],
    }]}
    findings, summary = validate_findings(
        [{"finding_id": "f-2", "claim": "Image failed", "type": "qa_broken_image",
          "image_url": "https://example.com/broken.png", "source_ids": ["s-1"],
          "evidence_ids": ["ev-1"]}],
        evidence, structured,
    )
    assert findings[0]["validation"]["verdict"] == "confirmed"
    assert summary["confirmed"] == 1
    assert findings[0]["validation"]["recommendation_context"]["observations"]


def test_unrelated_evidence_does_not_confirm_broken_image():
    evidence = [{"evidence_id": "ev-1", "url": "https://example.com"}]
    structured = {"qa_page_smoke": [{
        "source_id": "s-1", "evidence_ids": ["ev-1"],
        "images": [{"src": "https://example.com/ok.png", "load_status": "loaded"}],
    }]}
    findings, _ = validate_findings(
        [{"claim": "Image failed", "type": "qa_broken_image",
          "image_url": "https://example.com/broken.png", "source_ids": ["s-1"],
          "evidence_ids": ["ev-1"]}],
        evidence, structured,
    )
    assert findings[0]["validation"]["verdict"] == "insufficient_evidence"


def test_skip_recommendations_env_keeps_validation(monkeypatch):
    monkeypatch.setenv("AUDIT_SKIP_RECOMMENDATIONS", "true")
    findings, summary = validate_findings(
        [{"claim": "Candidate", "type": "unknown", "evidence_ids": ["ev-1"]}],
        [{"evidence_id": "ev-1"}], {},
    )
    prioritised = prioritise_findings(findings)
    assert findings[0]["validation"]["verdict"] == "insufficient_evidence"
    assert summary["recommendations_skipped"] is True
    assert "remediation" not in prioritised[0]
    assert prioritised[0]["recommendation_status"] == "skipped_by_configuration"


def test_seo_warning_requires_the_claimed_signal_not_just_any_seo_record():
    evidence = [{"evidence_id": "ev-2", "source_url": "https://example.com"}]
    base = {"source_id": "s-2", "evidence_ids": ["ev-2"], "title": "A useful page title",
            "title_length": 21, "meta_description": "A valid description"}
    finding = {"claim": "Page title exceeds 60 characters; review truncation risk.",
               "type": "seo_warning", "source_ids": ["s-2"], "evidence_ids": ["ev-2"]}
    findings, _ = validate_findings([finding], evidence, {"seo_checks": [base]})
    assert findings[0]["validation"]["verdict"] == "insufficient_evidence"
    base["title"] = "A" * 70
    base["title_length"] = 70
    findings, _ = validate_findings([finding], evidence, {"seo_checks": [base]})
    assert findings[0]["validation"]["verdict"] == "confirmed"


def test_browser_error_must_match_a_captured_console_or_page_error():
    evidence = [{"evidence_id": "ev-3"}]
    finding = {"claim": "Console error: TypeError: missing function", "type": "qa_browser_error",
               "source_ids": ["s-3"], "evidence_ids": ["ev-3"]}
    structured = {"qa_runtime_events": [
        {"source_id": "s-3", "evidence_ids": ["ev-3"], "kind": "console_error",
         "message": "TypeError: missing function"},
    ]}
    findings, _ = validate_findings([finding], evidence, structured)
    assert findings[0]["validation"]["verdict"] == "confirmed"
    structured["qa_runtime_events"][0]["message"] = "A different error"
    findings, _ = validate_findings([finding], evidence, structured)
    assert findings[0]["validation"]["verdict"] == "insufficient_evidence"


def test_off_origin_redirect_requires_captured_redirect_evidence():
    evidence = [{"evidence_id": "ev-4"}]
    finding = {"claim": "Internal link redirects outside the audited origin",
               "type": "off_origin_internal_redirect", "source_ids": ["s-4"],
               "evidence_ids": ["ev-4"]}
    structured = {"internal_link_checks": [{
        "source_id": "s-4", "evidence_ids": ["ev-4"],
        "checks": [{"url": "https://example.com/out", "ok": False,
                    "off_origin_redirect": True, "final_url": "https://other.example/"}],
    }]}
    findings, _ = validate_findings([finding], evidence, structured)
    assert findings[0]["validation"]["verdict"] == "confirmed"


def test_accessibility_finding_requires_the_matching_semantic_signal():
    evidence = [{"evidence_id": "ev-5"}]
    finding = {"claim": "2 visible link(s) lack a detectable accessible name.",
               "type": "qa_accessibility_issue", "source_ids": ["s-5"],
               "evidence_ids": ["ev-5"]}
    structured = {"qa_extended": [{
        "source_id": "s-5", "evidence_ids": ["ev-5"],
        "semantic_accessibility": {"links_without_accessible_name": 0,
                                   "buttons_without_accessible_name": 2},
    }]}
    findings, _ = validate_findings([finding], evidence, structured)
    assert findings[0]["validation"]["verdict"] == "insufficient_evidence"
    structured["qa_extended"][0]["semantic_accessibility"]["links_without_accessible_name"] = 2
    findings, _ = validate_findings([finding], evidence, structured)
    assert findings[0]["validation"]["verdict"] == "confirmed"
