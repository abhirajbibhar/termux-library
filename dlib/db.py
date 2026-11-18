"""SQLite access layer.

The app uses the standard library ``sqlite3`` module: no ORM, no native
extensions. WAL mode keeps reads fast while the scanner writes in the
background, which matters once the library grows past a few thousand books.
"""

import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL DEFAULT '',
    author        TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    published_year TEXT NOT NULL DEFAULT '',
    publisher     TEXT NOT NULL DEFAULT '',
    language      TEXT NOT NULL DEFAULT '',
    isbn          TEXT NOT NULL DEFAULT '',
    page_count    INTEGER NOT NULL DEFAULT 0,
    format        TEXT NOT NULL,
    file_path     TEXT NOT NULL UNIQUE,
    file_size     INTEGER NOT NULL DEFAULT 0,
    cover_path    TEXT,
    file_mtime    REAL NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_books_author ON books(author COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_books_format ON books(format);
CREATE INDEX IF NOT EXISTS idx_books_added ON books(added_at DESC);

CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_collections (
    book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, collection_id)
);

CREATE INDEX IF NOT EXISTS idx_book_collections_collection
    ON book_collections(collection_id);

CREATE TABLE IF NOT EXISTS progress (
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id          INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    location         TEXT NOT NULL DEFAULT '',
    progress_percent REAL NOT NULL DEFAULT 0,
    page             INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (user_id, book_id)
);

CREATE INDEX IF NOT EXISTS idx_progress_updated ON progress(updated_at DESC);

CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'note',
    cfi         TEXT,
    page        INTEGER,
    text        TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#ffd54f',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_annotations_book_user
    ON annotations(book_id, user_id);
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    db = connect(app.config["DATABASE"])
    try:
        db.executescript(SCHEMA)
        db.commit()
    finally:
        db.close()
