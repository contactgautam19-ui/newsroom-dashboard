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
              <div class="h-full rounded-full" style="width:${Math.round(s.score / maxScore * 100)}%;background:${s.status === 'breaking' ? '#D92D20' : s.status === 'verified' ? '#079455' : '#F79009'}"></div>
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

  // Live breaking feed: TV channels breaking on air + X news signals + viral
  // spikes, newest first. Auto-refreshes every 60s while the page is open.
  const ALERT_STYLE = {
    tv:       { icon: '📺', bar: '#D92D20', chip: 'bg-red1 text-red8' },
    x:        { icon: '𝕏',  bar: '#0B1526', chip: 'bg-paper text-ink' },
    velocity: { icon: '🚀', bar: '#2563EB', chip: 'bg-blue1 text-blue8' },
  };
  let alertsTimer = null;

  async function alerts() {
    const el = document.getElementById('alerts-body');
    const badge = document.getElementById('alert-badge');
    if (badge) badge.classList.add('hidden');
    if (typeof alertCount !== 'undefined') alertCount = 0;

    let items = [];
    try { items = await (await fetch('/api/alerts/feed?hours=24')).json(); } catch { /* empty ok */ }

    el.innerHTML = `
      <p class="text-[12.5px] text-sub mb-1">Live feed — what TV channels are breaking on air, news signals from monitored X handles, and viral spikes. Refreshes every minute.</p>
      ${items.length ? items.map(a => {
        const st = ALERT_STYLE[a.kind] || ALERT_STYLE.velocity;
        return `
        <div class="bg-white border border-line rounded-2xl p-4 flex items-center gap-4" style="border-left:4px solid ${st.bar}">
          <div class="shrink-0 w-10 h-10 rounded-full bg-paper flex items-center justify-center text-[16px]">${st.icon}</div>
          <div class="min-w-0 flex-1">
            <p class="text-[12px] mb-0.5">
              <span class="px-2 py-0.5 rounded font-bold text-[10px] tracking-wide ${st.chip}">${esc(a.tag)}</span>
              <span class="font-semibold ml-1.5">${esc(a.source)}</span>
            </p>
            <p class="text-[14px] leading-snug">${esc(a.title)}</p>
          </div>
          <span class="text-[12px] text-sub whitespace-nowrap">${ageLabel(a.at)}</span>
        </div>`;
      }).join('')
      : '<div class="bg-white border border-line rounded-2xl p-8 text-center text-sub text-[14px]">Nothing breaking right now. TV breaking banners, X news signals and viral spikes land here the moment they\'re detected.</div>'}`;

    // keep it live while the page is visible
    clearInterval(alertsTimer);
    alertsTimer = setInterval(() => {
      const page = document.getElementById('page-alerts');
      if (page && !page.classList.contains('hidden') && document.visibilityState === 'visible') alerts();
      else clearInterval(alertsTimer);
    }, 60000);
  }

  return { analytics, alerts };
})();

// Global breaking watch: flash the strip when a TV channel starts breaking a
// story or a fresh high-signal X post lands — independent of the story board.
(() => {
  const seen = new Set();
  let first = true;
  async function tick() {
    if (document.visibilityState !== 'visible') return;
    let items = [];
    try { items = await (await fetch('/api/alerts/feed?hours=2&limit=15')).json(); } catch { return; }
    for (const a of items) {
      const key = `${a.kind}:${a.source}:${(a.title || '').slice(0, 60)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (first) continue; // seed silently on load
      if (a.kind === 'tv') Flash.show('breaking', `${a.source} breaking: ${a.title}`);
      else if (a.kind === 'x' && /breaking|flash/i.test(a.tag)) Flash.show('breaking', `${a.source}: ${a.title}`);
      else if (a.kind === 'velocity') Flash.show('viral', a.title);
      else bumpAlerts();
    }
    first = false;
  }
  tick();
  setInterval(tick, 120000);
})();
