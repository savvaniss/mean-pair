import { showToast } from './ui.js';

const statusEl = () => document.getElementById('trendStatus');
const configForm = () => document.getElementById('trendConfigForm');
const historyBody = () => document.getElementById('trendHistoryBody');
const historyStatus = () => document.getElementById('trendHistoryStatus');
const tradesBody = () => document.getElementById('trendTradesBody');
const tradesStatus = () => document.getElementById('trendTradesStatus');
let trendChart = null;
let lastTrendPrice = null;

export function initTrendFollowing() {
  const form = configForm();
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      enabled: false,
      symbol: form.symbol.value,
      poll_interval_sec: Number(form.poll_interval_sec.value || 0),
      fast_window: Number(form.fast_window.value || 0),
      slow_window: Number(form.slow_window.value || 0),
      atr_window: Number(form.atr_window.value || 0),
      atr_stop_mult: Number(form.atr_stop_mult.value || 0),
      max_position_usd: Number(form.max_position_usd.value || 0),
      use_all_balance: form.use_all_balance.checked,
      cooldown_sec: Number(form.cooldown_sec.value || 0),
      use_testnet: form.use_testnet.checked,
    };

    const resp = await fetch('/trend_config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      showToast('Trend config saved', 'success');
      await refreshTrendFollowing();
    } else {
      const msg = await resp.text();
      showToast(`Failed to save trend config: ${msg}`, 'error');
    }
  });

  const startBtn = document.getElementById('startTrendBtn');
  const stopBtn = document.getElementById('stopTrendBtn');
  if (startBtn && stopBtn) {
    startBtn.addEventListener('click', () => toggleTrend(true));
    stopBtn.addEventListener('click', () => toggleTrend(false));
  }
}

async function toggleTrend(start) {
  const endpoint = start ? '/trend_start' : '/trend_stop';
  const label = start ? 'start' : 'stop';
  try {
    const resp = await fetch(endpoint, { method: 'POST' });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || resp.statusText);
    }
    showToast(`Trend bot ${label}ed`, 'success');
    await refreshTrendFollowing();
  } catch (err) {
    console.error(err);
    showToast(`Failed to ${label} trend bot: ${err.message}`, 'error');
  }
}

async function loadConfig() {
  const resp = await fetch('/trend_config');
  const cfg = await resp.json();
  const form = configForm();
  if (!form) return;

  form.symbol.value = cfg.symbol || '';
  form.poll_interval_sec.value = cfg.poll_interval_sec;
  form.fast_window.value = cfg.fast_window;
  form.slow_window.value = cfg.slow_window;
  form.atr_window.value = cfg.atr_window;
  form.atr_stop_mult.value = cfg.atr_stop_mult;
  form.max_position_usd.value = cfg.max_position_usd;
  form.use_all_balance.checked = cfg.use_all_balance;
  form.cooldown_sec.value = cfg.cooldown_sec;
  form.use_testnet.checked = cfg.use_testnet;

  updateTrendConfigSummary(cfg);
}

function updateTrendConfigSummary(cfg) {
  const summary = document.getElementById('trendConfigSummary');
  if (!summary) return;

  if (!cfg) {
    summary.textContent = 'Save a configuration to see a quick snapshot here.';
    return;
  }

  const env = cfg.use_testnet ? 'Testnet' : 'Mainnet';
  const cooldown = Number.isFinite(cfg.cooldown_sec) ? `${cfg.cooldown_sec}s` : '—';
  const notional = Number.isFinite(cfg.max_position_usd) ? `${cfg.max_position_usd} USDT` : 'Not set';

  summary.innerHTML = `
    <div class="summary-title">Saved config snapshot</div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Symbol</div>
        <div class="metric-value">${cfg.symbol || 'Not set'}</div>
      </div>
      <div>
        <div class="metric-label">EMA windows</div>
        <div class="metric-value">${cfg.fast_window} / ${cfg.slow_window}</div>
      </div>
      <div>
        <div class="metric-label">ATR window</div>
        <div class="metric-value">${cfg.atr_window}</div>
      </div>
    </div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Stop mult</div>
        <div class="metric-value">${cfg.atr_stop_mult}</div>
      </div>
      <div>
        <div class="metric-label">Cooldown</div>
        <div class="metric-value">${cooldown}</div>
      </div>
      <div>
        <div class="metric-label">Exposure</div>
        <div class="metric-value">${cfg.use_all_balance ? 'Use full balance' : notional}</div>
      </div>
    </div>
    <div class="chip-row">
      <span class="chip chip-primary">${env}</span>
    </div>
  `;
}

async function loadStatus() {
  const resp = await fetch('/trend_status');
  const data = await resp.json();
  const el = statusEl();
  if (!el) return;

  const botChip = `<span class="chip ${data.enabled ? 'chip-primary' : 'chip-muted'}">Trend Bot: ${
    data.enabled ? 'RUNNING' : 'STOPPED'
  }</span>`;
  const envChip = `<span class="chip chip-primary">${data.use_testnet ? 'TESTNET' : 'MAINNET'}</span>`;
  const symbol = data.symbol || '-';

  const priceDirection =
    data.price !== undefined && data.price !== null && lastTrendPrice !== null
      ? data.price > lastTrendPrice
        ? 'price-up'
        : data.price < lastTrendPrice
          ? 'price-down'
          : 'price-flat'
      : 'price-flat';
  const priceIcon =
    priceDirection === 'price-up' ? '↗' : priceDirection === 'price-down' ? '↘' : '';

  const priceLabel = data.price !== undefined && data.price !== null ? data.price.toFixed(4) : '—';
  const fast = (data.fast_ema ?? 0).toFixed(4);
  const slow = (data.slow_ema ?? 0).toFixed(4);
  const atr = (data.atr ?? 0).toFixed(4);
  const qty = (data.qty_asset ?? 0).toFixed(4);
  const quoteBal = (data.quote_balance ?? 0).toFixed(4);
  const realized = (data.realized_pnl_usd ?? 0).toFixed(2);
  const unrealized = (data.unrealized_pnl_usd ?? 0).toFixed(2);

  el.innerHTML = `
    <div class="status-chip-row">
      ${envChip}
      ${botChip}
      <span class="chip">Symbol: ${symbol}</span>
    </div>

    <div class="metric-grid">
      <div class="metric-group">
        <div class="metric-label">Last price</div>
        <div class="metric-value">
          <span class="price-pill ${priceDirection}">
            ${priceLabel}
            ${priceIcon ? `<span class="price-trend-icon">${priceIcon}</span>` : ''}
          </span>
        </div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Fast EMA</div>
        <div class="metric-value">${fast}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Slow EMA</div>
        <div class="metric-value">${slow}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">ATR</div>
        <div class="metric-value">${atr}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Position</div>
        <div class="metric-value">${data.position}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Base held (${data.base_asset || '—'})</div>
        <div class="metric-value">${qty}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Quote balance (${data.quote_asset || '—'})</div>
        <div class="metric-value">${quoteBal}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Cooldown</div>
        <div class="metric-value">${data.cooldown_sec || 0}s</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">ATR stop (x)</div>
        <div class="metric-value">${(data.atr_stop_mult ?? 0).toFixed(2)}</div>
      </div>
    </div>

    <div class="status-line">
      <b>PnL (realized):</b> ${realized} USD |
      <b>PnL (unrealized):</b> ${unrealized} USD
    </div>
  `;

  if (data.price !== undefined && data.price !== null) {
    lastTrendPrice = data.price;
  }
}

async function loadHistory() {
  const resp = await fetch('/trend_history');
  const rows = await resp.json();
  const body = historyBody();
  const status = historyStatus();
  const chartInfo = document.getElementById('trendChartInfo');
  if (!body || !status) return;

  body.innerHTML = '';
  if (!rows.length) {
    status.textContent = 'No trend snapshots yet';
    if (chartInfo) chartInfo.textContent = '';
    if (trendChart) {
      trendChart.data.labels = [];
      trendChart.data.datasets.forEach((d) => (d.data = []));
      trendChart.update();
    }
    return;
  }
  status.textContent = '';

  const labels = rows.map((r) => new Date(r.ts).toLocaleTimeString());
  const prices = rows.map((r) => r.price);
  const fast = rows.map((r) => r.fast_ema);
  const slow = rows.map((r) => r.slow_ema);
  const atr = rows.map((r) => r.atr);

  const latest = rows[rows.length - 1];
  if (chartInfo && latest) {
    chartInfo.textContent = `Latest → Price ${latest.price.toFixed(4)} | Fast ${latest.fast_ema.toFixed(4)} | Slow ${latest.slow_ema.toFixed(4)} | ATR ${latest.atr.toFixed(4)}`;
  }

  if (!trendChart) {
    const ctx = document.getElementById('trendChart')?.getContext('2d');
    if (ctx) {
      trendChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Price', data: prices, borderWidth: 2, borderColor: '#4bc0c0', fill: false },
            { label: 'Fast EMA', data: fast, borderWidth: 1.5, borderColor: '#90caf9', fill: false },
            { label: 'Slow EMA', data: slow, borderWidth: 1.5, borderColor: '#f48fb1', fill: false },
            { label: 'ATR', data: atr, borderWidth: 1, borderColor: '#ffb74d', fill: false, yAxisID: 'y1' },
          ],
        },
        options: {
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: { labels: { color: '#eee' } } },
          scales: {
            x: { ticks: { color: '#ccc' }, grid: { color: '#333' } },
            y: { ticks: { color: '#ccc' }, grid: { color: '#333' }, title: { display: true, text: 'Price / EMA', color: '#ccc' } },
            y1: {
              position: 'right',
              ticks: { color: '#ccc' },
              grid: { drawOnChartArea: false },
              title: { display: true, text: 'ATR', color: '#ccc' },
            },
          },
        },
      });
    }
  } else {
    trendChart.data.labels = labels;
    trendChart.data.datasets[0].data = prices;
    trendChart.data.datasets[1].data = fast;
    trendChart.data.datasets[2].data = slow;
    trendChart.data.datasets[3].data = atr;
    trendChart.update();
  }

  for (const row of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${row.ts}</td>
      <td>${row.price.toFixed(4)}</td>
      <td>${row.fast_ema.toFixed(4)}</td>
      <td>${row.slow_ema.toFixed(4)}</td>
      <td>${row.atr.toFixed(4)}</td>`;
    body.appendChild(tr);
  }
}

async function loadTrades() {
  const body = tradesBody();
  const status = tradesStatus();
  if (!body || !status) return;

  const resp = await fetch('/trend_trades?limit=100');
  if (!resp.ok) {
    status.textContent = 'Unable to load trades';
    return;
  }

  const rows = await resp.json();
  body.innerHTML = '';
  if (!rows.length) {
    status.textContent = 'No trades yet';
    return;
  }

  status.textContent = '';
  rows.forEach((t) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${new Date(t.ts).toLocaleString()}</td>
      <td>${t.symbol}</td>
      <td>${t.side}</td>
      <td>${t.qty.toFixed(5)}</td>
      <td>${t.price.toFixed(5)}</td>
      <td>${t.notional.toFixed(2)}</td>
      <td>${(t.fee ?? 0).toFixed(2)}</td>
      <td>${t.pnl_usd.toFixed(2)}</td>
      <td>${t.is_testnet ? 'Testnet' : 'Mainnet'}</td>`;
    body.appendChild(tr);
  });
}

export async function refreshTrendFollowing() {
  await Promise.all([loadConfig(), loadStatus(), loadHistory(), loadTrades()]);
}
