// "What's on air — by the hour" panel: what each news channel is broadcasting,
// read from its LIVE stream title (not uploaded clips/articles). Shows only the
// current hour by default; hour-band buttons load past hours on demand. Flags
// channels that are BREAKING a story. Data: /api/live-coverage (fast) +
// POST /api/live-coverage/refresh (polls the live streams now).

const LiveCoverage = (() => {
  let data = null;          // last payload
  let selectedKey = null;   // hour_key currently shown
  let autoTriggered = false;

  const CHANNEL_COLORS = {
    'NDTV 24x7': '#D92D20', 'India Today': '#2563EB', 'Times Now': '#7A5AF8',
    'Republic TV': '#DC6803', 'CNN-News18': '#0E9384', 'WION': '#DD2590',
  };
  const accent = c => CHANNEL_COLORS[c] || '#667085';

  function itemLine(it) {
    const tag = it.breaking
      ? '<span class="shrink-0 px-1.5 py-0.5 rounded bg-red6 text-white text-[9.5px] font-bold tracking-wide">BREAKING</span> '
      : '';
    // via: 'ocr' = read off the live player (default), 'x' = channel's own
    // aired-story post — mark the X ones so editors know the provenance
    const via = it.via === 'x'
      ? ' <span class="text-sub text-[10px] font-semibold" title="From the channel\'s X post of the aired segment">𝕏</span>'
      : '';
    return `<li class="text-[13px] leading-snug flex gap-1.5 items-start">
      <span class="text-sub">·</span><span>${tag}${esc(it.headline)}${via}</span></li>`;
  }

  function channelBlock(c) {
    const color = accent(c.channel);
    const brk = c.breaking
      ? '<span class="px-1.5 py-0.5 rounded bg-red1 text-red8 text-[10px] font-bold">🔴 BREAKING</span>'
      : '';
    return `
      <div class="pl-3 py-1" style="border-left:3px solid ${color}">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[12.5px] font-bold" style="color:${color}">${esc(c.channel)}</span>
          <span class="text-[11px] text-sub">${c.count} on air</span>
          ${brk}
        </div>
        <ul class="space-y-0.5">${c.items.map(itemLine).join('')}</ul>
      </div>`;
  }

  function hourButtons() {
    if (!data || !data.hours.length) return '';
    return `<div class="flex items-center gap-1.5 flex-wrap mb-3">` +
      data.hours.map(h => {
        const isNow = h.hour_key === data.current_hour_key;
        const sel = h.hour_key === selectedKey;
        const label = isNow ? `● Now · ${esc(h.label)}` : esc(h.label);
        const brkDot = h.breaking ? '<span class="ml-1 w-1.5 h-1.5 rounded-full bg-red6 inline-block align-middle"></span>' : '';
        return `<button onclick="LiveCoverage.select('${esc(h.hour_key)}')"
          class="px-3 py-1.5 rounded-lg text-[12.5px] font-semibold border ${sel ? 'bg-navy text-white border-navy' : 'bg-white text-sub border-line hover:border-ink'}">
          ${label}${brkDot}</button>`;
      }).join('') + `</div>`;
  }

  function render() {
    const body = document.getElementById('live-cov-body');
    const label = document.getElementById('live-cov-label');
    if (!body) return;

    if (!data || !data.hours.length) {
      body.innerHTML = '<p class="text-sub text-[13.5px] py-6 text-center">Nothing captured on the live streams yet — hit Refresh to read what\'s on air now.</p>';
    } else {
      if (!data.hours.some(h => h.hour_key === selectedKey)) {
        selectedKey = (data.hours.find(h => h.hour_key === data.current_hour_key)
                       || data.hours[0]).hour_key;
      }
      const hour = data.hours.find(h => h.hour_key === selectedKey);
      const isNow = hour.hour_key === data.current_hour_key;
      const heading = `${isNow ? 'On air now' : 'Aired'} · ${esc(hour.label)} <span class="text-sub font-normal">${esc(hour.date)}</span>`;
      body.innerHTML = hourButtons() + `
        <div class="border border-line rounded-xl p-3.5">
          <div class="flex items-center gap-2 mb-2.5">
            <span class="text-[13.5px] font-bold">${heading}</span>
            <span class="ml-auto text-[11.5px] text-sub">${hour.total} on air · ${hour.channels.length} ${hour.channels.length === 1 ? 'channel' : 'channels'}${hour.breaking ? ` · <span class="text-red6 font-semibold">${hour.breaking} breaking</span>` : ''}</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-x-5 gap-y-2">
            ${hour.channels.map(channelBlock).join('')}
          </div>
        </div>`;
    }
    if (label && data && data.generated_at) {
      label.textContent = `Updated ${ageLabel(data.generated_at)}`;
    }
  }

  function select(key) { selectedKey = key; render(); }

  function apply(payload) { data = payload; render(); }

  async function load() {
    try {
      apply(await (await fetch('/api/live-coverage')).json());
      if ((!data.hours || !data.hours.length) && !autoTriggered) {
        autoTriggered = true;
        refresh();
      }
    } catch { /* refresh button still works */ }
  }

  async function refresh() {
    const icon = document.getElementById('live-cov-icon');
    const label = document.getElementById('live-cov-label');
    if (icon) icon.textContent = '…';
    if (label) label.textContent = 'reading live streams…';
    try {
      apply(await (await fetch('/api/live-coverage/refresh', { method: 'POST' })).json());
    } catch {
      if (label) label.textContent = 'refresh failed — try again';
    }
    if (icon) icon.textContent = '⟳';
  }

  // Light auto-refresh while the Story Desk is visible (keyless, no budget).
  function autoTick() {
    const page = document.getElementById('page-stories');
    if (page && !page.classList.contains('hidden') && document.visibilityState === 'visible') {
      // READ-only refresh (GET) — do NOT trigger a poll here, so the auto loop
      // never spends TwtAPI budget. Only the manual Refresh button polls.
      const keepNow = !selectedKey || (data && selectedKey === data.current_hour_key);
      load().then(() => { if (keepNow && data) selectedKey = data.current_hour_key; render(); });
    }
  }

  load();
  setInterval(autoTick, 5 * 60000);

  return { load, refresh, select, render };
})();
