// Menü ikonları. Manifest yalnızca ikon ADINI verir (`ui.nav.icon`), çizim
// burada durur — kabuk modül adı bilmez, ikon adı bilir.
//
// Hepsi 24×24 kutuda, yalnızca çizgi (fill yok), ortak kalınlık.
// Bilinmeyen ad `dot`a düşer; menü asla boş kutu göstermez.

export const ICONS = {
  dot: ['M12 12h.01'],

  calendar: ['M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z', 'M8 2v4', 'M16 2v4', 'M3 10h18'],
  bell: ['M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'],
  utensils: ['M5 3v7a2 2 0 0 0 4 0V3', 'M7 12v9', 'M18 3c-1.6 1.2-2.5 3.2-2.5 5.5S16.4 13 18 13v8'],
  graduation: ['m12 3 10 5-10 5L2 8z', 'M6 11v5c0 1.7 2.7 3 6 3s6-1.3 6-3v-5'],
  box: ['M21 8v8a2 2 0 0 1-1 1.7l-7 4a2 2 0 0 1-2 0l-7-4A2 2 0 0 1 3 16V8a2 2 0 0 1 1-1.7l7-4a2 2 0 0 1 2 0l7 4A2 2 0 0 1 21 8', 'm3.3 7 8.7 5 8.7-5', 'M12 22V12'],
  cart: ['M8.5 20.5h.01', 'M18 20.5h.01', 'M2 3h2.2l2.5 12.2a2 2 0 0 0 2 1.6h8.1a2 2 0 0 0 2-1.6L21 7H5.2'],
  credit_card: ['M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z', 'M2 10h20', 'M6 15h4'],
  chart: ['M3 3v18h18', 'M8 17v-6', 'M13 17V7', 'M18 17v-3'],
  archive: ['M3 5a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v3H3z', 'M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8', 'M10 12h4'],
  message: ['M21 14a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
  gauge: ['M20.5 16a9 9 0 1 0-17 0', 'm12 15 4.5-4.5'],
  layers: ['m12 2 9 5-9 5-9-5z', 'm3 12 9 5 9-5', 'm3 17 9 5 9-5'],
  tag: ['M20.6 13.4 12 22l-9-9V3h10l7.6 7.6a2 2 0 0 1 0 2.8z', 'M7.5 7.5h.01'],
  image: ['M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z', 'M8.5 9.5h.01', 'm21 16-5-5L5 21'],
  file_text: ['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z', 'M14 3v5h5', 'M9 13h6', 'M9 17h6'],
  sparkles: ['m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z', 'm18.5 15.5.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z'],
  users: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2', 'M9 3.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7', 'M22 21v-2a4 4 0 0 0-3-3.9', 'M16 3.7a4 4 0 0 1 0 7.5'],
  inbox: ['M22 12h-6l-2 3h-4l-2-3H2', 'M5.4 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.4-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.8 1.1z'],
  truck: ['M10 17V5H2v12h3', 'M14 17h-4', 'M20 17h2v-4l-3-4h-5v8h2', 'M7.5 15.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4', 'M17.5 15.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4'],
  receipt: ['M6 2h12v20l-3-2-3 2-3-2-3 2z', 'M9.5 7h5', 'M9.5 11h5', 'M9.5 15h3'],
  percent: ['M19 5 5 19', 'M6.5 4a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5', 'M17.5 15a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5'],
  rotate: ['M3 12a9 9 0 1 0 2.6-6.4L3 8', 'M3 3v5h5'],
  list: ['M8 6h13', 'M8 12h13', 'M8 18h13', 'M3.5 6h.01', 'M3.5 12h.01', 'M3.5 18h.01'],
  megaphone: ['M3 11v2a1 1 0 0 0 1 1h3l9 5V5L7 10H4a1 1 0 0 0-1 1z', 'M19 9a3.5 3.5 0 0 1 0 6'],
  cpu: ['M6 8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2z', 'M10 10h4v4h-4z', 'M9 2v4', 'M15 2v4', 'M9 18v4', 'M15 18v4', 'M2 9h4', 'M2 15h4', 'M18 9h4', 'M18 15h4'],
  monitor: ['M2 5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z', 'M8 21h8', 'M12 16v5'],
  pulse: ['M22 12h-4l-3 8-6-16-3 8H2'],
  clipboard: ['M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2', 'M9 2h6v4H9z', 'M9 12h6', 'M9 16h4'],
  book: ['M2 4h5.5A2.5 2.5 0 0 1 10 6.5V20a2 2 0 0 0-2-2H2z', 'M22 4h-5.5A2.5 2.5 0 0 0 14 6.5V20a2 2 0 0 1 2-2h6z'],
  repeat: ['m17 2 4 4-4 4', 'M3 11V9a4 4 0 0 1 4-4h14', 'm7 22-4-4 4-4', 'M21 13v2a4 4 0 0 1-4 4H3'],
  wallet: ['M3 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2', 'M3 6v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2H3', 'M17.5 13h.01'],
  file_plus: ['M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z', 'M14 3v5h5', 'M12 12v6', 'M9 15h6'],
  mail: ['M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z', 'm2.5 7.5 9.5 6 9.5-6'],
  sliders: ['M4 21v-6', 'M4 11V3', 'M12 21v-9', 'M12 8V3', 'M20 21v-4', 'M20 13V3', 'M1.5 15h5', 'M9.5 8h5', 'M17.5 17h5'],
  printer: ['M6 9V3h12v6', 'M6 18H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2', 'M6 14h12v7H6z'],
  shield: ['M12 22s8-3.9 8-10V5.5L12 2.5 4 5.5V12c0 6.1 8 10 8 10z'],
  search: ['M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14', 'm20 20-4-4'],
};

/** İkonu SVG olarak döndürür. Bilinmeyen ad sessizce `dot`a düşer. */
export function iconSvg(name) {
  const paths = ICONS[name] || ICONS.dot;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  for (const d of paths) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  return svg;
}
