"""Lightweight full-stack Flask server hosting the dynamic discovery dashboard and managing Notion synchronisation."""

import logging
import os
import socket
import threading
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from src.notion_push import (
    create_pipeline_item,
    archive_trend_by_title,
    update_pipeline_item_status,
    delete_pipeline_item,
    sync_notion_to_local_files,
    update_pipeline_item_script
)

# ── Logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("server")

app = Flask(__name__)
CORS(app)  # Enable Cross-Origin Resource Sharing for easy local development

# Lock to prevent parallel cron/manual scrapes
_scrape_lock = threading.Lock()
_is_scraping = False


def get_local_ip() -> str:
    """Fetch the computer's local Wi-Fi/Ethernet IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── Dashboard & Static File Assets ───────────────────────────────────

@app.route("/")
@app.route("/game-content-radar.html")
def index():
    """Serve the game-content-radar.html dashboard."""
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    html_path = os.path.join(root_dir, 'game-content-radar.html')
    if not os.path.exists(html_path):
        return "Error: game-content-radar.html not found in repository root.", 404
        
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return content


@app.route("/pipeline")
@app.route("/content-pipeline")
@app.route("/content-pipeline.html")
def pipeline_dashboard():
    """Serve the content-pipeline.html dedicated board."""
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    html_path = os.path.join(root_dir, 'content-pipeline.html')
    if not os.path.exists(html_path):
        return "Error: content-pipeline.html not found in repository root.", 404
        
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return content



@app.route("/trends.json")
def get_trends_json():
    """Serve the local synced trends cache directly to the dashboard."""
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    return send_from_directory(root_dir, 'trends.json')


@app.route("/pipeline.json")
def get_pipeline_json():
    """Serve the local synced pipeline cache directly to the dashboard."""
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    return send_from_directory(root_dir, 'pipeline.json')


# ── Notion Database Actions API ──────────────────────────────────────

@app.route("/api/pipeline/add", methods=["POST"])
def api_add_to_pipeline():
    """Move a trend card into the Content Pipeline, or add a custom idea."""
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    tier_num = data.get("tier", 3)
    notes = data.get("note", "").strip()
    script = data.get("script", "").strip()
    trend_title = data.get("trend_title", "").strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    tier_str = f"T{tier_num}"

    logger.info("API: Adding pipeline card: '%s' (%s)", title[:60], tier_str)
    
    # 1. Create page inside your Notion Content Pipeline database
    success = create_pipeline_item(title=title, tier=tier_str, notes=notes, script=script)
    if not success:
        return jsonify({"error": "Failed to create Notion pipeline card"}), 500

    # 2. If added from the Trending list, archive the trend card from the Notion Trending database
    if trend_title:
        logger.info("API: Archiving moved trend card: '%s'...", trend_title[:60])
        archive_trend_by_title(trend_title)

    # 3. Synchronise the updated Notion databases down to local JSON files
    sync_notion_to_local_files()

    return jsonify({"status": "success", "message": "Card successfully moved to pipeline!"})


@app.route("/api/pipeline/update-status", methods=["POST"])
def api_update_status():
    """Update status of a pipeline item in Notion."""
    data = request.get_json() or {}
    page_id = data.get("page_id", "").strip()
    status = data.get("status", "").strip()

    if not page_id or not status:
        return jsonify({"error": "page_id and status are required"}), 400

    logger.info("API: Updating status of pipeline card %s to '%s'...", page_id, status)
    success = update_pipeline_item_status(page_id=page_id, status=status)
    if not success:
        return jsonify({"error": "Failed to update status in Notion"}), 500

    return jsonify({"status": "success", "message": "Status updated successfully!"})


@app.route("/api/pipeline/delete", methods=["POST"])
def api_delete_pipeline_item():
    """Archive a pipeline card in Notion."""
    data = request.get_json() or {}
    page_id = data.get("page_id", "").strip()

    if not page_id:
        return jsonify({"error": "page_id is required"}), 400

    logger.info("API: Archiving pipeline card %s...", page_id)
    success = delete_pipeline_item(page_id=page_id)
    if not success:
        return jsonify({"error": "Failed to delete item from Notion"}), 500

    return jsonify({"status": "success", "message": "Card deleted successfully!"})


@app.route("/api/pipeline/update-script", methods=["POST"])
def api_update_script():
    """Update note and script of a pipeline item in Notion by combining them."""
    data = request.get_json() or {}
    page_id = data.get("page_id", "").strip()
    note = data.get("note", "").strip()
    script = data.get("script", "").strip()

    if not page_id:
        return jsonify({"error": "page_id is required"}), 400

    logger.info("API: Updating script of pipeline card %s...", page_id)
    success = update_pipeline_item_script(page_id=page_id, note=note, script=script)
    if not success:
        return jsonify({"error": "Failed to update notes/script in Notion"}), 500

    return jsonify({"status": "success", "message": "Script updated successfully!"})


# ── Live Gemini Curation & Scrape Refresh ────────────────────────────

def _background_scrape():
    """Run the pipeline orchestrator in a background thread."""
    global _is_scraping
    with _scrape_lock:
        _is_scraping = True
        try:
            logger.info("Background thread: starting live YouTube/Reddit/Gemini scrape...")
            from src.main import run as run_pipeline
            run_pipeline()
            logger.info("Background thread: scrape completed successfully!")
        except Exception as e:
            logger.error("Background thread: scrape failed: %s", e)
        finally:
            _is_scraping = False


@app.route("/api/refresh", methods=["POST"])
def api_refresh_scrapes():
    """Trigger a background thread to scrape the web and refresh databases."""
    global _is_scraping
    if _is_scraping:
        return jsonify({"status": "running", "message": "Scrape is already running in background!"})

    threading.Thread(target=_background_scrape).start()
    return jsonify({"status": "started", "message": "Scrape started successfully in background!"})


@app.route("/api/status", methods=["GET"])
def api_get_status():
    """Retrieve status of background scraping thread."""
    return jsonify({"is_scraping": _is_scraping})


@app.route("/api/sync-notion", methods=["GET", "POST"])
def api_sync_notion():
    """Pull latest data from Notion and update local JSON caches.

    Faster than /api/refresh — skips the full scraping pipeline and just
    re-syncs whatever is already in Notion down to trends.json / pipeline.json.
    Returns the fresh data so the dashboard can update without a second fetch.
    """
    try:
        from src.notion_push import sync_notion_to_local_files
        sync_notion_to_local_files()

        import json as _json
        root_dir = os.path.join(os.path.dirname(__file__), '..')
        with open(os.path.join(root_dir, 'trends.json'), 'r', encoding='utf-8') as f:
            trends_data = _json.load(f)
        with open(os.path.join(root_dir, 'pipeline.json'), 'r', encoding='utf-8') as f:
            pipeline_data = _json.load(f)

        return jsonify({"status": "synced", "trends": trends_data, "pipeline": pipeline_data})
    except Exception as exc:
        logger.error("Notion sync failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Dynamic Creator Config API ───────────────────────────────────────

def _creators_local_fallback() -> dict:
    """Read creators.json as flat {youtube: [...], instagram: [...]} for fallback."""
    import json as _json
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root_dir, 'creators.json')
    if not os.path.exists(path):
        empty = {"youtube": [], "instagram": []}
        with open(path, 'w', encoding='utf-8') as f:
            _json.dump(empty, f)
        return empty
    with open(path, 'r', encoding='utf-8') as f:
        return _json.load(f)


def _write_creators_local(grouped: dict) -> None:
    """Write {youtube: [...], instagram: [...]} to creators.json."""
    import json as _json
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root_dir, 'creators.json')
    with open(path, 'w', encoding='utf-8') as f:
        _json.dump(grouped, f, ensure_ascii=False, indent=2)


@app.route("/api/creators", methods=["GET"])
def api_get_creators():
    """Retrieve tracked creators from Notion (falls back to creators.json).

    Response format:
      {"youtube": [{"handle": "UC...", "page_id": "..."}, ...], "instagram": [...]}
    """
    try:
        from src.notion_push import fetch_creators_from_notion
        from src.config import NOTION_CREATORS_DB_ID

        if NOTION_CREATORS_DB_ID:
            notion_list = fetch_creators_from_notion()
            grouped: dict = {"youtube": [], "instagram": []}
            for c in notion_list:
                grouped.setdefault(c["platform"], []).append(
                    {"handle": c["handle"], "page_id": c["page_id"]}
                )
            return jsonify(grouped)
    except Exception as exc:
        logger.warning("Notion creator fetch failed, falling back to local: %s", exc)

    # Fallback: wrap local handles as objects without page_id
    local = _creators_local_fallback()
    return jsonify({
        "youtube": [{"handle": h, "page_id": None} for h in local.get("youtube", [])],
        "instagram": [{"handle": h, "page_id": None} for h in local.get("instagram", [])],
    })


@app.route("/api/creators/add", methods=["POST"])
def api_add_creator():
    """Add a tracked creator to Notion and creators.json."""
    data = request.get_json() or {}
    platform = data.get("platform", "").strip().lower()
    handle = data.get("handle", "").strip()

    if platform not in ["youtube", "instagram"] or not handle:
        return jsonify({"error": "platform (youtube/instagram) and handle are required"}), 400

    page_id = None

    # Push to Notion if configured
    try:
        from src.notion_push import push_creator_to_notion, fetch_creators_from_notion
        from src.config import NOTION_CREATORS_DB_ID
        if NOTION_CREATORS_DB_ID:
            page_id = push_creator_to_notion(handle, platform)
    except Exception as exc:
        logger.warning("Notion creator push failed: %s", exc)

    # Always update creators.json
    try:
        local = _creators_local_fallback()
        if platform not in local:
            local[platform] = []
        if handle not in local[platform]:
            local[platform].append(handle)
        _write_creators_local(local)
    except Exception as exc:
        logger.error("Failed to update creators.json: %s", exc)

    logger.info("API: Added %s creator: '%s' (page_id=%s)", platform, handle, page_id)

    # Return fresh Notion list (or local fallback)
    return api_get_creators()


@app.route("/api/creators/delete", methods=["POST"])
def api_delete_creator():
    """Delete a tracked creator from Notion and creators.json.

    Body: {"page_id": "..."} — preferred (Notion deletion)
          OR {"platform": "youtube", "handle": "UC..."} — local-only fallback
    """
    data = request.get_json() or {}
    page_id = data.get("page_id", "").strip()
    platform = data.get("platform", "").strip().lower()
    handle = data.get("handle", "").strip()

    if not page_id and not (platform and handle):
        return jsonify({"error": "page_id or (platform + handle) required"}), 400

    # Delete from Notion if we have a page_id
    if page_id:
        try:
            from src.notion_push import delete_creator_from_notion
            delete_creator_from_notion(page_id)
        except Exception as exc:
            logger.warning("Notion creator delete failed: %s", exc)

    # Remove from creators.json — find handle by page_id if not provided
    if not handle and page_id:
        try:
            from src.notion_push import fetch_creators_from_notion
            for c in fetch_creators_from_notion():
                if c["page_id"] == page_id:
                    handle = c["handle"]
                    platform = c["platform"]
                    break
        except Exception:
            pass

    if handle and platform:
        try:
            local = _creators_local_fallback()
            if platform in local and handle in local[platform]:
                local[platform].remove(handle)
                _write_creators_local(local)
        except Exception as exc:
            logger.error("Failed to update creators.json: %s", exc)

    logger.info("API: Deleted creator page_id=%s handle='%s'", page_id, handle)
    return api_get_creators()


# ── Server Boot ──────────────────────────────────────────────────────


def run_server():
    """Start the server and print easy-to-use access guides."""
    local_ip = get_local_ip()
    port = int(os.getenv("PORT", "8080"))

    logger.info("=========================================================")
    logger.info("🎮 CUSTOM DASHBOARD SERVER RUNNING 🎮")
    logger.info("=========================================================")
    logger.info("  👉 Desktop Access: http://localhost:%d", port)
    logger.info("  👉 Mobile Phone Access (Same Wi-Fi): http://%s:%d", local_ip, port)
    logger.info("=========================================================")

    # Start Flask server (binds on 0.0.0.0 so phone can connect!)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    run_server()
