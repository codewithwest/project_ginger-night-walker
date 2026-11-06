from nightwalker.goal_state import GoalProgressProposal, GoalStateManager


def test_goal_completion_requires_substantive_relevant_exact_quote():
    state = GoalStateManager.initialise(
        "Compare pricing", ["Compare listed product pricing"]
    )
    captured = (
        "The product pricing page lists the standard subscription at 129 dollars per month. "
        "The annual subscription costs 1200 dollars, which represents a discount compared "
        "with twelve monthly payments. The page identifies billing frequency and the "
        "available subscription plans in its pricing table."
    )
    evidence = [{"evidence_id": "ev-1", "captured_text": captured}]
    proposal = GoalProgressProposal.model_validate({
        "observation": "Found substantive pricing information.",
        "completed_with_evidence": [{
            "criterion": "Compare listed product pricing",
            "evidence_id": "ev-1",
            "quote": "The product pricing page lists the standard subscription at 129 dollars per month.",
        }],
    })
    result = GoalStateManager.apply_proposal(state, proposal, evidence)
    assert result["completed"] == ["Compare listed product pricing"]


def test_goal_completion_rejects_search_chrome_and_unrelated_content():
    state = GoalStateManager.initialise(
        "Identify technical features", ["Identify technical features"]
    )
    proposal = GoalProgressProposal.model_validate({
        "completed_with_evidence": [{
            "criterion": "Identify technical features",
            "evidence_id": "ev-search",
            "quote": "Images Videos Shopping Maps News",
        }],
    })
    result = GoalStateManager.apply_proposal(state, proposal, [{
        "evidence_id": "ev-search",
        "captured_text": "Images Videos Shopping Maps News",
    }])
    assert result["completed"] == []
    assert result["remaining"] == ["Identify technical features"]


def test_one_evidence_record_cannot_verify_multiple_distinct_criteria():
    state = GoalStateManager.initialise(
        "Research technical features and progression rules",
        ["Identify technical features", "Establish progression rules"],
    )
    text = (
        "The game features weapon upgrades, stamina management, armour crafting, "
        "and a skill tree. Players unlock advanced abilities by completing quests "
        "and defeating bosses, earning experience points for each completed challenge."
    )
    evidence = [{"evidence_id": "ev-one", "captured_text": text}]
    proposal = GoalProgressProposal.model_validate({
        "completed_with_evidence": [
            {"criterion": "Identify technical features", "evidence_id": "ev-one",
             "quote": "The game features weapon upgrades, stamina management, armour crafting"},
            {"criterion": "Establish progression rules", "evidence_id": "ev-one",
             "quote": "Players unlock advanced abilities by completing quests"},
        ],
    })
    result = GoalStateManager.apply_proposal(state, proposal, evidence)
    assert len(result["completed"]) == 1


def test_goal_state_keeps_original_objective_and_supports_twelve_criteria():
    objective = "Research five competitors and compare their pricing"
    state = GoalStateManager.initialise(objective, [f"Criterion {i}" for i in range(10)])
    assert state["original_goal"] == objective
    assert len(state["criteria"]) == 10
    assert state["remaining"] == state["criteria"]
