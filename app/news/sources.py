"""Curated 50-source ingestion matrix (Newsroom_50_Source_Ingestion_Matrix).

Replaces the generic Google News top-stories pull with the ranked source list:
every active RSS feed is polled concurrently each cycle, the freshest items
are pooled, and near-identical headlines from different outlets are clustered
into a single story whose `sources` list carries every corroborating outlet —
which makes the two-source guardrail a genuine cross-outlet check.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import httpx

from app import config
from app.news.models import RawArticle

ITEMS_PER_FEED = 3
FEED_TIMEOUT = 8
MAX_WORKERS = 12
CLUSTER_MIN_SHARED = 3     # shared significant tokens to merge two headlines
CLUSTER_OVERLAP = 0.6      # ...or this fraction of the smaller token set

_word_re = re.compile(r"[^a-z0-9 ]")
_STOP = set(
    "the a an and or of in on to for with at by from as is are was be after amid"
    " over under his her their its says say said new live update updates india"
    " indian latest news today breaking".split()
)


def load_sources(active_only: bool = True) -> list[dict]:
    path = config.DATA_DIR / "news_sources.json"
    if not path.exists():
        return []
    sources = json.loads(path.read_text(encoding="utf-8"))
    return [s for s in sources if s["active"]] if active_only else sources


def _fetch_feed(source: dict) -> list[RawArticle]:
    resp = httpx.get(
        source["rss_url"], timeout=FEED_TIMEOUT, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"},
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    articles = []
    for entry in feed.entries[:ITEMS_PER_FEED]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        published_iso = (datetime(*published[:6], tzinfo=timezone.utc).isoformat()
                         if published else datetime.now(timezone.utc).isoformat())
        articles.append(RawArticle(
            title=title,
            url=entry.get("link", ""),
            publisher=source["source"],
            published_at=published_iso,
            summary=entry.get("summary", ""),
            category_hint=source.get("category_hint", ""),
            source_rank=source.get("rank", 99),
        ))
    return articles


def fetch_matrix_articles() -> tuple[list[RawArticle], dict]:
    """Poll all active feeds concurrently. Returns (clustered articles, stats)."""
    sources = load_sources()
    pooled: list[RawArticle] = []
    failed: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_feed, s): s for s in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                pooled.extend(fut.result())
            except Exception:
                failed.append(src["source"])

    clustered = _cluster(pooled)
    stats = {
        "feeds_polled": len(sources),
        "feeds_failed": len(failed),
        "failed_sources": failed[:10],
        "items_pooled": len(pooled),
        "stories_clustered": len(clustered),
    }
    return clustered, stats


def _sig_tokens(title: str) -> set[str]:
    return {w for w in _word_re.sub("", title.lower()).split()
            if len(w) > 3 and w not in _STOP}


def _same_story(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    return shared >= CLUSTER_MIN_SHARED or shared / min(len(a), len(b)) >= CLUSTER_OVERLAP


def _cluster(articles: list[RawArticle]) -> list[RawArticle]:
    """Merge near-identical headlines across outlets; union their publishers."""
    clusters: list[tuple[set[str], RawArticle]] = []
    for art in sorted(articles, key=lambda x: x.source_rank):
        tokens = _sig_tokens(art.title)
        for c_tokens, lead in clusters:
            if _same_story(tokens, c_tokens):
                for pub in (art.corroborators or [art.publisher]):
                    if pub and pub not in lead.corroborators:
                        lead.corroborators.append(pub)
                if len(art.summary) > len(lead.summary):
                    lead.summary = art.summary
                if art.published_at < lead.published_at:
                    lead.published_at = art.published_at
                if art.discovered_via and not lead.discovered_via:
                    lead.discovered_via = art.discovered_via
                c_tokens |= tokens
                break
        else:
            if not art.corroborators:
                art.corroborators = [art.publisher]
            clusters.append((tokens, art))
    return [lead for _, lead in clusters]
