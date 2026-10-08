"""Vercel serverless entrypoint. The Python runtime serves this ASGI app.

vercel.json rewrites every request to /api/index. Newer Vercel Python runtimes
hand the function the REWRITTEN path ("/api/index") rather than the one the
visitor asked for, which sends every request into the "/api/" branch of the
passcode gate (401 for /, /login, /static/...). The rewrite therefore carries
the original path in a __path query parameter, and this wrapper puts it back
before FastAPI routes the request. When the runtime already passes the real
path, the wrapper is a no-op.
"""
from urllib.parse import parse_qsl, urlencode

from app.main import app as _app


class _RestoreOriginalPath:
    def __init__(self, inner):
        self.inner = inner

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
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
                path = headers.get("x-vercel-original-pathname") or headers.get("x-original-path") or "/"
            if not path.startswith("/"):
                path = "/" + path
            scope = dict(scope)
            scope["path"] = path
            scope["raw_path"] = path.encode("utf-8")
            scope["query_string"] = urlencode(rest).encode("latin-1")
        await self.inner(scope, receive, send)


app = _RestoreOriginalPath(_app)
