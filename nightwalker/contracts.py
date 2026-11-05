from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Capability(str, Enum):
    NAVIGATE = "navigate"
    INTERACT = "interact"
    EXTRACT = "extract"
    RECOVER = "recover"


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    capability: Capability
    objective: str = Field(min_length=1, max_length=500)
    target_url: HttpUrl | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    requirements: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    evidence_id: str
    kind: str
    description: str


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    capability: Capability
    status: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class BrowserExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    objective: str = Field(default="Extract page title and main heading", max_length=500)


class BrowserExtractResponse(BaseModel):
    task_id: str
    status: str
    url: str
    title: str | None = None
    headings: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    issues: list[str] = Field(default_factory=list)
