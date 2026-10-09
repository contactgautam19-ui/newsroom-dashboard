// Analytics + Alerts views (sidebar second group).

const Views = (() => {
  async function analytics() {
    const el = document.getElementById('analytics-body');
    let ops = {};
    try { ops = await (await fetch('/api/ops')).json(); } catch { /* partial ok */ }
    const stories = StoryDesk.stories.filter(s => !s.picked);
    const li = ops.last_ingest || {};

    const statuses = { breaking: 0, developing: 0, verified: 0 };
    stories.forEach(s => { statuses[s.status] = (statuses[s.status] || 0) + 1; });
    const maxScore = Math.max(1, ...stories.map(s => s.score));
    const top = [...stories].sort((a, b) => b.score - a.score).slice(0, 8);

    el.innerHTML = `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="bg-white border border-line rounded-2xl p-4"><p class="text-[11.5px] text-sub">Active stories</p><p class="text-[22px] font-bold">${stories.length}</p></div>
        <div class="bg-white border border-line rounded-2xl p-4"><p class="text-[11.5px] text-sub">Breaking</p><p class="text-[22px] font-bold text-red6">${statuses.breaking}</p></div>
        <div class="bg-white border border-line rounded-2xl p-4"><p class="text-[11.5px] text-sub">Needs review</p><p class="text-[22px] font-bold text-amber6">${stories.filter(s => s.needs_review).length}</p></div>
        <div class="bg-white border border-line rounded-2xl p-4"><p class="text-[11.5px] text-sub">Trending on X</p><p class="text-[22px] font-bold text-blue6">${stories.filter(s => s.trend_boost > 0).length}</p></div>
      </div>

      <div class="bg-white border border-line rounded-2xl p-5">
        <p class="text-[11.5px] font-semibold tracking-widest text-sub uppercase mb-3">Score leaderboard</p>
        <div class="space-y-2">${top.map(s => `
          <div class="flex items-center gap-3 text-[13px]">
            <span class="w-8 text-right font-bold">${s.score}</span>
            <div class="flex-1 h-3 rounded-full bg-paper overflow-hidden">
              <div class="h-full rounded-full" style="width:${Math.round(s.score / maxScore * 100)}%;background:${s.status === 'breaking' ? '#EC4A4D' : s.status === 'verified' ? '#34C38A' : '#F79009'}"></div>
            </div>
            <span class="w-[45%] truncate">${esc(s.title)}</span>
          </div>`).join('') || '<p class="text-sub text-[13px]">No stories yet.</p>'}
        </div>
      </div>

      <div class="bg-white border border-line rounded-2xl p-5">
        <p class="text-[11.5px] font-semibold tracking-widest text-sub uppercase mb-3">Last refresh cycle</p>
        <div class="text-[13px] text-sub space-y-1">
          ${li.last_ingest ? `
            <p>Ran ${ageLabel(li.last_ingest)} — ${li.new_stories ?? 0} new stories, top score ${li.max_score ?? '—'}</p>
            <p>Keywords: ${(li.keywords || []).map(esc).join(', ') || '—'}</p>
            <p>${li.discovery_hits ?? 0} past-hour hits · ${(li.feeds_polled ?? 0) - (li.feeds_failed ?? 0)}/${li.feeds_polled ?? 0} feeds healthy · ${li.dropped_stale ?? 0} stale dropped · ${li.dropped_junk ?? 0} junk dropped</p>
          ` : '<p>No cycle has run yet this session.</p>'}
        </div>
      </div>`;
  }

  // Live breaking feed: board stories the moment they are detected, TV channels
  // breaking on air, X news signals and viral spikes — newest first. While the
  // page is open it re-reads the feed every 20s and, every few minutes, asks
  // the server to go and scan the sources again, so new signals keep arriving
  // without anyone touching Refresh.
  const ALERT_STYLE = {
    story:    { icon: '⚡', bar: '#EC4A4D', chip: 'bg-red1 text-red8', label: 'Board' },
    tv:       { icon: '📺', bar: '#F0A93B', chip: 'bg-amber1 text-amber8', label: 'On air' },
    x:        { icon: 'X',  bar: '#8C9AB0', chip: 'bg-paper text-ink', label: 'X' },
    velocity: { icon: '🚀', bar: '#8FB4E8', chip: 'bg-blue1 text-blue8', label: 'Viral' },
  };
  const FEED_EVERY_MS = 20000;
  const SCAN_EVERY_MS = 150000;
  const A = { items: [], known: null, fresh: new Set(), kind: 'all',
              feedTimer: null, scanTimer: null, tickTimer: null,
              scanning: false, lastScan: null, lastFeed: null };

  const alertKey = a => `${a.kind}:${a.source}:${(a.title || '').slice(0, 80)}`;
  const alertsVisible = () => {
    const page = document.getElementById('page-alerts');
    return page && !page.classList.contains('hidden') && document.visibilityState === 'visible';
  };

  function alertRow(a) {
    const st = ALERT_STYLE[a.kind] || ALERT_STYLE.velocity;
    const isNew = A.fresh.has(alertKey(a));
    const jump = a.story_id != null
      ? `onclick="Flash.jumpTo(${Number(a.story_id)})" role="button" title="Open on the story board"` : '';
    return `
      <div ${jump} class="${isNew ? 'fade-up ' : ''}bg-white border border-line rounded-2xl p-4 flex items-center gap-4 ${a.story_id != null ? 'cursor-pointer hover:border-ink' : ''}"
           style="border-left:4px solid ${st.bar};${isNew ? 'background:#1A1418' : ''}">
        <div class="shrink-0 w-10 h-10 rounded-full bg-paper flex items-center justify-center text-[16px] font-bold">${st.icon}</div>
        <div class="min-w-0 flex-1">
          <p class="text-[12px] mb-0.5">
            ${isNew ? '<span class="px-1.5 py-0.5 rounded font-bold text-[10px] tracking-wide bg-brand text-white mr-1">NEW</span>' : ''}
            <span class="px-2 py-0.5 rounded font-bold text-[10px] tracking-wide ${st.chip}">${esc(a.tag)}</span>
            <span class="font-semibold ml-1.5">${esc(a.source)}</span>
          </p>
          <p class="text-[14px] leading-snug">${esc(a.title)}</p>
        </div>
        <span class="text-[12px] text-sub whitespace-nowrap">${ageLabel(a.at)}</span>
      </div>`;
  }

  function secsAgo(t) { return t ? Math.max(0, Math.round((Date.now() - t) / 1000)) : null; }

  function renderAlertStatus() {
    const el = document.getElementById('alerts-status');
    if (!el) return;
    const feed = secsAgo(A.lastFeed);
    const scan = secsAgo(A.lastScan);
    const parts = [];
    if (A.scanning) parts.push('Scanning news sources and live channels…');
    else if (scan != null) {
      const next = Math.max(0, Math.round(SCAN_EVERY_MS / 1000) - scan);
      parts.push(`Sources scanned ${scan < 60 ? scan + 's' : Math.floor(scan / 60) + ' min'} ago · next scan in ${Math.floor(next / 60)}:${String(next % 60).padStart(2, '0')}`);
    }
    if (feed != null) parts.push(`feed checked ${feed}s ago`);
    if (A.fresh.size) parts.push(`${A.fresh.size} new since you opened this page`);
    el.textContent = parts.join(' · ');
    const dot = document.getElementById('alerts-dot');
    if (dot) dot.style.background = A.scanning ? '#F0A93B' : '#34C38A';
  }

  function renderAlerts() {
    const list = document.getElementById('alerts-list');
    const chips = document.getElementById('alerts-kinds');
    if (!list) return;
    const counts = {};
    A.items.forEach(a => { counts[a.kind] = (counts[a.kind] || 0) + 1; });
    if (chips) {
      chips.innerHTML = [['all', 'All', A.items.length], ...Object.keys(ALERT_STYLE).map(k => [k, ALERT_STYLE[k].label, counts[k] || 0])]
        .map(([k, label, n]) => `<button onclick="Views.alertKind('${k}')" class="px-3 py-1.5 rounded-lg text-[12.5px] font-semibold border bg-white text-sub hover:border-ink"
          ${A.kind === k ? 'style="background:#EEF1F6 !important;color:#05080F !important;border-color:#EEF1F6"' : ''}>${label} <span class="opacity-60">${n}</span></button>`).join('');
    }
    const view = A.kind === 'all' ? A.items : A.items.filter(a => a.kind === A.kind);
    list.innerHTML = view.length ? view.map(alertRow).join('')
      : '<div class="bg-white border border-line rounded-2xl p-8 text-center text-sub text-[14px]">Nothing breaking right now. New board stories, TV breaking banners, X news signals and viral spikes land here the moment they\'re detected — this page keeps scanning.</div>';
    renderAlertStatus();
  }

  async function loadAlertFeed() {
    let items;
    try { items = await (await fetch('/api/alerts/feed?hours=12&limit=60', { cache: 'no-store' })).json(); }
    catch { return; }
    if (!Array.isArray(items)) return;
    const keys = items.map(alertKey);
    if (A.known) keys.forEach(k => { if (!A.known.has(k)) A.fresh.add(k); });
    A.known = new Set([...(A.known || []), ...keys]);
    A.items = items;
    A.lastFeed = Date.now();
    renderAlerts();
  }

  async function scanAlerts(manual) {
    if (A.scanning) return;
    A.scanning = true;
    renderAlertStatus();
    try {
      const r = await (await fetch('/api/alerts/scan', { method: 'POST' })).json();
      if (r && r.last_scan) A.lastScan = new Date(r.last_scan).getTime();
      if (manual && r && r.scanned === false) Toast.show(`Sources were scanned moments ago — next scan in ${r.next_in}s`);
    } catch { /* the feed poll still runs */ }
    A.scanning = false;
    await loadAlertFeed();
  }

  function stopAlerts() {
    clearInterval(A.feedTimer); clearInterval(A.scanTimer); clearInterval(A.tickTimer);
    A.feedTimer = A.scanTimer = A.tickTimer = null;
  }

  async function alerts() {
    const el = document.getElementById('alerts-body');
    const badge = document.getElementById('alert-badge');
    if (badge) badge.classList.add('hidden');
    if (typeof alertCount !== 'undefined') alertCount = 0;

    // a fresh visit starts a fresh "new since you opened" count
    A.known = null; A.fresh = new Set();
    el.innerHTML = `
      <div class="bg-white border border-line rounded-2xl px-4 py-3 flex items-center gap-3 flex-wrap">
        <span class="flex items-center gap-2 text-[13px] font-bold"><span id="alerts-dot" class="w-2 h-2 rounded-full" style="background:#34C38A"></span>Live</span>
        <span id="alerts-status" class="text-[12.5px] text-sub">Loading…</span>
        <button onclick="Views.scanAlerts(true)" class="ml-auto flex items-center gap-2 bg-navy text-white text-[13px] font-semibold px-4 py-2 rounded-xl hover:bg-navy2">⟳ Scan now</button>
      </div>
      <div id="alerts-kinds" class="flex items-center gap-1.5 flex-wrap"></div>
      <div id="alerts-list" class="space-y-2.5"></div>`;

    await loadAlertFeed();
    scanAlerts(false);   // look for anything newer than what is stored

    stopAlerts();
    A.feedTimer = setInterval(() => { if (alertsVisible()) loadAlertFeed(); }, FEED_EVERY_MS);
    A.scanTimer = setInterval(() => { if (alertsVisible()) scanAlerts(false); }, SCAN_EVERY_MS);
    A.tickTimer = setInterval(() => {
      if (document.getElementById('page-alerts').classList.contains('hidden')) stopAlerts();
      else renderAlertStatus();
    }, 1000);
  }

  function alertKind(k) { A.kind = k; renderAlerts(); }

  return { analytics, alerts, alertKind, scanAlerts };
})();

// Global breaking watch: flash the strip when a breaking story reaches the
// board, a TV channel starts breaking one, or a high-signal X post lands —
// on every page, not just Alerts.
(() => {
  const seen = new Set();
  let first = true;
  async function tick() {
    if (document.visibilityState !== 'visible') return;
    let items = [];
    try { items = await (await fetch('/api/alerts/feed?hours=2&limit=20', { cache: 'no-store' })).json(); } catch { return; }
    if (!Array.isArray(items)) return;
    for (const a of items) {
      const key = `${a.kind}:${a.source}:${(a.title || '').slice(0, 60)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (first) continue; // seed silently on load
      if (a.kind === 'story' && a.tag === 'BREAKING') Flash.show('breaking', a.title, a.story_id);
      else if (a.kind === 'tv') Flash.show('breaking', `${a.source} breaking: ${a.title}`);
      else if (a.kind === 'x' && /breaking|flash/i.test(a.tag)) Flash.show('breaking', `${a.source}: ${a.title}`);
      else if (a.kind === 'velocity') Flash.show('viral', a.title);
      else bumpAlerts();
    }
    first = false;
  }
  tick();
  setInterval(tick, 45000);
})();
