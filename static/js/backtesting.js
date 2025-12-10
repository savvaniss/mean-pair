import { showToast } from './ui.js';

const freqtradeOptions = [
  { value: 'pattern_recognition', label: 'Freqtrade – Pattern recognition' },
  { value: 'strategy001', label: 'Freqtrade – Strategy 001' },
  { value: 'strategy002', label: 'Freqtrade – Strategy 002' },
  { value: 'strategy003', label: 'Freqtrade – Strategy 003' },
  { value: 'supertrend', label: 'Freqtrade – Supertrend' },
];

export function initBacktesting() {
  const strategy = document.getElementById('backtestStrategy');
  const form = document.getElementById('backtestForm');
  if (!strategy || !form) return;

  // Populate strategy dropdown dynamically to keep labels centralized
  const baseOptions = [
    { value: 'mean_reversion', label: 'Mean reversion (pair)' },
    { value: 'bollinger', label: 'Bollinger bands' },
    { value: 'trend_following', label: 'Trend following' },
  ];
  for (const opt of [...baseOptions, ...freqtradeOptions]) {
    const el = document.createElement('option');
    el.value = opt.value;
    el.textContent = opt.label;
    strategy.appendChild(el);
  }

  strategy.addEventListener('change', updateVisibleFields);
  updateVisibleFields();

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await runBacktest();
  });
}

export async function runBacktest() {
  const strategy = document.getElementById('backtestStrategy')?.value;
  const symbol = document.getElementById('backtestSymbol')?.value?.trim();
  const assetA = document.getElementById('backtestAssetA')?.value?.trim();
  const assetB = document.getElementById('backtestAssetB')?.value?.trim();
  const interval = document.getElementById('backtestInterval')?.value || '1h';
  const lookbackDays = Number(document.getElementById('backtestLookback')?.value || 14);
  const startDateStr = document.getElementById('backtestStartDate')?.value;
  const endDateStr = document.getElementById('backtestEndDate')?.value;
  const startingBalance = Number(
    document.getElementById('backtestStartingBalance')?.value || 1000
  );
  const windowSize = Number(document.getElementById('backtestWindow')?.value || 70);
  const numStd = Number(document.getElementById('backtestStd')?.value || 3);
  const zEntry = Number(document.getElementById('backtestZEntry')?.value || 3);
  const zExit = Number(document.getElementById('backtestZExit')?.value || 0.4);
  const fastWindow = Number(document.getElementById('backtestFast')?.value || 12);
  const slowWindow = Number(document.getElementById('backtestSlow')?.value || 26);
  const atrWindow = Number(document.getElementById('backtestAtrWindow')?.value || 14);
  const atrStop = Number(document.getElementById('backtestAtrStop')?.value || 2.0);

  if (!strategy) {
    showToast('Please choose a strategy', 'warning');
    return;
  }

  const isFreqtrade = freqtradeOptions.some((opt) => opt.value === strategy);

  if (strategy === 'mean_reversion') {
    if (!assetA || !assetB) {
      showToast('Asset A and Asset B are required for mean reversion backtests.', 'warning');
      return;
    }
    if (windowSize <= 0) {
      showToast('Window size must be greater than 0 for mean reversion.', 'warning');
      return;
    }
  } else if (strategy === 'bollinger') {
    if (!symbol) {
      showToast('Symbol is required for Bollinger backtests.', 'warning');
      return;
    }
    if (windowSize <= 0 || numStd <= 0) {
      showToast('Provide a valid window size and standard deviation for Bollinger.', 'warning');
      return;
    }
  } else if (strategy === 'trend_following') {
    if (!symbol) {
      showToast('Symbol is required for trend-following backtests.', 'warning');
      return;
    }
    if (fastWindow <= 0 || slowWindow <= 0) {
      showToast('Fast and slow EMA windows must be greater than 0.', 'warning');
      return;
    }
  } else if (isFreqtrade) {
    if (!symbol) {
      showToast('Symbol is required for Freqtrade backtests.', 'warning');
      return;
    }
  }

  if ((startDateStr && !endDateStr) || (endDateStr && !startDateStr)) {
    showToast('Please set both start and end dates to run a custom window.', 'warning');
    return;
  }

  const startDate = startDateStr ? new Date(`${startDateStr}T00:00:00Z`).toISOString() : undefined;
  const endDate = endDateStr ? new Date(`${endDateStr}T23:59:59Z`).toISOString() : undefined;

  const payload = {
    strategy,
    symbol: symbol || undefined,
    asset_a: assetA || undefined,
    asset_b: assetB || undefined,
    interval,
    lookback_days: lookbackDays,
    start_date: startDate,
    end_date: endDate,
    starting_balance: startingBalance,
    window_size: windowSize,
    num_std: numStd,
    z_entry: zEntry,
    z_exit: zExit,
    fast_window: fastWindow,
    slow_window: slowWindow,
    atr_window: atrWindow,
    atr_stop_mult: atrStop,
  };

  try {
    const resp = await fetch('/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || 'Request failed');
    }
    const data = await resp.json();
    renderBacktestResult(data);
    showToast('Backtest complete', 'success');
  } catch (err) {
    console.error('Backtest failed', err);
    showToast(`Backtest failed: ${err}`, 'danger');
  }
}

function updateVisibleFields() {
  const strategy = document.getElementById('backtestStrategy')?.value;
  const pairFields = document.getElementById('backtestPairFields');
  const bollFields = document.getElementById('backtestBollFields');
  const trendFields = document.getElementById('backtestTrendFields');
  const symbolRow = document.getElementById('backtestSymbolRow');
  const mrFields = document.getElementById('backtestMrFields');
  const commonFields = document.getElementById('backtestCommonFields');

  const isPair = strategy === 'mean_reversion';
  const isBoll = strategy === 'bollinger';
  const isTrend = strategy === 'trend_following';

  if (pairFields) pairFields.style.display = isPair ? 'flex' : 'none';
  if (symbolRow) symbolRow.style.display = isPair ? 'none' : 'flex';
  if (bollFields) bollFields.style.display = isBoll ? 'flex' : 'none';
  if (trendFields) trendFields.style.display = isTrend ? 'flex' : 'none';
  if (mrFields) mrFields.style.display = isPair ? 'flex' : 'none';
  if (commonFields) commonFields.style.display = 'flex';
}

function renderBacktestResult(result) {
  const summary = document.getElementById('backtestSummary');
  if (!summary) return;

  summary.innerHTML = `
    <div class="metric-grid">
      <div><div class="metric-label">Strategy</div><div class="metric-value">${result.strategy}</div></div>
      <div><div class="metric-label">Final balance</div><div class="metric-value">${result.final_balance.toFixed(2)}</div></div>
      <div><div class="metric-label">Return</div><div class="metric-value">${(result.return_pct * 100).toFixed(2)}%</div></div>
      <div><div class="metric-label">Win rate</div><div class="metric-value">${(result.win_rate * 100).toFixed(1)}%</div></div>
      <div><div class="metric-label">Max drawdown</div><div class="metric-value">${(result.max_drawdown * 100).toFixed(2)}%</div></div>
      <div><div class="metric-label">Period</div><div class="metric-value">${formatDate(result.start)} → ${formatDate(result.end)}</div></div>
    </div>
  `;

  const tradesBody = document.querySelector('#backtestTrades tbody');
  if (tradesBody) {
    tradesBody.innerHTML = '';
    result.trades.forEach((t) => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${formatDate(t.ts)}</td>
        <td>${t.action}</td>
        <td>${t.size.toFixed(4)}</td>
        <td>${t.price.toFixed(4)}</td>
        <td>${t.pnl.toFixed(2)}</td>
      `;
      tradesBody.appendChild(row);
    });
  }

  const equityBody = document.querySelector('#backtestEquity tbody');
  if (equityBody) {
    equityBody.innerHTML = '';
    result.equity_curve.slice(-50).forEach((p) => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${formatDate(p.ts)}</td>
        <td>${p.equity.toFixed(2)}</td>
      `;
      equityBody.appendChild(row);
    });
  }
}

function formatDate(ts) {
  const date = new Date(ts);
  return date.toLocaleString();
}

export async function refreshBacktesting() {
  // no-op placeholder to align with refreshAll wiring
  return Promise.resolve();
}

