import pytest
from datetime import datetime, timezone, timedelta
from src.utils import calculate_relevance_score

def test_source_tier_ranking():
    """Verify that elite sources (OpenAI) get higher scores than general news."""
    item_openai = {'title': 'OpenAI Update', 'description': 'News', 'link': 'https://openai.com/1'}
    item_general = {'title': 'General Tech', 'description': 'News', 'link': 'https://techcrunch.com/1'}
    
    now = datetime.now(timezone.utc)
    score_openai = calculate_relevance_score(item_openai, now, [])
    score_general = calculate_relevance_score(item_general, now, [])
    
    assert score_openai > score_general

def test_groundbreaking_boost():
    """Verify that 'breakthrough' keywords significantly boost the score."""
    item_normal = {'title': 'AI Tool', 'description': 'An app.', 'link': 'https://techcrunch.com/1'}
    item_frontier = {'title': 'Frontier Model Breakthrough', 'description': 'SOTA benchmark scaling.', 'link': 'https://techcrunch.com/2'}
    
    now = datetime.now(timezone.utc)
    score_normal = calculate_relevance_score(item_normal, now, [])
    score_frontier = calculate_relevance_score(item_frontier, now, [])
    
    assert score_frontier > score_normal # The groundbreaking boost (+7) should beat the product boost (+5)

def test_topic_diversity_penalty():
    """Verify that recently discussed topics (Topic Memory) get penalized."""
    item = {'title': 'LLM Scaling', 'description': 'GPT news.', 'link': 'https://techcrunch.com/1'}
    now = datetime.now(timezone.utc)
    
    # 1. No penalty
    score_fresh = calculate_relevance_score(item, now, [])
    
    # 2. Penalty (LLM is in recent topics)
    score_penalized = calculate_relevance_score(item, now, ["LLMs"])
    
    assert score_penalized == pytest.approx(score_fresh - 12.0)

def test_time_decay():
    """Verify that older articles lose points over time."""
    item = {'title': 'News', 'description': 'Description', 'link': 'https://techcrunch.com/1'}
    now = datetime.now(timezone.utc)
    older = now - timedelta(hours=10)
    
    score_new = calculate_relevance_score(item, now, [])
    score_old = calculate_relevance_score(item, older, [])
    
    assert score_new > score_old
