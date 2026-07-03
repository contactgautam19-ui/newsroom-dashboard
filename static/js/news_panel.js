// Section 1: ranked story rows with expandable evidence breakdowns and the
// inline "X TREND ACCELERATION" sub-row driven by broker velocity events.

const NewsPanel = (() => {
  const container = () => document.getElementById('news-rows');
  const expanded = new Set();          // story ids with open evidence panel
  const latestVelocity = new Map();    // story_id -> latest velocity event

  const STATUS_STYLE = {
    breaking: 'bg-breaking/15 text-breaking border-breaking',
    developing: 'bg-develop/15 text-develop border-develop',
    verified: 'bg-verified/15 text-verified border-verified',
  };

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML;
  }

  function scoreColor(score) {
    if (score >= 70) return 'text-breaking';
    if (score >= 45) return 'text-develop';
    return 'text-dim';
  }

  function evidenceHtml(story) {
    const rows = (story.breakdown || [])
      .sort((a, b) => b.points - a.points)
      .map(b => `
        <div class="flex gap-2 items-baseline py-0.5">
          <span class="w-40 shrink-0 text-faint">${esc(b.variable)}</span>
          <span class="w-14 shrink-0 font-mono ${b.points > 0 ? 'text-accent' : 'text-faint'}">${b.points}/${b.max_points}</span>
          <span class="text-dim">${(b.evidence || []).map(esc).join(' · ') || '—'}</span>
        </div>`).join('');
    const decay = story.decay > 0
      ? `<div class="flex gap-2 items-baseline py-0.5"><span class="w-40 shrink-0 text-faint">repetitive decay</span><span class="w-14 shrink-0 font-mono text-develop">−${story.decay}</span><span class="text-dim">story has not materially developed (${story.stale_cycles} stale cycle${story.stale_cycles === 1 ? '' : 's'})</span></div>`
      : '';
    return `<div class="mt-2 pt-2 border-t border-edge text-[11px]">${rows}${decay}</div>`;
  }

  function accelerationHtml(story) {
    if (!story.trend_boost || story.trend_boost <= 0) return '';
    const ve = latestVelocity.get(story.id);
    const tag = ve ? ve.hashtag : '#Trending';
    const pct = ve ? `+${Math.round(ve.velocity_pct)}%` : '';
    const pph = ve ? `${ve.posts_per_hour.toLocaleString()} posts/hr` : '';
    return `
      <div class="mt-1.5 flex items-center gap-3 rounded bg-accent/10 border border-accent/40 px-2 py-1 text-[11px]">
        <span class="flame">${story.high_demand ? '🔥' : '🚀'}</span>
        <span class="font-bold tracking-widest text-accent">X TREND ACCELERATION</span>
        <span class="font-mono text-white">${esc(tag)}</span>
        <span class="font-mono text-verified">${pct}</span>
        <span class="text-dim">${pph}</span>
        <span class="ml-auto font-mono text-accent">+${story.trend_boost} pts → Trend Momentum</span>
        ${story.high_demand ? '<span class="font-bold text-breaking">HIGH-DEMAND AIRTIME</span>' : ''}
      </div>`;
  }

  function rowHtml(story, rank) {
    const st = STATUS_STYLE[story.status] || STATUS_STYLE.developing;
    const srcCount = (story.sources || []).length;
    const flags = Object.entries(story.flags || {})
      .filter(([k, v]) => v === true && !['breaking', 'developing'].includes(k))
      .map(([k]) => `<span class="px-1.5 rounded bg-edge text-faint text-[10px] uppercase">${esc(k)}</span>`)
      .join(' ');
    return `
      <div class="rounded-md bg-panel border border-edge px-3 py-2 cursor-pointer hover:border-faint transition-colors ${story.status === 'breaking' ? 'pulse-breaking' : ''}"
           data-story="${story.id}">
        <div class="flex items-center gap-3">
          <span class="font-mono text-faint w-5 text-right">${rank}</span>
          <span class="font-mono font-bold text-lg w-14 text-center ${scoreColor(story.score)}">${story.score}<span class="text-[10px] text-faint">/100</span></span>
          <span class="px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase tracking-wider ${st}">${esc(story.status)}</span>
          <div class="min-w-0 flex-1">
            <div class="text-white font-semibold truncate">${esc(story.title)}</div>
            <div class="text-[11px] text-dim flex gap-2 items-center flex-wrap">
              <span>${esc(story.publisher || '')}</span>
              ${srcCount > 1 ? `<span class="text-verified">+${srcCount - 1} corroborating</span>` : '<span class="text-develop">single source</span>'}
              <span>· ${esc(story.category)} · ${esc(story.location)}</span>
              ${flags}
            </div>
          </div>
          <div class="text-right shrink-0">
            <div class="font-mono text-[11px] ${story.confidence < 70 ? 'text-develop' : 'text-verified'}">conf ${story.confidence}%</div>
            ${story.needs_review ? '<div class="text-[10px] font-bold text-develop">⚠ NEEDS REVIEW</div>' : ''}
            ${story.high_demand ? '<div class="text-[10px] font-bold text-breaking">HIGH-DEMAND</div>' : ''}
          </div>
        </div>
        ${accelerationHtml(story)}
        ${expanded.has(story.id) ? evidenceHtml(story) : ''}
      </div>`;
  }

  function render(stories) {
    const el = container();
    if (!el) return;
    el.innerHTML = stories.map((s, i) => rowHtml(s, i + 1)).join('');
    el.querySelectorAll('[data-story]').forEach(node => {
      node.addEventListener('click', () => {
        const id = Number(node.dataset.story);
        expanded.has(id) ? expanded.delete(id) : expanded.add(id);
        render(stories);
      });
    });
    window.__lastRundown = stories;
  }

  function onVelocity(ev) {
    latestVelocity.set(ev.story_id, ev);
  }

  return { render, onVelocity };
})();
