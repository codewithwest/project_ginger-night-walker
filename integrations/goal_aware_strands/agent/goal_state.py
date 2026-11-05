"""Qwen 0.6B proposes goal criteria and state changes; Python owns durable state."""
import json
from dataclasses import dataclass, field, asdict
from .structured import parse_object, strict_bool, short_string


def usable_criterion(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().strip(". -_") != "" and value.strip().lower() not in ("none", "n/a", "todo", "placeholder")

@dataclass
class GoalState:
    original_goal: str
    criteria: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    observation: str = ""
    evidence: list[str] = field(default_factory=list)
    last_error: str = ""
    proposed_complete: bool = False
    def snapshot(self): return asdict(self)

class GoalStateManager:
    INIT_SYSTEM = ("You are a tiny task-state interpreter. Extract 1-5 concrete success criteria from user objective. "
                   "Do not change the user's objective. Respond with one JSON object: "
                   '{"criteria":["..."],"remaining":["..."]}. Keep each item short.')
    UPDATE_SYSTEM = ("You are a tiny observer, not a tool caller. Assess only evidence from ACTUAL tool results. "
                     "Respond compactly with JSON keys: observation (short string), progress (true/false), "
                     "completed (array of criteria that have evidence), remaining (array), goal_complete (true/false). "
                     "Never claim completion without evidence. Do not invent unseen information.")

    def __init__(self, model): self.model = model

    def initialize(self, goal):
        raw = self.model.generate(self.INIT_SYSTEM, json.dumps({"goal": goal}), max_tokens=260)
        data = parse_object(raw)
        criteria = [short_string(v, 160) for v in data.get("criteria", []) if usable_criterion(v)][:5] if isinstance(data.get("criteria"), list) else []
        if not criteria: criteria = [goal[:160]]
        remaining = [short_string(v, 160) for v in data.get("remaining", []) if usable_criterion(v)][:5] if isinstance(data.get("remaining"), list) else []
        return GoalState(original_goal=goal, criteria=criteria, remaining=remaining or criteria.copy())

    def update(self, state, action, result):
        raw = self.model.generate(self.UPDATE_SYSTEM, json.dumps({"original_goal": state.original_goal,
                "criteria": state.criteria, "completed": state.completed, "previous_observation": state.observation,
                "action": action, "actual_tool_result": result}, ensure_ascii=False)[:13000], max_tokens=320)
        parsed = parse_object(raw)
        state.observation = short_string(parsed.get("observation") or str(result), 500)
        supported = result.get("is_error") is False and bool(result.get("content"))
        if supported:
            completed = parsed.get("completed", [])
            if isinstance(completed, list):
                # Only accept exact known criteria and always retain an auditable evidence entry.
                for item in completed:
                    if isinstance(item, str) and item in state.criteria and item not in state.completed:
                        state.completed.append(item)
                        state.evidence.append(f"{item}: {str(result.get('content'))[:400]}")
        state.remaining = [c for c in state.criteria if c not in state.completed]
        state.proposed_complete = strict_bool(parsed.get("goal_complete")) is True
        state.last_error = str(result.get("content", ""))[:400] if result.get("is_error") else ""
        return {"raw": raw, "parsed": parsed, "progress": strict_bool(parsed.get("progress")) is True,
                "proposed_complete": state.proposed_complete, "remaining": state.remaining}
