"""Vercel serverless entrypoint for runtimes that use api/; current runtimes
auto-detect app/main.py and serve it directly. Path restoration lives in
app/asgi_path.py either way."""

from app.main import app  # noqa: F401
