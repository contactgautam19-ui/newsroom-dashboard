"""News retrieval for N-Pro — grounds every script in real, recent reporting.

Uses Google News' keyless search RSS (India edition) so it works without any API
key. ``search_news`` powers the initial "what happened" retrieval; ``more_context``
runs angle-specific searches (background, chronology, legal, economic impact …)
and de-duplicates against what the editor has already seen, so each Get More
Context click surfaces genuinely new reporting.
"""

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

SEARCH_URL = ("https://news.google.com/rss/search?q={q}"
              "&hl=en-IN&gl=IN&ceid=IN:en")
TIMEOUT = 8
_HEADERS = {"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"}
_tag_re = re.compile(r"<[^>]+>")

# angle -> query suffix, for Get More Context. Ordered by how an editor widens a
# story; each click advances to the next unused angle.
CONTEXT_ANGLES = [
    ("background", "background explained"),
    ("chronology", "timeline what happened"),
    ("people", "who is involved profile"),
    ("previous incidents", "similar past incidents history"),
    ("legal", "legal probe investigation law"),
    ("economic impact", "economic impact cost market"),
    ("geopolitical", "international reaction geopolitics"),
    ("organisations", "company organisation response"),
    ("official statements", "official statement government reaction"),
    ("expert opinion", "expert analysis opinion"),
    ("historical comparison", "historical comparison precedent"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_tag_re.sub(" ", text or ""))).strip()


def _parse_entries(content: bytes, limit: int) -> list[dict]:
    feed = feedparser.parse(content)
    out = []
    for e in feed.entries[:limit]:
        published = e.get("published_parsed") or e.get("updated_parsed")
        pub_iso = (datetime(*published[:6], tzinfo=timezone.utc).isoformat()
                   if published else "")
        out.append({
            "title": _clean(e.get("title", "")),
            "url": e.get("link", ""),
            "publisher": (e.get("source", {}) or {}).get("title", ""),
            "published_at": pub_iso,
            "summary": _clean(e.get("summary", ""))[:400],
        })
    return out


RECENT_DAYS = 2        # default freshness window for story retrieval
WIDE_DAYS = 7          # how far a thin search may widen before giving up on "recent"
_STOP = set(
    "the a an and or of in on to for with at by from as is are was be after amid "
    "over his her their its says say said new what why how who when where day "
    "days ahead against former orders order amid blow big top live watch news "
    "video update updates latest today this that will can not out now".split())


def _raw_search(query: str, limit: int = 30) -> list[dict]:
    """One Google News search, in Google's own (relevance) order."""
    if not query.strip():
        return []
    try:
        resp = httpx.get(SEARCH_URL.format(q=quote_plus(query)), timeout=TIMEOUT,
                         follow_redirects=True, headers=_HEADERS)
        resp.raise_for_status()
        return _parse_entries(resp.content, limit)
    except (httpx.HTTPError, ValueError):
        return []


def _newest_first(items: list[dict]) -> list[dict]:
    """De-duplicate by title and order strictly by publish time, newest first.
    Google News returns relevance order, which regularly puts a week-old piece
    above this hour's update — the desk must never be handed that as 'latest'."""
    seen, out = set(), []
    for it in items:
        key = _norm(it.get("title", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda it: it.get("published_at") or "", reverse=True)
    return out


def _headline(query: str) -> str:
    """A board title minus its site tail ("… | Tensions Escalate | News")."""
    return re.split(r"\s+\|\s+", query.strip())[0]


def _key_terms(query: str, n: int = 5) -> str:
    """The distinctive words of a long headline — a full title used as a query
    often matches only its own article. Numbers and filler words are dropped;
    what is left is the names and places that identify the story."""
    text = re.sub(r"(?<=\d),(?=\d)", "", _headline(query))
    words = [w for w in re.sub(r"[^\w\s]", " ", text).split()
             if len(w) > 2 and not w.isdigit() and w.lower() not in _STOP]
    return " ".join(words[:n])


def search_news(query: str, limit: int = 8, recent_days: int | None = RECENT_DAYS) -> list[dict]:
    """Articles matching a query, newest first, keyless.

    With ``recent_days`` (default) only reporting from that window is returned.
    A thin result widens in steps — the headline's key terms, then a 7-day
    window — and stops there: a few genuinely recent reports beat a list padded
    with months-old pieces. Undated results are used only when the last week
    holds nothing at all, and they still come back newest first.
    ``recent_days=None`` keeps Google's relevance order for background/context
    lookups where reaching back in time is the point."""
    query = query.strip()
    if not query:
        return []
    if recent_days is None or "when:" in query:
        items = _raw_search(query)
        return (_newest_first(items) if "when:" in query else items)[:limit]

    head, terms = _headline(query), _key_terms(query)
    window = f" when:{recent_days}d"
    items = _newest_first(_raw_search(head + window))
    if len(items) < 3 and terms and terms.lower() != head.lower():
        items = _newest_first(items + _raw_search(terms + window))
    if len(items) < 3 and terms:
        items = _newest_first(items + _raw_search(_key_terms(query, 3) + window))
    if len(items) < 3:
        items = _newest_first(items + _raw_search((terms or head) + f" when:{WIDE_DAYS}d"))
    if not items:
        items = _newest_first(_raw_search(terms or head))
    return items[:limit]


def search_news_past_hour(keyword: str, limit: int = 5) -> list[dict]:
    """Google News 'past hour' search for a keyword — mirrors the manual flow
    (search the keyword -> News -> Tools -> Past hour). Returns the freshest
    ``limit`` headlines with source, using Google News' ``when:1h`` operator."""
    if not keyword.strip():
        return []
    return search_news(f"{keyword.strip()} when:1h", limit=limit)


def search_news_today(keyword: str, limit: int = 5) -> list[dict]:
    """Past-24-hours fallback for when nothing was filed in the last hour."""
    if not keyword.strip():
        return []
    return search_news(f"{keyword.strip()} when:1d", limit=limit)


def more_context(topic: str, used_angles: list[str], seen_urls: list[str],
                 seen_titles: list[str]) -> dict:
    """One fresh block of context on the next unused angle.

    Returns {angle, label, items:[...]} where items exclude anything already
    seen (by URL or near-identical title). Advances through CONTEXT_ANGLES so
    repeated clicks widen the story instead of repeating it.
    """
    used = set(used_angles or [])
    seen_u = set(seen_urls or [])
    seen_t = {_norm(t) for t in (seen_titles or [])}

    head, terms = _headline(topic), _key_terms(topic, 4)
    for angle, suffix in CONTEXT_ANGLES:
        if angle in used:
            continue
        # context angles (background, history, precedent) are meant to reach
        # back in time, so no freshness window here — items carry their date.
        # The full headline is tried first; its key terms when that is too
        # narrow to match anything beyond the article itself.
        items = search_news(f"{head} {suffix}", limit=10, recent_days=None)
        if len(items) < 3 and terms:
            items += search_news(f"{terms} {suffix}", limit=10, recent_days=None)
        fresh = [it for it in items
                 if it["url"] and it["url"] not in seen_u
                 and _norm(it["title"]) not in seen_t]
        # collapse near-duplicate titles within this batch
        deduped, batch_seen = [], set()
        for it in fresh:
            n = _norm(it["title"])
            if n in batch_seen:
                continue
            batch_seen.add(n)
            deduped.append(it)
            if len(deduped) >= 4:
                break
        if deduped:
            return {"angle": angle, "label": angle.title(), "items": deduped}
    return {"angle": None, "label": None, "items": []}


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (title or "").lower()).strip()


def fetch_concurrently(queries: list[str], limit: int = 5) -> list[dict]:
    """Run several searches in parallel and flatten (used for intelligence)."""
    results: list[dict] = []
    if not queries:
        return results
    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
        for batch in pool.map(lambda q: search_news(q, limit=limit), queries):
            results.extend(batch)
    return results
