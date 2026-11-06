import pytest

from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.ollama_client import OllamaClient


class FakeTab:
    def __init__(self, url: str, title: str) -> None:
        self.url = url
        self._title = title
        self.closed = False

    def is_closed(self):
        return self.closed

    async def title(self):
        return self._title

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


@pytest.mark.asyncio
async def test_tab_inventory_protects_primary_and_ai_cleanup_closes_selected_extras():
    runtime = BrowserRuntime()
    primary = FakeTab("https://retailer.co.za/", "Furniture retailer")
    popup = FakeTab("https://ads.example/", "Special offer")
    useful = FakeTab("https://retailer.co.za/sofas", "Sofas")
    runtime._task_contexts["task-1"] = FakeContext([primary, popup, useful])
    runtime._task_pages["task-1"] = primary

    inventory = await runtime.tab_inventory("task-1")
    assert [tab["index"] for tab in inventory] == [0, 1, 2]
    assert inventory[0]["primary"] is True
    assert inventory[1]["title"] == "Special offer"

    result = await runtime.cleanup_task_tabs("task-1", [0, 1], "Close the ad popup")

    assert result["closed"] == 1
    assert result["remaining"] == 2
    assert primary.closed is False
    assert popup.closed is True
    assert useful.closed is False


@pytest.mark.asyncio
async def test_tab_cleanup_enforces_hard_cap_even_when_ai_keeps_every_extra(monkeypatch):
    monkeypatch.setenv("BROWSER_MAX_TABS", "3")
    runtime = BrowserRuntime()
    pages = [FakeTab(f"https://example.com/{i}", f"Tab {i}") for i in range(5)]
    runtime._task_contexts["task-2"] = FakeContext(pages)
    runtime._task_pages["task-2"] = pages[0]

    result = await runtime.cleanup_task_tabs("task-2", [], "AI kept the useful tabs")

    assert result["closed"] == 2
    assert result["remaining"] == 3
    assert pages[0].closed is False
    assert pages[1].closed is False
    assert pages[2].closed is False
    assert pages[3].closed is True
    assert pages[4].closed is True


@pytest.mark.asyncio
async def test_light_model_tab_janitor_validates_indices_and_never_returns_primary(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test")
    monkeypatch.setenv("OLLAMA_MODEL_LIGHT", "qwen3.5:4b")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "content": (
                        '{"close_indices":[1,0,9,1,true],'
                        '"reason":"Close the ad and duplicate tabs"}'
                    )
                }
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResponse()

    monkeypatch.setattr("nightwalker.ollama_client.httpx.AsyncClient", FakeAsyncClient)
    result = await OllamaClient().cleanup_browser_tabs(
        "Research South African furniture retailers",
        [
            {"index": 0, "primary": True, "url": "https://retailer.co.za", "title": "Retailer"},
            {"index": 1, "primary": False, "url": "https://ads.example", "title": "Ad"},
            {"index": 2, "primary": False, "url": "https://retailer.co.za/sofas", "title": "Sofas"},
        ],
        task_id="task-3",
        round_number=4,
    )

    assert result == {
        "close_indices": [1],
        "reason": "Close the ad and duplicate tabs",
    }
    assert captured["body"]["model"] == "qwen3.5:4b"
    assert captured["headers"]["X-Ginger-Agent"] == "browser-tab-janitor"
    assert captured["headers"]["X-Ginger-Research-Round"] == "4"
    assert captured["body"]["format"]["additionalProperties"] is False
