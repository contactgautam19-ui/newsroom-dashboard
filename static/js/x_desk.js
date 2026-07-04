// X desk: one clean chronological feed of monitored tweets, filterable by
// source group and news-signal, plus the Top 5 signals block and manual refresh.

const XDesk = (() => {
  const MAX_TWEETS = 150;
  let allTweets = []; // newest first
  let groupFilter = 'all'; // 'all' | 'A' | 'B' | 'C'
  let signalsOnly = false;

  const GROUPS = {
    A: { label: 'Government & wires', color: '#079455' },
    B: { label: 'Rivals', color: '#DC6803' },
    C: { label: 'Field reporters', color: '#2563EB' },
  };

  function trustColor(score) {
    return score >= 90 ? 'text-green6' : score >= 70 ? 'text-blue6' : 'text-amber6';
  }

  const AVATAR_COLORS = ['#2563EB', '#079455', '#DC6803', '#7A5AF8', '#DD2590'];

  function avatarHtml(t, i) {
    const letter = (t.display_name || t.handle || '?').replace('@', '').charAt(0).toUpperCase();
    const fallback = `<div class="w-9 h-9 rounded-full shrink-0 items-center justify-center text-white text-[13px] font-bold"
      style="background:${AVATAR_COLORS[i % AVATAR_COLORS.length]};display:${t.avatar_url ? 'none' : 'flex'}">${letter}</div>`;
    const img = t.avatar_url
      ? `<img src="${esc(t.avatar_url)}" alt="" class="w-9 h-9 rounded-full object-cover shrink-0 border border-line"
           onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
      : '';
    return `<div class="shrink-0 flex">${img}${fallback}</div>`;
  }

  function tweetCard(t, i) {
    const grp = GROUPS[t.stream_column] || { label: t.stream_column || '', color: '#667085' };
    return `
      <div class="fade-up bg-white border border-line rounded-2xl px-4 py-3 flex items-start gap-3" style="border-left:3px solid ${grp.color}">
        ${avatarHtml(t, i)}
        <div class="min-w-0 flex-1">
          <p class="text-[13px] leading-tight flex items-center gap-1.5 flex-wrap">
            <span class="font-bold">${esc(t.display_name || t.handle)}</span>
            <span class="text-sub">${esc(t.handle)}</span>
            <span class="font-mono text-[11px] ${trustColor(t.trust_score)}">T${t.trust_score}</span>
            <span class="text-sub text-[12px]">${postedLabel(t.created_at)}</span>
          </p>
          <p class="text-[13.5px] leading-snug mt-1">${esc(t.text)}</p>
          <div class="flex items-center gap-1.5 flex-wrap mt-1.5">
            ${t.news_signal ? `<span class="text-[10.5px] uppercase tracking-wide text-green6 font-medium">◉ ${esc(t.news_signal)}</span>` : ''}
            <span class="text-[10.5px] font-semibold px-1.5 py-0.5 rounded" style="background:${grp.color}1A;color:${grp.color}">${esc(grp.label)}</span>
          </div>
        </div>
      </div>`;
  }

  function matchesFilter(t) {
    if (groupFilter !== 'all' && t.stream_column !== groupFilter) return false;
    if (signalsOnly && !t.news_signal) return false;
    return true;
  }

  function renderFilters() {
    const el = document.getElementById('x-filters');
    if (!el) return;
    const chips = [
      { key: 'all', label: 'All' },
      { key: 'A', label: GROUPS.A.label },
      { key: 'B', label: GROUPS.B.label },
      { key: 'C', label: GROUPS.C.label },
    ];
    const groupChips = chips.map(c => {
      const active = c.key === groupFilter;
      return `<button onclick="XDesk.setGroupFilter('${c.key}')" class="px-3.5 py-1.5 rounded-lg text-[12.5px] font-semibold border bg-white text-sub hover:border-ink"
        ${active ? 'style="background:#0B1526;color:#fff;border-color:#0B1526"' : ''}>${esc(c.label)}</button>`;
    }).join('');
    const signalChip = `<button onclick="XDesk.toggleSignalsOnly()" class="px-3.5 py-1.5 rounded-lg text-[12.5px] font-semibold border bg-white text-sub hover:border-ink"
      ${signalsOnly ? 'style="background:#0B1526;color:#fff;border-color:#0B1526"' : ''}>News signals only</button>`;
    el.innerHTML = groupChips + signalChip;
  }

  function renderFeed() {
    const el = document.getElementById('x-feed');
    if (!el) return;
    const view = allTweets.filter(matchesFilter);
    el.innerHTML = view.length
      ? view.map((t, i) => tweetCard(t, i)).join('')
      : '<p class="text-sub text-[14px] py-6 text-center">No posts match this filter yet.</p>';
  }

  function render() {
    renderFilters();
    renderFeed();
  }

  function setGroupFilter(f) { groupFilter = f; render(); }

  function toggleSignalsOnly() { signalsOnly = !signalsOnly; render(); }

  function add(t) {
    allTweets.unshift(t);
    if (allTweets.length > MAX_TWEETS) allTweets.length = MAX_TWEETS;
    render();
  }

  function budget(r) {
    const el = document.getElementById('x-budget');
    if (el && r && r.monthly_remaining != null) {
      el.textContent = `${r.monthly_remaining} API calls left this month`;
    }
  }

  function note(text) {
    const el = document.getElementById('x-note');
    if (el) el.textContent = text || '';
  }

  async function refresh() {
    const btn = document.getElementById('x-refresh-btn');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '𝕏 fetching…';
    try {
      const r = await api('/api/x/refresh');
      budget(r);
      btn.textContent = r.ok ? `𝕏 +${r.tweets_new} tweets` : '𝕏 failed';
      note(r.ok ? 'stories re-ranking with fresh tweets…' : (r.error || ''));
      if (r.ok) loadSignals();
    } catch {
      btn.textContent = '𝕏 failed';
    } finally {
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500);
    }
  }
  document.getElementById('x-refresh-btn').addEventListener('click', refresh);

  const SIGNAL_AVATAR_COLORS = ['#2563EB', '#079455', '#DC6803', '#7A5AF8', '#DD2590'];

  function signalAvatarHtml(s, i) {
    const letter = (s.display_name || s.handle || '?').replace('@', '').charAt(0).toUpperCase();
    const fallback = `<div class="w-10 h-10 rounded-full shrink-0 items-center justify-center text-white text-[15px] font-bold"
      style="background:${SIGNAL_AVATAR_COLORS[i % SIGNAL_AVATAR_COLORS.length]};display:${s.avatar_url ? 'none' : 'flex'}">${letter}</div>`;
    const img = s.avatar_url
      ? `<img src="${esc(s.avatar_url)}" alt="" class="w-10 h-10 rounded-full object-cover shrink-0 border border-line"
           onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
      : '';
    return `<div class="shrink-0 flex">${img}${fallback}</div>`;
  }

  // Sharp signal card: who posted, what the post says, why it ranks.
  function signalCard(s, rank) {
    const chips = (s.reasons || []).map(r => {
      if (r === 'matches a board story' && s.linked_story) {
        return `<button onclick="Nav.go('stories');setTimeout(()=>{const c=document.querySelector('article[data-id=\\'${s.linked_story.story_id}\\']');if(c){c.scrollIntoView({behavior:'smooth',block:'center'});c.style.outline='2px solid #2563EB';setTimeout(()=>c.style.outline='',2500);}},150)"
          class="px-2 py-1 rounded-lg bg-blue1 text-blue8 text-[11.5px] font-medium hover:bg-blue6 hover:text-white transition-colors" title="${esc(s.linked_story.story_title)}">↗ matches a board story</button>`;
      }
      return `<span class="px-2 py-1 rounded-lg bg-paper text-sub text-[11.5px] font-medium">${esc(r)}</span>`;
    }).join('');
    return `
      <div class="fade-up bg-white border border-line rounded-2xl px-4 py-3 flex items-start gap-3.5" style="border-left:4px solid #2563EB">
        <span class="text-[13px] font-bold text-sub w-3 shrink-0 pt-2.5">${rank}</span>
        ${signalAvatarHtml(s, rank - 1)}
        <div class="min-w-0 flex-1">
          <p class="text-[13.5px] leading-tight">
            <span class="font-bold">${esc(s.display_name || s.handle)}</span>
            <span class="text-sub text-[12px] font-normal">${esc(s.handle)}</span>
          </p>
          <p class="text-[13px] leading-snug mt-1" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(s.summary || '')}</p>
          <div class="flex items-center gap-1.5 flex-wrap mt-2">${chips}</div>
        </div>
      </div>`;
  }

  async function loadSignals() {
    const el = document.getElementById('top-signals');
    if (!el) return;
    try {
      const signals = await (await fetch('/api/x/top-signals')).json();
      el.innerHTML = signals.length ? `
        <p class="text-[11px] font-semibold tracking-widest text-blue8 uppercase mb-2">Top 5 signals — needs editorial attention</p>
        <div class="space-y-2">${signals.map((s, i) => signalCard(s, i + 1)).join('')}</div>` : '';
    } catch { /* non-fatal */ }
  }

  async function backfill() {
    loadSignals();
    try {
      const tweets = await (await fetch('/api/tweets')).json();
      allTweets = tweets.slice(0, MAX_TWEETS); // already newest-first
      render();
    } catch { /* stream fills it */ }
    try {
      const s = await (await fetch('/api/x/status')).json();
      budget(s);
      if (s.manual_only && !s.key_configured) note('API key missing — set TWT_API_KEY in .env');
    } catch { /* non-fatal */ }
  }
  backfill();

  return { add, budget, note, refresh, loadSignals, setGroupFilter, toggleSignalsOnly };
})();
