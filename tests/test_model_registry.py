from nightwalker.contracts import Capability
from nightwalker.model_registry import ModelRegistry


def test_default_model_is_used(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL_DEFAULT", "test-model:latest")
    monkeypatch.delenv("OLLAMA_MODEL_EXTRACT", raising=False)
    assert ModelRegistry().model_for(Capability.EXTRACT) == "test-model:latest"


def test_capability_specific_model_overrides_default(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL_DEFAULT", "general:latest")
    monkeypatch.setenv("OLLAMA_MODEL_EXTRACT", "extractor:latest")
    assert ModelRegistry().model_for(Capability.EXTRACT) == "extractor:latest"
