import json
import pytest
from agent.config import Config
from agent.demo import FakeCoder, FakeDecider, FakeStateModel
from agent.goal_state import GoalStateManager
from agent.models import StrandsDecider
from agent.coder import CodingToolGenerator
from agent.runner import AgentRunner
from agent.tools import MockBrowser
from agent.structured import parse_object, strict_bool
from agent.validation import ValidationError, validate_arguments, enforce_grounded_reference


def test_demo_end_to_end():
    with MockBrowser() as tools:
        result = AgentRunner(GoalStateManager(FakeStateModel()), FakeDecider(), FakeCoder(), tools, Config()).run("Visit python.org and open Downloads")
    assert result["status"] == "completed"
    assert len(result["steps"]) == 3
    assert result["final_state"]["evidence"]


def test_parse_relaxed_json():
    assert parse_object('some text ```json\n{"ok":true}\n```')["ok"] is True
    assert parse_object("garbage") == {}
    assert strict_bool("maybe") is None


def test_wrong_args():
    with pytest.raises(ValidationError):
        validate_arguments({"url":8}, {"type":"object","properties":{"url":{"type":"string"}}, "required":["url"]})
    with pytest.raises(ValidationError):
        validate_arguments({"url":"x","evil":1}, {"type":"object","properties":{"url":{"type":"string"}}, "additionalProperties":False})


def test_grounding():
    with pytest.raises(ValidationError): enforce_grounded_reference("browser_click", {"ref":"e99"}, "Downloads [ref=e13]")
    enforce_grounded_reference("browser_click", {"ref":"e13"}, "Downloads [ref=e13]")


def test_decider_response_variants(monkeypatch):
    import agent.models as m
    monkeypatch.setattr(m, "post_json", lambda *a, **k:{"model":"strands-decider-2B-qwen3.5-v1-2610", "answers":{"next_action":{"type":"choice","choice":"click", "confidence":0.8,"probabilities":{"click":0.85,"navigate":0.15}}}})
    assert StrandsDecider("http://localhost:8021").choose("task",{},["click","navigate"])["action"] == "click"


def test_coder_receives_goal_and_schema():
    class Capture:
        def generate(self, system, user, **kw):
            self.context = json.loads(user)
            return '{"ref":"e13","element":"Downloads"}'
    from agent.goal_state import GoalState
    mdl = Capture()
    state = GoalState("Go to Downloads", ["Open Downloads"], remaining=["Open Downloads"])
    tool = MockBrowser().list_tools()[2]
    args, _ = CodingToolGenerator(mdl).generate(state,"click",tool,"link Downloads [ref=e13]")
    assert args["ref"] == "e13"
    assert mdl.context["original_goal"] == "Go to Downloads"
    assert mdl.context["tool"]["inputSchema"]


def test_finish_without_evidence_rejected():
    class Premature:
        def choose(self, goal, state, actions): return {"action":"finish", "confidence":1}
    with MockBrowser() as tools:
        result = AgentRunner(GoalStateManager(FakeStateModel()), Premature(), FakeCoder(), tools, Config(max_steps=2)).run("Open Downloads")
    assert result["status"] == "step_limit"
    assert "Finish rejected" in result["steps"][0]["error"]


def test_strands_uses_official_systemone_payload(monkeypatch):
    import agent.models as m
    captured = {}
    def fake_post(url, payload, **kwargs):
        captured.update(url=url, payload=payload, headers=kwargs.get("headers"))
        return {"answers":{"next_action":{"type":"choice","choice":"read_page","confidence":0.6}}}
    monkeypatch.setattr(m, "post_json", fake_post)
    out = StrandsDecider("http://127.0.0.1:8021").choose("Read page",{"page_snapshot":""},["read_page","finish"])
    assert out["action"] == "read_page"
    assert captured["url"] == "http://127.0.0.1:8021/v1/systemone"
    assert captured["payload"]["questions"]["next_action"]["type"] == "choice"
    assert "original_goal" in captured["payload"]["state"]
    assert captured["headers"]["X-Ginger-Agent"] == "strands-decider"
    assert captured["headers"]["X-Ginger-Capability"] == "action-selection"


def test_ollama_native_chat_payload(monkeypatch):
    import agent.models as m
    captured = {}
    def fake_post(url, payload, **kwargs):
        captured.update(url=url, payload=payload, headers=kwargs.get("headers"))
        return {"message":{"role":"assistant","content":"{\"criteria\":[\"done\"]}"},"done":True}
    monkeypatch.setattr(m, "post_json", fake_post)
    response = m.ChatModel("http://127.0.0.1:11434", "qwen3:0.6b").generate("sys", "task", 200)
    assert response.startswith("{")
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["think"] is False
    assert captured["payload"]["options"]["num_predict"] == 200
    assert captured["headers"]["X-Ginger-Agent"] == "goal-state"
    assert captured["headers"]["X-Ginger-Integration"] == "goal-aware-strands"


def test_placeholder_goal_is_rejected():
    class Placeholder:
        def generate(self, *args, **kwargs):
            return '{"criteria":["..."],"remaining":["..."]}'
    state = GoalStateManager(Placeholder()).initialize("Visit python.org and open Downloads")
    assert state.criteria == ["Visit python.org and open Downloads"]
    assert state.remaining == state.criteria

def test_failed_stage_in_trace():
    class OfflineDecider:
        def choose(self, *args):
            raise ConnectionError("Strands server unavailable")
    with MockBrowser() as tools:
        report = AgentRunner(GoalStateManager(FakeStateModel()), OfflineDecider(), FakeCoder(), tools, Config()).run("Open Downloads")
    assert report["status"] == "error"
    assert report["steps"][0]["failed_stage"] == "strands_decision"
    assert "server unavailable" in report["steps"][0]["error"]
