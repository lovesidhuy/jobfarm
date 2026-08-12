window.addEventListener('BridgeToMonitorRequest', async (event) => {
  const { requestId, payload } = event.detail;

  // Gather your internal extension states or execute internal automation functions safely here
  const internalData = {
    status: "active",
    timestamp: Date.now()
    // Add or map your existing automation metrics here
  };

  // Dispatch the results back to the MAIN world bridge
  window.dispatchEvent(new CustomEvent('MonitorToBridgeResponse', {
    detail: { requestId, data: internalData }
  }));
});
