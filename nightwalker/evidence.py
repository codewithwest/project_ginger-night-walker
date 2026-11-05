from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class EvidenceStore:
    """Persist bounded, task-scoped browser observations as immutable JSON records."""

    def __init__(self, directory: str | None = None) -> None:
        self.directory = Path(directory or os.getenv("EVIDENCE_DIR", "./evidence"))

    def save(self, task_id: str, payload: dict[str, Any]) -> str:
        evidence_id = f"ev-{uuid4().hex}"
        record = {
            "evidence_id": evidence_id,
            "task_id": task_id,
            "created_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{evidence_id}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
        return evidence_id
