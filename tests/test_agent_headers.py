from nightwalker.agent_headers import agent_headers
from nightwalker.ollama_client import OllamaClient


def test_agent_headers_identify_agent_capability_and_trace_context():
    headers = agent_headers(
        agent="research-decider",
        capability="research-planning",
        operation="choose-source",
        model="qwen3.5:9b",
        task_id="task-123",
        round_number=4,
    )
    assert headers == {
        "X-Ginger-Agent": "research-decider",
        "X-Ginger-Capability": "research-planning",
        "X-Ginger-Operation": "choose-source",
        "X-Ginger-Integration": "night-walker",
        "X-Ginger-Model": "qwen3.5:9b",
        "X-Ginger-Task-Id": "task-123",
        "X-Ginger-Research-Round": "4",
    }


def test_agent_headers_strip_newlines_and_bound_values():
    headers = agent_headers(agent="agent\r\nforged: yes", capability="extract", operation="run")
    assert "\\r" not in headers["X-Ginger-Agent"]
    assert "\\n" not in headers["X-Ginger-Agent"]
    assert len(headers["X-Ginger-Agent"]) <= 200


def test_ollama_headers_include_auth_and_identity(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "secret-token")
    headers = OllamaClient()._headers(
        agent="extract-agent", capability="extract", operation="extract-evidence",
        model="qwen3.5:9b", task_id="task-abc",
    )
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["X-Ginger-Agent"] == "extract-agent"
    assert headers["X-Ginger-Task-Id"] == "task-abc"
