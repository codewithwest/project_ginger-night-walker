from __future__ import annotations

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

    @classmethod
    def validate_plan(cls, value: ResearchRoundPlan, allowed_urls: set[str]) -> ResearchRoundPlan:
        if value.done and value.next_url:
            raise ValueError("A completed research plan cannot select another URL")
        if not value.done and not value.next_url:
            raise ValueError("An unfinished research plan must select a URL")
        if value.next_url:
            selected = str(value.next_url)
            if not any(selected.rstrip("/") == item.rstrip("/") for item in allowed_urls):
                raise ValueError("The selected URL was not discovered by the browser")
        return value


class ResearchSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=3000)
    coverage_summary: str = Field(default="", max_length=1200)
    gaps: list[str] = Field(default_factory=list, max_length=12)
