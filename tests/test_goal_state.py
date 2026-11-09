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


def test_cheapest_product_goal_can_be_verified_from_completed_plan_and_exact_evidence():
    state = GoalStateManager.initialise(
        "find me the cheapest product on weylandts.co.za homepage",
        ["Identify the cheapest product on the homepage"],
    )
    captured = (
        "Shop Now New Arrivals Miguel Vase Range The price depends on the options chosen "
        "on the product page R 895.00 Tuileries Garden Throw R 1,995.00 "
        "Tuileries Palace Throw R 1,995.00 Brindille Nest Basket R 2,995.00 "
        "Palm Sculpture R 5,995.00 Turtle II Sculpture R 29,995.00 "
        "Cubist Patchwork Ottoman Large Waxed Tobacco R 6,995.00 "
        "Jungle Pod Planter Range R 5,995.00"
    )
    evidence = [{"evidence_id": "ev-home", "captured_text": captured}]
    plan = {"steps": [{
        "step_id": "identify-cheapest", "objective": "Identify the cheapest product from the extracted list",
        "status": "completed", "evidence_refs": ["ev-home"],
        "result": "Lowest extracted homepage price: Miguel Vase Range — R 895.00; compared 8 priced products",
    }]}
    products = [
        {"name": "Miguel Vase Range", "price": "R 895.00", "price_value": 895},
        {"name": "Tuileries Garden Throw", "price": "R 1,995.00", "price_value": 1995},
        {"name": "Tuileries Palace Throw", "price": "R 1,995.00", "price_value": 1995},
    ]
    result = GoalStateManager.apply_verified_plan_results(
        state, plan, evidence, {"cheapest_product": products[0]}, {"products": products},
    )
    assert result["completed"] == ["Identify the cheapest product on the homepage"]
    assert result["remaining"] == []
    assert result["evidence_by_criterion"]["Identify the cheapest product on the homepage"]["evidence_id"] == "ev-home"


def test_cheapest_product_plan_bridge_rejects_non_minimum_or_missing_capture():
    state = GoalStateManager.initialise(
        "find the cheapest product", ["Identify the cheapest product"],
    )
    plan = {"steps": [{
        "objective": "Identify the cheapest product", "status": "completed",
        "evidence_refs": ["ev-1"], "result": "Lowest product price",
    }]}
    products = [
        {"name": "A", "price": "R 100.00", "price_value": 100},
        {"name": "B", "price": "R 200.00", "price_value": 200},
    ]
    evidence = [{"evidence_id": "ev-1", "captured_text": "A R 100.00 and B R 200.00"}]
    result = GoalStateManager.apply_verified_plan_results(
        state, plan, evidence, {"cheapest_product": products[1]}, {"products": products},
    )
    assert result["completed"] == []
    assert result["remaining"] == ["Identify the cheapest product"]
