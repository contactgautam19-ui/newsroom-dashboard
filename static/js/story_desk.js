// Story desk: overview donut, editor insight, quick actions, ranked cards.

const StoryDesk = (() => {
  let stories = [];
  let filter = 'All';
  let sortBy = 'score';

  const STATUS = {
    breaking:   { label: 'Breaking',   bar: '#D92D20', bg: 'bg-red1',   text: 'text-red8',   circleBg: '#FEF3F2', circleText: '#B42318' },
    developing: { label: 'Developing', bar: '#F79009', bg: 'bg-amber1', text: 'text-amber8', circleBg: '#FFFAEB', circleText: '#B54708' },
    verified:   { label: 'Verified',   bar: '#079455', bg: 'bg-green1', text: 'text-green8', circleBg: '#ECFDF3', circleText: '#085D3A' },
  };

  const CHIP_COLORS = {
    Breaking: 'text-red6 border-red6/30', Developing: 'text-amber6 border-amber6/30',
    Business: 'text-blue6 border-blue6/30', Entertainment: 'text-purple6 border-purple6/30',
    International: 'text-blue6 border-blue6/30', National: 'text-teal6 border-teal6/30',
    Politics: 'text-pink6 border-pink6/30', Legal: 'text-blue8 border-blue8/30',
    Sports: 'text-green6 border-green6/30', Technology: 'text-purple6 border-purple6/30',
  };

  const WHY = {
    breaking:  b => b.evidence[0]?.includes('published') ? 'Very fresh development' : 'Breaking development',
    political: () => 'Power centre involved',
    emotion:   () => 'Strong emotional pull',
    celebrity: () => 'High-profile names',
    economy:   () => 'Money impact for viewers',
    safety:    () => 'Public-safety relevance',
    visual:    () => 'Strong visuals available',
    novelty:   () => 'Unusual, unexpected angle',
    trend:     () => 'Trending on X right now',
  };

  function whyLine(s) {
    const parts = (s.breakdown || [])
      .filter(b => b.points > 0)
      .sort((a, b) => b.points - a.points)
      .slice(0, 3)
      .map(b => (WHY[b.variable] || (() => b.variable))(b));
    const srcs = (s.sources || []).length;
    if (srcs > 1) parts.push(`Corroborated by ${srcs} outlets`);
    else if (s.needs_review) parts.push('Single source — verify before air');
    if (Array.isArray(s.rival_coverage) && s.rival_coverage.length) {
      parts.unshift(`Rivals airing this now (${s.rival_coverage.join(' & ')})`);
    }
    return parts.join('  ·  ');
  }

  function card(s) {
    const st = STATUS[s.status] || STATUS.developing;
    const mins = Math.round((Date.now() - new Date(s.published_at).getTime()) / 60000);
    const ageColor = mins <= 60 ? 'text-green6' : mins <= 180 ? 'text-amber6' : 'text-sub';
    const corroborated = (s.sources || []).length > 1;
    return `
    <article class="fade-up bg-white border border-line rounded-2xl p-4 md:p-5 flex flex-col sm:flex-row gap-4" style="border-left:4px solid ${st.bar}" data-id="${s.id}">
      <div class="text-center shrink-0 hidden sm:block pt-1">
        <div class="w-[52px] h-[52px] rounded-full flex items-center justify-center text-[19px] font-bold" style="background:${st.circleBg};color:${st.circleText}">${s.score}</div>
        <div class="text-[9.5px] font-semibold tracking-widest text-sub mt-1.5">SCORE</div>
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 flex-wrap mb-1.5 text-[12px]">
          <span class="sm:hidden font-bold text-[14px]" style="color:${st.circleText}">${s.score}</span>
          <span class="px-2.5 py-0.5 rounded-md font-semibold ${st.bg} ${st.text}">${st.label}</span>
          ${s.needs_review ? '<span class="px-2.5 py-0.5 rounded-md font-semibold bg-amber1 text-amber8">Needs Review</span>' : ''}
          ${s.high_demand ? '<span class="px-2.5 py-0.5 rounded-md font-semibold bg-red1 text-red8">High Demand</span>' : ''}
          ${Array.isArray(s.rival_coverage) && s.rival_coverage.length ? `<span class="px-2.5 py-0.5 rounded-md font-semibold bg-red1 text-red8">📺 On air: ${esc(s.rival_coverage.join(', '))}</span>` : ''}
          <span class="font-medium ${ageColor}">${ageLabel(s.published_at)}</span>
        </div>
        <h3 class="text-[16.5px] font-bold leading-snug mb-1">
          ${s.url ? `<a href="${esc(s.url)}" target="_blank" class="hover:underline">${esc(s.title)}</a>` : esc(s.title)}
        </h3>
        <p class="text-[13px] text-sub flex items-center gap-1.5 flex-wrap">
          <span>${esc(s.publisher || '')}</span>
          <span>·</span><span>${esc(s.category)}</span>
          ${corroborated ? '<svg width="15" height="15" viewBox="0 0 24 24" fill="#2563EB"><path d="M12 2l2.4 2.4 3.4-.5 1 3.3 3.2 1.4-1.4 3.1 1.7 3-2.9 1.9-.2 3.5-3.4.3-1.9 2.9-3.1-1.5-3.1 1.5-1.9-2.9-3.4-.3-.2-3.5L2.3 14l1.7-3-1.4-3.1 3.2-1.4 1-3.3 3.4.5z"/><path d="M9 12.5l2 2 4-4.5" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round"/></svg>' : ''}
          ${corroborated ? `<span class="text-green6 font-medium">+${s.sources.length - 1} more</span>` : ''}
          ${s.trend_boost > 0 ? '<span class="text-blue6 font-medium">↗ trending on X</span>' : ''}
        </p>
        <p class="text-[12.5px] text-sub mt-1.5"><span class="font-semibold text-ink/60">Why:</span> ${esc(whyLine(s))}</p>
      </div>
      <div class="w-full sm:w-auto flex sm:flex-col flex-row gap-2 items-stretch shrink-0 sm:justify-center">
        <button onclick="StoryDesk.pick(${s.id})" class="flex-1 sm:flex-none text-[12.5px] font-semibold px-4 py-2 rounded-xl ${s.picked ? 'bg-green1 text-green8 border border-green6/30' : 'bg-navy text-white hover:bg-navy2'} whitespace-nowrap transition-colors">${s.picked ? '✓ Picked' : 'Pick Story'}</button>
        <button onclick="StoryDesk.openPack(${s.id})" class="flex-1 sm:flex-none text-[12.5px] font-semibold px-4 py-2 rounded-xl border border-line bg-white hover:border-ink whitespace-nowrap">Story Pack</button>
        <div class="relative shrink-0">
          <button onclick="StoryDesk.menu(event, ${s.id})" class="text-sub hover:text-ink p-2 text-[17px] leading-none">⋮</button>
        </div>
      </div>
    </article>`;
  }

  const MIX_COLORS = ['#2563EB', '#079455', '#F79009', '#D92D20', '#7A5AF8', '#0E9384', '#DD2590', '#667085'];

  function renderMix(view) {
    const el = document.getElementById('board-mix');
    if (!el) return;
    const counts = {};
    view.forEach(s => { counts[s.category] = (counts[s.category] || 0) + 1; });
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const total = view.length;
    if (!total) { el.innerHTML = '<p class="text-[13px] text-sub">No active stories yet.</p>'; return; }

    const R = 34, C = 2 * Math.PI * R;
    let offset = 0;
    const segs = entries.map(([cat, n], i) => {
      const len = (n / total) * C - 1.5;
      const seg = `<circle r="${R}" cx="44" cy="44" fill="none" stroke="${MIX_COLORS[i % MIX_COLORS.length]}"
        stroke-width="12" stroke-dasharray="${Math.max(len, 0.5)} ${C - Math.max(len, 0.5)}" stroke-dashoffset="${-offset}"
        stroke-linecap="butt" transform="rotate(-90 44 44)"/>`;
      offset += (n / total) * C;
      return seg;
    }).join('');

    el.innerHTML = `
      <svg width="88" height="88" viewBox="0 0 88 88" class="shrink-0">${segs}
        <text x="44" y="42" text-anchor="middle" font-size="20" font-weight="700" fill="#101828">${total}</text>
        <text x="44" y="56" text-anchor="middle" font-size="8.5" fill="#667085">Total Stories</text>
      </svg>
      <div class="flex-1 space-y-1.5 min-w-0">
        ${entries.map(([cat, n], i) => `
          <div class="flex items-center gap-2 text-[13px]">
            <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${MIX_COLORS[i % MIX_COLORS.length]}"></span>
            <span class="truncate">${esc(cat)}</span>
            <span class="ml-auto font-semibold">${n}</span>
            <span class="text-sub text-[12px] w-11 text-right">(${Math.round(n / total * 100)}%)</span>
          </div>`).join('')}
      </div>`;
  }

  function renderInsight(active) {
    const el = document.getElementById('insight-text');
    if (!el) return;
    const breaking = active.filter(s => s.status === 'breaking').length;
    const review = active.filter(s => s.needs_review).length;
    const trending = active.filter(s => s.trend_boost > 0).length;
    let msg;
    if (breaking) msg = `${breaking} breaking ${breaking === 1 ? 'story' : 'stories'} on the board right now.`;
    else msg = 'High volume of developing stories right now.';
    if (review) msg += ` ${review} need${review === 1 ? 's' : ''} immediate editorial attention.`;
    else if (trending) msg += ` ${trending} ${trending === 1 ? 'is' : 'are'} trending on X.`;
    el.textContent = msg;
  }

  function renderFilters() {
    const active = stories.filter(s => !s.picked);
    const cats = [...new Set(active.map(s => s.category))].sort();
    const pickedCount = stories.filter(s => s.picked).length;
    const chips = ['All', 'Breaking', 'Developing', ...cats];
    if (pickedCount) chips.push(`Picked (${pickedCount})`);
    document.getElementById('filters').innerHTML = chips.map(c => {
      const key = c.startsWith('Picked') ? 'Picked' : c;
      const activeChip = key === filter;
      const color = CHIP_COLORS[key] || 'text-sub border-line';
      return `<button onclick="StoryDesk.setFilter('${esc(key)}')" class="px-3.5 py-1.5 rounded-lg text-[12.5px] font-semibold border bg-white ${activeChip ? 'bg-navy text-white border-navy' : color + ' hover:border-ink'}" ${activeChip ? 'style="background:#0B1526;color:#fff;border-color:#0B1526"' : ''}>${esc(c)}</button>`;
    }).join('');
  }

  function render(data) {
    if (data) stories = data;
    renderFilters();
    const active = stories.filter(s => !s.picked);
    let view;
    if (filter === 'Picked') view = stories.filter(s => s.picked);
    else {
      view = active;
      if (filter === 'Breaking') view = view.filter(s => s.status === 'breaking');
      else if (filter === 'Developing') view = view.filter(s => s.status === 'developing');
      else if (filter !== 'All') view = view.filter(s => s.category === filter);
    }
    if (sortBy === 'newest') view = [...view].sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
    renderMix(active);
    renderInsight(active);
    const countEl = document.getElementById('story-count');
    if (countEl) countEl.textContent = view.length;
    const el = document.getElementById('story-list');
    el.innerHTML = view.length
      ? view.map(s => card(s)).join('')
      : '<p class="text-sub text-[14px] py-8 text-center">No stories match this filter yet.</p>';
  }

  function setFilter(f) { filter = f; render(); }

  document.getElementById('sort-by').addEventListener('change', e => {
    sortBy = e.target.value;
    render();
  });

  async function pick(id) {
    const s = stories.find(x => x.id === id);
    if (s) { s.picked = s.picked ? 0 : 1; render(); }
    const r = await api(`/api/stories/${id}/pick`);
    if (r.refreshing) { setUpdated('story picked — refreshing…'); setLive('busy'); }
    if (r.picked === true) {
      Toast.show('Story moved to My Picks', { actionLabel: 'Undo', onAction: () => StoryDesk.pick(id) });
    } else if (r.picked === false) {
      Toast.show('Story returned to the board');
    }
  }

  function pickTop() {
    const top = stories.filter(s => !s.picked)[0];
    if (top) pick(top.id);
  }

  function openTopPack() {
    const top = stories.filter(s => !s.picked)[0];
    if (top) openPack(top.id);
  }

  function menu(evt, id) {
    evt.stopPropagation();
    const s = stories.find(x => x.id === id);
    if (!s) return;
    document.querySelectorAll('.story-menu').forEach(m => m.remove());
    const wrap = evt.target.closest('.relative');
    wrap.insertAdjacentHTML('beforeend', `
      <div class="story-menu absolute right-0 top-7 z-20 bg-white border border-line rounded-xl shadow-lg py-1 w-44 text-[13px]">
        ${s.url ? `<a href="${esc(s.url)}" target="_blank" class="block px-3.5 py-2 hover:bg-paper">Open article ↗</a>` : ''}
        <button onclick="StoryDesk.openPack(${id})" class="block w-full text-left px-3.5 py-2 hover:bg-paper">Story pack</button>
        <button onclick="StoryDesk.pick(${id})" class="block w-full text-left px-3.5 py-2 hover:bg-paper">${s.picked ? 'Un-pick story' : 'Pick story'}</button>
      </div>`);
    setTimeout(() => document.addEventListener('click', () => {
      document.querySelectorAll('.story-menu').forEach(m => m.remove());
    }, { once: true }), 0);
  }

  async function refresh() {
    document.getElementById('refresh-icon').textContent = '…';
    setUpdated('refreshing…');
    setLive('busy');
    try { await api('/api/ingest'); } finally {
      setTimeout(() => { document.getElementById('refresh-icon').textContent = '⟳'; }, 800);
    }
  }
  document.getElementById('refresh-stories').addEventListener('click', refresh);

  async function openPack(id) {
    const p = await (await fetch(`/api/stories/${id}/pack`)).json();
    const st = STATUS[p.status] || STATUS.developing;
    const html = `
      <div class="flex items-start gap-3 mb-4">
        <div>
          <div class="flex gap-2 mb-1.5 text-[11.5px]">
            <span class="px-2 py-0.5 rounded-md font-semibold ${st.bg} ${st.text}">${st.label}</span>
            <span class="text-sub">${ageLabel(p.published_at)} · score ${p.score}/100 · confidence ${p.confidence}%</span>
          </div>
          <h2 class="text-[18px] font-bold leading-snug">${esc(p.title)}</h2>
        </div>
        <button onclick="StoryDesk.closePack()" class="ml-auto text-sub hover:text-ink text-[20px] leading-none px-1">✕</button>
      </div>

      <div class="mb-4 border border-line rounded-xl p-3">
        <p class="text-[11.5px] font-semibold uppercase tracking-widest text-sub mb-2">Write with AI</p>
        <div id="ai-write-buttons" class="flex gap-2 flex-wrap">
          <button onclick="StoryDesk.writeArticle(${id}, 'web')" class="ai-write-btn flex-1 min-w-[30%] rounded-xl border border-line font-semibold text-[12.5px] px-3 py-2 hover:border-ink">Web article</button>
          <button onclick="StoryDesk.writeArticle(${id}, 'broadcast')" class="ai-write-btn flex-1 min-w-[30%] rounded-xl border border-line font-semibold text-[12.5px] px-3 py-2 hover:border-ink">Broadcast script</button>
          <button onclick="StoryDesk.writeArticle(${id}, 'social')" class="ai-write-btn flex-1 min-w-[30%] rounded-xl border border-line font-semibold text-[12.5px] px-3 py-2 hover:border-ink">Social copy</button>
        </div>
        <p id="ai-write-status" class="text-[12px] text-sub mt-2"></p>
        <div id="ai-write-output" class="mt-3 space-y-3"></div>
      </div>

      <div class="mb-4">
        <p class="text-[11.5px] font-semibold uppercase tracking-widest text-sub mb-1.5">Sources (${(p.sources || []).length})</p>
        <p class="text-[13.5px]">${(p.sources || []).map(esc).join(' · ') || esc(p.publisher || '')}</p>
        ${p.url ? `<a href="${esc(p.url)}" target="_blank" class="text-[13px] text-blue6 hover:underline">Open lead article ↗</a>` : ''}
      </div>

      <div class="mb-4">
        <p class="text-[11.5px] font-semibold uppercase tracking-widest text-sub mb-2">Score anatomy — ${p.score}/100</p>
        <div class="space-y-1.5">${(p.breakdown || []).map(b => {
          const pct = b.max_points ? Math.round(b.points / b.max_points * 100) : 0;
          return `
          <div class="flex items-center gap-2 text-[12.5px]">
            <span class="w-20 shrink-0 capitalize ${b.points > 0 ? 'font-medium' : 'text-sub'}">${esc(b.variable)}</span>
            <div class="flex-1 h-2.5 rounded-full bg-paper overflow-hidden">
              <div class="h-full rounded-full" style="width:${pct}%;background:${b.points > 0 ? '#2563EB' : '#E4E7EC'}"></div>
            </div>
            <span class="w-11 shrink-0 text-right font-mono ${b.points > 0 ? 'text-blue6' : 'text-sub'}">${b.points}/${b.max_points}</span>
          </div>`;
        }).join('')}</div>
      </div>

      <div class="mb-4">
        <p class="text-[11.5px] font-semibold uppercase tracking-widest text-sub mb-1.5">Why it ranks</p>
        <ul class="text-[13.5px] space-y-1">${(p.evidence_lines || []).map(e => `<li class="flex gap-2"><span class="text-sub">•</span><span>${esc(e)}</span></li>`).join('')}</ul>
      </div>

      ${(p.related_tweets || []).length ? `
      <div class="mb-4">
        <p class="text-[11.5px] font-semibold uppercase tracking-widest text-sub mb-1.5">From monitored handles</p>
        <div class="space-y-2">${p.related_tweets.map(t => `
          <div class="border border-line rounded-xl p-2.5">
            <p class="text-[12.5px]"><span class="font-semibold">${esc(t.display_name || t.handle)}</span> <span class="text-sub">${esc(t.handle)} · ${postedLabel(t.created_at)}</span></p>
            <p class="text-[13px] mt-0.5">${esc(t.text)}</p>
          </div>`).join('')}
        </div>
      </div>` : ''}

      <div>
        <p class="text-[11.5px] font-semibold uppercase tracking-widest text-sub mb-1.5">Suggested formats</p>
        <div class="space-y-2">${(p.format_suggestions || []).map(f => `
          <div class="border border-line rounded-xl p-2.5">
            <p class="text-[13px]"><span class="font-bold">${esc(f.platform)}:</span> ${esc(f.format)}</p>
            <p class="text-[12px] text-sub mt-0.5">Because: ${esc(f.because)}</p>
          </div>`).join('')}
        </div>
      </div>`;
    document.getElementById('pack-content').innerHTML = html;
    document.getElementById('pack-overlay').classList.remove('hidden');
    loadWriterState(id);
  }

  // ── AI writer ────────────────────────────────────────────────────────────

  function draftCard(a, isPrevious) {
    const meta = `${esc(a.format)} · ${esc(a.model || '')} · ${ageLabel(a.created_at)}`;
    const isMock = a.model === 'mock';
    const badge = isMock
      ? '<span class="bg-blue1 text-blue8 font-semibold text-[11px] px-2 py-0.5 rounded-md">MOCK DRAFT — template output</span>'
      : '<span class="bg-amber1 text-amber8 font-semibold text-[11px] px-2 py-0.5 rounded-md">AI DRAFT — review before use</span>';
    return `
      <div class="border border-line rounded-xl p-3">
        <div class="flex items-center gap-2 mb-1.5">
          ${badge}
          <button onclick="StoryDesk.copyDraft(event)" class="ml-auto text-[12px] text-blue6 hover:underline">Copy</button>
        </div>
        <p class="text-[11.5px] text-sub mb-2">${meta}</p>
        <pre class="draft-body text-[13.5px] leading-relaxed" style="white-space:pre-wrap;font-family:inherit">${esc(a.content)}</pre>
      </div>`;
  }

  async function loadWriterState(id) {
    const status = document.getElementById('ai-write-status');
    const out = document.getElementById('ai-write-output');
    let settings = {};
    try { settings = await (await fetch('/api/settings')).json(); } catch {}
    if (!settings.key_configured && status) {
      status.innerHTML = 'No API key configured — drafts will be instant template mocks. Add a key in Ops → AI writer settings for channel-voice AI drafts.';
    }
    let drafts = [];
    try { drafts = await (await fetch(`/api/stories/${id}/articles`)).json(); } catch {}
    if (out && Array.isArray(drafts) && drafts.length) {
      out.innerHTML = `<p class="text-[11.5px] text-sub">Previous drafts</p>` +
        drafts.map(a => draftCard(a, true)).join('');
    }
  }

  async function writeArticle(id, fmt) {
    const buttons = document.querySelectorAll('#ai-write-buttons .ai-write-btn');
    const status = document.getElementById('ai-write-status');
    const out = document.getElementById('ai-write-output');
    buttons.forEach(b => { b.disabled = true; b.classList.add('opacity-50', 'cursor-not-allowed'); });
    if (status) { status.classList.remove('text-red6'); status.classList.add('text-sub'); status.textContent = 'Preparing draft…'; }
    let r;
    try {
      const res = await fetch(`/api/stories/${id}/write?format=${fmt}`, { method: 'POST' });
      r = await res.json();
    } catch {
      r = { ok: false, error: 'Network error reaching the writer.' };
    }
    buttons.forEach(b => { b.disabled = false; b.classList.remove('opacity-50', 'cursor-not-allowed'); });
    if (r && r.ok && r.article) {
      if (status) status.textContent = '';
      out.insertAdjacentHTML('afterbegin', draftCard(r.article, false));
    } else {
      const msg = (r && r.error) || 'The writer could not produce a draft.';
      if (status) { status.classList.remove('text-sub'); status.classList.add('text-red6'); status.textContent = ''; }
      if (status) status.innerHTML = `<span class="text-red6 text-[12.5px]">${esc(msg)}</span>`;
    }
  }

  function copyDraft(evt) {
    const btn = evt.currentTarget;
    const card = btn.closest('.border');
    const pre = card ? card.querySelector('.draft-body') : null;
    if (pre) navigator.clipboard.writeText(pre.textContent);
    const prev = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = prev; }, 1500);
  }

  function closePack() {
    document.getElementById('pack-overlay').classList.add('hidden');
  }

  return { render, setFilter, pick, pickTop, openPack, openTopPack, closePack, refresh, menu,
           writeArticle, copyDraft,
           get stories() { return stories; } };
})();
