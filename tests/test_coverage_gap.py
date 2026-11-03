"""The Karnataka sting story of 1-3 November 2026, which Echo missed for three
days while seven outlets ran it, pinned as four regression tests.

Nothing here needs a network or a database.

    py -3.14 tests/test_coverage_gap.py
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.news import fastlane, tracker  # noqa: E402
from app.news.ingest import _sig_tokens  # noqa: E402
from app.npro import engine, retrieval  # noqa: E402

NOW = datetime.now(timezone.utc)

# Real headlines from the three days Echo had nothing.
STING = [
    "'Nobody has the guts': H.D. Kumaraswamy dares police to take action against him",
    "Union Minister Kumaraswamy 'masterminded' release of sting video: Byre Gowda",
    "Siddaramaiah sting op row: Kumaraswamy questions SIT delay",
    "Fake Sting Case: Police Trace Phone to Kumaraswamy Home; JD(S) Alleges Bias",
    "H.D. Kumaraswamy accuses D.K. Shivakumar of meeting and conspiring with accused",
]


def _raw(title, age_min=25, rank=50):
    return SimpleNamespace(
        title=title, url="", publisher="p",
        published_at=(NOW - timedelta(minutes=age_min)).isoformat(),
        corroborators=[], summary="", source_country="IN",
        source_rank=rank, discovered_via="")


# --- 1. the topic an instruction points at -------------------------------
def test_an_instruction_is_not_a_search_query():
    """What the editor typed was "Find articles on this…". Searching those
    words returned a Tom Hanks feature for a question about Karnataka."""
    typed = ("Find articles on this. There have been reports on Deccan Herald, "
             "Indian Express, etc.")
    assert engine.search_topic(typed, "Kumaraswamy controversy") == "Kumaraswamy controversy"
    follow = ("Give me a script in the form of the story so far with regard to "
              "this controversy, explained so the common man can understand.")
    assert engine.search_topic(follow, "Kumaraswamy controversy") == "Kumaraswamy controversy"


def test_a_real_subject_is_kept_and_scaffolding_stripped():
    assert "Siddaramaiah" in engine.search_topic(
        "What is happening with the Siddaramaiah sting operation", "")
    assert engine.search_topic("write me a package on the Vizag refinery fire",
                               "") == "the Vizag refinery fire"
    # a named story wins over whatever was on screen before
    assert engine.search_topic("Cyclone Murasu landfall", "older topic") \
        == "Cyclone Murasu landfall"


def test_an_instruction_with_no_subject_searches_nothing():
    """Better a straight "which story?" than a plausible list of unrelated
    articles the desk might believe."""
    assert engine.search_topic("Find articles on this.", "") == ""
    assert retrieval.search_news("Find articles on this.") == []
    assert retrieval.search_news("write a script") == []


# --- 2. political stories are events too ----------------------------------
def test_allegation_wording_scores():
    """None of these carry a casualty or enforcement word, which is why every
    one of them scored below the bar."""
    for title in STING:
        _, why = fastlane.urgency(_raw(title), 2, NOW)
        assert any("political allegation" in w for w in why), title


def test_the_political_lane_alone_does_not_clear_the_bar():
    """Deliberate: an allegation is a claim, not a confirmed act. It still
    needs corroboration, which is what the regional feeds supply."""
    lone, _ = fastlane.urgency(_raw(STING[0]), 2, NOW)
    assert lone < fastlane.PROMOTE_FLOOR, lone        # 58 against a floor of 62
    with_outlets, _ = fastlane.urgency(_raw(STING[0]), 3, NOW)
    assert with_outlets >= fastlane.PROMOTE_FLOOR, with_outlets


def test_an_incident_still_outranks_an_allegation():
    fire, _ = fastlane.urgency(_raw("Massive fire breaks out at Vizag refinery"), 3, NOW)
    claim, _ = fastlane.urgency(_raw(STING[0]), 3, NOW)
    assert fire > claim, (fire, claim)


# --- 3. the desk watchlist -------------------------------------------------
LABEL = "Karnataka sting row: Siddaramaiah, Kumaraswamy, Shivakumar"
WATCHING = [(_sig_tokens(LABEL), LABEL)]


def test_every_headline_in_the_story_matches_the_watchlist():
    """Including the ones naming only one of the three, which is why the
    two-shared-words dedup rule was too strict to reuse here."""
    for title in STING:
        assert tracker.watch_hit(title, WATCHING), title


def test_the_watchlist_does_not_swallow_unrelated_news():
    for title in ["Cyclone Murasu landfall begins on Andhra coast",
                  "Massive fire at Visakhapatnam refinery after explosion",
                  "Karnataka hikes Nandini milk price by Rs 8 per litre",
                  "Karnataka to set up fire stations in every taluk",
                  "Ranji Trophy: Karnataka past 490 against Bengal",
                  "Spanish footballer dies after collision with goalkeeper"]:
        assert not tracker.watch_hit(title, WATCHING), title


# --- 3b. N-Pro context: when is this still the same story? ----------------
def test_the_same_story_keeps_its_context():
    """A False here throws away the pinned story, the reporter's note and the
    who/where/when, so it has to be right."""
    for a, b in [
        ("Kumaraswamy controversy", "Kumaraswamy sting video Siddaramaiah"),
        ("Cyclone Murasu landfall Andhra", "Cyclone Murasu death toll rises"),
        ("Vizag refinery fire", "Visakhapatnam HPCL refinery blast"),
        ("Delhi Police protest", "Delhi Police protest: SOP demands raised"),
        ("Karnataka milk price hike", "Nandini milk price hiked by Rs 8"),
    ]:
        assert engine.same_subject(a, b), (a, b)


def test_a_different_story_is_a_context_switch():
    """A True here silently attaches one story's reporter note and pinned
    who/where/when to another story's script."""
    for a, b in [
        ("Kumaraswamy controversy", "Siddaramaiah sting op row"),
        ("Cyclone Murasu landfall Andhra", "Kumaraswamy sting row"),
        ("Karnataka milk price hike", "Karnataka bus fare hike"),
        ("Bengaluru hotel electrocution", "Bengaluru traffic curbs"),
    ]:
        assert not engine.same_subject(a, b), (a, b)


def test_an_empty_topic_is_not_a_switch():
    assert engine.same_subject("", "")
    assert not engine.same_subject("Cyclone Murasu", "")


def test_a_broad_word_never_matches_on_its_own():
    """A watch naming a state must not pull in the whole state."""
    broad = [(_sig_tokens("Karnataka government policy"), "Karnataka")]
    assert not tracker.watch_hit("Karnataka lorry driver dies in Sivaganga", broad)


def test_no_watchlist_means_no_match():
    assert tracker.watch_hit(STING[0], []) == ""


# --- 4. the roster actually reaches the regional handles -------------------
def test_every_handle_is_polled_within_a_dozen_scans():
    """Top-trust-per-column polled 59 of 233 handles forever and the other 174
    never once — every regional-language channel among them."""
    import json

    from app import config, settings_store
    from app.x.twtapi_provider import TwtAPIProvider

    path = pathlib.Path(__file__).resolve().parent.parent / "data" / "handles.json"
    handles = json.loads(path.read_text(encoding="utf-8"))
    by_col: dict[str, list[dict]] = {}
    for h in handles:
        by_col.setdefault(h["stream_column"], []).append(h)

    store: dict[str, str] = {}
    real_get, real_set = settings_store.get_setting, settings_store.set_setting
    settings_store.get_setting = lambda k, d="": store.get(k, d)
    settings_store.set_setting = lambda k, v: store.__setitem__(k, v)
    try:
        seen: set[str] = set()
        for _ in range(12):
            for col, members in by_col.items():
                seen.update(h["handle"]
                            for h in TwtAPIProvider._column_slice(col, members))
    finally:
        settings_store.get_setting, settings_store.set_setting = real_get, real_set

    missed = {h["handle"] for h in handles} - seen
    assert not missed, f"{len(missed)} handles never polled: {sorted(missed)[:6]}"
    assert config.X_HANDLES_PER_COLUMN >= 2


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok    {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    print("all passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
