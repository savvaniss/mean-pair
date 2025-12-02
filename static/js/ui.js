export function initCollapsibles() {
  document.querySelectorAll('[data-collapsible]').forEach((card) => {
    const header = card.querySelector('.card-header');
    const body = card.querySelector('.card-body');
    const icon = card.querySelector('.toggle-icon');

    header.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      const isHidden = body.style.display === 'none';
      body.style.display = isHidden ? 'block' : 'none';
      icon.textContent = isHidden ? '▼' : '▲';
    });
  });
}

export function initTabs() {
  const tabs = document.querySelectorAll('.tab');
  const contents = document.querySelectorAll('.tab-content');

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      tabs.forEach((t) => t.classList.remove('active'));
      contents.forEach((c) => c.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(`tab-${target}`).classList.add('active');
    });
  });
}

export function applyQuoteLabels(value) {
  document.querySelectorAll('.quote-label').forEach((el) => {
    el.textContent = value;
  });
}

export function openOverlay(id) {
  const overlay = document.getElementById(id);
  if (overlay) {
    overlay.style.display = 'flex';
  }
}

export function closeOverlay(id) {
  const overlay = document.getElementById(id);
  if (overlay) {
    overlay.style.display = 'none';
  }
}
