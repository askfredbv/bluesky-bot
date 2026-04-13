# AskFred: The Multi-Channel Tech Mentor (v4.0.0) 🚀

AskFred is a sophisticated, autonomous content engine designed to manage a professional technical presence across the decentralized web (**Bluesky** and **Mastodon**). It functions as a dual-purpose agent, alternating between technical curation and professional mentorship.

## 🌟 New in v4.0: "The Multi-Channel Expansion"

- **Multi-Platform Broadcasting**: Simultaneous automated posting to **Bluesky** and **Mastodon**.
- **Smart Threading Engine**: AI-driven logic that identifies complex technical updates and automatically generates a linked **3-5 post thread** to provide deep context without hitting character limits.
- **Improved Interaction Loop**: Monitors notifications and handles replies in the "Mentor" persona, fostering genuine community growth.
- **Accessibility Suite**:
    - **CamelCase Hashtags**: All hashtags are formatted for screen-reader compatibility (e.g., `#OpenSource`).
    - **AI-Enhanced Alt Text**: Context-aware accessibility descriptions are generated for every AI image using a dedicated vision-pass.

## 🤖 Core Personas (Gemini 3.1 Flash)

- **The Curator (Morning Slot - 08:00 UTC)**: Synthesizes breaking tech and AI news from 15+ top-tier RSS feeds.
- **The Mentor (Afternoon Slot - 14:00 UTC)**: Shares professional wisdom, daily themed advice, and historical "On this day" facts in **English** or **Dutch**.

## 🛠️ Prerequisites

- Python 3.11+
- [Bluesky](https://bsky.app/) account & App Password.
- [Mastodon](https://mastodon.social/) account & Access Token.
- [Google Gemini API Key](https://aistudio.google.com/)
- (Optional) [OpenAI API Key](https://platform.openai.com/) for images.

## 🚀 Setup & Automation

1. **GitHub Secrets**: Add the following to your repo settings:
   - `GEMINI_API_KEY`, `BLUESKY_USERNAME`, `BLUESKY_APP_PASSWORD`
   - `MASTODON_ACCESS_TOKEN`, `MASTODON_API_BASE_URL` (usually `https://mastodon.social`)
   - `OPENAI_API_KEY` (Optional)

2. **Persistence**: The bot automatically commits its internal "memory" (`seen_articles.json`, `replied_to.json`) back to the repo using GitHub Actions.

## 👨‍💻 Local Usage

```bash
pip install -r requirements.txt
python main.py
```
