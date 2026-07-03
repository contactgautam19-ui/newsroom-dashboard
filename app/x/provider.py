"""XProvider interface — the seam where real scraping layers plug in.

The pipeline treats providers as an ordered fallback chain (Master Prompt:
twscrape -> Nitter/RSS -> Playwright). Today only SimulatedProvider is
functional; the real layers live as documented stubs in stubs.py.
"""

from abc import ABC, abstractmethod

from app.x.models import Tweet


class XProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_new_tweets(self, handles: list[dict]) -> list[Tweet]:
        """Return tweets newer than the last call for the given handle records.

        Raise ProviderUnavailable on rate-limits/network failure so the
        pipeline can down-shift to the next layer.
        """


class ProviderUnavailable(Exception):
    pass
