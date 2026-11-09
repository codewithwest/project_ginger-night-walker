"""Read-only page-level SEO and QA signals captured from the live DOM."""
from __future__ import annotations

from typing import Any


async def collect_page_signals(page: Any) -> dict[str, Any]:
    """Collect observable DOM signals; do not treat these as full audit passes."""
    return await page.evaluate("""() => {
      const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || null;
      const links = Array.from(document.querySelectorAll("a[href]"));
      const images = Array.from(document.images);
      const controls = Array.from(document.querySelectorAll("button,input,select,textarea,[role=button]"));
      const named = el => Boolean((el.getAttribute("aria-label") || "").trim() ||
        (el.getAttribute("aria-labelledby") || "").trim() ||
        (el.labels && Array.from(el.labels).some(label => (label.innerText || "").trim())) ||
        (el.innerText || "").trim() || (el.getAttribute("title") || "").trim());
      const ids = Array.from(document.querySelectorAll("[id]")).map(el => el.id).filter(Boolean);
      const duplicates = Array.from(new Set(ids.filter((id, i) => ids.indexOf(id) !== i)));
      const visible = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect();
        return s.visibility !== "hidden" && s.display !== "none" && r.width > 0 && r.height > 0; };
      return {
        seo: { title: document.title || null, title_length: (document.title || "").length,
          meta_description: meta("description"), canonical_url: document.querySelector('link[rel="canonical"]')?.href || null,
          robots_directive: meta("robots") || meta("googlebot"), language: document.documentElement.lang || null,
          viewport: meta("viewport"), h1_count: document.querySelectorAll("h1").length,
          heading_count: document.querySelectorAll("h1,h2,h3,h4,h5,h6").length,
          heading_levels: Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6")).slice(0, 100).map(h => Number(h.tagName.slice(1))),
          image_count: images.length, images_missing_alt: images.filter(img => !img.hasAttribute("alt")).length,
          images_empty_alt: images.filter(img => img.hasAttribute("alt") && !img.getAttribute("alt").trim()).length,
          internal_link_count: links.filter(a => { try { return new URL(a.href).origin === location.origin; } catch { return false; } }).length,
          external_link_count: links.filter(a => { try { return new URL(a.href).origin !== location.origin; } catch { return false; } }).length,
          internal_links: links.filter(a => { try { return new URL(a.href).origin === location.origin; } catch { return false; } }).slice(0, 200).map(a => ({url: a.href, label: (a.innerText || a.getAttribute("aria-label") || "").trim().slice(0, 160)})),
          canonical_is_same_origin: (() => { const c=document.querySelector('link[rel="canonical"]'); try { return c ? new URL(c.href).origin === location.origin : null; } catch { return false; } })(),
          noindex: /(^|[,\\s])noindex([,\\s]|$)/i.test(meta("robots") || meta("googlebot") || ""),
          json_ld_blocks: Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0, 30).map(s => { try { JSON.parse(s.textContent || ""); return {valid_json:true, excerpt:(s.textContent || "").slice(0,500)}; } catch { return {valid_json:false, excerpt:(s.textContent || "").slice(0,500)}; } })
        },
        qa: { page_ready_state: document.readyState, visible_control_count: controls.filter(visible).length,
          controls_without_accessible_name: controls.filter(el => visible(el) && !named(el)).length,
          duplicate_ids: duplicates.slice(0, 30), form_count: document.forms.length, link_count: links.length }
      };
    }""")


def page_signal_findings(signals: dict[str, Any], workflow_type: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    seo = signals.get("seo") or {}
    qa = signals.get("qa") or {}
    if workflow_type in {"seo", "seo_qa"}:
        if not seo.get("title"): findings.append({"claim": "Page title is missing.", "type": "seo_issue", "severity": "high"})
        elif seo.get("title_length", 0) > 60: findings.append({"claim": "Page title exceeds 60 characters; review truncation risk.", "type": "seo_warning", "severity": "medium"})
        if not seo.get("meta_description"): findings.append({"claim": "Meta description is missing.", "type": "seo_issue", "severity": "medium"})
        if seo.get("h1_count", 0) == 0: findings.append({"claim": "No H1 heading was found.", "type": "seo_issue", "severity": "medium"})
        if seo.get("h1_count", 0) > 1: findings.append({"claim": "Multiple H1 headings were found; review heading structure.", "type": "seo_warning", "severity": "low"})
        if seo.get("images_missing_alt", 0): findings.append({"claim": str(seo["images_missing_alt"]) + " image(s) have no alt attribute.", "type": "seo_issue", "severity": "medium"})
    if workflow_type in {"qa", "seo_qa"}:
        if qa.get("controls_without_accessible_name", 0): findings.append({"claim": str(qa["controls_without_accessible_name"]) + " visible control(s) have no detectable accessible name.", "type": "qa_accessibility_warning", "severity": "medium"})
        if qa.get("duplicate_ids"): findings.append({"claim": "Duplicate HTML IDs were detected: " + ", ".join(qa["duplicate_ids"][:8]), "type": "qa_dom_issue", "severity": "low"})
    return findings
