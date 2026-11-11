"""Tiny Nimble classifier for cart product specificity.

Uses Ollama ``/v1/systemone`` (``ollama pull nimble``, Ollama ≥0.35) to decide
whether a cart mission named an exact product or allows any matching listing.
Falls back to the deterministic heuristic in ``cart_progress`` when Nimble is
disabled or unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Literal

from nightwalker.cart_progress import cart_allows_alternate_product

logger = logging.getLogger(__name__)

CartProductMode = Literal["exact", "flexible"]

_CACHE: dict[str, CartProductMode] = {}


def _classifier_enabled() -> bool:
    return os.getenv("OLLAMA_CLASSIFIER_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _classifier_model() -> str:
    return (
        os.getenv("OLLAMA_MODEL_CLASSIFIER", "").strip()
        or os.getenv("OLLAMA_MODEL_NIMBLE", "").strip()
        or "nimble"
    )


def _ollama_base_url() -> str:
    return (
        os.getenv("OLLAMA_BASE_URL_CLASSIFIER", "").strip()
        or os.getenv("OLLAMA_BASE_URL", "").strip()
    ).rstrip("/")


def _cache_key(objective: str) -> str:
    return " ".join((objective or "").casefold().split())[:800]


def _heuristic_mode(objective: str) -> CartProductMode:
    return "flexible" if cart_allows_alternate_product(objective) else "exact"


def _systemone(
    *,
    base_url: str,
    model: str,
    state: dict[str, Any],
    questions: dict[str, Any],
    timeout_sec: float,
) -> dict[str, Any] | None:
    url = f"{base_url}/v1/systemone"
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.2, float(timeout_sec))) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Nimble systemone unavailable: %s", exc)
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _nimble_mode(objective: str) -> CartProductMode | None:
    base_url = _ollama_base_url()
    if not base_url:
        return None
    timeout = float(os.getenv("OLLAMA_CLASSIFIER_TIMEOUT_SECONDS", "8"))
    payload = _systemone(
        base_url=base_url,
        model=_classifier_model(),
        state={"user_request": (objective or "")[:1200]},
        questions={
            "product_specificity": {
                "type": "choice",
                "instructions": (
                    "For this shopping/cart request, did the user name one exact product "
                    "(specific SKU, colourway, or quoted product title that must be used), "
                    "or may any matching product in the category be used?"
                ),
                "criteria": {
                    "exact": (
                        "The user named a specific product that must be used "
                        "(for example Add \"Dark Grey\" or a unique product title)."
                    ),
                    "flexible": (
                        "The user did not pin an exact product; any matching item is fine "
                        "(for example any tile, a product, any item in a category)."
                    ),
                },
            }
        },
        timeout_sec=timeout,
    )
    if payload is None:
        return None
    answer = (payload.get("answers") or {}).get("product_specificity") or {}
    choice = str(answer.get("choice") or "").strip().casefold()
    if choice in {"exact", "flexible"}:
        confidence = float(answer.get("confidence") or 0.0)
        min_confidence = float(os.getenv("OLLAMA_CLASSIFIER_MIN_CONFIDENCE", "0.35"))
        if confidence and confidence < min_confidence:
            logger.info(
                "Nimble cart classifier low confidence (%.3f < %.3f); falling back",
                confidence, min_confidence,
            )
            return None
        logger.info(
            "Nimble cart classifier choice=%s confidence=%s",
            choice, answer.get("confidence"),
        )
        return choice  # type: ignore[return-value]
    return None


def classify_cart_product_mode(objective: str) -> CartProductMode:
    """Return whether the mission requires an exact product or allows alternates."""
    key = _cache_key(objective)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    mode: CartProductMode | None = None
    if _classifier_enabled():
        mode = _nimble_mode(objective)
    if mode is None:
        mode = _heuristic_mode(objective)
        logger.debug("Cart product mode heuristic=%s", mode)
    _CACHE[key] = mode
    return mode


def cart_mission_allows_alternate(objective: str) -> bool:
    """True when OOS on the first listing should trigger trying another product."""
    return classify_cart_product_mode(objective) == "flexible"


def clear_cart_product_mode_cache() -> None:
    """Test helper."""
    _CACHE.clear()
