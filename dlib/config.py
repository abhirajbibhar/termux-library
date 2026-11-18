"""Configuration loading for the digital library.

Values come from ``config.json`` (next to ``app.py``) and can be overridden
with environment variables. Relative paths are resolved against the project
root so the app can be started from any directory.
"""

import json
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 2104,
    "library_dir": "library",
    "covers_dir": "covers",
    "data_dir": "data",
    "database": "",
    "secret_key": "",
    "allow_registration": True,
    "session_days": 30,
    "secure_cookies": False,
    "max_upload_mb": 2048,
    "cors_origins": [],
    # Automatically look up title/author/cover online when an upload has no
    # usable metadata. Set to false on a slow/metered connection.
    "auto_metadata": True,
}


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env(name, key, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return None


def _resolve(value):
    value = os.path.expanduser(str(value))
    if not os.path.isabs(value):
        value = os.path.join(BASE_DIR, value)
    return os.path.normpath(value)


def load_config():
    path = os.environ.get("LIBRARY_CONFIG", os.path.join(BASE_DIR, "config.json"))
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Cannot read {path}: {exc}")

    overrides = {
        "host": ("HOST", str),
        "port": ("PORT", int),
        "library_dir": ("LIBRARY_DIR", str),
        "covers_dir": ("COVERS_DIR", str),
        "data_dir": ("DATA_DIR", str),
        "database": ("LIBRARY_DATABASE", str),
        "secret_key": ("SECRET_KEY", str),
        "allow_registration": ("ALLOW_REGISTRATION", _as_bool),
        "secure_cookies": ("SECURE_COOKIES", _as_bool),
        "max_upload_mb": ("MAX_UPLOAD_MB", int),
        "auto_metadata": ("AUTO_METADATA", _as_bool),
    }
    for key, (env_name, cast) in overrides.items():
        value = _env(env_name, key, cast)
        if value is not None:
            cfg[key] = value

    for key in ("library_dir", "covers_dir", "data_dir"):
        cfg[key] = _resolve(cfg[key])

    cfg["database"] = (
        _resolve(cfg["database"]) if cfg["database"] else os.path.join(cfg["data_dir"], "library.db")
    )
    cfg["tmp_dir"] = os.path.join(cfg["data_dir"], "tmp")
    cfg["secret_key_file"] = os.path.join(cfg["data_dir"], "secret_key")

    for directory in (cfg["library_dir"], cfg["covers_dir"], cfg["data_dir"], cfg["tmp_dir"]):
        os.makedirs(directory, exist_ok=True)

    if not cfg.get("secret_key"):
        cfg["secret_key"] = _load_or_create_secret(cfg["secret_key_file"])

    cfg["max_upload_bytes"] = int(cfg["max_upload_mb"]) * 1024 * 1024
    if not isinstance(cfg["cors_origins"], list):
        cfg["cors_origins"] = [cfg["cors_origins"]]
    cfg["cors_origins"] = [str(origin).rstrip("/") for origin in cfg["cors_origins"] if origin]
    return cfg


def _load_or_create_secret(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key
