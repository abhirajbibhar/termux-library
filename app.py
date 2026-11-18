#!/usr/bin/env python3
"""Entry point: python app.py (Termux-friendly, no Docker, no Node).

Serves the app with Waitress (pure Python) unless FLASK_DEBUG=1 is set, in
which case the Flask reloader is used for development.
"""

import os

from dlib import create_app

app = create_app()


def main():
    config = app.config["APP_CONFIG"]
    host = os.environ.get("HOST", config["host"])
    port = int(os.environ.get("PORT", config["port"]))
    debug = os.environ.get("FLASK_DEBUG", "").strip() in {"1", "true", "yes"}

    if debug:
        app.run(host=host, port=port, debug=True, threaded=True)
        return

    try:
        from waitress import serve
    except ImportError:
        print("Waitress is not installed; falling back to the development server.")
        app.run(host=host, port=port, threaded=True)
        return

    print(f"Digital Library running on http://{host}:{port}")
    print(f"Library folder: {config['library_dir']}")
    serve(
        app,
        host=host,
        port=port,
        threads=8,
        channel_timeout=900,
        max_request_body_size=config["max_upload_bytes"] + 16 * 1024 * 1024,
        inbuf_overflow=16 * 1024 * 1024,
        ident="DigitalLibrary",
    )


if __name__ == "__main__":
    main()
