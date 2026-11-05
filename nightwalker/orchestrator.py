from __future__ import annotations

from uuid import uuid4

from playwright.async_api import Error as PlaywrightError

from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.contracts import (
    AgentResult,
    AgentTask,
    BrowserExtractRequest,
    BrowserExtractResponse,
)
from nightwalker.ollama_client import OllamaClient


class Orchestrator:
    def __init__(self, browser: BrowserRuntime, ollama: OllamaClient | None = None) -> None:
        self.browser = browser
        self.ollama = ollama or OllamaClient()

    async def run_agent_task(self, task: AgentTask) -> AgentResult:
        return await self.ollama.run_agent(task)

    async def run_browser_extract(self, request: BrowserExtractRequest) -> BrowserExtractResponse:
        task_id = str(uuid4())
        try:
            data = await self.browser.extract_basic(str(request.url))
            return BrowserExtractResponse(
                task_id=task_id,
                status="completed",
                url=data["url"],
                title=data["title"],
                headings=data["headings"],
            )
        except PermissionError as exc:
            return BrowserExtractResponse(
                task_id=task_id,
                status="blocked",
                url=str(request.url),
                issues=[str(exc)[:500]],
            )
        except (RuntimeError, PlaywrightError, OSError) as exc:
            return BrowserExtractResponse(
                task_id=task_id,
                status="failed",
                url=str(request.url),
                issues=[str(exc)[:500]],
            )
