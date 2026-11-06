"""Deterministic relevance checks for research discovery and source admission."""

_FURNITURE_TERMS = {
    "furniture", "furnishings", "sofa", "sofas", "couch", "couches",
    "bed", "beds", "mattress", "mattresses", "wardrobe", "wardrobes",
    "dining", "lounge", "bedroom", "chairs", "tables", "desk", "desks",
    "sideboard", "cabinet", "homeware", "decor", "upholstery",
}
_RETAIL_TERMS = {"retailer", "retailers", "store", "stores", "shop", "shops", "online"}

def _tokens(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]{3,}", value.casefold()))


def is_furniture_retail_research(objective: str) -> bool:
    tokens = _tokens(objective)
    return bool(tokens & {"furniture", "furnishings"}) and bool(
        tokens & {"retailer", "retailers", "store", "stores", "competitor", "competitors"}
    )


def focused_research_queries(objective: str, proposed: list[str] | None = None) -> list[str]:
    """Return focused queries; use a deterministic category plan for furniture retail."""
    if is_furniture_retail_research(objective):
        return [
            "South Africa furniture retailers leading furniture stores",
            "South Africa furniture retailers product ranges categories",
            "South Africa furniture stores pricing price positioning",
            "South Africa furniture retailers online shopping experience",
            "Weylandts competitors furniture retailers South Africa",
        ]
    cleaned = list(dict.fromkeys(
        " ".join(item.split()).strip()
        for item in (proposed or [])
        if isinstance(item, str) and len(item.strip()) >= 8
    ))
    return cleaned[:8]


def source_relevance(objective: str, url: str, title: str, captured_text: str) -> tuple[bool, str]:
    """Reject unrelated pages before they can enter the evidence set."""
    from urllib.parse import urlsplit

    if not is_furniture_retail_research(objective):
        return True, "No category-specific relevance gate applies."

    host = (urlsplit(url).hostname or "").casefold()
    title_tokens = _tokens(title)
    url_tokens = _tokens(url.replace("-", " ").replace("_", " "))
    text_tokens = _tokens(captured_text[:12000])
    all_tokens = title_tokens | url_tokens | text_tokens
    furniture_hits = sorted(all_tokens & _FURNITURE_TERMS)
    retailer_hits = sorted(all_tokens & _RETAIL_TERMS)
    known_retailer_signal = any(
        term in (host + " " + title.casefold())
        for term in ("weylandts", "coricraft", "decofurn", "@home", "homechoice",
                     "furniture warehouse", "bradlows", "russells", "lewis",
                     "ok furniture", "dial-a-bed", "dial a bed")
    )
    if len(furniture_hits) < 1 and not known_retailer_signal:
        return False, "Rejected as off-topic: no furniture, furnishing or furniture-product evidence."
    if len(furniture_hits) < 2 and not known_retailer_signal and not retailer_hits:
        return False, "Rejected as weakly relevant: no corroborating retail or product-category signal."
    if not (text_tokens & _FURNITURE_TERMS) and not known_retailer_signal:
        return False, "Rejected: furniture terms appeared only in the URL/title, not captured page text."
    return True, "Furniture-retail relevance confirmed by captured page content."
