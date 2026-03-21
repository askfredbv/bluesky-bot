# Bluesky Daily Poster

This is an automated Python script that generates and posts daily content to Bluesky. It is designed to post professional, positive, interesting, and helpful content, such as "On this day" historical facts or productivity tips.

## Features

- **Automated Content Generation**: Uses Google's Gemini (gemini-2.5-flash) to write engaging daily posts.
- **Daily Themed Schedule**: Automatically selects a topic based on the day of the week (Motivational Monday, Tool Tuesday, Positive Wednesday, Throwback Thursday, Failure Friday, Shoutout Saturday, and Sunday Reset).
- **Interactive Prompts**: 50% chance to append a conversation-starting question to posts to boost engagement.
- **Bilingual Delivery**: Posts are randomly generated in either **English** or **Dutch** (50/50 chance).
- **AI Image Attachments**: Approximately 20% of the time, the script will draft a prompt and use **OpenAI (DALL-E 3)** to generate a corresponding image attachment.
- **Fully Automated**: Runs daily via a GitHub Actions scheduled workflow at 08:00 UTC (09:00 CET / 10:00 CEST).

## Prerequisites

- Python 3.11+
- A [Bluesky](https://bsky.app/) account and App Password
- A [Google Gemini API Key](https://aistudio.google.com/)
- (Optional) An [OpenAI API Key](https://platform.openai.com/) for generating images

## Setup

1. **Clone the repository and install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables:**
   Copy the provided `.env.example` to a new `.env` file:
   ```bash
   cp .env.example .env
   ```
   Fill in your credentials:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   BLUESKY_USERNAME=your.bsky.handle
   BLUESKY_PASSWORD=your_app_password_here
   OPENAI_API_KEY=your_openai_api_key_here  # Optional: For AI image generation
   ```

## Usage

### Run Locally
To generate a post and instantly send it to Bluesky:
```bash
python main.py
```

### GitHub Actions (Automated Run)
This repository includes a `.github/workflows/daily_post.yml` workflow that triggers the script every day. 
To enable this:
1. Go to your repository's **Settings > Secrets and variables > Actions**.
2. Add the following repository secrets:
   - `GEMINI_API_KEY`
   - `BLUESKY_USERNAME`
   - `BLUESKY_PASSWORD`
   - `OPENAI_API_KEY` (Optional)

You can also run the workflow manually anytime by clicking **Run workflow** under the "Actions" tab.
