from __future__ import annotations

import os

from nightwalker.contracts import Capability


class ModelRegistry:
    """Server-side model routing; callers cannot select arbitrary Ollama models."""

    ENV_KEYS = {
        Capability.NAVIGATE: "OLLAMA_MODEL_NAVIGATE",
        Capability.INTERACT: "OLLAMA_MODEL_INTERACT",
        Capability.EXTRACT: "OLLAMA_MODEL_EXTRACT",
        Capability.RECOVER: "OLLAMA_MODEL_RECOVER",
    }

    def model_for(self, capability: Capability) -> str:
        key = self.ENV_KEYS[capability]
        model = os.getenv(key) or os.getenv("OLLAMA_MODEL_DEFAULT", "qwen2.5:7b")
        if not model.strip():
            raise ValueError(f"No Ollama model configured for {capability.value}")
        return model.strip()
