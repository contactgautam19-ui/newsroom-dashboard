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
    } catch {
      btn.textContent = '𝕏 failed';
    } finally {
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500);
    }
  }
  document.getElementById('x-refresh-btn').addEventListener('click', refresh);

  async function backfill() {
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

  return { add, budget, note, refresh };
})();
