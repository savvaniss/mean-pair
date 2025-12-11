import { applyQuoteLabels, showToast } from './ui.js';

let bollConfig = null;
let bollChart = null;
let cachedQuote = 'USDT';
let lastBollPrice = null;
let groupedSymbols = {};
const curatedSymbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'HBARUSDC', 'DOGEUSDC', 'LINKUSDT', 'MATICUSDT'];

function inferQuoteAsset(symbol, fallback) {
  if (!symbol) return fallback;
  const knownQuotes = ['USDT', 'USDC', 'BTC', 'BNB'];
  const match = knownQuotes.find((q) => symbol.endsWith(q));
  return match || fallback;
}

export function initBollinger() {
  const form = document.getElementById('bollConfigForm');
  const startBtn = document.getElementById('startBollBtn');
  const stopBtn = document.getElementById('stopBollBtn');
  const quoteFilter = document.getElementById('bollQuoteFilter');
  const generateBtn = document.getElementById('bollGenerateConfigBtn');

  if (!form || !startBtn || !stopBtn) return;

  form.addEventListener('submit', saveBollConfig);
  startBtn.addEventListener('click', startBoll);
  stopBtn.addEventListener('click', stopBoll);
  if (quoteFilter) quoteFilter.addEventListener('change', renderSymbolSelect);
  if (generateBtn) generateBtn.addEventListener('click', generateBollConfigFromHistory);
}

export async function refreshBollinger() {
  await fetchSymbols();
  await fetchBollConfig();
  await fetchBollStatus();
  await fetchBollBalances();
  await fetchBollTrades();
  await fetchBollHistory();
}

async function fetchSymbols() {
  try {
    const r = await fetch('/symbols_grouped');
    groupedSymbols = await r.json();
    renderSymbolSelect();
    renderSymbolPills();
  } catch (e) {
    console.error(e);
  }
}

async function generateBollConfigFromHistory() {
  const btn = document.getElementById('bollGenerateConfigBtn');
  const original = btn.textContent;

  try {
    btn.disabled = true;
    btn.textContent = 'Generating...';

    const symbol = document.getElementById('boll_symbol').value;
    const suffix = symbol ? `?symbol=${encodeURIComponent(symbol)}` : '';
    const r = await fetch(`/boll_config_best${suffix}`);
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText || 'Unable to generate config');
    }

    const cfg = await r.json();
    applyBollConfigToForm(cfg);
    showToast('Config suggested from Bollinger history. Review and save to apply.', 'success');
  } catch (e) {
    console.error(e);
    showToast('Unable to generate Bollinger config from history: ' + e.message, 'danger');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function renderSymbolSelect() {
  const select = document.getElementById('boll_symbol');
  const current = select.value;
  const filter = document.getElementById('bollQuoteFilter').value;

  select.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select symbol';
  select.appendChild(placeholder);

  const order = ['USDT', 'USDC', 'BTC', 'BNB'];
  order.forEach((quote) => {
    if (filter !== 'all' && filter !== quote) return;
    const list = groupedSymbols[quote] || [];
    if (list.length === 0) return;
    const og = document.createElement('optgroup');
    og.label = `${quote} pairs`;
    list
      .slice(0, 50)
      .sort((a, b) => a.baseAsset.localeCompare(b.baseAsset))
      .forEach((s) => {
        const label = `${s.symbol} (${s.baseAsset}/${s.quoteAsset})`;
        const opt = document.createElement('option');
        opt.value = s.symbol;
        opt.textContent = label;
        og.appendChild(opt);
      });
    select.appendChild(og);
  });

  if (current) select.value = current;
}

function renderSymbolPills() {
  const pills = document.getElementById('bollSymbolPills');
  pills.innerHTML = '';
  curatedSymbols.forEach((sym) => {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'pill';
    pill.textContent = sym;
    pill.addEventListener('click', () => {
      document.getElementById('boll_symbol').value = sym;
      fetchBollHistory();
      fetchBollStatus();
    });
    pills.appendChild(pill);
  });
}

async function fetchBollConfig() {
  const r = await fetch('/boll_config');
  const cfg = await r.json();
  applyBollConfigToForm(cfg);
}

async function saveBollConfig(event) {
  event.preventDefault();
  const cfg = {
    symbol: document.getElementById('boll_symbol').value,
    max_position_usd: parseFloat(document.getElementById('boll_trade_notional').value || '0'),
    window_size: parseInt(document.getElementById('boll_window').value || '0'),
    num_std: parseFloat(document.getElementById('boll_dev').value || '0'),
    use_testnet: document.getElementById('boll_use_testnet').checked,
    use_all_balance: document.getElementById('boll_use_all_balance').checked,
    stop_loss_pct: parseFloat(document.getElementById('boll_stop_loss').value || '0'),
    take_profit_pct: parseFloat(document.getElementById('boll_take_profit').value || '0'),
    cooldown_sec: parseInt(document.getElementById('boll_cooldown').value || '0'),
  };

  const r = await fetch('/boll_config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });

  const newCfg = await r.json();
  applyBollConfigToForm(newCfg);
  showToast('Bollinger config saved.', 'success');
}

async function fetchBollStatus() {
  try {
    const r = await fetch('/boll_status');
    const data = await r.json();

    cachedQuote = data.quote_asset || inferQuoteAsset(data.symbol, data.use_testnet ? 'USDT' : 'USDC');
    applyQuoteLabels(cachedQuote);

    const envChip = `<span class="chip chip-primary">${data.use_testnet ? 'TESTNET' : 'MAINNET'}</span>`;
    const botChip = `<span class="chip ${data.enabled ? 'chip-primary' : 'chip-muted'}">Bollinger Bot: ${
      data.enabled ? 'RUNNING' : 'STOPPED'
    }</span>`;

    const priceDirection =
      data.price && lastBollPrice
        ? data.price > lastBollPrice
          ? 'price-up'
          : data.price < lastBollPrice
            ? 'price-down'
            : 'price-flat'
        : 'price-flat';

    const priceLabel = data.price ? data.price.toFixed(4) : '—';
    const priceIcon =
      priceDirection === 'price-up' ? '↗' : priceDirection === 'price-down' ? '↘' : '';

    document.getElementById('bollStatus').innerHTML = `
      <div class="status-chip-row">
        ${envChip}
        ${botChip}
        <span class="chip">Symbol: ${data.symbol || 'Not set'}</span>
      </div>
      <div class="metric-grid">
        <div class="metric-group">
          <div class="metric-label">Position</div>
          <div class="metric-value">${data.position}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Base held (${data.base_asset || '—'})</div>
          <div class="metric-value">${(data.qty_asset || 0).toFixed(6)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Quote balance (${cachedQuote})</div>
          <div class="metric-value">${(data.quote_balance || 0).toFixed(2)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Last price</div>
          <div class="metric-value">
            <span class="price-pill ${priceDirection}">
              ${priceLabel}
              ${priceIcon ? `<span class="price-trend-icon">${priceIcon}</span>` : ''}
            </span>
          </div>
        </div>
      </div>
      <div class="status-line">
        <b>MA:</b> ${data.ma ? data.ma.toFixed(6) : '—'} |
        <b>Upper:</b> ${data.upper ? data.upper.toFixed(6) : '—'} |
        <b>Lower:</b> ${data.lower ? data.lower.toFixed(6) : '—'}
      </div>
    `;

    document.getElementById('bollPosition').innerHTML = `
      <div class="chip">Unrealized: ${(data.unrealized_pnl_usd || 0).toFixed(2)} ${cachedQuote}</div>
      <div class="chip">Realized: ${(data.realized_pnl_usd || 0).toFixed(2)} ${cachedQuote}</div>
    `;

    lastBollPrice = data.price ?? lastBollPrice;
  } catch (e) {
    console.error(e);
    document.getElementById('bollStatus').innerText = 'Error loading Bollinger status';
  }
}

function applyBollConfigToForm(cfg) {
  bollConfig = cfg;
  cachedQuote = inferQuoteAsset(cfg.symbol, cfg.use_testnet ? 'USDT' : 'USDC');
  applyQuoteLabels(cachedQuote);

  document.getElementById('boll_symbol').value = cfg.symbol || '';
  document.getElementById('boll_trade_notional').value = cfg.max_position_usd ?? '';
  document.getElementById('boll_window').value = cfg.window_size ?? '';
  document.getElementById('boll_dev').value = cfg.num_std ?? '';
  document.getElementById('boll_use_testnet').checked = cfg.use_testnet;
  document.getElementById('boll_use_all_balance').checked = cfg.use_all_balance;
  document.getElementById('boll_stop_loss').value = cfg.stop_loss_pct ?? '';
  document.getElementById('boll_take_profit').value = cfg.take_profit_pct ?? '';
  document.getElementById('boll_cooldown').value = cfg.cooldown_sec ?? '';

  updateBollConfigSummary(cfg);
}

function updateBollConfigSummary(cfg) {
  const summaryEl = document.getElementById('bollConfigSummary');
  if (!summaryEl) return;

  if (!cfg) {
    summaryEl.textContent = 'Save a configuration to see a quick snapshot here.';
    return;
  }

  const quote = inferQuoteAsset(cfg.symbol, cfg.use_testnet ? 'USDT' : 'USDC');
  const maxPosition = Number.isFinite(Number(cfg.max_position_usd))
    ? Number(cfg.max_position_usd).toFixed(2)
    : 'Not set';
  const stopLoss = Number.isFinite(Number(cfg.stop_loss_pct))
    ? `${Math.round(Number(cfg.stop_loss_pct) * 100)}%`
    : 'Off';
  const takeProfit = Number.isFinite(Number(cfg.take_profit_pct))
    ? `${Math.round(Number(cfg.take_profit_pct) * 100)}%`
    : 'Off';
  const cooldown = Number.isFinite(Number(cfg.cooldown_sec)) ? `${cfg.cooldown_sec}s` : 'Not set';
  const windowSize = cfg.window_size || '—';
  const deviation = Number.isFinite(Number(cfg.num_std)) ? Number(cfg.num_std).toFixed(2) : '—';

  summaryEl.innerHTML = `
    <div class="summary-title">Saved config snapshot</div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Symbol</div>
        <div class="metric-value">${cfg.symbol || 'Not set'}</div>
      </div>
      <div>
        <div class="metric-label">Band window</div>
        <div class="metric-value">${windowSize} • k=${deviation}</div>
      </div>
      <div>
        <div class="metric-label">Max position</div>
        <div class="metric-value">${maxPosition} ${quote}</div>
      </div>
    </div>
    <div class="summary-grid">
      <div>
        <div class="metric-label">Stop loss</div>
        <div class="metric-value">${stopLoss}</div>
      </div>
      <div>
        <div class="metric-label">Take profit</div>
        <div class="metric-value">${takeProfit}</div>
      </div>
      <div>
        <div class="metric-label">Cooldown</div>
        <div class="metric-value">${cooldown}</div>
      </div>
    </div>
    <div class="chip-row">
      <span class="chip chip-primary">${cfg.use_testnet ? 'Testnet routing' : 'Mainnet routing'}</span>
      <span class="chip">${cfg.use_all_balance ? 'Uses full quote balance' : `Cap: ${maxPosition} ${quote}`}</span>
      <span class="chip chip-muted">${cfg.num_std >= 2 ? 'Conservative bands' : 'Tight bands'}</span>
    </div>
  `;
}

async function fetchBollBalances() {
  try {
    const r = await fetch('/boll_balances');
    const balances = await r.json();
    const container = document.getElementById('bollBalances');
    container.innerHTML = '';

    balances
      .sort((a, b) => b.free - a.free)
      .slice(0, 15)
      .forEach((b) => {
        const row = document.createElement('div');
        row.className = 'balance-row';
        row.innerHTML = `
          <div class="balance-asset">${b.asset}</div>
          <div class="balance-qty">${b.free.toFixed(6)}</div>
          <div class="balance-locked">locked: ${b.locked.toFixed(4)}</div>
        `;
        container.appendChild(row);
      });
  } catch (e) {
    console.error(e);
    document.getElementById('bollBalances').textContent = 'Unable to load balances';
  }
}

async function fetchBollHistory() {
  try {
    const symbol = document.getElementById('boll_symbol').value || bollConfig?.symbol;
    const status = document.getElementById('bollHistoryStatus');
    const tbody = document.getElementById('bollHistoryBody');
    status.textContent = '';
    tbody.innerHTML = '';

    if (!symbol) {
      status.textContent = 'Select or save a symbol to view history.';
      return;
    }

    const r = await fetch(`/boll_history?symbol=${encodeURIComponent(symbol)}`);
    if (!r.ok) {
      document.getElementById('bollBandInfo').textContent = 'Select a symbol to load history.';
      status.textContent = 'No saved history available yet.';
      return;
    }
    const data = await r.json();

    if (!data.length) {
      status.textContent = `No saved history yet for ${symbol}. Start the bot to record snapshots.`;
      document.getElementById('bollBandInfo').textContent = 'Waiting for saved Bollinger bands.';
      if (bollChart) {
        bollChart.data.labels = [];
        bollChart.data.datasets.forEach((d) => (d.data = []));
        bollChart.update();
      }
      return;
    }

    const labels = data.map((d) => new Date(d.ts).toLocaleTimeString());
    const price = data.map((d) => d.price);
    const ma = data.map((d) => d.ma);
    const upper = data.map((d) => d.upper);
    const lower = data.map((d) => d.lower);

    const latest = data[data.length - 1];
    if (latest) {
      document.getElementById('bollBandInfo').textContent = `
        Latest → Price: ${latest.price.toFixed(6)} | MA: ${latest.ma.toFixed(6)} | Upper: ${latest.upper.toFixed(6)} | Lower: ${latest.lower.toFixed(6)}
      `;
    }

    if (!bollChart) {
      const ctx = document.getElementById('bollChart').getContext('2d');
      bollChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [
            { label: 'Price', data: price, borderWidth: 2, fill: false, borderColor: '#4bc0c0' },
            { label: 'Moving average', data: ma, borderWidth: 1, fill: false, borderColor: '#90caf9' },
            { label: 'Upper band', data: upper, borderWidth: 1, fill: false, borderColor: '#ef5350', borderDash: [6, 4] },
            { label: 'Lower band', data: lower, borderWidth: 1, fill: false, borderColor: '#66bb6a', borderDash: [6, 4] },
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
      bollChart.data.labels = labels;
      bollChart.data.datasets[0].data = price;
      bollChart.data.datasets[1].data = ma;
      bollChart.data.datasets[2].data = upper;
      bollChart.data.datasets[3].data = lower;
      bollChart.update();
    }

    data
      .slice()
      .reverse()
      .forEach((row) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${new Date(row.ts).toLocaleString()}</td>
          <td>${row.price.toFixed(6)}</td>
          <td>${row.ma.toFixed(6)}</td>
          <td>${row.upper.toFixed(6)}</td>
          <td>${row.lower.toFixed(6)}</td>
        `;
        tbody.appendChild(tr);
      });
    status.textContent = `Showing ${data.length} saved points for ${symbol}.`;
  } catch (e) {
    console.error(e);
    document.getElementById('bollBandInfo').textContent = 'Error loading Bollinger history.';
    const status = document.getElementById('bollHistoryStatus');
    if (status) status.textContent = 'Error loading history.';
  }
}

async function fetchBollTrades() {
  try {
    const r = await fetch('/boll_trades?limit=100');
    const data = await r.json();
    const tbody = document.getElementById('bollTradesBody');
    tbody.innerHTML = '';
    data.forEach((t) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${t.ts}</td>
        <td>${t.symbol}</td>
        <td>${t.side}</td>
        <td>${t.qty.toFixed(6)}</td>
        <td>${t.price.toFixed(6)}</td>
        <td>${t.notional.toFixed(2)}</td>
        <td>${(t.fee ?? 0).toFixed(2)}</td>
        <td>${t.pnl_usd.toFixed(2)}</td>
        <td>${t.is_testnet ? 'Testnet' : 'Mainnet'}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error(e);
  }
}

async function startBoll() {
  try {
    const r = await fetch('/boll_start', { method: 'POST' });
    if (!r.ok) {
      const err = await r.json();
      showToast('Cannot start Bollinger bot: ' + (err.detail || r.statusText), 'danger');
    }
    await fetchBollStatus();
  } catch (e) {
    console.error(e);
  }
}

async function stopBoll() {
  try {
    await fetch('/boll_stop', { method: 'POST' });
    await fetchBollStatus();
  } catch (e) {
    console.error(e);
  }
}
