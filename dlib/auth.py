"""Session/basic authentication helpers."""

import time
from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

_FAILURES = {}
_FAILURE_WINDOW = 600
_MAX_FAILURES = 12


def hash_password(password):
    return generate_password_hash(password, method="pbkdf2:sha256:260000")


def verify_password(password_hash, password):
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def _lookup_uid(uid):
    return get_db().execute(
        "SELECT id, username, display_name, created_at FROM users WHERE id = ?", (uid,)
    ).fetchone()


def _lookup_basic():
    auth = request.authorization
    if not auth or auth.type != "basic" or not auth.username:
        return None
    row = get_db().execute("SELECT * FROM users WHERE username = ?", (auth.username,)).fetchone()
    if row and verify_password(row["password_hash"], auth.password or ""):
        return row
    return None


def load_user():
    if request.endpoint in {"static", "covers"}:
        return
    g.user = None
    uid = session.get("uid")
    if uid is not None:
        row = _lookup_uid(uid)
        if row is not None:
            g.user = row
        else:
            session.clear()
    if g.user is None:
        row = _lookup_basic()
        if row is not None:
            g.user = {
                "id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "created_at": row["created_at"],
            }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if request.path.startswith("/api/") or request.path.startswith("/opds"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("pages.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def login_failures(ip):
    now = time.time()
    recent = [stamp for stamp in _FAILURES.get(ip, []) if now - stamp < _FAILURE_WINDOW]
    _FAILURES[ip] = recent
    return len(recent)


def record_login_failure(ip):
    _FAILURES.setdefault(ip, []).append(time.time())


def clear_login_failures(ip):
    _FAILURES.pop(ip, None)
