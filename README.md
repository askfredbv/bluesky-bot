# Bluesky & Mastodon Daily Poster (v4.4.0) 🚀

![Social Preview](og-image.png)

An autonomous, multi-channel technical broadcasting engine for **Bluesky** and **Mastodon**. This project is part of the **askfred** technical suite, focused on delivering high-signal academic and professional insights to the decentralized web.

## 📡 Live Status
| Component | Status | Last Run | Mode |
| :--- | :--- | :--- | :--- |
| **Broadcaster** | Operational | 2026-04-13 | 💡 Mentor |
| **Signal Strength** | Elite (Async) | -- | -- |

---

## 🚀 Key Features

### 🎓 The Scholar Engine (v4.4)
*   **Academic Priority**: Automatically prioritizes research from **arXiv** (`cs.AI`, `cs.LG`, `cs.RO`) using a weighted ranking algorithm.
*   **Scholar Highlight**: specialized synthesis logic that translates research into pragmatics for IT leadership and business strategy.

### 🛡️ Security & Resilience (The Fortress)
*   **Interaction Circuit Breakers**: Hard caps on daily replies (max 10/session) to prevent mention-spam and API credit exhaustion.
*   **Anti-Spam Sanitization**: Automatic redaction of prompt-injection keywords from user mentions.
*   **API Backoff & Jitter**: Implements exponential backoff and randomized jitter to ensure the bot survives platform instability and avoids rate-limit bans.

### 📡 Multi-Channel Broadcasting
*   **Asynchronous Parallel Delivery**: Concurrent posting to Bluesky and Mastodon for maximum performance and reduced jitter.
*   **Smart Threading**: AI-driven logic that automatically generates a linked **3-5 post thread** with human-like timing.

### 🤖 Dual-Persona Intelligence
*   **Morning Run (08:00 UTC / 10:00 local)**: **The Curator** — High-signal tech and research synthesis.
*   **Afternoon Run (14:30 UTC / 16:30 local)**: **The Mentor** — Professional wisdom, career advice, and work-life balance tips.

## 🛠️ Setup & Configuration

### Environment Variables
Add the following to your GitHub repo settings:

| Secret | Description |
| :--- | :--- |
| `GEMINI_API_KEY` | Google AI Studio key for content generation. |
| `BLUESKY_USERNAME` | Your Bluesky handle (e.g., askfred.be). |
| `BLUESKY_APP_PASSWORD` | App-specific password for your Bluesky account. |
| `MASTODON_ACCESS_TOKEN` | Access token from your Mastodon instance. |
| `MASTODON_API_BASE_URL` | Your Mastodon instance URL (usually `https://mastodon.social`). |
| `OPENAI_API_KEY` | (Optional) Used for DALL-E 3 image generation. |

### Installation
```bash
pip install -r requirements.txt
python main.py
```

## ⚖️ License
MIT License. Built with ❤️ by **askfred**.
