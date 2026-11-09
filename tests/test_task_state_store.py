from nightwalker.task_state_store import TaskStateStore


def test_task_state_store_round_trips_checkpoint_and_task_metadata(tmp_path):
    store = TaskStateStore(str(tmp_path))
    record = {
        "task_id": "task/one",
        "status": "running",
        "objective": "Research a topic",
        "checkpoint": {
            "round_count": 4,
            "next_url": "https://example.com/next",
            "sources": [{"source_id": "s-001", "url": "https://example.com"}],
        },
    }

    store.save(record)

    assert store.load_all() == [record]


def test_task_state_store_ignores_corrupt_files_and_deletes_by_task_id(tmp_path):
    store = TaskStateStore(str(tmp_path))
    store.save({"task_id": "keep", "status": "completed"})
    (tmp_path / "corrupt.json").write_text("{", encoding="utf-8")

    assert [item["task_id"] for item in store.load_all()] == ["keep"]
    store.delete("keep")
    assert store.load_all() == []


def test_task_state_store_rejects_snapshot_copied_under_another_task_filename(tmp_path):
    import json

    store = TaskStateStore(str(tmp_path))
    store.save({"task_id": "original-task", "status": "completed"})
    original = tmp_path / store._filename("original-task")
    copied = tmp_path / store._filename("different-task")
    copied.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    assert [item["task_id"] for item in store.load_all()] == ["original-task"]
