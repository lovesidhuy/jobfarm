(function() {
  if (window.WebFormMonitor && window.WebFormMonitor.startOrStatus) return;

  // Listen to logs from isolated world and re-log them in the main world
  window.addEventListener('message', (event) => {
    if (event.source !== window || !event.data) return;
    if (event.data.type === 'UFH_ISOLATED_LOG') {
      const { level, text } = event.data;
      const prefixedText = `[IsolatedWorld] ${text}`;
      if (level === 'error') {
        console.error(prefixedText);
      } else if (level === 'warn') {
        console.warn(prefixedText);
      } else {
        console.log(prefixedText);
      }
    }
  });

  window.WebFormMonitor = {
    startOrStatus: async function(payload) {
      return new Promise((resolve) => {
        const requestId = 'req_' + Math.random().toString(36).slice(2, 11);
        const timeout = setTimeout(() => {
          window.removeEventListener('message', responseHandler);
          resolve({ ok: false, error: `Timed out waiting for ${payload && payload.command}` });
        }, 15000);

        function responseHandler(event) {
          if (event.source !== window || !event.data) return;
          if (event.data.type !== 'UFH_RUNNER_RESPONSE') return;
          if (event.data.requestId !== requestId) return;

          clearTimeout(timeout);
          window.removeEventListener('message', responseHandler);
          const { ok, result, error } = event.data;
          resolve({ ok, result, error });
        }

        window.addEventListener('message', responseHandler);
        window.postMessage({
          type: 'UFH_RUNNER_COMMAND',
          requestId,
          command: payload && payload.command,
          options: payload && payload.options ? payload.options : {}
        }, '*');
      });
    }
  };
})();
