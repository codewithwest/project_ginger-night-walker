"""Evidence-first finding validation and recommendation gating.

Validation is deliberately conservative: an evidence reference alone is not proof.
A candidate is confirmed only when a matching structured observation supports it.
"""
from __future__ import annotations

import os
from typing import Any


def recommendations_skipped() -> bool:
    return os.getenv("AUDIT_SKIP_RECOMMENDATIONS", "false").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _linked_records(finding: dict[str, Any], structured_data: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_ids = set(finding.get("evidence_ids") or [])
    source_ids = set(finding.get("source_ids") or [])
    records: list[dict[str, Any]] = []
    for key, values in structured_data.items():
        values = values if isinstance(values, list) else [values]
        for value in values:
            if not isinstance(value, dict):
                continue
            linked = bool(evidence_ids.intersection(value.get("evidence_ids") or []))
            linked = linked or bool(source_ids and value.get("source_id") in source_ids)
            if linked:
                records.append({"dataset": key, **value})
    return records


def _support_for_type(
    finding: dict[str, Any], records: list[dict[str, Any]],
) -> tuple[bool, str, list[dict[str, Any]]]:
    kind = str(finding.get("type") or "")
    claim = str(finding.get("claim") or "").casefold()
    matched: list[dict[str, Any]] = []

    if kind == "qa_broken_image":
        matched = [r for r in records if r.get("dataset") in {"qa_page_smoke", "seo_checks", "page_signals"}
                   and any(i.get("load_status") == "broken" and
                           (not finding.get("image_url") or i.get("src") == finding.get("image_url"))
                           for i in r.get("images", []) if isinstance(i, dict))]
    elif kind == "qa_browser_error":
        matched = [r for r in records if r.get("dataset") == "qa_runtime_events"
                   and any(e.get("kind") in {"console_error", "page_error"}
                           for e in (r.get("events") or r.get("runtime_events") or []) if isinstance(e, dict))]
    elif kind in {"broken_internal_link", "qa_navigation_failure"}:
        matched = [r for r in records if r.get("dataset") in {"internal_link_checks", "qa_navigation_journeys"}
                   and any(c.get("ok") is False and not c.get("off_origin_redirect")
                           for c in r.get("checks", []) if isinstance(c, dict))]
    elif kind.startswith("duplicate_"):
        group_key = {
            "duplicate_canonical": "duplicate_canonical_groups",
            "duplicate_title": "duplicate_title_groups",
            "duplicate_meta_description": "duplicate_meta_description_groups",
            "duplicate_content": "duplicate_content_groups",
        }.get(kind)
        matched = [r for r in records if r.get("dataset") == "duplicate_analysis"
                   and bool((r.get(group_key) or []))]
    elif kind == "qa_responsive_issue":
        matched = [r for r in records if r.get("dataset") == "qa_extended"
                   and any(v.get("horizontal_overflow") for v in r.get("responsive_viewports", [])
                           if isinstance(v, dict))]
    elif kind in {"seo_issue", "seo_warning", "qa_page_issue", "qa_accessibility_issue",
                  "qa_accessibility_warning", "qa_dom_issue", "qa_interaction_issue"}:
        # Require the finding to be tied to a captured signal record, not just a prose claim.
        matched = [r for r in records if r.get("dataset") in {
            "seo_checks", "qa_checks", "qa_page_smoke", "qa_extended", "page_signals",
        }]
    else:
        # Unknown finding types remain unverified until a dedicated validator exists.
        return False, "No type-specific validator exists for this finding type.", []

    if matched:
        return True, "A finding-type-specific structured observation supports the claim.", matched
    if not records:
        return False, "No structured observations are linked to the finding's evidence/source references.", []
    return False, "Linked observations do not confirm the claimed defect.", []


def validate_findings(
    findings: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    structured_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach an auditable validation packet and conservative verdict to every finding."""
    known_evidence = {str(item.get("evidence_id")) for item in evidence if item.get("evidence_id")}
    validated: list[dict[str, Any]] = []
    counts = {"confirmed": 0, "rejected": 0, "insufficient_evidence": 0}
    for original in findings:
        item = dict(original)
        evidence_ids = [str(value) for value in item.get("evidence_ids", []) if value]
        missing_refs = [value for value in evidence_ids if value not in known_evidence]
        linked = _linked_records(item, structured_data)
        if not evidence_ids or missing_refs:
            supported, reason, matched = False, (
                "Finding has no evidence reference." if not evidence_ids
                else "One or more referenced evidence IDs are absent from the captured evidence store."
            ), []
        else:
            supported, reason, matched = _support_for_type(item, linked)
        verdict = "confirmed" if supported else "insufficient_evidence"
        counts[verdict] += 1
        item["validation"] = {
            "status": "validated" if supported else "needs_more_evidence",
            "verdict": verdict,
            "confidence": 1.0 if supported else 0.0,
            "validation_method": "deterministic_structured_evidence_match",
            "evidence_ids_checked": evidence_ids,
            "missing_evidence_ids": missing_refs,
            "supporting_observations": [
                {"dataset": record.get("dataset"), "source_id": record.get("source_id"),
                 "evidence_ids": record.get("evidence_ids", [])}
                for record in matched
            ],
            "contradictory_evidence": [],
            "limitations": [] if supported else [reason],
            "reason": reason,
        }
        validated.append(item)
    summary = {
        "total": len(validated),
        **counts,
        "recommendations_skipped": recommendations_skipped(),
        "policy": "Only confirmed findings may receive recommendations; unsupported candidates remain visible without remediation.",
    }
    return validated, summary
