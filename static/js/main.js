const { createApp } = Vue;

const refreshSeconds = 20;

function formatNumber(value, decimals = 2) {
  if (value === null || value === undefined) return '—';
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(decimals) : value;
}

function castValue(original, input) {
  if (Array.isArray(original)) {
    return String(input || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (typeof original === 'boolean') return input === 'true' || input === true;
  if (typeof original === 'number') return Number(input);
  return input;
}

function renderMeta(status) {
  return Object.entries(status || {}).map(([label, value]) => ({ label, value }));
}

createApp({
  data() {
    return {
      refreshSeconds,
      timer: null,
      heartbeat: 'Live',
      toast: { visible: false, message: '', error: false },
      overview: {},
      mr: {
        status: {},
        next: {},
        history: [],
        trades: [],
        pairs: [],
        directions: [],
        manual: { direction: '', notional: null, qty: null },
        config: { fields: {}, available_pairs: [], pairSelection: '' },
      },
      boll: { status: {}, history: [], trades: [], symbols: [], manual: { symbol: '', qty: null }, config: { fields: {} } },
      trend: { status: {}, history: [], config: { fields: {} } },
      rs: { status: {}, history: [], config: { fields: {} } },
      trading: {
        balances: [],
        form: { account: '', use_testnet: 'true', symbol: '', side: 'buy', qty: null },
      },
      liq: { status: {}, config: { fields: {} } },
      modals: { mr: false, boll: false, trend: false, rs: false, liq: false },
      originals: { mr: {}, boll: {}, trend: {}, rs: {}, liq: {} },
    };
  },
  computed: {
    displayMeta() {
      return (obj) => renderMeta(obj);
    },
  },
  methods: {
    async api(path, options = {}) {
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
    },
    showToast(message, error = false) {
      this.toast = { visible: true, message, error };
      setTimeout(() => (this.toast.visible = false), 2600);
    },
    normalizeConfig(targetKey, config) {
      this.originals[targetKey] = { ...config };
      const fields = {};
      Object.entries(config).forEach(([key, value]) => {
        if (key === 'available_pairs') return;
        if (Array.isArray(value)) fields[key] = value.join(', ');
        else if (typeof value === 'boolean') fields[key] = value ? 'true' : 'false';
        else fields[key] = value ?? '';
      });
      return fields;
    },
    async loadMeanReversion() {
      const status = await this.api('/status');
      this.mr.status = {
        Pair: `${status.asset_a}/${status.asset_b}`,
        Prices: `${formatNumber(status.price_a, 4)} / ${formatNumber(status.price_b, 4)}`,
        'Ratio / z-score': `${formatNumber(status.ratio, 4)} / ${formatNumber(status.zscore, 2)}`,
        'Mean / std': `${formatNumber(status.mean_ratio, 4)} / ${formatNumber(status.std_ratio, 4)}`,
        Balances: `${formatNumber(status.base_balance, 2)} • ${formatNumber(status.asset_a_balance, 2)} ${status.asset_a} • ${formatNumber(status.asset_b_balance, 2)} ${status.asset_b}`,
        'Current position': `${formatNumber(status.current_qty, 4)} ${status.current_asset}`,
        'PnL (real / unreal)': `${formatNumber(status.realized_pnl_usd, 2)} / ${formatNumber(status.unrealized_pnl_usd, 2)}`,
        Enabled: status.enabled ? 'Yes' : 'No',
        Mode: status.use_testnet ? 'Testnet' : 'Mainnet',
      };
      this.overview = {
        pair: `${status.asset_a}/${status.asset_b}`,
        wallet: `${formatNumber(status.base_balance, 2)} base`,
        pnl: formatNumber(status.realized_pnl_usd, 2),
        mode: status.use_testnet ? 'Testnet' : 'Mainnet',
      };
      this.mr.directions = [
        `${status.asset_a}->${status.asset_b}`,
        `${status.asset_b}->${status.asset_a}`,
      ];
      if (!this.mr.manual.direction) this.mr.manual.direction = this.mr.directions[0];

      const next = await this.api('/next_signal');
      this.mr.next = {
        Direction: next.direction,
        Reason: next.reason,
        'Ratio / z': `${formatNumber(next.ratio, 4)} / ${formatNumber(next.zscore, 2)}`,
        Bands: `${formatNumber(next.lower_band, 4)} ↔ ${formatNumber(next.upper_band, 4)}`,
        Qty: `${next.qty_from} → ${next.qty_to}`,
      };

      const cfg = await this.api('/config');
      this.mr.config.available_pairs = cfg.available_pairs || [];
      this.mr.config.pairSelection = `${cfg.asset_a}|${cfg.asset_b}`;
      this.mr.config.fields = this.normalizeConfig('mr', cfg);

      const history = await this.api('/history?limit=120');
      this.mr.history = history.map((h) => ({
        ts: h.ts.split('T')[1].slice(0, 8),
        ratio: formatNumber(h.ratio, 4),
        z: formatNumber(h.zscore, 2),
      }));

      const trades = await this.api('/trades');
      this.mr.trades = trades.map((t) => ({
        ts: t.ts.split('T')[1].slice(0, 8),
        side: t.side,
        qty: `${t.qty_from} → ${t.qty_to}`,
        pnl: formatNumber(t.pnl_usd, 2),
      }));

      const pairs = await this.api('/pair_history');
      this.mr.pairs = pairs.map((p) => ({
        pair: `${p.asset_a}/${p.asset_b}`,
        samples: p.samples,
        z: formatNumber(p.last_z, 2),
      }));
    },
    async loadBollinger() {
      const status = await this.api('/boll_status');
      this.boll.status = {
        Symbol: status.symbol || 'Not set',
        Price: formatNumber(status.price, 4),
        Bands: `${formatNumber(status.lower, 4)} ↔ ${formatNumber(status.upper, 4)}`,
        Position: status.position,
        'PnL (real / unreal)': `${formatNumber(status.realized_pnl_usd, 2)} / ${formatNumber(status.unrealized_pnl_usd, 2)}`,
        'Quote balance': formatNumber(status.quote_balance, 2),
        Enabled: status.enabled ? 'Yes' : 'No',
      };

      const cfg = await this.api('/boll_config');
      this.boll.config.fields = this.normalizeConfig('boll', cfg);

      const symbols = await this.api('/symbols');
      this.boll.symbols = symbols;
      if (!this.boll.manual.symbol && symbols.length) this.boll.manual.symbol = symbols[0];

      const history = await this.api('/boll_history');
      this.boll.history = history.map((h) => ({
        ts: h.ts.split('T')[1].slice(0, 8),
        price: formatNumber(h.price, 4),
        upper: formatNumber(h.upper, 4),
        lower: formatNumber(h.lower, 4),
      }));

      const trades = await this.api('/boll_trades');
      this.boll.trades = trades.map((t) => ({
        ts: t.ts.split('T')[1].slice(0, 8),
        side: t.side,
        qty: formatNumber(t.qty, 4),
        pnl: formatNumber(t.pnl_usd, 2),
      }));
    },
    async loadTrend() {
      const status = await this.api('/trend_status');
      this.trend.status = {
        Symbol: status.symbol || 'Not set',
        Price: formatNumber(status.price, 4),
        'MA / Upper / Lower': `${formatNumber(status.ma, 4)} / ${formatNumber(status.upper, 4)} / ${formatNumber(status.lower, 4)}`,
        Position: status.position,
        'PnL (real / unreal)': `${formatNumber(status.realized_pnl_usd, 2)} / ${formatNumber(status.unrealized_pnl_usd, 2)}`,
        Enabled: status.enabled ? 'Yes' : 'No',
      };

      const cfg = await this.api('/trend_config');
      this.trend.config.fields = this.normalizeConfig('trend', cfg);

      const history = await this.api('/trend_history');
      this.trend.history = history.map((h) => ({
        ts: h.ts.split('T')[1].slice(0, 8),
        price: formatNumber(h.price, 4),
        ma: formatNumber(h.ma, 4),
      }));
    },
    async loadRS() {
      const status = await this.api('/rs_status');
      this.rs.status = {
        Symbol: status.symbol || 'Not set',
        Price: formatNumber(status.price, 4),
        Score: formatNumber(status.score, 2),
        Position: status.position,
        'PnL (real / unreal)': `${formatNumber(status.realized_pnl_usd, 2)} / ${formatNumber(status.unrealized_pnl_usd, 2)}`,
        Enabled: status.enabled ? 'Yes' : 'No',
      };

      const cfg = await this.api('/rs_config');
      this.rs.config.fields = this.normalizeConfig('rs', cfg);

      const history = await this.api('/rs_history');
      this.rs.history = history.map((h) => ({
        ts: h.ts.split('T')[1].slice(0, 8),
        price: formatNumber(h.price, 4),
        score: formatNumber(h.score, 2),
      }));
    },
    async loadBalances() {
      const balances = await this.api('/trading/balances');
      this.trading.balances = balances;
    },
    async loadLiquidation() {
      const status = await this.api('/liquidation/status');
      this.liq.status = {
        Symbol: status.symbol,
        Clusters: status.cluster_count,
        'Has signal': status.has_signal ? 'Yes' : 'No',
        'Last execution': status.last_execution
          ? `${status.last_execution.side} @ ${status.last_execution.price}`
          : '—',
      };
      const cfg = status.config || {};
      this.liq.config.fields = this.normalizeConfig('liq', cfg);
    },
    async submitManualMean() {
      const payload = {
        direction: this.mr.manual.direction,
        notional_usd: this.mr.manual.notional ?? null,
        from_asset_qty: this.mr.manual.qty ?? null,
      };
      try {
        await this.api('/manual_trade', { method: 'POST', body: JSON.stringify(payload) });
        this.showToast('Manual trade sent');
        this.loadMeanReversion();
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    async syncState() {
      try {
        await this.api('/sync_state_from_balances', { method: 'POST' });
        this.showToast('State refreshed from balances');
        this.loadMeanReversion();
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    async toggleRun(target, start) {
      const routes = {
        mr: start ? '/start' : '/stop',
        boll: start ? '/boll_start' : '/boll_stop',
        trend: start ? '/trend_start' : '/trend_stop',
        rs: start ? '/rs_start' : '/rs_stop',
      };
      const labels = {
        mr: 'Mean reversion',
        boll: 'Bollinger',
        trend: 'Trend',
        rs: 'Relative strength',
      };
      try {
        await this.api(routes[target], { method: 'POST' });
        this.showToast(`${labels[target]} ${start ? 'started' : 'stopped'}`);
        this.refreshFor(target);
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    async applyBest(target) {
      try {
        if (target === 'mr') {
          const best = await this.api('/config_best');
          await this.api('/config', { method: 'POST', body: JSON.stringify(best) });
          this.showToast('Best config applied');
          this.loadMeanReversion();
        } else if (target === 'boll') {
          const best = await this.api('/boll_config_best');
          await this.api('/boll_config', { method: 'POST', body: JSON.stringify(best) });
          this.showToast('Best Bollinger config applied');
          this.loadBollinger();
        }
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    isBoolean(target, key) {
      const original = this.originals[target]?.[key];
      return typeof original === 'boolean';
    },
    isArray(target, key) {
      const original = this.originals[target]?.[key];
      return Array.isArray(original);
    },
    inputType(target, key) {
      const original = this.originals[target]?.[key];
      if (typeof original === 'number') return 'number';
      return 'text';
    },
    async saveConfig(target) {
      try {
        if (target === 'mr') {
          const payload = {};
          const original = this.originals.mr;
          Object.entries(this.mr.config.fields).forEach(([key, val]) => {
            payload[key] = castValue(original[key], val);
          });
          if (this.mr.config.available_pairs.length) {
            const [a, b] = (this.mr.config.pairSelection || '').split('|');
            payload.asset_a = a;
            payload.asset_b = b;
            payload.available_pairs = this.mr.config.available_pairs;
          }
          await this.api('/config', { method: 'POST', body: JSON.stringify(payload) });
          this.showToast('Mean reversion config saved');
          this.loadMeanReversion();
        } else if (target === 'boll') {
          const payload = {};
          const original = this.originals.boll;
          Object.entries(this.boll.config.fields).forEach(([key, val]) => {
            payload[key] = castValue(original[key], val);
          });
          await this.api('/boll_config', { method: 'POST', body: JSON.stringify(payload) });
          this.showToast('Bollinger config saved');
          this.loadBollinger();
        } else if (target === 'trend') {
          const payload = {};
          const original = this.originals.trend;
          Object.entries(this.trend.config.fields).forEach(([key, val]) => {
            payload[key] = castValue(original[key], val);
          });
          await this.api('/trend_config', { method: 'POST', body: JSON.stringify(payload) });
          this.showToast('Trend config saved');
          this.loadTrend();
        } else if (target === 'rs') {
          const payload = {};
          const original = this.originals.rs;
          Object.entries(this.rs.config.fields).forEach(([key, val]) => {
            payload[key] = castValue(original[key], val);
          });
          await this.api('/rs_config', { method: 'POST', body: JSON.stringify(payload) });
          this.showToast('Relative strength config saved');
          this.loadRS();
        } else if (target === 'liq') {
          const payload = {};
          const original = this.originals.liq;
          Object.entries(this.liq.config.fields).forEach(([key, val]) => {
            payload[key] = castValue(original[key], val);
          });
          await this.api('/liquidation/config', { method: 'POST', body: JSON.stringify(payload) });
          this.showToast('Liquidation config saved');
          this.loadLiquidation();
        }
      } catch (e) {
        this.showToast(e.message, true);
      } finally {
        this.closeConfig();
      }
    },
    openConfig(target) {
      this.modals[target] = true;
    },
    closeConfig() {
      Object.keys(this.modals).forEach((k) => (this.modals[k] = false));
    },
    async submitBollinger() {
      const payload = { symbol: this.boll.manual.symbol, qty_base: this.boll.manual.qty };
      try {
        await this.api('/bollinger_manual_sell', { method: 'POST', body: JSON.stringify(payload) });
        this.showToast('Manual Bollinger sell placed');
        this.loadBollinger();
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    async submitTrade() {
      const fd = this.trading.form;
      const payload = {
        account: fd.account,
        use_testnet: fd.use_testnet === 'true',
        symbol: fd.symbol,
        side: fd.side,
        qty_base: fd.qty,
      };
      try {
        await this.api('/trading/order', { method: 'POST', body: JSON.stringify(payload) });
        this.showToast('Order sent');
        this.loadBalances();
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    async runLiquidation(action) {
      const routes = { scan: '/liquidation/scan', execute: '/liquidation/execute' };
      try {
        await this.api(routes[action], { method: 'POST' });
        this.showToast(action === 'scan' ? 'Manual scan completed' : 'Execution sent');
        this.loadLiquidation();
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
    refreshFor(target) {
      const map = {
        mr: this.loadMeanReversion,
        boll: this.loadBollinger,
        trend: this.loadTrend,
        rs: this.loadRS,
      };
      map[target]?.();
    },
    async refreshAll() {
      try {
        await Promise.all([
          this.loadMeanReversion(),
          this.loadBollinger(),
          this.loadTrend(),
          this.loadRS(),
          this.loadBalances(),
          this.loadLiquidation(),
        ]);
      } catch (e) {
        this.showToast(e.message, true);
      }
    },
  },
  mounted() {
    this.refreshAll();
    this.timer = setInterval(() => {
      this.refreshAll();
    }, refreshSeconds * 1000);
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer);
  },
}).mount('#app');
