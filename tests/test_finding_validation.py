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
