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
