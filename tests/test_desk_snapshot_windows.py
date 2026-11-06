"""N-Pro must be able to see past the one-hour board.

The board is deliberately the last hour: for "which stories should I pick at
the top of the hour" that is exactly right. "Which story is likely to go
viral" is a different question — virality does not respect our ingest window
— and answering it from the last hour alone threw away everything that broke
earlier the same day. On 6 November the board held 22 stories while 169 had
been published since midnight, among them a Bengaluru mass-murder case
scoring 56 that the desk could not see at all.

The snapshot now carries both windows, labelled, so the model can tell "live
now" from "biggest today" instead of being handed one and asked both.
"""

import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


class _Board:
    """A scratch database holding one fresh story and one from this morning."""

    def __enter__(self):
        from app import config, db
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="echo-snap-"))
        self._saved = config.DB_PATH
        config.DB_PATH = db.config.DB_PATH = self.dir / "t.db"
        db.init_db()
        now = datetime.now(timezone.utc)
        rows = [
            ("Minor item filed just now", 20, (now - timedelta(minutes=10))),
            ("Big thing that broke this morning", 88, (now - timedelta(hours=5))),
        ]
        with db.connect() as con:
            for i, (title, base, when) in enumerate(rows, 1):
                con.execute(
                    "INSERT INTO stories (id, title, url, publisher, published_at, "
                    "first_seen_at, last_updated_at, base_score, score, decay, "
                    "confidence, status, category, active, dedup_key) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i, title, f"http://x/{i}", "Test", when.isoformat(),
                     when.isoformat(), when.isoformat(), base, base, 0, 70,
                     "developing", "National", 1, f"k{i}"))
        self.db = db
        return self

    def __exit__(self, *exc):
        from app import config
        config.DB_PATH = self.db.config.DB_PATH = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def test_the_day_section_shows_what_the_board_cannot():
    from app.npro import desk

    with _Board():
        lines = desk._today_lines()
        text = "\n".join(lines)
        assert lines, "the day section rendered nothing"
        assert "Big thing that broke this morning" in text, (
            "a story from earlier today is still invisible to the desk — "
            "this is the whole point of the section")
        # and it must outrank the fresh-but-minor one, since the day view is
        # ranked by strength rather than by how recently it landed
        assert text.index("Big thing") < text.index("Minor item")


def test_the_two_windows_are_told_apart():
    """If the model cannot tell live from merely-today it will offer an
    hours-old story as the top of the next bulletin."""
    from app.npro import desk

    with _Board():
        text = "\n".join(desk._today_lines())
        assert "also on the board now" in text, \
            "nothing marks which of today's stories are still live"
        fresh_line = next(l for l in text.splitlines() if "Minor item" in l)
        old_line = next(l for l in text.splitlines() if "Big thing" in l)
        assert "also on the board now" in fresh_line
        assert "also on the board now" not in old_line


def test_the_snapshot_carries_both_sections():
    from app.npro import desk

    with _Board():
        snap = desk.desk_snapshot()
        assert "RANKED BOARD" in snap, "the live board section has gone"
        assert "TODAY'S STRONGEST" in snap, "the day section is not in the snapshot"
        # the day header has to say plainly that it is not all live, or the
        # model reads it as more board
        head = next(l for l in snap.splitlines() if l.startswith("TODAY'S STRONGEST"))
        assert "last hour" in head.lower()


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
