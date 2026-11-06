import asyncio
from types import SimpleNamespace

import pytest

from nightwalker.task_manager import TaskManager


@pytest.mark.asyncio
async def test_retry_resumes_failed_task_from_checkpoint_without_discarding_progress():
    manager = TaskManager(SimpleNamespace())
    record = {
        "task_id": "nw-retry",
        "objective": "Research Acme",
        "execution_objective": "Research Acme",
        "status": "failed",
        "error": "Steel session unavailable",
        "round": 3,
        "result": {"sources": [{"url": "https://example.com"}]},
        "checkpoint": {
            "round_in_progress": True,
            "next_url": "https://example.com/next",
            "round_count": 3,
        },
        "logs": [],
        "progress_events": [],
        "scratchpad": [],
    }
    manager.tasks["nw-retry"] = record
    manager._persist = lambda task_id: None
    manager._log = lambda task_id, message, level="info": record["logs"].append(message)
    captured = {}

    async def fake_execute(task_id, objective, resume=False):
        captured.update(task_id=task_id, objective=objective, resume=resume)

    manager._execute_decided = fake_execute

    retried = manager.retry("nw-retry")
    await manager.running["nw-retry"]

    assert retried["status"] == "queued"
    assert record["status"] == "queued"
    assert record["error"] is None
    assert record["retry_count"] == 1
    assert record["round"] == 3
    assert record["result"]["sources"][0]["url"] == "https://example.com"
    assert captured == {
        "task_id": "nw-retry",
        "objective": "Research Acme",
        "resume": True,
    }


def test_retry_rejects_non_failed_task():
    manager = TaskManager(SimpleNamespace())
    manager.tasks["nw-active"] = {
        "task_id": "nw-active",
        "status": "running",
        "logs": [],
        "progress_events": [],
    }

    with pytest.raises(ValueError, match="Only failed tasks"):
        manager.retry("nw-active")
