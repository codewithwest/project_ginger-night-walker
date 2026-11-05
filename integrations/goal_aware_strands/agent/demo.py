"""Explicit scripted test doubles. These do not pretend to be actual model inference."""
from .goal_state import GoalStateManager
from .structured import parse_object

class FakeStateModel:
    def generate(self, system, user, **kwargs):
        if "success criteria" in system:
            return '{"criteria":["Open the Python downloads page"],"remaining":["Open the Python downloads page"]}'
        info = parse_object(user)
        content = str(info.get("actual_tool_result", {}).get("content", ""))
        done = "python.org/downloads/" in content
        if done:
            return '{"observation":"Downloads page opened","progress":true,"completed":["Open the Python downloads page"],"remaining":[],"goal_complete":true}'
        return '{"observation":"Browser is not yet on the downloads page","progress":true,"completed":[],"remaining":["Open the Python downloads page"],"goal_complete":false}'

class FakeDecider:
    def choose(self, goal, state, actions):
        if state["completed"]: action = "finish"
        elif "python.org" not in state["page_snapshot"]: action = "navigate"
        elif "ref=e13" in state["page_snapshot"]: action = "click"
        else: action = "read_page"
        return {"action":action, "confidence":1.0, "source":"scripted_test_double"}

class FakeCoder:
    def generate(self, state, action, tool, snapshot, feedback=""):
        if action == "navigate": return {"url":"https://www.python.org"}, "scripted"
        if action == "click": return {"ref":"e13", "element":"Downloads"}, "scripted"
        return {}, "scripted"
