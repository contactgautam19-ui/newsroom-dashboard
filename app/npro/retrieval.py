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


def search_news(query: str, limit: int = 8) -> list[dict]:
    """Recent articles matching a query (newest-first), keyless."""
    if not query.strip():
        return []
    try:
        resp = httpx.get(SEARCH_URL.format(q=quote_plus(query)), timeout=TIMEOUT,
                         follow_redirects=True, headers=_HEADERS)
        resp.raise_for_status()
        return _parse_entries(resp.content, limit)
    except (httpx.HTTPError, ValueError):
        return []


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

    for angle, suffix in CONTEXT_ANGLES:
        if angle in used:
            continue
        items = search_news(f"{topic} {suffix}", limit=10)
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
        for batch in pool.map(lambda q: search_news(q, limit), queries):
            results.extend(batch)
    return results
