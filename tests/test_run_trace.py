from nightwalker.run_trace import TaskRunTraceStore


def test_trace_redacts_secrets_and_preserves_phase_order(tmp_path):
    store = TaskRunTraceStore(str(tmp_path))
    store.append("task-1", "act", {
        "api_key": "never-store-this",
        "url": "https://user:password@example.com/path?token=secret&view=all",
        "message": "action started",
    })
    store.append("task-1", "verify", {"status": "verified"})
    events = store.read("task-1")
    assert [event["phase"] for event in events] == ["act", "verify"]
    assert events[0]["details"]["api_key"] == "[REDACTED]"
    assert "password" not in events[0]["details"]["url"]
    assert "user@" not in events[0]["details"]["url"]
    assert "secret" not in events[0]["details"]["url"]
    assert events[1]["details"]["status"] == "verified"


def test_trace_store_is_task_isolated(tmp_path):
    store = TaskRunTraceStore(str(tmp_path))
    store.append("task-a", "observe", {"value": 1})
    assert store.read("task-b") == []
