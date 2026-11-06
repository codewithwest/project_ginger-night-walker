OBJECTIVE = (
    "Research the top five furniture retailers in South Africa. Compare product "
    "categories, pricing, target customers and online shopping experiences, and opportunities for Weylandts."
)


def test_furniture_retail_queries_are_category_led_and_specific():
    from nightwalker.research_relevance import (
        focused_research_queries,
        is_furniture_retail_research,
    )

    assert is_furniture_retail_research(OBJECTIVE)
    queries = focused_research_queries(OBJECTIVE, ["top", "dictionary top meaning"])
    assert len(queries) >= 4
    assert all("furniture" in query.casefold() for query in queries)
    assert any("weylandts" in query.casefold() for query in queries)


def test_rejects_supermarket_dictionary_and_music_sources():
    from nightwalker.research_relevance import source_relevance

    for url, title, text in [
        ("https://www.topsatspar.co.za/Home", "TOPS at SPAR", "Online shopping wine specials rewards"),
        ("https://dictionary.cambridge.org/dictionary/english/top", "TOP meaning", "Meaning of top in English examples"),
        ("https://music.apple.com/za/playlist/top-100", "Top 100 South Africa", "Most-played songs in South Africa"),
    ]:
        accepted, reason = source_relevance(OBJECTIVE, url, title, text)
        assert not accepted
        assert "off-topic" in reason or "Rejected" in reason


def test_accepts_substantive_furniture_retail_page():
    from nightwalker.research_relevance import source_relevance

    accepted, reason = source_relevance(
        OBJECTIVE,
        "https://www.example.co.za/furniture",
        "Furniture stores and sofas",
        "Explore our furniture range including sofas, beds, dining tables and bedroom furniture.",
    )
    assert accepted
    assert "confirmed" in reason


def test_generic_objectives_keep_model_queries():
    from nightwalker.research_relevance import focused_research_queries

    queries = focused_research_queries("Research cloud hosting providers", ["cloud hosting providers"])
    assert queries == ["cloud hosting providers"]


def test_discovery_candidates_prioritise_retailers_and_drop_general_reference_pages():
    from nightwalker.research_relevance import rank_research_candidates

    candidates = [
        {"url": "https://dictionary.cambridge.org/dictionary/english/furniture", "label": "Furniture definition"},
        {"url": "https://www.polokwane.gov.za/", "label": "Polokwane municipality"},
        {"url": "https://www.weylandts.co.za/", "label": "Weylandts | Furniture and homeware"},
        {"url": "https://www.example.co.za/furniture-stores", "label": "Furniture stores in South Africa"},
        {"url": "https://www.example.co.za/about", "label": "About our company"},
    ]

    ranked = rank_research_candidates(OBJECTIVE, candidates)

    assert [item["url"] for item in ranked] == [
        "https://www.weylandts.co.za/",
        "https://www.example.co.za/furniture-stores",
    ]


def test_candidate_ranking_does_not_filter_non_furniture_research():
    from nightwalker.research_relevance import rank_research_candidates

    candidates = [{"url": "https://dictionary.cambridge.org/", "label": "Reference"}]
    assert rank_research_candidates("Research South African universities", candidates) == candidates
