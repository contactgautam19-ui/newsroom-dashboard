"""Google News RSS ingestion — top stories, top-of-the-hour cycle.

Google News item summaries embed a list of related coverage with publisher
names; those are harvested into `sources` to power the two-source rule.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from app import config, db, events
from app.news import enrich as enrich_mod
from app.news import guardrails, scoring
from app.news.models import RawArticle

_norm_re = re.compile(r"[^a-z0-9 ]+")


def _dedup_key(title: str) -> str:
    norm = _norm_re.sub("", title.lower())
    tokens = sorted(set(norm.split()))
    return hashlib.sha1(" ".join(tokens).encode()).hexdigest()


def _related_publishers(summary_html: str) -> list[str]:
    """Publisher names from the 'related coverage' font tags in GN summaries."""
    return re.findall(r'<font color="#6f6f6f">([^<]+)</font>', summary_html or "")


def fetch_top_articles(limit: int) -> list[RawArticle]:
    resp = httpx.get(
        config.NEWS_RSS_URL,
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"},
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    articles = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "").strip()
        # GN titles are formatted "Headline - Publisher"
        publisher = ""
        if " - " in title:
            title, publisher = title.rsplit(" - ", 1)
        if not publisher:
            publisher = (entry.get("source") or {}).get("title", "")

        published = entry.get("published_parsed")
        if published:
            published_iso = datetime(*published[:6], tzinfo=timezone.utc).isoformat()
        else:
            published_iso = datetime.now(timezone.utc).isoformat()

        articles.append(RawArticle(
            title=title,
            url=entry.get("link", ""),
            publisher=publisher.strip(),
            published_at=published_iso,
            summary=entry.get("summary", ""),
        ))
    return articles


def _persist_story(con, story, score, confidence, verdict, now_iso) -> tuple[int, bool]:
    """Insert or refresh a story. Returns (story_id, is_new)."""
    key = _dedup_key(story.raw.title)
    flags = {k: v for k, v in story.flags.items() if not k.startswith("_")}
    media = {k: v for k, v in story.media.items() if not k.startswith("_")}
    sources = sorted(set(story.sources))
    trend_base = next((b["points"] for b in score["breakdown"]
                       if b["variable"] == "trend"), 0)
    discovered_via = story.raw.discovered_via or None

    existing = con.execute(
        "SELECT id, sources, base_score, stale_cycles FROM stories WHERE dedup_key = ?",
        (key,),
    ).fetchone()

    if existing:
        prev_sources = set(json.loads(existing["sources"]))
        merged = sorted(prev_sources | set(sources))
        developed = set(merged) != prev_sources or score["total"] > existing["base_score"]
        stale_cycles = 0 if developed else existing["stale_cycles"] + 1
        decay = guardrails.decay_for_stale_cycles(stale_cycles)
        con.execute(
            """UPDATE stories SET last_updated_at=?, sources=?, base_score=?,
               decay=?, score=MAX(0, ? + trend_boost - ?), confidence=?, status=?,
               needs_review=?, stale_cycles=?, trend_base=?,
               discovered_via=COALESCE(?, discovered_via), active=1 WHERE id=?""",
            (now_iso, json.dumps(merged), score["total"], decay,
             score["total"], decay, confidence, verdict["status"],
             int(verdict["needs_review"]), stale_cycles, trend_base,
             discovered_via, existing["id"]),
        )
        story_id = existing["id"]
        is_new = False
    else:
        cur = con.execute(
            """INSERT INTO stories (dedup_key, title, url, publisher, published_at,
               first_seen_at, last_updated_at, location, category, flags, media,
               sources, base_score, score, confidence, status, needs_review,
               trend_base, discovered_via)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, story.raw.title, story.raw.url, story.raw.publisher,
             story.raw.published_at, now_iso, now_iso, story.location,
             story.category, json.dumps(flags), json.dumps(media),
             json.dumps(sources), score["total"], score["total"], confidence,
             verdict["status"], int(verdict["needs_review"]),
             trend_base, discovered_via),
        )
        story_id = cur.lastrowid
        is_new = True

    for b in score["breakdown"]:
        # keep any broker-applied trend evidence on refresh
        if not is_new and b["variable"] == "trend":
            continue
        con.execute(
            """INSERT INTO score_breakdowns (story_id, variable, max_points, points, evidence)
               VALUES (?,?,?,?,?)
               ON CONFLICT(story_id, variable) DO UPDATE SET
               max_points=excluded.max_points, points=excluded.points,
               evidence=excluded.evidence""",
            (story_id, b["variable"], b["max_points"], b["points"],
             json.dumps(b["evidence"])),
        )
    return story_id, is_new


def run_ingest_cycle(manual: bool = False) -> dict:
    """One full ingest -> enrich -> score -> guardrail -> persist cycle.

    Two discovery lanes, clustered together:
    1. Keyword lane (primary freshness signal): hot terms from the monitored X
       accounts + live trending searches, each run through a past-hour Google
       News search — the automated version of the editor's manual workflow.
    2. The curated 50-source ingestion matrix for corroboration and coverage.

    Anything older than FRESHNESS_HOURS is dropped before ranking so stale
    evergreen items can't occupy the rundown.
    """
    from app.news import discovery as discovery_mod
    from app.news import sources as sources_mod

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    events.publish("system_status", {"state": "ingesting", "at": now_iso})

    feed_stats = {}
    try:
        discovered, disco_stats = discovery_mod.fetch_discovery_articles()
        feed_stats.update(disco_stats)
        if sources_mod.load_sources():
            matrix_articles, matrix_stats = sources_mod.fetch_matrix_articles()
            feed_stats.update(matrix_stats)
        else:
            matrix_articles = fetch_top_articles(config.STORIES_PER_CYCLE)
        # discovery hits first so they lead their clusters and keep the keyword
        candidates = sources_mod._cluster(discovered + matrix_articles)
    except Exception as exc:
        events.publish("system_status", {"state": "ingest_error", "error": str(exc)})
        return {"ok": False, "error": str(exc), "stories": 0, "max_score": 0}

    # Freshness gate: the whole point of the keyword lane is *latest* stories
    cutoff = (now - timedelta(hours=config.FRESHNESS_HOURS)).isoformat()
    fresh = [a for a in candidates if a.published_at >= cutoff]
    feed_stats["dropped_stale"] = len(candidates) - len(fresh)
    candidates = fresh

    # Score every clustered candidate, keep the strongest for the rundown
    scored = []
    for raw in candidates:
        story = enrich_mod.enrich(raw)
        story.sources = sorted(
            set(story.sources) | set(raw.corroborators)
            | set(_related_publishers(raw.summary))
        )
        scored.append((scoring.score_story(story), story))
    scored.sort(key=lambda pair: pair[0]["total"], reverse=True)
    articles = scored[: config.STORIES_PER_CYCLE]

    max_score = 0
    new_count = 0
    seen_ids = []
    with db.connect() as con:
        for score, story in articles:
            confidence = scoring.compute_confidence(story, score)
            verdict = guardrails.apply_guardrails(story, score, confidence)
            story_id, is_new = _persist_story(con, story, score, confidence, verdict, now_iso)
            seen_ids.append(story_id)
            new_count += int(is_new)
            max_score = max(max_score, score["total"])

        # retire anything published too long ago — an old story that keeps
        # re-clustering must not occupy the "latest" board indefinitely
        retire_cutoff = (now - timedelta(hours=config.RETIRE_HOURS)).isoformat()
        retired = con.execute(
            "UPDATE stories SET active=0 WHERE active=1 AND published_at < ?",
            (retire_cutoff,),
        ).rowcount
        feed_stats["retired"] = retired

        # stories not in this cycle age one stale cycle (repetitive decay)
        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            con.execute(
                f"""UPDATE stories SET stale_cycles = stale_cycles + 1,
                    decay = (stale_cycles + 1) * ?,
                    score = MAX(0, base_score + trend_boost - (stale_cycles + 1) * ?)
                    WHERE active = 1 AND id NOT IN ({placeholders})""",
                [config.REPETITIVE_DECAY_PER_HOUR, config.REPETITIVE_DECAY_PER_HOUR, *seen_ids],
            )

    publish_rundown()
    events.publish("system_status", {
        "state": "idle", "last_ingest": now_iso, "new_stories": new_count,
        "max_score": max_score, "manual": manual, **feed_stats,
    })
    return {"ok": True, "stories": len(articles), "new": new_count,
            "max_score": max_score, **feed_stats}


def get_rundown(limit: int = 12) -> list[dict]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM stories WHERE active=1 ORDER BY score DESC, last_updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        rundown = db.rows_to_dicts(rows)
        for story in rundown:
            br = con.execute(
                "SELECT variable, max_points, points, evidence FROM score_breakdowns WHERE story_id=?",
                (story["id"],),
            ).fetchall()
            story["breakdown"] = db.rows_to_dicts(br)
    return rundown


def publish_rundown() -> None:
    events.publish("rundown", get_rundown())
