"""Book catalogue, upload, download, bulk actions and metadata editing."""

import math
import os
import shutil
import tempfile
import uuid
import zipfile

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    request,
    send_file,
)

from ..auth import login_required
from ..db import get_db
from ..metadata import (
    best_match,
    cover_from_url,
    extract_embedded_cover,
    extract_metadata,
    save_cover,
    validate_book_file,
)
from ..utils import (
    FORMATS,
    MIME_TYPES,
    book_with_details,
    clean_filename,
    delete_book_files,
    get_book,
    move_book_file,
    now_iso,
    resolve_library_path,
    sanitize_component,
    split_tags,
    tags_to_string,
    title_from_filename,
    unique_library_path,
)

bp = Blueprint("books", __name__)

SORTS = {
    "added_desc": "b.added_at DESC, b.id DESC",
    "added_asc": "b.added_at ASC, b.id ASC",
    "title": "b.title COLLATE NOCASE ASC",
    "author": "b.author COLLATE NOCASE ASC, b.title COLLATE NOCASE ASC",
    "recent": "COALESCE(p.updated_at, '') DESC, b.added_at DESC",
    "size": "b.file_size DESC",
}


def _int(value, default, minimum=1, maximum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _parse_ids(raw):
    ids = []
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    return list(dict.fromkeys(ids))


def _filters(args, user_id):
    where, params = [], []
    query = (args.get("q") or "").strip()
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append(
            "(b.title LIKE ? ESCAPE '\\' OR b.author LIKE ? ESCAPE '\\' "
            "OR b.tags LIKE ? ESCAPE '\\' OR b.description LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like])
    fmt = (args.get("format") or "").strip().upper()
    if fmt:
        where.append("b.format = ?")
        params.append(fmt)
    status = (args.get("status") or "").strip().lower()
    if status == "unread":
        where.append("COALESCE(p.progress_percent, 0) < 1")
    elif status == "reading":
        where.append("COALESCE(p.progress_percent, 0) >= 1 AND COALESCE(p.progress_percent, 0) < 99.5")
    elif status == "finished":
        where.append("COALESCE(p.progress_percent, 0) >= 99.5")
    collection = args.get("collection")
    if collection:
        where.append(
            "EXISTS (SELECT 1 FROM book_collections bc WHERE bc.book_id = b.id AND bc.collection_id = ?)"
        )
        params.append(_int(collection, 0))
    ids = _parse_ids(args.get("ids"))
    if ids:
        where.append(f"b.id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return clause, params


@bp.get("/api/books")
@login_required
def list_books():
    db = get_db()
    user_id = g.user["id"]
    page = _int(request.args.get("page"), 1)
    per_page = _int(request.args.get("per_page"), 24, 1, 200)
    clause, params = _filters(request.args, user_id)
    sort = SORTS.get(request.args.get("sort"), SORTS["added_desc"])

    join = "LEFT JOIN progress p ON p.book_id = b.id AND p.user_id = ?"
    total = db.execute(
        f"SELECT COUNT(*) AS c FROM books b {join} {clause}", [user_id, *params]
    ).fetchone()["c"]
    rows = db.execute(
        f"SELECT b.* FROM books b {join} {clause} ORDER BY {sort} LIMIT ? OFFSET ?",
        [user_id, *params, per_page, (page - 1) * per_page],
    ).fetchall()
    return jsonify(
        {
            "books": [book_with_details(row) for row in rows],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": max(1, math.ceil(total / per_page)),
        }
    )


@bp.get("/api/books/<int:book_id>")
@login_required
def book_detail(book_id):
    row = get_book(book_id)
    if row is None:
        abort(404)
    return jsonify({"book": book_with_details(row)})


@bp.get("/api/stats")
@login_required
def stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    formats = {
        row["format"]: row["c"]
        for row in db.execute("SELECT format, COUNT(*) AS c FROM books GROUP BY format")
    }
    status_rows = db.execute(
        """SELECT
             SUM(CASE WHEN progress_percent >= 99.5 THEN 1 ELSE 0 END) AS finished,
             SUM(CASE WHEN progress_percent >= 1 AND progress_percent < 99.5 THEN 1 ELSE 0 END) AS reading,
             SUM(CASE WHEN progress_percent IS NULL OR progress_percent < 1 THEN 1 ELSE 0 END) AS unread
           FROM books b
           LEFT JOIN progress p ON p.book_id = b.id AND p.user_id = ?""",
        (g.user["id"],),
    ).fetchone()
    return jsonify(
        {
            "total": total,
            "formats": formats,
            "status": {
                "finished": status_rows["finished"] or 0,
                "reading": status_rows["reading"] or 0,
                "unread": status_rows["unread"] or total,
            },
        }
    )


@bp.get("/api/books/<int:book_id>/file")
@bp.get("/api/books/<int:book_id>/file/<path:filename>")
@login_required
def book_file(book_id, filename=None):
    row = get_book(book_id)
    if row is None:
        abort(404)
    path = resolve_library_path(row["file_path"])
    if not os.path.isfile(path):
        return jsonify({"error": "The book file is missing from LIBRARY_DIR"}), 404
    download = request.args.get("download") == "1"
    response = send_file(
        path,
        mimetype=MIME_TYPES.get(row["format"], "application/octet-stream"),
        conditional=True,
        etag=True,
        last_modified=os.path.getmtime(path),
        as_attachment=download,
        download_name=os.path.basename(path),
        max_age=0,
    )
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return response


@bp.get("/covers/<path:filename>")
def covers(filename):
    if g.get("user") is None:
        abort(401)
    name = os.path.basename(filename)
    path = os.path.join(current_app.config["COVERS_DIR"], name)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, conditional=True, max_age=86400)


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #

def _looks_incomplete(metadata, original_name):
    return not metadata.get("author") or metadata.get("title") == title_from_filename(original_name)


def _auto_enrich(metadata, original_name):
    """Fill missing metadata + cover from Google Books/OpenLibrary."""
    if not current_app.config["APP_CONFIG"].get("auto_metadata"):
        return
    if not _looks_incomplete(metadata, original_name):
        return
    match = best_match(metadata.get("title") or title_from_filename(original_name), metadata.get("author") or "")
    if not match:
        return
    for key in ("author", "description", "published_year", "publisher", "language", "isbn"):
        if not metadata.get(key) and match.get(key):
            metadata[key] = match[key]
    if not metadata.get("tags") and match.get("tags"):
        metadata["tags"] = match["tags"]
    if not metadata.get("title") and match.get("title"):
        metadata["title"] = match["title"]
    if match.get("cover_url"):
        metadata["_online_cover"] = match["cover_url"]


def _store_file(storage, original_name, allow_auto):
    filename, ext = clean_filename(original_name)
    fmt = FORMATS.get(ext)
    if fmt is None:
        return {"name": original_name, "error": "Unsupported file type"}
    tmp_path = os.path.join(current_app.config["TMP_DIR"], f"{uuid.uuid4().hex}{ext}")
    try:
        storage.save(tmp_path)
        validate_book_file(tmp_path, fmt)
        metadata = extract_metadata(tmp_path, fmt, original_name)
        if allow_auto:
            try:
                _auto_enrich(metadata, original_name)
            except Exception:  # noqa: BLE001 - online lookup is best-effort
                pass

        author = metadata.get("author") or ""
        title = metadata.get("title") or title_from_filename(original_name)
        relative = unique_library_path(author, title, filename)
        destination = os.path.join(current_app.config["LIBRARY_DIR"], *relative.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(tmp_path, destination)
        stat = os.stat(destination)

        timestamp = now_iso()
        db = get_db()
        cursor = db.execute(
            """INSERT INTO books (title, author, description, tags, published_year, publisher,
                language, isbn, page_count, format, file_path, file_size, file_mtime, added_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                author,
                metadata.get("description") or "",
                ", ".join(split_tags(metadata.get("tags") or [])),
                metadata.get("published_year") or "",
                metadata.get("publisher") or "",
                metadata.get("language") or "",
                metadata.get("isbn") or "",
                int(metadata.get("page_count") or 0),
                fmt,
                relative,
                stat.st_size,
                stat.st_mtime,
                timestamp,
                timestamp,
            ),
        )
        book_id = cursor.lastrowid

        cover_name = None
        embedded = extract_embedded_cover(destination, fmt)
        if embedded:
            cover_name = save_cover(book_id, embedded[0], embedded[1])
        if not cover_name and metadata.get("_online_cover"):
            try:
                data, content_type = cover_from_url(metadata["_online_cover"])
                cover_name = save_cover(book_id, data, content_type)
            except (ValueError, OSError):
                cover_name = None
        if cover_name:
            db.execute("UPDATE books SET cover_path = ? WHERE id = ?", (cover_name, book_id))
        db.commit()
        row = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return {
            "name": original_name,
            "book": book_with_details(row),
            "needs_metadata": _looks_incomplete(metadata, original_name),
        }
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@bp.post("/api/books/upload")
@login_required
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files were uploaded"}), 400
    paths = request.form.getlist("paths")
    allow_auto = len(files) <= 3 and request.form.get("auto_meta", "1") != "0"
    added, errors = [], []
    for index, storage in enumerate(files):
        original_name = paths[index] if index < len(paths) and paths[index] else storage.filename
        if not (original_name or "").strip():
            continue
        try:
            result = _store_file(storage, original_name, allow_auto)
        except ValueError as exc:
            errors.append({"name": original_name, "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - report per file, keep batch going
            current_app.logger.exception("Upload failed for %s", original_name)
            errors.append({"name": original_name, "error": str(exc)})
            continue
        if result.get("error"):
            errors.append(result)
        else:
            added.append(result)
    return jsonify({"added": added, "errors": errors, "count": len(added)}), (200 if added else 400)


# --------------------------------------------------------------------------- #
# Edit / delete
# --------------------------------------------------------------------------- #

@bp.patch("/api/books/<int:book_id>")
@login_required
def edit_book(book_id):
    row = get_book(book_id)
    if row is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    db = get_db()
    updates, params = [], []

    for field in ("title", "author", "description", "publisher", "language", "isbn", "published_year"):
        if field in data:
            value = str(data.get(field) or "").strip()
            if field == "title" and not value:
                return jsonify({"error": "Title cannot be empty"}), 400
            updates.append(f"{field} = ?")
            params.append(value[:8000] if field == "description" else value[:300])
    if "tags" in data:
        updates.append("tags = ?")
        params.append(tags_to_string(data.get("tags") or ""))
    if "page_count" in data:
        updates.append("page_count = ?")
        params.append(_int(data.get("page_count"), 0, 0))

    if not updates:
        return jsonify({"error": "Nothing to update"}), 400

    new_title = str(data.get("title", row["title"]) or "").strip()
    new_author = str(data.get("author", row["author"]) or "").strip()
    target = None
    if new_title != row["title"] or new_author != row["author"]:
        try:
            target = move_book_file(row, new_author, new_title)
        except OSError as exc:
            current_app.logger.warning("Could not reorganise %s: %s", row["file_path"], exc)
        if target:
            updates.append("file_path = ?")
            params.append(target)

    updates.append("updated_at = ?")
    params.append(now_iso())
    params.append(book_id)
    db.execute(f"UPDATE books SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    updated = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return jsonify({"book": book_with_details(updated)})


@bp.post("/api/books/<int:book_id>/cover")
@login_required
def replace_cover(book_id):
    row = get_book(book_id)
    if row is None:
        abort(404)
    db = get_db()
    upload_file = request.files.get("cover")
    data = content_type = None
    if upload_file is not None:
        data = upload_file.read(8 * 1024 * 1024 + 1)
        content_type = upload_file.mimetype or ""
        if len(data) > 8 * 1024 * 1024:
            return jsonify({"error": "Cover image must be smaller than 8 MB"}), 400
    else:
        url = (request.get_json(silent=True) or {}).get("url") or ""
        try:
            data, content_type = cover_from_url(url)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    cover_name = save_cover(book_id, data, content_type or "")
    if not cover_name:
        return jsonify({"error": "Unsupported image format (use JPEG, PNG, WebP or GIF)"}), 400
    db.execute(
        "UPDATE books SET cover_path = ?, updated_at = ? WHERE id = ?",
        (cover_name, now_iso(), book_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return jsonify({"book": book_with_details(updated)})


@bp.delete("/api/books/<int:book_id>")
@login_required
def delete_book(book_id):
    row = get_book(book_id)
    if row is None:
        abort(404)
    delete_file = request.args.get("delete_file", "1") != "0"
    db = get_db()
    db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    db.commit()
    if delete_file:
        delete_book_files(row)
    return jsonify({"ok": True, "deleted": book_id})


# --------------------------------------------------------------------------- #
# Bulk operations
# --------------------------------------------------------------------------- #

@bp.post("/api/books/bulk")
@login_required
def bulk_action():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    ids = [int(item) for item in (data.get("ids") or []) if str(item).isdigit()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return jsonify({"error": "No books selected"}), 400
    db = get_db()
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"SELECT * FROM books WHERE id IN ({placeholders})", ids).fetchall()

    if action == "delete":
        delete_files = data.get("delete_files", True)
        db.execute(f"DELETE FROM books WHERE id IN ({placeholders})", ids)
        db.commit()
        if delete_files:
            for row in rows:
                delete_book_files(row)
        return jsonify({"ok": True, "count": len(rows)})

    collection_id = data.get("collection_id")
    if action in {"add_to_collection", "remove_from_collection", "move_to_collection"}:
        if not str(collection_id).isdigit():
            return jsonify({"error": "Choose a collection"}), 400
        collection = db.execute("SELECT id FROM collections WHERE id = ?", (int(collection_id),)).fetchone()
        if collection is None:
            return jsonify({"error": "Collection not found"}), 404
        if action == "move_to_collection":
            db.execute(
                f"DELETE FROM book_collections WHERE book_id IN ({placeholders})", ids
            )
            db.executemany(
                "INSERT OR IGNORE INTO book_collections (book_id, collection_id) VALUES (?, ?)",
                [(row["id"], int(collection_id)) for row in rows],
            )
        elif action == "add_to_collection":
            db.executemany(
                "INSERT OR IGNORE INTO book_collections (book_id, collection_id) VALUES (?, ?)",
                [(row["id"], int(collection_id)) for row in rows],
            )
        else:
            db.executemany(
                "DELETE FROM book_collections WHERE book_id = ? AND collection_id = ?",
                [(row["id"], int(collection_id)) for row in rows],
            )
        db.commit()
        return jsonify({"ok": True, "count": len(rows)})

    return jsonify({"error": "Unknown action"}), 400


@bp.get("/api/books/bulk/download")
@login_required
def bulk_download():
    db = get_db()
    ids = _parse_ids(request.args.get("ids"))
    collection_id = request.args.get("collection")
    if collection_id and str(collection_id).isdigit():
        ids = [
            row["id"]
            for row in db.execute(
                "SELECT book_id AS id FROM book_collections WHERE collection_id = ?",
                (int(collection_id),),
            )
        ]
    if not ids:
        return jsonify({"error": "No books selected"}), 400
    if len(ids) > 1000:
        return jsonify({"error": "Select at most 1000 books for a ZIP download"}), 400

    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"SELECT * FROM books WHERE id IN ({placeholders})", ids).fetchall()
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".zip", dir=current_app.config["TMP_DIR"]
    )
    tmp.close()
    used, written = set(), 0
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for row in rows:
            try:
                path = resolve_library_path(row["file_path"])
            except Exception:  # noqa: BLE001
                continue
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(path)[1]
            base = f"{sanitize_component(row['author'], 'Unknown Author')} - {sanitize_component(row['title'], 'Untitled')}"
            arcname = f"{base}{ext}"
            counter = 2
            while arcname.lower() in used:
                arcname = f"{base} ({counter}){ext}"
                counter += 1
            used.add(arcname.lower())
            archive.write(path, arcname)
            written += 1

    if not written:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        return jsonify({"error": "None of the selected files are available"}), 404

    response = send_file(
        tmp.name,
        mimetype="application/zip",
        as_attachment=True,
        download_name="library-selection.zip",
    )
    response.call_on_close(lambda: _remove_quietly(tmp.name))
    return response


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass
