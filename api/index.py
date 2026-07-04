"""Vercel serverless entrypoint. The Python runtime serves this ASGI app."""

from app.main import app  # noqa: F401
