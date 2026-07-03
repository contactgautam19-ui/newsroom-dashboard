# Newsroom Intelligence Dashboard

A light-theme, editor-first newsroom dashboard with three pages:

- **Story desk** (home): the decision page. Ranked story cards with a score badge, status pill, freshness age, and a plain-English "Why" line; category/Breaking filters; a refresh button (stories also auto-refresh every 10 minutes within the active window — configurable in Ops). **Pick story** marks a story as taken; **Story pack** opens everything a writer needs: all corroborating sources, the evidence trail, related posts from monitored X handles, and per-platform format suggestions (X / Instagram / Facebook, per the PRD omnichannel framework).
- **X desk**: three columns of real tweets with manual refresh and the API budget badge.
- **Ops**: the machinery — feed health, keywords searched, guardrail audit, auto-refresh interval, email brief controls. Editors never need this page.

The layout is fully responsive (columns stack, tabs scroll on phones).

## The two engines behind it

- **AI News Monitoring Agent**: every cycle (hourly 06:00–21:00, or faster via AUTO refresh) it (1) collects hot keywords from the monitored X accounts and Google Trends India, (2) runs each through a **past-hour Google News search** (`when:1h` — the automated version of the editor's manual keyword → News → Past hour workflow), (3) polls the curated 50-source RSS matrix, then clusters everything cross-outlet, drops candidates older than `FRESHNESS_HOURS` (3h), retires board stories older than `RETIRE_HOURS` (12h), and ranks the survivors on the evidence-based 100-point framework with editorial guardrails (two-source rule, <70% confidence review flag, repetitive decay). Stories found via a trending keyword carry a 🔍 chip and earn Search Trend Momentum points with the keyword cited as evidence.
- **X Monitoring Desk**: three independently scrolling columns (A: Institutional & Government, B: Rival/Competitor Channels, C: Field Reporters & Responders) showing **real tweets** (exact text, actual post time) from the 242-handle India X master database via TwtAPI, filtered by editorial guardrails. Refresh is **manual-only** (`𝕏 Refresh tweets` button) to respect the monthly API budget: each refresh spends one Search call per column (~3 total) covering the top-trust handles, and the header shows the remaining monthly calls after every refresh. If the API is unavailable the columns stay quiet — the desk never shows fabricated content. (`X_PROVIDER=sim` in `.env` switches back to the clearly-labelled simulated demo feed, which is auto-purged whenever real mode is active.)
- **X Conversation Broker**: every 60s aggregates term volume across scraped tweets; a >150% spike in a rolling 5-minute window matching an active headline fires a *Viral Acceleration Event*, injecting a dynamic offset into that story's Search Trend Momentum score (capped at the variable's 15 points) and showing a "trending on X" chip on the story card. Surges past 5,000 posts/hour override low-confidence holds and elevate to a *High-Demand Airtime Recommendation*.

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

### TwtAPI (real tweets)

1. Get your API key from your [TwtAPI dashboard](https://www.twtapi.com).
2. In `.env`: set `TWT_API_KEY=<your key>` and restart the server.
3. Click `𝕏 Refresh tweets` whenever you want the latest posts. Budgeting: ~3 calls per refresh → a 300-call month allows ~100 refreshes; `X_HANDLES_PER_COLUMN` (default 20) controls how many top-trust handles each column's search covers.

### Refreshing the handle database

`data/handles.json` is generated from the master spreadsheet:

```bash
python scripts/import_handles.py "path\to\india_x_master_database_1.xlsx"
```

## Demo / debug endpoints

| Endpoint | What it does |
|---|---|
| `POST /api/ingest` | Run a story refresh (discovery + matrix) now |
| `POST /api/x/refresh` | Fetch real tweets (~3 API calls), then chain a free story re-rank |
| `POST /api/stories/{id}/pick` | Toggle a story's picked state |
| `GET /api/stories/{id}/pack` | Story pack: sources, evidence, related tweets, format suggestions |
| `POST /api/brief` | Generate (and send, if enabled) a brief now |
| `POST /api/settings/news-refresh?minutes=N` | Set the auto-refresh interval (0 = hourly only) |
| `GET /api/ops` | Ops summary: last cycle stats, budget, guardrail audit |
| `GET /api/rundown` | Ranked stories + score breakdowns (JSON) |
| `GET /api/velocity` | Recent velocity events |
| `POST /api/sim/spike` | Demo viral spike (only with `X_PROVIDER=sim`) |

Every action is also available from the UI (Story desk and Ops pages).

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
