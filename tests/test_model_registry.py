from nightwalker.contracts import Capability
from nightwalker.model_registry import ModelRegistry


def test_main_model_handles_planning_and_navigation(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL_DEFAULT", "main:latest")
    monkeypatch.setenv("OLLAMA_MODEL_LIGHT", "light:latest")
    registry = ModelRegistry()
    assert registry.model_for(Capability.NAVIGATE) == "main:latest"


def test_light_model_handles_interaction_extraction_and_recovery(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL_DEFAULT", "main:latest")
    monkeypatch.setenv("OLLAMA_MODEL_LIGHT", "light:latest")
    registry = ModelRegistry()
    assert registry.model_for(Capability.INTERACT) == "light:latest"
    assert registry.model_for(Capability.EXTRACT) == "light:latest"
    assert registry.model_for(Capability.RECOVER) == "light:latest"


def test_default_model_tiers_are_two_models(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL_DEFAULT", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL_LIGHT", raising=False)
    registry = ModelRegistry()
    assert registry.model_for(Capability.NAVIGATE) == "qwen3.5:9b"
    assert registry.model_for(Capability.EXTRACT) == "qwen3.5:4b"
