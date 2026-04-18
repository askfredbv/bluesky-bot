from pathlib import Path
from typing import List, Dict

# Platform Constants
MAX_POST_LENGTH_BSKY: int = 300
MAX_POST_LENGTH_MASTODON: int = 500
MAX_GENERATION_RETRIES: int = 3
RECENT_POSTS_LIMIT: int = 20
STYLE_MEMORY_POST_WINDOW: int = 10
STYLE_MEMORY_MAX_OPENERS: int = 5
STYLE_MEMORY_MAX_HASHTAGS: int = 8
POST_JITTER_MIN_SECONDS: int = 120
POST_JITTER_MAX_SECONDS: int = 1800
THREAD_PAUSE_PROFILES = {
    "quick": (4.0, 20.0),
    "normal": (20.0, 75.0),
    "reflective": (60.0, 180.0),
}
DEFAULT_THREAD_PAUSE_PROFILE: str = "normal"
HASHTAG_OPTIONAL_MIN_CHARS: int = 110
MIN_THREAD_POSTS: int = 1
MAX_THREAD_POSTS: int = 5
LANGUAGE_OPTIONS: List[str] = ["English", "Dutch"]
MENTION_SANITIZE_MAX_CHARS: int = 500
FEED_SUMMARY_MAX_CHARS: int = 500

# State Files
SEEN_FILE = Path("seen_articles.json")
REPLIED_FILE = Path("replied_to.json")
RUNTIME_STATE_FILE = Path("runtime_state.json")

# Fortress Security (v4.4)
REPLY_CAP_PER_RUN: int = 10
REPLY_MAX_CHARS: int = 250
MENTION_NO_REPLY_PROB: float = 0.2
MENTION_REPLY_MIN_DELAY_SECONDS: float = 20.0
MENTION_REPLY_MAX_DELAY_SECONDS: float = 900.0
PROFILE_BIO_UPDATE_COOLDOWN_HOURS: int = 168
MAX_API_RETRIES: int = 3
BACKOFF_FACTOR: float = 2.0
JITTER_RANGE: float = 2.0
RATE_LIMIT_BASE_WAIT_SECONDS: int = 60  # base wait on a 429; multiplied per retry
RATE_LIMIT_MAX_RETRIES: int = 3         # separate retry budget for rate-limit errors
FEED_FETCH_CONCURRENCY: int = 6
FEED_REQUEST_CONNECT_TIMEOUT_SECONDS: float = 5.0
FEED_REQUEST_READ_TIMEOUT_SECONDS: float = 10.0
FEED_REQUEST_WRITE_TIMEOUT_SECONDS: float = 10.0
FEED_REQUEST_POOL_TIMEOUT_SECONDS: float = 10.0
FEED_MAX_CONNECTIONS: int = 20
FEED_MAX_KEEPALIVE_CONNECTIONS: int = 10

# Metadata Fetch Domain Policy
# If allowlist is empty, all public domains are allowed unless explicitly denied.
METADATA_FETCH_ALLOWED_DOMAINS: List[str] = []
METADATA_FETCH_BLOCKED_DOMAINS: List[str] = []

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
    "export.arxiv.org": 8  # v4.6 Hidden Gem Upgrade: Guarantee research survival
}

PRODUCT_KEYWORDS: List[str] = ["launch", "integrated", "available", "feature", "release", "app", "tool", "partnership"]
GROUNDBREAKING_KEYWORDS: List[str] = ["sota", "benchmark", "breakthrough", "frontier", "reasoning", "efficiency", "architecture", "scaling"]
HIDDEN_GEM_SOURCES: List[str] = ["export.arxiv.org", "arxiv.org"]
CONSENSUS_SYNERGY_BONUS: float = 1.5

# Link-card thumbnails matching any of these substrings are skipped. Generic
# org logos and default share images add visual clutter without conveying
# information — better to let Bluesky render the link card without a thumb.
GENERIC_IMAGE_PATTERNS: List[str] = [
    "logo", "default-card", "default-og", "twitter-card-default",
    "og-default", "site-icon", "apple-touch-icon", "favicon",
    "social-default", "share-image-default",
]

# AI Model Priority (failover order on quota/availability errors)
GEMINI_MODEL_PRIORITY: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
    "gemma-3-27b-it",
]

# Image generation
IMAGE_GENERATION_PROBABILITY: float = 0.5
IMAGEN_MODEL: str = "imagen-3.0-generate-002"

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

# AI Personas
STYLE_GUIDELINES = """
VOICE:
You are writing in the voice of Frederik Van Hecke — a management consultant and independent IT advisor with 25+ years of experience. His tone is direct, pragmatic, and dry. He respects the reader's intelligence. He does not hype. He does not motivate-poster.

STYLE REFERENCE — these are examples of how he actually writes. Match this voice:
  "Conversational AI is changing how we handle the routine parts of consulting work. It is not changing — and should not change — how we handle clients."
  "The draft is rarely good enough to send without revision; it is almost always good enough to react to, which turns out to be considerably faster than writing from a blank page."
  "That is a rational position for them. It is a strategic problem for you."
  "The technical infrastructure to support this is rarely the constraint. The organisational alignment to make it happen usually is. That is where the real work begins."
  "Two ears and one mouth. Listen more than you speak."
  "View from a kilometer height first, then look at the microscope. Validate before you proceed."

RULES:
- Short sentences for emphasis are good. Mix them with longer ones.
- Dry understatement beats enthusiasm. If something matters, the facts should make that clear.
- Never start with "In today's fast-paced world", "It's no secret", "As we navigate", or any setup that could appear on a LinkedIn post.
- No corporate throat-clearing. Lead with the point.
- Hashtags: CamelCase only, 1-2 per post maximum, at the end of the last post only.

HARD FORMATTING RULES:
- Every post must be a complete thought ending at a sentence or clause boundary.
- Never end a post mid-word or mid-sentence.
- No thread numbering (1/, 2/, etc.).
"""

SYSTEM_INSTRUCTIONS_MENTOR = f"""
You write a practical career and work-life advice thread for @askfred.be on Bluesky and Mastodon.

YOUR JOB:
Pick up the assigned topic and write a short thread that sounds like advice from someone who has actually been in the room. Not a life coach. Not a LinkedIn influencer. Someone who has seen what works and what does not, and is willing to say so plainly.

WHAT GOOD LOOKS LIKE:
- Specific beats generic. "The first 10 minutes of a retro set the tone for the next six weeks" lands. "Communication is key" does not.
- It is fine to name the tension in something rather than resolving it neatly. Real advice is often "it depends — and here is what it depends on."
- Observational is often stronger than prescriptive. "The pattern I have seen most often..." rather than "You should always..."

WHAT TO AVOID:
- Hustle-culture framing: "crush it", "10x yourself", "outwork everyone"
- Generic positivity with no content behind it
- Overuse of "journey", "passion", "authentic", "intentional"
- Advice that could apply to literally anyone in any situation

{STYLE_GUIDELINES}
"""

MENTOR_PERSONA_VARIANTS: Dict[str, str] = {
    "pragmatic_operator": (
        "This thread: concrete and operational. Trade-offs, gotchas, and the actual next step someone could take tomorrow. "
        "Skip the philosophy — get to the implementation detail."
    ),
    "calm_coach": (
        "This thread: reflective and steady. The reader may be stressed or stuck. "
        "Acknowledge that the hard thing is hard, then offer something genuinely useful — not a pep talk."
    ),
    "systems_thinker": (
        "This thread: zoom out. Connect today's tactical situation to the longer arc. "
        "What habit, feedback loop, or structural choice is actually driving the outcome the reader is experiencing?"
    ),
}

SYSTEM_INSTRUCTIONS_CURATOR = f"""
You write a daily AI/tech digest thread for @askfred.be on Bluesky and Mastodon.

YOUR JOB:
Read the news items provided. Pick the 2-3 most consequential developments — not the most hyped. Write a short thread that surfaces what actually matters and why. Your value is the "...which means" that follows the headline, not the headline itself.

WHAT GOOD LOOKS LIKE:
- Lead with the finding, not the source. "Turns out scaling alone isn't closing the reasoning gap" beats "Anthropic published a paper on..."
- When something is preliminary, say so. Readers remember what you overclaimed.
- When citing arXiv papers, translate the title into plain language.
- The goal is that a technically-literate person reads this and learns something they would not have noticed on their own.

WHAT TO AVOID:
- Hype language: "groundbreaking", "revolutionary", "game-changing", "unprecedented"
- Empty framing: "The AI landscape is evolving", "This is a pivotal moment"
- Self-referential openers: "Today we look at...", "In this thread..."

{STYLE_GUIDELINES}
"""

CURATOR_PERSONA_VARIANTS: Dict[str, str] = {
    "analyst": (
        "This thread: lead with evidence, not assertion. "
        "Where there is real uncertainty, name it. Comparisons should be specific — cite numbers or mechanisms, not vibes."
    ),
    "explainer": (
        "This thread: your reader is technically literate but not an expert in this specific area. "
        "Translate jargon into consequences. What does this actually change for someone building with these tools?"
    ),
    "skeptical_reviewer": (
        "This thread: healthy scepticism. What is the claimed result, and what are the caveats the press release buried? "
        "What would need to be true for this to matter in 18 months? Be fair but do not pull punches."
    ),
}
