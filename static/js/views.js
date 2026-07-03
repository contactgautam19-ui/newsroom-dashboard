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

  async function alerts() {
    const el = document.getElementById('alerts-body');
    let events = [];
    try { events = await (await fetch('/api/velocity?limit=25')).json(); } catch { /* empty ok */ }
    const badge = document.getElementById('alert-badge');
    if (badge) badge.classList.add('hidden');
    if (typeof alertCount !== 'undefined') alertCount = 0;

    el.innerHTML = events.length ? events.map(v => `
      <div class="bg-white border border-line rounded-2xl p-4 flex items-center gap-4" style="border-left:4px solid ${v.high_demand ? '#D92D20' : '#2563EB'}">
        <div class="shrink-0 w-10 h-10 rounded-full ${v.high_demand ? 'bg-red1' : 'bg-blue1'} flex items-center justify-center text-[16px]">${v.high_demand ? '🔥' : '🚀'}</div>
        <div class="min-w-0 flex-1">
          <p class="text-[14px] font-semibold">#${esc(v.term)} <span class="text-green6">+${Math.round(v.velocity_pct)}%</span>
            <span class="text-sub font-normal">· ${Math.round(v.posts_per_hour).toLocaleString('en-IN')} posts/hr · +${v.boost} pts injected</span>
            ${v.high_demand ? '<span class="text-red6 font-semibold"> · HIGH DEMAND</span>' : ''}
          </p>
          <p class="text-[13px] text-sub truncate">${esc(v.story_title || 'no matching board story')}</p>
        </div>
        <span class="text-[12px] text-sub whitespace-nowrap">${ageLabel(v.created_at)}</span>
      </div>`).join('')
      : '<div class="bg-white border border-line rounded-2xl p-8 text-center text-sub text-[14px]">No viral acceleration events yet. They appear here when a term spikes >150% and matches a board story.</div>';
  }

  return { analytics, alerts };
})();
