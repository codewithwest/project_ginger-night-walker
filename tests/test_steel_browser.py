import asyncio

import pytest

from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.ollama_client import OllamaClient
from nightwalker.steel_mcp import normalise_steel_target, normalise_steel_tool_arguments

def test_browser_backend_defaults_to_local(monkeypatch):
    monkeypatch.delenv("BROWSER_BACKEND", raising=False)
    monkeypatch.delenv("STEEL_API_KEY", raising=False)

    runtime = BrowserRuntime()

    assert runtime.backend == "local"


def test_browser_backend_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("BROWSER_BACKEND", "unknown")

    with pytest.raises(ValueError, match="BROWSER_BACKEND"):
        BrowserRuntime()


def test_self_hosted_steel_backend_does_not_require_cloud_api_key(monkeypatch):
    monkeypatch.setenv("BROWSER_BACKEND", "steel")
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    runtime = BrowserRuntime()

    class FakePlaywright:
        stopped = False

        async def stop(self):
            self.stopped = True

    fake = FakePlaywright()

    class FakePlaywrightStarter:
        async def start(self):
            return fake

    monkeypatch.setattr("nightwalker.browser_runtime.async_playwright", FakePlaywrightStarter)

    asyncio.run(runtime.start())

    assert runtime._playwright is fake
    asyncio.run(runtime.close())
    assert fake.stopped


@pytest.mark.asyncio
async def test_self_hosted_steel_session_connects_without_api_key(monkeypatch):
    monkeypatch.setenv("BROWSER_BACKEND", "steel")
    monkeypatch.setenv("STEEL_API_BASE_URL", "http://steel-browser:3000/v1")
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    runtime = BrowserRuntime()
    captured = {}
    monkeypatch.setattr(
        "nightwalker.browser_runtime.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 3000))],
    )

    class FakeContext:
        async def route(self, pattern, handler):
            captured["route_pattern"] = pattern

    context = FakeContext()

    class FakeBrowser:
        def __init__(self):
            self.contexts = [context]

        def is_connected(self):
            return True

        async def close(self):
            return None

    browser = FakeBrowser()

    class FakeChromium:
        async def connect_over_cdp(self, url, timeout):
            captured["websocket_url"] = url
            return browser

    class FakePlaywright:
        chromium = FakeChromium()

    runtime._playwright = FakePlaywright()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "session-123",
                "websocketUrl": "ws://0.0.0.0:3000/",
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers, json):
            captured["api_url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("nightwalker.browser_runtime.httpx.AsyncClient", FakeAsyncClient)
    result = await runtime._guarded_context()

    assert result is context
    assert captured["api_url"] == "http://steel-browser:3000/v1/sessions"
    assert "steel-api-key" not in captured["headers"]
    assert captured["websocket_url"] == "ws://127.0.0.1:3000/"
    assert captured["route_pattern"] == "**/*"


@pytest.mark.parametrize(
    ("websocket_url", "expected"),
    [
        ("ws://0.0.0.0:3000/", "ws://10.0.0.12:3000/"),
        ("ws://[::]:3000/cdp", "ws://10.0.0.12:3000/cdp"),
        ("ws://steel-browser:9223/cdp", "ws://10.0.0.12:9223/cdp"),
        ("ws://127.0.0.1:3000/cdp", "ws://127.0.0.1:3000/cdp"),
    ],
)
def test_steel_websocket_url_uses_ip_host_header(monkeypatch, websocket_url, expected):
    monkeypatch.setenv("BROWSER_BACKEND", "steel")
    runtime = BrowserRuntime()
    monkeypatch.setattr(
        "nightwalker.browser_runtime.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.12", 3000))],
    )

    assert runtime._normalise_steel_websocket_url(
        websocket_url, "http://steel-browser:3000/v1"
    ) == expected



def test_steel_debug_url_is_host_reachable_for_compose_ui(monkeypatch):
    monkeypatch.setenv("BROWSER_BACKEND", "steel")
    runtime = BrowserRuntime()

    assert runtime._normalise_steel_debug_url(
        "http://steel-browser:3000/session/abc?token=viewer"
    ) == "http://localhost:3000/session/abc?token=viewer"
    assert runtime._normalise_steel_debug_url(
        "http://0.0.0.0:3000/session/abc"
    ) == "http://localhost:3000/session/abc"
    assert runtime._normalise_steel_debug_url(
        "http://steel-browser:3000/v1/sessions/abc/debug"
    ) == "http://localhost:3000/v1/sessions/debug"


class _ConnectedBrowser:
    def is_connected(self) -> bool:
        return True


def test_task_session_exposes_browser_activity_without_session_id(monkeypatch):
    monkeypatch.setenv("BROWSER_BACKEND", "steel")
    runtime = BrowserRuntime()
    runtime._steel_sessions["task-1"] = ("internal-session-id", _ConnectedBrowser())
    runtime._task_sessions["task-1"] = {
        "backend": "steel",
        "status": "active",
        "active": True,
        "session_id": "internal-session-id",
        "debug_url": "http://localhost:3000/session/abc",
        "activity": [{"message": "Navigated"}],
    }

    snapshot = runtime.task_session("task-1")

    assert snapshot["status"] == "active"
    assert snapshot["debug_url"] == "http://localhost:3000/session/abc"
    assert "session_id" not in snapshot


def test_bare_steel_refs_are_prefixed_with_at():
    assert normalise_steel_target("e271") == "@e271"
    assert normalise_steel_target("@e271") == "@e271"
    assert normalise_steel_target("button.remove") == "button.remove"
    assert normalise_steel_tool_arguments(
        "steel_act",
        {"action": "click", "target": "e271"},
    ) == {"action": "click", "target": "@e271"}


def test_steel_tool_proposal_recovers_trailing_garbage_json():
    raw = '{"tool_name":"steel_act","arguments":{"action":"click","target":"@e12"},"reason":"open"}\ntrailing'
    assert OllamaClient._loads_steel_tool_proposal(raw)["tool_name"] == "steel_act"
    compact = OllamaClient._compact_steel_observation(
        {
            "url": "https://example.test",
            "session_id": "abc",
            "snapshot": {"content": [{"type": "text", "text": "x" * 5000}]},
        },
        limit=100,
    )
    assert compact["snapshot_text"].endswith("…[snapshot truncated]")
    assert len(compact["snapshot_text"]) < 150


def test_steel_tool_proposal_salvages_broken_delimiter_json():
    # Small models often break mid-object (unescaped quotes / truncation).
    raw = (
        '{"tool_name":"steel_act","arguments":{"action":"click","target":"@e271",'
        '"value":"oops},"reason":"click Remove'
    )
    salvaged = OllamaClient._loads_steel_tool_proposal(raw)
    assert salvaged["tool_name"] == "steel_act"
    assert salvaged["arguments"]["action"] == "click"
    assert salvaged["arguments"]["target"] == "@e271"


def test_open_search_box_is_typed_and_submitted():
    snapshot = """
    - button "Close search" @e74
    - search "Store"
    - textbox "Search" @e73 [value=""]
    - textbox "Email Address" @e54 [value=""] [required=true] [off-screen]
    """
    assert search_follow_up_actions(snapshot, "Dark Grey") == [
        {"action": "type", "target": "@e73", "value": "Dark Grey"},
        {"action": "press", "target": "@e73", "value": "Enter"},
    ]


def test_search_follow_up_only_submits_when_the_query_is_already_typed():
    snapshot = '- textbox "Search" @e73 [value="Dark Grey"]'
    assert search_follow_up_actions(snapshot, "Dark Grey") == [
        {"action": "press", "target": "@e73", "value": "Enter"},
    ]


def test_search_follow_up_does_nothing_when_no_search_box_is_open():
    snapshot = '- button "Toggle search form" @e5\n- textbox "Email Address" @e54 [value=""] [off-screen]'
    assert search_follow_up_actions(snapshot, "Dark Grey") == []
