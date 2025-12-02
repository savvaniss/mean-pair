import { applyQuoteLabels } from './ui.js';

let cachedSymbols = [];

export function initTrading() {
  document.getElementById('tradingEnv').addEventListener('change', refreshTrading);
  document.getElementById('tradingAccount').addEventListener('change', refreshTrading);
  document.getElementById('tradingOrderForm').addEventListener('submit', submitOrder);
}

export async function refreshTrading() {
  await Promise.all([loadSymbols(), loadBalances()]);
}

async function loadSymbols() {
  if (cachedSymbols.length > 0) return;
  try {
    const r = await fetch('/symbols');
    cachedSymbols = await r.json();
    const datalist = document.getElementById('tradingSymbolList');
    datalist.innerHTML = '';
    cachedSymbols.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.symbol;
      opt.label = `${s.baseAsset}/${s.quoteAsset}`;
      datalist.appendChild(opt);
    });
  } catch (e) {
    console.error('Failed to load symbols', e);
  }
}

function currentEnv() {
  return document.getElementById('tradingEnv').value === 'testnet';
}

function currentAccount() {
  return document.getElementById('tradingAccount').value;
}

async function loadBalances() {
  try {
    const useTestnet = currentEnv();
    const r = await fetch(`/trading/balances?use_testnet=${useTestnet}`);
    const data = await r.json();

    applyQuoteLabels(useTestnet ? 'USDT' : 'USDC');

    const container = document.getElementById('tradingBalances');
    container.innerHTML = '';

    data.forEach((account) => {
      const card = document.createElement('div');
      card.className = 'balance-card';

      const heading = document.createElement('div');
      heading.className = 'balance-card__header';
      heading.innerHTML = `<div class="chip">${account.account === 'mr' ? 'MR keys' : 'Bollinger keys'}</div>`;

      const list = document.createElement('div');
      list.className = 'balance-grid';

      if (account.error) {
        const error = document.createElement('div');
        error.className = 'muted';
        error.textContent = account.error;
        list.appendChild(error);
      } else if (account.balances.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'muted';
        empty.textContent = 'No balances for this environment.';
        list.appendChild(empty);
      } else {
        account.balances
          .sort((a, b) => b.free - a.free)
          .slice(0, 12)
          .forEach((b) => {
            const row = document.createElement('div');
            row.className = 'balance-row';
            row.innerHTML = `
              <div class="balance-asset">${b.asset}</div>
              <div class="balance-qty">${b.free.toFixed(6)}</div>
              <div class="balance-locked">locked: ${b.locked.toFixed(4)}</div>
            `;
            list.appendChild(row);
          });
      }

      card.appendChild(heading);
      card.appendChild(list);
      container.appendChild(card);
    });
  } catch (e) {
    console.error('Failed to load balances', e);
  }
}

async function submitOrder(event) {
  event.preventDefault();
  const symbol = document.getElementById('tradingSymbol').value.trim().toUpperCase();
  const side = document.getElementById('tradingSide').value;
  const qty = parseFloat(document.getElementById('tradingQty').value);

  if (!symbol) {
    alert('Enter a symbol like BTCUSDT.');
    return false;
  }
  if (isNaN(qty) || qty <= 0) {
    alert('Quantity must be > 0');
    return false;
  }

  const body = {
    account: currentAccount(),
    use_testnet: currentEnv(),
    symbol,
    side,
    qty_base: qty,
  };

  try {
    const r = await fetch('/trading/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) {
      alert('Trade failed: ' + (data.detail || r.statusText));
      return false;
    }

    alert(
      `${data.side} ${data.qty_executed.toFixed(6)} ` +
        `${data.symbol} @ ${data.price_used.toFixed(6)} (${data.quote_asset})`
    );

    await loadBalances();
  } catch (e) {
    console.error(e);
    alert('Request failed.');
  }

  return false;
}
