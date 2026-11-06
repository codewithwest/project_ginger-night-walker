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


def test_steel_backend_requires_api_key_at_start(monkeypatch):
    monkeypatch.setenv("BROWSER_BACKEND", "steel")
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    runtime = BrowserRuntime()

    class FakePlaywright:
        async def stop(self):
            self.stopped = True

    fake = FakePlaywright()

    class FakePlaywrightStarter:
        async def start(self):
            return fake

    monkeypatch.setattr("nightwalker.browser_runtime.async_playwright", FakePlaywrightStarter)

    import asyncio

    with pytest.raises(RuntimeError, match="STEEL_API_KEY"):
        asyncio.run(runtime.start())

    assert runtime._playwright is None
