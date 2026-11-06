from nightwalker.ollama_client import OllamaClient, OllamaUnavailable


def test_role_endpoint_overrides_global_endpoint(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://shared.example")
    monkeypatch.setenv("OLLAMA_BASE_URL_GOAL_STATE", "https://state.example/")
    client = OllamaClient()

    assert client._require_base_url("GOAL_STATE") == "https://state.example"
    assert client._require_base_url("DECIDER") == "https://shared.example"


def test_role_endpoint_falls_back_to_global(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.internal:11434")
    monkeypatch.delenv("OLLAMA_BASE_URL_SYNTHESIS", raising=False)

    assert OllamaClient()._require_base_url("SYNTHESIS") == "http://ollama.internal:11434"


def test_role_endpoint_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://shared.example")
    monkeypatch.setenv("OLLAMA_BASE_URL_EXTRACT", "file:///tmp/model")

    try:
        OllamaClient()._require_base_url("EXTRACT")
    except OllamaUnavailable as exc:
        assert "HTTP or HTTPS" in str(exc)
    else:
        raise AssertionError("Invalid endpoint scheme must be rejected")
