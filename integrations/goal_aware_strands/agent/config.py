import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ACTIONS = ("navigate", "read_page", "click", "type_text", "select_option", "extract_text", "finish", "blocked")

@dataclass
class Config:
    state_base_url: str = "http://127.0.0.1:11434"
    state_model: str = "qwen3.5:0.8b"
    coder_base_url: str = "http://127.0.0.1:11434"
    coder_model: str = "qwen3.5:9b"
    decider_url: str = "http://127.0.0.1:8021"
    decider_path: str = "/v1/systemone"
    api_key: str = "local"
    ollama_think: bool = False
    max_steps: int = 12
    max_repeats: int = 2
    timeout_seconds: float = 60.0
    mcp_command: list[str] = field(default_factory=lambda: ["npx", "@playwright/mcp@latest", "--headless"])
    action_tools: dict[str, list[str]] = field(default_factory=lambda: {
        "navigate": ["browser_navigate"], "read_page": ["browser_snapshot"],
        "click": ["browser_click"], "type_text": ["browser_type"],
        "select_option": ["browser_select_option"],
        "extract_text": ["browser_snapshot"],
    })

    @classmethod
    def load(cls, path=None):
        data = {}
        if path:
            data.update(json.loads(Path(path).read_text()))
        names = {name for name in cls.__dataclass_fields__}
        env = {
            "state_base_url": "STATE_BASE_URL", "state_model": "STATE_MODEL",
            "coder_base_url": "CODER_BASE_URL", "coder_model": "CODER_MODEL",
            "decider_url": "DECIDER_URL", "decider_path": "DECIDER_PATH", "api_key": "MODEL_API_KEY",
            "max_steps": "MAX_STEPS", "timeout_seconds": "TIMEOUT_SECONDS",
        }
        for key, var in env.items():
            if os.getenv(var):
                data[key] = os.environ[var]
        if "max_steps" in data: data["max_steps"] = int(data["max_steps"])
        if "timeout_seconds" in data: data["timeout_seconds"] = float(data["timeout_seconds"])
        unknown = set(data) - names
        if unknown: raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
        return cls(**data)
