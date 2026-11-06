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
