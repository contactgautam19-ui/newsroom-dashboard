// N-Pro — AI news-script assistant. A full-screen newsroom console launched
// from "Pick Story". Conversational flow: retrieve → summarise → choose a
// production format → answer its pre-questions → generate a broadcast script,
// with Get More Context and smart action chips. Right rail = Story Intelligence.

const NPro = (() => {
  let S = {};                 // session state, reset on open()
  let meta = null;            // {formats, actions} from /api/npro/formats

  function reset(storyId, topic) {
    S = {
      storyId: storyId ?? null, topic: topic || '',
      retrieved: [], usedAngles: [], seenUrls: new Set(), seenTitles: [],
      format: null, qIndex: 0, params: {}, multiSel: new Set(),
      guests: [], lastScript: null, history: [],
    };
  }

  // ── shell ────────────────────────────────────────────────────────────────
  function overlay() { return document.getElementById('npro'); }
  function thread() { return document.getElementById('npro-thread'); }
  function scrollDown() { const t = thread(); t.scrollTop = t.scrollHeight; }

  async function open(storyId) {
    reset(storyId, '');
    overlay().classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    thread().innerHTML = '';
    setTitle('N-Pro', 'Retrieving the latest reporting…');
    renderRecent();
    aiTyping();
    if (!meta) { try { meta = await (await fetch('/api/npro/formats')).json(); } catch {} }
    let data;
    try { data = await postJSON('/api/npro/open', { story_id: storyId }); }
    catch { clearTyping(); msgAI('I couldn’t reach the desk. Try again in a moment.'); return; }
    applyRetrieval(data, true);
  }

  function close() {
    overlay().classList.add('hidden');
    document.body.style.overflow = '';
  }

  function toggleIntel() {
    const p = document.getElementById('npro-intel-panel');
    const hidden = p.classList.contains('hidden');
    p.classList.toggle('hidden', !hidden);
    p.classList.toggle('flex', hidden);
    if (hidden) { p.classList.add('fixed', 'inset-y-0', 'right-0', 'z-10', 'shadow-2xl', 'lg:static', 'lg:shadow-none'); }
  }

  function setTitle(t, sub) {
    document.getElementById('npro-title').textContent = t;
    document.getElementById('npro-status').textContent = sub;
  }

  // ── message primitives ─────────────────────────────────────────────────────
  function bubble(side, inner) {
    const wrap = document.createElement('div');
    wrap.className = 'fade-up flex ' + (side === 'user' ? 'justify-end' : 'justify-start');
    wrap.innerHTML = side === 'user'
      ? `<div class="max-w-[85%] bg-navy text-white rounded-2xl rounded-br-sm px-4 py-2.5 text-[14px]">${inner}</div>`
      : `<div class="max-w-[92%] w-full"><div class="flex items-center gap-2 mb-1"><span class="w-5 h-5 rounded bg-brand text-white text-[10px] font-extrabold flex items-center justify-center">N</span><span class="text-[11.5px] font-semibold text-sub">N-Pro</span></div><div class="bg-white border border-line rounded-2xl rounded-tl-sm px-4 py-3 text-[14px] leading-relaxed">${inner}</div></div>`;
    thread().appendChild(wrap);
    scrollDown();
    return wrap;
  }
  function msgAI(html) { clearTyping(); return bubble('ai', html); }
  function msgUser(text) { return bubble('user', esc(text)); }
  function aiTyping() {
    clearTyping();
    const w = document.createElement('div');
    w.id = 'npro-typing';
    w.className = 'flex justify-start';
    w.innerHTML = `<div class="bg-white border border-line rounded-2xl px-4 py-3 text-[13px] text-sub">N-Pro is thinking…</div>`;
    thread().appendChild(w); scrollDown();
  }
  function clearTyping() { const t = document.getElementById('npro-typing'); if (t) t.remove(); }

  // ── retrieval + summary ─────────────────────────────────────────────────────
  function applyRetrieval(data, initial) {
    clearTyping();
    S.topic = data.topic || S.topic;
    S.retrieved = data.retrieved || [];
    S.usedAngles = []; S.seenUrls = new Set(); S.seenTitles = [];
    S.retrieved.forEach(a => { if (a.url) S.seenUrls.add(a.url); if (a.title) S.seenTitles.push(a.title); });
    if (data.has_key === false) setTitle(S.topic || 'N-Pro', 'Template mode — add an API key in Ops for live AI scripts');
    else setTitle(S.topic || 'N-Pro', `${S.retrieved.length} sources · News-production assistant`);
    pushHistory(S.topic);
    const srcCount = S.retrieved.length;
    msgAI(`${srcLine(srcCount)}${esc(data.summary || '')}`);
    askFormat();
    loadIntel();
  }

  function srcLine(n) {
    return n ? `<p class="text-[11.5px] text-sub mb-2">Pulled ${n} recent report${n === 1 ? '' : 's'} from multiple publishers.</p>` : '';
  }

  // ── format menu ─────────────────────────────────────────────────────────────
  function askFormat() {
    const btns = (meta?.formats || []).map(f =>
      `<button onclick="NPro.pickFormat('${f.id}')" class="flex items-center gap-2 border border-line rounded-xl px-3.5 py-2.5 text-left hover:border-navy hover:bg-paper transition-colors">
         <span class="text-[16px]">${f.icon}</span>
         <span><span class="block text-[13.5px] font-semibold">${esc(f.label)}</span><span class="block text-[11.5px] text-sub">${esc(f.blurb)}</span></span>
       </button>`).join('');
    msgAI(`<p class="mb-2.5 font-medium">How would you like to produce this story?</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">${btns}</div>`);
  }

  function pickFormat(fid) {
    S.format = fid; S.qIndex = 0; S.params = {}; S.multiSel = new Set(); S.guests = [];
    const f = meta.formats.find(x => x.id === fid);
    msgUser(f ? f.label : fid);
    nextQuestion();
  }

  // ── question flow ────────────────────────────────────────────────────────────
  function currentRecipe() { return meta.formats.find(x => x.id === S.format); }

  function nextQuestion() {
    const r = currentRecipe();
    if (!r || S.qIndex >= r.questions.length) return doGenerate();
    const q = r.questions[S.qIndex];
    if (q.type === 'chips') return renderChips(q, false);
    if (q.type === 'chips_custom') return renderChips(q, true);
    if (q.type === 'text') return renderText(q);
    if (q.type === 'multi') return renderMulti(q);
    if (q.type === 'guests') return renderGuests(q);
    // unknown -> skip
    S.qIndex++; nextQuestion();
  }

  function renderChips(q, allowCustom) {
    const opts = q.options.map(o =>
      `<button data-fid="${esc(S.format)}" data-qi="${S.qIndex}" data-val="${esc(o)}" onclick="NPro.answerEl(this)" class="px-3 py-1.5 rounded-lg text-[13px] font-semibold border border-line bg-white hover:border-navy">${esc(o)}</button>`).join('');
    const custom = allowCustom ? `
      <button onclick="NPro.showCustom(this)" class="px-3 py-1.5 rounded-lg text-[13px] font-semibold border border-dashed border-line bg-white hover:border-navy">Custom…</button>
      <div class="hidden w-full mt-2 flex gap-2">
        <input type="text" placeholder="${esc(q.custom_hint || 'Type a custom value')}" class="flex-1 border border-line rounded-lg px-3 py-2 text-[13px]" onkeydown="if(event.key==='Enter')NPro.answerText('${S.format}', ${S.qIndex}, this.value)">
        <button onclick="NPro.answerText('${S.format}', ${S.qIndex}, this.previousElementSibling.value)" class="bg-navy text-white rounded-lg px-3 text-[13px] font-semibold">Use</button>
      </div>` : '';
    msgAI(`<p class="mb-2 font-medium">${esc(q.prompt)}</p><div class="flex flex-wrap gap-2 items-center">${opts}${custom}</div>`);
  }

  function renderText(q) {
    msgAI(`<p class="mb-2 font-medium">${esc(q.prompt)}</p>
      <div class="flex gap-2">
        <input type="text" placeholder="${esc(q.placeholder || '')}" class="flex-1 border border-line rounded-lg px-3 py-2 text-[13px]" onkeydown="if(event.key==='Enter')NPro.answerText('${S.format}', ${S.qIndex}, this.value)">
        <button onclick="NPro.answerText('${S.format}', ${S.qIndex}, this.previousElementSibling.value)" class="bg-navy text-white rounded-lg px-4 text-[13px] font-semibold">Next</button>
      </div>`);
  }

  function renderMulti(q) {
    const opts = q.options.map(o =>
      `<button data-v="${esc(o)}" onclick="NPro.toggleMulti(this)" class="px-3 py-1.5 rounded-lg text-[13px] font-semibold border border-line bg-white">${esc(o)}</button>`).join('');
    msgAI(`<p class="mb-2 font-medium">${esc(q.prompt)}</p>
      <div class="flex flex-wrap gap-2 mb-2.5">${opts}</div>
      <button onclick="NPro.finishMulti('${S.format}', ${S.qIndex})" class="bg-navy text-white rounded-lg px-4 py-2 text-[13px] font-semibold">Continue</button>`);
  }

  function renderGuests(q) {
    const fields = q.fields.map(f =>
      `<input data-f="${esc(f)}" placeholder="${esc(f)}" class="border border-line rounded-lg px-3 py-2 text-[13px] w-full mb-1.5">`).join('');
    msgAI(`<p class="mb-2 font-medium">${esc(q.prompt)}</p>
      <div id="npro-guest-list" class="space-y-1 mb-2 text-[13px]"></div>
      <div data-guestform class="border border-line rounded-xl p-3">${fields}
        <div class="flex gap-2 mt-1">
          <button onclick="NPro.addGuest(this)" class="flex-1 border border-line rounded-lg px-3 py-2 text-[13px] font-semibold hover:border-navy">＋ Add guest</button>
          <button onclick="NPro.finishGuests('${S.format}', ${S.qIndex})" class="bg-navy text-white rounded-lg px-4 py-2 text-[13px] font-semibold">Build debate</button>
        </div>
      </div>`);
  }

  // answer handlers
  function answer(fid, qi, value) { if (fid !== S.format || qi !== S.qIndex) return; recordAndAdvance(value); }
  function answerEl(el) { answer(el.dataset.fid, Number(el.dataset.qi), el.dataset.val); }
  function answerText(fid, qi, value) {
    if (fid !== S.format || qi !== S.qIndex) return;
    const v = (value || '').trim(); if (!v) return; recordAndAdvance(v);
  }
  function showCustom(btn) { const box = btn.nextElementSibling; box.classList.remove('hidden'); box.classList.add('flex'); box.querySelector('input').focus(); }
  function toggleMulti(btn) {
    const v = btn.dataset.v;
    if (S.multiSel.has(v)) { S.multiSel.delete(v); btn.classList.remove('bg-navy', 'text-white', 'border-navy'); }
    else { S.multiSel.add(v); btn.classList.add('bg-navy', 'text-white', 'border-navy'); }
  }
  function finishMulti(fid, qi) { if (fid !== S.format || qi !== S.qIndex) return; recordAndAdvance([...S.multiSel]); S.multiSel = new Set(); }
  function addGuest(btn) {
    const box = btn.closest('[data-guestform]');
    if (!box) return;
    const g = {};
    box.querySelectorAll('input[data-f]').forEach(i => { g[i.dataset.f] = i.value.trim(); i.value = ''; });
    if (!g[Object.keys(g)[0]]) return; // need a name
    S.guests.push(g);
    const list = document.getElementById('npro-guest-list');
    if (list) list.innerHTML = S.guests.map(x => `<div class="bg-paper rounded-lg px-2.5 py-1.5">👤 <b>${esc(x['Guest name'] || '')}</b> — ${esc(x['Designation'] || '')}</div>`).join('');
  }
  function finishGuests(fid, qi) {
    if (fid !== S.format || qi !== S.qIndex) return;
    if (!S.guests.length) { addGuest(document.querySelector('#npro-thread button[onclick*="addGuest"]')); }
    recordAndAdvance(S.guests);
  }

  function recordAndAdvance(value) {
    const r = currentRecipe();
    const q = r.questions[S.qIndex];
    S.params[q.id] = value;
    const shown = Array.isArray(value) ? (value.length && value[0].__proto__ === Object.prototype && value[0]['Guest name'] ? value.map(g => g['Guest name']).join(', ') : value.join(', ')) : value;
    if (shown) msgUser(String(shown));
    S.qIndex++;
    nextQuestion();
  }

  // ── generation ───────────────────────────────────────────────────────────────
  async function doGenerate() {
    aiTyping();
    let res;
    try {
      res = await postJSON('/api/npro/generate', {
        story_id: S.storyId, format: S.format, params: S.params, retrieved: S.retrieved,
      });
    } catch { return msgAI('Generation failed — try again.'); }
    if (!res.ok) return msgAI(esc(res.error || 'Could not generate.'));
    S.lastScript = res.script;
    renderScript(res.script, res.model);
  }

  function renderScript(script, model) {
    const badge = model === 'mock'
      ? '<span class="bg-blue1 text-blue8 text-[10px] font-bold px-2 py-0.5 rounded">TEMPLATE</span>'
      : '<span class="bg-amber1 text-amber8 text-[10px] font-bold px-2 py-0.5 rounded">AI DRAFT — REVIEW</span>';
    const chips = (meta?.actions || []).map(a =>
      `<button onclick="NPro.action('${a.id}', this)" class="px-2.5 py-1 rounded-lg text-[12px] font-medium border border-line bg-white hover:border-navy">${esc(a.label)}</button>`).join('');
    const w = msgAI(`
      <div class="flex items-center gap-2 mb-2">${badge}
        <button onclick="NPro.copyLast(this)" class="ml-auto text-[12px] text-accent font-semibold hover:underline">Copy</button></div>
      <div class="npro-script text-[13.5px]" style="white-space:pre-wrap">${fmtScript(script)}</div>
      <div class="mt-3 pt-3 border-t border-line">
        <button onclick="NPro.moreContext(this)" class="w-full mb-2.5 flex items-center justify-center gap-2 bg-accent text-white rounded-xl px-4 py-2.5 text-[13px] font-semibold hover:opacity-90">🔍 Get More Context</button>
        <div class="flex flex-wrap gap-1.5">${chips}</div>
      </div>`);
    w.dataset.script = script;
  }

  function fmtScript(text) {
    return esc(text).replace(/^([A-Z][A-Z0-9 ,'’\-\/&()]{2,}:)/gm, '<span class="font-bold text-navy">$1</span>');
  }

  function copyLast(btn) {
    const card = btn.closest('[data-script]');
    const txt = card ? card.dataset.script : S.lastScript;
    if (txt) navigator.clipboard.writeText(txt);
    const p = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => btn.textContent = p, 1500);
  }

  // ── smart actions ────────────────────────────────────────────────────────────
  async function action(actionId, btn) {
    const card = btn.closest('[data-script]');
    const content = card ? card.dataset.script : S.lastScript;
    if (!content) return;
    const label = (meta.actions.find(a => a.id === actionId) || {}).label || actionId;
    msgUser(label);
    aiTyping();
    let res;
    try { res = await postJSON('/api/npro/action', { action: actionId, content, story_id: S.storyId, retrieved: S.retrieved }); }
    catch { return msgAI('That action failed — try again.'); }
    if (!res.ok) return msgAI(esc(res.error || 'Action failed.'));
    // treat a full rewrite as a new script (with its own chips); others as a note
    const rewriteish = ['shorter', 'conversational', 'dramatic', 'more_facts', 'history', 'hindi', 'english', 'digital', 'ott'].includes(actionId);
    if (rewriteish) { S.lastScript = res.result; renderScript(res.result, res.model); }
    else msgAI(`<div class="npro-script text-[13.5px]" style="white-space:pre-wrap">${fmtScript(res.result)}</div>`);
  }

  // ── Get More Context ─────────────────────────────────────────────────────────
  async function moreContext(btn) {
    btn.disabled = true; const orig = btn.innerHTML; btn.innerHTML = 'Finding new angles…';
    let res;
    try {
      res = await postJSON('/api/npro/context', {
        topic: S.topic, used_angles: S.usedAngles,
        seen_urls: [...S.seenUrls], seen_titles: S.seenTitles,
      });
    } catch { btn.disabled = false; btn.innerHTML = orig; return; }
    btn.disabled = false; btn.innerHTML = orig;
    if (!res.angle || !res.items.length) {
      msgAI('No fresh angles left on this story right now — I’ve exhausted the obvious threads. Try a new query below.');
      return;
    }
    S.usedAngles.push(res.angle);
    res.items.forEach(it => { if (it.url) S.seenUrls.add(it.url); if (it.title) S.seenTitles.push(it.title); S.retrieved.push(it); });
    const items = res.items.map(it =>
      `<li class="flex gap-2"><span class="text-sub">•</span><span><a href="${esc(it.url)}" target="_blank" class="hover:underline font-medium">${esc(it.title)}</a> <span class="text-sub text-[11.5px]">${esc(it.publisher || '')}</span></span></li>`).join('');
    msgAI(`<p class="text-[11px] font-bold uppercase tracking-widest text-accent mb-1.5">More context · ${esc(res.label)}</p>
      <ul class="space-y-1 text-[13px]">${items}</ul>`);
    loadIntel(); // panel keeps growing with the story
  }

  // ── intelligence panel ───────────────────────────────────────────────────────
  async function loadIntel() {
    const el = document.getElementById('npro-intel');
    if (!el) return;
    let d;
    try { d = await postJSON('/api/npro/intelligence', { story_id: S.storyId, topic: S.topic, retrieved: S.retrieved }); }
    catch { return; }
    document.getElementById('npro-intel-src').textContent = d.source === 'ai' ? 'AI' : 'auto';
    const sec = (title, items, render) => (items && items.length)
      ? `<div><p class="text-[10.5px] font-bold uppercase tracking-widest text-sub mb-1.5">${title}</p>${render(items)}</div>` : '';
    const chips = items => `<div class="flex flex-wrap gap-1">${items.map(i => `<span class="bg-paper border border-line rounded px-1.5 py-0.5 text-[12px]">${esc(i)}</span>`).join('')}</div>`;
    const list = items => `<ul class="space-y-1">${items.map(i => `<li class="flex gap-1.5"><span class="text-sub">•</span><span>${esc(i)}</span></li>`).join('')}</ul>`;
    const checks = items => `<ul class="space-y-1">${items.map(i => `<li class="flex gap-1.5"><span class="text-green6">☑</span><span>${esc(i)}</span></li>`).join('')}</ul>`;
    el.innerHTML = [
      sec('Timeline', d.timeline, list),
      sec('People', d.people, chips),
      sec('Organizations', d.organizations, chips),
      sec('Locations', d.locations, chips),
      sec('Quick facts', d.quick_facts, list),
      sec('Numbers', d.numbers, chips),
      sec('Key quotes', d.key_quotes, list),
      sec('Suggested graphics', d.suggested_graphics, list),
      sec('Suggested visuals', d.suggested_visuals, list),
      sec('Related stories', d.related_stories, list),
      sec('Verification checklist', d.verification_checklist, checks),
    ].filter(Boolean).join('') || '<p class="text-sub text-[12.5px]">No intelligence extracted yet.</p>';
  }

  // ── free-form ask ────────────────────────────────────────────────────────────
  async function ask() {
    const inp = document.getElementById('npro-input');
    const q = (inp.value || '').trim();
    if (!q) return;
    inp.value = ''; inp.style.height = 'auto';
    msgUser(q);
    aiTyping();
    S.storyId = null; // free-form query is not tied to a board story
    let data;
    try { data = await postJSON('/api/npro/retrieve', { query: q }); }
    catch { return msgAI('I couldn’t retrieve that — try again.'); }
    applyRetrieval(data, false);
  }

  // ── left rail ──────────────────────────────────────────────────────────────
  function renderRecent() {
    const el = document.getElementById('npro-recent');
    if (!el) return;
    const stories = (window.StoryDesk?.stories || []).filter(s => !s.picked).slice(0, 8);
    el.innerHTML = stories.length ? stories.map(s =>
      `<button onclick="NPro.open(${s.id})" class="block w-full text-left px-2 py-1.5 rounded-lg hover:bg-navy2 text-[12.5px] text-white/80 truncate">${esc(s.title)}</button>`).join('')
      : '<p class="px-2 text-white/40 text-[12px]">No board stories loaded</p>';
  }
  function pushHistory(topic) {
    if (!topic) return;
    S.history = [topic, ...S.history.filter(t => t !== topic)].slice(0, 10);
    const el = document.getElementById('npro-history');
    if (el) el.innerHTML = S.history.map(t =>
      `<div class="px-2 py-1 rounded text-[12.5px] text-white/60 truncate">${esc(t)}</div>`).join('');
  }

  // ── utils ──────────────────────────────────────────────────────────────────
  async function postJSON(url, body) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return res.json();
  }
  function jattr(s) { return JSON.stringify(String(s)); }

  // input: Enter to send, auto-grow
  window.addEventListener('DOMContentLoaded', () => {
    const inp = document.getElementById('npro-input');
    if (!inp) return;
    inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } });
    inp.addEventListener('input', () => { inp.style.height = 'auto'; inp.style.height = Math.min(inp.scrollHeight, 128) + 'px'; });
  });

  return { open, close, toggleIntel, pickFormat, answer, answerEl, answerText,
           showCustom, toggleMulti, finishMulti, addGuest, finishGuests, action,
           moreContext, copyLast, ask };
})();
