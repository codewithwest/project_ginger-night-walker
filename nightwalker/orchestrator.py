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
from nightwalker.decision_contracts import DecisionPlan
from nightwalker.ollama_client import OllamaClient, OllamaUnavailable


class Orchestrator:
    def __init__(self, browser: BrowserRuntime, ollama: OllamaClient | None = None) -> None:
        self.browser = browser
        self.ollama = ollama or OllamaClient()

    async def run_agent_task(self, task: AgentTask) -> AgentResult:
        return await self.ollama.run_agent(task)

    async def run_browser_workflow(self, task_id: str, objective: str, plan: DecisionPlan) -> dict:
        if not plan.target_url:
            raise ValueError("The Decider plan has no target URL")
        steps = [step.model_dump(mode="json") for step in plan.steps]
        try:
            result = await self.browser.run_plan(
                task_id=task_id,
                url=str(plan.target_url),
                steps=steps,
                objective=objective,
            )
            # AI classifies only secondary tabs. The browser runtime protects the
            # primary page and enforces the hard tab cap regardless of model output.
            tabs = await self.browser.tab_inventory(task_id)
            if len(tabs) > 1:
                try:
                    proposal = await self.ollama.cleanup_browser_tabs(
                        objective, tabs, task_id=task_id
                    )
                    cleanup = await self.browser.cleanup_task_tabs(
                        task_id, proposal["close_indices"], proposal.get("reason", "AI recommendation")
                    )
                    cleanup["mode"] = "ai"
                except (OllamaUnavailable, RuntimeError, ValueError, TypeError, KeyError) as cleanup_error:
                    cleanup = await self.browser.cleanup_task_tabs(
                        task_id, list(range(1, len(tabs))),
                        "Safe fallback: AI cleanup unavailable (" + str(cleanup_error)[:160] + ")",
                    )
                    cleanup["mode"] = "fallback"
                result["tab_cleanup"] = cleanup
            return result
        except PermissionError as exc:
            return {"status": "blocked", "issues": [str(exc)[:500]]}
        except (RuntimeError, PlaywrightError, OSError, ValueError) as exc:
            return {"status": "failed", "issues": [str(exc)[:500]]}

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
                evidence_id=data.get("evidence_id"),
            )
        except PermissionError as exc:
            return BrowserExtractResponse(
                task_id=task_id,
                status="blocked",
                url=str(request.url),
                issues=[str(exc)[:500]],
            )
        except (RuntimeError, PlaywrightError, OSError, ValueError) as exc:
            return BrowserExtractResponse(
                task_id=task_id,
                status="failed",
                url=str(request.url),
                issues=[str(exc)[:500]],
            )
