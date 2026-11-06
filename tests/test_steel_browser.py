import asyncio

import pytest

from nightwalker.browser_runtime import BrowserRuntime


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
