"""N-Pro must answer about stories the board never picked up.

The board only holds what broke in the last hour or so. The whole point of
N-Pro's retrieval is that a question is not limited to that: it goes out to
live reporting on its own. These tests hold the two ways that quietly broke.

1. How an editor opens a subject. "Tell me about X" and "Explain X" were not
   in the scaffold the parser strips, so the verb leaked into the search —
   "tell me about the Bhopal verdict" went out as "Tell about Bhopal gas
   tragedy" and answered a question nobody asked.

2. What happens when a subject genuinely has no recent coverage. Google News
   answers a query it found nothing for with general headlines, so on
   5 November "the Bhopal gas tragedy verdict" came back with six reports
   about Assam politics. The model would have written over them. An empty
   hand is the honest answer; unrelated material under a confident one is not.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def test_the_ways_an_editor_opens_a_subject_all_parse():
    """Each phrasing must reduce to the subject, with no verb left in it."""
    from app.npro import engine, retrieval

    for query, subject in [
        ("Tell me about the Bhopal gas tragedy verdict", "bhopal gas tragedy verdict"),
        ("Tell me about Karnataka bus fares", "karnataka bus fares"),
        ("Explain the Bihar election result", "bihar election result"),
        ("Describe the Delhi pollution emergency", "delhi pollution emergency"),
        ("Summarise the RBI repo rate decision", "rbi repo rate decision"),
        ("Summarize the RBI repo rate decision", "rbi repo rate decision"),
        ("Walk me through the POK protests", "pok protests"),
        # the phrasings that already worked must keep working
        ("Find articles on the Delhi AQI", "delhi aqi"),
        ("Give me a script on the Sensex rally", "sensex rally"),
    ]:
        terms = retrieval._key_terms(engine.search_topic(query, "")).lower()
        assert terms == subject, f"{query!r} searched for {terms!r}"
        for verb in ("tell", "explain", "describe", "summari", "walk", "find", "give"):
            assert verb not in terms, f"{query!r} left {verb!r} in the search"


def test_a_subject_with_no_coverage_returns_nothing_not_something_else():
    """The guard is what stops a confident answer over unrelated reporting."""
    from app.npro import retrieval

    unrelated = [
        {"title": "Roses are given on Valentine's Day: Himanta responds to Rahul"},
        {"title": "Debate in Parliament: Himanta Sarma hits back at Rahul Gandhi"},
        {"title": "Assam CM challenges Rahul to debate PM Modi's record"},
    ]
    assert retrieval._on_topic(unrelated, "Bhopal gas tragedy verdict") == [], \
        "reports sharing no word with the question were passed off as an answer"


def test_the_guard_keeps_genuinely_related_reporting():
    """It must not be so strict that real coverage is thrown away — including
    across a plural, which is where a naive word match would fail."""
    from app.npro import retrieval

    related = [
        {"title": "BJP protests against bus fare hike in Karnataka"},   # fare/fares
        {"title": "Karnataka government's fare hike on electric buses questioned"},
    ]
    kept = retrieval._on_topic(related, "Karnataka bus fares")
    assert len(kept) == 2, f"on-topic reporting was dropped: {kept}"

    pok = [{"title": "PoK Violence Escalates: Drones And Firing Reported"}]
    assert retrieval._on_topic(pok, "POK protests") == pok


def test_stemming_matches_singular_and_plural():
    from app.npro.retrieval import _stem
    assert _stem("fares") == _stem("fare")
    assert _stem("protests") == _stem("protest")
    assert _stem("rallies") == _stem("rally")
    assert _stem("gas") == "gas"          # short words are left alone


STARTER_BUTTONS = [
    "Which stories should I pick at the top of the hour?",
    "Which story is likely to go viral?",
    "Which stories are trending on X right now?",
    "Which stories could generate the highest views?",
    "What are rival channels airing right now?",
    "What are competitors covering that we are missing?",
]


def test_every_starter_button_is_answered_from_the_board():
    """The six chips in the N-Pro welcome card are all questions about our
    own desk. If one of them ever routes to a news search it will answer from
    the open web instead of the board, which is not what it is asking."""
    from app.npro import engine

    for q in STARTER_BUTTONS:
        assert engine.is_desk_question(q), f"starter button went to news: {q!r}"


def test_desk_vocabulary_does_not_capture_a_named_subject():
    """"trending", "viral" and "views" are desk words and ordinary news words
    both. Treating every one of them as a desk question answered "what's
    trending in the Bihar election?" from a board that had never carried it."""
    from app.npro import engine

    for q in ["What's trending in the Bihar election?",
              "Is the Delhi AQI story going viral?",
              "What's trending about Karnataka bus fares?",
              "Which way is the POK story trending?"]:
        assert not engine.is_desk_question(q), \
            f"a named subject was answered from the board: {q!r}"


def test_questions_about_our_own_board_still_reach_it():
    from app.npro import engine

    for q in ["What should I lead with?",
              "What's the latest in the rundown?",
              "Anything missing from the board?",
              "What are our top stories right now?",
              "What are rival channels airing?"]:
        assert engine.is_desk_question(q), f"a desk question went to news: {q!r}"


def test_a_question_about_a_place_still_reaches_live_reporting():
    """The geographic phrasings route to the deterministic past-hour pull
    rather than the board, which is what makes an off-board place answerable."""
    from app.npro import engine

    for q in ["What's the latest in Karnataka?",
              "What is the latest from Assam?",
              "Latest from Bengaluru"]:
        assert engine.latest_keyword(q), f"{q!r} no longer reaches live reporting"


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
