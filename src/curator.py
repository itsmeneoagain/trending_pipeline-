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
    "forza", "ace combat", "no law", "star citizen", "crimson desert", "black flag",
])
_EASTER_PATTERNS = frozenset([
    "easter egg", "hidden", "secret", "reference", "cameo", "tribute", "saddest",
    "connection", "easter", "you missed", "notice", "kisi ne", "99%",
])
_HISTORY_PATTERNS = frozenset([
    "history", "story of", "what happened", "how did", "why did", "evolution",
    "origin", "rise of", "fall of", "remember when", "retro", "classic", "nostalgia",
    "anniversary", "years ago", "old school", "comeback", "poora safar", "saal",
    "journey", "first time",
])
_INDUSTRY_PATTERNS = frozenset([
    "million copies", "ban", "lawsuit", "layoffs", "shut down", "acquired",
    "controversy", "apolog", "drama", "backlash", "studio closed", "cancelled",
    "price increase", "microtransaction", "scam", "refund", "negative review",
    "wishlist", "flopped", "doob", "failed", "failure",
    "khatam", "asli kahani", "din mein khatam", "dooba", "band ho",
])
_INDIE_PATTERNS = frozenset([
    "indie", "solo dev", "small team", "my game", "our game", "working on",
    "making a game", "demo release", "kickstarter", "early access", "wishlist",
    "tuffted", "tufted", "rug",
])
_NEWS_PATTERNS = frozenset([
    "rated", "leaked", "reportedly", "confirmed", "trailer", "reveal",
    "announcement", "pre-order", "release date", "coming soon", "just announced",
    "expansion", "dlc", "season", "open beta", "playtest",
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
    """Rule-based angle generator tuned to @NotAgainNeo's 10 proven formats.

    Ordered by actual channel view performance:
      1. KISI NE NOTICE KIYA?      (3,123 views) — hidden developer detail
      2. GAMER IS BEING REPLACED   (2,265 views) — AI/automation angle
      3. PHIR BHI DOOB GAYA        (1,924 views) — launch failure story
      4. GAME EXPLAINED            (1,201 views) — franchise story/lore
      5. HIDDEN GEM INTRO          (1,788 views) — underrated game discovery
      6. POORA SAFAR               (1,297 views) — franchise history
      7. 99% LOGON NE MISS KIYA    (1,042 views) — movie/pop-culture tie-in
      8. KOI BAAT KYUN NAHI KAR RAHA? (954 views) — overlooked game
      9. RS X NE RS Y KO HARAYA    (919  views) — Indian pricing comparison
     10. ASLI KAHANI               (738  views) — behind-the-scenes drama
    """
    title = item.get("title", "")
    source = item.get("source", "")
    tier = item.get("tier", "T3")
    is_time_sensitive = item.get("time_sensitive", False)
    t = title.lower()
    short = title[:55] + ("…" if len(title) > 55 else "")

    is_story_game  = any(g in t for g in _STORY_GAMES)
    is_easter      = any(k in t for k in _EASTER_PATTERNS)
    is_history     = any(k in t for k in _HISTORY_PATTERNS)
    is_industry    = any(k in t for k in _INDUSTRY_PATTERNS)
    is_indie       = any(k in t for k in _INDIE_PATTERNS)
    is_news        = any(k in t for k in _NEWS_PATTERNS)
    is_esports     = any(k in t for k in _ESPORTS_PATTERNS)
    is_mod         = any(k in t for k in _MOD_PATTERNS)
    is_cinema      = any(k in t for k in ("movie", "film", "series", "netflix", "trailer", "web series"))
    is_pricing     = any(k in t for k in ("price", "cost", "₹", "dollar", "expensive", "cheap", "budget", "sale", "discount"))
    is_ai          = any(k in t for k in ("replace", " ai ", "automat", "robot", " job ", "automation", "artificial"))
    is_failure     = any(k in t for k in ("fail", "flop", "doob", "wishlist", "negative review", "shutdown", "cancelled", "bankrupt", "layoff"))

    # FORMAT 1: "KISI NE NOTICE KIYA?" — hidden/developer-confirmed detail, easter egg
    if is_easter:
        return {
            "angle": (
                f"KISI NE NOTICE KIYA?: 'Bhai, '{short}' mein ek cheez hai "
                f"jo developer ne khud confirm ki — aur sirf 1% log jaante hain.' "
                f"Zero Hinglish coverage."
            ),
            "why": "Hidden detail / easter egg — channel's #1 proven format (3,123 views).",
        }

    # FORMAT 2: "GAMER IS BEING REPLACED" — AI / automation / real-job angle
    if is_ai:
        return {
            "angle": (
                f"GAMER IS BEING REPLACED: 'Ye game tumhe apni hi cheez se replace "
                f"karna sikhata hai.' '{short}' ka real-world AI/job angle."
            ),
            "why": "AI / automation angle — channel's #2 proven format (2,265 views).",
        }

    # FORMAT 3 + 10: "PHIR BHI DOOB GAYA" / "ASLI KAHANI" — launch failure, drama
    if is_failure or is_industry:
        return {
            "angle": (
                f"PHIR BHI DOOB GAYA: '[Budget/wishlists] tha, phir bhi doob gaya.' "
                f"'{short}' ki asli kahani — Concord/Outbound style failure story."
            ),
            "why": "Game failure / studio drama — channel's #3 proven format (1,924 views).",
        }

    # FORMAT 7: "99% LOGON NE MISS KIYA" — movie/cinema/pop-culture connection
    if is_cinema:
        return {
            "angle": (
                f"99% LOGON NE MISS KIYA: '[Movie/Series] mein '{short}' ka easter egg "
                f"chhupa hai jo director ne confirm kiya.' RE Movie + Forza style cinema tie-in."
            ),
            "why": "Cinema connection — Neo is a film lover, proven 1,042-view format.",
        }

    # FORMAT 9: "RS X NE RS Y KO HARAYA" — Indian pricing / budget vs AAA
    if is_pricing:
        return {
            "angle": (
                f"RS X NE RS Y KO HARAYA: 'Rs[price] ke '{short}' ne Rs[AAA] waale game ko "
                f"kaise haraya? India mein worth it hai?' Direct comparison."
            ),
            "why": "Indian pricing angle — proven 919-view Neo format.",
        }

    # FORMAT 6: "POORA SAFAR" — franchise / series history in one Short
    if is_history:
        return {
            "angle": (
                f"POORA SAFAR: '[X] saal. [Y] games. '{short}' ka poora safar.' "
                f"Franchise history cinematic Short — '13 saal. 6 desh.' style."
            ),
            "why": "Franchise history — proven 1,297-view Neo format.",
        }

    # FORMAT 8: "KOI BAAT KYUN NAHI KAR RAHA?" — overlooked news / upcoming game
    if is_time_sensitive or (is_news and tier in ("T1", "T2")):
        return {
            "angle": (
                f"KOI BAAT KYUN NAHI KAR RAHA?: '{short}' aa raha hai / confirm hua — "
                f"aur koi baat kyun nahi kar raha? Fast first-mover Short. 007 First Light style."
            ),
            "why": "Overlooked news — first-mover, 954-view proven format.",
        }

    # FORMAT 5: "HIDDEN GEM INTRO" — T1 underrated / indie game
    if tier == "T1" or is_indie:
        return {
            "angle": (
                f"HIDDEN GEM INTRO: 'Ye game exist karta hai aur koi baat nahi karta. "
                f"Aaj hum batate hain.' '{short}' — Hacknet style zero-coverage discovery."
            ),
            "why": "T1 hidden gem — open lane, channel's proven 1,788-view format.",
        }

    # FORMAT 4: "GAME EXPLAINED" — story/lore/mechanics of known franchise
    if is_story_game and any(k in t for k in ("story", "lore", "explained", "breakdown", "origin", "saga", "journey")):
        return {
            "angle": (
                f"GAME EXPLAINED: 'Poori kahani 60 seconds mein.' '{short}' — "
                f"Black Flag Explained / AC Explained style cinematic story Short."
            ),
            "why": "Game story explainer — proven 1,201-view Black Flag/AC Neo format.",
        }

    if is_esports:
        return {
            "angle": (
                f"KOI BAAT KYUN NAHI KAR RAHA? (oblique): '{short}' — competitive scene ka "
                f"Indian angle. 'Indian players yahan kyun dominate karte hain?' Sideways T2 take."
            ),
            "why": "Esports — oblique Indian competitive angle.",
        }

    if is_mod:
        return {
            "angle": (
                f"HIDDEN GEM INTRO (oblique): '{short}' mein jo mechanic/mod hai — "
                f"'Ye possible hai?' ya developer story approach. Avoid direct coverage."
            ),
            "why": "Viral mod — oblique T2 sideways angle on base game.",
        }

    return {
        "angle": (
            f"'{short}' — check: KISI NE NOTICE KIYA?, PHIR BHI DOOB GAYA, "
            f"HIDDEN GEM INTRO, ya POORA SAFAR? Phir Hinglish hook draft karo."
        ),
        "why": f"Trending on {source} — manual Neo format matching needed.",
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
    Falls back to _smart_fallback_angle (Neo's 10 proven formats) on any failure.

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

    prompt = (
        'You are a content-strategy AI for the YouTube Shorts channel "@NotAgainNeo" '
        "— a Hinglish gaming discovery channel.\n\n"
        "## TRENDING ITEM TO ANALYSE\n"
        f'Title: "{title}"\n'
        f"Source: {source}\n"
        f"Stats: {stats}\n\n"
        "Research this on the web to understand why it's trending, then generate ONE "
        "video idea using the channel's PROVEN formats.\n\n"
        "## CHANNEL IDENTITY\n"
        '- Bio: "kaam se coder, dil se gamer, aur huge cinema lover"\n'
        "- Language: Hinglish — casual, first-person, direct-to-camera, no-filter\n"
        "- Core lane: Discovery/explainer — underrated games, industry stories, hidden "
        "details that big Hindi creators ignore\n\n"
        "## 10 PROVEN FORMATS (pick the BEST fit — actual top-performing videos)\n\n"
        "1. **KISI NE NOTICE KIYA?** — Developer-confirmed hidden detail / easter egg. (3,123 views)\n"
        '   Hook: "Bhai, [game] mein ek cheez hai jo sirf 1% log jaante hain..."\n\n'
        "2. **GAMER IS BEING REPLACED** — Game mirroring AI/automation replacing real jobs. (2,265 views)\n"
        '   Hook: "Ye game tumhe apni hi [job/skill] se replace karna sikhata hai."\n\n'
        "3. **PHIR BHI DOOB GAYA** — Massive hype game that still flopped / cancelled. (1,924 views)\n"
        '   Hook: "[X] million wishlists thi / [budget] tha, phir bhi doob gaya."\n\n'
        "4. **HIDDEN GEM INTRO** — Underrated game with zero Hinglish coverage. (1,788 views — Hacknet)\n"
        '   Hook: "Ye game exist karta hai aur koi baat nahi karta. Aaj hum batate hain."\n\n'
        "5. **POORA SAFAR** — Full franchise or genre history in one Short. (1,297 views — Forza 13 saal)\n"
        '   Hook: "[X] saal. [Y] games. [Franchise] ka poora safar."\n\n'
        "6. **99% LOGON NE MISS KIYA** — Movie/show easter egg in a game. (1,042 views — RE movie)\n"
        '   Hook: "[Movie] trailer mein [game] ka easter egg chhupa hai jo director ne confirm kiya."\n\n'
        "7. **KOI BAAT KYUN NAHI KAR RAHA?** — Overlooked upcoming/new game. (954 views — 007 First Light)\n"
        '   Hook: "[Game] aa raha hai [date] ko — aur koi baat kyun nahi kar raha?"\n\n'
        "8. **Rs X NE Rs Y KO HARAYA** — Budget indie beats expensive AAA, Indian price angle. (919 views)\n"
        '   Hook: "Rs[X] ke game ne Rs[Y] AAA ko kaise haraya?"\n\n'
        "9. **ASLI KAHANI** — Behind-the-scenes studio failure or industry drama. (738 views — Concord)\n"
        '   Hook: "[Budget] ka budget, [timeline] mein khatam. [Game] ki asli kahani."\n\n'
        "10. **GAME EXPLAINED** — Story/lore/mechanics of a franchise in 60 seconds. (1,201 views — Black Flag)\n"
        '    Hook: "[Game] explained — poori [story/journey/mechanic] 60 seconds mein."\n\n'
        "## STRICT RULES\n"
        "- Pick EXACTLY ONE format — include its name in the angle\n"
        "- NEVER suggest: let's plays, reaction videos, rank push, crate opening, 'covering the buzz'\n"
        "- Write hook in Hinglish — punchy, first-person, 40 words max\n"
        "- Give the actual opening sentence for the Short\n\n"
        "Output ONLY valid JSON:\n"
        "{{\n"
        '  "why": "why this is trending — 1 sentence, max 15 words",\n'
        '  "angle": "[FORMAT NAME]: specific Hinglish hook opening line — max 40 words"\n'
        "}}"
    )

    # Attempt 1: Gemini with Google Search grounding enabled
    try:
        logger.debug("Requesting AI curation for: '%s' with Google Search grounding...", title[:50])
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(
            tools=[grounding_tool],
            temperature=0.7,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config,
        )
        result = json.loads(response.text.strip())
        if isinstance(result, dict) and "angle" in result and "why" in result:
            return result

    except Exception as exc:
        logger.debug("Gemini with search grounding failed, retrying without tools: %s", exc)

        # Attempt 2: standard generation without search tools
        try:
            config_fallback = types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config_fallback,
            )
            result = json.loads(response.text.strip())
            if isinstance(result, dict) and "angle" in result and "why" in result:
                return result
        except Exception as exc_fallback:
            logger.error(
                "Gemini standard generation fallback failed for '%s': %s",
                title[:50],
                exc_fallback,
            )

    # Both Gemini attempts failed — use Neo's proven rule-based fallback
    return _smart_fallback_angle(item)


def curate_trends(items: list[dict], limit: int = 10) -> list[dict]:
    """Curate a list of items, annotating them in-place with 'ai_angle' and 'ai_why'.

    Top `limit` items are sent to Gemini; the rest use the rule-based fallback.

    Args:
        items: List of scored trend dictionaries.
        limit: Maximum number of items to send to Gemini.

    Returns:
        The annotated list of items.
    """
    if not client:
        logger.info("Skipping Gemini (no API key) — using rule-based Neo formats for all items.")
        for item in items:
            fallback = _smart_fallback_angle(item)
            item["ai_angle"] = fallback["angle"]
            item["ai_why"] = fallback["why"]
        return items

    logger.info("AI Curation Layer starting (curating top %d items).", limit)

    curated_count = 0
    for idx, item in enumerate(items):
        if idx >= limit:
            # Items beyond Gemini limit also get the rule-based fallback
            fallback = _smart_fallback_angle(item)
            item["ai_angle"] = fallback["angle"]
            item["ai_why"] = fallback["why"]
            continue

        start_time = time.monotonic()
        curation = curate_item(item)

        item["ai_angle"] = curation.get("angle", "")
        item["ai_why"] = curation.get("why", "") or item.get("why", "")

        curated_count += 1
        elapsed = time.monotonic() - start_time
        logger.info(
            "AI Curation [%d/%d] done: '%s' (%.1fs)",
            curated_count, limit, item.get("title", "")[:40], elapsed,
        )

        # Gemini free tier: 5 RPM — sleep 12.5s between requests
        if idx < limit - 1:
            time.sleep(12.5)

    logger.info("AI Curation complete: %d items curated.", curated_count)
    return items
