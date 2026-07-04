// Shared helpers, sidebar navigation, header clock/live state, flash strip.

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

async function api(path, method = 'POST') {
  const res = await fetch(path, { method });
  return res.json();
}

function ageLabel(iso) {
  if (!iso) return '';
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)} h ${mins % 60} min ago`;
  return `${Math.floor(mins / 1440)} d ago`;
}

function postedLabel(iso) {
  try {
    const d = new Date(iso);
    const t = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
    return new Date().toDateString() === d.toDateString()
      ? `posted ${t}`
      : `posted ${d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })} ${t}`;
  } catch { return ''; }
}

// Sidebar navigation. Views map onto three page sections plus two extra
// views; My Picks / Assignments / Top Stories are filtered story views.
const Nav = (() => {
  const VIEWS = {
    stories: { page: 'page-stories', nav: 'stories', title: 'Story Desk', subtitle: 'Your command center for real-time editorial decisions.' },
    top: { page: 'page-stories', nav: 'top', title: 'Top Stories', subtitle: 'The full ranked board, highest score first.', filter: 'All' },
    picks: { page: 'page-stories', nav: 'picks', title: 'My Picks', subtitle: 'Stories you have taken for coverage.', filter: 'Picked' },
    assignments: { page: 'page-stories', nav: 'assignments', title: 'Assignments', subtitle: 'Everything currently being worked on.', filter: 'Picked' },
    xdesk: { page: 'page-xdesk', nav: 'xdesk', title: 'X Desk', subtitle: 'Real posts from monitored handles, ranked for action.' },
    ops: { page: 'page-ops', nav: 'ops', title: 'Ops Desk', subtitle: 'System health, controls, and guardrail audit.' },
    analytics: { page: 'page-analytics', nav: 'analytics', title: 'Analytics', subtitle: 'Board balance and scoring anatomy.' },
    alerts: { page: 'page-alerts', nav: 'alerts', title: 'Alerts', subtitle: 'Breaking flashes and viral acceleration events.' },
  };
  const PAGES = ['page-stories', 'page-xdesk', 'page-ops', 'page-analytics', 'page-alerts'];
  let current = 'stories';

  function go(name) {
    const v = VIEWS[name] || VIEWS.stories;
    current = name;
    PAGES.forEach(id => document.getElementById(id).classList.toggle('hidden', id !== v.page));
    document.getElementById('page-title').textContent = v.title;
    document.getElementById('page-subtitle').textContent = v.subtitle;
    document.querySelectorAll('.navlink').forEach(b => {
      b.classList.remove('active', 'active-soft');
      if (b.dataset.nav === v.nav) {
        b.classList.add(['stories', 'xdesk', 'ops'].includes(v.nav) ? 'active' : 'active-soft');
      }
    });
    if (v.filter) StoryDesk.setFilter(v.filter);
    else if (v.page === 'page-stories') StoryDesk.setFilter('All');
    if (name === 'ops') Ops.load();
    if (name === 'analytics') Views.analytics();
    if (name === 'alerts') Views.alerts();
    if (window.innerWidth < 1024) hideSidebar();
    location.hash = name;
  }

  function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    const bd = document.getElementById('sidebar-backdrop');
    const open = sb.classList.contains('hidden');
    sb.classList.toggle('hidden', !open);
    sb.classList.toggle('flex', open);
    bd.classList.toggle('hidden', !open);
  }

  function hideSidebar() {
    if (window.innerWidth >= 1024) return;
    document.getElementById('sidebar').classList.add('hidden');
    document.getElementById('sidebar').classList.remove('flex');
    document.getElementById('sidebar-backdrop').classList.add('hidden');
  }

  return { go, toggleSidebar, get current() { return current; } };
})();

function setLive(state) {
  const dot = document.getElementById('live-dot');
  const label = document.getElementById('live-label');
  if (!dot) return;
  dot.style.background = state === 'live' ? '#079455' : state === 'busy' ? '#DC6803' : '#D92D20';
  label.textContent = state === 'live' ? 'Live' : state === 'busy' ? 'Refreshing' : 'Offline';
}

function setUpdated(text) {
  const el = document.getElementById('refresh-label');
  if (el) el.textContent = text;
}

(function clock() {
  const el = document.getElementById('clock');
  function tick() {
    const now = new Date();
    if (el) el.textContent = now.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true })
      + ' · ' + now.toLocaleDateString('en-IN', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  tick();
  setInterval(tick, 15000);
})();

let alertCount = 0;
function bumpAlerts() {
  alertCount += 1;
  const b = document.getElementById('alert-badge');
  if (b) { b.textContent = alertCount > 9 ? '9+' : alertCount; b.classList.remove('hidden'); }
}

// Flash strip: slides down for new breaking stories and viral X spikes.
const Flash = (() => {
  const KINDS = {
    breaking: { badge: 'FLASH · BREAKING', bg: '#D92D20' },
    viral: { badge: 'VIRAL ON X', bg: '#2563EB' },
  };
  const recent = new Map();
  let timer = null;
  let targetId = null;

  function show(kind, title, storyId) {
    const key = `${kind}:${storyId ?? title}`;
    if (recent.has(key) && Date.now() - recent.get(key) < 300000) return;
    recent.set(key, Date.now());
    bumpAlerts();
    const cfg = KINDS[kind] || KINDS.breaking;
    document.getElementById('flash-inner').style.background = cfg.bg;
    document.getElementById('flash-badge').textContent = cfg.badge;
    document.getElementById('flash-title').textContent = title;
    targetId = storyId;
    const strip = document.getElementById('flash-strip');
    strip.classList.remove('hidden');
    strip.style.animation = 'none';
    void strip.offsetHeight;
    strip.style.animation = '';
    clearTimeout(timer);
    timer = setTimeout(hide, 10000);
  }

  function hide() {
    document.getElementById('flash-strip').classList.add('hidden');
  }

  function jump() {
    hide();
    Nav.go('stories');
    if (targetId) {
      const card = document.querySelector(`article[data-id="${targetId}"]`);
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.style.outline = '2px solid #2563EB';
        setTimeout(() => { card.style.outline = ''; }, 2500);
      }
    }
  }

  return { show, hide, jump };
})();

window.addEventListener('DOMContentLoaded', () => {
  const initial = location.hash.replace('#', '');
  if (initial && initial !== 'stories') Nav.go(initial);
  else Nav.go('stories');
  autoRefreshOnOpen();
});

// Auto-refresh the story board once per page load if the last ingest is
// missing or stale (>10 min), so the board is fresh the moment the app opens.
async function autoRefreshOnOpen() {
  try {
    const ops = await (await fetch('/api/ops')).json();
    const last = ops && ops.last_ingest && ops.last_ingest.last_ingest;
    const staleMs = 10 * 60 * 1000;
    const isStale = !last || (Date.now() - new Date(last).getTime()) > staleMs;
    if (isStale) {
      setLive('busy');
      setUpdated('refreshing…');
      await api('/api/ingest');
    }
  } catch { /* silent — SSE will still deliver data */ }
}
