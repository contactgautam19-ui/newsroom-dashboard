// Story desk: ranked cards with a plain-English "why", filters, pick + pack.

const StoryDesk = (() => {
  let stories = [];
  let filter = 'All';

  const STATUS = {
    breaking:   { label: 'Breaking',   bar: '#D92D20', bg: 'bg-red1',   text: 'text-red8',   circleBg: '#FEF3F2', circleText: '#912018' },
    developing: { label: 'Developing', bar: '#DC6803', bg: 'bg-amber1', text: 'text-amber8', circleBg: '#FFFAEB', circleText: '#7A3707' },
    verified:   { label: 'Verified',   bar: '#079455', bg: 'bg-green1', text: 'text-green8', circleBg: '#ECFDF3', circleText: '#085D3A' },
  };

  const WHY = {
    breaking:  b => b.evidence[0]?.includes('published') ? 'very fresh development' : 'breaking development',
    political: () => 'power centre involved',
    emotion:   b => 'strong emotional pull',
    celebrity: () => 'high-profile names',
    economy:   () => 'money impact for viewers',
    safety:    () => 'public-safety relevance',
    visual:    () => 'strong visuals available',
    novelty:   () => 'unusual, unexpected angle',
    trend:     () => 'trending on X right now',
  };

  function whyLine(s) {
    const parts = (s.breakdown || [])
      .filter(b => b.points > 0)
      .sort((a, b) => b.points - a.points)
      .slice(0, 3)
      .map(b => (WHY[b.variable] || (() => b.variable))(b));
    const srcs = (s.sources || []).length;
    if (srcs > 1) parts.push(`corroborated by ${srcs} outlets`);
    else if (s.needs_review) parts.push('single source — verify before air');
    return parts.join(' · ');
  }

  function card(s, rank) {
    const st = STATUS[s.status] || STATUS.developing;
    const mins = Math.round((Date.now() - new Date(s.published_at).getTime()) / 60000);
    const ageColor = mins <= 60 ? 'text-green6 font-medium' : mins <= 180 ? 'text-amber6' : 'text-sub';
    return `
    <article class="fade-up bg-white border border-line rounded-xl p-4 flex gap-3.5" style="border-left:3px solid ${st.bar}" data-id="${s.id}">
      <div class="text-center shrink-0 hidden sm:block">
        <div class="w-11 h-11 rounded-full flex items-center justify-center text-[16px] font-semibold" style="background:${st.circleBg};color:${st.circleText}">${s.score}</div>
        <div class="text-[10px] text-sub mt-1">score</div>
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 flex-wrap mb-1 text-[11.5px]">
          <span class="sm:hidden font-semibold" style="color:${st.circleText}">${s.score} pts</span>
          <span class="px-2 py-0.5 rounded-full font-medium ${st.bg} ${st.text}">${st.label}</span>
          ${s.trend_boost > 0 ? '<span class="px-2 py-0.5 rounded-full bg-blue1 text-blue8 font-medium">↗ trending on X</span>' : ''}
          ${s.high_demand ? '<span class="px-2 py-0.5 rounded-full bg-red1 text-red8 font-medium">High demand</span>' : ''}
          ${s.needs_review ? '<span class="px-2 py-0.5 rounded-full bg-amber1 text-amber8 font-medium">Needs review</span>' : ''}
          <span class="${ageColor}">${ageLabel(s.published_at)}</span>
        </div>
        <h2 class="text-[16px] font-semibold leading-snug mb-1">
          ${s.url ? `<a href="${esc(s.url)}" target="_blank" class="hover:underline">${esc(s.title)}</a>` : esc(s.title)}
        </h2>
        <p class="text-[13px] text-sub">
          ${esc(s.publisher || '')}${(s.sources || []).length > 1 ? ` <span class="text-green6">+ ${s.sources.length - 1} more</span>` : ''}
          · ${esc(s.category)}
        </p>
        <p class="text-[13px] text-sub mt-1.5"><span class="text-ink/40">Why:</span> ${esc(whyLine(s))}</p>
      </div>
      <div class="flex flex-col gap-2 shrink-0 justify-center">
        <button onclick="StoryDesk.pick(${s.id})" class="text-[12.5px] font-medium px-3 py-1.5 rounded-full ${s.picked ? 'bg-green1 text-green8 border border-green6/30' : 'bg-ink text-white hover:bg-ink/85'} whitespace-nowrap transition-colors">${s.picked ? '✓ Picked' : 'Pick story'}</button>
        <button onclick="StoryDesk.openPack(${s.id})" class="text-[12.5px] px-3 py-1.5 rounded-full border border-line bg-white hover:border-ink whitespace-nowrap">Story pack</button>
      </div>
    </article>`;
  }

  function renderFilters() {
    const cats = [...new Set(stories.map(s => s.category))].sort();
    const chips = ['All', 'Breaking', ...cats];
    document.getElementById('filters').innerHTML = chips.map(c =>
      `<button onclick="StoryDesk.setFilter('${esc(c)}')" class="px-3 py-1 rounded-full text-[12px] border ${c === filter ? 'chip-active' : 'border-line bg-white text-sub hover:border-ink'}">${esc(c)}</button>`
    ).join('');
  }

  function render(data) {
    if (data) stories = data;
    renderFilters();
    let view = stories;
    if (filter === 'Breaking') view = stories.filter(s => s.status === 'breaking');
    else if (filter !== 'All') view = stories.filter(s => s.category === filter);
    const el = document.getElementById('story-list');
    el.innerHTML = view.length
      ? view.map((s, i) => card(s, i + 1)).join('')
      : '<p class="text-sub text-[14px] py-8 text-center">No stories match this filter yet.</p>';
  }

  function setFilter(f) { filter = f; render(); }

  async function pick(id) {
    await api(`/api/stories/${id}/pick`);
  }

  async function refresh() {
    const icon = document.getElementById('refresh-icon');
    icon.textContent = '…';
    try { await api('/api/ingest'); } finally {
      setTimeout(() => { icon.textContent = '⟳'; }, 800);
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
            <span class="px-2 py-0.5 rounded-full font-medium ${st.bg} ${st.text}">${st.label}</span>
            <span class="text-sub">${ageLabel(p.published_at)} · score ${p.score}/100 · confidence ${p.confidence}%</span>
          </div>
          <h2 class="text-[18px] font-semibold leading-snug">${esc(p.title)}</h2>
        </div>
        <button onclick="StoryDesk.closePack()" class="ml-auto text-sub hover:text-ink text-[20px] leading-none px-1">✕</button>
      </div>

      <div class="mb-4">
        <p class="text-[12px] font-semibold uppercase tracking-wide text-sub mb-1.5">Sources (${(p.sources || []).length})</p>
        <p class="text-[13.5px]">${(p.sources || []).map(esc).join(' · ') || esc(p.publisher || '')}</p>
        ${p.url ? `<a href="${esc(p.url)}" target="_blank" class="text-[13px] text-blue6 hover:underline">Open lead article ↗</a>` : ''}
      </div>

      <div class="mb-4">
        <p class="text-[12px] font-semibold uppercase tracking-wide text-sub mb-1.5">Why it ranks</p>
        <ul class="text-[13.5px] space-y-1">${(p.evidence_lines || []).map(e => `<li class="flex gap-2"><span class="text-sub">•</span><span>${esc(e)}</span></li>`).join('')}</ul>
      </div>

      ${(p.related_tweets || []).length ? `
      <div class="mb-4">
        <p class="text-[12px] font-semibold uppercase tracking-wide text-sub mb-1.5">From monitored handles</p>
        <div class="space-y-2">${p.related_tweets.map(t => `
          <div class="border border-line rounded-lg p-2.5">
            <p class="text-[12.5px]"><span class="font-medium">${esc(t.display_name || t.handle)}</span> <span class="text-sub">${esc(t.handle)} · ${postedLabel(t.created_at)}</span></p>
            <p class="text-[13px] mt-0.5">${esc(t.text)}</p>
          </div>`).join('')}
        </div>
      </div>` : ''}

      <div>
        <p class="text-[12px] font-semibold uppercase tracking-wide text-sub mb-1.5">Suggested formats</p>
        <div class="space-y-2">${(p.format_suggestions || []).map(f => `
          <div class="border border-line rounded-lg p-2.5">
            <p class="text-[13px]"><span class="font-semibold">${esc(f.platform)}:</span> ${esc(f.format)}</p>
            <p class="text-[12px] text-sub mt-0.5">Because: ${esc(f.because)}</p>
          </div>`).join('')}
        </div>
      </div>`;
    document.getElementById('pack-content').innerHTML = html;
    document.getElementById('pack-overlay').classList.remove('hidden');
  }

  function closePack() {
    document.getElementById('pack-overlay').classList.add('hidden');
  }

  return { render, setFilter, pick, openPack, closePack, refresh };
})();
