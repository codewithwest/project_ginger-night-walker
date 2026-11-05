from __future__ import annotations

import pytest

from nightwalker.research_contracts import ResearchRoundPlan


def test_round_plan_must_select_a_discovered_url():
    plan = ResearchRoundPlan(
        done=False,
        next_url="https://example.com/research",
        assignment="Inspect the research page",
    )
    validated = ResearchRoundPlan.validate_plan(
        plan, {"https://example.com/research/"}
    )
    assert str(validated.next_url) == "https://example.com/research/"


def test_round_plan_rejects_model_invented_url():
    plan = ResearchRoundPlan(
        done=False,
        next_url="https://not-discovered.example/",
        assignment="Inspect another source",
    )
    with pytest.raises(ValueError, match="not discovered"):
        ResearchRoundPlan.validate_plan(plan, {"https://example.com/"})


def test_completed_round_cannot_select_another_url():
    plan = ResearchRoundPlan(
        done=True,
        assignment="Sufficient evidence collected",
        next_url="https://example.com/",
    )
    with pytest.raises(ValueError, match="cannot select"):
        ResearchRoundPlan.validate_plan(plan, {"https://example.com/"})
