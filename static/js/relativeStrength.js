import { showToast } from './ui.js';

const statusEl = () => document.getElementById('rsStatus');
const configForm = () => document.getElementById('rsConfigForm');
const historyBody = () => document.getElementById('rsHistoryBody');
const historyStatus = () => document.getElementById('rsHistoryStatus');

export function initRelativeStrength() {
  const form = configForm();
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const symbols = (form.symbols.value || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    const payload = {
      enabled: false,
      poll_interval_sec: Number(form.poll_interval_sec.value || 0),
      lookback_window: Number(form.lookback_window.value || 0),
      rebalance_interval_sec: Number(form.rebalance_interval_sec.value || 0),
      top_n: Number(form.top_n.value || 0),
      bottom_n: Number(form.bottom_n.value || 0),
      min_rs_gap: Number(form.min_rs_gap.value || 0),
      max_notional_usd: Number(form.max_notional_usd.value || 0),
      use_all_balance: form.use_all_balance.checked,
      symbols,
      use_testnet: form.use_testnet.checked,
    };

    const resp = await fetch('/rs_config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      showToast('RS config saved', 'success');
      await refreshRelativeStrength();
    } else {
      const msg = await resp.text();
      showToast(`Failed to save RS config: ${msg}`, 'error');
    }
  });

  const startBtn = document.getElementById('startRSBtn');
  const stopBtn = document.getElementById('stopRSBtn');
  if (startBtn && stopBtn) {
    startBtn.addEventListener('click', () => toggleRS(true));
    stopBtn.addEventListener('click', () => toggleRS(false));
  }
}

async function toggleRS(start) {
  const endpoint = start ? '/rs_start' : '/rs_stop';
  const label = start ? 'start' : 'stop';
  try {
    const resp = await fetch(endpoint, { method: 'POST' });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || resp.statusText);
    }
    showToast(`Relative strength bot ${label}ed`, 'success');
    await refreshRelativeStrength();
  } catch (err) {
    console.error(err);
    showToast(`Failed to ${label} RS bot: ${err.message}`, 'error');
  }
}

async function loadConfig() {
  const resp = await fetch('/rs_config');
  if (!resp.ok) return;
  const cfg = await resp.json();
  const form = configForm();
  if (!form) return;

  form.poll_interval_sec.value = cfg.poll_interval_sec;
  form.lookback_window.value = cfg.lookback_window;
  form.rebalance_interval_sec.value = cfg.rebalance_interval_sec;
  form.top_n.value = cfg.top_n;
  form.bottom_n.value = cfg.bottom_n;
  form.min_rs_gap.value = cfg.min_rs_gap;
  form.max_notional_usd.value = cfg.max_notional_usd;
  form.use_all_balance.checked = cfg.use_all_balance;
  form.use_testnet.checked = cfg.use_testnet;
  form.symbols.value = (cfg.symbols || []).join(',');
}

function renderBucket(title, scores) {
  if (!scores.length) return `<div class="help-text">No ${title.toLowerCase()} signals yet</div>`;
  return `
    <div class="bucket">
      <div class="bucket-title">${title}</div>
      <ul>
        ${scores
          .map(
            (s) => `
              <li>
                <span class="chip">${s.symbol}</span>
                <span class="mono">RS ${s.rs.toFixed(3)}</span>
                <span class="mono">${s.price.toFixed(4)}</span>
              </li>`
          )
          .join('')}
      </ul>
    </div>`;
}

async function loadStatus() {
  const resp = await fetch('/rs_status');
  if (!resp.ok) return;
  const data = await resp.json();
  const el = statusEl();
  if (!el) return;

  const envChip = `<span class="chip chip-primary">${data.use_testnet ? 'TESTNET' : 'MAINNET'}</span>`;
  const botChip = `<span class="chip ${data.enabled ? 'chip-primary' : 'chip-muted'}">RS Bot: ${data.enabled ? 'RUNNING' : 'STOPPED'}</span>`;
  const balance = (data.quote_balance ?? 0).toFixed(4);
  const lastRebalance = data.last_rebalance || '—';

  const spreads = data.active_spreads || [];
  const spreadRows = spreads
    .map(
      (sp) => `
        <tr>
          <td>${sp.long}</td>
          <td>${sp.short}</td>
          <td>${sp.rs_gap.toFixed(3)}</td>
          <td>${sp.notional_usd.toFixed(2)}</td>
        </tr>`
    )
    .join('');

  el.innerHTML = `
    <div class="status-chip-row">
      ${envChip}
      ${botChip}
      <span class="chip">Lookback: ${data.lookback_window}</span>
      <span class="chip">Rebalance: ${data.rebalance_interval_sec}s</span>
      <span class="chip">Quote balance (${data.quote_asset}): ${balance}</span>
    </div>
    <div class="bucket-row">
      ${renderBucket('Strongest', data.top_symbols || [])}
      ${renderBucket('Weakest', data.bottom_symbols || [])}
    </div>
    <div class="table-wrapper">
      <div class="table-header">Active spreads (last rebalance: ${lastRebalance})</div>
      <table class="table">
        <thead>
          <tr><th>Long</th><th>Short</th><th>RS gap</th><th>Notional (USD)</th></tr>
        </thead>
        <tbody>
          ${spreadRows || '<tr><td colspan="4">No spreads selected yet</td></tr>'}
        </tbody>
      </table>
    </div>
  `;
}

async function loadHistory() {
  const resp = await fetch('/rs_history');
  if (!resp.ok) return;
  const rows = await resp.json();
  const body = historyBody();
  const status = historyStatus();
  if (!body || !status) return;

  body.innerHTML = '';
  if (!rows.length) {
    status.textContent = 'No RS snapshots yet';
    return;
  }
  status.textContent = '';
  for (const row of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${row.ts}</td>
      <td>${row.symbol}</td>
      <td>${row.price.toFixed(4)}</td>
      <td>${row.rs.toFixed(4)}</td>`;
    body.appendChild(tr);
  }
}

export async function refreshRelativeStrength() {
  await Promise.all([loadConfig(), loadStatus(), loadHistory()]);
}
