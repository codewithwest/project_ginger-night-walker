"""Consistent, non-authoritative identity headers for model-service calls."""
from __future__ import annotations

import re
from typing import Any


def agent_headers(
    *,
    agent: str,
    capability: str,
    operation: str,
    model: str | None = None,
    task_id: str | None = None,
    round_number: int | None = None,
    integration: str = "night-walker",
) -> dict[str, str]:
    """Build trace headers; these identify a caller but never grant permissions."""
    values: dict[str, Any] = {
        "X-Ginger-Agent": agent,
        "X-Ginger-Capability": capability,
        "X-Ginger-Operation": operation,
        "X-Ginger-Integration": integration,
        "X-Ginger-Model": model,
        "X-Ginger-Task-Id": task_id,
        "X-Ginger-Research-Round": round_number,
    }
    result = {}
    for key, value in values.items():
        if value is None or value == "":
            continue
        text = str(value).strip()
        # Header values must be single-line and bounded.
        text = re.sub(r"[\r\n\x00-\x1f\x7f]", " ", text)[:200]
        if text:
            result[key] = text
    return result
