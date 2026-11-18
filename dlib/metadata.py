"""Book metadata and cover handling.

Everything here is pure Python: ``zipfile`` + ``xml.etree`` for EPUB/CBZ/FB2,
``pypdf`` for PDFs, and ``urllib`` for the optional Google Books /
OpenLibrary lookups. No Pillow, no ImageMagick, no native builds.
"""

import html
import json
import os
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from xml.etree import ElementTree as ET

from flask import current_app

from .utils import (
    cover_extension,
    split_tags,
    title_from_filename,
)

USER_AGENT = "DigitalLibrary/1.0 (self-hosted; Termux)"
MAX_COVER_BYTES = 8 * 1024 * 1024

ALLOWED_COVER_HOSTS = (
    "covers.openlibrary.org",
    "books.google.com",
    "books.googleusercontent.com",
    "lh3.googleusercontent.com",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _children_text(element, name):
    values = []
    for child in element.iter():
        if _local(child.tag) == name and (child.text or "").strip():
            values.append(child.text.strip())
    return values


def _strip_html(value):
    if not value:
        return ""
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
    value = re.sub(r"</\s*p\s*>", "\n\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:6000]


def _normalise_date(value):
    match = re.search(r"\d{4}", value or "")
    return match.group(0) if match else ""


def _normalise_isbn(values):
    for value in values:
        digits = re.sub(r"[^0-9Xx]", "", value or "")
        if len(digits) in (10, 13) and (digits.startswith("978") or digits.startswith("979") or len(digits) == 10):
            return digits.upper()
    return ""


def _read_zip_text(zf, name, limit=4 * 1024 * 1024):
    with zf.open(name) as fh:
        return fh.read(limit)


def _epub_package(zf):
    opf_name = None
    try:
        container = ET.fromstring(_read_zip_text(zf, "META-INF/container.xml"))
        for element in container.iter():
            if _local(element.tag) == "rootfile" and element.get("full-path"):
                opf_name = element.get("full-path")
                break
    except (KeyError, ET.ParseError, OSError):
        opf_name = None
    if not opf_name:
        names = zf.namelist()
        opf_name = next((n for n in names if n.lower().endswith(".opf")), None)
    if not opf_name:
        return None, None
    try:
        opf = ET.fromstring(_read_zip_text(zf, opf_name))
    except (KeyError, ET.ParseError, OSError):
        return None, None
    return opf, posixpath.dirname(opf_name)


def _epub_metadata(zf):
    opf, base = _epub_package(zf)
    if opf is None:
        return {}
    metadata = next((el for el in opf.iter() if _local(el.tag) == "metadata"), opf)
    titles = _children_text(metadata, "title")
    creators = _children_text(metadata, "creator")
    subjects = _children_text(metadata, "subject")
    descriptions = _children_text(metadata, "description")
    publishers = _children_text(metadata, "publisher")
    dates = _children_text(metadata, "date")
    languages = _children_text(metadata, "language")
    identifiers = _children_text(metadata, "identifier")
    return {
        "title": titles[0] if titles else "",
        "author": ", ".join(dict.fromkeys(creators)),
        "description": _strip_html(descriptions[0]) if descriptions else "",
        "tags": split_tags(subjects),
        "published_year": _normalise_date(dates[0]) if dates else "",
        "publisher": publishers[0] if publishers else "",
        "language": languages[0] if languages else "",
        "isbn": _normalise_isbn(identifiers),
    }


def _epub_cover(zf):
    opf, base = _epub_package(zf)
    if opf is None:
        return None
    manifest = {}
    cover_id = ""
    cover_property_href = ""
    guide_href = ""
    for element in opf.iter():
        name = _local(element.tag)
        if name == "meta" and (element.get("name") or "").lower() == "cover":
            cover_id = element.get("content") or ""
        elif name == "item":
            item_id = element.get("id") or ""
            href = element.get("href") or ""
            media = (element.get("media-type") or "").lower()
            props = (element.get("properties") or "").lower()
            manifest[item_id] = (href, media)
            if "cover-image" in props and media.startswith("image/"):
                cover_property_href = href
        elif name == "reference":
            if (element.get("type") or "").lower() == "cover":
                guide_href = element.get("href") or ""
    candidates = []
    if cover_id and cover_id in manifest:
        candidates.append(manifest[cover_id])
    if cover_property_href:
        candidates.append((cover_property_href, "image/jpeg"))
    if guide_href:
        candidates.append((guide_href, "image/jpeg"))
    for item_id, (href, media) in manifest.items():
        if media.startswith("image/") and "cover" in (href or "").lower():
            candidates.append((href, media))
    for href, media in candidates:
        if not href:
            continue
        path = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(href)))
        try:
            data = zf.read(path)
        except KeyError:
            continue
        if data and len(data) <= MAX_COVER_BYTES:
            return data, media
    return None


def _cbz_cover(zf):
    images = [
        name
        for name in zf.namelist()
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
        and not name.startswith("__MACOSX")
    ]
    if not images:
        return None
    images.sort(key=lambda value: [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", value)])
    data = zf.read(images[0])
    if len(data) > MAX_COVER_BYTES:
        return None
    return data, ""


def _fb2_payload(path):
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head[:2] == b"PK":
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".fb2")), None)
            if not name:
                return None
            return zf.read(name)
    with open(path, "rb") as fh:
        return fh.read(8 * 1024 * 1024)


def _fb2_metadata(path):
    data = _fb2_payload(path)
    if not data:
        return {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    title_info = next((el for el in root.iter() if _local(el.tag) == "title-info"), None)
    if title_info is None:
        return {}
    titles = _children_text(title_info, "book-title")
    authors = []
    for author in title_info:
        if _local(author.tag) != "author":
            continue
        parts = [
            (el.text or "").strip()
            for el in author
            if _local(el.tag) in ("first-name", "middle-name", "last-name") and (el.text or "").strip()
        ]
        if (el_nick := next((el.text for el in author if _local(el.tag) == "nickname" and el.text), "")):
            parts = [el_nick.strip()]
        if parts:
            authors.append(" ".join(parts))
    annotation = ""
    for child in title_info:
        if _local(child.tag) == "annotation":
            paragraphs = _children_text(child, "p")
            annotation = "\n\n".join(paragraphs) if paragraphs else "".join(child.itertext()).strip()
            break
    genres = _children_text(title_info, "genre")
    year = _normalise_date(" ".join(_children_text(title_info, "date")))
    lang = _children_text(title_info, "lang")
    return {
        "title": titles[0] if titles else "",
        "author": ", ".join(dict.fromkeys(authors)),
        "description": _strip_html(annotation),
        "tags": split_tags(genres),
        "published_year": year,
        "language": lang[0] if lang else "",
    }


def _pdf_metadata(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return {}
    try:
        with open(path, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            info = reader.metadata or {}
            pages = len(reader.pages)
            created = str(info.get("/CreationDate") or "")
        meta = {
            "title": str(info.get("/Title") or "").strip(),
            "author": str(info.get("/Author") or "").strip(),
            "publisher": str(info.get("/Publisher") or "").strip(),
            "description": _strip_html(str(info.get("/Subject") or "").strip()),
            "page_count": pages,
        }
        meta["published_year"] = _normalise_date(created)
        return {key: value for key, value in meta.items() if value}
    except Exception:  # noqa: BLE001 - damaged PDFs must not break uploads
        return {}


def validate_book_file(path, fmt):
    """Reject files that clearly are not the format their extension claims."""
    if fmt in {"EPUB", "CBZ"}:
        if not zipfile.is_zipfile(path):
            raise ValueError(f"Not a valid {fmt} archive")
    elif fmt == "PDF":
        with open(path, "rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise ValueError("Not a valid PDF file")
    elif fmt == "FB2":
        data = _fb2_payload(path)
        if not data or b"<" not in data[:2000]:
            raise ValueError("Not a valid FB2 file")
    elif fmt in {"MOBI", "AZW3"}:
        with open(path, "rb") as fh:
            head = fh.read(200)
        if b"BOOKMOBI" not in head and b"TEXtREAd" not in head:
            raise ValueError(f"Not a valid {fmt} file")
    return True


def extract_metadata(path, fmt, original_name):
    """Best-effort metadata for a book file. Never raises."""
    meta = {}
    try:
        if fmt == "EPUB":
            with zipfile.ZipFile(path) as zf:
                meta = _epub_metadata(zf)
        elif fmt == "CBZ":
            with zipfile.ZipFile(path) as zf:
                meta = {}
        elif fmt == "FB2":
            meta = _fb2_metadata(path)
        elif fmt == "PDF":
            meta = _pdf_metadata(path)
    except Exception:  # noqa: BLE001
        meta = {}
    fallback_title = title_from_filename(original_name)
    meta.setdefault("title", "")
    if not meta["title"]:
        meta["title"] = fallback_title
    meta.setdefault("author", "")
    meta.setdefault("description", "")
    meta.setdefault("tags", [])
    meta.setdefault("published_year", "")
    meta.setdefault("publisher", "")
    meta.setdefault("language", "")
    meta.setdefault("isbn", "")
    meta.setdefault("page_count", 0)
    return meta


def extract_embedded_cover(path, fmt):
    """Return ``(bytes, content_type)`` for a book's own cover, or ``None``."""
    try:
        if fmt == "EPUB":
            with zipfile.ZipFile(path) as zf:
                return _epub_cover(zf)
        if fmt == "CBZ":
            with zipfile.ZipFile(path) as zf:
                return _cbz_cover(zf)
    except Exception:  # noqa: BLE001
        return None
    return None


# --------------------------------------------------------------------------- #
# Cover cache
# --------------------------------------------------------------------------- #

def save_cover(book_id, data, content_type="", extension=""):
    """Write cover bytes to the cache dir and return the stored filename."""
    if not data or len(data) > MAX_COVER_BYTES:
        return None
    ext = cover_extension(data[:16], content_type) or extension
    if not ext:
        return None
    covers_dir = current_app.config["COVERS_DIR"]
    filename = f"{book_id}{ext}"
    for existing in os.listdir(covers_dir):
        if existing.startswith(f"{book_id}."):
            try:
                os.remove(os.path.join(covers_dir, existing))
            except OSError:
                pass
    with open(os.path.join(covers_dir, filename), "wb") as fh:
        fh.write(data)
    return filename


def cover_from_url(url):
    """Download an image from an allow-listed metadata provider."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Cover URLs must use https")
    if not _host_allowed(parsed.hostname or ""):
        raise ValueError("Cover host is not allow-listed")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final_host = urllib.parse.urlparse(response.geturl()).hostname or ""
            if not _host_allowed(final_host):
                raise ValueError("Cover redirects to a disallowed host")
            content_type = response.headers.get("Content-Type", "")
            data = response.read(MAX_COVER_BYTES + 1)
    except urllib.error.URLError as exc:
        raise ValueError(f"Could not download cover: {exc}") from exc
    if len(data) > MAX_COVER_BYTES:
        raise ValueError("Cover image is too large")
    if not cover_extension(data[:16], content_type):
        raise ValueError("Downloaded file is not a supported image")
    return data, content_type


def _host_allowed(host):
    host = (host or "").lower()
    return host in ALLOWED_COVER_HOSTS or host.endswith(".archive.org") or host.endswith(".googleusercontent.com")


# --------------------------------------------------------------------------- #
# Online metadata providers
# --------------------------------------------------------------------------- #

def _fetch_json(url, timeout=15):
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _openlibrary_search(query, limit=8):
    params = urllib.parse.urlencode(
        {
            "q": query,
            "limit": limit,
            "fields": "key,title,author_name,first_publish_year,publisher,isbn,cover_i,"
            "subject,language,number_of_pages_median",
        }
    )
    data = _fetch_json(f"https://openlibrary.org/search.json?{params}")
    results = []
    for doc in data.get("docs", []):
        cover_id = doc.get("cover_i")
        results.append(
            {
                "source": "OpenLibrary",
                "title": doc.get("title") or "",
                "author": ", ".join(doc.get("author_name") or []),
                "published_year": str(doc.get("first_publish_year") or ""),
                "publisher": (doc.get("publisher") or [""])[0],
                "description": "",
                "tags": (doc.get("subject") or [])[:8],
                "isbn": _normalise_isbn(doc.get("isbn") or []),
                "page_count": doc.get("number_of_pages_median") or 0,
                "language": (doc.get("language") or [""])[0],
                "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else "",
                "key": doc.get("key") or "",
            }
        )
    return results


def _googlebooks_search(query, limit=8):
    params = urllib.parse.urlencode({"q": query, "maxResults": limit, "printType": "books"})
    data = _fetch_json(f"https://www.googleapis.com/books/v1/volumes?{params}")
    results = []
    for item in data.get("items", []):
        info = item.get("volumeInfo") or {}
        links = info.get("imageLinks") or {}
        cover = (links.get("thumbnail") or links.get("smallThumbnail") or "").replace("http://", "https://")
        if cover:
            cover = re.sub(r"&zoom=\d", "&zoom=1", cover)
        identifiers = info.get("industryIdentifiers") or []
        isbn = ""
        for identifier in identifiers:
            value = re.sub(r"[^0-9Xx]", "", identifier.get("identifier") or "")
            if len(value) in (10, 13):
                isbn = value.upper()
                break
        results.append(
            {
                "source": "Google Books",
                "title": info.get("title") or "",
                "author": ", ".join(info.get("authors") or []),
                "published_year": _normalise_date(info.get("publishedDate")),
                "publisher": info.get("publisher") or "",
                "description": _strip_html(info.get("description") or ""),
                "tags": (info.get("categories") or [])[:8],
                "isbn": isbn,
                "page_count": info.get("pageCount") or 0,
                "language": info.get("language") or "",
                "cover_url": cover,
                "key": item.get("id") or "",
            }
        )
    return results


def search_online(query):
    """Query both providers, tolerating offline/failed lookups."""
    query = (query or "").strip()
    if not query:
        return []
    results, errors = [], []
    for provider in (_googlebooks_search, _openlibrary_search):
        try:
            results.extend(provider(query))
        except Exception as exc:  # noqa: BLE001 - network is optional
            errors.append(f"{provider.__name__}: {exc}")
    merged, seen = [], set()
    for item in results:
        key = (item["title"].strip().lower(), item["author"].strip().lower())
        if not item["title"] or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    for item in merged:
        item["tags"] = split_tags(item.get("tags"))
    if not merged and errors:
        raise RuntimeError("; ".join(errors))
    return merged[:12]


def best_match(title, author):
    """Fetch a single best online record used to fill in missing metadata."""
    query = " ".join(part for part in (title, author) if part).strip()
    if len(query) < 3:
        return None
    try:
        results = search_online(query)
    except Exception:  # noqa: BLE001
        return None
    if not results:
        return None
    wanted = (title or "").strip().lower()
    for item in results:
        if item["title"].strip().lower() == wanted:
            return item
    return results[0]
