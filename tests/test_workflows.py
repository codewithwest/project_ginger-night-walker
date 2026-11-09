from nightwalker.workflows import WorkflowType, build_workflow_blueprint, classify_workflow, target_url_from_objective


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
    assert len(blueprint.success_criteria) == 11
    assert len(blueprint.subtasks) == 11
    assert len({item.criterion_id for item in blueprint.success_criteria}) == 11
    assert len({item.subtask_id for item in blueprint.subtasks}) == 11
    assert [item.subtask_id for item in blueprint.subtasks].count("combined-audit-crawl") == 1
    assert "combined-audit-crawl" in [item.subtask_id for item in blueprint.subtasks]


def test_target_url_is_extracted_without_treating_entity_names_as_urls():
    assert target_url_from_objective('Audit https://example.com/products') == 'https://example.com/products'
    assert target_url_from_objective('Audit example.com') == 'https://example.com'
    assert target_url_from_objective('What is Velocity Knight Trainer?') is None
