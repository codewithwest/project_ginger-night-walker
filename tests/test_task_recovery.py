import asyncio

import pytest

from nightwalker.scratchpad import TaskScratchpad
from nightwalker.task_manager import TaskManager
from nightwalker.task_state_store import TaskStateStore


class NoExecutionOrchestrator:
    ollama = object()

    async def run_browser_workflow(self, *args, **kwargs):
        raise AssertionError("A completed checkpoint must not restart browser work")


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
