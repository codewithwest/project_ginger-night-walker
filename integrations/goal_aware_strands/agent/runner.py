"""Execution loop with explicit goal conditioning, audit logs, and repeat protection."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .config import ACTIONS
from .validation import ValidationError

class AgentRunner:
    def __init__(self, manager, decider, coder, tools, config):
        self.manager, self.decider, self.coder, self.tools, self.config = manager, decider, coder, tools, config

    def run(self, goal, trace_path=None):
        task_id = str(uuid4())
        for model in (getattr(self.manager, "model", None), getattr(self.coder, "model", None), self.decider):
            if hasattr(model, "set_context"):
                model.set_context(task_id=task_id, step=0)
        state = self.manager.initialize(goal)
        events = []
        snapshot = ""
        last_signature, repeats = None, 0
        tool_specs = {t["name"]:t for t in self.tools.list_tools()}
        status = "step_limit"
        stage = "initialization"
        for step in range(1, self.config.max_steps + 1):
            for model in (getattr(self.manager, "model", None), getattr(self.coder, "model", None), self.decider):
                if hasattr(model, "set_context"):
                    model.set_context(task_id=task_id, step=step)
            record = {"step":step,"time":datetime.now(timezone.utc).isoformat(),
                      "state_before":state.snapshot()}
            try:
                stage = "strands_decision"
                choice = self.decider.choose(state.original_goal, {
                    "goal":state.original_goal, "criteria":state.criteria, "completed":state.completed,
                    "remaining":state.remaining,"observation":state.observation,
                    "last_error":state.last_error,"page_snapshot":snapshot[-6000:]}, ACTIONS)
                action = choice["action"]
                record["decision"] = choice
                if action == "blocked":
                    status = "blocked"; record["error"] = "Decider blocked"; events.append(record); break
                if action == "finish":
                    # Never trust a finish classification in the absence of checked criteria and evidence.
                    if state.criteria and len(state.completed) == len(state.criteria) and state.evidence:
                        status = "completed"; events.append(record); break
                    record["error"] = "Finish rejected: success criteria lack evidence"
                    state.last_error = record["error"]
                    events.append(record)
                    continue
                candidates = [tool_specs[name] for name in self.config.action_tools.get(action, []) if name in tool_specs]
                if not candidates:
                    record["error"] = f"No MCP tool mapped/discovered for {action}"
                    state.last_error = record["error"]
                    events.append(record); continue
                tool = candidates[0]
                stage = "coding_model_arguments"
                try:
                    args, raw = self.coder.generate(state, action, tool, snapshot)
                except ValidationError as exc:
                    args, raw = self.coder.generate(state, action, tool, snapshot, str(exc))
                signature = (tool["name"], json.dumps(args, sort_keys=True), snapshot)
                repeats = repeats + 1 if signature == last_signature else 0
                if repeats >= self.config.max_repeats:
                    status = "repeated_action"; record["error"] = "Identical action and state repeated"; events.append(record); break
                last_signature = signature
                record["tool"] = tool["name"]; record["arguments"] = args
                stage = "mcp_tool_execution"
                result = self.tools.call(tool["name"], args)
                content = "\n".join(item.get("text", "") for item in result.get("content", []) if isinstance(item, dict))
                normalized = {"is_error": bool(result.get("isError", False)), "content":content[:12000]}
                record["result"] = normalized
                # Snapshot content is what the model can ground refs against. Update only from browser outputs.
                if tool["name"] in ("browser_snapshot", "browser_navigate", "browser_click", "browser_type", "browser_select_option") and not normalized["is_error"]:
                    snapshot = content
                stage = "qwen_state_update"
                record["state_update"] = self.manager.update(state, action, normalized)
                record["state_after"] = state.snapshot()
                events.append(record)
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["failed_stage"] = stage
                state.last_error = record["error"]
                events.append(record)
                # Decider/model/MCP connectivity failures should be visible, not silently looped.
                status = "error"
                break
        report = {"status":status, "task_id":task_id, "goal":goal, "final_state":state.snapshot(), "steps":events,
                  "note":"Model-proposed criteria/evidence are heuristic; verify externally before high-stakes reliance."}
        if trace_path:
            p = Path(trace_path); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return report
