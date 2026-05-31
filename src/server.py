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
    sync_notion_to_local_files
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
    trend_title = data.get("trend_title", "").strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    tier_str = f"T{tier_num}"

    logger.info("API: Adding pipeline card: '%s' (%s)", title[:60], tier_str)
    
    # 1. Create page inside your Notion Content Pipeline database
    success = create_pipeline_item(title=title, tier=tier_str, notes=notes)
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


# ── Dynamic Creator Config API ───────────────────────────────────────

@app.route("/api/creators", methods=["GET"])
def api_get_creators():
    """Retrieve the list of dynamic YouTube and Instagram competitor handles."""
    import json
    root_dir = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root_dir, 'creators.json')
    if not os.path.exists(path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"youtube": [], "instagram": []}, f)
            
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/creators/add", methods=["POST"])
def api_add_creator():
    """Add a tracked competitor creator (youtube or instagram) handle/ID."""
    import json
    data = request.get_json() or {}
    platform = data.get("platform", "").strip().lower()  # 'youtube' or 'instagram'
    handle = data.get("handle", "").strip()

    if platform not in ["youtube", "instagram"] or not handle:
        return jsonify({"error": "platform (youtube/instagram) and handle are required"}), 400

    root_dir = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root_dir, 'creators.json')
    
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                creators = json.load(f)
        else:
            creators = {"youtube": [], "instagram": []}

        if platform not in creators:
            creators[platform] = []

        if handle not in creators[platform]:
            creators[platform].append(handle)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(creators, f, ensure_ascii=False, indent=2)

        logger.info("API: Added %s creator: '%s'", platform, handle)
        return jsonify({"status": "success", "creators": creators})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/creators/delete", methods=["POST"])
def api_delete_creator():
    """Delete a tracked competitor creator handle/ID."""
    import json
    data = request.get_json() or {}
    platform = data.get("platform", "").strip().lower()
    handle = data.get("handle", "").strip()

    if platform not in ["youtube", "instagram"] or not handle:
        return jsonify({"error": "platform and handle are required"}), 400

    root_dir = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root_dir, 'creators.json')

    try:
        if not os.path.exists(path):
            return jsonify({"error": "No creators configured"}), 404

        with open(path, 'r', encoding='utf-8') as f:
            creators = json.load(f)

        if platform in creators and handle in creators[platform]:
            creators[platform].remove(handle)

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(creators, f, ensure_ascii=False, indent=2)

            logger.info("API: Deleted %s creator: '%s'", platform, handle)
            return jsonify({"status": "success", "creators": creators})
        
        return jsonify({"error": "Creator not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
