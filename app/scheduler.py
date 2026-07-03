"""Background job wiring (APScheduler).

- Hourly ingest at the top of the hour, 6:00-21:00 (PRD active window)
- Fallback loop: an all-zero ingest cycle schedules a one-shot retry in 5 min
- X pipeline poll every 5s (simulated feed cadence)
- Conversation Broker tick every 60s
- Hourly email brief at :58 within the active window
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app import broker, config
from app.news import ingest
from app.x.pipeline import pipeline

log = logging.getLogger("newsroom.scheduler")
scheduler = BackgroundScheduler()


def in_active_window() -> bool:
    return config.ACTIVE_WINDOW_START <= datetime.now().hour < config.ACTIVE_WINDOW_END


def hourly_ingest_job() -> None:
    if not in_active_window():
        log.info("outside active window, skipping ingest")
        return
    result = ingest.run_ingest_cycle()
    if result.get("ok") and result.get("max_score") == 0:
        # PRD fallback loop: all-zero cycle -> 5-minute cooldown, run again
        run_at = datetime.now() + timedelta(seconds=config.FALLBACK_COOLDOWN_SECONDS)
        scheduler.add_job(hourly_ingest_job, "date", run_date=run_at,
                          id="ingest_cooldown_retry", replace_existing=True)
        log.info("all-zero cycle; cooldown retry scheduled for %s", run_at)


def x_poll_job() -> None:
    pipeline.poll()


def broker_job() -> None:
    broker.run_broker_tick()


def briefing_job() -> None:
    if not in_active_window():
        return
    from app import briefing
    briefing.generate_and_send()


def start() -> None:
    scheduler.add_job(hourly_ingest_job, "cron", minute=0, id="hourly_ingest")
    scheduler.add_job(x_poll_job, "interval", seconds=5, id="x_poll")
    scheduler.add_job(broker_job, "interval",
                      seconds=config.BROKER_TICK_SECONDS, id="broker_tick")
    scheduler.add_job(briefing_job, "cron", minute=58, id="hourly_brief")
    scheduler.start()


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
