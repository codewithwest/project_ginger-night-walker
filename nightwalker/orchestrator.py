from __future__ import annotations

from uuid import uuid4
from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.contracts import BrowserExtractRequest, BrowserExtractResponse


class Orchestrator:
    def __init__(self, browser: BrowserRuntime) -> None:
        self.browser = browser

    async def run_browser_extract(self, request: BrowserExtractRequest) -> BrowserExtractResponse:
        task_id = str(uuid4())
        try:
            data = await self.browser.extract_basic(str(request.url))
            return BrowserExtractResponse(
                task_id=task_id, status="completed", url=data["url"],
                title=data["title"], headings=data["headings"]
            )
        except Exception as exc:
            return BrowserExtractResponse(
                task_id=task_id, status="blocked" if isinstance(exc, PermissionError) else "failed",
                url=str(request.url), issues=[str(exc)[:500]]
            )
