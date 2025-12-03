import { showToast } from './ui.js';

let heatmapCtx = null;
let heatmapChart = null;
let lastConfig = null;

function fmt(v) {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'number') return v.toFixed(2);
  return v;
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

function renderSignal(res) {
  const chip = document.getElementById('liqSignalChip');
  const summary = document.getElementById('liqSignalSummary');
  const clusterCount = document.getElementById('liqClusterCount');
  if (!chip || !summary || !clusterCount) return;

  document.getElementById('liqSymbolLabel').textContent = res.symbol;
  clusterCount.textContent = `${res.cluster_count} pools`;

  if (!res.has_signal || !res.signal) {
    chip.textContent = 'No sweep yet';
    chip.className = 'chip chip-muted';
    summary.textContent = 'Watching clusters for next sweep...';
    ['liqSweepLevel', 'liqConfidenceLabel', 'liqEntryLabel', 'liqStopLabel', 'liqTargetLabel'].forEach(
      (id) => (document.getElementById(id).textContent = '-')
    );
    return;
  }

  const sig = res.signal;
  chip.textContent = `${sig.direction} setup`;
  chip.className = sig.direction === 'LONG' ? 'chip chip-success' : 'chip chip-danger';
  const reclaimText = sig.reclaim_confirmed ? 'reclaimed' : 'waiting reclaim';
  summary.textContent = `${sig.direction} sweep at ${fmt(sig.sweep_level)} — wick reclaim ${reclaimText}`;

  document.getElementById('liqSweepLevel').textContent = fmt(sig.sweep_level);
  document.getElementById('liqConfidenceLabel').textContent = `${(sig.confidence * 100).toFixed(0)}%`;
  document.getElementById('liqEntryLabel').textContent = fmt(sig.entry);
  document.getElementById('liqStopLabel').textContent = fmt(sig.stop_loss);
  document.getElementById('liqTargetLabel').textContent = fmt(sig.take_profit);
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

function wireButtons() {
  const rescanBtn = document.getElementById('liqRescanBtn');
  if (rescanBtn) rescanBtn.addEventListener('click', manualRescan);

  const execBtn = document.getElementById('liqExecuteBtn');
  if (execBtn) execBtn.addEventListener('click', triggerExecute);

  const startBtn = document.getElementById('liqEnableBtn');
  if (startBtn) startBtn.addEventListener('click', () => toggleEnabled(true));

  const stopBtn = document.getElementById('liqDisableBtn');
  if (stopBtn) stopBtn.addEventListener('click', () => toggleEnabled(false));
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
