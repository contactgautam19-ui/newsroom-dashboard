"""Stealth-scrape each news channel's WEBSITE for its live-desk headlines.

This is the accurate replacement for YouTube-title monitoring. A channel's site
(timesnownews.com, ndtv.com, indiatoday.in…) surfaces the stories its desk is
pushing right now — its breaking band, live blog and top-story rail — as real
HTML text. Reading that through the stealth browser (app/news/stealth_browser)
gives current, specific headlines with zero OCR and zero API cost, and the
fingerprint masking gets us past the bot walls these sites serve to plain
headless Chromium.

Local worker only (browser-driven; never Vercel). Results go into the same
``live_onair`` table the panel, Alerts feed and India-ranking already read.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone

from app import db
from app.news import onair, stealth_browser

log = logging.getLogger("newsroom.web_extract")

# Generic, site-agnostic headline harvester. Scores every anchor by how
# headline-like it is and which section it sits in (breaking/live/top rails
# rank highest), so it works across differently-built news sites without a
# hand-tuned selector each. Returns [{text, breaking, rank}] newest/strongest.
_EXTRACT_JS = r"""
() => {
  const BREAKING = /breaking|big breaking|big development|just in|newsflash|big story|alert/i;
  const HOT = /breaking|live|top|lead|hero|highlight|headline|flash/i;
  const seen = new Set();
  const out = [];
  const anchors = [...document.querySelectorAll('a')];
  for (const a of anchors) {
    const txt = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (txt.length < 24 || txt.length > 160) continue;
    const words = txt.split(' ');
    if (words.length < 4) continue;
    const letters = (txt.match(/[A-Za-z]/g) || []).length;
    if (letters < txt.length * 0.6) continue;           // skip nav/labels/numbers
    const norm = txt.toLowerCase();
    if (seen.has(norm)) continue;
    // visible + reasonably near the top of the page (live rails are high up)
    const r = a.getBoundingClientRect();
    if (r.width < 40 || r.height < 8) continue;
    // section signal: className chain of the anchor + up to 4 ancestors
    let ctx = a.className + ' ';
    let n = a, hops = 0;
    while (n && hops < 4) { ctx += (n.className || '') + ' '; n = n.parentElement; hops++; }
    const hot = HOT.test(ctx);
    const breaking = BREAKING.test(txt) || /breaking|flash/i.test(ctx);
    let rank = 0;
    if (breaking) rank += 100;
    if (hot) rank += 40;
    rank += Math.max(0, 30 - Math.floor(r.top / 120));   // higher on page = better
    seen.add(norm);
    out.push({ text: txt, breaking, rank });
  }
  out.sort((x, y) => y.rank - x.rank);
  return out.slice(0, 12);
}
"""

_CONSENT = ('button:has-text("Accept all")', 'button:has-text("Accept All")',
            'button[aria-label*="Accept"]', 'button:has-text("I Agree")',
            'button:has-text("Agree")', '#onetrust-accept-btn-handler')

# live-blog wrapper / tracker titles — rolling desk pages, not stories
_JUNK = re.compile(
    r"track here|latest headlines of|live updates:|live blog|"
    r"top headlines|news highlights|as it happened|catch all the",
    re.IGNORECASE)

# trailing site-name / section noise trimmed from a scraped headline
_TRAIL = re.compile(
    r"\s*[-|–]\s*(times now|ndtv|india today|republic|cnn-?news18|news18|wion|"
    r"aaj tak|zee news|mirror now|latest news|breaking news|watch|video)\s*$",
    re.IGNORECASE)


def _clean(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _TRAIL.sub("", text).strip(" -–—|:•")
    return re.sub(r"\s+", " ", text).strip()


def extract_channel(page, url: str) -> list[dict]:
    """Load a channel site through the (already-stealth) page and harvest its
    top/breaking/live headlines. Returns [{headline, breaking}]."""
    page.goto(url, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(2500)
    for sel in _CONSENT:
        try:
            page.click(sel, timeout=1500)
            break
        except Exception:
            pass
    page.wait_for_timeout(4000)
    try:
        raw = page.evaluate(_EXTRACT_JS)
    except Exception as exc:
        log.warning("extract failed for %s: %s", url, exc)
        return []
    items, seen = [], set()
    for r in raw:
        h = _clean(r.get("text", ""))
        if len(h) < 20 or _JUNK.search(h):
            continue
        low = h.lower()
        if low in seen or low in onair._GENERIC:
            continue
        seen.add(low)
        items.append({"headline": h, "breaking": bool(r.get("breaking"))})
    return items[:8]


def _upsert(channel: str, items: list[dict]) -> int:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    hour_key = onair._hour_key(now.astimezone(onair.IST))
    n = 0
    with db.connect() as con:
        for it in items:
            slug = hashlib.sha1(
                f"{channel}|{it['headline'].lower()}|{hour_key}".encode()
            ).hexdigest()[:16]
            con.execute(
                """INSERT INTO live_onair
                   (slug, channel, headline, hour_key, breaking, first_seen,
                    last_seen, source)
                   VALUES (?,?,?,?,?,?,?,'web')
                   ON CONFLICT (slug) DO UPDATE SET
                     last_seen=excluded.last_seen, headline=excluded.headline,
                     breaking=CASE WHEN live_onair.breaking=1
                                   OR excluded.breaking=1 THEN 1 ELSE 0 END""",
                (slug, channel, it["headline"], hour_key,
                 1 if it["breaking"] else 0, now_iso, now_iso))
            n += 1
    return n


def poll_web(channels: list[dict]) -> dict:
    """Scrape every channel that has a ``site`` through one stealth browser
    session, upserting headlines into live_onair. Channels without ``site`` are
    left to the YouTube-title poller."""
    sites = [c for c in channels if c.get("site")]
    if not sites:
        return {"channels": 0, "headlines": 0, "breaking": 0, "errors": []}
    if not stealth_browser.available():
        return {"channels": 0, "headlines": 0, "breaking": 0,
                "errors": ["playwright not installed"]}
    total = brk = 0
    errors = []
    with stealth_browser.stealth_page() as page:
        for c in sites:
            name = c.get("name", "?")
            try:
                items = extract_channel(page, c["site"])
                if not items:
                    errors.append(f"{name}: no headlines")
                    continue
                brk += sum(1 for i in items if i["breaking"])
                total += _upsert(name, items)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                log.warning("web poll failed for %s: %s", name, exc)
    return {"channels": len(sites), "headlines": total, "breaking": brk,
            "errors": errors}
