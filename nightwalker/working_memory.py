from __future__ import annotations

from typing import Any


class WorkingMemory:
    """Bound prompt context without mutating durable task history or evidence."""

    def __init__(self, max_chars: int = 6000, max_items: int = 40) -> None:
        self.max_chars = max(500, max_chars)
        self.max_items = max(1, max_items)

    def compact_value(self, value: Any, *, depth: int = 0) -> Any:
        if depth > 5:
            return "[depth limit]"
        if isinstance(value, str):
            return value if len(value) <= self.max_chars else value[:self.max_chars] + "…[truncated]"
        if isinstance(value, list):
            items = [self.compact_value(item, depth=depth + 1) for item in value[:self.max_items]]
            if len(value) > self.max_items:
                items.append(f"[{len(value) - self.max_items} additional items omitted]")
            return items
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= self.max_items:
                    result["_omitted_keys"] = len(value) - self.max_items
                    break
                result[str(key)] = self.compact_value(item, depth=depth + 1)
            return result
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:500]

    def compile(self, *, task_id: str, objective: str, workflow: str, active_step: dict[str, Any] | None, observations: list[dict[str, Any]]) -> dict[str, Any]:
        packet = {
            "task_id": task_id,
            "objective": objective[:1000],
            "workflow": workflow,
            "active_step": self.compact_value(active_step or {}),
            "recent_observations": self.compact_value(observations[-8:]),
        }
        import json
        if len(json.dumps(packet, ensure_ascii=False)) > self.max_chars:
            packet["recent_observations"] = []
            packet["context_note"] = "Observation history omitted from this prompt due to context budget; durable task history and evidence remain available."
        return packet
