import { initCollapsibles, initTabs } from './ui.js';
import { initMeanReversion, refreshMeanReversion } from './meanReversion.js';
import { initBollinger, refreshBollinger } from './bollinger.js';

async function bootstrap() {
  initTabs();
  initCollapsibles();
  initMeanReversion();
  initBollinger();

  await refreshMeanReversion();
  await refreshBollinger();

  setInterval(async () => {
    await refreshMeanReversion();
    await refreshBollinger();
  }, 10000);
}

document.addEventListener('DOMContentLoaded', bootstrap);
