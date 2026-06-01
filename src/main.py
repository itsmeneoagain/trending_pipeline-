"""Main orchestrator for the trending content pipeline.

Sequence:
    1. Fetch YouTube trending + creator videos
    2. Fetch Reddit hot posts from gaming subreddits
    3. Score, categorise, and deduplicate all items
    4. Push new items to Notion (skipping duplicates)
    5. Log summary and exit
"""

import logging
import sys
import time

# ── Logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trending")


def run() -> int:
    """Execute the full pipeline and return an exit code (0 = success)."""
    start = time.monotonic()
    logger.info("━━━ Trending Pipeline — starting ━━━")

    all_items: list[dict] = []

    # ── 0. Sync creators from Notion ─────────────────────────────────
    try:
        from src.notion_push import sync_creators_from_notion_to_local
        sync_creators_from_notion_to_local()
    except Exception as exc:
        logger.debug("Creator Notion sync skipped: %s", exc)

    # ── 1. YouTube creator videos ────────────────────────────────────
    try:
        from src.fetch_youtube import fetch_all_youtube

        yt_items = fetch_all_youtube()
        all_items.extend(yt_items)
        logger.info("YouTube: %d items fetched", len(yt_items))
    except Exception as exc:
        logger.error("YouTube fetch failed — continuing: %s", exc)

    # ── 2. Reddit ────────────────────────────────────────────────────
    try:
        from src.fetch_reddit import fetch_reddit_posts

        reddit_items = fetch_reddit_posts()
        all_items.extend(reddit_items)
        logger.info("Reddit: %d items fetched", len(reddit_items))
    except Exception as exc:
        logger.error("Reddit fetch failed — continuing: %s", exc)

    if not all_items:
        logger.warning("No items fetched from any source — exiting")
        return 0

    # ── 3. Scoring & categorisation ──────────────────────────────────
    try:
        from src.scorer import score_and_categorise

        scored = score_and_categorise(all_items)
        logger.info("Scored: %d items above threshold", len(scored))
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        return 1

    if not scored:
        logger.info("No items passed the score threshold — nothing to push")
        return 0

    # ── 3.5. AI Curation ─────────────────────────────────────────────
    curated = scored
    try:
        from src.curator import curate_trends

        curated = curate_trends(scored, limit=10)
    except Exception as exc:
        logger.error("AI Curation failed — continuing with raw trends: %s", exc)

    # ── 4. Push to Notion ────────────────────────────────────────────
    pushed, skipped = 0, 0
    try:
        from src.notion_push import push_trending_items

        pushed, skipped = push_trending_items(curated)
    except Exception as exc:
        logger.error("Notion push failed: %s", exc)


    # ── 5. Summary ───────────────────────────────────────────────────
    elapsed = time.monotonic() - start

    hot_count = sum(1 for i in scored if i.get("heat_level") == "hot")
    warm_count = sum(1 for i in scored if i.get("heat_level") == "warm")
    t1_count = sum(1 for i in scored if i.get("tier") == "T1")

    logger.info("━━━ Pipeline Summary ━━━")
    logger.info("  Total fetched : %d", len(all_items))
    logger.info("  Scored (pass) : %d", len(scored))
    logger.info("  🔥 Hot        : %d", hot_count)
    logger.info("  🌤  Warm       : %d", warm_count)
    logger.info("  💎 T1 Gems    : %d", t1_count)
    logger.info("  ✅ Pushed     : %d", pushed)
    logger.info("  ⏭  Skipped    : %d", skipped)
    logger.info("  ⏱  Duration   : %.1fs", elapsed)
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━")

    return 0


if __name__ == "__main__":
    sys.exit(run())
