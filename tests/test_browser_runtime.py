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

    async def evaluate(self, expression):
        return self.text[:20000]

    async def evaluate_all(self, expression):
        return []


class FakePage:
    url = "https://example.com/"

    def __init__(self):
        self.closed = False

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True

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
        self.pages = [self.page]
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

    async def new_context(task_id=None):
        runtime._task_contexts[task_id] = context
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
    assert result["schema_version"] == "1.0"
    assert result["sources"][0]["source_id"] == "s-001"
    assert result["findings"][0]["finding_id"] == "f-001"
    assert result["findings"][0]["source_ids"] == ["s-001"]
    assert result["findings"][0]["evidence_ids"] == [result["evidence_id"]]
    assert result["evidence"][0]["evidence_id"] == result["evidence_id"]
    assert not context.closed  # The task owns the browser context across research rounds.
    await runtime.close_task_session("task-1")
    assert context.closed
    assert (tmp_path / f"{result['evidence_id']}.json").exists()



def test_extract_products_from_visible_text_returns_names_and_prices_in_order():
    text = """
    Shop Now
    New Arrivals
    Press to skip carousel
    Moyo Dining Table | Rectangular
    R 31,995.00
    Baza Console | Iconic Black
    R 45,995.00
    Moyo Coffee & Side Table
    The price depends on the options chosen on the product page
    From
    R 6,995.00
    """

    products = BrowserRuntime._extract_products_from_text(text)

    assert products[:2] == [
        {"name": "Moyo Dining Table | Rectangular", "price": "R 31,995.00"},
        {"name": "Baza Console | Iconic Black", "price": "R 45,995.00"},
    ]
    assert products[2] == {
        "name": "Moyo Coffee & Side Table",
        "price": "R 6,995.00",
    }


def test_browser_runtime_extract_product_search_query():
    assert BrowserRuntime._extract_product_search_query('Search for "Blue Top"') == "Blue Top"
    assert BrowserRuntime._extract_product_search_query('Submit the search query "Dark Grey".') == "Dark Grey"
    assert BrowserRuntime._extract_product_search_query("Verify the presence of 'Blue Top' on https://example.com") == "Blue Top"
    assert BrowserRuntime._extract_product_search_query('Open "https://example.com" and check for "Blue Top"') == "Blue Top"
    assert BrowserRuntime._extract_product_search_query('Check page title and console errors on https://example.com') is None


@pytest.mark.asyncio
async def test_interact_step_executes_requested_qa_search(monkeypatch):
    runtime = BrowserRuntime()
    expected = {"query": "Dark Grey", "performed": True, "status": "matched"}
    calls = []

    async def search(page, objective, task_id=None):
        calls.append((page, objective, task_id))
        return expected

    monkeypatch.setattr(runtime, "_run_product_search_smoke", search)
    page = object()

    result, destination = await runtime._execute_interaction(
        page, 'Submit search query "Dark Grey".', "qa", task_id="task-qa-search",
    )

    assert result is expected
    assert destination is None
    assert calls == [(page, 'Submit search query "Dark Grey".', "task-qa-search")]

