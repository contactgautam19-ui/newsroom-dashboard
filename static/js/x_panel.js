// Section 2: three independently scrolling handle-group columns.

const XPanel = (() => {
  const MAX_PER_COL = 60;
  let kept = 0;

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML;
  }

  function trustBadge(score) {
    const color = score >= 90 ? 'text-verified' : score >= 70 ? 'text-accent' : 'text-develop';
    return `<span class="font-mono ${color}">T${score}</span>`;
  }

  // Shows when the X post was actually made (not when we fetched it):
  // time-of-day for today's posts, date + time for older ones.
  function timeStr(iso) {
    try {
      const d = new Date(iso);
      const time = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
      const today = new Date().toDateString() === d.toDateString();
      return today ? `posted ${time}`
        : `posted ${d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })} ${time}`;
    } catch { return ''; }
  }

  function cardHtml(t) {
    return `
      <div class="slide-in rounded bg-panel border border-edge px-2 py-1.5 mx-1">
        <div class="flex items-center gap-2 text-[11px]">
          <span class="font-bold text-white truncate">${esc(t.display_name || t.handle)}</span>
          <span class="text-faint truncate">${esc(t.handle)}</span>
          ${trustBadge(t.trust_score)}
          <span class="ml-auto font-mono text-faint">${timeStr(t.created_at)}</span>
        </div>
        <div class="text-gray-300 mt-0.5 leading-snug">${esc(t.text)}</div>
        ${t.news_signal ? `<div class="mt-1 text-[10px] uppercase tracking-wider text-verified">◉ ${esc(t.news_signal)}</div>` : ''}
      </div>`;
  }

  function add(tweet) {
    const col = document.getElementById(`col-${tweet.stream_column}`);
    if (!col) return;
    col.insertAdjacentHTML('afterbegin', cardHtml(tweet));
    while (col.children.length > MAX_PER_COL) col.removeChild(col.lastChild);
    kept += 1;
    const stats = document.getElementById('x-stats');
    if (stats) stats.textContent = `${kept} tweets surfaced this session (guardrails active)`;
  }

  async function backfill() {
    try {
      const res = await fetch('/api/tweets');
      const tweets = await res.json();
      tweets.reverse().forEach(add);
      kept = tweets.length;
    } catch { /* stream will fill it */ }
  }

  return { add, backfill };
})();
