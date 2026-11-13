from nightwalker.cart_report import (
    apply_cart_goal_from_plan,
    build_cart_journey_report,
    cart_limitations_from_plan,
)


PLAN = {
    "steps": [
        {
            "step_id": "qa-navigation",
            "status": "completed",
            "objective": "Navigate to the exact storefront URL and capture its identity.",
            "result": "Captured Best Furniture & Homeware Store in South Africa | Weylandts",
            "evidence_refs": ["ev-nav"],
        },
        {
            "step_id": "qa-discover",
            "status": "completed",
            "objective": "Find the exact requested product.",
            "result": "Submitted search query 'Dark Grey' and captured its navigation outcome",
            "evidence_refs": ["ev-search"],
        },
        {
            "step_id": "qa-availability",
            "status": "completed",
            "objective": "Add the item if in stock.",
            "result": "Cart count is 1 after adding the item",
            "evidence_refs": ["ev-add"],
        },
        {
            "step_id": "qa-content",
            "status": "completed",
            "objective": "Verify price and quantity.",
            "result": "Captured displayed price R 15,999.00 and quantity 1",
            "evidence_refs": ["ev-line"],
        },
        {
            "step_id": "qa-validate",
            "status": "completed",
            "objective": "Remove the item.",
            "result": "Removed the item and the cart is empty",
            "evidence_refs": ["ev-remove"],
        },
        {
            "step_id": "qa-synthesise",
            "status": "completed",
            "objective": "Confirm the cart is empty.",
            "result": "Confirmed the cart is empty",
            "evidence_refs": ["ev-empty"],
        },
    ]
}


def test_cart_report_is_a_step_by_step_markdown_breakdown():
    report = build_cart_journey_report(
        'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart.',
        PLAN,
    )
    assert report.startswith("# Cart journey report")
    assert "## Step-by-step findings" in report
    assert "### 1. Navigate to the exact storefront URL" in report
    assert "Cart count is 1 after adding the item" in report
    assert "No checkout actions were initiated." in report
    assert "Crawl inspected" not in report
    assert "Deterministic audit summary" not in report


def test_cart_goal_criteria_are_reconciled_from_completed_plan_steps():
    goal_state = {
        "original_goal": "cart mission",
        "criteria": [
            "Successful navigation to the provided URL.",
            "Identification of a product with a 'Dark Grey' variant and successful addition to the cart.",
            "Verification of the item's presence in the cart with correct price and quantity.",
            "Removal of the 'Dark Grey' item from the cart.",
            "Verification that the cart is empty after removal.",
            "Ensure no checkout actions were initiated.",
        ],
        "completed": [],
        "remaining": [],
        "evidence_by_criterion": {},
    }
    apply_cart_goal_from_plan(goal_state, PLAN)
    assert goal_state["remaining"] == []
    assert len(goal_state["completed"]) == 6
    assert cart_limitations_from_plan(PLAN, goal_state) == []



def test_completed_partial_plan_is_not_reported_as_completed_journey():
    partial = {
        "steps": [
            {
                "step_id": "open-site",
                "objective": "Open the explicitly supplied website and inspect its publicly accessible pages.",
                "status": "completed",
                "result": "Navigated to https://www.ctm.co.za/ and captured page evidence",
                "evidence_refs": ["ev-open"],
            },
            {
                "step_id": "inspect-site",
                "objective": "Extract publicly visible information relevant to the user's requested research.",
                "status": "completed",
                "result": "Captured publicly visible information",
                "evidence_refs": ["ev-inspect"],
            },
        ]
    }
    goal_state = {
        "remaining": [
            "Find an in-stock product and add it to the cart.",
            "Increase the cart quantity by one.",
            "Reach and verify the checkout page.",
        ]
    }
    report = build_cart_journey_report(
        "Visit ctm.co.za, add an in-stock product, increase quantity by one and continue to checkout.",
        partial,
        goal_state=goal_state,
    )
    assert "requested shopping journey was NOT verified" in report
    assert "Find an in-stock product and add it to the cart." in report
    assert "Checkout was requested but was not reached or verified." in report
    assert "All steps in the Decider-authored plan were verified" not in report
