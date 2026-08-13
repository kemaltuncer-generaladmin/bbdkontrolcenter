// Tarih alanı — kendi takvimimiz.
//
// NEDEN YERLİ `<input type="date">` KULLANILMIYOR: masaüstü kabuğu Linux'ta
// WebKitGTK ile çalışıyor ve oradaki yerleşik tarih seçici bu sürümde bozuk —
// gün seçilince açılır pencere KAPANMIYOR, alan takılı kalıyor. Tarayıcıda
// sorunsuz göründüğü için gözden kaçmıştı; uygulamanın içinde kullanılamaz.
//
// Bu bileşen tamamen bizim denetimimizde: gün tıklanınca kapanır, Esc kapatır,
// dışarı tıklamak kapatır, klavyeyle "13.08.2026" ya da "2026-08-13" yazılabilir.
//
// Tek kopya (ADR 0011).

import { button, h, todayIso } from './kit.js';

const TR_MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
  'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
// Pazartesi başlangıçlı — Türkiye'de hafta böyle başlar.
const TR_DAYS = ['Pt', 'Sa', 'Ça', 'Pe', 'Cu', 'Ct', 'Pz'];

const pad = (value) => String(value).padStart(2, '0');

/** Date → "2026-08-13". `toISOString()` UTC'ye kaydırır, kullanılmaz. */
export function isoOf(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "2026-08-13" → Date (yerel gün başı). Geçersizse null. */
export function dateOf(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(iso || ''))) return null;
  const [year, month, day] = iso.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "2026-08-13" → "13.08.2026". */
export function trOf(iso) {
  const date = dateOf(iso);
  return date ? `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}` : '';
}

/** Kullanıcının yazdığını çözer: "13.08.2026", "13/08/2026", "2026-08-13". */
export function parseTyped(text) {
  const cleaned = String(text || '').trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(cleaned)) return dateOf(cleaned) ? cleaned : null;

  const match = cleaned.match(/^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$/);
  if (!match) return null;
  const [, day, month, year] = match;
  const iso = `${year}-${pad(month)}-${pad(day)}`;
  const date = dateOf(iso);
  // 31.02.2026 gibi bir tarih Date tarafından kaydırılır; kaydıysa geçersizdir.
  return date && isoOf(date) === iso ? iso : null;
}

/**
 * Tarih alanı kurar.
 *
 * @returns {{node, get, set, destroy}} — `destroy()` GLOBAL dinleyicileri
 *   bırakır; panel cleanup'ında çağrılmazsa kapalı panelin takvimi hâlâ
 *   `document` üzerinde `mousedown`/`keydown` dinler.
 */
export function dateField({ value = '', label = 'Tarih', onChange } = {}) {
  let current = value;
  let cursor = dateOf(current) || new Date();
  let open = false;

  const wrap = h('div', 'kit-datefield');
  const input = h('input', 'df-input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.autocomplete = 'off';
  input.placeholder = 'gg.aa.yyyy';
  input.setAttribute('aria-label', label);
  input.value = trOf(current);

  const toggle = h('button', 'df-toggle', '📅');
  toggle.type = 'button';
  toggle.tabIndex = -1;
  toggle.setAttribute('aria-label', `${label} takvimini aç`);

  const pop = h('div', 'df-pop');
  pop.hidden = true;

  const commit = (iso, { close = true } = {}) => {
    current = iso;
    input.value = trOf(iso);
    input.classList.remove('bad');
    if (close) setOpen(false);
    if (onChange) onChange(iso);
  };

  // --- takvim çizimi --------------------------------------------------
  const head = h('div', 'df-head');
  const prev = h('button', 'df-nav', '‹');
  prev.type = 'button';
  prev.setAttribute('aria-label', 'Önceki ay');
  const title = h('span', 'df-title');
  const next = h('button', 'df-nav', '›');
  next.type = 'button';
  next.setAttribute('aria-label', 'Sonraki ay');
  head.append(prev, title, next);

  const grid = h('div', 'df-grid');
  const foot = h('div', 'df-foot');
  const today = h('button', 'df-quick', 'Bugün');
  today.type = 'button';
  const clear = h('button', 'df-quick', 'Kapat');
  clear.type = 'button';
  foot.append(today, h('span', 'df-spacer'), clear);
  pop.append(head, grid, foot);

  function paint() {
    title.textContent = `${TR_MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`;
    grid.replaceChildren();
    for (const name of TR_DAYS) grid.append(h('span', 'df-dow', name));

    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    // getDay(): 0 = Pazar. Pazartesi başlangıcına kaydır.
    const lead = (first.getDay() + 6) % 7;
    const days = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
    const now = todayIso();

    for (let i = 0; i < lead; i += 1) grid.append(h('span', 'df-empty'));

    for (let day = 1; day <= days; day += 1) {
      const iso = `${cursor.getFullYear()}-${pad(cursor.getMonth() + 1)}-${pad(day)}`;
      const cell = h('button', 'df-day', String(day));
      cell.type = 'button';
      cell.classList.toggle('on', iso === current);
      cell.classList.toggle('today', iso === now);
      // Gün seçilince pencere KAPANIR — bozuk davranışın düzeltildiği yer burası.
      cell.addEventListener('click', () => commit(iso));
      grid.append(cell);
    }
  }

  function setOpen(next_) {
    open = next_;
    pop.hidden = !open;
    wrap.classList.toggle('open', open);
    if (open) {
      cursor = dateOf(current) || new Date();
      paint();
      document.addEventListener('mousedown', onOutside, true);
      document.addEventListener('keydown', onKey, true);
    } else {
      document.removeEventListener('mousedown', onOutside, true);
      document.removeEventListener('keydown', onKey, true);
    }
  }

  const onOutside = (event) => { if (!wrap.contains(event.target)) setOpen(false); };
  const onKey = (event) => {
    if (event.key === 'Escape') { setOpen(false); input.focus(); }
  };

  prev.addEventListener('click', () => {
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1);
    paint();
  });
  next.addEventListener('click', () => {
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
    paint();
  });
  today.addEventListener('click', () => commit(todayIso()));
  clear.addEventListener('click', () => setOpen(false));
  toggle.addEventListener('click', () => setOpen(!open));

  // Elle yazma da çalışsın: alan terk edilince ya da Enter'da çözülür.
  const readTyped = () => {
    const parsed = parseTyped(input.value);
    if (parsed) {
      commit(parsed, { close: false });
    } else if (input.value.trim() === '') {
      input.value = trOf(current);
      input.classList.remove('bad');
    } else {
      input.classList.add('bad');
    }
  };
  input.addEventListener('blur', readTyped);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); readTyped(); setOpen(false); }
    if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); }
  });

  wrap.append(input, toggle, pop);

  return {
    node: wrap,
    get: () => current,
    set: (iso) => {
      current = iso;
      input.value = trOf(iso);
      input.classList.remove('bad');
      if (open) { cursor = dateOf(iso) || new Date(); paint(); }
    },
    destroy: () => setOpen(false),
  };
}

/** Hazır aralıklar — 20 ekranda aynı isimler kullanılsın diye burada. */
export const RANGE_PRESETS = [
  { key: 'today', label: 'Bugün', days: 1 },
  { key: 'yesterday', label: 'Dün', days: 1, offset: 1 },
  { key: 'week', label: '7 gün', days: 7 },
  { key: 'month', label: '30 gün', days: 30 },
  { key: 'quarter', label: '90 gün', days: 90 },
];

/**
 * İki tarih alanı + hazır aralık çipleri.
 *
 * @returns {{node, get, set, destroy}} — `get()` → {start, end}
 */
export function dateRange({ start = todayIso(-6), end = todayIso(), label = 'Aralık',
  presets = RANGE_PRESETS, onChange } = {}) {
  const node = h('div', 'kit-field-row');
  let value = { start, end };

  const fire = () => onChange?.({ ...value });

  const from = dateField({
    value: value.start,
    label: `${label} başlangıç`,
    onChange: (iso) => { value.start = iso; fire(); },
  });
  const to = dateField({
    value: value.end,
    label: `${label} bitiş`,
    onChange: (iso) => { value.end = iso; fire(); },
  });

  node.append(h('span', 'kit-filter-label', label), from.node, h('span', undefined, '–'), to.node);

  for (const preset of presets) {
    const chip = h('button', 'kit-chip', preset.label);
    chip.type = 'button';
    chip.addEventListener('click', () => {
      const offset = preset.offset || 0;
      value = {
        start: todayIso(-(preset.days - 1) - offset),
        end: todayIso(-offset),
      };
      from.set(value.start);
      to.set(value.end);
      fire();
    });
    node.append(chip);
  }

  return {
    node,
    get: () => ({ ...value }),
    set(next) {
      value = { ...value, ...next };
      from.set(value.start);
      to.set(value.end);
    },
    // Panel cleanup'ında ÇAĞRILMALI: iki takvim de global dinleyici tutuyor.
    destroy() { from.destroy(); to.destroy(); },
  };
}
