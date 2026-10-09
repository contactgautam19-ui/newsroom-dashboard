// Two views over the same engine (app/hyper.py):
//   Social  — what is trending on Google, YouTube and X, side by side
//   Hyper   — the AI production deck built from those trends + the board

const Social = (() => {
  const PLATFORM = {
    google:  { label: 'Google Search', color: '#8FB4E8' },
    youtube: { label: 'YouTube',       color: '#EC4A4D' },
    x:       { label: 'X',             color: '#EEF1F6' },
  };
  let data = null;
  let busy = false;

  const $ = id => document.getElementById(id);
  const vol = n => !n ? '' : n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'
    : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n);

  function column(p) {
    const c = PLATFORM[p];
    const items = (data && data.trends && data.trends[p]) || [];
    const err = data && data.errors && data.errors[p];
    let body;
    if (items.length) {
      body = `<ol class="space-y-0.5">${items.slice(0, 15).map((t, i) => `
        <li class="group flex items-center gap-2 text-[13.5px] leading-snug rounded-lg px-1.5 py-1 -mx-1.5 hover:bg-paper">
          <span class="w-5 shrink-0 text-right text-sub font-mono text-[11.5px]">${i + 1}</span>
          <span class="min-w-0 flex-1 truncate" title="${esc(t.keyword)}">${t.url ? `<a href="${esc(t.url)}" target="_blank" class="hover:underline">${esc(t.keyword)}</a>` : esc(t.keyword)}</span>
          ${t.volume ? `<span class="shrink-0 text-[11.5px] text-sub group-hover:hidden">${vol(t.volume)}</span>` : ''}
          <button data-q="${esc(t.keyword)}" onclick="Social.unpack(this.dataset.q)" title="Unpack in N-Pro"
            class="hidden group-hover:block shrink-0 text-[11px] font-bold px-2 py-0.5 rounded-md bg-navy text-white">N-Pro</button>
        </li>`).join('')}</ol>`;
    } else if (p === 'youtube' && data && data.youtube_configured === false) {
      body = '<p class="text-[12.5px] text-sub">API key needed.</p>';
    } else {
      body = `<p class="text-[12.5px] text-sub">${esc(err || (data ? 'Nothing right now.' : 'Loading…'))}</p>`;
    }
    return `
      <div class="bg-white border border-line rounded-2xl p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="w-2 h-2 rounded-full" style="background:${c.color}"></span>
          <p class="text-[13.5px] font-bold">${c.label}</p>
        </div>${body}
      </div>`;
  }

  function render() {
    $('social-trends').innerHTML = ['google', 'youtube', 'x'].map(column).join('');
    $('social-status').textContent = data && data.scanned_at ? `Updated ${ageLabel(data.scanned_at)}` : '';
  }

  async function scan(manual) {
    if (busy) return;
    busy = true;
    const btn = $('social-scan-btn');
    btn.disabled = true;
    $('social-status').textContent = 'Refreshing…';
    try {
      const d = await (await fetch('/api/hyper/scan' + (manual ? '?force=true' : ''), { method: 'POST' })).json();
      if (d && d.scanned_at) data = d;
    } catch { /* keep what is on screen */ }
    busy = false;
    btn.disabled = false;
    render();
  }

  // Hand a trending topic to N-Pro as a question, so it can be scripted.
  async function unpack(keyword) {
    await NPro.openStandalone();
    NPro.ask(`What's the latest on ${keyword.replace(/^#/, '')}`);
  }

  async function open() {
    if (!data) {
      try {
        const d = await (await fetch('/api/hyper')).json();
        if (d.scan && d.scan.scanned_at) data = d.scan;
      } catch { /* scan below fills it */ }
    }
    render();
    const stale = !data || !data.scanned_at || (Date.now() - new Date(data.scanned_at).getTime()) > 10 * 60000;
    if (stale) scan(false);
  }

  return { open, scan, unpack };
})();


const Hyper = (() => {
  let deck = null;
  const $ = id => document.getElementById(id);

  function card(a) {
    const r = a.populist_resonance_index;
    const tone = r >= 8 ? '#FF8A8A' : r >= 6 ? '#F6C777' : '#B7D0F5';
    const fl = a.compliance_risk_flags || {};
    const risky = fl.communal_risk_level === 'High' || fl.communal_risk_level === 'Medium';
    const story = (a.stories || [])[0];
    const more = [
      (a.narrative_pillars || []).length ? `<ol class="space-y-1 text-ink">${a.narrative_pillars.map((p, i) => `<li class="flex gap-2"><span class="text-sub font-semibold">${i + 1}</span><span>${esc(p)}</span></li>`).join('')}</ol>` : '',
      a.production_directive ? `<p class="text-ink"><b>Do:</b> ${esc(a.production_directive)}</p>` : '',
      a.hyper_local_phrasing ? `<p class="italic">“${esc(a.hyper_local_phrasing)}”</p>` : '',
      a.human_anchor && a.human_anchor.source_element ? `<p><b>Anchor:</b> ${esc(a.human_anchor.source_element)}</p>` : '',
      a.sourcing_asset ? `<p><b>Pull:</b> ${esc(a.sourcing_asset)}</p>` : '',
      fl.notes ? `<p class="text-amber6">${esc(fl.notes)}</p>` : '',
    ].filter(Boolean).join('');
    return `
      <div class="bg-white border border-line rounded-2xl p-4 flex flex-col">
        <div class="flex items-start gap-3">
          <div class="shrink-0 w-11 h-11 rounded-full flex items-center justify-center text-[15px] font-bold" style="background:${tone}14;color:${tone}" title="Resonance, out of 10">${r.toFixed(1)}</div>
          <div class="min-w-0">
            <p class="text-[14.5px] font-bold leading-snug" style="display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden" title="${esc(a.story_vector)}">${esc(a.story_vector)}</p>
            <div class="flex gap-1 flex-wrap mt-1.5">
              ${a.thematic_buckets.map(b => `<span class="text-[10.5px] font-semibold px-1.5 py-0.5 rounded bg-paper border border-line">${esc(b)}</span>`).join('')}
              ${risky ? `<span class="text-[10.5px] font-semibold px-1.5 py-0.5 rounded ${fl.communal_risk_level === 'High' ? 'bg-red1 text-red8' : 'bg-amber1 text-amber8'}">${esc(fl.communal_risk_level)} risk</span>` : ''}
              ${fl.regulatory_review_required ? '<span class="text-[10.5px] font-semibold px-1.5 py-0.5 rounded bg-red1 text-red8">Legal review</span>' : ''}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-2 mt-auto pt-3">
          ${story ? `<button onclick="NPro.open(${Number(story.id)})" class="text-[12.5px] font-semibold px-3.5 py-1.5 rounded-lg bg-navy text-white hover:bg-navy2">Script in N-Pro</button>` : ''}
          ${more ? '<button onclick="this.parentElement.nextElementSibling.classList.toggle(\'hidden\')" class="text-[12.5px] font-semibold px-3.5 py-1.5 rounded-lg border border-line hover:border-ink">Details</button>' : ''}
        </div>
        <div class="hidden text-[12.5px] text-sub space-y-1.5 mt-3 pt-3 border-t border-line">${more}</div>
      </div>`;
  }

  function render() {
    const el = $('hyper-deck');
    if (!deck || !(deck.alerts || []).length) {
      el.innerHTML = '<div class="bg-white border border-line rounded-2xl p-8 text-center text-sub text-[14px]">No deck yet.</div>';
      $('hyper-status').textContent = '';
      return;
    }
    $('hyper-status').textContent = `Built ${ageLabel(deck.built_at)}`;
    el.innerHTML = `
      <div class="border border-line rounded-2xl px-5 py-5 mb-3">
        <p class="text-[26px] leading-tight" style="font-family:var(--f-display);letter-spacing:-.02em">${esc(deck.deck_headline)}</p>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">${deck.alerts.map(card).join('')}</div>`;
  }

  async function run() {
    const btn = $('hyper-deck-btn');
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = '✦ Running…';
    $('hyper-status').textContent = 'About a minute';
    try {
      const r = await fetch('/api/hyper/deck?force=true', { method: 'POST' });
      const d = await r.json();
      if (r.status === 403) guestNotice();
      else if (d && d.ok) deck = d;
      else Toast.show((d && d.error) || 'Hyper Search could not run.', { ms: 7000 });
    } catch { Toast.show('Hyper Search could not run — try again.'); }
    btn.disabled = false;
    btn.textContent = label;
    render();
  }

  async function open() {
    if (!deck) {
      try {
        const d = await (await fetch('/api/hyper')).json();
        if (d.deck && d.deck.alerts) deck = d.deck;
      } catch { /* empty state */ }
    }
    render();
  }

  return { open, run };
})();
