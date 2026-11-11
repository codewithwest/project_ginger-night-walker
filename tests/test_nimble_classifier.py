from nightwalker.nimble_classifier import (
    cart_mission_allows_alternate,
    classify_cart_product_mode,
    clear_cart_product_mode_cache,
)


def test_nimble_classifier_uses_systemone_choice(monkeypatch):
    clear_cart_product_mode_cache()
    monkeypatch.setenv("OLLAMA_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://classifier.test")
    monkeypatch.setenv("OLLAMA_MODEL_CLASSIFIER", "nimble")

    def fake_systemone(**kwargs):
        assert kwargs["model"] == "nimble"
        assert "any tile" in kwargs["state"]["user_request"].casefold()
        return {
            "answers": {
                "product_specificity": {
                    "choice": "flexible",
                    "confidence": 0.91,
                }
            }
        }

    monkeypatch.setattr("nightwalker.nimble_classifier._systemone", fake_systemone)
    assert classify_cart_product_mode(
        "Open https://www.ctm.co.za/. Add any tile product to the cart."
    ) == "flexible"
    assert cart_mission_allows_alternate(
        "Open https://www.ctm.co.za/. Add any tile product to the cart."
    ) is True


def test_nimble_classifier_falls_back_to_heuristic(monkeypatch):
    clear_cart_product_mode_cache()
    monkeypatch.setenv("OLLAMA_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://classifier.test")
    monkeypatch.setattr("nightwalker.nimble_classifier._systemone", lambda **kwargs: None)
    assert classify_cart_product_mode(
        'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart.'
    ) == "exact"
    assert cart_mission_allows_alternate(
        'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart.'
    ) is False


def test_nimble_classifier_can_be_disabled(monkeypatch):
    clear_cart_product_mode_cache()
    monkeypatch.setenv("OLLAMA_CLASSIFIER_ENABLED", "false")
    called = {"count": 0}

    def fake_systemone(**kwargs):
        called["count"] += 1
        return {"answers": {"product_specificity": {"choice": "exact", "confidence": 0.99}}}

    monkeypatch.setattr("nightwalker.nimble_classifier._systemone", fake_systemone)
    assert classify_cart_product_mode("Add any tile product to the cart.") == "flexible"
    assert called["count"] == 0
