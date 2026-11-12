import pytest

from nightwalker.scratchpad import TaskScratchpad
from nightwalker.task_manager import TaskManager
from nightwalker.task_state_store import TaskStateStore


class _StubBrowser:
    def task_session(self, task_id):
        return None


class _StubOllama:
    def inference_events_for_task(self, task_id):
        return []


class NoExecutionOrchestrator:
    ollama = _StubOllama()
    browser = _StubBrowser()

    async def run_browser_workflow(self, *args, **kwargs):
        raise AssertionError("A completed checkpoint must not restart browser work")


class InFlightOrchestrator:
    ollama = _StubOllama()
    browser = _StubBrowser()

    def __init__(self):
        self.calls = []

    async def run_browser_workflow(self, task_id, objective, plan, **kwargs):
        self.calls.append(str(plan.target_url))
        return {"status": "failed", "issues": ["simulated interrupted-source retry"], "candidate_urls": []}


@pytest.mark.asyncio
async def test_restart_restores_task_and_resumes_from_completed_checkpoint(tmp_path):
    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "evidence"))
    manager.state_store.save({
        "task_id": "resume-me",
        "capability": "auto",
        "objective": "Research an example topic",
        "status": "running",
        "created_at": "2026-11-05T10:00:00+00:00",
        "updated_at": "2026-11-05T10:01:00+00:00",
        "result": None,
        "error": None,
        "plan": None,
        "current_step": {"url": "https://example.com"},
        "step_results": [],
        "logs": [],
        "progress_events": [],
        "round": 2,
        "max_rounds": 30,
        "scratchpad": [],
        "follow_up_instructions": [],
        "goal_state": None,
        "checkpoint": {
            "version": 1,
            "initial_plan": {
                "status": "ready",
                "summary": "Research the topic",
                "site_name": "Example",
                "target_url": "https://example.com/",
                "clarification_question": None,
                "steps": [
                    {"step_id": "open", "capability": "navigate", "objective": "Open source"},
                    {"step_id": "extract", "capability": "extract", "objective": "Capture evidence"},
                ],
            },
            "sources": [],
            "findings": [],
            "evidence": [],
            "visited_urls": [],
            "candidates": [],
            "structured_data": {},
            "limitations": [],
            "loop_guard": {"attempts": {}, "recent_outcomes": [], "repeated_actions": [], "max_attempts_per_url": 2},
            "round_count": 2,
            "next_url": "",
            "next_assignment": "Final synthesis",
            "research_complete": True,
        },
    })

    restored = await manager.restore_and_resume()
    assert restored == 1
    await manager.running["resume-me"]

    task = manager.snapshot("resume-me")
    assert task["status"] == "blocked"
    assert any(event["event_type"] == "task_resumed" for event in task["progress_events"])
    assert task["checkpoint"]["round_count"] == 2
    assert task["checkpoint"]["research_complete"] is True


@pytest.mark.asyncio
async def test_restart_does_not_resume_terminal_or_clarification_tasks(tmp_path):
    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path))
    manager.state_store.save({"task_id": "done", "status": "completed"})
    manager.state_store.save({"task_id": "waiting", "status": "needs_clarification"})

    restored = await manager.restore_and_resume()

    assert restored == 0
    assert manager.snapshot("done")["status"] == "completed"
    assert manager.snapshot("waiting")["status"] == "needs_clarification"
    assert manager.running == {}


@pytest.mark.asyncio
async def test_restart_retries_in_flight_source_without_incrementing_round(tmp_path):
    orchestrator = InFlightOrchestrator()
    manager = TaskManager(orchestrator)
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "evidence"))
    manager.state_store.save({
        "task_id": "in-flight",
        "capability": "auto",
        "objective": "Research an example topic",
        "status": "running",
        "created_at": "2026-11-05T10:00:00+00:00",
        "updated_at": "2026-11-05T10:01:00+00:00",
        "result": None,
        "error": None,
        "plan": None,
        "current_step": None,
        "step_results": [],
        "logs": [],
        "progress_events": [],
        "round": 3,
        "max_rounds": 30,
        "scratchpad": [],
        "follow_up_instructions": [],
        "goal_state": None,
        "execution_plan": {
            "version": 1,
            "status": "pending",
            "steps": [
                {
                    "step_id": "open", "index": 1, "objective": "Open source",
                    "capability": "navigate", "depends_on": [], "success_criteria": [],
                    "expected_output": "Page evidence", "status": "pending",
                    "attempts": 0, "evidence_refs": [], "result": None,
                },
                {
                    "step_id": "extract", "index": 2, "objective": "Capture evidence",
                    "capability": "extract", "depends_on": ["open"], "success_criteria": [],
                    "expected_output": "Evidence", "status": "pending",
                    "attempts": 0, "evidence_refs": [], "result": None,
                },
            ],
            "history": [],
            "source": "decider_plan",
            "ordering": "decider_authored",
        },
        "checkpoint": {
            "version": 1,
            "initial_plan": {
                "status": "ready",
                "summary": "Research the topic",
                "site_name": "Example",
                "target_url": "https://example.com/",
                "clarification_question": None,
                "steps": [
                    {"step_id": "open", "capability": "navigate", "objective": "Open source"},
                    {"step_id": "extract", "capability": "extract", "objective": "Capture evidence"},
                ],
            },
            "sources": [],
            "findings": [],
            "evidence": [],
            "visited_urls": [],
            "candidates": [],
            "structured_data": {},
            "limitations": [],
            "loop_guard": {
                "attempts": {"https://example.com/next": 1},
                "recent_outcomes": [],
                "repeated_actions": [],
                "max_attempts_per_url": 2,
            },
            "round_count": 3,
            "next_url": "https://example.com/next",
            "next_assignment": "Inspect next source",
            "research_complete": False,
            "round_in_progress": True,
        },
    })

    assert await manager.restore_and_resume() == 1
    await manager.running["in-flight"]

    task = manager.snapshot("in-flight")
    # The failed source stays the live page; the next authored step continues on it
    # and the failed step is recorded as failed instead of being re-selected forever.
    assert orchestrator.calls == ["https://example.com/next", "https://example.com/next"]
    assert task["round"] == 4
    assert task["status"] == "blocked"
    steps = {step["step_id"]: step["status"] for step in task["execution_plan"]["steps"]}
    assert steps == {"open": "failed", "extract": "failed"}


@pytest.mark.asyncio
async def test_restart_retries_legacy_website_only_clarification(tmp_path):
    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "scratch"))
    manager.state_store.save({
        "task_id": "old-site-prompt",
        "objective": "Perform a comprehensive SEO audit of https://example.com",
        "status": "needs_clarification",
        "plan": {
            "status": "needs_clarification",
            "clarification_question": (
                "Which website or detail should I use to continue? "
                "Please provide the exact website URL if the site is ambiguous."
            ),
        },
        "logs": [],
        "progress_events": [],
        "scratchpad": [],
        "follow_up_instructions": [],
        "step_results": [],
        "checkpoint": None,
    })
    called = {}

    async def fake_execute(task_id, objective, resume=False):
        called.update(task_id=task_id, objective=objective, resume=resume)
        manager.tasks[task_id]["status"] = "completed"
        manager._persist(task_id)

    manager._execute_decided = fake_execute

    assert await manager.restore_and_resume() == 1
    await manager.running["old-site-prompt"]

    assert called["task_id"] == "old-site-prompt"
    assert called["resume"] is True
    assert manager.snapshot("old-site-prompt")["status"] == "completed"



@pytest.mark.asyncio
async def test_cart_retry_preserves_decider_authored_step_sequence(tmp_path, monkeypatch):
    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "evidence"))
    authored_plan = {
        "steps": [
            {"step_id": "open-store", "objective": "Open the requested storefront",
             "capability": "navigate", "status": "completed", "evidence_refs": ["ev-open"]},
            {"step_id": "inspect-product-options", "objective": "Inspect available product options",
             "capability": "extract", "status": "pending", "depends_on": ["open-store"]},
            {"step_id": "perform-authorised-action", "objective": "Perform the specifically authorised action",
             "capability": "interact", "status": "pending", "depends_on": ["inspect-product-options"]},
        ],
        "history": [],
    }
    manager.tasks["decider-cart"] = {
        "task_id": "decider-cart",
        "objective": (
            'Open https://example.com. Add "Example item" to the cart, '
            "verify it and remove it without checkout."
        ),
        "execution_objective": "original objective",
        "execution_plan": authored_plan,
        "workflow_type": "qa",
        "status": "blocked",
        "logs": [],
        "progress_events": [],
        "checkpoint": None,
        "retry_count": 0,
    }

    async def no_wait_retry(task_id, objective, delay_seconds):
        return None

    monkeypatch.setattr(manager, "_retry_after_backoff", no_wait_retry)
    result = manager.retry("decider-cart")
    await manager.running["decider-cart"]

    assert result is not None
    assert manager.tasks["decider-cart"]["execution_plan"] == authored_plan
    assert [step["step_id"] for step in manager.tasks["decider-cart"]["execution_plan"]["steps"]] == [
        "open-store", "inspect-product-options", "perform-authorised-action",
    ]
    assert [step["capability"] for step in manager.tasks["decider-cart"]["execution_plan"]["steps"]] == [
        "navigate", "extract", "interact",
    ]
    assert not hasattr(manager, "_migrate_legacy_cart_plan")


@pytest.mark.asyncio
async def test_delete_mission_removes_it_from_the_workspace(tmp_path):
    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path))
    manager.tasks["nw-delete"] = {
        "task_id": "nw-delete",
        "status": "blocked",
        "logs": [],
        "progress_events": [],
    }
    manager.state_store.save(manager.tasks["nw-delete"])

    assert await manager.delete_mission("nw-delete") is True
    assert "nw-delete" not in manager.tasks
    assert manager.state_store.load_all() == []
    assert await manager.delete_mission("nw-delete") is False
