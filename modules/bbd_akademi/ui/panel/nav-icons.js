// BBD Akademi — gezinme simgeleri (ana grup şeridi ve ana sayfa kutucukları).
//
// Kabuğun menü simgeleriyle aynı dil: 24×24 kutu, yalnız çizgi, ortak kalınlık
// (çizim CSS'te — `.ak-ico`). Panel kabuğun `icons.js`'ini import etmez (panel
// yalnız ui-kit'e bağlanır); bilinmeyen ad `dot`a düşer, kutu asla boş kalmaz.
// Simgeler SÜSTÜR (`aria-hidden`): anlamı her zaman yanındaki yazı taşır.

const PATHS = {
  dot: ['M12 12h.01'],
  home: ['M3 10.5 12 3l9 7.5', 'M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5'],
  sun: ['M12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10', 'M12 1.5v2', 'M12 20.5v2', 'M4.2 4.2l1.4 1.4', 'M18.4 18.4l1.4 1.4',
    'M1.5 12h2', 'M20.5 12h2', 'M4.2 19.8l1.4-1.4', 'M18.4 5.6l1.4-1.4'],
  users: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2', 'M9 3.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7',
    'M22 21v-2a4 4 0 0 0-3-3.9', 'M16 3.7a4 4 0 0 1 0 7.5'],
  compass: ['M12 2.5a9.5 9.5 0 1 1 0 19 9.5 9.5 0 0 1 0-19', 'm15.8 8.2-2.1 5.5-5.5 2.1 2.1-5.5z'],
  calendar: ['M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z', 'M8 2v4', 'M16 2v4',
    'M3 10h18'],
  check: ['M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2', 'M9 2h6v4H9z',
    'm9 14 2 2 4-4'],
  book: ['M2 4h5.5A2.5 2.5 0 0 1 10 6.5V20a2 2 0 0 0-2-2H2z', 'M22 4h-5.5A2.5 2.5 0 0 0 14 6.5V20a2 2 0 0 1 2-2h6z'],
  megaphone: ['M3 11v2a1 1 0 0 0 1 1h3l9 5V5L7 10H4a1 1 0 0 0-1 1z', 'M19 9a3.5 3.5 0 0 1 0 6'],
  wallet: ['M3 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2', 'M3 6v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2H3',
    'M17.5 13h.01'],
  chart: ['M3 3v18h18', 'M8 17v-6', 'M13 17V7', 'M18 17v-3'],
  gear: ['M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6',
    'M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'],
  arrow: ['M5 12h14', 'm13 6 6 6-6 6'],
  lock: ['M5 11a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z', 'M8 9V7a4 4 0 0 1 8 0v2'],
};

/** Süs simgesi (`aria-hidden`). `name` bilinmiyorsa nokta çizilir. */
export function navIcon(name, className = 'ak-ico') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', className);
  for (const d of PATHS[name] || PATHS.dot) {
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  return svg;
}
