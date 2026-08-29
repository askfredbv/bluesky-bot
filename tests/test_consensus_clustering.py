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
    base = _item("OpenAI launches GPT-5 reasoning model", "https://example.com/a")

    solo = dict(base, cross_publisher_domains=1)
    clustered = dict(base, cross_publisher_domains=3)
    delta = (calculate_relevance_score(clustered, _NOW, [])
             - calculate_relevance_score(solo, _NOW, []))
    assert delta == pytest.approx(CONSENSUS_SYNERGY_BONUS * 2, abs=1e-3)  # (3 - 1) publishers


def test_consensus_bonus_is_capped():
    base = _item("OpenAI launches GPT-5 reasoning model", "https://example.com/a")
    solo = dict(base, cross_publisher_domains=1)
    huge = dict(base, cross_publisher_domains=25)
    delta = (calculate_relevance_score(huge, _NOW, [])
             - calculate_relevance_score(solo, _NOW, []))
    assert delta == pytest.approx(CONSENSUS_SYNERGY_BONUS * 4, abs=1e-3)  # capped multiplier


def test_exact_url_and_cross_publisher_take_the_stronger():
    base = _item("OpenAI launches GPT-5 reasoning model", "https://example.com/a")
    # 2 feeds same URL, but 4 distinct publishers → cross-publisher wins
    item = dict(base, source_feeds=["f1", "f2"], cross_publisher_domains=4)
    solo = dict(base, source_feeds=["f1"], cross_publisher_domains=1)
    delta = calculate_relevance_score(item, _NOW, []) - calculate_relevance_score(solo, _NOW, [])
    assert delta == pytest.approx(CONSENSUS_SYNERGY_BONUS * 3, abs=1e-3)  # max(2,4) - 1
