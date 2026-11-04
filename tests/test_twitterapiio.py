"""TwitterAPI.io as a second X vendor.

The failure that prompted this was silent: a vendor ran out of credits and the
desk simply stopped. So these tests care less about the happy path than about
the ways a second provider could fail quietly — a payload that parses but
loses its authors, an exhausted balance that does not trigger failover, or a
down-shift that leaves the desk reporting the wrong account's budget.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.x.provider import ProviderUnavailable                      # noqa: E402
from app.x.twitterapiio_provider import TwitterAPIioProvider        # noqa: E402

HANDLES = [
    {"handle": "@ThanthiTV", "name": "Thanthi TV", "stream_column": "B",
     "trust_score": 75},
    {"handle": "@TV9Telugu", "name": "TV9 Telugu", "stream_column": "B",
     "trust_score": 75},
]
HANDLE_MAP = {h["handle"].lstrip("@").lower(): h for h in HANDLES}

# the vendor's documented shape, camelCase throughout
PAYLOAD = {
    "tweets": [
        {"id": "1", "text": "Chennai rains: schools shut",
         "createdAt": "Tue Nov 04 06:30:00 +0000 2026",
         "author": {"userName": "ThanthiTV", "name": "Thanthi TV",
                    "profilePicture": "https://img/x.jpg"}},
        {"id": "2", "text": "Hyderabad metro update",
         "createdAt": "Tue Nov 04 06:31:00 +0000 2026",
         "author": {"userName": "TV9Telugu", "name": "TV9 Telugu"}},
    ],
    "has_next_page": False,
}


def test_camelcase_payload_keeps_author_and_timestamp():
    """The shared extractor used to read only snake_case. A tweet with no
    author is dropped outright, so this is the difference between the vendor
    working and returning nothing at all."""
    tweets = TwitterAPIioProvider()._extract_tweets(PAYLOAD, HANDLE_MAP)
    assert len(tweets) == 2, "camelCase authors were dropped"
    by_handle = {t.handle: t for t in tweets}
    assert set(by_handle) == {"@ThanthiTV", "@TV9Telugu"}
    thanthi = by_handle["@ThanthiTV"]
    assert thanthi.text == "Chennai rains: schools shut"
    assert thanthi.display_name == "Thanthi TV"
    assert thanthi.stream_column == "B"
    assert thanthi.avatar_url == "https://img/x.jpg"
    # a real timestamp, not the parser quietly falling back to "now"
    assert thanthi.created_at.startswith("2026-11-04T06:30"), thanthi.created_at


def test_unmonitored_handles_are_ignored():
    """A from: search should only ever return roster handles; anything else
    is a vendor bug and must not reach the desk."""
    stray = {"tweets": [{"id": "9", "text": "spam",
                         "createdAt": "Tue Nov 04 06:30:00 +0000 2026",
                         "author": {"userName": "SomeoneElse"}}]}
    assert TwitterAPIioProvider()._extract_tweets(stray, HANDLE_MAP) == []


def test_exhausted_balance_raises_so_the_pipeline_down_shifts():
    """402 is what an empty balance looks like. It has to raise
    ProviderUnavailable, because that is the only signal the pipeline
    down-shifts on — anything else and the desk goes dark again."""
    import httpx

    from app import config

    def fake_get(url, **kw):
        return httpx.Response(402, text="insufficient credits",
                              request=httpx.Request("GET", url))

    p = TwitterAPIioProvider()
    saved_key, saved_get = config.TWITTERAPIIO_KEY, httpx.get
    config.TWITTERAPIIO_KEY, httpx.get = "test-key", fake_get
    try:
        raised = False
        try:
            p._search("from:ThanthiTV")
        except ProviderUnavailable as exc:
            raised = True
            assert "402" in str(exc)
        assert raised, "an empty balance did not trigger failover"
    finally:
        config.TWITTERAPIIO_KEY, httpx.get = saved_key, saved_get


def test_calls_are_spaced_for_the_free_tier():
    """The free tier allows one call every five seconds and a refresh makes
    three back to back. Unspaced, columns two and three come back 429 and the
    desk silently loses two thirds of its feed — which is exactly what
    happened the first time this was wired up."""
    import httpx

    from app import config
    from app.x import twitterapiio_provider as mod

    calls = []

    def fake_get(url, **kw):
        calls.append(mod.time.monotonic())
        return httpx.Response(200, json={"tweets": []},
                              request=httpx.Request("GET", url))

    slept = []
    p = TwitterAPIioProvider()
    saved = (config.TWITTERAPIIO_KEY, httpx.get, mod.time.sleep,
             mod.TwitterAPIioProvider._last_call_at)
    config.TWITTERAPIIO_KEY, httpx.get = "k", fake_get
    mod.time.sleep = lambda s: slept.append(s)      # measure, don't wait
    mod.TwitterAPIioProvider._last_call_at = 0.0
    try:
        for _ in range(3):
            p._search("from:a OR from:b")
        assert len(calls) == 3
        # the first call may go straight out; the next two must be held back
        assert len(slept) >= 2, f"calls were not spaced: slept={slept}"
        assert all(s > 4 for s in slept), f"spacing too short: {slept}"
    finally:
        (config.TWITTERAPIIO_KEY, httpx.get, mod.time.sleep,
         mod.TwitterAPIioProvider._last_call_at) = saved


def test_a_429_is_retried_before_giving_up():
    """A single throttled call should not cost the desk its column."""
    import httpx

    from app import config
    from app.x import twitterapiio_provider as mod

    responses = [429, 429, 200]
    seen = []

    def fake_get(url, **kw):
        code = responses[len(seen)]
        seen.append(code)
        return httpx.Response(code, json={"tweets": []} if code == 200 else {},
                              request=httpx.Request("GET", url))

    p = TwitterAPIioProvider()
    saved = (config.TWITTERAPIIO_KEY, httpx.get, mod.time.sleep)
    config.TWITTERAPIIO_KEY, httpx.get = "k", fake_get
    mod.time.sleep = lambda s: None
    try:
        payload = p._search("from:a")
        assert seen == [429, 429, 200], seen
        assert payload == {"tweets": []}
    finally:
        config.TWITTERAPIIO_KEY, httpx.get, mod.time.sleep = saved


def test_missing_key_names_the_right_variable():
    from app import config

    saved = config.TWITTERAPIIO_KEY
    config.TWITTERAPIIO_KEY = ""
    try:
        TwitterAPIioProvider()._search("from:ThanthiTV")
        raise AssertionError("expected ProviderUnavailable")
    except ProviderUnavailable as exc:
        assert "TWITTERAPIIO_KEY" in str(exc), str(exc)
    finally:
        config.TWITTERAPIIO_KEY = saved


def test_spend_estimate_is_labelled_an_estimate():
    """There is no status endpoint, so the desk must not be handed a number
    that looks like a balance. Only an explicitly-named estimate."""
    p = TwitterAPIioProvider()
    p._note_spend(PAYLOAD)
    assert p.last_status["credits_spent_estimate"] == 2 * 15
    p._note_spend({"tweets": []})          # empty search still costs the minimum
    assert p.last_status["credits_spent_estimate"] == 2 * 15 + 15
    assert "credits" not in p.last_status, "an estimate must not pose as a balance"


def test_the_two_vendors_back_each_other_up():
    from app import config
    from app.x.pipeline import XPipeline

    keys = (config.TWT_API_KEY, config.TWITTERAPIIO_KEY, config.X_PROVIDER)
    try:
        config.TWT_API_KEY, config.TWITTERAPIIO_KEY = "a", "b"

        config.X_PROVIDER = "twtapi"
        assert [l.name for l in XPipeline().layers] == ["twtapi", "twitterapiio"]

        config.X_PROVIDER = "twitterapiio"
        assert [l.name for l in XPipeline().layers] == ["twitterapiio", "twtapi"]

        # no second key means no phantom layer that can only fail
        config.TWITTERAPIIO_KEY = ""
        config.X_PROVIDER = "twtapi"
        assert [l.name for l in XPipeline().layers] == ["twtapi"]

        # sim stays alone: simulated tweets never share a board with real ones
        config.X_PROVIDER = "sim"
        assert [l.name for l in XPipeline().layers] == ["simulated"]
    finally:
        config.TWT_API_KEY, config.TWITTERAPIIO_KEY, config.X_PROVIDER = keys


def test_budget_follows_the_layer_that_answered():
    """After a down-shift the desk must show the serving vendor's position,
    not the exhausted one's — otherwise the button stays disabled on a
    provider that is working fine."""
    from app import config
    from app.x.pipeline import XPipeline

    keys = (config.TWT_API_KEY, config.TWITTERAPIIO_KEY, config.X_PROVIDER)
    try:
        config.TWT_API_KEY, config.TWITTERAPIIO_KEY = "a", "b"
        config.X_PROVIDER = "twtapi"
        pipe = XPipeline()
        assert pipe.current_provider().name == "twtapi"
        pipe.active_layer = "twitterapiio"
        assert pipe.current_provider().name == "twitterapiio"
    finally:
        config.TWT_API_KEY, config.TWITTERAPIIO_KEY, config.X_PROVIDER = keys


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    raise SystemExit(failures)
