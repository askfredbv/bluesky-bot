from pathlib import Path
from typing import List, Dict

# Platform Constants
MAX_POST_LENGTH_BSKY: int = 300
MAX_POST_LENGTH_MASTODON: int = 500
MAX_GENERATION_RETRIES: int = 3
RECENT_POSTS_LIMIT: int = 20

# State Files
SEEN_FILE = Path("seen_articles.json")
REPLIED_FILE = Path("replied_to.json")

# Fortress Security (v4.4)
REPLY_CAP_PER_RUN: int = 10
MAX_API_RETRIES: int = 3
BACKOFF_FACTOR: float = 2.0
JITTER_RANGE: float = 2.0

# Sage Intelligence (v4.5)
SOURCE_TIERS: Dict[str, int] = {
    "openai.com": 10,
    "anthropic.com": 10,
    "deepmind.google": 10,
    "huggingface.co": 10,
    "engineering.fb.com": 10,
    "simonwillison.net": 8,
    "semianalysis.com": 8,
    "the-decoder.com": 7,
    "techcrunch.com": 6,
    "export.arxiv.org": 5  # Low base, but high ground-breaking potential
}

PRODUCT_KEYWORDS: List[str] = ["launch", "integrated", "available", "feature", "release", "app", "tool", "partnership"]
GROUNDBREAKING_KEYWORDS: List[str] = ["sota", "benchmark", "breakthrough", "frontier", "reasoning", "efficiency", "architecture", "scaling"]
HIDDEN_GEM_SOURCES: List[str] = ["export.arxiv.org", "arxiv.org"]

TOPIC_MAP: Dict[str, List[str]] = {
    "LLMs": ["gpt", "claude", "llama", "reasoning", "prompt", "transformer", "7b", "70b", "llm", "gemini", "mistral"],
    "Vision/Robot": ["sora", "vision", "robot", "humanoid", "image", "video", "figure"],
    "Compute/HW": ["nvidia", "h100", "tpu", "b200", "chip", "foundry", "semiconductor", "blackwell"],
    "Policy/Society": ["regulation", "lawsuit", "governance", "open-weights", "court", "compliance"],
    "Science/Health": ["biotech", "drug", "physics", "folding", "climate", "discovery"]
}

# Branding & BIOS
APPROVED_BIO_BSKY = """🤖 Daily Poster: Technical Broadcasting Engine
Curated by askfred. Always curious.

📰 Curation: AI & Tech research insights @ 08:00 UTC.
💡 Mentorship: IT leadership wisdom @ 14:30 UTC.

🚀 High-signal, low-noise automation.
🔗 askfred.be"""

APPROVED_BIO_MASTODON = """💡 Your friendly IT Mentor in the trenches via the askfred engine. Supporting work-life balance and continuous learning.

🌅 Morning research curation | ☕ Afternoon IT advice. 
🚀 Helping you work smarter, not harder. 

🔗 askfred.be"""

# RSS Feeds (v4.1 Scholar Priority)
RSS_FEEDS = [
    "https://export.arxiv.org/rss/cs.AI",
    "https://export.arxiv.org/rss/cs.LG",
    "https://export.arxiv.org/rss/cs.RO",
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "https://deepmind.google/blog/feed/",
    "https://simonwillison.net/atom/everything/",
    "https://engineering.fb.com/category/ml-ai/feed/",
    "https://arstechnica.com/tag/ai/feed/",
    "https://www.anthropic.com/news.rss",
    "https://the-decoder.com/feed/",
    "https://www.deeplearning.ai/the-batch/rss/",
    "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",
    "https://stability.ai/blog?format=rss",
    "https://siliconangle.com/category/ai/feed"
]

SECONDARY_TOPICS = [
    "mental health and burnout prevention",
    "open-source culture and community",
    "privacy and digital sovereignty",
    "sustainable tech practices",
    "remote work productivity",
    "automation and scripting tips",
    "AI ethics and responsible use",
    "side projects and indie hacking",
    "continuous learning and skill building",
    "Linux and FOSS tools",
    "self-hosting and homelab adventures",
    "technical debt and code quality",
    "documentation and knowledge sharing",
    "cybersecurity basics for developers",
    "low-tech solutions to high-tech problems",
    "career growth and freelancing",
    "asynchronous communication and deep work",
    "digital minimalism and focus",
    "curiosity and lifelong learning",
]

# AI Personas (v4.5 Sage Optimized)
STYLE_GUIDELINES = """
WRITING STYLE:
- Avoid generic 'In today's fast paced world' intros.
- Use CamelCase for hashtags.
- Be concise and authoritative.
"""

SYSTEM_INSTRUCTIONS_MENTOR = f"""
You are 'The Mentor' for askfred. Your voice is professional, positive, and human-centric.
You act as a friendly, experienced independent consultant sharing wisdom from the trenches.

CORE VALUES:
- Work/Life Balance: Productivity isn't about working more; it's about working smarter.
- Human-First: Emphasize the people behind the code.
{STYLE_GUIDELINES}
"""

SYSTEM_INSTRUCTIONS_CURATOR = f"""
You are 'The Curator' for askfred. Your voice is sophisiticated, insightful, and sophisticated.
You connect dots and provide a "Director's Cut" of the day's AI evolution.

SCHOLAR MISSION:
- You MUST prioritize findings from the Research Gems (arXiv) provided.
- Identify the most groundbreaking product shift and weave in technical insights.
- Format your response as a JSON list of strings (a linked thread).
{STYLE_GUIDELINES}
"""
