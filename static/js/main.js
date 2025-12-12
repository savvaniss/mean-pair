import { closeOverlay, initCollapsibles, initTabs, openOverlay } from './ui.js';
import { handleMeanReversionStream, initMeanReversion, refreshMeanReversion } from './meanReversion.js';
import { initBollinger, refreshBollinger } from './bollinger.js';
import { initTrendFollowing, refreshTrendFollowing } from './trendFollowing.js';
import { initRelativeStrength, refreshRelativeStrength } from './relativeStrength.js';
import { initTrading, refreshTrading } from './trading.js';
import { initLiquidation, refreshLiquidation } from './liquidationHunt.js';
import { initListings, refreshListings } from './listings.js';
import { initFreqtradeAdapters, refreshFreqtradeAdapters } from './freqtrade.js';
import { initBacktesting, refreshBacktesting } from './backtesting.js';
import { initAmplification, refreshAmplification } from './amplification.js';
import { startDashboardStream } from './streaming.js';

let lastMeanReversionStreamTs = 0;

async function bootstrap() {
  const authenticated = await initAuthDisplay();
  if (!authenticated) return;

  safeRun('tabs', initTabs);
  safeRun('collapsibles', initCollapsibles);
  safeRun('overlays', initOverlays);
  safeRun('mean reversion init', initMeanReversion);
  safeRun('bollinger init', initBollinger);
  safeRun('trend init', initTrendFollowing);
  safeRun('relative strength init', initRelativeStrength);
  safeRun('amplification init', initAmplification);
  safeRun('trading init', initTrading);
  safeRun('liquidation init', initLiquidation);
  safeRun('listings init', initListings);
  safeRun('freqtrade adapters init', initFreqtradeAdapters);
  safeRun('backtesting init', initBacktesting);

  await safeRefresh('mean reversion', refreshMeanReversion);
  await safeRefresh('bollinger', refreshBollinger);
  await safeRefresh('trend', refreshTrendFollowing);
  await safeRefresh('relative strength', refreshRelativeStrength);
  await safeRefresh('amplification', refreshAmplification);
  await safeRefresh('trading', refreshTrading);
  await safeRefresh('liquidation', refreshLiquidation);
  await safeRefresh('listings', refreshListings);
  await safeRefresh('freqtrade adapters', refreshFreqtradeAdapters);
  await safeRefresh('backtesting', refreshBacktesting);

  startDashboardStream((payload) => {
    if (payload.mean_reversion) {
      lastMeanReversionStreamTs = Date.now();
      handleMeanReversionStream(payload.mean_reversion);
    }
  });

  setInterval(async () => {
    if (Date.now() - lastMeanReversionStreamTs > 15000) {
      await safeRefresh('mean reversion (fallback)', refreshMeanReversion);
    }
    await safeRefresh('bollinger', refreshBollinger);
    await safeRefresh('trend', refreshTrendFollowing);
    await safeRefresh('relative strength', refreshRelativeStrength);
    await safeRefresh('amplification', refreshAmplification);
    await safeRefresh('trading', refreshTrading);
    await safeRefresh('liquidation', refreshLiquidation);
    await safeRefresh('freqtrade adapters', refreshFreqtradeAdapters);
    await safeRefresh('backtesting', refreshBacktesting);
  }, 10000);
}

document.addEventListener('DOMContentLoaded', bootstrap);

async function initAuthDisplay() {
  const badge = document.getElementById('userBadge');
  const logoutBtn = document.getElementById('logoutButton');
  try {
    const resp = await fetch('/api/auth/me');
    if (resp.status === 401) {
      window.location.href = '/login';
      return false;
    }
    const data = await resp.json();
    if (badge) {
      badge.textContent = `Signed in as ${data.username}`;
    }
    logoutBtn?.addEventListener('click', async () => {
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } finally {
        window.location.href = '/login';
      }
    });
  } catch (err) {
    console.error('Unable to verify session', err);
  }
  return true;
}

function wireOverlay(ids, overlayId) {
  ids
    .map((id) => document.getElementById(id))
    .filter(Boolean)
    .forEach((btn) => btn.addEventListener('click', () => openOverlay(overlayId)));
}

function safeRun(label, fn) {
  try {
    fn();
  } catch (err) {
    console.error(`Failed to init ${label}`, err);
  }
}

async function safeRefresh(label, fn) {
  try {
    await fn();
  } catch (err) {
    console.error(`Failed to refresh ${label}`, err);
  }
}

function initOverlays() {
  wireOverlay(['openListingConfigInline'], 'listingConfigOverlay');
  wireOverlay(['openMrConfigInline'], 'mrConfigOverlay');
  wireOverlay(['openBollConfigInline'], 'bollConfigOverlay');
  wireOverlay(['openTrendConfigInline'], 'trendConfigOverlay');
  wireOverlay(['openRSConfigInline'], 'rsConfigOverlay');
  wireOverlay(['openAmpConfigInline'], 'ampConfigOverlay');
  wireOverlay(['openFtConfig'], 'ftConfigOverlay');
  wireOverlay(['openLiqConfigInlineSecondary'], 'liqConfigOverlay');

  document.querySelectorAll('[data-close-overlay]').forEach((btn) => {
    const target = btn.getAttribute('data-close-overlay');
    btn.addEventListener('click', () => closeOverlay(target));
  });
}
