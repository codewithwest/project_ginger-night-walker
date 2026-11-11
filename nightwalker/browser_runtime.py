from __future__ import annotations

import asyncio
import base64
import copy
import logging
import os
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import httpx
from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright

from nightwalker.evidence import EvidenceStore
from nightwalker.execution_control import ElementResolver
from nightwalker.policy import PolicyEngine
from nightwalker.workflows import extract_product_search_query
from nightwalker.steel_mcp import SteelMCPClient


class BrowserRuntime:
    """Task-isolated, read-only Playwright execution runtime.

    The model never receives a Playwright object. It can only influence the bounded
    plan produced by the Decider; this runtime owns and validates every browser action.
    """

    @staticmethod
    def _extract_product_search_query(objective: str) -> str | None:
        """Extract a product search query from a QA objective using broad patterns."""
        return extract_product_search_query(objective)

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._steel_sessions: dict[int, tuple[str, Browser]] = {}
        self._task_sessions: dict[str, dict] = {}
        self._task_contexts: dict[str, BrowserContext] = {}
        self._task_pages: dict[str, Page] = {}
        self._tab_listener_contexts: set[int] = set()
        self._qa_listener_pages: set[int] = set()
        self.backend = os.getenv("BROWSER_BACKEND", "local").strip().lower()
        if self.backend not in {"local", "steel"}:
            raise ValueError("BROWSER_BACKEND must be 'local' or 'steel'")
        self._allow_private_networks = os.getenv("ALLOW_PRIVATE_NETWORKS", "false").strip().lower() == "true"
        self.policy = PolicyEngine()
        self.element_resolver = ElementResolver()
        self.evidence = EvidenceStore()
        self.steel_mcp = SteelMCPClient()
        self._steel_mcp_start_error: str | None = None

    async def release_untracked_steel_sessions(self) -> int:
        """Delete Steel sessions this process does not own.

        A crashed or retried mission can leave an idle session with no timeout.
        Steel then refuses every new session, so the live viewer never appears.
        Idle leftovers from a previous run are always eligible for reclaim so a
        new mission never inherits another journey's browser.
        """
        api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")
        api_key = os.getenv("STEEL_API_KEY", "").strip()
        tracked = set(getattr(self.steel_mcp, "_sessions", {}).values())
        tracked.update(session_id for session_id, _browser in self._steel_sessions.values())
        headers = {"steel-api-key": api_key} if api_key else {}
        released = 0
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{api_base}/sessions", headers=headers)
                if response.status_code >= 400:
                    return 0
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return 0
        sessions = payload.get("sessions") if isinstance(payload, dict) else payload
        if not isinstance(sessions, list):
            return 0
        for item in sessions:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("id") or item.get("sessionId") or "")
            status = str(item.get("status") or "").casefold()
            if not session_id:
                continue
            # Already-released history can grow into hundreds of rows; never POST
            # /release for those or startup hangs reclaiming dead sessions.
            if status in {"released", "failed"}:
                continue
            if session_id in tracked and status not in {"idle"}:
                continue
            await self._release_steel_session(session_id)
            for task_id, mapped in list(getattr(self.steel_mcp, "_sessions", {}).items()):
                if mapped == session_id:
                    self.steel_mcp._sessions.pop(task_id, None)
            released += 1
        return released

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        if self.backend == "local":
            self._browser = await self._playwright.chromium.launch(headless=True)
        try:
            self.steel_mcp.reclaim_sessions = self.release_untracked_steel_sessions
            self.steel_mcp.release_remote_session = self._release_steel_session
            await self.steel_mcp.start()
            self._steel_mcp_start_error = None
            await self.release_untracked_steel_sessions()
        except Exception as exc:  # noqa: BLE001 - MCP is optional; preserve the Playwright runtime
            self._steel_mcp_start_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            logging.getLogger(__name__).warning(
                "Optional Steel MCP startup failed; continuing with the configured browser backend: %s",
                self._steel_mcp_start_error,
            )

    async def close(self) -> None:
        await self.steel_mcp.close()
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
        """End task ownership while retaining Steel's live viewer for report review."""
        # MCP-created sessions must be explicitly released when a task ends.
        # Retaining every completed MCP session consumes Steel's finite session slots;
        # the remote TTL is only a fallback, not the normal lifecycle mechanism.
        await self.steel_mcp.release_task_session(task_id)
        context = self._task_contexts.pop(task_id, None)
        primary_page = self._task_pages.pop(task_id, None)
        if primary_page is not None:
            self._qa_listener_pages.discard(id(primary_page))
        if context is not None:
            self._tab_listener_contexts.discard(id(context))
            for page in getattr(context, "pages", []):
                self._qa_listener_pages.discard(id(page))
        state = self._task_sessions.get(task_id)
        steel_session = self._steel_sessions.get(id(context)) if context is not None else None
        if steel_session and task_id != "direct-browser-extract":
            retention_ms = max(60_000, int(os.getenv("STEEL_SESSION_RETENTION_MS", "600000")))
            retained_until = datetime.now(UTC).timestamp() + retention_ms / 1000
            if state is not None:
                state["finished_at"] = datetime.now(UTC).isoformat()
                state["retained_until"] = datetime.fromtimestamp(retained_until, UTC).isoformat()
            self._browser_event(
                task_id,
                f"Steel browser retained for report review ({retention_ms // 60000} minute(s))",
                status="retained",
            )
            # Leave the remote context alive for the report iframe. Steel session TTL
            # is the final cleanup guard; app shutdown releases retained sessions.
            return
        if state:
            self._browser_event(task_id, "Browser session closed after task completion", status="finished")
            state["finished_at"] = datetime.now(UTC).isoformat()
        if context is not None:
            await context.close()
        if steel_session:
            session_id, browser = steel_session
            if browser.is_connected():
                await browser.close()
            await self._release_steel_session(session_id)

    @staticmethod
    def _normalise_steel_websocket_url(websocket_url: str, api_base: str) -> str:
        """Use a Steel-reachable IP for CDP while keeping REST on Docker DNS."""
        websocket = urlparse(websocket_url)
        api = urlparse(api_base)
        configured_host = os.getenv("STEEL_CDP_HOST", "auto").strip()
        host = configured_host
        if not host or host.lower() == "auto":
            api_host = (api.hostname or "").strip("[]")
            if not api_host:
                raise RuntimeError("STEEL_API_BASE_URL must include a reachable hostname")
            try:
                host = socket.gethostbyname(api_host)
            except OSError as exc:
                raise RuntimeError(
                    f"Could not resolve Steel API host {api_host!r} for CDP: {exc}"
                ) from exc
        netloc = f"[{host}]" if ":" in host else host
        # Steel rejects Docker service names in the WebSocket Host header.
        # Resolve the service name to its container IP; preserve the advertised
        # CDP port, path and token.
        port = websocket.port or api.port
        if port:
            netloc += f":{port}"
        scheme = "wss" if api.scheme == "https" else "ws"
        return websocket._replace(scheme=scheme, netloc=netloc).geturl()

    async def _release_steel_session(self, session_id: str) -> None:
        api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")
        api_key = os.getenv("STEEL_API_KEY", "").strip()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"steel-api-key": api_key} if api_key else {}
                # Current Steel builds reject DELETE and only free the slot via POST /release.
                response = await client.post(
                    f"{api_base}/sessions/{session_id}/release",
                    headers=headers,
                )
                if response.status_code == 404:
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
        """Pin the viewer to Steel's host and point it at the served live viewer route.

        Steel builds in use serve the live viewer only at the shared
        ``/v1/sessions/debug`` page (it streams the active cast). Per-session
        ``/sessions/{id}/debug`` routes return 404, so they are rewritten here.
        """
        parsed = urlparse(debug_url)
        api = urlparse(os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1"))
        host = os.getenv("STEEL_VIEWER_HOST", "localhost").strip()
        path = parsed.path
        if re.fullmatch(r"/(?:v1/)?sessions/(?:debug|[^/]+/debug)/?", path):
            prefix = "/v1" if path.startswith("/v1/") else ""
            path = f"{prefix}/sessions/debug"
        if not host:
            return parsed._replace(path=path).geturl()
        netloc = f"[{host}]" if ":" in host else host
        port = parsed.port or api.port
        if port:
            netloc += f":{port}"
        scheme = "https" if api.scheme == "https" else "http"
        return parsed._replace(scheme=scheme, netloc=netloc, path=path).geturl()

    @staticmethod
    def _steel_ui_url(api_base: str) -> str:
        """Build the externally reachable Steel UI URL from the configured API."""
        api = urlparse(api_base)
        host = os.getenv("STEEL_VIEWER_HOST", "localhost").strip() or "localhost"
        if not host:
            return ""
        netloc = f"[{host}]" if ":" in host else host
        if api.port:
            netloc += f":{api.port}"
        scheme = "https" if api.scheme == "https" else "http"
        return api._replace(scheme=scheme, netloc=netloc, path="/ui", params="", query="", fragment="").geturl()

    def _browser_event(
        self, task_id: str, message: str, *, url: str | None = None,
        title: str | None = None, status: str | None = None,
        action: str | None = None, plan_step_id: str | None = None,
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
            state["active"] = status in {"connecting", "active", "retained"}
        event = {
            "at": datetime.now(UTC).isoformat(),
            "message": message[:500],
            "url": url,
        }
        if action:
            event["action"] = action[:80]
        if plan_step_id:
            event["plan_step_id"] = plan_step_id[:120]
        state.setdefault("activity", []).append(event)
        state["activity"] = state["activity"][-30:]

    def steel_mcp_status(self) -> dict:
        return {
            "enabled": os.getenv("STEEL_MCP_ENABLED", "false").strip().lower() == "true",
            "mission_execution_enabled": os.getenv("STEEL_MCP_MISSION_ENABLED", "true").strip().lower() == "true",
            "connected": self.steel_mcp.connected,
            "tool_count": len(self.steel_mcp.tools),
            "tools": [item["name"] for item in self.steel_mcp.tools],
            "startup_error": self._steel_mcp_start_error,
        }

    def task_session(self, task_id: str) -> dict | None:
        """Return safe, task-scoped live browser observability for the UI."""
        state = self._task_sessions.get(task_id)
        if not state:
            return None
        if state.get("backend") == "steel" and state.get("status") in {"active", "retained"}:
            retained_until = state.get("retained_until")
            expired = False
            if state.get("status") == "retained" and retained_until:
                try:
                    expired = datetime.now(UTC) >= datetime.fromisoformat(retained_until)
                except ValueError:
                    expired = True
            session_id = state.get("session_id")
            browser = next(
                (item for sid, item in self._steel_sessions.values() if sid == session_id),
                None,
            )
            if expired or browser is None or not browser.is_connected():
                state["status"] = "offline"
                state["active"] = False
                state["last_error"] = "The Steel browser connection is no longer active."
        return {key: value for key, value in state.items() if key != "session_id"}

    async def _guarded_context(self, task_id: str | None = None) -> BrowserContext:
        if not self._playwright:
            raise RuntimeError("Browser runtime is not started")
        if task_id and task_id in self._task_contexts:
            return self._task_contexts[task_id]
        if self.backend == "steel":
            api_key = os.getenv("STEEL_API_KEY", "").strip()
            api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")
            task_timeout_ms = max(60_000, int(os.getenv("STEEL_SESSION_TIMEOUT_MS", "300000")))
            retention_ms = max(60_000, int(os.getenv("STEEL_SESSION_RETENTION_MS", "600000")))
            timeout_ms = task_timeout_ms + retention_ms
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
                    "steel_ui_url": self._steel_ui_url(api_base),
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
                    self.policy.validate_url, route.request.url,
                    allow_private_networks=self._allow_private_networks,
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
        # Resolve only a unique, grounded accessible link. Similar matches stay
        # ambiguous rather than allowing a guessed destination to drive navigation.
        resolution = self.element_resolver.resolve(
            [
                {"ref": href, "role": "link", "name": str(item.get("text", ""))}
                for score, href in ranked
                for item in candidates
                if urljoin(page.url, str(item.get("href", ""))) == href
            ],
            objective,
            role="link",
        )
        if resolution.status != "resolved" or not resolution.ref:
            return None
        destination = resolution.ref
        self.policy.validate_url(destination, allow_private_networks=False)
        await page.goto(
            destination,
            wait_until="domcontentloaded",
            timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
        )
        return page.url

    async def _run_product_search_smoke(
        self, page: Page, objective: str, task_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find a usable product search from the supplied page or the site's real catalogue."""
        query = self._extract_product_search_query(objective)
        if not query:
            return None
        route = [{"url": page.url, "action": "opened_requested_url"}]
        origin = urlparse(page.url)

        async def wait_for_search_ui(stage: str) -> None:
            """Wait for the SPA to render its search UI; DOMContentLoaded is not enough."""
            try:
                await page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=int(os.getenv("QA_SEARCH_DOM_TIMEOUT_MS", "8000")),
                )
            except Exception:
                # Some pages are already past DOMContentLoaded or expose unusual
                # navigation lifecycles. The bounded selector wait below is decisive.
                pass
            selector = (
                'input[type="search"], input[name*="search" i], input[id*="search" i], '
                'input[placeholder*="search" i], input[aria-label*="search" i], '
                'form[role="search"] input, [role="search"] input, '
                'form[action*="search" i] input, form[id*="search" i] input, '
                'form[class*="search" i] input, button[aria-label*="search" i], '
                'a[aria-label*="search" i], button[title*="search" i], a[title*="search" i], '
                '[role="button"][aria-label*="search" i]'
            )
            timeout_ms = max(1000, min(
                20000, int(os.getenv("QA_SEARCH_UI_TIMEOUT_MS", "10000"))
            ))
            try:
                await page.wait_for_function(
                    """selector => Array.from(document.querySelectorAll(selector)).some(el => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' &&
                            rect.width > 0 && rect.height > 0 && !el.disabled;
                    })""",
                    arg=selector,
                    timeout=timeout_ms,
                )
                if task_id:
                    self._browser_event(
                        task_id, f"Search UI became available after dynamic page rendering ({stage})",
                        url=page.url,
                    )
            except Exception:
                if task_id:
                    self._browser_event(
                        task_id, f"Search UI did not appear within {timeout_ms}ms ({stage}); inspecting current DOM",
                        url=page.url,
                    )

        async def visible_search_fields():
            # Storefronts frequently use an unlabelled text input inside a search
            # form. Inspect visible inputs and their form/container semantics rather
            # than requiring the input itself to contain the word "search".
            locator = page.locator(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea'
            )
            ranked = []
            for index in range(min(await locator.count(), 40)):
                field = locator.nth(index)
                try:
                    if not await field.is_visible() or not await field.is_enabled():
                        continue
                    score = await field.evaluate("""el => {
                        const attrs = [
                            el.type, el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
                            el.getAttribute('autocomplete'), el.className,
                            el.form && [el.form.getAttribute('role'), el.form.getAttribute('action'),
                                el.form.id, el.form.className, el.form.innerHTML].join(' ')
                        ].join(' ').toLowerCase();
                        let value = 0;
                        if (/search|query|keyword|find/.test(attrs)) value += 8;
                        if (el.type === 'search') value += 12;
                        if (el.form && (el.form.getAttribute('role') === 'search' ||
                            /search/i.test([el.form.action, el.form.id, el.form.className].join(' ')))) value += 10;
                        if (el.form && /search|query|keyword|magnif/.test(attrs)) value += 6;
                        if (el.disabled || el.readOnly) value = -100;
                        return value;
                    }""")
                    if score > 0:
                        ranked.append((score, index, field))
                except Exception:
                    continue
            ranked.sort(key=lambda item: (-item[0], item[1]))
            # Prefer the strongest semantic match; multiple search boxes (e.g.
            # desktop/mobile duplicates) are not a reason to abandon the search.
            return [ranked[0][2]] if ranked else []

        async def discover_catalogue_links():
            links = await page.locator("a[href]").evaluate_all(
                """nodes => nodes.slice(0, 2000).map(a => ({
                    href: a.href,
                    text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim()
                }))"""
            )
            candidates = []
            seen = set()
            for link in links:
                href = str(link.get("href") or "")
                label = str(link.get("text") or "")
                parsed = urlparse(href)
                if (parsed.scheme, parsed.hostname, parsed.port) != (origin.scheme, origin.hostname, origin.port):
                    continue
                signal = (label + " " + parsed.path).casefold()
                if not re.search(
                    r"\b(products?|product range|furniture|homeware|shop|catalogue|catalog|collection|store)\b",
                    signal,
                ):
                    continue
                clean = href.split("#", 1)[0]
                if clean and clean not in seen and clean != page.url:
                    seen.add(clean)
                    candidates.append({"url": clean, "label": label[:160]})
            candidates.sort(key=lambda item: (
                0 if re.search(r"\b(furniture|products?|shop|catalogue|catalog)\b", item["label"], re.I) else 1,
                0 if re.search(r"/(furniture|products?|shop|catalogue|catalog)(/|$)", urlparse(item["url"]).path, re.I) else 1,
            ))
            return candidates

        async def reveal_search_if_needed():
            fields = await visible_search_fields()
            if fields:
                return fields
            # Many themes expose only an icon button (often an SVG with no
            # accessible label). Score visible controls by their attributes and
            # descendant SVG/icon markup; do not click arbitrary page buttons.
            triggers = page.locator('button, [role="button"], a')
            ranked = []
            for index in range(min(await triggers.count(), 120)):
                trigger = triggers.nth(index)
                try:
                    if not await trigger.is_visible() or not await trigger.is_enabled():
                        continue
                    score = await trigger.evaluate("""el => {
                        const signal = [
                            el.getAttribute('aria-label'), el.title, el.id, el.className,
                            el.getAttribute('data-testid'), el.getAttribute('data-action'),
                            el.innerText, el.querySelector('svg')?.outerHTML,
                            el.querySelector('use')?.getAttribute('href')
                        ].join(' ').toLowerCase();
                        if (/search|magnif|\bfind\b/.test(signal)) return 10;
                        return 0;
                    }""")
                    if score:
                        ranked.append((score, index, trigger))
                except Exception:
                    continue
            ranked.sort(key=lambda item: (-item[0], item[1]))
            if ranked:
                try:
                    await ranked[0][2].click(timeout=3000)
                    await page.wait_for_timeout(500)
                except Exception:
                    pass
            return await visible_search_fields()

        # The search control is injected asynchronously on some storefronts.
        # Wait for the SPA's actual control, not just the navigation lifecycle.
        await wait_for_search_ui("requested page")
        fields = await reveal_search_if_needed()
        if not fields:
            candidates = await discover_catalogue_links()
            # A not-found/empty route may not render the site's navigation.
            # In that case inspect the origin homepage for the real catalogue link.
            if not candidates and origin.scheme and origin.netloc:
                homepage = f"{origin.scheme}://{origin.netloc}/"
                if page.url.rstrip("/") != homepage.rstrip("/"):
                    try:
                        response = await page.goto(
                            homepage, wait_until="domcontentloaded",
                            timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
                        )
                        route.append({
                            "url": page.url, "action": "opened_homepage_for_catalogue_discovery",
                            "http_status": response.status if response else None,
                        })
                        candidates = await discover_catalogue_links()
                    except Exception as exc:
                        return {
                            "query": query, "performed": False, "status": "catalogue_discovery_failed",
                            "route": route, "error": str(exc)[:300],
                            "result_count": 0, "matches": [],
                        }
            if not candidates:
                return {
                    "query": query, "performed": False, "status": "search_and_catalogue_not_found",
                    "route": route, "result_count": 0, "matches": [],
                    "reason": "No usable search field or same-origin furniture/product catalogue link was found."
                }
            destination = candidates[0]["url"]
            try:
                response = await page.goto(
                    destination, wait_until="domcontentloaded",
                    timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
                )
            except Exception as exc:
                return {
                    "query": query, "performed": False, "status": "catalogue_navigation_failed",
                    "route": route + [{"url": destination, "action": "navigation_failed", "error": str(exc)[:300]}],
                    "result_count": 0, "matches": []
                }
            route.append({
                "url": page.url, "action": "opened_discovered_catalogue",
                "http_status": response.status if response else None,
                "discovered_from": route[-1]["url"], "link_label": candidates[0]["label"]
            })
            await wait_for_search_ui("discovered catalogue")
            fields = await reveal_search_if_needed()

        if len(fields) != 1:
            return {
                "query": query, "performed": False,
                "status": "search_control_not_found" if not fields else "ambiguous_search_control",
                "visible_search_controls": len(fields), "route": route,
                "result_count": 0, "matches": []
            }

        field = fields[0]
        before_url = page.url
        before_cards = await page.locator(
            '.productinfo, .product-image-wrapper, .single-products, .product-card, '
            '.product-item, [data-product-id], [data-product]'
        ).evaluate_all(
            "nodes => nodes.slice(0, 100).map(node => (node.innerText || '').trim().slice(0, 500))"
        )
        # Use real browser input events in the same order as a person:
        # move/click the pointer on the field, type with the keyboard, then Enter.
        # Do not use fill(), requestSubmit(), or synthetic form submission here;
        # those bypass the interaction path that QA is intended to exercise.
        try:
            await field.click(timeout=5000, force=False)
            route.append({
                "url": page.url, "action": "mouse_clicked_search_input",
                "query": query,
            })
            focused = await field.evaluate("(el) => document.activeElement === el")
            if not focused:
                return {
                    "query": query, "performed": False, "status": "search_input_focus_failed",
                    "route": route, "result_count": 0, "matches": [],
                    "reason": "The search field did not receive browser keyboard focus after the mouse click."
                }
            await page.keyboard.type(query, delay=35)
            route.append({
                "url": page.url, "action": "keyboard_typed_search_query",
                "typed_length": len(query),
            })
            typed_value = await field.input_value()
            if typed_value.strip().casefold() != query.strip().casefold():
                return {
                    "query": query, "performed": False, "status": "search_input_text_mismatch",
                    "route": route, "result_count": 0, "matches": [],
                    "observed_input_value": typed_value[:240],
                    "reason": "The visible search field value did not match the requested query after keyboard typing."
                }
            await page.keyboard.press("Enter")
            submit_method = "mouse_click_type_enter"
            route.append({
                "url": page.url, "action": "keyboard_pressed_enter_to_submit_search",
            })
        except Exception as exc:
            return {
                "query": query, "performed": False, "status": "search_keyboard_interaction_failed",
                "route": route, "result_count": 0, "matches": [],
                "submit_method": "mouse_click_type_enter", "error": str(exc)[:300],
            }
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        settle_ms = min(4000, max(1000, int(os.getenv("QA_PRODUCT_RESULT_SETTLE_MS", "2000"))))
        try:
            await page.wait_for_function(
                "(oldUrl) => location.href !== oldUrl", arg=before_url, timeout=1500
            )
        except Exception:
            pass
        await page.wait_for_timeout(settle_ms)
        submit_event_observed = False
        after_url = page.url
        matches = await page.locator(
            '.productinfo, .product-image-wrapper, .single-products, .product-card, '
            '.product-item, [data-product-id], [data-product]'
        ).evaluate_all(
            """nodes => nodes.slice(0, 100).map(node => {
                const image = node.querySelector('img');
                const titleNode = node.querySelector(
                    '[class*="product-name"], [class*="product-title"], h2, h3, h4, ' +
                    '[itemprop="name"], a[title]'
                );
                const title = (
                    (titleNode && (titleNode.innerText || titleNode.getAttribute('title'))) ||
                    (image && image.getAttribute('alt')) || ''
                ).trim();
                return {
                    text: (node.innerText || '').trim().slice(0, 1200),
                    product_title: title.slice(0, 240),
                    image_src: image ? (image.currentSrc || image.src || image.getAttribute('src') || '') : '',
                    image_alt: image ? (image.getAttribute('alt') || '') : '',
                    image_complete: image ? !!image.complete : false,
                    image_natural_width: image ? (image.naturalWidth || 0) : 0,
                    product_url: (node.querySelector('a[href]') && node.querySelector('a[href]').href) || ''
                };
            })"""
        )
        normalise = lambda value: re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
        query_normal = normalise(query)
        query_tokens = set(query_normal.split())
        matched = []
        for item in matches:
            title_value = str(item.get("product_title") or "")
            title_normal = normalise(title_value)
            title_tokens = set(title_normal.split())
            # Match the product title, not incidental mentions in card copy or page chrome.
            phrase_match = bool(query_normal and query_normal in title_normal)
            token_match = bool(query_tokens and query_tokens.issubset(title_tokens))
            if not (phrase_match or token_match):
                continue
            text_value = str(item.get("text") or "")
            price = re.search(r'(?i)(?:Rs\\.?\\s*|R\\s*)[\\d][\\d,\\s]*(?:\\.\\d{1,2})?', text_value)
            matched.append({
                **item, "name_match": title_value,
                "query_match": "phrase" if phrase_match else "all_query_tokens",
                "price": price.group(0).strip() if price else None,
                "image_present": bool(item.get("image_src")),
                "image_loaded": bool(item.get("image_complete") and item.get("image_natural_width", 0) > 0),
            })
        after_cards = [str(item.get("text") or "")[:500] for item in matches]
        no_results = await page.locator(
            'text=/no products found|no results found|no items found|0 results/i'
        ).count() > 0
        results_changed = after_url != before_url or after_cards != before_cards or no_results
        performed = submit_event_observed or results_changed
        if not performed:
            status = "search_submission_unconfirmed"
        elif matched:
            status = "matched"
        elif no_results:
            status = "no_results"
        else:
            status = "submitted_results_unconfirmed"
        return {
            "query": query, "performed": performed, "status": status,
            "submit_method": submit_method,
            "submit_event_observed": submit_event_observed,
            "results_changed": results_changed,
            "result_count": len(matches), "matches": matched[:12],
            "searched_url": page.url, "route": route,
        }

    async def _execute_interaction(
        self, page: Page, objective: str, workflow_type: str,
        existing_product_search: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Execute a requested QA search, or fall back to a grounded same-site link."""
        query = self._extract_product_search_query(objective)
        if query and workflow_type in {"qa", "seo_qa"}:
            if (
                existing_product_search
                and str(existing_product_search.get("query", "")).casefold() == query.casefold()
            ):
                return existing_product_search, None
            if task_id:
                return await self._run_product_search_smoke(page, objective, task_id), None
            return await self._run_product_search_smoke(page, objective), None
        return None, await self._follow_relevant_link(page, objective)

    @staticmethod
    def _qa_product_evidence(
        search_result: dict[str, Any] | None, source_page_url: str,
    ) -> list[dict[str, Any]]:
        """Expose exact product-card values and their provenance for the final report."""
        if not isinstance(search_result, dict):
            return []
        evidence = []
        for match in search_result.get("matches", []):
            if not isinstance(match, dict):
                continue
            image_present = bool(match.get("image_src"))
            evidence.append({
                "product_name": str(match.get("name_match") or match.get("product_title") or ""),
                "query": str(search_result.get("query") or ""),
                "price": match.get("price"),
                "image_url": match.get("image_src") or None,
                "image_status": (
                    "loaded" if match.get("image_loaded")
                    else "not_confirmed_loaded" if image_present else "not_present"
                ),
                "product_url": match.get("product_url") or None,
                "source_page_url": search_result.get("searched_url") or source_page_url,
                "price_source": "visible_product_card_text" if match.get("price") else None,
                "image_source": "rendered_product_card_image" if image_present else None,
            })
        return evidence

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
        from urllib.parse import urljoin
        from urllib.robotparser import RobotFileParser

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
            cached_result["cache_hit"] = True
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
            root_match = re.search(r"<\s*(?:[a-z0-9_-]+:)?(sitemapindex|urlset)\b", xml_text, re.IGNORECASE)
            root_name = root_match.group(1).casefold() if root_match else ""
            values = [
                unescape(match).strip()
                for match in re.findall(
                    r"<(?:[a-z0-9_-]+:)?loc\b[^>]*>\s*(.*?)\s*</(?:[a-z0-9_-]+:)?loc\s*>",
                    xml_text, flags=re.IGNORECASE | re.DOTALL,
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

        sitemap_disallowed_count = 0
        if robots_text and result["robots_txt"].get("status", 0) < 500:
            sitemap_disallowed_count = sum(
                not parser.can_fetch("GingerNightWalker", url) for url in page_urls
            )
            page_urls = [
                url for url in page_urls
                if parser.can_fetch("GingerNightWalker", url)
            ]
        elif result["robots_txt"].get("status", 0) >= 500 or result["robots_txt"].get("crawl_policy") == "unknown":
            sitemap_disallowed_count = len(page_urls)
            page_urls = []

        page_urls = list(dict.fromkeys(page_urls))
        result["sitemap"].update({
            "disallowed_url_count": sitemap_disallowed_count,
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
                    timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
                )
                item.update({
                    "status": response.status if response else None,
                    "final_url": page.url,
                    "title": (await page.title())[:240],
                    "ok": bool(response is None or response.status < 400) and (
                        (urlparse(page.url).scheme, urlparse(page.url).hostname, urlparse(page.url).port)
                        == (origin.scheme, origin.hostname, origin.port)
                    ),
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
                    timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
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

    @staticmethod
    def _restore_qa_product_search(session: dict, query: str | None) -> dict[str, Any] | None:
        """Restore only the matching query's captured cards from task-scoped state."""
        stored = session.get("qa_product_search") if isinstance(session, dict) else None
        if (
            isinstance(stored, dict) and query
            and str(stored.get("query") or "").casefold() == query.casefold()
        ):
            return copy.deepcopy(stored)
        return None

    async def run_steel_mcp_step(
        self, task_id: str, url: str, step: dict, objective: str, ollama,
    ) -> dict[str, Any]:
        """Run one authorised mission step through the official Steel MCP tool surface."""
        self.policy.validate_url(url, allow_private_networks=False)
        capability = str(step.get("capability") or "").strip().lower()
        step_id = str(step.get("step_id") or "")
        step_objective = str(step.get("objective") or "").strip()
        if capability not in {"navigate", "interact", "extract", "recover"} or not step_id or not step_objective:
            raise PermissionError("Invalid authorised mission step for Steel MCP")
        self._task_sessions.setdefault(task_id, {
            "backend": "steel-mcp", "status": "connecting", "active": True,
            "activity": [], "started_at": datetime.now(UTC).isoformat(),
        })
        state = self._task_sessions[task_id]
        state["backend"] = "steel-mcp"
        self._browser_event(
            task_id, f"Steel MCP executing authorised {capability} step: {step_objective}",
            url=url, action="steel_mcp_" + capability, plan_step_id=step_id,
        )
        api_base = os.getenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1").rstrip("/")

        def expose_live_session(session_id: str) -> None:
            # Publish the viewer as soon as Steel allocates the session, before
            # navigation or snapshot capture can take a long time.
            state["session_id"] = session_id
            state["debug_url"] = self._normalise_steel_debug_url(f"{api_base}/sessions/debug")
            state["steel_ui_url"] = self._steel_ui_url(api_base)
            state["status"] = "active"
            state["active"] = True
            self._browser_event(
                task_id, "Steel browser session created; live viewer is available",
                url=url, action="steel_session_ready", plan_step_id=step_id,
            )

        timeout_seconds = max(30, min(300, int(os.getenv("STEEL_MCP_STEP_TIMEOUT_SECONDS", "120"))))
        try:
            result = await asyncio.wait_for(
                self.steel_mcp.execute_mission_step(
                    task_id, url, step_objective, capability,
                    ollama.propose_steel_mcp_tool_call,
                    session_callback=expose_live_session,
                    mission_objective=objective,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            await self.steel_mcp.release_task_session(task_id)
            message = f"Steel MCP {capability} step timed out after {timeout_seconds} seconds"
            self._browser_event(
                task_id, message, url=url, status="failed",
                action="steel_mcp_timeout", plan_step_id=step_id,
            )
            state["last_error"] = message
            state["active"] = False
            return {
                "status": "failed", "issues": [message],
                "browser_backend": "steel-mcp",
            }
        except (RuntimeError, OSError, ValueError) as exc:
            message = str(exc)[:500]
            self._browser_event(
                task_id, f"Steel MCP session failed: {message[:350]}",
                url=url, status="failed", action="steel_mcp_failed", plan_step_id=step_id,
            )
            state["last_error"] = message
            state["active"] = False
            raise
        session_id = str(result.get("session_id") or "")
        if session_id:
            expose_live_session(session_id)
        snapshots = result.get("snapshot") or {}
        content = snapshots.get("content") or []
        captured_text = "\n".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict)
        )[:20000]
        tool_content = (result.get("tool_result") or {}).get("content") or []
        action_text = "\n".join(
            str(item.get("text") or "") for item in tool_content if isinstance(item, dict)
        )[:12000]
        combined_text = "\n".join(part for part in (action_text, captured_text) if part).strip()
        # Steel's snapshot identifies the actual live URL and document title.
        # Prefer that observed state over the task's original/previous URL.
        page_match = re.search(
            r"(https?://[^\s<>]+?)\s+—\s+(.+?)\s+\(snapshot",
            captured_text,
        )
        final_url = page_match.group(1).rstrip(".,)") if page_match else url
        page_title = page_match.group(2).strip()[:240] if page_match else ""
        evidence_payload = {
            "url": final_url, "final_url": final_url, "title": page_title,
            "text": combined_text, "headings": [],
            "evidence": [{
                "url": final_url, "title": page_title, "captured_text": combined_text[:15000],
                "capture_method": "steel_mcp_snapshot",
            }],
            "sources": [{"url": final_url, "title": page_title, "source_type": "webpage"}],
            "structured_data": {
                "browser_backend": "steel-mcp",
                "steel_tool": result.get("tool_name"),
                "steel_session_id": result.get("session_id"),
                "mission_step": {"step_id": step_id, "capability": capability},
                **(
                    {"qa_product_search": result.get("search_submission")}
                    if isinstance(result.get("search_submission"), dict) else {}
                ),
                **(
                    {"qa_cart_progress": result.get("cart_progress")}
                    if isinstance(result.get("cart_progress"), dict) else {}
                ),
            },
        }
        evidence_id = self.evidence.save(task_id, evidence_payload)
        evidence_payload["evidence"][0]["evidence_id"] = evidence_id
        self._browser_event(
            task_id, f"Steel MCP tool {result.get('tool_name')} completed; snapshot evidence captured",
            url=url, status="active", action="steel_mcp_result", plan_step_id=step_id,
        )
        return {
            "status": "completed" if combined_text else "blocked",
            "final_url": final_url, "http_status": None, "title": page_title,
            "headings": [], "text": combined_text,
            "evidence": evidence_payload["evidence"],
            "sources": evidence_payload["sources"],
            "structured_data": evidence_payload["structured_data"],
            "evidence_id": evidence_id,
            "issues": [] if combined_text else ["Steel MCP returned no snapshot text to verify this step."],
            "browser_backend": "steel-mcp",
            "steel_tool": result.get("tool_name"),
            "steel_session_id": result.get("session_id"),
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
            from nightwalker.workflows import classify_workflow, qa_requires_site_crawl
            workflow_type = classify_workflow(objective).value
            single_page_qa = workflow_type == "qa" and not qa_requires_site_crawl(objective)
            if workflow_type in {"qa", "seo_qa"} and id(page) not in self._qa_listener_pages:
                runtime_events = self._task_sessions[task_id].setdefault("qa_runtime_events", [])

                def record_qa_event(
                    kind: str, message: str, event_url: str | None = None,
                    details: dict[str, Any] | None = None,
                ) -> None:
                    runtime_events.append({
                        "kind": kind, "message": str(message)[:1000],
                        "url": event_url, "details": details or {},
                        "captured_at": datetime.now(UTC).isoformat(),
                    })
                    if len(runtime_events) > 500:
                        del runtime_events[:-500]

                page.on("console", lambda message: (
                    record_qa_event(
                        "console_error", message.text, page.url,
                        {"location": message.location},
                    )
                    if message.type == "error" and not message.text.startswith("[Recorder]") else None
                ))
                page.on("pageerror", lambda error: record_qa_event(
                    "page_error", str(error), page.url,
                    {"stack": str(getattr(error, "stack", "") or "")[:3000]},
                ))
                page.on("requestfailed", lambda request: record_qa_event(
                    "request_failed", f"{request.method} {request.url}: {request.failure}",
                    request.url, {"method": request.method, "failure": str(request.failure or ""),
                                  "resource_type": request.resource_type},
                ))
                self._qa_listener_pages.add(id(page))
            # Secondary pages are reviewed by the AI janitor in Orchestrator;
            # this runtime only enforces the hard cap and protects the primary page.
            # Keep the same live page between sequential mission steps. Re-navigating
            # on every round hides the user's view of the journey, resets page state,
            # and can make an extraction step look like a fresh page-open action.
            # Navigate only when there is no usable page at the requested URL.
            response = None
            current_url = str(getattr(page, "url", "") or "")
            needs_navigation = (
                not current_url
                or current_url == "about:blank"
                or current_url.rstrip("/") != str(url).rstrip("/")
            )
            if needs_navigation:
                self._browser_event(
                    task_id, "Opening the planned target page",
                    url=url, action="navigate", plan_step_id=str(steps[0].get("step_id") or ""),
                )
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
                )
            else:
                self._browser_event(
                    task_id, "Reusing the live page for the next authorised plan step",
                    url=page.url, action="reuse_page", plan_step_id=str(steps[0].get("step_id") or ""),
                )
            http_status = response.status if response else None
            product_search_requested = self._extract_product_search_query(objective) is not None
            has_authorized_search_interaction = any(
                str(step.get("capability") or "").strip().lower() == "interact"
                for step in steps
            )
            # A plan-level interaction authorises the action, but the action
            # must execute at that interaction step, not before the plan loop.
            # Product-search QA spans multiple mission-plan rounds. Keep the
            # captured result in task-scoped state so extraction/validation rounds
            # do not lose the result when this local variable is re-created.
            requested_query = self._extract_product_search_query(objective)
            qa_product_search = self._restore_qa_product_search(
                self._task_sessions[task_id], requested_query,
            )
            if workflow_type == "qa" and product_search_requested and not has_authorized_search_interaction and qa_product_search is None:
                self._browser_event(
                    task_id, "QA product search skipped: plan contains no authorised interact step",
                    url=page.url,
                )
            navigation_objective = next((
                str(step.get("objective") or "").strip()
                for step in steps
                if step.get("capability") == "navigate" and str(step.get("objective") or "").strip()
            ), "")
            self._browser_event(
                task_id,
                f"Completed navigation: {navigation_objective}" if navigation_objective
                else "Navigated to target page",
                url=page.url, title=(await page.title())[:500],
                action="plan_step_completed",
            )
            visited = [page.url]
            issues: list[str] = []
            if response and response.status >= 400:
                initial_status = response.status
                initial_url = page.url
                if workflow_type == "qa" and product_search_requested:
                    # Record a failed deep link, then recover only to the exact
                    # same-origin root and discover the catalogue from real nav.
                    from urllib.parse import urljoin
                    site_root = urljoin(initial_url, "/")
                    self._browser_event(
                        task_id,
                        f"Supplied URL returned HTTP {initial_status}; recording the failure and checking the same-origin site root for navigation routes",
                        url=initial_url,
                    )
                    root_response = await page.goto(
                        site_root,
                        wait_until="domcontentloaded",
                        timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
                    )
                    visited.append(page.url)
                    if root_response and root_response.status < 400:
                        issues.append(f"Supplied URL failed with HTTP {initial_status}: {initial_url}. Continued from the same-origin root to discover a valid catalogue route.")
                        http_status = root_response.status
                        self._browser_event(
                            task_id, "Recovered to same-origin site root after recording the supplied URL failure",
                            url=page.url, title=(await page.title())[:500],
                        )
                    else:
                        return {
                            "status": "failed",
                            "issues": [
                                f"Supplied URL returned HTTP {initial_status}: {initial_url}",
                                f"Same-origin root recovery also failed with HTTP {root_response.status if root_response else 'unknown'}",
                            ],
                            "final_url": page.url,
                        }
                else:
                    return {
                        "status": "failed",
                        "issues": [f"Website returned HTTP {response.status}"],
                        "final_url": page.url,
                    }
            for step in steps:
                capability = step.get("capability")
                step_objective = str(step.get("objective", ""))[:500]
                if capability == "navigate":
                    continue
                step_id = str(step.get("step_id") or "")
                # Steel SESSION ACTIVITY is the operator-facing action feed.
                # Show the actual model-authored instruction, not an opaque step ID.
                if not step_id.endswith(("qa-discover", "qa-availability", "qa-validate", "qa-content", "qa-synthesise")):
                    self._browser_event(
                        task_id,
                        f"Next action ({capability or 'execute'}): {step_objective or 'Follow the planned step'}",
                        url=page.url, action="plan_step_start", plan_step_id=step_id or None,
                    )
                if capability == "interact":
                    if workflow_type == "qa" and product_search_requested and qa_product_search is None:
                        self._browser_event(
                            task_id, "Executing requested product search at authorised interaction step",
                            url=page.url,
                        )
                        search_result = await self._run_product_search_smoke(page, objective, task_id)
                        destination = None
                    else:
                        search_result, destination = await self._execute_interaction(
                            page, step_objective, workflow_type, qa_product_search, task_id,
                        )
                    if search_result is not None:
                        qa_product_search = copy.deepcopy(search_result)
                        self._task_sessions[task_id]["qa_product_search"] = copy.deepcopy(search_result)
                        self._task_sessions[task_id]["qa_product_search_query"] = str(search_result.get("query") or "")
                        if search_result.get("performed"):
                            self._browser_event(
                                task_id, f"Submitted requested product search: {search_result.get('query', '')}",
                                url=search_result.get("searched_url") or page.url,
                                title=(await page.title())[:500],
                            )
                        else:
                            issues.append(
                                f"Product search interaction was not confirmed: {search_result.get('status')}"
                            )
                    if destination:
                        visited.append(destination)
                        self._browser_event(task_id, "Followed a relevant same-site link", url=page.url, title=(await page.title())[:500])
                    elif search_result is None:
                        issues.append(
                            f"No sufficiently relevant same-site link found for: {step_objective}"
                        )
                elif capability == "recover":
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "45000")),
                    )
                    visited.append(page.url)
                    self._browser_event(task_id, "Reloaded the current page for recovery", url=page.url, title=(await page.title())[:500])
                elif capability == "extract":
                    step_id = str(step.get("step_id") or "")
                    continue

            title = (await page.title())[:500]
            self._browser_event(task_id, "Captured page title, headings and visible text", url=page.url, title=title)
            # Use the final navigated origin so a legitimate www/non-www redirect
            # does not cause every discovered page to be excluded.
            target_parts = urlparse(page.url)
            target_origin = (target_parts.scheme, target_parts.hostname, target_parts.port)
            candidate_links = [] if single_page_qa else await page.locator("a[href]").evaluate_all(
                """nodes => nodes.slice(0, 2000).map(a => ({
                    label: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 240),
                    url: a.href,
                    context: a.closest('main, article') ? 'content' :
                        a.closest('nav, header') ? 'navigation' :
                        a.closest('footer') ? 'footer' : 'body'
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
                        "discovery_context": str(item.get("context", "body")),
                    })
                if len(candidates) >= 200:
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
            from nightwalker.audit_analysis import content_fingerprint
            from nightwalker.page_signals import (
                collect_internal_link_checks,
                collect_page_signals,
                settle_page_images,
                collect_qa_extended_signals,
                page_signal_findings,
                qa_extended_findings,
            )
            if workflow_type in {"qa", "seo_qa"}:
                # Scroll to trigger lazy-loaded assets, then wait within a bounded
                # window. The final snapshot distinguishes broken, pending and lazy images.
                audit_signals_image_capture = await settle_page_images(
                    page, timeout_ms=int(os.getenv("QA_IMAGE_SETTLE_TIMEOUT_MS", "8000"))
                )
            else:
                audit_signals_image_capture = None
            audit_signals = await collect_page_signals(page)
            if audit_signals_image_capture is not None:
                audit_signals.setdefault("seo", {})["image_capture"] = audit_signals_image_capture
            x_robots_tag = (response.headers.get("x-robots-tag") if response else None)
            audit_signals.setdefault("seo", {})["x_robots_tag"] = x_robots_tag
            if x_robots_tag and re.search(r"(?i)\bnoindex\b", x_robots_tag):
                audit_signals["seo"]["noindex"] = True
            audit_signals["internal_link_checks"] = (
                await collect_internal_link_checks(page, limit=40, policy=self.policy)
                if not single_page_qa else {"checks": [], "checked_count": 0, "broken_count": 0, "unknown_count": 0, "scope": "not_requested_single_page_qa"}
            )
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
                audit_signals["qa_extended"] = (
                    {} if single_page_qa else await collect_qa_extended_signals(page)
                )
                audit_signals["qa_navigation_journeys"] = (
                    await self._run_readonly_navigation_smoke(page, limit=5)
                    if not single_page_qa else {"checks": [], "checked_count": 0, "scope": "not_requested_single_page_qa"}
                )
                audit_signals["qa_interaction_journeys"] = (
                    await self._run_safe_interaction_smoke(page, limit=5)
                    if not single_page_qa else {"checks": [], "checked_count": 0, "scope": "not_requested_single_page_qa"}
                )
                audit_signals["qa_runtime_events"] = list(
                    self._task_sessions[task_id].get("qa_runtime_events", [])
                )
                self._task_sessions[task_id]["qa_runtime_events"] = []
                if single_page_qa:
                    page_seo = audit_signals.get("seo") or {}
                    runtime_events = audit_signals["qa_runtime_events"]
                    audit_signals["qa_page_smoke"] = {
                        "url": page.url,
                        "document_title": page_seo.get("title"),
                        "h1_count": page_seo.get("h1_count"),
                        "h1_headings": page_seo.get("h1_headings", []),
                        "image_count": page_seo.get("image_count"),
                        "images": page_seo.get("images", []),
                        "broken_image_count": page_seo.get("broken_image_count"),
                        "pending_image_count": sum(item.get("load_status") == "pending" for item in page_seo.get("images", [])),
                        "console_error_count": sum(item.get("kind") == "console_error" for item in runtime_events),
                        "page_error_count": sum(item.get("kind") == "page_error" for item in runtime_events),
                        "request_failed_count": sum(item.get("kind") == "request_failed" for item in runtime_events),
                        "runtime_events": runtime_events,
                    }
                    if audit_signals["qa_page_smoke"]["pending_image_count"]:
                        issues.append(
                            f"{audit_signals['qa_page_smoke']['pending_image_count']} image(s) were still loading at capture time; "
                            "they are marked pending and are not counted as confirmed broken images."
                        )
            # A named product search is a focused user journey, not a general QA audit.
            # Suppress unrelated homepage DOM, accessibility, metadata and content findings.
            focused_product_search = (
                workflow_type == "qa"
                and self._extract_product_search_query(objective) is not None
            )
            audit_findings = (
                [] if focused_product_search
                else page_signal_findings(audit_signals, workflow_type)
            )
            for check in ([] if focused_product_search else audit_signals["internal_link_checks"].get("checks", [])):
                if check.get("off_origin_redirect"):
                    audit_findings.append({
                        "claim": f"Internal link redirects outside the audited origin: {check.get('url')} → {check.get('final_url')}",
                        "type": "off_origin_internal_redirect", "severity": "medium",
                        "affected_urls": [check.get("url")], "source_url": page.url,
                    })
                elif check.get("ok") is False:
                    audit_findings.append({
                        "claim": f"Internal link destination returned HTTP {check.get('status')}: {check.get('url')}",
                        "type": "broken_internal_link", "severity": "high",
                        "affected_urls": [check.get("url")], "source_url": page.url,
                    })
            for journey in ([] if focused_product_search else (audit_signals.get("qa_navigation_journeys") or {}).get("checks", [])[:20]):
                if journey.get("ok") is False:
                    audit_findings.append({
                        "claim": f"Same-origin navigation journey failed for {journey.get('url')}: "
                                 f"{journey.get('status') or journey.get('error') or 'off-origin redirect'}",
                        "type": "qa_navigation_failure", "severity": "high",
                        "affected_urls": [journey.get("url")],
                    })
            for interaction in ([] if focused_product_search else (audit_signals.get("qa_interaction_journeys") or {}).get("checks", [])[:10]):
                if interaction.get("ok") is False:
                    audit_findings.append({
                        "claim": f"Reversible disclosure interaction did not complete and restore cleanly: {interaction.get('label') or 'unnamed control'}",
                        "type": "qa_interaction_issue", "severity": "low",
                        "affected_urls": [page.url],
                    })
            for event in ([] if focused_product_search else (audit_signals.get("qa_runtime_events") or [])[:20]):
                if event.get("kind") in {"console_error", "page_error"}:
                    # Runtime errors are diagnostic observations, not automatically
                    # user-facing defects. Keep them visible for investigation, but do
                    # not let a console/page exception alone validate a QA failure.
                    audit_findings.append({
                        "claim": f"Runtime observation ({event.get('kind')}): {event.get('message', '')[:240]}",
                        "type": "qa_runtime_observation", "severity": "info",
                        "affected_urls": [event.get("url") or page.url],
                    })
            if workflow_type in {"qa", "seo_qa"} and not focused_product_search:
                audit_findings.extend(qa_extended_findings(
                    audit_signals.get("qa_extended") or {}, page.url
                ))
            wants_products = bool(re.search(r"(?i)\b(product|products|item|items|price|pricing)\b", objective)) and not focused_product_search
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
                except Exception:  # noqa: BLE001 S110 - card product extraction is best-effort; silent skip is intentional
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
            if title and not focused_product_search:
                findings.append({
                    "claim": f"Page title: {title}",
                    "type": "page_metadata",
                    "confidence": "observed",
                })
            if not focused_product_search:
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
            if not focused_product_search:
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
            if focused_product_search:
                # The product-search result records below are the only relevant data.
                structured_data = {}
            if workflow_type in {"seo", "seo_qa"}:
                structured_data["seo_checks"] = audit_signals.get("seo", {})
                discovery_signals = audit_signals.get("seo_discovery", {})
                if not discovery_signals.get("cache_hit"):
                    structured_data["seo_discovery"] = discovery_signals
            if workflow_type in {"seo", "qa", "seo_qa"}:
                from nightwalker.crawl_intelligence import classify_page_url, template_key_for_url
                structured_data["crawl_page"] = {
                    "url": page.url, "title": title,
                    "page_type": classify_page_url(page.url),
                    "template_key": template_key_for_url(page.url),
                    "canonical_url": (audit_signals.get("seo") or {}).get("canonical_url"),
                    "meta_description": (audit_signals.get("seo") or {}).get("meta_description"),
                    "content_fingerprint": content_fingerprint(body_text),
                    "http_status": http_status,
                }
                structured_data["internal_link_checks"] = audit_signals.get("internal_link_checks", {})
            if workflow_type in {"qa", "seo_qa"}:
                # Preserve the page-level DOM/image/title signals used to validate QA findings,
                # including site-wide QA pages that do not use the single-page smoke contract.
                structured_data["qa_page_signals"] = audit_signals.get("seo", {})
                structured_data["qa_checks"] = audit_signals.get("qa", {})
                if audit_signals.get("qa_page_smoke"):
                    structured_data["qa_page_smoke"] = audit_signals["qa_page_smoke"]
                if qa_product_search is not None:
                    structured_data["qa_product_search"] = qa_product_search
                    structured_data["qa_product_evidence"] = self._qa_product_evidence(
                        qa_product_search, qa_product_search.get("searched_url") or page.url,
                    )
                structured_data["qa_extended"] = audit_signals.get("qa_extended", {})
                structured_data["qa_navigation_journeys"] = audit_signals.get("qa_navigation_journeys", {})
                structured_data["qa_interaction_journeys"] = audit_signals.get("qa_interaction_journeys", {})
                structured_data["qa_runtime_events"] = audit_signals.get("qa_runtime_events", [])
                structured_data["internal_link_checks"] = audit_signals.get("internal_link_checks", {})
            limitations = [] if focused_product_search else list(issues)
            if qa_product_search is not None:
                if not qa_product_search.get("performed"):
                    limitations.append(
                        f"Product search for {qa_product_search.get('query')!r} was not performed: "
                        f"{qa_product_search.get('status')}. The requested product result remains unverified."
                    )
                elif not qa_product_search.get("matches"):
                    limitations.append(
                        f"The search for {qa_product_search.get('query')!r} was executed, but no matching "
                        "product card was captured. Product presence, image and price could not be verified."
                    )
                else:
                    for product_match in qa_product_search.get("matches", []):
                        if not product_match.get("image_present"):
                            limitations.append(
                                f"Matching product {product_match.get('name_match')!r} has no image element in its captured card."
                            )
                        elif not product_match.get("image_loaded"):
                            limitations.append(
                                f"The image for matching product {product_match.get('name_match')!r} was not confirmed loaded."
                            )
                        if not product_match.get("price"):
                            limitations.append(
                                f"No displayed price could be extracted from the matching product card for {product_match.get('name_match')!r}."
                            )
            if workflow_type in {"seo", "seo_qa"}:
                limitations.append("Crawl coverage is bounded by the configured round/page budget and discovered same-origin links/sitemap entries; inaccessible, JavaScript-only, orphaned and undiscovered URLs may be absent. Core Web Vitals are not measured.")
            if workflow_type in {"qa", "seo_qa"} and not focused_product_search:
                limitations.append("QA checks are read-only: forms are not submitted, state-changing controls are not activated, and keyboard focus is sampled rather than exhaustively tested. Automated WCAG conformance is not claimed.")
            if not body_text.strip():
                limitations.append("No visible page text was available to analyse.")
            if focused_product_search and qa_product_search:
                product_evidence = self._qa_product_evidence(
                    qa_product_search, qa_product_search.get("searched_url") or page.url,
                )
                if product_evidence:
                    # Prefer an exact phrase match, then a result with all requested
                    # fields present; preserve the remaining matches as alternatives.
                    product_evidence.sort(key=lambda item: (
                        0 if next((m.get("query_match") for m in qa_product_search.get("matches", [])
                                   if str(m.get("name_match") or m.get("product_title") or "") == item["product_name"]), "") == "phrase" else 1,
                        0 if item.get("price") and item.get("image_url") and item.get("image_status") == "loaded" else 1,
                    ))
                    first_product = product_evidence[0]
                    answer = (
                        f"Found {len(product_evidence)} matching product card(s). "
                        f"{first_product['product_name'] or 'Product'}"
                        + (f" — {first_product['price']}" if first_product.get("price") else " — price not captured")
                        + (f". Image: {first_product['image_url']}" if first_product.get("image_url") else ". Image URL not captured")
                        + (f". Product page: {first_product['product_url']}" if first_product.get("product_url") else "")
                    )
                else:
                    answer = (
                        f"Product search for {qa_product_search.get('query')!r} "
                        f"was {qa_product_search.get('status')}; no matching product card was captured."
                    )
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
                "qa_product_search": qa_product_search,
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
                "status": "completed" if body_text.strip() and http_status is not None and http_status < 400 else "blocked",
                **report,
                "final_url": page.url,
                "http_status": http_status,
                "title": title,
                "headings": headings,
                "text": body_text,
                "products": products,
                "qa_product_search": qa_product_search,
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
