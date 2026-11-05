import pytest

from nightwalker.policy import DEFAULT_MANIFESTS, PolicyDenied, PolicyEngine


def test_default_deny_for_ungranted_capability():
    with pytest.raises(PolicyDenied):
        PolicyEngine().authorize(DEFAULT_MANIFESTS["extraction"], "browser.navigate")


def test_extraction_can_read_dom():
    PolicyEngine().authorize(DEFAULT_MANIFESTS["extraction"], "browser.read_dom")


def test_blocks_non_http_protocol():
    with pytest.raises(PolicyDenied):
        PolicyEngine().validate_url("file:///etc/passwd")
