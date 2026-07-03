// Shared helpers + tab navigation.

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

const Tabs = (() => {
  const pages = { stories: 'page-stories', xdesk: 'page-xdesk', ops: 'page-ops' };

  function show(name) {
    Object.entries(pages).forEach(([key, id]) => {
      document.getElementById(id).classList.toggle('hidden', key !== name);
    });
    document.querySelectorAll('.tab').forEach(b => {
      const active = b.dataset.tab === name;
      b.classList.toggle('tab-active', active);
      b.classList.toggle('text-sub', !active);
    });
    if (name === 'ops') Ops.load();
    location.hash = name;
  }

  document.querySelectorAll('.tab').forEach(b =>
    b.addEventListener('click', () => show(b.dataset.tab)));

  const initial = location.hash.replace('#', '');
  if (pages[initial]) show(initial);
  return { show };
})();

function setUpdated(text) {
  const el = document.getElementById('updated');
  if (el) el.textContent = text;
}

// Flash strip: slides down for new breaking stories and viral X spikes.
// Deduped per story+kind for 5 minutes so sustained spikes don't spam.
const Flash = (() => {
  const KINDS = {
    breaking: { badge: 'FLASH · BREAKING', bg: '#D92D20' },
    viral: { badge: 'VIRAL ON X', bg: '#1570EF' },
  };
  const recent = new Map();
  let timer = null;
  let targetId = null;

  function show(kind, title, storyId) {
    const key = `${kind}:${storyId ?? title}`;
    if (recent.has(key) && Date.now() - recent.get(key) < 300000) return;
    recent.set(key, Date.now());
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
    Tabs.show('stories');
    if (targetId) {
      const card = document.querySelector(`article[data-id="${targetId}"]`);
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.style.outline = '2px solid #1570EF';
        setTimeout(() => { card.style.outline = ''; }, 2500);
      }
    }
  }

  return { show, hide, jump };
})();
