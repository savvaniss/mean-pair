import { applyQuoteLabels } from './ui.js';

let bollConfig = null;
let bollChart = null;
let cachedQuote = 'USDT';

export function initBollinger() {
  document.getElementById('bollConfigForm').addEventListener('submit', saveBollConfig);
  document.getElementById('startBollBtn').addEventListener('click', startBoll);
  document.getElementById('stopBollBtn').addEventListener('click', stopBoll);
  document.getElementById('bollManualForm').addEventListener('submit', bollingerManualSell);
}

export async function refreshBollinger() {
  await fetchSymbols();
  await fetchBollStatus();
  await fetchBollConfig();
  await fetchBollTrades();
  await fetchBollHistory();
}

async function fetchSymbols() {
  try {
    const r = await fetch('/symbols_grouped');
    const grouped = await r.json();

    const select = document.getElementById('boll_symbol');
    const manualSelect = document.getElementById('boll_manual_symbol');

    const current = select.value;
    const currentManual = manualSelect.value;

    select.innerHTML = '';
    manualSelect.innerHTML = '';

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select symbol';
    select.appendChild(placeholder.cloneNode(true));
    manualSelect.appendChild(placeholder.cloneNode(true));

    const order = ['USDT', 'USDC', 'BTC', 'BNB'];

    order.forEach((quote) => {
      const list = grouped[quote];
      if (!list || list.length === 0) return;

      const og = document.createElement('optgroup');
      og.label = `${quote} pairs`;

      const ogManual = document.createElement('optgroup');
      ogManual.label = `${quote} pairs`;

      list.forEach((s) => {
        const label = `${s.symbol} (${s.baseAsset}/${s.quoteAsset})`;

        const opt = document.createElement('option');
        opt.value = s.symbol;
        opt.textContent = label;
        og.appendChild(opt);

        const opt2 = document.createElement('option');
        opt2.value = s.symbol;
        opt2.textContent = label;
        ogManual.appendChild(opt2);
      });

      select.appendChild(og);
      manualSelect.appendChild(ogManual);
    });

    if (current) select.value = current;
    if (currentManual) manualSelect.value = currentManual;
  } catch (e) {
    console.error(e);
  }
}

async function fetchBollConfig() {
  const r = await fetch('/boll_config');
  const cfg = await r.json();
  bollConfig = cfg;
  cachedQuote = cfg.use_testnet ? 'USDT' : 'USDC';
  applyQuoteLabels(cachedQuote);

  document.getElementById('boll_symbol').value = cfg.symbol;
  document.getElementById('boll_trade_notional').value = cfg.trade_notional_usd;
  document.getElementById('boll_window').value = cfg.window_size;
  document.getElementById('boll_dev').value = cfg.k;
  document.getElementById('boll_use_testnet').checked = cfg.use_testnet;
  document.getElementById('boll_only_buy').checked = cfg.only_buy;
}

async function saveBollConfig(event) {
  event.preventDefault();
  const cfg = {
    symbol: document.getElementById('boll_symbol').value,
    trade_notional_usd: parseFloat(document.getElementById('boll_trade_notional').value),
    window_size: parseInt(document.getElementById('boll_window').value),
    k: parseFloat(document.getElementById('boll_dev').value),
    use_testnet: document.getElementById('boll_use_testnet').checked,
    only_buy: document.getElementById('boll_only_buy').checked,
  };

  const r = await fetch('/boll_config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });

  const newCfg = await r.json();
  bollConfig = newCfg;
  cachedQuote = bollConfig.use_testnet ? 'USDT' : 'USDC';
  applyQuoteLabels(cachedQuote);
  alert('Bollinger config saved.');
}

async function fetchBollStatus() {
  try {
    const r = await fetch('/boll_status');
    const data = await r.json();

    cachedQuote = data.use_testnet ? 'USDT' : 'USDC';
    applyQuoteLabels(cachedQuote);

    const envChip = `<span class="chip chip-primary">${data.use_testnet ? 'TESTNET' : 'MAINNET'}</span>`;
    const botChip = `<span class="chip ${data.enabled ? 'chip-primary' : 'chip-muted'}">Bollinger Bot: ${
      data.enabled ? 'RUNNING' : 'STOPPED'
    }</span>`;

    document.getElementById('bollStatus').innerHTML = `
      <div class="status-chip-row">
        ${envChip}
        ${botChip}
        <span class="chip">Symbol: ${data.symbol || 'Not set'}</span>
      </div>
      <div class="metric-grid">
        <div class="metric-group">
          <div class="metric-label">Base balance</div>
          <div class="metric-value">${(data.base_balance || 0).toFixed(6)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Quote balance (${cachedQuote})</div>
          <div class="metric-value">${(data.quote_balance || 0).toFixed(2)}</div>
        </div>
        <div class="metric-group">
          <div class="metric-label">Last price</div>
          <div class="metric-value">${data.last_price ? data.last_price.toFixed(4) : '—'}</div>
        </div>
      </div>
      <div class="status-line">
        <b>MA:</b> ${data.ma ? data.ma.toFixed(6) : '—'} |
        <b>Upper:</b> ${data.upper ? data.upper.toFixed(6) : '—'} |
        <b>Lower:</b> ${data.lower ? data.lower.toFixed(6) : '—'}
      </div>
    `;
  } catch (e) {
    console.error(e);
    document.getElementById('bollStatus').innerText = 'Error loading Bollinger status';
  }
}

async function fetchBollHistory() {
  try {
    const symbol = document.getElementById('boll_symbol').value;
    if (!symbol) return;

    const r = await fetch(`/boll_history?symbol=${symbol}`);
    if (!r.ok) {
      document.getElementById('bollBandInfo').textContent = 'Select a symbol to load history.';
      return;
    }
    const data = await r.json();

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
  } catch (e) {
    console.error(e);
    document.getElementById('bollBandInfo').textContent = 'Error loading Bollinger history.';
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
      alert('Cannot start Bollinger bot: ' + (err.detail || r.statusText));
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

async function bollingerManualSell(event) {
  event.preventDefault();
  const symbol = document.getElementById('boll_manual_symbol').value;
  const qtyStr = document.getElementById('boll_manual_qty').value;
  const qty = parseFloat(qtyStr);

  if (!symbol) {
    alert('Please select a symbol.');
    return false;
  }
  if (isNaN(qty) || qty <= 0) {
    alert('Enter a valid quantity > 0.');
    return false;
  }

  if (!confirm(`Sell ${qty} of the base asset in ${symbol}?`)) {
    return false;
  }

  try {
    const r = await fetch('/bollinger_manual_sell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol, qty_base: qty }),
    });

    const data = await r.json();

    if (!r.ok) {
      alert('Error: ' + (data.detail || r.statusText));
      return false;
    }

    alert(
      `Sold ${data.qty_sold.toFixed(6)} ${data.base_asset} ` +
        `for ~${data.quote_received_est.toFixed(2)} ${data.quote_asset}.`
    );

    await refreshBollinger();
  } catch (e) {
    console.error(e);
    alert('Request failed. Check console for details.');
  }

  return false;
}
