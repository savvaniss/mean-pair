import { showToast } from './ui.js';

const statusEl = () => document.getElementById('trendStatus');
const configForm = () => document.getElementById('trendConfigForm');
const historyBody = () => document.getElementById('trendHistoryBody');
const historyStatus = () => document.getElementById('trendHistoryStatus');

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
}

async function loadStatus() {
  const resp = await fetch('/trend_status');
  const data = await resp.json();
  const el = statusEl();
  if (!el) return;
  el.innerHTML = `
    <div class="stat-grid">
      <div><span class="label">Symbol</span><span class="value">${data.symbol || '-'}</span></div>
      <div><span class="label">Price</span><span class="value">${data.price.toFixed(4)}</span></div>
      <div><span class="label">Fast EMA</span><span class="value">${data.fast_ema.toFixed(4)}</span></div>
      <div><span class="label">Slow EMA</span><span class="value">${data.slow_ema.toFixed(4)}</span></div>
      <div><span class="label">ATR</span><span class="value">${data.atr.toFixed(4)}</span></div>
      <div><span class="label">Position</span><span class="value">${data.position}</span></div>
      <div><span class="label">Qty</span><span class="value">${data.qty_asset.toFixed(4)}</span></div>
      <div><span class="label">Quote balance</span><span class="value">${data.quote_balance.toFixed(4)} ${data.quote_asset}</span></div>
      <div><span class="label">PnL (realized)</span><span class="value">${data.realized_pnl_usd.toFixed(2)} USD</span></div>
      <div><span class="label">PnL (unrealized)</span><span class="value">${data.unrealized_pnl_usd.toFixed(2)} USD</span></div>
    </div>
  `;
}

async function loadHistory() {
  const resp = await fetch('/trend_history');
  const rows = await resp.json();
  const body = historyBody();
  const status = historyStatus();
  if (!body || !status) return;

  body.innerHTML = '';
  if (!rows.length) {
    status.textContent = 'No trend data yet';
    return;
  }
  status.textContent = '';
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

export async function refreshTrendFollowing() {
  await Promise.all([loadConfig(), loadStatus(), loadHistory()]);
}
