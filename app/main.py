"""Newsroom Intelligence Dashboard — FastAPI app.

Endpoints:
  GET  /                     dashboard UI
  GET  /api/stream           SSE: rundown | tweet | velocity_event | system_status
  GET  /api/rundown          current ranked stories with score breakdowns
  GET  /api/tweets           recent kept tweets per column
  GET  /api/velocity         recent velocity events
  GET  /api/briefings/latest most recent hourly brief (HTML)
  POST /api/ingest           manual ingest cycle (debug)
  POST /api/brief            generate + send brief now (debug)
  POST /api/sim/spike        trigger simulated viral spike on the top story (demo)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from app import broker, config, db, events, scheduler
from app.news import ingest
from app.x.pipeline import pipeline, seed_handles_table

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("newsroom")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    seeded = seed_handles_table()
    log.info("seeded %d handles", seeded)
    pipeline.load_handles()
    events.bind_loop(asyncio.get_running_loop())
    if config.IS_SERVERLESS:
        # No background threads on Vercel: the scheduler and warm executors are
        # skipped. Refresh loops are driven by the /api/cron/* endpoints (and
        # the client auto-refresh) instead. init_db + seeding above are cheap
        # and idempotent (IF NOT EXISTS + upserts), so they stay.
        log.info("serverless mode: scheduler + warm cycles disabled")
        yield
        return
    scheduler.start()
    # first boot with an empty board: pull a cycle immediately
    with db.connect() as con:
        empty = con.execute("SELECT COUNT(*) c FROM stories").fetchone()["c"] == 0
    if empty:
        asyncio.get_running_loop().run_in_executor(None, ingest.run_ingest_cycle)
    # warm the live rival-TV monitor so chips/keywords are ready at startup
    from app.news import live_monitor
    asyncio.get_running_loop().run_in_executor(None, live_monitor.run_live_cycle)
    yield
    scheduler.shutdown()


app = FastAPI(title="Newsroom Intelligence Dashboard", lifespan=lifespan)


# --------------------------------------------------------------------------
# Shared passcode gate (active only when PASSCODE is set — local dev unaffected)
# --------------------------------------------------------------------------
def _passcode_hash() -> str:
    return hashlib.sha256(config.PASSCODE.encode()).hexdigest()


# paths reachable without the cookie: the login flow, static assets, and the
# cron endpoints (which carry their own secret)
_AUTH_EXEMPT_PREFIXES = ("/login", "/api/login", "/static/", "/api/cron/")


@app.middleware("http")
async def passcode_gate(request: Request, call_next):
    if not config.PASSCODE:
        return await call_next(request)  # no-op when unset (local dev)
    path = request.url.path
    if any(path == p or path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if request.cookies.get("nk_auth") == _passcode_hash():
        return await call_next(request)
    # unauthenticated: JSON 401 for API calls, redirect to /login for browsers
    if path.startswith("/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return RedirectResponse("/login", status_code=302)


_LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Newsroom</title></head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0B1526;font-family:-apple-system,Segoe UI,Arial,sans-serif;">
<div style="background:#fff;border-radius:12px;padding:36px 40px;width:320px;box-shadow:0 12px 40px rgba(0,0,0,.4);text-align:center;">
  <div style="display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:20px;">
    <span style="width:12px;height:12px;border-radius:50%;background:#E02424;display:inline-block;"></span>
    <span style="font-size:20px;font-weight:700;color:#0B1526;">Newsroom</span>
  </div>
  <input id="pc" type="password" placeholder="Passcode" autofocus
    style="width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid #cfd8e3;border-radius:8px;font-size:15px;margin-bottom:12px;">
  <button id="go" style="width:100%;padding:11px;border:0;border-radius:8px;background:#E02424;color:#fff;font-size:15px;font-weight:600;cursor:pointer;">Enter newsroom</button>
  <div id="err" style="color:#E02424;font-size:13px;margin-top:12px;height:16px;"></div>
</div>
<script>
const inp=document.getElementById('pc'),err=document.getElementById('err');
async function submit(){
  err.textContent='';
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({passcode:inp.value})});
  const d=await r.json();
  if(d.ok){location.href='/';}else{err.textContent='Wrong passcode';inp.value='';inp.focus();}
}
document.getElementById('go').onclick=submit;
inp.addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
</script>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _LOGIN_HTML


@app.post("/api/login")
def do_login(payload: dict = Body(...)):
    if config.PASSCODE and payload.get("passcode") == config.PASSCODE:
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            "nk_auth", _passcode_hash(), max_age=60 * 60 * 24 * 30,
            httponly=True, samesite="lax", secure=config.IS_SERVERLESS,
        )
        return resp
    return JSONResponse({"ok": False}, status_code=401)


@app.get("/api/stream")
async def stream():
    if config.IS_SERVERLESS:
        # No long-lived connections on Vercel: the client falls back to polling.
        return JSONResponse({"disabled": True}, status_code=404)
    q = events.subscribe()

    async def gen():
        # snapshot on connect so a fresh client paints immediately
        yield events.sse_format({"type": "rundown", "data": ingest.get_rundown()})
        yield events.sse_format({"type": "system_status",
                                 "data": {"x_layer": pipeline.active_layer}})
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield events.sse_format(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/rundown")
def rundown():
    return ingest.get_rundown()


@app.get("/api/tweets")
def tweets(limit: int = 40):
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM tweets WHERE discarded=0 ORDER BY created_at DESC LIMIT ?",
            (limit * 3,),
        ).fetchall()
    return db.rows_to_dicts(rows)


@app.get("/api/sources")
def news_sources():
    from app.news.sources import load_sources
    return load_sources(active_only=False)


@app.get("/api/velocity")
def velocity(limit: int = 20):
    with db.connect() as con:
        rows = con.execute(
            """SELECT v.*, s.title AS story_title FROM velocity_events v
               LEFT JOIN stories s ON s.id = v.story_id
               ORDER BY v.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return db.rows_to_dicts(rows)


@app.get("/api/briefings/latest", response_class=HTMLResponse)
def latest_briefing():
    with db.connect() as con:
        row = con.execute(
            "SELECT html FROM briefings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise HTTPException(404, "No briefings generated yet — POST /api/brief")
    return row["html"]


@app.post("/api/ingest")
async def manual_ingest():
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: ingest.run_ingest_cycle(manual=True)
    )


@app.post("/api/brief")
async def manual_brief():
    from app import briefing
    return await asyncio.get_running_loop().run_in_executor(
        None, briefing.generate_and_send
    )


def _require_cron(secret: str, request: "Request") -> None:
    """Cron endpoints are GET (so external pingers like cron-job.org and Vercel
    Cron work) and guarded by the shared CRON_SECRET. Vercel Cron sends it as
    `Authorization: Bearer <CRON_SECRET>`; external pingers use `?secret=`.
    Either is accepted. When CRON_SECRET is unset the endpoints refuse to run."""
    if not config.CRON_SECRET:
        raise HTTPException(403, "forbidden")
    auth = request.headers.get("authorization", "")
    header_secret = auth[7:] if auth.lower().startswith("bearer ") else ""
    if secret != config.CRON_SECRET and header_secret != config.CRON_SECRET:
        raise HTTPException(403, "forbidden")


@app.get("/api/cron/tick")
def cron_tick(request: Request, secret: str = ""):
    _require_cron(secret, request)
    return ingest.run_ingest_cycle()


@app.get("/api/cron/live")
def cron_live(request: Request, secret: str = ""):
    _require_cron(secret, request)
    from app.news import live_monitor
    return live_monitor.run_live_cycle()


@app.get("/api/cron/brief")
def cron_brief(request: Request, secret: str = ""):
    _require_cron(secret, request)
    from app import briefing
    return briefing.generate_and_send()


@app.post("/api/x/refresh")
async def x_refresh():
    """Manual X-desk refresh (~3 API calls) followed by a free story re-rank,
    so fresh tweets flow straight into keywords and rankings."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, pipeline.manual_refresh)
    if result.get("ok"):
        loop.run_in_executor(None, lambda: ingest.run_ingest_cycle(manual=True))
        result["stories_refreshing"] = True
    return result


@app.post("/api/stories/{story_id}/pick")
async def pick_story(story_id: int):
    from datetime import datetime, timezone
    with db.connect() as con:
        row = con.execute("SELECT picked FROM stories WHERE id=?", (story_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Story not found")
        picked = 0 if row["picked"] else 1
        con.execute("UPDATE stories SET picked=?, picked_at=? WHERE id=?",
                    (picked, datetime.now(timezone.utc).isoformat() if picked else None,
                     story_id))
    ingest.publish_rundown()
    if picked:
        # a picked story is handled — refresh in the background so the board
        # backfills with the next candidates
        asyncio.get_running_loop().run_in_executor(
            None, lambda: ingest.run_ingest_cycle(manual=True))
    return {"id": story_id, "picked": bool(picked), "refreshing": bool(picked)}


@app.get("/api/stories/{story_id}/pack")
def story_pack(story_id: int):
    from app.news.pack import build_pack
    pack = build_pack(story_id)
    if pack is None:
        raise HTTPException(404, "Story not found")
    return pack


@app.post("/api/stories/{story_id}/write")
async def write_article(story_id: int, format: str = "web"):
    if format not in ("web", "broadcast", "social"):
        raise HTTPException(400, "format must be one of web, broadcast, social")
    from app.news.writer import generate_article
    try:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: generate_article(story_id, format)
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/stories/{story_id}/articles")
def story_articles(story_id: int):
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, story_id, format, content, model, created_at, "
            "input_tokens, output_tokens FROM articles "
            "WHERE story_id=? AND error IS NULL ORDER BY id DESC",
            (story_id,),
        ).fetchall()
    return db.rows_to_dicts(rows)


@app.get("/api/settings")
def get_settings():
    from app import settings_store
    pub = settings_store.get_public_settings()
    pub["key_configured"] = bool(settings_store.get_setting("anthropic_api_key", ""))
    return pub


@app.post("/api/settings")
def save_settings(payload: dict = Body(...)):
    from app import settings_store
    for key in ("channel_name", "voice_description", "sample_articles",
                "writer_model", "brief_recipients"):
        if key in payload and payload[key] is not None:
            settings_store.set_setting(key, str(payload[key]))
    api_key = payload.get("anthropic_api_key")
    if api_key:  # empty string means "unchanged"
        settings_store.set_setting("anthropic_api_key", str(api_key))
    return settings_store.get_public_settings()


@app.get("/api/ops")
def ops_summary():
    from datetime import datetime, timezone
    from app.news.sources import load_sources
    from app.news import live_monitor
    # portable "today" comparison: pass the ISO date from Python so SQLite and
    # Postgres share one query (created_at is stored as an ISO timestamp string)
    today_iso = datetime.now(timezone.utc).date().isoformat()
    with db.connect() as con:
        briefs_today = con.execute(
            "SELECT COUNT(*) c FROM briefings WHERE created_at >= ?", (today_iso,)
        ).fetchone()["c"]
        discarded = db.rows_to_dicts(con.execute(
            "SELECT handle, text, discard_reason, created_at FROM tweets "
            "WHERE discarded=1 ORDER BY created_at DESC LIMIT 8").fetchall())
        handles_count = con.execute("SELECT COUNT(*) c FROM handles").fetchone()["c"]
    return {
        "last_ingest": ingest.LAST_STATS,
        "news_refresh_minutes": scheduler.get_news_interval(),
        "email_enabled": config.EMAIL_ENABLED,
        "briefs_today": briefs_today,
        "handles_count": handles_count,
        "sources_total": len(load_sources(active_only=False)),
        "x_budget": pipeline.twtapi.last_status,
        "discarded_recent": discarded,
        "live_monitor": live_monitor.LAST_POLL,
    }


@app.post("/api/settings/news-refresh")
def set_news_refresh(minutes: int):
    scheduler.set_news_interval(minutes)
    return {"news_refresh_minutes": scheduler.get_news_interval()}


@app.get("/api/x/top-signals")
def x_top_signals():
    from app.x.signals import top_signals
    return top_signals()


@app.get("/api/x/status")
def x_status():
    return {"provider": config.X_PROVIDER, "layer": pipeline.active_layer,
            "manual_only": pipeline.manual_only,
            "key_configured": bool(config.TWT_API_KEY),
            **pipeline.twtapi.last_status}


@app.post("/api/sim/spike")
async def sim_spike():
    if pipeline.manual_only:
        raise HTTPException(409, "Spike demo only available with X_PROVIDER=sim")
    with db.connect() as con:
        row = con.execute(
            "SELECT id, title FROM stories WHERE active=1 ORDER BY score DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise HTTPException(409, "No active stories to spike — POST /api/ingest first")
    info = pipeline.sim.trigger_spike(dict(row))
    return {"spiking": row["title"], **info,
            "note": "watch the ticker + story row over the next 1-2 broker ticks"}


@app.get("/", response_class=HTMLResponse)
def index():
    html = (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Cache-bust the JS. On Vercel, deployed file mtimes are reset to a fixed
    # epoch, so mtime-based stamps never change between deploys and browsers
    # keep serving stale JS. Prefer the per-deploy git commit SHA there.
    stamp = os.getenv("VERCEL_GIT_COMMIT_SHA") or os.getenv("VERCEL_DEPLOYMENT_ID")
    if not stamp:
        js_dir = config.STATIC_DIR / "js"
        mtimes = [(config.STATIC_DIR / "index.html").stat().st_mtime]
        mtimes += [p.stat().st_mtime for p in js_dir.glob("*.js")]
        stamp = int(max(mtimes)) if mtimes else 0
    stamp = str(stamp)[:12]

    def _bust(match: "re.Match") -> str:
        name = match.group(1)
        return f'src="/static/js/{name}.js?v={stamp}"'

    html = re.sub(r'src="/static/js/([^".?]+)\.js"', _bust, html)
    # HTML must always revalidate so the freshest version stamp reaches the
    # browser; the versioned JS URLs below can then be cached safely.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
