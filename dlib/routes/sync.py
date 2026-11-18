"""Cross-device reading progress, bookmarks, notes and highlights."""

import re

from flask import Blueprint, abort, g, jsonify, request

from ..auth import login_required
from ..db import get_db
from ..utils import get_book, now_iso

bp = Blueprint("sync", __name__)

KINDS = {"bookmark", "highlight", "note"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clamp_percent(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(min(100.0, max(0.0, number)), 2)


def _annotation_dict(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "cfi": row["cfi"],
        "page": row["page"],
        "text": row["text"],
        "note": row["note"],
        "color": row["color"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@bp.get("/api/books/<int:book_id>/progress")
@login_required
def get_progress(book_id):
    if get_book(book_id) is None:
        abort(404)
    row = get_db().execute(
        "SELECT * FROM progress WHERE user_id = ? AND book_id = ?", (g.user["id"], book_id)
    ).fetchone()
    if row is None:
        return jsonify({"progress": {"location": "", "percent": 0, "page": 0, "updated_at": None}})
    return jsonify(
        {
            "progress": {
                "location": row["location"],
                "percent": row["progress_percent"],
                "page": row["page"],
                "updated_at": row["updated_at"],
            }
        }
    )


@bp.post("/api/books/<int:book_id>/progress")
@login_required
def save_progress(book_id):
    if get_book(book_id) is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    location = str(data.get("location") or "")[:4000]
    percent = _clamp_percent(data.get("progress_percent"))
    page = data.get("page")
    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0
    timestamp = now_iso()
    db = get_db()
    db.execute(
        """INSERT INTO progress (user_id, book_id, location, progress_percent, page, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, book_id) DO UPDATE SET
             location = excluded.location,
             progress_percent = excluded.progress_percent,
             page = excluded.page,
             updated_at = excluded.updated_at""",
        (g.user["id"], book_id, location, percent, page, timestamp),
    )
    db.commit()
    return jsonify({"ok": True, "progress": {"location": location, "percent": percent, "page": page, "updated_at": timestamp}})


@bp.delete("/api/books/<int:book_id>/progress")
@login_required
def reset_progress(book_id):
    db = get_db()
    db.execute("DELETE FROM progress WHERE user_id = ? AND book_id = ?", (g.user["id"], book_id))
    db.commit()
    return jsonify({"ok": True})


@bp.get("/api/books/<int:book_id>/annotations")
@login_required
def list_annotations(book_id):
    if get_book(book_id) is None:
        abort(404)
    kind = (request.args.get("kind") or "").strip().lower()
    sql = "SELECT * FROM annotations WHERE user_id = ? AND book_id = ?"
    params = [g.user["id"], book_id]
    if kind in KINDS:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY COALESCE(page, 0), created_at"
    rows = get_db().execute(sql, params).fetchall()
    return jsonify({"annotations": [_annotation_dict(row) for row in rows]})


@bp.post("/api/books/<int:book_id>/annotations")
@login_required
def create_annotation(book_id):
    if get_book(book_id) is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "note").strip().lower()
    if kind not in KINDS:
        return jsonify({"error": "kind must be bookmark, highlight or note"}), 400
    cfi = (data.get("cfi") or "").strip()[:4000] or None
    note = (data.get("note") or "").strip()[:8000]
    text = (data.get("text") or "").strip()[:8000]
    color = (data.get("color") or "#ffd54f").strip()
    if not HEX_COLOR.match(color):
        color = "#ffd54f"
    page = data.get("page")
    try:
        page = max(1, int(page)) if page not in (None, "") else None
    except (TypeError, ValueError):
        page = None
    if kind == "highlight" and not cfi and not text:
        return jsonify({"error": "A highlight needs a CFI or selected text"}), 400
    if kind == "note" and not note:
        return jsonify({"error": "Write a note first"}), 400
    if kind == "bookmark" and not cfi and page is None:
        return jsonify({"error": "No reading location supplied"}), 400

    timestamp = now_iso()
    db = get_db()
    cursor = db.execute(
        """INSERT INTO annotations (user_id, book_id, kind, cfi, page, text, note, color, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (g.user["id"], book_id, kind, cfi, page, text, note, color, timestamp, timestamp),
    )
    db.commit()
    row = db.execute("SELECT * FROM annotations WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify({"annotation": _annotation_dict(row)}), 201


@bp.patch("/api/annotations/<int:annotation_id>")
@login_required
def edit_annotation(annotation_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM annotations WHERE id = ? AND user_id = ?", (annotation_id, g.user["id"])
    ).fetchone()
    if row is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    note = (data.get("note") or row["note"]).strip()[:8000]
    text = (data.get("text") or row["text"]).strip()[:8000]
    color = (data.get("color") or row["color"]).strip()
    if not HEX_COLOR.match(color):
        color = row["color"]
    db.execute(
        "UPDATE annotations SET note = ?, text = ?, color = ?, updated_at = ? WHERE id = ?",
        (note, text, color, now_iso(), annotation_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
    return jsonify({"annotation": _annotation_dict(updated)})


@bp.delete("/api/annotations/<int:annotation_id>")
@login_required
def delete_annotation(annotation_id):
    db = get_db()
    row = db.execute(
        "SELECT id FROM annotations WHERE id = ? AND user_id = ?", (annotation_id, g.user["id"])
    ).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
    db.commit()
    return jsonify({"ok": True})
