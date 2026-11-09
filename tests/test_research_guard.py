from nightwalker.research_guard import ResearchLoopGuard, canonical_url_key


def test_url_key_ignores_fragment_host_case_default_port_and_trailing_slash():
    assert canonical_url_key("HTTPS://EXAMPLE.com:443/research/#top") == "https://example.com/research"


def test_candidate_filter_deduplicates_canonical_urls_and_visited_sources():
    guard = ResearchLoopGuard()
    candidates = [
        {"url": "https://EXAMPLE.com:443/a/", "label": "first"},
        {"url": "https://example.com/a#section", "label": "duplicate"},
        {"url": "https://example.com/b", "label": "second"},
    ]
    filtered = guard.filter_candidates(candidates, ["https://example.com/a"])
    assert [item["url"] for item in filtered] == ["https://example.com/b"]


def test_failed_url_gets_one_retry_then_is_exhausted():
    guard = ResearchLoopGuard(max_attempts_per_url=2)
    url = "https://example.com/unavailable"
    first = guard.begin(url, "Inspect source")
    assert first["attempt"] == 1
    assert first["should_skip"] is False
    assert guard.filter_candidates([{"url": url}], []) == [{"url": url, "label": ""}]

    second = guard.begin(url + "/", "Inspect source")
    assert second["attempt"] == 2
    assert second["action_repeat"] == 2
    assert second["should_skip"] is False
    assert guard.filter_candidates([{"url": url}], []) == []


def test_third_attempt_is_blocked_and_fallback_selects_another_source():
    guard = ResearchLoopGuard(max_attempts_per_url=2)
    url = "https://example.com/retry"
    guard.begin(url, "Open")
    guard.begin(url, "Open")
    third = guard.begin(url, "Open")
    assert third["should_skip"] is True
    repeated = guard.snapshot()["repeated_actions"]
    assert repeated[0]["attempts"] == 3
    assert repeated[0]["assignment"] == "open"
    fallback = guard.fallback_candidate(
        [{"url": url}, {"url": "https://example.com/other", "label": "other"}],
        [],
    )
    assert fallback == {"url": "https://example.com/other", "label": "other"}


def test_loop_guard_records_bounded_outcomes():
    guard = ResearchLoopGuard()
    for index in range(70):
        guard.record_outcome(f"https://example.com/{index}", "Inspect", "failed", "timeout")
    snapshot = guard.snapshot()
    assert len(snapshot["recent_outcomes"]) == 12
    assert snapshot["max_attempts_per_url"] == 2


def test_loop_guard_restores_attempt_counts_and_recent_diagnostics():
    original = ResearchLoopGuard(max_attempts_per_url=2)
    original.begin("https://example.com/retry", "Inspect")
    original.record_outcome("https://example.com/retry", "Inspect", "failed", "timeout")

    restored = ResearchLoopGuard.from_snapshot(original.snapshot())

    assert restored.attempts["https://example.com/retry"] == 1
    assert restored.filter_candidates(
        [{"url": "https://example.com/retry"}], []
    ) == [{"url": "https://example.com/retry", "label": ""}]
    assert restored.snapshot()["recent_outcomes"][0]["reason"] == "timeout"


def test_candidate_filter_supports_larger_bounded_audit_queues():
    guard = ResearchLoopGuard()
    candidates = [{"url": f"https://example.com/{index}"} for index in range(250)]
    filtered = guard.filter_candidates(candidates, [], limit=200)
    assert len(filtered) == 200
