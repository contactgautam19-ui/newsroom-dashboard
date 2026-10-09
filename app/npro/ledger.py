"""The notebook. Every script N-Pro writes is filed with what it was written
from: the reporter's note as it arrived, its translation, the who/where/when
it was pinned to, each report that was admitted as context (with its date, its
link and the text as it read at the time), and what the checks found.

Web pages change and searches drift. The record does not, so a line can be
traced back to its evidence long after the script went to air.
"""

import json
import secrets
from datetime import datetime, timezone

from app import db

_ready = False


def _ensure(con) -> None:
    global _ready
    if not _ready:
        con.execute("CREATE TABLE IF NOT EXISTS npro_ledger ("
                    "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                    "topic TEXT NOT NULL, format TEXT, record TEXT NOT NULL)")
        _ready = True


def file_record(topic: str, format_id: str | None, script: str, notes: list,
                verdict: dict | None, corpus, params: dict | None = None,
                kind: str = "script") -> str | None:
    """Store one script with its evidence. Returns the record id, or None if
    the notebook could not be written (a script is never lost over that)."""
    rid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    record = {
        "id": rid, "kind": kind, "topic": topic, "format": format_id,
        "script": script, "notes": notes, "checks": verdict,
        "params": params or {},
        "note": corpus.note, "triad": corpus.triad,
        "sources": [{"sid": s["sid"], "publisher": s["publisher"], "title": s["title"],
                     "url": s["url"], "published_at": s["published_at"],
                     "role": s.get("role", ""), "full": s["full"], "text": s["text"]}
                    for s in corpus.sources],
        "left_out": corpus.rejected,
    }
    try:
        with db.connect() as con:
            _ensure(con)
            con.execute("INSERT INTO npro_ledger (id, created_at, topic, format, record) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (rid, datetime.now(timezone.utc).isoformat(), (topic or "")[:300],
                         format_id or kind, json.dumps(record, ensure_ascii=False)))
        return rid
    except Exception:
        return None


def recent(limit: int = 30) -> list[dict]:
    try:
        with db.connect() as con:
            _ensure(con)
            rows = con.execute("SELECT id, created_at, topic, format FROM npro_ledger "
                               "ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
        return db.rows_to_dicts(rows)
    except Exception:
        return []


def get(record_id: str) -> dict | None:
    try:
        with db.connect() as con:
            _ensure(con)
            row = con.execute("SELECT created_at, record FROM npro_ledger WHERE id=?",
                              (record_id,)).fetchone()
        if not row:
            return None
        rec = json.loads(row["record"])
        rec["created_at"] = row["created_at"]
        return rec
    except Exception:
        return None
