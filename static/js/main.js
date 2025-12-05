import { closeOverlay, initCollapsibles, initTabs, openOverlay } from './ui.js';
import { initMeanReversion, refreshMeanReversion } from './meanReversion.js';
import { initBollinger, refreshBollinger } from './bollinger.js';
import { initTrendFollowing, refreshTrendFollowing } from './trendFollowing.js';
import { initRelativeStrength, refreshRelativeStrength } from './relativeStrength.js';
import { initTrading, refreshTrading } from './trading.js';
import { initLiquidation, refreshLiquidation } from './liquidationHunt.js';
import { initListings, refreshListings } from './listings.js';

async function bootstrap() {
  safeRun('tabs', initTabs);
  safeRun('collapsibles', initCollapsibles);
  safeRun('overlays', initOverlays);
  safeRun('mean reversion init', initMeanReversion);
  safeRun('bollinger init', initBollinger);
  safeRun('trend init', initTrendFollowing);
  safeRun('relative strength init', initRelativeStrength);
  safeRun('trading init', initTrading);
  safeRun('liquidation init', initLiquidation);
  safeRun('listings init', initListings);

  document.getElementById('refreshAll')?.addEventListener('click', () => {
    void safeRefresh('mean reversion', refreshMeanReversion);
    void safeRefresh('bollinger', refreshBollinger);
    void safeRefresh('trend', refreshTrendFollowing);
    void safeRefresh('relative strength', refreshRelativeStrength);
    void safeRefresh('trading', refreshTrading);
    void safeRefresh('liquidation', refreshLiquidation);
    void safeRefresh('listings', refreshListings);
  });

  await safeRefresh('mean reversion', refreshMeanReversion);
  await safeRefresh('bollinger', refreshBollinger);
  await safeRefresh('trend', refreshTrendFollowing);
  await safeRefresh('relative strength', refreshRelativeStrength);
  await safeRefresh('trading', refreshTrading);
  await safeRefresh('liquidation', refreshLiquidation);
  await safeRefresh('listings', refreshListings);

  setInterval(async () => {
    await safeRefresh('mean reversion', refreshMeanReversion);
    await safeRefresh('bollinger', refreshBollinger);
    await safeRefresh('trend', refreshTrendFollowing);
    await safeRefresh('relative strength', refreshRelativeStrength);
    await safeRefresh('trading', refreshTrading);
    await safeRefresh('liquidation', refreshLiquidation);
  }, 10000);
}

document.addEventListener('DOMContentLoaded', bootstrap);

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
  wireOverlay(['openActionCenter'], 'actionModalOverlay');
  wireOverlay(['openListingConfig', 'openListingConfigInline'], 'listingConfigOverlay');
  wireOverlay(['openMrConfig', 'openMrConfigInline'], 'mrConfigOverlay');
  wireOverlay(['openBollConfig', 'openBollConfigInline'], 'bollConfigOverlay');
  wireOverlay(['openTrendConfig', 'openTrendConfigInline'], 'trendConfigOverlay');
  wireOverlay(['openRSConfig', 'openRSConfigInline'], 'rsConfigOverlay');
  wireOverlay(['openLiqConfigInline'], 'liqConfigOverlay');

  document.querySelectorAll('[data-close-overlay]').forEach((btn) => {
    const target = btn.getAttribute('data-close-overlay');
    btn.addEventListener('click', () => closeOverlay(target));
  });
}
