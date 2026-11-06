"""Evidence-gated goal state adapted from the Goal-Aware Strands prototype.

The original objective and criteria are immutable for a task. Model proposals are never
accepted as completion unless they quote text from a current, captured evidence record.
"""
from __future__ import annotations

from typing import Any

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
        evidence_text = {
            str(item.get("evidence_id")): str(item.get("captured_text") or "")
            for item in evidence if item.get("evidence_id")
        }
        for claim in proposal.completed_with_evidence:
            if claim.criterion not in state["criteria"]:
                continue
            captured = evidence_text.get(claim.evidence_id, "")
            if captured and claim.quote.casefold() in captured.casefold():
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
