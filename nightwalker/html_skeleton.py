"""Compact structural extraction from HTML, excluding scripts and visual noise."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


class _SkeletonParser(HTMLParser):
    def __init__(self, limit: int = 120) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.items: list[dict[str, Any]] = []
        self._capture: tuple[str, dict[str, Any], list[str]] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag in {"script", "style", "svg", "noscript", "template"}:
            self._skip += 1
            return
        if self._skip:
            return
        role = values.get("role", "")
        if tag in {"main", "header", "nav", "footer", "article", "section", "aside", "form"}:
            self.items.append({
                "type": "landmark", "tag": tag,
                "name": values.get("aria-label") or values.get("aria-labelledby", ""),
            })
        if tag in {"h1", "h2", "h3", "h4"}:
            self._capture = ("heading", {"level": int(tag[1]), "id": values.get("id", "")}, [])
        elif tag in {"button", "input", "select", "textarea"} or role in {
            "button", "link", "checkbox", "radio", "combobox", "searchbox", "tab",
        }:
            self.items.append({
                "type": "control", "tag": tag, "role": role,
                "name": values.get("aria-label") or values.get("name") or values.get("placeholder") or values.get("title", ""),
                "input_type": values.get("type", ""),
                "id": values.get("id", ""),
                "disabled": "disabled" in values,
            })
        elif tag == "a" and values.get("href"):
            self.items.append({
                "type": "link", "name": values.get("aria-label") or values.get("title", ""),
                "href": values.get("href", "")[:500],
            })

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript", "template"} and self._skip:
            self._skip -= 1
        if self._capture and tag in {"h1", "h2", "h3", "h4"}:
            kind, metadata, text = self._capture
            metadata["text"] = " ".join(" ".join(text).split())[:240]
            if metadata["text"]:
                self.items.append({"type": kind, **metadata})
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture and not self._skip and data.strip():
            self._capture[2].append(data.strip())


def extract_html_skeleton(html: str, *, limit: int = 120) -> list[dict[str, Any]]:
    """Return landmarks, headings, controls and links without page boilerplate."""
    parser = _SkeletonParser(limit=limit)
    try:
        parser.feed(html or "")
    except (ValueError, AssertionError):
        pass
    return parser.items[:max(1, limit)]
