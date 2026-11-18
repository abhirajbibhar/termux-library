"""Shared helpers: paths, filenames, serialisation and file cleanup."""

import os
import re
import shutil
from datetime import datetime, timezone
from urllib.parse import quote

from flask import abort, current_app, g

FORMATS = {
    ".epub": "EPUB",
    ".mobi": "MOBI",
    ".azw3": "AZW3",
    ".fb2": "FB2",
    ".cbz": "CBZ",
    ".pdf": "PDF",
}

MIME_TYPES = {
    "EPUB": "application/epub+zip",
    "MOBI": "application/x-mobipocket-ebook",
    "AZW3": "application/vnd.amazon.ebook",
    "FB2": "application/x-fictionbook+xml",
    "CBZ": "application/vnd.comicbook+zip",
    "PDF": "application/pdf",
}

COVER_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

IMAGE_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACES = re.compile(r"\s+")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_component(value, fallback="Unknown", max_length=90):
    value = (value or "").strip()
    value = _UNSAFE.sub(" ", value)
    value = _SPACES.sub(" ", value).strip(" .")
    if not value:
        value = fallback
    if len(value) > max_length:
        value = value[:max_length].rstrip(" .")
    return value or fallback


def clean_filename(name, fallback="book"):
    base = os.path.basename((name or "").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    stem = sanitize_component(stem, fallback)
    ext = ext.lower()
    return stem + ext, ext


def guess_format(filename):
    ext = os.path.splitext(filename)[1].lower()
    return FORMATS.get(ext)


def title_from_filename(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"[_\.]+", " ", stem)
    stem = re.sub(r"\s*[\(\[]\s*(z-?lib(rary)?|libgen|annas? archive)[^\)\]]*[\)\]]", "", stem, flags=re.I)
    stem = _SPACES.sub(" ", stem).strip(" -–—")
    return stem or "Untitled"


def resolve_library_path(relative_path):
    root = current_app.config["LIBRARY_DIR"]
    path = os.path.normpath(os.path.join(root, *relative_path.split("/")))
    root_norm = os.path.normpath(root)
    if path != root_norm and not path.startswith(root_norm + os.sep):
        abort(400, "Invalid book path")
    return path


def book_target_path(author, title, filename):
    """Return the desired LIBRARY_DIR/<Author>/<Title>/<file> relative path."""
    author_dir = sanitize_component(author, "Unknown Author")
    title_dir = sanitize_component(title, "Untitled")
    return "/".join((author_dir, title_dir, filename))


def unique_library_path(author, title, filename):
    """Pick a free path, appending ``(2)``, ``(3)`` ... when needed."""
    root = current_app.config["LIBRARY_DIR"]
    stem, ext = os.path.splitext(filename)
    relative = book_target_path(author, title, filename)
    counter = 2
    while os.path.exists(os.path.join(root, *relative.split("/"))):
        relative = book_target_path(author, title, f"{stem} ({counter}){ext}")
        counter += 1
    return relative


def split_tags(value):
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[,;]", str(value or ""))
    seen, tags = set(), []
    for part in parts:
        tag = _SPACES.sub(" ", str(part)).strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def tags_to_string(value):
    return ", ".join(split_tags(value))


def status_from_percent(percent):
    if percent is None:
        return "unread"
    if percent >= 99.5:
        return "finished"
    if percent > 0:
        return "reading"
    return "unread"


def book_row_to_dict(row, progress=None, collections=None):
    data = {
        "id": row["id"],
        "title": row["title"] or "Untitled",
        "author": row["author"],
        "description": row["description"],
        "tags": split_tags(row["tags"]),
        "published_year": row["published_year"],
        "publisher": row["publisher"],
        "language": row["language"],
        "isbn": row["isbn"],
        "page_count": row["page_count"],
        "format": row["format"],
        "file_path": row["file_path"],
        # Keep the real filename in the URL: Foliate detects CBZ/FB2 formats
        # from the path extension (view.js fetchFile -> new File(..., pathname)).
        "file_url": f"/api/books/{row['id']}/file/{quote(os.path.basename(row['file_path']))}",
        "download_url": f"/api/books/{row['id']}/file?download=1",
        "file_size": row["file_size"],
        "cover": row["cover_path"],
        "cover_url": f"/covers/{row['cover_path']}" if row["cover_path"] else None,
        "added_at": row["added_at"],
        "updated_at": row["updated_at"],
        "collections": collections or [],
    }
    percent = None
    location = ""
    if progress is not None:
        percent = progress["progress_percent"]
        location = progress["location"]
    data["progress"] = {
        "location": location,
        "percent": round(percent, 2) if percent is not None else 0,
        "updated_at": progress["updated_at"] if progress is not None else None,
        "status": status_from_percent(percent),
    }
    return data


def get_book(book_id):
    row = g.db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        return None
    return row


def book_with_details(row):
    progress = g.db.execute(
        "SELECT * FROM progress WHERE user_id = ? AND book_id = ?", (g.user["id"], row["id"])
    ).fetchone()
    collections = [
        {"id": r["id"], "name": r["name"]}
        for r in g.db.execute(
            """SELECT c.id, c.name FROM book_collections bc
               JOIN collections c ON c.id = bc.collection_id
               WHERE bc.book_id = ? ORDER BY c.name COLLATE NOCASE""",
            (row["id"],),
        )
    ]
    return book_row_to_dict(row, progress, collections)


def delete_book_files(row):
    """Remove the book file, its cover and any directories left empty."""
    root = current_app.config["LIBRARY_DIR"]
    try:
        path = resolve_library_path(row["file_path"])
        if os.path.isfile(path):
            os.remove(path)
        parent = os.path.dirname(path)
        author_dir = os.path.dirname(parent)
        for directory in (parent, author_dir):
            if os.path.normpath(directory) != os.path.normpath(root):
                try:
                    os.rmdir(directory)
                except OSError:
                    break
    except OSError:
        pass
    if row["cover_path"]:
        try:
            os.remove(os.path.join(current_app.config["COVERS_DIR"], row["cover_path"]))
        except OSError:
            pass


def move_book_file(row, author, title):
    """Move a book into <Author>/<Title>/ after a metadata edit."""
    root = current_app.config["LIBRARY_DIR"]
    current = resolve_library_path(row["file_path"])
    filename = os.path.basename(current)
    target_rel = unique_library_path(author, title, filename)
    if target_rel == row["file_path"]:
        return None
    target = os.path.join(root, *target_rel.split("/"))
    if not os.path.exists(current):
        return None
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(current, target)
    old_dir = os.path.dirname(current)
    for directory in (old_dir, os.path.dirname(old_dir)):
        if os.path.normpath(directory) != os.path.normpath(root):
            try:
                os.rmdir(directory)
            except OSError:
                break
    return target_rel


def human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def cover_extension(head, content_type=""):
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type in COVER_MIME:
        return COVER_MIME[content_type]
    for magic, ext in IMAGE_MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None
