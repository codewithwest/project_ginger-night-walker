"""Persistable wall-clock and monotonic stage timing for Night Walker jobs."""
from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Any


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class TaskTiming:
    """Accumulates active stage durations and exposes total wall-clock duration."""

    def __init__(self) -> None:
        self._phase_started: dict[str, float] = {}

    @staticmethod
    def _state(record: dict[str, Any]) -> dict[str, Any]:
        value = record.setdefault("timing", {})
        value.setdefault("phases_ms", {})
        value.setdefault("started_at", record.get("created_at"))
        value.setdefault("finished_at", None)
        value.setdefault("elapsed_ms", 0)
        value.setdefault("active_elapsed_ms", 0)
        value.setdefault("active_phase", None)
        return value

    def start(self, record: dict[str, Any], phase: str) -> None:
        self.end(record)
        state = self._state(record)
        state["finished_at"] = None
        if not state.get("execution_started_at"):
            created = _parse(state.get("started_at") or record.get("created_at"))
            if created:
                state.setdefault("phases_ms", {})["queue"] = max(0, round((datetime.now(UTC) - created).total_seconds() * 1000))
            state["execution_started_at"] = datetime.now(UTC).isoformat()
        state["active_phase"] = phase
        state["active_phase_started_at"] = datetime.now(UTC).isoformat()
        self._phase_started[str(record["task_id"])] = monotonic()

    def end(self, record: dict[str, Any]) -> None:
        state = self._state(record)
        task_id = str(record.get("task_id") or "")
        started = self._phase_started.pop(task_id, None)
        phase = state.get("active_phase")
        if started is not None and phase:
            elapsed = max(0, round((monotonic() - started) * 1000))
            phases = state.setdefault("phases_ms", {})
            phases[phase] = int(phases.get(phase, 0)) + elapsed
            state["active_elapsed_ms"] = sum(int(value) for value in phases.values())
        state["active_phase"] = None
        state["active_phase_started_at"] = None
        self.refresh(record)

    def finish(self, record: dict[str, Any]) -> None:
        self.end(record)
        state = self._state(record)
        state["finished_at"] = datetime.now(UTC).isoformat()
        self.refresh(record)

    def refresh(self, record: dict[str, Any]) -> dict[str, Any]:
        state = self._state(record)
        started = _parse(state.get("started_at") or record.get("created_at"))
        finished = _parse(state.get("finished_at"))
        end = finished or datetime.now(UTC)
        state["elapsed_ms"] = max(0, round((end - started).total_seconds() * 1000)) if started else 0
        active = self._phase_started.get(str(record.get("task_id") or ""))
        state["current_phase_ms"] = 0
        if active is not None and state.get("active_phase"):
            current = max(0, round((monotonic() - active) * 1000))
            state["current_phase_ms"] = current
            state["active_elapsed_ms"] = sum(int(v) for v in state.get("phases_ms", {}).values()) + current
        else:
            state["active_elapsed_ms"] = sum(int(v) for v in state.get("phases_ms", {}).values())
        state["phases"] = {
            name: {"duration_ms": int(duration), "share_percent": round((int(duration) / max(1, state["active_elapsed_ms"])) * 100, 1)}
            for name, duration in state.get("phases_ms", {}).items()
        }
        return state

    def snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        return dict(self.refresh(record))
