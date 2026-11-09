from __future__ import annotations

import asyncio
import base64
import copy
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
            if re.search(
                r"(?i)(logout|log-out|delete|remove|checkout|add-to-cart|cart/add|purchase|order|subscribe|unsubscribe|signout|sign-out)",
                parsed.path + "?" + parsed.query,
            ):
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

    async def _collect_seo_discovery_signals(
        self, page: Page, candidate_urls: list[str] | None = None, task_id: str | None = None,
    ) -> dict:
        """Inspect robots.txt and bounded same-origin sitemap indexes/URL sets once per task origin."""
        from html import unescape
        from urllib.robotparser import RobotFileParser
        from urllib.parse import urljoin

        origin = urlparse(page.url)
        base = f"{origin.scheme}://{origin.netloc}/"
        robots_url = urljoin(base, "robots.txt")
        session = self._task_sessions.get(task_id or "", {})
        cache = session.get("seo_discovery_cache") or {}
        if cache.get("origin") == f"{origin.scheme}://{origin.netloc}":
            cached_result = copy.deepcopy(cache.get("result") or {})
            cached_text = str(cache.get("robots_text") or "")
            if cached_text:
                cached_parser = RobotFileParser(robots_url)
                cached_parser.parse(cached_text.splitlines())
                cached_result.setdefault("robots_txt", {})["target_url_allowed"] = cached_parser.can_fetch(
                    "GingerNightWalker", page.url
                )
                cached_result["robots_txt"]["candidate_urls_disallowed"] = [
                    url for url in (candidate_urls or [])
                    if not cached_parser.can_fetch("GingerNightWalker", url)
                ][:100]
            return cached_result
        self.policy.validate_url(robots_url, allow_private_networks=False)
        result = {
            "robots_txt": {
                "url": robots_url, "status": None, "sitemap_urls": [],
                "disallow_rules": [], "target_url_allowed": None,
                "candidate_urls_disallowed": [],
            },
            "sitemap": {
                "url": None, "status": None, "url_count": 0,
                "sample_urls": [], "child_sitemap_count": 0,
            },
        }
        robots_text = ""
        parser = RobotFileParser(robots_url)
        try:
            response = await page.request.get(robots_url, timeout=10000)
            result["robots_txt"]["status"] = response.status
            if response.ok:
                robots_text = (await response.text())[:200000]
                parser.parse(robots_text.splitlines())
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
                result["robots_txt"]["target_url_allowed"] = parser.can_fetch(
                    "GingerNightWalker", page.url
                )
                result["robots_txt"]["candidate_urls_disallowed"] = [
                    url for url in (candidate_urls or [])
                    if not parser.can_fetch("GingerNightWalker", url)
                ][:100]
            elif response.status >= 500:
                result["robots_txt"]["crawl_policy"] = "temporarily_unavailable_conservatively_blocked"
            else:
                result["robots_txt"]["crawl_policy"] = "robots_unavailable"
        except Exception as exc:  # noqa: BLE001 - record endpoint outcome
            result["robots_txt"]["error"] = str(exc)[:180]
            result["robots_txt"]["crawl_policy"] = "unknown"

        def same_origin(value: str) -> bool:
            parsed = urlparse(value)
            return (parsed.scheme, parsed.hostname, parsed.port) == (
                origin.scheme, origin.hostname, origin.port
            )

        def loc_values(xml_text: str) -> tuple[str, list[str]]:
            # Bounded tag extraction avoids expanding untrusted XML entities.
            root_match = re.search(r"<\\s*(?:[a-z0-9_-]+:)?(sitemapindex|urlset)\\b", xml_text, re.I)
            root_name = root_match.group(1).casefold() if root_match else ""
            values = [
                unescape(match).strip()
                for match in re.findall(
                    r"<(?:[a-z0-9_-]+:)?loc\\b[^>]*>\\s*(.*?)\\s*</(?:[a-z0-9_-]+:)?loc\\s*>",
                    xml_text, flags=re.I | re.S,
                )
                if match.strip()
            ]
            return root_name, values

        sitemap_candidates = list(result["robots_txt"]["sitemap_urls"])
        if not sitemap_candidates:
            sitemap_candidates = [urljoin(base, "sitemap.xml")]
        page_urls: list[str] = []
        child_sitemaps: list[str] = []
        sitemap_errors: list[str] = []
        for sitemap_url in sitemap_candidates[:5]:
            try:
                if not same_origin(sitemap_url):
                    continue
                self.policy.validate_url(sitemap_url, allow_private_networks=False)
                response = await page.request.get(sitemap_url, timeout=12000)
                if not response.ok:
                    if result["sitemap"]["status"] is None:
                        result["sitemap"].update({"url": sitemap_url, "status": response.status})
                    continue
                root_name, locs = loc_values((await response.text())[:2000000])
                if root_name == "sitemapindex":
                    child_sitemaps.extend(url for url in locs if same_origin(url))
                else:
                    page_urls.extend(url for url in locs if same_origin(url))
                if result["sitemap"]["url"] is None:
                    result["sitemap"].update({"url": sitemap_url, "status": response.status})
            except Exception as exc:  # noqa: BLE001
                sitemap_errors.append(str(exc)[:160])

        for child_url in child_sitemaps[:20]:
            try:
                if not same_origin(child_url):
                    continue
                self.policy.validate_url(child_url, allow_private_networks=False)
                response = await page.request.get(child_url, timeout=12000)
                if not response.ok:
                    sitemap_errors.append(f"{child_url}: HTTP {response.status}")
                    continue
                root_name, locs = loc_values((await response.text())[:2000000])
                if root_name != "sitemapindex":
                    page_urls.extend(url for url in locs if same_origin(url))
            except Exception as exc:  # noqa: BLE001
                sitemap_errors.append(str(exc)[:160])

        if robots_text and result["robots_txt"].get("status", 0) < 500:
            page_urls = [
                url for url in page_urls
                if parser.can_fetch("GingerNightWalker", url)
            ]
        elif result["robots_txt"].get("status", 0) >= 500 or result["robots_txt"].get("crawl_policy") == "unknown":
            page_urls = []

        page_urls = list(dict.fromkeys(page_urls))
        result["sitemap"].update({
            "url_count": len(page_urls),
            "sample_urls": page_urls[:1000],
            "child_sitemap_count": len(child_sitemaps),
        })
        if sitemap_errors:
            result["sitemap"]["errors"] = sitemap_errors[:20]
        if task_id:
            session["seo_discovery_cache"] = {
                "origin": f"{origin.scheme}://{origin.netloc}",
                "result": copy.deepcopy(result),
                "robots_text": robots_text,
            }
        return result

    async def _run_safe_interaction_smoke(self, page: Page, limit: int = 5) -> dict:
        """Exercise only reversible disclosure controls; never submit forms or business actions."""
        selector = 'button[aria-expanded], details > summary'
        locator = page.locator(selector)
        checks = []
        try:
            count = await locator.count()
        except Exception as exc:  # noqa: BLE001
            return {"checks": [], "checked_count": 0, "error": str(exc)[:180]}
        for index in range(min(count, max(1, min(8, limit)))):
            item = locator.nth(index)
            try:
                if not await item.is_visible():
                    continue
                before = await item.evaluate("""el => ({
                  tag: el.tagName.toLowerCase(),
                  in_form: Boolean(el.closest("form")),
                  type: el.getAttribute("type"),
                  expanded: el.getAttribute("aria-expanded"),
                  details_open: el.parentElement?.tagName === "DETAILS" ? el.parentElement.open : null,
                  label: (el.getAttribute("aria-label") || el.innerText || "").trim().slice(0,120)
                })""")
                if before["in_form"] or before["type"] == "submit":
                    continue
                if before["expanded"] not in {"true", "false"} and before["details_open"] is not False:
                    continue
                await item.click(timeout=2500)
                after = await item.evaluate("""el => ({
                  expanded: el.getAttribute("aria-expanded"),
                  details_open: el.parentElement?.tagName === "DETAILS" ? el.parentElement.open : null
                })""")
                changed = (
                    after["expanded"] != before["expanded"]
                    if before["expanded"] in {"true", "false"}
                    else after["details_open"] != before["details_open"]
                )
                restored = False
                if changed:
                    await item.click(timeout=2500)
                    restored_state = await item.evaluate("""el => ({
                      expanded: el.getAttribute("aria-expanded"),
                      details_open: el.parentElement?.tagName === "DETAILS" ? el.parentElement.open : null
                    })""")
                    restored = (
                        restored_state["expanded"] == before["expanded"]
                        and restored_state["details_open"] == before["details_open"]
                    )
                checks.append({
                    "label": before["label"], "tag": before["tag"],
                    "changed_state": changed, "restored": restored,
                    "ok": changed and restored,
                    "interaction": "reversible_local_disclosure_toggle",
                })
            except Exception as exc:  # noqa: BLE001 - record rather than abort audit
                checks.append({"ok": False, "error": str(exc)[:180]})
        return {
            "checks": checks,
            "checked_count": len(checks),
            "passed_count": sum(item.get("ok") is True for item in checks),
            "scope": "Only reversible aria-expanded buttons and details disclosures; no forms or business actions activated.",
        }

    async def _run_readonly_navigation_smoke(self, page: Page, limit: int = 5) -> dict:
        """Navigate a small sample of safe same-origin anchors using GET only."""
        original_url = page.url
        origin = urlparse(original_url)
        links = await page.locator("a[href]").evaluate_all(
            """nodes => nodes.map(a => ({
              url: a.href,
              label: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0,120)
            }))"""
        )
        seen = set()
        checks = []
        skipped = 0
        for link in links:
            candidate = str(link.get("url") or "").split("#", 1)[0]
            parsed = urlparse(candidate)
            if (
                parsed.scheme not in {"http", "https"}
                or (parsed.scheme, parsed.hostname, parsed.port) != (origin.scheme, origin.hostname, origin.port)
                or candidate.rstrip("/") == original_url.rstrip("/")
                or candidate in seen
            ):
                continue
            seen.add(candidate)
            if re.search(r"(?i)(logout|log-out|delete|remove|checkout|add-to-cart|cart/add|purchase|order|subscribe|unsubscribe|signout|sign-out)", parsed.path + "?" + parsed.query):
                skipped += 1
                continue
            if len(checks) >= max(1, min(10, limit)):
                break
            item = {"url": candidate, "label": str(link.get("label") or ""), "method": "browser_get_navigation"}
            try:
                self.policy.validate_url(candidate, allow_private_networks=False)
                response = await page.goto(
                    candidate, wait_until="domcontentloaded",
                    timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000")),
                )
                item.update({
                    "status": response.status if response else None,
                    "final_url": page.url,
                    "title": (await page.title())[:240],
                    "ok": bool(response is None or response.status < 400),
                    "same_origin_after_redirect": (
                        (urlparse(page.url).scheme, urlparse(page.url).hostname, urlparse(page.url).port)
                        == (origin.scheme, origin.hostname, origin.port)
                    ),
                })
            except Exception as exc:  # noqa: BLE001 - preserve failed journey as evidence
                item.update({"ok": False, "error": str(exc)[:180]})
            checks.append(item)
            try:
                await page.goto(
                    original_url, wait_until="domcontentloaded",
                    timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000")),
                )
            except Exception as exc:  # noqa: BLE001
                item["restore_error"] = str(exc)[:160]
                break
        if page.url != original_url:
            try:
                await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
            except Exception as exc:  # noqa: BLE001
                return {
                    "checks": checks, "checked_count": len(checks), "skipped_sensitive_count": skipped,
                    "restored": False, "restore_error": str(exc)[:180],
                    "scope": "Direct same-origin GET navigation only; no forms or state-changing controls activated.",
                }
        return {
            "checks": checks, "checked_count": len(checks), "skipped_sensitive_count": skipped,
            "restored": page.url == original_url,
            "broken_count": sum(item.get("ok") is False for item in checks),
            "scope": "Direct same-origin GET navigation only; no forms or state-changing controls activated.",
        }

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
            from nightwalker.page_signals import (
                collect_internal_link_checks, collect_page_signals,
                collect_qa_extended_signals, page_signal_findings,
            )
            from nightwalker.audit_analysis import content_fingerprint
            audit_signals = await collect_page_signals(page)
            x_robots_tag = (response.headers.get("x-robots-tag") if response else None)
            audit_signals.setdefault("seo", {})["x_robots_tag"] = x_robots_tag
            if x_robots_tag and re.search(r"(?i)\\bnoindex\\b", x_robots_tag):
                audit_signals["seo"]["noindex"] = True
            audit_signals["internal_link_checks"] = await collect_internal_link_checks(page, limit=40)
            if workflow_type in {"seo", "seo_qa"}:
                audit_signals["seo_discovery"] = await self._collect_seo_discovery_signals(
                    page, [item.get("url", "") for item in candidates], task_id=task_id
                )
                sitemap_urls = (audit_signals["seo_discovery"].get("sitemap") or {}).get("sample_urls", [])
                robots = audit_signals["seo_discovery"].get("robots_txt") or {}
                disallowed = set(robots.get("candidate_urls_disallowed") or [])
                for sitemap_url in sitemap_urls:
                    if sitemap_url != page.url and not any(item.get("url") == sitemap_url for item in candidates):
                        candidates.append({"url": sitemap_url, "label": "URL discovered in XML sitemap"})
                if robots.get("crawl_policy") in {"temporarily_unavailable_conservatively_blocked", "unknown"}:
                    candidates = []
                else:
                    candidates = [item for item in candidates if item.get("url") not in disallowed]
                candidates = candidates[:1000]
            if workflow_type in {"qa", "seo_qa"}:
                audit_signals["qa_extended"] = await collect_qa_extended_signals(page)
                audit_signals["qa_navigation_journeys"] = await self._run_readonly_navigation_smoke(page, limit=5)
                audit_signals["qa_interaction_journeys"] = await self._run_safe_interaction_smoke(page, limit=5)
            audit_findings = page_signal_findings(audit_signals, workflow_type)
            for check in audit_signals["internal_link_checks"].get("checks", []):
                if check.get("ok") is False:
                    audit_findings.append({
                        "claim": f"Internal link destination returned HTTP {check.get('status')}: {check.get('url')}",
                        "type": "broken_internal_link", "severity": "high",
                        "affected_urls": [check.get("url")], "source_url": page.url,
                    })
            for viewport in (audit_signals.get("qa_extended") or {}).get("responsive_viewports", []):
                if viewport.get("horizontal_overflow"):
                    audit_findings.append({
                        "claim": f"Horizontal overflow detected at {viewport.get('viewport')} viewport ({viewport.get('viewport_width')}px).",
                        "type": "qa_responsive_issue", "severity": "medium",
                        "affected_url": page.url,
                    })
            qa_semantic = (audit_signals.get("qa_extended") or {}).get("semantic_accessibility") or {}
            for field, description, severity in (
                ("visible_form_controls_without_label", "visible form control(s) lack a detectable programmatic label", "high"),
                ("links_without_accessible_name", "visible link(s) lack a detectable accessible name", "medium"),
                ("buttons_without_accessible_name", "visible button(s) lack a detectable accessible name", "high"),
                ("focusable_inside_aria_hidden", "focusable element(s) are inside aria-hidden content", "high"),
            ):
                count = int(qa_semantic.get(field) or 0)
                if count:
                    audit_findings.append({
                        "claim": f"{count} {description}.",
                        "type": "qa_accessibility_issue", "severity": severity,
                        "affected_url": page.url,
                    })
            landmark_counts = qa_semantic.get("landmark_counts") or {}
            if not landmark_counts.get("main"):
                audit_findings.append({
                    "claim": "No main landmark was detected on the page.",
                    "type": "qa_accessibility_warning", "severity": "low",
                    "affected_url": page.url,
                })
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
            for finding in findings:
                finding.setdefault("affected_urls", [page.url])
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
                structured_data["seo_discovery"] = audit_signals.get("seo_discovery", {})
            if workflow_type in {"seo", "qa", "seo_qa"}:
                structured_data["crawl_page"] = {
                    "url": page.url, "title": title,
                    "canonical_url": (audit_signals.get("seo") or {}).get("canonical_url"),
                    "meta_description": (audit_signals.get("seo") or {}).get("meta_description"),
                    "content_fingerprint": content_fingerprint(body_text),
                    "http_status": http_status,
                }
                structured_data["internal_link_checks"] = audit_signals.get("internal_link_checks", {})
            if workflow_type in {"qa", "seo_qa"}:
                structured_data["qa_checks"] = audit_signals.get("qa", {})
                structured_data["qa_extended"] = audit_signals.get("qa_extended", {})
                structured_data["qa_navigation_journeys"] = audit_signals.get("qa_navigation_journeys", {})
                structured_data["qa_interaction_journeys"] = audit_signals.get("qa_interaction_journeys", {})
                structured_data["internal_link_checks"] = audit_signals.get("internal_link_checks", {})
            limitations = list(issues)
            if workflow_type in {"seo", "seo_qa"}:
                limitations.append("Crawl coverage is bounded by the configured round/page budget and discovered same-origin links/sitemap entries; inaccessible, JavaScript-only, orphaned and undiscovered URLs may be absent. Core Web Vitals are not measured.")
            if workflow_type in {"qa", "seo_qa"}:
                limitations.append("QA checks are read-only: forms are not submitted, state-changing controls are not activated, and keyboard focus is sampled rather than exhaustively tested. Automated WCAG conformance is not claimed.")
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
