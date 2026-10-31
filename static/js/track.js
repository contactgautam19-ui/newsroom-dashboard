// Story tracking — following one running story across the day it develops.
//
// The board shows the last hour and retires fast, which is right for a
// rundown. A rally tomorrow produces police permissions, traffic curbs, a
// deployment number and an opposition reaction over eight hours, and no
// hourly snapshot shows that shape. This panel does.
//
// Replaces the old "Pick a Story" quick action, which only ever opened the
// top-ranked story and gave the editor no say in which.

const Track = (() => {
  let tracks = [];

  // There is no shared modal helper in this app — Story Pack and N-Pro each
  // own their overlay — so this owns #track-overlay the same way.
  function showModal(html) {
    document.getElementById('track-content').innerHTML = html;
    document.getElementById('track-overlay').classList.remove('hidden');
  }
  function closeModal() {
    document.getElementById('track-overlay').classList.add('hidden');
  }

  // The board's current stories, via StoryDesk's getter.
  const boardStories = () =>
    (typeof StoryDesk !== 'undefined' && Array.isArray(StoryDesk.stories))
      ? StoryDesk.stories : [];

  const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // Each kind gets its own colour so a producer can read the column by shape
  // rather than by headline. These are editorial categories, not statuses, so
  // they stay low-chroma — identity is carried by the label.
  const KIND_STYLE = {
    permission: { c: '#22C58B', t: 'Permission' },
    security:   { c: '#C49A5E', t: 'Police & security' },
    traffic:    { c: '#7C9BC4', t: 'Traffic' },
    legal:      { c: '#9389C4', t: 'Legal' },
    reaction:   { c: '#C96E70', t: 'Reaction' },
    scale:      { c: '#5FA99B', t: 'Scale & turnout' },
    statement:  { c: '#A5B2C5', t: 'Statement' },
    update:     { c: '#75849A', t: 'Update' },
  };

  function ago(iso) {
    if (!iso) return '';
    const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    return h < 24 ? `${h} hr ago` : `${Math.round(h / 24)}d ago`;
  }

  function istTime(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata',
    }).toUpperCase();
  }

  // ── the panel on Story Desk ─────────────────────────────────────────────
  async function load() {
    try { tracks = await (await fetch('/api/track', { cache: 'no-store' })).json(); }
    catch { return; }
    if (!Array.isArray(tracks)) tracks = [];
    render();
  }

  function render() {
    const panel = document.getElementById('track-panel');
    const list = document.getElementById('track-list');
    const meta = document.getElementById('track-meta');
    if (!panel || !list) return;
    if (!tracks.length) { panel.classList.add('hidden'); return; }
    panel.classList.remove('hidden');

    const totalNew = tracks.reduce((n, t) => n + (t.unseen || 0), 0);
    meta.textContent = `${tracks.length} ${tracks.length === 1 ? 'story' : 'stories'}`
      + (totalNew ? ` · ${totalNew} new ${totalNew === 1 ? 'development' : 'developments'}` : '');

    list.innerHTML = tracks.map(t => `
      <article onclick="Track.open(${t.id})" class="bg-paper border border-line rounded-xl p-4 cursor-pointer hover:border-ink transition-colors">
        <div class="flex items-start gap-2 mb-2">
          <p class="text-[14px] font-semibold leading-snug flex-1 min-w-0">${esc(t.label)}</p>
          ${t.unseen ? `<span class="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded" style="background:var(--green);color:#06210F">${t.unseen} NEW</span>` : ''}
        </div>
        <p class="text-[12px] text-sub">
          ${t.updates_count || 0} ${t.updates_count === 1 ? 'development' : 'developments'}
          ${t.last_update_at ? ` · latest ${esc(ago(t.last_update_at))}` : ' · nothing yet'}
        </p>
      </article>`).join('');
  }

  // ── starting a track ────────────────────────────────────────────────────
  function openPicker() {
    // Offer the board's stories to choose from AND a free-text box, because
    // the story you want to follow is often tomorrow's, which by definition
    // is not on a board that only shows the last hour.
    const board = boardStories().filter(s => !s.picked);
    const rows = board.slice(0, 8).map(s => `
      <button onclick="Track.startFromStory(${s.id})" class="w-full text-left px-4 py-3 border-b border-line hover:bg-paper last:border-0">
        <p class="text-[13.5px] leading-snug">${esc(s.title)}</p>
        <p class="text-[11.5px] text-sub mt-0.5">${esc(s.publisher || '')}</p>
      </button>`).join('');

    showModal(`
      <div class="p-5 border-b border-line">
        <p class="font-serif text-[21px]">Track a story</p>
        <p class="text-[13px] text-sub mt-1">Echo will keep searching for developments on it — permissions, police, traffic, reactions — and build a timeline as the day goes.</p>
      </div>
      <div class="p-5 border-b border-line">
        <label class="text-[11.5px] font-semibold tracking-widest text-sub uppercase">Follow any story</label>
        <div class="flex gap-2 mt-2">
          <input id="track-input" placeholder="e.g. INDIA bloc Ramlila Maidan rally" maxlength="120"
                 class="flex-1 bg-paper border border-line rounded-xl px-3.5 py-2.5 text-[14px] focus:outline-none focus:border-ink">
          <button onclick="Track.startFromInput()" class="bg-navy text-white text-[13.5px] font-semibold px-5 rounded-xl hover:bg-navy2">Track</button>
        </div>
        <p class="text-[12px] text-sub mt-2">Name it the way you would say it on the desk. It does not have to be on the board yet.</p>
      </div>
      ${rows ? `<div class="p-5 pb-2"><p class="text-[11.5px] font-semibold tracking-widest text-sub uppercase">Or pick one from the board</p></div>
      <div class="max-h-[40vh] overflow-y-auto">${rows}</div>` : ''}`);
    setTimeout(() => { const i = document.getElementById('track-input'); if (i) i.focus(); }, 60);
  }

  async function start(label, storyId) {
    closeModal();
    Toast.show(`Tracking “${label}” — looking for developments…`);
    try {
      const r = await fetch('/api/track', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label, story_id: storyId ?? null }),
      });
      const data = await r.json();
      if (!r.ok || data.ok === false) {
        Toast.show(data.error || 'Could not start tracking that.');
        return;
      }
      await load();
      Toast.show(`Tracking “${label}” — ${data.updates_count || 0} developments so far`);
      if (data.id) open(data.id);
    } catch {
      Toast.show('Could not reach the server.');
    }
  }

  function startFromInput() {
    const el = document.getElementById('track-input');
    const label = (el && el.value || '').trim();
    if (!label) { if (el) el.focus(); return; }
    start(label, null);
  }

  function startFromStory(id) {
    const s = boardStories().find(x => x.id === id);
    if (s) start(s.title, id);
  }

  // ── the timeline ────────────────────────────────────────────────────────
  async function open(id) {
    let t;
    try { t = await (await fetch(`/api/track/${id}`, { cache: 'no-store' })).json(); }
    catch { Toast.show('Could not load that story.'); return; }
    if (!t || !t.id) { Toast.show('Could not load that story.'); return; }

    const shape = Object.entries(t.breakdown || {})
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => {
        const st = KIND_STYLE[k] || KIND_STYLE.update;
        return `<span class="inline-flex items-center gap-1.5 text-[12px] mr-3">
          <span class="w-2 h-2 rounded-full" style="background:${st.c}"></span>${esc(st.t)}
          <span class="text-sub">${n}</span></span>`;
      }).join('');

    const rows = (t.updates || []).map(u => {
      const st = KIND_STYLE[u.kind] || KIND_STYLE.update;
      return `
      <a href="${esc(u.url)}" target="_blank" rel="noopener noreferrer"
         class="flex items-start gap-3 px-5 py-3 border-b border-line hover:bg-paper last:border-0">
        <span class="shrink-0 w-[62px] font-mono text-[11px] text-muted pt-0.5">${esc(istTime(u.published_at))}</span>
        <span class="shrink-0 w-1 self-stretch rounded" style="background:${st.c}"></span>
        <span class="min-w-0 flex-1">
          <p class="text-[13.5px] leading-snug">${esc(u.title)}</p>
          <p class="text-[11.5px] text-sub mt-1">
            <span style="color:${st.c}">${esc(st.t)}</span>
            ${u.publisher ? ` · ${esc(u.publisher)}` : ''}
          </p>
        </span>
      </a>`;
    }).join('');

    showModal(`
      <div class="p-5 border-b border-line">
        <div class="flex items-start gap-3">
          <div class="min-w-0 flex-1">
            <p class="font-serif text-[21px] leading-tight">${esc(t.label)}</p>
            <p class="text-[12.5px] text-sub mt-1.5">
              ${t.updates_count || 0} developments · ${t.outlets || 0} outlets
              ${t.last_checked_at ? ` · checked ${esc(ago(t.last_checked_at))}` : ''}
            </p>
          </div>
          <button onclick="Track.checkNow(${t.id})" class="shrink-0 text-[12.5px] font-semibold px-3.5 py-1.5 rounded-lg border border-line hover:border-ink">⟳ Check now</button>
          <button onclick="Track.stop(${t.id})" class="shrink-0 text-[12.5px] font-semibold px-3.5 py-1.5 rounded-lg border border-line text-sub hover:border-ink">Stop</button>
        </div>
        ${shape ? `<div class="mt-3.5">${shape}</div>` : ''}
      </div>
      <div class="max-h-[55vh] overflow-y-auto">${rows || '<p class="p-8 text-center text-sub text-[13.5px]">Nothing yet. Echo checks again with every scan.</p>'}</div>`);

    fetch(`/api/track/${id}/seen`, { method: 'POST' }).then(load).catch(() => {});
  }

  async function checkNow(id) {
    Toast.show('Looking for developments…');
    try {
      const r = await (await fetch(`/api/track/${id}/check`, { method: 'POST' })).json();
      Toast.show(r.new ? `${r.new} new ${r.new === 1 ? 'development' : 'developments'}`
                       : 'Nothing new since the last check.');
      await open(id);
    } catch { Toast.show('Could not check right now.'); }
  }

  async function stop(id) {
    try { await fetch(`/api/track/${id}`, { method: 'DELETE' }); } catch { }
    closeModal();
    Toast.show('Stopped tracking.');
    load();
  }

  return { load, render, openPicker, start, startFromInput, startFromStory,
           open, checkNow, stop, closeModal };
})();
