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
        def event_matches(event: dict[str, Any]) -> bool:
            event_kind = str(event.get("kind") or "")
            message = str(event.get("message") or "").casefold()
            if event_kind not in {"console_error", "page_error"}:
                return False
            if message and (message in claim or claim in message):
                return True
            return ("console error" in claim and event_kind == "console_error") or (
                "page error" in claim or "page exception" in claim
            ) and event_kind == "page_error"
        matched = [r for r in records if r.get("dataset") == "qa_runtime_events"
                   and event_matches(r)]
        matched += [r for r in records if r.get("dataset") == "qa_runtime_events"
                    and any(event_matches(e) for e in (r.get("events") or r.get("runtime_events") or [])
                            if isinstance(e, dict))]
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
        semantic_fields = {
            "form control": "visible_form_controls_without_label",
            "link": "links_without_accessible_name",
            "button": "buttons_without_accessible_name",
            "focusable": "focusable_inside_aria_hidden",
            "aria-labelledby": "broken_aria_labelledby_references",
            "generic": "generic_link_names",
            "nested interactive": "nested_interactive_controls",
            "positive tabindex": "positive_tabindex_count",
            "table": "tables_without_header_cells",
            "new-tab": "target_blank_without_noopener",
        }
        expected_field = next((field for phrase, field in semantic_fields.items() if phrase in claim), None)
        for record in records:
            if record.get("dataset") == "qa_checks":
                if "accessible name" in claim and int(record.get("controls_without_accessible_name") or 0) > 0:
                    matched.append(record)
                elif "duplicate" in claim and bool(record.get("duplicate_ids")):
                    matched.append(record)
            if record.get("dataset") == "qa_extended":
                semantic = record.get("semantic_accessibility") or {}
                if expected_field and int(semantic.get(expected_field) or 0) > 0:
                    matched.append(record)
                elif "main landmark" in claim:
                    main_count = int((semantic.get("landmark_counts") or {}).get("main") or 0)
                    if main_count == 0 or ("multiple" in claim and main_count > 1):
                        matched.append(record)
                elif "document title" in claim and semantic.get("document_title_missing"):
                    matched.append(record)
                elif "document language" in claim and semantic.get("document_language_missing"):
                    matched.append(record)
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



def _recommendation_observations(
    finding: dict[str, Any], matched: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the recommendation packet rich but relevant and bounded."""
    kind = str(finding.get("type") or "")
    output: list[dict[str, Any]] = []
    for record in matched[:12]:
        dataset = record.get("dataset")
        base = {
            "dataset": dataset,
            "source_id": record.get("source_id"),
            "evidence_ids": record.get("evidence_ids", []),
            "url": record.get("url") or record.get("page_url"),
        }
        if dataset == "qa_page_smoke":
            images = record.get("images") or []
            image_url = finding.get("image_url")
            if kind == "qa_broken_image" and image_url:
                images = [image for image in images if image.get("src") == image_url]
            else:
                images = []
            base.update({
                "document_title": record.get("document_title"),
                "h1_count": record.get("h1_count"),
                "h1_headings": record.get("h1_headings", [])[:30],
                "image_count": record.get("image_count"),
                "broken_image_count": record.get("broken_image_count"),
                "pending_image_count": record.get("pending_image_count"),
                "images": images[:10],
                "runtime_events": record.get("runtime_events", [])[:30],
            })
        elif dataset == "seo_checks":
            keys = (
                "title", "title_length", "meta_description", "canonical_url",
                "canonical_is_same_origin", "robots_directive", "noindex", "language",
                "viewport", "h1_count", "h1_headings", "heading_levels",
                "heading_outline", "images_missing_alt", "image_count", "images",
                "json_ld_blocks",
            )
            base.update({key: record.get(key) for key in keys if key in record and key not in {"images", "heading_outline", "json_ld_blocks"}})
            if "images" in record:
                images = record.get("images") or []
                if "alt" in str(finding.get("claim") or "").casefold():
                    images = [image for image in images if not image.get("alt")]
                base["images"] = images[:10]
            if "heading_outline" in record:
                base["heading_outline"] = record.get("heading_outline", [])[:30]
            if "json_ld_blocks" in record:
                base["json_ld_blocks"] = record.get("json_ld_blocks", [])[:5]
        elif dataset == "qa_checks":
            keys = ("page_ready_state", "visible_control_count", "controls_without_accessible_name",
                    "controls_without_accessible_name_details", "duplicate_ids", "duplicate_id_details",
                    "form_count", "link_count")
            base.update({key: record.get(key) for key in keys if key in record and key not in {"controls_without_accessible_name_details", "duplicate_id_details"}})
            if record.get("controls_without_accessible_name_details"):
                base["controls_without_accessible_name_details"] = record["controls_without_accessible_name_details"][:10]
            if record.get("duplicate_id_details"):
                base["duplicate_id_details"] = record["duplicate_id_details"][:10]
        elif dataset == "qa_extended":
            base.update({
                "responsive_viewports": record.get("responsive_viewports", []),
                "semantic_accessibility": record.get("semantic_accessibility", {}),
                "keyboard_focus_sequence": record.get("keyboard_focus_sequence", [])[:20],
                "interaction_scope": record.get("interaction_scope"),
            })
        elif dataset == "qa_runtime_events":
            base.update({key: record.get(key) for key in ("kind", "message", "details", "captured_at")})
        elif dataset in {"internal_link_checks", "qa_navigation_journeys", "qa_interaction_journeys"}:
            checks = record.get("checks") or []
            base["checks"] = [
                check for check in checks
                if check.get("ok") is False or check.get("off_origin_redirect")
            ][:30]
            base["checked_count"] = record.get("checked_count")
        elif dataset == "duplicate_analysis":
            base["matched_groups"] = record.get("matched_groups", [])[:20]
        else:
            base.update({key: value for key, value in record.items()
                         if key not in {"dataset", "source_id", "evidence_ids"}})
        output.append(base)
    return output

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
            {"evidence_id": row.get("evidence_id"), "url": row.get("source_url") or row.get("url"),
             "source_id": row.get("source_id"), "kind": row.get("kind"),
             "captured_at": row.get("captured_at"), "description": row.get("description"),
             "captured_text": str(row.get("captured_text") or "")[:3000]}
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
                "observations": _recommendation_observations(item, matched),
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
