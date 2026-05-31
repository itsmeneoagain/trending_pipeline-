"""Curate content angles and research trending topics using the modern Google Gen AI SDK."""

import json
import logging
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

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
        return {
            "angle": "AI Curation skipped (no GEMINI_API_KEY configured).",
            "why": "Trending on " + item.get("source", "web")
        }

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

Please output the following two fields:
1. "why": A short, 1-sentence explanation of why it is trending (max 15 words).
2. "angle": A highly engaging, creative Hinglish content angle / video hook idea for a 60-second video (Shorts/Reels) exactly matching our profile. Write in our casual, direct-to-camera voice. Provide the specific hook and angle idea (max 40 words).

Respond with ONLY a valid JSON object matching this schema:
{
  "why": "why it is trending",
  "angle": "creative Hinglish hook and content angle matching our positioning"
}"""

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

    # Return safe default values if both generation attempts failed
    return {
        "angle": f"Create a Short/Reel covering the latest buzz around {title}. Focus on community reactions.",
        "why": f"Trending topic on {source}."
    }


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
