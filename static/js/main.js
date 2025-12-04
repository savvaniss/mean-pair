const toastEl = document.getElementById('toast');

function toast(msg, isError = false) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.style.borderColor = isError ? 'var(--danger)' : 'var(--border)';
  toastEl.classList.remove('hidden');
  requestAnimationFrame(() => toastEl.classList.add('show'));
  setTimeout(() => toastEl.classList.remove('show'), 2500);
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : {};
}

function asTable(rows) {
  if (!rows || rows.length === 0) return '<p class="muted">No data</p>';
  const headers = Object.keys(rows[0]);
  const headHtml = headers.map((h) => `<th>${h}</th>`).join('');
  const body = rows
    .map((r) => `<tr>${headers.map((h) => `<td>${r[h]}</td>`).join('')}</tr>`)
    .join('');
  return `<table><thead><tr>${headHtml}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderStack(container, entries) {
  container.innerHTML = entries
    .map((e) => `<div><div class="label">${e.label}</div><div class="value">${e.value ?? '—'}</div></div>`)
    .join('');
}

function buildConfigForm(container, config, onSubmit) {
  const form = document.createElement('form');
  form.className = 'form-grid';

  Object.entries(config).forEach(([key, value]) => {
    if (key === 'available_pairs') return;
    const wrapper = document.createElement('label');
    wrapper.textContent = key;
    let input;
    if (typeof value === 'boolean') {
      input = document.createElement('select');
      input.innerHTML = '<option value="true">true</option><option value="false">false</option>';
      input.value = value ? 'true' : 'false';
    } else if (Array.isArray(value)) {
      input = document.createElement('input');
      input.value = value.join(',');
    } else {
      input = document.createElement('input');
      input.type = typeof value === 'number' ? 'number' : 'text';
      input.value = value ?? '';
      if (typeof value === 'number') input.step = '0.0001';
    }
    input.name = key;
    wrapper.appendChild(input);
    form.appendChild(wrapper);
  });

  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.textContent = 'Save';
  submit.className = 'btn primary';
  form.appendChild(submit);

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const payload = {};
    for (const el of form.elements) {
      if (!el.name) continue;
      const original = config[el.name];
      if (typeof original === 'boolean') payload[el.name] = el.value === 'true';
      else if (typeof original === 'number') payload[el.name] = Number(el.value);
      else if (Array.isArray(original)) payload[el.name] = el.value.split(',').map((s) => s.trim()).filter(Boolean);
      else payload[el.name] = el.value;
    }
    onSubmit(payload);
  });

  container.innerHTML = '';
  container.appendChild(form);
}

async function loadMeanReversion() {
  const status = await api('/status');
  const summary = document.getElementById('globalSummary');
  summary.innerHTML = `
    <div class="metric-row"><span>Pair</span><strong>${status.asset_a}/${status.asset_b}</strong></div>
    <div class="metric-row"><span>Wallet</span><strong>${status.base_balance?.toFixed?.(2)} ${status.asset_a_balance?.toFixed ? status.asset_a : ''}</strong></div>
    <div class="metric-row"><span>Realized PnL</span><strong>${status.realized_pnl_usd?.toFixed?.(2)}</strong></div>
    <div class="metric-row"><span>Mode</span><strong>${status.use_testnet ? 'Testnet' : 'Mainnet'}</strong></div>`;
  renderStack(document.getElementById('mrStatus'), [
    { label: 'Pair', value: `${status.asset_a}/${status.asset_b}` },
    { label: 'BTC', value: status.btc?.toFixed?.(2) },
    { label: 'Prices', value: `${status.price_a?.toFixed?.(4)} / ${status.price_b?.toFixed?.(4)}` },
    { label: 'Ratio / z-score', value: `${status.ratio?.toFixed?.(4)} / ${status.zscore?.toFixed?.(2)}` },
    { label: 'PnL (realized / unrealized)', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
    { label: 'Balances', value: `${status.base_balance?.toFixed?.(2)} base • ${status.asset_a_balance?.toFixed?.(2)} ${status.asset_a} • ${status.asset_b_balance?.toFixed?.(2)} ${status.asset_b}` },
    { label: 'Mode', value: status.use_testnet ? 'Testnet' : 'Mainnet' },
    { label: 'Bot enabled', value: status.enabled ? 'Yes' : 'No' },
  ]);

  const dirSelect = document.querySelector('#mrManual select[name="direction"]');
  dirSelect.innerHTML = `<option value="${status.asset_a}->${status.asset_b}">${status.asset_a} → ${status.asset_b}</option>` +
    `<option value="${status.asset_b}->${status.asset_a}">${status.asset_b} → ${status.asset_a}</option>`;

  const next = await api('/next_signal');
  renderStack(document.getElementById('mrNext'), [
    { label: 'Direction', value: next.direction },
    { label: 'Reason', value: next.reason },
    { label: 'Ratio / z', value: `${next.ratio?.toFixed?.(4)} / ${next.zscore?.toFixed?.(2)}` },
    { label: 'Bands', value: `${next.lower_band?.toFixed?.(4)} ↔ ${next.upper_band?.toFixed?.(4)}` },
    { label: 'Qty', value: `${next.qty_from} → ${next.qty_to}` },
  ]);

  const cfg = await api('/config');
  if (cfg.available_pairs) cfg.available_pairs = cfg.available_pairs;
  const cfgContainer = document.getElementById('mrConfig');
  cfgContainer.innerHTML = '';
  if (cfg.available_pairs) {
    const select = document.createElement('select');
    cfg.available_pairs.forEach(([a, b]) => {
      const opt = document.createElement('option');
      opt.value = `${a}|${b}`;
      opt.textContent = `${a}/${b}`;
      if (cfg.asset_a === a && cfg.asset_b === b) opt.selected = true;
      select.appendChild(opt);
    });
    const label = document.createElement('label');
    label.textContent = 'Trading pair';
    label.appendChild(select);
    cfgContainer.appendChild(label);
    cfgContainer.appendChild(document.createElement('hr'));
  }
  buildConfigForm(cfgContainer, cfg, async (payload) => {
    if (cfg.available_pairs) {
      const [a, b] = cfgContainer.querySelector('select')?.value?.split('|') ?? [cfg.asset_a, cfg.asset_b];
      payload.asset_a = a;
      payload.asset_b = b;
      payload.available_pairs = cfg.available_pairs;
    }
    await api('/config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Mean reversion config saved');
    loadMeanReversion();
  });

  const history = await api('/history?limit=120');
  document.getElementById('mrHistory').innerHTML = asTable(
    history.map((h) => ({ ts: h.ts.split('T')[1].slice(0, 8), ratio: h.ratio.toFixed(4), z: h.zscore.toFixed(2) }))
  );

  const trades = await api('/trades');
  document.getElementById('mrTrades').innerHTML = asTable(
    trades.map((t) => ({ ts: t.ts.split('T')[1].slice(0, 8), side: t.side, qty: `${t.qty_from} → ${t.qty_to}`, pnl: t.pnl_usd.toFixed(2) }))
  );

  const pairs = await api('/pair_history');
  document.getElementById('mrPairs').innerHTML = asTable(pairs.map((p) => ({ pair: `${p.asset_a}/${p.asset_b}`, samples: p.samples, z: p.last_z?.toFixed?.(2) })));
}

async function loadBollinger() {
  const status = await api('/boll_status');
  renderStack(document.getElementById('bollStatus'), [
    { label: 'Symbol', value: status.symbol || 'Not set' },
    { label: 'Price', value: status.price?.toFixed?.(4) },
    { label: 'Bands', value: `${status.lower?.toFixed?.(4)} ↔ ${status.upper?.toFixed?.(4)}` },
    { label: 'Position', value: status.position },
    { label: 'PnL', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
    { label: 'Quote balance', value: status.quote_balance?.toFixed?.(2) },
    { label: 'Enabled', value: status.enabled ? 'Yes' : 'No' },
  ]);

  const cfg = await api('/boll_config');
  buildConfigForm(document.getElementById('bollConfig'), cfg, async (payload) => {
    await api('/boll_config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Bollinger config saved');
    loadBollinger();
  });

  const history = await api('/boll_history');
  document.getElementById('bollHistory').innerHTML = asTable(
    history.map((h) => ({ ts: h.ts.split('T')[1].slice(0, 8), price: h.price.toFixed(4), upper: h.upper.toFixed(4), lower: h.lower.toFixed(4) }))
  );

  const trades = await api('/boll_trades');
  document.getElementById('bollTrades').innerHTML = asTable(
    trades.map((t) => ({ ts: t.ts.split('T')[1].slice(0, 8), side: t.side, qty: t.qty.toFixed(4), pnl: t.pnl_usd.toFixed(2) }))
  );
}

async function loadTrend() {
  const status = await api('/trend_status');
  renderStack(document.getElementById('trendStatus'), [
    { label: 'Symbol', value: status.symbol || 'Not set' },
    { label: 'Price', value: status.price?.toFixed?.(4) },
    { label: 'Fast/Slow EMA', value: `${status.fast_ema?.toFixed?.(4)} / ${status.slow_ema?.toFixed?.(4)}` },
    { label: 'ATR', value: status.atr?.toFixed?.(4) },
    { label: 'Position', value: status.position },
    { label: 'PnL', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
  ]);

  const cfg = await api('/trend_config');
  buildConfigForm(document.getElementById('trendConfig'), cfg, async (payload) => {
    await api('/trend_config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Trend config saved');
    loadTrend();
  });

  const history = await api('/trend_history');
  document.getElementById('trendHistory').innerHTML = asTable(
    history.map((h) => ({ ts: h.ts.split('T')[1].slice(0, 8), price: h.price.toFixed(4), fast: h.fast_ema.toFixed(4), slow: h.slow_ema.toFixed(4) }))
  );
}

async function loadRS() {
  const status = await api('/rs_status');
  renderStack(document.getElementById('rsStatus'), [
    { label: 'Quote asset', value: status.quote_asset },
    { label: 'Quote balance', value: status.quote_balance?.toFixed?.(2) },
    { label: 'Enabled', value: status.enabled ? 'Yes' : 'No' },
    { label: 'Last rebalance', value: status.last_rebalance || '—' },
    { label: 'Active spreads', value: status.active_spreads.map((s) => `${s.long}/${s.short}`).join(', ') || '—' },
    { label: 'Top', value: status.top_symbols.map((s) => `${s.symbol} (${s.rs.toFixed(2)})`).join(', ') },
    { label: 'Bottom', value: status.bottom_symbols.map((s) => `${s.symbol} (${s.rs.toFixed(2)})`).join(', ') },
  ]);

  const cfg = await api('/rs_config');
  buildConfigForm(document.getElementById('rsConfig'), cfg, async (payload) => {
    await api('/rs_config', { method: 'POST', body: JSON.stringify(payload) });
    toast('RS config saved');
    loadRS();
  });

  const history = await api('/rs_history');
  document.getElementById('rsHistory').innerHTML = asTable(
    history.map((h) => ({ ts: h.ts.split('T')[1].slice(0, 8), symbol: h.symbol, rs: h.rs.toFixed(2) }))
  );
}

async function loadTrading() {
  const balances = await api('/trading/balances');
  document.getElementById('tradingBalances').innerHTML = balances
    .map((acc) => `<h4>${acc.account} (${acc.use_testnet ? 'testnet' : 'mainnet'})</h4>${asTable(acc.balances)}`)
    .join('');
}

async function loadLiquidation() {
  const status = await api('/liquidation/status');
  renderStack(document.getElementById('liqStatus'), [
    { label: 'Symbol', value: status.symbol },
    { label: 'Signal', value: status.latest_signal ? status.latest_signal.reason : 'No signal' },
    { label: 'Clusters', value: status.latest_clusters?.length || 0 },
    { label: 'Auto trade', value: status.config?.auto_trade ? 'Yes' : 'No' },
  ]);

  if (status.config) {
    buildConfigForm(document.getElementById('liqConfig'), status.config, async (payload) => {
      await api('/liquidation/config', { method: 'POST', body: JSON.stringify(payload) });
      toast('Liquidation config saved');
      loadLiquidation();
    });
  }
}

function wireActions() {
  document.getElementById('refreshAll').addEventListener('click', () => refreshAll());
  document.getElementById('syncBalances').addEventListener('click', async () => { await api('/sync_state_from_balances', { method: 'POST' }); toast('Balances synced'); refreshAll(); });
  document.getElementById('nextSignal').addEventListener('click', async () => { const n = await api('/next_signal'); toast(`Next: ${n.direction} (${n.reason})`); renderStack(document.getElementById('mrNext'), [ { label: 'Direction', value: n.direction }, { label: 'Reason', value: n.reason } ]); });

  document.getElementById('mrStart').addEventListener('click', async () => { await api('/start', { method: 'POST' }); toast('Mean reversion started'); refreshAll(); });
  document.getElementById('mrStop').addEventListener('click', async () => { await api('/stop', { method: 'POST' }); toast('Mean reversion stopped'); refreshAll(); });
  document.getElementById('mrManual').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    data.notional_usd = data.notional_usd ? Number(data.notional_usd) : null;
    data.from_asset_qty = data.from_asset_qty ? Number(data.from_asset_qty) : null;
    await api('/manual_trade', { method: 'POST', body: JSON.stringify(data) });
    toast('Manual MR trade sent');
    refreshAll();
  });

  document.getElementById('bollStart').addEventListener('click', async () => { await api('/boll_start', { method: 'POST' }); toast('Bollinger started'); refreshAll(); });
  document.getElementById('bollStop').addEventListener('click', async () => { await api('/boll_stop', { method: 'POST' }); toast('Bollinger stopped'); refreshAll(); });
  document.getElementById('bollManual').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    data.qty_base = Number(data.qty_base);
    await api('/bollinger_manual_sell', { method: 'POST', body: JSON.stringify(data) });
    toast('Manual sell placed');
    refreshAll();
  });

  document.getElementById('trendStart').addEventListener('click', async () => { await api('/trend_start', { method: 'POST' }); toast('Trend bot started'); refreshAll(); });
  document.getElementById('trendStop').addEventListener('click', async () => { await api('/trend_stop', { method: 'POST' }); toast('Trend bot stopped'); refreshAll(); });

  document.getElementById('rsStart').addEventListener('click', async () => { await api('/rs_start', { method: 'POST' }); toast('RS bot started'); refreshAll(); });
  document.getElementById('rsStop').addEventListener('click', async () => { await api('/rs_stop', { method: 'POST' }); toast('RS bot stopped'); refreshAll(); });

  document.getElementById('tradingOrder').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const data = Object.fromEntries(form.entries());
    data.use_testnet = data.use_testnet === 'true';
    data.qty_base = Number(data.qty_base);
    await api('/trading/order', { method: 'POST', body: JSON.stringify(data) });
    toast('Manual order sent');
    loadTrading();
  });

  document.getElementById('liqScan').addEventListener('click', async () => { await api('/liquidation/scan', { method: 'POST' }); toast('Scan complete'); refreshAll(); });
  document.getElementById('liqExecute').addEventListener('click', async () => { await api('/liquidation/execute', { method: 'POST' }); toast('Execution attempted'); refreshAll(); });
}

async function refreshAll() {
  try {
    await Promise.all([loadMeanReversion(), loadBollinger(), loadTrend(), loadRS(), loadTrading(), loadLiquidation()]);
  } catch (e) {
    console.error(e);
    toast(e.message, true);
  }
}

async function init() {
  wireActions();
  await refreshAll();
}

init();
