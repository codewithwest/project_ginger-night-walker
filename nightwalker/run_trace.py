from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

_SENSITIVE_KEY = re.compile(r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|session)", re.I)
_SENSITIVE_QUERY = re.compile(r"(token|key|secret|password|auth|session)", re.I)


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if not host:
            return "[REDACTED_URL]"
        netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
        if parts.port:
            netloc += f":{parts.port}"
        query = [(key, "[REDACTED]" if _SENSITIVE_QUERY.search(key) else val) for key, val in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))
    except ValueError:
        return "[REDACTED_URL]"


def redact(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value[:100]]
    if isinstance(value, str):
        text = value[:4000]
        return _redact_url(text) if text.startswith(("http://", "https://")) else text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


class TaskRunTraceStore:
    """Append-only, redacted JSONL execution traces separate from durable task state."""

    def __init__(self, directory: str | None = None, retention_days: int = 14) -> None:
        root = Path(directory or os.getenv("EVIDENCE_DIR", "./evidence"))
        self.directory = root / "traces"
        self.retention_days = max(1, retention_days)
        self._lock = threading.RLock()

    @staticmethod
    def _filename(task_id: str) -> str:
        return hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".jsonl"

    def append(self, task_id: str, phase: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        if not task_id or not phase:
            raise ValueError("Trace records require a task id and phase")
        event = {"event_id": uuid4().hex, "task_id": task_id, "at": datetime.now(UTC).isoformat(), "phase": phase[:40], "details": redact(details or {})}
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / self._filename(task_id)
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def read(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        path = self.directory / self._filename(task_id)
        if not path.is_file():
            return []
        records: deque[dict[str, Any]] = deque(maxlen=2000)
        try:
            with self._lock, path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("task_id") == task_id:
                        records.append(item)
        except OSError:
            return []
        return list(records)[-max(1, min(500, limit)):]

    def cleanup_expired(self, now: datetime | None = None) -> int:
        if not self.directory.is_dir():
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        removed = 0
        with self._lock:
            for path in self.directory.glob("*.jsonl"):
                try:
                    if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed
