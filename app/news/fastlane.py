"""Breaking-news fast lane — detection decoupled from ranking.

The full ingest cycle enriches and scores every one of ~200 clustered
candidates, which is why it takes about two minutes and cannot be run often.
But the expensive half is the enrichment, not the looking: pulling all 50
feeds takes about four seconds and the keyword lane about two more.

So this module does only the cheap half, and does it often. It pulls the
same sources, clusters them, and asks one question of each cluster that is
NOT already on the board: does this look like something breaking right now?
That judgement is made from the headline, the number of outlets carrying it
and how recently it was filed — no network calls per story, no model calls,
no enrichment. Anything over the bar is written to `fast_signals`, which the
Alerts page reads, so an editor sees it within one scan interval of it
reaching the wires.

The strongest few are then promoted: enriched, scored and persisted through
the ordinary pipeline, so they arrive on the Story Desk with real scores and
guardrails rather than as a second-class row. Enrichment of three stories is
affordable in a way that enrichment of two hundred is not.

What it deliberately does NOT do: invent urgency. A single outlet filing a
headline with an alarming verb is not breaking news, and the scoring below
is weighted so corroboration — several outlets independently filing the same
event within minutes — is the signal that actually moves it over the bar.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app import config, db, events
from app.news import guardrails, scoring
from app.news import enrich as enrich_mod

log = logging.getLogger("newsroom.fastlane")

# How far back a story may have been filed and still count as "right now".
# The Conrad Hotel story was 37 minutes old across three outlets before the
# board had it at all, so a window shorter than this would miss the case
# that prompted the lane.
FAST_WINDOW_MIN = 90

# Urgency needed to raise an alert, and (higher) to pull the story onto the
# board in the same scan. Both on the 0-100 scale computed below.
ALERT_FLOOR = 45
PROMOTE_FLOOR = 62
MAX_PROMOTIONS = 4        # enrichment budget per scan

# Pull more per feed than the ranking cycle does. Corroboration is the main
# urgency signal, and a fast-moving outlet pushes a story past position 5 of
# its feed within minutes — at 5 items only one outlet carried the Conrad
# story; deeper, the Indian Express and TOI takes clustered together.
FAST_ITEMS_PER_FEED = 20

# Vocabulary of events that are, by their nature, reported as they happen.
# Weighted: a death toll is a stronger breaking signal than a resignation.
_HARD_RE = re.compile(
    r"\b(killed|kills|dead|dies|died|death toll|blast|explosion|bomb|"
    r"shot dead|stabbed|electrocut\w*|drown\w*|crash(?:es|ed)?|derail\w*|"
    r"collapse[sd]?|stampede|fire breaks out|gutted|hijack\w*|"
    r"earthquake|tsunami|cloudburst|landslide|flash flood)\b", re.I)
_SOFT_RE = re.compile(
    r"\b(arrested|detained|resigns?|quits?|sacked|verdict|convicted|"
    r"acquitted|raid(?:s|ed)?|summoned|booked|injured|hospitalised|"
    r"evacuat\w*|rescued|suspended|banned|recalled|grounded)\b", re.I)
# Markers that a headline is commentary about an event, not the event
_ANALYSIS_RE = re.compile(
    r"\b(explained|explainer|analysis|opinion|editorial|what we know|"
    r"timeline|in pictures|watch:|full text|highlights|preview|review|"
    r"live updates?|key takeaways|here's what|all you need to know)\b", re.I)


def _signal_key(title: str) -> str:
    """Stable id for a signal, so repeated scans update rather than duplicate."""
    from app.news.ingest import _dedup_key
    return _dedup_key(title)


def urgency(raw, outlets: int, now: datetime) -> tuple[int, list[str]]:
    """Score 0-100 for "is this breaking right now", with its reasons.

    Returns the reasons too, because an alert an editor cannot interrogate is
    an alert they learn to ignore. Every point is traceable to a sentence the
    Alerts page can show.
    """
    points, why = 0, []

    # 1. Corroboration — the load-bearing signal. Several outlets filing the
    #    same event inside the window is very hard to fake and very easy to
    #    verify, which is exactly what a desk wants before it reacts.
    if outlets >= 4:
        points += 40; why.append(f"{outlets} outlets carrying it")
    elif outlets == 3:
        points += 32; why.append("3 outlets carrying it")
    elif outlets == 2:
        points += 22; why.append("2 outlets carrying it")
    else:
        points += 4; why.append("single outlet so far")

    # 2. Recency inside the window.
    try:
        age_min = (now - datetime.fromisoformat(raw.published_at)).total_seconds() / 60
    except (ValueError, TypeError):
        age_min = FAST_WINDOW_MIN
    age_min = max(0.0, age_min)
    if age_min <= 15:
        points += 26; why.append("filed in the last 15 minutes")
    elif age_min <= 30:
        points += 20; why.append("filed in the last half hour")
    elif age_min <= 60:
        points += 12; why.append("filed within the hour")
    else:
        points += 4; why.append(f"filed {int(age_min)} minutes ago")

    # 3. What kind of event the headline describes.
    title = raw.title or ""
    if _HARD_RE.search(title):
        points += 22; why.append("casualty or incident wording")
    elif _SOFT_RE.search(title):
        points += 12; why.append("enforcement or official-action wording")

    # 4. Where it came from. An Indian outlet high in the source matrix filing
    #    this is worth more to an Indian desk than a foreign wire's version.
    if getattr(raw, "source_country", "") == "IN":
        points += 6; why.append("Indian outlet")
    if getattr(raw, "source_rank", 99) <= 10:
        points += 5; why.append("top-ranked source")

    # 5. Surfaced by a live trending keyword rather than a routine feed poll.
    if getattr(raw, "discovered_via", ""):
        points += 8; why.append(f"trending on “{raw.discovered_via}”")

    # Penalty: commentary about an event is not the event. Applied last so it
    # can pull an otherwise-strong-looking explainer back under the bar.
    if _ANALYSIS_RE.search(title):
        points -= 30; why.append("reads as analysis or a follow-up, not the event")

    return max(0, min(100, points)), why


def _on_board(con, title: str) -> bool:
    """Is this story already on the Story Desk, exactly or as the same event?"""
    from app.news.ingest import _dedup_key, _sig_tokens, _same_event
    if con.execute("SELECT 1 FROM stories WHERE dedup_key = ?",
                   (_dedup_key(title),)).fetchone():
        return True
    tokens = _sig_tokens(title)
    if len(tokens) < 2:
        return False
    for row in con.execute("SELECT title FROM stories WHERE active = 1").fetchall():
        if _same_event(tokens, _sig_tokens(row["title"] or "")):
            return True
    return False


def run_fast_scan(promote: bool = True) -> dict:
    """One cheap pass over the wires. Safe to run every couple of minutes."""
    from app.news import discovery as discovery_mod
    from app.news import sources as sources_mod
    from app.news import ingest as ingest_mod

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cutoff = (now - timedelta(minutes=FAST_WINDOW_MIN)).isoformat()

    # Pull both lanes. A failure in one must not cost us the other: during
    # breaking news a partial scan beats no scan.
    pool, stats = [], {"feeds_failed": 0}
    try:
        discovered, disco_stats = discovery_mod.fetch_discovery_articles()
        pool += discovered
        stats["discovery_hits"] = len(discovered)
        stats["keywords"] = disco_stats.get("keywords", [])
    except Exception as exc:
        stats["discovery_error"] = str(exc)
    try:
        original = sources_mod.ITEMS_PER_FEED
        sources_mod.ITEMS_PER_FEED = FAST_ITEMS_PER_FEED
        try:
            matrix, matrix_stats = sources_mod.fetch_matrix_articles()
        finally:
            sources_mod.ITEMS_PER_FEED = original
        pool += matrix
        stats["items_pooled"] = matrix_stats.get("items_pooled", 0)
        stats["feeds_failed"] = matrix_stats.get("feeds_failed", 0)
    except Exception as exc:
        stats["matrix_error"] = str(exc)

    if not pool:
        return {"ok": False, "error": "no sources returned", **stats}

    clustered = sources_mod._cluster(pool)
    fresh = [a for a in clustered
             if (a.published_at or "") >= cutoff
             and not ingest_mod.is_junk_title(a.title)]
    stats["candidates"] = len(fresh)

    # Score everything first. Promotion has a hard enrichment budget, so the
    # candidates have to be ranked before any of it is spent — scoring as we
    # go and promoting the first four over the line spends the whole budget
    # on whatever the feeds happened to return first.
    scored, seen_keys = [], set()
    for raw in fresh:
        key = _signal_key(raw.title)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        outlets = len(set(raw.corroborators)
                      | set(ingest_mod._related_publishers(raw.summary))
                      | {raw.publisher}) if raw.publisher else len(raw.corroborators)
        score, why = urgency(raw, outlets, now)
        if score >= ALERT_FLOOR:
            scored.append((score, outlets, why, key, raw))
    scored.sort(key=lambda row: -row[0])

    raised, promoted = [], []
    with db.connect() as con:
        for score, outlets, why, key, raw in scored:
            on_board = _on_board(con, raw.title)
            existing = con.execute(
                "SELECT urgency, promoted FROM fast_signals WHERE signal_key = ?",
                (key,)).fetchone()

            if existing:
                # Keep the running peak: a story that gains outlets between
                # scans should climb, never silently fall back.
                con.execute(
                    "UPDATE fast_signals SET urgency = ?, outlets = ?, reasons = ?, "
                    "last_seen = ?, on_board = ? WHERE signal_key = ?",
                    (max(score, existing["urgency"]), outlets, json.dumps(why),
                     now_iso, int(on_board), key))
            else:
                con.execute(
                    "INSERT INTO fast_signals (signal_key, title, url, publisher, "
                    "published_at, outlets, urgency, reasons, first_seen, last_seen, "
                    "on_board, promoted) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                    (key, raw.title, raw.url, raw.publisher, raw.published_at,
                     outlets, score, json.dumps(why), now_iso, now_iso, int(on_board)))
                raised.append({"title": raw.title, "urgency": score,
                               "publisher": raw.publisher, "outlets": outlets,
                               "why": why, "on_board": on_board})

            # Promotion: pull the strongest ones onto the board now, through
            # the ordinary enrich -> score -> guardrail path so they are
            # indistinguishable from stories the full cycle found.
            already_promoted = bool(existing and existing["promoted"])
            if (promote and not on_board and not already_promoted
                    and score >= PROMOTE_FLOOR and len(promoted) < MAX_PROMOTIONS):
                try:
                    story = enrich_mod.enrich(raw)
                    story.sources = sorted(
                        set(story.sources) | set(raw.corroborators)
                        | set(ingest_mod._related_publishers(raw.summary)))
                    sc = scoring.score_story(story)
                    conf = scoring.compute_confidence(story, sc)
                    verdict = guardrails.apply_guardrails(story, sc, conf)
                    story_id, is_new = ingest_mod._persist_story(
                        con, story, sc, conf, verdict, now_iso)
                    con.execute(
                        "UPDATE fast_signals SET promoted = 1, story_id = ? "
                        "WHERE signal_key = ?", (story_id, key))
                    promoted.append({"title": raw.title, "story_id": story_id,
                                     "score": sc["total"], "new": is_new})
                except Exception as exc:
                    # A story that cannot be enriched still stands as an alert.
                    log.warning("fast promote failed for %r: %s", raw.title[:60], exc)

    if promoted:
        ingest_mod.publish_rundown()
    for sig in raised:
        events.publish("fast_signal", sig)

    # Standing searches ride the same cadence as the breaking watch: a story
    # an editor asked to follow should update as often as the board does.
    try:
        from app.news import tracker
        stats["tracking"] = tracker.check_all()
    except Exception as exc:          # tracking must never cost us a scan
        log.warning("tracked-story check failed: %s", exc)
        stats["tracking"] = {"error": str(exc)}

    result = {"ok": True, "at": now_iso, "raised": len(raised),
              "promoted": len(promoted), "promotions": promoted,
              "top": sorted(raised, key=lambda s: -s["urgency"])[:5], **stats}
    LAST_SCAN.clear()
    LAST_SCAN.update(result)
    return result


LAST_SCAN: dict = {}


def recent_signals(minutes: int = 180, limit: int = 40) -> list[dict]:
    """Fast signals for the Alerts page, newest first."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM fast_signals WHERE first_seen >= ? "
            "ORDER BY first_seen DESC LIMIT ?", (since, limit)).fetchall()
    out = []
    for r in db.rows_to_dicts(rows):
        try:
            r["reasons"] = json.loads(r.get("reasons") or "[]")
        except (ValueError, TypeError):
            r["reasons"] = []
        out.append(r)
    return out
