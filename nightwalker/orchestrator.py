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

    async def _cleanup_browser_tabs(self, task_id: str, objective: str) -> dict | None:
        tabs = await self.browser.tab_inventory(task_id)
        if len(tabs) <= 1:
            return None
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
        return cleanup

    async def run_browser_workflow(
        self,
        task_id: str,
        objective: str,
        plan: DecisionPlan,
        *,
        authorized_step: dict | None = None,
    ) -> dict:
        if not plan.target_url:
            raise ValueError("The Decider plan has no target URL")
        if not isinstance(authorized_step, dict):
            raise PermissionError("No active mission plan step authorises browser execution")
        if authorized_step.get("status") != "in_progress":
            raise PermissionError("Browser execution requires an in-progress mission plan step")
        step_id = str(authorized_step.get("step_id") or "")
        capability = str(authorized_step.get("capability") or "")
        step_objective = str(authorized_step.get("objective") or "").strip()
        if not step_id or not step_objective or capability not in {"navigate", "interact", "extract", "recover"}:
            raise PermissionError("The active mission plan step is invalid or requests an unsupported capability")
        steps = [step.model_dump(mode="json") for step in plan.steps]
        if len(steps) != 1:
            raise PermissionError("A browser run must be scoped to exactly one active mission plan step")
        requested = steps[0]
        if (
            str(requested.get("step_id") or "") != step_id
            or str(requested.get("capability") or "") != capability
            or str(requested.get("objective") or "").strip() != step_objective
        ):
            raise PermissionError("Browser instructions do not match the active mission plan step")
        try:
            before_cleanup = await self._cleanup_browser_tabs(task_id, objective)
            result = await self.browser.run_plan(
                task_id=task_id,
                url=str(plan.target_url),
                steps=steps,
                objective=objective,
            )
            result["authorised_plan_step"] = {
                "step_id": step_id,
                "capability": capability,
                "objective": step_objective,
            }
            after_cleanup = await self._cleanup_browser_tabs(task_id, objective)
            cleanup = after_cleanup or before_cleanup
            if cleanup:
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
