from nightwalker.goal_state import GoalStateManager


def test_workflow_criteria_complete_only_from_completed_steps_with_captured_evidence():
    description = "Page metadata was inspected."
    state = GoalStateManager.initialise("SEO audit", [description, "Technical crawl was inspected."])
    blueprint = {
        "success_criteria": [
            {"criterion_id": "seo-metadata", "description": description},
            {"criterion_id": "seo-technical", "description": "Technical crawl was inspected."},
        ]
    }
    plan = {
        "steps": [
            {
                "step_id": "seo-page",
                "status": "completed",
                "success_criteria": ["seo-metadata"],
                "evidence_refs": ["ev-1"],
            },
            {
                "step_id": "seo-technical",
                "status": "completed",
                "success_criteria": ["seo-technical"],
                "evidence_refs": ["ev-missing"],
            },
        ]
    }
    evidence = [{"evidence_id": "ev-1", "captured_text": "Title and metadata captured."}]

    result = GoalStateManager.apply_verified_workflow_results(
        state, blueprint, plan, evidence
    )

    assert result["completed"] == [description]
    assert result["remaining"] == ["Technical crawl was inspected."]


def test_workflow_criteria_do_not_complete_from_pending_steps():
    description = "Page metadata was inspected."
    state = GoalStateManager.initialise("SEO audit", [description])
    blueprint = {
        "success_criteria": [{"criterion_id": "seo-metadata", "description": description}]
    }
    plan = {
        "steps": [{
            "step_id": "seo-page",
            "status": "in_progress",
            "success_criteria": ["seo-metadata"],
            "evidence_refs": ["ev-1"],
        }]
    }
    evidence = [{"evidence_id": "ev-1", "captured_text": "Page metadata."}]

    result = GoalStateManager.apply_verified_workflow_results(
        state, blueprint, plan, evidence
    )

    assert result["completed"] == []
    assert result["remaining"] == [description]


def test_cart_criteria_are_reconciled_against_specific_verified_steps():
    descriptions = {
        "sc-navigation": "The requested storefront is reached.",
        "sc-product-discovery": "The requested product and its add-to-cart control are identified.",
        "sc-product-addition": "The requested product is added to the cart.",
    }
    state = GoalStateManager.initialise("Cart QA", list(descriptions.values()))
    # Simulate an LLM proposal that incorrectly marked product discovery and addition
    # complete using the same generic homepage evidence as navigation.
    state["completed"] = list(descriptions.values())
    state["remaining"] = []
    state["evidence_by_criterion"] = {
        descriptions[key]: {"evidence_id": "ev-home", "verification": "model_proposal"}
        for key in descriptions
    }
    blueprint = {
        "success_criteria": [
            {"criterion_id": key, "description": value}
            for key, value in descriptions.items()
        ]
    }
    plan = {
        "steps": [{
            "step_id": "qa-navigation",
            "status": "completed",
            "success_criteria": ["sc-navigation"],
            "evidence_refs": ["ev-home"],
        }]
    }
    result = GoalStateManager.apply_verified_workflow_results(
        state, blueprint, plan,
        [{"evidence_id": "ev-home", "captured_text": "Weylandts homepage"}],
    )
    assert result["completed"] == [descriptions["sc-navigation"]]
    assert result["remaining"] == [
        descriptions["sc-product-discovery"],
        descriptions["sc-product-addition"],
    ]
    assert descriptions["sc-product-addition"] not in result["evidence_by_criterion"]
