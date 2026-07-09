"""Local always-on on-air worker.

Vercel has no background scheduler, so the "What's on air — by the hour" panel
only accumulates history while someone has the dashboard open. Run this on any
always-on machine to build the broadcast record: it OCRs every channel's live
player (the ground truth the panel shows), polls the channels' X accounts for
aired-story posts on a budget, and keeps the title/web layers running for the
Alerts feed and rival-coverage matching.

    python live_worker.py                 # loop, ~every 5 min, titles+web only
    python live_worker.py --once          # a single cycle then exit
    python live_worker.py --ocr           # also OCR every channel's live player
    python live_worker.py --interval 5    # override the cadence (minutes)
    python live_worker.py --x-every 120   # channel-X poll cadence, min (0=off)

It targets Supabase when DATABASE_URL_DEPLOY is set in .env (so the live site
sees the data); otherwise it writes the local SQLite dev DB. It deliberately
does NOT set VERCEL, so the richer /live resolution runs.
"""

import argparse
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

# Point at the production DB (Supabase) when its URL is provided, so the worker
# feeds the same database the deployed dashboard reads. Must happen before app
# modules import config.
_deploy_db = os.getenv("DATABASE_URL_DEPLOY")
if _deploy_db and not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = _deploy_db
os.environ.pop("VERCEL", None)  # ensure non-serverless: enable /live resolution


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def _ascii(text: str) -> str:
    # stdout may be a cp1252-encoded log file on Windows
    return text.encode("ascii", "replace").decode()


def cycle(no_web: bool, with_ocr: bool, x_every_min: int) -> None:
    from app.news import onair

    # 1. channels' X accounts — aired-story posts ("… shares more details with
    #    …", breaking tags, segment videos). Spends TwtAPI budget (~300 calls a
    #    month), so gated to one call per x_every_min and to broadcast hours
    #    (06–23 IST). The last-call clock is persisted in settings, so worker
    #    restarts and serverless refreshes share a single budget.
    ist_hour = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).hour
    if x_every_min > 0 and 6 <= ist_hour <= 23:
        from app.news import channel_x
        xr = channel_x.poll_channel_x(min_interval_min=x_every_min)
        if xr.get("throttled"):
            pass  # inside the budget window — quiet skip
        elif xr.get("ok"):
            print(f"[{_now()}] channel-X: {xr['headlines']} aired posts"
                  f" - {xr.get('breaking', 0)} breaking"
                  f" - {xr.get('channels', 0)} channels", flush=True)
        else:
            print(f"[{_now()}] channel-X skipped: {xr.get('reason')}", flush=True)

    # 2. lightweight YouTube-title baseline (keyless, fast; feeds alerts +
    #    rival matching — NOT the on-air panel, which trusts only ocr/x rows)
    stats = onair.poll_onair()
    line = (f"[{_now()}] titles: {stats['headlines']} headlines"
            f" - {stats['breaking']} breaking - {stats['streams']} streams")
    if stats["errors"]:
        line += f" - errors: {', '.join(stats['errors'])}"
    print(_ascii(line), flush=True)

    # 3. stealth web scrape — channel-site headlines (alerts + matching signal)
    if not no_web:
        from app.news import web_extract
        w = web_extract.poll_web(onair.load_streams())
        wline = (f"[{_now()}] web:    {w['headlines']} headlines"
                 f" - {w['breaking']} breaking - {w['channels']} sites")
        if w["errors"]:
            wline += f" - errors: {', '.join(w['errors'])}"
        print(_ascii(wline), flush=True)

    # 4. live-player OCR — the broadcast ground truth the panel displays
    if with_ocr:
        from app.news import live_ocr
        res = live_ocr.run_ocr_cycle()
        line = (f"[{_now()}] OCR:    {res['headlines']} reads"
                f" - {res['breaking']} breaking"
                f" - {len(res.get('reads', []))}/{res['channels']} channels")
        if res.get("errors"):
            line += f" - misses: {', '.join(res['errors'])}"
        print(_ascii(line), flush=True)
        for chan, head in res.get("reads", []):
            print(_ascii(f"        {chan}: {head}"), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="On-air live monitor worker")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--no-web", action="store_true",
                    help="skip the stealth website scrape (titles only)")
    ap.add_argument("--x-every", type=int, default=120, metavar="MIN",
                    help="minutes between channel-X polls (TwtAPI budget; 0=off)")
    ap.add_argument("--ocr", action="store_true",
                    help="also OCR every channel's live player")
    ap.add_argument("--interval", type=float, default=5.0, help="minutes between cycles")
    args = ap.parse_args()

    from app import config, db
    db.init_db()
    target = "Supabase" if db.IS_PG else f"SQLite ({config.DB_PATH})"
    # ASCII only: stdout may be a cp1252-encoded log file on Windows
    print(f"on-air worker -> {target} | every {args.interval} min"
          f"{' | +live-player OCR' if args.ocr else ''}"
          f"{f' | X every {args.x_every}m' if args.x_every > 0 else ''}", flush=True)

    while True:
        try:
            cycle(args.no_web, args.ocr, args.x_every)
        except Exception as exc:  # keep the loop alive across transient failures
            print(_ascii(f"[{_now()}] cycle error: {exc}"), flush=True)
        if args.once:
            break
        time.sleep(max(30.0, args.interval * 60))


if __name__ == "__main__":
    main()
