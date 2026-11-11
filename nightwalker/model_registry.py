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

        # Planning / navigate / synthesis stay on the main model.
        # Site interactions (click/type/ref selection), extraction and recovery
        # use the light tier so operators can run a much smaller HTML-logic model.
        if capability in {Capability.INTERACT, Capability.EXTRACT, Capability.RECOVER}:
            return light_model
        return main_model
