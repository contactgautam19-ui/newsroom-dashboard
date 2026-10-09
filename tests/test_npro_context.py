"""Context accuracy: the who / where / when rule, translation figure checks,
and the notebook. No network, no model.

    py -3.14 tests/test_npro_context.py
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.npro import checks, ledger, schemas, triad  # noqa: E402
from app.npro.sources import Corpus, trace  # noqa: E402

NOW = datetime.now(timezone.utc)
TODAY = (NOW + timedelta(hours=5, minutes=30)).date().isoformat()


def iso(days_ago=0.0):
    return (NOW - timedelta(days=days_ago)).isoformat()


TRIAD = {
    "entities": [{"name": "Samrat Choudhary", "aliases": ["Samrat Chaudhary"], "kind": "person"},
                 {"name": "Patna Police", "aliases": [], "kind": "organisation"}],
    "places": [{"name": "Gandhi Setu", "aliases": ["Mahatma Gandhi Setu"]},
               {"name": "Patna", "aliases": []}],
    "when": {"said": "this morning", "date": TODAY},
    "event_terms": ["bus", "truck", "collision", "crash", "accident"],
    "queries": ["Gandhi Setu bus truck collision"],
}
NOTE = {"english": "A bus and a truck collided on Gandhi Setu in Patna this morning. "
                   "Police say 7 people died and 23 are injured. Chief Minister Samrat "
                   "Choudhary has announced an inquiry.",
        "original": "पटना: आज सुबह गांधी सेतु पर बस और ट्रक की टक्कर। 7 की मौत, 23 घायल।",
        "language": "Hindi"}


def J(title, text, days_ago=0.0, dated=True, t=TRIAD):
    return triad.judge(title, text, iso(days_ago) if dated else "", t)


def test_same_people_same_place_today_is_the_same_development():
    v = J("Bus-truck crash on Gandhi Setu kills 7", "Patna Police said the driver was held.")
    assert v["ok"] and v["role"] == "same"


def test_older_report_on_same_who_and_where_is_dated_background():
    v = J("Patna Police plan new traffic rules for Gandhi Setu",
          "The move follows a bus accident on the bridge.", days_ago=40)
    assert v["ok"] and v["role"] == "background" and v["date"]


def test_same_official_same_city_but_another_story_is_left_out():
    v = J("Chief Minister Samrat Choudhary opens flower show in Patna", "He spoke near Gandhi Setu.")
    assert not v["ok"] and v["why"] == "a different event"


def test_similar_event_same_place_at_another_time_is_left_out():
    v = J("Bus and truck collide on Gandhi Setu, 3 hurt", "The collision blocked traffic in Patna.", days_ago=400)
    assert not v["ok"] and v["why"] == "a different time"
    assert J("Bus and truck collide on Gandhi Setu, 7 dead", "The collision blocked traffic in Patna.")["ok"]


def test_officials_who_only_comment_do_not_identify_a_story():
    t2 = {"entities": [{"name": "Sangeetha", "aliases": [], "kind": "person", "role": "voice"},
                       {"name": "Tamil Nadu Fire and Rescue Services", "aliases": [], "kind": "organisation", "role": "voice"}],
          "places": [{"name": "Madurai", "aliases": []}], "when": {"said": "", "date": TODAY},
          "event_terms": ["fire", "blaze", "textile", "shop", "gutted", "showroom"], "queries": []}
    # same Collector, same city, another story
    assert not J("Girl swept away at sea near Madurai", "Collector Sangeetha said a search was on. "
                 "Tamil Nadu Fire and Rescue Services personnel joined.", t=t2)["ok"]
    assert not J("Bus and lorry collide in Madurai", "Fire and Rescue Services from Tamil Nadu cut open the bus.", t=t2)["ok"]
    # the same fire, reported today
    assert J("Textile showroom gutted in Madurai fire", "The blaze began at 4 pm.", t=t2)["role"] == "same"
    # a similar fire in the same city last year is not this fire
    assert J("Textile shop fire in Madurai", "The blaze gutted the showroom.", days_ago=300, t=t2)["why"] == "a different time"
    assert triad.summary(t2)["who"] == ["Sangeetha", "Tamil Nadu Fire and Rescue Services"]


def test_future_date_in_a_note_does_not_move_the_story():
    future = (NOW + timedelta(days=6)).date().isoformat()
    t2 = dict(TRIAD, when={"said": "hearing fixed for next week", "date": future})
    assert J("Bus-truck crash on Gandhi Setu kills 7", "Patna Police said ...", t=t2)["role"] == "same"


def test_lookalike_in_another_place_is_left_out():
    v = J("Bus and truck collide on Yamuna Expressway, 7 dead",
          "Agra police said 23 were injured. Chief Minister Samrat Choudhary was not involved.")
    assert not v["ok"] and v["why"] == "a different place"


def test_same_place_different_people_is_left_out():
    v = J("Chhath crowds gather on Gandhi Setu in Patna", "Devotees thronged the bridge at dawn.")
    assert not v["ok"] and v["why"] == "different people"


def test_undated_report_is_left_out():
    v = J("Bus-truck crash on Gandhi Setu", "Patna Police said seven died.", dated=False)
    assert not v["ok"] and v["why"] == "no date"


def test_spelling_variants_still_match():
    v = J("CM Samrat Chaudhary orders probe into Mahatma Gandhi Setu crash", "")
    assert v["ok"], v
    t2 = dict(TRIAD, entities=[{"name": "Thiruvananthapuram Corporation", "aliases": [], "kind": "organisation"}],
              places=[{"name": "Thiruvananthapuram", "aliases": ["Trivandrum"]}], event_terms=[])
    assert J("Tiruvanantapuram Corporation budget", "Trivandrum civic body ...", t=t2)["ok"]


def test_namesake_does_not_match():
    t2 = dict(TRIAD, entities=[{"name": "Rajiv Mishra", "aliases": [], "kind": "person"}])
    assert not J("Rajiv Kumar takes charge in Patna", "Gandhi Setu traffic ...", t=t2)["ok"]
    assert not J("Sanjay Mishra film shot near Gandhi Setu, Patna", "", t=t2)["ok"]


def test_unnamed_event_needs_event_terms_and_place():
    t2 = {"entities": [], "places": [{"name": "Kullu", "aliases": []}],
          "when": {"said": "", "date": TODAY}, "event_terms": ["bus", "gorge"], "queries": []}
    assert J("Bus falls into gorge in Kullu, several hurt", "", t=t2)["ok"]
    assert not J("Bus falls into gorge in Pauri, several hurt", "", t=t2)["ok"]
    assert not J("Kullu Dussehra begins", "Thousands gather in Kullu.", t=t2)["ok"]


def test_corpus_keeps_only_reports_that_pass():
    reports = [
        {"title": "Bus-truck crash on Gandhi Setu kills 7 - Paper A", "publisher": "Paper A",
         "url": "https://example.com/1", "published_at": iso(0.05)},
        {"title": "Yamuna Expressway bus-truck collision kills 9 - Paper B", "publisher": "Paper B",
         "url": "https://example.com/2", "published_at": iso(0.1)},
        {"title": "Patna Police review Gandhi Setu safety after March crash - Paper C",
         "publisher": "Paper C", "url": "https://example.com/3", "published_at": iso(200)},
        {"title": "Gandhi Setu crash: Patna Police file case - Paper D", "publisher": "Paper D",
         "url": "https://example.com/4", "published_at": ""},
    ]
    c = Corpus(None, reports, "Gandhi Setu crash", read_full=False, note=NOTE, triad=TRIAD)
    assert [s["role"] for s in c.sources] == ["same", "background"]
    assert {r["why"] for r in c.rejected} == {"a different place", "no date"}
    block = c.prompt_block()
    assert "<reporter_note" in block and 'role="same development"' in block
    assert 'role="background, dated' in block and "Yamuna" not in block
    assert "Paper A" not in block
    # the lookalike's figure is not something a script may use
    bad = ("HEADLINES:\nGandhi Setu crash kills 7\n\nANCHOR READ:\nA bus and a truck collided on Gandhi "
           "Setu. Nine people died.\n\nWHY THIS MATTERS:\nAn inquiry has been announced.\n\nPRODUCER NOTES:\nNone.")
    assert ("hard", "figure") in [(i["level"], i["code"]) for i in
                                  checks.check_script(bad, "av_read", c, {"duration": "20 seconds"})]


def test_note_only_script_is_grounded_in_the_note():
    c = Corpus(None, [], "Gandhi Setu crash", read_full=False, note=NOTE, triad=TRIAD)
    good = ("HEADLINES:\nGandhi Setu crash kills 7\n\nANCHOR READ:\nA bus and a truck collided on Gandhi Setu "
            "in Patna this morning. Police say 7 people died and 23 are injured.\n\nWHY THIS MATTERS:\n"
            "Chief Minister Samrat Choudhary has announced an inquiry.\n\nPRODUCER NOTES:\nNone.")
    assert checks.hard(checks.check_script(good, "av_read", c, {"duration": "20 seconds"})) == []
    assert "add no background" in c.prompt_block()
    t = trace("Police say 7 people died and 23 are injured.", c)
    assert t["matches"] and t["matches"][0]["publisher"] == "Reporter's note"


def test_weak_triad_on_a_board_story_keeps_reports_and_says_so():
    reports = [{"title": f"Report {i} about something else", "publisher": "P", "url": f"https://example.com/{i}",
                "published_at": iso(0.1)} for i in range(4)]
    c = Corpus(None, reports, "Topic", read_full=False, triad=TRIAD)       # no note
    assert c.triad_weak and len(c.sources) == 4 and c.rejected == []


def test_translation_figure_check():
    assert triad.figures_lost("७ की मौत, 23 घायल, ৩৮ জন", "7 dead, 23 injured, 38 people") == []
    assert triad.figures_lost("23 घायल", "32 injured") == ["23"]
    assert triad.figures_lost("40 हजार घर", "40,000 homes") == []
    assert triad.figures_lost("3 கோடி", "30 million rupees") == ["3"]      # converted, so flagged for a look
    assert triad.script_of("மதுரை தீ விபத்து") == "Tamil" and triad.script_of("hello") is None


def test_notebook_round_trip_and_trace_from_record():
    reports = [{"title": "Bus-truck crash on Gandhi Setu kills 7 - Paper A", "publisher": "Paper A",
                "url": "https://example.com/1", "published_at": iso(0.05)}]
    c = Corpus(None, reports, "Gandhi Setu crash", read_full=False, note=NOTE, triad=TRIAD)
    c.sources[0]["text"] = "Patna Police said the truck driver was detained after the Gandhi Setu collision."
    rid = ledger.file_record("Gandhi Setu crash", "av_read", "ANCHOR READ:\nThe truck driver was detained.",
                             ["Confirm toll."], {"status": "passed", "lines": []}, c, {"duration": "20 seconds"})
    assert rid
    rec = ledger.get(rid)
    assert rec["note"]["original"].startswith("पटना") and rec["triad"]["places"][0]["name"] == "Gandhi Setu"
    assert rec["sources"][0]["text"].startswith("Patna Police") and rec["sources"][0]["role"] == "same"
    assert any(r["id"] == rid for r in ledger.recent(5))
    again = Corpus(None, [], rec["topic"], note=rec["note"], triad=rec["triad"], stored=rec["sources"])
    t = trace("The truck driver was detained after the collision.", again)
    assert t["matches"] and t["matches"][0]["publisher"] == "Paper A"
    from app import db
    with db.connect() as con:
        con.execute("DELETE FROM npro_ledger WHERE id=?", (rid,))


def test_schemas_for_note_and_triad():
    from pydantic import ValidationError
    try:
        schemas.NoteIn.model_validate({"text": "too short"})
        raise AssertionError("accepted a stub note")
    except ValidationError:
        pass
    g = schemas.GenerateIn.model_validate({
        "format": "av_read", "note": NOTE, "record_id": "../../etc/passwd",
        "triad": {"entities": [{"name": "A" * 900, "aliases": ["x"] * 50, "kind": "weird"}, "junk"],
                  "places": "nope", "when": {"date": "tomorrow"}, "event_terms": ["a"] * 40}})
    t = g.triad_dict()
    assert len(t["entities"]) == 1 and len(t["entities"][0]["name"]) == 160
    assert len(t["entities"][0]["aliases"]) == 8 and t["places"] == []
    assert t["when"]["date"] == "" and len(t["event_terms"]) == 6
    assert g.record_id == "" and g.note_dict()["language"] == "Hindi"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "->", type(exc).__name__, exc)
    print("\n%d failed" % failed)
    sys.exit(1 if failed else 0)
