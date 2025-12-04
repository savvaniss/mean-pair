const toastEl = document.getElementById('toast');
function toast(msg, tone = 'neutral') {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.style.borderColor = tone === 'danger' ? 'rgba(255,107,107,0.6)' : 'var(--border)';
  toastEl.classList.add('show');
  setTimeout(() => toastEl.classList.remove('show'), 2400);
}

function openModal(id) { const m = document.getElementById(id); if (m) m.style.display = 'flex'; }
function closeModal(id) { const m = document.getElementById(id); if (m) m.style.display = 'none'; }

document.querySelectorAll('[data-close]').forEach((btn) => btn.addEventListener('click', () => closeModal(btn.dataset.close)));

async function fetchJson(url, options = {}) {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

let heatmapChart;
function renderHeatmap(clusters) {
  const ctx = document.getElementById('liq-heatmap').getContext('2d');
  const labels = clusters.map((c) => c.level.toFixed(2));
  const longs = clusters.map((c) => c.long_count || 0);
  const shorts = clusters.map((c) => c.short_count || 0);
  if (heatmapChart) heatmapChart.destroy();
  heatmapChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Long liquidity', data: longs, backgroundColor: 'rgba(122,208,255,0.6)' },
        { label: 'Short liquidity', data: shorts, backgroundColor: 'rgba(255,107,107,0.6)' },
      ],
    },
    options: {
      plugins: { legend: { labels: { color: '#f3e8ff' } } },
      scales: {
        x: { ticks: { color: '#c8bfd6', maxRotation: 45, minRotation: 45 } },
        y: { ticks: { color: '#c8bfd6' }, grid: { color: 'rgba(255,255,255,0.08)' } },
      },
    },
  });
}

function renderSummary(signal) {
  const chip = document.getElementById('liq-chip');
  const grid = document.getElementById('liq-summary');
  chip.textContent = signal && signal.signal ? signal.signal : 'No signal';
  grid.innerHTML = '';
  if (!signal) return;
  const items = [
    { label: 'Symbol', value: signal.symbol || '—' },
    { label: 'Confidence', value: signal.confidence ? `${(signal.confidence * 100).toFixed(0)}%` : '—' },
    { label: 'Sweep level', value: signal.sweep_level ? signal.sweep_level.toFixed(4) : '—' },
    { label: 'Entry', value: signal.entry ? signal.entry.toFixed(4) : '—' },
    { label: 'Stop', value: signal.stop ? signal.stop.toFixed(4) : '—' },
    { label: 'Target', value: signal.target ? signal.target.toFixed(4) : '—' },
  ];
  items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'stat';
    card.innerHTML = `<div class="label">${item.label}</div><div class="value">${item.value}</div>`;
    grid.appendChild(card);
  });
}

function renderCandles(candles) {
  const tbody = document.querySelector('#liq-candles tbody');
  tbody.innerHTML = '';
  candles.slice(-60).forEach((c) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${new Date(c[0]).toLocaleTimeString()}</td><td>${Number(c[1]).toFixed(2)}</td><td>${Number(c[2]).toFixed(2)}</td><td>${Number(c[3]).toFixed(2)}</td><td>${Number(c[4]).toFixed(2)}</td>`;
    tbody.appendChild(tr);
  });
}

async function loadStatus() {
  try {
    const data = await fetchJson('/liquidation/status');
    renderSummary(data.latest_signal);
    renderHeatmap(data.latest_clusters || []);
    renderCandles(data.latest_candles || []);
  } catch (err) {
    toast('Unable to load status', 'danger');
  }
}

async function rescan() {
  try {
    await fetchJson('/liquidation/scan', { method: 'POST' });
    toast('Scan refreshed');
    loadStatus();
  } catch (err) {
    toast(err.message || 'Scan failed', 'danger');
  }
}

async function execute() {
  try {
    await fetchJson('/liquidation/execute', { method: 'POST' });
    toast('Execution sent');
  } catch (err) {
    toast(err.message || 'No executable signal', 'danger');
  }
}

async function loadConfig() {
  try {
    const cfg = await fetchJson('/liquidation/status');
    const c = cfg.config || {};
    document.getElementById('liq-symbol').value = c.symbol || '';
    document.getElementById('liq-lookback').value = c.lookback_candles || '';
    document.getElementById('liq-tolerance').value = c.cluster_tolerance_bps || '';
    document.getElementById('liq-wick').value = c.wick_body_ratio || '';
    document.getElementById('liq-rr').value = c.risk_reward || '';
    document.getElementById('liq-poll').value = c.poll_interval_sec || '';
    document.getElementById('liq-notional').value = c.auto_trade_notional_usd || '';
    document.getElementById('liq-auto').checked = !!c.enable_auto_trade;
    document.getElementById('liq-testnet').checked = !!c.use_testnet;
  } catch (err) {
    toast('Unable to load config', 'danger');
  }
}

async function saveConfig(evt) {
  evt.preventDefault();
  const payload = {
    symbol: document.getElementById('liq-symbol').value,
    lookback_candles: Number(document.getElementById('liq-lookback').value),
    cluster_tolerance_bps: Number(document.getElementById('liq-tolerance').value),
    wick_body_ratio: Number(document.getElementById('liq-wick').value),
    risk_reward: Number(document.getElementById('liq-rr').value),
    poll_interval_sec: Number(document.getElementById('liq-poll').value),
    auto_trade_notional_usd: Number(document.getElementById('liq-notional').value),
    enable_auto_trade: document.getElementById('liq-auto').checked,
    use_testnet: document.getElementById('liq-testnet').checked,
  };
  try {
    await fetchJson('/liquidation/config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Config updated');
    closeModal('liq-config-modal');
    loadStatus();
  } catch (err) {
    toast(err.message || 'Config failed', 'danger');
  }
}

function bind() {
  document.getElementById('liq-rescan').addEventListener('click', rescan);
  document.getElementById('liq-execute').addEventListener('click', execute);
  document.getElementById('liq-open-config').addEventListener('click', () => { openModal('liq-config-modal'); loadConfig(); });
  document.getElementById('liq-config-form').addEventListener('submit', saveConfig);
}

document.addEventListener('DOMContentLoaded', () => {
  bind();
  loadStatus();
});
