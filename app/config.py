import os
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


DB_PATH = BASE_DIR / "newsroom.db"
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
BRIEF_OUT_DIR = BASE_DIR / "briefings_out"

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
BRIEF_RECIPIENT = os.getenv("BRIEF_RECIPIENT", "gautam.news9@gmail.com")
EMAIL_ENABLED = _bool("EMAIL_ENABLED", False)

ACTIVE_WINDOW_START = _int("ACTIVE_WINDOW_START", 6)   # 6:00 AM
ACTIVE_WINDOW_END = _int("ACTIVE_WINDOW_END", 21)      # 9:00 PM

NEWS_RSS_URL = os.getenv(
    "NEWS_RSS_URL", "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
)
STORIES_PER_CYCLE = _int("STORIES_PER_CYCLE", 5)

X_PROVIDER = os.getenv("X_PROVIDER", "sim")
SIM_TWEETS_PER_MIN = _int("SIM_TWEETS_PER_MIN", 12)

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
