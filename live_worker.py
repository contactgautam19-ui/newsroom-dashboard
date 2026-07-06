"""Local always-on on-air worker.

Vercel has no background scheduler, so the "What's on air — by the hour" panel
only accumulates history while someone has the dashboard open. Run this on any
always-on machine to poll every news channel's LIVE stream title on a fixed
cadence and write it straight into the shared database — giving the panel real,
gap-free hourly history. It also does the /live resolution that the serverless
path skips (self-healing stale pins, Times Now), and can optionally OCR the
Times Now web player as a broadcast-accurate cross-check.

    python live_worker.py                 # loop, ~every 3 min, YouTube titles
    python live_worker.py --once          # a single cycle then exit
    python live_worker.py --ocr           # also OCR Times Now (needs playwright)
    python live_worker.py --interval 5    # override the cadence (minutes)

It targets Supabase when DATABASE_URL_DEPLOY is set in .env (so the live site
sees the data); otherwise it writes the local SQLite dev DB. It deliberately
does NOT set VERCEL, so the richer /live resolution runs.
"""

import argparse
import os
import time
from datetime import datetime, timezone

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


def cycle(with_ocr: bool) -> None:
    from app.news import onair
    stats = onair.poll_onair()
    line = (f"[{_now()}] titles: {stats['headlines']} headlines"
            f" · {stats['breaking']} breaking · {stats['streams']} streams")
    if stats["errors"]:
        line += f" · errors: {', '.join(stats['errors'])}"
    print(line, flush=True)

    if with_ocr:
        from app.news import timesnow_ocr
        res = timesnow_ocr.run_ocr_cycle()
        if res.get("ok"):
            print(f"[{_now()}] Times Now OCR: "
                  f"{'🔴 ' if res.get('breaking') else ''}{res['headline']}", flush=True)
        else:
            print(f"[{_now()}] Times Now OCR skipped: {res.get('reason')}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="On-air live monitor worker")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--ocr", action="store_true", help="also OCR Times Now web player")
    ap.add_argument("--interval", type=float, default=3.0, help="minutes between cycles")
    args = ap.parse_args()

    from app import config, db
    db.init_db()
    target = "Supabase" if db.IS_PG else f"SQLite ({config.DB_PATH})"
    print(f"on-air worker → {target} · every {args.interval} min"
          f"{' · +Times Now OCR' if args.ocr else ''}", flush=True)

    while True:
        try:
            cycle(args.ocr)
        except Exception as exc:  # keep the loop alive across transient failures
            print(f"[{_now()}] cycle error: {exc}", flush=True)
        if args.once:
            break
        time.sleep(max(30.0, args.interval * 60))


if __name__ == "__main__":
    main()
