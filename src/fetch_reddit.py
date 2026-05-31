"""Fetch trending gaming posts from Reddit using RSS feeds.

Uses Reddit's public RSS feeds (no API key required) as the primary method.
Falls back to PRAW if API credentials are configured and RSS fails.

RSS feeds are the most stable free method for accessing Reddit data as of 2025/2026,
following Reddit's tightened API access policies (Responsible Builder Policy).
"""

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional

import requests

from src.config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    SUBREDDITS,
)

logger = logging.getLogger(__name__)

# Posts older than this are filtered out
MAX_AGE_SECONDS = 48 * 3600  # 48 hours
MIN_SCORE_PRAW = 100  # only used for PRAW fallback
POSTS_PER_SUB = 15  # RSS returns ~25 by default, we take top N

# RSS namespaces
_ATOM_NS = "http://www.w3.org/2005/Atom"

# Rate limit for RSS fetches (be respectful)
_RSS_DELAY = 2.0  # seconds between subreddit fetches


# ── RSS-based fetching (primary, no API key needed) ─────────────────

def _fetch_rss(subreddit: str) -> list[dict]:
    """Fetch hot posts from a subreddit via its public RSS feed.

    Args:
        subreddit: Subreddit name (without r/ prefix).

    Returns:
        List of normalised post dicts.
    """
    url = f"https://www.reddit.com/r/{subreddit}/hot.rss"
    headers = {
        "User-Agent": REDDIT_USER_AGENT or "TrendingPipeline/1.0",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("RSS fetch failed for r/%s: %s", subreddit, exc)
        return []

    return _parse_rss_feed(resp.text, subreddit)


def _parse_rss_feed(xml_text: str, subreddit: str) -> list[dict]:
    """Parse Reddit RSS (Atom) feed XML into normalised post dicts.

    Reddit RSS feeds use Atom format. Each <entry> contains:
    - <title>: Post title
    - <link href="...">: Post URL
    - <updated>: Timestamp
    - <content>: HTML content (may contain score info)
    - <author><name>: Author username
    """
    posts: list[dict] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("Failed to parse RSS XML for r/%s: %s", subreddit, exc)
        return []

    # Find all entries (Atom format)
    entries = root.findall(f"{{{_ATOM_NS}}}entry")

    for entry in entries[:POSTS_PER_SUB]:
        try:
            title_el = entry.find(f"{{{_ATOM_NS}}}title")
            link_el = entry.find(f"{{{_ATOM_NS}}}link")
            updated_el = entry.find(f"{{{_ATOM_NS}}}updated")
            content_el = entry.find(f"{{{_ATOM_NS}}}content")
            author_el = entry.find(f"{{{_ATOM_NS}}}author")

            title = title_el.text if title_el is not None else ""
            title = unescape(title) if title else ""

            # Skip empty titles (deleted posts)
            if not title or title.strip() == "":
                continue

            # Get permalink
            permalink = ""
            if link_el is not None:
                permalink = link_el.get("href", "")

            # Parse timestamp
            created_utc = 0.0
            if updated_el is not None and updated_el.text:
                try:
                    dt = datetime.fromisoformat(
                        updated_el.text.replace("Z", "+00:00")
                    )
                    created_utc = dt.timestamp()
                except (ValueError, TypeError):
                    pass

            # Check freshness
            if created_utc > 0 and not _is_fresh(created_utc):
                continue

            # Extract score from content HTML if available
            # Reddit RSS content sometimes has "submitted by" and score info
            score = _extract_score_from_content(content_el)
            num_comments = _extract_comments_from_content(content_el)

            # Get author
            author_name = ""
            if author_el is not None:
                name_el = author_el.find(f"{{{_ATOM_NS}}}name")
                if name_el is not None and name_el.text:
                    author_name = name_el.text.strip("/u/")

            posts.append({
                "title": title,
                "subreddit": subreddit,
                "score": score,
                "num_comments": num_comments,
                "url": permalink,
                "permalink": permalink,
                "created_utc": created_utc,
                "upvote_ratio": 0.0,  # not available via RSS
                "author": author_name,
                "source": "reddit",
                "fetch_method": "rss",
            })

        except Exception as exc:
            logger.debug("Failed to parse RSS entry in r/%s: %s", subreddit, exc)
            continue

    return posts


def _extract_score_from_content(content_el) -> int:
    """Try to extract post score from RSS content HTML.

    Reddit RSS content sometimes includes score info in the HTML.
    Returns 0 if not found (RSS doesn't reliably provide scores).
    """
    if content_el is None or not content_el.text:
        return 0

    # RSS feeds don't consistently include scores
    # We return 0 and rely on the scorer to use other signals
    return 0


def _extract_comments_from_content(content_el) -> int:
    """Try to extract comment count from RSS content HTML.

    Reddit RSS content sometimes includes "[X comments]" links.
    """
    if content_el is None or not content_el.text:
        return 0

    import re
    match = re.search(r"\[(\d+)\s+comments?\]", content_el.text)
    if match:
        return int(match.group(1))
    return 0


def _is_fresh(created_utc: float) -> bool:
    """Return True if the post was created within MAX_AGE_SECONDS."""
    now = datetime.now(timezone.utc).timestamp()
    return (now - created_utc) <= MAX_AGE_SECONDS


# ── PRAW-based fetching (fallback, requires API credentials) ────────

def _praw_available() -> bool:
    """Check if PRAW credentials are configured."""
    return bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


def _fetch_with_praw() -> list[dict]:
    """Fallback: Fetch posts using PRAW (requires API credentials).

    Only called if RSS fails AND PRAW credentials are configured.
    """
    try:
        import praw
        from prawcore.exceptions import PrawcoreException
    except ImportError:
        logger.warning("PRAW not installed — cannot use API fallback")
        return []

    try:
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
    except Exception as exc:
        logger.error("PRAW setup failed: %s", exc)
        return []

    posts: list[dict] = []
    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.hot(limit=10):
                if post.stickied:
                    continue
                if not _is_fresh(post.created_utc):
                    continue
                if post.score < MIN_SCORE_PRAW:
                    continue

                posts.append({
                    "title": post.title,
                    "subreddit": sub_name,
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "url": post.url,
                    "permalink": f"https://reddit.com{post.permalink}",
                    "created_utc": post.created_utc,
                    "upvote_ratio": getattr(post, "upvote_ratio", 0.0),
                    "source": "reddit",
                    "fetch_method": "praw",
                })
        except Exception as exc:
            logger.error("PRAW failed for r/%s: %s", sub_name, exc)

    return posts


# ── Public API ───────────────────────────────────────────────────────

def fetch_reddit_posts() -> list[dict]:
    """Fetch trending gaming posts from configured subreddits.

    Strategy:
        1. Try RSS feeds first (no API key needed, stable, free)
        2. Fall back to PRAW if RSS returns nothing AND credentials exist

    Returns:
        A list of normalised post dicts.
    """
    logger.info("Fetching Reddit posts via RSS feeds (no API key needed)...")
    posts: list[dict] = []

    for sub_name in SUBREDDITS:
        rss_posts = _fetch_rss(sub_name)
        posts.extend(rss_posts)
        logger.info("r/%s: %d posts via RSS", sub_name, len(rss_posts))

        # Be respectful — wait between requests
        time.sleep(_RSS_DELAY)

    if posts:
        logger.info(
            "Reddit RSS total: %d posts from %d subreddits",
            len(posts), len(SUBREDDITS),
        )
        return posts

    # Fallback to PRAW if RSS returned nothing and credentials exist
    if _praw_available():
        logger.warning(
            "RSS returned 0 posts — falling back to PRAW API (requires approval)"
        )
        posts = _fetch_with_praw()
        logger.info("Reddit PRAW fallback: %d posts", len(posts))
        return posts

    logger.warning(
        "No Reddit posts fetched. RSS returned nothing and PRAW not configured."
    )
    return []
