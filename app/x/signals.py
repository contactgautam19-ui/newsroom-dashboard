"""Top tweet signals — the five posts that most warrant editorial action.

Priority follows the X Monitoring Guardrails: credibility (trust score),
news-signal strength (rule 5), editorial relevance (linkage to an active
board story), live momentum (trending terms), and recency. Follower counts
play no part (rule 12).
"""

import json
import re
from datetime import datetime, timedelta, timezone

from app import db

WINDOW_HOURS = 6
TOP_N = 5

SIGNAL_WEIGHTS = {
    "breaking announcement": 25,
    "official document": 22,
    "court order": 22,
    "public safety": 22,
    "government notification": 20,
    "exclusive reporting": 18,
    "press conference": 15,
    "eyewitness/ground report": 15,
    "update/developing": 10,
}

_word_re = re.compile(r"[^a-z0-9 ]")
_STOP = set(
    "the a an and or of in on to for with at by from as is are was be after"
    " amid over under his her their its says say said new live update updates"
    " india indian latest news today breaking case".split()
)


def _tokens(title: str) -> set[str]:
    return {w for w in _word_re.sub("", title.lower()).split()
            if len(w) > 3 and w not in _STOP}


def top_signals() -> list[dict]:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=WINDOW_HOURS)).isoformat()

    with db.connect() as con:
        tweets = db.rows_to_dicts(con.execute(
            "SELECT * FROM tweets WHERE discarded=0 AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 500", (since,)).fetchall())
        stories = [(r["id"], r["title"], _tokens(r["title"]))
                   for r in con.execute(
                       "SELECT id, title FROM stories WHERE active=1 AND picked=0")]
        trending = {r["term"] for r in con.execute(
            "SELECT DISTINCT term FROM velocity_events WHERE created_at >= ?",
            (since,))}

    scored = []
    for t in tweets:
        if len((t.get("text") or "").strip()) < 25:
            continue  # bare links carry no standalone signal
        terms = set(t.get("terms") or [])
        score = 0
        reasons = []

        trust = t.get("trust_score", 0)
        score += round(trust * 0.3)
        if trust >= 90:
            reasons.append("wire/official-grade source")
        elif trust >= 75:
            reasons.append("high-trust source")

        signal = t.get("news_signal")
        if signal:
            score += SIGNAL_WEIGHTS.get(signal, 10)
            reasons.append(signal)

        linked = None
        for sid, title, tokens in stories:
            if len(terms & tokens) >= 2:
                linked = {"story_id": sid, "story_title": title}
                score += 20
                reasons.append("matches a board story")
                break

        if terms & trending:
            score += 10
            reasons.append("term trending on X")

        age_min = (now - datetime.fromisoformat(t["created_at"])).total_seconds() / 60
        if age_min <= 30:
            score += 15
            reasons.append("posted in the last 30 min")
        elif age_min <= 120:
            score += 10
        elif age_min <= 360:
            score += 4

        scored.append({
            "score": score, "reasons": reasons, "linked_story": linked,
            "handle": t["handle"], "display_name": t["display_name"],
            "text": t["text"], "created_at": t["created_at"],
            "trust_score": trust, "stream_column": t["stream_column"],
        })

    scored.sort(key=lambda s: (-s["score"], s["created_at"]))
    return scored[:TOP_N]
