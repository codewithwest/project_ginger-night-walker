from nightwalker.working_memory import WorkingMemory


def test_working_memory_bounds_large_observations_without_mutating_input():
    original = [{"text": "x" * 1000, "index": index} for index in range(100)]
    memory = WorkingMemory(max_chars=1000, max_items=5)
    packet = memory.compile(task_id="task-1", objective="Audit the site", workflow="seo", active_step={"step_id": "inspect"}, observations=original)
    assert packet["task_id"] == "task-1"
    assert packet["context_note"].startswith("Observation history omitted")
    assert len(original) == 100


def test_compact_value_limits_lists_and_strings():
    memory = WorkingMemory(max_chars=500, max_items=2)
    result = memory.compact_value({"items": ["a", "b", "c"], "text": "z" * 700})
    assert result["items"][-1] == "[1 additional items omitted]"
    assert result["text"].endswith("[truncated]")
