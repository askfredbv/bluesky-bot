# Bluesky Daily Poster (v2.0) 🚀

This is a sophisticated, automated Python system that manages a professional tech presence on Bluesky. It functions as a dual-purpose agent, alternating between breaking news curation and mentorship-style advice.

## 🌟 Features

- **Dual-Post Scheduling**: The bot posts **twice daily** to maximize global reach (08:00 and 14:00 UTC).
- **Dual Personas (Gemini 3.1 Flash)**:
    - **The Curator (Morning Slot)**: Synthesizes breaking tech and AI news from 15+ top-tier RSS feeds (OpenAI, TechCrunch, DeepMind, etc.).
    - **The Mentor (Afternoon Slot)**: Shares professional wisdom, daily themed advice (Motivational Monday, Tool Tuesday, etc.), and historical "On this day" facts.
- **State Persistence**: Uses an automated `seen_articles.json` tracking system to ensure news stories are never repeated.
- **Interactive Prompts**: 90% chance to append a conversation-starting question to posts to boost human engagement.
- **Bilingual Delivery**: Mentor-style posts are randomly generated in either **English** or **Dutch**.
- **AI Image Attachments**: Context-aware images generated via **OpenAI (DALL-E 3)**, with logic to feel more organic/less bot-like (20% chance).
- **Safety Net & Validation**: 
    - **Rescue Logic**: Automatically appends hashtags if the AI forgets them.
    - **Quality Guard**: Blocks repetitive gibberish or suspiciously short "failure" posts.
- **Robust Engineering**: Includes dynamic image compression/resizing (1MB limit), global network timeouts, and detailed error logging for GitHub Actions.

## 🛠️ Prerequisites

- Python 3.11+
- A [Bluesky](https://bsky.app/) account and **App Password** (Required for security)
- A [Google Gemini API Key](https://aistudio.google.com/)
- (Optional) An [OpenAI API Key](https://platform.openai.com/) for images

## 🚀 Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   Copy `.env.example` to `.env` and fill in:
   ```env
   GEMINI_API_KEY=your_key
   BLUESKY_USERNAME=your.handle.bsky.social
   BLUESKY_APP_PASSWORD=your_app_password
   OPENAI_API_KEY=your_openai_key  # Optional
   ```

## 🤖 GitHub Actions (Automation)

The system is fully automated via GitHub Actions. It includes a persistence layer that commits its "seen" state back to your repository.

**To enable:**
1. Go to **Settings > Secrets and variables > Actions**.
2. Add: `GEMINI_API_KEY`, `BLUESKY_USERNAME`, `BLUESKY_APP_PASSWORD`, and `OPENAI_API_KEY`.
3. The workflow runs at `08:00` and `14:00` UTC daily.

## 👨‍💻 Local Usage

Run manually to see what it would post in the current time slot:
```bash
python main.py
```
