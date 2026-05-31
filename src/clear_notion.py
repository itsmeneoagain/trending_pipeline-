"""Purge/archive all existing items in the Notion Trending Topics database."""

import logging
import time
import requests

from src.config import NOTION_API_KEY, NOTION_TRENDING_DB_ID

# ── Logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("clear_notion")

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def fetch_all_page_ids() -> list[str]:
    """Query the entire database and fetch all page IDs (paginated)."""
    page_ids = []
    has_more = True
    start_cursor = None
    url = f"{NOTION_BASE_URL}/databases/{NOTION_TRENDING_DB_ID}/query"

    logger.info("Querying database to retrieve all existing trends...")

    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
            if resp.status_code != 200:
                logger.error("Failed to query Notion database: %d - %s", resp.status_code, resp.text)
                break

            data = resp.json()
            results = data.get("results", [])
            for page in results:
                page_ids.append(page.get("id"))

            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
            
            # Rate limit throttle
            time.sleep(0.35)

        except requests.RequestException as e:
            logger.error("HTTP request error during query: %s", e)
            break

    logger.info("Found %d active pages in the Trending Topics database.", len(page_ids))
    return page_ids


def archive_page(page_id: str) -> bool:
    """Set the archived state of a single Notion page to True."""
    url = f"{NOTION_BASE_URL}/pages/{page_id}"
    payload = {"archived": True}

    try:
        resp = requests.patch(url, headers=_headers(), json=payload, timeout=15)
        if resp.status_code == 200:
            return True
        elif resp.status_code == 429:
            # Handle rate limiting dynamically
            retry_after = float(resp.headers.get("Retry-After", 1))
            logger.warning("Rate limited, retrying page %s in %.1fs", page_id, retry_after)
            time.sleep(retry_after)
            return archive_page(page_id)
        else:
            logger.error("Failed to archive page %s: %d - %s", page_id, resp.status_code, resp.text[:200])
            return False
    except requests.RequestException as e:
        logger.error("Request error while archiving page %s: %s", page_id, e)
        return False


def clear_trending_db():
    """Archive all pages in the configured Notion Trending Topics database."""
    if not NOTION_API_KEY or not NOTION_TRENDING_DB_ID:
        logger.error("Notion credentials or Database ID missing from environment. Aborting.")
        return

    logger.info("━━━ Starting Purge of Notion database ━━━")
    
    page_ids = fetch_all_page_ids()
    if not page_ids:
        logger.info("No pages found. The database is already empty!")
        return

    logger.info("Purging/Archiving %d pages (this may take a minute to stay rate-limit safe)...", len(page_ids))
    
    archived_count = 0
    for idx, pid in enumerate(page_ids):
        success = archive_page(pid)
        if success:
            archived_count += 1
            if archived_count % 10 == 0 or archived_count == len(page_ids):
                logger.info("Progress: Archived %d/%d pages...", archived_count, len(page_ids))
        
        # Enforce rate limiting (~3 requests/second)
        time.sleep(0.35)

    logger.info("━━━ Purge complete! Successfully archived %d/%d pages. ━━━", archived_count, len(page_ids))


if __name__ == "__main__":
    clear_trending_db()
