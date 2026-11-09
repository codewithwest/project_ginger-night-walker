from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=300)


class NavigateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl


class FollowLinkArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=3, max_length=500)


class CaptureArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_chars: int = Field(default=12000, ge=100, le=20000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    argument_model: type[BaseModel]
    workflows: frozenset[str]
    read_only: bool = True

    def validate_arguments(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.argument_model.model_validate(value).model_dump(mode="json")


class ToolRegistry:
    """Explicit, workflow-scoped registry; unknown tools and extra args are rejected."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        if not spec.read_only:
            raise ValueError("Only read-only tools may be registered in the beta runtime")
        self._tools[spec.name] = spec

    def get(self, name: str, workflow: str | None = None) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ValueError(f"Unregistered tool: {name}")
        if workflow is not None and workflow not in spec.workflows:
            raise PermissionError(f"Tool {name} is not permitted for workflow {workflow}")
        return spec

    def names_for(self, workflow: str) -> list[str]:
        return sorted(name for name, spec in self._tools.items() if workflow in spec.workflows)

    def validate_call(self, name: str, arguments: dict[str, Any], workflow: str) -> dict[str, Any]:
        return self.get(name, workflow).validate_arguments(arguments)


DEFAULT_TOOL_REGISTRY = ToolRegistry()
for _spec in (
    ToolSpec("browser.navigate", "Navigate to a public HTTP(S) page.", NavigateArguments, frozenset({"seo", "qa", "seo_qa"})),
    ToolSpec("browser.follow_relevant_link", "Follow one relevant same-origin link.", FollowLinkArguments, frozenset({"seo", "qa", "seo_qa"})),
    ToolSpec("browser.capture_page", "Capture bounded page text and audit evidence.", CaptureArguments, frozenset({"seo", "qa", "seo_qa"})),
):
    DEFAULT_TOOL_REGISTRY.register(_spec)


def _normalise(value: str) -> str:
    stop_words = {"go", "find", "open", "visit", "navigate", "show", "click", "select", "the", "a", "an", "to", "for", "page", "link"}
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold())
    return " ".join(token for token in tokens if token not in stop_words)


@dataclass(frozen=True)
class ElementResolution:
    status: str
    ref: str | None
    confidence: float
    candidates: tuple[dict[str, Any], ...]
    reason: str


class ElementResolver:
    """Resolve only grounded accessibility refs; never fabricate CSS/XPath selectors."""

    def __init__(self, minimum_confidence: float = 0.72, minimum_margin: float = 0.12):
        self.minimum_confidence = minimum_confidence
        self.minimum_margin = minimum_margin

    @staticmethod
    def _score(query: str, candidate: str) -> float:
        q, c = _normalise(query), _normalise(candidate)
        if not q or not c:
            return 0.0
        if q == c:
            return 1.0
        if q in c or c in q:
            return 0.91
        q_tokens, c_tokens = set(q.split()), set(c.split())
        overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))
        return 0.55 + 0.4 * overlap if overlap else 0.0

    def resolve(self, snapshot: list[dict[str, Any]], query: str, *, role: str | None = None) -> ElementResolution:
        ranked: list[dict[str, Any]] = []
        for node in snapshot[:500]:
            if not isinstance(node, dict):
                continue
            if role and str(node.get("role", "")).casefold() != role.casefold():
                continue
            ref = node.get("ref")
            if not isinstance(ref, str) or not ref:
                continue
            name = str(node.get("name") or node.get("text") or "")
            score = self._score(query, name)
            if score:
                ranked.append({"ref": ref, "role": node.get("role"), "name": name[:240], "confidence": round(score, 4)})
        ranked.sort(key=lambda item: item["confidence"], reverse=True)
        candidates = tuple(ranked[:5])
        if not ranked or ranked[0]["confidence"] < 0.5:
            return ElementResolution("no_match", None, 0.0, candidates, "No grounded element matched the request")
        top = ranked[0]
        margin = top["confidence"] - (ranked[1]["confidence"] if len(ranked) > 1 else 0.0)
        if top["confidence"] >= self.minimum_confidence and margin >= self.minimum_margin:
            return ElementResolution("resolved", top["ref"], top["confidence"], candidates, "Unique high-confidence accessibility match")
        return ElementResolution("ambiguous", None, top["confidence"], candidates, "Multiple or insufficiently distinct matches; no action authorised")


@dataclass(frozen=True)
class VerificationResult:
    status: str
    retryable: bool
    checks: tuple[dict[str, Any], ...]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "retryable": self.retryable, "checks": list(self.checks), "reason": self.reason}


class ActionVerifier:
    """Python-owned outcome verification; model evaluation cannot override this result."""

    def verify_workflow_result(self, result: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(result, dict):
            return VerificationResult("unknown", False, (), "No structured browser result was returned").as_dict()
        status = str(result.get("status") or "unknown")
        if status != "completed":
            return VerificationResult(
                "failed" if status in {"failed", "blocked"} else "unknown",
                status in {"failed", "unknown"},
                ({"check": "browser_status", "passed": False, "actual": status},),
                f"Browser workflow ended with status {status}",
            ).as_dict()
        evidence_id = result.get("evidence_id")
        evidence = result.get("evidence") or []
        evidence_match = bool(
            isinstance(evidence_id, str) and evidence_id
            and any(isinstance(item, dict) and item.get("evidence_id") == evidence_id for item in evidence)
        )
        checks = (
            {"check": "browser_status", "passed": True, "actual": status},
            {"check": "evidence_persisted", "passed": evidence_match, "actual": bool(evidence_id)},
        )
        if not evidence_match:
            return VerificationResult("failed", False, checks, "Browser reported completion without a matching persisted evidence reference").as_dict()
        return VerificationResult("verified", False, checks, "Browser completion and persisted evidence reference verified").as_dict()
