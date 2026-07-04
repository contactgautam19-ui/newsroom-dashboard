"""Top tweet signals — the five posts that most warrant editorial action.

Priority follows the X Monitoring Guardrails: credibility (trust score),
news-signal strength (rule 5), editorial relevance (linkage to an active
board story), live momentum (trending terms), and recency. Follower counts
play no part (rule 12).
"""

import html
import json
import re
from datetime import datetime, timedelta, timezone

from app import db

WINDOW_HOURS = 6
TOP_N = 5

# Weights per guardrail news signal (rule 5) — the primary metric.
SIGNAL_WEIGHTS = {
    "breaking announcement": 25,
    "official document": 22,
    "court order": 22,
    "public safety": 22,
    "government notification": 20,
    "investigation": 20,
    "company filing": 18,
    "exclusive reporting": 18,
    "press conference": 15,
    "eyewitness report": 15,
}

_word_re = re.compile(r"[^a-z0-9 ]")
_url_re = re.compile(r"https?://\S+")
_report_re = re.compile(r"\(?\s*report(ed)? by:?\s*@\w+\s*\)?", re.I)
_cta_re = re.compile(r"\b(read|watch|full story|details|more here)\s*[:|]\s*", re.I)


def _summarize(text: str, limit: int = 180) -> str:
    """Readable gist of the post: exact wording minus links, credits and
    formatting noise (accuracy rule — we condense, never paraphrase)."""
    clean = html.unescape(text)
    clean = _url_re.sub("", clean)
    clean = _report_re.sub("", clean)
    clean = _cta_re.sub("", clean)
    clean = clean.replace("#BREAKING", "").replace("#WATCH", "")
    clean = re.sub(r"\(\s*\)", "", clean)
    clean = re.sub(r"^[\s|:•\-–—]+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" |·-–—")
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0]
    return cut + "…"
_STOP = set(
    "the a an and or of in on to for with at by from as is are was be after"
    " amid over under his her their its says say said new live update updates"
    " india indian latest news today breaking case"
    " january february march april may june july august september october"
    " november december jan feb mar apr jun jul aug sep oct nov dec"
    " monday tuesday wednesday thursday friday saturday sunday".split()
)


def _tokens(title: str) -> set[str]:
    return {w for w in _word_re.sub("", title.lower()).split()
            if len(w) > 3 and w not in _STOP and not w.isdigit()}


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

    from app.x.guardrails import detect_signal

    scored = []
    for t in tweets:
        if len((t.get("text") or "").strip()) < 25:
            continue  # bare links carry no standalone signal
        terms = set(t.get("terms") or [])
        score = 0
        reasons = []

        # re-detect against the current rules so stored rows can't carry a
        # stale or wrongly-attributed signal label
        signal = detect_signal(t.get("text") or "")

        linked = None
        for sid, title, tokens in stories:
            if len(terms & tokens) >= 2:
                linked = {"story_id": sid, "story_title": title}
                break

        # Rule 5 gate: no news signal and no board-story match means this
        # post is not alert-worthy, however trusted or fresh the handle.
        if not signal and not linked:
            continue

        trust = t.get("trust_score", 0)
        score += round(trust * 0.3)
        if trust >= 90:
            reasons.append("wire/official-grade source")
        elif trust >= 75:
            reasons.append("high-trust source")

        if signal:
            score += SIGNAL_WEIGHTS.get(signal, 10)
            reasons.append(signal)

        if linked:
            score += 20
            reasons.append("matches a board story")

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
            "avatar_url": t.get("avatar_url") or "",
            "summary": _summarize(t["text"]),
            "created_at": t["created_at"],
            "trust_score": trust, "stream_column": t["stream_column"],
        })

    scored.sort(key=lambda s: (-s["score"], s["created_at"]))
    return scored[:TOP_N]
