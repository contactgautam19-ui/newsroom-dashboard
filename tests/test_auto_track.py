"""Stories that keep running open their own track.

Which stories are worth following is a judgement an editor makes by noticing
that something keeps coming back, and a desk is busiest exactly when nobody
is noticing. The breaking lane already carries the evidence: how many outlets
carried a headline, the running peak of its urgency, and — because a signal
still being filed gets its last_seen stamped again — how long it has run.

The test is "sustained AND corroborated", never "loud once". On 6 November
the lane raised 483 signals in twelve hours; 383 were re-seen on a later scan
and only 93 were carried by five outlets or more. Urgency alone would have
opened a track on every fire in Delhi, so most of what follows is about what
must NOT be tracked.
"""

import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

NOW = datetime.now(timezone.utc)


class _Lane:
    """A scratch database with a breaking lane and no network."""

    def __enter__(self):
        from app import config, db
        from app.news import tracker
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="echo-autotrack-"))
        self._db = config.DB_PATH
        config.DB_PATH = db.config.DB_PATH = self.dir / "t.db"
        db.init_db()
        # a track that opens must not go to the network for its backfill
        self._check = tracker.check_one
        tracker.check_one = lambda *a, **k: {"ok": True, "new": 0, "total": 0}
        self.db, self.tracker, self.config = db, tracker, config
        self._cfg = {k: getattr(config, k) for k in (
            "AUTO_TRACK_ENABLED", "AUTO_TRACK_URGENCY", "AUTO_TRACK_OUTLETS",
            "AUTO_TRACK_MIN_MINUTES", "AUTO_TRACK_MAX_PER_DAY", "AUTO_TRACK_PER_SCAN")}
        return self

    def signal(self, key, title, urgency=90, outlets=6,
               started_min_ago=120, still_running=True):
        first = NOW - timedelta(minutes=started_min_ago)
        last = (NOW - timedelta(minutes=5)) if still_running else first
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO fast_signals (signal_key, title, url, publisher, "
                "published_at, outlets, urgency, reasons, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, title, "http://x", "Test", first.isoformat(), outlets,
                 urgency, "[]", first.isoformat(), last.isoformat()))

    def tracks(self):
        with self.db.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT label, auto FROM tracked_stories WHERE active=1").fetchall()]

    def __exit__(self, *exc):
        from app import config
        for k, v in self._cfg.items():
            setattr(config, k, v)
        config.DB_PATH = self.db.config.DB_PATH = self._db
        self.tracker.check_one = self._check
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def test_a_story_that_keeps_running_opens_its_own_track():
    with _Lane() as lane:
        lane.signal("a", "Seven killed as building collapses in Delhi")
        out = lane.tracker.auto_track_from_signals()
        assert out["opened"] == 1, out
        assert [t["label"] for t in lane.tracks()] == \
            ["Seven killed as building collapses in Delhi"]
        assert lane.tracks()[0]["auto"] == 1, "an auto track must be marked as one"


def test_loud_once_is_not_a_running_story():
    """The commonest false positive: one outlet shouting, never picked up."""
    with _Lane() as lane:
        lane.signal("a", "Fire breaks out at shop in Delhi", still_running=False)
        assert lane.tracker.auto_track_from_signals()["opened"] == 0
        assert lane.tracks() == []


def test_a_story_too_young_to_judge_is_left_alone():
    with _Lane() as lane:
        lane.signal("a", "Blast reported near market", started_min_ago=10)
        assert lane.tracker.auto_track_from_signals()["opened"] == 0


def test_one_outlet_is_not_corroboration():
    with _Lane() as lane:
        lane.signal("a", "Minister resigns, says one outlet", outlets=1)
        assert lane.tracker.auto_track_from_signals()["opened"] == 0


def test_a_quiet_signal_is_not_tracked_however_long_it_runs():
    with _Lane() as lane:
        lane.signal("a", "Routine traffic advisory issued", urgency=50,
                    started_min_ago=600)
        assert lane.tracker.auto_track_from_signals()["opened"] == 0


def test_the_same_event_worded_differently_opens_one_track_not_two():
    """Six outlets word one event six ways. Each wording is its own signal,
    and without an event test each would open its own track."""
    with _Lane() as lane:
        lane.signal("a", "Bengaluru cab driver kills seven of wife's family")
        lane.signal("b", "Cab driver in Bengaluru kills wife and six relatives")
        lane.config.AUTO_TRACK_PER_SCAN = 5
        out = lane.tracker.auto_track_from_signals()
        assert out["opened"] == 1, f"opened {out['opened']} tracks for one story"
        assert out["skipped"]["duplicate"] == 1
        # and the loser is settled, so it is never weighed again
        with lane.db.connect() as con:
            done = con.execute("SELECT auto_tracked FROM fast_signals "
                               "WHERE signal_key='b'").fetchone()[0]
        assert done == 1


DISTINCT_EVENTS = [
    "Seven killed as building collapses in Shahdara",
    "Cyclone Murasu makes landfall near Machilipatnam",
    "Supreme Court stays Karnataka mining lease auction",
    "Train derails near Kharagpur, twelve injured",
    "Finance minister resigns over audit findings",
    "Blast at fireworks unit in Sivakasi kills four",
]


def test_the_per_scan_limit_holds():
    """Each new track backfills with a search, so a burst cannot open ten."""
    with _Lane() as lane:
        for i, title in enumerate(DISTINCT_EVENTS[:5]):
            lane.signal(f"s{i}", title)
        lane.config.AUTO_TRACK_PER_SCAN = 2
        assert lane.tracker.auto_track_from_signals()["opened"] == 2


def test_the_daily_cap_holds_across_scans():
    with _Lane() as lane:
        lane.config.AUTO_TRACK_PER_SCAN = 10
        lane.config.AUTO_TRACK_MAX_PER_DAY = 3
        for i, title in enumerate(DISTINCT_EVENTS):
            lane.signal(f"s{i}", title)
        first = lane.tracker.auto_track_from_signals()
        assert first["opened"] == 3, first
        again = lane.tracker.auto_track_from_signals()
        assert again["opened"] == 0, "the daily cap did not survive a second scan"
        assert len(lane.tracks()) == 3


def test_a_digest_is_never_tracked():
    """A digest is many stories under one headline. It runs all morning and
    looks sustained to every other test here, while being the one thing a
    standing search cannot follow — there is no single story under it."""
    with _Lane() as lane:
        for i, title in enumerate([
                "HT Morning Brief Nov 3: Indian tourist killed in Nepal",
                "Asian Markets Today: Nikkei enters bear market, Kospi slides",
                "Gold 10 gm or Silver 1 kg? What to buy on Dhanteras 2026",
                "Top stories of the day: everything that happened",
        ]):
            lane.signal(f"d{i}", title)
        lane.config.AUTO_TRACK_PER_SCAN = 5
        assert lane.tracker.auto_track_from_signals()["opened"] == 0
        assert lane.tracks() == []


def test_a_headline_running_all_day_is_evergreen_not_breaking():
    """Twenty hours of filing is syndication, not development."""
    with _Lane() as lane:
        lane.signal("a", "Election Commission defends Form 6 use for deletions",
                    started_min_ago=1300)
        assert lane.tracker.auto_track_from_signals()["opened"] == 0


def test_a_real_running_story_survives_both_bounds():
    """The guard against evergreen must not cut a big story that genuinely
    runs for hours — the Delhi collapse ran 145 minutes across 13 outlets."""
    with _Lane() as lane:
        lane.signal("a", "24 hours after Delhi building collapse, several still feared trapped",
                    urgency=99, outlets=13, started_min_ago=150)
        assert lane.tracker.auto_track_from_signals()["opened"] == 1


def test_the_kill_switch_stops_it_dead():
    with _Lane() as lane:
        lane.signal("a", "Seven killed as building collapses in Delhi")
        lane.config.AUTO_TRACK_ENABLED = False
        out = lane.tracker.auto_track_from_signals()
        assert out == {"enabled": False, "opened": 0}
        assert lane.tracks() == []


def test_it_never_duplicates_a_track_an_editor_already_opened():
    with _Lane() as lane:
        lane.tracker.start(label="Seven killed as building collapses in Delhi",
                           created_by="editor")
        lane.signal("a", "Seven killed as building collapses in Delhi")
        out = lane.tracker.auto_track_from_signals()
        assert out["opened"] == 0
        assert len(lane.tracks()) == 1


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
