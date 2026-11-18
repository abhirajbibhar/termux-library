"""Custom collections / shelves."""

from flask import Blueprint, abort, jsonify, request

from ..auth import login_required
from ..db import get_db
from ..utils import now_iso

bp = Blueprint("collections", __name__)


@bp.get("/api/collections")
@login_required
def list_collections():
    rows = get_db().execute(
        """SELECT c.id, c.name, c.description, c.created_at,
                  COUNT(bc.book_id) AS book_count
           FROM collections c
           LEFT JOIN book_collections bc ON bc.collection_id = c.id
           GROUP BY c.id
           ORDER BY c.name COLLATE NOCASE"""
    )
    return jsonify({"collections": [dict(row) for row in rows]})


@bp.post("/api/collections")
@login_required
def create_collection():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name or len(name) > 80:
        return jsonify({"error": "Collection name is required (max 80 characters)"}), 400
    description = (data.get("description") or "").strip()[:500]
    db = get_db()
    exists = db.execute("SELECT id FROM collections WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if exists:
        return jsonify({"error": "A collection with that name already exists"}), 409
    cursor = db.execute(
        "INSERT INTO collections (name, description, created_at) VALUES (?, ?, ?)",
        (name, description, now_iso()),
    )
    db.commit()
    return jsonify(
        {
            "collection": {
                "id": cursor.lastrowid,
                "name": name,
                "description": description,
                "book_count": 0,
            }
        }
    ), 201


@bp.patch("/api/collections/<int:collection_id>")
@login_required
def edit_collection(collection_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        abort(404)
    name = (data.get("name") or row["name"]).strip()[:80]
    if not name:
        return jsonify({"error": "Collection name is required"}), 400
    if name.lower() != row["name"].lower():
        clash = db.execute(
            "SELECT id FROM collections WHERE name = ? COLLATE NOCASE AND id != ?",
            (name, collection_id),
        ).fetchone()
        if clash:
            return jsonify({"error": "A collection with that name already exists"}), 409
    description = (data.get("description") or row["description"]).strip()[:500]
    db.execute(
        "UPDATE collections SET name = ?, description = ? WHERE id = ?",
        (name, description, collection_id),
    )
    db.commit()
    return jsonify({"ok": True, "collection": {"id": collection_id, "name": name, "description": description}})


@bp.delete("/api/collections/<int:collection_id>")
@login_required
def delete_collection(collection_id):
    db = get_db()
    row = db.execute("SELECT id FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    db.commit()
    return jsonify({"ok": True})
