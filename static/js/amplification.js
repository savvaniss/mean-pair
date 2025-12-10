import { showToast } from './ui.js';

const statusEl = () => document.getElementById('ampStatus');
const tableBody = () => document.getElementById('ampTableBody');
const formEl = () => document.getElementById('ampConfigForm');
const configSummaryEl = () => document.getElementById('ampConfigSummary');

let latestConfig = null;
let latestSummary = null;

export function initAmplification() {
  const form = formEl();
  if (form) {
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
        conversion_symbol: document.getElementById('ampConversion')?.value || undefined,
        switch_cooldown: Number(document.getElementById('ampCooldown')?.value || 0),
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

  const startBtn = document.getElementById('startAmpBtn');
  const stopBtn = document.getElementById('stopAmpBtn');
  if (startBtn && stopBtn) {
    startBtn.addEventListener('click', () => toggleEngine(true));
    stopBtn.addEventListener('click', () => toggleEngine(false));
  }
}

async function toggleEngine(start) {
  const endpoint = start ? '/amplification/start' : '/amplification/stop';
  const label = start ? 'start' : 'stop';
  try {
    const resp = await fetch(endpoint, { method: 'POST' });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || resp.statusText);
    }
    showToast(`Amplification engine ${label}ed`, 'success');
    await refreshAmplification();
  } catch (err) {
    console.error(err);
    showToast(`Failed to ${label} amplification: ${err.message}`, 'error');
  }
}

export async function refreshAmplification() {
  await loadConfig();
  await loadSummary();
  await loadStatus();
}

async function loadConfig() {
  const resp = await fetch('/amplification/config');
  if (!resp.ok) return;
  const cfg = await resp.json();
  latestConfig = cfg;

  const baseEl = document.getElementById('ampBaseSymbol');
  if (baseEl) baseEl.value = cfg.base_symbol;
  const intervalEl = document.getElementById('ampInterval');
  if (intervalEl) intervalEl.value = cfg.interval;
  const lookbackEl = document.getElementById('ampLookback');
  if (lookbackEl) lookbackEl.value = cfg.lookback_days;
  const symbolsEl = document.getElementById('ampSymbols');
  if (symbolsEl) symbolsEl.value = (cfg.symbols || []).join(',');
  const momentumEl = document.getElementById('ampMomentum');
  if (momentumEl) momentumEl.value = cfg.momentum_window;
  const minBetaEl = document.getElementById('ampMinBeta');
  if (minBetaEl) minBetaEl.value = cfg.min_beta;
  const topEl = document.getElementById('ampTop');
  if (topEl) topEl.value = cfg.suggest_top_n;
  const cooldownEl = document.getElementById('ampCooldown');
  if (cooldownEl) cooldownEl.value = cfg.switch_cooldown ?? 0;
  const conversionEl = document.getElementById('ampConversion');
  if (conversionEl) conversionEl.value = cfg.conversion_symbol || '';

  renderConfigSummary();
}

async function loadSummary() {
  const resp = await fetch('/amplification/summary');
  const el = statusEl();
  const body = tableBody();
  if (!resp.ok || !el || !body) {
    if (el && !resp.ok) el.textContent = 'Failed to load amplification snapshot';
    return;
  }

  const data = await resp.json();
  latestSummary = data;
  const conversionEl = document.getElementById('ampConversion');
  if (conversionEl) conversionEl.value = data.conversion_symbol || '';

  el.innerHTML = `
    <div class="status-chip-row">
      <span class="chip chip-primary">Base: ${data.base}</span>
      <span class="chip">Interval: ${data.interval}</span>
      <span class="chip">Lookback: ${data.lookback_days}d</span>
      <span class="chip">Conversion: ${data.conversion_symbol || 'auto'}</span>
      <span class="chip">Cooldown: ${data.switch_cooldown} bars</span>
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

  renderConfigSummary();
}

async function loadStatus() {
  const resp = await fetch('/amplification/status');
  if (!resp.ok) return;
  const data = await resp.json();
  const el = statusEl();
  if (!el) return;

  const enabledChip = `<span class="chip ${data.enabled ? 'chip-primary' : 'chip-muted'}">Engine: ${
    data.enabled ? 'RUNNING' : 'STOPPED'
  }</span>`;
  const lastGen = data.last_generated_at ? new Date(data.last_generated_at).toLocaleString() : '—';

  el.innerHTML = `
    <div class="status-chip-row">
      ${enabledChip}
      <span class="chip">Base: ${data.base_symbol}</span>
      <span class="chip">Interval: ${data.interval}</span>
      <span class="chip">Lookback: ${data.lookback_days}d</span>
      <span class="chip">Conversion: ${data.conversion_symbol || 'auto'}</span>
      <span class="chip">Cooldown: ${data.switch_cooldown} bars</span>
    </div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Last snapshot</div>
        <div class="metric-value">${lastGen}</div>
      </div>
      <div>
        <div class="metric-label">Latest suggestions</div>
        <div class="metric-value">${(data.latest_suggestions || []).join(', ') || '—'}</div>
      </div>
    </div>
  `;
}

function renderConfigSummary() {
  const container = configSummaryEl();
  if (!container) return;
  const cfg = latestConfig;
  if (!cfg) {
    container.textContent = 'Save amplification settings to see a snapshot here.';
    return;
  }

  container.innerHTML = `
    <div class="summary-title">Saved amplification config</div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Base</div>
        <div class="metric-value">${cfg.base_symbol}</div>
      </div>
      <div>
        <div class="metric-label">Interval</div>
        <div class="metric-value">${cfg.interval}</div>
      </div>
      <div>
        <div class="metric-label">Lookback</div>
        <div class="metric-value">${cfg.lookback_days} days</div>
      </div>
    </div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Alt symbols</div>
        <div class="metric-value">${(cfg.symbols || []).join(', ')}</div>
      </div>
      <div>
        <div class="metric-label">Conversion</div>
        <div class="metric-value">${cfg.conversion_symbol || latestSummary?.conversion_symbol || 'auto'}</div>
      </div>
      <div>
        <div class="metric-label">Cooldown</div>
        <div class="metric-value">${cfg.switch_cooldown || 0} bars</div>
      </div>
    </div>
  `;
}
