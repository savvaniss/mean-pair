import { showToast } from './ui.js';

let tableBody;
let refreshBtn;
let healthDiv;
let scoutChip;
let scoutPositions;
let startScoutBtn;
let stopScoutBtn;
let refreshScoutBtn;
let binanceAccountSelect;
let binanceEnvSelect;
let binanceNotionalInput;
let inputs;
let listingsInterval;
let healthInterval;

export function initListings() {
  tableBody = document.querySelector('#listingsTable tbody');
  refreshBtn = document.querySelector('#refreshListings');
  healthDiv = document.querySelector('#healthStatus');
  scoutChip = document.getElementById('scoutStatus');
  scoutPositions = document.getElementById('scoutPositions');
  startScoutBtn = document.getElementById('startScoutBtn');
  stopScoutBtn = document.getElementById('stopScoutBtn');
  refreshScoutBtn = document.getElementById('refreshScoutBtn');
  binanceAccountSelect = document.getElementById('binanceAccount');
  binanceEnvSelect = document.getElementById('binanceEnv');
  binanceNotionalInput = document.getElementById('binanceNotional');
  inputs = {
    exchangeType: document.getElementById('exchangeType'),
    exchange: document.getElementById('exchange'),
    network: document.getElementById('network'),
    minutes: document.getElementById('minutes'),
    search: document.getElementById('search'),
    sort: document.getElementById('sort'),
  };

  if (!tableBody || !refreshBtn || !healthDiv) return;

  refreshBtn.addEventListener('click', () => {
    refreshListings();
  });

  tableBody.addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-symbol]');
    if (btn && btn.dataset.action === 'buy-binance') {
      quickBuy(btn.dataset.symbol);
    }
  });

  [startScoutBtn, document.getElementById('startScoutInline')].forEach((btn) =>
    btn?.addEventListener('click', () => toggleScout(true))
  );
  [stopScoutBtn, document.getElementById('stopScoutInline')].forEach((btn) =>
    btn?.addEventListener('click', () => toggleScout(false))
  );
  [refreshScoutBtn, document.getElementById('refreshScoutInline')].forEach((btn) =>
    btn?.addEventListener('click', loadScoutStatus)
  );

  clearInterval(listingsInterval);
  clearInterval(healthInterval);
  listingsInterval = setInterval(fetchListings, 15000);
  healthInterval = setInterval(fetchHealth, 60000);

  loadScoutStatus();
}

export async function refreshListings() {
  if (!tableBody) return;
  await Promise.all([fetchListings(), fetchHealth(), loadScoutStatus()]);
}

async function fetchListings() {
  const params = new URLSearchParams();
  if (inputs.exchangeType?.value) params.append('exchange_type', inputs.exchangeType.value);
  if (inputs.exchange?.value) params.append('exchange', inputs.exchange.value);
  if (inputs.network?.value) params.append('network', inputs.network.value);
  if (inputs.minutes?.value) params.append('minutes', inputs.minutes.value);
  if (inputs.search?.value) params.append('search', inputs.search.value);
  if (inputs.sort?.value) params.append('sort', inputs.sort.value);

  try {
    const response = await fetch(`/api/listings/latest?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Listings fetch failed (${response.status})`);
    }
    const data = await response.json();
    renderTable(data);
  } catch (err) {
    renderErrorRow(err instanceof Error ? err.message : 'Failed to load listings');
  }
}

async function fetchHealth() {
  try {
    const response = await fetch('/api/listings/health');
    if (!response.ok) {
      throw new Error(`Health fetch failed (${response.status})`);
    }
    const data = await response.json();
    renderHealth(data);
  } catch (err) {
    renderHealth({ Error: { last_run: null, last_error: err instanceof Error ? err.message : 'Failed', count: 0 } });
  }
}

function renderTable(listings) {
  tableBody.innerHTML = '';
  listings.forEach((item) => {
    const row = document.createElement('tr');
    const actionCell =
      item.source === 'Binance'
        ? `<button class="btn btn-primary small" data-action="buy-binance" data-symbol="${item.symbol}">Buy ${item.symbol} (${item.exchange_type})</button>`
        : '';
    row.innerHTML = `
      <td>${item.symbol}</td>
      <td>${item.name}</td>
      <td>${item.pair}</td>
      <td>${item.network || '-'}</td>
      <td>${item.source} (${item.exchange_type.toUpperCase()})</td>
      <td>${new Date(item.listed_at).toLocaleString()}</td>
      <td>${item.url ? `<a href="${item.url}" target="_blank">View</a>` : '-'}</td>
      <td class="actions-cell">${actionCell || '-'}</td>
    `;
    tableBody.appendChild(row);
  });
}

function renderErrorRow(message) {
  tableBody.innerHTML = '';
  const row = document.createElement('tr');
  row.innerHTML = `<td colspan="8" style="text-align:center;">${message}</td>`;
  tableBody.appendChild(row);
}

function renderHealth(stats) {
  healthDiv.innerHTML = '';
  Object.entries(stats).forEach(([name, meta]) => {
    const card = document.createElement('div');
    card.className = 'health-card';
    card.innerHTML = `
      <strong>${name}</strong><br>
      Last run: ${meta.last_run ? new Date(meta.last_run).toLocaleTimeString() : 'n/a'}<br>
      Results: ${meta.count ?? 0}<br>
      Error: ${meta.last_error || 'none'}
    `;
    healthDiv.appendChild(card);
  });
}

async function quickBuy(symbol) {
  const notional = parseFloat(binanceNotionalInput?.value || '10');
  if (Number.isNaN(notional) || notional <= 0) {
    showToast('Notional must be greater than zero', 'warning');
    return;
  }

  const body = {
    symbol,
    notional,
    account: binanceAccountSelect?.value || 'mr',
    use_testnet: (binanceEnvSelect?.value || 'mainnet') === 'testnet',
  };

  try {
    const resp = await fetch('/api/listings/binance/buy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(`Buy failed: ${data.detail || resp.statusText}`, 'danger');
      return;
    }

    showToast(
      `Bought ${data.qty_executed.toFixed(6)} ${data.symbol} @ ${data.price_used.toFixed(6)} (${data.quote_asset})`,
      'success'
    );
  } catch (err) {
    console.error(err);
    showToast('Request failed', 'danger');
  }
}

async function loadScoutStatus() {
  if (!scoutChip) return;
  try {
    const resp = await fetch('/api/listings/binance/scout/status');
    if (!resp.ok) throw new Error('Failed to load scout status');
    const data = await resp.json();

    scoutChip.textContent = data.enabled
      ? `Scout running (${data.use_testnet ? 'testnet' : 'mainnet'})`
      : 'Scout stopped';
    scoutChip.className = data.enabled ? 'chip chip-primary' : 'chip';

    scoutPositions.innerHTML = '';
    if (!data.positions || data.positions.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No open scout positions yet.';
      scoutPositions.appendChild(empty);
    } else {
      data.positions.forEach((p) => {
        const card = document.createElement('div');
        card.className = 'health-card';
        card.innerHTML = `
          <strong>${p.symbol}</strong><br>
          Qty: ${p.qty.toFixed(6)}<br>
          Entry: ${p.entry_price.toFixed(6)} → Target: ${p.target_price.toFixed(6)}
        `;
        scoutPositions.appendChild(card);
      });
    }
  } catch (err) {
    scoutChip.textContent = 'Scout unavailable';
    scoutChip.className = 'chip chip-danger';
  }
}

async function toggleScout(shouldStart) {
  const use_testnet = (binanceEnvSelect?.value || 'mainnet') === 'testnet';
  const url = shouldStart ? '/api/listings/binance/scout/start' : '/api/listings/binance/scout/stop';
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: shouldStart ? JSON.stringify({ use_testnet }) : undefined,
    });
    if (!resp.ok) {
      const data = await resp.json();
      showToast(`Scout request failed: ${data.detail || resp.statusText}`, 'danger');
      return;
    }
    showToast(shouldStart ? 'Listing scout started' : 'Listing scout stopped', 'success');
    await loadScoutStatus();
  } catch (err) {
    console.error(err);
    showToast('Scout request failed', 'danger');
  }
}
