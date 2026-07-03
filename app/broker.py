"""The X Conversation Broker — real-time trend velocity layer.

Every 60s it rebuilds a term-volume dictionary from scraped tweets, computes a
Post Velocity Gradient over a rolling 5-minute window, and when a term matching
an active Google News headline spikes >150%, classifies a Viral Acceleration
Event. The event bridges back into the news panel: a +1..+10 dynamic offset on
that story's Search Trend Momentum score (capped at 15), and — past 5,000
posts/hour — a confidence override to "High-Demand Airtime Recommendation".
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from app import config, db, events

_word_re = re.compile(r"[^a-z0-9 ]")


def _headline_terms(title: str) -> set[str]:
    from app.x.sim_provider import STOPWORDS
    return {w for w in _word_re.sub("", title.lower()).split()
            if len(w) > 4 and w not in STOPWORDS}


def _term_counts_between(con, start_iso: str, end_iso: str) -> Counter:
    counts: Counter = Counter()
    rows = con.execute(
        "SELECT terms FROM tweets WHERE created_at >= ? AND created_at < ?",
        (start_iso, end_iso),
    ).fetchall()
    for r in rows:
        for term in json.loads(r["terms"]):
            counts[term] += 1
    return counts


def run_broker_tick() -> list[dict]:
    """One aggregation pass. Returns the velocity events fired."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=config.SPIKE_WINDOW_SECONDS)
    recent_start = now - timedelta(seconds=config.BROKER_TICK_SECONDS)

    fired: list[dict] = []
    with db.connect() as con:
        recent = _term_counts_between(con, recent_start.isoformat(), now.isoformat())
        baseline_window = _term_counts_between(
            con, window_start.isoformat(), recent_start.isoformat()
        )
        if not recent:
            return fired

        baseline_minutes = max(
            1, (config.SPIKE_WINDOW_SECONDS - config.BROKER_TICK_SECONDS) / 60
        )

        stories = con.execute(
            "SELECT id, title, base_score, trend_boost, decay, confidence, "
            "needs_review, trend_base, discovered_via FROM stories WHERE active=1"
        ).fetchall()

        boosted_story_ids = set()
        for term, count in recent.most_common(40):
            if count < 3:
                continue
            per_min_baseline = baseline_window.get(term, 0) / baseline_minutes
            if per_min_baseline > 0:
                velocity_pct = (count / per_min_baseline - 1) * 100
            else:
                velocity_pct = 100.0 * count  # brand-new term: velocity by volume

            if velocity_pct <= config.SPIKE_THRESHOLD_PCT:
                continue

            window_total = count + baseline_window.get(term, 0)
            posts_per_hour = window_total * (3600 / config.SPIKE_WINDOW_SECONDS)

            # Match spiking term to an active headline
            story = None
            for s in stories:
                if s["id"] in boosted_story_ids:
                    continue
                if term in _headline_terms(s["title"]):
                    story = s
                    break
            if story is None:
                continue
            boosted_story_ids.add(story["id"])

            boost = max(config.TREND_BOOST_MIN,
                        min(config.TREND_BOOST_MAX, round(velocity_pct / 100)))
            high_demand = posts_per_hour > config.HIGH_DEMAND_POSTS_PER_HOUR
            _apply_boost(con, story, term, boost, velocity_pct, posts_per_hour,
                         high_demand, now.isoformat())

            event = {
                "term": term, "hashtag": f"#{term.capitalize()}",
                "velocity_pct": round(velocity_pct, 1),
                "posts_per_hour": round(posts_per_hour),
                "story_id": story["id"], "story_title": story["title"],
                "boost": boost, "high_demand": high_demand,
                "created_at": now.isoformat(),
            }
            con.execute(
                """INSERT INTO velocity_events (created_at, term, velocity_pct,
                   posts_per_hour, story_id, boost, high_demand)
                   VALUES (?,?,?,?,?,?,?)""",
                (now.isoformat(), term, velocity_pct, posts_per_hour,
                 story["id"], boost, int(high_demand)),
            )
            fired.append(event)

    for event in fired:
        events.publish("velocity_event", event)
    if fired:
        from app.news.ingest import publish_rundown
        publish_rundown()
    return fired


def _apply_boost(con, story, term, boost, velocity_pct, posts_per_hour,
                 high_demand, now_iso) -> None:
    # keep the strongest surge, but discovery base + boost never exceeds the
    # variable's 15-point ceiling
    trend_base = story["trend_base"] or 0
    headroom = max(0, config.TREND_MOMENTUM_CAP - trend_base)
    new_boost = min(headroom, max(story["trend_boost"], boost))
    needs_review = story["needs_review"]
    if high_demand and needs_review:
        needs_review = 0  # >5000/hr overrides the low-confidence hold

    con.execute(
        """UPDATE stories SET trend_boost=?, score=MAX(0, base_score + ? - decay),
           high_demand=?, needs_review=?, last_updated_at=? WHERE id=?""",
        (new_boost, new_boost, int(high_demand), needs_review, now_iso, story["id"]),
    )

    evidence = []
    if story["discovered_via"]:
        evidence.append(
            f"surfaced via trending keyword '{story['discovered_via']}' "
            "(past-hour Google News search)"
        )
    evidence += [
        f"VIRAL ACCELERATION: '{term}' volume +{velocity_pct:.0f}% in rolling 5-min window",
        f"~{posts_per_hour:.0f} posts/hour across monitored handles",
        f"dynamic offset +{new_boost} pts injected by X Conversation Broker",
    ]
    if high_demand:
        evidence.append("surge >5,000 posts/hr — elevated to High-Demand Airtime Recommendation")
    con.execute(
        """INSERT INTO score_breakdowns (story_id, variable, max_points, points, evidence)
           VALUES (?,'trend',?,?,?)
           ON CONFLICT(story_id, variable) DO UPDATE SET
           points=excluded.points, evidence=excluded.evidence""",
        (story["id"], config.TREND_MOMENTUM_CAP, trend_base + new_boost,
         json.dumps(evidence)),
    )
