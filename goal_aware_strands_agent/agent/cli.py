import argparse
import json
import sys
from .config import Config
from .models import ChatModel, StrandsDecider
from .goal_state import GoalStateManager
from .coder import CodingToolGenerator
from .tools import MCPStdio, MockBrowser
from .runner import AgentRunner


def main(argv=None):
    parser = argparse.ArgumentParser(description="Goal-aware Strands + Qwen 0.6B + coding model MCP agent")
    parser.add_argument("task", nargs="?", default="Visit python.org and open the Downloads page")
    parser.add_argument("--config", help="JSON configuration file")
    parser.add_argument("--output", default="trace.json", help="Path to JSON trace")
    parser.add_argument("--demo", action="store_true", help="Run scripted mocks (NO model inference and NO internet)")
    parser.add_argument("--mock-browser", action="store_true", help="Use mock browser with real local models")
    parser.add_argument("--max-steps", type=int)
    args = parser.parse_args(argv)
    config = Config.load(args.config)
    if args.max_steps: config.max_steps = args.max_steps
    if args.demo:
        from .demo import FakeStateModel, FakeDecider, FakeCoder
        manager = GoalStateManager(FakeStateModel())
        decider = FakeDecider()
        coder = FakeCoder()
    else:
        manager = GoalStateManager(ChatModel(config.state_base_url, config.state_model, config.api_key, config.timeout_seconds, config.ollama_think))
        decider = StrandsDecider(config.decider_url, config.decider_path, config.timeout_seconds)
        coder = CodingToolGenerator(ChatModel(config.coder_base_url, config.coder_model, config.api_key, config.timeout_seconds, config.ollama_think))
    backend = MockBrowser() if (args.demo or args.mock_browser) else MCPStdio(config.mcp_command, config.timeout_seconds)
    try:
        with backend as tools:
            report = AgentRunner(manager, decider, coder, tools, config).run(args.task, args.output)
        print(json.dumps({"status":report["status"], "completed":report["final_state"]["completed"],
                          "remaining":report["final_state"]["remaining"], "steps":len(report["steps"]),
                          "trace":args.output,
                          "last_error":next((e.get("error") for e in reversed(report["steps"]) if e.get("error")), None),
                          "failed_stage":next((e.get("failed_stage") for e in reversed(report["steps"]) if e.get("failed_stage")), None)}, indent=2))
        return 0 if report["status"] == "completed" else 1
    except Exception as exc:
        print(f"Agent startup failed: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__": raise SystemExit(main())
