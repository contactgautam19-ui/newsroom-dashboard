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
      if (el) el.textContent = data.x_error
        ? `layer: ${data.x_layer} — ${data.x_error}`.slice(0, 90)
        : `layer: ${data.x_layer}`;
      const spike = document.getElementById('spike-btn');
      if (spike) spike.classList.toggle('hidden', data.x_layer !== 'simulated');
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
        const feeds = data.feeds_polled
          ? ` · ${data.feeds_polled - (data.feeds_failed || 0)}/${data.feeds_polled} feeds`
          : '';
        const disco = data.keywords_searched
          ? ` · ${data.keywords_searched} keywords → ${data.discovery_hits ?? 0} past-hour hits`
          : '';
        const stale = data.dropped_stale ? ` · ${data.dropped_stale} stale dropped` : '';
        el.textContent = `last ingest ${t}${disco}${feeds}${stale} · ${data.new_stories ?? 0} new · top score ${data.max_score ?? '—'}`;
        el.title = (data.keywords || []).join(', ');
      }
    }
  }

  return { add, status };
})();

// Auto-refresh: re-runs the source-matrix ingest on a timer.
// Click cycles OFF -> 2m -> 5m -> 15m; choice persists across reloads.
const AutoRefresh = (() => {
  const STEPS = [0, 120, 300, 900]; // seconds; 0 = off
  let stepIdx = Number(localStorage.getItem('autoRefreshStep') || 0);
  if (!STEPS[stepIdx]) stepIdx = STEPS[stepIdx] === 0 ? stepIdx : 0;
  let nextAt = null;
  let timer = null;

  function label() {
    const el = document.getElementById('auto-refresh');
    if (!el) return;
    const secs = STEPS[stepIdx];
    if (!secs) {
      el.textContent = 'AUTO ⟳ OFF';
      el.classList.remove('border-accent', 'text-accent');
      return;
    }
    const remain = Math.max(0, Math.round((nextAt - Date.now()) / 1000));
    const mm = String(Math.floor(remain / 60)).padStart(2, '0');
    const ss = String(remain % 60).padStart(2, '0');
    el.textContent = `AUTO ⟳ ${secs / 60}m · ${mm}:${ss}`;
    el.classList.add('border-accent', 'text-accent');
  }

  function arm() {
    clearInterval(timer);
    const secs = STEPS[stepIdx];
    if (!secs) { label(); return; }
    nextAt = Date.now() + secs * 1000;
    label();
    timer = setInterval(async () => {
      if (Date.now() >= nextAt) {
        nextAt = Date.now() + secs * 1000;
        try { await api('/api/ingest'); } catch { /* next tick retries */ }
      }
      label();
    }, 1000);
  }

  function cycle() {
    stepIdx = (stepIdx + 1) % STEPS.length;
    localStorage.setItem('autoRefreshStep', String(stepIdx));
    arm();
  }

  arm();
  return { cycle };
})();

async function api(path) {
  const res = await fetch(path, { method: 'POST' });
  return res.json();
}

// Manual X-desk refresh — deliberate spend of the monthly TwtAPI budget.
const XRefresh = (() => {
  function budget(r) {
    const el = document.getElementById('x-budget');
    if (el && r && r.monthly_remaining != null) {
      el.textContent = `· ${r.monthly_remaining} API calls left this month`;
    }
  }

  async function run() {
    const btn = document.getElementById('x-refresh-btn');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '𝕏 fetching…';
    try {
      const r = await api('/api/x/refresh');
      budget(r);
      btn.textContent = r.ok
        ? `𝕏 +${r.tweets_new} tweets`
        : '𝕏 refresh failed';
      if (!r.ok && r.error) {
        const el = document.getElementById('x-layer');
        if (el) el.textContent = `layer: ${r.layer} — ${r.error}`.slice(0, 90);
      }
    } catch {
      btn.textContent = '𝕏 refresh failed';
    } finally {
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500);
    }
  }

  // show budget + provider state on load without spending any calls
  fetch('/api/x/status').then(r => r.json()).then(s => {
    budget(s);
    if (!s.key_configured && s.manual_only) {
      const el = document.getElementById('x-layer');
      if (el) el.textContent = 'layer: twtapi — API key missing (set TWT_API_KEY in .env)';
    }
  }).catch(() => {});

  return { run, budget };
})();
