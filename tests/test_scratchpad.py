from __future__ import annotations

import json

from nightwalker.scratchpad import TaskScratchpad


def test_scratchpad_appends_task_scoped_notes(tmp_path):
    store = TaskScratchpad(str(tmp_path))

    first = store.append("task-a", {"round": 1, "status": "completed"})
    second = store.append("task-a", {"round": 2, "status": "partial"})

    assert first["round"] == 1
    assert second["round"] == 2
    assert [item["round"] for item in store.read("task-a")] == [1, 2]
    assert store.read("task-b") == []
    assert json.loads((tmp_path / "scratchpads" / "task-a.json").read_text())[0]["status"] == "completed"
