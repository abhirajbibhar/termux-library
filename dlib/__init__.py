"""Application factory for the self-hosted digital library."""

import os
from datetime import timedelta
from urllib.parse import urlparse

from flask import Flask, current_app, jsonify, render_template, request

from . import auth as auth_helpers
from .config import load_config
from .db import close_db, init_db

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob: https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "connect-src 'self' https://cdn.jsdelivr.net https://www.googleapis.com "
    "https://openlibrary.org https://covers.openlibrary.org; "
    "frame-src 'self' blob: data:; "
    "worker-src 'self' blob:; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def create_app(overrides=None):
    cfg = load_config()
    if overrides:
        cfg.update(overrides)
    # Overrides (tests, custom deployments) may point at directories that do not
    # exist yet, so make sure the storage layout is present.
    if not cfg.get("tmp_dir"):
        cfg["tmp_dir"] = os.path.join(cfg["data_dir"], "tmp")
    for directory in (cfg["library_dir"], cfg["covers_dir"], cfg["data_dir"], cfg["tmp_dir"]):
        os.makedirs(directory, exist_ok=True)
    if not cfg.get("database"):
        cfg["database"] = os.path.join(cfg["data_dir"], "library.db")

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.update(
        SECRET_KEY=cfg["secret_key"],
        DATABASE=cfg["database"],
        LIBRARY_DIR=cfg["library_dir"],
        COVERS_DIR=cfg["covers_dir"],
        DATA_DIR=cfg["data_dir"],
        TMP_DIR=cfg["tmp_dir"],
        APP_CONFIG=cfg,
        ALLOW_REGISTRATION=bool(cfg["allow_registration"]),
        MAX_CONTENT_LENGTH=cfg["max_upload_bytes"],
        SESSION_COOKIE_NAME="library_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(cfg["secure_cookies"]),
        PERMANENT_SESSION_LIFETIME=timedelta(days=int(cfg["session_days"])),
        SEND_FILE_MAX_AGE_DEFAULT=0,
        JSON_SORT_KEYS=False,
    )

    init_db(app)
    app.teardown_appcontext(close_db)
    app.before_request(auth_helpers.load_user)
    app.before_request(_same_origin_guard)

    from .routes.auth import bp as auth_bp
    from .routes.books import bp as books_bp
    from .routes.collections import bp as collections_bp
    from .routes.meta import bp as meta_bp
    from .routes.opds import bp as opds_bp
    from .routes.pages import bp as pages_bp
    from .routes.sync import bp as sync_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(books_bp)
    app.register_blueprint(collections_bp)
    app.register_blueprint(sync_bp)
    app.register_blueprint(meta_bp)
    app.register_blueprint(opds_bp)

    app.after_request(_security_headers)
    app.after_request(_cors)
    _register_error_handlers(app)
    return app


def _same_origin_guard():
    """Reject cross-site state-changing requests (defence in depth for cookies)."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    origin = request.headers.get("Origin")
    if not origin:
        return None
    parsed = urlparse(origin)
    if parsed.netloc == request.host:
        return None
    allowed = current_app.config["APP_CONFIG"].get("cors_origins", [])
    if origin.rstrip("/") in allowed:
        return None
    return jsonify({"error": "Cross-origin request blocked"}), 403


def _security_headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _cors(response):
    origin = request.headers.get("Origin")
    allowed = current_app.config["APP_CONFIG"].get("cors_origins", [])
    if origin and origin.rstrip("/") in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Range"
        response.headers["Access-Control-Expose-Headers"] = "Content-Range, Accept-Ranges, Content-Length"
    return response


def _wants_json():
    return (
        request.path.startswith("/api/")
        or request.path.startswith("/opds")
        or request.accept_mimetypes.best == "application/json"
    )


def _register_error_handlers(app):
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        if _wants_json():
            return jsonify({"error": exc.description or exc.name}), exc.code
        return render_template("error.html", code=exc.code, message=exc.description or exc.name), exc.code

    @app.errorhandler(Exception)
    def handle_unexpected(exc):  # noqa: BLE001 - log and show a friendly page
        app.logger.exception("Unhandled error: %s", exc)
        if _wants_json():
            return jsonify({"error": "Internal server error"}), 500
        return render_template("error.html", code=500, message="Something went wrong."), 500

    @app.errorhandler(413)
    def handle_too_large(exc):  # pragma: no cover - depends on request body size
        message = "Upload exceeds the configured size limit."
        if _wants_json():
            return jsonify({"error": message}), 413
        return render_template("error.html", code=413, message=message), 413
