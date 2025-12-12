const RECONNECT_DELAY_MS = 3000;

export function startDashboardStream(onMessage) {
  let socket = null;
  let reconnectTimer = null;

  const connect = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${protocol}://${window.location.host}/ws/dashboard`);

    socket.onopen = () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage?.(data);
      } catch (err) {
        console.error('Failed to parse streaming payload', err);
      }
    };

    const scheduleReconnect = () => {
      if (reconnectTimer) return;
      reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
    };

    socket.onerror = (err) => {
      console.error('Dashboard stream error', err);
      socket?.close();
    };

    socket.onclose = scheduleReconnect;
  };

  connect();

  return () => {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    socket?.close();
  };
}
