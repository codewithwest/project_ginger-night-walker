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



def test_round_plan_accepts_http_url_canonicalisation():
    plan = ResearchRoundPlan(
        done=False,
        next_url="https://EXAMPLE.com:443/research/",
        assignment="Inspect the discovered page",
    )
    validated = ResearchRoundPlan.validate_plan(
        plan, {"https://example.com/research"}
    )
    assert validated.next_url is not None


def test_round_plan_rejects_different_path_or_query():
    plan = ResearchRoundPlan(
        done=False,
        next_url="https://example.com/research?other=1",
        assignment="Inspect another page",
    )
    with pytest.raises(ValueError, match="not discovered"):
        ResearchRoundPlan.validate_plan(
            plan, {"https://example.com/research?allowed=1"}
        )


def test_round_plan_rejects_credentials_even_if_host_matches():
    plan = ResearchRoundPlan(
        done=False,
        next_url="https://user:pass@example.com/research",
        assignment="Inspect a page",
    )
    with pytest.raises(ValueError, match="without credentials"):
        ResearchRoundPlan.validate_plan(
            plan, {"https://example.com/research"}
        )
