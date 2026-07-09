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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from app import config, db

log = logging.getLogger("newsroom.onair")

OEMBED = "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
FETCH_TIMEOUT = 8
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


_LIVE_URL = "https://www.youtube.com/channel/{cid}/live"
_VIDEOID_RE = re.compile(r'"videoId":"([A-Za-z0-9_-]{6,})"')


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


def resolve_live_video_id(channel_id: str) -> str | None:
    """Current live video for a channel via its /live page (None if not live).

    The videoId sits deep in a ~1.2MB page, so this pulls real bytes and is
    slow from a throttled datacenter IP — it is therefore only used off the
    serverless path (see ``stream_title``); the always-on local worker does the
    resolution and self-healing, and the Vercel refresh sticks to fast pinned
    oEmbed lookups."""
    try:
        resp = httpx.get(_LIVE_URL.format(cid=channel_id), timeout=20,
                         follow_redirects=True, headers=_HEADERS)
        if resp.status_code != 200:
            return None
        m = _VIDEOID_RE.search(resp.text)
        return m.group(1) if m else None
    except httpx.HTTPError:
        return None


def stream_title(st: dict) -> str | None:
    """Title for one configured stream. Uses the pinned persistent video_id when
    set; falls back to resolving the channel's current /live video (which also
    self-heals a pinned stream that has since ended, and covers channels like
    Times Now that only run rotating live streams)."""
    vid = st.get("video_id")
    title = fetch_live_title(vid) if vid else None
    # /live resolution pulls a big page and is slow from Vercel's IPs, so keep
    # it off the serverless request path — the local worker handles resolution,
    # Times Now, and self-healing of stale pins.
    if not title and st.get("channel_id") and not config.IS_SERVERLESS:
        live_vid = resolve_live_video_id(st["channel_id"])
        if live_vid:
            title = fetch_live_title(live_vid)
    return title


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

    streams = [s for s in load_streams() if s.get("source") == "youtube"]

    # Fetch all live titles in parallel — YouTube oEmbed can be slow from a
    # datacenter IP, and sequential fetches blew past the serverless 60s cap.
    def _titled(st: dict) -> tuple[str, str | None]:
        try:
            return st.get("name", "?"), stream_title(st)
        except Exception:  # recorded per stream below
            return st.get("name", "?"), None
    with ThreadPoolExecutor(max_workers=max(1, len(streams))) as pool:
        results = list(pool.map(_titled, streams)) if streams else []

    with db.connect() as con:
        for name, title in results:
            if not title:
                errors.append(f"{name}: no live title")
                continue
            for item in parse_headlines(title):
                slug = hashlib.sha1(
                    f"{name}|{item['headline'].lower()}|{hour_key}".encode()
                ).hexdigest()[:16]
                # sticky-max breaking flag, portable across SQLite + Postgres
                # (Postgres MAX() is aggregate-only, so use CASE not MAX(a,b))
                con.execute(
                    """INSERT INTO live_onair
                       (slug, channel, headline, hour_key, breaking,
                        first_seen, last_seen, source)
                       VALUES (?,?,?,?,?,?,?,'title')
                       ON CONFLICT (slug) DO UPDATE SET
                         last_seen=excluded.last_seen,
                         headline=excluded.headline,
                         breaking=CASE WHEN live_onair.breaking=1
                                       OR excluded.breaking=1 THEN 1 ELSE 0 END""",
                    (slug, name, item["headline"], hour_key,
                     1 if item["breaking"] else 0, now_iso, now_iso),
                )
                n_head += 1
                n_break += 1 if item["breaking"] else 0

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


def _near_duplicate(headline: str, kept: list[dict]) -> bool:
    """True when an OCR/X headline is a token-level rerun of one already kept —
    successive OCR passes of the same chyron differ by a word or two, and
    without this the hour bucket fills with five variants of one story."""
    toks = {w for w in re.findall(r"[a-z]{3,}", headline.lower())}
    if not toks:
        return True
    for k in kept:
        ktoks = {w for w in re.findall(r"[a-z]{3,}", k["headline"].lower())}
        if not ktoks:
            continue
        overlap = len(toks & ktoks) / min(len(toks), len(ktoks))
        if overlap >= 0.7:
            # keep the longer read; swap in place if the new one is fuller
            if len(headline) > len(k["headline"]):
                k["headline"] = headline
            return True
    return False


# what counts as broadcast evidence for the "What's on air" panel: player OCR
# and the channel's own aired-story X posts. YouTube title tags ('title') and
# website scrapes ('web') still feed alerts + rival matching but NOT this panel
AIRED_SOURCES = ("ocr", "x")


def onair_hourly(hours_back: int = WINDOW_HOURS) -> dict:
    """Aired headlines grouped by IST hour -> channel, newest hour first.

    Only broadcast-evidence rows (source in AIRED_SOURCES) appear: player OCR
    reads and channels' own "we aired this" X posts. Each hour: {label, date,
    hour_key, total, breaking, channels:[{channel, count, breaking,
    items:[{headline, breaking, via}]}]}. Also returns ``current_hour_key``
    so the UI can default to the live hour.
    """
    now = datetime.now(timezone.utc)
    current_key = _hour_key(now.astimezone(IST))
    since = (now - timedelta(hours=hours_back)).isoformat()
    marks = ",".join("?" for _ in AIRED_SOURCES)
    with db.connect() as con:
        rows = con.execute(
            "SELECT channel, headline, hour_key, breaking, first_seen, source "
            f"FROM live_onair WHERE first_seen >= ? AND source IN ({marks}) "
            "ORDER BY hour_key DESC, breaking DESC, first_seen ASC",
            (since, *AIRED_SOURCES),
        ).fetchall()
    rows = db.rows_to_dicts(rows)

    # display-time quality gate for OCR rows: a stored read that fails the
    # current junk/quality rules never renders, so filter improvements clean
    # up historical rows retroactively without touching the DB
    from app.news import live_ocr

    def _display_junk(r: dict) -> bool:
        if r["source"] != "ocr":
            return False
        return (live_ocr._is_junk(r["headline"])
                or not live_ocr._candidate(r["headline"]))

    # hour_key -> channel -> list[items]
    hours: dict[str, dict[str, list]] = {}
    hour_break: dict[str, int] = {}
    for r in rows:
        if _display_junk(r):
            continue
        chans = hours.setdefault(r["hour_key"], {})
        items = chans.setdefault(r["channel"], [])
        if _near_duplicate(r["headline"], items):
            continue
        items.append({"headline": r["headline"], "breaking": bool(r["breaking"]),
                      "via": r["source"]})
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
