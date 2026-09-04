"""Tests for cross-publisher story clustering (fuzzy consensus) in src/utils."""
from datetime import datetime, timezone

import pytest

from src.config import CONSENSUS_SYNERGY_BONUS
from src.utils import (
    _title_tokens,
    _titles_cluster,
    annotate_cross_publisher_consensus,
    calculate_relevance_score,
)

_NOW = datetime.now(timezone.utc)


def test_title_tokens_drops_stopwords_and_short_words():
    toks = _title_tokens("The new GPT-5 model is out")
    # "the"/"new"/"is" (stopwords), "5" (short) dropped; real tokens kept
    assert "gpt" in toks
    assert "model" in toks
    assert "the" not in toks and "new" not in toks and "is" not in toks


def test_titles_cluster_matches_same_story_not_distinct():
    a = _title_tokens("OpenAI launches GPT-5 reasoning model")
    b = _title_tokens("OpenAI launches GPT-5 model for developers")
    c = _title_tokens("Apple unveils new iPhone camera system")
    assert _titles_cluster(a, b) is True     # same story, different headline
    assert _titles_cluster(a, c) is False    # unrelated
    # too little to go on → never cluster
    assert _titles_cluster(_title_tokens("gpt5 out"), b) is False


def test_version_numbers_survive_tokenisation_and_prevent_false_cluster():
    # single-digit version tokens must be kept, or GPT-4 and GPT-5 collapse
    assert "5" in _title_tokens("OpenAI launches GPT-5 reasoning model")
    a = _title_tokens("OpenAI launches GPT-4 reasoning model")
    b = _title_tokens("OpenAI launches GPT-5 reasoning model")
    assert _titles_cluster(a, b) is False  # different model versions, different story


def test_alternate_number_formatting_still_clusters():
    # "$40 billion" vs "$40bn": same story, different number formatting — the
    # version-veto must NOT fire on funding amounts (only on version digits).
    a = _title_tokens("OpenAI raises $40 billion in landmark funding round")
    b = _title_tokens("OpenAI raises $40bn in landmark funding round")
    assert _titles_cluster(a, b) is True


def _item(title, link):
    return {"title": title, "description": "", "link": link, "source_feeds": [link]}


def test_annotate_counts_distinct_publishers_only():
    items = [
        _item("OpenAI launches GPT-5 reasoning model", "https://openai.com/a"),
        _item("OpenAI launches GPT-5 model for developers", "https://theverge.com/b"),
        _item("OpenAI launches GPT-5, its new flagship model", "https://techcrunch.com/c"),
        _item("OpenAI launches GPT-5 model, second take", "https://theverge.com/dup"),  # same domain
        _item("Apple unveils new iPhone camera system", "https://apple.com/x"),
    ]
    annotate_cross_publisher_consensus(items)
    # three distinct publishers cover the GPT-5 story (the-verge dup does not add a 4th)
    assert items[0]["cross_publisher_domains"] == 3
    assert items[1]["cross_publisher_domains"] == 3
    assert items[2]["cross_publisher_domains"] == 3
    # the unrelated story stands alone
    assert items[4]["cross_publisher_domains"] == 1


def test_consensus_bonus_uses_cross_publisher_signal():
    # Fixture deliberately names NO momentum flagship: from v4.25 a flagship
    # crossing LANDMARK_CONSENSUS_MIN_PUBLISHERS also becomes a landmark, which
    # would add LANDMARK_LAUNCH_BONUS on top and mask the pure consensus delta.
    base = _item("Acme ships a new reasoning model", "https://example.com/a")

    solo = dict(base, cross_publisher_domains=1)
    clustered = dict(base, cross_publisher_domains=3)
    delta = (calculate_relevance_score(clustered, _NOW, [])
             - calculate_relevance_score(solo, _NOW, []))
    assert delta == pytest.approx(CONSENSUS_SYNERGY_BONUS * 2, abs=1e-3)  # (3 - 1) publishers


def test_consensus_bonus_is_capped():
    base = _item("Acme ships a new reasoning model", "https://example.com/a")
    solo = dict(base, cross_publisher_domains=1)
    huge = dict(base, cross_publisher_domains=25)
    delta = (calculate_relevance_score(huge, _NOW, [])
             - calculate_relevance_score(solo, _NOW, []))
    assert delta == pytest.approx(CONSENSUS_SYNERGY_BONUS * 4, abs=1e-3)  # capped multiplier


def test_exact_url_and_cross_publisher_take_the_stronger():
    base = _item("Acme ships a new reasoning model", "https://example.com/a")
    # 2 feeds same URL, but 4 distinct publishers → cross-publisher wins
    item = dict(base, source_feeds=["f1", "f2"], cross_publisher_domains=4)
    solo = dict(base, source_feeds=["f1"], cross_publisher_domains=1)
    delta = calculate_relevance_score(item, _NOW, []) - calculate_relevance_score(solo, _NOW, [])
    assert delta == pytest.approx(CONSENSUS_SYNERGY_BONUS * 3, abs=1e-3)  # max(2,4) - 1


# ---------------------------------------------------------------------------
# annotate_flagship_consensus — entity-level consensus for the landmark gate
# ---------------------------------------------------------------------------

def test_flagship_consensus_counts_publishers_across_worded_headlines():
    """Punchy launch headlines share too few title tokens to cluster, so the
    STORY-level signal reports 1 for each. Entity-level flagship counting must
    still see three independent publishers (Codex #106)."""
    from src.news import annotate_flagship_consensus
    items = [
        _item("OpenAI launches GPT-5", "https://techcrunch.com/a"),
        _item("GPT-5 is here", "https://theverge.com/b"),
        _item("Introducing GPT-5", "https://arstechnica.com/c"),
    ]
    annotate_cross_publisher_consensus(items)
    annotate_flagship_consensus(items)

    assert [i["cross_publisher_domains"] for i in items] == [1, 1, 1]   # story-level blind
    assert [i["flagship_publisher_domains"] for i in items] == [3, 3, 3]


def test_flagship_consensus_ignores_same_publisher_repeats():
    """Three stories from ONE publisher must not inflate the flagship count."""
    from src.news import annotate_flagship_consensus
    items = [
        _item("OpenAI launches GPT-5", "https://techcrunch.com/a"),
        _item("GPT-5 is here", "https://techcrunch.com/b"),
        _item("Introducing GPT-5", "https://techcrunch.com/c"),
    ]
    annotate_flagship_consensus(items)
    assert [i["flagship_publisher_domains"] for i in items] == [1, 1, 1]


def test_flagship_consensus_is_zero_without_a_named_flagship():
    from src.news import annotate_flagship_consensus
    items = [
        _item("A startup ships a toaster", "https://techcrunch.com/a"),
        _item("The toaster is here", "https://theverge.com/b"),
    ]
    annotate_flagship_consensus(items)
    assert [i["flagship_publisher_domains"] for i in items] == [0, 0]


def test_flagship_matching_is_word_bounded():
    """Short flagship names must not fire inside unrelated identifiers (Codex #106).

    "o3" appearing inside "o365"/"o3de" previously only nudged the momentum bonus;
    with entity-level consensus it could hand a widely-covered unrelated product a
    landmark, so the matcher is word-bounded (with a dot-number version tail).
    """
    from src.news import _flagships_in
    assert _flagships_in("microsoft o365 now available") == []
    assert _flagships_in("o3de engine ships") == []
    assert _flagships_in("gemini 30 rumours") == []
    assert _flagships_in("chatgpt-5000 clone") == []
    # genuine mentions still match, including a decimal version tail
    assert _flagships_in("openai o3 launches") == ["o3"]
    assert _flagships_in("gpt-5 review") == ["gpt-5"]
    assert _flagships_in("gemini 3.8 is here") == ["gemini 3"]


def test_flagship_consensus_ignores_unrelated_identifier_across_publishers():
    """Three publishers covering O365 must not produce a flagship consensus."""
    from src.news import annotate_flagship_consensus
    items = [
        _item("Microsoft O365 gets new features", "https://techcrunch.com/a"),
        _item("O365 outage hits users", "https://theverge.com/b"),
        _item("O365 pricing changes", "https://arstechnica.com/c"),
    ]
    annotate_flagship_consensus(items)
    assert [i["flagship_publisher_domains"] for i in items] == [0, 0, 0]


def test_flagship_family_normalizes_aliases_and_variants():
    """Aliases and model variants of one family share a canonical key (Codex #106)."""
    from src.news import _flagship_family
    assert _flagship_family("gpt-5") == _flagship_family("gpt 5")
    assert _flagship_family("claude 4") == _flagship_family("claude opus 4") \
        == _flagship_family("claude sonnet 4")
    assert _flagship_family("deepseek v4") == "deepseek4"
    # genuinely different versions must NOT merge
    assert _flagship_family("grok 3") != _flagship_family("grok 4")


def test_flagship_consensus_counts_across_aliases():
    """Publishers split across "GPT-5" and "GPT 5" still form one consensus."""
    from src.news import annotate_flagship_consensus
    items = [
        _item("OpenAI launches GPT-5", "https://techcrunch.com/a"),
        _item("GPT 5 is here", "https://theverge.com/b"),
        _item("Introducing GPT-5", "https://arstechnica.com/c"),
    ]
    annotate_flagship_consensus(items)
    assert [i["flagship_publisher_domains"] for i in items] == [3, 3, 3]


def test_flagship_consensus_counts_across_model_variants():
    """Claude 4 / Opus 4 / Sonnet 4 are one family launch, not three lone stories."""
    from src.news import annotate_flagship_consensus
    items = [
        _item("Claude 4 launches", "https://techcrunch.com/a"),
        _item("Claude Opus 4 benchmarks", "https://theverge.com/b"),
        _item("Claude Sonnet 4 is here", "https://arstechnica.com/c"),
    ]
    annotate_flagship_consensus(items)
    assert [i["flagship_publisher_domains"] for i in items] == [3, 3, 3]


def test_flagship_consensus_keeps_distinct_versions_apart():
    """A Grok 3 story must not borrow consensus from Grok 4 coverage."""
    from src.news import annotate_flagship_consensus
    items = [
        _item("Grok 3 review", "https://techcrunch.com/a"),
        _item("Grok 4 launches", "https://theverge.com/b"),
        _item("Grok 4 benchmarks", "https://arstechnica.com/c"),
    ]
    annotate_flagship_consensus(items)
    assert [i["flagship_publisher_domains"] for i in items] == [1, 2, 2]
