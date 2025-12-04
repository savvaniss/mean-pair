import { closeOverlay, initCollapsibles, initTabs, openOverlay } from './ui.js';
import { initMeanReversion, refreshMeanReversion } from './meanReversion.js';
import { initBollinger, refreshBollinger } from './bollinger.js';
import { initTrendFollowing, refreshTrendFollowing } from './trendFollowing.js';
import { initRelativeStrength, refreshRelativeStrength } from './relativeStrength.js';
import { initTrading, refreshTrading } from './trading.js';
import { initLiquidation, refreshLiquidation } from './liquidationHunt.js';
import { initListings, refreshListings } from './listings.js';

async function bootstrap() {
  initTabs();
  initCollapsibles();
  initOverlays();
  initMeanReversion();
  initBollinger();
  initTrendFollowing();
  initRelativeStrength();
  initTrading();
  initLiquidation();
  initListings();

  await refreshMeanReversion();
  await refreshBollinger();
  await refreshTrendFollowing();
  await refreshRelativeStrength();
  await refreshTrading();
  await refreshLiquidation();
  await refreshListings();

  setInterval(async () => {
    await refreshMeanReversion();
    await refreshBollinger();
    await refreshTrendFollowing();
    await refreshRelativeStrength();
    await refreshTrading();
    await refreshLiquidation();
  }, 10000);
}

document.addEventListener('DOMContentLoaded', bootstrap);

function wireOverlay(ids, overlayId) {
  ids
    .map((id) => document.getElementById(id))
    .filter(Boolean)
    .forEach((btn) => btn.addEventListener('click', () => openOverlay(overlayId)));
}

function initOverlays() {
  wireOverlay(['openActionCenter'], 'actionModalOverlay');
  wireOverlay(['openMrConfig', 'openMrConfigInline'], 'mrConfigOverlay');
  wireOverlay(['openBollConfig', 'openBollConfigInline'], 'bollConfigOverlay');
  wireOverlay(['openTrendConfig', 'openTrendConfigInline'], 'trendConfigOverlay');
  wireOverlay(['openRSConfig', 'openRSConfigInline'], 'rsConfigOverlay');

  document.querySelectorAll('[data-close-overlay]').forEach((btn) => {
    const target = btn.getAttribute('data-close-overlay');
    btn.addEventListener('click', () => closeOverlay(target));
  });
}
