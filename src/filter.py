"""Content relevance filter for @NotAgainNeo pipeline.

Primary: Gemini batch classification — one API call classifies all items.
Fallback: Lightweight regex for when Gemini is not configured.

Gemini understands context regex can't: "OpTic TEXAS SCUMP WATCH PARTY" is
a gaming event but not Neo's content; "Warhammer 40K Official Cinematic Trailer"
is. Batch mode keeps quota usage to 1 call per pipeline run.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# ── Regex pre-pass (catches obvious junk cheaply before Gemini) ──────

_HARD_DROP = re.compile(
    # Mobile live streams / ranked grind
    r"\blive\b|🔴|\[live\]|rank\s*push|crate\s*open|\buc\b"
    r"|conqueror.*rank|#bgmilive|#fflive|free\s*fire.*live|bgmi.*live"
    r"|\d{2,}\+?\s*kills?\s*(world\s*)?record"
    # GTA roleplay
    r"|franklin.*shinchan|shinchan.*franklin|gta\s*\d*\s*real\s*life"
    r"|gta\s+rp\b|gta.*roleplay|starting\s*a\s*fake\s+tow"
    # SMP / day-N series
    r"|\bsmp\b|day\s+\d+.*(minecraft|roblox|survival|hardcore)"
    r"|(minecraft|roblox|survival|hardcore).*day\s+\d+"
    # Automated Reddit threads
    r"|daily\s+(question|discussion)\s+thread|weekly\s+(discussion|question)\s+thread"
    r"|what\s+are\s+you\s+playing\s+thread|simple\s+questions\s+sunday"
    r"|indie\s+sunday\s+hub|pc\s+game\s+discounts.*weekend",
    re.IGNORECASE,
)


def _regex_prefilter(items: list[dict]) -> list[dict]:
    kept, dropped = [], 0
    for item in items:
        if _HARD_DROP.search(item.get("title", "")):
            dropped += 1
        else:
            kept.append(item)
    if dropped:
        logger.info("Regex pre-filter removed %d obvious off-lane items", dropped)
    return kept


# ── Gemini batch classifier ──────────────────────────────────────────

_SYSTEM_PROMPT = """You are a content relevance classifier for the YouTube channel @NotAgainNeo.

CHANNEL PROFILE:
- Hinglish (Hindi+English) gaming discovery channel
- Bio: "kaam se coder, dil se gamer, aur huge cinema lover"
- NEVER covers: let's plays, gameplay series, live streams, ranked grinding,
  crate openings, GTA roleplay, Minecraft/Roblox challenge series, mobile
  gaming streams, esports watch parties, hardware questions, tech support,
  simple game recommendation requests, daily/weekly subreddit threads,
  acquisition posts ("just got this for ₹X")
- DOES cover: official game trailers, reveals, announcements, industry news,
  game launch analysis (especially failures), hidden gems/indie games,
  easter eggs, developer/studio stories, gaming history, price/value analysis,
  franchise explainers, pop-culture tie-ins

Given a numbered list of gaming titles, return ONLY a JSON array of the
index numbers that are relevant for this channel. Be strict — if a title
looks like a let's play, challenge, or generic gameplay video, exclude it."""


def gemini_batch_filter(items: list[dict], client) -> list[dict]:
    """Classify all items in one Gemini call and return only relevant ones.

    Args:
        items: List of trend dicts with at least a 'title' field.
        client: An initialised google.genai.Client (or None to skip).

    Returns:
        Filtered list of relevant items.
    """
    if not items:
        return items

    # Always run the cheap regex pre-pass first
    items = _regex_prefilter(items)
    if not items:
        return items

    if client is None:
        logger.info("Gemini not configured — using regex-only filter (%d items kept)", len(items))
        return items

    # Build numbered title list
    title_lines = "\n".join(f"{i}. {item.get('title', '')}" for i, item in enumerate(items))
    prompt = _SYSTEM_PROMPT + f"\n\nTitles to classify:\n{title_lines}\n\nReturn ONLY a JSON array of relevant index numbers."

    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config,
        )
        indices = json.loads(response.text.strip())
        if not isinstance(indices, list):
            raise ValueError(f"Unexpected response type: {type(indices)}")

        kept = [items[i] for i in indices if isinstance(i, int) and 0 <= i < len(items)]
        dropped = len(items) - len(kept)
        logger.info(
            "Gemini batch filter: %d kept, %d removed as off-lane",
            len(kept), dropped,
        )
        return kept

    except Exception as exc:
        logger.warning("Gemini batch filter failed (%s) — keeping regex-filtered items", exc)
        return items
