import time

from nightwalker.task_timing import TaskTiming


def test_task_timing_accumulates_stages_and_freezes_on_finish():
    timing = TaskTiming()
    record = {
        "task_id": "timed-job",
        "created_at": "2026-01-01T00:00:00+00:00",
        "timing": {"started_at": "2026-01-01T00:00:00+00:00", "phases_ms": {}},
    }
    timing.start(record, "planning")
    time.sleep(0.01)
    timing.end(record)
    timing.start(record, "browser")
    time.sleep(0.01)
    timing.finish(record)

    snapshot = timing.snapshot(record)
    assert snapshot["finished_at"] is not None
    assert snapshot["phases_ms"]["planning"] >= 5
    assert snapshot["phases_ms"]["browser"] >= 5
    assert snapshot["active_phase"] is None
    assert snapshot["elapsed_ms"] >= snapshot["active_elapsed_ms"]


def test_task_timing_can_resume_after_clarification():
    timing = TaskTiming()
    record = {
        "task_id": "clarify-job",
        "created_at": "2026-01-01T00:00:00+00:00",
        "timing": {"started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:01:00+00:00", "phases_ms": {}},
    }
    timing.start(record, "planning")
    assert record["timing"]["finished_at"] is None
    time.sleep(0.01)
    timing.finish(record)
    assert record["timing"]["phases_ms"]["planning"] >= 5
