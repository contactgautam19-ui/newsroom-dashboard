"""Hyper Search — what India is searching, watching and posting right now.

Ported from the N-Pro project (CMS Integration): three platform trend lists are
pulled side by side, merged where the same topic shows on more than one, and
scored with explicit, tunable rules. Detection is code, never AI. The optional
production deck (editor only) is the single AI step, layered on top.

  Google  — public trending-searches RSS, keyless
  YouTube — Data API v3 most-popular list (needs YOUTUBE_API_KEY, free quota)
  X       — TwtAPI Trends (1 billed call), so it is cached for an hour and only
            ever refreshed by an editor's scan

Scans and decks are cached in the settings table, so one scan serves every
viewer and survives serverless cold starts.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from xml.etree import ElementTree

import httpx

from app import config, db, settings_store

log = logging.getLogger("newsroom.hyper")

GOOGLE_RSS = "https://trends.google.com/trending/rss"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_NEWS_CATEGORY = "25"      # News & Politics — the list a newsroom cares about
X_WOEID_INDIA = "23424848"
_NS = {"ht": "https://trends.google.com/trending/rss"}

# --- detection parameters (tune freely) ---
NEW_ENTRANT_HOURS = 3             # first seen this recently = "new"
SEEN_RETENTION_HOURS = 48
COVERAGE_WINDOW_HOURS = 48        # no board story this recent = coverage gap
GOOGLE_VOLUME_SPIKE = 20_000      # approx searches marking a spike (IN)
YOUTUBE_VOLUME_SPIKE = 2_500_000  # views marking a spike (news videos run high)
TOP_RANK = 5                      # a platform's top-N earns the rank bonus
SCORE_BASE = 20
SCORE_CROSS_PLATFORM = 30
SCORE_NEW_ENTRANT = 25
SCORE_VOLUME_SPIKE = 15
SCORE_TOP_RANK = 10
SCORE_COVERAGE_GAP = 10
SIGNAL_THRESHOLD = 40             # minimum score to surface
MAX_SIGNALS = 10
MAX_PER_PLATFORM = 4              # single-platform signals from any one list

SCAN_TTL_MIN = 10                 # a scan newer than this is served from cache
X_TTL_MIN = 60                    # X trends are billed: at most one call an hour
DECK_TTL_MIN = 15

_SCAN_KEY, _X_KEY, _SEEN_KEY, _DECK_KEY = ("hyper_scan", "hyper_x_trends",
                                           "hyper_seen", "hyper_deck")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(key: str) -> dict:
    try:
        return json.loads(settings_store.get_setting(key, "") or "{}")
    except (TypeError, ValueError):
        return {}


def _save(key: str, value: dict) -> None:
    settings_store.set_setting(key, json.dumps(value, ensure_ascii=False))


def _age_min(iso: str | None) -> float | None:
    try:
        return (_now() - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


# ── platform fetchers ──────────────────────────────────────────────────────

def fetch_google(geo: str = "IN") -> list[dict]:
    resp = httpx.get(GOOGLE_RSS, params={"geo": geo}, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0 (Echo/1.0)"})
    resp.raise_for_status()
    items = []
    for item in ElementTree.fromstring(resp.text).iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = item.findtext("ht:approx_traffic", default="0", namespaces=_NS) or "0"
        news = item.find("ht:news_item", _NS)
        items.append({
            "keyword": title, "platform": "google",
            "volume": int("".join(c for c in traffic if c.isdigit()) or 0),
            "context": (news.findtext("ht:news_item_title", default="", namespaces=_NS)
                        if news is not None else ""),
            "url": (news.findtext("ht:news_item_url", default="", namespaces=_NS)
                    if news is not None else ""),
        })
    return items


def fetch_youtube(region: str = "IN") -> list[dict]:
    if not config.YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not set")
    resp = httpx.get(YOUTUBE_API, timeout=15, params={
        "part": "snippet,statistics", "chart": "mostPopular", "regionCode": region,
        "videoCategoryId": YOUTUBE_NEWS_CATEGORY, "maxResults": "25",
        "key": config.YOUTUBE_API_KEY,
    })
    if resp.status_code != 200:
        # never echo the request URL — it carries the key
        try:
            reason = resp.json()["error"]["message"]
        except Exception:
            reason = f"HTTP {resp.status_code}"
        raise RuntimeError(f"YouTube API: {reason}")
    items = []
    for v in resp.json().get("items", []):
        sn = v.get("snippet", {})
        title = (sn.get("title") or "").strip()
        if not title:
            continue
        views = v.get("statistics", {}).get("viewCount", "0")
        items.append({
            "keyword": title, "platform": "youtube",
            "volume": int(views) if str(views).isdigit() else 0,
            "context": sn.get("channelTitle") or "",
            "url": f"https://www.youtube.com/watch?v={v.get('id', '')}",
            "published_at": sn.get("publishedAt") or "",
        })
    return items


def fetch_x_trends(woeid: str = X_WOEID_INDIA) -> list[dict]:
    if not config.TWT_API_KEY:
        raise RuntimeError("TWT_API_KEY is not set")
    resp = httpx.get(f"{config.TWT_API_BASE}/Trends", params={"woeid": woeid},
                     timeout=20, headers={"X-API-Key": config.TWT_API_KEY})
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and payload.get("code") not in (None, 0, 200):
        raise RuntimeError(f"TwtAPI error {payload.get('code')}: {payload.get('msg')}")
    trends = []
    modules = payload.get("modules") if isinstance(payload, dict) else None
    if isinstance(modules, list):                      # live shape
        trends = [m["trend"] for m in modules
                  if isinstance(m, dict) and isinstance(m.get("trend"), dict)]
    elif isinstance((payload or {}).get("data"), dict):  # documented shape
        trends = [t for t in payload["data"].get("trends", []) if isinstance(t, dict)]
    out = []
    for t in trends:
        name = (t.get("name") or "").strip()
        if name:
            vol = t.get("tweet_volume")
            out.append({"keyword": name, "platform": "x",
                        "volume": int(vol) if isinstance(vol, (int, float)) else 0,
                        "context": "", "url": ""})
    return out


def _x_trends_cached(allow_call: bool) -> tuple[list[dict], str | None, str | None]:
    """(items, fetched_at, error). The billed call happens only when the cache is
    older than X_TTL_MIN *and* the caller is allowed to spend (an editor)."""
    cached = _load(_X_KEY)
    age = _age_min(cached.get("at"))
    if cached.get("items") and (not allow_call or (age is not None and age < X_TTL_MIN)):
        return cached["items"], cached.get("at"), None
    if not allow_call:
        return [], None, "X trends load on an editor's scan"
    try:
        items = fetch_x_trends()
        at = _now().isoformat()
        _save(_X_KEY, {"at": at, "items": items})
        return items, at, None
    except Exception as exc:
        log.warning("x trends failed: %s", exc)
        return cached.get("items") or [], cached.get("at"), str(exc)


# ── editorial newsworthiness policy (data/signal_policy.json) ──────────────

@lru_cache
def _policy() -> dict:
    raw = json.loads((config.DATA_DIR / "signal_policy.json").read_text(encoding="utf-8"))
    comp = lambda pats: [re.compile(p, re.IGNORECASE) for p in pats]  # noqa: E731
    return {
        "news": [str(m).casefold() for m in raw.get("always_newsworthy", [])],
        "promo": comp(raw.get("promotional_patterns", [])),
        "brands": [str(b).casefold() for b in raw.get("commercial_brands", [])],
        "ent": comp(raw.get("entertainment_patterns", [])),
        "bare_names": bool(raw.get("treat_bare_person_names_as_unclear", True)),
        "surface": set(raw.get("surface", ["newsworthy", "unclear"])),
    }


def classify(keyword: str, context: str = "") -> tuple[str, str]:
    """(verdict, reason): newsworthy | unclear | promotional | entertainment.
    Trend lists are mostly not news — campaigns, film promos, stock tips. This
    leads the list with what a desk can act on; nothing is silently dropped."""
    p = _policy()
    raw = keyword.strip()
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw.lstrip("#"))
    text = spaced.replace("_", " ").casefold()
    for marker in p["news"]:
        hit = (re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text) is not None
               if marker.isascii() else marker in text)
        if hit:
            return "newsworthy", f"matches news marker '{marker}'"
    for pat in p["promo"]:
        if pat.search(text) or pat.search(raw.casefold()):
            return "promotional", "brand-campaign or market-tip pattern"
    for brand in p["brands"]:
        if re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", text):
            return "promotional", f"consumer brand '{brand}' with no news angle"
    for pat in p["ent"]:
        if pat.search(text) or pat.search(raw.casefold()):
            return "entertainment", "film, OTT or music promotion"
    if p["bare_names"] and re.fullmatch(r"[A-Z][a-z]+ [A-Z][a-z]+", raw):
        return "unclear", "looks like a person's name — no news angle detected"
    return "unclear", "no editorial signal detected"


# ── merge + score ──────────────────────────────────────────────────────────

_STOP = set("the a an and of in on to for with at by from is are was after over "
            "news live today latest video full watch india indian".split())


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.casefold().lstrip("#"))


def _tokens(s: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.lstrip("#"))
    return {w for w in re.sub(r"[^\w\s]", " ", spaced.casefold()).split()
            if len(w) >= 4 and w not in _STOP and not w.isdigit()}


def _same_topic(short: str, long_: str) -> bool:
    """A search/hashtag term ('#MumbaiFloods', 'ind vs aus') against another
    term or a video/headline title. Containment for compact terms; for longer
    phrases every distinctive word of the shorter one must appear."""
    a, b = _norm(short), _norm(long_)
    if not a or not b:
        return False
    if len(a) > len(b):
        a, b, short, long_ = b, a, long_, short
    if len(a) >= 5 and a in b:
        return True
    ta, tb = _tokens(short), _tokens(long_)
    return len(ta) >= 2 and ta <= tb


def _board_match(keyword: str, context: str, stories: list[dict]) -> dict | None:
    probe = f"{keyword} {context}"
    toks = sorted(_tokens(probe), key=len, reverse=True)
    strong = [t for t in toks if len(t) >= 6][:4] or toks[:1]
    if not strong:
        return None
    need = 1 if len(strong) == 1 else 2
    for s in stories:
        title_toks = _tokens(s["title"])
        if sum(1 for t in strong if t in title_toks) >= need or _same_topic(keyword, s["title"]):
            return {"id": s["id"], "title": s["title"], "active": bool(s["active"])}
    return None


def run_scan(spend_x: bool = False) -> dict:
    """Pull every platform, merge cross-platform topics, score and classify.
    A dead or unconfigured platform is reported and skipped — never fatal."""
    now = _now()
    lists: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    fetched: dict[str, str] = {}
    for platform, fn in (("google", fetch_google), ("youtube", fetch_youtube)):
        try:
            lists[platform] = fn()
            fetched[platform] = now.isoformat()
        except Exception as exc:
            errors[platform] = str(exc)
    x_items, x_at, x_err = _x_trends_cached(spend_x)
    if x_items:
        lists["x"] = x_items
        fetched["x"] = x_at or now.isoformat()
    if x_err:
        errors["x"] = x_err

    # first-seen memory: the time axis that makes "new" mean something
    seen = _load(_SEEN_KEY)
    cold_start = not seen
    keep_after = (now - timedelta(hours=SEEN_RETENTION_HOURS)).isoformat()
    seen = {k: v for k, v in seen.items() if (v or "") >= keep_after}
    # what a first-ever scan finds is stamped just outside the "new" window
    cold_stamp = (now - timedelta(hours=NEW_ENTRANT_HOURS, minutes=5)).isoformat()

    with db.connect() as con:
        since = (now - timedelta(hours=COVERAGE_WINDOW_HOURS)).isoformat()
        stories = [dict(r) for r in con.execute(
            "SELECT id, title, active FROM stories WHERE first_seen_at >= ? "
            "ORDER BY first_seen_at DESC LIMIT 600", (since,)).fetchall()]

    signals: list[dict] = []
    for platform, items in lists.items():
        for rank, it in enumerate(items, start=1):
            it["rank"] = rank
            key = _norm(it["keyword"])
            first = seen.get(key)
            if first is None:
                # a cold start has no history, so nothing can be called new yet
                seen[key] = first = (cold_stamp if cold_start else now.isoformat())
            merged = next((s for s in signals
                           if platform not in s["platforms"]
                           and _same_topic(s["keyword"], it["keyword"])), None)
            if merged is not None:
                merged["platforms"].append(platform)
                merged["reasons"].append(f"also trending on {_LABEL[platform]}")
                merged["volume"] = max(merged["volume"], it["volume"])
                merged["links"].append({"platform": platform, "label": it["keyword"],
                                        "url": it.get("url", "")})
                continue
            if any(_norm(s["keyword"]) == key for s in signals):
                continue
            age = _age_min(first)
            is_new = not cold_start and age is not None and age <= NEW_ENTRANT_HOURS * 60
            reasons = []
            if is_new:
                reasons.append(f"new in the last {NEW_ENTRANT_HOURS}h")
            spike = ((platform == "google" and it["volume"] >= GOOGLE_VOLUME_SPIKE)
                     or (platform == "youtube" and it["volume"] >= YOUTUBE_VOLUME_SPIKE))
            if spike:
                unit = "searches" if platform == "google" else "views"
                reasons.append(f"volume spike ({it['volume']:,}+ {unit})")
            if rank <= TOP_RANK:
                reasons.append(f"#{rank} on {_LABEL[platform]}")
            signals.append({
                "keyword": it["keyword"], "context": it.get("context", ""),
                "platforms": [platform], "reasons": reasons, "volume": it["volume"],
                "top_rank": rank <= TOP_RANK,
                "is_new": is_new, "first_seen": first,
                "links": [{"platform": platform, "label": it["keyword"],
                           "url": it.get("url", "")}],
            })
    _save(_SEEN_KEY, seen)

    for s in signals:
        match = _board_match(s["keyword"], s["context"], stories)
        s["board_story"] = match
        s["coverage_gap"] = match is None
        if match is None:
            s["reasons"].append(f"not on our board in the last {COVERAGE_WINDOW_HOURS}h")
        score = SCORE_BASE
        if len(s["platforms"]) >= 2:
            score += SCORE_CROSS_PLATFORM
        if s["is_new"]:
            score += SCORE_NEW_ENTRANT
        if any(r.startswith("volume spike") for r in s["reasons"]):
            score += SCORE_VOLUME_SPIKE
        if s["top_rank"]:
            score += SCORE_TOP_RANK
        if s["coverage_gap"]:
            score += SCORE_COVERAGE_GAP
        s["score"] = min(100, score)
        s["verdict"], s["verdict_reason"] = classify(s["keyword"], s["context"])
        if s["verdict"] == "entertainment" and s["platforms"] == ["youtube"]:
            # the YouTube list is already the News & Politics chart, so the
            # film/shorts patterns (#shorts, "song", "reaction") misfire on it
            s["verdict"], s["verdict_reason"] = "unclear", "news-category video"

    surface = _policy()["surface"]
    ranked = sorted((s for s in signals if s["score"] >= SIGNAL_THRESHOLD),
                    key=lambda s: -s["score"])
    editorial = [s for s in ranked if s["verdict"] in surface]
    editorial.sort(key=lambda s: (-len(s["platforms"]), s["verdict"] != "newsworthy",
                                  -s["score"]))
    # one busy list must not crowd the others out of the signals
    taken: dict[str, int] = {}
    balanced = []
    for s in editorial:
        if len(s["platforms"]) == 1:
            p = s["platforms"][0]
            if taken.get(p, 0) >= MAX_PER_PLATFORM:
                continue
            taken[p] = taken.get(p, 0) + 1
        balanced.append(s)
    editorial = balanced
    result = {
        "scanned_at": now.isoformat(),
        "signals": editorial[:MAX_SIGNALS],
        "filtered": [s for s in ranked if s["verdict"] not in surface][:12],
        "trends": {p: items[:20] for p, items in lists.items()},
        "fetched": fetched, "errors": errors, "cold_start": cold_start,
        "youtube_configured": bool(config.YOUTUBE_API_KEY),
    }
    _save(_SCAN_KEY, result)
    return result


_LABEL = {"google": "Google", "youtube": "YouTube", "x": "X"}


def get_scan(force: bool = False, spend_x: bool = False) -> dict:
    """Cached scan, refreshed when older than SCAN_TTL_MIN (or on ``force``)."""
    cached = _load(_SCAN_KEY)
    age = _age_min(cached.get("scanned_at"))
    if cached and not force and age is not None and age < SCAN_TTL_MIN:
        return {**cached, "cached": True}
    return {**run_scan(spend_x=spend_x), "cached": False}


def latest_scan() -> dict:
    """Whatever the last scan stored — never triggers a fetch."""
    return _load(_SCAN_KEY)


# ── production deck (editor only — the one AI step) ────────────────────────

_BUCKETS = {"Civic Breakdown", "Cultural Anomaly", "India-Optics Global",
            "Human Anchor", "Financial Policy"}

_DECK_PROMPT = """You are the Hyper Search engine inside Echo, a newsroom intelligence dashboard. AI recommends; humans publish. Mine the ingested corpus and live pulse below for civic disruptions, national-identity signals and cultural friction, and parse them into narrative structures a production team can act on within minutes.

TARGET AUDIENCE: Indian tier-2 and tier-3 viewers. Direct-to-camera, explanatory, high-clarity storytelling. Complex policy becomes simple pillars; abstract numbers become lived consequences.

THEMATIC BUCKETS (one or two per alert): "Civic Breakdown" (public-safety negligence, infrastructure failure, institutional accountability), "Cultural Anomaly" (viral counter-cultural events), "India-Optics Global" (international news through the how-it-impacts-India lens), "Human Anchor" (first-person testimony or appeals that can anchor a monologue), "Financial Policy" (usually LOW resonance unless there is direct household impact).

NEWSROOM CORPUS (stories Echo ingested — your ONLY factual source; ids included; ageHours = how long ago each reached the board):
{corpus}

RECENCY IS PART OF THE RANKING. Lead with what broke or moved in the last 6 hours. A story older than ~12 hours may lead only if it genuinely developed since, and you must say what moved. Never present yesterday's top line as today's.

LIVE PULSE — platform trends right now:
{trends}

DETECTED SIGNALS (deterministic, scored 0-100):
{signals}

{voice}Hard rules:
1. Ground every alert in the corpus and/or pulse above. NEVER invent events, quotes, letters or testimony. If no human anchor exists, set has_direct_appeal to false.
2. populist_resonance_index (0.0-10.0) is a relative estimate of mass-audience alignment: household impact, moral clarity, civic identity. Calibrate honestly — a deck of all 9s is useless.
3. narrative_pillars: exactly 3 plain-language sentences a viewer can repeat. No jargon.
4. hyper_local_phrasing: the story the way a viewer in a tier-2 town would say it.
5. Compliance: set communal_risk_level to Medium/High whenever a story touches religion, caste or community identity and say why in notes; recommend verification and legal review as the first step for such items. Accuracy outranks resonance.
6. story_ids: corpus ids each alert draws on (empty only for pulse-only alerts).

Respond with ONLY a JSON object:
{{"deck_headline": "one line", "summary": "2-3 sentences", "alerts": [{{"story_vector": "", "thematic_buckets": [""], "populist_resonance_index": 0.0, "sourcing_asset": "", "human_anchor": {{"has_direct_appeal": false, "source_element": ""}}, "narrative_pillars": ["", "", ""], "production_directive": "", "hyper_local_phrasing": "", "compliance_risk_flags": {{"communal_risk_level": "Low", "regulatory_review_required": false, "notes": ""}}, "story_ids": [""]}}], "risks": [""]}}
Order alerts by populist_resonance_index descending. At most 6 alerts. Keep story_vector to one headline-length line of at most 16 words; put the detail in the pillars."""


def _corpus(limit: int = 40) -> list[dict]:
    now = _now()
    since = (now - timedelta(hours=24)).isoformat()
    with db.connect() as con:
        rows = db.rows_to_dicts(con.execute(
            "SELECT id, title, publisher, category, status, score, sources, first_seen_at "
            "FROM stories WHERE first_seen_at >= ? ORDER BY score DESC, first_seen_at DESC "
            "LIMIT ?", (since, limit)).fetchall())
    out = []
    for r in rows:
        age = _age_min(r.get("first_seen_at"))
        out.append({"id": str(r["id"]), "headline": r["title"],
                    "publisher": r.get("publisher") or "",
                    "category": r.get("category") or "", "status": r.get("status") or "",
                    "outlets": len(r.get("sources") or []),
                    "ageHours": round(age / 60, 1) if age is not None else None})
    return out


def _clamp(value) -> float:
    try:
        return round(min(10.0, max(0.0, float(value))), 1)
    except (TypeError, ValueError):
        return 0.0


def _normalise_alert(raw: dict, known: dict[str, str]) -> dict:
    anchor = raw.get("human_anchor") if isinstance(raw.get("human_anchor"), dict) else {}
    flags = (raw.get("compliance_risk_flags")
             if isinstance(raw.get("compliance_risk_flags"), dict) else {})
    risk = str(flags.get("communal_risk_level", "Low")).title()
    ids = [i for i in map(str, raw.get("story_ids") or []) if i in known]
    return {
        "story_vector": str(raw.get("story_vector", "")).strip(),
        "thematic_buckets": [b for b in raw.get("thematic_buckets") or [] if b in _BUCKETS]
                            or ["Civic Breakdown"],
        "populist_resonance_index": _clamp(raw.get("populist_resonance_index")),
        "sourcing_asset": str(raw.get("sourcing_asset", "")),
        "human_anchor": {"has_direct_appeal": bool(anchor.get("has_direct_appeal")),
                         "source_element": str(anchor.get("source_element", ""))},
        "narrative_pillars": [str(p) for p in raw.get("narrative_pillars") or []][:3],
        "production_directive": str(raw.get("production_directive", "")),
        "hyper_local_phrasing": str(raw.get("hyper_local_phrasing", "")),
        "compliance_risk_flags": {
            "communal_risk_level": risk if risk in ("Low", "Medium", "High") else "Low",
            "regulatory_review_required": bool(flags.get("regulatory_review_required")),
            "notes": str(flags.get("notes", ""))},
        # only ids that exist in the corpus we sent — hallucinated ids are dropped
        "stories": [{"id": int(i), "title": known[i]} for i in ids],
    }


def latest_deck() -> dict:
    return _load(_DECK_KEY)


def build_deck(force: bool = False) -> dict:
    """Claude turns the scan + the last 24h of board stories into a ranked
    production deck. Costs AI tokens, so it is an explicit editor action and
    the result is shared for DECK_TTL_MIN."""
    from app.npro import engine
    cached = _load(_DECK_KEY)
    age = _age_min(cached.get("built_at"))
    if cached.get("alerts") and not force and age is not None and age < DECK_TTL_MIN:
        return {**cached, "cached": True, "ok": True}
    if not engine.has_key():
        return {"ok": False, "error": "No Anthropic API key configured — add one in "
                                      "Ops → AI writer settings to build the deck."}
    scan = get_scan()
    corpus = _corpus()
    if not corpus and not scan.get("signals"):
        return {"ok": False, "error": "Nothing to mine yet — run a scan and a story refresh first."}
    slim = lambda items: [{"keyword": i["keyword"], "volume": i["volume"],   # noqa: E731
                           "context": i.get("context", "")} for i in items[:15]]
    voice = settings_store.get_setting("voice_description", "") or ""
    prompt = _DECK_PROMPT.format(
        corpus=json.dumps(corpus, ensure_ascii=False, indent=1),
        trends=json.dumps({p: slim(v) for p, v in (scan.get("trends") or {}).items()},
                          ensure_ascii=False, indent=1),
        signals=json.dumps([{k: s[k] for k in ("keyword", "score", "platforms", "reasons",
                                               "verdict")} for s in scan.get("signals", [])],
                           ensure_ascii=False, indent=1),
        voice=f"HOUSE STYLE: {voice}\n\n" if voice.strip() else "")
    text = engine._call("You output only valid JSON.", prompt, max_tokens=7000)
    if not text:
        return {"ok": False, "error": "The model did not return a deck — try again in a moment."}
    try:
        payload = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return {"ok": False, "error": "The model returned an unreadable deck — try again."}
    known = {c["id"]: c["headline"] for c in corpus}
    alerts = [_normalise_alert(a, known) for a in payload.get("alerts") or []
              if isinstance(a, dict)]
    alerts = sorted((a for a in alerts if a["story_vector"]),
                    key=lambda a: -a["populist_resonance_index"])[:6]
    deck = {
        "built_at": _now().isoformat(),
        "deck_headline": str(payload.get("deck_headline", "")),
        "summary": str(payload.get("summary", "")),
        "alerts": alerts,
        "risks": [str(r) for r in payload.get("risks") or []][:8],
        "corpus_size": len(corpus),
        "platforms": sorted((scan.get("trends") or {}).keys()),
    }
    _save(_DECK_KEY, deck)
    return {**deck, "cached": False, "ok": True}
