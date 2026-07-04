"""Live rival-TV coverage monitor.

Rival TV channels' YouTube "uploads" RSS feeds mirror their on-air rundown
within minutes, so polling them tells the desk what competitors are airing
right now. Two effects for editors:

1. Board stories that rivals are already airing get an "On air" flag, so the
   producer knows the competition is on it.
2. Topics rivals are airing that our board is *missing* become priority
   discovery keywords (origin ``live-tv``) so the next ingest goes and finds
   them — turning the competition into a lead generator.

The feed is the public, key-free ``feeds/videos.xml?channel_id=`` endpoint;
entries carry title, published_parsed and yt_videoid.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from app import config, db

log = logging.getLogger("newsroom.live_monitor")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={id}"
FEED_TIMEOUT = 15
_HEADERS = {"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"}
MATCH_MIN_SHARED = 2   # shared significant tokens for a story<->clip match

# ops status snapshot, read by /api/ops
LAST_POLL: dict = {
    "at": None,
    "channels": 0,
    "clips_in_window": 0,
    "recent_titles": [],
    "errors": [],
}

_word_re = re.compile(r"[^a-z0-9 ]")
_MONTHS = (
    "january february march april may june july august september october"
    " november december jan feb mar apr jun jul aug sep oct nov dec"
)
_WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday"
# generic tv-noise words that carry no story signal
_TV_NOISE = (
    "live news breaking today latest updates watch video full show debate"
    " hosts episode"
)
# prepositions/fillers that must never become discovery keywords
_FILLERS = (
    "under over after amid into from with about between against before during"
    " since while this that these those their there where when what says said"
    " tells told big top"
)
_STOP = set((_MONTHS + " " + _WEEKDAYS + " " + _TV_NOISE + " " + _FILLERS).split())

# lowercased branding fragments that trail a headline after a '|'
_BRAND_WORDS = {"ndtv", "indiatoday", "india today", "live", "news"}


def load_channels() -> list[dict]:
    """Rival channel list; tolerates a missing file (returns [])."""
    path = config.DATA_DIR / "live_channels.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _clean_title(title: str) -> str:
    """Strip trailing channel branding/noise from a YouTube headline.

    Titles are typically "Real Headline | NDTV 24x7 LIVE"; drop the trailing
    pipe-segments that are only channel branding words. Conservative: never
    returns empty, falls back to the collapsed original.
    """
    raw = (title or "").strip()
    if not raw:
        return raw
    segments = [seg.strip() for seg in raw.split("|")]
    kept = []
    for seg in segments:
        squashed = _word_re.sub("", seg.lower()).replace(" ", "")
        words = {w for w in _word_re.sub("", seg.lower()).split() if w}
        # drop segments whose words are entirely branding/noise
        is_branding = bool(words) and words <= (_BRAND_WORDS | {"24x7", "24"})
        if not squashed or is_branding:
            continue
        kept.append(seg)
    cleaned = " ".join(re.sub(r"\s+", " ", s).strip() for s in kept).strip()
    return cleaned or re.sub(r"\s+", " ", raw).strip()


def _tokens(title: str) -> list[str]:
    """Significant tokens (mirrors sources._sig_tokens): lowercase, alnum-only,
    len>3, not a stopword/month/weekday/tv-noise word, not all digits."""
    return [
        w for w in _word_re.sub("", (title or "").lower()).split()
        if len(w) > 3 and w not in _STOP and not w.isdigit()
    ]


def _parse_published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def poll_live_coverage() -> None:
    """Poll every rival channel's uploads feed and store in-window clips.

    Per-channel try/except so one failing feed doesn't kill the poll. Records
    error strings into LAST_POLL. Housekeeps rows older than 24h.
    """
    channels = load_channels()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=config.LIVE_WINDOW_HOURS)
    errors: list[str] = []
    stored = 0

    with db.connect() as con:
        for ch in channels:
            name = ch.get("name", "?")
            try:
                url = FEED_URL.format(id=ch["channel_id"])
                resp = httpx.get(url, timeout=FEED_TIMEOUT,
                                 follow_redirects=True, headers=_HEADERS)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    published = _parse_published(entry)
                    if not published or published < window_start:
                        continue
                    video_id = entry.get("yt_videoid") or entry.get("id", "")
                    if not video_id:
                        continue
                    clean = _clean_title(entry.get("title", ""))
                    if not clean:
                        continue
                    terms = json.dumps(_tokens(clean))
                    con.execute(
                        """INSERT OR REPLACE INTO live_coverage
                           (video_id, channel, title, published_at, fetched_at, terms)
                           VALUES (?,?,?,?,?,?)""",
                        (video_id, name, clean, published.isoformat(),
                         now.isoformat(), terms),
                    )
                    stored += 1
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                log.warning("live poll failed for %s: %s", name, exc)

        # housekeeping: drop clips older than 24h
        cutoff_24h = (now - timedelta(hours=24)).isoformat()
        con.execute("DELETE FROM live_coverage WHERE published_at < ?", (cutoff_24h,))

        window_rows = con.execute(
            "SELECT title FROM live_coverage WHERE published_at >= ? "
            "ORDER BY published_at DESC",
            (window_start.isoformat(),),
        ).fetchall()

    clips_in_window = len(window_rows)
    recent_titles = [r["title"] for r in window_rows[:6]]

    LAST_POLL.clear()
    LAST_POLL.update({
        "at": now.isoformat(),
        "channels": len(channels),
        "clips_in_window": clips_in_window,
        "recent_titles": recent_titles,
        "errors": errors,
    })


def _window_rows() -> list[dict]:
    """Recent live-coverage rows within the window (terms parsed)."""
    window_start = (datetime.now(timezone.utc)
                    - timedelta(hours=config.LIVE_WINDOW_HOURS)).isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT channel, title, terms FROM live_coverage WHERE published_at >= ?",
            (window_start,),
        ).fetchall()
    return db.rows_to_dicts(rows)


def match_to_board() -> int:
    """Flag active board stories that rivals are airing right now.

    A story matches a clip when they share >= MATCH_MIN_SHARED significant
    tokens. Writes the sorted set of covering channels to
    ``stories.rival_coverage`` and pushes a fresh rundown when anything changed.
    Returns the number of stories whose coverage changed.
    """
    coverage = _window_rows()
    changed = 0
    with db.connect() as con:
        stories = con.execute(
            "SELECT id, title, rival_coverage FROM stories WHERE active=1"
        ).fetchall()
        for story in stories:
            story_tokens = set(_tokens(story["title"]))
            if not story_tokens:
                channels_covering: list[str] = []
            else:
                channels_covering = sorted({
                    row["channel"] for row in coverage
                    if len(set(row["terms"]) & story_tokens) >= MATCH_MIN_SHARED
                })
            current = story["rival_coverage"]
            new_json = json.dumps(channels_covering)
            if current != new_json:
                con.execute(
                    "UPDATE stories SET rival_coverage=? WHERE id=?",
                    (new_json, story["id"]),
                )
                changed += 1

    if changed:
        from app.news import ingest
        ingest.publish_rundown()
    return changed


def live_hot_terms(limit: int = 10) -> list[dict]:
    """Priority discovery keywords: topics rivals are airing that our board is
    *missing*. Weight = mentions + 2*(distinct channels carrying the term), so
    cross-channel consensus counts double. Terms already matching an active
    board story are excluded (those are covered — we want the gaps)."""
    coverage = _window_rows()
    if not coverage:
        return []

    counts: Counter = Counter()
    channels_per_term: dict[str, set] = {}
    for row in coverage:
        for term in set(row["terms"]):
            counts[term] += 1
            channels_per_term.setdefault(term, set()).add(row["channel"])

    # tokens already on the board are covered — skip them
    with db.connect() as con:
        stories = con.execute(
            "SELECT title FROM stories WHERE active=1"
        ).fetchall()
    board_tokens: set = set()
    for s in stories:
        board_tokens |= set(_tokens(s["title"]))

    scored = []
    for term, count in counts.items():
        if term in board_tokens:
            continue
        weight = count + 2 * len(channels_per_term[term])
        if weight >= 2:
            scored.append({"term": term, "weight": weight, "origin": "live-tv"})
    scored.sort(key=lambda k: k["weight"], reverse=True)
    return scored[:limit]


def run_live_cycle() -> dict:
    """Single scheduler entry point: poll rival feeds, then re-match the board."""
    poll_live_coverage()
    changed = match_to_board()
    return {"clips_in_window": LAST_POLL.get("clips_in_window", 0),
            "stories_changed": changed}
