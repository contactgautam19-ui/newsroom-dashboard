// Shared helpers, sidebar navigation, header clock/live state, flash strip.

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

async function api(path, method = 'POST') {
  const res = await fetch(path, { method });
  if (res.status === 403 && document.body.classList.contains('guest')) guestNotice();
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
    picks: { page: 'page-stories', nav: 'picks', title: 'My Picks', subtitle: 'Stories you have taken for coverage.', filter: 'Picked' },
    hyper: { page: 'page-hyper', nav: 'hyper', title: 'Hyper Search', subtitle: 'Production deck from the last 24 hours.' },
    social: { page: 'page-social', nav: 'social', title: 'Social Monitor', subtitle: 'Trending on Google, YouTube and X.' },
    xdesk: { page: 'page-xdesk', nav: 'xdesk', title: 'X Desk', subtitle: 'Real posts from monitored handles, ranked for action.' },
    ops: { page: 'page-ops', nav: 'ops', title: 'Ops Desk', subtitle: 'System health, controls, and guardrail audit.' },
    analytics: { page: 'page-analytics', nav: 'analytics', title: 'Analytics', subtitle: 'Board balance and scoring anatomy.' },
    alerts: { page: 'page-alerts', nav: 'alerts', title: 'Alerts', subtitle: 'Breaking flashes and viral acceleration events.' },
  };
  const PAGES = ['page-stories', 'page-hyper', 'page-social', 'page-xdesk', 'page-ops', 'page-analytics', 'page-alerts'];
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
        b.classList.add(['stories', 'hyper', 'social', 'xdesk', 'ops'].includes(v.nav) ? 'active' : 'active-soft');
      }
    });
    if (v.filter) StoryDesk.setFilter(v.filter);
    else if (v.page === 'page-stories') StoryDesk.setFilter('All');
    if (name === 'ops') Ops.load();
    if (name === 'analytics') Views.analytics();
    if (name === 'alerts') Views.alerts();
    if (name === 'hyper') Hyper.open();
    if (name === 'social') Social.open();
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
  dot.style.background = state === 'live' ? '#34C38A' : state === 'busy' ? '#F0A93B' : '#EC4A4D';
  label.textContent = state === 'live' ? 'Live' : state === 'busy' ? 'Refreshing' : 'Offline';
}

function setUpdated(text) {
  const el = document.getElementById('refresh-label');
  if (el) el.textContent = text;
}

// Toast: small bottom-center pill for lightweight action feedback (pick/undo).
// Only one toast at a time — showing a new one replaces whatever is up.
const Toast = (() => {
  let el = null;
  let timer = null;

  function ensure() {
    if (el) return el;
    el = document.createElement('div');
    el.id = 'toast';
    el.className = 'fade-up fixed bottom-6 left-1/2 -translate-x-1/2 z-50 rounded-full px-5 py-2.5 text-[13px] font-medium shadow-lg hidden mb-[env(safe-area-inset-bottom)]';
    document.body.appendChild(el);
    return el;
  }

  function show(message, opts = {}) {
    const { actionLabel, onAction, ms = 5000 } = opts;
    const node = ensure();
    clearTimeout(timer);
    node.innerHTML = `<span>${esc(message)}</span>` +
      (actionLabel ? `<button class="ml-3 underline font-semibold hover:opacity-70">${esc(actionLabel)}</button>` : '');
    node.style.background = '#EEF1F6'; node.style.color = '#05080F';
    node.classList.remove('hidden');
    node.classList.add('flex', 'items-center');
    node.style.animation = 'none';
    void node.offsetHeight;
    node.style.animation = '';
    if (actionLabel && typeof onAction === 'function') {
      const btn = node.querySelector('button');
      if (btn) btn.onclick = () => { hide(); onAction(); };
    }
    timer = setTimeout(hide, ms);
  }

  function hide() {
    if (el) { el.classList.add('hidden'); el.classList.remove('flex', 'items-center'); }
    clearTimeout(timer);
  }

  return { show, hide };
})();

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
    breaking: { badge: 'FLASH · BREAKING', bg: '#EC4A4D' },
    viral: { badge: 'VIRAL ON X', bg: '#8FB4E8' },
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

  function jumpTo(id) { targetId = id; jump(); }

  function jump() {
    hide();
    Nav.go('stories');
    if (targetId) {
      const card = document.querySelector(`article[data-id="${targetId}"]`);
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.style.outline = '2px solid #8FB4E8';
        setTimeout(() => { card.style.outline = ''; }, 2500);
      }
    }
  }

  return { show, hide, jump, jumpTo };
})();

window.addEventListener('DOMContentLoaded', () => {
  const initial = location.hash.replace('#', '');
  if (initial && initial !== 'stories') Nav.go(initial);
  else Nav.go('stories');
  autoRefreshOnOpen();
  applyRole();
});

// Guests (emailed-code sessions) get a demo view: the Ops desk is hidden, the
// paid features are metered (3 live X pulls, a capped number of N-Pro
// requests) and the sidebar shows who they are.
let guestQuota = null; // {x, ai} remaining for a guest; null for editors
async function applyRole() {
  let me = {};
  try { me = await (await fetch('/api/me')).json(); } catch { return; }
  if (me.role !== 'guest') return;
  document.body.classList.add('guest');
  guestQuota = me.quota || null;
  const name = me.name || 'Guest';
  document.getElementById('user-name').textContent = name;
  document.getElementById('user-role').textContent = 'Guest · demo access';
  document.getElementById('user-initial').textContent = name.trim()[0].toUpperCase();
  if (location.hash.replace('#', '') === 'ops') Nav.go('stories');
  if (typeof XDesk !== 'undefined') XDesk.guestPulls(guestQuota ? guestQuota.x : null);
  if (me.expires_at) guestCountdown(me.expires_at * 1000);
}

// A demo sitting is 15 minutes: show the time left, then hand the guest back
// to the guest page rather than leaving them on a dashboard that stopped working.
function guestCountdown(expiresMs) {
  const role = document.getElementById('user-role');
  const tick = () => {
    const left = Math.round((expiresMs - Date.now()) / 1000);
    if (left <= 0) { location.href = '/guest?ended=1'; return; }
    role.textContent = `Demo · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left`;
    role.style.color = left <= 120 ? '#FDA29B' : '';
    if (left === 120) Toast.show('Your demo session ends in 2 minutes.');
  };
  tick();
  setInterval(tick, 1000);
}

function guestNotice() {
  if (document.getElementById('guest-notice')) return;
  const n = document.createElement('div');
  n.id = 'guest-notice';
  n.textContent = 'Demo access — that action is for editors.';
  n.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#EEF1F6;color:#05080F;padding:11px 18px;border-radius:999px;font-weight:600;font-size:13.5px;box-shadow:0 10px 30px rgba(0,0,0,.35);z-index:99';
  document.body.appendChild(n);
  setTimeout(() => n.remove(), 2800);
}

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
