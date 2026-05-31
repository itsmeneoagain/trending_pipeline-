# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

A gaming content trend discovery pipeline for an Indian Hinglish gaming channel. It fetches trending content from YouTube and Reddit, scores items by engagement velocity, optionally curates them with Gemini AI (generating Hinglish hooks/angles), then pushes results to two Notion databases. A Flask server hosts a local web dashboard that reads from the synced JSON files and talks back to Notion via REST endpoints.

## Running the Project

```bash
# Set up virtualenv (Python 3.11)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy env template and fill in keys
cp .env.example .env

# Run the pipeline (fetch → score → curate → push to Notion)
python -m src.main

# Run the local dashboard server (default port 8080)
python -m src.server
```

The dashboard is at `http://localhost:8080` (game-content-radar) and `http://localhost:8080/pipeline` (content pipeline board).

## Environment Variables

All keys are loaded from `.env` at project root via `python-dotenv`. Required:
- `YOUTUBE_API_KEY` — YouTube Data API v3 (region defaults to `IN`, category `20` for Gaming)
- `NOTION_API_KEY` — Notion integration token (`ntn_...`)
- `NOTION_TRENDING_DB_ID` — 32-char hex ID of the Trending Topics database
- `NOTION_PIPELINE_DB_ID` — 32-char hex ID of the Content Pipeline database

Optional:
- `GEMINI_API_KEY` — enables AI curation layer (Gemini 2.5 Flash with Google Search grounding)
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — only needed as PRAW fallback; RSS is used by default
- `CREATOR_CHANNEL_IDS` — comma-separated YouTube channel IDs (also dynamically managed via `creators.json`)
- `SCORE_THRESHOLD` (default `30`), `TREND_FETCH_LIMIT` (default `25`)

## Architecture

### Pipeline (`src/main.py`)
Orchestrates the full run as a sequence of isolated try/except stages:
1. **Fetch** — `fetch_youtube.py` (YouTube trending + creator channels) and `fetch_reddit.py` (8 subreddits via RSS, PRAW fallback)
2. **Score & categorise** — `scorer.py` applies per-source heat formulas, assigns `heat_level` (hot/warm/cool), `tier` (T1 hidden gem / T2 high volume / T3 news), `time_sensitive` flag, then deduplicates using fuzzy title matching (SequenceMatcher ≥0.75)
3. **AI Curation** — `curator.py` calls Gemini 2.5 Flash for the top 10 items to generate a Hinglish hook (`ai_angle`) and `ai_why`; sleeps 12.5s between calls to stay under the free-tier 5 RPM limit
4. **Push** — `notion_push.py` checks for duplicates by exact title match then creates Notion pages; calls `sync_notion_to_local_files()` after push to update `trends.json` and `pipeline.json`

### Dashboard Server (`src/server.py`)
Flask app serving two static HTML pages and a REST API:
- `GET /` or `/game-content-radar.html` — Trending radar dashboard (reads `trends.json`)
- `GET /pipeline` or `/content-pipeline.html` — Content pipeline kanban board (reads `pipeline.json`)
- `GET /trends.json`, `/pipeline.json` — serve the local JSON caches
- `POST /api/pipeline/add` — creates a Notion pipeline card and optionally archives the source trend
- `POST /api/pipeline/update-status` — updates Notion card status by page ID
- `POST /api/pipeline/delete` — archives a Notion pipeline card
- `POST /api/pipeline/update-script` — updates notes + script (stored combined with `=== SCRIPT ===` delimiter)
- `POST /api/refresh` — spawns a background thread running the full pipeline
- `GET /api/creators`, `POST /api/creators/add`, `POST /api/creators/delete` — manage `creators.json`

### Key Data Files
- `trends.json` — local cache of Notion Trending Topics DB, written by `sync_notion_to_local_files()`
- `pipeline.json` — local cache of Notion Content Pipeline DB
- `creators.json` — dynamic list of tracked YouTube channel IDs (takes priority over `CREATOR_CHANNEL_IDS` env var)

### Notion Schema Convention
Notes and scripts are stored in a single `Notes` rich_text property, delimited by `=== SCRIPT ===`. AI angles and "why trending" are appended to `Source Stats` using emoji markers (`🧠 Why it's trending:` / `🎬 Hook & Content Angle:`). Both conventions are parsed back out during `sync_notion_to_local_files()`.

### GitHub Actions
`.github/workflows/fetch-trends.yml` runs `python -m src.main` every 6 hours (cron `0 */6 * * *`) using repository secrets for all API keys.

## Scoring Logic (scorer.py)

YouTube heat score (0–100): views velocity (log-scale, 40%) + like ratio (30%) + comment density (20%) + recency bonus decaying over 48h (10%).

Reddit heat score: RSS posts get a baseline of 35 (being in hot feed is implicit signal) + comment engagement + subreddit niche factor + recency. PRAW posts use full upvote/score data.

Tier classification: T1 hidden gem = <500k views with >4% engagement (YouTube) or niche sub + high score (Reddit); T2 = >1M views or Reddit score ≥5000; T3 = everything else.
