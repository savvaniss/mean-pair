import { initCollapsibles, initTabs } from './ui.js';
import { initMeanReversion, refreshMeanReversion } from './meanReversion.js';
import { initBollinger, refreshBollinger } from './bollinger.js';
import { initTrendFollowing, refreshTrendFollowing } from './trendFollowing.js';
import { initTrading, refreshTrading } from './trading.js';

async function bootstrap() {
  initTabs();
  initCollapsibles();
  initMeanReversion();
  initBollinger();
  initTrendFollowing();
  initTrading();

  await refreshMeanReversion();
  await refreshBollinger();
  await refreshTrendFollowing();
  await refreshTrading();

  setInterval(async () => {
    await refreshMeanReversion();
    await refreshBollinger();
    await refreshTrendFollowing();
    await refreshTrading();
  }, 10000);
}

document.addEventListener('DOMContentLoaded', bootstrap);
