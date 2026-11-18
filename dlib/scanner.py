"""Folder scanning: reconcile LIBRARY_DIR with the SQLite catalogue.

Scanning runs in a background thread because walking 100k files on Android
storage can take minutes. The UI polls /api/scan/status for progress.
"""

import os
import threading

from .db import get_db
from .metadata import extract_embedded_cover, extract_metadata, save_cover, validate_book_file
from .utils import FORMATS, now_iso

SKIP_DIRS = {"@eaDir", "$RECYCLE.BIN", "System Volume Information", "lost+found", ".thumbnails"}

_lock = threading.Lock()
_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "processed": 0,
    "added": 0,
    "updated": 0,
    "removed": 0,
    "error": None,
    "library_dir": "",
}


def scan_status():
    with _lock:
        return dict(_state)


def start_scan(app):
    with _lock:
        if _state["running"]:
            return False
        _state.update(
            running=True,
            started_at=now_iso(),
            finished_at=None,
            total=0,
            processed=0,
            added=0,
            updated=0,
            removed=0,
            error=None,
            library_dir=app.config["LIBRARY_DIR"],
        )
    thread = threading.Thread(target=_run_scan, args=(app,), daemon=True, name="library-scan")
    thread.start()
    return True


def _bump(**changes):
    with _lock:
        _state.update(changes)


def _collect_files(root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            fmt = FORMATS.get(ext)
            if not fmt:
                continue
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            relative = os.path.relpath(full, root).replace(os.sep, "/")
            found[relative] = (fmt, stat.st_size, stat.st_mtime)
    return found


def _run_scan(app):
    try:
        with app.app_context():
            _scan(app)
    except Exception as exc:  # noqa: BLE001 - surface failures to the UI
        _bump(error=str(exc), running=False, finished_at=now_iso())
    finally:
        with _lock:
            if _state["running"]:
                _state.update(running=False, finished_at=now_iso())


def _scan(app):
    db = get_db()
    root = app.config["LIBRARY_DIR"]
    found = _collect_files(root)
    existing = {
        row["file_path"]: row for row in db.execute("SELECT * FROM books").fetchall()
    }
    _bump(total=len(found))

    if not found and existing:
        _bump(
            error="No book files found. Is LIBRARY_DIR mounted? Aborting removal to protect the catalogue.",
            running=False,
            finished_at=now_iso(),
        )
        return

    processed = 0
    added = updated = 0
    for relative, (fmt, size, mtime) in found.items():
        processed += 1
        row = existing.get(relative)
        full = os.path.join(root, *relative.split("/"))
        try:
            if row is None:
                validate_book_file(full, fmt)
                metadata = extract_metadata(full, fmt, os.path.basename(full))
                timestamp = now_iso()
                cursor = db.execute(
                    """INSERT INTO books (title, author, description, tags, published_year,
                        publisher, language, isbn, page_count, format, file_path, file_size,
                        file_mtime, added_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        metadata["title"],
                        metadata["author"],
                        metadata["description"],
                        ", ".join(metadata["tags"]),
                        metadata["published_year"],
                        metadata["publisher"],
                        metadata["language"],
                        metadata["isbn"],
                        int(metadata["page_count"] or 0),
                        fmt,
                        relative,
                        size,
                        mtime,
                        timestamp,
                        timestamp,
                    ),
                )
                book_id = cursor.lastrowid
                cover = extract_embedded_cover(full, fmt)
                if cover:
                    cover_name = save_cover(book_id, cover[0], cover[1])
                    if cover_name:
                        db.execute(
                            "UPDATE books SET cover_path = ? WHERE id = ?", (cover_name, book_id)
                        )
                added += 1
            elif row["file_size"] != size or abs((row["file_mtime"] or 0) - mtime) > 1:
                db.execute(
                    "UPDATE books SET file_size = ?, file_mtime = ?, updated_at = ? WHERE id = ?",
                    (size, mtime, now_iso(), row["id"]),
                )
                updated += 1
        except Exception:  # noqa: BLE001 - skip broken files, keep scanning
            pass
        if processed % 100 == 0:
            db.commit()
            _bump(processed=processed, added=added, updated=updated)

    removed = 0
    for relative, row in existing.items():
        if relative in found:
            continue
        if row["cover_path"]:
            try:
                os.remove(os.path.join(app.config["COVERS_DIR"], row["cover_path"]))
            except OSError:
                pass
        db.execute("DELETE FROM books WHERE id = ?", (row["id"],))
        removed += 1
    db.commit()
    _bump(
        processed=processed,
        added=added,
        updated=updated,
        removed=removed,
        running=False,
        finished_at=now_iso(),
    )
