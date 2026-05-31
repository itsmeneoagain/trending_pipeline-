"""Curate content angles and research trending topics using the modern Google Gen AI SDK."""

import json
import logging
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# ── Keyword sets for rule-based content angle matching ───────────────

_STORY_GAMES = frozenset([
    "tekken", "mortal kombat", "resident evil", "witcher", "god of war", "red dead",
    "cyberpunk", "final fantasy", "dark souls", "elden ring", "zelda", "halo",
    "mass effect", "dragon age", "assassin's creed", "batman", "spider-man", "max payne",
    "street fighter", "devil may cry", "metal gear", "silent hill", "bioshock",
    "fallout", "elder scrolls", "doom", "gta", "half-life", "portal", "dishonored",
])
_EASTER_PATTERNS = frozenset([
    "easter egg", "hidden", "secret", "reference", "cameo", "tribute", "saddest",
    "connection", "easter", "you missed",
])
_HISTORY_PATTERNS = frozenset([
    "history", "story of", "what happened", "how did", "why did", "evolution",
    "origin", "rise of", "fall of", "remember when", "retro", "classic", "nostalgia",
    "anniversary", "years ago", "old school", "comeback",
])
_INDUSTRY_PATTERNS = frozenset([
    "million copies", "ban", "lawsuit", "layoffs", "shut down", "acquired",
    "controversy", "apolog", "drama", "backlash", "studio closed", "cancelled",
    "price increase", "microtransaction", "scam", "refund",
])
_INDIE_PATTERNS = frozenset([
    "indie", "solo dev", "small team", "my game", "our game", "working on",
    "making a game", "demo release", "kickstarter", "early access", "wishlist",
])
_NEWS_PATTERNS = frozenset([
    "rated", "leaked", "reportedly", "confirmed", "trailer", "reveal",
    "announcement", "pre-order", "release date", "coming soon", "just announced",
    "expansion", "dlc", "season",
])
_GENRE_PATTERNS = frozenset([
    "co-op", "multiplayer", "horror games", "best games", "top games",
    "games like", "similar to", "recommend", "roguelike", "rpg list", "strategy games",
])
_ESPORTS_PATTERNS = frozenset([
    "world record", "rank push", "conqueror", "grandmaster", "tournament",
    "bmps", "pro player", "ranked match", "global rank",
])
_MOD_PATTERNS = frozenset([
    "but you can", "but i", "mod", "as pokemon", "in real life", "100 days",
    "challenge", "bought everything", "purchased",
])


def _smart_fallback_angle(item: dict) -> dict:
    """Channel-strategy-aligned fallback angle when Gemini is unavailable or fails.

    Maps trends to the 6 core content angles — game intro, topical explainer,
    breaking news, pop-culture tie-in, profile/dev story, or gaming history —
    instead of generic 'reaction/let's-play' suggestions.
    """
    title = item.get("title", "")
    source = item.get("source", "")
    tier = item.get("tier", "T3")
    is_time_sensitive = item.get("time_sensitive", False)
    t = title.lower()
    short = title[:55] + ("…" if len(title) > 55 else "")

    is_story_game = any(g in t for g in _STORY_GAMES)
    is_easter = any(k in t for k in _EASTER_PATTERNS)
    is_history = any(k in t for k in _HISTORY_PATTERNS)
    is_industry = any(k in t for k in _INDUSTRY_PATTERNS)
    is_indie = any(k in t for k in _INDIE_PATTERNS)
    is_news = any(k in t for k in _NEWS_PATTERNS)
    is_genre = any(k in t for k in _GENRE_PATTERNS)
    is_esports = any(k in t for k in _ESPORTS_PATTERNS)
    is_mod = any(k in t for k in _MOD_PATTERNS)

    if is_time_sensitive and is_news:
        return {
            "angle": f"Fast 30-sec Short: '{short}' — 'reportedly' ya 'just confirmed' framing. First-mover ban jao is news pe.",
            "why": "Time-sensitive — sabse pehle cover karne ka chance.",
        }
    if is_easter:
        return {
            "angle": f"'{short}' ka hidden connection reveal karo — 'Kya tumne yeh notice kiya?' hook. Pop culture tie-in, zero Hinglish coverage.",
            "why": "Easter egg / hidden reference angle — high hook potential.",
        }
    if is_story_game and any(k in t for k in ("story", "lore", "history", "part", "chapter", "origin")):
        return {
            "angle": f"'{short}' ki puri kahani ek Reel mein — cinematic cuts, mystery opener. 'Yeh game itna dark kyun hai?' type hook.",
            "why": "Game story breakdown — almost no competition in Hinglish.",
        }
    if is_history:
        return {
            "angle": f"'{short}' — 'Yeh kab hua aur kyun?' explainer. Gaming history as cinematic storytelling, direct-to-camera no-filter voice.",
            "why": "Gaming history explainers drive strong organic reach.",
        }
    if is_industry:
        return {
            "angle": f"'{short}' ka real story — industry drama ka player impact angle. Indian pricing ya developer struggle pe focus.",
            "why": "Industry news — oblique angle avoids crowded coverage.",
        }
    if tier == "T1" and (is_indie or source == "reddit"):
        return {
            "angle": f"Hidden gem intro: '{short}' — zero Hinglish coverage. Hook: 'Koi nahi jaanta yeh game exist karta hai — aur yeh galti hai.'",
            "why": "Open-lane T1 discovery — channel's core positioning.",
        }
    if is_genre:
        return {
            "angle": f"'{short}' pe Tier 3 list banao — Indian context add karo: pricing, availability, PC/mobile support, regional relevance.",
            "why": "Genre list content drives recommendations and repeat views.",
        }
    if is_news:
        return {
            "angle": f"'{short}' — news ko sideways cover karo. 'Indian gamers ke liye iska kya matlab hai?' Avoid obvious headline take.",
            "why": "Trending news — T2 adjacency approach keeps us differentiated.",
        }
    if is_esports:
        return {
            "angle": f"'{short}' — player profile ya competitive scene ka 'rise of Indian esports' angle. Oblique T2 — bada game, chhota unexplored story.",
            "why": "Esports achievement trending — Indian competitive gaming storyline.",
        }
    if is_mod:
        return {
            "angle": f"'{short}' — yeh mod ya challenge Indian audience ne nahi dekha Hinglish mein. 'Games within games' ya 'yeh possible hai?' hook.",
            "why": "Viral mod/challenge content — oblique T2 angle on base game.",
        }
    if is_indie:
        return {
            "angle": f"'{short}' — 'Ek chhota team ne yeh banaya' developer story angle. Small studio, big ambition, zero Hinglish coverage.",
            "why": "Indie game profile — high discovery value, T1 lane.",
        }
    return {
        "angle": f"'{short}' — pehle check karo: T1 gem hai, oblique T2 angle hai, ya developer story hai? Phir 30-sec Hinglish hook draft karo.",
        "why": f"Trending on {source} — manual strategy fit check needed.",
    }


# Initialize client if key is configured
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini AI Client initialized successfully using modern google-genai SDK.")
    except Exception as e:
        logger.error("Failed to initialize Gemini Client: %s", e)
else:
    logger.warning("GEMINI_API_KEY not found in environment. AI curation will be skipped.")


def curate_item(item: dict) -> dict:
    """Research and generate a creative content angle for a single trend item.

    Uses Gemini 2.5 Flash with Google Search grounding.
    Gracefully falls back to standard generation if the search tool is unavailable,
    or returns empty values if the API key is not configured or fails.

    Args:
        item: Scored trend dictionary containing 'title', 'source', etc.

    Returns:
        Dict with 'angle' and 'why' fields.
    """
    if not client:
        return _smart_fallback_angle(item)

    title = item.get("title", "")
    source = item.get("source", "")
    stats = item.get("stats", "") or "No stats available"

    prompt = f"""You are a content-discovery assistant for an Indian gaming channel (YouTube Shorts + Instagram Reels) making Hinglish (Hindi + English) content.

The following gaming topic is trending right now:
Title: "{title}"
Source: {source}
Stats: {stats}

Please research this topic on the web to understand why it is currently popular (recent trailers, announcements, player discussions, or drama).
Then, generate a creative content angle / video hook idea tailored exactly to our channel strategy profile.

## CHANNEL PROFILE & ALIGNMENT
- Identity: Hinglish (Hindi + English) gaming discovery channel. Casual, direct-to-camera, no-filter voice.
- Core Positioning: Covers underrated, indie, and overlooked games — and stories — that the big Hindi/Hinglish creators ignore. We do NOT compete head-on on mainstream titles.
- Tiers to align:
  - Tier 1 (Hidden Gem): Underrated game with little/no Hinglish coverage.
  - Tier 2 (Big-Search Adjacency): Massive game approached from an oblique/adjacent angle, never the obvious take.
  - Tier 3 (Genre List): Themed best-of lists.
- Content Angles (must choose one):
  - Game introductions ("you've never heard of this — here's why it's special")
  - Topical explainers
  - Breaking news (time-sensitive, report leaks as "reportedly" or "leaked")
  - Movie/pop-culture tie-ins & easter eggs
  - Profile pieces (industry people / developers)
  - Game history & cinematic storytelling
- Format: Shorts ~35-45s or Reels ~22-28s. Narrative-first, mystery-building hooks, cinematic. Avoid tutorials and roasts.
- CRITICAL: NEVER suggest let's plays, reaction videos, commentary streams, "covering the buzz", or "community reactions" type content. Always pick exactly one of the 6 core angles above.

Please output the following two fields:
1. "why": A short, 1-sentence explanation of why it is trending (max 15 words).
2. "angle": A highly engaging, creative Hinglish content angle / video hook idea for a 60-second video (Shorts/Reels) exactly matching our profile. Write in our casual, direct-to-camera voice. Give a specific hook and angle (max 40 words). Must be one of the 6 core angles.

Respond with ONLY a valid JSON object matching this schema:
{{
  "why": "why it is trending",
  "angle": "creative Hinglish hook and content angle matching our positioning"
}}"""

    # Attempt 1: Gemini with Google Search grounding enabled
    try:
        logger.debug("Requesting AI curation for: '%s' with Google Search grounding...", title[:50])
        
        # Configure tool and generation parameters
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(
            tools=[grounding_tool],
            temperature=0.7,
            response_mime_type="application/json"
        )
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config
        )
        
        result = json.loads(response.text.strip())
        if isinstance(result, dict) and "angle" in result and "why" in result:
            return result

    except Exception as exc:
        logger.debug("Gemini with search grounding failed, retrying without tools: %s", exc)
        
        # Attempt 2: Fallback to standard text generation without search tools
        try:
            config_fallback = types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json"
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config_fallback
            )
            result = json.loads(response.text.strip())
            if isinstance(result, dict) and "angle" in result and "why" in result:
                return result
        except Exception as exc_fallback:
            logger.error("Gemini standard generation fallback failed for '%s': %s", title[:50], exc_fallback)

    # Both Gemini attempts failed — use rule-based strategy-aligned fallback
    return _smart_fallback_angle(item)


def curate_trends(items: list[dict], limit: int = 10) -> list[dict]:
    """Curate a list of items, annotating them in-place with 'ai_angle' and 'ai_why'.

    Limits curation to the top `limit` items to respect free-tier rate limits and speed up execution.

    Args:
        items: List of scored trend dictionaries.
        limit: Maximum number of items to curate.

    Returns:
        The annotated list of items.
    """
    if not client:
        logger.info("Skipping AI Curation Layer (GEMINI_API_KEY not configured).")
        for item in items:
            item["ai_angle"] = ""
            item["ai_why"] = item.get("why") or f"Trending on {item.get('source')}."
        return items

    logger.info("━━━ AI Curation Layer starting (curating top %d items) ━━━", limit)
    
    curated_count = 0
    for idx, item in enumerate(items):
        if idx >= limit:
            # For remaining items, add blank or simple default fields
            item["ai_angle"] = ""
            item["ai_why"] = item.get("why") or f"Trending on {item.get('source')}."
            continue

        start_time = time.monotonic()
        curation = curate_item(item)
        
        item["ai_angle"] = curation.get("angle", "")
        # Update the 'why' field with the researched explanation
        item["ai_why"] = curation.get("why", "") or item.get("why", "")

        curated_count += 1
        elapsed = time.monotonic() - start_time
        logger.info("AI Curation [%d/%d] done: '%s' (took %.1fs)", curated_count, limit, item.get("title", "")[:40], elapsed)

        # To comply with the standard Gemini Free Tier 5 Requests Per Minute (RPM) limit
        # (which requires at least 12 seconds between requests), we sleep 12.5 seconds.
        if idx < limit - 1:
            time.sleep(12.5)

    logger.info("━━━ AI Curation Layer complete: %d items curated ━━━", curated_count)
    return items
