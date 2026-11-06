from __future__ import annotations

import asyncio
import base64
import os
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import httpx
from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright

from nightwalker.evidence import EvidenceStore
from nightwalker.policy import PolicyEngine


class BrowserRuntime:
    """Task-isolated, read-only Playwright execution runtime.

    The model never receives a Playwright object. It can only influence the bounded
    plan produced by the Decider; this runtime owns and validates every browser action.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._steel_sessions: dict[int, tuple[str, Browser]] = {}
        self.backend = os.getenv("BROWSER_BACKEND", "local").strip().lower()
        if self.backend not in {"local", "steel"}:
            raise ValueError("BROWSER_BACKEND must be 'local' or 'steel'")
        self.policy = PolicyEngine()
        self.evidence = EvidenceStore()

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        if self.backend == "local":
            self._browser = await self._playwright.chromium.launch(headless=True)

    async def close(self) -> None:
        for context_id, (session_id, browser) in list(self._steel_sessions.items()):
            try:
                if browser.is_connected():
                    await browser.close()
            finally:
                await self._release_steel_session(session_id)
                self._steel_sessions.pop(context_id, None)
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _release_steel_session(self, session_id: str) -> None:
        api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")
        api_key = os.getenv("STEEL_API_KEY", "").strip()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.delete(
                    f"{api_base}/sessions/{session_id}",
                    headers={"steel-api-key": api_key},
                )
                if response.status_code not in {200, 202, 204, 404}:
                    response.raise_for_status()
        except httpx.HTTPError:
            # Session TTL remains the final cleanup guard if the API is unavailable.
            pass

    async def _guarded_context(self) -> BrowserContext:
        if not self._playwright:
            raise RuntimeError("Browser runtime is not started")
        if self.backend == "steel":
            api_key = os.getenv("STEEL_API_KEY", "").strip()
            api_base = os.getenv("STEEL_API_BASE_URL", "https://api.steel.dev/v1").rstrip("/")
            timeout_ms = max(60_000, int(os.getenv("STEEL_SESSION_TIMEOUT_MS", "300000")))
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{api_base}/sessions",
                    headers={"steel-api-key": api_key, "Content-Type": "application/json"},
                    json={"timeout": timeout_ms},
                )
                response.raise_for_status()
                session = response.json()
            session_id = str(session.get("id") or session.get("sessionId") or "")
            websocket_url = str(session.get("websocketUrl") or session.get("websocket_url") or "")
            if not session_id or not websocket_url:
                if session_id:
                    await self._release_steel_session(session_id)
                raise RuntimeError("Steel session response did not include id and websocketUrl")
            browser = None
            try:
                connect_url = websocket_url
                if api_key:
                    separator = "&" if "?" in connect_url else "?"
                    connect_url += separator + urlencode({"apiKey": api_key})
                browser = await self._playwright.chromium.connect_over_cdp(
                    connect_url,
                    timeout=30_000,
                )
                if not browser.contexts:
                    raise RuntimeError("Steel session connected without a browser context")
                context = browser.contexts[0]
                self._steel_sessions[id(context)] = (session_id, browser)
            except Exception:
                if browser and browser.is_connected():
                    await browser.close()
                await self._release_steel_session(session_id)
                raise
        else:
            if not self._browser:
                raise RuntimeError("Browser runtime is not started")
            context = await self._browser.new_context(
                service_workers="block",
                accept_downloads=False,
                java_script_enabled=True,
            )

        async def guard_request(route: Route) -> None:
            try:
                await asyncio.to_thread(
                    self.policy.validate_url, route.request.url, allow_private_networks=False
                )
            except (PermissionError, ValueError):
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await context.route("**/*", guard_request)
        return context

    @staticmethod
    def _resolve_search_result_url(value: str) -> str:
        """Unwrap common search-engine redirect URLs to their actual destination."""
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host.endswith("bing.com") and parsed.path.startswith("/ck/a"):
            encoded = parse_qs(parsed.query).get("u", [""])[0]
            if encoded.startswith("a1"):
                try:
                    decoded = base64.urlsafe_b64decode(encoded[2:] + "==").decode("utf-8", "ignore")
                    if decoded.startswith(("http://", "https://")):
                        return decoded
                except (ValueError, UnicodeError):
                    pass
            decoded = unquote(encoded)
            if decoded.startswith(("http://", "https://")):
                return decoded
        return value

    @staticmethod
    def _link_score(text: str, objective: str) -> float:
        ignored = {"the", "and", "for", "from", "with", "find", "open", "click", "visit", "page"}
        def normalize(word: str) -> str:
            word = {"catalogue": "catalog", "catalogues": "catalog"}.get(word, word)
            return word[:-1] if len(word) > 4 and word.endswith("s") else word

        words = {
            normalize(word)
            for word in re.findall(r"[a-z0-9]{3,}", objective.lower())
            if word not in ignored
        }
        if not words:
            return 0.0
        label = {
            normalize(word) for word in re.findall(r"[a-z0-9]{3,}", text.lower())
        }
        return len(words & label) / len(words)

    async def _follow_relevant_link(self, page: Page, objective: str) -> str | None:
        candidates = await page.locator("a[href]").evaluate_all(
            """nodes => nodes.slice(0, 300).map(a => ({
                text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 180),
                href: a.href
            }))"""
        )
        current = urlparse(page.url)
        ranked: list[tuple[float, str]] = []
        for item in candidates:
            href = urljoin(page.url, str(item.get("href", "")))
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or parsed.hostname != current.hostname:
                continue
            score = self._link_score(str(item.get("text", "")), objective)
            if score >= 0.25:
                ranked.append((score, href))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        destination = ranked[0][1]
        self.policy.validate_url(destination, allow_private_networks=False)
        await page.goto(
            destination,
            wait_until="domcontentloaded",
            timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000")),
        )
        return page.url

    @staticmethod
    def _extract_products_from_text(text: str, limit: int = 12) -> list[dict[str, str]]:
        """Extract ordered product names and prices from visible page text."""
        lines = [re.sub(r"\s+", " ", line).strip(" •|\t") for line in text.splitlines()]
        lines = [line for line in lines if line]
        price_pattern = re.compile(
            r"(?i)(?:R\s*)\d[\d,]*(?:\.\d{2})?"
        )
        ignored = {
            "from", "shop now", "view collection", "new arrivals",
            "press to skip carousel", "add to cart", "quick view",
        }
        products: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for index, line in enumerate(lines):
            match = price_pattern.search(line)
            if not match:
                continue
            price = match.group(0).strip()
            name = line[:match.start()].strip(" -|") if match.start() else ""
            if not name and index:
                previous = index - 1
                while previous >= 0 and (
                    lines[previous].lower() in ignored
                    or "price depends on the options" in lines[previous].lower()
                ):
                    previous -= 1
                name = lines[previous] if previous >= 0 else ""
            if (
                not name
                or name.lower() in ignored
                or "price depends on the options" in name.lower()
                or price.lower() == name.lower()
            ):
                continue
            if len(name) > 180 or re.fullmatch(r"(?i)r?\s*[\d,.]+", name):
                continue
            key = (name.casefold(), price.casefold())
            if key in seen:
                continue
            seen.add(key)
            products.append({"name": name, "price": price})
            if len(products) >= limit:
                break

        return products

    async def run_plan(self, task_id: str, url: str, steps: list[dict], objective: str) -> dict:
        """Execute only public-site navigation, same-origin link following and extraction."""
        self.policy.validate_url(url, allow_private_networks=False)
        context = await self._guarded_context()
        try:
            page = await context.new_page()
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000")),
            )
            if response and response.status >= 400:
                return {
                    "status": "failed",
                    "issues": [f"Website returned HTTP {response.status}"],
                    "final_url": page.url,
                }

            visited = [page.url]
            issues: list[str] = []
            for step in steps:
                capability = step.get("capability")
                step_objective = str(step.get("objective", ""))[:500]
                if capability == "navigate":
                    continue
                if capability == "interact":
                    destination = await self._follow_relevant_link(page, step_objective)
                    if destination:
                        visited.append(destination)
                    else:
                        issues.append(
                            f"No sufficiently relevant same-site link found for: {step_objective}"
                        )
                elif capability == "recover":
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000")),
                    )
                    visited.append(page.url)
                elif capability == "extract":
                    continue

            title = (await page.title())[:500]
            candidate_links = await page.locator("a[href]").evaluate_all(
                """nodes => nodes.slice(0, 500).map(a => ({
                    label: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 240),
                    url: a.href
                })).filter(item => item.url && /^https?:\\/\\//i.test(item.url))"""
            )
            candidates = []
            seen_candidates = set()
            for item in candidate_links:
                candidate_url = self._resolve_search_result_url(str(item.get("url", ""))).split("#", 1)[0]
                candidate_host = (urlparse(candidate_url).hostname or "").lower()
                if (
                    candidate_url and candidate_url not in seen_candidates
                    and candidate_url != page.url
                    and not candidate_host.endswith(("bing.com", "microsoft.com"))
                ):
                    seen_candidates.add(candidate_url)
                    candidates.append({
                        "url": candidate_url,
                        "label": str(item.get("label", ""))[:240],
                    })
                if len(candidates) >= 40:
                    break
            headings = [
                text.strip()[:300]
                for text in (await page.locator("h1, h2").all_text_contents())[:40]
                if text.strip()
            ]
            main = page.locator("main")
            if await main.count():
                body_text = await main.first.evaluate(
                    "element => (element.innerText || '').slice(0, 20000)"
                )
            else:
                body_text = await page.locator("body").evaluate(
                    "element => (element.innerText || '').slice(0, 20000)"
                )
            wants_products = bool(re.search(r"(?i)\b(product|products|item|items|price|pricing)\b", objective))
            products = self._extract_products_from_text(body_text) if wants_products else []
            if wants_products and not products:
                issues.append(
                    "The page was read, but no product name and price could be confidently extracted."
                )
            answer = (
                f"{products[0]['name']} — {products[0]['price']}"
                if products else None
            )
            retrieved_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC
            ).isoformat()
            findings = []
            if title:
                findings.append({
                    "claim": f"Page title: {title}",
                    "type": "page_metadata",
                    "confidence": "observed",
                })
            findings.extend(
                {
                    "claim": heading,
                    "type": "page_heading",
                    "confidence": "observed",
                }
                for heading in headings
            )
            excerpt_lines = [
                re.sub(r"\s+", " ", line).strip()
                for line in body_text.splitlines()
                if len(line.strip()) >= 35
            ][:8]
            findings.extend(
                {
                    "claim": "Visible page text: " + line[:400],
                    "type": "visible_text_excerpt",
                    "confidence": "observed",
                }
                for line in excerpt_lines
            )
            sources = [{
                "source_id": "s-001",
                "url": page.url,
                "title": title,
                "retrieved_at": retrieved_at,
            }]
            structured_data = {"products": products} if products else {}
            limitations = list(issues)
            if not body_text.strip():
                limitations.append("No visible page text was available to analyse.")
            report = {
                "schema_version": "1.0",
                "answer": answer or (
                    f"Captured the page “{title}” and its visible content."
                    if title else "Captured page content; no concise answer was inferred."
                ),
                "findings": findings,
                "sources": sources,
                "evidence": [{
                    "evidence_id": None,
                    "source_id": "s-001",
                    "kind": "visible_text",
                    "captured_at": retrieved_at,
                    "source_url": page.url,
                    "captured_text": body_text,
                    "visited_urls": visited[:10],
                }],
                "structured_data": structured_data,
                "limitations": limitations,
                "suggested_memory": [],
            }
            payload = {
                "objective": objective[:2000],
                **report,
                "products": products,
                "final_url": page.url,
                "title": title,
                "headings": headings,
                "text": body_text,
                "visited_urls": visited[:10],
                "candidate_urls": candidates,
                "issues": issues,
            }
            evidence_id = self.evidence.save(task_id, payload)
            report["evidence"][0]["evidence_id"] = evidence_id
            for index, finding in enumerate(report["findings"], start=1):
                finding["finding_id"] = "f-" + str(index).zfill(3)
                finding["kind"] = "observed"
                finding["source_ids"] = ["s-001"]
                finding["evidence_ids"] = [evidence_id]
            return {
                "status": "completed" if body_text.strip() and not issues else "blocked",
                **report,
                "final_url": page.url,
                "title": title,
                "headings": headings,
                "text": body_text,
                "products": products,
                "visited_urls": visited[:10],
                "candidate_urls": candidates,
                "issues": issues,
                "evidence_id": evidence_id,
            }
        finally:
            steel_session = self._steel_sessions.pop(id(context), None)
            try:
                await context.close()
            finally:
                if steel_session:
                    session_id, browser = steel_session
                    if browser.is_connected():
                        await browser.close()
                    await self._release_steel_session(session_id)

    async def extract_basic(self, url: str) -> dict:
        """Compatibility endpoint for a deterministic title/H1 extraction."""
        result = await self.run_plan(
            task_id="direct-browser-extract",
            url=url,
            steps=[{"capability": "navigate"}, {"capability": "extract"}],
            objective="Extract page title, headings and visible text",
        )
        if result["status"] != "completed":
            raise RuntimeError("; ".join(result.get("issues", [])) or "Page extraction failed")
        return {
            "url": result["final_url"],
            "status_code": None,
            "title": result["title"],
            "headings": result["headings"],
            "text": result["text"],
            "evidence_id": result["evidence_id"],
        }
