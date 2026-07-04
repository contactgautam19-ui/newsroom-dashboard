# Newsroom Intelligence Dashboard

A light-theme, editor-first newsroom dashboard: a navy sidebar shell around three desks and five story views.

- **Sidebar shell**: navy fixed sidebar with live/offline indicator, clock, and flash-alert badge. Views: **Top Stories**, **My Picks**, **Assignments**, **Story Desk** (home), **X Desk**, **Ops Desk**, **Analytics**, **Alerts**.
- **Story desk**: the decision page. Ranked story cards with a score badge, status pill, freshness age, rival on-air chips, and a plain-English "Why" line; category/Breaking/Developing/Picked filter chips; a refresh button (stories also auto-refresh on page open if the last ingest is stale, and every `NEWS_REFRESH_MINUTES` within the active window — configurable in Ops). **Pick story** marks a story as taken — it leaves the main board, a confirmation toast offers **Undo**, and the story reappears under the **Picked (n)** filter chip and the **My Picks** / **Assignments** sidebar views. **Story pack** opens everything a writer needs: all corroborating sources, the score anatomy, the evidence trail, related posts from monitored X handles, per-platform format suggestions (X / Instagram / Facebook), and the AI article writer.
- **X desk**: three columns of real tweets (Institutional & Government / Rival & Competitor / Field Reporters & Responders) with manual refresh and the API budget badge, plus a Top 5 signals block for posts needing editorial attention.
- **Ops desk**: the machinery — feed health, keywords searched, guardrail audit, auto-refresh interval, live rival-TV monitor status, AI writer settings, email brief controls. Editors never need this page day-to-day.
- **Analytics**: board balance (category mix, status split) and scoring anatomy across the live board.
- **Alerts**: breaking-news flashes and viral X acceleration events, with a flash strip that slides down over any page and jumps straight to the story.

The layout is fully responsive (columns stack, sidebar collapses to a drawer on phones).

## AI article writer

Every story pack has a **Write with AI** panel with three one-click formats — **Web article**, **Broadcast script**, **Social copy** (X thread + Instagram + Facebook). Drafts are strictly grounded: the writer is instructed (and, for the mock path, mechanically restricted) to use only facts already present in the story pack — sources, evidence lines, and related tweets — never inventing quotes, names, or numbers. Every draft is labeled for human review before use, and previous drafts stay listed on the pack for quick reuse.

- **With an Anthropic API key** (set in **Ops → AI writer settings**, along with your channel name, voice description, and optional sample articles for style-matching): drafts are generated live by Claude in your channel's voice and stored with token counts, shown with an amber **AI DRAFT — review before use** badge.
- **Without a key**: the same buttons still work — the backend falls back to `app/news/mock_writer.py`, which assembles an instant, clearly-labeled **`[MOCK DRAFT]`** template purely from story-pack data (headline options, bulleted evidence, verbatim tweet quotes, hashtags derived from the headline). Shown with a blue **MOCK DRAFT — template output** badge, so the pick → pack → write flow never dead-ends waiting on a key.

## Live rival-TV monitor

Polls six rival channels' YouTube "uploads" RSS feeds (`data/live_channels.json`: NDTV 24x7, India Today, Times Now, Republic TV, CNN-News18, WION) every `LIVE_POLL_MINUTES` (default 5). Two effects:

1. Board stories a rival is already airing get an on-air chip (`📺 On air: <channel>`) so the producer knows the competition is on it.
2. Topics rivals are covering that our board is *missing* become priority discovery keywords (origin `live-tv`) for the next ingest cycle — turning the competition into a lead generator. Keyword extraction is hardened against TV-noise words, filler/preposition tokens, and date/weekday tokens so only genuine story signal reaches discovery.

Status (last poll time, channels covered, clips in window, recent titles/errors) is visible on the Ops page.

## Top 5 signals, flash alerts, and the pick workflow

- **Top 5 signals**: the X desk surfaces the five posts most needing editorial attention, ranked from the same guardrail metrics that drive the story board — never an arbitrary/separate ranking.
- **Flash alerts**: a red **FLASH · BREAKING** strip for new high-score breaking stories and a blue **VIRAL ON X** strip for viral-acceleration events slide down over any page, badge the Alerts nav item, and jump straight to the story card on click.
- **Pick → My Picks**: picking a story is optimistic (the card moves immediately) and confirmed by a bottom-center toast with an **Undo** action; a successful pick also kicks off a background board refresh so the next-best candidates backfill the space it left.

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env       # then edit .env
python run.py                # http://127.0.0.1:8000
```

Static JS is served with a build-time cache-busting version stamp (`?v=<mtime>`) appended to every local script tag, so an edited/deployed `static/js/*.js` is always fetched fresh — no more "button does nothing" from a stale cached script.

### AI writer key (optional)

1. Get an API key from the [Anthropic Console](https://console.anthropic.com).
2. In the app, go to **Ops Desk → AI writer settings**, paste the key, and optionally set your channel name, voice description, and sample articles.
3. Without a key, the writer still works — see **AI article writer** above for the mock-draft fallback.

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
| `POST /api/stories/{id}/write?format=web\|broadcast\|social` | Generate an AI (or mock, if no key) draft and store it |
| `GET /api/stories/{id}/articles` | Previously generated drafts for a story |
| `GET /api/settings` / `POST /api/settings` | AI writer settings (channel voice, model, API key, `key_configured`) |
| `POST /api/brief` | Generate (and send, if enabled) a brief now |
| `POST /api/settings/news-refresh?minutes=N` | Set the auto-refresh interval (0 = hourly only) |
| `GET /api/ops` | Ops summary: last cycle stats, budget, guardrail audit, live-monitor status |
| `GET /api/rundown` | Ranked stories + score breakdowns (JSON) |
| `GET /api/velocity` | Recent velocity events |
| `GET /api/x/top-signals` | Top 5 X posts needing editorial attention |
| `POST /api/sim/spike` | Demo viral spike (only with `X_PROVIDER=sim`) |

Every action is also available from the UI (Story Desk, X Desk, and Ops pages).

## Architecture

```
Google News RSS ──▶ ingest ─▶ enrich ─▶ 100-pt scoring ─▶ guardrails ─▶ SQLite ─▶ SSE ─▶ UI (top)
                                              ▲                                        ▲
                                   +1..+10 trend offset                    rival-TV on-air chips
                                              │                                        │
X provider chain ─▶ dedup ─▶ X guardrails ─▶ broker (60s velocity ticks) ─▶ SSE ─▶ UI (bottom)
(twtapi today; twscrape → Nitter → Playwright stubs in app/x/stubs.py)

Rival YouTube feeds ─▶ live_monitor (poll every 5 min) ─▶ on-air match + missed-topic keywords ─▶ ingest
```

- **Backend**: FastAPI + APScheduler + SQLite (WAL), SSE at `/api/stream` (typed events: `rundown`, `tweet`, `velocity_event`, `system_status`).
- **Frontend**: `static/index.html` + vanilla JS modules (`app`, `story_desk`, `x_desk`, `ops`, `views`, `stream`), Tailwind (CDN), colors per spec (navy sidebar `#0B1526`, `#D92D20` breaking, `#F79009` developing, `#079455` verified).
- **Scoring** (`app/news/scoring.py`): Breaking 15 · Emotion 15 · Political 12 · Celebrity 10 · Economy 12 · Safety 15 · Visual 8 · Novelty 8 · Trend 15. Every scorer returns `(points, evidence[])`; the story pack's evidence trail and the email brief show the exact matched strings, so every point is programmatically traceable.
- **X feed**: the `XProvider` interface (`app/x/provider.py`) is the plug-in seam; TwtAPI is the default real-tweet provider, `SimulatedProvider` a demo fallback keyed to live headlines. Zero-API scraping tiers from the spec are documented stubs in `app/x/stubs.py`.
- **AI writer** (`app/news/writer.py` + `app/news/mock_writer.py`): shared story-pack grounding for both the live Anthropic path and the key-free mock fallback; both paths store every draft (success or mock) in the `articles` table.
- **Live monitor** (`app/news/live_monitor.py`): key-free YouTube RSS polling of six rival channels' upload feeds, with noise/filler/date-token filtering before anything becomes a discovery keyword.

Built from the specification documents in `AI Newsroom 3` (Master Prompt, Newsroom Dashboard MVP PRD, X Intelligence Dashboard spec, X Monitoring Guardrails, India X master database).

## Next steps (per the specs, not yet wired)

- Real scraping layers: twscrape account pool, self-hosted Nitter/RSS bridges, Playwright automation (+proxy rotation/backoff — skeletons and notes in `app/x/stubs.py`).
- Google Trends-backed Search Trend Momentum baseline.
- Handle verification pass for the spreadsheet's unverified ("black text") rows.
- Auth / multi-user / deployment hardening.
