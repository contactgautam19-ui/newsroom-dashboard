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

from fastapi import FastAPI, HTTPException
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


@app.post("/api/sim/spike")
async def sim_spike():
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
