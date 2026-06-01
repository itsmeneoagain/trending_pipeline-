"""Fetch creator uploads from YouTube Data API v3.

YouTube trending fetch is disabled — only tracked creator channels are monitored.
To re-enable trending, call fetch_trending_videos() from fetch_all_youtube().
"""

import json
import logging
import os
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import YOUTUBE_API_KEY, TREND_FETCH_LIMIT

logger = logging.getLogger(__name__)


def _build_service():
    """Build an authenticated YouTube Data API service client."""
    if not YOUTUBE_API_KEY:
        raise ValueError("YOUTUBE_API_KEY is not set in environment")
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def _parse_video_item(item: dict, source: str) -> dict:
    """Parse a YouTube API video resource into a normalised dict."""
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
    """Load YouTube channel IDs fresh from creators.json at call time.

    Always reads the file on each call so pipeline picks up Notion-synced changes
    without needing a process restart.
    """
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

    # Fall back to env var
    raw = os.getenv("CREATOR_CHANNEL_IDS", "")
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def fetch_trending_videos() -> list[dict]:
    """Fetch top trending gaming videos (disabled by default — kept for re-enabling).

    To re-enable: call this from fetch_all_youtube().
    """
    from src.config import YOUTUBE_REGION_CODE, YOUTUBE_CATEGORY_ID
    try:
        yt = _build_service()
    except ValueError as exc:
        logger.error("YouTube setup failed: %s", exc)
        return []

    videos: list[dict] = []
    try:
        response = yt.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=YOUTUBE_REGION_CODE,
            videoCategoryId=YOUTUBE_CATEGORY_ID,
            maxResults=min(TREND_FETCH_LIMIT, 50),
        ).execute()
        for item in response.get("items", []):
            videos.append(_parse_video_item(item, source="youtube_trending"))
        logger.info("Fetched %d trending YouTube videos", len(videos))
    except HttpError as exc:
        logger.error("YouTube trending API error (HTTP %s): %s", exc.resp.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching YouTube trending: %s", exc)
    return videos


def fetch_creator_videos() -> list[dict]:
    """Fetch recent uploads from all tracked creator channels.

    Reads channel IDs fresh from creators.json on every call so that Notion-synced
    additions are picked up without a restart.

    Returns:
        A list of video dicts tagged with source='youtube_creator'.
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
            logger.error(
                "YouTube search failed for channel %s (HTTP %s): %s",
                channel_id, exc.resp.status, exc,
            )
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
    """Fetch creator videos only (trending disabled).

    Returns:
        A list of video dicts from tracked creator channels.
    """
    creators = fetch_creator_videos()
    logger.info("YouTube total: %d creator videos", len(creators))
    return creators
