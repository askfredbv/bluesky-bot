# Bluesky & Mastodon Daily Poster (v4.2.0) 🚀

![Social Preview](og-image.png)

An autonomous, multi-channel technical broadcasting engine for **Bluesky** and **Mastodon**. This project is part of the **askfred** technical suite, focused on delivering high-signal academic and professional insights to the decentralized web.

## 📡 Live Status
| Component | Status | Last Run | Mode |
| :--- | :--- | :--- | :--- |
| **Broadcaster** | Operational | 2026-04-13 | ☕ Curator |
| **Signal Strength** | High (Scholar) | -- | -- |

---

## 🚀 Key Features

### 🎓 The Scholar Engine (v4.2)
*   **Academic Priority**: Automatically prioritizes and surfaces papers from **arXiv** (`cs.AI`, `cs.LG`, `cs.RO`) over standard tech blogs.
*   **Scholar Highlight**: Specialized synthesis logic that translates complex research into pragmatics for IT leadership and business strategy.

### 📡 Multi-Channel Broadcasting
*   **Dual-Platform Sync**: Concurrent broadcasting to Bluesky and Mastodon with platform-specific character optimizations.
*   **Smart Threading**: AI-driven logic that identifies complex updates and automatically generates a linked **3-5 post thread**.

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
