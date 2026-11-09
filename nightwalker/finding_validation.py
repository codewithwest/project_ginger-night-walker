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
    structured_data: dict[str, Any],
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
                   and r.get("kind") in {"console_error", "page_error"}]
        matched += [r for r in records if r.get("dataset") == "qa_runtime_events"
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
        duplicate_data = structured_data.get("duplicate_analysis") or {}
        groups = duplicate_data.get(group_key) or []
        affected = set(finding.get("affected_urls") or [])
        matched = [{"dataset": "duplicate_analysis", **duplicate_data, "matched_groups": [
            group for group in groups if affected.intersection(group.get("urls") or [])
        ]}] if any(affected.intersection(group.get("urls") or []) for group in groups) else []
    elif kind == "qa_responsive_issue":
        matched = [r for r in records if r.get("dataset") == "qa_extended"
                   and any(v.get("horizontal_overflow") for v in r.get("responsive_viewports", [])
                           if isinstance(v, dict))]
    elif kind in {"seo_issue", "seo_warning"}:
        for record in records:
            if record.get("dataset") != "seo_checks":
                continue
            if "title" in claim and ("missing" in claim or "no " in claim):
                ok = not str(record.get("title") or "").strip()
            elif "title" in claim and "60 characters" in claim:
                ok = int(record.get("title_length") or 0) > 60
            elif "meta description" in claim and "missing" in claim:
                ok = not str(record.get("meta_description") or "").strip()
            elif "meta description" in claim and "160 characters" in claim:
                ok = len(str(record.get("meta_description") or "")) > 160
            elif "meta description" in claim and "short" in claim:
                ok = 0 < len(str(record.get("meta_description") or "").strip()) < 70
            elif "canonical" in claim and "missing" in claim:
                ok = not record.get("canonical_url")
            elif "canonical" in claim and "different origin" in claim:
                ok = record.get("canonical_is_same_origin") is False
            elif "language" in claim:
                ok = not str(record.get("language") or "").strip()
            elif "viewport" in claim:
                ok = not str(record.get("viewport") or "").strip()
            elif "h1" in claim:
                ok = int(record.get("h1_count") or 0) == 0 if "no h1" in claim or "no h1 heading" in claim else int(record.get("h1_count") or 0) > 1
            elif "alt" in claim:
                ok = int(record.get("images_missing_alt") or 0) > 0
            elif "noindex" in claim:
                ok = bool(record.get("noindex"))
            elif "json-ld" in claim:
                ok = any(block.get("valid_json") is False for block in record.get("json_ld_blocks", []) if isinstance(block, dict))
            elif "heading levels" in claim:
                levels = record.get("heading_levels") or []
                ok = any(current > previous + 1 for previous, current in zip(levels, levels[1:]))
            else:
                ok = False
            if ok:
                matched.append(record)
    elif kind == "qa_page_issue":
        matched = [r for r in records if r.get("dataset") == "qa_page_smoke" and (
            ("title" in claim and not str(r.get("document_title") or "").strip())
            or ("h1" in claim and int(r.get("h1_count") or 0) == 0)
        )]
    elif kind in {"qa_accessibility_issue", "qa_accessibility_warning"}:
        matched = [r for r in records if (
            r.get("dataset") == "qa_checks" and (
                ("accessible name" in claim and int(r.get("controls_without_accessible_name") or 0) > 0)
                or ("duplicate" in claim and bool(r.get("duplicate_ids")))
            )
        ) or (
            r.get("dataset") == "qa_extended" and any(
                isinstance(value, (int, float)) and value > 0
                for key, value in r.items()
                if key in {"controls_without_label", "links_without_accessible_name",
                           "buttons_without_accessible_name", "focusable_inside_aria_hidden",
                           "broken_aria_labelledby_references"}
            )
        )]
    elif kind == "qa_dom_issue":
        matched = [r for r in records if r.get("dataset") == "qa_checks" and bool(r.get("duplicate_ids"))]
    elif kind == "qa_interaction_issue":
        matched = [r for r in records if r.get("dataset") == "qa_interaction_journeys"
                   and any(c.get("ok") is False for c in r.get("checks", []) if isinstance(c, dict))]
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
            supported, reason, matched = _support_for_type(item, linked, structured_data)
        verdict = "confirmed" if supported else "insufficient_evidence"
        counts[verdict] += 1
        evidence_context = [
            {"evidence_id": row.get("evidence_id"), "url": row.get("url"),
             "description": row.get("description"), "captured_text": str(row.get("captured_text") or "")[:3000]}
            for row in evidence if row.get("evidence_id") in evidence_ids
        ]
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
            # Full matching browser/DOM/network observations plus evidence excerpts
            # travel with the finding so downstream recommendation has source context.
            "recommendation_context": {
                "observations": matched[:20],
                "evidence": evidence_context[:10],
                "affected_urls": item.get("affected_urls") or [],
                "original_claim": item.get("claim"),
            },
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
