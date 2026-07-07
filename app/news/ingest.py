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

LAST_STATS: dict = {}  # most recent cycle stats, for the Ops page

# Non-news recurring content never belongs on an editorial rundown
_JUNK_TOPIC_RE = re.compile(
    r"lottery|horoscope|panchang|numerolog|astrolog|word of the day|"
    r"wordle|crossword|gold rate|silver rate|petrol.{0,12}price today|quiz",
    re.I,
)

# Bulletin/listing formats: recurring slots, not standalone headlines
_JUNK_BULLETIN_RE = re.compile(
    r"\bnews bulletin\b|\btop headlines\b|\bheadlines of the day\b|"
    r"\bmorning headlines\b|\bevening bulletin\b|\bnews wrap\b|\bnewswrap\b|"
    r"\bas it happened\b|\bin pics\b|\bin pictures\b|\bphotos of the day\b|"
    r"\bpodcast\b|\bepisode \d+\b|\bdaily briefing\b|\blive blog\b|\be-?paper\b",
    re.I,
)

# Live-blog wrapper titles ("World News Today Live Updates on July 7, 2026 : …")
# are rolling desk pages, not stories — the buried item after the colon is
# whatever the desk last touched, so the whole title is junk.
_JUNK_LIVEBLOG_RE = re.compile(
    r"\blive updates?\s+(on|for)?\s*"
    r"(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+\d{1,2}",
    re.I,
)

# Structural junk detection: date/time-stamped titles with little substance
_MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}
_WEEKDAY_NAMES = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
_DATE_TIME_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES) + r")\b|\b\d{1,2}[:.]\d{2}\b|\b(am|pm)\b|\b20\d{2}\b",
    re.I,
)
_JUNK_STOPWORDS = {
    "the", "and", "for", "with", "from", "news", "live", "updates", "today", "latest",
}
_token_re = re.compile(r"[^a-z0-9 ]")


def _substantive_tokens(title: str) -> list[str]:
    words = _token_re.sub("", title.lower()).split()
    return [
        w for w in words
        if w.isalpha() and len(w) >= 3
        and w not in _JUNK_STOPWORDS
        and w not in _MONTH_NAMES
        and w not in _WEEKDAY_NAMES
        and w not in ("am", "pm")
    ]


def is_junk_title(title: str) -> bool:
    """True when a title is recurring non-news content or a bulletin/listing
    slot (bulletin name, timestamp, brand) rather than an actual headline."""
    if _JUNK_TOPIC_RE.search(title):
        return True
    if _JUNK_BULLETIN_RE.search(title):
        return True
    if _JUNK_LIVEBLOG_RE.search(title):
        return True
    if _DATE_TIME_RE.search(title) and len(_substantive_tokens(title)) < 3:
        return True
    return False


def _dedup_key(title: str) -> str:
    norm = _norm_re.sub("", title.lower())
    tokens = sorted(set(norm.split()))
    return hashlib.sha1(" ".join(tokens).encode()).hexdigest()


# Near-duplicate (same-event) detection. The exact dedup_key above only merges
# identical word-sets, so three differently-worded takes on the same event
# ("Wayanad landslide kills 12", "Death toll rises in Wayanad landslide", …)
# each got their own board row. We collapse them by significant-token overlap.
_DEDUP_STOP = _JUNK_STOPWORDS | _MONTH_NAMES | _WEEKDAY_NAMES | {
    "over", "after", "amid", "into", "from", "with", "says", "said", "dead",
    "kills", "killed", "live", "video", "watch", "big", "top", "new", "how",
    "why", "what", "who", "amid", "case", "man", "day", "set", "get", "may",
    "will", "can", "not", "out", "now", "his", "her", "was", "are", "has",
    "amid", "as", "on", "in", "of", "to", "the", "and", "for",
}


def _sig_tokens(title: str) -> set[str]:
    """Distinctive tokens for same-event matching (place/entity words survive,
    filler drops)."""
    return {
        w for w in _token_re.sub("", (title or "").lower()).split()
        if w.isalpha() and len(w) >= 4 and w not in _DEDUP_STOP
    }


# Generic words that recur across *unrelated* stories, so two titles sharing
# only these (e.g. "Supreme Court" in a Yoon story and an Ayodhya story) are NOT
# the same event. Place names and proper nouns are deliberately absent — those
# are the distinctive tokens that actually identify a story.
_COMMON_EVENT_WORDS = {
    # institutions / roles
    "supreme", "court", "high", "minister", "president", "prime", "government",
    "govt", "police", "chief", "party", "leader", "official", "officials",
    "cabinet", "parliament", "assembly", "ministry", "department", "committee",
    "commission", "council", "board", "bench", "judge", "cops", "forces",
    "army", "troops", "centre", "state", "union", "opposition",
    # process / event nouns
    "report", "reports", "reported", "case", "probe", "alert", "meeting",
    "statement", "protest", "rally", "verdict", "ruling", "order", "notice",
    "hearing", "session", "plan", "scheme", "project", "deal", "talks", "visit",
    "event", "issue", "issues", "matter", "move", "moves", "poll", "polls",
    "vote", "result", "results", "update", "meet", "calls", "seeks", "files",
    "holds", "slams", "warns", "urges", "demands", "claims",
    # generic descriptors / scale
    "heavy", "massive", "major", "huge", "latest", "several", "feared",
    "trapped", "workers", "people", "death", "dies", "serious", "amid",
    "after", "rain", "rains", "flood", "floods", "weather", "tunnel",
}


def _same_event(a: set[str], b: set[str]) -> bool:
    """True when two titles are the same story: they share >=2 significant
    tokens AND at least one shared token is distinctive (a place/name/specific
    noun, not a generic institution or descriptor). This merges long,
    differently-worded takes on one event (they still share the place + subject)
    without collapsing unrelated stories that merely share "supreme court" etc."""
    if len(a) < 2 or len(b) < 2:
        return False
    shared = a & b
    if len(shared) < 2:
        return False
    return any(w not in _COMMON_EVENT_WORDS for w in shared)


def _collapse_duplicates(con) -> int:
    """Merge same-event stories already on the board into one row: keep the
    highest-scored take, fold the others' sources into it (so the survivor shows
    the corroboration that makes a big story look big), and retire the rest."""
    rows = db.rows_to_dicts(con.execute(
        "SELECT id, title, sources FROM stories WHERE active=1 "
        "ORDER BY score DESC, id ASC").fetchall())
    kept: list[dict] = []
    removed = 0
    for r in rows:
        toks = _sig_tokens(r["title"])
        survivor = next((k for k in kept
                         if len(toks) >= 2 and _same_event(toks, k["tokens"])), None)
        if survivor is None:
            kept.append({"id": r["id"], "tokens": toks,
                         "sources": set(r["sources"] or []), "grew": False})
        else:
            before = len(survivor["sources"])
            survivor["sources"] |= set(r["sources"] or [])
            survivor["grew"] = survivor["grew"] or len(survivor["sources"]) > before
            con.execute("UPDATE stories SET active=0 WHERE id=?", (r["id"],))
            removed += 1
    for k in kept:
        if k["grew"]:
            con.execute("UPDATE stories SET sources=? WHERE id=?",
                        (json.dumps(sorted(k["sources"])), k["id"]))
    return removed


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

    # No exact-title match: check whether this is another take on a story
    # already on the board (same event, different wording) and merge into it.
    if not existing:
        new_tokens = _sig_tokens(story.raw.title)
        if len(new_tokens) >= 2:
            for row in con.execute(
                    "SELECT id, title, sources, base_score, stale_cycles "
                    "FROM stories WHERE active=1").fetchall():
                if _same_event(new_tokens, _sig_tokens(row["title"])):
                    existing = row
                    break

    if existing:
        prev_sources = set(json.loads(existing["sources"]))
        merged = sorted(prev_sources | set(sources))
        developed = set(merged) != prev_sources or score["total"] > existing["base_score"]
        stale_cycles = 0 if developed else existing["stale_cycles"] + 1
        decay = guardrails.decay_for_stale_cycles(stale_cycles)
        _clamp = "GREATEST" if db.IS_PG else "MAX"
        con.execute(
            f"""UPDATE stories SET last_updated_at=?, sources=?, base_score=?,
               decay=?, score={_clamp}(0, ? + trend_boost - ?), confidence=?, status=?,
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

    # Non-news recurring content never belongs on an editorial rundown
    candidates = [a for a in fresh if not is_junk_title(a.title)]
    feed_stats["dropped_junk"] = len(fresh) - len(candidates)

    # Score every clustered candidate, keep the strongest for the rundown.
    # India Audience Fit is layered on top of the base framework: stories
    # rivals are airing rise, foreign filler with no India angle sinks.
    from app.news import audience
    scored = []
    for raw in candidates:
        story = enrich_mod.enrich(raw)
        story.sources = sorted(
            set(story.sources) | set(raw.corroborators)
            | set(_related_publishers(raw.summary))
        )
        score = scoring.score_story(story)
        try:
            entry = audience.audience_entry(raw.title, raw.summary)
            score["breakdown"].append(entry)
            score["total"] = min(100, score["total"] + entry["points"])
        except Exception:
            pass  # audience layer must never block the cycle
        scored.append((score, story))
    scored.sort(key=lambda pair: pair[0]["total"], reverse=True)
    articles = scored[: config.STORIES_PER_CYCLE]

    max_score = 0
    new_count = 0
    seen_ids = []
    flashes = []
    with db.connect() as con:
        for score, story in articles:
            confidence = scoring.compute_confidence(story, score)
            verdict = guardrails.apply_guardrails(story, score, confidence)
            story_id, is_new = _persist_story(con, story, score, confidence, verdict, now_iso)
            seen_ids.append(story_id)
            new_count += int(is_new)
            max_score = max(max_score, score["total"])
            if is_new and verdict["status"] == "breaking":
                flashes.append({"kind": "breaking", "story_id": story_id,
                                "title": story.raw.title})

        # retire anything published too long ago — an old story that keeps
        # re-clustering must not occupy the "latest" board indefinitely
        retire_cutoff = (now - timedelta(hours=config.RETIRE_HOURS)).isoformat()
        retired = con.execute(
            "UPDATE stories SET active=0 WHERE active=1 AND published_at < ?",
            (retire_cutoff,),
        ).rowcount
        feed_stats["retired"] = retired
        # sweep previously-ingested junk off the board too
        for row in con.execute("SELECT id, title FROM stories WHERE active=1"):
            if is_junk_title(row["title"]):
                con.execute("UPDATE stories SET active=0 WHERE id=?", (row["id"],))

        # collapse same-event duplicates already on the board into one row
        feed_stats["deduped"] = _collapse_duplicates(con)

        # stories not in this cycle age one stale cycle (repetitive decay) —
        # at most once per hour regardless of refresh cadence, capped total
        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            age_cutoff = (now - timedelta(minutes=55)).isoformat()
            # scalar clamps: SQLite MIN/MAX vs Postgres LEAST/GREATEST
            _lo = "LEAST" if db.IS_PG else "MIN"
            _hi = "GREATEST" if db.IS_PG else "MAX"
            con.execute(
                f"""UPDATE stories SET stale_cycles = stale_cycles + 1,
                    last_aged_at = ?,
                    decay = {_lo}(?, (stale_cycles + 1) * ?),
                    score = {_hi}(0, base_score + trend_boost
                                - {_lo}(?, (stale_cycles + 1) * ?))
                    WHERE active = 1 AND id NOT IN ({placeholders})
                    AND (last_aged_at IS NULL OR last_aged_at < ?)""",
                [now_iso, config.REPETITIVE_DECAY_CAP, config.REPETITIVE_DECAY_PER_HOUR,
                 config.REPETITIVE_DECAY_CAP, config.REPETITIVE_DECAY_PER_HOUR,
                 *seen_ids, age_cutoff],
            )

    publish_rundown()
    for flash in flashes:
        events.publish("flash", flash)
    result = {"ok": True, "last_ingest": now_iso, "stories": len(articles),
              "new_stories": new_count, "max_score": max_score, **feed_stats}
    LAST_STATS.clear()
    LAST_STATS.update(result)
    events.publish("system_status", {"state": "idle", "manual": manual, **result})
    return result


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
