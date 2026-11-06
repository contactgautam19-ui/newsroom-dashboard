import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Deployment / storage mode
#   DATABASE_URL set   -> Postgres (Supabase) via psycopg2 (see app/db.py)
#   DATABASE_URL unset -> local SQLite dev (default, unchanged)
DATABASE_URL = os.getenv("DATABASE_URL", "")
# Vercel injects VERCEL=1 in its serverless runtime. In serverless mode there
# are no background threads (scheduler), no SSE, and the filesystem is read-only
# except /tmp — refresh loops are driven by the /api/cron/* endpoints instead.
IS_SERVERLESS = bool(os.getenv("VERCEL"))
CRON_SECRET = os.getenv("CRON_SECRET", "")   # guards /api/cron/* endpoints
PASSCODE = os.getenv("PASSCODE", "")          # shared-gate passcode (empty = off)

DB_PATH = BASE_DIR / "newsroom.db"
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
# Serverless filesystem is read-only except /tmp, so briefs are written there.
BRIEF_OUT_DIR = (
    Path(tempfile.gettempdir()) if IS_SERVERLESS else BASE_DIR / "briefings_out"
)

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
BRIEF_RECIPIENT = os.getenv("BRIEF_RECIPIENT", "gautam.news9@gmail.com")
EMAIL_ENABLED = _bool("EMAIL_ENABLED", False)
APP_URL = os.getenv("APP_URL", "https://newsroom-dashboard-three.vercel.app")

ACTIVE_WINDOW_START = _int("ACTIVE_WINDOW_START", 6)   # 6:00 AM
ACTIVE_WINDOW_END = _int("ACTIVE_WINDOW_END", 21)      # 9:00 PM

NEWS_RSS_URL = os.getenv(
    "NEWS_RSS_URL", "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
)
STORIES_PER_CYCLE = _int("STORIES_PER_CYCLE", 5)

# twtapi / twitterapiio = real tweets, sim = demo feed. Whichever real vendor
# is named goes first; the other follows as a fallback when its key is set, so
# an exhausted balance or a dead vendor down-shifts instead of going dark.
X_PROVIDER = os.getenv("X_PROVIDER", "twtapi")
SIM_TWEETS_PER_MIN = _int("SIM_TWEETS_PER_MIN", 12)

# TwtAPI (api.twtapi.com) — real tweet text, manual refresh only to respect
# the small monthly call budget. One Search call covers ~20 handles via
# "from:a OR from:b" chains; a refresh spends one call per column (3 total).
TWT_API_KEY = os.getenv("TWT_API_KEY", "")
TWT_API_BASE = os.getenv("TWT_API_BASE", "https://api.twtapi.com/api/v1/twitter")
TWT_STATUS_URL = os.getenv("TWT_STATUS_URL", "https://api.twtapi.com/myapi/status")

# TwitterAPI.io — the same query grammar and the same X-API-Key header, so it
# reuses the one-call-per-column design. It bills per tweet returned rather
# than per call, so a refresh costs ~900 credits where TwtAPI charges 3 calls.
TWITTERAPIIO_KEY = os.getenv("TWITTERAPIIO_KEY", "")
TWITTERAPIIO_BASE = os.getenv("TWITTERAPIIO_BASE", "https://api.twitterapi.io/twitter")
# YouTube Data API v3 key (Hyper Search: most-popular News & Politics videos).
# Free quota: 10,000 units/day, one unit per scan. Empty = YouTube is skipped.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
X_HANDLES_PER_COLUMN = _int("X_HANDLES_PER_COLUMN", 20)  # top-trust handles searched per column

# Keyword-driven fresh-story discovery (mirrors the manual editor workflow:
# keyword -> Google News -> "Past hour" filter)
NEWS_SEARCH_URL = os.getenv(
    "NEWS_SEARCH_URL",
    "https://news.google.com/rss/search?q={query}+when:1h&hl=en-IN&gl=IN&ceid=IN:en",
)
GOOGLE_TRENDS_RSS = os.getenv(
    "GOOGLE_TRENDS_RSS", "https://trends.google.com/trending/rss?geo=IN"
)
DISCOVERY_KEYWORDS = _int("DISCOVERY_KEYWORDS", 8)   # keywords searched per cycle
FRESHNESS_HOURS = _int("FRESHNESS_HOURS", 1)         # drop new candidates older than this
RETIRE_HOURS = _int("RETIRE_HOURS", 2)               # retire board stories older than this
BOARD_WINDOW_HOURS = _int("BOARD_WINDOW_HOURS", 1)   # board shows only stories newer than this (strict)
X_TERM_WINDOW_MIN = _int("X_TERM_WINDOW_MIN", 30)    # X-desk hot-term lookback

# Auto-tracking: a story that keeps running across scans and keeps being
# picked up by more outlets is one the desk should be following, and asking an
# editor to notice that by eye is how a running story gets missed. The test is
# deliberately "sustained AND corroborated", not "loud once": on 6 November
# 483 signals were raised in twelve hours, 383 of them re-seen on a later
# scan, and only 93 carried by five outlets or more. Urgency alone would open
# a track on every fire in Delhi.
AUTO_TRACK_ENABLED = _bool("AUTO_TRACK_ENABLED", True)
AUTO_TRACK_URGENCY = _int("AUTO_TRACK_URGENCY", 70)      # running peak, 0-100
AUTO_TRACK_OUTLETS = _int("AUTO_TRACK_OUTLETS", 4)       # distinct outlets carrying it
AUTO_TRACK_MIN_MINUTES = _int("AUTO_TRACK_MIN_MINUTES", 60)   # ran at least this long
# ...and not much longer. A headline still being filed twenty hours on is not
# a story that keeps developing, it is a service or evergreen piece being
# syndicated round the clock: on 6 November "Gold 10 gm or Silver 1 kg? What
# to buy on Dhanteras" had run 1,243 minutes across seven outlets and passed
# every other test here.
AUTO_TRACK_MAX_HOURS = _int("AUTO_TRACK_MAX_HOURS", 12)
AUTO_TRACK_MAX_PER_DAY = _int("AUTO_TRACK_MAX_PER_DAY", 6)    # so the panel cannot flood
AUTO_TRACK_PER_SCAN = _int("AUTO_TRACK_PER_SCAN", 2)          # each costs a backfill search

# Ranking / broker thresholds (from the PRD and Master Prompt)
CONFIDENCE_REVIEW_THRESHOLD = 70          # below this -> NEEDS REVIEW flag
SPIKE_THRESHOLD_PCT = 150                 # >150% volume spike in rolling window
SPIKE_WINDOW_SECONDS = 300                # rolling 5-minute window
BROKER_TICK_SECONDS = 60                  # term aggregation cadence
HIGH_DEMAND_POSTS_PER_HOUR = 5000         # confidence override threshold
TREND_BOOST_MIN = 1
TREND_BOOST_MAX = 10
TREND_MOMENTUM_CAP = 15                   # Search Trend Momentum max points
FALLBACK_COOLDOWN_SECONDS = 300           # 5-minute re-ingest cooldown
REPETITIVE_DECAY_PER_HOUR = 3             # points lost per stale hour
REPETITIVE_DECAY_CAP = 15                 # decay never exceeds this
NEWS_REFRESH_MINUTES = _int("NEWS_REFRESH_MINUTES", 10)  # auto story refresh (0 = hourly only)

# Live rival-TV coverage monitor: rival channels' YouTube uploads feeds mirror
# their on-air rundown within minutes, so polling them flags what rivals are
# airing and surfaces topics our board is missing as priority keywords.
LIVE_POLL_MINUTES = _int("LIVE_POLL_MINUTES", 5)
LIVE_WINDOW_HOURS = _int("LIVE_WINDOW_HOURS", 3)
