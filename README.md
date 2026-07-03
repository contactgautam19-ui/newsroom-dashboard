# Newsroom Intelligence Dashboard

A single-screen, dark-theme newsroom dashboard combining two live systems:

- **Top half — AI News Monitoring Agent**: ingests the top Google News (India) stories every hour (06:00–21:00), maps them into a strict schema, ranks them on an evidence-based 100-point framework, and applies editorial guardrails (two-source rule, <70% confidence review flag, repetitive-decay for stale stories).
- **Bottom half — X Monitoring Desk**: three independently scrolling columns (A: Institutional & Government, B: Rival/Competitor Channels, C: Field Reporters & Responders) fed from the 242-handle India X master database, filtered by editorial guardrails.
- **X Conversation Broker**: every 60s aggregates term volume across scraped tweets; a >150% spike in a rolling 5-minute window matching an active headline fires a *Viral Acceleration Event*, injecting a +1..+10 point offset into that story's Search Trend Momentum score and rendering an inline `X TREND ACCELERATION` sub-row. Surges past 5,000 posts/hour override low-confidence holds and elevate to a *High-Demand Airtime Recommendation*.

Built from the specification documents in `AI Newsroom 3` (Master Prompt, Newsroom Dashboard MVP PRD, X Intelligence Dashboard spec, X Monitoring Guardrails, India X master database).

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env       # then edit .env
python run.py                # http://127.0.0.1:8000
```

### Email briefs (optional)

Hourly HTML briefs go to `BRIEF_RECIPIENT` (default `gautam.news9@gmail.com`) at :58 within the active window. To enable:

1. Google Account → Security → 2-Step Verification → App passwords → create one.
2. In `.env`: set `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_ENABLED=true`.

Every brief is also saved to `briefings_out/` and viewable at `/api/briefings/latest` regardless of email settings.

### Refreshing the handle database

`data/handles.json` is generated from the master spreadsheet:

```bash
python scripts/import_handles.py "path\to\india_x_master_database_1.xlsx"
```

## Demo / debug endpoints

| Endpoint | What it does |
|---|---|
| `POST /api/ingest` | Run a Google News ingest cycle now |
| `POST /api/sim/spike` | Flood the top story's terms for 5 min — watch the Viral Acceleration → score-boost loop fire |
| `POST /api/brief` | Generate (and send, if enabled) a brief now |
| `GET /api/rundown` | Ranked stories + score breakdowns (JSON) |
| `GET /api/velocity` | Recent velocity events |

The header buttons trigger the same actions from the UI.

## Architecture

```
Google News RSS ──▶ ingest ─▶ enrich ─▶ 100-pt scoring ─▶ guardrails ─▶ SQLite ─▶ SSE ─▶ UI (top)
                                              ▲                                        
                                   +1..+10 trend offset                               
                                              │                                        
X provider chain ─▶ dedup ─▶ X guardrails ─▶ broker (60s velocity ticks) ─▶ SSE ─▶ UI (bottom)
(sim today; twscrape → Nitter → Playwright stubs in app/x/stubs.py)
```

- **Backend**: FastAPI + APScheduler + SQLite (WAL), SSE at `/api/stream` (typed events: `rundown`, `tweet`, `velocity_event`, `system_status`).
- **Frontend**: `static/index.html` + vanilla JS modules, Tailwind (CDN), colors per spec (`#0F1419` bg, `#E02424` breaking, `#D97706` developing, `#059669` verified).
- **Scoring** (`app/news/scoring.py`): Breaking 15 · Emotion 15 · Political 12 · Celebrity 10 · Economy 12 · Safety 15 · Visual 8 · Novelty 8 · Trend 15. Every scorer returns `(points, evidence[])`; the UI evidence expander and the email brief show the exact matched strings, so every point is programmatically traceable.
- **X feed**: the `XProvider` interface (`app/x/provider.py`) is the plug-in seam. Today `SimulatedProvider` generates realistic traffic from the real handle DB (keyed to live headlines so broker matching genuinely fires). The zero-API scraping tiers from the spec are documented stubs in `app/x/stubs.py`.

## Next steps (per the specs, not yet wired)

- Real scraping layers: twscrape account pool, self-hosted Nitter/RSS bridges, Playwright automation (+proxy rotation/backoff — skeletons and notes in `app/x/stubs.py`).
- Google Trends-backed Search Trend Momentum baseline.
- Omnichannel per-platform format suggestions (PRD §5).
- Handle verification pass for the spreadsheet's unverified ("black text") rows.
- Auth / multi-user / deployment hardening.
