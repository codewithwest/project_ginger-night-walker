"""Read-only page-level SEO and QA signals captured from the live DOM."""
from __future__ import annotations

import asyncio
from typing import Any


async def collect_page_signals(page: Any) -> dict[str, Any]:
    """Collect observable DOM signals; do not treat these as full audit passes."""
    return await page.evaluate(r"""() => {
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
      const selectorFor = el => {
        if (el.id) return "#" + CSS.escape(el.id);
        const parts = [];
        for (let node=el; node && node.nodeType===1 && parts.length<4; node=node.parentElement) {
          let part=node.tagName.toLowerCase();
          if (node.classList?.length) part += "." + Array.from(node.classList).slice(0,2).map(CSS.escape).join(".");
          const siblings=node.parentElement ? Array.from(node.parentElement.children).filter(x=>x.tagName===node.tagName) : [];
          if (siblings.length>1) part += ":nth-of-type(" + (siblings.indexOf(node)+1) + ")";
          parts.unshift(part);
        }
        return parts.join(" > ");
      };
      return {
        seo: { title: document.title || null, title_length: (document.title || "").length,
          meta_description: meta("description"), canonical_url: document.querySelector('link[rel="canonical"]')?.href || null,
          robots_directive: meta("robots") || meta("googlebot"), language: document.documentElement.lang || null,
          viewport: meta("viewport"), h1_count: document.querySelectorAll("h1").length,
          heading_count: document.querySelectorAll("h1,h2,h3,h4,h5,h6").length,
          heading_levels: Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6")).slice(0, 100).map(h => Number(h.tagName.slice(1))),
          heading_outline: Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6")).slice(0, 150).map(h => ({
            level: Number(h.tagName.slice(1)), text: (h.innerText || h.textContent || "").trim().slice(0,300),
            selector: selectorFor(h), id: h.id || null, outer_html: h.outerHTML.slice(0,1200)
          })),
          image_count: images.length, images_missing_alt: images.filter(img => !img.hasAttribute("alt")).length,
          h1_headings: Array.from(document.querySelectorAll("h1")).map(h => (h.innerText || h.textContent || "").trim()).slice(0, 100),
          images: images.map(img => { const r=img.getBoundingClientRect(); return {
            src: img.currentSrc || img.src || "", alt: img.getAttribute("alt"), complete: img.complete,
            natural_width: img.naturalWidth, natural_height: img.naturalHeight,
            rendered_width: Math.round(r.width), rendered_height: Math.round(r.height),
            visible: visible(img), selector: selectorFor(img), outer_html: img.outerHTML.slice(0,1600),
            load_status: img.complete ? (img.naturalWidth > 0 ? "loaded" : "broken") : "pending"
          }; }).slice(0, 500),
          broken_image_count: images.filter(img => img.complete && img.naturalWidth === 0).length,
          images_empty_alt: images.filter(img => img.hasAttribute("alt") && !img.getAttribute("alt").trim()).length,
          internal_link_count: links.filter(a => { try { return new URL(a.href).origin === location.origin; } catch { return false; } }).length,
          external_link_count: links.filter(a => { try { return new URL(a.href).origin !== location.origin; } catch { return false; } }).length,
          internal_links: links.filter(a => { try { return new URL(a.href).origin === location.origin; } catch { return false; } }).slice(0, 200).map(a => ({url: a.href, label: (a.innerText || a.getAttribute("aria-label") || "").trim().slice(0, 160)})),
          canonical_is_same_origin: (() => { const c=document.querySelector('link[rel="canonical"]'); try { return c ? new URL(c.href).origin === location.origin : null; } catch { return false; } })(),
          noindex: /(^|[,\s])noindex([,\s]|$)/i.test(meta("robots") || meta("googlebot") || ""),
          json_ld_blocks: Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0, 30).map(s => { try { JSON.parse(s.textContent || ""); return {valid_json:true, excerpt:(s.textContent || "").slice(0,500)}; } catch { return {valid_json:false, excerpt:(s.textContent || "").slice(0,500)}; } })
        },
        qa: { page_ready_state: document.readyState, visible_control_count: controls.filter(visible).length,
          controls_without_accessible_name: controls.filter(el => visible(el) && !named(el)).length,
          controls_without_accessible_name_details: controls.filter(el => visible(el) && !named(el)).slice(0,100).map(el => ({
            tag: el.tagName.toLowerCase(), type: el.getAttribute("type"), role: el.getAttribute("role"),
            id: el.id || null, selector: selectorFor(el), aria_label: el.getAttribute("aria-label"),
            aria_labelledby: el.getAttribute("aria-labelledby"), title: el.getAttribute("title"),
            text: (el.tagName === "BUTTON" ? (el.innerText || "") : "").trim().slice(0,300),
            outer_html: (() => { const clone=el.cloneNode(false); ["value","checked","selected"].forEach(name=>clone.removeAttribute(name)); return clone.outerHTML.slice(0,1800); })()
          })),
          duplicate_ids: duplicates.slice(0, 30),
          duplicate_id_details: duplicates.slice(0,30).map(id => ({
            id, elements: Array.from(document.querySelectorAll("[id]")).filter(el=>el.id===id).slice(0,10)
              .map(el=>({tag:el.tagName.toLowerCase(),selector:selectorFor(el),outer_html:el.outerHTML.slice(0,1600)}))
          })),
          form_count: document.forms.length, link_count: links.length }
      };
    }""")


def page_signal_findings(signals: dict[str, Any], workflow_type: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    seo = signals.get("seo") or {}
    qa = signals.get("qa") or {}
    if workflow_type in {"seo", "seo_qa"}:
        if not seo.get("title"): findings.append({"claim": "Page title is missing.", "type": "seo_issue", "severity": "high"})
        elif seo.get("title_length", 0) > 60: findings.append({"claim": "Page title exceeds 60 characters; review truncation risk.", "type": "seo_warning", "severity": "medium"})
        description = str(seo.get("meta_description") or "").strip()
        if not description: findings.append({"claim": "Meta description is missing.", "type": "seo_issue", "severity": "medium"})
        elif len(description) > 160: findings.append({"claim": "Meta description exceeds 160 characters; review likely truncation.", "type": "seo_warning", "severity": "low"})
        elif len(description) < 70: findings.append({"claim": "Meta description is unusually short; review whether it communicates the page purpose.", "type": "seo_warning", "severity": "low"})
        if not seo.get("canonical_url"): findings.append({"claim": "Canonical link element is missing.", "type": "seo_warning", "severity": "low"})
        if not seo.get("language"): findings.append({"claim": "Document language attribute is missing.", "type": "seo_warning", "severity": "low"})
        if not seo.get("viewport"): findings.append({"claim": "Viewport meta tag is missing.", "type": "seo_warning", "severity": "medium"})
        if seo.get("h1_count", 0) == 0: findings.append({"claim": "No H1 heading was found.", "type": "seo_issue", "severity": "medium"})
        if seo.get("h1_count", 0) > 1: findings.append({"claim": "Multiple H1 headings were found; review heading structure.", "type": "seo_warning", "severity": "low"})
        if seo.get("images_missing_alt", 0): findings.append({"claim": str(seo["images_missing_alt"]) + " image(s) have no alt attribute.", "type": "seo_issue", "severity": "medium"})
        if seo.get("canonical_url") and seo.get("canonical_is_same_origin") is False: findings.append({"claim": "Canonical URL points to a different origin.", "type": "seo_issue", "severity": "high"})
        if seo.get("noindex"): findings.append({"claim": "A noindex directive is present.", "type": "seo_warning", "severity": "high"})
        if any(not block.get("valid_json") for block in seo.get("json_ld_blocks", [])): findings.append({"claim": "At least one JSON-LD block contains invalid JSON.", "type": "seo_issue", "severity": "medium"})
        levels = seo.get("heading_levels", [])
        if any(current > previous + 1 for previous, current in zip(levels, levels[1:])): findings.append({"claim": "Heading levels skip one or more ranks; review document outline.", "type": "seo_warning", "severity": "low"})
    if workflow_type in {"qa", "seo_qa"}:
        if not seo.get("title"):
            findings.append({"claim": "The page has no non-empty document title.", "type": "qa_page_issue", "severity": "medium"})
        if seo.get("h1_count", 0) == 0:
            findings.append({"claim": "No H1 heading was found on the requested page.", "type": "qa_page_issue", "severity": "medium"})
        broken_images = [item for item in seo.get("images", []) if item.get("load_status") == "broken"]
        for image in broken_images:
            findings.append({
                "claim": "Image failed to load: " + str(image.get("src") or "(empty image source)")[:300],
                "type": "qa_broken_image", "severity": "medium",
                "image_url": image.get("src"),
            })
        if qa.get("controls_without_accessible_name", 0): findings.append({"claim": str(qa["controls_without_accessible_name"]) + " visible control(s) have no detectable accessible name.", "type": "qa_accessibility_warning", "severity": "medium"})
        if qa.get("duplicate_ids"): findings.append({"claim": "Duplicate HTML IDs were detected: " + ", ".join(qa["duplicate_ids"][:8]), "type": "qa_dom_issue", "severity": "low"})
    return findings



async def collect_internal_link_checks(page: Any, limit: int = 40, policy: Any = None) -> dict[str, Any]:
    """Verify same-origin anchors without allowing automatic cross-origin redirects."""
    from urllib.parse import urljoin, urlsplit

    links = await page.locator("a[href]").evaluate_all(
        """nodes => {
          const seen = new Set();
          return nodes.map(a => a.href).filter(href => {
            try {
              const u = new URL(href);
              if (!/^https?:$/.test(u.protocol) || u.origin !== location.origin) return false;
              u.hash = "";
              const key = u.href;
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            } catch { return false; }
          });
        }"""
    )
    origin = urlsplit(page.url)
    origin_key = (origin.scheme, origin.hostname, origin.port)
    semaphore = asyncio.Semaphore(8)

    async def check(url: str) -> dict[str, Any]:
        item = {
            "url": url, "method": "HEAD", "status": None, "ok": None,
            "final_url": None, "redirect_hops": 0,
        }
        async with semaphore:
            try:
                current = url
                for hop in range(6):
                    parsed = urlsplit(current)
                    if (parsed.scheme, parsed.hostname, parsed.port) != origin_key:
                        item.update({
                            "ok": False, "off_origin_redirect": True,
                            "final_url": current, "redirect_hops": hop,
                            "error": "Internal link redirected outside the audited origin",
                        })
                        break
                    if policy is not None:
                        await asyncio.to_thread(
                            policy.validate_url, current, allow_private_networks=False
                        )
                    response = await page.request.head(current, timeout=8000, max_redirects=0)
                    if response.status in {405, 501}:
                        response = await page.request.get(current, timeout=8000, max_redirects=0)
                        item["method"] = "GET_fallback_after_HEAD_unsupported"
                    item["status"] = response.status
                    item["final_url"] = current
                    item["redirect_hops"] = hop
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            item.update({"ok": None, "error": "Redirect response had no Location header"})
                            break
                        if hop >= 5:
                            item.update({"ok": None, "error": "Redirect limit reached"})
                            break
                        current = urljoin(current, location)
                        continue
                    access_limited = response.status in {401, 403, 429}
                    item.update({
                        "ok": None if access_limited else 200 <= response.status < 400,
                        "access_limited": access_limited,
                        "final_url": current,
                    })
                    break
            except Exception as exc:  # noqa: BLE001 - unknown is not a confirmed broken link
                item["error"] = str(exc)[:180]
        return item

    selected = links[:max(1, min(100, limit))]
    checks = await asyncio.gather(*(check(url) for url in selected))
    return {
        "checked_count": len(checks),
        "discovered_count": len(links),
        "truncated": len(links) > len(checks),
        "checks": checks,
        "broken_count": sum(item.get("ok") is False and not item.get("off_origin_redirect") for item in checks),
        "off_origin_redirect_count": sum(bool(item.get("off_origin_redirect")) for item in checks),
        "unknown_count": sum(item.get("ok") is None for item in checks),
    }

async def collect_qa_extended_signals(page: Any) -> dict[str, Any]:
    """Read-only responsive, semantics and keyboard-focus smoke checks."""
    original = page.viewport_size
    responsive = []
    try:
        for width, height, label in ((375, 812, "mobile"), (768, 1024, "tablet"), (1280, 900, "desktop")):
            await page.set_viewport_size({"width": width, "height": height})
            await page.wait_for_timeout(100)
            measurement = await page.evaluate("""() => {
              const root = document.documentElement;
              const body = document.body;
              const width = window.innerWidth;
              const offenders = Array.from(document.querySelectorAll("body *"))
                .filter(el => {
                  const r = el.getBoundingClientRect();
                  const s = getComputedStyle(el);
                  return s.position !== "fixed" && s.position !== "absolute" &&
                    r.width > 0 && (r.right > width + 2 || r.left < -2);
                }).slice(0, 10).map(el => ({
                  tag: el.tagName.toLowerCase(),
                  selector: el.id ? "#" + el.id : el.className?.toString().split(" ")[0] || "",
                  right: Math.round(el.getBoundingClientRect().right)
                }));
              return {
                viewport_width: width,
                document_width: Math.max(root.scrollWidth, body?.scrollWidth || 0),
                horizontal_overflow: Math.max(root.scrollWidth, body?.scrollWidth || 0) > width + 2,
                overflow_offenders: offenders
              };
            }""")
            responsive.append({"viewport": label, **measurement})
    finally:
        if original:
            await page.set_viewport_size(original)

    semantic = await page.evaluate("""() => {
      const visible = el => { const r=el.getBoundingClientRect(), s=getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden"; };
      const controls = Array.from(document.querySelectorAll("input:not([type=hidden]),select,textarea,button"));
      const labels = controls.filter(el => visible(el) &&
        !((el.getAttribute("aria-label") || "").trim() ||
          (el.getAttribute("aria-labelledby") || "").trim() ||
          (el.labels && Array.from(el.labels).some(x => (x.innerText || "").trim())) ||
          (el.closest("label")?.innerText || "").trim() ||
          (el.tagName === "BUTTON" && (el.innerText || "").trim())));
      const images = Array.from(document.images).filter(visible);
      const headings = Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6"))
        .map(el => Number(el.tagName.slice(1)));
      return {
        landmark_counts: {
          main: document.querySelectorAll("main,[role=main]").length,
          navigation: document.querySelectorAll("nav,[role=navigation]").length,
          banner: document.querySelectorAll("header,[role=banner]").length,
          contentinfo: document.querySelectorAll("footer,[role=contentinfo]").length
        },
        visible_form_controls_without_label: labels.length,
        visible_images_without_alt: images.filter(img => !img.hasAttribute("alt")).length,
        heading_level_skips: headings.slice(1).filter((level, i) => level > headings[i] + 1).length,
        links_without_accessible_name: Array.from(document.querySelectorAll("a[href]"))
          .filter(el => visible(el) && !((el.getAttribute("aria-label") || "").trim() ||
            (el.getAttribute("aria-labelledby") || "").trim() ||
            (el.innerText || "").trim() || (el.querySelector("img[alt]")?.getAttribute("alt") || "").trim())).length,
        buttons_without_accessible_name: Array.from(document.querySelectorAll("button,[role=button]"))
          .filter(el => visible(el) && !((el.getAttribute("aria-label") || "").trim() ||
            (el.getAttribute("aria-labelledby") || "").trim() || (el.innerText || "").trim() ||
            (el.getAttribute("title") || "").trim())).length,
        focusable_inside_aria_hidden: Array.from(document.querySelectorAll(
          'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])'
        )).filter(el => visible(el) && el.closest('[aria-hidden="true"]')).length,
        broken_aria_labelledby_references: Array.from(document.querySelectorAll("[aria-labelledby]"))
          .filter(el => (el.getAttribute("aria-labelledby") || "").trim().split(/\\s+/)
            .some(id => id && !document.getElementById(id))).length,
        generic_link_names: Array.from(document.querySelectorAll("a[href]"))
          .filter(el => visible(el) && /^(click here|here|read more|more|learn more)$/i
            .test((el.getAttribute("aria-label") || el.innerText || "").trim())).length,
        nested_interactive_controls: Array.from(document.querySelectorAll("a[href] button,button a[href],[role=button] a[href],[role=link] button"))
          .filter(visible).length,
        positive_tabindex_count: Array.from(document.querySelectorAll("[tabindex]"))
          .filter(el => visible(el) && Number(el.getAttribute("tabindex")) > 0).length,
        multiple_main_landmarks: document.querySelectorAll("main,[role=main]").length > 1,
        tables_without_header_cells: Array.from(document.querySelectorAll("table"))
          .filter(table => !table.querySelector("th,[role=columnheader],[role=rowheader]")).length,
        target_blank_without_noopener: Array.from(document.querySelectorAll('a[target="_blank"]'))
          .filter(el => !/\\bnoopener\\b/i.test(el.getAttribute("rel") || "")).length,
        document_title_missing: !document.title.trim(),
        document_language_missing: !(document.documentElement.lang || "").trim(),
        forms: Array.from(document.forms).slice(0, 30).map(form => ({
          method: (form.method || "get").toLowerCase(),
          action: form.action,
          control_count: form.querySelectorAll("input,select,textarea,button").length,
          submission_tested: false
        }))
      };
    }""")

    focus = []
    try:
        for _ in range(6):
            await page.keyboard.press("Tab")
            focus.append(await page.evaluate("""() => {
              const el = document.activeElement;
              if (!el || el === document.body) return null;
              return {
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute("role"),
                name: (el.getAttribute("aria-label") || el.innerText || el.getAttribute("title") || "").trim().slice(0,120),
                disabled: Boolean(el.disabled)
              };
            }"""))
    except Exception as exc:  # noqa: BLE001
        focus.append({"error": str(exc)[:160]})

    return {
        "responsive_viewports": responsive,
        "semantic_accessibility": semantic,
        "keyboard_focus_sequence": focus,
        "interaction_scope": "Read-only smoke checks; no forms submitted and no state-changing controls activated."
    }


def qa_extended_findings(extended: dict[str, Any], page_url: str) -> list[dict[str, Any]]:
    """Convert deterministic responsive/semantic smoke signals into reviewable findings."""
    findings: list[dict[str, Any]] = []
    for viewport in extended.get("responsive_viewports", []):
        if viewport.get("horizontal_overflow"):
            findings.append({
                "claim": f"Horizontal overflow detected at {viewport.get('viewport')} viewport ({viewport.get('viewport_width')}px).",
                "type": "qa_responsive_issue", "severity": "medium",
                "affected_urls": [page_url],
            })
    semantic = extended.get("semantic_accessibility") or {}
    checks = (
        ("visible_form_controls_without_label", "visible form control(s) lack a detectable programmatic label", "high"),
        ("links_without_accessible_name", "visible link(s) lack a detectable accessible name", "medium"),
        ("buttons_without_accessible_name", "visible button(s) lack a detectable accessible name", "high"),
        ("focusable_inside_aria_hidden", "focusable element(s) are inside aria-hidden content", "high"),
        ("broken_aria_labelledby_references", "element(s) reference missing IDs through aria-labelledby", "high"),
        ("generic_link_names", "visible link(s) use generic labels such as ‘click here’ or ‘read more’", "low"),
        ("nested_interactive_controls", "nested interactive control(s) were detected", "high"),
        ("positive_tabindex_count", "element(s) use positive tabindex values, which can disrupt natural focus order", "medium"),
        ("tables_without_header_cells", "table(s) have no detectable header cells", "medium"),
        ("target_blank_without_noopener", "new-tab link(s) lack rel=noopener", "low"),
    )
    for field, description, severity in checks:
        count = int(semantic.get(field) or 0)
        if count:
            findings.append({
                "claim": f"{count} {description}.",
                "type": "qa_accessibility_issue" if severity in {"high", "medium"} else "qa_accessibility_warning",
                "severity": severity, "affected_urls": [page_url],
            })
    landmarks = semantic.get("landmark_counts") or {}
    if not landmarks.get("main"):
        findings.append({
            "claim": "No main landmark was detected on the page.",
            "type": "qa_accessibility_warning", "severity": "low",
            "affected_urls": [page_url],
        })
    if semantic.get("multiple_main_landmarks"):
        findings.append({
            "claim": "Multiple main landmarks were detected; review the page's primary content structure.",
            "type": "qa_accessibility_warning", "severity": "low",
            "affected_urls": [page_url],
        })
    if semantic.get("document_title_missing"):
        findings.append({
            "claim": "The document has no non-empty title.",
            "type": "qa_accessibility_issue", "severity": "medium",
            "affected_urls": [page_url],
        })
    if semantic.get("document_language_missing"):
        findings.append({
            "claim": "The document language is not declared.",
            "type": "qa_accessibility_warning", "severity": "medium",
            "affected_urls": [page_url],
        })
    return findings
