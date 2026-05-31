"""Score, categorise, and deduplicate trending items from all sources."""

import logging
import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from src.config import SCORE_THRESHOLD

logger = logging.getLogger(__name__)

# ── Subreddit size tiers (rough subscriber counts for weighting) ─────
_SUBREDDIT_WEIGHT: dict[str, float] = {
    "gaming": 1.0,          # ~40M — massive, harder to break out
    "Games": 0.9,           # ~3M
    "pcgaming": 0.85,
    "PS5": 0.8,
    "XboxSeriesX": 0.75,
    "NintendoSwitch": 0.8,
    "IndianGaming": 0.6,    # smaller niche = hidden-gem potential
    "indiegames": 0.55,
}

# ── Thresholds for tier assignment ───────────────────────────────────
_T1_MAX_VIEWS = 500_000       # "hidden gem" ceiling
_T1_MIN_ENGAGEMENT = 0.04    # like-to-view ratio
_T2_MIN_VIEWS = 1_000_000    # "high volume" floor
_URGENT_HOURS = 6            # posts younger than this are time-sensitive


def _hours_since(iso_or_epoch) -> float:
    """Return hours elapsed since a timestamp (ISO-8601 str or epoch float)."""
    now = datetime.now(timezone.utc)
    if isinstance(iso_or_epoch, (int, float)):
        dt = datetime.fromtimestamp(iso_or_epoch, tz=timezone.utc)
    elif isinstance(iso_or_epoch, str) and iso_or_epoch:
        # Handle ISO-8601 with or without trailing Z
        cleaned = iso_or_epoch.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    else:
        return 24.0  # default fallback

    delta = (now - dt).total_seconds() / 3600
    return max(delta, 0.1)  # avoid division by zero


# ── Scoring functions ────────────────────────────────────────────────

def _score_youtube(item: dict) -> float:
    """Compute heat_score (0-100) for a YouTube video.

    Formula weights:
        - Views velocity (views / hours since publish) — 40 %
        - Like-to-view ratio                           — 30 %
        - Comment density (comments / views)           — 20 %
        - Recency bonus                                — 10 %
    """
    views = item.get("views", 0)
    likes = item.get("likes", 0)
    comments = item.get("comments", 0)
    hours = _hours_since(item.get("published_at"))

    # Views velocity — log-scale so a 10M-view video doesn't swamp everything
    velocity = views / hours if hours else 0
    velocity_score = min(math.log10(max(velocity, 1)) * 10, 40)

    # Like ratio
    like_ratio = likes / max(views, 1)
    like_score = min(like_ratio * 500, 30)  # 6 % ratio → full marks

    # Comment density
    comment_ratio = comments / max(views, 1)
    comment_score = min(comment_ratio * 2000, 20)  # 1 % → full marks

    # Recency bonus — decays linearly over 48 hours
    recency_score = max(10 - (hours / 48) * 10, 0)

    return round(
        min(velocity_score + like_score + comment_score + recency_score, 100), 1
    )


def _score_reddit(item: dict) -> float:
    """Compute heat_score (0-100) for a Reddit post.

    Handles two data quality levels:
        - PRAW (full data): score, num_comments, upvote_ratio
        - RSS (limited data): may have 0 score, 0 upvote_ratio

    For RSS posts, we give a baseline score for being in the 'hot' feed
    and boost based on available signals (comments, recency, subreddit).

    Formula weights (PRAW):
        - Normalised score (log)                       — 35 %
        - Comment engagement                           — 25 %
        - Upvote ratio                                 — 20 %
        - Subreddit-size factor (smaller = more signal)— 10 %
        - Recency bonus                                — 10 %
    """
    score = item.get("score", 0)
    num_comments = item.get("num_comments", 0)
    upvote_ratio = item.get("upvote_ratio", 0.0)
    sub = item.get("subreddit", "")
    hours = _hours_since(item.get("created_utc"))
    is_rss = item.get("fetch_method") == "rss"

    # Subreddit size factor — niche subs get a slight boost
    sub_weight = _SUBREDDIT_WEIGHT.get(sub, 0.7)
    sub_factor = (1 - sub_weight) * 10  # max 5 points for niche

    # Recency bonus
    recency_score = max(10 - (hours / 48) * 10, 0)

    if is_rss and score == 0:
        # RSS mode: post is in the 'hot' feed so it has implicit relevance
        # Give a baseline of 35 (being in hot feed = meaningful signal)
        baseline = 35.0

        # Comment engagement — log-scale, cap at 25
        comment_norm = min(math.log10(max(num_comments, 1)) * 8, 25)

        return round(
            min(baseline + comment_norm + sub_factor + recency_score, 100),
            1,
        )

    # PRAW mode: full data available
    # Score — log-scale, cap at 35
    score_norm = min(math.log10(max(score, 1)) * 8, 35)

    # Comment engagement — log-scale, cap at 25
    comment_norm = min(math.log10(max(num_comments, 1)) * 8, 25)

    # Upvote ratio bonus (0.95+ is very strong consensus)
    ratio_score = max((upvote_ratio - 0.5) * 40, 0)  # caps at 20
    ratio_score = min(ratio_score, 20)

    return round(
        min(score_norm + comment_norm + ratio_score + sub_factor + recency_score, 100),
        1,
    )



# ── Categorisation ───────────────────────────────────────────────────

def _classify_heat(score: float) -> str:
    """Map numeric score to heat level label."""
    if score >= 70:
        return "hot"
    elif score >= SCORE_THRESHOLD:
        return "warm"
    return "cool"


def _classify_tier(item: dict, score: float) -> str:
    """Determine content tier for production prioritisation.

    T1 — Hidden Gem: moderate reach but exceptional engagement.
    T2 — High Volume: massive views / scores, proven interest.
    T3 — News / List: topical but lower engagement.
    """
    source = item.get("source", "")

    if source.startswith("youtube"):
        views = item.get("views", 0)
        likes = item.get("likes", 0)
        engagement = likes / max(views, 1)

        if views < _T1_MAX_VIEWS and engagement >= _T1_MIN_ENGAGEMENT:
            return "T1"
        if views >= _T2_MIN_VIEWS:
            return "T2"
        return "T3"

    if source == "reddit":
        post_score = item.get("score", 0)
        comments = item.get("num_comments", 0)
        sub = item.get("subreddit", "")

        # Hidden gem: niche sub + strong engagement
        if sub in ("IndianGaming", "indiegames") and post_score >= 200:
            return "T1"
        if post_score >= 5000:
            return "T2"
        return "T3"

    return "T3"


def _is_time_sensitive(item: dict, score: float) -> bool:
    """Return True if the item is fresh and gaining traction fast."""
    ts = item.get("published_at") or item.get("created_utc")
    if ts is None:
        return False
    hours = _hours_since(ts)
    return hours <= _URGENT_HOURS and score >= 50


# ── Deduplication ────────────────────────────────────────────────────

def _normalise(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _is_duplicate(a: str, b: str, threshold: float = 0.75) -> bool:
    """Fuzzy title match using SequenceMatcher."""
    return SequenceMatcher(None, _normalise(a), _normalise(b)).ratio() >= threshold


def deduplicate(items: list[dict]) -> list[dict]:
    """Remove near-duplicate items, keeping the higher-scored one."""
    seen: list[dict] = []
    for item in sorted(items, key=lambda x: x.get("heat_score", 0), reverse=True):
        title = item.get("title", "")
        if any(_is_duplicate(title, s.get("title", "")) for s in seen):
            logger.debug("Dedup: dropping '%s'", title[:60])
            continue
        seen.append(item)
    dropped = len(items) - len(seen)
    if dropped:
        logger.info("Deduplication removed %d items", dropped)
    return seen


# ── Public API ───────────────────────────────────────────────────────

def score_and_categorise(items: list[dict]) -> list[dict]:
    """Score, categorise, and deduplicate a list of raw trend items.

    Each item is annotated in-place with:
        heat_score, heat_level, tier, time_sensitive

    Items below SCORE_THRESHOLD are dropped.

    Returns:
        Deduplicated list sorted by heat_score descending.
    """
    scored: list[dict] = []

    for item in items:
        source = item.get("source", "")

        if source.startswith("youtube"):
            heat = _score_youtube(item)
        elif source == "reddit":
            heat = _score_reddit(item)
        else:
            heat = 0.0

        item["heat_score"] = heat
        item["heat_level"] = _classify_heat(heat)
        item["tier"] = _classify_tier(item, heat)
        item["time_sensitive"] = _is_time_sensitive(item, heat)

        if heat >= SCORE_THRESHOLD:
            scored.append(item)
        else:
            logger.debug("Below threshold (%.1f): '%s'", heat, item.get("title", "")[:60])

    logger.info(
        "Scoring: %d / %d items above threshold (%d)",
        len(scored),
        len(items),
        SCORE_THRESHOLD,
    )

    deduped = deduplicate(scored)
    return sorted(deduped, key=lambda x: x["heat_score"], reverse=True)
