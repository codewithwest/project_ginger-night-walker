from __future__ import annotations

import asyncio

import pytest

from nightwalker.task_manager import TaskManager
from nightwalker.scratchpad import TaskScratchpad


class SlowOllama:
    async def decide_task(self, objective):
        await asyncio.sleep(30)


class FakeOrchestrator:
    def __init__(self):
        self.ollama = SlowOllama()


@pytest.mark.asyncio
async def test_running_task_can_be_cancelled_and_recorded(tmp_path):
    manager = TaskManager(FakeOrchestrator())
    manager.scratchpad = TaskScratchpad(str(tmp_path))
    manager.submit_natural_language("cancel-me", "Research something")

    stopped = await manager.stop("cancel-me")

    assert stopped is not None
    assert stopped["status"] == "stopped"
    assert stopped["progress_events"][-1]["event_type"] == "cancelled"
    assert manager.scratchpad.read("cancel-me")[-1]["assignment"] == "Cancellation"
