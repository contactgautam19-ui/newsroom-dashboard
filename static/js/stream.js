// Client feed: tries SSE (/api/stream) for local dev; on Vercel serverless
// that endpoint 404s / never opens, so after two failures within ~10s of the
// initial attempt we permanently fall back to polling for the rest of the
// session. Polling re-fetches rundown/tweets/ops on fixed intervals and does
// client-side breaking/viral flash detection since the server can't push.

(() => {
  let pollingMode = false;
  let failCount = 0;
  let firstAttemptAt = 0;

  function connect() {
    firstAttemptAt = Date.now();
    failCount = 0;
    const source = new EventSource('/api/stream');

    source.addEventListener('rundown', e => {
      StoryDesk.render(JSON.parse(e.data));
    });

    let signalsTimer = null;
    source.addEventListener('tweet', e => {
      XDesk.add(JSON.parse(e.data));
      clearTimeout(signalsTimer);
      signalsTimer = setTimeout(() => XDesk.loadSignals(), 1500);
    });

    source.addEventListener('velocity_event', e => {
      const ev = JSON.parse(e.data);
      Flash.show('viral', `${ev.hashtag} +${Math.round(ev.velocity_pct)}% — ${ev.story_title || ''}`, ev.story_id);
    });

    source.addEventListener('flash', e => {
      const f = JSON.parse(e.data);
      Flash.show(f.kind, f.title, f.story_id);
    });

    source.addEventListener('system_status', e => {
      const d = JSON.parse(e.data);
      if (d.state === 'ingesting') { setLive('busy'); setUpdated('refreshing…'); }
      else if (d.last_ingest) { setLive('live'); setUpdated(`Refreshed ${ageLabel(d.last_ingest)}`); }
      else if (d.x_error) XDesk.note(d.x_error);
    });

    source.addEventListener('x_refresh', e => {
      XDesk.budget(JSON.parse(e.data));
    });

    source.onopen = () => { setLive('live'); };

    source.onerror = () => {
      setLive('offline');
      if (pollingMode) return;
      // On Vercel serverless /api/stream returns 404, which the browser treats
      // as a fatal error: it fires onerror once and CLOSES the EventSource
      // permanently (no auto-retry). So a single close with readyState CLOSED
      // means SSE is unavailable — fall back to polling immediately rather than
      // waiting for a second failure that will never come.
      if (source.readyState === EventSource.CLOSED) {
        startPolling();
        return;
      }
      failCount += 1;
      const withinWindow = Date.now() - firstAttemptAt < 10000;
      if (failCount >= 2 && withinWindow) {
        source.close();
        startPolling();
        return;
      }
      // Otherwise EventSource will auto-retry the connection on its own.
    };
  }

  // ── Polling fallback (serverless / no SSE) ──────────────────────────────

  function startPolling() {
    if (pollingMode) return;
    pollingMode = true;

    const seenTweetIds = new Set();
    let prevStories = null; // id -> { status, trend_boost }; null until first poll seeds it

    function trackAndFlash(stories) {
      const nextMap = new Map();
      const isFirstPoll = prevStories === null;
      (stories || []).forEach(s => {
        nextMap.set(s.id, { status: s.status, trend_boost: s.trend_boost });
        if (isFirstPoll) return; // seed silently — nothing is "new" on the first poll
        const prev = prevStories.get(s.id);
        if (!prev) {
          if (s.status === 'breaking') Flash.show('breaking', s.title, s.id);
        } else if (!(prev.trend_boost > 0) && s.trend_boost > 0) {
          Flash.show('viral', `Trending on X — ${s.title}`, s.id);
        }
      });
      prevStories = nextMap;
    }

    async function pollRundown() {
      try {
        const res = await fetch('/api/rundown');
        const data = await res.json();
        trackAndFlash(data);
        StoryDesk.render(data);
        setLive('live');
      } catch {
        setLive('offline');
      }
    }

    let tweetsSeeded = false;
    async function pollTweets() {
      try {
        const res = await fetch('/api/tweets');
        const list = await res.json(); // newest first
        const fresh = Array.isArray(list) ? [...list].reverse() : []; // oldest -> newest
        if (!tweetsSeeded) {
          // XDesk already loaded the current list on page load (backfill) —
          // just record these ids as seen instead of re-adding duplicates.
          fresh.forEach(t => { if (t && t.id != null) seenTweetIds.add(t.id); });
          tweetsSeeded = true;
        } else {
          fresh.forEach(t => {
            if (t && t.id != null && !seenTweetIds.has(t.id)) {
              seenTweetIds.add(t.id);
              XDesk.add(t);
            }
          });
        }
        XDesk.loadSignals();
        setLive('live');
      } catch {
        setLive('offline');
      }
    }

    async function pollOps() {
      try {
        const res = await fetch('/api/ops');
        const d = await res.json();
        if (d && d.last_ingest && d.last_ingest.last_ingest) {
          setUpdated(`Refreshed ${ageLabel(d.last_ingest.last_ingest)}`);
        }
        setLive('live');
      } catch {
        setLive('offline');
      }
    }

    // Seed immediately, then start the recurring cadences.
    pollRundown();
    pollTweets();
    pollOps();

    setInterval(pollRundown, 30000);
    setInterval(pollTweets, 60000);
    setInterval(pollOps, 60000);

    // Client-side refresh loop replacing the server scheduler on serverless.
    setInterval(async () => {
      try { await fetch('/api/ingest', { method: 'POST' }); } catch { /* fire-and-forget */ }
    }, 10 * 60000);

    setInterval(pollOps, 5 * 60000);
  }

  // Paint the board immediately on load from a plain fetch, independent of
  // whether SSE or polling ends up driving live updates — the board is never
  // empty while the transport is being established.
  (async () => {
    try {
      const data = await (await fetch('/api/rundown')).json();
      if (Array.isArray(data) && data.length) StoryDesk.render(data);
    } catch { /* SSE/polling will fill it in */ }
  })();

  connect();

  setInterval(() => StoryDesk.render(), 60000); // keep relative times honest
})();
