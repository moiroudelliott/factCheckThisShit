// vachecker.info — thème clair ou sombre (fiche et résumé).
// Chargé dans <head>, avant l'affichage : pas d'éclair blanc en thème sombre.
// ?theme=sombre (ou clair) dans l'adresse l'emporte sur le choix mémorisé.
(function () {
  'use strict';

  const KEY = 'vc-theme';
  const root = document.documentElement;
  let saved = '';
  try { saved = localStorage.getItem(KEY) || ''; } catch (_) { /* navigation privée */ }
  const asked = new URLSearchParams(location.search).get('theme');
  if ((asked || saved) === 'sombre') root.dataset.theme = 'sombre';

  const isDark = () => root.dataset.theme === 'sombre';

  function set(dark) {
    if (dark) root.dataset.theme = 'sombre';
    else delete root.dataset.theme;
    try { localStorage.setItem(KEY, dark ? 'sombre' : 'clair'); } catch (_) { /* rien */ }
    // L'adresse suit le choix : un rechargement ne revient pas à l'ancien thème
    const url = new URL(location.href);
    if (url.searchParams.has('theme')) {
      url.searchParams.set('theme', dark ? 'sombre' : 'clair');
      history.replaceState(null, '', url);
    }
  }

  // Un bouton « Thème sombre » / « Thème clair » ; onChange : liens à mettre à jour
  function bind(button, onChange) {
    const label = () => { button.textContent = isDark() ? 'Thème clair' : 'Thème sombre'; };
    button.addEventListener('click', () => {
      set(!isDark());
      label();
      if (onChange) onChange();
    });
    label();
  }

  window.vcTheme = { isDark, bind };
})();
