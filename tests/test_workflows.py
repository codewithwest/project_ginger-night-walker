from nightwalker.workflows import (
    WorkflowType, _qa_content_objective, build_workflow_blueprint, classify_workflow,
    build_cart_journey_blueprint, build_product_search_blueprint,
    extract_product_search_query, is_ecommerce_cart_workflow,
    qa_requires_site_crawl, qa_scope_for_objective, target_url_from_objective,
)


def test_routes_seo_audit_to_seo_workflow():
    assert classify_workflow('Perform a technical SEO audit of https://example.com') == WorkflowType.SEO


def test_routes_website_functional_testing_to_qa_workflow():
    assert classify_workflow('Run functional and regression testing on https://example.com') == WorkflowType.QA


def test_routes_explicit_combined_request_to_combined_workflow():
    assert classify_workflow('Run an SEO audit and QA testing for https://example.com') == WorkflowType.SEO_QA


def test_named_subject_alone_does_not_become_shopping_or_general_research():
    assert classify_workflow('What is Velocity Knight Trainer known for?') == WorkflowType.UNSUPPORTED


def test_seo_blueprint_has_measurable_acceptance_criteria():
    blueprint = build_workflow_blueprint('SEO audit https://example.com', WorkflowType.SEO)
    assert len(blueprint.success_criteria) == 7
    assert len(blueprint.subtasks) == 7
    assert any(item.subtask_id == "seo-validate" for item in blueprint.subtasks)
    assert any(item.criterion_id == "seo-validation" for item in blueprint.success_criteria)
    assert any(item.subtask_id == "seo-crawl" for item in blueprint.subtasks)
    assert all(item.evidence_required and item.acceptance_test for item in blueprint.success_criteria)


def test_combined_blueprint_has_unique_ids_and_valid_dependencies():
    blueprint = build_workflow_blueprint('SEO and QA audit https://example.com', WorkflowType.SEO_QA)
    assert len(blueprint.success_criteria) == 14
    assert len(blueprint.subtasks) == 13
    assert len({item.criterion_id for item in blueprint.success_criteria}) == 14
    assert len({item.subtask_id for item in blueprint.subtasks}) == 13
    assert "combined-seo-validate" in [item.subtask_id for item in blueprint.subtasks]
    assert "combined-qa-validate" in [item.subtask_id for item in blueprint.subtasks]
    assert [item.subtask_id for item in blueprint.subtasks].count("combined-audit-crawl") == 1
    assert "combined-audit-crawl" in [item.subtask_id for item in blueprint.subtasks]


def test_target_url_is_extracted_without_treating_entity_names_as_urls():
    assert target_url_from_objective('Audit https://example.com/products') == 'https://example.com/products'
    assert target_url_from_objective('Audit example.com') == 'https://example.com'
    assert target_url_from_objective('What is Velocity Knight Trainer?') is None


def test_explicit_site_wide_qa_blueprint_keeps_a_dedicated_crawl_step():
    objective = 'Run full-site QA testing across every page at https://example.com'
    blueprint = build_workflow_blueprint(objective, WorkflowType.QA)
    crawl_step = next(item for item in blueprint.subtasks if item.subtask_id == "qa-crawl")
    assert crawl_step.capability.value == "navigate"
    assert crawl_step.success_criteria == ["qa-coverage"]


def test_explicit_site_wide_qa_blueprint_requires_discovered_url_coverage():
    blueprint = build_workflow_blueprint('Run a site-wide QA crawl for https://example.com', WorkflowType.QA)
    assert any(item.criterion_id == "qa-coverage" for item in blueprint.success_criteria)


def test_page_smoke_qa_defaults_to_single_requested_url():
    objective = 'Open https://example.com and check broken images and console errors'
    assert qa_scope_for_objective(objective) == "single_page"
    assert not qa_requires_site_crawl(objective)
    blueprint = build_workflow_blueprint(objective, WorkflowType.QA)
    step_ids = [item.subtask_id for item in blueprint.subtasks]
    criterion_ids = [item.criterion_id for item in blueprint.success_criteria]
    assert step_ids == ["qa-discover", "qa-availability", "qa-content", "qa-validate", "qa-synthesise"]
    assert "qa-crawl" not in step_ids
    assert "qa-coverage" not in criterion_ids
    assert "qa-page-smoke" in criterion_ids
    assert "qa-validation" in criterion_ids
    product_step = next(item for item in blueprint.subtasks if item.subtask_id == "qa-content")
    assert "interactive controls" in product_step.objective
    assert "Do not audit SEO metadata" in product_step.objective


def test_explicit_site_wide_phrasing_enables_qa_crawl():
    assert qa_scope_for_objective("Test every page on https://example.com") == "site_wide"
    assert qa_requires_site_crawl("Crawl the site at https://example.com")


def test_seo_metadata_checks_route_to_seo_not_functional_qa():
    objective = "Check the page title and H1 heading structure on https://example.com"
    assert classify_workflow(objective) == WorkflowType.SEO

def test_broken_images_and_console_errors_route_to_functional_qa():
    objective = "Check broken images and console errors on https://example.com"
    assert classify_workflow(objective) == WorkflowType.QA
    assert qa_scope_for_objective(objective) == "single_page"


def test_routes_product_search_result_verification_to_qa():
    objective = 'Open https://www.automationexercise.com/products. Search for "Blue Top". Verify the product appears with its name, image, and price. Report any issues.'
    assert classify_workflow(objective) == WorkflowType.QA
    assert qa_scope_for_objective(objective) == "single_page"


def test_product_noun_without_testing_intent_is_not_qa():
    assert classify_workflow('Tell me about Blue Top on https://example.com/products') == WorkflowType.UNSUPPORTED

def test_incidental_audit_keywords_do_not_route_general_questions_to_qa():
    prompts = [
        "What is causing bugs on this website?",
        "Tell me about the product listings on https://example.com",
        "What does the basket icon mean on this page?",
        "How are search results ranked on https://example.com?",
        "What is the quantity shown for this product?",
        "Explain the defects mentioned in this article.",
    ]
    assert all(classify_workflow(prompt) == WorkflowType.UNSUPPORTED for prompt in prompts)


def test_explicit_qa_intent_still_routes_to_qa():
    prompts = [
        "Run a QA audit on https://example.com",
        "Test the website navigation and forms on https://example.com",
        "Find bugs in the web application at https://example.com",
        "Run smoke testing on https://example.com",
    ]
    assert all(classify_workflow(prompt) == WorkflowType.QA for prompt in prompts)


def test_extract_product_search_query_matches_supported_patterns():
    assert extract_product_search_query('Search for "Blue Top"') == "Blue Top"
    assert extract_product_search_query('Submit the search query "Dark Grey".') == "Dark Grey"
    assert extract_product_search_query("Submit search query Dark Grey.") == "Dark Grey"
    assert extract_product_search_query("Verify the presence of 'Blue Top' on https://example.com") == "Blue Top"
    assert extract_product_search_query('Confirm that "Blue Top" product appears on https://example.com') == "Blue Top"
    assert extract_product_search_query('Open "https://example.com" and check for "Blue Top"') == "Blue Top"
    assert extract_product_search_query('Check page title, h1 headings and broken images on https://example.com') is None


def test_qa_content_objective_injects_extracted_product():
    obj_with_product = 'Open https://example.com/products. Search for "Blue Top". Verify the product appears.'
    content_obj = _qa_content_objective(obj_with_product)
    assert 'Search for "Blue Top" when a usable control is found' in content_obj

    obj_without_product = 'Open https://example.com and check page title and console errors'
    fallback_obj = _qa_content_objective(obj_without_product)
    assert 'Search for the requested product only when a usable control is found' in fallback_obj

    # Quoted URL must not be mistakenly extracted as the product query
    obj_with_quoted_url = 'Open "https://example.com" and check page title and h1 headings'
    url_fallback_obj = _qa_content_objective(obj_with_quoted_url)
    assert 'Search for "https://example.com"' not in url_fallback_obj
    assert 'Search for the requested product only when a usable control is found' in url_fallback_obj




def test_product_search_blueprint_requires_only_explicitly_requested_fields():
    objective = 'Search for "Blue Top" and capture its price on https://example.com/products'
    blueprint = build_product_search_blueprint(objective)
    criteria = {item.criterion_id for item in blueprint.success_criteria}
    assert "sc-product-name" in criteria
    assert "sc-product-price" in criteria
    assert "sc-product-image" not in criteria
    assert "sc-product-url" not in criteria

def test_product_search_blueprint_authorises_search_as_interaction():
    objective = 'Search for "Dark Grey" and verify its name, image and price on https://weylandts.co.za'
    blueprint = build_product_search_blueprint(objective)
    steps = {item.subtask_id: item for item in blueprint.subtasks}

    assert list(steps) == [
        "qa-navigation", "qa-availability", "qa-content",
        "qa-validate", "qa-synthesise",
    ]
    assert steps["qa-navigation"].capability.value == "navigate"
    assert steps["qa-availability"].capability.value == "interact"
    assert 'Search for "Dark Grey"' in steps["qa-availability"].objective
    assert steps["qa-availability"].depends_on == ["qa-navigation"]
    assert steps["qa-content"].capability.value == "extract"
    assert steps["qa-content"].depends_on == ["qa-availability"]
    assert steps["qa-validate"].depends_on == ["qa-content"]
    assert steps["qa-synthesise"].depends_on == ["qa-validate"]
    assert {
        item.criterion_id for item in blueprint.success_criteria
    } == {
        "sc-navigation", "sc-search-submission", "sc-product-name",
        "sc-product-image", "sc-product-price",
    }



def test_routes_ecommerce_cart_journey_to_qa_without_product_search_override():
    objective = 'Open https://www.weylandts.co.za/products. Add “Dark Grey” to the cart. Verify its price and quantity, then remove it. Confirm the cart is empty and report any problems. Do not checkout.'
    assert classify_workflow(objective) == WorkflowType.QA
    assert extract_product_search_query(objective) is None



def test_cart_detector_accepts_product_name_between_add_action_and_cart():
    prompts = [
        'Add “Dark Grey” to the cart, verify its price and quantity, then remove it.',
        "Add the Dark Grey product to my shopping cart and check the quantity.",
        "Put the 'Dark Grey' item into the cart, verify the price, then remove the item from the cart.",
    ]
    assert all(is_ecommerce_cart_workflow(prompt) for prompt in prompts)


def test_cart_detector_does_not_classify_product_research_as_cart_workflow():
    prompts = [
        'Search for "Dark Grey" and report its price and quantity on https://example.com',
        "Find Dark Grey products in the catalogue and compare prices.",
    ]
    assert not any(is_ecommerce_cart_workflow(prompt) for prompt in prompts)


def test_cart_journey_uses_deterministic_ordered_interaction_plan():
    objective = 'Open https://www.weylandts.co.za/products. Add “Dark Grey” to the cart. Verify its price and quantity, then remove it. Confirm the cart is empty and report any problems. Do not checkout.'
    assert is_ecommerce_cart_workflow(objective)
    blueprint = build_cart_journey_blueprint(objective)
    steps = {item.subtask_id: item for item in blueprint.subtasks}
    assert list(steps) == [
        "qa-navigation", "qa-discover", "qa-availability",
        "qa-content", "qa-validate", "qa-synthesise",
    ]
    assert steps["qa-discover"].capability.value == "interact"
    assert steps["qa-availability"].capability.value == "interact"
    assert steps["qa-validate"].capability.value == "interact"
    assert steps["qa-availability"].depends_on == ["qa-discover"]
    assert steps["qa-content"].depends_on == ["qa-availability"]
    assert steps["qa-validate"].depends_on == ["qa-content"]
    assert steps["qa-synthesise"].depends_on == ["qa-validate"]
    assert "checkout" in steps["qa-synthesise"].objective.lower()
    assert len(blueprint.success_criteria) == 6
