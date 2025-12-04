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

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.target || tab.dataset.tab;
      tabs.forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');

      const section = document.getElementById(target) || document.getElementById(`tab-${target}`);
      if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
}

export function initModals() {
  document.querySelectorAll('[data-modal-open]').forEach((btn) => {
    btn.addEventListener('click', () => openOverlay(btn.dataset.modalOpen));
  });

  document.querySelectorAll('[data-modal-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeOverlay(btn.dataset.modalClose));
  });

  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        closeOverlay(overlay.id);
      }
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

const toastIcons = {
  success: '✓',
  info: 'ℹ',
  warning: '!',
  danger: '⚠',
};

function ensureToastContainer() {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container';
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
  }
  return container;
}

export function showToast(message, variant = 'info', duration = 4200) {
  const container = ensureToastContainer();

  const toast = document.createElement('div');
  toast.className = `toast toast-${variant}`;
  toast.setAttribute('role', 'status');

  const icon = document.createElement('div');
  icon.className = 'toast-icon';
  icon.textContent = toastIcons[variant] || toastIcons.info;

  const content = document.createElement('div');
  content.className = 'toast-content';
  content.textContent = message;

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', 'Dismiss notification');
  close.textContent = '×';

  toast.appendChild(icon);
  toast.appendChild(content);
  toast.appendChild(close);
  container.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add('visible'));

  let hideTimeout = setTimeout(() => removeToast(), duration);

  function removeToast() {
    toast.classList.remove('visible');
    const fallback = setTimeout(() => toast.remove(), 260);
    toast.addEventListener(
      'transitionend',
      () => {
        clearTimeout(fallback);
        toast.remove();
      },
      { once: true }
    );
  }

  toast.addEventListener('mouseenter', () => {
    clearTimeout(hideTimeout);
  });
  toast.addEventListener('mouseleave', () => {
    hideTimeout = setTimeout(() => removeToast(), 1400);
  });
  close.addEventListener('click', () => {
    clearTimeout(hideTimeout);
    removeToast();
  });
}
