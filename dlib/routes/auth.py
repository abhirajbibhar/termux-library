"""Login, registration and session endpoints."""

import re

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for, current_app

from ..auth import (
    clear_login_failures,
    hash_password,
    login_failures,
    record_login_failure,
    verify_password,
)
from ..db import get_db
from ..utils import now_iso

bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


@bp.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    ip = request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"
    if login_failures(ip) >= 12:
        return jsonify({"error": "Too many attempts. Try again in a few minutes."}), 429

    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not verify_password(row["password_hash"], password):
        record_login_failure(ip)
        return jsonify({"error": "Invalid username or password"}), 401

    clear_login_failures(ip)
    session.clear()
    session["uid"] = row["id"]
    session.permanent = True
    return jsonify({"user": _public_user(row)})


@bp.post("/api/auth/register")
def register():
    db = get_db()
    user_count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    if user_count and not current_app.config["ALLOW_REGISTRATION"]:
        return jsonify({"error": "Registration is disabled. Ask the admin for an account."}), 403

    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip() or username
    if not USERNAME_RE.match(username):
        return jsonify({"error": "Username must be 3-32 characters (letters, numbers, . _ -)"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    exists = db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        return jsonify({"error": "That username is already taken"}), 409
    cursor = db.execute(
        "INSERT INTO users (username, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
        (username, hash_password(password), display_name, now_iso()),
    )
    db.commit()
    session.clear()
    session["uid"] = cursor.lastrowid
    session.permanent = True
    return jsonify({"user": {"id": cursor.lastrowid, "username": username, "display_name": display_name}}), 201


@bp.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
def me():
    if g.get("user") is None:
        return jsonify({"user": None}), 401
    return jsonify({"user": _public_user(g.user)})


@bp.post("/api/auth/password")
def change_password():
    if g.get("user") is None:
        return jsonify({"error": "Authentication required"}), 401
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if row is None or not verify_password(row["password_hash"], current):
        return jsonify({"error": "Current password is incorrect"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), g.user["id"]),
    )
    db.commit()
    return jsonify({"ok": True})


def _public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
    }
