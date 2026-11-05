import pytest

from nightwalker.browser_runtime import BrowserRuntime
from nightwalker.evidence import EvidenceStore


class FakeResponse:
    status = 200


class FakeLocator:
    def __init__(self, text="Visible content") -> None:
        self.text = text
        self.first = self

    async def count(self):
        return 0

    async def inner_text(self, timeout=None):
        return self.text

    async def all_text_contents(self):
        return ["Heading"]


class FakePage:
    url = "https://example.com/"

    async def goto(self, url, **kwargs):
        self.url = url
        return FakeResponse()

    async def title(self):
        return "Example"

    def locator(self, selector):
        if selector == "h1, h2":
            return FakeLocator()
        return FakeLocator("Visible content")


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_run_plan_uses_task_context_and_persists_evidence(tmp_path, monkeypatch):
    runtime = BrowserRuntime()
    context = FakeContext()
    runtime.evidence = EvidenceStore(str(tmp_path))
    monkeypatch.setattr(runtime.policy, "validate_url", lambda *args, **kwargs: None)

    async def new_context():
        return context

    monkeypatch.setattr(runtime, "_guarded_context", new_context)
    result = await runtime.run_plan(
        task_id="task-1",
        url="https://example.com/",
        steps=[
            {"capability": "navigate", "objective": "Open the site"},
            {"capability": "extract", "objective": "Read the page"},
        ],
        objective="Read the example page",
    )

    assert result["status"] == "completed"
    assert result["title"] == "Example"
    assert result["text"] == "Visible content"
    assert result["evidence_id"].startswith("ev-")
    assert context.closed
    assert (tmp_path / f"{result['evidence_id']}.json").exists()
