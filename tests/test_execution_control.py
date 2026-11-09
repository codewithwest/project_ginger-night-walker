from nightwalker.execution_control import ActionVerifier, DEFAULT_TOOL_REGISTRY, ElementResolver


def test_tool_registry_rejects_unknown_tools_and_extra_arguments():
    assert "browser.navigate" in DEFAULT_TOOL_REGISTRY.names_for("seo")
    try:
        DEFAULT_TOOL_REGISTRY.validate_call("browser.delete", {}, "seo")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown tool was accepted")
    try:
        DEFAULT_TOOL_REGISTRY.validate_call("browser.capture_page", {"max_chars": 1000, "submit": True}, "qa")
    except Exception:
        pass
    else:
        raise AssertionError("extra argument was accepted")


def test_tool_registry_enforces_workflow_scope():
    try:
        DEFAULT_TOOL_REGISTRY.validate_call("browser.capture_page", {}, "unsupported")
    except PermissionError:
        pass
    else:
        raise AssertionError("tool escaped workflow scope")


def test_element_resolver_requires_unique_grounded_match():
    resolver = ElementResolver()
    ambiguous = resolver.resolve([
        {"ref": "node-1", "role": "link", "name": "Contact us"},
        {"ref": "node-2", "role": "link", "name": "Contact us"},
    ], "Contact us", role="link")
    assert ambiguous.status == "ambiguous" and ambiguous.ref is None
    resolved = resolver.resolve([{"ref": "node-3", "role": "link", "name": "Contact us"}], "Navigate to the contact page", role="link")
    assert resolved.status == "resolved" and resolved.ref == "node-3"


def test_element_resolver_never_invents_refs():
    result = ElementResolver().resolve([{"role": "button", "name": "Continue"}], "Continue")
    assert result.status == "no_match" and result.ref is None


def test_action_verifier_requires_persisted_evidence():
    verifier = ActionVerifier()
    missing = verifier.verify_workflow_result({"status": "completed", "evidence_id": "ev-1", "evidence": []})
    assert missing["status"] == "failed"
    valid = verifier.verify_workflow_result({"status": "completed", "evidence_id": "ev-1", "evidence": [{"evidence_id": "ev-1"}]})
    assert valid["status"] == "verified"
