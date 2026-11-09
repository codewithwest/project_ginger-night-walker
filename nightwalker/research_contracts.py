from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ResearchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    label: str = Field(default="", max_length=240)


class ResearchRoundPlan(BaseModel):
    """Select one already-discovered public URL or finish the research task."""
    model_config = ConfigDict(extra="forbid")
    done: bool
    next_url: HttpUrl | None = None
    assignment: str = Field(min_length=1, max_length=400)
    reason: str = Field(default="", max_length=400)

    @staticmethod
    def _canonical_url(value: str) -> str:
        """Normalise URL syntax without weakening path/query identity."""
        parsed = urlsplit(str(value).strip())
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if not scheme or not hostname or parsed.username or parsed.password:
            raise ValueError("Research source must be an absolute URL without credentials")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Research source has an invalid port") from exc
        if (scheme, port) in {("http", 80), ("https", 443)}:
            port = None
        netloc = hostname if port is None else f"{hostname}:{port}"
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/") or "/"
        return urlunsplit((scheme, netloc, path, parsed.query, ""))

    @classmethod
    def validate_plan(cls, value: ResearchRoundPlan, allowed_urls: set[str]) -> ResearchRoundPlan:
        if value.done and value.next_url:
            raise ValueError("A completed research plan cannot select another URL")
        if not value.done and not value.next_url:
            raise ValueError("An unfinished research plan must select a URL")
        if value.next_url:
            selected = cls._canonical_url(str(value.next_url))
            allowed = {cls._canonical_url(item) for item in allowed_urls}
            if selected not in allowed:
                raise ValueError("The selected URL was not discovered by the browser")
        return value


class ResearchSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Leave room for evidence-backed detail while keeping reports bounded.
    answer: str = Field(min_length=1, max_length=6000)
    coverage_summary: str = Field(default="", max_length=1200)
    gaps: list[str] = Field(default_factory=list, max_length=12)
