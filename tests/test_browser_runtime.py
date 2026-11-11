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

def test_browser_event_preserves_action_and_plan_step_metadata():
    runtime = BrowserRuntime()

    runtime._browser_event(
        "task-events",
        "Opening the planned target page",
        url="https://example.com/",
        action="navigate",
        plan_step_id="qa-navigation",
    )

    event = runtime.task_session("task-events")["activity"][-1]
    assert event["action"] == "navigate"
    assert event["plan_step_id"] == "qa-navigation"
    assert event["url"] == "https://example.com/"


class FakeSteelBrowser:
    def __init__(self, connected=True):
        self.connected = connected

    def is_connected(self):
        return self.connected


@pytest.mark.asyncio
async def test_steel_session_is_retained_for_report_preview_then_marked_offline(monkeypatch):
    runtime = BrowserRuntime()
    context = FakeContext()
    steel = FakeSteelBrowser()
    runtime._task_contexts["task-steel"] = context
    runtime._task_pages["task-steel"] = context.page
    runtime._steel_sessions[id(context)] = ("steel-session-1", steel)
    runtime._task_sessions["task-steel"] = {
        "backend": "steel", "status": "active", "active": True,
        "session_id": "steel-session-1", "activity": [],
    }
    monkeypatch.setenv("STEEL_SESSION_RETENTION_MS", "600000")

    await runtime.close_task_session("task-steel")

    retained = runtime.task_session("task-steel")
    assert retained["status"] == "retained"
    assert retained["active"] is True
    assert context.closed is False
    assert id(context) in runtime._steel_sessions

    steel.connected = False
    offline = runtime.task_session("task-steel")
    assert offline["status"] == "offline"
    assert offline["active"] is False

def test_steel_urls_use_configured_remote_host_and_api_port(monkeypatch):
    api_base = "http://192.168.100.18:3000/v1"
    monkeypatch.setenv("STEEL_API_BASE_URL", api_base)
    monkeypatch.setenv("STEEL_VIEWER_HOST", "192.168.100.18")
    debug = BrowserRuntime._normalise_steel_debug_url(
        "http://localhost/v1/sessions/session-1/debug"
    )
    websocket = BrowserRuntime._normalise_steel_websocket_url(
        "ws://localhost:3000/v1/sessions/session-1/cdp", api_base
    )
    assert debug == "http://192.168.100.18:3000/v1/sessions/debug"
    assert websocket == "ws://192.168.100.18:3000/v1/sessions/session-1/cdp"


def test_steel_debug_url_uses_served_shared_viewer_route(monkeypatch):
    monkeypatch.setenv("STEEL_API_BASE_URL", "http://192.168.100.18:3000/v1")
    monkeypatch.setenv("STEEL_VIEWER_HOST", "192.168.100.18")
    debug = BrowserRuntime._normalise_steel_debug_url(
        "http://localhost:3000/v1/sessions/debug"
    )
    assert debug == "http://192.168.100.18:3000/v1/sessions/debug"


def test_steel_debug_url_rewrites_unserved_scoped_route(monkeypatch):
    monkeypatch.setenv("STEEL_API_BASE_URL", "http://192.168.100.18:3000/v1")
    monkeypatch.setenv("STEEL_VIEWER_HOST", "192.168.100.18")
    debug = BrowserRuntime._normalise_steel_debug_url(
        "http://localhost:3000/v1/sessions/session-abc/debug"
    )
    assert debug == "http://192.168.100.18:3000/v1/sessions/debug"



def test_qa_product_evidence_preserves_exact_price_and_source_urls():
    result = BrowserRuntime._qa_product_evidence({
        "query": "Urban Dark Grey",
        "searched_url": "https://example.com/search?q=Urban+Dark+Grey",
        "matches": [{
            "name_match": "Urban Dark Grey | Fabric Roll | 75m",
            "price": "R 1,299.00",
            "image_src": "https://cdn.example.com/urban-dark-grey.jpg",
            "image_present": True,
            "image_loaded": True,
            "product_url": "https://example.com/products/urban-dark-grey",
        }],
    }, "https://example.com/products")
    assert result == [{
        "product_name": "Urban Dark Grey | Fabric Roll | 75m",
        "query": "Urban Dark Grey",
        "price": "R 1,299.00",
        "image_url": "https://cdn.example.com/urban-dark-grey.jpg",
        "image_status": "loaded",
        "product_url": "https://example.com/products/urban-dark-grey",
        "source_page_url": "https://example.com/search?q=Urban+Dark+Grey",
        "price_source": "visible_product_card_text",
        "image_source": "rendered_product_card_image",
    }]


def test_cart_product_name_uses_explicit_quoted_item_and_ignores_url():
    objective = 'Open https://www.weylandts.co.za/products. Add “Dark Grey” to the cart. Verify price and quantity; do not checkout.'
    assert BrowserRuntime._cart_product_name(objective) == "Dark Grey"


def test_cart_product_name_returns_none_without_an_explicit_item():
    assert BrowserRuntime._cart_product_name("Add the selected item to the cart") is None



def test_restore_qa_product_search_reuses_only_the_matching_query():
    captured = {
        "query": "Dark Grey",
        "performed": True,
        "status": "matched",
        "searched_url": "https://example.com/search?q=Dark+Grey",
        "matches": [{"name_match": "Dark Grey Sofa", "price": "R 1,000", "image_src": "https://cdn.example.com/sofa.jpg"}],
    }
    session = {"qa_product_search": captured}

    restored = BrowserRuntime._restore_qa_product_search(session, "Dark Grey")
    assert restored == captured
    assert restored is not captured
    assert restored["matches"] is not captured["matches"]
    assert BrowserRuntime._restore_qa_product_search(session, "Light Grey") is None
    assert BrowserRuntime._restore_qa_product_search({}, "Dark Grey") is None
