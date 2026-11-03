"""Story tracking — following one running story across the day it develops.

The board answers "what should we lead with in the last hour" and retires
aggressively, which is right for a rundown and useless for a story that keeps
moving. A rally tomorrow produces police permissions, traffic curbs, a
deployment number, an opposition reaction and a leader's statement over
eight hours, each from a different outlet. No single hourly snapshot shows
that shape; a timeline does.

So a tracked story is a standing search an editor has asked for by name. Each
scan re-runs it, drops anything already recorded, and appends what is new —
tagged with the KIND of development it is, so a producer can scan the column
rather than read every headline.

Deliberately not a model call. "Permission granted", "traffic diverted",
"4,000 personnel deployed" and "opposition slams" are recognisable from the
headline, and a desk needs this to be instant, free and the same every time.
"""

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from app import db

log = logging.getLogger("newsroom.tracker")

SEARCH_TIMEOUT = 12
MAX_ACTIVE_TRACKS = 12        # each costs one HTTP request per scan
RESULTS_PER_CHECK = 60

# A new track must open on the story so far, not on an empty panel. The
# windows are tried in order and their results merged: one day gives today's
# detail, a week gives the run-up. The unbounded search is only reached if
# those two came back thin, because on a long-running topic it reaches back
# months and most of that is not a development.
BACKFILL_WINDOWS = ["1d", "7d"]
BACKFILL_DEEP = ""            # no when: filter — only if the ladder is thin
BACKFILL_THIN = 8             # items below which we go deeper

# Polls size their window to the gap since the last check rather than using a
# fixed one. A machine that slept overnight, or a scan loop that stopped,
# would otherwise come back asking only about the last six hours and lose
# everything that happened while nothing was looking.
POLL_FLOOR = "2h"
_HEADERS = {"User-Agent": "Mozilla/5.0 (NewsroomDashboard/1.0)"}
_SEARCH = ("https://news.google.com/rss/search?q={q}{w}"
           "&hl=en-IN&gl=IN&ceid=IN:en")


def _window_for_gap(last_checked_at: str | None) -> str:
    """Pick a search window that covers everything since the last check."""
    if not last_checked_at:
        return "7d"
    try:
        gap_h = ((datetime.now(timezone.utc)
                  - datetime.fromisoformat(last_checked_at)).total_seconds() / 3600)
    except (ValueError, TypeError):
        return "7d"
    if gap_h <= 1:
        return POLL_FLOOR        # generous: Google's clock is not ours
    if gap_h <= 5:
        return "12h"
    if gap_h <= 20:
        return "1d"
    if gap_h <= 72:
        return "7d"
    return "7d"

_norm_re = re.compile(r"[^a-z0-9 ]+")

# What kind of development is this? Ordered: the first match wins, so the
# more specific categories are checked before the catch-alls.
_KINDS = [
    ("permission", re.compile(
        r"\b(permission|permit(?:ted|s)?|nod|approval|approved|cleared|"
        r"denied|refus\w+|rejected|disallow\w+|no objection|noc)\b", re.I)),
    ("security", re.compile(
        r"\b(police|security|personnel|deploy\w+|bandobast|barricad\w+|"
        r"paramilitary|crpf|rapid action|detain\w+|prohibitory|section 144|"
        r"bns 163)\b", re.I)),
    ("traffic", re.compile(
        r"\b(traffic|curbs?|advisory|diversion[s]?|divert\w+|road closure|"
        r"closed for|metro|parking|route[s]?|commuters?)\b", re.I)),
    ("legal", re.compile(
        r"\b(court|high court|supreme court|petition|plea|hearing|verdict|"
        r"stay|notice|fir|case filed)\b", re.I)),
    ("reaction", re.compile(
        r"\b(slams?|hits? out|targets?|attacks?|reacts?|responds?|"
        r"criticis\w+|condemn\w+|questions?|dares?|counters?|rebuts?|"
        r"accuses?|blames?)\b", re.I)),
    ("scale", re.compile(
        r"\b(lakh|crore|turnout|crowd|gather\w+|thousands|expected to|"
        r"footfall|attendance)\b", re.I)),
    ("statement", re.compile(
        r"\b(says?|said|tells?|told|addresses?|speaks?|announces?|"
        r"declares?|appeals?|invites?|urges?|writes?)\b", re.I)),
]

KIND_LABELS = {
    "permission": "Permission",
    "security": "Police & security",
    "traffic": "Traffic",
    "legal": "Legal",
    "reaction": "Reaction",
    "scale": "Scale & turnout",
    "statement": "Statement",
    "update": "Update",
}


def classify(title: str) -> str:
    for kind, pattern in _KINDS:
        if pattern.search(title or ""):
            return kind
    return "update"


def _key(tracked_id: int, title: str) -> str:
    norm = " ".join(sorted(set(_norm_re.sub("", (title or "").lower()).split())))
    return hashlib.sha1(f"{tracked_id}|{norm}".encode()).hexdigest()


# Words that make a search worse, not better: every Indian headline has them.
_QUERY_STOP = set(
    "the a an and or of in on to for with at by from as is are was be after "
    "amid over under his her their its says say said new live update updates "
    "latest news today breaking video watch big top how why what who".split()
)


def query_from_title(title: str) -> str:
    """Turn a headline into a standing search.

    A whole headline is a bad query — it is one outlet's wording of one
    moment, and Google matches it too narrowly to catch tomorrow's follow-up.
    The significant nouns are what the story is actually about.
    """
    words = _norm_re.sub("", (title or "").lower()).split()
    keep = [w for w in words if w not in _QUERY_STOP and len(w) > 2]
    return " ".join(keep[:6]) or (title or "").strip()[:80]


def start(label: str, query: str = "", story_id: int | None = None,
          created_by: str = "editor") -> dict:
    """Begin tracking. Returns the track, with its first developments already in."""
    label = (label or "").strip()
    if not label:
        raise ValueError("A tracked story needs a name.")
    query = (query or "").strip() or query_from_title(label)
    now = datetime.now(timezone.utc).isoformat()

    with db.connect() as con:
        active = con.execute(
            "SELECT COUNT(*) c FROM tracked_stories WHERE active=1").fetchone()["c"]
        if active >= MAX_ACTIVE_TRACKS:
            raise ValueError(
                f"Already tracking {active} stories, which is the limit. "
                "Stop one before starting another.")
        existing = con.execute(
            "SELECT id FROM tracked_stories WHERE active=1 AND lower(label)=?",
            (label.lower(),)).fetchone()
        if existing:
            return get(existing["id"])
        cur = con.execute(
            "INSERT INTO tracked_stories (label, query, story_id, created_at, "
            "created_by) VALUES (?,?,?,?,?)",
            (label, query, story_id, now, created_by))
        tracked_id = cur.lastrowid

    # Backfill straight away, so the editor sees the story so far rather than
    # an empty panel and a promise. A track that opens on zero is worse than
    # useless: it reads as "nothing is happening" when the truth is "I have
    # not looked yet".
    result = check_one(tracked_id, backfill=True)
    track = get(tracked_id)
    if not result.get("ok"):
        track["backfill_error"] = result.get("error", "")
    return track


def stop(tracked_id: int) -> bool:
    with db.connect() as con:
        n = con.execute("UPDATE tracked_stories SET active=0 WHERE id=?",
                        (tracked_id,)).rowcount
    return bool(n)


def _fetch(query: str, window: str) -> list[dict]:
    """One search. `window` is a Google `when:` value, or "" for no filter."""
    url = _SEARCH.format(q=quote_plus(query),
                         w=f"+when:{window}" if window else "")
    resp = httpx.get(url, timeout=SEARCH_TIMEOUT, follow_redirects=True,
                     headers=_HEADERS)
    resp.raise_for_status()
    out = []
    for entry in feedparser.parse(resp.content).entries[:RESULTS_PER_CHECK]:
        title = (entry.get("title") or "").strip()
        publisher = ""
        if " - " in title:          # Google formats as "Headline - Publisher"
            title, publisher = title.rsplit(" - ", 1)
        if not publisher:
            publisher = (entry.get("source") or {}).get("title", "")
        parsed = entry.get("published_parsed")
        published = (datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
                     if parsed else datetime.now(timezone.utc).isoformat())
        if title:
            out.append({"title": title.strip(), "url": entry.get("link", ""),
                        "publisher": publisher.strip(), "published_at": published})
    return out


def check_one(tracked_id: int, window: str | None = None,
              backfill: bool = False) -> dict:
    """Re-run one standing search and record whatever is new.

    `window` defaults to whatever covers the gap since the last check, so a
    track that went unchecked overnight catches up instead of asking about
    the last few hours and silently losing the rest.
    """
    with db.connect() as con:
        row = con.execute("SELECT * FROM tracked_stories WHERE id=?",
                          (tracked_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "no such tracked story"}

    now = datetime.now(timezone.utc).isoformat()
    windows = (BACKFILL_WINDOWS if backfill
               else [window or _window_for_gap(row["last_checked_at"])])

    items, errors = [], []
    seen_urls = set()
    for win in windows:
        try:
            for it in _fetch(row["query"], win):
                if it["url"] and it["url"] in seen_urls:
                    continue
                seen_urls.add(it["url"])
                items.append(it)
        except Exception as exc:
            errors.append(f"when:{win or 'all'}: {exc}")
            log.warning("track %s fetch failed (%s): %s", tracked_id, win, exc)

    # A new track with almost nothing in it is usually a story that started
    # before the week, not a story with no coverage. Reach further back.
    if backfill and len(items) < BACKFILL_THIN:
        try:
            for it in _fetch(row["query"], BACKFILL_DEEP):
                if it["url"] and it["url"] in seen_urls:
                    continue
                seen_urls.add(it["url"])
                items.append(it)
        except Exception as exc:
            errors.append(f"deep: {exc}")

    if not items and errors:
        # Every search failed. Do NOT stamp last_checked_at — leaving it unset
        # keeps this track on the wide catch-up window so the next scan tries
        # the full backfill again rather than treating the gap as covered.
        return {"ok": False, "error": "; ".join(errors), "new": 0,
                "total": row["updates_count"] or 0}

    added = []
    with db.connect() as con:
        for it in items:
            key = _key(tracked_id, it["title"])
            if con.execute("SELECT 1 FROM tracked_updates WHERE tracked_id=? "
                           "AND dedup_key=?", (tracked_id, key)).fetchone():
                continue
            kind = classify(it["title"])
            con.execute(
                "INSERT INTO tracked_updates (tracked_id, dedup_key, title, url, "
                "publisher, published_at, first_seen, kind) VALUES (?,?,?,?,?,?,?,?)",
                (tracked_id, key, it["title"], it["url"], it["publisher"],
                 it["published_at"], now, kind))
            added.append({**it, "kind": kind})

        newest = con.execute(
            "SELECT MAX(published_at) m FROM tracked_updates WHERE tracked_id=?",
            (tracked_id,)).fetchone()["m"]
        total = con.execute(
            "SELECT COUNT(*) c FROM tracked_updates WHERE tracked_id=?",
            (tracked_id,)).fetchone()["c"]
        con.execute(
            "UPDATE tracked_stories SET last_checked_at=?, last_update_at=?, "
            "updates_count=? WHERE id=?", (now, newest, total, tracked_id))

    return {"ok": True, "new": len(added), "total": total, "added": added}


def check_all() -> dict:
    """Re-run every active standing search. Called from the fast lane."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, last_checked_at, updates_count FROM tracked_stories "
            "WHERE active=1 ORDER BY id LIMIT ?", (MAX_ACTIVE_TRACKS,)).fetchall()
    if not rows:
        return {"tracked": 0, "new": 0}

    results = {}
    # One request each, in parallel: twelve sequential searches would make the
    # fast lane slow enough to stop being a fast lane. A track still sitting
    # on zero gets the full backfill again — its first attempt evidently did
    # not land, and an empty tracked story is the one state worth retrying.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(check_one, r["id"], None, not (r["updates_count"] or 0)): r["id"]
            for r in rows}
        for fut in as_completed(futures):
            tid = futures[fut]
            try:
                results[tid] = fut.result().get("new", 0)
            except Exception as exc:
                log.warning("track %s check failed: %s", tid, exc)
                results[tid] = 0
    return {"tracked": len(rows), "new": sum(results.values()), "per_story": results}


def watch_signatures() -> list[tuple[set, str]]:
    """Token signatures of the storylines the desk has asked to follow.

    A storyline an editor has named by hand is a far stronger statement of news
    value than any wording rule, so the fast lane lets a signal matching one
    onto the board whatever it scored. It is still scored honestly once there,
    which means a watched story can sit low on the board — present and
    arguable, rather than absent.
    """
    from app.news.ingest import _sig_tokens
    out: list[tuple[set, str]] = []
    try:
        with db.connect() as con:
            rows = con.execute(
                "SELECT label, query FROM tracked_stories "
                "WHERE active = 1 AND watch = 1").fetchall()
    except Exception as exc:              # a missing column must not stop a scan
        log.warning("watch signatures unavailable: %s", exc)
        return out
    for r in rows:
        # label and query together: the label is what the editor typed, the
        # query carries the names lifted off the headline it was started from.
        tokens = _sig_tokens(f"{r['label'] or ''} {r['query'] or ''}")
        if tokens:
            out.append((tokens, r["label"] or r["query"]))
    return out


def watch_hit(title: str, signatures: list[tuple[set, str]]) -> str:
    """The watched storyline this headline belongs to, or "".

    Deliberately more generous than the dedup matcher. That one needs two
    shared significant words because a wrong merge hides a story; here an
    editor has named the storyline by hand, so one distinctive name is enough
    — a headline reading only "Kumaraswamy dares police to act" shares nothing
    with a label built around Siddaramaiah, and missing it is the whole fault
    being fixed. Broad words never match alone, so a Karnataka watch does not
    pull in the state's milk price.
    """
    if not signatures:
        return ""
    from app.news.ingest import BROAD_TERMS, _sig_tokens
    tokens = _sig_tokens(title or "")
    if not tokens:
        return ""
    for seen, label in signatures:
        shared = tokens & seen
        if not shared:
            continue
        if any(t not in BROAD_TERMS and len(t) >= 5 for t in shared):
            return label
        if len(shared) >= 2:
            return label
    return ""


def _shape(row: dict) -> dict:
    row = dict(row)
    row["kind_labels"] = KIND_LABELS
    return row


def list_all(include_stopped: bool = False) -> list[dict]:
    where = "" if include_stopped else "WHERE active=1 "
    # A track that has never found anything has last_update_at NULL. SQLite
    # sorts NULLs lowest (so DESC puts them last, which is what we want);
    # Postgres sorts them highest and needs telling.
    order = ("ORDER BY last_update_at DESC NULLS LAST, id DESC" if db.IS_PG
             else "ORDER BY last_update_at DESC, id DESC")
    with db.connect() as con:
        rows = db.rows_to_dicts(
            con.execute(f"SELECT * FROM tracked_stories {where}{order}").fetchall())
        # One grouped query rather than one per track.
        unseen = {r["tracked_id"]: r["c"] for r in con.execute(
            "SELECT tracked_id, COUNT(*) c FROM tracked_updates "
            "WHERE seen=0 GROUP BY tracked_id").fetchall()}
    for r in rows:
        r["unseen"] = unseen.get(r["id"], 0)
    return rows


def get(tracked_id: int, limit: int = 120) -> dict:
    """One tracked story with its full timeline, newest first."""
    with db.connect() as con:
        row = con.execute("SELECT * FROM tracked_stories WHERE id=?",
                          (tracked_id,)).fetchone()
        if not row:
            return {}
        updates = db.rows_to_dicts(con.execute(
            "SELECT * FROM tracked_updates WHERE tracked_id=? "
            "ORDER BY published_at DESC LIMIT ?", (tracked_id, limit)).fetchall())
    track = dict(row)
    track["updates"] = updates
    track["kind_labels"] = KIND_LABELS
    # A count per kind, so the panel can say what SHAPE the day has had
    # rather than only how many headlines there were.
    breakdown = {}
    for u in updates:
        breakdown[u["kind"]] = breakdown.get(u["kind"], 0) + 1
    track["breakdown"] = breakdown
    track["outlets"] = len({u["publisher"] for u in updates if u["publisher"]})
    return track


def mark_seen(tracked_id: int) -> None:
    with db.connect() as con:
        con.execute("UPDATE tracked_updates SET seen=1 WHERE tracked_id=?",
                    (tracked_id,))
