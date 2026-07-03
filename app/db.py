"""SQLite storage. One connection per call site via connect(); WAL mode so the
scheduler thread and request handlers can read/write concurrently."""

import json
import sqlite3
from typing import Any, Iterable

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    publisher TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    location TEXT,
    category TEXT,
    flags TEXT NOT NULL DEFAULT '{}',          -- thematic booleans (political, celebrity, ...)
    media TEXT NOT NULL DEFAULT '{}',          -- rich-media indicators
    sources TEXT NOT NULL DEFAULT '[]',        -- corroborating publishers
    base_score INTEGER NOT NULL DEFAULT 0,
    trend_boost INTEGER NOT NULL DEFAULT 0,
    decay INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    confidence INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'developing', -- breaking | developing | verified
    needs_review INTEGER NOT NULL DEFAULT 0,
    high_demand INTEGER NOT NULL DEFAULT 0,
    stale_cycles INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS score_breakdowns (
    story_id INTEGER NOT NULL REFERENCES stories(id),
    variable TEXT NOT NULL,
    max_points INTEGER NOT NULL,
    points INTEGER NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (story_id, variable)
);

CREATE TABLE IF NOT EXISTS handles (
    handle TEXT PRIMARY KEY,
    name TEXT,
    category TEXT,
    sub_category TEXT,
    organization TEXT,
    region TEXT,
    stream_column TEXT NOT NULL,               -- A | B | C
    trust_score INTEGER NOT NULL DEFAULT 60,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS tweets (
    id TEXT PRIMARY KEY,                       -- provider tweet id (hash for sim)
    handle TEXT NOT NULL,
    display_name TEXT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    stream_column TEXT NOT NULL,
    trust_score INTEGER NOT NULL DEFAULT 60,
    news_signal TEXT,                          -- which guardrail signal admitted it, if any
    discarded INTEGER NOT NULL DEFAULT 0,      -- filtered by guardrails (kept for audit)
    discard_reason TEXT,
    terms TEXT NOT NULL DEFAULT '[]'           -- extracted hashtags/entities
);

CREATE TABLE IF NOT EXISTS velocity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    term TEXT NOT NULL,
    velocity_pct REAL NOT NULL,
    posts_per_hour REAL NOT NULL,
    story_id INTEGER REFERENCES stories(id),
    boost INTEGER NOT NULL DEFAULT 0,
    high_demand INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    subject TEXT NOT NULL,
    html TEXT NOT NULL,
    emailed INTEGER NOT NULL DEFAULT 0,
    email_error TEXT
);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con


MIGRATIONS = [
    # keyword-driven discovery (2026-07): where a story was found + how many
    # Trend Momentum points came from discovery vs the broker's live boost
    "ALTER TABLE stories ADD COLUMN discovered_via TEXT",
    "ALTER TABLE stories ADD COLUMN trend_base INTEGER NOT NULL DEFAULT 0",
    # real-tweet provenance (2026-07): rows without it predate the TwtAPI
    # integration and are simulated content
    "ALTER TABLE tweets ADD COLUMN provider TEXT NOT NULL DEFAULT 'simulated'",
    # editor workflow (2026-07): picked stories + hourly (not per-cycle) decay
    "ALTER TABLE stories ADD COLUMN picked INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE stories ADD COLUMN picked_at TEXT",
    "ALTER TABLE stories ADD COLUMN last_aged_at TEXT",
]


def init_db() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                con.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("flags", "media", "sources", "terms", "evidence"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]
