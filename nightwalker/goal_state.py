"""Evidence-gated goal state adapted from the Goal-Aware Strands prototype.

The original objective and criteria are immutable for a task. Model proposals are never
accepted as completion unless they quote text from a current, captured evidence record.
"""
from __future__ import annotations

import re
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
        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in evidence if item.get("evidence_id")
        }
        stopwords = {
            "about", "after", "based", "clear", "specific", "within", "which",
            "their", "there", "these", "those", "through", "directly", "determine",
            "establish", "identify", "objectives", "objective", "criteria",
            "progress", "unique", "mechanics", "known",
            "other", "game", "games", "trainer", "velocity", "knight",
        }
        def terms(value: str) -> set[str]:
            return {
                token[:-1] if len(token) > 4 and token.endswith("s") else token
                for token in re.findall(r"[a-z0-9]{3,}", value.casefold())
                if token not in stopwords
            }
        synonyms = {
            "technical": {"weapon", "stamina", "armour", "crafting", "combat", "skill"},
            "features": {"weapon", "upgrade", "skill", "mechanic", "crafting"},
            "progression": {"unlock", "experience", "quest", "level", "boss", "ability"},
            "rules": {"unlock", "earn", "gain", "level"},
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
            criterion_terms = criterion_terms | {
                alias for term in criterion_terms for alias in synonyms.get(term, set())
            }
            overlap = criterion_terms & set(meaningful_words)
            relevant = len(overlap) >= 2 if len(criterion_terms) > 2 else bool(overlap)
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

    @staticmethod
    def apply_verified_plan_results(
        state: dict[str, Any],
        execution_plan: dict[str, Any] | None,
        evidence: list[dict[str, Any]],
        plan_outputs: dict[str, Any] | None,
        structured_data: dict[str, list[Any]] | None = None,
    ) -> dict[str, Any]:
        """Bridge trusted, evidence-backed mission results into matching goal criteria.

        This is deliberately narrow: the task manager must have completed a plan step,
        the comparison must contain at least two priced products, the selected item must
        be the numeric minimum, and its exact name and displayed price must occur in the
        captured evidence. Model proposals are not used for this verification.
        """
        import re

        if not isinstance(execution_plan, dict) or not isinstance(plan_outputs, dict):
            return state
        products = (structured_data or {}).get("products") or []
        priced: list[tuple[float, dict[str, Any]]] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            value = product.get("price_value")
            if not isinstance(value, (int, float)):
                match = re.search(r"[\\d,]+(?:\\.\\d{1,2})?", str(product.get("price") or ""))
                value = float(match.group(0).replace(",", "")) if match else None
            if isinstance(value, (int, float)):
                priced.append((float(value), product))
        if len(priced) < 2:
            return state
        selected = plan_outputs.get("cheapest_product")
        if not isinstance(selected, dict):
            return state
        selected_name = str(selected.get("name") or "").strip()
        selected_price = selected.get("price_value")
        if not isinstance(selected_price, (int, float)):
            match = re.search(r"[\\d,]+(?:\\.\\d{1,2})?", str(selected.get("price") or ""))
            selected_price = float(match.group(0).replace(",", "")) if match else None
        if not selected_name or not isinstance(selected_price, (int, float)):
            return state
        minimum, minimum_product = min(priced, key=lambda item: item[0])
        if minimum != float(selected_price) or str(minimum_product.get("name") or "").strip().casefold() != selected_name.casefold():
            return state

        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in evidence if item.get("evidence_id")
        }
        completed_steps = [
            step for step in execution_plan.get("steps", [])
            if isinstance(step, dict)
            and step.get("status") == "completed"
            and step.get("evidence_refs")
        ]
        for step in completed_steps:
            objective = str(step.get("objective") or "").casefold()
            result = str(step.get("result") or "").casefold()
            combined = objective + " " + result
            if not any(term in combined for term in ("cheapest", "lowest", "minimum")):
                continue
            if not any(term in combined for term in ("product", "item")):
                continue
            for criterion in state.get("remaining", []):
                criterion_lower = str(criterion).casefold()
                if not any(term in criterion_lower for term in ("cheapest", "lowest", "minimum")):
                    continue
                if not any(term in criterion_lower for term in ("product", "item")):
                    continue
                for evidence_id in step.get("evidence_refs", []):
                    item = evidence_by_id.get(str(evidence_id))
                    captured = str(item.get("captured_text") or "") if item else ""
                    name_index = captured.casefold().find(selected_name.casefold())
                    if name_index < 0:
                        continue
                    # Match the selected numeric price in the captured text after the
                    # product name, accepting common currency/spacing formats.
                    price_match = None
                    for match in re.finditer(r"(?:R\\s*)?[\\d,]+(?:\\.\\d{2})?", captured[name_index:], re.IGNORECASE):
                        number = float(re.sub(r"[^\\d.]", "", match.group(0)))
                        if abs(number - float(selected_price)) < 0.000001:
                            price_match = match
                            break
                    if price_match is None:
                        continue
                    quote_end = name_index + price_match.end()
                    quote = captured[name_index:quote_end].strip()[:500]
                    state.setdefault("evidence_by_criterion", {})[criterion] = {
                        "evidence_id": str(evidence_id),
                        "quote": quote,
                        "verification": "trusted_plan_numeric_minimum",
                    }
                    if criterion not in state.setdefault("completed", []):
                        state["completed"].append(criterion)
                    break
        state["remaining"] = [
            item for item in state.get("criteria", [])
            if item not in state.get("completed", [])
        ]
        return state

