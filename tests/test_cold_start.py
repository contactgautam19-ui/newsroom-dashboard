"""Cold start cost, which on Vercel is paid on every idle wake.

Measured 5 November against the production database: a cold boot spent 16.9s
re-writing all 242 handle rows with values identical to the ones already
there, and the landing page took 63s to answer after an idle spell. For a
30-minute guest demo that is a minute of the sitting gone before anything
renders.

The rows only change when data/handles.json changes, so the file's own
fingerprint decides whether the write is needed. These tests hold that: it
must skip when nothing changed, and it must NOT skip when something did.

(pytest's tmp_path fixture cannot create its directory on this machine, so
these manage their own temp dirs.)
"""

import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


class _Sandbox:
    """An empty SQLite database and a scratch data dir, restored on exit."""

    def __enter__(self):
        from app import config, db, settings_store
        from app.x import pipeline as pl
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="echo-coldstart-"))
        self._saved = (config.DB_PATH, pl.config.DATA_DIR)
        config.DB_PATH = self.dir / "t.db"
        db.config.DB_PATH = self.dir / "t.db"
        db.init_db()
        settings_store.set_setting(pl._SEED_FINGERPRINT_KEY, "")   # first boot
        self.db, self.pl, self.settings = db, pl, settings_store
        return self

    def __exit__(self, *exc):
        from app import config
        from app.x import pipeline as pl
        config.DB_PATH, pl.config.DATA_DIR = self._saved
        self.db.config.DB_PATH = self._saved[0]
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def test_seeding_writes_once_then_skips():
    with _Sandbox() as s:
        assert s.pl.seed_handles_table() > 0, \
            "the first boot must actually seed the roster"
        assert s.pl.seed_handles_table() == 0, (
            "the roster was re-written although handles.json had not changed; "
            "that is the 16.9s every cold start used to pay")


def test_a_changed_roster_is_still_written():
    """The skip must key on the file's content, not merely on having run
    before — otherwise editing handles.json would silently never take."""
    from app import config

    with _Sandbox() as s:
        assert s.pl.seed_handles_table() > 0
        assert s.pl.seed_handles_table() == 0          # settled

        real = json.loads((config.DATA_DIR / "handles.json").read_text(encoding="utf-8"))
        extra = dict(real[0], handle="@ColdStartProbe", name="Cold Start Probe")
        data_dir = s.dir / "data"
        data_dir.mkdir()
        (data_dir / "handles.json").write_text(json.dumps(real + [extra]),
                                               encoding="utf-8")
        s.pl.config.DATA_DIR = data_dir

        assert s.pl.seed_handles_table() > 0, "an edited roster was skipped"
        with s.db.connect() as con:
            row = con.execute("SELECT name FROM handles WHERE handle = ?",
                              ("@ColdStartProbe",)).fetchone()
        assert row is not None, "the new handle never reached the table"


def test_a_failed_seed_is_retried_next_boot():
    """The fingerprint is recorded only after the write succeeds, so a boot
    that dies mid-seed does not mark the roster as done."""
    with _Sandbox() as s:
        real_connect = s.pl.db.connect

        def explode(*a, **k):
            raise RuntimeError("database went away")

        s.pl.db.connect = explode
        try:
            s.pl.seed_handles_table()
        except RuntimeError:
            pass
        finally:
            s.pl.db.connect = real_connect

        assert s.settings.get_setting(s.pl._SEED_FINGERPRINT_KEY, "") == "", \
            "a failed seed recorded itself as done and would never be retried"
        assert s.pl.seed_handles_table() > 0


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
