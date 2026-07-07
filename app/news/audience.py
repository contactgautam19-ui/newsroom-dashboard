"""India-audience fit scoring — ranks the board for an Indian TV newsroom.

Two signals, one breakdown entry ("audience", max 10):

1. **India connection** — the story names an Indian place, institution or
   figure. Foreign stories without one score zero here, which is what sinks
   world-desk filler (Venezuela ceremonials, minor foreign elections) below
   genuinely Indian news without hard-blocking global mega-stories, which
   earn their slot through breaking/safety/political points instead.
2. **Rival TV match** — the story shares significant tokens with what Indian
   news channels are airing RIGHT NOW (live_onair) or aired in the last few
   hours (live_coverage clips). If competitors are on it, it is by
   definition TV-worthy for this audience — the board learns from TV.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from app import db

_word_re = re.compile(r"[^a-z0-9 ]")

# Indian places, institutions and civic vocabulary. Lowercase; matched on word
# boundaries against title+summary. Deliberately broad — one hit is enough.
INDIA_MARKERS = (
    "india indian delhi mumbai bengaluru bangalore kolkata chennai hyderabad "
    "pune ahmedabad jaipur lucknow surat kanpur nagpur indore bhopal patna "
    "vadodara ludhiana agra varanasi srinagar amritsar chandigarh guwahati "
    "kochi coimbatore mysuru thiruvananthapuram bhubaneswar ranchi raipur "
    "dehradun shimla goa kerala tamil nadu karnataka telangana andhra "
    "maharashtra gujarat rajasthan punjab haryana uttarakhand himachal bihar "
    "jharkhand odisha bengal assam tripura manipur meghalaya mizoram nagaland "
    "sikkim kashmir jammu ladakh uttar pradesh madhya pradesh chhattisgarh "
    "modi rahul gandhi kejriwal yogi adityanath mamata banerjee nitish "
    "lok sabha rajya sabha sansad bjp congress aap dmk tmc ncp "
    "shiv sena rss vhp supreme court high court cji election commission "
    "rbi sebi isro drdo cbi nia ed enforcement directorate income tax "
    "railways irctc aadhaar upi gst nifty sensex rupee crore lakh "
    "ipl bcci team india kohli sharma bumrah "
    "bollywood ayodhya ram mandir tirupati kumbh amarnath vande bharat "
    "monsoon imd cyclone panchayat sarpanch collector "
    "pakistan china border loc lac brahmos rafale agniveer indian army"
).split()
# multi-word markers checked as phrases
_INDIA_PHRASES = [m for m in (
    "tamil nadu", "uttar pradesh", "madhya pradesh", "lok sabha", "rajya sabha",
    "supreme court", "high court", "election commission", "shiv sena",
    "ram mandir", "team india", "vande bharat", "indian army",
    "enforcement directorate", "income tax", "rahul gandhi",
    "mamata banerjee", "yogi adityanath",
) ]
_INDIA_SINGLE = {m for m in INDIA_MARKERS if " " not in m}

_STOP = set(
    "the and for with from this that says said after amid over under live news"
    " breaking today latest updates india indian world".split()
)


def _tokens(text: str) -> set[str]:
    return {w for w in _word_re.sub("", (text or "").lower()).split()
            if len(w) > 3 and w not in _STOP and not w.isdigit()}


def india_hits(text: str) -> list[str]:
    low = " " + _word_re.sub(" ", (text or "").lower()) + " "
    hits = [p for p in _INDIA_PHRASES if f" {p} " in low]
    words = set(low.split())
    hits += [w for w in _INDIA_SINGLE if w in words]
    return hits[:5]


# on-air terms cache: (built_at, [(channel, token_set), ...])
_onair_cache: tuple = (None, [])
_CACHE_TTL = 120  # seconds


def _onair_term_sets() -> list[tuple[str, set]]:
    """Token sets for everything Indian channels aired recently — live-stream
    headlines (live_onair, last 3h) plus uploads clips (live_coverage)."""
    global _onair_cache
    now = datetime.now(timezone.utc)
    if _onair_cache[0] and (now - _onair_cache[0]).total_seconds() < _CACHE_TTL:
        return _onair_cache[1]
    since = (now - timedelta(hours=3)).isoformat()
    sets: list[tuple[str, set]] = []
    try:
        with db.connect() as con:
            for r in con.execute(
                    "SELECT channel, headline FROM live_onair WHERE last_seen >= ?",
                    (since,)).fetchall():
                toks = _tokens(r["headline"])
                if len(toks) >= 2:
                    sets.append((r["channel"], toks))
            for r in con.execute(
                    "SELECT channel, terms FROM live_coverage WHERE fetched_at >= ?",
                    (since,)).fetchall():
                try:
                    toks = {t for t in json.loads(r["terms"]) if t not in _STOP}
                except (json.JSONDecodeError, TypeError):
                    continue
                if len(toks) >= 2:
                    sets.append((r["channel"], toks))
    except Exception:
        return _onair_cache[1]  # DB hiccup: reuse last known
    _onair_cache = (now, sets)
    return sets


def tv_match(title: str) -> list[str]:
    """Channels currently/recently airing something that shares >=2 significant
    tokens with this story title."""
    toks = _tokens(title)
    if not toks:
        return []
    channels = {ch for ch, terms in _onair_term_sets()
                if len(terms & toks) >= 2}
    return sorted(channels)


def audience_entry(title: str, summary: str) -> dict:
    """Breakdown entry: India Audience Fit, max 10.

    10 = rivals airing it (TV-proven) · 6 = clear India angle · 0 = foreign
    story no Indian channel is touching."""
    text = f"{title} {summary or ''}"
    channels = tv_match(title)
    hits = india_hits(text)
    if channels:
        points = 10
        evidence = [f"Indian channels airing this now: {', '.join(channels)}"]
        if hits:
            evidence.append(f"India angle: '{hits[0]}'")
    elif hits:
        points = 6
        evidence = [f"India angle: '{h}'" for h in hits[:3]]
    else:
        points = 0
        evidence = ["no India angle and no Indian channel airing it — "
                    "down-ranked for this audience"]
    return {"variable": "audience", "label": "India Audience Fit",
            "max_points": 10, "points": points, "evidence": evidence}
