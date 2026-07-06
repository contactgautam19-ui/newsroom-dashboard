"""Desk snapshot for N-Pro's editorial brain.

Condenses everything the dashboard already knows — the ranked board, X-desk
signals, rival live coverage and viral velocity events — into one compact text
block the model can reason over. This is what lets N-Pro answer questions like
"what should lead the bulletin?", "what's likely to go viral?" or "what are
rivals airing that we're missing?" from *live desk data*, not guesses.

Every section is independently fail-safe: a broken subsystem degrades the
snapshot instead of killing the chat.
"""

from datetime import datetime, timedelta, timezone

from app import db


def _age(iso: str) -> str:
    try:
        mins = int((datetime.now(timezone.utc)
                    - datetime.fromisoformat(iso)).total_seconds() // 60)
        return f"{mins}m" if mins < 120 else f"{mins // 60}h"
    except (TypeError, ValueError):
        return "?"


def _board_lines(limit: int = 12) -> list[str]:
    from app.news import ingest
    lines = ["RANKED BOARD (our scored stories, highest first):"]
    for i, s in enumerate(ingest.get_rundown(limit), 1):
        flags = []
        if s.get("status") == "breaking":
            flags.append("BREAKING")
        if s.get("trend_boost", 0) > 0:
            flags.append(f"trending on X +{s['trend_boost']}")
        if s.get("needs_review"):
            flags.append("single-source, needs verification")
        rc = s.get("rival_coverage") or []
        if rc:
            flags.append(f"rivals airing: {', '.join(rc)}")
        srcs = len(s.get("sources") or [])
        if srcs > 1:
            flags.append(f"{srcs} outlets")
        if s.get("picked"):
            flags.append("already picked")
        lines.append(
            f"{i}. [{s.get('score', 0)}/100 {s.get('status', '')}] "
            f"{s.get('title', '')} ({_age(s.get('published_at', ''))} old"
            + (f"; {'; '.join(flags)}" if flags else "") + ")")
    return lines


def _x_lines(limit: int = 5) -> list[str]:
    from app.x.signals import top_signals
    sigs = top_signals()[:limit]
    if not sigs:
        return []
    lines = ["TOP X SIGNALS (monitored handles, ranked):"]
    for t in sigs:
        reasons = ", ".join(t.get("reasons") or [])
        linked = t.get("linked") or {}
        link = f" -> board story: {linked['story_title']}" if linked.get("story_title") else ""
        lines.append(f"- {t.get('handle', '')}: {(t.get('text') or '')[:120]}"
                     f" ({reasons}){link}")
    return lines


def _onair_lines() -> list[str]:
    from app.news import onair
    data = onair.onair_hourly(hours_back=2)
    hours = data.get("hours") or []
    if not hours:
        return []
    cur = hours[0]
    lines = [f"RIVAL CHANNELS ON AIR NOW ({cur['label']} IST):"]
    for c in cur["channels"]:
        brk = " [BREAKING]" if c.get("breaking") else ""
        heads = "; ".join(i["headline"] for i in c["items"][:3])
        lines.append(f"- {c['channel']}{brk}: {heads}")
    return lines


def _velocity_lines(limit: int = 5) -> list[str]:
    since = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT v.term, v.velocity_pct, s.title AS story_title, v.created_at "
            "FROM velocity_events v LEFT JOIN stories s ON s.id = v.story_id "
            "WHERE v.created_at >= ? ORDER BY v.created_at DESC LIMIT ?",
            (since, limit)).fetchall()
    rows = db.rows_to_dicts(rows)
    if not rows:
        return []
    lines = ["VIRAL VELOCITY EVENTS (X spikes, last 3h):"]
    for r in rows:
        lines.append(f"- '{r['term']}' +{round(r.get('velocity_pct') or 0)}%"
                     + (f" -> {r['story_title']}" if r.get("story_title") else ""))
    return lines


def desk_snapshot() -> str:
    """The whole desk, as one prompt-ready block. Sections fail independently."""
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    blocks = [f"LIVE DESK SNAPSHOT — {now.strftime('%d %b, %I:%M %p')} IST"]
    for fn in (_board_lines, _x_lines, _onair_lines, _velocity_lines):
        try:
            lines = fn()
            if lines:
                blocks.append("\n".join(lines))
        except Exception:
            continue
    return "\n\n".join(blocks)
