from nightwalker.workflows import (
    WorkflowType, build_workflow_blueprint, classify_workflow,
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
    assert len(blueprint.success_criteria) == 6
    assert len(blueprint.subtasks) == 6
    assert any(item.subtask_id == "seo-crawl" for item in blueprint.subtasks)
    assert all(item.evidence_required and item.acceptance_test for item in blueprint.success_criteria)


def test_combined_blueprint_has_unique_ids_and_valid_dependencies():
    blueprint = build_workflow_blueprint('SEO and QA audit https://example.com', WorkflowType.SEO_QA)
    assert len(blueprint.success_criteria) == 12
    assert len(blueprint.subtasks) == 11
    assert len({item.criterion_id for item in blueprint.success_criteria}) == 12
    assert len({item.subtask_id for item in blueprint.subtasks}) == 11
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
    objective = 'Open https://example.com and check page title, H1 headings, broken images and console errors'
    assert qa_scope_for_objective(objective) == "single_page"
    assert not qa_requires_site_crawl(objective)
    blueprint = build_workflow_blueprint(objective, WorkflowType.QA)
    step_ids = [item.subtask_id for item in blueprint.subtasks]
    criterion_ids = [item.criterion_id for item in blueprint.success_criteria]
    assert step_ids == ["qa-discover", "qa-availability", "qa-content", "qa-synthesise"]
    assert "qa-crawl" not in step_ids
    assert "qa-coverage" not in criterion_ids
    assert "qa-page-smoke" in criterion_ids


def test_explicit_site_wide_phrasing_enables_qa_crawl():
    assert qa_scope_for_objective("Test every page on https://example.com") == "site_wide"
    assert qa_requires_site_crawl("Crawl the site at https://example.com")


def test_page_title_image_and_console_smoke_request_routes_to_qa():
    objective = "Check the page title, H1 headings, broken images and console errors on https://example.com"
    assert classify_workflow(objective) == WorkflowType.QA
    assert qa_scope_for_objective(objective) == "single_page"
