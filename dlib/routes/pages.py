"""HTML pages: library dashboard, login and reader."""

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, url_for

from ..auth import login_required
from ..db import get_db
from ..utils import book_with_details, get_book

bp = Blueprint("pages", __name__)


@bp.get("/login")
def login():
    if g.get("user") is not None:
        return redirect(url_for("pages.library"))
    has_users = get_db().execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] > 0
    return render_template(
        "login.html",
        allow_registration=has_users is False or _allow_registration(),
        has_users=has_users,
    )


def _allow_registration():
    from flask import current_app

    return current_app.config["ALLOW_REGISTRATION"]


@bp.get("/")
@login_required
def library():
    db = get_db()
    collections = [
        dict(row)
        for row in db.execute(
            """SELECT c.id, c.name, COUNT(bc.book_id) AS book_count
               FROM collections c LEFT JOIN book_collections bc ON bc.collection_id = c.id
               GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
        )
    ]
    formats = [
        row["format"]
        for row in db.execute("SELECT DISTINCT format FROM books ORDER BY format")
    ]
    total = db.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    return render_template(
        "library.html",
        collections=collections,
        formats=formats,
        total_books=total,
        allow_registration=_allow_registration(),
    )


@bp.get("/read/<int:book_id>")
@login_required
def reader(book_id):
    row = get_book(book_id)
    if row is None:
        abort(404)
    book = book_with_details(row)
    is_pdf = row["format"] == "PDF"
    return render_template("reader.html", book=book, is_pdf=is_pdf)


@bp.get("/favicon.ico")
def favicon():
    return current_app.send_static_file("favicon.svg")


@bp.get("/healthz")
def healthz():
    return jsonify({"ok": True})
