let heatmapCtx = null;
let heatmapChart = null;

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
  const tbody = document.querySelector('#candleTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  candles
    .slice()
    .reverse()
    .forEach((c) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${new Date(c.ts).toLocaleTimeString()}</td><td>${fmt(c.open)}</td><td>${fmt(c.high)}</td><td>${fmt(c.low)}</td><td>${fmt(c.close)}</td>`;
      tbody.appendChild(tr);
    });
}

function renderSignal(res) {
  const chip = document.getElementById('signalChip');
  const summary = document.getElementById('signalSummary');
  const clusterCount = document.getElementById('clusterCount');
  if (!chip || !summary || !clusterCount) return;

  document.getElementById('symbolLabel').textContent = res.symbol;
  clusterCount.textContent = `${res.cluster_count} pools`;

  if (!res.has_signal || !res.signal) {
    chip.textContent = 'No sweep yet';
    chip.className = 'chip chip-muted';
    summary.textContent = 'Watching clusters for next sweep...';
    ['sweepLevel', 'confidenceLabel', 'entryLabel', 'stopLabel', 'targetLabel'].forEach(
      (id) => (document.getElementById(id).textContent = '-')
    );
    return;
  }

  const sig = res.signal;
  chip.textContent = `${sig.direction} setup`;
  chip.className = sig.direction === 'LONG' ? 'chip chip-success' : 'chip chip-danger';
  const reclaimText = sig.reclaim_confirmed ? 'reclaimed' : 'waiting reclaim';
  summary.textContent = `${sig.direction} sweep at ${fmt(sig.sweep_level)} — wick reclaim ${reclaimText}`;

  document.getElementById('sweepLevel').textContent = fmt(sig.sweep_level);
  document.getElementById('confidenceLabel').textContent = `${(sig.confidence * 100).toFixed(0)}%`;
  document.getElementById('entryLabel').textContent = fmt(sig.entry);
  document.getElementById('stopLabel').textContent = fmt(sig.stop_loss);
  document.getElementById('targetLabel').textContent = fmt(sig.take_profit);
}

async function fetchStatus(endpoint = '/liquidation/status') {
  const res = await fetch(endpoint);
  if (!res.ok) throw new Error('Failed to load status');
  return res.json();
}

async function refreshStatus() {
  try {
    const res = await fetchStatus();
    renderSignal(res);
    renderHeatmap(res.heatmap);
    renderTable(res.recent_candles || []);
  } catch (err) {
    console.error(err);
  }
}

async function manualRescan() {
  try {
    const res = await fetchStatus('/liquidation/scan');
    renderSignal(res);
    renderHeatmap(res.heatmap);
    renderTable(res.recent_candles || []);
  } catch (err) {
    console.error(err);
  }
}

function wireConfigForm() {
  const form = document.getElementById('configForm');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      symbol: document.getElementById('cfgSymbol').value,
      lookback_candles: Number(document.getElementById('cfgLookback').value),
      cluster_tolerance_bps: Number(document.getElementById('cfgTolerance').value),
      wick_body_ratio: Number(document.getElementById('cfgWickRatio').value),
      risk_reward: Number(document.getElementById('cfgRR').value),
    };
    await fetch('/liquidation/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    await manualRescan();
  });
}

function wireButtons() {
  const btn = document.getElementById('rescanBtn');
  if (btn) {
    btn.addEventListener('click', manualRescan);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  heatmapCtx = document.getElementById('heatmapChart')?.getContext('2d');
  wireConfigForm();
  wireButtons();
  refreshStatus();
  setInterval(refreshStatus, 15000);
});

