from nightwalker.goal_state import GoalProgressProposal, GoalStateManager


def test_goal_completion_requires_exact_quote_from_current_evidence():
    state = GoalStateManager.initialise("Compare pricing", ["Compare pricing"])
    evidence = [{
        "evidence_id": "ev-1",
        "captured_text": "The listed price is R 1,299.00 for this item.",
    }]
    proposal = GoalProgressProposal.model_validate({
        "observation": "Found a listed price.",
        "completed_with_evidence": [{
            "criterion": "Compare pricing",
            "evidence_id": "ev-1",
            "quote": "The listed price is R 1,299.00",
        }],
    })
    result = GoalStateManager.apply_proposal(state, proposal, evidence)
    assert result["completed"] == ["Compare pricing"]
    assert result["remaining"] == []


def test_goal_completion_rejects_unseen_quotes_and_unknown_criteria():
    state = GoalStateManager.initialise("Compare pricing", ["Compare pricing"])
    evidence = [{"evidence_id": "ev-1", "captured_text": "A product is available."}]
    proposal = GoalProgressProposal.model_validate({
        "completed_with_evidence": [
            {"criterion": "Compare pricing", "evidence_id": "ev-1", "quote": "R 1,299.00 pricing"},
            {"criterion": "Invented criterion", "evidence_id": "ev-1", "quote": "A product is available"},
        ],
    })
    result = GoalStateManager.apply_proposal(state, proposal, evidence)
    assert result["completed"] == []
    assert result["remaining"] == ["Compare pricing"]


def test_goal_state_keeps_original_objective_and_bounds_criteria():
    objective = "Research five competitors and compare their pricing"
    state = GoalStateManager.initialise(objective, [f"Criterion {i}" for i in range(10)])
    assert state["original_goal"] == objective
    assert len(state["criteria"]) == 6
    assert state["remaining"] == state["criteria"]
