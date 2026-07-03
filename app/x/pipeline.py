"""X ingestion pipeline: layered provider fallback + dedup + guardrails.

Providers are tried in configured order; a ProviderUnavailable down-shifts to
the next layer (Master Prompt: twscrape -> Nitter/RSS -> Playwright; here the
simulated layer sits first while real layers are stubs). Tweet IDs are hashed
into an in-memory set backed by the tweets table, so duplicates never reach
the UI.
"""

import json

from app import config, db, events
from app.x import guardrails
from app.x.models import Tweet
from app.x.provider import ProviderUnavailable, XProvider
from app.x.sim_provider import SimulatedProvider
from app.x.stubs import NitterProvider, PlaywrightProvider, TwscrapeProvider


class XPipeline:
    def __init__(self):
        self.sim = SimulatedProvider(config.SIM_TWEETS_PER_MIN)
        if config.X_PROVIDER == "sim":
            self.layers: list[XProvider] = [self.sim]
        else:
            self.layers = [TwscrapeProvider(), NitterProvider(),
                           PlaywrightProvider(), self.sim]
        self.active_layer = self.layers[0].name
        self._seen_ids: set[str] = set()
        self._handles: list[dict] = []

    def load_handles(self) -> None:
        with db.connect() as con:
            rows = con.execute("SELECT * FROM handles").fetchall()
            self._handles = db.rows_to_dicts(rows)
            for r in con.execute("SELECT id FROM tweets ORDER BY rowid DESC LIMIT 5000"):
                self._seen_ids.add(r["id"])

    def poll(self) -> list[Tweet]:
        """One ingestion tick: fetch via fallback chain, dedup, apply guardrails,
        persist, and publish kept tweets to the SSE stream."""
        if not self._handles:
            self.load_handles()
            if not self._handles:
                return []

        tweets: list[Tweet] = []
        for layer in self.layers:
            try:
                tweets = layer.fetch_new_tweets(self._handles)
                if self.active_layer != layer.name:
                    self.active_layer = layer.name
                    events.publish("system_status", {"x_layer": layer.name})
                break
            except ProviderUnavailable:
                continue

        fresh = [t for t in tweets if t.id not in self._seen_ids]
        self._seen_ids.update(t.id for t in fresh)
        if len(self._seen_ids) > 20000:
            self._seen_ids = set(list(self._seen_ids)[-10000:])

        kept = []
        with db.connect() as con:
            for t in fresh:
                verdict = guardrails.evaluate(t)
                con.execute(
                    """INSERT OR IGNORE INTO tweets (id, handle, display_name, text,
                       created_at, stream_column, trust_score, news_signal,
                       discarded, discard_reason, terms)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (t.id, t.handle, t.display_name, t.text, t.created_at,
                     t.stream_column, t.trust_score, verdict["news_signal"],
                     int(not verdict["keep"]), verdict["reason"],
                     json.dumps(t.terms)),
                )
                if verdict["keep"]:
                    kept.append((t, verdict["news_signal"]))

        for t, signal in kept:
            events.publish("tweet", {
                "id": t.id, "handle": t.handle, "display_name": t.display_name,
                "text": t.text, "created_at": t.created_at,
                "stream_column": t.stream_column, "trust_score": t.trust_score,
                "news_signal": signal, "terms": t.terms,
            })
        return [t for t, _ in kept]


def seed_handles_table() -> int:
    """Load data/handles.json into the handles table (idempotent)."""
    path = config.DATA_DIR / "handles.json"
    if not path.exists():
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    with db.connect() as con:
        for h in records:
            con.execute(
                """INSERT INTO handles (handle, name, category, sub_category,
                   organization, region, stream_column, trust_score, notes)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(handle) DO UPDATE SET
                   name=excluded.name, category=excluded.category,
                   sub_category=excluded.sub_category, organization=excluded.organization,
                   region=excluded.region, stream_column=excluded.stream_column,
                   trust_score=excluded.trust_score, notes=excluded.notes""",
                (h["handle"], h["name"], h["category"], h["sub_category"],
                 h["organization"], h["region"], h["stream_column"],
                 h["trust_score"], h["notes"]),
            )
    return len(records)


pipeline = XPipeline()
