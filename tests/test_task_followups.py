from __future__ import annotations

import pytest

from nightwalker.task_manager import TaskManager
from nightwalker.scratchpad import TaskScratchpad
from nightwalker.task_state_store import TaskStateStore


def _manager(tmp_path):
    manager = TaskManager(orchestrator=object())
    manager.scratchpad = TaskScratchpad(str(tmp_path / "scratchpads"))
    manager.state_store = TaskStateStore(str(tmp_path / "tasks"))
    manager.tasks["task-1"] = {
        "task_id": "task-1",
        "status": "running",
        "round": 3,
        "updated_at": "now",
        "logs": [],
        "progress_events": [],
        "scratchpad": [],
        "follow_up_instructions": [],
    }
    return manager


def test_active_task_accepts_follow_up_and_seeds_scratchpad(tmp_path):
    manager = _manager(tmp_path)

    snapshot = manager.add_follow_up("task-1", "Prioritise primary sources")

    assert snapshot is not None
    assert snapshot["follow_up_instructions"] == ["Prioritise primary sources"]
    assert snapshot["progress_events"][-1]["event_type"] == "user_follow_up"
    assert manager._effective_objective("task-1", "Research topic").endswith(
        "- Prioritise primary sources"
    )
    assert manager.scratchpad.read("task-1")[-1]["operator_input"] == "Prioritise primary sources"


def test_terminal_task_rejects_follow_up(tmp_path):
    manager = _manager(tmp_path)
    manager.tasks["task-1"]["status"] = "completed"

    with pytest.raises(ValueError, match="only be added while"):
        manager.add_follow_up("task-1", "Add one more source")
