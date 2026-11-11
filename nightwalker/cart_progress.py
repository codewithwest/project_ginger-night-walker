"""Storefront-agnostic cart progress from accessibility snapshots.

Works across Magento/Hyvä, Shopify, custom storefronts, and other carts.
Matchers key off common cart language in the live snapshot, not one site's
component names. Site-specific labels remain supported as one of many forms.
"""
from __future__ import annotations

import re

_QUOTED_NAME = re.compile(
    r"[\"'\u201c\u201d\u2018\u2019]([^\"'\u201c\u201d\u2018\u2019]{2,80})[\"'\u201c\u201d\u2018\u2019]"
)
_HEADING = re.compile(r'heading "([^"]+)"', re.IGNORECASE)
# Prices: common symbols/codes, not one market's currency.
_PRICE = re.compile(
    r"(?:R|ZAR|USD|EUR|GBP|\$|€|£)\s*\d[\d\s,]*(?:\.\d{2})?"
    r"|\d[\d\s,]*(?:\.\d{2})?\s*(?:R|ZAR|USD|EUR|GBP)",
    re.IGNORECASE,
)
_QUANTITY = re.compile(
    r'(?:textbox|spinbutton|combobox) "(?:Quantity|Qty|Qty\.|QTY)" @e\d+'
    r'(?:[^\n]*?\[value="(\d+)"\])?',
    re.IGNORECASE,
)
# Commerce-scoped stock signals only. Do not match review/marketing prose such as
# "collection branch had sold out" on a homepage.
_OUT_OF_STOCK = re.compile(
    r'(?:'
    r'(?:button|link)\s+"[^"]{0,80}\b(?:Notify me when available|Out of stock|Sold out|Currently unavailable)\b[^"]{0,40}"'
    r'|(?:StaticText|heading)\s+"(?:Out of stock\.?|Sold out\.?|Currently unavailable\.?|'
    r'Unavailable for (?:purchase|sale|delivery)|Not available(?: for (?:purchase|sale))?)"'
    r')',
    re.IGNORECASE,
)
_NOTIFY_WHEN_AVAILABLE = re.compile(
    r'(?:button|link)\s+"[^"]*\bNotify me when available\b[^"]*"',
    re.IGNORECASE,
)

# Cart count / empty markers seen across storefronts.
_CART_COUNT_PATTERNS = (
    re.compile(r"Toggle minicart,\s*(\d+)\s+items?", re.IGNORECASE),
    re.compile(r"(?:Mini\s*)?Cart[^\"\n]{0,40}?\b(\d+)\s+items?\b", re.IGNORECASE),
    re.compile(r"\b(?:Cart|Basket|Bag|Shopping\s+(?:cart|bag|basket))\b[^\"\n]{0,40}?\((\d+)\)", re.IGNORECASE),
    re.compile(r"\((\d+)\)\s*(?:in\s+)?(?:cart|basket|bag)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s+items?\s+in\s+(?:your\s+)?(?:cart|basket|bag)\b", re.IGNORECASE),
    re.compile(r'button "[^"]*\b(?:Cart|Basket|Bag)\b[^"]*\b(\d+)\b[^"]*" @e\d+', re.IGNORECASE),
)
_CART_EMPTY_PATTERNS = (
    re.compile(r"Toggle minicart,\s*Cart is empty", re.IGNORECASE),
    re.compile(r"\b(?:cart|basket|bag)\s+is\s+empty\b", re.IGNORECASE),
    re.compile(r"\byour\s+(?:cart|basket|bag)\s+is\s+empty\b", re.IGNORECASE),
    re.compile(r"\b(?:no\s+items?|0\s+items?)\s+in\s+(?:your\s+)?(?:cart|basket|bag)\b", re.IGNORECASE),
    re.compile(r'Toggle minicart,\s*0\s+items?', re.IGNORECASE),
)

# Open / close cart controls.
_CART_TOGGLE = re.compile(
    r'(?:button|link) "(?:'
    r'Toggle minicart[^"]*'
    r'|[^"]*\b(?:Mini\s*)?Cart\b[^"]*'
    r'|[^"]*\b(?:Basket|Bag|Shopping\s+(?:cart|bag|basket))\b[^"]*'
    r'|Open (?:cart|basket|bag|mini\s*cart)[^"]*'
    r'|View (?:cart|basket|bag)[^"]*'
    r')" @(e\d+)',
    re.IGNORECASE,
)
_CART_CLOSE = re.compile(
    r'(?:button|link) "(?:Close (?:minicart|mini\s*cart|cart|basket|bag)[^"]*|Close)" @(e\d+)',
    re.IGNORECASE,
)

# Remove controls.
_REMOVE_NAMED = re.compile(
    r'(?:button|link) "Remove (?:product|item) .+ from (?:cart|basket|bag)" @(e\d+)',
    re.IGNORECASE,
)
_REMOVE_GENERIC = re.compile(
    r'(?:button|link) "(?:'
    r'Remove item'
    r'|Remove from (?:cart|basket|bag)'
    r'|Delete item'
    r'|Remove'
    r')" @(e\d+)',
    re.IGNORECASE,
)

_ADD_TO_CART = re.compile(
    r'(?:button|link) "[^"]*\b(?:Add to (?:[Cc]art|[Bb]asket|[Bb]ag)|Add to bag|Buy now)\b[^"]*"',
    re.IGNORECASE,
)


def product_out_of_stock(snapshot_text: str) -> bool:
    """True when the live product page reports it cannot be purchased.

    Requires accessibility-role stock labels (button/link/StaticText/heading), not
    free-text anecdotes like customer reviews mentioning sold-out branches.
    """
    return bool(_OUT_OF_STOCK.search(snapshot_text or ""))


# Category / search tokens — not an exact SKU the user insisted on.
_GENERIC_PRODUCT_QUOTES = frozenset({
    "tile", "tiles", "sofa", "sofas", "couch", "chair", "chairs", "table",
    "tables", "bed", "beds", "lamp", "lamps", "rug", "rugs", "product",
    "item", "sku", "variant",
})
_FLEXIBLE_PRODUCT = re.compile(
    r"\b(?:add|put|place|choose|pick|select|find|open)\s+(?:any|whichever|another)\b"
    r"|\b(?:any|whichever|another)\s+(?:\w+\s+){0,4}(?:product|item|tile|tiles|sku|variant)\b"
    r"|\badd\s+an?\s+(?:\w+\s+){0,3}(?:product|item|tile)\b",
    re.IGNORECASE,
)


def cart_allows_alternate_product(objective: str) -> bool:
    """True when the objective did not pin an exact product name.

    Flexible missions ("any tile", "a product") must try another listing when
    the first opened PDP is out of stock. Exact quoted names ("Dark Grey") may
    end as a verified out-of-stock outcome.
    """
    text = objective or ""
    value = " ".join(text.casefold().split())
    if _FLEXIBLE_PRODUCT.search(value):
        return True
    quoted = [match.group(1).strip() for match in _QUOTED_NAME.finditer(text)]
    specific = [
        name for name in quoted
        if name.casefold() not in _GENERIC_PRODUCT_QUOTES
    ]
    if specific:
        return False
    # Cart journey with only a category quote (or no quote) is flexible.
    return "cart" in value or bool(quoted)


def cart_intent(objective: str) -> str | None:
    """Classify the cart action this step is responsible for."""
    value = " ".join(objective.casefold().split())
    if re.search(r"\bremove\b", value) and any(term in value for term in ("cart", "basket", "bag")):
        return "remove"
    if any(
        term in value
        for term in (
            "cart is empty",
            "cart is now empty",
            "basket is empty",
            "bag is empty",
            "verify the cart is empty",
            "verify that the cart is empty",
            "verify that the cart is now empty",
            "confirm the cart is empty",
            "confirm that the cart is empty",
            "empty cart",
            "empty basket",
        )
    ):
        return "confirm_empty"
    if ("price" in value and "quantity" in value) or (
        any(term in value for term in ("cart line", "line item", "basket line"))
        and ("price" in value or "quantity" in value)
    ):
        return "verify_line"
    if any(
        term in value
        for term in (
            "variant",
            "stock availability",
            "in stock",
            "select the",
            "select a product",
            "select any required",
            "product detail",
            "open its product",
            "identify a specific product",
            "identify a product",
            "find the exact requested product",
            "find a product",
        )
    ):
        return "select"
    if re.search(r"\badd\b", value) and any(term in value for term in ("cart", "basket", "bag")):
        return "add"
    if any(term in value for term in ("add to cart", "add to basket", "add to bag", "click the add")):
        return "add"
    return None


def minicart_count(snapshot_text: str) -> int | None:
    """Return the latest cart count from common storefront markers."""
    text = snapshot_text or ""
    markers: list[tuple[int, int]] = []
    for pattern in _CART_COUNT_PATTERNS:
        markers.extend((match.start(), int(match.group(1))) for match in pattern.finditer(text))
    for pattern in _CART_EMPTY_PATTERNS:
        markers.extend((match.start(), 0) for match in pattern.finditer(text))
    if not markers:
        return None
    markers.sort(key=lambda item: item[0])
    return markers[-1][1]


def cart_popup_open(snapshot_text: str) -> bool:
    text = snapshot_text or ""
    if _CART_CLOSE.search(text):
        return True
    # Drawer/dialog content often exposes remove/qty without a close control yet.
    return bool(_REMOVE_NAMED.search(text) or _REMOVE_GENERIC.search(text))


def _toggle_ref(snapshot_text: str) -> str | None:
    match = _CART_TOGGLE.search(snapshot_text or "")
    return match.group(1) if match else None


def _remove_ref(snapshot_text: str) -> str | None:
    named = _REMOVE_NAMED.search(snapshot_text or "")
    if named:
        return named.group(1)
    plain = _REMOVE_GENERIC.search(snapshot_text or "")
    return plain.group(1) if plain else None


def cart_follow_up_actions(snapshot_text: str, objective: str) -> list[dict[str, str]]:
    """One next click: open the cart, or remove the line once it is open."""
    intent = cart_intent(objective)
    text = snapshot_text or ""
    if intent == "remove":
        remove_ref = _remove_ref(text)
        count = minicart_count(text)
        # Count may be missing on non-Magento carts; a visible remove control is enough.
        if remove_ref and count != 0:
            return [{"action": "click", "target": "@" + remove_ref}]
        toggle_ref = _toggle_ref(text)
        if toggle_ref and not cart_popup_open(text):
            return [{"action": "click", "target": "@" + toggle_ref}]
        return []
    if intent in {"confirm_empty", "verify_line"} and not cart_popup_open(text):
        toggle_ref = _toggle_ref(text)
        if toggle_ref:
            return [{"action": "click", "target": "@" + toggle_ref}]
    return []


def _on_product_page(text: str, out_of_stock: bool = False) -> bool:
    """True only with purchase UI — never from OOS alone (homepage false positives)."""
    if _ADD_TO_CART.search(text) or _QUANTITY.search(text):
        return True
    # OOS PDP often replaces Add to Cart with Notify me; still a product page.
    return bool(out_of_stock and _NOTIFY_WHEN_AVAILABLE.search(text))


def on_product_detail_page(snapshot_text: str) -> bool:
    """Public helper: live snapshot looks like a product detail page."""
    text = snapshot_text or ""
    return _on_product_page(text, product_out_of_stock(text))


def cart_step_verification(
    objective: str,
    snapshot_text: str,
    *,
    mission_objective: str | None = None,
    allow_alternate: bool | None = None,
) -> str | None:
    """Return a verified-step result when the snapshot already proves the objective.

    Pass ``mission_objective`` (the original user goal) so flexible cart missions
    do not treat the first out-of-stock listing as journey completion. Prefer
    ``allow_alternate`` from the Nimble classifier when available.
    """
    intent = cart_intent(objective)
    text = snapshot_text or ""
    count = minicart_count(text)
    out_of_stock = product_out_of_stock(text)
    # Only the mission scope decides alternate-product behaviour. Step-only calls
    # (unit tests / legacy) keep out-of-stock as a terminal verified outcome.
    if allow_alternate is None:
        allow_alternate = (
            mission_objective is not None
            and cart_allows_alternate_product(mission_objective)
        )
    if intent == "select":
        quoted = _QUOTED_NAME.search(objective or "")
        name = quoted.group(1).strip() if quoted else ""
        headings = _HEADING.findall(text)
        on_product = _on_product_page(text, out_of_stock)
        named_heading = bool(name) and any(
            name.casefold() in heading.casefold() for heading in headings
        )
        # Missions that say "any tile/product" often have no quoted SKU. A PDP
        # heading plus add-to-cart / stock state is enough on any storefront.
        generic_product = on_product and bool(headings)
        label = name or (headings[0].strip() if headings else "the selected product")
        if named_heading and name and headings and "search for" in (objective or "").casefold():
            matched = next(
                (heading for heading in headings if name.casefold() in heading.casefold()),
                None,
            )
            if matched and matched.strip().casefold() != name.casefold():
                label = matched.strip()
        if (named_heading or generic_product) and out_of_stock:
            if allow_alternate:
                # Flexible request: keep the step open so another product is tried.
                return None
            return f"Opened the product page for {label}; item is out of stock"
        if named_heading or generic_product:
            return f"Opened the product page for {label}"
    if intent == "add":
        if out_of_stock and _on_product_page(text, out_of_stock):
            if allow_alternate:
                return None
            return "Product is out of stock; add-to-cart is not available"
        if count is not None and count >= 1:
            return f"Cart count is {count} after adding the item"
    if intent == "verify_line":
        if (
            out_of_stock
            and _on_product_page(text, out_of_stock)
            and (count is None or count == 0)
        ):
            if allow_alternate:
                return None
            return "Cart line unavailable because the product is out of stock"
        price = _PRICE.search(text)
        quantity = _QUANTITY.search(text)
        if price and (quantity or (count is not None and count >= 1)):
            quantity_label = quantity.group(1) if quantity and quantity.group(1) else str(count)
            return f"Captured displayed price {price.group(0)} and quantity {quantity_label}"
    if intent == "remove" and count == 0:
        if out_of_stock:
            if allow_alternate:
                return None
            return "No cart line to remove; product is out of stock"
        return "Removed the item and the cart is empty"
    if intent == "confirm_empty" and count == 0:
        return "Confirmed the cart is empty"
    return None
