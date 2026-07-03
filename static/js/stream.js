// SSE client: one /api/stream connection routes typed events to the pages.

(() => {
  function connect() {
    const source = new EventSource('/api/stream');

    source.addEventListener('rundown', e => {
      StoryDesk.render(JSON.parse(e.data));
    });

    let signalsTimer = null;
    source.addEventListener('tweet', e => {
      XDesk.add(JSON.parse(e.data));
      clearTimeout(signalsTimer);
      signalsTimer = setTimeout(() => XDesk.loadSignals(), 1500);
    });

    source.addEventListener('velocity_event', e => {
      const ev = JSON.parse(e.data);
      setUpdated(`↗ ${ev.hashtag} spiking on X`);
      setTimeout(() => setUpdated('live'), 6000);
      Flash.show('viral', `${ev.hashtag} +${Math.round(ev.velocity_pct)}% — ${ev.story_title || ''}`, ev.story_id);
    });

    source.addEventListener('flash', e => {
      const f = JSON.parse(e.data);
      Flash.show(f.kind, f.title, f.story_id);
    });

    source.addEventListener('system_status', e => {
      const d = JSON.parse(e.data);
      if (d.state === 'ingesting') setUpdated('refreshing stories…');
      else if (d.last_ingest) setUpdated(`updated ${ageLabel(d.last_ingest)}`);
      else if (d.x_error) XDesk.note(d.x_error);
    });

    source.addEventListener('x_refresh', e => {
      XDesk.budget(JSON.parse(e.data));
    });

    source.onopen = () => setUpdated('live');
    source.onerror = () => setUpdated('reconnecting…');
  }
  connect();

  setInterval(() => {
    // keep relative times honest without any server chatter
    const el = document.getElementById('updated');
    if (el?.textContent?.startsWith('updated')) StoryDesk.render();
  }, 60000);
})();
