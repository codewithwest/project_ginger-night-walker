from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TaskScratchpad:
    """Append-only, task-scoped execution notes; evidence remains in EvidenceStore."""

    def __init__(self, directory: str | None = None) -> None:
        root = Path(directory or os.getenv("EVIDENCE_DIR", "./evidence"))
        self.directory = root / "scratchpads"

    def append(self, task_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o777)
        except OSError:
            pass
        safe_id = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        path = self.directory / f"{safe_id}.json"
        existing = self.read(task_id)
        item = {
            "recorded_at": datetime.now(UTC).isoformat(),
            **entry,
        }
        existing.append(item)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return item

    def read(self, task_id: str) -> list[dict[str, Any]]:
        safe_id = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        path = self.directory / f"{safe_id}.json"
        if not path.is_file():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []
