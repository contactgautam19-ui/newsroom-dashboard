// Velocity-event ticker strip + header clock/status.

const Ticker = (() => {
  const MAX_ITEMS = 8;

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML;
  }

  function add(ev) {
    const el = document.getElementById('ticker');
    if (!el) return;
    if (el.firstElementChild && el.firstElementChild.classList.contains('text-faint')) {
      el.innerHTML = '';
    }
    const item = `
      <span class="slide-in inline-flex items-center gap-1.5 shrink-0">
        <span class="flame">${ev.high_demand ? '🔥' : '🚀'}</span>
        <span class="font-mono text-white">${esc(ev.hashtag)}</span>
        <span class="font-mono text-verified">+${Math.round(ev.velocity_pct)}%</span>
        <span class="text-faint">${Number(ev.posts_per_hour).toLocaleString()}/hr</span>
        <span class="text-accent">+${ev.boost}pts</span>
        ${ev.high_demand ? '<span class="font-bold text-breaking">HIGH-DEMAND</span>' : ''}
      </span>`;
    el.insertAdjacentHTML('afterbegin', item);
    while (el.children.length > MAX_ITEMS) el.removeChild(el.lastChild);
  }

  function clock() {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleTimeString('en-IN', { hour12: false });
  }
  setInterval(clock, 1000);
  clock();

  function status(data) {
    if (data.x_layer) {
      const el = document.getElementById('x-layer');
      if (el) el.textContent = `layer: ${data.x_layer}`;
    }
    const sys = document.getElementById('sys-status');
    if (sys && data.state) {
      sys.textContent = data.state === 'ingesting' ? '⟳ ingesting Google News…'
        : data.state === 'ingest_error' ? `ingest error: ${data.error || ''}`
        : 'live';
    }
    if (data.last_ingest) {
      const el = document.getElementById('ingest-info');
      if (el) {
        const t = new Date(data.last_ingest).toLocaleTimeString('en-IN', { hour12: false });
        el.textContent = `last ingest ${t} · ${data.new_stories ?? 0} new · top score ${data.max_score ?? '—'}`;
      }
    }
  }

  return { add, status };
})();

async function api(path) {
  const res = await fetch(path, { method: 'POST' });
  return res.json();
}
