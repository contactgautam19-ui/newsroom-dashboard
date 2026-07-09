"""OCR every channel's live player — broadcast ground truth for the on-air panel.

The "What's on air" panel must show ONLY what a channel is actually airing.
YouTube live-stream titles are stale tag lists and channel websites push
article headlines, so neither is broadcast evidence. This module reads the
broadcast itself: it opens each channel's live player (YouTube watch page for
the five channels with stable streams; timesnownews.com's own player for Times
Now), screenshots the video frame and OCRs the lower-third chyron / top band
with tesseract. Verified working 2026-07-08: NDTV's frame OCR'd to the exact
debate band on air ("THE E20 DEBATE … HARDEEP SINGH PURI").

Local worker only (needs Playwright + tesseract; never runs on Vercel).
Rows land in ``live_onair`` with source='ocr', which the panel trusts.
"""

import hashlib
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.news import onair, timesnow_ocr

log = logging.getLogger("newsroom.live_ocr")

WATCH_URL = "https://www.youtube.com/watch?v={vid}"
MAX_PER_CHANNEL = 2          # chyron + one ticker/top-band read per pass
PLAYER_SETTLE_MS = 10000     # let the stream start painting frames
CONTROLS_FADE_MS = 3500      # controls/title overlay fade after mouse-away

# YouTube page chrome + player UI the OCR must never mistake for a chyron —
# extends the Times Now site-player junk list.
_YT_JUNK_RE = re.compile(
    r"watch later|copy link|share|shorts|live chat|skip navigation|"
    r"subscribe|sign in|search|up next|autoplay|playback|"
    r"older version of your browser|update it to use|youtube|"
    r"views watching|watching now|started streaming",
    re.I,
)

# sponsor bands / pre-roll ads that play inside the stream ("Switch To Smart
# Trading … subscriptions stoxkar com") — an ad is never an aired story
_AD_RE = re.compile(
    r"trading|subscript|demat|invest now|loan|emi\b|casino|betting|"
    r"download now|install|offer|discount|sale ends|shop now|buy now|"
    r"\b\w+\s?\.\s?(com|in|io)\b|\bcom\b",
    re.I,
)

# site navigation vocabulary — a full-page fallback screenshot OCRs the menu
# bar; 4+ hits on one line means it's a nav rail, not a chyron
_NAV_WORDS = {
    "india", "world", "entertainment", "sports", "business", "lifestyle",
    "tech", "videos", "games", "crypto", "photos", "latest", "stories",
    "home", "opinion", "astrology", "education", "health", "elections",
    "movies", "auto", "web",
}


def _nav_menu(line: str) -> bool:
    toks = re.findall(r"[a-z]+", line.lower())
    return len(toks) >= 5 and sum(t in _NAV_WORDS for t in toks) >= 4


def _is_junk(line: str) -> bool:
    return bool(timesnow_ocr._UI_JUNK_RE.search(line) or _YT_JUNK_RE.search(line)
                or _AD_RE.search(line) or _nav_menu(line))


# 1–2 char tokens that ARE real headline words, not OCR fragments
_SHORT_OK = {"a", "i", "an", "in", "on", "at", "to", "of", "by", "as", "is",
             "it", "no", "us", "uk", "vs", "pm", "cm", "ai", "ed", "mp", "up"}


def _candidate(line: str) -> bool:
    """Sentence-like broadcast band: mostly letters, several real words, no
    clock. Frame-transition mush ('ot ilo EO eet ag Te gt ieee') OCRs as runs
    of 1–2 char fragments, so demand mostly full-length words too."""
    letters = sum(c.isalpha() for c in line)
    words = line.split()
    if not (len(line) >= 14 and letters >= len(line) * 0.6
            and len(words) >= 4
            and not re.search(r"\d{1,2}:\d{2}", line)):
        return False
    # pure-punctuation tokens don't count; legit short headline words do
    cores = [c for c in (re.sub(r"[^A-Za-z0-9]", "", w) for w in words) if c]
    if len(cores) < 4:
        return False
    short = sum(1 for c in cores
                if len(c) <= 2 and c.lower() not in _SHORT_OK)
    full = sum(1 for c in cores if len(c) >= 4)
    return short <= len(cores) * 0.3 and full >= len(cores) * 0.4


def _tidy(text: str) -> str:
    """Scrub OCR grit off a band read: edge glyph noise ('i ?NEGOTIATING…',
    trailing 'Vv' play-button artifacts) that tesseract picks up around the
    graphics. Chyrons are ALL-CAPS, so a stray lowercase edge token is noise."""
    text = re.sub(r"\s+", " ", text).strip()
    # leading 1–2 char lowercase token before caps/punct = frame noise
    text = re.sub(r"^[a-z]{1,2}\s+(?=[^a-z])", "", text)
    text = re.sub(r"^[^A-Za-z0-9\"']+", "", text)
    text = re.sub(r"[^A-Za-z0-9\"'?!.%]+$", "", text)
    words = text.split()
    # trailing short token that isn't a clean word or number ('Vv', '[F',
    # 'Typ') = player-control / graphics artifact
    def _junk_tail(tok: str) -> bool:
        if tok.isdigit():
            return False
        if not tok.isalnum():
            return True
        return not tok.isupper() and not tok.islower()
    while len(words) > 3 and len(words[-1]) <= 3 and _junk_tail(words[-1]):
        words.pop()
    # in an ALL-CAPS chyron, short lowercase edge tokens ('g', 'ip') are grit
    letters = [c for c in " ".join(words) if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) >= len(letters) * 0.7:
        while len(words) > 3 and len(words[-1]) <= 2 and words[-1].islower():
            words.pop()
        while len(words) > 3 and len(words[0]) <= 2 and words[0].islower():
            words.pop(0)
    return " ".join(words)


def _continuation(line: str) -> str | None:
    """Cleaned short fragment of a wrapped chyron ('OF TIME? Vv' -> 'OF TIME?'),
    or None when the line isn't a continuation."""
    core = re.sub(r"[^A-Za-z0-9'?! ]+", " ", line)
    core = re.sub(r"\s+", " ", core).strip()
    if not (2 <= len(core) <= 30) or len(core.split()) > 5 or _is_junk(line):
        return None
    letters = sum(c.isalpha() for c in core)
    if letters < len(core) * 0.6:
        return None
    return core


def extract_headlines(ocr_text: str) -> list[dict]:
    """Best broadcast headlines from raw tesseract output (longest first)."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in ocr_text.splitlines()]
    cands: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln and not _is_junk(ln) and _candidate(ln):
            text = ln
            # re-join a chyron that wrapped onto the next line
            cont = _continuation(lines[i + 1]) if i + 1 < len(lines) else None
            if cont:
                text = f"{text} {cont}"
                i += 1
            cands.append(text)
        i += 1
    breaking_anywhere = bool(onair._BREAKING_RE.search(" ".join(lines)))
    out: list[dict] = []
    for text in sorted(cands, key=len, reverse=True)[:MAX_PER_CHANNEL]:
        # OCR catches stray '|' from frame graphics; parse_headlines would
        # split a joined chyron apart on it, so flatten pipes to spaces first
        parsed = onair.parse_headlines(text.replace("|", " "))
        headline = _tidy(parsed[0]["headline"] if parsed else text)
        if len(headline) < 14:
            continue
        out.append({"headline": headline,
                    "breaking": breaking_anywhere and not out})
    return out


def _ocr_text(png: Path) -> str:
    """Raw tesseract text of a frame ('' on failure)."""
    import subprocess
    tess = timesnow_ocr._tesseract_bin()
    if not tess:
        return ""
    try:
        proc = subprocess.run(
            [tess, str(png), "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        return proc.stdout or ""
    except Exception as exc:  # noqa: BLE001
        log.warning("tesseract failed: %s", exc)
        return ""


def _capture_youtube(page, video_id: str, png: Path) -> bool:
    """Screenshot the playing video frame of a YouTube watch page."""
    page.goto(WATCH_URL.format(vid=video_id), wait_until="domcontentloaded",
              timeout=40000)
    page.wait_for_timeout(PLAYER_SETTLE_MS)
    try:  # EU-style consent wall, when YouTube serves one
        page.locator('button[aria-label*="Accept"], '
                     'button:has-text("Accept all")').first.click(timeout=2000)
        page.wait_for_timeout(4000)
    except Exception:
        pass
    # park the mouse so the title overlay + controls fade off the frame
    page.mouse.move(5, 400)
    page.wait_for_timeout(CONTROLS_FADE_MS)
    video = page.locator("video").first
    if not video.count() or not video.is_visible():
        return False
    video.screenshot(path=str(png), timeout=10000)
    return png.exists() and png.stat().st_size > 0


def _capture_site_player(page, url: str, png: Path) -> bool:
    """Screenshot the player on a channel's own live-TV page (Times Now).

    Strictly the <video> frame — a full-page fallback would OCR the site's
    nav menu and article rails, which is exactly what the panel must not show."""
    page.goto(url, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(9000)
    video = page.locator("video").first
    if not video.count() or not video.is_visible():
        return False
    video.screenshot(path=str(png), timeout=8000)
    return png.exists() and png.stat().st_size > 0


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
                   VALUES (?,?,?,?,?,?,?,'ocr')
                   ON CONFLICT (slug) DO UPDATE SET
                     last_seen=excluded.last_seen, headline=excluded.headline,
                     breaking=CASE WHEN live_onair.breaking=1
                                   OR excluded.breaking=1 THEN 1 ELSE 0 END""",
                (slug, channel, it["headline"], hour_key,
                 1 if it["breaking"] else 0, now_iso, now_iso))
            n += 1
    return n


def run_ocr_cycle() -> dict:
    """OCR every channel's live player once; upsert what's actually on air.

    Returns {channels, headlines, breaking, errors, reads:[(channel, head)]}.
    """
    if not timesnow_ocr._playwright_available():
        return {"channels": 0, "headlines": 0, "breaking": 0,
                "errors": ["playwright not installed"], "reads": []}
    if not timesnow_ocr._tesseract_bin():
        return {"channels": 0, "headlines": 0, "breaking": 0,
                "errors": ["tesseract not found"], "reads": []}

    from playwright.sync_api import sync_playwright
    streams = onair.load_streams()
    total = brk = 0
    errors: list[str] = []
    reads: list[tuple[str, str]] = []

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        for st in streams:
            name = st.get("name", "?")
            png = Path(tmp) / f"{name.replace(' ', '_')}.png"
            try:
                if name == "Times Now" and not st.get("video_id"):
                    # rotating YT streams — its own site player is the stable feed
                    ok = _capture_site_player(page, timesnow_ocr.LIVE_URL, png)
                else:
                    vid = st.get("video_id")
                    ok = bool(vid) and _capture_youtube(page, vid, png)
                    if not ok and st.get("channel_id"):
                        # pinned stream rotated/ended — resolve the current one
                        live_vid = onair.resolve_live_video_id(st["channel_id"])
                        ok = bool(live_vid) and _capture_youtube(page, live_vid, png)
                if not ok:
                    errors.append(f"{name}: no frame")
                    continue
                items = extract_headlines(_ocr_text(png))
                if not items:
                    errors.append(f"{name}: no readable band")
                    continue
                total += _upsert(name, items)
                brk += sum(1 for i in items if i["breaking"])
                reads.append((name, items[0]["headline"]))
            except Exception as exc:  # keep going: one channel must not kill the pass
                errors.append(f"{name}: {str(exc)[:80]}")
                log.warning("live OCR failed for %s: %s", name, exc)
        browser.close()

    return {"channels": len(streams), "headlines": total, "breaking": brk,
            "errors": errors, "reads": reads}
