let cachedConfigs = {};

const statusContainer = () => document.getElementById('ftStatus');
const historyBody = () => document.getElementById('ftHistoryBody');
const tradesBody = () => document.getElementById('ftTradesBody');
const chartCanvas = () => document.getElementById('ftChart');

export function initFreqtradeAdapters() {
  const form = document.getElementById('ftConfigForm');
  const selector = document.getElementById('ftStrategySelect');
  if (selector) {
    selector.addEventListener('change', () => loadConfig(selector.value));
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const strategy = selector?.value;
      if (!strategy) return;
      const payload = {
        enabled: false,
        symbol: form.symbol.value,
        timeframe: form.timeframe.value,
        poll_interval_sec: Number(form.poll_interval_sec.value || 0),
        max_position_usd: Number(form.max_position_usd.value || 0),
        use_testnet: form.use_testnet.checked,
        buy_threshold: form.buy_threshold ? Number(form.buy_threshold.value || 0) : undefined,
      };

      const resp = await fetch(`/ft_config/${strategy}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        showToast('Adapter configuration saved', 'success');
        await refreshFreqtradeAdapters();
      } else {
        const msg = await resp.text();
        showToast(`Failed to save config: ${msg}`, 'error');
      }
    });
  }

  const startBtn = document.getElementById('ftStartBtn');
  const stopBtn = document.getElementById('ftStopBtn');
  const helpBtn = document.getElementById('openFtHelp');
  if (startBtn && stopBtn) {
    startBtn.addEventListener('click', () => toggleStrategy(true));
    stopBtn.addEventListener('click', () => toggleStrategy(false));
  }
  if (helpBtn) {
    helpBtn.addEventListener('click', () => {
      showToast('PatternRecognition: wait for CDL High-Wave (-100). Strategy001: EMA20>EMA50 with green HA candle.', 'info');
    });
  }
}

async function toggleStrategy(start) {
  const selector = document.getElementById('ftStrategySelect');
  const strategy = selector?.value;
  if (!strategy) return;
  const endpoint = start ? `/ft_start/${strategy}` : `/ft_stop/${strategy}`;
  try {
    const resp = await fetch(endpoint, { method: 'POST' });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || resp.statusText);
    }
    showToast(`Strategy ${start ? 'started' : 'stopped'}`, 'success');
    await refreshFreqtradeAdapters();
  } catch (err) {
    console.error(err);
    showToast(err.message, 'error');
  }
}

export async function refreshFreqtradeAdapters() {
  await Promise.all([loadConfigs(), loadStatus(), loadHistory(), loadTrades()]);
}

async function loadConfigs() {
  const resp = await fetch('/ft_configs');
  cachedConfigs = await resp.json();
  const selector = document.getElementById('ftStrategySelect');
  if (selector && !selector.value) {
    selector.value = Object.keys(cachedConfigs)[0] || '';
  }
  if (selector?.value) {
    await loadConfig(selector.value);
  }
}

async function loadConfig(strategy) {
  const cfg = cachedConfigs[strategy];
  const form = document.getElementById('ftConfigForm');
  if (!cfg || !form) return;
  form.symbol.value = cfg.symbol || '';
  form.timeframe.value = cfg.timeframe || '';
  form.poll_interval_sec.value = cfg.poll_interval_sec ?? 0;
  form.max_position_usd.value = cfg.max_position_usd ?? 0;
  form.use_testnet.checked = cfg.use_testnet;
  if (form.buy_threshold) {
    form.buy_threshold.value = cfg.buy_threshold ?? 0;
    form.buy_threshold.closest('.form-field')?.classList.toggle('hidden', strategy !== 'pattern_recognition');
  }
  updateConfigSummary(cfg, strategy);
}

function updateConfigSummary(cfg, strategy) {
  const el = document.getElementById('ftConfigSummary');
  if (!el) return;
  const env = cfg.use_testnet ? 'Testnet' : 'Mainnet';
  el.innerHTML = `
    <div class="summary-title">${strategy} configuration</div>
    <div class="summary-grid">
      <div><div class="metric-label">Symbol</div><div class="metric-value">${cfg.symbol}</div></div>
      <div><div class="metric-label">Timeframe</div><div class="metric-value">${cfg.timeframe}</div></div>
      <div><div class="metric-label">Poll every</div><div class="metric-value">${cfg.poll_interval_sec}s</div></div>
    </div>
    <div class="summary-grid">
      <div><div class="metric-label">Max position</div><div class="metric-value">${cfg.max_position_usd} USD</div></div>
      <div><div class="metric-label">Buy threshold</div><div class="metric-value">${cfg.buy_threshold ?? '—'}</div></div>
      <div><div class="metric-label">Environment</div><div class="metric-value">${env}</div></div>
    </div>
  `;
}

async function loadStatus() {
  const resp = await fetch('/ft_status');
  const data = await resp.json();
  const container = statusContainer();
  if (!container) return;
  container.innerHTML = data
    .map(
      (row) => `
        <div class="metric-row">
          <div class="metric-label">${row.strategy} · ${row.symbol}</div>
          <div class="metric-value">${row.price ? row.price.toFixed(4) : '—'}</div>
          <div class="chip ${row.enabled ? 'chip-primary' : 'chip-muted'}">${row.enabled ? 'RUNNING' : 'PAUSED'}</div>
          <div class="chip chip-primary">${row.use_testnet ? 'TESTNET' : 'MAINNET'}</div>
          <div class="chip">Pos: ${row.position}</div>
          <div class="chip">P&L: ${(row.realized_pnl_usd + row.unrealized_pnl_usd).toFixed(2)} USD</div>
        </div>
      `
    )
    .join('');
}

async function loadHistory() {
  const selector = document.getElementById('ftStrategySelect');
  const strategy = selector?.value;
  if (!strategy) return;
  const resp = await fetch(`/ft_history?strategy=${strategy}&limit=200`);
  const rows = await resp.json();
  const body = historyBody();
  if (body) {
    body.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td>${new Date(row.ts).toLocaleString()}</td>
            <td>${row.price.toFixed(4)}</td>
            <td>${row.indicator_a.toFixed(4)}</td>
            <td>${row.indicator_b.toFixed(4)}</td>
          </tr>
        `
      )
      .join('');
  }
  renderChart(rows);
}

async function loadTrades() {
  const selector = document.getElementById('ftStrategySelect');
  const strategy = selector?.value;
  if (!strategy) return;
  const resp = await fetch(`/ft_trades?strategy=${strategy}&limit=50`);
  const rows = await resp.json();
  const body = tradesBody();
  if (!body) return;
  body.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${new Date(row.ts).toLocaleTimeString()}</td>
          <td>${row.side}</td>
          <td>${row.price.toFixed(4)}</td>
          <td>${row.qty.toFixed(5)}</td>
          <td>${row.notional.toFixed(2)}</td>
          <td class="${row.pnl_usd >= 0 ? 'text-success' : 'text-danger'}">${row.pnl_usd.toFixed(2)}</td>
        </tr>
      `
    )
    .join('');
}

function renderChart(rows) {
  const canvas = chartCanvas();
  if (!canvas || !rows.length) return;
  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  const prices = rows.map((r) => r.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const scale = max - min || 1;
  ctx.strokeStyle = '#82d6ff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  prices.forEach((p, idx) => {
    const x = (idx / Math.max(1, prices.length - 1)) * (width - 10) + 5;
    const y = height - ((p - min) / scale) * (height - 10) - 5;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}
