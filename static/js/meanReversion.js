import { applyQuoteLabels, openOverlay, closeOverlay, showToast } from './ui.js';

let botConfig = null;
let priceChart = null;
let ratioChart = null;
let mrSocket = null;
let lastMeanRatio = null;
let lastStdRatio = null;
let lastPriceA = null;
let lastPriceB = null;
let lastRatio = null;
let currentQuote = 'USDT';
let currentPair = { asset_a: 'HBAR', asset_b: 'DOGE' };
let latestStatus = null;

function applyConfigToForm(cfg) {
  botConfig = cfg;

  document.getElementById('poll_interval_sec').value = cfg.poll_interval_sec;
  document.getElementById('window_size').value = cfg.window_size;
  document.getElementById('z_entry').value = cfg.z_entry;
  document.getElementById('z_exit').value = cfg.z_exit;
  document.getElementById('trade_notional_usd').value = cfg.trade_notional_usd;
  document.getElementById('use_all_balance').checked = cfg.use_all_balance;
  document.getElementById('use_ratio_thresholds').checked = cfg.use_ratio_thresholds;
  document.getElementById('sell_ratio_threshold').value = cfg.sell_ratio_threshold;
  document.getElementById('buy_ratio_threshold').value = cfg.buy_ratio_threshold;
  document.getElementById('use_testnet').checked = cfg.use_testnet;

  if (cfg.available_pairs) {
    updatePairControls(cfg.available_pairs, [cfg.asset_a, cfg.asset_b]);
  }

  currentPair = { asset_a: cfg.asset_a, asset_b: cfg.asset_b };

  currentQuote = cfg.use_testnet ? 'USDT' : 'USDC';
  applyQuoteLabels(currentQuote);
  updateMeanConfigSummary(cfg);
}

function updateMeanConfigSummary(cfg) {
  const summaryEl = document.getElementById('mrConfigSummary');
  if (!summaryEl) return;

  if (!cfg) {
    summaryEl.textContent = 'Save a configuration to see a quick snapshot here.';
    return;
  }

  const quote = cfg.use_testnet ? 'USDT' : 'USDC';
  const notional = Number.isFinite(Number(cfg.trade_notional_usd))
    ? `${Number(cfg.trade_notional_usd).toFixed(2)} ${quote}`
    : 'Not set';
  const thresholds = cfg.use_ratio_thresholds
    ? `${cfg.buy_ratio_threshold?.toFixed(5)} / ${cfg.sell_ratio_threshold?.toFixed(5)}`
    : `${cfg.z_entry} / ${cfg.z_exit}`;
  const thresholdLabel = cfg.use_ratio_thresholds ? 'Ratio bands' : 'Z-entry / exit';

  summaryEl.innerHTML = `
    <div class="summary-title">Saved config snapshot</div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Pair</div>
        <div class="metric-value">${cfg.asset_a}/${cfg.asset_b}</div>
      </div>
      <div>
        <div class="metric-label">Window / poll</div>
        <div class="metric-value">${cfg.window_size} • ${cfg.poll_interval_sec}s</div>
      </div>
      <div>
        <div class="metric-label">Notional</div>
        <div class="metric-value">${notional}</div>
      </div>
    </div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">${thresholdLabel}</div>
        <div class="metric-value">${thresholds}</div>
      </div>
      <div>
        <div class="metric-label">Routing</div>
        <div class="metric-value">${cfg.use_testnet ? 'Testnet' : 'Mainnet'}</div>
      </div>
      <div>
        <div class="metric-label">Balance usage</div>
        <div class="metric-value">${cfg.use_all_balance ? 'Use full balances' : 'Cap at notional'}</div>
      </div>
    </div>
  `;
}

function getDirectionClass(current, last) {
  if (current === undefined || current === null || last === null) return 'price-flat';
  if (current > last) return 'price-up';
  if (current < last) return 'price-down';
  return 'price-flat';
}

function renderPricePill(value, direction) {
  const icon = direction === 'price-up' ? '↗' : direction === 'price-down' ? '↘' : '';
  return `
    <span class="price-pill ${direction}">
      ${value}
      ${icon ? `<span class="price-trend-icon">${icon}</span>` : ''}
    </span>
  `;
}

function updatePairControls(pairs, selected) {
  const select = document.getElementById('pair_select');
  select.innerHTML = '';

  pairs.forEach(([a, b]) => {
    const opt = document.createElement('option');
    opt.value = `${a}|${b}`;
    opt.textContent = `${a}/${b}`;
    if (selected && selected[0] === a && selected[1] === b) {
      opt.selected = true;
    }
    select.appendChild(opt);
  });

  const manualDirection = document.getElementById('manual_direction');
  manualDirection.innerHTML = '';
  const dir1 = document.createElement('option');
  dir1.value = `${selected[0]}->${selected[1]}`;
  dir1.textContent = `${selected[0]} → ${selected[1]}`;
  const dir2 = document.createElement('option');
  dir2.value = `${selected[1]}->${selected[0]}`;
  dir2.textContent = `${selected[1]} → ${selected[0]}`;
  manualDirection.appendChild(dir1);
  manualDirection.appendChild(dir2);

  currentPair = { asset_a: selected[0], asset_b: selected[1] };
  updateManualTradeForm();
}

function getFromAssetInfo(direction) {
  const [fromAsset] = direction.split('->');
  let balance = 0;
  let price = null;

  if (latestStatus) {
    if (fromAsset === currentPair.asset_a) {
      balance = latestStatus.asset_a_balance || 0;
      price = latestStatus.price_a;
    } else if (fromAsset === currentPair.asset_b) {
      balance = latestStatus.asset_b_balance || 0;
      price = latestStatus.price_b;
    }
  }

  return { fromAsset, balance, price };
}

function updateManualTradeForm() {
  const directionEl = document.getElementById('manual_direction');
  if (!directionEl) return;

  const { fromAsset, balance } = getFromAssetInfo(directionEl.value);
  const label = document.getElementById('manual_amount_label');
  if (label) label.textContent = fromAsset || '—';

  const hint = document.getElementById('manual_balance_hint');
  if (hint) {
    if (balance && balance > 0) {
      hint.textContent = `Available: ${balance.toFixed(4)} ${fromAsset}`;
    } else {
      hint.textContent = 'Available: 0';
    }
  }
}

async function setManualMax() {
  const directionEl = document.getElementById('manual_direction');
  const input = document.getElementById('manual_amount');

  if (!directionEl || !input) return;

  // Ensure we have a recent balance snapshot before calculating max
  if (!latestStatus) {
    await fetchStatus();
  }

  const { fromAsset, balance } = getFromAssetInfo(directionEl.value);

  if (!balance || balance <= 0) {
    showToast(`No ${fromAsset || 'selected asset'} balance available.`, 'warning');
    return;
  }

  const clampedBalance = Number(balance.toFixed(8));
  input.value = clampedBalance;
  showToast(`Using max ${fromAsset} balance: ${clampedBalance}`, 'info');
}

async function manualTrade(event) {
  event.preventDefault();
  const direction = document.getElementById('manual_direction').value;
  const amountField = document.getElementById('manual_amount');
  const amount = parseFloat(amountField.value);
  const { fromAsset, balance, price } = getFromAssetInfo(direction);

  if (isNaN(amount) || amount <= 0) {
    showToast('Enter a valid trade amount > 0', 'warning');
    return;
  }

  if (!price || price <= 0) {
    showToast('Missing price data. Refresh the dashboard and try again.', 'warning');
    return;
  }

  if (!balance || balance <= 0) {
    showToast(`No available ${fromAsset} balance to trade.`, 'warning');
    return;
  }

  let qtyToUse = amount;
  if (amount > balance) {
    qtyToUse = balance;
    amountField.value = balance;
    showToast(`Clamped amount to available ${fromAsset} balance (${balance.toFixed(4)}).`, 'warning');
  }

  try {
    const r = await fetch('/manual_trade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: direction, from_asset_qty: qtyToUse }),
    });

    if (!r.ok) {
      const err = await r.json();
      showToast('Error: ' + (err.detail || r.statusText), 'danger');
      return;
    }

    showToast('Manual trade executed.', 'success');
    await refreshMeanReversion();
  } catch (e) {
    console.error(e);
    showToast('Request failed. See console.', 'danger');
  }
}

async function fetchPairHealth() {
  const container = document.getElementById('pairHealth');
  try {
    const r = await fetch('/pair_history');
    if (!r.ok) throw new Error('Failed to load pair history');
    const data = await r.json();

    const badge = data.is_good_pair
      ? '<span class="chip chip-primary">Healthy movement</span>'
      : '<span class="chip chip-muted">Needs more movement</span>';

    const rows = (data.history || []).slice(-10).map((h) => {
      return `<tr>
        <td>${new Date(h.ts).toLocaleTimeString()}</td>
        <td>${h.price_a?.toFixed(4)}</td>
        <td>${h.price_b?.toFixed(4)}</td>
        <td>${h.ratio?.toFixed(6)}</td>
        <td>${h.zscore?.toFixed(2)}</td>
      </tr>`;
    });

    container.innerHTML = `
      <div class="status-line">Pair: <b>${data.pair}</b> ${badge} | Std: ${data.std.toFixed(6)}</div>
      <table class="simple-table">
        <thead>
          <tr><th>Time</th><th>Price A</th><th>Price B</th><th>Ratio</th><th>Z</th></tr>
        </thead>
        <tbody>${rows.join('')}</tbody>
      </table>
    `;
  } catch (e) {
    console.error(e);
    container.innerText = 'Unable to load pair history';
  }
}

async function syncState() {
  try {
    const r = await fetch('/sync_state_from_balances', { method: 'POST' });
    if (!r.ok) {
      const err = await r.json();
      showToast('Sync failed: ' + (err.detail || r.statusText), 'danger');
      return;
    }
    const data = await r.json();
    showToast(`Synced to ${data.current_asset} @ ${data.current_qty.toFixed(4)}`, 'success');
    await refreshMeanReversion();
  } catch (e) {
    console.error(e);
    showToast('Sync request failed.', 'danger');
  }
}

async function fetchStatus() {
  try {
    const r = await fetch('/status');
    const data = await r.json();
    applyStatus(data);
  } catch (e) {
    console.error(e);
    document.getElementById('status').innerText = 'Error loading status';
  }
}

function applyStatus(data) {
  lastMeanRatio = data.mean_ratio;
  lastStdRatio = data.std_ratio;

  const priceADirection = getDirectionClass(data.price_a, lastPriceA);
  const priceBDirection = getDirectionClass(data.price_b, lastPriceB);
  const ratioDirection = getDirectionClass(data.ratio, lastRatio);

  currentPair = { asset_a: data.asset_a, asset_b: data.asset_b };
  if (botConfig && botConfig.available_pairs) {
    updatePairControls(botConfig.available_pairs, [data.asset_a, data.asset_b]);
  }

  currentQuote = data.use_testnet ? 'USDT' : 'USDC';
  applyQuoteLabels(currentQuote);

  latestStatus = {
    base_balance: data.base_balance,
    asset_a_balance: data.asset_a_balance,
    asset_b_balance: data.asset_b_balance,
    price_a: data.price_a,
    price_b: data.price_b,
  };

  updateManualTradeForm();

  const envChip = `<span class="chip chip-primary">${data.use_testnet ? 'TESTNET' : 'MAINNET'}</span>`;
  const botChip = `<span class="chip ${data.enabled ? 'chip-primary' : 'chip-muted'}">MR Bot: ${
    data.enabled ? 'RUNNING' : 'STOPPED'
  }</span>`;

  const pairLabel = `${data.asset_a}/${data.asset_b}`;

  document.getElementById('status').innerHTML = `
      <div class="status-chip-row">
        ${envChip}
        ${botChip}
        <span class="chip">Pair: ${pairLabel}</span>
      </div>

      <div class="metric-grid">
        <div class="metric-group">
          <div class="metric-label">BTC${currentQuote}</div>
          <div class="metric-value">${data.btc.toFixed(2)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">${data.asset_a}${currentQuote}</div>
          <div class="metric-value">${renderPricePill(data.price_a.toFixed(4), priceADirection)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">${data.asset_b}${currentQuote}</div>
          <div class="metric-value">${renderPricePill(data.price_b.toFixed(4), priceBDirection)}</div>
        </div>

        <div class="metric-group">
          <div class="metric-label">Ratio (${data.asset_a}/${data.asset_b})</div>
          <div class="metric-value">${renderPricePill(data.ratio.toFixed(6), ratioDirection)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Mean ratio</div>
          <div class="metric-value">${data.mean_ratio.toFixed(6)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Std dev</div>
          <div class="metric-value">${data.std_ratio.toFixed(6)}</div>
        </div>

        <div class="metric-group">
          <div class="metric-label">z-score</div>
          <div class="metric-value">${data.zscore.toFixed(2)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Current asset</div>
          <div class="metric-value">${data.current_asset}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Current qty</div>
          <div class="metric-value">${data.current_qty.toFixed(4)}</div>
        </div>
      </div>

      <div class="status-line">
        <b>PnL (realized):</b> ${data.realized_pnl_usd.toFixed(2)} ${currentQuote} |
        <b>PnL (unrealized):</b> ${data.unrealized_pnl_usd.toFixed(2)} ${currentQuote}
      </div>
      <div class="status-line">
        <b>Balances:</b> ${currentQuote}: ${data.base_balance.toFixed(2)} |
        ${data.asset_a}: ${data.asset_a_balance.toFixed(2)} |
        ${data.asset_b}: ${data.asset_b_balance.toFixed(2)}
      </div>
    `;

  if (priceChart) {
    priceChart.data.datasets[0].label = data.asset_a + currentQuote;
    priceChart.data.datasets[1].label = data.asset_b + currentQuote;
    priceChart.update();
  }

  lastPriceA = data.price_a ?? lastPriceA;
  lastPriceB = data.price_b ?? lastPriceB;
  lastRatio = data.ratio ?? lastRatio;
}

async function fetchConfig() {
  const r = await fetch('/config');
  const cfg = await r.json();
  applyConfigToForm(cfg);
}

async function saveConfig(event) {
  event.preventDefault();
  const cfg = {
    asset_a: document.getElementById('pair_select').value.split('|')[0],
    asset_b: document.getElementById('pair_select').value.split('|')[1],
    poll_interval_sec: parseInt(document.getElementById('poll_interval_sec').value),
    window_size: parseInt(document.getElementById('window_size').value),
    z_entry: parseFloat(document.getElementById('z_entry').value),
    z_exit: parseFloat(document.getElementById('z_exit').value),
    trade_notional_usd: parseFloat(document.getElementById('trade_notional_usd').value),
    use_all_balance: document.getElementById('use_all_balance').checked,
    use_ratio_thresholds: document.getElementById('use_ratio_thresholds').checked,
    sell_ratio_threshold: parseFloat(document.getElementById('sell_ratio_threshold').value || '0'),
    buy_ratio_threshold: parseFloat(document.getElementById('buy_ratio_threshold').value || '0'),
    use_testnet: document.getElementById('use_testnet').checked,
  };
  const r = await fetch('/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  const newCfg = await r.json();
  botConfig = newCfg;

  currentQuote = botConfig.use_testnet ? 'USDT' : 'USDC';
  applyQuoteLabels(currentQuote);

  showToast('Config saved. If you switched testnet/mainnet, verify your balances.', 'success');
}

async function generateConfigFromHistory() {
  const btn = document.getElementById('generateConfigBtn');
  const originalLabel = btn.textContent;

  try {
    btn.disabled = true;
    btn.textContent = 'Generating...';

    const r = await fetch('/config_best');
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText || 'Unable to generate config');
    }

    const cfg = await r.json();
    applyConfigToForm(cfg);
    showToast('Config updated from historical performance. Review and save if desired.', 'success');
  } catch (e) {
    console.error(e);
    showToast('Unable to generate config from history: ' + e.message, 'danger');
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

async function startBot() {
  await fetch('/start', { method: 'POST' });
  fetchStatus();
  fetchNextSignal();
}

async function stopBot() {
  await fetch('/stop', { method: 'POST' });
  fetchStatus();
  fetchNextSignal();
}

async function fetchTrades() {
  const r = await fetch('/trades?limit=100');
  const data = await r.json();
  const tbody = document.getElementById('tradesBody');
  tbody.innerHTML = '';
  data.forEach((t) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${t.ts}</td>
      <td>${t.side}</td>
      <td>${t.from_asset}</td>
      <td>${t.to_asset}</td>
      <td>${t.qty_from.toFixed(4)}</td>
      <td>${t.qty_to.toFixed(4)}</td>
      <td>${t.price.toFixed(4)}</td>
      <td>${t.pnl_usd.toFixed(2)}</td>
      <td>${t.is_testnet ? 'Testnet' : 'Mainnet'}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function fetchNextSignal() {
  try {
    const r = await fetch('/next_signal');
    if (!r.ok) {
      const err = await r.json();
      document.getElementById('nextSignalModalBody').innerText =
        'Error: ' + (err.detail || r.statusText);
      return;
    }
    const s = await r.json();

    let msg = `
      <div class="status-line"><b>Decision engine:</b> ${
        s.reason === 'z_score' ? 'z-score bands' : s.reason === 'ratio_thresholds' ? 'ratio thresholds' : s.reason
      }</div>
      <div class="metric-grid">
        <div class="metric-group">
          <div class="metric-label">Ratio now</div>
          <div class="metric-value">${s.ratio.toFixed(6)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Mean</div>
          <div class="metric-value">${s.mean_ratio.toFixed(6)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Std dev</div>
          <div class="metric-value">${s.std_ratio.toFixed(6)}</div>
        </div>
      </div>
      <div class="status-line">
        Z upper (sell HBAR): <b>${s.upper_band.toFixed(6)}</b> |
        Z lower (buy HBAR): <b>${s.lower_band.toFixed(6)}</b>
      </div>
    `;

    if (s.sell_threshold > 0 || s.buy_threshold > 0) {
      msg += `<div class="status-line">
                Thresholds → Sell: <b>${s.sell_threshold.toFixed(6)}</b> |
                Buy: <b>${s.buy_threshold.toFixed(6)}</b>
              </div>`;
    }

    if (s.direction === 'NONE') {
      msg += `<div class="status-line"><b>Next trade:</b> No trade would be executed right now.</div>`;
    } else {
      msg += `
        <div class="status-line"><b>Next trade:</b> ${s.direction}</div>
        <div class="status-line">
          From <b>${s.from_asset}</b> ≈ ${s.qty_from.toFixed(6)}
          → To <b>${s.to_asset}</b> ≈ ${s.qty_to.toFixed(6)}
        </div>
      `;
    }

    document.getElementById('nextSignalModalBody').innerHTML = msg;
  } catch (e) {
    console.error(e);
    document.getElementById('nextSignalModalBody').innerText = 'Error calculating next signal.';
  }
}

async function fetchHistory() {
  const r = await fetch('/history?limit=300');
  const data = await r.json();
  if (data.length === 0) return;

  const labels = data.map((d) => new Date(d.ts).toLocaleTimeString());
  const priceA = data.map((d) => d.price_a);
  const priceB = data.map((d) => d.price_b);
  const ratio = data.map((d) => d.ratio);

  if (!priceChart) {
    const ctx1 = document.getElementById('priceChart').getContext('2d');
    priceChart = new Chart(ctx1, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: currentPair.asset_a + currentQuote,
            data: priceA,
            borderWidth: 1,
            fill: false,
            borderColor: '#4bc0c0',
          },
          {
            label: currentPair.asset_b + currentQuote,
            data: priceB,
            borderWidth: 1,
            fill: false,
            borderColor: '#90caf9',
          },
        ],
      },
      options: {
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: '#eee' } } },
        scales: {
          x: { ticks: { color: '#ccc' }, grid: { color: '#333' } },
          y: { ticks: { color: '#ccc' }, grid: { color: '#333' } },
        },
      },
    });
  } else {
    priceChart.data.labels = labels;
    priceChart.data.datasets[0].data = priceA;
    priceChart.data.datasets[1].data = priceB;
    priceChart.data.datasets[0].label = currentPair.asset_a + currentQuote;
    priceChart.data.datasets[1].label = currentPair.asset_b + currentQuote;
    priceChart.update();
  }

  let upperEntry = null,
    lowerEntry = null,
    upperExit = null,
    lowerExit = null;

  if (botConfig && lastMeanRatio !== null) {
    const std = lastStdRatio || 0;

    if (std > 0) {
      upperEntry = lastMeanRatio + botConfig.z_entry * std;
      lowerEntry = lastMeanRatio - botConfig.z_entry * std;

      if (botConfig.z_exit && botConfig.z_exit > 0) {
        upperExit = lastMeanRatio + botConfig.z_exit * std;
        lowerExit = lastMeanRatio - botConfig.z_exit * std;
      }
    }
  }

  const upperEntryArr = ratio.map(() => upperEntry);
  const lowerEntryArr = ratio.map(() => lowerEntry);

  const upperExitArr = ratio.map(() => (upperExit !== null ? upperExit : null));
  const lowerExitArr = ratio.map(() => (lowerExit !== null ? lowerExit : null));

  let info = '';
  if (upperEntry !== null && lowerEntry !== null) {
    info += `Z-entry bands → Upper: ${upperEntry.toFixed(6)} | Lower: ${lowerEntry.toFixed(6)}. `;
  }
  if (upperExit !== null && lowerExit !== null) {
    info += `Z-exit bands → Upper: ${upperExit.toFixed(6)} | Lower: ${lowerExit.toFixed(6)}. `;
  }

  let sellArr = ratio.map(() => null);
  let buyArr = ratio.map(() => null);
  if (botConfig && botConfig.use_ratio_thresholds) {
    if (botConfig.sell_ratio_threshold > 0) {
      sellArr = ratio.map(() => botConfig.sell_ratio_threshold);
      info += `Sell threshold: ${botConfig.sell_ratio_threshold.toFixed(6)}. `;
    }
    if (botConfig.buy_ratio_threshold > 0) {
      buyArr = ratio.map(() => botConfig.buy_ratio_threshold);
      info += `Buy threshold: ${botConfig.buy_ratio_threshold.toFixed(6)}.`;
    }
  }

  document.getElementById('bandInfo').textContent =
    info || 'Bands/thresholds not available yet (need some history or config).';

  if (!ratioChart) {
    const ctx2 = document.getElementById('ratioChart').getContext('2d');
    ratioChart = new Chart(ctx2, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Ratio HBAR/DOGE',
            data: ratio,
            borderWidth: 2,
            fill: false,
            borderColor: '#4bc0c0',
          },
          {
            label: 'Upper z-band (sell HBAR)',
            data: upperEntryArr,
            borderWidth: 1,
            borderColor: '#90caf9',
            borderDash: [6, 4],
            fill: false,
          },
          {
            label: 'Lower z-band (buy HBAR)',
            data: lowerEntryArr,
            borderWidth: 1,
            borderColor: '#26c6da',
            borderDash: [6, 4],
            fill: false,
          },
          {
            label: 'Upper z-exit (take profit HBAR→DOGE)',
            data: upperExitArr,
            borderWidth: 1,
            borderColor: '#ffb300',
            borderDash: [4, 3],
            fill: false,
          },
          {
            label: 'Lower z-exit (take profit DOGE→HBAR)',
            data: lowerExitArr,
            borderWidth: 1,
            borderColor: '#ffb300',
            borderDash: [4, 3],
            fill: false,
          },
          {
            label: 'Sell ratio threshold',
            data: sellArr,
            borderWidth: 1,
            borderColor: '#66bb6a',
            borderDash: [2, 2],
            fill: false,
          },
          {
            label: 'Buy ratio threshold',
            data: buyArr,
            borderWidth: 1,
            borderColor: '#ef5350',
            borderDash: [2, 2],
            fill: false,
          },
        ],
      },
      options: {
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: '#eee' } } },
        scales: {
          x: { ticks: { color: '#ccc' }, grid: { color: '#333' } },
          y: { ticks: { color: '#ccc' }, grid: { color: '#333' } },
        },
      },
    });
  } else {
    ratioChart.data.labels = labels;
    ratioChart.data.datasets[0].data = ratio;
    ratioChart.data.datasets[1].data = upperEntryArr;
    ratioChart.data.datasets[2].data = lowerEntryArr;
    ratioChart.data.datasets[3].data = upperExitArr;
    ratioChart.data.datasets[4].data = lowerExitArr;
    ratioChart.data.datasets[5].data = sellArr;
    ratioChart.data.datasets[6].data = buyArr;
    ratioChart.update();
  }
}

function applySnapshot(snapshot) {
  if (!snapshot || !priceChart || !ratioChart) return;

  const label = new Date(snapshot.ts).toLocaleTimeString();
  const maxPoints = 300;
  const trimPush = (arr, val) => {
    arr.push(val);
    if (arr.length > maxPoints) arr.shift();
  };

  trimPush(priceChart.data.labels, label);
  trimPush(priceChart.data.datasets[0].data, snapshot.price_a);
  trimPush(priceChart.data.datasets[1].data, snapshot.price_b);

  const std = lastStdRatio || 0;
  let upperEntry = null,
    lowerEntry = null,
    upperExit = null,
    lowerExit = null,
    sellBand = null,
    buyBand = null;

  if (botConfig && lastMeanRatio !== null && std > 0) {
    upperEntry = lastMeanRatio + botConfig.z_entry * std;
    lowerEntry = lastMeanRatio - botConfig.z_entry * std;

    if (botConfig.z_exit && botConfig.z_exit > 0) {
      upperExit = lastMeanRatio + botConfig.z_exit * std;
      lowerExit = lastMeanRatio - botConfig.z_exit * std;
    }

    if (botConfig.use_ratio_thresholds) {
      sellBand = botConfig.sell_ratio_threshold || null;
      buyBand = botConfig.buy_ratio_threshold || null;
    }
  }

  const ratioData = ratioChart.data.datasets;
  trimPush(ratioChart.data.labels, label);
  trimPush(ratioData[0].data, snapshot.ratio);
  trimPush(ratioData[1].data, upperEntry);
  trimPush(ratioData[2].data, lowerEntry);
  trimPush(ratioData[3].data, upperExit !== null ? upperExit : null);
  trimPush(ratioData[4].data, lowerExit !== null ? lowerExit : null);
  trimPush(ratioData[5].data, sellBand);
  trimPush(ratioData[6].data, buyBand);

  priceChart.update('none');
  ratioChart.update('none');
}

export async function refreshMeanReversion() {
  await fetchStatus();
  await fetchConfig();
  await fetchTrades();
  await fetchHistory();
  await fetchPairHealth();
  await fetchNextSignal().catch(() => {});
}

export function initMeanReversion() {
  const configForm = document.getElementById('configForm');
  const manualForm = document.getElementById('manualTradeForm');
  const manualDirection = document.getElementById('manual_direction');
  const manualMax = document.getElementById('manual_max_btn');
  const startBtn = document.getElementById('startBotBtn');
  const stopBtn = document.getElementById('stopBotBtn');
  const genConfigBtn = document.getElementById('generateConfigBtn');
  const nextClose = document.getElementById('nextModalClose');
  const nextCloseFooter = document.getElementById('nextModalCloseFooter');
  const nextRecalc = document.getElementById('nextModalRecalc');

  if (configForm) configForm.addEventListener('submit', saveConfig);
  if (manualForm) manualForm.addEventListener('submit', manualTrade);
  if (manualDirection) manualDirection.addEventListener('change', updateManualTradeForm);
  if (manualMax) manualMax.addEventListener('click', setManualMax);
  if (startBtn) startBtn.addEventListener('click', startBot);
  if (stopBtn) stopBtn.addEventListener('click', stopBot);
  if (genConfigBtn) genConfigBtn.addEventListener('click', generateConfigFromHistory);
  if (nextClose) nextClose.addEventListener('click', () => closeOverlay('nextModalOverlay'));
  if (nextCloseFooter) nextCloseFooter.addEventListener('click', () => closeOverlay('nextModalOverlay'));
  if (nextRecalc) nextRecalc.addEventListener('click', fetchNextSignal);

  connectMeanWebsocket();
}

export function getCurrentQuote() {
  return currentQuote;
}

function connectMeanWebsocket() {
  if (mrSocket) return;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/mean_reversion`;
  mrSocket = new WebSocket(wsUrl);

  mrSocket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.status) applyStatus(payload.status);
      if (payload.snapshot) applySnapshot(payload.snapshot);
    } catch (err) {
      console.error('Failed to handle websocket update', err);
    }
  };

  mrSocket.onclose = () => {
    mrSocket = null;
    setTimeout(connectMeanWebsocket, 2000);
  };
}
