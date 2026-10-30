// "What's on air — by the hour" panel: what each news channel is broadcasting,
// read from its LIVE stream title (not uploaded clips/articles). Shows only the
// current hour by default; the hour dropdown loads past hours on demand. Flags
// channels that are BREAKING a story. Data: /api/live-coverage (fast) +
// POST /api/live-coverage/refresh (polls the live streams now).
//
// Layout rules, set from Gautam's reference (2026-10-23):
//   · ONE hour control, a dropdown — a row of hour buttons wrapped onto three
//     lines and was most of the clutter.
//   · One headline per ROW, never wrapped: time, headline, chevron. A row that
//     wraps to three lines turns a channel card into a wall of text.
//   · Each channel is its own card with a coloured rule and a count pill.
// Never add a source/provenance marker to these rows (Gautam, twice).

const LiveCoverage = (() => {
  let data = null;          // last payload
  let selectedKey = null;   // hour_key currently shown
  let autoTriggered = false;

  // Channel colours say WHO aired it, never what state it is in. They are
  // deliberately lower-chroma than the semantic set (breaking red #F04452,
  // AI blue #438CFF, live green #22C58B): at full saturation NDTV's red read
  // as "breaking" and India Today's blue read as "AI generated" on every
  // card. Identity is carried by the rule and the name; status is carried by
  // a fill, and only status gets to be saturated.
  const CHANNEL_COLORS = {
    'NDTV 24x7': '#C96E70', 'India Today': '#7C9BC4', 'Times Now': '#9389C4',
    'Republic TV': '#C49A5E', 'CNN-News18': '#5FA99B', 'WION': '#C48BA6',
  };
  const accent = c => CHANNEL_COLORS[c] || '#8C9AB0';

  // Airing time in IST, e.g. "6:12 PM". Falls back to nothing rather than a
  // wrong time if the row predates the `at` field.
  function clock(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    // en-IN renders "7:41 pm"; the desk writes "7:41 PM".
    return d.toLocaleTimeString('en-IN', {
      hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata',
    }).replace(/ /g, ' ').replace(/\b(am|pm)\b/i, m => m.toUpperCase());
  }

  function itemRow(it) {
    const time = clock(it.at);
    const brk = it.breaking
      ? '<span class="shrink-0 px-1.5 py-[1px] rounded bg-red6 text-white text-[9px] font-bold tracking-wide align-middle mr-1.5">BREAKING</span>'
      : '';
    return `
      <button type="button" data-h="${esc(it.headline)}" onclick="LiveCoverage.unpack(this.dataset.h)"
        title="${esc(it.headline)} — open in N-Pro"
        class="group w-full flex items-center gap-3 px-3.5 py-2.5 text-left border-t border-line hover:bg-paper">
        <span class="shrink-0 w-[58px] font-mono text-[11.5px] text-sub tabular-nums">${esc(time)}</span>
        <span class="min-w-0 flex-1 truncate text-[13.5px] leading-snug">${brk}${esc(it.headline)}</span>
        <span class="shrink-0 text-sub text-[15px] leading-none group-hover:text-ink">&rsaquo;</span>
      </button>`;
  }

  function channelCard(c) {
    const color = accent(c.channel);
    return `
      <section class="bg-white border border-line rounded-xl overflow-hidden">
        <header class="flex items-center gap-2.5 px-3.5 py-3">
          <span class="w-[3px] h-5 rounded-full shrink-0" style="background:${color}"></span>
          <span class="text-[15px] font-bold truncate" style="color:${color}">${esc(c.channel)}</span>
          <span class="shrink-0 ml-1 inline-flex items-center gap-1.5 pl-2 pr-2.5 py-0.5 rounded-full bg-paper border border-line">
            <span class="font-mono text-[12px] font-semibold">${c.count}</span>
            <span class="text-[11.5px] text-sub">${c.count === 1 ? 'story' : 'stories'}</span>
          </span>
          ${c.breaking ? '<span class="ml-auto shrink-0 w-1.5 h-1.5 rounded-full bg-red6"></span>' : ''}
        </header>
        ${c.items.map(itemRow).join('')}
      </section>`;
  }

  // One control instead of a wrapping row of hour buttons.
  function hourPicker(hour) {
    const opts = data.hours.map(h => {
      const isNow = h.hour_key === data.current_hour_key;
      const label = `${isNow ? 'NOW · ' : ''}${h.label}${isNow ? '' : ' · ' + h.date}${h.breaking ? ' ●' : ''}`;
      return `<option value="${esc(h.hour_key)}"${h.hour_key === selectedKey ? ' selected' : ''}>${esc(label)}</option>`;
    }).join('');
    return `
      <div class="flex items-center gap-3 flex-wrap mb-3.5">
        <div class="relative inline-flex items-center">
          <span class="absolute left-3 text-sub pointer-events-none text-[13px] leading-none">◷</span>
          <select onchange="LiveCoverage.select(this.value)" aria-label="Hour to show"
            class="appearance-none bg-white border border-line rounded-xl pl-8 pr-8 py-2 text-[13.5px] font-semibold text-ink hover:border-ink focus:outline-none focus:border-ink cursor-pointer">
            ${opts}
          </select>
          <span class="absolute right-3 text-sub pointer-events-none text-[11px] leading-none">▾</span>
        </div>
        <span class="text-[12.5px] text-sub">
          ${hour.total} ${hour.total === 1 ? 'story' : 'stories'}
          <span class="mx-1.5 opacity-50">·</span>
          ${hour.channels.length} ${hour.channels.length === 1 ? 'channel' : 'channels'}
          ${hour.breaking ? `<span class="mx-1.5 opacity-50">·</span><span class="text-red6 font-semibold">${hour.breaking} breaking</span>` : ''}
        </span>
      </div>`;
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
      body.innerHTML = hourPicker(hour) + `
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3.5 items-start">
          ${hour.channels.map(channelCard).join('')}
        </div>`;
    }
    if (label && data && data.generated_at) {
      label.textContent = `Updated ${ageLabel(data.generated_at)}`;
    }
  }

  function select(key) { selectedKey = key; render(); }

  function apply(payload) { data = payload; render(); }

  // Same move as the Social Monitor: hand the headline to N-Pro so the desk
  // can ask what we hold on a story a rival just aired.
  async function unpack(headline) {
    await NPro.openStandalone();
    NPro.ask(`What's the latest on ${headline}`);
  }

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

  return { load, refresh, select, render, unpack };
})();
