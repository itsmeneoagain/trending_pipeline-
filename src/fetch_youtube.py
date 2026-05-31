"""Fetch trending gaming videos and creator uploads from YouTube Data API v3."""

import logging
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import (
    YOUTUBE_API_KEY,
    YOUTUBE_REGION_CODE,
    YOUTUBE_CATEGORY_ID,
    CREATOR_CHANNEL_IDS,
    TREND_FETCH_LIMIT,
)

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
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "published_at": snippet.get("publishedAt", ""),
        "video_url": f"https://www.youtube.com/watch?v={item['id']}",
        "source": source,
        "thumbnail_url": snippet.get("thumbnails", {})
        .get("high", {})
        .get("url", ""),
    }


def fetch_trending_videos() -> list[dict]:
    """Fetch the top trending gaming videos for the configured region.

    Returns:
        A list of video dicts from the YouTube trending chart.
    """
    try:
        yt = _build_service()
    except ValueError as exc:
        logger.error("YouTube setup failed: %s", exc)
        return []

    videos: list[dict] = []

    try:
        request = yt.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=YOUTUBE_REGION_CODE,
            videoCategoryId=YOUTUBE_CATEGORY_ID,
            maxResults=min(TREND_FETCH_LIMIT, 50),
        )
        response = request.execute()

        for item in response.get("items", []):
            videos.append(_parse_video_item(item, source="youtube_trending"))

        logger.info("Fetched %d trending YouTube videos", len(videos))

    except HttpError as exc:
        if exc.resp.status == 403:
            logger.error("YouTube API quota exceeded: %s", exc)
        else:
            logger.error("YouTube API error (HTTP %s): %s", exc.resp.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching YouTube trending: %s", exc)

    return videos


def fetch_creator_videos() -> list[dict]:
    """Fetch recent uploads from tracked creator channels.

    Uses search.list to get latest videos from each channel ID in
    CREATOR_CHANNEL_IDS, then enriches with statistics via videos.list.

    Returns:
        A list of video dicts from creator channels.
    """
    if not CREATOR_CHANNEL_IDS:
        logger.debug("No creator channel IDs configured — skipping")
        return []

    try:
        yt = _build_service()
    except ValueError as exc:
        logger.error("YouTube setup failed: %s", exc)
        return []

    video_ids: list[str] = []

    for channel_id in CREATOR_CHANNEL_IDS:
        try:
            search_req = yt.search().list(
                part="id",
                channelId=channel_id,
                order="date",
                type="video",
                maxResults=5,
            )
            search_resp = search_req.execute()
            for item in search_resp.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid:
                    video_ids.append(vid)

        except HttpError as exc:
            logger.error(
                "YouTube search failed for channel %s (HTTP %s): %s",
                channel_id,
                exc.resp.status,
                exc,
            )
        except Exception as exc:
            logger.exception(
                "Unexpected error searching channel %s: %s", channel_id, exc
            )

    if not video_ids:
        return []

    # Batch fetch statistics for discovered videos
    videos: list[dict] = []
    try:
        # Process in chunks of 50 (API limit)
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            detail_req = yt.videos().list(
                part="snippet,statistics",
                id=",".join(chunk),
            )
            detail_resp = detail_req.execute()
            for item in detail_resp.get("items", []):
                videos.append(
                    _parse_video_item(item, source="youtube_creator")
                )

        logger.info("Fetched %d creator videos from %d channels",
                     len(videos), len(CREATOR_CHANNEL_IDS))

    except HttpError as exc:
        logger.error("YouTube video details failed (HTTP %s): %s",
                      exc.resp.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching video details: %s", exc)

    return videos


def fetch_all_youtube() -> list[dict]:
    """Fetch trending + creator videos and return a combined list."""
    trending = fetch_trending_videos()
    creators = fetch_creator_videos()
    combined = trending + creators
    logger.info(
        "YouTube total: %d items (%d trending, %d creator)",
        len(combined),
        len(trending),
        len(creators),
    )
    return combined
