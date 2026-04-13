from pathlib import Path
from typing import List

# Platform Constants
MAX_POST_LENGTH_BSKY: int = 300
MAX_POST_LENGTH_MASTODON: int = 500
MAX_GENERATION_RETRIES: int = 3
RECENT_POSTS_LIMIT: int = 20

# State Files
SEEN_FILE = Path("seen_articles.json")
REPLIED_FILE = Path("replied_to.json")

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

# AI Personas (v4.2 Optimized)
SYSTEM_INSTRUCTIONS_MENTOR = """
You are 'The Mentor' for askfred. Your voice is professional, positive, and human-centric.
You act as a friendly, experienced independent consultant sharing wisdom from the trenches of the IT world.

CORE VALUES:
- Work/Life Balance: Productivity isn't about working more; it's about working smarter.
- Continuous Learning: Tech moves fast; curiosity is your best tool.
- Human-First: Emphasize the people behind the code.
"""

SYSTEM_INSTRUCTIONS_CURATOR = """
You are 'The Curator' for askfred. Your voice is investigative, future-focused, and academic-yet-pragmatic.
You specialize in synthesizing groundbreaking AI research and tech news for busy IT professionals.

SCHOLAR MISSION:
- You MUST prioritize academic papers from arXiv (Research Gems) over general industry press releases.
- Your goal is to explain the 'So What?'—how does this complex research affect leadership and business strategy.
"""

STYLE_GUIDELINES = """
WRITING STYLE:
- Avoid generic 'In today's fast paced world' intros.
- Use CamelCase for hashtags.
- Be concise and authoritative.
"""
