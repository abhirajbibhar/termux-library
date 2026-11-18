"""Online metadata lookup and library folder scanning."""

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..db import get_db
from ..metadata import search_online
from ..scanner import scan_status, start_scan

bp = Blueprint("meta", __name__)


@bp.get("/api/metadata/search")
@login_required
def metadata_search():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"error": "Type at least two characters to search"}), 400
    try:
        results = search_online(query)
    except RuntimeError as exc:
        return jsonify({"error": f"Online lookup failed: {exc}"}), 502
    return jsonify({"results": results})


@bp.post("/api/scan")
@login_required
def scan_library():
    started = start_scan(current_app._get_current_object())
    if not started:
        return jsonify({"started": False, "status": scan_status()}), 202
    return jsonify({"started": True, "status": scan_status()}), 202


@bp.get("/api/scan/status")
@login_required
def scan_state():
    status = scan_status()
    db = get_db()
    status["book_count"] = db.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    status["library_dir"] = current_app.config["LIBRARY_DIR"]
    return jsonify(status)
