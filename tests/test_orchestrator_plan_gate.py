import pytest

from nightwalker.orchestrator import Orchestrator
from nightwalker.decision_contracts import normalise_decision_plan


class FakeBrowser:
    def __init__(self):
        self.calls = []

    async def tab_inventory(self, task_id):
        return []

    async def run_plan(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "completed", "final_url": kwargs["url"]}

    async def close_task_session(self, task_id):
        return None


def browser_plan(step_id="home", capability="navigate", objective="Open the homepage"):
    return normalise_decision_plan({
        "status": "ready",
        "summary": "Execute one authorised mission step",
        "target_url": "https://example.com/",
        "steps": [{
            "step_id": step_id,
            "capability": capability,
            "objective": objective,
        }],
    })


@pytest.mark.asyncio
async def test_browser_workflow_rejects_missing_active_plan_step():
    browser = FakeBrowser()
    orchestrator = Orchestrator(browser, ollama=object())

    with pytest.raises(PermissionError, match="No active mission plan step"):
        await orchestrator.run_browser_workflow(
            "task-1", "Open example.com", browser_plan()
        )
    assert browser.calls == []


@pytest.mark.asyncio
async def test_browser_workflow_rejects_decider_step_that_differs_from_authorised_plan():
    browser = FakeBrowser()
    orchestrator = Orchestrator(browser, ollama=object())
    authorised = {
        "step_id": "home",
        "capability": "navigate",
        "objective": "Open the homepage",
        "status": "in_progress",
    }

    with pytest.raises(PermissionError, match="do not match"):
        await orchestrator.run_browser_workflow(
            "task-1",
            "Open example.com",
            browser_plan(step_id="extract", capability="extract", objective="Extract products"),
            authorized_step=authorised,
        )
    assert browser.calls == []


@pytest.mark.asyncio
async def test_browser_workflow_executes_only_the_authorised_plan_step():
    browser = FakeBrowser()
    orchestrator = Orchestrator(browser, ollama=object())
    authorised = {
        "step_id": "home",
        "capability": "navigate",
        "objective": "Open the homepage",
        "status": "in_progress",
    }

    result = await orchestrator.run_browser_workflow(
        "task-1",
        "Open example.com",
        browser_plan(),
        authorized_step=authorised,
    )

    assert result["status"] == "completed"
    assert result["authorised_plan_step"]["step_id"] == "home"
    assert len(browser.calls) == 1
    assert browser.calls[0]["steps"] == [{
        "step_id": "home",
        "capability": "navigate",
        "objective": "Open the homepage",
    }]


@pytest.mark.asyncio
async def test_browser_workflow_routes_authorised_step_through_steel_mcp(monkeypatch):
    monkeypatch.setenv("STEEL_MCP_MISSION_ENABLED", "true")
    browser = FakeBrowser()

    class FakeMCP:
        connected = True

    browser.steel_mcp = FakeMCP()
    captured = {}

    async def run_mcp(task_id, url, step, objective, ollama):
        captured.update({
            "task_id": task_id, "url": url, "step": step,
            "objective": objective, "ollama": ollama,
        })
        return {"status": "completed", "evidence_id": "ev-test", "browser_backend": "steel-mcp"}

    browser.run_steel_mcp_step = run_mcp
    ollama = object()
    orchestrator = Orchestrator(browser, ollama=ollama)
    authorised = {
        "step_id": "home", "capability": "navigate",
        "objective": "Open the homepage", "status": "in_progress",
    }

    result = await orchestrator.run_browser_workflow(
        "task-1", "Open example.com", browser_plan(), authorized_step=authorised,
    )

    assert result["status"] == "completed"
    assert result["browser_backend"] == "steel-mcp"
    assert captured["step"]["step_id"] == "home"
    assert captured["ollama"] is ollama
    assert browser.calls == []
