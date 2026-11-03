from __future__ import annotations

import asyncio
import os
from playwright.async_api import async_playwright, Browser, Page
from nightwalker.policy import PolicyEngine, DEFAULT_MANIFESTS


class BrowserRuntime:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self.policy = PolicyEngine()

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def extract_basic(self, url: str) -> dict:
        manifest = DEFAULT_MANIFESTS["navigation"]
        self.policy.authorize(manifest, "browser.navigate")
        self.policy.validate_url(
            url, allow_private_networks=os.getenv("ALLOW_PRIVATE_NETWORKS", "false").lower() == "true"
        )
        if not self._page:
            raise RuntimeError("Browser runtime is not started")
        async with self._lock:
            response = await self._page.goto(
                url, wait_until="domcontentloaded",
                timeout=int(os.getenv("NAVIGATION_TIMEOUT_MS", "20000"))
            )
            title = await self._page.title()
            headings = await self._page.locator("h1").all_text_contents()
            return {
                "url": self._page.url,
                "status_code": response.status if response else None,
                "title": title[:500],
                "headings": [h.strip()[:500] for h in headings[:20] if h.strip()],
            }
