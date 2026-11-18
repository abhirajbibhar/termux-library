"""Minimal OPDS 1.2 catalogue for KOReader, Moon+ Reader, etc."""

import math
from xml.etree import ElementTree as ET

from flask import Blueprint, Response, abort, g, redirect, request

from ..auth import login_required
from ..db import get_db
from ..utils import MIME_TYPES, now_iso

bp = Blueprint("opds", __name__)

ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/terms/"
OPDS = "http://opds-spec.org/2010/catalog"

ET.register_namespace("", ATOM)
ET.register_namespace("dc", DC)
ET.register_namespace("opds", OPDS)

ACQUISITION = "application/atom+xml;profile=opds-catalog;kind=acquisition"
NAVIGATION = "application/atom+xml;profile=opds-catalog;kind=navigation"


def _abs(path):
    return request.url_root.rstrip("/") + path


def _sub(parent, tag, text=None, **attrs):
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = str(text)
    return element


def _feed(title, feed_id, kind, links, entries, self_path):
    root = ET.Element(f"{{{ATOM}}}feed")
    _sub(root, f"{{{ATOM}}}id", feed_id)
    _sub(root, f"{{{ATOM}}}title", title)
    _sub(root, f"{{{ATOM}}}updated", now_iso())
    author = _sub(root, f"{{{ATOM}}}author")
    _sub(author, f"{{{ATOM}}}name", "Digital Library")
    _sub(root, f"{{{ATOM}}}link", rel="self", type=kind, href=_abs(self_path))
    _sub(root, f"{{{ATOM}}}link", rel="start", type=NAVIGATION, href=_abs("/opds/"))
    _sub(
        root,
        f"{{{ATOM}}}link",
        rel="search",
        type="application/atom+xml",
        href=_abs("/opds/search?q={searchTerms}"),
    )
    for rel, kind_attr, href in links:
        _sub(root, f"{{{ATOM}}}link", rel=rel, type=kind_attr, href=href)
    for entry in entries:
        root.append(entry)
    return root


def _xml_response(root, kind):
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(payload, content_type=kind)


def _nav_entry(title, path, description=""):
    entry = ET.Element(f"{{{ATOM}}}entry")
    _sub(entry, f"{{{ATOM}}}id", f"urn:digital-library:navigation:{path}")
    _sub(entry, f"{{{ATOM}}}title", title)
    _sub(entry, f"{{{ATOM}}}updated", now_iso())
    if description:
        _sub(entry, f"{{{ATOM}}}content", description, type="text")
    _sub(entry, f"{{{ATOM}}}link", rel="subsection", type=ACQUISITION, href=_abs(path))
    return entry


def _book_entry(row):
    entry = ET.Element(f"{{{ATOM}}}entry")
    _sub(entry, f"{{{ATOM}}}id", f"urn:uuid:book-{row['id']}")
    _sub(entry, f"{{{ATOM}}}title", row["title"] or "Untitled")
    _sub(entry, f"{{{ATOM}}}updated", row["updated_at"] or row["added_at"])
    author = _sub(entry, f"{{{ATOM}}}author")
    _sub(author, f"{{{ATOM}}}name", row["author"] or "Unknown author")
    if row["description"]:
        _sub(entry, f"{{{ATOM}}}content", row["description"][:1500], type="text")
    if row["language"]:
        _sub(entry, f"{{{DC}}}language", row["language"])
    if row["isbn"]:
        _sub(entry, f"{{{DC}}}identifier", row["isbn"])
    if row["publisher"]:
        _sub(entry, f"{{{DC}}}publisher", row["publisher"])
    if row["published_year"]:
        _sub(entry, f"{{{DC}}}issued", row["published_year"])
    for tag in [value.strip() for value in (row["tags"] or "").split(",") if value.strip()]:
        _sub(entry, f"{{{ATOM}}}category", term=tag, label=tag)

    mime = MIME_TYPES.get(row["format"], "application/octet-stream")
    _sub(
        entry,
        f"{{{ATOM}}}link",
        rel="http://opds-spec.org/acquisition",
        type=mime,
        href=_abs(f"/api/books/{row['id']}/file?download=1"),
    )
    _sub(entry, f"{{{ATOM}}}link", rel="alternate", type="text/html", href=_abs(f"/read/{row['id']}"))
    if row["cover_path"]:
        cover = _abs(f"/covers/{row['cover_path']}")
        _sub(entry, f"{{{ATOM}}}link", rel="http://opds-spec.org/image", type="image/jpeg", href=cover)
        _sub(
            entry,
            f"{{{ATOM}}}link",
            rel="http://opds-spec.org/image/thumbnail",
            type="image/jpeg",
            href=cover,
        )
    return entry


def _clause(status=None, fmt=None, query=None):
    where, params = [], []
    if status == "unread":
        where.append("COALESCE(p.progress_percent, 0) < 1")
    elif status == "reading":
        where.append("COALESCE(p.progress_percent, 0) BETWEEN 1 AND 99.49")
    elif status == "finished":
        where.append("COALESCE(p.progress_percent, 0) >= 99.5")
    if fmt:
        where.append("b.format = ?")
        params.append(fmt)
    if query:
        like = f"%{query}%"
        where.append("(b.title LIKE ? OR b.author LIKE ? OR b.tags LIKE ?)")
        params.extend([like, like, like])
    return ("WHERE " + " AND ".join(where)) if where else "", params


def _acquisition_feed(title, self_path, status=None, fmt=None, query=None, sort="added"):
    page = max(1, _int(request.args.get("page"), 1))
    per_page = 50
    db = get_db()
    clause, params = _clause(status, fmt, query)
    join = "LEFT JOIN progress p ON p.book_id = b.id AND p.user_id = ?"
    user_id = g.user["id"]
    total = db.execute(f"SELECT COUNT(*) AS c FROM books b {join} {clause}", [user_id, *params]).fetchone()["c"]
    order = "b.added_at DESC" if sort == "added" else "b.title COLLATE NOCASE ASC"
    rows = db.execute(
        f"SELECT b.* FROM books b {join} {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        [user_id, *params, per_page, (page - 1) * per_page],
    ).fetchall()
    entries = [_book_entry(row) for row in rows]
    links = []
    last_page = max(1, math.ceil(total / per_page))
    if page > 1:
        links.append(("previous", ACQUISITION, _abs(_with_page(self_path, page - 1))))
        links.append(("first", ACQUISITION, _abs(_with_page(self_path, 1))))
    if page < last_page:
        links.append(("next", ACQUISITION, _abs(_with_page(self_path, page + 1))))
        links.append(("last", ACQUISITION, _abs(_with_page(self_path, last_page))))
    links.append(("up", NAVIGATION, _abs("/opds/")))
    root = _feed(title, _abs(self_path), ACQUISITION, links, entries, self_path)
    _sub(root, f"{{{OPDS}}}totalResults", total)
    _sub(root, f"{{{OPDS}}}itemsPerPage", per_page)
    _sub(root, f"{{{OPDS}}}startIndex", (page - 1) * per_page + 1)
    return _xml_response(root, ACQUISITION)


def _with_page(path, page):
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}page={page}"


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.get("/opds")
def opds_root_redirect():
    return redirect("/opds/", code=302)


@bp.get("/opds/")
@login_required
def opds_root():
    db = get_db()
    total = db.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    entries = [
        _nav_entry("All Books", "/opds/all", f"{total} books"),
        _nav_entry("Recently Added", "/opds/recent"),
        _nav_entry("Unread", "/opds/unread"),
        _nav_entry("Currently Reading", "/opds/reading"),
        _nav_entry("Finished", "/opds/finished"),
    ]
    formats = [row["format"] for row in db.execute("SELECT DISTINCT format FROM books ORDER BY format")]
    for fmt in formats:
        entries.append(_nav_entry(f"{fmt} books", f"/opds/format/{fmt}"))
    entries.append(_nav_entry("Search", "/opds/search?q="))
    root = _feed("Digital Library", _abs("/opds/"), NAVIGATION, [], entries, "/opds/")
    return _xml_response(root, NAVIGATION)


@bp.get("/opds/all")
@login_required
def opds_all():
    return _acquisition_feed("All Books", "/opds/all", sort="added")


@bp.get("/opds/recent")
@login_required
def opds_recent():
    return _acquisition_feed("Recently Added", "/opds/recent", sort="added")


@bp.get("/opds/unread")
@login_required
def opds_unread():
    return _acquisition_feed("Unread", "/opds/unread", status="unread", sort="title")


@bp.get("/opds/reading")
@login_required
def opds_reading():
    return _acquisition_feed("Currently Reading", "/opds/reading", status="reading", sort="title")


@bp.get("/opds/finished")
@login_required
def opds_finished():
    return _acquisition_feed("Finished", "/opds/finished", status="finished", sort="title")


@bp.get("/opds/format/<fmt>")
@login_required
def opds_format(fmt):
    fmt = fmt.upper()
    if fmt not in {"EPUB", "MOBI", "AZW3", "FB2", "CBZ", "PDF"}:
        abort(404)
    return _acquisition_feed(f"{fmt} books", f"/opds/format/{fmt}", fmt=fmt, sort="title")


@bp.get("/opds/search")
@login_required
def opds_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        root = _feed("Search", _abs("/opds/search"), NAVIGATION, [], [], "/opds/search")
        _sub(root, f"{{{ATOM}}}content", "Add ?q=terms to search the catalogue.", type="text")
        return _xml_response(root, NAVIGATION)
    return _acquisition_feed(f"Search: {query}", "/opds/search?q=" + query, query=query, sort="title")


@bp.get("/opds/opensearch.xml")
def opensearch():
    root = ET.Element("OpenSearchDescription", xmlns="http://a9.com/-/spec/opensearch/1.1/")
    _sub(root, "ShortName", "Digital Library")
    _sub(root, "Description", "Search the self-hosted library")
    _sub(
        root,
        "Url",
        type=ACQUISITION,
        template=_abs("/opds/search?q={searchTerms}"),
    )
    return Response(
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        content_type="application/opensearchdescription+xml",
    )
