from __future__ import annotations

import os

from nightwalker.contracts import Capability


class ModelRegistry:
    """Route capabilities across exactly two configured model tiers."""

    def model_for(self, capability: Capability) -> str:
        main_model = os.getenv("OLLAMA_MODEL_DEFAULT", "qwen3.5:9b").strip()
        light_model = os.getenv("OLLAMA_MODEL_LIGHT", "qwen3.5:4b").strip()
        if not main_model or not light_model:
            raise ValueError("OLLAMA_MODEL_DEFAULT and OLLAMA_MODEL_LIGHT must be configured")

        # Navigation and interaction planning benefit from the stronger model.
        # Extraction and bounded recovery are narrower, cheaper tasks.
        if capability in {Capability.EXTRACT, Capability.RECOVER}:
            return light_model
        return main_model
