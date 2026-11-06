from nightwalker.ollama_client import OllamaClient


def test_website_only_clarification_is_not_essential():
    assert not OllamaClient._clarification_needs_user_input(
        "Which website or detail should I use to continue? Please provide the exact website URL."
    )


def test_general_research_without_a_website_uses_public_search():
    assert not OllamaClient._clarification_needs_user_input(
        "Which website should I use to research this topic?"
    )


def test_missing_material_scope_still_requires_clarification():
    assert OllamaClient._clarification_needs_user_input(
        "Which product model are you comparing?"
    )
    assert OllamaClient._clarification_needs_user_input(
        "What city should I use for nearby services?"
    )


def test_nonessential_generic_question_falls_back_to_search():
    assert not OllamaClient._clarification_needs_user_input(
        "Could you provide more details about what you are looking for?"
    )


def test_public_search_plan_is_a_valid_bounded_read_only_plan():
    from nightwalker.decision_contracts import DecisionPlan, DecisionStatus

    plan = OllamaClient._public_search_plan("Compare current solar panel options")
    validated = DecisionPlan.validate_plan(plan)

    assert validated.status == DecisionStatus.READY
    assert str(validated.target_url).startswith("https://www.bing.com/search?q=")
    assert [step.capability.value for step in validated.steps] == ["navigate", "extract"]
