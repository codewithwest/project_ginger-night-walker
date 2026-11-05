import json

import httpx
import pytest

from nightwalker.contracts import AgentTask, Capability
from nightwalker.ollama_client import OllamaClient, OllamaUnavailable


@pytest.mark.asyncio
async def test_agent_request_uses_remote_chat_and_validates_contract(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example.test")
    monkeypatch.setenv("OLLAMA_MODEL_DEFAULT", "unit-test-model")
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            result = {
                "task_id": "task-1",
                "capability": "extract",
                "status": "planned",
                "data": {"fields": ["name"]},
                "evidence": [],
                "issues": [],
            }
            return httpx.Response(
                200,
                json={"message": {"content": json_dumps(result)}},
                request=httpx.Request("POST", url),
            )

    def json_dumps(value):
        return json.dumps(value)

    monkeypatch.setattr("nightwalker.ollama_client.httpx.AsyncClient", FakeAsyncClient)
    task = AgentTask(
        task_id="task-1",
        capability=Capability.EXTRACT,
        objective="Extract product names",
        input_data={"dom_snapshot": "<h1>Products</h1>", "unrelated_secret": "do-not-send"},
        requirements={"fields": ["name"]},
    )
    result = await OllamaClient().run_agent(task)

    assert result.status == "planned"
    assert captured["url"] == "https://ollama.example.test/api/chat"
    assert captured["body"]["model"] == "unit-test-model"
    assert captured["body"]["stream"] is False
    assert "unrelated_secret" not in captured["body"]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_agent_rejects_mismatched_task_identity(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example.test")

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            result = {
                "task_id": "different-task",
                "capability": "extract",
                "status": "planned",
                "data": {},
                "evidence": [],
                "issues": [],
            }
            return httpx.Response(
                200,
                json={"message": {"content": __import__("json").dumps(result)}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("nightwalker.ollama_client.httpx.AsyncClient", FakeAsyncClient)
    task = AgentTask(
        task_id="task-1", capability=Capability.EXTRACT, objective="Extract fields"
    )
    with pytest.raises(OllamaUnavailable, match="did not match"):
        await OllamaClient().run_agent(task)


@pytest.mark.asyncio
async def test_missing_remote_url_fails_closed(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    with pytest.raises(OllamaUnavailable, match="not configured"):
        await OllamaClient().health()


@pytest.mark.asyncio
async def test_decider_empty_content_reports_ollama_generation_metadata(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example.test")
    monkeypatch.setenv("OLLAMA_MODEL_DECIDER", "qwen3.5:9b")

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            return httpx.Response(
                200,
                json={
                    "model": "qwen3.5:9b",
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 100,
                    "eval_count": 0,
                    "message": {"role": "assistant", "content": "", "thinking": "reasoning"},
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("nightwalker.ollama_client.httpx.AsyncClient", FakeAsyncClient)
    with pytest.raises(
        OllamaUnavailable,
        match=r"empty message.content.*done_reason='stop'.*thinking_chars=9",
    ):
        await OllamaClient().decide_task("Find the latest product catalogue")


def test_chat_content_reports_ollama_error_payload():
    with pytest.raises(OllamaUnavailable, match="model is unavailable"):
        OllamaClient._chat_content(
            {"error": "model is unavailable"},
            model="qwen3.5:9b",
            operation="Decider",
        )
