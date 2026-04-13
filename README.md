# AskFred: The Scholar-Level Tech Mentor (v4.1.0) 🎓

AskFred is a sophisticated, autonomous content engine designed to manage a professional technical presence across the decentralized web (**Bluesky** and **Mastodon**). 

Starting with v4.1 (The Scholar Update), AskFred has evolved from a news aggregator into a **Technical Authority**, prioritizing peer-reviewed academic research over general industry news.

## 🚀 Key Features

### 🎓 The Scholar Engine (v4.1)
*   **Academic Priority**: Automatically prioritizes and surfaces papers from **arXiv** (`cs.AI`, `cs.LG`, `cs.RO`) over standard tech blogs.
*   **Scholar Highlight**: Specialized synthesis logic that translates complex research into pragmatics for IT leadership and business strategy.

### 📡 Multi-Channel Broadcasting (v4.0)
*   **Dual-Platform Sync**: Concurrent broadcasting to Bluesky and Mastodon with platform-specific character optimizations.
*   **Smart Threading**: AI-driven logic that identifies complex updates and automatically generates a linked **3-5 post thread** to provide deep context.

### 🤖 Dual-Persona Intelligence
*   **Morning Run (08:00 UTC)**: **The Curator** — High-signal tech and research synthesis.
*   **Afternoon Run (14:00 UTC)**: **The Mentor** — Professional IT wisdom, career advice, and work-life balance tips.

### 🦾 Improved Interaction Loop
*   **Continuous Engagement**: Monitors notifications and handles replies in the "Mentor" persona, fostering genuine community growth.

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
MIT License. Built with ❤️ for the technical community.
