"""Evidence-gated goal state adapted from the Goal-Aware Strands prototype.

The original objective and criteria are immutable for a task. Model proposals are never
accepted as completion unless they quote text from a current, captured evidence record.
"""
from __future__ import annotations

from typing import Any
import re

from pydantic import BaseModel, ConfigDict, Field


class GoalCriteriaPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[str] = Field(min_length=1, max_length=12)


class GoalEvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str
    evidence_id: str
    quote: str = Field(min_length=8, max_length=500)


class GoalProgressProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: str = Field(default="", max_length=500)
    completed_with_evidence: list[GoalEvidenceClaim] = Field(default_factory=list, max_length=6)


class GoalStateManager:
    """Python-owned goal ledger; models can suggest progress, never grant completion."""

    @staticmethod
    def initialise(objective: str, criteria: list[str]) -> dict[str, Any]:
        cleaned = list(dict.fromkeys(
            item.strip()[:240] for item in criteria
            if isinstance(item, str) and item.strip()
        ))[:12]
        if not cleaned:
            cleaned = [objective.strip()[:240] or "Answer the user's research objective"]
        return {
            "original_goal": objective,
            "criteria": cleaned,
            "completed": [],
            "evidence_by_criterion": {},
            "remaining": list(cleaned),
            "observations": [],
        }

    @staticmethod
    def apply_proposal(
        state: dict[str, Any],
        proposal: GoalProgressProposal,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in evidence if item.get("evidence_id")
        }
        stopwords = {
            "about", "after", "based", "clear", "specific", "within", "which",
            "their", "there", "these", "those", "through", "directly", "determine",
            "establish", "identify", "objectives", "objective", "criteria", "rules",
            "progression", "progress", "unique", "features", "mechanics", "known",
            "other", "game", "games", "trainer", "velocity", "knight",
        }
        def terms(value: str) -> set[str]:
            return {
                token for token in re.findall(r"[a-z0-9]{3,}", value.casefold())
                if token not in stopwords
            }
        for claim in proposal.completed_with_evidence:
            if claim.criterion not in state["criteria"]:
                continue
            item = evidence_by_id.get(claim.evidence_id)
            captured = str(item.get("captured_text") or "") if item else ""
            words = re.findall(r"[a-z0-9]{3,}", captured.casefold())
            substantive = len(set(words)) >= 12 and len(captured.strip()) >= 120
            boilerplate = {"images", "videos", "shopping", "maps", "news", "all", "search"}
            meaningful_words = [word for word in words if word not in boilerplate]
            criterion_terms = terms(claim.criterion)
            overlap = criterion_terms & set(meaningful_words)
            relevant = len(overlap) >= 2 if len(criterion_terms) >= 3 else bool(overlap)
            quote_is_informative = len(terms(claim.quote)) >= 3
            already_used = any(
                value.get("evidence_id") == claim.evidence_id
                for key, value in state["evidence_by_criterion"].items()
                if key != claim.criterion
            )
            if (
                item and substantive and relevant and quote_is_informative
                and not already_used
                and claim.quote.casefold() in captured.casefold()
            ):
                state["evidence_by_criterion"][claim.criterion] = {
                    "evidence_id": claim.evidence_id,
                    "quote": claim.quote,
                }
                if claim.criterion not in state["completed"]:
                    state["completed"].append(claim.criterion)
        state["remaining"] = [
            item for item in state["criteria"] if item not in state["completed"]
        ]
        if proposal.observation:
            state["observations"].append(proposal.observation)
            state["observations"] = state["observations"][-30:]
        return state
