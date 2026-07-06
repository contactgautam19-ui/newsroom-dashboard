"""Storage layer with two interchangeable backends.

Local dev (default, unchanged): SQLite. One connection per call site via
connect(); WAL mode so the scheduler thread and request handlers can read/write
concurrently.

Serverless / Vercel: Postgres (Supabase) — activated when ``DATABASE_URL`` is
set. A thin wrapper (``_PGConnection`` / ``_PGCursor``) makes psycopg2 behave
like the sqlite3 connection the rest of the codebase expects:

  * ``with db.connect() as con:``   commits on clean exit, rolls back on error
  * ``con.execute(sql, params)``    returns a cursor with .fetchone/.fetchall/
                                    .rowcount/.lastrowid; ``?`` placeholders and
                                    a few SQLite-only SQL idioms are translated
  * ``con.executescript(sql)``      used only by init_db()
  * ``row["col"]``                  RealDictCursor rows behave like dicts

``IS_PG`` lets the few statements that can't be translated purely
mechanically (scalar MAX/MIN inside UPDATE ... SET) pick a portable form at the
call site.
"""

import json
import re
import sqlite3
from typing import Any, Iterable

from app import config

IS_PG = bool(config.DATABASE_URL)

if IS_PG:
    import psycopg2
    import psycopg2.extras

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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS live_coverage (
    video_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    terms TEXT NOT NULL DEFAULT '[]'           -- significant tokens for board matching
);

CREATE TABLE IF NOT EXISTS live_onair (
    slug TEXT PRIMARY KEY,                     -- hash(channel|headline|hour_key)
    channel TEXT NOT NULL,
    headline TEXT NOT NULL,
    hour_key TEXT NOT NULL,                    -- IST hour bucket 'YYYY-MM-DDTHH'
    breaking INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""


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
    # poster profile image for the Top 5 signals cards
    "ALTER TABLE tweets ADD COLUMN avatar_url TEXT",
    # live rival-TV monitor (2026-07): channel names currently airing this story
    "ALTER TABLE stories ADD COLUMN rival_coverage TEXT",
]


# --------------------------------------------------------------------------
# Postgres SQL translation
# --------------------------------------------------------------------------
# Tables whose INSERT should return the new serial id so a caller reading
# .lastrowid keeps working. These are exactly the tables with an ``id`` serial
# column; tables with text/composite primary keys (tweets, handles, settings,
# score_breakdowns, live_coverage) must NOT get a RETURNING id clause.
_ID_TABLES = ("stories", "articles", "briefings", "velocity_events")

_LIVE_COVERAGE_UPSERT = (
    "INSERT INTO live_coverage"
)
_LIVE_COVERAGE_ON_CONFLICT = (
    " ON CONFLICT (video_id) DO UPDATE SET "
    "channel=EXCLUDED.channel, title=EXCLUDED.title, "
    "published_at=EXCLUDED.published_at, fetched_at=EXCLUDED.fetched_at, "
    "terms=EXCLUDED.terms"
)

_insert_table_re = re.compile(r"INSERT\s+INTO\s+([a-z_]+)", re.IGNORECASE)


def translate(sql: str) -> str:
    """Translate a SQLite SQL string into portable Postgres SQL.

    Only mechanical rewrites live here; the scalar MAX(0,...)/MIN(...) inside
    UPDATE ... SET expressions are handled at the call sites (guarded by IS_PG)
    because a blind regex there would also rewrite aggregate MAX/MIN. This
    function is deliberately pure so it can be unit-tested without a PG server.
    """
    out = sql

    # INSERT OR REPLACE INTO live_coverage -> proper upsert (the only OR REPLACE)
    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+live_coverage", out, re.IGNORECASE):
        out = re.sub(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+live_coverage",
            "INSERT INTO live_coverage",
            out,
            flags=re.IGNORECASE,
        )
        out = out.rstrip().rstrip(";") + _LIVE_COVERAGE_ON_CONFLICT

    # INSERT OR IGNORE INTO x -> INSERT INTO x ... ON CONFLICT DO NOTHING
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", out, re.IGNORECASE):
        out = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", out,
                     flags=re.IGNORECASE)
        out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # ? placeholders -> %s (SQL strings in this codebase contain no literal
    # question marks besides placeholders — verified by grep)
    out = out.replace("?", "%s")

    # RETURNING id for id-table inserts so .lastrowid works. Skip statements
    # that already have RETURNING or ON CONFLICT DO NOTHING (the latter may
    # insert no row, so RETURNING would yield nothing to fetch).
    m = _insert_table_re.match(out.lstrip())
    if (m and m.group(1).lower() in _ID_TABLES
            and "RETURNING" not in out.upper()
            and "ON CONFLICT DO NOTHING" not in out.upper()):
        out = out.rstrip().rstrip(";") + " RETURNING id"

    return out


def _translate_script(sql: str) -> list[str]:
    """Translate a CREATE-heavy schema script for Postgres and split it into
    individual statements (psycopg2 has no executescript)."""
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    return [s.strip() for s in sql.split(";") if s.strip()]


# --------------------------------------------------------------------------
# Postgres connection / cursor wrappers (sqlite3-compatible surface)
# --------------------------------------------------------------------------
class _PGCursor:
    """Wraps a psycopg2 RealDictCursor; exposes .fetchone/.fetchall/.rowcount/
    .lastrowid and is iterable like sqlite3 cursors."""

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PGConnection:
    """sqlite3.Connection-compatible wrapper over a psycopg2 connection.

    Context-manager semantics mirror sqlite3: commit on clean exit, rollback on
    exception, and always close the underlying connection (serverless-friendly:
    one connection per connect() call, Supabase's pooler recycles it)."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: Iterable = ()):  # noqa: A003
        translated = translate(sql)
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(translated, tuple(params))
        wrapped = _PGCursor(cur)
        if translated.rstrip().upper().endswith("RETURNING ID"):
            row = cur.fetchone()
            if row is not None:
                wrapped.lastrowid = row["id"]
        return wrapped

    def executescript(self, sql: str):
        cur = self._raw.cursor()
        for stmt in _translate_script(sql):
            cur.execute(stmt)
        cur.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


def connect():
    """Open a fresh connection. SQLite locally; Postgres when DATABASE_URL set."""
    if IS_PG:
        raw = psycopg2.connect(config.DATABASE_URL)
        return _PGConnection(raw)
    con = sqlite3.connect(config.DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def init_db() -> None:
    if IS_PG:
        # executescript for the schema, then each migration in its own
        # autocommit transaction so a duplicate-column error only rolls back
        # that one ALTER (mirrors the SQLite try/except-per-migration pattern).
        with connect() as con:
            con.executescript(SCHEMA)
        raw = psycopg2.connect(config.DATABASE_URL)
        raw.autocommit = True
        try:
            for stmt in MIGRATIONS:
                cur = raw.cursor()
                try:
                    cur.execute(stmt)
                except psycopg2.Error:
                    pass  # column already exists (autocommit isolates the error)
                finally:
                    cur.close()
        finally:
            raw.close()
        return

    with connect() as con:
        con.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                con.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def row_to_dict(row) -> dict[str, Any]:
    d = dict(row)  # sqlite3.Row and psycopg2 RealDictRow both convert cleanly
    for key in ("flags", "media", "sources", "terms", "evidence", "rival_coverage"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d


def rows_to_dicts(rows: Iterable) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]
