"""Push scored trending items to Notion databases via the Notion API."""

import logging
import time
from datetime import datetime, timezone

import requests

from src.config import NOTION_API_KEY, NOTION_TRENDING_DB_ID, NOTION_PIPELINE_DB_ID

logger = logging.getLogger(__name__)

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Notion rate limit: 3 requests / second — we throttle to stay safe
_MIN_INTERVAL = 0.35  # seconds between requests
_last_request_time: float = 0.0


def _headers() -> dict:
    """Return Notion API request headers."""
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _throttle():
    """Enforce minimum interval between Notion API requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


def _notion_request(method: str, path: str, **kwargs) -> requests.Response | None:
    """Make a rate-limited request to the Notion API with retry on 429.

    Args:
        method: HTTP method ('get', 'post', 'patch').
        path: API path appended to NOTION_BASE_URL.
        **kwargs: Passed to requests.request.

    Returns:
        Response object, or None if all retries failed.
    """
    url = f"{NOTION_BASE_URL}{path}"
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        _throttle()
        try:
            resp = requests.request(method, url, headers=_headers(), timeout=15, **kwargs)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 1))
                logger.warning("Notion rate-limited, retrying in %.1fs", retry_after)
                time.sleep(retry_after)
                continue

            if resp.status_code >= 400:
                logger.error(
                    "Notion API %s %s → %d: %s",
                    method.upper(),
                    path,
                    resp.status_code,
                    resp.text[:300],
                )
                return None

            return resp

        except requests.RequestException as exc:
            logger.error("Notion request failed (attempt %d): %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(2 ** attempt)

    return None


# ── Check for duplicates ─────────────────────────────────────────────

def check_existing(title: str) -> bool:
    """Check if a trend with this title already exists in the Trending DB.

    Args:
        title: The title to search for.

    Returns:
        True if a page with an exact title match exists.
    """
    if not NOTION_API_KEY or not NOTION_TRENDING_DB_ID:
        logger.debug("Notion not configured — skipping duplicate check")
        return False

    payload = {
        "filter": {
            "property": "Title",
            "title": {"equals": title},
        },
        "page_size": 1,
    }

    resp = _notion_request(
        "post",
        f"/databases/{NOTION_TRENDING_DB_ID}/query",
        json=payload,
    )

    if resp is None:
        return False  # fail-open: allow push if we can't verify

    data = resp.json()
    return len(data.get("results", [])) > 0


# ── Push to Trending Topics DB ──────────────────────────────────────

def _format_source_stats(item: dict) -> str:
    """Build a human-readable stats string for the Source Stats field."""
    source = item.get("source", "")
    parts: list[str] = []

    if source.startswith("youtube"):
        parts.append(f"Views: {item.get('views', 0):,}")
        parts.append(f"Likes: {item.get('likes', 0):,}")
        parts.append(f"Comments: {item.get('comments', 0):,}")
    elif source == "reddit":
        parts.append(f"Score: {item.get('score', 0):,}")
        parts.append(f"Comments: {item.get('num_comments', 0):,}")
        parts.append(f"r/{item.get('subreddit', '?')}")

    return " | ".join(parts)


def _source_url(item: dict) -> str:
    """Extract the best URL for the item."""
    return item.get("video_url") or item.get("permalink") or item.get("url", "")


def _format_content_angle(item: dict) -> str:
    """Build a comprehensive AI curation string with research explanation and hook."""
    why = item.get("ai_why", "").strip()
    angle = item.get("ai_angle", "").strip()

    if not why and not angle:
        return ""

    parts = []
    if why:
        parts.append(f"🧠 Why it's trending:\n{why}")
    if angle:
        parts.append(f"🎬 Hook & Content Angle:\n{angle}")

    return "\n\n".join(parts)


def _build_page_properties(item: dict) -> dict:
    """Build Notion page properties from a scored item."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # Since 'Content Angle' property may not exist yet in the user's Notion database,
    # we combine stats and curated content angles into 'Source Stats' to preserve the UI
    # without causing API validation errors!
    stats_content = _format_source_stats(item)
    ai_angle = _format_content_angle(item)
    if ai_angle:
        combined_stats = f"{stats_content}\n\n{ai_angle}"
    else:
        combined_stats = stats_content

    return {
        "Title": {
            "title": [{"text": {"content": item.get("title", "")[:2000]}}]
        },
        "Source": {
            "select": {"name": item.get("source", "unknown")}
        },
        "Heat Score": {
            "number": item.get("heat_score", 0)
        },
        "Heat Level": {
            "select": {"name": item.get("heat_level", "cool")}
        },
        "Tier": {
            "select": {"name": item.get("tier", "T3")}
        },
        "Time Sensitive": {
            "checkbox": item.get("time_sensitive", False)
        },
        "Source URL": {
            "url": _source_url(item) or None
        },
        "Source Stats": {
            "rich_text": [
                {"text": {"content": combined_stats[:2000]}}
            ]
        },
        "Discovered At": {
            "date": {"start": now_iso}
        },
    }




def push_trending_items(items: list[dict]) -> tuple[int, int]:
    """Push a list of scored items to the Notion Trending Topics database.

    Skips items that already exist (by title).

    Args:
        items: Scored and categorised trend dicts.

    Returns:
        Tuple of (pushed_count, skipped_duplicates).
    """
    if not NOTION_API_KEY or not NOTION_TRENDING_DB_ID:
        logger.warning("Notion credentials not configured — skipping push")
        return 0, 0

    pushed = 0
    skipped = 0

    for item in items:
        title = item.get("title", "")

        if check_existing(title):
            logger.debug("Duplicate, skipping: '%s'", title[:60])
            skipped += 1
            continue

        payload = {
            "parent": {"database_id": NOTION_TRENDING_DB_ID},
            "properties": _build_page_properties(item),
        }

        resp = _notion_request("post", "/pages", json=payload)
        if resp is not None:
            pushed += 1
            logger.debug("Pushed: '%s' (score=%.1f)",
                         title[:60], item.get("heat_score", 0))
        else:
            logger.warning("Failed to push: '%s'", title[:60])

    logger.info("Notion push complete: %d pushed, %d duplicates skipped",
                pushed, skipped)
    
    # Automatically sync state down to local JSON files for the dashboard
    sync_notion_to_local_files()
    
    return pushed, skipped


# ── Create Pipeline Item ─────────────────────────────────────────────

def create_pipeline_item(title: str, tier: str, notes: str = "", script: str = "") -> bool:
    """Create a new item in the Pipeline database for content production.

    Args:
        title: Video title / topic.
        tier: Content tier (T1 / T2 / T3).
        notes: Optional production notes.
        script: Optional video script text.

    Returns:
        True if created successfully.
    """
    if not NOTION_API_KEY or not NOTION_PIPELINE_DB_ID:
        logger.warning("Pipeline DB not configured — skipping")
        return False

    notes_clean = notes.strip()
    script_clean = script.strip()
    if script_clean:
        combined = f"{notes_clean}\n\n=== SCRIPT ===\n{script_clean}"
    else:
        combined = notes_clean

    payload = {
        "parent": {"database_id": NOTION_PIPELINE_DB_ID},
        "properties": {
            "Title": {
                "title": [{"text": {"content": title[:2000]}}]
            },
            "Tier": {
                "select": {"name": tier}
            },
            "Notes": {
                "rich_text": [{"text": {"content": combined[:2000]}}]
            },
        },
    }

    resp = _notion_request("post", "/pages", json=payload)
    if resp is not None:
        logger.info("Pipeline item created: '%s' (%s)", title[:60], tier)
        
        # Sync state down to local files
        sync_notion_to_local_files()
        
        return True

    logger.warning("Failed to create pipeline item: '%s'", title[:60])
    return False


# ── Sync Notion to Local JSON ────────────────────────────────────────

def sync_notion_to_local_files():
    """Fetch the latest active entries from Notion and write them to trends.json and pipeline.json."""
    if not NOTION_API_KEY:
        logger.warning("Notion API key not set — skipping local JSON sync")
        return

    # 1. Fetch Trending Topics
    trends_list = []
    if NOTION_TRENDING_DB_ID:
        logger.info("Syncing Trending Topics from Notion to trends.json...")
        url = f"{NOTION_BASE_URL}/databases/{NOTION_TRENDING_DB_ID}/query"
        payload = {
            "sorts": [
                {"property": "Heat Score", "direction": "descending"}
            ],
            "page_size": 100
        }
        resp = _notion_request("post", f"/databases/{NOTION_TRENDING_DB_ID}/query", json=payload)
        if resp is not None:
            results = resp.json().get("results", [])
            for page in results:
                props = page.get("properties", {})
                
                # Extract Title
                title = ""
                title_list = props.get("Title", {}).get("title", [])
                if title_list:
                    title = title_list[0].get("text", {}).get("content", "")
                
                # Extract Source
                source = props.get("Source", {}).get("select", {})
                source_name = source.get("name", "unknown") if source else "unknown"
                
                # Extract Heat Score
                heat_score = props.get("Heat Score", {}).get("number", 0)
                
                # Extract Heat Level
                heat_lvl = props.get("Heat Level", {}).get("select", {})
                heat_level = heat_lvl.get("name", "cool") if heat_lvl else "cool"
                
                # Extract Tier
                tier_data = props.get("Tier", {}).get("select", {})
                tier = tier_data.get("name", "T3") if tier_data else "T3"
                try:
                    tier_val = int(tier.replace("T", ""))
                except Exception:
                    tier_val = 3
                
                # Extract Time Sensitive
                time_sensitive = props.get("Time Sensitive", {}).get("checkbox", False)
                
                # Extract Source URL
                source_url = props.get("Source URL", {}).get("url", "")
                
                # Extract Source Stats
                stats = ""
                stats_list = props.get("Source Stats", {}).get("rich_text", [])
                if stats_list:
                    stats = stats_list[0].get("text", {}).get("content", "")
                
                # Extract Discovered At
                discovered_at = props.get("Discovered At", {}).get("date", {})
                discovered_str = discovered_at.get("start", "") if discovered_at else ""

                # Format trends item to match dashboard schema
                # We separate out the AI Angle if it is appended in combined stats
                angle = ""
                why = ""
                if "🎬 Hook & Content Angle:" in stats:
                    parts = stats.split("🎬 Hook & Content Angle:\n")
                    if len(parts) > 1:
                        angle = parts[1].strip()
                    main_part = parts[0]
                    if "🧠 Why it's trending:\n" in main_part:
                        why_parts = main_part.split("🧠 Why it's trending:\n")
                        if len(why_parts) > 1:
                            why = why_parts[1].strip()
                        stats_clean = why_parts[0].strip()
                    else:
                        stats_clean = main_part.strip()
                elif "🧠 Why it's trending:\n" in stats:
                    why_parts = stats.split("🧠 Why it's trending:\n")
                    if len(why_parts) > 1:
                        why = why_parts[1].strip()
                    stats_clean = why_parts[0].strip()
                else:
                    stats_clean = stats.strip()

                trends_list.append({
                    "title": title,
                    "source": source_name,
                    "heat_score": heat_score,
                    "heat": heat_level,
                    "tier": tier_val,
                    "urgent": time_sensitive,
                    "url": source_url,
                    "stats": stats_clean,
                    "angle": angle or f"Short/Reel covering {title}",
                    "why": why or f"Trending gaming news",
                    "discovered_at": discovered_str
                })

    # 2. Fetch Content Pipeline
    pipeline_list = []
    if NOTION_PIPELINE_DB_ID:
        logger.info("Syncing Content Pipeline from Notion to pipeline.json...")
        url = f"{NOTION_BASE_URL}/databases/{NOTION_PIPELINE_DB_ID}/query"
        payload = {"page_size": 100}
        resp = _notion_request("post", f"/databases/{NOTION_PIPELINE_DB_ID}/query", json=payload)
        if resp is not None:
            results = resp.json().get("results", [])
            for page in results:
                props = page.get("properties", {})
                
                # Extract Title
                title = ""
                title_list = props.get("Title", {}).get("title", [])
                if title_list:
                    title = title_list[0].get("text", {}).get("content", "")
                
                # Extract Status
                status_data = props.get("Status", {}).get("status") or props.get("Status", {}).get("select") or {}
                status = status_data.get("name", "Idea") if status_data else "Idea"
                
                # Extract Tier
                tier_data = props.get("Tier", {}).get("select", {})
                tier = tier_data.get("name", "T3") if tier_data else "T3"
                try:
                    tier_val = int(tier.replace("T", ""))
                except Exception:
                    tier_val = 3
                
                # Extract Platform
                platform_list = props.get("Platform", {}).get("multi_select", [])
                if not platform_list:
                    # Try single select select
                    platform_list = [props.get("Platform", {}).get("select", {})]
                platform_names = [p.get("name") for p in platform_list if p and p.get("name")] if platform_list else ["Both"]
                platform_str = ", ".join(platform_names) if platform_names else "Both"
                
                # Extract Notes & Script
                notes = ""
                notes_list = props.get("Notes", {}).get("rich_text", [])
                if notes_list:
                    notes = notes_list[0].get("text", {}).get("content", "")

                note_text = notes
                script_text = ""
                if "=== SCRIPT ===" in notes:
                    parts = notes.split("=== SCRIPT ===")
                    note_text = parts[0].strip()
                    script_text = parts[1].strip()

                pipeline_list.append({
                    "id": page.get("id"),
                    "title": title,
                    "tier": tier_val,
                    "platform": platform_str,
                    "status": status,
                    "note": note_text,
                    "script": script_text
                })

    # Write files to root folder
    import json
    import os
    
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    
    trends_path = os.path.join(root_dir, 'trends.json')
    pipeline_path = os.path.join(root_dir, 'pipeline.json')
    
    with open(trends_path, 'w', encoding='utf-8') as f:
        json.dump(trends_list, f, ensure_ascii=False, indent=2)
    logger.info("Successfully updated %s with %d trends.", trends_path, len(trends_list))

    with open(pipeline_path, 'w', encoding='utf-8') as f:
        json.dump(pipeline_list, f, ensure_ascii=False, indent=2)
    logger.info("Successfully updated %s with %d pipeline items.", pipeline_path, len(pipeline_list))


# ── Full-Stack Notion Helper Actions ──────────────────────────────────

def archive_trend_by_title(title: str) -> bool:
    """Find a page in the Trending database by title and archive (delete) it."""
    if not NOTION_API_KEY or not NOTION_TRENDING_DB_ID:
        return False

    payload = {
        "filter": {
            "property": "Title",
            "title": {"equals": title},
        },
        "page_size": 1,
    }

    # Query the page ID
    resp = _notion_request("post", f"/databases/{NOTION_TRENDING_DB_ID}/query", json=payload)
    if resp is None:
        return False

    results = resp.json().get("results", [])
    if not results:
        logger.warning("No trend page found with title: '%s' to archive", title[:60])
        return False

    page_id = results[0].get("id")
    
    # Archive the page
    archive_payload = {"archived": True}
    archive_resp = _notion_request("patch", f"/pages/{page_id}", json=archive_payload)
    if archive_resp is not None:
        logger.info("Successfully archived trend card: '%s'", title[:60])
        return True

    return False


def update_pipeline_item_status(page_id: str, status: str) -> bool:
    """Update the Status property of a pipeline item in Notion."""
    if not NOTION_API_KEY:
        return False

    payload = {
        "properties": {
            "Status": {
                "status": {"name": status}
            }
        }
    }

    resp = _notion_request("patch", f"/pages/{page_id}", json=payload)
    if resp is not None:
        logger.info("Successfully updated Notion card %s status to '%s'", page_id, status)
        
        # Sync state down to local files
        sync_notion_to_local_files()
        
        return True

    return False


def delete_pipeline_item(page_id: str) -> bool:
    """Archive (delete) a pipeline item from Notion."""
    if not NOTION_API_KEY:
        return False

    payload = {"archived": True}
    resp = _notion_request("patch", f"/pages/{page_id}", json=payload)
    if resp is not None:
        logger.info("Successfully archived Notion pipeline card: %s", page_id)
        
        # Sync state down to local files
        sync_notion_to_local_files()
        
        return True

    return False


def update_pipeline_item_script(page_id: str, note: str, script: str) -> bool:
    """Update the Notes and Script property in Notion by combining them."""
    if not NOTION_API_KEY:
        return False

    note_clean = note.strip()
    script_clean = script.strip()
    if script_clean:
        combined = f"{note_clean}\n\n=== SCRIPT ===\n{script_clean}"
    else:
        combined = note_clean

    payload = {
        "properties": {
            "Notes": {
                "rich_text": [{"text": {"content": combined[:2000]}}]
            }
        }
    }

    resp = _notion_request("patch", f"/pages/{page_id}", json=payload)
    if resp is not None:
        logger.info("Successfully updated Notion card %s notes and script", page_id)
        
        # Sync state down to local files
        sync_notion_to_local_files()
        
        return True

    return False

