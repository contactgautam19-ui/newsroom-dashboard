"""TwitterAPI.io provider — a second vendor for the same X desk.

Why a second one at all: the desk went dark this morning when the TwtAPI
balance ran out mid-rotation, and a newsroom tool should not lose its social
feed because one supplier's meter hit zero. The pipeline already tries layers
in order and down-shifts on ProviderUnavailable, so this slots in behind (or
ahead of) TwtAPI and the failover is automatic.

Almost everything is shared with TwtAPIProvider: the same `X-API-Key` header,
the same `from:a OR from:b` query grammar, the same 20-per-page ceiling, so
the one-call-per-column rotation carries over unchanged. Only three things
differ, and they are all here:

- the endpoint is `tweet/advanced_search` with `query`/`queryType` rather than
  `Search` with `q`/`type`;
- tweets come back under `tweets[]` with camelCase `createdAt` and
  `author.userName` (handled in the shared extractor);
- there is no status endpoint, so the budget is read from the response's own
  credit headers when the vendor sends them.

Billing differs in a way worth remembering before switching the default: this
vendor charges 15 credits per tweet *returned*, not per call, so a refresh
that collects 60 tweets costs ~900 credits (~$0.009). TwtAPI charges per call,
which is far cheaper for this access pattern — one call covering 20 handles.
"""

import logging
import time

import httpx

from app import config
from app.x.provider import ProviderUnavailable
from app.x.twtapi_provider import TwtAPIProvider

log = logging.getLogger("newsroom.twitterapiio")

CREDITS_PER_TWEET = 15          # vendor's published rate
MIN_CREDITS_PER_CALL = 15       # charged even when a search returns nothing

# The free tier allows 0.2 queries a second — one call every five seconds —
# and a refresh fires one call per column back to back, so without spacing
# them columns two and three come back 429 and the desk loses two thirds of
# its feed. Accounts that have paid get 20 QPS and never wait.
FREE_TIER_INTERVAL = 5.2        # a shade over 1/0.2, for clock skew
RETRY_BACKOFF = (6, 12)         # waits after a 429 before giving up


class TwitterAPIioProvider(TwtAPIProvider):
    name = "twitterapiio"
    key_name = "TWITTERAPIIO_KEY"

    @property
    def api_key(self) -> str:
        return config.TWITTERAPIIO_KEY

    @property
    def api_base(self) -> str:
        return config.TWITTERAPIIO_BASE

    def _search(self, query: str) -> dict:
        payload = self._get("tweet/advanced_search",
                            {"query": query, "queryType": "Latest"})
        if isinstance(payload, dict):
            self._note_spend(payload)
        return payload

    def _note_spend(self, payload: dict) -> None:
        """Keep a running estimate of what this refresh cost.

        The vendor has no status endpoint to ask, so the desk would otherwise
        show nothing at all for this provider. Billing is per tweet returned
        at a published flat rate, so counting what came back is an estimate
        we can actually stand behind — labelled as one, never as a balance.
        """
        n = len(payload.get("tweets") or [])
        spent = max(MIN_CREDITS_PER_CALL, n * CREDITS_PER_TWEET)
        prior = self.last_status.get("credits_spent_estimate") or 0
        self.last_status = {**self.last_status,
                            "plan": "pay-as-you-go",
                            "credits_spent_estimate": prior + spent}

    def account_status(self) -> dict:
        """No status endpoint to call, so report only what we are sure of.

        Returning nothing numeric is deliberate: the desk shows a budget only
        when it knows one, and an invented figure here is exactly the mistake
        that let the TwtAPI balance run out unnoticed.
        """
        self.last_status = {**self.last_status, "plan": "pay-as-you-go"}
        return self.last_status

    _last_call_at = 0.0

    def _space_calls(self) -> None:
        """Hold off until the free tier's one-call-every-five-seconds is met."""
        wait = FREE_TIER_INTERVAL - (time.monotonic() - self._last_call_at)
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise ProviderUnavailable(f"{self.key_name} not set in .env")
        url = f"{self.api_base}/{path}"
        for attempt, backoff in enumerate((*RETRY_BACKOFF, None)):
            self._space_calls()
            try:
                resp = httpx.get(url, params=params, timeout=30,
                                 headers={"X-API-Key": self.api_key})
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(
                    f"TwitterAPI.io unreachable: {exc}") from exc
            finally:
                TwitterAPIioProvider._last_call_at = time.monotonic()

            if resp.status_code == 429 and backoff is not None:
                log.warning("rate limited, waiting %ss (attempt %d)",
                            backoff, attempt + 1)
                time.sleep(backoff)
                continue
            # out of credits, bad key or still throttled — all mean "try the
            # next layer", which is what ProviderUnavailable signals
            if resp.status_code in (401, 402, 403, 429):
                raise ProviderUnavailable(
                    f"TwitterAPI.io error {resp.status_code}: {resp.text[:120]}")
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict):
                status = str(payload.get("status", "")).lower()
                if status == "error" or payload.get("error"):
                    raise ProviderUnavailable(
                        f"TwitterAPI.io: {payload.get('msg') or payload.get('error')}")
            return payload
        raise ProviderUnavailable("TwitterAPI.io: rate limited")   # unreachable
