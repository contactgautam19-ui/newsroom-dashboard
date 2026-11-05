"""Three failures found by working the desk on a phone, 5 November.

Each one looked like a missing feature from the outside and was a routing or
display bug underneath: the work had been done and then thrown away. These
tests hold the seams where that happened.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# --- "what's the latest in Karnataka?" ------------------------------------
# The deterministic past-hour pull answers this in one call. It was reachable
# only through "on" and "about", so every geographic phrasing an Indian desk
# actually uses fell through to the model path and hung until the serverless
# request timed out — and the feature looked like it did not exist.
def test_a_desk_can_ask_for_a_place_the_way_it_speaks():
    from app.npro import engine

    for query, expected in [
        ("What's the latest in Karnataka?", "Karnataka"),
        ("What is the latest from Karnataka?", "Karnataka"),
        ("What's the latest on Karnataka?", "Karnataka"),       # the old path
        ("Latest from Bengaluru", "Bengaluru"),
        ("Any updates in Tamil Nadu?", "Tamil Nadu"),
        ("latest news out of Punjab", "Punjab"),
        ("breaking in West Bengal", "West Bengal"),
        ("What's the latest about the Delhi pollution case?",
         "the Delhi pollution case"),
    ]:
        assert engine.latest_keyword(query) == expected, query


def test_a_question_about_our_own_board_is_not_sent_to_the_news():
    """Widening the prepositions also catches "latest in the rundown", which
    is about our board, not a subject to go and search for."""
    from app.npro import engine

    for query in [
        "What's the latest in the rundown?",
        "Whats new on the board",
        "Which stories are trending on X right now?",
        "What should I lead with in the bulletin?",
    ]:
        assert engine.latest_keyword(query) is None, query


# --- "what's on air — by the hour" ----------------------------------------
def test_the_panel_shows_the_rows_its_own_refresh_writes():
    """The refresh button's X path needs a TwtAPI key and quota, and demo
    guests never get it, so the keyless YouTube-title poll is the only path
    that always runs. It writes rows tagged 'title'. The panel filtered those
    out, so Refresh stored 16 headlines and the panel said nothing was there."""
    from app.news import onair

    assert "title" in onair.AIRED_SOURCES, (
        "the keyless poll's own rows are invisible to the panel again")
    # the poll really does tag its rows that way
    src = pathlib.Path(onair.__file__).read_text(encoding="utf-8")
    assert "'title'" in src or '"title"' in src


def test_every_source_the_poll_writes_can_be_displayed():
    """Any source the poll can write must be one the panel can show, or the
    same silent-discard bug comes back under a different name."""
    from app.news import onair

    written = {"title"}                      # poll_onair's INSERT
    assert written <= set(onair.AIRED_SOURCES)


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
