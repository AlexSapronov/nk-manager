/* Same preference key as gis-trueapi, km-split and marking-bm.
   Browser storage is shared only when applications use the same origin. */
(() => {
  const key = 'gis_theme';
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let chosen;
  try { chosen = localStorage.getItem(key); } catch (_) { /* Storage is optional. */ }

  function refresh() {
    const theme = ['light', 'dark'].includes(chosen)
      ? chosen : media.matches ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      const label = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
      button.textContent = theme === 'dark' ? '☀' : '☾';
      button.title = label;
      button.setAttribute('aria-label', label);
    });
  }

  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    chosen = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(key, chosen); } catch (_) { /* Still switch this page. */ }
    refresh();
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { chosen = localStorage.getItem(key); } catch (_) { chosen = undefined; }
    refresh();
  });
  media.addEventListener('change', refresh);
  refresh();
})();
