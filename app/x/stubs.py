"""Real scraping layer stubs — the documented plug-in points for later phases.

The X Intelligence spec calls for a three-tier zero-API fallback chain:
  1. twscrape (needs X account cookies; `pip install twscrape`, add accounts
     via `twscrape add_accounts` then `twscrape login_accounts`)
  2. Self-hosted Nitter / RSS bridges (most public instances are dead; expect
     to self-host with valid session tokens)
  3. Headless Playwright automation (`pip install playwright && playwright
     install chromium`), slowest but most resilient layer

Each should return `list[Tweet]` newer than its last high-water mark and raise
ProviderUnavailable on rate-limit/network failure so the pipeline down-shifts.
"""

from app.x.models import Tweet
from app.x.provider import ProviderUnavailable, XProvider


class TwscrapeProvider(XProvider):
    name = "twscrape"

    def fetch_new_tweets(self, handles: list[dict]) -> list[Tweet]:
        # Planned: twscrape API pool with rolling cookie/auth rotation.
        #   from twscrape import API; api = API(); api.pool.add_account(...)
        #   async for tweet in api.user_tweets(user_id): ...
        raise ProviderUnavailable("twscrape layer not configured (no account pool)")


class NitterProvider(XProvider):
    name = "nitter-rss"

    def fetch_new_tweets(self, handles: list[dict]) -> list[Tweet]:
        # Planned: GET {instance}/{handle}/rss across a rotating instance list,
        # parse with feedparser, exponential backoff per instance (2m/5m/15m).
        raise ProviderUnavailable("no healthy Nitter instances configured")


class PlaywrightProvider(XProvider):
    name = "playwright"

    def fetch_new_tweets(self, handles: list[dict]) -> list[Tweet]:
        # Planned: headless chromium contexts simulating human scrolls over
        # x.com profiles, DOM extraction, per-context proxy rotation.
        raise ProviderUnavailable("playwright layer not implemented")
