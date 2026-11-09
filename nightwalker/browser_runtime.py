from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
import socket
from datetime import UTC, datetime
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
        self._task_sessions: dict[str, dict] = {}
        self._task_contexts: dict[str, BrowserContext] = {}
        self._task_pages: dict[str, Page] = {}
        self._tab_listener_contexts: set[int] = set()
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
        for task_id in list(self._task_contexts):
            await self.close_task_session(task_id)
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

    @staticmethod
    def _max_browser_tabs() -> int:
        try:
            return max(1, min(8, int(os.getenv("BROWSER_MAX_TABS", "3"))))
        except ValueError:
            return 4

    async def _enforce_tab_limit(self, task_id: str, opened: Page) -> None:
        """Hard-stop popup storms before they can exhaust the browser session."""
        context = self._task_contexts.get(task_id)
        if context is None:
            return
        try:
            pages = [page for page in context.pages if not page.is_closed()]
            if len(pages) <= self._max_browser_tabs():
                return
            primary = self._task_pages.get(task_id)
            if opened is not primary and not opened.is_closed():
                await opened.close()
                self._browser_event(
                    task_id, "Closed a new tab immediately because the browser tab limit was reached",
                    url=getattr(opened, "url", None),
                )
        except Exception as exc:  # noqa: BLE001 - popup cleanup must not crash navigation
            self._browser_event(task_id, f"Tab-limit cleanup warning: {str(exc)[:180]}")

    async def tab_inventory(self, task_id: str) -> list[dict]:
        """Return bounded metadata for the AI tab janitor; never expose page objects."""
        context = self._task_contexts.get(task_id)
        if context is None:
            return []
        primary = self._task_pages.get(task_id)
        pages = [page for page in getattr(context, "pages", []) if not page.is_closed()]
        pages.sort(key=lambda page: page is not primary)
        inventory = []
        for index, page in enumerate(pages[:8]):
            try:
                title = (await page.title())[:180]
            except Exception:  # noqa: BLE001 - inaccessible tabs remain cleanable
                title = ""
            inventory.append({
                "index": index,
                "primary": page is primary,
                "url": str(getattr(page, "url", ""))[:500],
                "title": title,
            })
        return inventory

    async def cleanup_task_tabs(
        self, task_id: str, close_indices: list[int], reason: str = "AI tab cleanup",
    ) -> dict:
        """Apply an AI proposal to extra tabs only; the primary research page is immutable."""
        context = self._task_contexts.get(task_id)
        if context is None:
            return {"closed": 0, "remaining": 0, "reason": "No active browser context"}
        primary = self._task_pages.get(task_id)
        pages = [page for page in getattr(context, "pages", []) if not page.is_closed()]
        pages.sort(key=lambda page: page is not primary)
        selected = {
            index for index in close_indices
            if isinstance(index, int) and not isinstance(index, bool) and 0 < index < len(pages)
        }
        max_tabs = min(self._max_browser_tabs(), 3)
        # A model cannot retain an unbounded set of secondary tabs.
        overflow = set(range(max_tabs, len(pages)))
        selected.update(overflow)
        closed = 0
        for index in sorted(selected, reverse=True):
            page = pages[index]
            if page is primary or page.is_closed():
                continue
            try:
                await page.close()
                closed += 1
            except Exception as exc:  # noqa: BLE001 - continue cleaning other tabs
                self._browser_event(task_id, f"Could not close extra tab: {str(exc)[:160]}")
        remaining = sum(1 for page in getattr(context, "pages", []) if not page.is_closed())
        message = f"AI tab janitor closed {closed} extra tab(s); {remaining} remain. {reason[:220]}"
        self._browser_event(task_id, message, status="active")
        return {"closed": closed, "remaining": remaining, "reason": reason[:300]}

    async def close_task_session(self, task_id: str) -> None:
        """Close the single browser context retained across this task's research rounds."""
        context = self._task_contexts.pop(task_id, None)
        self._task_pages.pop(task_id, None)
        if context is not None:
            self._tab_listener_contexts.discard(id(context))
        state = self._task_sessions.get(task_id)
        if state:
            self._browser_event(task_id, "Browser session closed after task completion", status="finished")
            state["finished_at"] = datetime.now(UTC).isoformat()
        if context is None:
            return
        steel_session = self._steel_sessions.pop(id(context), None)
        try:
            await context.close()
        finally:
            if steel_session:
                session_id, browser = steel_session
                if browser.is_connected():
                    await browser.close()
                await self._release_steel_session(session_id)

    @staticmethod
    def _normalise_steel_websocket_url(websocket_url: str, api_base: str) -> str:
        """Replace wildcard bind hosts in Steel's advertised CDP URL.

        Steel may advertise 0.0.0.0 or ::, which are listen addresses and cannot
        be used as a client destination. Resolve those to the hostname used for
        the Steel API so Docker clients connect over the Compose network.
        """
        websocket = urlparse(websocket_url)
        api = urlparse(api_base)
        host = (websocket.hostname or "").strip("[]").lower()
        if host in {"0.0.0.0", "::", ""}:
            host = (api.hostname or "").strip("[]").lower()
        if not host:
            raise RuntimeError("STEEL_API_BASE_URL must include a reachable hostname")

        # Steel's self-hosted CDP gateway rejects DNS-name Host headers. Resolve
        # the Compose service name and connect using its container IP instead.
        try:
            ipaddress.ip_address(host)
            connect_host = host
        except ValueError:
            port = websocket.port or api.port or 80
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses:
                raise RuntimeError(f"Could not resolve Steel browser host: {host}")
            connect_host = addresses[0][4][0]

        if ":" in connect_host:
            netloc = f"[{connect_host}]"
        else:
            netloc = connect_host
        port = websocket.port or api.port
        if port:
            netloc += f":{port}"
        return websocket._replace(netloc=netloc).geturl()

    async def _release_steel_session(self, session_id: str) -> None:
        api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")
        api_key = os.getenv("STEEL_API_KEY", "").strip()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"steel-api-key": api_key} if api_key else {}
                response = await client.delete(
                    f"{api_base}/sessions/{session_id}",
                    headers=headers,
                )
                if response.status_code not in {200, 202, 204, 404}:
                    response.raise_for_status()
        except httpx.HTTPError:
            # Session TTL remains the final cleanup guard if the API is unavailable.
            pass

    @staticmethod
    def _normalise_steel_debug_url(debug_url: str) -> str:
        """Make a container-advertised Steel viewer URL reachable from the host UI."""
        parsed = urlparse(debug_url)
        host = (parsed.hostname or "").strip("[]").lower()
        if host in {"steel-browser", "ginger-steel-browser", "0.0.0.0", "::"}:
            netloc = "localhost"
            if parsed.port:
                netloc += f":{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
        return debug_url

    def _browser_event(
        self, task_id: str, message: str, *, url: str | None = None,
        title: str | None = None, status: str | None = None,
    ) -> None:
        state = self._task_sessions.setdefault(task_id, {
            "backend": self.backend, "status": "connecting", "active": True,
            "activity": [], "started_at": datetime.now(UTC).isoformat(),
        })
        if url:
            state["current_url"] = url
        if title:
            state["page_title"] = title
        if status:
            state["status"] = status
            state["active"] = status in {"connecting", "active"}
        state.setdefault("activity", []).append({
            "at": datetime.now(UTC).isoformat(),
            "message": message[:500],
            "url": url,
        })
        state["activity"] = state["activity"][-30:]

    def task_session(self, task_id: str) -> dict | None:
        """Return safe, task-scoped live browser observability for the UI."""
        state = self._task_sessions.get(task_id)
        if not state:
            return None
        return {key: value for key, value in state.items() if key != "session_id"}

    async def _guarded_context(self, task_id: str | None = None) -> BrowserContext:
        if not self._playwright:
            raise RuntimeError("Browser runtime is not started")
        if task_id and task_id in self._task_contexts:
            return self._task_contexts[task_id]
        if self.backend == "steel":
            api_key = os.getenv("STEEL_API_KEY", "").strip()
            api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")
            timeout_ms = max(60_000, int(os.getenv("STEEL_SESSION_TIMEOUT_MS", "300000")))
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["steel-api-key"] = api_key
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{api_base}/sessions",
                    headers=headers,
                    json={"timeout": timeout_ms},
                )
                response.raise_for_status()
                session = response.json()
            session_id = str(session.get("id") or session.get("sessionId") or "")
            debug_url = str(session.get("debugUrl") or session.get("debug_url") or "")
            if task_id:
                self._task_sessions[task_id] = {
                    "backend": "steel", "status": "connecting", "active": True,
                    "session_id": session_id,
                    "debug_url": self._normalise_steel_debug_url(debug_url) if debug_url else None,
                    "activity": [],
                    "started_at": datetime.now(UTC).isoformat(),
                }
            websocket_url = str(session.get("websocketUrl") or session.get("websocket_url") or "")
            if websocket_url:
                websocket_url = self._normalise_steel_websocket_url(websocket_url, api_base)
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
                if task_id:
                    self._browser_event(task_id, "Connected to Steel browser session", status="active")
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
        if task_id:
            self._task_contexts[task_id] = context
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
        current_origin = (current.scheme, current.hostname, current.port)
        ranked: list[tuple[float, str]] = []
        for item in candidates:
            href = urljoin(page.url, str(item.get("href", "")))
            parsed = urlparse(href)
            candidate_origin = (parsed.scheme, parsed.hostname, parsed.port)
            if parsed.scheme not in {"http", "https"} or candidate_origin != current_origin:
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

    async def _collect_seo_discovery_signals(self, page: Page) -> dict:
        """Read robots.txt and a discovered sitemap without leaving the site origin."""
        from urllib.parse import urljoin

        origin = urlparse(page.url)
        base = f"{origin.scheme}://{origin.netloc}/"
        robots_url = urljoin(base, "robots.txt")
        self.policy.validate_url(robots_url, allow_private_networks=False)
        result = {
            "robots_txt": {"url": robots_url, "status": None, "sitemap_urls": [], "disallow_rules": []},
            "sitemap": {"url": None, "status": None, "url_count": 0, "sample_urls": []},
        }
        robots_text = ""
        try:
            response = await page.request.get(robots_url, timeout=10000)
            result["robots_txt"]["status"] = response.status
            if response.ok:
                robots_text = (await response.text())[:200000]
                result["robots_txt"]["sitemap_urls"] = [
                    line.split(":", 1)[1].strip()
                    for line in robots_text.splitlines()
                    if line.strip().casefold().startswith("sitemap:")
                    and line.split(":", 1)[1].strip()
                ][:10]
                result["robots_txt"]["disallow_rules"] = [
                    line.split(":", 1)[1].strip()
                    for line in robots_text.splitlines()
                    if line.strip().casefold().startswith("disallow:")
                ][:50]
        except Exception as exc:  # noqa: BLE001 - record unavailable endpoint, do not fail page capture
            result["robots_txt"]["error"] = str(exc)[:180]

        sitemap_candidates = list(result["robots_txt"]["sitemap_urls"])
        if not sitemap_candidates:
            sitemap_candidates = [urljoin(base, "sitemap.xml")]
        for sitemap_url in sitemap_candidates[:3]:
            try:
                parsed = urlparse(sitemap_url)
                if (parsed.scheme, parsed.hostname, parsed.port) != (origin.scheme, origin.hostname, origin.port):
                    continue
                self.policy.validate_url(sitemap_url, allow_private_networks=False)
                response = await page.request.get(sitemap_url, timeout=12000)
                if not response.ok:
                    if result["sitemap"]["status"] is None:
                        result["sitemap"].update({"url": sitemap_url, "status": response.status})
                    continue
                body = (await response.text())[:2000000]
                locs = re.findall(r"<loc>\\s*(.*?)\\s*</loc>", body, flags=re.IGNORECASE | re.DOTALL)
                urls = []
                for raw in locs:
                    candidate = re.sub(r"&amp;", "&", raw.strip())
                    parsed_candidate = urlparse(candidate)
                    if (parsed_candidate.scheme, parsed_candidate.hostname, parsed_candidate.port) == (origin.scheme, origin.hostname, origin.port):
                        urls.append(candidate)
                result["sitemap"] = {
                    "url": sitemap_url, "status": response.status,
                    "url_count": len(urls), "sample_urls": urls[:200],
                }
                break
            except Exception as exc:  # noqa: BLE001
                if result["sitemap"]["status"] is None:
                    result["sitemap"].update({"url": sitemap_url, "error": str(exc)[:180]})
        return result

    async def run_plan(self, task_id: str, url: str, steps: list[dict], objective: str) -> dict:
        """Execute only public-site navigation, same-origin link following and extraction."""
        self.policy.validate_url(url, allow_private_networks=False)
        self._task_sessions.setdefault(task_id, {
            "backend": self.backend, "status": "connecting", "active": True,
            "activity": [], "started_at": datetime.now(UTC).isoformat(),
        })
        self._task_sessions[task_id]["status"] = "connecting"
        self._task_sessions[task_id]["active"] = True
        self._browser_event(task_id, "Starting or reusing the task browser session")
        try:
            context = await self._guarded_context(task_id)
        except Exception as exc:
            self._browser_event(task_id, f"Browser session failed: {str(exc)[:350]}", status="failed")
            self._task_sessions[task_id]["last_error"] = str(exc)[:500]
            raise
        try:
            page = self._task_pages.get(task_id)
            if page is None or page.is_closed():
                page = next(
                    (candidate for candidate in getattr(context, "pages", []) if not candidate.is_closed()),
                    None,
                ) or await context.new_page()
                self._task_pages[task_id] = page
                # New tabs are briefly visible to the AI janitor after the page capture.
                # A hard cap remains active so popup storms cannot grow without bound.
                if hasattr(context, "on") and id(context) not in self._tab_listener_contexts:
                    context.on(
                        "page",
                        lambda opened: asyncio.create_task(self._enforce_tab_limit(task_id, opened)),
                    )
                    self._tab_listener_contexts.add(id(context))
            # Secondary pages are reviewed by the AI janitor in Orchestrator;
            # this runtime only enforces the hard cap and protects the primary page.
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000")),
            )
            http_status = response.status if response else None
            self._browser_event(task_id, "Navigated to target page", url=page.url, title=(await page.title())[:500])
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
                        self._browser_event(task_id, "Followed a relevant same-site link", url=page.url, title=(await page.title())[:500])
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
                    self._browser_event(task_id, "Reloaded the current page for recovery", url=page.url, title=(await page.title())[:500])
                elif capability == "extract":
                    continue

            title = (await page.title())[:500]
            self._browser_event(task_id, "Captured page title, headings and visible text", url=page.url, title=title)
            from nightwalker.workflows import classify_workflow
            workflow_type = classify_workflow(objective).value
            # Use the final navigated origin so a legitimate www/non-www redirect
            # does not cause every discovered page to be excluded.
            target_parts = urlparse(page.url)
            target_origin = (target_parts.scheme, target_parts.hostname, target_parts.port)
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
                candidate_parts = urlparse(candidate_url)
                candidate_origin = (candidate_parts.scheme, candidate_parts.hostname, candidate_parts.port)
                same_origin_required = workflow_type in {"seo", "qa", "seo_qa"}
                if (
                    candidate_url and candidate_url not in seen_candidates
                    and candidate_url != page.url
                    and (not same_origin_required or candidate_origin == target_origin)
                    and not (candidate_parts.hostname or "").lower().endswith(("bing.com", "microsoft.com"))
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
            from nightwalker.page_signals import collect_page_signals, page_signal_findings
            audit_signals = await collect_page_signals(page)
            if workflow_type in {"seo", "seo_qa"}:
                audit_signals["seo_discovery"] = await self._collect_seo_discovery_signals(page)
                sitemap_urls = (audit_signals["seo_discovery"].get("sitemap") or {}).get("sample_urls", [])
                for sitemap_url in sitemap_urls:
                    if sitemap_url != page.url and not any(item.get("url") == sitemap_url for item in candidates):
                        candidates.append({"url": sitemap_url, "label": "URL discovered in XML sitemap"})
                candidates = candidates[:200]
            audit_findings = page_signal_findings(audit_signals, workflow_type)
            wants_products = bool(re.search(r"(?i)\b(product|products|item|items|price|pricing)\b", objective))
            products = self._extract_products_from_text(body_text, limit=200) if wants_products else []
            if wants_products:
                try:
                    card_products = await page.locator(
                        '[data-product], .product-item, .product-card, li.product, '
                        '[class*="product-card"], [class*="product-item"]'
                    ).evaluate_all("""nodes => nodes.slice(0, 200).map(node => {
                        const link = node.querySelector('a[href]');
                        const nameNode = node.querySelector(
                            '[itemprop="name"], .product-name, .product-item-link, '
                            '[class*="product-title"], h2, h3, img[alt]'
                        );
                        const priceNode = node.querySelector(
                            '[itemprop="price"], .price, [class*="price"], [data-price]'
                        );
                        const name = (nameNode?.getAttribute('content') ||
                            nameNode?.getAttribute('alt') || nameNode?.innerText ||
                            link?.getAttribute('aria-label') || '').trim();
                        const price = (priceNode?.getAttribute('content') ||
                            priceNode?.getAttribute('data-price') || priceNode?.innerText || '').trim();
                        return {name, price, url: link?.href || ''};
                    }).filter(x => x.name && x.price)""")
                    for item in card_products:
                        price_match = re.search(r'(?i)R\s*[\d,]+(?:\.\d{2})?', item["price"])
                        if price_match:
                            products.append({"name": item["name"][:180], "price": price_match.group(0), "url": item["url"]})
                except Exception:
                    pass
                deduped = {}
                for item in products:
                    match = re.search(r"[\d,]+(?:\.\d{1,2})?", item["price"])
                    if match:
                        amount = float(match.group(0).replace(",", ""))
                        deduped[(item["name"].casefold(), amount)] = {**item, "price_value": amount}
                products = sorted(deduped.values(), key=lambda item: item["price_value"])
            if wants_products and not products:
                issues.append("The page was read, but no product name and price could be confidently extracted.")
            cheapest = products[0] if products else None
            answer = f"{cheapest['name']} — {cheapest['price']}" if cheapest else None
            retrieved_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC
            ).isoformat()
            findings = list(audit_findings)
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
            if workflow_type in {"seo", "seo_qa"}:
                structured_data["seo_checks"] = audit_signals.get("seo", {})
            if workflow_type in {"qa", "seo_qa"}:
                structured_data["qa_checks"] = audit_signals.get("qa", {})
            limitations = list(issues)
            if workflow_type in {"seo", "seo_qa"}:
                limitations.append("Page-level SEO signals only: robots.txt, XML sitemap coverage, full-site crawl completeness, structured-data validation and field Core Web Vitals are not yet measured.")
            if workflow_type in {"qa", "seo_qa"}:
                limitations.append("Read-only QA smoke check only: interactive journeys, form submission, keyboard navigation, cross-viewport rendering and automated accessibility rules are not yet executed.")
            if not body_text.strip():
                limitations.append("No visible page text was available to analyse.")
            report = {
                "schema_version": "1.0",
                "workflow_type": workflow_type,
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
                "http_status": http_status,
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
                "http_status": http_status,
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
            # Keep one context and one page alive for the entire research task.
            # TaskManager closes the session once the task reaches a terminal state.
            self._browser_event(task_id, "Research round captured; reusing the same browser tab", status="active")

    async def extract_basic(self, url: str) -> dict:
        """Compatibility endpoint for a deterministic title/H1 extraction."""
        task_id = "direct-browser-extract"
        try:
            result = await self.run_plan(
                task_id=task_id,
                url=url,
                steps=[{"capability": "navigate"}, {"capability": "extract"}],
                objective="Extract page title, headings and visible text",
            )
        finally:
            await self.close_task_session(task_id)
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
