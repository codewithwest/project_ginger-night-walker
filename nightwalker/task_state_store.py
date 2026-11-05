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
        temporary.write_text(
            json.dumps(task, ensure_ascii=False, separators=(",", ":"), default=str),
            encoding="utf-8",
        )
        os.replace(temporary, destination)

    def load_all(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("task_id"), str):
                records.append(value)
        return records

    def delete(self, task_id: str) -> None:
        path = self.directory / self._filename(task_id)
        path.unlink(missing_ok=True)
