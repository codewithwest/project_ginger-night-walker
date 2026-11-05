from nightwalker.context_compiler import ContextCompiler
from nightwalker.contracts import AgentTask, Capability


def test_extraction_context_is_allowlisted():
    task = AgentTask(
        task_id="t1", capability=Capability.EXTRACT, objective="Extract products",
        input_data={"dom_snapshot": "<main/>", "url": "https://example.com",
                    "conversation": "must not leak", "browser_cookie": "secret"},
        requirements={"fields": ["name", "price"], "output_schema": "ProductList",
                      "user_profile": {"email": "private"}},
    )
    context = ContextCompiler().compile(task)
    assert context.input_data == {"dom_snapshot": "<main/>", "url": "https://example.com"}
    assert context.requirements == {"fields": ["name", "price"], "output_schema": "ProductList"}
    assert context.permissions == frozenset()


def test_navigation_context_does_not_receive_extraction_input():
    task = AgentTask(
        task_id="t2", capability=Capability.NAVIGATE, objective="Go to results",
        input_data={"url": "https://example.com", "dom_snapshot": "large dom"},
        requirements={"timeout_ms": 1000, "fields": ["price"]},
    )
    context = ContextCompiler().compile(task)
    assert context.input_data == {"url": "https://example.com"}
    assert context.requirements == {"timeout_ms": 1000}
