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

  function signalCard(s, rank) {
    const jump = s.linked_story
      ? `<button onclick="Tabs.show('stories');setTimeout(()=>{const c=document.querySelector('article[data-id=\\'${s.linked_story.story_id}\\']');if(c){c.scrollIntoView({behavior:'smooth',block:'center'});c.style.outline='2px solid #1570EF';setTimeout(()=>c.style.outline='',2500);}},150)" class="text-[11.5px] text-blue6 hover:underline text-left">↗ ${esc(s.linked_story.story_title.slice(0, 60))}…</button>`
      : '';
    return `
      <div class="fade-up bg-white border border-line rounded-xl p-3 flex gap-3" style="border-left:3px solid #1570EF">
        <div class="text-center shrink-0">
          <div class="w-8 h-8 rounded-full bg-blue1 text-blue8 flex items-center justify-center text-[13px] font-semibold">${rank}</div>
          <div class="text-[10px] text-sub mt-0.5 font-mono">${s.score}</div>
        </div>
        <div class="min-w-0 flex-1">
          <p class="text-[12.5px]">
            <span class="font-semibold">${esc(s.display_name || s.handle)}</span>
            <span class="text-sub">${esc(s.handle)} · ${postedLabel(s.created_at)} · <span class="font-mono ${trustColor(s.trust_score)}">T${s.trust_score}</span></span>
          </p>
          <p class="text-[13px] leading-snug mt-0.5">${esc(s.text)}</p>
          <div class="flex items-center gap-1.5 flex-wrap mt-1.5">
            ${(s.reasons || []).map(r => `<span class="px-1.5 py-0.5 rounded bg-paper text-sub text-[10.5px]">${esc(r)}</span>`).join('')}
          </div>
          ${jump}
        </div>
      </div>`;
  }

  async function loadSignals() {
    const el = document.getElementById('top-signals');
    if (!el) return;
    try {
      const signals = await (await fetch('/api/x/top-signals')).json();
      el.innerHTML = signals.length ? `
        <p class="text-[11px] font-semibold tracking-wide text-blue8 uppercase mb-2">Top 5 signals — needs editorial attention</p>
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
