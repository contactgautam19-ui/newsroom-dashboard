// SSE client: single /api/stream connection routing typed events to panels.
// EventSource auto-reconnects; we also backfill columns on (re)connect.

(() => {
  let source;

  function connect() {
    source = new EventSource('/api/stream');

    source.addEventListener('rundown', e => {
      NewsPanel.render(JSON.parse(e.data));
    });

    source.addEventListener('tweet', e => {
      XPanel.add(JSON.parse(e.data));
    });

    source.addEventListener('velocity_event', e => {
      const ev = JSON.parse(e.data);
      NewsPanel.onVelocity(ev);
      Ticker.add(ev);
    });

    source.addEventListener('system_status', e => {
      Ticker.status(JSON.parse(e.data));
    });

    source.onopen = () => {
      const sys = document.getElementById('sys-status');
      if (sys) sys.textContent = 'live';
    };

    source.onerror = () => {
      const sys = document.getElementById('sys-status');
      if (sys) sys.textContent = 'reconnecting…';
    };
  }

  XPanel.backfill();
  connect();
})();
