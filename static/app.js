const toastEl = document.getElementById('toast');
const modals = document.querySelectorAll('.modal');

function showToast(message, tone = 'neutral') {
  if (!toastEl) return;
  toastEl.textContent = message;
  toastEl.style.borderColor = tone === 'danger' ? 'rgba(255,107,107,0.6)' : 'var(--border)';
  toastEl.classList.add('show');
  setTimeout(() => toastEl.classList.remove('show'), 2600);
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.style.display = 'flex';
}
function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.style.display = 'none';
}

modals.forEach((m) => m.addEventListener('click', (e) => {
  if (e.target === m) closeModal(m.id);
}));

document.querySelectorAll('[data-close]').forEach((btn) => {
  btn.addEventListener('click', () => closeModal(btn.dataset.close));
});

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

function renderStats(container, items) {
  if (!container) return;
  container.innerHTML = '';
  items.forEach(({ label, value }) => {
    const div = document.createElement('div');
    div.className = 'stat';
    div.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
    container.appendChild(div);
  });
}

// =======================
// Mean reversion section
// =======================
let mrChart;
async function loadMrConfig() {
  try {
    const cfg = await fetchJson('/config');
    const pairSelect = document.getElementById('mr-pair');
    pairSelect.innerHTML = '';
    cfg.available_pairs.forEach(([a, b]) => {
      const opt = document.createElement('option');
      opt.value = `${a}|${b}`;
      opt.textContent = `${a} / ${b}`;
      if (a === cfg.asset_a && b === cfg.asset_b) opt.selected = true;
      pairSelect.appendChild(opt);
    });
    document.getElementById('mr-poll').value = cfg.poll_interval_sec;
    document.getElementById('mr-window').value = cfg.window_size;
    document.getElementById('mr-entry').value = cfg.z_entry;
    document.getElementById('mr-exit').value = cfg.z_exit;
    document.getElementById('mr-notional-usd').value = cfg.trade_notional_usd;
    document.getElementById('mr-use-all').checked = cfg.use_all_balance;
    document.getElementById('mr-use-testnet').checked = cfg.use_testnet;
    document.getElementById('mr-use-ratio').checked = cfg.use_ratio_thresholds;
    document.getElementById('mr-sell-th').value = cfg.sell_ratio_threshold;
    document.getElementById('mr-buy-th').value = cfg.buy_ratio_threshold;
  } catch (err) {
    showToast(`Config load failed: ${err.message}`, 'danger');
  }
}

async function saveMrConfig(evt) {
  evt.preventDefault();
  const [asset_a, asset_b] = document.getElementById('mr-pair').value.split('|');
  const payload = {
    asset_a,
    asset_b,
    poll_interval_sec: Number(document.getElementById('mr-poll').value),
    window_size: Number(document.getElementById('mr-window').value),
    z_entry: Number(document.getElementById('mr-entry').value),
    z_exit: Number(document.getElementById('mr-exit').value),
    trade_notional_usd: Number(document.getElementById('mr-notional-usd').value),
    use_all_balance: document.getElementById('mr-use-all').checked,
    use_testnet: document.getElementById('mr-use-testnet').checked,
    use_ratio_thresholds: document.getElementById('mr-use-ratio').checked,
    sell_ratio_threshold: Number(document.getElementById('mr-sell-th').value),
    buy_ratio_threshold: Number(document.getElementById('mr-buy-th').value),
  };
  try {
    await fetchJson('/config', { method: 'POST', body: JSON.stringify(payload) });
    closeModal('mr-config-modal');
    showToast('Mean reversion settings saved');
    loadMrStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function suggestMrConfig() {
  try {
    const cfg = await fetchJson('/config_best');
    document.getElementById('mr-poll').value = cfg.poll_interval_sec;
    document.getElementById('mr-window').value = cfg.window_size;
    document.getElementById('mr-entry').value = cfg.z_entry;
    document.getElementById('mr-exit').value = cfg.z_exit;
    document.getElementById('mr-notional-usd').value = cfg.trade_notional_usd;
    document.getElementById('mr-use-all').checked = cfg.use_all_balance;
    showToast('Suggested settings applied');
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function loadMrStatus() {
  try {
    const data = await fetchJson('/status');
    document.getElementById('mr-enabled').textContent = data.enabled ? 'Running' : 'Stopped';
    const stats = [
      { label: 'Pair', value: `${data.asset_a} / ${data.asset_b}` },
      { label: 'Price A', value: data.price_a.toFixed(4) },
      { label: 'Price B', value: data.price_b.toFixed(4) },
      { label: 'Ratio', value: data.ratio.toFixed(4) },
      { label: 'Z-score', value: data.zscore.toFixed(3) },
      { label: 'Mean ratio', value: data.mean_ratio.toFixed(4) },
      { label: 'Std dev', value: data.std_ratio.toFixed(4) },
      { label: 'Current asset', value: `${data.current_asset} (${data.current_qty.toFixed(4)})` },
      { label: 'Realized PNL', value: `$${data.realized_pnl_usd.toFixed(2)}` },
      { label: 'Unrealized PNL', value: `$${data.unrealized_pnl_usd.toFixed(2)}` },
      { label: 'Balances', value: `Base ${data.base_balance.toFixed(2)} | ${data.asset_a} ${data.asset_a_balance.toFixed(2)} | ${data.asset_b} ${data.asset_b_balance.toFixed(2)}` },
    ];
    renderStats(document.getElementById('mr-status'), stats);
    const dirSelect = document.getElementById('mr-direction');
    dirSelect.innerHTML = '';
    const options = [
      { value: `${data.asset_a}->${data.asset_b}`, label: `${data.asset_a} → ${data.asset_b}` },
      { value: `${data.asset_b}->${data.asset_a}`, label: `${data.asset_b} → ${data.asset_a}` },
    ];
    options.forEach((opt) => {
      const o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.label;
      dirSelect.appendChild(o);
    });
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function loadMrHistory() {
  try {
    const history = await fetchJson('/history');
    document.getElementById('mr-last-refresh').textContent = `Last ${new Date().toLocaleTimeString()}`;
    const ctx = document.getElementById('mr-chart').getContext('2d');
    const labels = history.map((h) => new Date(h.ts).toLocaleTimeString());
    const ratio = history.map((h) => h.ratio);
    const zscore = history.map((h) => h.zscore);
    if (mrChart) mrChart.destroy();
    mrChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Ratio', data: ratio, borderColor: '#d5b26e', tension: 0.3, fill: false },
          { label: 'Z-score', data: zscore, borderColor: '#7ad0ff', tension: 0.3, yAxisID: 'y1' },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#f3e8ff' } } },
        scales: {
          x: { ticks: { color: '#c8bfd6' }, grid: { color: 'rgba(255,255,255,0.05)' } },
          y: { ticks: { color: '#c8bfd6' }, grid: { color: 'rgba(255,255,255,0.05)' } },
          y1: { position: 'right', ticks: { color: '#c8bfd6' }, grid: { display: false } },
        },
      },
    });
  } catch (err) {
    showToast('History unavailable');
  }
}

async function loadMrTrades() {
  try {
    const trades = await fetchJson('/trades');
    const tbody = document.querySelector('#mr-trades tbody');
    tbody.innerHTML = '';
    trades.forEach((t) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${new Date(t.ts).toLocaleString()}</td><td>${t.side}</td><td>${t.qty_from.toFixed(4)}</td><td>${t.price.toFixed(4)}</td><td>${t.pnl_usd.toFixed(2)}</td>`;
      tbody.appendChild(tr);
    });
  } catch (err) {
    showToast('Could not load trades');
  }
}

async function loadMrPairHealth() {
  try {
    const pairs = await fetchJson('/pair_history');
    const rail = document.getElementById('mr-pair-health');
    rail.innerHTML = '';
    pairs.forEach((p) => {
      const pill = document.createElement('div');
      pill.className = 'pill';
      pill.innerHTML = `<strong>${p.asset_a}/${p.asset_b}</strong> · ${p.correlation.toFixed(3)} corr · ${p.samples} samples`;
      rail.appendChild(pill);
    });
  } catch (err) {
    showToast('Pair health unavailable');
  }
}

async function mrManualTrade(evt) {
  evt.preventDefault();
  const direction = document.getElementById('mr-direction').value;
  const notional = document.getElementById('mr-notional').value;
  const qty = document.getElementById('mr-qty').value;
  const payload = { direction };
  if (notional) payload.notional_usd = Number(notional);
  if (qty) payload.from_asset_qty = Number(qty);
  try {
    await fetchJson('/manual_trade', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Manual trade sent');
    loadMrStatus();
    loadMrTrades();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function mrStartStop(start) {
  try {
    await fetchJson(start ? '/start' : '/stop', { method: 'POST' });
    showToast(start ? 'Bot started' : 'Bot stopped');
    loadMrStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function mrSync() {
  try {
    await fetchJson('/sync_state_from_balances', { method: 'POST' });
    showToast('Synced from balances');
    loadMrStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function mrNext() {
  try {
    const next = await fetchJson('/next_signal');
    showToast(`${next.direction}: ${next.reason}`);
  } catch (err) {
    showToast('No signal available', 'danger');
  }
}

// =======================
// Bollinger
// =======================
async function loadBollConfig() {
  try {
    const cfg = await fetchJson('/boll_config');
    document.getElementById('boll-symbol').value = cfg.symbol;
    document.getElementById('boll-poll').value = cfg.poll_interval_sec;
    document.getElementById('boll-window').value = cfg.window_size;
    document.getElementById('boll-std').value = cfg.num_std;
    document.getElementById('boll-max').value = cfg.max_position_usd;
    document.getElementById('boll-cool').value = cfg.cooldown_sec;
    document.getElementById('boll-sl').value = cfg.stop_loss_pct;
    document.getElementById('boll-tp').value = cfg.take_profit_pct;
    document.getElementById('boll-use-all').checked = cfg.use_all_balance;
    document.getElementById('boll-testnet').checked = cfg.use_testnet;
  } catch (err) {
    showToast('Bollinger config missing', 'danger');
  }
}

async function saveBollConfig(evt) {
  evt.preventDefault();
  const payload = {
    symbol: document.getElementById('boll-symbol').value,
    poll_interval_sec: Number(document.getElementById('boll-poll').value),
    window_size: Number(document.getElementById('boll-window').value),
    num_std: Number(document.getElementById('boll-std').value),
    max_position_usd: Number(document.getElementById('boll-max').value),
    cooldown_sec: Number(document.getElementById('boll-cool').value),
    stop_loss_pct: Number(document.getElementById('boll-sl').value),
    take_profit_pct: Number(document.getElementById('boll-tp').value),
    use_all_balance: document.getElementById('boll-use-all').checked,
    use_testnet: document.getElementById('boll-testnet').checked,
  };
  try {
    await fetchJson('/boll_config', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Bollinger config saved');
    closeModal('boll-config-modal');
    loadBollStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function loadBollStatus() {
  try {
    const data = await fetchJson('/boll_status');
    document.getElementById('boll-enabled').textContent = data.enabled ? 'Running' : 'Stopped';
    const stats = [
      { label: 'Symbol', value: data.symbol || '—' },
      { label: 'Price', value: data.price.toFixed(4) },
      { label: 'MA', value: data.ma.toFixed(4) },
      { label: 'Upper', value: data.upper.toFixed(4) },
      { label: 'Lower', value: data.lower.toFixed(4) },
      { label: 'Position', value: `${data.position} (${data.qty_asset.toFixed(4)})` },
      { label: 'Realized PNL', value: `$${data.realized_pnl_usd.toFixed(2)}` },
      { label: 'Unrealized PNL', value: `$${data.unrealized_pnl_usd.toFixed(2)}` },
      { label: 'Quote balance', value: data.quote_balance.toFixed(2) },
      { label: 'Mode', value: data.use_testnet ? 'Testnet' : 'Live' },
    ];
    renderStats(document.getElementById('boll-status'), stats);
    document.getElementById('boll-sell-symbol').value = data.symbol || '';
  } catch (err) {
    showToast('Bollinger status unavailable');
  }
}

async function bollStartStop(start) {
  try {
    await fetchJson(start ? '/boll_start' : '/boll_stop', { method: 'POST' });
    showToast(start ? 'Bollinger running' : 'Bollinger halted');
    loadBollStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function bollSell(evt) {
  evt.preventDefault();
  const symbol = document.getElementById('boll-sell-symbol').value;
  const qty = Number(document.getElementById('boll-sell-qty').value);
  try {
    await fetchJson('/bollinger_manual_sell', { method: 'POST', body: JSON.stringify({ symbol, qty_base: qty }) });
    showToast('Manual sell placed');
    loadBollStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

// =======================
// Trend
// =======================
async function loadTrendConfig() {
  try {
    const cfg = await fetchJson('/trend_config');
    document.getElementById('trend-symbol').value = cfg.symbol;
    document.getElementById('trend-poll').value = cfg.poll_interval_sec;
    document.getElementById('trend-fast').value = cfg.fast_window;
    document.getElementById('trend-slow').value = cfg.slow_window;
    document.getElementById('trend-atr').value = cfg.atr_window;
    document.getElementById('trend-testnet').checked = cfg.use_testnet;
  } catch (err) {
    showToast('Trend config missing', 'danger');
  }
}

async function saveTrendConfig(evt) {
  evt.preventDefault();
  const payload = {
    symbol: document.getElementById('trend-symbol').value,
    poll_interval_sec: Number(document.getElementById('trend-poll').value),
    fast_window: Number(document.getElementById('trend-fast').value),
    slow_window: Number(document.getElementById('trend-slow').value),
    atr_window: Number(document.getElementById('trend-atr').value),
    use_testnet: document.getElementById('trend-testnet').checked,
  };
  try {
    await fetchJson('/trend_config', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Trend config saved');
    closeModal('trend-config-modal');
    loadTrendStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function loadTrendStatus() {
  try {
    const data = await fetchJson('/trend_status');
    document.getElementById('trend-enabled').textContent = data.enabled ? 'Running' : 'Stopped';
    const stats = [
      { label: 'Symbol', value: data.symbol || '—' },
      { label: 'Price', value: data.price.toFixed(4) },
      { label: 'Fast EMA', value: data.fast_ema.toFixed(4) },
      { label: 'Slow EMA', value: data.slow_ema.toFixed(4) },
      { label: 'ATR', value: data.atr.toFixed(4) },
      { label: 'Position', value: `${data.position} (${data.qty_asset.toFixed(4)})` },
      { label: 'Realized PNL', value: `$${data.realized_pnl_usd.toFixed(2)}` },
      { label: 'Unrealized PNL', value: `$${data.unrealized_pnl_usd.toFixed(2)}` },
      { label: 'Quote balance', value: data.quote_balance.toFixed(2) },
      { label: 'Mode', value: data.use_testnet ? 'Testnet' : 'Live' },
    ];
    renderStats(document.getElementById('trend-status'), stats);
  } catch (err) {
    showToast('Trend status unavailable');
  }
}

async function trendStartStop(start) {
  try {
    await fetchJson(start ? '/trend_start' : '/trend_stop', { method: 'POST' });
    showToast(start ? 'Trend bot started' : 'Trend bot stopped');
    loadTrendStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

// =======================
// Relative strength
// =======================
async function loadRsConfig() {
  try {
    const cfg = await fetchJson('/rs_config');
    document.getElementById('rs-symbols').value = (cfg.symbols || []).join(', ');
    document.getElementById('rs-poll').value = cfg.poll_interval_sec;
    document.getElementById('rs-lookback').value = cfg.lookback_window;
    document.getElementById('rs-rebalance').value = cfg.rebalance_interval_sec;
    document.getElementById('rs-top').value = cfg.top_n;
    document.getElementById('rs-bottom').value = cfg.bottom_n;
    document.getElementById('rs-gap').value = cfg.min_rs_gap;
    document.getElementById('rs-max-notional').value = cfg.max_notional_usd;
    document.getElementById('rs-all').checked = cfg.use_all_balance;
    document.getElementById('rs-testnet').checked = cfg.use_testnet;
  } catch (err) {
    showToast('RS config missing', 'danger');
  }
}

async function saveRsConfig(evt) {
  evt.preventDefault();
  const payload = {
    symbols: document.getElementById('rs-symbols').value.split(',').map((s) => s.trim()).filter(Boolean),
    poll_interval_sec: Number(document.getElementById('rs-poll').value),
    lookback_window: Number(document.getElementById('rs-lookback').value),
    rebalance_interval_sec: Number(document.getElementById('rs-rebalance').value),
    top_n: Number(document.getElementById('rs-top').value),
    bottom_n: Number(document.getElementById('rs-bottom').value),
    min_rs_gap: Number(document.getElementById('rs-gap').value),
    max_notional_usd: Number(document.getElementById('rs-max-notional').value),
    use_all_balance: document.getElementById('rs-all').checked,
    use_testnet: document.getElementById('rs-testnet').checked,
  };
  try {
    await fetchJson('/rs_config', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Relative strength config saved');
    closeModal('rs-config-modal');
    loadRsStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function loadRsStatus() {
  try {
    const data = await fetchJson('/rs_status');
    document.getElementById('rs-enabled').textContent = data.enabled ? 'Running' : 'Stopped';
    const tbody = document.querySelector('#rs-table tbody');
    tbody.innerHTML = '';
    (data.top_symbols || []).forEach((s) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>Top</td><td>${s.symbol}</td><td>${s.rs.toFixed(3)}</td><td>${s.price.toFixed(4)}</td>`;
      tbody.appendChild(tr);
    });
    (data.bottom_symbols || []).forEach((s) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>Bottom</td><td>${s.symbol}</td><td>${s.rs.toFixed(3)}</td><td>${s.price.toFixed(4)}</td>`;
      tbody.appendChild(tr);
    });
    const spreadRail = document.getElementById('rs-spreads');
    spreadRail.innerHTML = '';
    (data.active_spreads || []).forEach((sp) => {
      const pill = document.createElement('div');
      pill.className = 'pill';
      pill.innerHTML = `${sp.long} vs ${sp.short} · gap ${sp.rs_gap.toFixed(2)} · $${sp.notional_usd}`;
      spreadRail.appendChild(pill);
    });
  } catch (err) {
    showToast('RS status unavailable');
  }
}

async function rsStartStop(start) {
  try {
    await fetchJson(start ? '/rs_start' : '/rs_stop', { method: 'POST' });
    showToast(start ? 'RS bot started' : 'RS bot stopped');
    loadRsStatus();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

// =======================
// Trading desk
// =======================
async function loadBalances() {
  try {
    const data = await fetchJson('/trading/balances');
    const tbody = document.querySelector('#trading-balances tbody');
    tbody.innerHTML = '';
    data.forEach((account) => {
      if (!account.balances || !account.balances.length) {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${account.account}</td><td colspan="3">${account.error || 'No balances'}</td>`;
        tbody.appendChild(row);
        return;
      }
      account.balances.forEach((bal) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${account.account}</td><td>${bal.asset}</td><td>${bal.free}</td><td>${bal.locked}</td>`;
        tbody.appendChild(tr);
      });
    });
  } catch (err) {
    showToast('Balances unavailable');
  }
}

async function placeOrder(evt) {
  evt.preventDefault();
  const payload = {
    account: document.getElementById('trade-account').value,
    use_testnet: document.getElementById('trade-env').value === 'true',
    symbol: document.getElementById('trade-symbol').value,
    side: document.getElementById('trade-side').value,
    qty_base: Number(document.getElementById('trade-qty').value),
  };
  try {
    await fetchJson('/trading/order', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Order placed');
    loadBalances();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

// =======================
// Wire everything
// =======================
function bindUI() {
  document.getElementById('mr-config-open').addEventListener('click', () => { openModal('mr-config-modal'); loadMrConfig(); });
  document.getElementById('mr-config-form').addEventListener('submit', saveMrConfig);
  document.getElementById('mr-config-best').addEventListener('click', suggestMrConfig);
  document.getElementById('mr-sync').addEventListener('click', mrSync);
  document.getElementById('mr-next').addEventListener('click', mrNext);
  document.getElementById('mr-start').addEventListener('click', () => mrStartStop(true));
  document.getElementById('mr-stop').addEventListener('click', () => mrStartStop(false));
  document.getElementById('mr-manual-form').addEventListener('submit', mrManualTrade);

  document.getElementById('boll-config-open').addEventListener('click', () => { openModal('boll-config-modal'); loadBollConfig(); });
  document.getElementById('boll-config-form').addEventListener('submit', saveBollConfig);
  document.getElementById('boll-start').addEventListener('click', () => bollStartStop(true));
  document.getElementById('boll-stop').addEventListener('click', () => bollStartStop(false));
  document.getElementById('boll-sell-form').addEventListener('submit', bollSell);

  document.getElementById('trend-config-open').addEventListener('click', () => { openModal('trend-config-modal'); loadTrendConfig(); });
  document.getElementById('trend-config-form').addEventListener('submit', saveTrendConfig);
  document.getElementById('trend-start').addEventListener('click', () => trendStartStop(true));
  document.getElementById('trend-stop').addEventListener('click', () => trendStartStop(false));

  document.getElementById('rs-config-open').addEventListener('click', () => { openModal('rs-config-modal'); loadRsConfig(); });
  document.getElementById('rs-config-form').addEventListener('submit', saveRsConfig);
  document.getElementById('rs-start').addEventListener('click', () => rsStartStop(true));
  document.getElementById('rs-stop').addEventListener('click', () => rsStartStop(false));

  document.getElementById('trading-form').addEventListener('submit', placeOrder);
}

async function initialLoad() {
  bindUI();
  await Promise.all([
    loadMrStatus(),
    loadMrHistory(),
    loadMrTrades(),
    loadMrPairHealth(),
    loadBollStatus(),
    loadTrendStatus(),
    loadRsStatus(),
    loadBalances(),
  ]);
}

document.addEventListener('DOMContentLoaded', initialLoad);
