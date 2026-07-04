"""Keyword-driven fresh-story discovery.

Automates the editor's manual workflow — type a keyword into Google, click
News, click "Past hour" — using Google News search RSS with the `when:1h`
operator. Keywords come from two live signals:

1. Hot terms from the monitored X accounts (the X desk's kept tweets over the
   last 30 minutes, volume-ranked, trust-weighted), and
2. Google Trends India real-time trending searches (the "X trends" proxy that
   works without any X API).

Every article found this way carries `discovered_via=<keyword>`, which the
scoring engine converts into Search Trend Momentum points with the keyword
cited as evidence.
"""

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from app import config, db
from app.news.models import RawArticle

log = logging.getLogger("newsroom.discovery")

RESULTS_PER_KEYWORD = 4
SEARCH_TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"}

# generic terms that spike constantly but identify no story
_TERM_BLOCKLIST = {
    "watch", "video", "visuals", "sources", "officials", "statement", "confirms",
    "report", "reports", "reporter", "press", "conference", "ministry", "minister",
    "police", "court", "government", "announced", "update", "developing", "alert",
    "against", "because", "before", "during", "between", "another", "several",
    "breaking", "expected", "underway", "shortly", "tracking", "notified",
}


def x_desk_hot_terms(limit: int = 12) -> list[dict]:
    """Volume-ranked terms from kept tweets in the recent window, weighted by
    handle trust so wire/official accounts count more than low-trust ones."""
    since = (datetime.now(timezone.utc)
             - timedelta(minutes=config.X_TERM_WINDOW_MIN)).isoformat()
    weights: Counter = Counter()
    with db.connect() as con:
        rows = con.execute(
            "SELECT terms, trust_score, news_signal FROM tweets "
            "WHERE discarded=0 AND created_at >= ?",
            (since,),
        ).fetchall()
    for r in rows:
        weight = r["trust_score"] / 100 * (1.5 if r["news_signal"] else 1.0)
        for term in json.loads(r["terms"]):
            if term not in _TERM_BLOCKLIST and len(term) > 4:
                weights[term] += weight
    return [{"term": t, "weight": round(w, 1), "origin": "x-desk"}
            for t, w in weights.most_common(limit) if w >= 2]


def google_trends_terms(limit: int = 10) -> list[dict]:
    """Real-time trending searches for India via the public Trends RSS."""
    try:
        resp = httpx.get(config.GOOGLE_TRENDS_RSS, timeout=SEARCH_TIMEOUT,
                         follow_redirects=True, headers=_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        return [{"term": e.title.strip(), "weight": limit - i, "origin": "google-trends"}
                for i, e in enumerate(feed.entries[:limit]) if e.get("title")]
    except Exception as exc:
        log.warning("google trends fetch failed: %s", exc)
        return []


def collect_keywords() -> list[dict]:
    """Merged keyword list from three live pools with reserved slots (capped at
    DISCOVERY_KEYWORDS):

    - live-tv (up to 3): topics rival channels are airing that our board is
      missing — the strongest freshness signal, taken first;
    - x-desk (up to 3): hot terms from the monitored X accounts;
    - google-trends (up to 2): real-time trending searches.

    Any unused slots are backfilled from the pools in that same priority order.
    """
    from app.news.live_monitor import live_hot_terms  # lazy: avoid import cycle

    live_slots, x_slots, trends_slots = 3, 3, 2

    seen: set[str] = set()
    merged: list[dict] = []

    def take(pool: list[dict], count: int) -> None:
        for kw in pool:
            if count <= 0:
                return
            key = kw["term"].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(kw)
            count -= 1

    live_terms = live_hot_terms()
    x_terms = x_desk_hot_terms()
    trend_terms = google_trends_terms()
    take(live_terms, live_slots)
    take(x_terms, x_slots)
    take(trend_terms, trends_slots)
    # backfill remaining capacity in priority order
    take(live_terms + x_terms + trend_terms,
         config.DISCOVERY_KEYWORDS - len(merged))
    return merged


def _search_past_hour(keyword: dict) -> list[RawArticle]:
    url = config.NEWS_SEARCH_URL.format(query=quote_plus(keyword["term"]))
    resp = httpx.get(url, timeout=SEARCH_TIMEOUT, follow_redirects=True,
                     headers=_HEADERS)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    articles = []
    for entry in feed.entries[:RESULTS_PER_KEYWORD]:
        title = (entry.get("title") or "").strip()
        publisher = ""
        if " - " in title:  # Google News formats titles as "Headline - Publisher"
            title, publisher = title.rsplit(" - ", 1)
        if not publisher:
            publisher = (entry.get("source") or {}).get("title", "")
        published = entry.get("published_parsed")
        published_iso = (datetime(*published[:6], tzinfo=timezone.utc).isoformat()
                         if published else datetime.now(timezone.utc).isoformat())
        articles.append(RawArticle(
            title=title,
            url=entry.get("link", ""),
            publisher=publisher.strip(),
            published_at=published_iso,
            summary=entry.get("summary", ""),
            source_rank=0,  # discovery hits lead their clusters
            discovered_via=keyword["term"],
        ))
    return articles


def fetch_discovery_articles() -> tuple[list[RawArticle], dict]:
    """Search Google News 'past hour' for every collected keyword."""
    keywords = collect_keywords()
    pooled: list[RawArticle] = []
    failed = 0
    if keywords:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_search_past_hour, kw): kw for kw in keywords}
            for fut in as_completed(futures):
                try:
                    pooled.extend(fut.result())
                except Exception as exc:
                    failed += 1
                    log.warning("search failed for %r: %s",
                                futures[fut]["term"], exc)
    stats = {
        "keywords_searched": len(keywords),
        "keywords": [k["term"] for k in keywords],
        "keyword_origins": {k["term"]: k["origin"] for k in keywords},
        "discovery_hits": len(pooled),
        "searches_failed": failed,
    }
    return pooled, stats
