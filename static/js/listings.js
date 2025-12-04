let tableBody;
let refreshBtn;
let healthDiv;
let inputs;
let listingsInterval;
let healthInterval;

export function initListings() {
  tableBody = document.querySelector('#listingsTable tbody');
  refreshBtn = document.querySelector('#refreshListings');
  healthDiv = document.querySelector('#healthStatus');
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

  clearInterval(listingsInterval);
  clearInterval(healthInterval);
  listingsInterval = setInterval(fetchListings, 15000);
  healthInterval = setInterval(fetchHealth, 60000);
}

export async function refreshListings() {
  if (!tableBody) return;
  await Promise.all([fetchListings(), fetchHealth()]);
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
    row.innerHTML = `
      <td>${item.symbol}</td>
      <td>${item.name}</td>
      <td>${item.pair}</td>
      <td>${item.network || '-'}</td>
      <td>${item.source} (${item.exchange_type.toUpperCase()})</td>
      <td>${new Date(item.listed_at).toLocaleString()}</td>
      <td>${item.url ? `<a href="${item.url}" target="_blank">View</a>` : '-'}</td>
    `;
    tableBody.appendChild(row);
  });
}

function renderErrorRow(message) {
  tableBody.innerHTML = '';
  const row = document.createElement('tr');
  row.innerHTML = `<td colspan="7" style="text-align:center;">${message}</td>`;
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
