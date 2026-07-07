"""Times Now web-player OCR cross-check (local worker only).

Times Now runs rotating YouTube live streams (often event-specific), so the
title-based monitor can miss its rolling news desk. This reads the actual
broadcast: it renders timesnownews.com/live-tv in a headless browser, screenshots
the player, and OCRs the lower-third / ticker band with tesseract to recover the
on-air headline and any BREAKING banner. Results are upserted into ``live_onair``
under channel "Times Now" so they sit beside the YouTube-title data.

Heavy + fragile by nature (needs Playwright + Chromium, and OCR of a live video
is noisy), so it is optional and never runs on Vercel — only from live_worker.py
with ``--ocr``. Requires: ``pip install playwright && playwright install chromium``
and the ``tesseract`` binary on PATH.
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.news import onair

log = logging.getLogger("newsroom.timesnow_ocr")

LIVE_URL = "https://www.timesnownews.com/live-tv"
CHANNEL = "Times Now"

# common Windows install location winget/the UB-Mannheim installer uses, in case
# tesseract isn't added to PATH
_TESSERACT_FALLBACKS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _tesseract_bin() -> str | None:
    """Path to the tesseract executable (PATH first, then known install dirs)."""
    found = shutil.which("tesseract")
    if found:
        return found
    for path in _TESSERACT_FALLBACKS:
        if os.path.exists(path):
            return path
    return None


def capture_frame(png_path: Path, timeout_ms: int = 30000) -> bool:
    """Render the live-tv page and screenshot the player region. Returns False if
    Playwright isn't installed or the page never produced a video frame."""
    if not _playwright_available():
        log.warning("playwright not installed — Times Now OCR skipped")
        return False
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            # give the JS player time to attach and start decoding frames
            page.wait_for_timeout(9000)
            target = page.locator("video").first
            try:
                if target.count():
                    target.screenshot(path=str(png_path), timeout=8000)
                else:
                    page.screenshot(path=str(png_path))
            except Exception:
                page.screenshot(path=str(png_path))
            browser.close()
        return png_path.exists() and png_path.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("Times Now capture failed: %s", exc)
        return False


# Player chrome / site UI text that OCR picks up but is not broadcast content
# ("Free watch time left: 04:55", sign-in nags, cookie bars, player controls).
_UI_JUNK_RE = re.compile(
    r"free watch|time left|watch time|sign in|log ?in|subscribe|register|"
    r"cookie|privacy|advert|download (the )?app|install app|notification|"
    r"volume|mute|settings|quality|fullscreen|buffer|loading|live tv online|"
    r"watch (live )?tv|continue watching|premium|paywall",
    re.I,
)


def ocr_headline(png_path: Path) -> dict | None:
    """OCR the lower band of the screenshot and return the best headline line."""
    tess = _tesseract_bin()
    if not tess:
        log.warning("tesseract not found — Times Now OCR skipped")
        return None
    try:
        proc = subprocess.run(
            [tess, str(png_path), "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        out = proc.stdout or ""
        if not out.strip():
            log.warning("tesseract empty output (rc=%s): %s",
                        proc.returncode, (proc.stderr or "")[:200])
            return None
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tesseract failed: %s", exc)
        return None

    lines = [re.sub(r"\s+", " ", ln).strip() for ln in out.splitlines()]
    # a chyron headline: mostly letters, several words, not UI chrome
    cands = []
    for ln in lines:
        if _UI_JUNK_RE.search(ln):
            continue
        letters = sum(c.isalpha() for c in ln)
        # a real chyron is a sentence-like band: mostly letters, 4+ words, and
        # not dominated by clock/countdown digits
        if (len(ln) >= 14 and letters >= len(ln) * 0.6
                and len(ln.split()) >= 4
                and not re.search(r"\d{1,2}:\d{2}", ln)):
            cands.append(ln)
    if not cands:
        return None
    headline = max(cands, key=len)
    breaking = bool(onair._BREAKING_RE.search(" ".join(lines)))
    # clean like the title parser does
    parsed = onair.parse_headlines(headline)
    headline = parsed[0]["headline"] if parsed else headline
    return {"headline": headline, "breaking": breaking}


def run_ocr_cycle() -> dict:
    """Capture + OCR Times Now, upserting the recovered headline into live_onair."""
    now = datetime.now(timezone.utc)
    hour_key = onair._hour_key(now.astimezone(onair.IST))
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "timesnow.png"
        if not capture_frame(png):
            return {"ok": False, "reason": "capture failed / playwright missing"}
        item = ocr_headline(png)
    if not item:
        return {"ok": False, "reason": "no headline read"}

    slug = hashlib.sha1(
        f"{CHANNEL}|{item['headline'].lower()}|{hour_key}".encode()
    ).hexdigest()[:16]
    now_iso = now.isoformat()
    with db.connect() as con:
        con.execute(
            """INSERT INTO live_onair
               (slug, channel, headline, hour_key, breaking, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT (slug) DO UPDATE SET
                 last_seen=excluded.last_seen, headline=excluded.headline,
                 breaking=CASE WHEN live_onair.breaking=1
                               OR excluded.breaking=1 THEN 1 ELSE 0 END""",
            (slug, CHANNEL, item["headline"], hour_key,
             1 if item["breaking"] else 0, now_iso, now_iso),
        )
    return {"ok": True, **item}
