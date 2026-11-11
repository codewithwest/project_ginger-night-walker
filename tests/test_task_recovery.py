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



def test_loaded_legacy_cart_task_is_migrated_before_snapshot_or_retry(tmp_path):
    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "evidence"))
    manager.tasks["legacy-cart"] = {
        "task_id": "legacy-cart",
        "objective": (
            'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart, '
            "verify price and quantity, remove it, and confirm the cart is empty. Do not checkout."
        ),
        "status": "failed",
        "execution_blueprint": {
            "version": 1,
            "subtasks": [
                {"subtask_id": "qa-navigation"},
                {"subtask_id": "qa-availability"},
                {"subtask_id": "qa-content"},
                {"subtask_id": "qa-validate"},
                {"subtask_id": "qa-crawl"},
                {"subtask_id": "qa-synthesise"},
            ],
        },
        "execution_plan": {
            "steps": [
                {
                    "step_id": "qa-navigation",
                    "status": "completed",
                    "evidence_refs": ["ev-storefront"],
                    "result": "Storefront loaded",
                },
                {"step_id": "qa-availability", "status": "blocked", "evidence_refs": []},
            ],
            "history": [],
        },
        "result": {"evidence": [{"evidence_id": "ev-storefront"}]},
        "progress_events": [],
    }

    record = manager.tasks["legacy-cart"]
    assert manager._migrate_legacy_cart_plan(record) is True
    ids = [item["subtask_id"] for item in record["execution_blueprint"]["subtasks"]]
    assert ids == [
        "qa-navigation", "qa-discover", "qa-availability",
        "qa-content", "qa-validate", "qa-synthesise",
    ]
    steps = record["execution_plan"]["steps"]
    assert steps[0]["status"] == "completed"
    assert steps[0]["evidence_refs"] == ["ev-storefront"]
    assert steps[1]["step_id"] == "qa-discover"
    assert steps[1]["status"] == "pending"
    assert record["active_plan_step_id"] is None
    assert record["workflow_type"] == "qa"


def test_cart_plan_with_canonical_ids_but_stale_capabilities_is_migrated(tmp_path):
    from nightwalker.workflows import build_cart_journey_blueprint

    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "evidence"))
    objective = (
        'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart, '
        "verify price and quantity, remove it, and confirm the cart is empty. Do not checkout."
    )
    canonical = build_cart_journey_blueprint(objective).model_dump(mode="json")
    stale = {
        **canonical,
        "subtasks": [
            {**item, "capability": "navigate"} if item["subtask_id"] == "qa-discover" else item
            for item in canonical["subtasks"]
        ],
    }
    manager.tasks["stale-semantics"] = {
        "task_id": "stale-semantics",
        "objective": objective,
        "status": "failed",
        "execution_blueprint": stale,
        "execution_plan": {
            "steps": [
                {"step_id": "qa-navigation", "status": "completed",
                 "evidence_refs": ["ev-storefront"], "result": "Storefront loaded"},
                {"step_id": "qa-discover", "status": "completed",
                 "evidence_refs": ["ev-storefront"], "result": "Captured homepage"},
            ],
            "history": [],
        },
        "result": {"evidence": [{"evidence_id": "ev-storefront"}]},
        "progress_events": [],
    }

    record = manager.tasks["stale-semantics"]
    assert manager._migrate_legacy_cart_plan(record) is True
    steps = record["execution_plan"]["steps"]
    assert steps[0]["status"] == "completed"
    assert steps[0]["evidence_refs"] == ["ev-storefront"]
    assert steps[1]["step_id"] == "qa-discover"
    assert steps[1]["capability"] == "interact"
    assert steps[1]["status"] == "pending"


def test_cart_task_migrates_stale_execution_ledger_even_when_blueprint_is_current(tmp_path):
    from nightwalker.plan_execution import initialise_plan
    from nightwalker.workflows import build_cart_journey_blueprint

    manager = TaskManager(NoExecutionOrchestrator())
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.scratchpad = TaskScratchpad(str(tmp_path / "evidence"))
    objective = (
        'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart, '
        "verify price and quantity, remove it, and confirm the cart is empty. Do not checkout."
    )
    blueprint = build_cart_journey_blueprint(objective).model_dump(mode="json")
    stale_plan = initialise_plan(blueprint)
    stale_plan["steps"] = stale_plan["steps"][:-1]
    manager.tasks["stale-ledger"] = {
        "task_id": "stale-ledger",
        "objective": objective,
        "workflow_type": "qa",
        "status": "failed",
        "execution_blueprint": blueprint,
        "execution_plan": stale_plan,
        "progress_events": [],
    }

    assert manager._migrate_legacy_cart_plan(manager.tasks["stale-ledger"]) is True
    steps = manager.tasks["stale-ledger"]["execution_plan"]["steps"]
    assert len(steps) == 6
    assert steps[1]["step_id"] == "qa-discover"
    assert steps[1]["capability"] == "interact"
    assert steps[1]["status"] == "pending"


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
