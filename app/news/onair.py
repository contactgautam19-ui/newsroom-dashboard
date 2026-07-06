"""Live on-air monitor — what each news channel is *broadcasting* right now.

Unlike the clip-based rival monitor (``live_monitor.py``, which reads channels'
YouTube *uploads* and therefore mixes in packaged clips, event promos and
article-style videos), this reads each channel's **persistent 24x7 LIVE stream
title**. Channels curate that title as a pipe-separated list of the stories
currently on air, e.g.:

    "India Today TV: Ram Mandir's Champat Rai Resigns | Khamenei Funeral |
     Mumbai Rain | Modi In Indonesia"

Polling that title every few minutes and recording the headlines it shows —
timestamped into IST clock-hour buckets — gives a faithful "what aired this
hour, per channel" log with zero API cost (keyless YouTube oEmbed). Breaking
segments (titles/segments carrying "BREAKING", "BIG DEVELOPMENT", "JUST IN"…)
are flagged so an editor sees when a rival is breaking on top of a running
story.

Times Now has no clean YouTube live stream; it is handled by a separate local
OCR worker (source ``web-ocr``) which writes into the same ``live_onair`` table.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app import config, db

log = logging.getLogger("newsroom.onair")

OEMBED = "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
FETCH_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"}
IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_HOURS = 12   # how far back the panel can look
KEEP_HOURS = 26     # housekeeping horizon

LAST_ONAIR_POLL: dict = {
    "at": None, "streams": 0, "headlines": 0, "breaking": 0, "errors": [],
}

# segments that are pure branding / generic desk labels, never a story
_GENERIC = {
    "news", "live", "live news", "news live", "breaking news", "latest news",
    "top news", "top headlines", "news headlines", "headlines", "top stories",
    "international news", "world news", "world latest english news",
    "english news", "national news", "india news", "hindi news", "watch live",
    "live updates", "news updates", "big news", "the news", "prime time",
    "coverage", "special coverage", "latest updates", "updates", "bulletin",
}
# brand tokens stripped when they trail/lead a headline
_BRAND_TOKENS = {
    "ndtv", "24x7", "indiatoday", "india", "today", "timesnow", "times", "now",
    "republic", "wion", "cnn", "news18", "n18", "aajtak", "tv", "live",
}
# keyword families that mark a channel actively *breaking* a story
_BREAKING_RE = re.compile(
    r"\b(big\s+breaking|breaking|big\s+development|big\s+story|big\s+news|"
    r"just\s+in|news\s?flash|big\s+reveal|big\s+expos[eé]|big\s+update)\b",
    re.IGNORECASE,
)
# label prefixes before a ':' are dropped only when they look like a show /
# channel banner (contain these), so real "Place: detail" headlines survive
_LABEL_HINTS = ("live", "tv", "24x7", "debate", "show", "newshour", "samvaad",
                "newstrack", "nation", "bulletin", "special", "exclusive")


def load_streams() -> list[dict]:
    path = config.DATA_DIR / "live_streams.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def fetch_live_title(video_id: str) -> str | None:
    """Current title of a live stream via keyless oEmbed (None if unavailable)."""
    try:
        resp = httpx.get(OEMBED.format(vid=video_id), timeout=FETCH_TIMEOUT,
                         follow_redirects=True, headers=_HEADERS)
        if resp.status_code != 200:
            return None
        return (resp.json() or {}).get("title")
    except (httpx.HTTPError, ValueError):
        return None


def _strip_label_prefix(seg: str) -> str:
    """Drop a leading 'Show/Banner LIVE:' label, keeping the headline after the
    colon — but only when the prefix looks like a banner, so 'Mumbai Rains: 6
    Dead' keeps its full text."""
    if ":" not in seg:
        return seg
    prefix, _, rest = seg.partition(":")
    low = prefix.lower()
    looks_label = any(h in low for h in _LABEL_HINTS) or any(
        b in low.split() for b in _BRAND_TOKENS)
    if looks_label and rest.strip():
        return rest.strip()
    return seg


def _trim_brand_words(text: str) -> str:
    """Strip leading/trailing pure-brand tokens (WION, India Today, News18…)."""
    words = text.split()
    while words and words[-1].lstrip("#").rstrip(":.").lower() in _BRAND_TOKENS:
        words.pop()
    while words and words[0].lstrip("#").rstrip(":.").lower() in _BRAND_TOKENS:
        words.pop(0)
    return " ".join(words).strip(" -–—|:#")


def parse_headlines(title: str) -> list[dict]:
    """Parse a live-stream title into on-air story headlines.

    Returns [{"headline": str, "breaking": bool}] in on-air order, de-duped.
    """
    if not title:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in title.split("|"):
        seg = re.sub(r"\s+", " ", raw).strip()
        if not seg:
            continue
        breaking = bool(_BREAKING_RE.search(seg))
        cleaned = _trim_brand_words(_strip_label_prefix(seg))
        # remove a leading breaking-word so it doesn't pollute the headline text
        cleaned = _BREAKING_RE.sub("", cleaned, count=1).strip(" -–—:|") or cleaned
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) < 4:
            continue
        norm = cleaned.lower()
        if norm in _GENERIC or norm in seen:
            continue
        seen.add(norm)
        out.append({"headline": cleaned, "breaking": breaking})
    return out


def _hour_key(dt_ist: datetime) -> str:
    return dt_ist.strftime("%Y-%m-%dT%H")


def poll_onair() -> dict:
    """Poll every configured YouTube live stream, parse the on-air headlines and
    upsert them into ``live_onair`` under the current IST hour. Times Now
    (source ``web-ocr``) is skipped here — the local OCR worker feeds it."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    hour_key = _hour_key(now.astimezone(IST))
    errors: list[str] = []
    n_head = n_break = 0

    with db.connect() as con:
        for st in load_streams():
            if st.get("source") != "youtube":
                continue
            name = st.get("name", "?")
            try:
                title = fetch_live_title(st["video_id"])
                if not title:
                    errors.append(f"{name}: no live title")
                    continue
                for item in parse_headlines(title):
                    slug = hashlib.sha1(
                        f"{name}|{item['headline'].lower()}|{hour_key}".encode()
                    ).hexdigest()[:16]
                    con.execute(
                        """INSERT INTO live_onair
                           (slug, channel, headline, hour_key, breaking,
                            first_seen, last_seen)
                           VALUES (?,?,?,?,?,?,?)
                           ON CONFLICT (slug) DO UPDATE SET
                             last_seen=excluded.last_seen,
                             headline=excluded.headline,
                             breaking=MAX(live_onair.breaking, excluded.breaking)""",
                        (slug, name, item["headline"], hour_key,
                         1 if item["breaking"] else 0, now_iso, now_iso),
                    )
                    n_head += 1
                    n_break += 1 if item["breaking"] else 0
            except Exception as exc:  # one bad stream shouldn't kill the poll
                errors.append(f"{name}: {exc}")
                log.warning("on-air poll failed for %s: %s", name, exc)

        cutoff = (now - timedelta(hours=KEEP_HOURS)).isoformat()
        con.execute("DELETE FROM live_onair WHERE last_seen < ?", (cutoff,))

    LAST_ONAIR_POLL.clear()
    LAST_ONAIR_POLL.update({
        "at": now_iso,
        "streams": sum(1 for s in load_streams() if s.get("source") == "youtube"),
        "headlines": n_head, "breaking": n_break, "errors": errors,
    })
    return dict(LAST_ONAIR_POLL)


def _hour_label(hour_key: str) -> str:
    """'YYYY-MM-DDTHH' (IST) -> '5 – 6 PM'."""
    dt = datetime.strptime(hour_key, "%Y-%m-%dT%H")

    def fmt(h: int) -> str:
        return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"

    return f"{fmt(dt.hour)} – {fmt((dt.hour + 1) % 24)}"


def onair_hourly(hours_back: int = WINDOW_HOURS) -> dict:
    """On-air headlines grouped by IST hour -> channel, newest hour first.

    Each hour: {label, date, hour_key, total, breaking, channels:[{channel,
    count, breaking, items:[{headline, breaking}]}]}. Also returns
    ``current_hour_key`` so the UI can default to the live hour.
    """
    now = datetime.now(timezone.utc)
    current_key = _hour_key(now.astimezone(IST))
    since = (now - timedelta(hours=hours_back)).isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT channel, headline, hour_key, breaking, first_seen "
            "FROM live_onair WHERE first_seen >= ? "
            "ORDER BY hour_key DESC, breaking DESC, first_seen ASC",
            (since,),
        ).fetchall()
    rows = db.rows_to_dicts(rows)

    # hour_key -> channel -> list[items]
    hours: dict[str, dict[str, list]] = {}
    hour_break: dict[str, int] = {}
    for r in rows:
        chans = hours.setdefault(r["hour_key"], {})
        items = chans.setdefault(r["channel"], [])
        items.append({"headline": r["headline"], "breaking": bool(r["breaking"])})
        if r["breaking"]:
            hour_break[r["hour_key"]] = hour_break.get(r["hour_key"], 0) + 1

    out_hours = []
    for hk in sorted(hours.keys(), reverse=True):
        chans = hours[hk]
        channels = []
        for ch, items in sorted(chans.items(),
                                key=lambda kv: (any(i["breaking"] for i in kv[1]),
                                                len(kv[1])), reverse=True):
            channels.append({
                "channel": ch, "count": len(items),
                "breaking": any(i["breaking"] for i in items),
                "items": items,
            })
        try:
            date = datetime.strptime(hk, "%Y-%m-%dT%H").strftime("%d %b")
        except ValueError:
            date = ""
        out_hours.append({
            "label": _hour_label(hk), "date": date, "hour_key": hk,
            "total": sum(len(i) for i in chans.values()),
            "breaking": hour_break.get(hk, 0),
            "channels": channels,
        })

    return {
        "generated_at": now.isoformat(),
        "current_hour_key": current_key,
        "streams": [s.get("name") for s in load_streams()],
        "hours": out_hours,
    }


def run_onair_cycle() -> dict:
    """Scheduler / refresh entry point."""
    stats = poll_onair()
    return {"headlines": stats.get("headlines", 0),
            "breaking": stats.get("breaking", 0)}
