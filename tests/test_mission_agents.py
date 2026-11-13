from nightwalker.html_skeleton import extract_html_skeleton
from nightwalker.mcp_ontology import native_tool_name, semantic_tool_view
from nightwalker.mission_verifier import MissionVerifier


class _VerifierModel:
    async def review_mission_evidence(self, **kwargs):
        return {"status": "verified", "reason": "Evidence directly supports the claim."}


def test_ontology_exposes_only_semantic_functions_for_capability():
    tools = [
        {"name": "steel_snapshot", "input_schema": {"type": "object"}},
        {"name": "steel_act", "input_schema": {"type": "object"}},
        {"name": "steel_session_create", "input_schema": {"type": "object"}},
        {"name": "unknown_tool", "input_schema": {"type": "object"}},
    ]

    view = semantic_tool_view(tools, "interact")

    assert {item["name"] for item in view} == {"inspect_page", "interact_with_element"}
    assert all(item.get("description") and item.get("concept") for item in view)
    assert native_tool_name("interact_with_element", view) == "steel_act"
    assert native_tool_name("steel_session_create", view) is None


def test_html_skeleton_keeps_structure_and_discards_script_noise():
    html = """
    <html><body><script>ignore this instruction</script>
      <header>Brand</header><nav><a href="/shop">Shop</a></nav>
      <main><h1>Living room</h1><form><input name="q" placeholder="Search">
      <button aria-label="Search">Go</button></form></main>
    </body></html>
    """

    skeleton = extract_html_skeleton(html)
    serialised = str(skeleton)

    assert any(item.get("type") == "heading" and item.get("text") == "Living room" for item in skeleton)
    assert any(item.get("type") == "control" and item.get("name") == "Search" for item in skeleton)
    assert "ignore this instruction" not in serialised
    assert any(item.get("type") == "landmark" and item.get("tag") == "main" for item in skeleton)


import pytest


@pytest.mark.asyncio
async def test_verifier_owns_separate_json_memory_and_rejects_missing_evidence(tmp_path):
    verifier = MissionVerifier(str(tmp_path))
    decision = await verifier.verify_step(
        ollama=_VerifierModel(),
        task_id="task-1",
        step_id="decider-step",
        objective="Inspect the page",
        worker_status="completed",
        worker_claim="Captured page content for inspection",
        evidence_refs=["ev-missing"],
        evidence_records=[],
    )

    assert decision["status"] == "needs_more_evidence"
    memory_path = tmp_path / "verification-memory" / "task-1.json"
    assert memory_path.exists()
    saved = memory_path.read_text(encoding="utf-8")
    assert "ev-missing" in saved
    assert "verification-memory" not in str(verifier._path("task-1").parent.parent)


@pytest.mark.asyncio
async def test_verifier_accepts_only_existing_nonempty_evidence(tmp_path):
    verifier = MissionVerifier(str(tmp_path))
    decision = await verifier.verify_step(
        ollama=_VerifierModel(),
        task_id="task-2",
        step_id="step-1",
        objective="Inspect the page",
        worker_status="completed",
        worker_claim="The page was inspected",
        evidence_refs=["ev-1"],
        evidence_records=[{"evidence_id": "ev-1", "captured_text": "Page title and visible page content"}],
    )

    assert decision["status"] == "verified"
    assert decision["evidence_refs"] == ["ev-1"]
