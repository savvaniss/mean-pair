import { showToast } from './ui.js';

const statusEl = () => document.getElementById('ampStatus');
const tableBody = () => document.getElementById('ampTableBody');
const formEl = () => document.getElementById('ampConfigForm');

export function initAmplification() {
  const form = formEl();
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const symbols = (document.getElementById('ampSymbols')?.value || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    const payload = {
      base_symbol: document.getElementById('ampBaseSymbol')?.value || undefined,
      interval: document.getElementById('ampInterval')?.value || '1d',
      lookback_days: Number(document.getElementById('ampLookback')?.value || 60),
      symbols,
      momentum_window: Number(document.getElementById('ampMomentum')?.value || 3),
      min_beta: Number(document.getElementById('ampMinBeta')?.value || 1.1),
      suggest_top_n: Number(document.getElementById('ampTop')?.value || 3),
    };

    const resp = await fetch('/amplification/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      showToast('Amplification config saved', 'success');
      await refreshAmplification();
    } else {
      const msg = await resp.text();
      showToast(`Failed to save amplification config: ${msg}`, 'error');
    }
  });
}

export async function refreshAmplification() {
  await loadConfig();
  await loadSummary();
}

async function loadConfig() {
  const resp = await fetch('/amplification/config');
  if (!resp.ok) return;
  const cfg = await resp.json();

  document.getElementById('ampBaseSymbol').value = cfg.base_symbol;
  document.getElementById('ampInterval').value = cfg.interval;
  document.getElementById('ampLookback').value = cfg.lookback_days;
  document.getElementById('ampSymbols').value = (cfg.symbols || []).join(',');
  document.getElementById('ampMomentum').value = cfg.momentum_window;
  document.getElementById('ampMinBeta').value = cfg.min_beta;
  document.getElementById('ampTop').value = cfg.suggest_top_n;
}

async function loadSummary() {
  const resp = await fetch('/amplification/summary');
  const el = statusEl();
  const body = tableBody();
  if (!el || !body) return;

  if (!resp.ok) {
    el.textContent = 'Failed to load amplification snapshot';
    return;
  }

  const data = await resp.json();
  el.innerHTML = `
    <div class="status-chip-row">
      <span class="chip chip-primary">Base: ${data.base}</span>
      <span class="chip">Interval: ${data.interval}</span>
      <span class="chip">Lookback: ${data.lookback_days}d</span>
    </div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Generated</div>
        <div class="metric-value">${new Date(data.generated_at).toLocaleString()}</div>
      </div>
      <div>
        <div class="metric-label">Suggested switches</div>
        <div class="metric-value">${(data.suggestions || []).join(', ') || '—'}</div>
      </div>
    </div>`;

  const rows = data.stats || [];
  body.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${row.symbol}</td>
          <td>${row.beta.toFixed(3)}</td>
          <td>${row.correlation.toFixed(3)}</td>
          <td>${row.up_capture.toFixed(3)}</td>
          <td>${row.down_capture.toFixed(3)}</td>
          <td>${row.sample_size}</td>
        </tr>`
    )
    .join('');
}
