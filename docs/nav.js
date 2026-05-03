const SUN_SVG = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`;
const MOON_SVG = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

function getTheme() {
  try {
    const stored = localStorage.getItem('theme');
    if (stored) return stored;
    const param = new URLSearchParams(window.location.search).get('theme');
    if (param === 'light' || param === 'dark') return param;
    return 'dark';
  } catch (e) { return 'dark'; }
}

function applyTheme(t) {
  document.documentElement.classList.remove('dark', 'light');
  document.documentElement.classList.add(t);
  document.querySelectorAll('.theme-icon').forEach(el => {
    el.innerHTML = t === 'dark' ? SUN_SVG : MOON_SVG;
  });
  try { localStorage.setItem('theme', t); } catch (e) { }
  window.updateChartColors?.();
}

function toggleTheme() { applyTheme(getTheme() === 'dark' ? 'light' : 'dark'); }

function updateNavPill() {
  const nav = document.getElementById('topnav-desktop');
  const pill = document.getElementById('topnav-pill');
  if (!nav || !pill) return;
  const active = nav.querySelector('.topnav-link.active');
  if (!active) return;
  const navRect = nav.getBoundingClientRect();
  const activeRect = active.getBoundingClientRect();
  pill.style.left = (activeRect.left - navRect.left) + 'px';
  pill.style.width = activeRect.width + 'px';
  pill.style.opacity = '1';
}

function toggleMobileMenu() {
  const menu = document.getElementById('topnav-mobile-menu');
  const icon = document.getElementById('hamburger-icon');
  const btn = document.getElementById('hamburger-btn');
  const isOpen = menu.style.display === 'block';
  menu.style.display = isOpen ? 'none' : 'block';
  icon.innerHTML = isOpen
    ? '<path d="M3 12h18M3 6h18M3 18h18"/>'
    : '<path d="M18 6L6 18M6 6l12 12"/>';
  btn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const menu = document.getElementById('topnav-mobile-menu');
    if (menu && menu.style.display === 'block') toggleMobileMenu();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  applyTheme(getTheme());
  updateNavPill();
});
