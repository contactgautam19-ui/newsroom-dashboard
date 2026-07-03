// Ops page: system health, controls, guardrail audit. Producers only.

const Ops = (() => {
  function metric(label, value) {
    return `<div class="bg-white border border-line rounded-xl p-3.5">
      <p class="text-[11.5px] text-sub">${esc(label)}</p>
      <p class="text-[20px] font-semibold mt-0.5">${esc(String(value))}</p>
    </div>`;
  }

  async function load() {
    let d;
    try { d = await (await fetch('/api/ops')).json(); } catch { return; }
    const li = d.last_ingest || {};
    const feedsOk = li.feeds_polled ? `${li.feeds_polled - (li.feeds_failed || 0)} / ${li.feeds_polled}` : '—';
    document.getElementById('ops-metrics').innerHTML = [
      metric('Feeds healthy', feedsOk),
      metric('X API calls left', d.x_budget?.monthly_remaining ?? '—'),
      metric('Briefs today', d.briefs_today ?? 0),
      metric('Handles monitored', d.handles_count ?? '—'),
    ].join('');

    const lc = document.getElementById('ops-lastcycle');
    lc.innerHTML = li.last_ingest ? `
      <p>Ran ${ageLabel(li.last_ingest)} — ${li.new_stories ?? 0} new stories, top score ${li.max_score ?? '—'}</p>
      <p>Keywords searched: ${(li.keywords || []).map(esc).join(', ') || '—'}</p>
      <p>${li.discovery_hits ?? 0} past-hour hits · ${li.dropped_stale ?? 0} stale dropped · ${li.dropped_junk ?? 0} junk dropped · ${li.retired ?? 0} retired</p>
      ${(li.failed_sources || []).length ? `<p>Feeds failing: ${li.failed_sources.map(esc).join(', ')}</p>` : ''}
    ` : '<p>No cycle has run yet this session.</p>';

    document.getElementById('ops-discarded').innerHTML =
      (d.discarded_recent || []).map(t => `
        <div class="border-b border-line pb-1.5">
          <p class="text-ink text-[12.5px]">${esc(t.handle)}: ${esc((t.text || '').slice(0, 90))}</p>
          <p class="text-[11.5px]">↳ ${esc(t.discard_reason || '')}</p>
        </div>`).join('') || '<p>Nothing filtered recently.</p>';

    const sel = document.getElementById('news-interval');
    sel.value = String(d.news_refresh_minutes ?? 10);
  }

  document.getElementById('news-interval').addEventListener('change', async e => {
    await api(`/api/settings/news-refresh?minutes=${e.target.value}`);
  });

  async function ingestNow(btn) {
    btn.textContent = '⟳ running…';
    try { await api('/api/ingest'); } finally {
      setTimeout(() => { btn.textContent = '⟳ Re-rank now'; load(); }, 1200);
    }
  }

  async function briefNow(btn) {
    btn.textContent = '✉ sending…';
    try {
      const r = await api('/api/brief');
      btn.textContent = r.emailed ? '✉ Sent' : '✉ Saved (email off)';
    } finally {
      setTimeout(() => { btn.textContent = '✉ Email brief now'; load(); }, 2500);
    }
  }

  return { load, ingestNow, briefNow };
})();
