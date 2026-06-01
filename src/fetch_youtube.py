"""Fetch gaming YouTube trending + creator uploads via YouTube Data API v3.

Trending pulls from region US (better PC/console content quality than IN).
An off-lane filter strips let's plays, live streams, and mobile grind videos
before they reach the pipeline — keeping only content aligned with @NotAgainNeo.
"""

import json
import logging
import os
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import YOUTUBE_API_KEY, TREND_FETCH_LIMIT

logger = logging.getLogger(__name__)

# ── Off-lane filter ──────────────────────────────────────────────────
# Titles matching any of these patterns are dropped before scoring.
# Targeting: live streams, mobile grind, GTA roleplay, kill-count videos.
_OFF_LANE = re.compile(
    r"\blive\b"                          # live stream keyword
    r"|🔴"                               # red circle = live indicator
    r"|\[live\]"
    r"|rank\s*push"
    r"|crate\s*open"
    r"|\buc\b"                           # UC (in-game currency) grind
    r"|conqueror.*rank|rank.*conqueror"
    r"|#bgmilive|#fflive"
    r"|free\s*fire.*\blive\b"
    r"|bgmi.*\blive\b"
    r"|franklin.*shinchan|shinchan.*franklin"  # GTA roleplay
    r"|gta\s*\d*\s*real\s*life"
    r"|buying\s+everything.*gta|franklin.*buying"
    r"|\d{2,}\+?\s*kills?\s*(world\s*)?record"   # kill count records
    r"|fat\s+to\s+fit"
    r"|barbie.*ice\s*scream"
    r"|petrol\s*pump.*vr"
    r"|prisoner.*lilyville"
    r"|day\s+\d+.*(minecraft|roblox|survival)"   # day-N series let's plays
    r"|(minecraft|roblox).*day\s+\d+"
    r"|60k\s*uc|uc\s*crate|crate.*opening",
    re.IGNORECASE,
)

# Titles matching these are positively relevant to the channel
_ON_LANE = re.compile(
    r"explained|trailer|reveal|announc|leak|reportedly|confirm"
    r"|easter\s*egg|hidden|secret|reference"
    r"|history|story\s*of|origin|poora\s*safar|years?\s*ago"
    r"|indie|hidden\s*gem|underrat"
    r"|fail|flop|cancel|shutdown|studio|developer|doob"
    r"|vs\.?|better\s*than|worth\s*it|\bprice\b|₹|\$\d"
    r"|review|breakdown|deep.?dive|everything\s*(about|you|need)"
    r"|kisi\s*ne|notice|99\s*%|miss(ed)?",
    re.IGNORECASE,
)


def _is_relevant(title: str) -> bool:
    """Return True if the video is worth including in the Neo pipeline.

    Drops clear off-lane content; gives a small boost to explicitly on-lane
    titles. Generic titles (no strong signal) pass through — the scorer handles
    final ranking.
    """
    if _OFF_LANE.search(title):
        return False
    return True


def _build_service():
    if not YOUTUBE_API_KEY:
        raise ValueError("YOUTUBE_API_KEY is not set in environment")
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def _parse_video_item(item: dict, source: str) -> dict:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "published_at": snippet.get("publishedAt", ""),
        "video_url": f"https://www.youtube.com/watch?v={item['id']}",
        "source": source,
        "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
    }


def _load_creator_ids() -> list[str]:
    """Read creator channel IDs fresh from creators.json on every call."""
    path = os.path.join(os.path.dirname(__file__), "..", "creators.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ids = [cid.strip() for cid in data.get("youtube", []) if cid.strip()]
                if ids:
                    return ids
        except Exception as exc:
            logger.warning("Could not read creators.json: %s", exc)
    raw = os.getenv("CREATOR_CHANNEL_IDS", "")
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def fetch_trending_videos() -> list[dict]:
    """Fetch top trending gaming videos from YouTube (region US, category Gaming).

    Fetches from the US region for better PC/console content diversity.
    Off-lane titles (let's plays, live streams, mobile grind) are filtered out.
    """
    try:
        yt = _build_service()
    except ValueError as exc:
        logger.error("YouTube setup failed: %s", exc)
        return []

    # Use US for trending — richer PC/console/indie game content vs IN
    region = os.getenv("YOUTUBE_TRENDING_REGION", "US")
    category = os.getenv("YOUTUBE_CATEGORY_ID", "20")  # 20 = Gaming

    raw: list[dict] = []
    try:
        response = yt.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=region,
            videoCategoryId=category,
            maxResults=min(TREND_FETCH_LIMIT, 50),
        ).execute()
        for item in response.get("items", []):
            raw.append(_parse_video_item(item, source="youtube_trending"))
        logger.info("Fetched %d raw trending videos (region=%s)", len(raw), region)
    except HttpError as exc:
        logger.error("YouTube trending API error (HTTP %s): %s", exc.resp.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching YouTube trending: %s", exc)

    videos = [v for v in raw if _is_relevant(v["title"])]
    dropped = len(raw) - len(videos)
    if dropped:
        logger.info("Relevance filter removed %d off-lane trending videos (%d kept)", dropped, len(videos))
    return videos


def fetch_creator_videos() -> list[dict]:
    """Fetch recent uploads from tracked creator channels.

    Reads channel IDs fresh from creators.json so Notion-synced additions are
    picked up without a restart. Creator videos are NOT filtered — if you
    added a channel you want everything it publishes.
    """
    channel_ids = _load_creator_ids()
    if not channel_ids:
        logger.info("No creator channel IDs configured — skipping creator fetch")
        return []

    try:
        yt = _build_service()
    except ValueError as exc:
        logger.error("YouTube setup failed: %s", exc)
        return []

    video_ids: list[str] = []
    for channel_id in channel_ids:
        try:
            resp = yt.search().list(
                part="id",
                channelId=channel_id,
                order="date",
                type="video",
                maxResults=5,
            ).execute()
            for item in resp.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid:
                    video_ids.append(vid)
        except HttpError as exc:
            logger.error("YouTube search failed for channel %s (HTTP %s): %s",
                         channel_id, exc.resp.status, exc)
        except Exception as exc:
            logger.exception("Unexpected error searching channel %s: %s", channel_id, exc)

    if not video_ids:
        return []

    videos: list[dict] = []
    try:
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i: i + 50]
            resp = yt.videos().list(part="snippet,statistics", id=",".join(chunk)).execute()
            for item in resp.get("items", []):
                videos.append(_parse_video_item(item, source="youtube_creator"))
        logger.info("Fetched %d creator videos from %d channels", len(videos), len(channel_ids))
    except HttpError as exc:
        logger.error("YouTube video details failed (HTTP %s): %s", exc.resp.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching video details: %s", exc)

    return videos


def fetch_all_youtube() -> list[dict]:
    """Fetch trending (filtered) + creator videos.

    Trending: US region, off-lane filter applied.
    Creators: all uploads from tracked channels, no filter.
    """
    trending = fetch_trending_videos()
    creators = fetch_creator_videos()
    combined = trending + creators
    logger.info(
        "YouTube total: %d items (%d trending after filter, %d creator)",
        len(combined), len(trending), len(creators),
    )
    return combined
