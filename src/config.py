"""Configuration module for the trending content pipeline.

Loads all settings from environment variables via .env file.
"""

import os
from dotenv import load_dotenv

# Load .env from project root (two levels up from src/)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


# ── YouTube Data API v3 ─────────────────────────────────────────────
YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_REGION_CODE: str = os.getenv("YOUTUBE_REGION_CODE", "IN")
YOUTUBE_CATEGORY_ID: str = os.getenv("YOUTUBE_CATEGORY_ID", "20")  # Gaming

# Channel IDs to track (dynamically loaded from creators.json, falls back to env)
import json
CREATOR_CHANNEL_IDS: list[str] = []
_creators_path = os.path.join(os.path.dirname(__file__), "..", "creators.json")
if os.path.exists(_creators_path):
    try:
        with open(_creators_path, "r", encoding="utf-8") as _cf:
            _cdata = json.load(_cf)
            CREATOR_CHANNEL_IDS = [cid.strip() for cid in _cdata.get("youtube", []) if cid.strip()]
    except Exception as _ce:
        pass

if not CREATOR_CHANNEL_IDS:
    _creator_ids_raw: str = os.getenv("CREATOR_CHANNEL_IDS", "")
    CREATOR_CHANNEL_IDS = [
        cid.strip() for cid in _creator_ids_raw.split(",") if cid.strip()
    ]


# ── Reddit (PRAW) ───────────────────────────────────────────────────
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT: str = os.getenv(
    "REDDIT_USER_AGENT", "TrendingPipeline/1.0"
)

SUBREDDITS: list[str] = [
    "IndianGaming",
    "gaming",
    "pcgaming",
    "NintendoSwitch",
    "PS5",
    "XboxSeriesX",
    "Games",
    "indiegames",
]

# ── Notion API ───────────────────────────────────────────────────────
NOTION_API_KEY: str = os.getenv("NOTION_API_KEY", "")
NOTION_TRENDING_DB_ID: str = os.getenv("NOTION_TRENDING_DB_ID", "")
NOTION_PIPELINE_DB_ID: str = os.getenv("NOTION_PIPELINE_DB_ID", "")
NOTION_CREATORS_DB_ID: str = os.getenv("NOTION_CREATORS_DB_ID", "")

# ── Gemini API (optional, for curation layer) ───────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# ── Pipeline tuning ─────────────────────────────────────────────────
TREND_FETCH_LIMIT: int = int(os.getenv("TREND_FETCH_LIMIT", "25"))
SCORE_THRESHOLD: int = int(os.getenv("SCORE_THRESHOLD", "30"))

