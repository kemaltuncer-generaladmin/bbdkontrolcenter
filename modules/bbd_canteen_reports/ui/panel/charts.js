// Grafikler — satır içi SVG.
//
// Dış kitaplık yok: kabuk bir bundler taşımıyor ve CSP dışarıdan script
// çekilmesini engelliyor. SVG hem keskin hem tema uyumlu, hem de yazdırılabilir.
//
// TASARIM KURALI: renk tek başına anlam taşımaz. Her grafiğin ekseni, etiketi
// ve sayısı vardır; renk yalnız gruplandırma içindir.

const NS = 'http://www.w3.org/2000/svg';

const el = (name, attrs = {}, text) => {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
};

/** Vurgu rengini beyazla karıştırır: 0 = beyaz, 1 = tam ton. */
function mix(hex, amount) {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift) => {
    const tone = (value >> shift) & 0xff;
    return Math.round(255 + (tone - 255) * Math.max(0, Math.min(1, amount)));
  };
  return `rgb(${channel(16)}, ${channel(8)}, ${channel(0)})`;
}

const INK = '#111722';
const SOFT = '#8792a5';
const LINE = '#e9ecf2';
const ACCENT = '#5b8cff';

/** Kuruşu kısa gösterir: 1234500 → "12,3B ₺" (eksen etiketleri için). */
function shortMoney(kurus) {
  const value = Number(kurus || 0) / 100;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1).replace('.', ',')}B ₺`;
  return `${Math.round(value)} ₺`;
}

/**
 * Çizgi/alan grafiği — zaman serisi.
 * @param {Array<{label: string, value: number}>} points
 */
export function lineChart(points, { height = 190, valueFormat = shortMoney } = {}) {
  const width = 720;
  const pad = { top: 14, right: 12, bottom: 30, left: 58 };
  const svg = el('svg', {
    viewBox: `0 0 ${width} ${height}`,
    class: 'cr-chart',
    role: 'img',
  });

  if (points.length === 0) {
    svg.append(el('text', { x: width / 2, y: height / 2, 'text-anchor': 'middle',
      fill: SOFT, 'font-size': 12 }, 'Veri yok'));
    return svg;
  }

  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const peak = Math.max(...points.map((point) => point.value), 1);
  // Üst sınırı yuvarla ki eksen etiketleri okunur çıksın.
  const top = peak * 1.1;

  // Yatay kılavuzlar + eksen değerleri
  for (let step = 0; step <= 4; step += 1) {
    const y = pad.top + (plotH * step) / 4;
    const value = top * (1 - step / 4);
    svg.append(el('line', { x1: pad.left, y1: y, x2: width - pad.right, y2: y,
      stroke: LINE, 'stroke-width': 1 }));
    svg.append(el('text', { x: pad.left - 8, y: y + 3.5, 'text-anchor': 'end',
      fill: SOFT, 'font-size': 9.5 }, valueFormat(value)));
  }

  const x = (index) => pad.left + (points.length === 1
    ? plotW / 2
    : (plotW * index) / (points.length - 1));
  const y = (value) => pad.top + plotH - (plotH * value) / top;

  const path = points.map((point, index) =>
    `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');

  // Alan dolgusu — eğilimi okunur kılar, ölçeği bozmaz.
  svg.append(el('path', {
    d: `${path} L${x(points.length - 1).toFixed(1)},${pad.top + plotH} `
      + `L${x(0).toFixed(1)},${pad.top + plotH} Z`,
    fill: ACCENT, 'fill-opacity': 0.10,
  }));
  svg.append(el('path', { d: path, fill: 'none', stroke: ACCENT, 'stroke-width': 2,
    'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));

  // Nokta + başlık (fare üstünde tam değer)
  for (const [index, point] of points.entries()) {
    const dot = el('circle', { cx: x(index), cy: y(point.value), r: 3,
      fill: '#fff', stroke: ACCENT, 'stroke-width': 1.8 });
    dot.append(el('title', {}, `${point.label}: ${valueFormat(point.value)}`));
    svg.append(dot);
  }

  // X etiketleri — sıklaşırsa seyreltilir, üst üste binmez.
  const every = Math.max(1, Math.ceil(points.length / 12));
  for (const [index, point] of points.entries()) {
    if (index % every !== 0 && index !== points.length - 1) continue;
    svg.append(el('text', { x: x(index), y: height - 10, 'text-anchor': 'middle',
      fill: SOFT, 'font-size': 9.5 }, point.label));
  }

  return svg;
}

/**
 * Yatay çubuk — sıralı kırılımlar (en çok satan ürün, sınıf vb.).
 * @param {Array<{label: string, value: number, display: string}>} rows
 */
export function barChart(rows, { max = 12 } = {}) {
  const list = rows.slice(0, max);
  const rowH = 24;
  const width = 720;
  const height = Math.max(1, list.length) * rowH + 8;
  const labelW = 190;
  const valueW = 92;

  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'cr-chart',
    role: 'img' });

  if (list.length === 0) {
    svg.append(el('text', { x: width / 2, y: 20, 'text-anchor': 'middle',
      fill: SOFT, 'font-size': 12 }, 'Veri yok'));
    return svg;
  }

  const peak = Math.max(...list.map((row) => row.value), 1);
  const plotW = width - labelW - valueW - 16;

  for (const [index, row] of list.entries()) {
    const y = index * rowH + 4;
    const barW = Math.max(2, (plotW * row.value) / peak);

    const label = el('text', { x: labelW - 10, y: y + 13.5, 'text-anchor': 'end',
      fill: INK, 'font-size': 11 }, row.label.length > 26 ? `${row.label.slice(0, 25)}…` : row.label);
    label.append(el('title', {}, row.label));
    svg.append(label);

    svg.append(el('rect', { x: labelW, y: y + 3, width: plotW, height: 14, rx: 4,
      fill: '#f1f4f9' }));
    const bar = el('rect', { x: labelW, y: y + 3, width: barW, height: 14, rx: 4,
      fill: row.tone || ACCENT });
    bar.append(el('title', {}, `${row.label}: ${row.display}`));
    svg.append(bar);

    svg.append(el('text', { x: width - 8, y: y + 13.5, 'text-anchor': 'end',
      fill: INK, 'font-size': 11 }, row.display));
  }

  return svg;
}

/**
 * Saat ısı şeridi — hangi teneffüste yoğunluk var.
 * Sayı da yazılır: renk tek başına anlam taşımaz.
 */
export function hourStrip(hours) {
  const width = 720;
  const height = 62;
  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'cr-chart',
    role: 'img' });

  const peak = Math.max(...hours.map((row) => row.count), 1);
  const cellW = width / 24;

  for (const row of hours) {
    const x = row.hour * cellW;
    const ratio = row.count / peak;
    // Açıktan koyuya tek renk ailesi — sıralı veride doğru olan budur.
    const fill = row.count === 0 ? '#f4f6fa' : mix(ACCENT, 0.12 + ratio * 0.78);

    const cell = el('rect', { x: x + 1, y: 4, width: cellW - 2, height: 28, rx: 4, fill });
    cell.append(el('title', {}, `${String(row.hour).padStart(2, '0')}:00 — ${row.count} işlem`));
    svg.append(cell);

    if (row.count > 0) {
      svg.append(el('text', {
        x: x + cellW / 2, y: 22, 'text-anchor': 'middle', 'font-size': 9,
        fill: ratio > 0.55 ? '#ffffff' : INK,
      }, String(row.count)));
    }
    if (row.hour % 2 === 0) {
      svg.append(el('text', { x: x + cellW / 2, y: 47, 'text-anchor': 'middle',
        fill: SOFT, 'font-size': 8.5 }, String(row.hour).padStart(2, '0')));
    }
  }

  svg.append(el('text', { x: 0, y: 59, fill: SOFT, 'font-size': 8.5 }, 'saat'));
  return svg;
}

/** Pareto: çubuk (ciro) + kümülatif pay çizgisi. ABC sınıflandırmasını görünür kılar. */
export function paretoChart(rows, { max = 15 } = {}) {
  const list = rows.slice(0, max);
  const width = 720;
  const height = 210;
  const pad = { top: 14, right: 46, bottom: 52, left: 52 };

  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'cr-chart',
    role: 'img' });

  if (list.length === 0) {
    svg.append(el('text', { x: width / 2, y: height / 2, 'text-anchor': 'middle',
      fill: SOFT, 'font-size': 12 }, 'Veri yok'));
    return svg;
  }

  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const peak = Math.max(...list.map((row) => row.total), 1);
  const slot = plotW / list.length;

  for (let step = 0; step <= 4; step += 1) {
    const y = pad.top + (plotH * step) / 4;
    svg.append(el('line', { x1: pad.left, y1: y, x2: width - pad.right, y2: y,
      stroke: LINE, 'stroke-width': 1 }));
    svg.append(el('text', { x: pad.left - 7, y: y + 3.5, 'text-anchor': 'end',
      fill: SOFT, 'font-size': 9 }, shortMoney(peak * (1 - step / 4))));
    svg.append(el('text', { x: width - pad.right + 7, y: y + 3.5,
      fill: SOFT, 'font-size': 9 }, `%${100 - step * 25}`));
  }

  const TONE = { A: '#4f7ce8', B: '#8aa9f0', C: '#c3d0ee' };

  for (const [index, row] of list.entries()) {
    const barH = (plotH * row.total) / peak;
    const x = pad.left + index * slot + slot * 0.18;
    const bar = el('rect', {
      x, y: pad.top + plotH - barH, width: slot * 0.64, height: Math.max(1, barH),
      rx: 3, fill: TONE[row.abc] || TONE.C,
    });
    bar.append(el('title', {},
      `${row.name}\n${row.qty} adet · pay %${row.share} · ${row.abc} sınıfı`));
    svg.append(bar);

    const label = el('text', {
      x: pad.left + index * slot + slot / 2, y: height - 34,
      'text-anchor': 'end', fill: SOFT, 'font-size': 8.5,
      transform: `rotate(-40 ${pad.left + index * slot + slot / 2} ${height - 34})`,
    }, row.name.length > 18 ? `${row.name.slice(0, 17)}…` : row.name);
    label.append(el('title', {}, row.name));
    svg.append(label);
  }

  const path = list.map((row, index) => {
    const x = pad.left + index * slot + slot / 2;
    const y = pad.top + plotH - (plotH * row.cumulativeShare) / 100;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  svg.append(el('path', { d: path, fill: 'none', stroke: '#b45309', 'stroke-width': 1.8,
    'stroke-dasharray': '4 3' }));

  // %80 eşiği — A sınıfının bittiği yer.
  const y80 = pad.top + plotH - plotH * 0.8;
  svg.append(el('line', { x1: pad.left, y1: y80, x2: width - pad.right, y2: y80,
    stroke: '#b45309', 'stroke-width': 1, 'stroke-dasharray': '2 4', opacity: 0.6 }));
  svg.append(el('text', { x: pad.left + 4, y: y80 - 4, fill: '#b45309', 'font-size': 8.5 },
    '%80 — A sınıfı sınırı'));

  return svg;
}
