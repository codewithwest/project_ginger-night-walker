"""Coding-specialized parameter generator. No free choice of unrelated tools."""
import json
from .structured import parse_object
from .validation import validate_arguments, enforce_navigation_policy, enforce_grounded_reference

SYSTEM = ("You are a precise MCP FUNCTION ARGUMENT generator, not a task planner. "
          "Goal and action are authoritative. Use only the specified function signature. "
          "Ground any element ref in the current page snapshot. Do not invent refs, URLs, or data. "
          "Return only a JSON object containing function arguments. No markdown, no explanation.")

class CodingToolGenerator:
    def __init__(self, model): self.model = model

    def generate(self, state, action, tool, snapshot, feedback=""):
        context = {"original_goal": state.original_goal, "criteria": state.criteria,
                   "completed": state.completed, "remaining": state.remaining,
                   "current_observation": state.observation, "selected_action": action,
                   "tool": {"name":tool["name"], "description":tool.get("description", "")[:500],
                            "inputSchema":tool.get("inputSchema", {})},
                   "browser_snapshot": snapshot[-9000:], "validation_feedback": feedback}
        raw = self.model.generate(SYSTEM, json.dumps(context, ensure_ascii=False), max_tokens=300)
        args = parse_object(raw)
        validate_arguments(args, tool.get("inputSchema", {}))
        enforce_navigation_policy(tool["name"], args)
        enforce_grounded_reference(tool["name"], args, snapshot)
        return args, raw
