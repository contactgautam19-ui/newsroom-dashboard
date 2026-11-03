"""Breaking-alert emails: what earns an inbox and what must never reach one.

The rules here are the difference between an alert an editor opens and one
they learn to ignore, so they are pinned rather than left to the regex.

    py -3.14 tests/test_alerts_mail.py
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import alerts_mail  # noqa: E402

NOW = datetime.now(timezone.utc)


def _signal(title, urgency=80, outlets=4, key=None, published=None, **kw):
    return {
        "signal_key": key or title[:40],
        "title": title,
        "url": "https://example.test/story",
        "publisher": "Test Wire",
        "published_at": (published or NOW).isoformat(),
        "outlets": outlets,
        "urgency": urgency,
        "reasons": '["%d outlets carrying it"]' % outlets,
        "on_board": 0,
        "promoted": 0,
        **kw,
    }


# --- scheduled content vs events ----------------------------------------
ROUTINE = [
    "Stock Market Today: Sensex Zooms 600 Points, Nifty 50 Near 23,600",
    "Gold Rate Today: Yellow metal climbs in Delhi, Mumbai",
    "Horoscope Today, November 3: Check your daily astrological prediction",
    "IND vs AUS Live Score, 3rd T20I",
    "Dream11 fantasy tips and playing XI predictions",
    "Top Headlines Today: Parliament session, cyclone watch",
    "Box Office Collection Day 4: film crosses Rs 100 crore",
    "Petrol diesel price today in Delhi, Mumbai, Chennai",
    # mailed at urgency 100 on 2026-11-03: markets borrow the vocabulary of
    # disasters, and "crash" let it through the escape hatch
    "Silver prices crash ₹8,500/kg, gold drops ₹2,000/10g as US-Iran "
    "tensions revive inflation fears; is more fall coming?",
    "Gold drops Rs 2,000/10g on US jobs data",
    "IPL 2027: BCCI announces new retention deadline, mini-auction date unchanged",
    # a digest is never an event, however grim the stories it lists. This one
    # mailed at urgency 99.
    "HT Morning Brief Nov 3: Indian tourist killed in Nepal; rain alert in Nilgiris",
    "Evening digest: blast in Kabul, three held in Pune",
    "SBI Q2 results: Net profit rises 14% on-year to Rs 23,038 crore",
    "Biratnagar Kings vs Janakpur Bolts",
    "Hardik Pandya For Arshdeep Singh? MI, PBKS Near IPL Trade Deal: Report",
]

EVENTS = [
    "2 foreign nationals die of suspected electrocution in Bengaluru hotel",
    "Rescue teams resume search for 6 bodies after chopper crash near Joshimath",
    "Cyclone Murasu Landfall Begins, Heavy Rain Batters Andhra Coast",
    "Major fire breaks out at Hindustan Petroleum refinery in Visakhapatnam",
    "ACP who led protest outside Delhi Police headquarters suspended",
    "Finance Minister announces GST cut on insurance premiums",
    "INDIA bloc rally at Ramlila Maidan: Delhi Police deny permission",
    # the escape hatch: a market word in a headline about something happening
    "Sensex-listed firm chairman arrested in fraud case",
    "Trading halted after blast near BSE building, 3 injured",
    # a fixture-shaped headline that is an event
    "India vs Pakistan talks collapse after border firing",
]


def test_routine_content_never_mails():
    for title in ROUTINE:
        assert alerts_mail._is_routine(title), title


def test_events_are_not_suppressed():
    for title in EVENTS:
        assert not alerts_mail._is_routine(title), title


# --- one row per event ---------------------------------------------------
def test_shortlist_collapses_the_same_death_reported_four_ways():
    rows = [
        _signal("Kaante director Sanjay Gupta dies at 57 after head injury", key="a"),
        _signal("Director Sanjay Gupta, known for Kaante, dies at 59", key="b"),
        _signal("RIP Sanjay Gupta: Kaante director dies in Mumbai", key="c"),
        _signal("Fire breaks out at Vizag HPCL refinery", key="d"),
    ]
    kept = alerts_mail._shortlist(rows)
    titles = [r["title"] for r in kept]
    assert len(kept) == 2, titles
    assert "Sanjay Gupta" in titles[0]
    assert "Vizag" in titles[1]


def test_an_already_mailed_event_does_not_lead_a_second_email():
    """The bug that sent five alerts for one killing off Oman: each outlet's
    wording is its own unmailed row."""
    from app.news.ingest import _sig_tokens
    mailed = [(_sig_tokens(
        "Seafarer killed in attack on Indian-flagged cargo vessel 'Al Jusufi' "
        "off Oman coast"), 86)]
    rows = [
        _signal("Indian Sailor Killed In Attack On Cargo Vessel 'Al Jusufi' "
                "Off Oman Coast", urgency=84, key="variant"),
        _signal("Cyclone Murasu landfall begins on Andhra coast",
                urgency=84, key="other"),
    ]
    kept = [r["signal_key"] for r in alerts_mail._shortlist(rows, mailed)]
    assert kept == ["other"], kept


def test_a_mailed_event_returns_once_it_genuinely_escalates():
    from app.news.ingest import _sig_tokens
    mailed = [(_sig_tokens("Cyclone Murasu landfall begins on Andhra coast"), 77)]
    climbed = _signal("Cyclone Murasu landfall: death toll rises to 30 in Andhra",
                      urgency=77 + alerts_mail.ESCALATION, key="climbed")
    crept = _signal("Cyclone Murasu landfall continues along Andhra coast",
                    urgency=77 + alerts_mail.ESCALATION - 1, key="crept")
    assert [r["signal_key"] for r in
            alerts_mail._shortlist([climbed], mailed)] == ["climbed"]
    assert alerts_mail._shortlist([crept], mailed) == []


def test_shortlist_drops_routine_before_deduping():
    rows = [
        _signal("Stock Market Today: Sensex Zooms 600 Points", key="mkt"),
        _signal("Cyclone Murasu landfall begins on Andhra coast", key="cyc"),
    ]
    kept = alerts_mail._shortlist(rows)
    assert [r["signal_key"] for r in kept] == ["cyc"]


# --- rendering -----------------------------------------------------------
def test_build_renders_reasons_and_marks_an_unscored_story():
    lead = alerts_mail._prepare(
        _signal("Major fire breaks out at Visakhapatnam refinery",
                urgency=85, outlets=7), NOW)
    subject, html = alerts_mail.build(lead, [], NOW)
    assert "Echo breaking:" in subject and "(+" not in subject
    assert "7 outlets carrying it" in html          # the why box
    assert "Not on the board yet" in html           # unscored, and says so
    assert "Stop these alerts" not in html          # editor copy, no unsub


def test_guest_copy_carries_an_unsubscribe_link():
    lead = alerts_mail._prepare(_signal("Cyclone makes landfall"), NOW)
    others = [alerts_mail._prepare(_signal("Second thing", key="x"), NOW)]
    subject, html = alerts_mail.build(
        lead, others, NOW, unsub=alerts_mail.unsub_link("visitor@example.test"))
    assert "(+1 more)" in subject
    assert "ALSO CROSSING NOW" in html
    assert "Stop these alerts" in html


def test_filed_reads_as_elapsed_time():
    assert alerts_mail._filed((NOW - timedelta(minutes=14)).isoformat(), NOW) == "14 min ago"
    assert alerts_mail._filed((NOW - timedelta(seconds=30)).isoformat(), NOW) == "moments ago"
    assert alerts_mail._filed(None, NOW) == "just now"
    assert alerts_mail._filed("not-a-date", NOW) == "just now"


# --- unsubscribe tokens --------------------------------------------------
def test_unsubscribe_token_is_per_address_and_unguessable():
    mine = alerts_mail.unsub_token("a@example.test")
    theirs = alerts_mail.unsub_token("b@example.test")
    assert mine != theirs
    assert len(mine) == 24
    # case does not create a second identity
    assert alerts_mail.unsub_token("A@Example.Test") == mine


def test_unsubscribe_rejects_a_forged_token():
    assert alerts_mail.unsubscribe("a@example.test", "deadbeef" * 3) is False
    assert alerts_mail.unsubscribe("a@example.test", "") is False
    assert alerts_mail.unsubscribe("", alerts_mail.unsub_token("")) is False


if __name__ == "__main__":
    import sys as _sys
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
    _sys.exit(1 if failures else 0)
