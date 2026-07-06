// What's on air — hourly rival-TV coverage panel on the Story Desk.
// Reads /api/live-coverage (fast, from stored clips); the Refresh button and a
// light 10-min auto-refresh POST /api/live-coverage/refresh to pull fresh feeds.

const LiveCoverage = (() => {
  let loadedOnce = false;
  let autoTriggered = false;

  // stable accent per channel so the eye can track a channel across hours
  const CHANNEL_COLORS = {
    'NDTV 24x7':  '#D92D20',
    'India Today': '#2563EB',
    'Times Now':  '#7A5AF8',
    'Republic TV': '#DC6803',
    'CNN-News18': '#0E9384',
    'WION':       '#DD2590',
  };
  const FALLBACK = '#667085';

  function accent(channel) { return CHANNEL_COLORS[channel] || FALLBACK; }

  function channelBlock(c) {
    const color = accent(c.channel);
    const extra = c.count > c.titles.length ? `<span class="text-sub text-[11.5px]">+${c.count - c.titles.length} more</span>` : '';
    return `
      <div class="pl-3 py-1" style="border-left:3px solid ${color}">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[12.5px] font-bold" style="color:${color}">${esc(c.channel)}</span>
          <span class="text-[11px] text-sub">${c.count} ${c.count === 1 ? 'clip' : 'clips'}</span>
        </div>
        <ul class="space-y-0.5">
          ${c.titles.map(t => `<li class="text-[13px] leading-snug flex gap-1.5"><span class="text-sub">·</span><span>${esc(t)}</span></li>`).join('')}
        </ul>
        ${extra}
      </div>`;
  }

  function hourBlock(h) {
    return `
      <div class="border border-line rounded-xl p-3.5">
        <div class="flex items-center gap-2 mb-2.5">
          <span class="px-2.5 py-0.5 rounded-md bg-navy text-white text-[12.5px] font-bold">${esc(h.label)}</span>
          <span class="text-[11.5px] text-sub">${esc(h.date)}</span>
          <span class="ml-auto text-[11.5px] text-sub">${h.total} ${h.total === 1 ? 'clip' : 'clips'} · ${h.channels.length} ${h.channels.length === 1 ? 'channel' : 'channels'}</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-x-5 gap-y-2">
          ${h.channels.map(channelBlock).join('')}
        </div>
      </div>`;
  }

  function render(data) {
    const body = document.getElementById('live-cov-body');
    const label = document.getElementById('live-cov-label');
    if (!body) return;
    const hours = (data && data.hours) || [];
    if (!hours.length) {
      body.innerHTML = '<p class="text-sub text-[13.5px] py-6 text-center">No rival coverage captured yet — hit Refresh to pull the latest on-air feeds.</p>';
    } else {
      body.innerHTML = `<div class="space-y-3">${hours.map(hourBlock).join('')}</div>`;
    }
    if (label && data && data.generated_at) {
      label.textContent = `Updated ${ageLabel(data.generated_at)} · ${data.clips_in_window || 0} clips`;
    }
  }

  async function load() {
    try {
      const data = await (await fetch('/api/live-coverage')).json();
      render(data);
      loadedOnce = true;
      // Serverless has no background poller, so a cold table is empty — pull
      // fresh feeds once automatically the first time we find nothing.
      if ((!data.hours || !data.hours.length) && !autoTriggered) {
        autoTriggered = true;
        refresh();
      }
    } catch { /* leave the loading text; refresh button still works */ }
  }

  async function refresh() {
    const icon = document.getElementById('live-cov-icon');
    const label = document.getElementById('live-cov-label');
    if (icon) icon.textContent = '…';
    if (label) label.textContent = 'pulling live feeds…';
    try {
      const res = await fetch('/api/live-coverage/refresh', { method: 'POST' });
      const data = await res.json();
      render(data);
      loadedOnce = true;
    } catch {
      if (label) label.textContent = 'refresh failed — try again';
    }
    if (icon) icon.textContent = '⟳';
  }

  // Light auto-refresh while the Story Desk is visible (keyless YouTube feeds,
  // no API budget). Skips when the tab is hidden or the desk isn't showing.
  function autoTick() {
    const page = document.getElementById('page-stories');
    const visible = page && !page.classList.contains('hidden');
    if (visible && document.visibilityState === 'visible') refresh();
  }

  // Paint from stored clips on load; kick the 10-min auto-refresh loop.
  load();
  setInterval(autoTick, 10 * 60000);

  return { load, refresh, render };
})();
