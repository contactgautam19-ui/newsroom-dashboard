// X desk: three columns of real tweets, manual refresh, budget badge.

const XDesk = (() => {
  const MAX_PER_COL = 50;

  function trustColor(score) {
    return score >= 90 ? 'text-green6' : score >= 70 ? 'text-blue6' : 'text-amber6';
  }

  function cardHtml(t) {
    return `
      <div class="fade-up bg-white border border-line rounded-lg p-2.5">
        <p class="text-[12.5px] leading-tight">
          <span class="font-semibold">${esc(t.display_name || t.handle)}</span>
          <span class="text-sub">${esc(t.handle)}</span>
          <span class="font-mono text-[11px] ${trustColor(t.trust_score)}">T${t.trust_score}</span>
        </p>
        <p class="text-[11.5px] text-sub mb-1">${postedLabel(t.created_at)}</p>
        <p class="text-[13px] leading-snug">${esc(t.text)}</p>
        ${t.news_signal ? `<p class="mt-1 text-[10.5px] uppercase tracking-wide text-green6 font-medium">◉ ${esc(t.news_signal)}</p>` : ''}
      </div>`;
  }

  function add(t) {
    const col = document.getElementById(`col-${t.stream_column}`);
    if (!col) return;
    col.insertAdjacentHTML('afterbegin', cardHtml(t));
    while (col.children.length > MAX_PER_COL) col.removeChild(col.lastChild);
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

  const AVATAR_COLORS = ['#2563EB', '#079455', '#DC6803', '#7A5AF8', '#DD2590'];

  function avatarHtml(s, i) {
    const letter = (s.display_name || s.handle || '?').replace('@', '').charAt(0).toUpperCase();
    const fallback = `<div class="w-10 h-10 rounded-full shrink-0 items-center justify-center text-white text-[15px] font-bold"
      style="background:${AVATAR_COLORS[i % AVATAR_COLORS.length]};display:${s.avatar_url ? 'none' : 'flex'}">${letter}</div>`;
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
        ${avatarHtml(s, rank - 1)}
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
      tweets.reverse().forEach(add);
    } catch { /* stream fills it */ }
    try {
      const s = await (await fetch('/api/x/status')).json();
      budget(s);
      if (s.manual_only && !s.key_configured) note('API key missing — set TWT_API_KEY in .env');
    } catch { /* non-fatal */ }
  }
  backfill();

  return { add, budget, note, refresh, loadSignals };
})();
