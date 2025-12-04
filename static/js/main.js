const toastEl = document.getElementById('toast');
const autoRefreshMs = 20000;

function toast(msg, isError = false) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.style.borderColor = isError ? 'var(--danger)' : 'var(--accent)';
  toastEl.classList.add('show');
  setTimeout(() => toastEl.classList.remove('show'), 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : {};
}

function keyValue(container, entries) {
  container.innerHTML = entries
    .map(
      ({ label, value }) => `
        <div class="meta-row">
          <span>${label}</span>
          <span>${value ?? '—'}</span>
        </div>`
    )
    .join('');
}

function tableify(container, rows, columns) {
  if (!rows || rows.length === 0) {
    container.innerHTML = '<p class="muted">No data</p>';
    return;
  }
  const cols = columns || Object.keys(rows[0]);
  const head = cols.map((c) => `<th>${c}</th>`).join('');
  const body = rows
    .map((r) => `<tr>${cols.map((c) => `<td>${r[c] ?? ''}</td>`).join('')}</tr>`)
    .join('');
  container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function buildConfigForm(container, config, onSubmit, extra = {}) {
  const { skip = [], clear = true } = extra;

  if (clear) {
    container.innerHTML = '<h3>Configuration</h3>';
  } else if (!container.querySelector('h3')) {
    const h = document.createElement('h3');
    h.textContent = 'Configuration';
    container.prepend(h);
  }

  const form = document.createElement('form');
  form.className = 'form-grid';

  Object.entries(config).forEach(([key, value]) => {
    if (skip.includes(key)) return;
    const field = document.createElement('label');
    field.className = 'field';
    field.textContent = key;

    let input;
    if (typeof value === 'boolean') {
      input = document.createElement('select');
      input.innerHTML = '<option value="true">true</option><option value="false">false</option>';
      input.value = value ? 'true' : 'false';
    } else if (Array.isArray(value)) {
      input = document.createElement('textarea');
      input.rows = 2;
      input.value = value.join(', ');
    } else {
      input = document.createElement('input');
      input.type = typeof value === 'number' ? 'number' : 'text';
      input.step = '0.0001';
      input.value = value ?? '';
    }

    input.name = key;
    field.appendChild(input);
    form.appendChild(field);
  });

  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'primary';
  submit.textContent = 'Save config';
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

  container.appendChild(form);
}

async function loadMeanReversion() {
  const status = await api('/status');
  const overview = document.getElementById('overview');
  if (overview) {
    overview.innerHTML = `
      <div><span>Pair</span><strong>${status.asset_a}/${status.asset_b}</strong></div>
      <div><span>Wallet</span><strong>${status.base_balance?.toFixed?.(2)} base</strong></div>
      <div><span>Realized PnL</span><strong>${status.realized_pnl_usd?.toFixed?.(2)}</strong></div>
      <div><span>Mode</span><strong>${status.use_testnet ? 'Testnet' : 'Mainnet'}</strong></div>`;
  }

  keyValue(document.getElementById('mrStatus'), [
    { label: 'Pair', value: `${status.asset_a}/${status.asset_b}` },
    { label: 'Prices', value: `${status.price_a?.toFixed?.(4)} / ${status.price_b?.toFixed?.(4)}` },
    { label: 'Ratio / z-score', value: `${status.ratio?.toFixed?.(4)} / ${status.zscore?.toFixed?.(2)}` },
    { label: 'Mean / std', value: `${status.mean_ratio?.toFixed?.(4)} / ${status.std_ratio?.toFixed?.(4)}` },
    { label: 'Balances', value: `${status.base_balance?.toFixed?.(2)} • ${status.asset_a_balance?.toFixed?.(2)} ${status.asset_a} • ${status.asset_b_balance?.toFixed?.(2)} ${status.asset_b}` },
    { label: 'Current position', value: `${status.current_qty?.toFixed?.(4)} ${status.current_asset}` },
    { label: 'PnL (real / unreal)', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
    { label: 'Enabled', value: status.enabled ? 'Yes' : 'No' },
    { label: 'Mode', value: status.use_testnet ? 'Testnet' : 'Mainnet' },
  ]);

  const dir = document.querySelector('#mrManual select[name="direction"]');
  dir.innerHTML = `
    <option value="${status.asset_a}->${status.asset_b}">${status.asset_a} → ${status.asset_b}</option>
    <option value="${status.asset_b}->${status.asset_a}">${status.asset_b} → ${status.asset_a}</option>`;

  const next = await api('/next_signal');
  keyValue(document.getElementById('mrNext'), [
    { label: 'Direction', value: next.direction },
    { label: 'Reason', value: next.reason },
    { label: 'Ratio / z', value: `${next.ratio?.toFixed?.(4)} / ${next.zscore?.toFixed?.(2)}` },
    { label: 'Bands', value: `${next.lower_band?.toFixed?.(4)} ↔ ${next.upper_band?.toFixed?.(4)}` },
    { label: 'Qty', value: `${next.qty_from} → ${next.qty_to}` },
  ]);

  const cfg = await api('/config');
  const container = document.getElementById('mrConfig');
  container.innerHTML = '<h3>Configuration</h3>';

  const bestBtn = document.createElement('button');
  bestBtn.type = 'button';
  bestBtn.textContent = 'Apply best config';
  bestBtn.className = 'ghost';
  bestBtn.addEventListener('click', async () => {
    try {
      const best = await api('/config_best');
      await api('/config', { method: 'POST', body: JSON.stringify(best) });
      toast('Best config applied');
      loadMeanReversion();
    } catch (e) {
      toast(e.message, true);
    }
  });
  container.appendChild(bestBtn);

  if (cfg.available_pairs) {
    const selector = document.createElement('label');
    selector.className = 'field';
    selector.textContent = 'Trading pair';
    const select = document.createElement('select');
    cfg.available_pairs.forEach(([a, b]) => {
      const opt = document.createElement('option');
      opt.value = `${a}|${b}`;
      opt.textContent = `${a}/${b}`;
      if (cfg.asset_a === a && cfg.asset_b === b) opt.selected = true;
      select.appendChild(opt);
    });
    selector.appendChild(select);
    container.appendChild(selector);
  }

  const configFields = { ...cfg };
  delete configFields.available_pairs;
  buildConfigForm(container, configFields, async (payload) => {
    if (cfg.available_pairs) {
      const [a, b] = container.querySelector('select')?.value?.split('|') ?? [cfg.asset_a, cfg.asset_b];
      payload.asset_a = a;
      payload.asset_b = b;
      payload.available_pairs = cfg.available_pairs;
    }
    await api('/config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Mean reversion config saved');
    loadMeanReversion();
  }, { clear: false });

  const history = await api('/history?limit=120');
  tableify(
    document.getElementById('mrHistory'),
    history.map((h) => ({
      ts: h.ts.split('T')[1].slice(0, 8),
      ratio: h.ratio.toFixed(4),
      z: h.zscore.toFixed(2),
    }))
  );

  const trades = await api('/trades');
  tableify(
    document.getElementById('mrTrades'),
    trades.map((t) => ({
      ts: t.ts.split('T')[1].slice(0, 8),
      side: t.side,
      qty: `${t.qty_from} → ${t.qty_to}`,
      pnl: t.pnl_usd.toFixed(2),
    }))
  );

  const pairs = await api('/pair_history');
  tableify(
    document.getElementById('mrPairs'),
    pairs.map((p) => ({ pair: `${p.asset_a}/${p.asset_b}`, samples: p.samples, z: p.last_z?.toFixed?.(2) }))
  );
}

async function loadBollinger() {
  const status = await api('/boll_status');
  keyValue(document.getElementById('bollStatus'), [
    { label: 'Symbol', value: status.symbol || 'Not set' },
    { label: 'Price', value: status.price?.toFixed?.(4) },
    { label: 'Bands', value: `${status.lower?.toFixed?.(4)} ↔ ${status.upper?.toFixed?.(4)}` },
    { label: 'Position', value: status.position },
    { label: 'PnL (real / unreal)', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
    { label: 'Quote balance', value: status.quote_balance?.toFixed?.(2) },
    { label: 'Enabled', value: status.enabled ? 'Yes' : 'No' },
  ]);

  const cfg = await api('/boll_config');
  buildConfigForm(document.getElementById('bollConfig'), cfg, async (payload) => {
    await api('/boll_config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Bollinger config saved');
    loadBollinger();
  });

  const symbols = await api('/symbols');
  const symSelect = document.querySelector('#bollManual select[name="symbol"]');
  symSelect.innerHTML = symbols.map((s) => `<option value="${s}">${s}</option>`).join('');

  const history = await api('/boll_history');
  tableify(
    document.getElementById('bollHistory'),
    history.map((h) => ({
      ts: h.ts.split('T')[1].slice(0, 8),
      price: h.price.toFixed(4),
      upper: h.upper.toFixed(4),
      lower: h.lower.toFixed(4),
    }))
  );

  const trades = await api('/boll_trades');
  tableify(
    document.getElementById('bollTrades'),
    trades.map((t) => ({
      ts: t.ts.split('T')[1].slice(0, 8),
      side: t.side,
      qty: t.qty.toFixed(4),
      pnl: t.pnl_usd.toFixed(2),
    }))
  );
}

async function loadTrend() {
  const status = await api('/trend_status');
  keyValue(document.getElementById('trendStatus'), [
    { label: 'Symbol', value: status.symbol || 'Not set' },
    { label: 'Price', value: status.price?.toFixed?.(4) },
    { label: 'MA / Upper / Lower', value: `${status.ma?.toFixed?.(4)} / ${status.upper?.toFixed?.(4)} / ${status.lower?.toFixed?.(4)}` },
    { label: 'Position', value: status.position },
    { label: 'PnL (real / unreal)', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
    { label: 'Enabled', value: status.enabled ? 'Yes' : 'No' },
  ]);

  const cfg = await api('/trend_config');
  buildConfigForm(document.getElementById('trendConfig'), cfg, async (payload) => {
    await api('/trend_config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Trend config saved');
    loadTrend();
  });

  const history = await api('/trend_history');
  tableify(
    document.getElementById('trendHistory'),
    history.map((h) => ({ ts: h.ts.split('T')[1].slice(0, 8), price: h.price.toFixed(4), ma: h.ma.toFixed(4) }))
  );
}

async function loadRS() {
  const status = await api('/rs_status');
  keyValue(document.getElementById('rsStatus'), [
    { label: 'Symbol', value: status.symbol || 'Not set' },
    { label: 'Price', value: status.price?.toFixed?.(4) },
    { label: 'Score', value: status.score?.toFixed?.(2) },
    { label: 'Position', value: status.position },
    { label: 'PnL (real / unreal)', value: `${status.realized_pnl_usd?.toFixed?.(2)} / ${status.unrealized_pnl_usd?.toFixed?.(2)}` },
    { label: 'Enabled', value: status.enabled ? 'Yes' : 'No' },
  ]);

  const cfg = await api('/rs_config');
  buildConfigForm(document.getElementById('rsConfig'), cfg, async (payload) => {
    await api('/rs_config', { method: 'POST', body: JSON.stringify(payload) });
    toast('RS config saved');
    loadRS();
  });

  const history = await api('/rs_history');
  tableify(
    document.getElementById('rsHistory'),
    history.map((h) => ({ ts: h.ts.split('T')[1].slice(0, 8), price: h.price.toFixed(4), score: h.score.toFixed(2) }))
  );
}

async function loadBalances() {
  const cards = document.getElementById('balances');
  const data = await api('/trading/balances');
  cards.innerHTML = data
    .map((acc) => {
      const list = acc.balances
        .map((b) => `<div class="meta-row"><span>${b.asset}</span><span>${b.free} / ${b.locked}</span></div>`)
        .join('');
      return `
        <div class="panel">
          <h3>${acc.account.toUpperCase()} (${acc.use_testnet ? 'Testnet' : 'Mainnet'})</h3>
          ${acc.error ? `<div class="small-note">${acc.error}</div>` : list}
        </div>`;
    })
    .join('');
}

async function loadLiquidation() {
  const status = await api('/liquidation/status');
  keyValue(document.getElementById('liqStatus'), [
    { label: 'Symbol', value: status.symbol },
    { label: 'Clusters', value: status.cluster_count },
    { label: 'Has signal', value: status.has_signal ? 'Yes' : 'No' },
    { label: 'Last execution', value: status.last_execution ? `${status.last_execution.side} @ ${status.last_execution.price}` : '—' },
  ]);

  const cfg = status.config || {};
  buildConfigForm(document.getElementById('liqConfig'), cfg, async (payload) => {
    await api('/liquidation/config', { method: 'POST', body: JSON.stringify(payload) });
    toast('Liquidation config saved');
    loadLiquidation();
  });
}

function wireActions() {
  document.getElementById('mrManual').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const payload = {
      direction: fd.get('direction'),
      notional_usd: fd.get('notional') ? Number(fd.get('notional')) : null,
      from_asset_qty: fd.get('qty') ? Number(fd.get('qty')) : null,
    };
    try {
      await api('/manual_trade', { method: 'POST', body: JSON.stringify(payload) });
      toast('Manual trade sent');
      loadMeanReversion();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('mrSync').addEventListener('click', async () => {
    try {
      await api('/sync_state_from_balances', { method: 'POST' });
      toast('State refreshed from balances');
      loadMeanReversion();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('mrStart').addEventListener('click', async () => {
    try {
      await api('/start', { method: 'POST' });
      toast('Mean reversion started');
      loadMeanReversion();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('mrStop').addEventListener('click', async () => {
    try {
      await api('/stop', { method: 'POST' });
      toast('Mean reversion stopped');
      loadMeanReversion();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('bollManual').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const payload = { symbol: fd.get('symbol'), qty_base: Number(fd.get('qty')) };
    try {
      await api('/bollinger_manual_sell', { method: 'POST', body: JSON.stringify(payload) });
      toast('Manual Bollinger sell placed');
      loadBollinger();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('bollStart').addEventListener('click', async () => {
    try {
      await api('/boll_start', { method: 'POST' });
      toast('Bollinger bot started');
      loadBollinger();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('bollStop').addEventListener('click', async () => {
    try {
      await api('/boll_stop', { method: 'POST' });
      toast('Bollinger bot stopped');
      loadBollinger();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('bollBest').addEventListener('click', async () => {
    try {
      const best = await api('/boll_config_best');
      await api('/boll_config', { method: 'POST', body: JSON.stringify(best) });
      toast('Best Bollinger config applied');
      loadBollinger();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('trendStart').addEventListener('click', async () => {
    try {
      await api('/trend_start', { method: 'POST' });
      toast('Trend bot started');
      loadTrend();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('trendStop').addEventListener('click', async () => {
    try {
      await api('/trend_stop', { method: 'POST' });
      toast('Trend bot stopped');
      loadTrend();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('rsStart').addEventListener('click', async () => {
    try {
      await api('/rs_start', { method: 'POST' });
      toast('Relative strength bot started');
      loadRS();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('rsStop').addEventListener('click', async () => {
    try {
      await api('/rs_stop', { method: 'POST' });
      toast('Relative strength bot stopped');
      loadRS();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('tradeForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const payload = {
      account: fd.get('account'),
      use_testnet: fd.get('use_testnet') === 'true',
      symbol: fd.get('symbol'),
      side: fd.get('side'),
      qty_base: Number(fd.get('qty')),
    };
    try {
      await api('/trading/order', { method: 'POST', body: JSON.stringify(payload) });
      toast('Order sent');
      loadBalances();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('liqScan').addEventListener('click', async () => {
    try {
      await api('/liquidation/scan', { method: 'POST' });
      toast('Manual scan completed');
      loadLiquidation();
    } catch (e) {
      toast(e.message, true);
    }
  });

  document.getElementById('liqExecute').addEventListener('click', async () => {
    try {
      await api('/liquidation/execute', { method: 'POST' });
      toast('Execution sent');
      loadLiquidation();
    } catch (e) {
      toast(e.message, true);
    }
  });
}

function scheduleRefresh() {
  setInterval(() => {
    loadMeanReversion().catch(() => {});
    loadBollinger().catch(() => {});
    loadTrend().catch(() => {});
    loadRS().catch(() => {});
    loadBalances().catch(() => {});
    loadLiquidation().catch(() => {});
  }, autoRefreshMs);
}

async function init() {
  wireActions();
  try {
    await loadMeanReversion();
    await loadBollinger();
    await loadTrend();
    await loadRS();
    await loadBalances();
    await loadLiquidation();
  } catch (e) {
    toast(e.message, true);
  }
  scheduleRefresh();
}

document.addEventListener('DOMContentLoaded', init);
