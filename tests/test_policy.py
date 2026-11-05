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


def test_blocks_loopback_and_private_addresses(monkeypatch):
    def resolve_private(*args, **kwargs):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr("nightwalker.policy.socket.getaddrinfo", resolve_private)
    with pytest.raises(PolicyDenied, match="non-global"):
        PolicyEngine().validate_url("http://internal.example")


def test_blocks_nonstandard_ports():
    with pytest.raises(PolicyDenied, match="standard"):
        PolicyEngine().validate_url("https://example.com:8443")


def test_blocks_embedded_credentials():
    with pytest.raises(PolicyDenied, match="credentials"):
        PolicyEngine().validate_url("https://user:pass@example.com")
