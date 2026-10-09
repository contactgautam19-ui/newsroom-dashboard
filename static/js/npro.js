// N-Pro — AI news-script assistant. A full-screen newsroom console launched
// from "Pick Story". Conversational flow: retrieve → summarise → choose a
// production format → answer its pre-questions → generate a broadcast script,
// with Get More Context and smart action chips. Right rail = Story Intelligence.
//
// Scripts never name the outlets the reporting came from. The reporting is
// still one click away (Show sourcing, or just ask), kept apart from the copy.

const NPro = (() => {
  let S = {};                 // session state, reset on open()
  let meta = null;            // {formats, actions} from /api/npro/formats

  function reset(storyId, topic) {
    S = {
      storyId: storyId ?? null, topic: topic || '',
      retrieved: [], usedAngles: [], seenUrls: new Set(), seenTitles: [],
      format: null, qIndex: 0, params: {}, multiSel: new Set(),
      guests: [], lastScript: null, lastFormat: null, history: [], convo: [],
      note: null, triad: null, recordId: '', pinning: null,
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
    setTitle('N-Pro', 'Pulling the latest reporting…');
    renderRecent(); renderRecords();
    aiTyping('Pulling the latest reports on this story');
    if (!meta) { try { meta = await (await fetch('/api/npro/formats')).json(); } catch {} }
    let data;
    try { data = await postJSON('/api/npro/open', { story_id: storyId }); }
    catch (e) { if (e.limit) return; clearTyping(); msgAI('I couldn’t reach the desk. Try again in a moment.'); return; }
    applyRetrieval(data);
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
      ? `<div class="max-w-[85%] bg-paper text-ink border border-line rounded-2xl rounded-br-sm px-4 py-2.5 text-[14px]">${inner}</div>`
      : `<div class="max-w-[92%] w-full"><div class="flex items-center gap-2 mb-1"><span class="w-5 h-5 rounded bg-brand text-white text-[10px] font-extrabold flex items-center justify-center">N</span><span class="text-[11.5px] font-semibold text-sub">N-Pro</span></div><div class="bg-white border border-line rounded-2xl rounded-tl-sm px-4 py-3 text-[14px] leading-relaxed">${inner}</div></div>`;
    thread().appendChild(wrap);
    scrollDown();
    return wrap;
  }
  function msgAI(html) { clearTyping(); return bubble('ai', html); }
  function msgUser(text) { return bubble('user', esc(text)); }

  // Markdown-lite renderer: **bold** headers, "- " bullets, paragraph spacing.
  // The editorial engine emits this exact dialect; raw asterisks never show.
  function mdlite(text) {
    let h = esc(text).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    const out = [];
    let inList = false;
    for (const ln of h.split('\n')) {
      const t = ln.trim();
      if (/^[-•] /.test(t)) {
        if (!inList) { out.push('<ul class="space-y-1 my-1">'); inList = true; }
        out.push(`<li class="flex gap-2"><span class="text-sub shrink-0">•</span><span>${t.slice(2)}</span></li>`);
        continue;
      }
      if (inList) { out.push('</ul>'); inList = false; }
      if (!t) continue;
      if (/^<b>[^<]{2,60}<\/b>:?$/.test(t)) {
        out.push(`<p class="font-semibold text-ink mt-3 first:mt-0 mb-0.5">${t}</p>`);
      } else {
        out.push(`<p class="mb-1">${ln}</p>`);
      }
    }
    if (inList) out.push('</ul>');
    return out.join('');
  }
  // What N-Pro is doing right now, in a journalist's words. Never "thinking".
  function aiTyping(label) {
    clearTyping();
    const w = document.createElement('div');
    w.id = 'npro-typing';
    w.className = 'flex justify-start';
    w.innerHTML = `<div class="bg-white border border-line rounded-2xl px-4 py-3 text-[13px] text-sub flex items-center gap-2">${stepIcon('run')}<span>${esc(label || 'Working on it')}</span></div>`;
    thread().appendChild(w); scrollDown();
  }
  function clearTyping() { const t = document.getElementById('npro-typing'); if (t) t.remove(); }

  // ── retrieval + summary ─────────────────────────────────────────────────────
  function applyRetrieval(data) {
    clearTyping();
    S.topic = data.topic || S.topic;
    S.retrieved = data.retrieved || [];
    S.usedAngles = []; S.seenUrls = new Set(); S.seenTitles = [];
    S.retrieved.forEach(a => { if (a.url) S.seenUrls.add(a.url); if (a.title) S.seenTitles.push(a.title); });
    if (data.has_key === false) setTitle(S.topic || 'N-Pro', 'Template mode — add an API key in Ops for live AI scripts');
    else setTitle(S.topic || 'N-Pro', `${S.retrieved.length} reports pulled · Editorial Intelligence Engine`);
    pushHistory(S.topic);
    const n = S.retrieved.length;
    // the brief is written while the editor is already free to pick a format
    const card = msgAI(`${srcLine(n)}<div data-brief class="text-sub text-[13px] flex items-center gap-2">${stepIcon('run')}<span>${n ? 'Reading the reports and writing your brief' : 'No fresh reporting could be pulled'}</span></div>`);
    askFormat();
    loadIntel();
    S.pinning = loadBrief(card.querySelector('[data-brief]'), S.topic);
  }

  async function loadBrief(slot, topic) {
    if (!slot) return;
    let summary = '';
    try {
      const d = await postJSON('/api/npro/brief', { topic, story_id: S.storyId, retrieved: S.retrieved });
      summary = d.summary || '';
      if (topic === S.topic && d.triad && !S.triad) {
        // the story is now pinned to who / where / when, with earlier
        // reporting to weigh as background: every format starts from this
        S.triad = d.triad;
        (d.background || []).forEach(it => {
          if (it.url && !S.seenUrls.has(it.url)) { S.seenUrls.add(it.url); S.seenTitles.push(it.title); S.retrieved.push(it); }
        });
      }
    } catch { summary = ''; }
    if (topic !== S.topic) return;            // the editor has moved on
    slot.className = '';
    slot.innerHTML = summary ? mdlite(summary)
      : '<span class="text-sub">The brief could not be written just now. You can still build a script.</span>';
    if (summary) S.convo.push({ role: 'assistant', content: summary });
  }

  function srcAge(iso) {
    if (!iso) return '';
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 60) return `${mins}m ago`;
    if (mins < 2880) return `${Math.floor(mins / 60)}h ago`;
    return `${Math.floor(mins / 1440)}d ago`;
  }

  // Sources behind an answer, newest first, each stamped with its age — so an
  // older report is never mistaken for a new development.
  function srcLine(n) {
    if (!n) return '';
    const items = (S.retrieved || []).slice(0, 8);
    const newest = items[0] && items[0].published_at ? ` · newest ${srcAge(items[0].published_at)}` : '';
    const rows = items.map(a => {
      const old = a.published_at && (Date.now() - new Date(a.published_at).getTime()) > 86400000;
      return `<li class="flex gap-2"><span class="shrink-0 w-[58px] ${old ? 'text-amber6' : 'text-green6'} font-semibold">${srcAge(a.published_at) || '—'}</span>
        <span class="min-w-0"><a href="${esc(a.url)}" target="_blank" class="hover:underline">${esc(a.title)}</a> <span class="text-sub">${esc(a.publisher || '')}</span></span></li>`;
    }).join('');
    return `<details class="mb-2 text-[11.5px]"><summary class="text-sub cursor-pointer select-none">Pulled ${n} report${n === 1 ? '' : 's'}${newest} · sources, for editorial reference</summary>
      <ul class="mt-1.5 space-y-1">${rows}</ul></details>`;
  }

  // ── format menu ─────────────────────────────────────────────────────────────
  function askFormat() {
    const btns = (meta?.formats || []).map(f =>
      `<button onclick="NPro.pickFormat('${f.id}')" class="flex items-center gap-2 border border-line rounded-xl px-3.5 py-2.5 text-left hover:border-ink hover:bg-paper transition-colors">
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
      `<button data-fid="${esc(S.format)}" data-qi="${S.qIndex}" data-val="${esc(o)}" onclick="NPro.answerEl(this)" class="px-3 py-1.5 rounded-lg text-[13px] font-semibold border border-line bg-white hover:border-ink">${esc(o)}</button>`).join('');
    const custom = allowCustom ? `
      <button onclick="NPro.showCustom(this)" class="px-3 py-1.5 rounded-lg text-[13px] font-semibold border border-dashed border-line bg-white hover:border-ink">Custom…</button>
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
          <button onclick="NPro.addGuest(this)" class="flex-1 border border-line rounded-lg px-3 py-2 text-[13px] font-semibold hover:border-ink">＋ Add guest</button>
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
  // One line of the work log: what was done, and how it went.
  function stepIcon(state) {
    if (state === 'ok') return '<span class="text-green6 font-bold shrink-0 w-3.5 text-center">✓</span>';
    if (state === 'warn') return '<span class="text-amber6 font-bold shrink-0 w-3.5 text-center">!</span>';
    return '<span class="shrink-0 w-3.5 flex justify-center"><span class="w-1.5 h-1.5 rounded-full bg-accent animate-pulse"></span></span>';
  }

  // The work card: a running log of the real steps, with the copy appearing
  // underneath as it is written.
  function workCard() {
    clearTyping();
    const el = bubble('ai', `<div data-steps class="space-y-1 text-[12.5px]"></div>
      <div data-live class="npro-script hidden mt-3 pt-3 border-t border-line text-[13.5px]" style="white-space:pre-wrap"></div>`);
    return { el, stepsEl: el.querySelector('[data-steps]'), live: el.querySelector('[data-live]'), rows: {}, text: '' };
  }

  function setStep(card, ev) {
    let row = card.rows[ev.id];
    if (!row) {
      row = document.createElement('div');
      row.className = 'flex items-start gap-2';
      card.stepsEl.appendChild(row);
      card.rows[ev.id] = row;
    }
    row.innerHTML = `${stepIcon(ev.state)}<span class="${ev.state === 'run' ? 'text-ink' : 'text-sub'}">${esc(ev.label)}</span>`;
    scrollDown();
  }

  function liveText(card, delta) {
    card.text += delta;
    const cutAt = card.text.search(/^\s*PRODUCER NOTES\s*:/m);      // notes are not copy
    card.live.classList.remove('hidden');
    card.live.innerHTML = fmtScript(cutAt >= 0 ? card.text.slice(0, cutAt) : card.text);
    const t = thread();
    if (t.scrollHeight - t.scrollTop - t.clientHeight < 160) scrollDown();
  }

  // When the script is ready the log folds away; it stays one click from view.
  function foldSteps(card) {
    const n = Object.keys(card.rows).length;
    if (!n) { card.el.remove(); return; }
    card.live.remove();
    const d = document.createElement('details');
    d.className = 'text-[12px]';
    d.innerHTML = `<summary class="text-sub cursor-pointer select-none">How this was put together · ${n} step${n === 1 ? '' : 's'}</summary>`;
    card.stepsEl.classList.add('mt-2');
    card.stepsEl.replaceWith(d);
    d.appendChild(card.stepsEl);
  }

  // Run a streamed job. Returns the final event, or throws.
  async function runStream(url, body, card) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const type = res.headers.get('content-type') || '';
    if (!res.ok || !type.includes('ndjson')) {
      let data = {};
      try { data = await res.json(); } catch {}
      if (data.limit_reached) {
        card.el.remove();
        msgAI(esc(data.error || 'Demo limit reached.'));
        throw Object.assign(new Error('demo limit'), { limit: true });
      }
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    let final = null;
    const handle = ev => {
      if (ev.t === 'step') setStep(card, ev);
      else if (ev.t === 'delta') liveText(card, ev.text);
      else if (ev.t === 'reset') { card.text = ''; card.live.innerHTML = ''; }
      else if (ev.t === 'done') final = ev;
    };
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) handle(JSON.parse(line));
      }
    }
    if (buf.trim()) handle(JSON.parse(buf));
    if (!final) throw new Error('stream ended early');
    return final;
  }

  async function doGenerate() {
    const body = { story_id: S.storyId, topic: S.topic, format: S.format, params: S.params, retrieved: S.retrieved, note: S.note, triad: S.triad };
    const card = workCard();
    if (S.pinning && !S.triad) {
      // the story is still being pinned to who / where / when: let that land
      // (a few seconds at most) so the script starts from checked context
      setStep(card, { id: 'triad', state: 'run', label: 'Pinning down who, where and when' });
      await Promise.race([S.pinning, new Promise(r => setTimeout(r, 9000))]);
      body.triad = S.triad; body.retrieved = S.retrieved;
      if (S.triad) card.rows.triad.remove(), delete card.rows.triad;
    }
    let res;
    try {
      res = await runStream('/api/npro/generate/stream', body, card);
    } catch (e) {
      if (e.limit) return;
      // the stream did not hold: fall back to the plain request
      setStep(card, { id: 'write', state: 'run', label: 'Writing the script' });
      try { res = await postJSON('/api/npro/generate', body); }
      catch (e2) { card.el.remove(); return e2.limit ? null : msgAI('The script could not be written. Try again.'); }
    }
    foldSteps(card);
    if (!res.ok) return msgAI(esc(res.error || 'Could not generate.'));
    S.lastScript = res.script;
    S.lastFormat = res.format || S.format;
    if (res.triad) S.triad = res.triad;
    S.recordId = res.record_id || '';
    S.convo.push({ role: 'assistant', content: res.script || '' });
    renderScript(res.script, res.model, res.notes, res.checks, res.record_id);
    renderRecords();
  }

  // What the checks found, in one line under the script.
  function checksLine(c) {
    if (!c) return '';
    const ok = c.status === 'passed';
    const tone = ok ? 'text-green6' : 'text-amber6';
    const head = ok ? `Checked against ${c.sources} report${c.sources === 1 ? '' : 's'}` : 'Needs a producer’s eye before air';
    return `<div class="mt-3 text-[11.5px] ${tone}"><span class="font-semibold">${ok ? '✓' : '!'} ${head}</span>
      <span class="text-sub"> · ${c.lines.map(esc).join(' ')}</span></div>`;
  }

  // Producer notes sit apart from the copy, so nothing here is read on air.
  function notesBox(notes) {
    if (!notes || !notes.length) return '';
    return `<div class="mt-3 border border-line rounded-xl px-3.5 py-2.5 bg-paper">
      <p class="text-[10.5px] font-bold uppercase tracking-widest text-amber6 mb-1.5">For the producer · not for air</p>
      <ul class="space-y-1 text-[12.5px]">${notes.map(n => `<li class="flex gap-2"><span class="text-sub shrink-0">•</span><span>${esc(n)}</span></li>`).join('')}</ul></div>`;
  }

  function renderScript(script, model, notes, checks, recordId) {
    const badge = model === 'mock'
      ? '<span class="bg-blue1 text-blue8 text-[10px] font-bold px-2 py-0.5 rounded">TEMPLATE</span>'
      : '<span class="bg-amber1 text-amber8 text-[10px] font-bold px-2 py-0.5 rounded">AI DRAFT — REVIEW</span>';
    const chips = (meta?.actions || []).map(a =>
      `<button onclick="NPro.action('${a.id}', this)" class="px-2.5 py-1 rounded-lg text-[12px] font-medium border border-line bg-white hover:border-ink">${esc(a.label)}</button>`).join('');
    const w = msgAI(`
      <div class="flex items-center gap-2 mb-2">${badge}
        <button onclick="NPro.copyLast(this)" class="ml-auto text-[12px] text-accent font-semibold hover:underline">Copy script</button></div>
      <div class="npro-script text-[13.5px]" style="white-space:pre-wrap">${fmtScript(script)}</div>
      ${notesBox(notes)}${checksLine(checks)}
      ${recordId ? `<p class="mt-1.5 text-[11px] text-sub">Filed in the notebook with its evidence · record ${esc(recordId)}</p>` : ''}
      <div class="mt-3 pt-3 border-t border-line">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2.5">
          <button onclick="NPro.moreContext(this)" class="flex items-center justify-center gap-2 bg-accent text-white rounded-xl px-4 py-2.5 text-[13px] font-semibold hover:opacity-90">Get more context</button>
          <button onclick="NPro.showSourcing(this)" class="flex items-center justify-center gap-2 border border-line rounded-xl px-4 py-2.5 text-[13px] font-semibold hover:border-ink">Show sourcing</button>
        </div>
        <div class="flex flex-wrap gap-1.5">${chips}</div>
      </div>`);
    w.dataset.script = script;
    w.dataset.record = recordId || '';
  }

  function fmtScript(text) {
    return esc(text)
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/^([A-Z][A-Z0-9 ,'’\-\/&()]{2,}:)/gm, '<span class="font-semibold text-ink">$1</span>');
  }

  function copyLast(btn) {
    const card = btn.closest('[data-script]');
    const txt = card ? card.dataset.script : S.lastScript;
    if (txt) navigator.clipboard.writeText(txt);
    const p = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => btn.textContent = p, 1500);
  }

  // ── smart actions ────────────────────────────────────────────────────────────
  async function action(actionId, btn) {
    const holder = btn.closest('[data-script]');
    const content = holder ? holder.dataset.script : S.lastScript;
    if (!content) return;
    const label = (meta.actions.find(a => a.id === actionId) || {}).label || actionId;
    msgUser(label);
    const body = { action: actionId, content, story_id: S.storyId, topic: S.topic, retrieved: S.retrieved, format: S.lastFormat || null, note: S.note, triad: S.triad };
    const card = workCard();
    let res;
    try {
      res = await runStream('/api/npro/action/stream', body, card);
    } catch (e) {
      if (e.limit) return;
      setStep(card, { id: 'write', state: 'run', label: `Working on: ${label.toLowerCase()}` });
      try { res = await postJSON('/api/npro/action', body); }
      catch (e2) { card.el.remove(); return e2.limit ? null : msgAI('That action failed. Try again.'); }
    }
    foldSteps(card);
    if (!res.ok) return msgAI(esc(res.error || 'Action failed.'));
    // treat a full rewrite as a new script (with its own chips); others as a note
    const rewriteish = ['shorter', 'conversational', 'dramatic', 'more_facts', 'history', 'hindi', 'english', 'digital', 'ott'].includes(actionId);
    if (rewriteish) { S.lastScript = res.result; S.recordId = res.record_id || S.recordId; renderScript(res.result, res.model, res.notes, res.checks, res.record_id); renderRecords(); }
    else msgAI(`<div class="npro-script text-[13.5px]" style="white-space:pre-wrap">${fmtScript(res.result)}</div>${notesBox(res.notes)}${checksLine(res.checks)}`);
  }

  // ── sourcing, on request ─────────────────────────────────────────────────────
  // The script carries no outlet names. This is where an editor (or a client
  // who asks) sees what each line rests on: the matching passage from the
  // reports themselves, with the report linked.
  function renderTrace(d) {
    const traces = d.traces || [];
    const rows = traces.map(t => {
      const m = t.matches || [];
      const body = m.length
        ? `<ul class="mt-1 space-y-1">${m.map(x => `<li class="flex gap-2"><span class="text-sub shrink-0">→</span><span>
             ${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener" class="font-semibold hover:underline">${esc(x.publisher || 'Report')}</a>` : `<span class="font-semibold">${esc(x.publisher || 'Report')}</span>`}
             <span class="text-sub">${x.day ? '· ' + esc(x.day) : (x.age ? '· ' + esc(x.age) : '')}${x.role === 'background' ? ' · background' : x.role === 'same' ? ' · this development' : ''}</span>
             <span class="block text-sub">“${esc(x.excerpt)}”</span></span></li>`).join('')}</ul>`
        : '<p class="mt-1 text-amber6">No close match in the reports pulled. Treat this line as unconfirmed until checked.</p>';
      const tag = t.confidence === 'strong' ? (t.corroborated > 1 ? `Backed by ${t.corroborated} reports` : 'Backed by one report')
        : t.confidence === 'partial' ? 'Partly matched' : 'Not matched';
      const tone = t.confidence === 'strong' ? 'text-green6' : 'text-amber6';
      return `<div class="py-2 border-t border-line first:border-t-0 first:pt-0">
        <p class="text-ink">${esc(t.line)}</p>
        <p class="text-[10.5px] font-bold uppercase tracking-widest ${tone} mt-1">${tag}</p>${body}</div>`;
    }).join('');
    const all = (d.sources || []).map(x =>
      `<li><a href="${esc(x.url)}" target="_blank" rel="noopener" class="hover:underline">${esc(x.title)}</a> <span class="text-sub">${esc(x.publisher || '')}${x.day ? ' · ' + esc(x.day) : (x.age ? ' · ' + esc(x.age) : '')}${x.role === 'background' ? ' · background' : ''}${x.full ? '' : ' · headline only'}</span></li>`).join('');
    msgAI(`<p class="text-[10.5px] font-bold uppercase tracking-widest text-accent mb-2">Sourcing · for editorial reference, not for air${d.from_record ? ' · from the notebook, as the reports read when this was written' : ''}</p>
      <div class="text-[12.5px]">${rows || '<p class="text-sub">There is no script on screen to trace yet.</p>'}</div>
      ${all ? `<details class="mt-2 text-[11.5px]"><summary class="text-sub cursor-pointer select-none">All ${d.sources.length} reports this script drew on</summary><ul class="mt-1.5 space-y-1">${all}</ul></details>` : ''}`);
  }

  async function showSourcing(btn) {
    const holder = btn.closest('[data-script]');
    const script = holder ? holder.dataset.script : S.lastScript;
    if (!script) return;
    btn.disabled = true; const orig = btn.innerHTML; btn.innerHTML = 'Matching each line to the reports…';
    let d;
    const record_id = (holder && holder.dataset.record) || '';
    try { d = await postJSON('/api/npro/trace', { script, story_id: S.storyId, topic: S.topic, retrieved: S.retrieved, note: S.note, triad: S.triad, record_id }); }
    catch { btn.disabled = false; btn.innerHTML = orig; return; }
    btn.disabled = false; btn.innerHTML = orig;
    renderTrace(d);
  }

  // ── Get More Context ─────────────────────────────────────────────────────────
  async function moreContext(btn) {
    btn.disabled = true; const orig = btn.innerHTML; btn.innerHTML = 'Looking for a fresh angle…';
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
      `<li class="flex gap-2"><span class="text-sub">•</span><span><a href="${esc(it.url)}" target="_blank" class="hover:underline font-medium">${esc(it.title)}</a> <span class="text-sub text-[11.5px]">${esc(it.publisher || '')}${it.published_at ? ' · ' + srcAge(it.published_at) : ''}</span></span></li>`).join('');
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

  // ── free-form ask → Editorial Intelligence chat ──────────────────────────────
  async function ask(preset) {
    const inp = document.getElementById('npro-input');
    const q = (preset || inp.value || '').trim();
    if (!q) return;
    if (!preset) { inp.value = ''; inp.style.height = 'auto'; }
    hideSuggest();
    if (!preset && looksLikeNote(q)) return takeNote(q);
    msgUser(q);
    const sourcing = /\b(sources?|sourced|attribut|cite|where (did|does|is|was)|who (said|reported)|which (report|outlet|publication)|how do (you|we) know)\b/i.test(q) && (S.lastScript || S.retrieved.length);
    const deskQ = /\b(pick|lead|bulletin|top of the hour|rundown|board|viral|trending|views|rivals?|competitors?|airing|missing|x desk)\b/i.test(q);
    aiTyping(sourcing ? 'Matching that against the reports' : deskQ ? 'Checking the board, the X desk and what rivals are airing' : 'Pulling the latest reports and reading them');
    let data;
    try {
      data = await postJSON('/api/npro/chat', {
        query: q, topic: S.topic, story_id: S.storyId,
        history: S.convo.slice(-10),
        script: S.lastScript || '', retrieved: S.retrieved,
        note: S.note, triad: S.triad, record_id: S.recordId,
      });
    }
    catch (e) { return e.limit ? null : msgAI('I couldn’t reach the desk — try again.'); }
    clearTyping();
    if (data.mode === 'trace') return renderTrace(data);
    S.convo.push({ role: 'user', content: q });
    S.convo.push({ role: 'assistant', content: data.answer || '' });
    if (data.mode === 'story' && (data.retrieved || []).length) {
      // a news topic: adopt it for retrieval/context and offer production
      S.topic = data.topic || q;
      S.retrieved = data.retrieved;
      S.usedAngles = []; S.seenUrls = new Set(); S.seenTitles = [];
      S.retrieved.forEach(a => { if (a.url) S.seenUrls.add(a.url); if (a.title) S.seenTitles.push(a.title); });
      setTitle(S.topic, `${S.retrieved.length} sources · N-Pro`);
      pushHistory(S.topic);
      msgAI(`${srcLine(S.retrieved.length)}${mdlite(data.answer || '')}${formatChips()}`);
      loadIntel();
    } else {
      msgAI(mdlite(data.answer || ''));
    }
  }

  // compact production row appended under a chat answer — lighter than the
  // full format menu, keeps the conversation flowing
  function formatChips() {
    if (!meta?.formats) return '';
    const chips = meta.formats.map(f =>
      `<button onclick="NPro.pickFormat('${f.id}')" class="px-2.5 py-1 rounded-lg text-[12px] font-semibold border border-line bg-white hover:border-ink">${f.icon} ${esc(f.label)}</button>`).join('');
    return `<div class="mt-3 pt-2.5 border-t border-line flex items-center gap-1.5 flex-wrap">
      <span class="text-[11.5px] text-sub font-medium">Produce:</span>${chips}</div>`;
  }

  // ── a reporter's note ────────────────────────────────────────────────────────
  // A note pasted as it arrived (WhatsApp, any Indian language) is translated,
  // pinned to who / where / when, and given only the context that passes that
  // check. From there it is scripted like any other story.
  function looksLikeNote(q) {
    if (/^\s*(note|reporter'?s? note|input)\s*[:\-]/i.test(q)) return true;
    const indic = (q.match(/[ऀ-ൿ؀-ۿ]/g) || []).length;
    if (indic >= 20) return true;
    return q.length >= 220 && !/\?\s*$/.test(q);
  }

  function chipRow(label, items) {
    if (!items || !items.length) return '';
    return `<div class="flex gap-2 items-baseline"><span class="w-[52px] shrink-0 text-[10.5px] font-bold uppercase tracking-widest text-sub">${label}</span>
      <span class="flex flex-wrap gap-1">${items.map(i => `<span class="bg-paper border border-line rounded px-1.5 py-0.5 text-[12px]">${esc(i)}</span>`).join('')}</span></div>`;
  }

  async function takeNote(text) {
    const clean = text.replace(/^\s*(note|reporter'?s? note|input)\s*[:\-]\s*/i, '');
    msgUser(clean.length > 420 ? clean.slice(0, 420) + '…' : clean);
    reset(null, '');
    const card = workCard();
    let d;
    try { d = await runStream('/api/npro/note', { text: clean }, card); }
    catch (e) { if (e.limit) return; card.el.remove(); return msgAI(esc(e.message && e.message.includes('valid') ? 'That note is too short to work from. Paste the full note.' : 'I couldn’t take the note in just now. Try again.')); }
    foldSteps(card);
    if (!d.ok) return msgAI(esc(d.error || 'The note could not be read.'));
    S.topic = d.topic || ''; S.note = d.note; S.triad = d.triad || null;
    S.retrieved = d.retrieved || [];
    S.retrieved.forEach(a => { if (a.url) S.seenUrls.add(a.url); if (a.title) S.seenTitles.push(a.title); });
    setTitle(S.topic || 'Reporter’s note', `From a reporter’s note · ${S.retrieved.length} report${S.retrieved.length === 1 ? '' : 's'} kept for context`);
    pushHistory(S.topic);
    const pin = d.pinned || {};
    const unsure = (d.uncertain || []).map(u => `<li class="flex gap-2"><span class="text-amber6 shrink-0">!</span><span><span class="text-ink">${esc(u.original)}</span> <span class="text-sub">— ${esc(u.reading)}</span></span></li>`).join('');
    const lost = (d.figures_lost || []).length ? `<p class="mt-2 text-[12.5px] text-amber6">Check these figures against the original: ${d.figures_lost.map(esc).join(', ')}</p>` : '';
    const kept = S.retrieved.map(a => `<li class="flex gap-2"><span class="shrink-0 w-[92px] ${a.role === 'background' ? 'text-amber6' : 'text-green6'} font-semibold">${a.role === 'background' ? 'Background' : 'This story'}</span>
      <span class="min-w-0"><a href="${esc(a.url)}" target="_blank" rel="noopener" class="hover:underline">${esc(a.title)}</a> <span class="text-sub">${srcAge(a.published_at)}</span></span></li>`).join('');
    const out = (d.left_out || []).map(a => `<li class="flex gap-2"><span class="shrink-0 w-[92px] text-sub">${esc(a.why)}</span><span class="min-w-0 text-sub">${esc(a.title)}</span></li>`).join('');
    msgAI(`
      <p class="text-[10.5px] font-bold uppercase tracking-widest text-accent mb-1.5">${d.note.translated ? 'Translated from ' + esc(d.note.language || 'the original') : 'Reporter’s note'}</p>
      <p class="text-[13.5px] leading-relaxed" style="white-space:pre-wrap">${esc(d.note.english)}</p>
      ${d.note.translated ? `<details class="mt-1.5 text-[12px]"><summary class="text-sub cursor-pointer select-none">Original note</summary><p class="mt-1 text-sub" style="white-space:pre-wrap">${esc(d.note.original)}</p></details>` : ''}
      ${unsure ? `<div class="mt-2"><p class="text-[10.5px] font-bold uppercase tracking-widest text-amber6 mb-1">Double-check with the reporter</p><ul class="space-y-1 text-[12.5px]">${unsure}</ul></div>` : ''}${lost}
      <div class="mt-3 pt-3 border-t border-line space-y-1.5">
        <p class="text-[10.5px] font-bold uppercase tracking-widest text-sub">This story is pinned to</p>
        ${chipRow('Who', pin.who)}${chipRow('Where', pin.where)}${chipRow('When', pin.when ? [pin.when] : [])}
        ${d.triad ? '' : '<p class="text-[12.5px] text-amber6">The note does not name who or what clearly enough to check context against.</p>'}
      </div>
      <details class="mt-3 text-[12px]"><summary class="text-sub cursor-pointer select-none">Context: ${S.retrieved.length} report${S.retrieved.length === 1 ? '' : 's'} kept${(d.left_out || []).length ? `, ${d.left_out.length} left out` : ''} · for editorial reference</summary>
        <ul class="mt-1.5 space-y-1">${kept || '<li class="text-sub">Nothing else passed the who, where and when check. The script will be written from the note alone, with no background added.</li>'}</ul>
        ${out ? `<p class="mt-2 text-[10.5px] font-bold uppercase tracking-widest text-sub">Left out</p><ul class="mt-1 space-y-1">${out}</ul>` : ''}
      </details>`);
    S.convo.push({ role: 'user', content: 'Reporter’s note: ' + (d.note.english || '') });
    askFormat();
    loadIntel();
  }

  // ── the notebook ─────────────────────────────────────────────────────────────
  // Every script is filed with the note, the reports and the checks it was
  // written from, so any line can be shown to have been accurate when aired.
  async function renderRecords() {
    const el = document.getElementById('npro-saved');
    if (!el) return;
    let d;
    try { const r = await fetch('/api/npro/records'); if (!r.ok) throw 0; d = await r.json(); } catch { return; }
    const rows = d.records || [];
    el.innerHTML = rows.length ? rows.slice(0, 14).map(r =>
      `<button onclick="NPro.openRecord('${esc(r.id)}')" title="${esc(r.id)}" class="block w-full text-left px-2 py-1.5 rounded-lg hover:bg-navy2 text-[12.5px] text-white/80">
         <span class="block truncate">${esc(r.topic || 'Script')}</span>
         <span class="block text-[10.5px] text-white/40">${esc((r.format || '').replace('_', ' '))} · ${srcAge(r.created_at)}</span></button>`).join('')
      : '<p class="px-2 text-white/40 text-[12px]">Nothing filed yet</p>';
  }

  async function openRecord(id) {
    let r;
    try { const res = await fetch('/api/npro/records/' + encodeURIComponent(id)); if (!res.ok) throw 0; r = await res.json(); }
    catch { return msgAI('That record could not be opened.'); }
    S.topic = r.topic || S.topic; S.note = r.note || null; S.triad = r.triad || null;
    S.recordId = r.id; S.lastScript = r.script; S.lastFormat = r.format || null;
    S.retrieved = (r.sources || []).map(x => ({ title: x.title, url: x.url, publisher: x.publisher, published_at: x.published_at, summary: '' }));
    setTitle(S.topic || 'Notebook', `Notebook record ${r.id}`);
    const when = new Date(r.created_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
    const src = (r.sources || []).map(x => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener" class="hover:underline">${esc(x.title)}</a>
      <span class="text-sub">${esc(x.publisher || '')} · ${x.published_at ? new Date(x.published_at).toLocaleDateString('en-IN', { dateStyle: 'medium' }) : 'undated'}${x.role === 'background' ? ' · background' : ''}</span></li>`).join('');
    msgAI(`<p class="text-[10.5px] font-bold uppercase tracking-widest text-accent mb-1.5">Notebook · written ${esc(when)}</p>
      ${r.note ? `<details class="text-[12px] mb-1.5"><summary class="text-sub cursor-pointer select-none">Reporter’s note${r.note.translated ? ' (' + esc(r.note.language || '') + ', with translation)' : ''}</summary>
         <p class="mt-1" style="white-space:pre-wrap">${esc(r.note.english || '')}</p>${r.note.translated ? `<p class="mt-1.5 text-sub" style="white-space:pre-wrap">${esc(r.note.original || '')}</p>` : ''}</details>` : ''}
      <details class="text-[12px]"><summary class="text-sub cursor-pointer select-none">${(r.sources || []).length} report${(r.sources || []).length === 1 ? '' : 's'} on file${(r.left_out || []).length ? `, ${r.left_out.length} left out` : ''}</summary><ul class="mt-1.5 space-y-1">${src || '<li class="text-sub">Written from the note alone.</li>'}</ul></details>`);
    renderScript(r.script, 'claude', r.notes, r.checks, r.id);
  }

  // ── standalone mode (sidebar tab) ────────────────────────────────────────────
  const STARTERS = [
    'Which stories should I pick at the top of the hour?',
    'Which story is likely to go viral?',
    'Which stories are trending on X right now?',
    'Which stories could generate the highest views?',
    'What are rival channels airing right now?',
    'What are competitors covering that we are missing?',
  ];

  async function openStandalone() {
    reset(null, '');
    overlay().classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    thread().innerHTML = '';
    setTitle('N-Pro', 'Editorial Intelligence Engine — ask the desk anything');
    renderRecent(); renderRecords();
    if (!meta) { try { meta = await (await fetch('/api/npro/formats')).json(); } catch {} }
    const chips = STARTERS.map(s =>
      `<button data-q="${esc(s)}" onclick="NPro.ask(this.dataset.q)" class="px-3 py-2 rounded-xl text-[12.5px] font-medium border border-line bg-white hover:border-ink text-left">${esc(s)}</button>`).join('');
    msgAI(`<p class="font-semibold text-ink mb-1">Your editorial board is in session.</p>
      <p class="mb-2.5">Ask me a desk question, name any story to unpack it, or paste a reporter’s note as it arrived, in any Indian language. I’ll translate it, pin down who, where and when, and build the script.</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5">${chips}</div>`);
    document.getElementById('npro-input').focus();
  }

  // ── left rail ──────────────────────────────────────────────────────────────
  function renderRecent() {
    const el = document.getElementById('npro-recent');
    if (!el) return;
    const stories = (typeof StoryDesk !== 'undefined' ? StoryDesk.stories : []).filter(s => !s.picked).slice(0, 8);
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
    const data = await res.json();
    if (data && data.limit_reached) {
      // demo guest ran out of N-Pro requests: say so once, in the thread
      msgAI(esc(data.error || 'Demo limit reached.'));
      throw Object.assign(new Error('demo limit'), { limit: true });
    }
    if (!res.ok && !(data && 'ok' in data)) throw new Error(`HTTP ${res.status}`);
    return data;
  }
  function jattr(s) { return JSON.stringify(String(s)); }

  // ── autocomplete (google-style suggestions above the input) ─────────────────
  let sugIndex = -1;

  function sugPool() {
    const stories = (typeof StoryDesk !== 'undefined' ? StoryDesk.stories : []).slice(0, 20)
      .map(s => 'Unpack: ' + s.title);
    return [...STARTERS, ...S.history, ...stories];
  }

  function showSuggest() {
    const inp = document.getElementById('npro-input');
    const box = document.getElementById('npro-suggest');
    if (!inp || !box) return;
    const q = inp.value.trim().toLowerCase();
    const pool = sugPool();
    const hits = (q
      ? pool.filter(s => s.toLowerCase().includes(q) && s.toLowerCase() !== q)
      : STARTERS
    ).slice(0, 6);
    if (!hits.length) return hideSuggest();
    sugIndex = -1;
    box.innerHTML = hits.map((s, i) =>
      `<button data-i="${i}" data-s="${esc(s)}" onmousedown="event.preventDefault();NPro.pickSuggest(this.dataset.s)"
        class="npro-sug block w-full text-left px-3.5 py-2 text-[13px] hover:bg-paper truncate">${esc(s)}</button>`).join('');
    box.classList.remove('hidden');
  }

  function hideSuggest() {
    const box = document.getElementById('npro-suggest');
    if (box) { box.classList.add('hidden'); box.innerHTML = ''; }
    sugIndex = -1;
  }

  function moveSuggest(dir) {
    const items = [...document.querySelectorAll('#npro-suggest .npro-sug')];
    if (!items.length) return false;
    sugIndex = (sugIndex + dir + items.length) % items.length;
    items.forEach((el, i) => el.classList.toggle('bg-paper', i === sugIndex));
    return true;
  }

  function pickSuggest(text) {
    const inp = document.getElementById('npro-input');
    inp.value = text;
    hideSuggest();
    ask();
  }

  // input: Enter to send, arrows navigate suggestions, auto-grow
  window.addEventListener('DOMContentLoaded', () => {
    const inp = document.getElementById('npro-input');
    if (!inp) return;
    inp.addEventListener('keydown', e => {
      const box = document.getElementById('npro-suggest');
      const open = box && !box.classList.contains('hidden');
      if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault(); moveSuggest(e.key === 'ArrowDown' ? 1 : -1); return;
      }
      if (open && e.key === 'Tab') {
        const sel = document.querySelectorAll('#npro-suggest .npro-sug')[Math.max(sugIndex, 0)];
        if (sel) { e.preventDefault(); inp.value = sel.dataset.s; hideSuggest(); return; }
      }
      if (e.key === 'Escape') { hideSuggest(); return; }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (open && sugIndex >= 0) {
          const sel = document.querySelectorAll('#npro-suggest .npro-sug')[sugIndex];
          if (sel) return pickSuggest(sel.dataset.s);
        }
        ask();
      }
    });
    inp.addEventListener('input', () => {
      inp.style.height = 'auto'; inp.style.height = Math.min(inp.scrollHeight, 128) + 'px';
      showSuggest();
    });
    inp.addEventListener('focus', showSuggest);
    inp.addEventListener('blur', () => setTimeout(hideSuggest, 150));
  });

  return { open, openStandalone, close, toggleIntel, pickFormat, answer, answerEl,
           answerText, showCustom, toggleMulti, finishMulti, addGuest, finishGuests,
           action, moreContext, showSourcing, copyLast, ask, pickSuggest, openRecord, takeNote };
})();
