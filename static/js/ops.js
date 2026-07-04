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

    WriterSettings.load();
  }

  const WriterSettings = (() => {
    const INPUT = 'w-full border border-line rounded-lg px-3 py-2 bg-white';

    async function load() {
      const host = document.getElementById('writer-settings');
      if (!host) return;
      let s;
      try { s = await (await fetch('/api/settings')).json(); } catch { return; }
      const keyPlaceholder = s.key_configured
        ? `${esc(s.anthropic_api_key)} — paste to replace`
        : 'Paste your Anthropic API key (sk-ant-…)';
      host.innerHTML = `
        <div>
          <label class="block font-semibold mb-1">Channel name</label>
          <input id="ws-channel" class="${INPUT}" value="${esc(s.channel_name || '')}">
        </div>
        <div>
          <label class="block font-semibold mb-1">Voice &amp; tone description</label>
          <textarea id="ws-voice" rows="3" class="${INPUT}" placeholder="Authoritative but conversational; short sentences; viewer-first; English with common Hindi terms where natural">${esc(s.voice_description || '')}</textarea>
        </div>
        <div>
          <label class="block font-semibold mb-1">Sample articles</label>
          <textarea id="ws-samples" rows="5" class="${INPUT}" placeholder="Paste 1-3 published articles that represent your voice">${esc(s.sample_articles || '')}</textarea>
        </div>
        <div>
          <label class="block font-semibold mb-1">Model</label>
          <select id="ws-model" class="${INPUT}">
            <option value="claude-opus-4-8"${s.writer_model === 'claude-opus-4-8' ? ' selected' : ''}>Opus — best quality</option>
            <option value="claude-sonnet-5"${s.writer_model === 'claude-sonnet-5' ? ' selected' : ''}>Sonnet — faster/cheaper</option>
          </select>
        </div>
        <div>
          <label class="block font-semibold mb-1">Anthropic API key</label>
          <input id="ws-key" type="password" class="${INPUT}" placeholder="${keyPlaceholder}">
        </div>
        <div>
          <button id="ws-save" onclick="Ops.saveSettings(this)" class="px-4 py-2 rounded-xl bg-navy text-white font-semibold hover:bg-navy2">Save settings</button>
        </div>`;
    }

    async function save(btn) {
      const body = {
        channel_name: document.getElementById('ws-channel').value,
        voice_description: document.getElementById('ws-voice').value,
        sample_articles: document.getElementById('ws-samples').value,
        writer_model: document.getElementById('ws-model').value,
        anthropic_api_key: document.getElementById('ws-key').value,
      };
      btn.textContent = 'Saving…';
      try {
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        btn.textContent = 'Saved';
        load();
      } catch {
        btn.textContent = 'Save failed';
      }
      setTimeout(() => { btn.textContent = 'Save settings'; }, 2000);
    }

    return { load, save };
  })();

  function saveSettings(btn) { WriterSettings.save(btn); }

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

  return { load, ingestNow, briefNow, saveSettings };
})();
