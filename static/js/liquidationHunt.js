import { openOverlay, showToast } from './ui.js';

let heatmapCtx = null;
let heatmapChart = null;
let lastConfig = null;

function fmt(v) {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'number') return v.toFixed(2);
  return v;
}

function pct(value, basis) {
  if (!basis || !isFinite(value)) return '-';
  return `${((value / basis) * 100).toFixed(2)}%`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = value;
  }
}

function renderHeatmap(data) {
  if (!heatmapCtx) return;

  const labels = [...data.long.map((b) => `${b.price}`), ...data.short.map((b) => `${b.price}`)];
  const strengths = [...data.long.map((b) => b.strength), ...data.short.map((b) => b.strength)];
  const colors = [
    ...data.long.map(() => 'rgba(59, 130, 246, 0.65)'),
    ...data.short.map(() => 'rgba(239, 68, 68, 0.65)'),
  ];

  if (!heatmapChart) {
    heatmapChart = new Chart(heatmapCtx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            data: strengths,
            backgroundColor: colors,
            borderWidth: 0,
          },
        ],
      },
      options: {
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => `Strength ${(ctx.raw * 100).toFixed(1)}%`,
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            max: 1,
            ticks: { callback: (v) => `${(v * 100).toFixed(0)}%` },
          },
        },
      },
    });
  } else {
    heatmapChart.data.labels = labels;
    heatmapChart.data.datasets[0].data = strengths;
    heatmapChart.data.datasets[0].backgroundColor = colors;
    heatmapChart.update();
  }
}

function renderTable(candles) {
  const tbody = document.querySelector('#liqCandleTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  candles
    .slice()
    .reverse()
    .forEach((c) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${new Date(c.ts).toLocaleTimeString()}</td><td>${fmt(c.open)}</td><td>${fmt(
        c.high
      )}</td><td>${fmt(c.low)}</td><td>${fmt(c.close)}</td>`;
      tbody.appendChild(tr);
    });
}

function renderLastExecution(execution) {
  const chip = document.getElementById('liqLastExecution');
  if (!chip) return;
  if (!execution) {
    chip.textContent = 'No trades yet';
    chip.className = 'chip chip-muted';
    return;
  }
  chip.className = 'chip chip-primary';
  chip.textContent = `${execution.side} ${fmt(execution.qty_executed)} @ ${fmt(execution.price_used)} (${execution.reason})`;
}

function renderRiskMath(signal) {
  const stopDistance = Math.abs(signal.entry - signal.stop_loss);
  const targetDistance = Math.abs(signal.take_profit - signal.entry);
  const liveRR = stopDistance > 0 ? (targetDistance / stopDistance).toFixed(2) : '-';

  setText('liqStopDistance', `${fmt(stopDistance)} (${pct(stopDistance, signal.entry)})`);
  setText('liqTargetDistance', `${fmt(targetDistance)} (${pct(targetDistance, signal.entry)})`);
  setText('liqLiveRR', liveRR);
}

function renderPoolSplit(heatmap = { long: [], short: [] }, clusterCount = 0) {
  const row = document.getElementById('liqPoolSplit');
  const countLabel = document.getElementById('liqClusterCount');
  if (!row) return;
  const longCount = heatmap.long?.length || 0;
  const shortCount = heatmap.short?.length || 0;
  const strongest = [...(heatmap.long || []), ...(heatmap.short || [])].sort((a, b) => b.strength - a.strength)[0];
  const strongestText = strongest
    ? `Strongest @ ${fmt(strongest.price)} (${(strongest.strength * 100).toFixed(0)}% intensity)`
    : 'No pools mapped yet';

  row.innerHTML = `
    <span class="chip chip-primary">Long pools: ${longCount}</span>
    <span class="chip chip-danger">Short pools: ${shortCount}</span>
    <span class="chip chip-muted">${strongestText}</span>
  `;

  if (countLabel) {
    countLabel.textContent = `${clusterCount} pools`;
  }
}

function renderSignal(res) {
  const chip = document.getElementById('liqSignalChip');
  const summary = document.getElementById('liqSignalSummary');
  const actionable = document.getElementById('liqActionableSummary');
  const nextStep = document.getElementById('liqNextStep');
  const playbook = document.getElementById('liqPlaybook');
  const clusterCount = document.getElementById('liqClusterCount');
  if (!chip || !summary || !clusterCount) return;

  const cfg = res.config || {};
  const envChip = `<span class="chip chip-primary">${cfg.use_testnet ? 'TESTNET' : 'MAINNET'}</span>`;
  const botChip = `<span class="chip ${cfg.enabled ? 'chip-primary' : 'chip-muted'}">Scanner: ${
    cfg.enabled ? 'RUNNING' : 'STOPPED'
  }</span>`;
  const symbolChip = `<span class="chip">Symbol: ${res.symbol || '-'}</span>`;

  document.getElementById('liqSymbolLabel').textContent = res.symbol;
  clusterCount.textContent = `${res.cluster_count} pools`;

  if (!res.has_signal || !res.signal) {
    chip.textContent = 'No sweep yet';
    chip.className = 'chip chip-muted';
    summary.innerHTML = `
      <div class="status-chip-row">
        ${envChip}
        ${botChip}
        ${symbolChip}
      </div>
      <div class="status-chip-row">
        <span class="chip chip-primary">${res.cluster_count} pools</span>
        <span class="chip chip-muted">Wick ≥ ${fmt(cfg.wick_body_ratio)}x</span>
        <span class="chip chip-muted">RR ${fmt(cfg.risk_reward)}x</span>
      </div>
      <div class="status-line">Watching clusters for next sweep...</div>
      <div class="status-line">We need a wick through liquidity plus a reclaim to light up an entry.</div>
    `;
    if (actionable) {
      actionable.textContent = `Scanner is standing by on ${res.symbol || cfg.symbol} with ${res.cluster_count} pools mapped.`;
    }
    if (nextStep) {
      nextStep.textContent = `Next step: wait for a wick ≥ ${fmt(cfg.wick_body_ratio)}x the candle body that sweeps a cluster.`;
    }
    if (playbook) {
      playbook.textContent = 'No signal detected yet. Once a sweep prints, you will see a narrated setup here.';
    }
    ['liqSweepLevel', 'liqConfidenceLabel', 'liqEntryLabel', 'liqStopLabel', 'liqTargetLabel'].forEach(
      (id) => (document.getElementById(id).textContent = '-')
    );
    ['liqStopDistance', 'liqTargetDistance', 'liqLiveRR'].forEach((id) => setText(id, '-'));
    return;
  }

  const sig = res.signal;
  chip.textContent = `${sig.direction} setup`;
  chip.className = sig.direction === 'LONG' ? 'chip chip-success' : 'chip chip-danger';
  const reclaimText = sig.reclaim_confirmed ? 'reclaimed' : 'waiting reclaim';
  if (actionable) {
    actionable.textContent = `${sig.direction} sweep through ${fmt(sig.sweep_level)} with ${(sig.confidence * 100).toFixed(0)}% confidence.`;
  }
  if (nextStep) {
    nextStep.textContent = sig.reclaim_confirmed
      ? 'Reclaim confirmed — you can trade or let auto-trading handle it.'
      : 'Waiting for candle to close back inside the swept level to confirm the reclaim.';
  }
  if (playbook) {
    playbook.textContent = `Price swept liquidity at ${fmt(sig.sweep_level)} and printed a ${sig.direction} setup. Entry ${fmt(
      sig.entry
    )}, stop ${fmt(sig.stop_loss)}, target ${fmt(sig.take_profit)}.`;
  }
  summary.innerHTML = `
    <div class="status-chip-row">
      ${envChip}
      ${botChip}
      ${symbolChip}
    </div>
    <div class="status-chip-row">
      <span class="chip ${sig.direction === 'LONG' ? 'chip-success' : 'chip-danger'}">${sig.direction} sweep</span>
      <span class="chip chip-primary">${(sig.confidence * 100).toFixed(0)}% confidence</span>
      <span class="chip ${sig.reclaim_confirmed ? 'chip-success' : 'chip-muted'}">${
        sig.reclaim_confirmed ? 'Reclaimed' : reclaimText
      }</span>
      <span class="chip chip-primary">${res.cluster_count} pools</span>
    </div>
    <div class="metric-grid">
      <div class="metric-group">
        <div class="metric-label">Sweep level</div>
        <div class="metric-value">${fmt(sig.sweep_level)}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Entry</div>
        <div class="metric-value">${fmt(sig.entry)}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Stop loss</div>
        <div class="metric-value">${fmt(sig.stop_loss)}</div>
      </div>
      <div class="metric-group">
        <div class="metric-label">Target</div>
        <div class="metric-value">${fmt(sig.take_profit)}</div>
      </div>
    </div>
    <div class="status-line">Sweeped liquidity at <b>${fmt(sig.sweep_level)}</b> with ${(sig.confidence * 100).toFixed(0)}% conviction.</div>
    <div class="status-line">Entry <b>${fmt(sig.entry)}</b> · Stop <b>${fmt(sig.stop_loss)}</b> · Target <b>${fmt(sig.take_profit)}</b></div>
    <div class="status-line">${
      sig.reclaim_confirmed
        ? 'Reclaim confirmed — signal is live.'
        : 'Waiting for the candle to close back inside to confirm the reclaim.'
    }</div>
  `;

  document.getElementById('liqSweepLevel').textContent = fmt(sig.sweep_level);
  document.getElementById('liqConfidenceLabel').textContent = `${(sig.confidence * 100).toFixed(0)}%`;
  document.getElementById('liqEntryLabel').textContent = fmt(sig.entry);
  document.getElementById('liqStopLabel').textContent = fmt(sig.stop_loss);
  document.getElementById('liqTargetLabel').textContent = fmt(sig.take_profit);
  renderRiskMath(sig);
}

function syncConfigForm(cfg) {
  if (!cfg) return;
  lastConfig = cfg;
  const form = document.getElementById('liqConfigForm');
  if (!form) return;
  document.getElementById('liqCfgSymbol').value = cfg.symbol;
  document.getElementById('liqCfgLookback').value = cfg.lookback_candles;
  document.getElementById('liqCfgTolerance').value = cfg.cluster_tolerance_bps;
  document.getElementById('liqCfgWickRatio').value = cfg.wick_body_ratio;
  document.getElementById('liqCfgRR').value = cfg.risk_reward;
  document.getElementById('liqCfgPoll').value = cfg.poll_interval_sec;
  document.getElementById('liqCfgNotional').value = cfg.trade_notional_usd;
  document.getElementById('liqCfgAuto').checked = cfg.auto_trade;
  document.getElementById('liqCfgTestnet').checked = cfg.use_testnet;

  const summary = document.getElementById('liqConfigSummary');
  if (summary) {
    summary.textContent = `Watching ${cfg.symbol} every ${cfg.poll_interval_sec}s; buying $${cfg.trade_notional_usd}` +
      ` with RR ${fmt(cfg.risk_reward)}x on ${cfg.use_testnet ? 'testnet' : 'mainnet'}`;
  }

  const envChip = document.getElementById('liqEnvChip');
  const autoChip = document.getElementById('liqAutoChip');
  const cadenceChip = document.getElementById('liqCadenceChip');
  if (envChip) envChip.textContent = `Environment: ${cfg.use_testnet ? 'Testnet' : 'Mainnet'}`;
  if (autoChip) {
    autoChip.textContent = cfg.auto_trade ? 'Auto-trading: Enabled' : 'Auto-trading: Off';
    autoChip.className = `chip ${cfg.auto_trade ? 'chip-success' : 'chip-muted'}`;
  }
  if (cadenceChip) cadenceChip.textContent = `Polling cadence: every ${cfg.poll_interval_sec}s`;
}

async function fetchStatus(endpoint = '/liquidation/status', options = undefined) {
  const res = await fetch(endpoint, options);
  if (!res.ok) throw new Error('Failed to load status');
  return res.json();
}

async function refreshStatus() {
  const target = document.getElementById('tab-liquidation') || document.getElementById('liqHeatmapChart');
  if (!target) return;
  try {
    const res = await fetchStatus();
    renderSignal(res);
    renderPoolSplit(res.heatmap, res.cluster_count);
    renderHeatmap(res.heatmap);
    renderTable(res.recent_candles || []);
    renderLastExecution(res.last_execution);
    syncConfigForm(res.config);
  } catch (err) {
    console.error(err);
  }
}

async function manualRescan() {
  try {
    const res = await fetchStatus('/liquidation/scan', { method: 'POST' });
    renderSignal(res);
    renderPoolSplit(res.heatmap, res.cluster_count);
    renderHeatmap(res.heatmap);
    renderTable(res.recent_candles || []);
    renderLastExecution(res.last_execution);
    syncConfigForm(res.config);
  } catch (err) {
    console.error(err);
    showToast('Manual rescan failed', 'danger');
  }
}

async function triggerExecute() {
  try {
    const res = await fetch('/liquidation/execute', { method: 'POST' });
    if (!res.ok) throw new Error('Execution failed');
    const body = await res.json();
    showToast(`Executed ${body.side} ${fmt(body.qty_executed)} ${body.symbol}`, 'success');
    await refreshStatus();
  } catch (err) {
    showToast('No executable signal available', 'danger');
  }
}

async function toggleEnabled(enabled) {
  try {
    await fetch('/liquidation/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    showToast(enabled ? 'Scanner started' : 'Scanner stopped', 'info');
    await refreshStatus();
  } catch (err) {
    console.error(err);
    showToast('Unable to update scanner status', 'danger');
  }
}

async function toggleAutoTradeSetting() {
  const nextState = !(lastConfig?.auto_trade);
  try {
    await fetch('/liquidation/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_trade: nextState }),
    });
    showToast(nextState ? 'Auto-trading enabled for liquidation hunts' : 'Auto-trading disabled', 'info');
    await refreshStatus();
  } catch (err) {
    console.error(err);
    showToast('Unable to toggle auto-trading', 'danger');
  }
}

function wireButtons() {
  const rescanBtn = document.getElementById('liqRescanBtn');
  if (rescanBtn) rescanBtn.addEventListener('click', manualRescan);

  const execBtn = document.getElementById('liqExecuteBtn');
  if (execBtn) execBtn.addEventListener('click', triggerExecute);

  const startBtn = document.getElementById('liqEnableBtn');
  if (startBtn) startBtn.addEventListener('click', () => toggleEnabled(true));

  const stopBtn = document.getElementById('liqDisableBtn');
  if (stopBtn) stopBtn.addEventListener('click', () => toggleEnabled(false));

  document.getElementById('liqInlineRescan')?.addEventListener('click', manualRescan);
  document.getElementById('liqInlineExecute')?.addEventListener('click', triggerExecute);
  document.getElementById('liqAutoToggleBtn')?.addEventListener('click', toggleAutoTradeSetting);
  document
    .getElementById('openLiqConfigInlineSecondary')
    ?.addEventListener('click', () => openOverlay('liqConfigOverlay'));
}

function wireConfigForm() {
  const form = document.getElementById('liqConfigForm');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      symbol: document.getElementById('liqCfgSymbol').value,
      lookback_candles: Number(document.getElementById('liqCfgLookback').value),
      cluster_tolerance_bps: Number(document.getElementById('liqCfgTolerance').value),
      wick_body_ratio: Number(document.getElementById('liqCfgWickRatio').value),
      risk_reward: Number(document.getElementById('liqCfgRR').value),
      poll_interval_sec: Number(document.getElementById('liqCfgPoll').value),
      trade_notional_usd: Number(document.getElementById('liqCfgNotional').value),
      auto_trade: document.getElementById('liqCfgAuto').checked,
      use_testnet: document.getElementById('liqCfgTestnet').checked,
      enabled: true,
    };
    try {
      const res = await fetch('/liquidation/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error('Save failed');
      showToast('Liquidation config saved', 'success');
      await manualRescan();
    } catch (err) {
      console.error(err);
      showToast('Unable to save liquidation config', 'danger');
    }
  });
}

export function initLiquidation() {
  const canvas = document.getElementById('liqHeatmapChart');
  if (!canvas) return;
  heatmapCtx = canvas.getContext('2d');
  wireConfigForm();
  wireButtons();
  refreshStatus();
  setInterval(refreshStatus, 15000);
}

export async function refreshLiquidation() {
  await refreshStatus();
}

// Standalone page support
document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('liqHeatmapChart')) {
    initLiquidation();
  }
});
