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
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
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
    scheduler.start()
    # first boot with an empty board: pull a cycle immediately
    with db.connect() as con:
        empty = con.execute("SELECT COUNT(*) c FROM stories").fetchone()["c"] == 0
    if empty:
        asyncio.get_running_loop().run_in_executor(None, ingest.run_ingest_cycle)
    yield
    scheduler.shutdown()


app = FastAPI(title="Newsroom Intelligence Dashboard", lifespan=lifespan)


@app.get("/api/stream")
async def stream():
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
                "writer_model"):
        if key in payload and payload[key] is not None:
            settings_store.set_setting(key, str(payload[key]))
    api_key = payload.get("anthropic_api_key")
    if api_key:  # empty string means "unchanged"
        settings_store.set_setting("anthropic_api_key", str(api_key))
    return settings_store.get_public_settings()


@app.get("/api/ops")
def ops_summary():
    from app.news.sources import load_sources
    with db.connect() as con:
        briefs_today = con.execute(
            "SELECT COUNT(*) c FROM briefings WHERE created_at >= date('now')"
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
    return (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
