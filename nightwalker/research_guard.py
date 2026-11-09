"""Deterministic loop detection and bounded source recovery for research rounds."""
from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def canonical_url_key(value: str) -> str:
    """Canonical comparison key; does not change the URL sent to the browser."""
    try:
        parts = urlsplit(str(value).strip())
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        if not scheme or not host:
            return str(value).strip().split("#", 1)[0]
        port = parts.port
        if (scheme, port) in {("http", 80), ("https", 443)}:
            port = None
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc += f":{port}"
        path = parts.path or "/"
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((scheme, netloc, path, parts.query, ""))
    except (TypeError, ValueError):
        return str(value).strip().split("#", 1)[0]


class ResearchLoopGuard:
    """Tracks source attempts and repeated URL/assignment pairs within one task."""

    def __init__(self, max_attempts_per_url: int = 2) -> None:
        self.max_attempts_per_url = max(1, max_attempts_per_url)
        self.attempts: Counter[str] = Counter()
        self.action_attempts: Counter[tuple[str, str]] = Counter()
        self.outcomes: list[dict[str, Any]] = []

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any] | None) -> ResearchLoopGuard:
        snapshot = snapshot or {}
        guard = cls(max_attempts_per_url=int(snapshot.get("max_attempts_per_url", 2)))
        for key, count in (snapshot.get("attempts") or {}).items():
            guard.attempts[str(key)] = max(0, int(count))
        for item in snapshot.get("repeated_actions", []):
            if isinstance(item, dict):
                action = (str(item.get("url_key", "")), str(item.get("assignment", "")))
                guard.action_attempts[action] = max(0, int(item.get("attempts", 0)))
        guard.outcomes = [
            item for item in snapshot.get("recent_outcomes", [])
            if isinstance(item, dict)
        ][-60:]
        return guard

    def begin(self, url: str, assignment: str) -> dict[str, Any]:
        key = canonical_url_key(url)
        action = (key, " ".join(str(assignment).casefold().split())[:300])
        self.attempts[key] += 1
        self.action_attempts[action] += 1
        return {
            "url_key": key,
            "attempt": self.attempts[key],
            "action_repeat": self.action_attempts[action],
            "should_skip": self.attempts[key] > self.max_attempts_per_url,
        }

    def record_outcome(
        self, url: str, assignment: str, status: str, reason: str = ""
    ) -> None:
        self.outcomes.append({
            "url_key": canonical_url_key(url),
            "assignment": " ".join(str(assignment).split())[:300],
            "status": str(status)[:40],
            "reason": str(reason)[:300],
        })
        self.outcomes = self.outcomes[-60:]

    @staticmethod
    def is_visited(url: str, visited_urls: list[str]) -> bool:
        key = canonical_url_key(url)
        return any(canonical_url_key(item) == key for item in visited_urls)

    def filter_candidates(
        self, candidates: list[dict[str, Any]], visited_urls: list[str], limit: int = 120
    ) -> list[dict[str, str]]:
        visited = {canonical_url_key(url) for url in visited_urls}
        selected: dict[str, dict[str, str]] = {}
        for item in candidates:
            url = str(item.get("url") or "").strip()
            key = canonical_url_key(url)
            if (
                not url
                or key in visited
                or self.attempts[key] >= self.max_attempts_per_url
                or key in selected
            ):
                continue
            selected[key] = {
                "url": url,
                "label": str(item.get("label") or "")[:240],
            }
        return list(selected.values())[:max(1, min(1000, int(limit)))]

    def fallback_candidate(
        self, candidates: list[dict[str, Any]], visited_urls: list[str]
    ) -> dict[str, str] | None:
        available = self.filter_candidates(candidates, visited_urls)
        return available[0] if available else None

    def snapshot(self) -> dict[str, Any]:
        return {
            "attempts": dict(self.attempts),
            "recent_outcomes": list(self.outcomes[-12:]),
            "repeated_actions": [
                {"url_key": key[0], "assignment": key[1], "attempts": count}
                for key, count in self.action_attempts.most_common(12)
            ],
            "max_attempts_per_url": self.max_attempts_per_url,
        }
