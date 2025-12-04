const tableBody = document.querySelector('#listingsTable tbody');
const refreshBtn = document.querySelector('#refresh');
const healthDiv = document.querySelector('#healthStatus');

const inputs = {
  exchangeType: document.getElementById('exchangeType'),
  exchange: document.getElementById('exchange'),
  network: document.getElementById('network'),
  minutes: document.getElementById('minutes'),
  search: document.getElementById('search'),
  sort: document.getElementById('sort'),
};

async function fetchListings() {
  const params = new URLSearchParams();
  if (inputs.exchangeType.value) params.append('exchange_type', inputs.exchangeType.value);
  if (inputs.exchange.value) params.append('exchange', inputs.exchange.value);
  if (inputs.network.value) params.append('network', inputs.network.value);
  if (inputs.minutes.value) params.append('minutes', inputs.minutes.value);
  if (inputs.search.value) params.append('search', inputs.search.value);
  if (inputs.sort.value) params.append('sort', inputs.sort.value);

  const response = await fetch(`/api/listings/latest?${params.toString()}`);
  const data = await response.json();
  renderTable(data);
}

async function fetchHealth() {
  const response = await fetch('/api/listings/health');
  const data = await response.json();
  renderHealth(data);
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

refreshBtn.addEventListener('click', () => {
  fetchListings();
  fetchHealth();
});

fetchListings();
fetchHealth();
setInterval(fetchListings, 15000);
setInterval(fetchHealth, 60000);
