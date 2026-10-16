// boot.js — İLK BOYADAN ÖNCE çalışan küçük betik (ADR 0032).
//
// `<head>` içinde, modül OLMADAN ve eşzamanlı yüklenir: tema, platform,
// yoğunluk ve kenar çubuğu tercihi sayfa çizilmeden kökte durur. Böylece
// koyu temalı bir makinede uygulama bir an beyaz parlayıp sonra kararmaz.
// CSP satır içi betiğe izin vermediği için ayrı dosyadır (`script-src 'self'`).
(function boot() {
  var root = document.documentElement;
  var nav = window.navigator || {};
  var hint = String(nav.userAgentData && nav.userAgentData.platform || nav.platform || nav.userAgent || '');
  root.dataset.platform = /mac/i.test(hint) ? 'mac' : (/win/i.test(hint) ? 'win' : 'linux');
  root.dataset.shell = window.__TAURI__ ? 'native' : 'web';
  // macOS kabuğu bütünleşik başlık çubuğuyla açılır (src-tauri/src/native.rs):
  // trafik ışıklarına ilk boyadan önce yer açılsın.
  if (root.dataset.platform === 'mac' && window.__TAURI__) root.dataset.titlebar = 'overlay';

  function pref(key, fallback) {
    try {
      return window.localStorage.getItem('km.pref.' + key) || fallback;
    } catch (error) {
      return fallback;
    }
  }

  var theme = pref('theme', 'system');
  var dark = theme === 'dark'
    || (theme === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme = dark ? 'dark' : 'light';
  root.dataset.themePref = theme;
  root.dataset.density = pref('density', 'comfortable') === 'compact' ? 'compact' : 'comfortable';
  if (pref('sidebar', 'shown') === 'hidden') root.classList.add('km-sidebar-hidden');

  // Soğuk açılış ölçüsünün sıfırı (perf.js).
  window.__kmBootAt = window.performance ? window.performance.now() : 0;
}());
