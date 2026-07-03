"""Story pack: everything a writer needs when an editor picks a story —
headline, sources, evidence trail, related posts from monitored X handles,
and per-platform format suggestions (PRD omnichannel framework)."""

import json
import re
from datetime import datetime, timedelta, timezone

from app import db

_word_re = re.compile(r"[^a-z0-9 ]")
_STOP = set(
    "the a an and or of in on to for with at by from as is are was be after"
    " amid over under his her their its says say said new live update updates"
    " india indian latest news today breaking case".split()
)


def _headline_tokens(title: str) -> set[str]:
    return {w for w in _word_re.sub("", title.lower()).split()
            if len(w) > 3 and w not in _STOP}


def _related_tweets(con, title: str, limit: int = 6) -> list[dict]:
    tokens = _headline_tokens(title)
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = con.execute(
        "SELECT * FROM tweets WHERE discarded=0 AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 400", (since,),
    ).fetchall()
    related = []
    for r in rows:
        terms = set(json.loads(r["terms"]))
        if len(tokens & terms) >= 2:
            related.append({
                "handle": r["handle"], "display_name": r["display_name"],
                "text": r["text"], "created_at": r["created_at"],
                "trust_score": r["trust_score"], "news_signal": r["news_signal"],
            })
            if len(related) >= limit:
                break
    return related


def _format_suggestions(breakdown: list[dict]) -> list[dict]:
    pts = {b["variable"]: b["points"] for b in breakdown}
    emotions = " ".join(
        e for b in breakdown if b["variable"] == "emotion" for e in b["evidence"]
    ).lower()

    x_reasons, ig_reasons, fb_reasons = [], [], []
    if pts.get("breaking", 0) >= 8:
        x_reasons.append("breaking development")
    if pts.get("political", 0) >= 8:
        x_reasons.append("power-centre involvement")
    if pts.get("trend", 0) > 0:
        x_reasons.append("already moving on X")
    if pts.get("visual", 0) > 0:
        ig_reasons.append("strong visual elements available")
    if "awe" in emotions or "surprise" in emotions:
        ig_reasons.append("high-arousal imagery potential")
    if pts.get("safety", 0) > 0:
        fb_reasons.append("public-safety value drives shares")
    if pts.get("economy", 0) > 0:
        fb_reasons.append("personal-finance impact")
    if "anger" in emotions or "outrage" in emotions:
        fb_reasons.append("justice/outrage vector")

    suggestions = []
    if x_reasons:
        suggestions.append({
            "platform": "X",
            "format": "Flash text alert now, then a rapid thread of verified facts "
                      "with links to live video.",
            "because": ", ".join(x_reasons),
        })
    if ig_reasons:
        suggestions.append({
            "platform": "Instagram",
            "format": "Short visual loop or reel with ground footage; concise "
                      "text carousel for context.",
            "because": ", ".join(ig_reasons),
        })
    if fb_reasons:
        suggestions.append({
            "platform": "Facebook",
            "format": "Longer explainer video with clear text post; end with a "
                      "question cue to stimulate comments.",
            "because": ", ".join(fb_reasons),
        })
    if not suggestions:
        suggestions.append({
            "platform": "All platforms",
            "format": "Standard report; monitor for developments before "
                      "committing premium formats.",
            "because": "no strong platform-specific signals yet",
        })
    return suggestions


def build_pack(story_id: int) -> dict | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
        if not row:
            return None
        story = db.row_to_dict(row)
        breakdown = db.rows_to_dicts(con.execute(
            "SELECT variable, max_points, points, evidence FROM score_breakdowns "
            "WHERE story_id=?", (story_id,),
        ).fetchall())
        story["breakdown"] = sorted(breakdown, key=lambda b: -b["points"])
        story["related_tweets"] = _related_tweets(con, story["title"])
        story["format_suggestions"] = _format_suggestions(breakdown)
        story["evidence_lines"] = [
            e for b in story["breakdown"] for e in b["evidence"]
        ][:10]
    return story
