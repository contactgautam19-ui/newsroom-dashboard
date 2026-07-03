"""TwtAPI (api.twtapi.com) provider — real tweet text from monitored handles.

Budget-first design for a small monthly call allowance:
- One Search call per stream column, covering the column's top-trust handles
  with an OR-chain of `from:` operators (~20 handles per call, 3 calls per
  refresh). No polling — refreshes are user-triggered from the dashboard.
- /myapi/status (an account endpoint, not a billed Twitter call) reports the
  remaining monthly budget, surfaced in the UI after every refresh.

The exact response shape isn't publicly documented, so `_extract_tweets`
tolerates the X API v2 layout ({data, includes.users}) and the common
RapidAPI-proxy layouts ({timeline}/{tweets} with legacy created_at). If a
payload doesn't parse, a sample is written to briefings_out/ for inspection
and the refresh reports the failure — no content is ever invented.
"""

import json
import logging
from datetime import datetime, timezone

import httpx

from app import config
from app.x.models import Tweet
from app.x.provider import ProviderUnavailable, XProvider
from app.x.sim_provider import extract_terms

log = logging.getLogger("newsroom.twtapi")


def _parse_time(value) -> str:
    """ISO-8601 or Twitter legacy ('Wed Oct 10 20:19:24 +0000 2018') -> ISO."""
    if not value:
        return datetime.now(timezone.utc).isoformat()
    value = str(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


class TwtAPIProvider(XProvider):
    name = "twtapi"

    def __init__(self):
        self.last_status: dict = {}

    def _get(self, path: str, params: dict) -> dict:
        if not config.TWT_API_KEY:
            raise ProviderUnavailable("TWT_API_KEY not set in .env")
        resp = httpx.get(
            f"{config.TWT_API_BASE}/{path}", params=params, timeout=30,
            headers={"X-API-Key": config.TWT_API_KEY},
        )
        resp.raise_for_status()
        payload = resp.json()
        # TwtAPI wraps errors as {"code": 401/402/429, "msg": ...} with HTTP 200
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, 200):
            raise ProviderUnavailable(
                f"TwtAPI error {payload.get('code')}: {payload.get('msg')}"
            )
        return payload

    def account_status(self) -> dict:
        """Plan / monthly usage; account endpoint, not a billed Twitter call."""
        try:
            resp = httpx.get(config.TWT_STATUS_URL, timeout=15,
                             headers={"X-API-Key": config.TWT_API_KEY})
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            self.last_status = {
                "plan": data.get("plan"),
                "month_calls": data.get("month_calls"),
                "monthly_remaining": data.get("monthly_remaining"),
                "credits": data.get("credits"),
            }
        except Exception as exc:
            log.warning("status fetch failed: %s", exc)
        return self.last_status

    def _extract_tweets(self, payload, handle_map: dict) -> list[Tweet]:
        tweets: list[Tweet] = []

        def add(tid, text, created, username, avatar=""):
            if not tid or not text or not username:
                return
            rec = handle_map.get(username.lower().lstrip("@"))
            if rec is None:
                return  # a from: search should only return monitored handles
            tweets.append(Tweet(
                id=str(tid),
                handle=rec["handle"],
                display_name=rec["name"],
                text=str(text).strip(),
                created_at=_parse_time(created),
                stream_column=rec["stream_column"],
                trust_score=rec["trust_score"],
                terms=extract_terms(str(text)),
                avatar_url=avatar or "",
            ))

        # TwtAPI's own normalization (verified live 2026-07-03): X GraphQL
        # tweets under _normalized.tweets[].result with legacy fields
        if isinstance(payload, dict) and isinstance(
                payload.get("_normalized", {}).get("tweets"), list):
            for entry in payload["_normalized"]["tweets"]:
                res = (entry or {}).get("result", {})
                if res.get("__typename") == "TweetWithVisibilityResults":
                    res = res.get("tweet", {})
                legacy = res.get("legacy", {})
                user = (res.get("core", {}).get("user_results", {})
                        .get("result", {}))
                username = (user.get("core", {}).get("screen_name")
                            or user.get("legacy", {}).get("screen_name") or "")
                avatar = (user.get("avatar", {}).get("image_url")
                          or user.get("legacy", {}).get("profile_image_url_https") or "")
                add(res.get("rest_id") or entry.get("rest_id"),
                    legacy.get("full_text"),
                    legacy.get("created_at"),
                    username, avatar)
            return tweets

        body = payload.get("data", payload) if isinstance(payload, dict) else payload

        # X API v2 layout: {data: [...], includes: {users: [...]}}
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            users = {u.get("id"): u.get("username")
                     for u in body.get("includes", {}).get("users", [])}
            for t in body["data"]:
                add(t.get("id"), t.get("text") or t.get("full_text"),
                    t.get("created_at"), users.get(t.get("author_id"), ""))
            return tweets

        # RapidAPI-style layouts: {timeline: [...]} / {tweets: [...]} / bare list
        items = None
        if isinstance(body, dict):
            for key in ("timeline", "tweets", "results", "statuses", "entries"):
                if isinstance(body.get(key), list):
                    items = body[key]
                    break
        elif isinstance(body, list):
            items = body
        if items is not None:
            for t in items:
                if not isinstance(t, dict):
                    continue
                user = t.get("user") or t.get("author") or {}
                username = (t.get("screen_name") or t.get("username")
                            or user.get("screen_name") or user.get("username")
                            or user.get("screen_name_lower") or "")
                add(t.get("tweet_id") or t.get("id") or t.get("id_str"),
                    t.get("text") or t.get("full_text") or t.get("tweet_text"),
                    t.get("created_at") or t.get("timestamp"),
                    username)
            return tweets

        # Unknown shape — save a sample for debugging, never fabricate
        config.BRIEF_OUT_DIR.mkdir(exist_ok=True)
        sample = config.BRIEF_OUT_DIR / "twtapi_unparsed_sample.json"
        sample.write_text(json.dumps(payload, indent=2)[:20000], encoding="utf-8")
        log.error("unrecognised TwtAPI payload shape; sample saved to %s", sample)
        return tweets

    def fetch_new_tweets(self, handles: list[dict]) -> list[Tweet]:
        """One Search call per column over its top-trust handles."""
        handle_map = {h["handle"].lstrip("@").lower(): h for h in handles}
        by_column: dict[str, list[dict]] = {}
        for h in handles:
            by_column.setdefault(h["stream_column"], []).append(h)

        collected: list[Tweet] = []
        errors: list[str] = []
        for column in sorted(by_column):
            top = sorted(by_column[column],
                         key=lambda h: -h["trust_score"])[: config.X_HANDLES_PER_COLUMN]
            query = " OR ".join(f"from:{h['handle'].lstrip('@')}" for h in top)
            try:
                payload = self._get("Search", {
                    "q": query, "type": "Latest", "count": 20,
                })
                collected.extend(self._extract_tweets(payload, handle_map))
            except ProviderUnavailable:
                raise
            except Exception as exc:
                errors.append(f"column {column}: {exc}")
                log.warning("search failed for column %s: %s", column, exc)

        if errors and not collected:
            raise ProviderUnavailable("; ".join(errors))
        collected.sort(key=lambda t: t.created_at)  # oldest first; newest ends on top
        return collected
