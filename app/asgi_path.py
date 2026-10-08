"""Restore the original request path behind Vercel's catch-all rewrite.

vercel.json rewrites every request to /api/index?__path=<original path>.
Vercel's current Python runtime auto-detects the FastAPI app in app/main.py and
serves it directly (api/index.py is not used), and it hands the app the
REWRITTEN path "/api/index" — which sent every request, including /login and
/static/*, into the "/api/" branch of the passcode gate (verified 2026-10-08).

This wrapper puts the original path back before routing. When the path is
already real (local uvicorn, or a runtime that passes it through), it is a
no-op. Attribute access is proxied so `app.state`, `app.mount(...)` etc. keep
working on the wrapped object.
"""
from urllib.parse import parse_qsl, urlencode


class RestoreOriginalPath:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket") and scope.get("path", "").rstrip("/") == "/api/index":
            pairs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
            path, rest = None, []
            for key, value in pairs:
                if key == "__path" and path is None:
                    path = value
                else:
                    rest.append((key, value))
            if path is None:
                path = "/"
            if not path.startswith("/"):
                path = "/" + path
            scope = dict(scope)
            scope["path"] = path
            scope["raw_path"] = path.encode("utf-8")
            scope["query_string"] = urlencode(rest).encode("latin-1")
        await self._inner(scope, receive, send)
