"""Atomic JSON persistence for task records and resumable research checkpoints."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class TaskStateStore:
    """Stores one task snapshot per file; writes are atomic within the mounted volume."""

    def __init__(self, directory: str | None = None) -> None:
        root = directory or os.getenv("TASK_STATE_DIR")
        self.directory = Path(root) if root else Path(os.getenv("EVIDENCE_DIR", "./evidence")) / "tasks"

    @staticmethod
    def _filename(task_id: str) -> str:
        return hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".json"

    def save(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise ValueError("Persisted task must have a task_id")
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / self._filename(task_id)
        temporary = destination.with_suffix(".tmp")
        # Flush the complete JSON document before the atomic rename. The rename
        # prevents readers from seeing a partial file; fsync makes the checkpoint
        # resilient to an abrupt container/host restart on the mounted volume.
        payload = json.dumps(task, ensure_ascii=False, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some filesystems do not support fsync on directories. The file itself
            # has still been flushed and atomically replaced.
            pass

    def load_all(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or not isinstance(value.get("task_id"), str):
                continue
            # A snapshot is trusted only when its filename is derived from the
            # task_id inside that same snapshot. This prevents copied/renamed task
            # JSON from being restored under another task's identity.
            if path.name != self._filename(value["task_id"]):
                continue
            records.append(value)
        return records

    def delete(self, task_id: str) -> None:
        path = self.directory / self._filename(task_id)
        path.unlink(missing_ok=True)
