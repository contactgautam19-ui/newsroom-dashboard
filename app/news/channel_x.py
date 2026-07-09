"""Monitor TV channels via their official X (Twitter) accounts.

A channel's verified X account is the cleanest "what we just aired" feed there
is: each post carries the story as plain headline text, attached video/photo
(the tell that it's a produced/aired segment), a precise timestamp, and the
channel's own #BreakingNews / #LIVE tags. One TwtAPI ``Search`` call with
``from:handle OR …`` pulls all channels at once, and — unlike the stealth site
scrape — it needs no browser, so it runs on Vercel too.

The only constraint is the TwtAPI budget (300 calls/month), so real calls are
throttled to at most once per ``MIN_INTERVAL`` (persisted in settings, shared
across serverless invocations). Results upsert into the same ``live_onair``
table the panel, Alerts feed and India-ranking read.
"""

import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from app import db, settings_store
from app.news import onair

log = logging.getLogger("newsroom.channel_x")

MIN_INTERVAL_MIN = 15          # min minutes between real TwtAPI calls
_LAST_CALL_KEY = "channel_x_last_call"

# TV channel display name -> X handle. Verified returning tweets 2026-07-07.
CHANNEL_X = {
    "India Today": "IndiaToday",
    "NDTV 24x7": "ndtv",
    "Republic TV": "republic",
    "CNN-News18": "CNNnews18",
    "WION": "WIONews",
    "Times Now": "TimesNow",
}
_HANDLE_TO_NAME = {v.lower(): k for k, v in CHANNEL_X.items()}

_BREAKING_TAG = re.compile(r"#?\bbig\s?breaking\b|#?\bbreaking(\s?news)?\b|"
                           r"#?\bjust\s?in\b|#?\bnews\s?flash\b", re.IGNORECASE)
# Channels tag posts of segments that just went to air with a reporter/anchor
# credit line — "@reporter shares more details with @anchor". The strongest
# "this story is ON AIR right now" signal a channel account gives.
_AIRED_CREDIT = re.compile(
    r"shares?\s+(more\s+)?details|details\s+with\s+@|\bon\s?cam\b|"
    r"\btune\s?in\b|\bwatch\s+the\s+(full\s+)?(report|debate|show)\b",
    re.IGNORECASE)
_TRAILER = re.compile(
    r"(?i)\b(read more|watch|watch:|details|full (story|video|coverage)|"
    r"click here|more details|share more details)\b.*$")
_URL = re.compile(r"https?://\S+")
_HASHTAG = re.compile(r"#(\w+)")
_MENTION = re.compile(r"@(\w+)")


def clean_headline(text: str) -> str:
    """Turn a channel tweet into a clean broadcast-style headline."""
    t = html.unescape(text or "")
    t = t.split("\n\n")[0]                 # drop trailing credit/anchor block
    t = t.split("\n")[0] if len(t.split("\n")[0]) > 30 else t.replace("\n", " ")
    t = re.sub(r"^\s*#\w+\s*[|:\-–]\s*", "", t)   # leading "#BreakingNews |"
    t = _TRAILER.sub("", t)
    t = _URL.sub("", t)
    t = _HASHTAG.sub("", t)                # drop trailing/other hashtags
    t = _MENTION.sub(r"\1", t)             # keep the name, drop the @
    t = re.sub(r"\s+", " ", t).strip(" |-–—:•")
    # normalise stylised unicode headlines (𝟔 𝐢𝐧𝐣𝐮𝐫𝐞𝐝…) back to ascii-ish
    if t and sum(c.isascii() for c in t) < len(t) * 0.6:
        t = t.encode("ascii", "ignore").decode().strip() or t
    if len(t) > 160:                       # trim to a sentence near 160 chars
        cut = t[:160]
        dot = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        t = (cut[:dot + 1] if dot > 80 else cut).strip()
    return t


def _parse(payload) -> list[dict]:
    """Extract [{channel, headline, breaking, media, created}] from a TwtAPI
    Search payload, keeping only posts that are an on-air signal: attached
    video/photo (an aired segment) OR a breaking/LIVE tag."""
    norm = (payload or {}).get("_normalized", {}).get("tweets") or []
    out, seen = [], set()
    for entry in norm:
        res = (entry or {}).get("result", {})
        if res.get("__typename") == "TweetWithVisibilityResults":
            res = res.get("tweet", {})
        legacy = res.get("legacy", {})
        user = res.get("core", {}).get("user_results", {}).get("result", {})
        sn = (user.get("core", {}).get("screen_name")
              or user.get("legacy", {}).get("screen_name") or "").lower()
        name = _HANDLE_TO_NAME.get(sn)
        if not name:
            continue
        raw = legacy.get("full_text") or ""
        media = (legacy.get("extended_entities", {}).get("media")
                 or legacy.get("entities", {}).get("media") or [])
        mtypes = {m.get("type") for m in media}
        has_media = bool(mtypes & {"video", "animated_gif", "photo"})
        is_breaking = bool(_BREAKING_TAG.search(raw))
        is_live = "#live" in raw.lower()
        is_aired_credit = bool(_AIRED_CREDIT.search(raw))
        if not (has_media or is_breaking or is_live or is_aired_credit):
            continue                       # skip pure text/opinion/article posts
        headline = clean_headline(raw)
        if len(headline) < 18:
            continue
        low = headline.lower()
        if low in seen or low in onair._GENERIC:
            continue
        seen.add(low)
        out.append({"channel": name, "headline": headline,
                    "breaking": is_breaking, "media": has_media,
                    "created": legacy.get("created_at")})
    return out


def _throttled(min_interval_min: int = MIN_INTERVAL_MIN) -> bool:
    """True if we called TwtAPI within the interval — skip a real call. The
    last-call time is persisted in settings, so the local worker and serverless
    refreshes share one budget clock."""
    last = settings_store.get_setting(_LAST_CALL_KEY, "")
    if not last:
        return False
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(last)
                ) < timedelta(minutes=min_interval_min)
    except ValueError:
        return False


def poll_channel_x(force: bool = False,
                   min_interval_min: int = MIN_INTERVAL_MIN) -> dict:
    """One TwtAPI call across all channel handles; upsert aired-story headlines
    into live_onair. Throttled unless ``force``; callers on a tighter budget
    (the worker's scheduled poll) pass a larger ``min_interval_min``. Returns a
    stats dict; a skipped call reports ``throttled``."""
    from app import config
    if not config.TWT_API_KEY:
        return {"ok": False, "reason": "no TwtAPI key", "headlines": 0}
    if not force and _throttled(min_interval_min):
        return {"ok": True, "throttled": True, "headlines": 0}

    from app.x.twtapi_provider import TwtAPIProvider
    provider = TwtAPIProvider()
    query = " OR ".join(f"from:{h}" for h in CHANNEL_X.values())
    try:
        payload = provider._get("Search", {"q": query, "type": "Latest",
                                           "count": 30})
    except Exception as exc:
        log.warning("channel-X fetch failed: %s", exc)
        return {"ok": False, "reason": str(exc), "headlines": 0}

    settings_store.set_setting(_LAST_CALL_KEY,
                               datetime.now(timezone.utc).isoformat())
    items = _parse(payload)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    hour_key = onair._hour_key(now.astimezone(onair.IST))
    n = brk = 0
    with db.connect() as con:
        for it in items:
            slug = hashlib.sha1(
                f"{it['channel']}|{it['headline'].lower()}|{hour_key}".encode()
            ).hexdigest()[:16]
            con.execute(
                """INSERT INTO live_onair
                   (slug, channel, headline, hour_key, breaking, first_seen,
                    last_seen, source)
                   VALUES (?,?,?,?,?,?,?,'x')
                   ON CONFLICT (slug) DO UPDATE SET
                     last_seen=excluded.last_seen, headline=excluded.headline,
                     breaking=CASE WHEN live_onair.breaking=1
                                   OR excluded.breaking=1 THEN 1 ELSE 0 END""",
                (slug, it["channel"], it["headline"], hour_key,
                 1 if it["breaking"] else 0, now_iso, now_iso))
            n += 1
            brk += 1 if it["breaking"] else 0
    return {"ok": True, "headlines": n, "breaking": brk,
            "channels": len({i["channel"] for i in items})}
