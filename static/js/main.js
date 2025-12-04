import { initCollapsibles, initTabs, initModals } from './ui.js';
import { initMeanReversion, refreshMeanReversion } from './meanReversion.js';
import { initBollinger, refreshBollinger } from './bollinger.js';
import { initTrendFollowing, refreshTrendFollowing } from './trendFollowing.js';
import { initRelativeStrength, refreshRelativeStrength } from './relativeStrength.js';
import { initTrading, refreshTrading } from './trading.js';
import { initLiquidation, refreshLiquidation } from './liquidationHunt.js';

async function bootstrap() {
  initTabs();
  initModals();
  initCollapsibles();
  initMeanReversion();
  initBollinger();
  initTrendFollowing();
  initRelativeStrength();
  initTrading();
  initLiquidation();

  await refreshMeanReversion();
  await refreshBollinger();
  await refreshTrendFollowing();
  await refreshRelativeStrength();
  await refreshTrading();
  await refreshLiquidation();

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
