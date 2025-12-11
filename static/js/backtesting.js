import { showToast } from './ui.js';

function setInlineStatus(targetId, message, variant = 'info') {
  const el = document.getElementById(targetId);
  if (!el) return;
  el.textContent = message;
  el.className = `status-line status-${variant}`;
  el.style.display = message ? 'flex' : 'none';
}

const freqtradeOptions = [
  { value: 'pattern_recognition', label: 'Freqtrade – Pattern recognition' },
  { value: 'strategy001', label: 'Freqtrade – Strategy 001' },
  { value: 'strategy002', label: 'Freqtrade – Strategy 002' },
  { value: 'strategy003', label: 'Freqtrade – Strategy 003' },
  { value: 'supertrend', label: 'Freqtrade – Supertrend' },
];

const supportedIntervals = ['20s', '1m', '5m', '15m', '1h', '4h', '1d'];

function parseNumberList(inputId) {
  const raw = document.getElementById(inputId)?.value || '';
  return raw
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean)
    .map((v) => Number(v))
    .filter((v) => !Number.isNaN(v));
}

function collectBacktestPayload() {
  const strategy = document.getElementById('backtestStrategy')?.value;
  const symbol = document.getElementById('backtestSymbol')?.value?.trim();
  const baseSymbol = document.getElementById('backtestBaseSymbol')?.value?.trim();
  const altSymbols = (document.getElementById('backtestAltSymbols')?.value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const conversionSymbol = document.getElementById('backtestConversion')?.value?.trim();
  const assetA = document.getElementById('backtestAssetA')?.value?.trim();
  const assetB = document.getElementById('backtestAssetB')?.value?.trim();
  const intervalSelect = document.getElementById('backtestInterval');
  const interval = intervalSelect?.value || '1h';
  const lookbackDays = Number(document.getElementById('backtestLookback')?.value || 14);
  const startDateStr = document.getElementById('backtestStartDate')?.value;
  const endDateStr = document.getElementById('backtestEndDate')?.value;
  const startingBalance = Number(document.getElementById('backtestStartingBalance')?.value || 1000);
  const windowSize = Number(document.getElementById('backtestWindow')?.value || 70);
  const numStd = Number(document.getElementById('backtestStd')?.value || 3);
  const zEntry = Number(document.getElementById('backtestZEntry')?.value || 3);
  const zExit = Number(document.getElementById('backtestZExit')?.value || 0.4);
  const fastWindow = Number(document.getElementById('backtestFast')?.value || 12);
  const slowWindow = Number(document.getElementById('backtestSlow')?.value || 26);
  const atrWindow = Number(document.getElementById('backtestAtrWindow')?.value || 14);
  const atrStop = Number(document.getElementById('backtestAtrStop')?.value || 2.0);
  const momentumWindow = Number(document.getElementById('backtestMomentum')?.value || 3);
  const minBeta = Number(document.getElementById('backtestMinBeta')?.value || 1.1);
  const switchCooldown = Number(document.getElementById('backtestCooldown')?.value || 0);

  if (!strategy) {
    showToast('Please choose a strategy', 'warning');
    return null;
  }

  if (Number.isNaN(lookbackDays) || lookbackDays <= 0) {
    showToast('Lookback days must be greater than 0.', 'warning');
    return null;
  }

  const isFreqtrade = freqtradeOptions.some((opt) => opt.value === strategy);

  if (!supportedIntervals.includes(interval)) {
    showToast(`Interval must be one of: ${supportedIntervals.join(', ')}`, 'warning');
    return null;
  }

  if (strategy === 'mean_reversion') {
    if (!assetA || !assetB) {
      showToast('Asset A and Asset B are required for mean reversion backtests.', 'warning');
      return null;
    }
    if (windowSize <= 0) {
      showToast('Window size must be greater than 0 for mean reversion.', 'warning');
      return null;
    }
  } else if (strategy === 'bollinger') {
    if (!symbol) {
      showToast('Symbol is required for Bollinger backtests.', 'warning');
      return null;
    }
    if (windowSize <= 0 || numStd <= 0) {
      showToast('Provide a valid window size and standard deviation for Bollinger.', 'warning');
      return null;
    }
  } else if (strategy === 'trend_following') {
    if (!symbol) {
      showToast('Symbol is required for trend-following backtests.', 'warning');
      return null;
    }
    if (fastWindow <= 0 || slowWindow <= 0 || atrWindow <= 0 || atrStop <= 0) {
      showToast('Please provide positive values for EMA windows and ATR stop.', 'warning');
      return null;
    }
  } else if (strategy === 'amplification') {
    if (!baseSymbol) {
      showToast('Base symbol is required for amplification.', 'warning');
      return null;
    }
    if (momentumWindow <= 0 || minBeta <= 0 || switchCooldown < 0) {
      showToast('Provide valid amplification parameters (momentum, beta, cooldown).', 'warning');
      return null;
    }
  } else if (isFreqtrade && !symbol) {
    showToast('Symbol is required for freqtrade backtests.', 'warning');
    return null;
  }

  if ((startDateStr && !endDateStr) || (endDateStr && !startDateStr)) {
    showToast('Please set both start and end dates to run a custom window.', 'warning');
    return null;
  }

  const startDate = startDateStr ? new Date(`${startDateStr}T00:00:00Z`).toISOString() : undefined;
  const endDate = endDateStr ? new Date(`${endDateStr}T23:59:59Z`).toISOString() : undefined;

  return {
    strategy,
    payload: {
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
      base_symbol: baseSymbol || undefined,
      alt_symbols: altSymbols,
      momentum_window: momentumWindow,
      min_beta: minBeta,
      conversion_symbol: conversionSymbol || undefined,
      switch_cooldown: switchCooldown,
    },
  };
}

function buildGridPayload(strategy) {
  const months = Number(document.getElementById('backtestGridMonths')?.value || 24);
  if (Number.isNaN(months) || months <= 0) {
    showToast('Months must be a positive number.', 'warning');
    return null;
  }

  const grid = {};

  if (strategy === 'mean_reversion') {
    const windows = parseNumberList('backtestGridWindow');
    const zEntries = parseNumberList('backtestGridZEntry');
    const zExits = parseNumberList('backtestGridZExit');
    if (windows.length) grid.window_sizes = windows;
    if (zEntries.length) grid.z_entries = zEntries;
    if (zExits.length) grid.z_exits = zExits;
  } else if (strategy === 'bollinger') {
    const windows = parseNumberList('backtestGridBollWindow');
    const widths = parseNumberList('backtestGridStd');
    if (windows.length) grid.window_sizes = windows;
    if (widths.length) grid.num_std_widths = widths;
  } else if (strategy === 'trend_following') {
    const fast = parseNumberList('backtestGridFast');
    const slow = parseNumberList('backtestGridSlow');
    const atrStops = parseNumberList('backtestGridAtrStop');
    if (fast.length) grid.fast_windows = fast;
    if (slow.length) grid.slow_windows = slow;
    if (atrStops.length) grid.atr_stop_mults = atrStops;
  } else if (strategy === 'amplification') {
    const momentum = parseNumberList('backtestGridMomentum');
    const betas = parseNumberList('backtestGridMinBeta');
    const cooldowns = parseNumberList('backtestGridCooldown');
    if (momentum.length) grid.momentum_windows = momentum;
    if (betas.length) grid.min_betas = betas;
    if (cooldowns.length) grid.switch_cooldowns = cooldowns;
  }

  return { months, grid };
}

export function initBacktesting() {
  const strategy = document.getElementById('backtestStrategy');
  const form = document.getElementById('backtestForm');
  const intervalSelect = document.getElementById('backtestInterval');
  if (!strategy || !form) return;

  if (intervalSelect) {
    intervalSelect.innerHTML = '';
    supportedIntervals.forEach((intv) => {
      const option = document.createElement('option');
      option.value = intv;
      option.textContent = intv;
      intervalSelect.appendChild(option);
    });
    intervalSelect.value = '1h';
  }

  // Populate strategy dropdown dynamically to keep labels centralized
  const baseOptions = [
    { value: 'mean_reversion', label: 'Mean reversion (pair)' },
    { value: 'bollinger', label: 'Bollinger bands' },
    { value: 'trend_following', label: 'Trend following' },
    { value: 'amplification', label: 'Amplification switcher' },
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

  const gridButton = document.getElementById('backtestGridButton');
  if (gridButton) {
    gridButton.addEventListener('click', async (e) => {
      e.preventDefault();
      await runBacktestGrid();
    });
  }
}

export async function runBacktest() {
  const collected = collectBacktestPayload();
  if (!collected) return;

  const { payload } = collected;
  const submitBtn = document.querySelector('#backtestForm button[type="submit"]');

  setInlineStatus('backtestStatus', 'Running backtest…', 'progress');
  if (submitBtn) submitBtn.disabled = true;

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
    setInlineStatus('backtestStatus', 'Backtest complete', 'success');
    showToast('Backtest complete', 'success');
  } catch (err) {
    console.error('Backtest failed', err);
    setInlineStatus('backtestStatus', `Backtest failed: ${err}`, 'danger');
    showToast(`Backtest failed: ${err}`, 'danger');
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

export async function runBacktestGrid() {
  const collected = collectBacktestPayload();
  if (!collected) return;

  const gridConfig = buildGridPayload(collected.strategy);
  if (!gridConfig) return;

  const runCount = estimateGridRuns(collected.strategy, gridConfig);
  const batchSize = 72;
  const batches = Math.max(1, Math.ceil(runCount / batchSize));

  const payload = {
    ...collected.payload,
    months: gridConfig.months,
    grid: gridConfig.grid,
  };

  const gridBtn = document.getElementById('backtestGridButton');

  const statusLabel = runCount
    ? `Running monthly grid in ${batches} batch${batches > 1 ? 'es' : ''} (${runCount} runs)…`
    : 'Running monthly grid…';
  setInlineStatus('backtestGridStatus', statusLabel, 'progress');
  if (gridBtn) gridBtn.disabled = true;

  try {
    const resp = await fetch('/backtest/grid', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || 'Request failed');
    }
    const data = await resp.json();
    renderBacktestGridResults(data);
    setInlineStatus('backtestGridStatus', 'Grid runs complete', 'success');
    showToast('Grid backtests complete', 'success');
  } catch (err) {
    console.error('Grid backtest failed', err);
    setInlineStatus('backtestGridStatus', `Grid backtest failed: ${err}`, 'danger');
    showToast(`Grid backtest failed: ${err}`, 'danger');
  } finally {
    if (gridBtn) gridBtn.disabled = false;
  }
}

function estimateGridRuns(strategy, gridConfig) {
  const months = gridConfig?.months ?? 1;
  const grid = gridConfig?.grid || {};

  const lengthOrOne = (arr) => (arr && arr.length ? arr.length : 1);

  if (strategy === 'mean_reversion') {
    return (
      months *
      lengthOrOne(grid.window_sizes) *
      lengthOrOne(grid.z_entries) *
      lengthOrOne(grid.z_exits)
    );
  }

  if (strategy === 'bollinger') {
    return months * lengthOrOne(grid.window_sizes) * lengthOrOne(grid.num_std_widths);
  }

  if (strategy === 'trend_following') {
    return (
      months *
      lengthOrOne(grid.fast_windows) *
      lengthOrOne(grid.slow_windows) *
      lengthOrOne(grid.atr_stop_mults)
    );
  }

  if (strategy === 'amplification') {
    return (
      months *
      lengthOrOne(grid.momentum_windows) *
      lengthOrOne(grid.min_betas) *
      lengthOrOne(grid.switch_cooldowns)
    );
  }

  return months;
}

function updateVisibleFields() {
  const strategy = document.getElementById('backtestStrategy')?.value;
  const pairFields = document.getElementById('backtestPairFields');
  const bollFields = document.getElementById('backtestBollFields');
  const trendFields = document.getElementById('backtestTrendFields');
  const ampFields = document.getElementById('backtestAmpFields');
  const symbolRow = document.getElementById('backtestSymbolRow');
  const mrFields = document.getElementById('backtestMrFields');
  const commonFields = document.getElementById('backtestCommonFields');
  const gridMeanFields = document.getElementById('backtestGridMeanFields');
  const gridBollFields = document.getElementById('backtestGridBollFields');
  const gridTrendFields = document.getElementById('backtestGridTrendFields');
  const gridAmpFields = document.getElementById('backtestGridAmpFields');

  const isPair = strategy === 'mean_reversion';
  const isBoll = strategy === 'bollinger';
  const isTrend = strategy === 'trend_following';
  const isAmp = strategy === 'amplification';

  if (pairFields) pairFields.style.display = isPair ? 'flex' : 'none';
  if (symbolRow) symbolRow.style.display = isPair || isAmp ? 'none' : 'flex';
  if (bollFields) bollFields.style.display = isBoll ? 'flex' : 'none';
  if (trendFields) trendFields.style.display = isTrend ? 'flex' : 'none';
  if (mrFields) mrFields.style.display = isPair ? 'flex' : 'none';
  if (ampFields) ampFields.style.display = isAmp ? 'flex' : 'none';
  if (commonFields) commonFields.style.display = 'flex';
  if (gridMeanFields) gridMeanFields.style.display = isPair ? 'flex' : 'none';
  if (gridBollFields) gridBollFields.style.display = isBoll ? 'flex' : 'none';
  if (gridTrendFields) gridTrendFields.style.display = isTrend ? 'flex' : 'none';
  if (gridAmpFields) gridAmpFields.style.display = isAmp ? 'flex' : 'none';
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

function renderBacktestGridResults(result) {
  const summary = document.getElementById('backtestGridSummaryText');
  if (summary) {
    summary.textContent = `Tested ${result.results.length} runs across ${result.months} months.`;
  }

  const tableBody = document.querySelector('#backtestGridResults tbody');
  if (!tableBody) return;

  tableBody.innerHTML = '';
  result.results.forEach((row) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${row.config_label}</td>
      <td>${formatDate(row.start)} → ${formatDate(row.end)}</td>
      <td>${(row.return_pct * 100).toFixed(2)}%</td>
      <td>${(row.win_rate * 100).toFixed(1)}%</td>
      <td>${(row.max_drawdown * 100).toFixed(2)}%</td>
      <td>${row.final_balance.toFixed(2)}</td>
    `;
    tableBody.appendChild(tr);
  });
}

function formatDate(ts) {
  const date = new Date(ts);
  return date.toLocaleString();
}

export async function refreshBacktesting() {
  // placeholder until backtesting has active refreshable data
  return Promise.resolve();
}

