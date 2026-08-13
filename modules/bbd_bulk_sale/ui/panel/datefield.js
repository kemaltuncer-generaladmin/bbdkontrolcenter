// Tarih alanı — kendi takvimimiz.
//
// NEDEN YERLİ `<input type="date">` KULLANILMIYOR: masaüstü kabuğu Linux'ta
// WebKitGTK ile çalışıyor ve oradaki yerleşik tarih seçici bu sürümde bozuk —
// gün seçilince açılır pencere KAPANMIYOR, alan takılı kalıyor. Tarayıcıda
// sorunsuz göründüğü için gözden kaçmıştı; uygulamanın içinde kullanılamaz.
//
// Bu bileşen tamamen bizim denetimimizde: gün tıklanınca kapanır, Esc kapatır,
// dışarı tıklamak kapatır, klavyeyle "13.08.2026" ya da "2026-08-13" yazılabilir.

const TR_MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
  'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
// Pazartesi başlangıçlı — Türkiye'de hafta böyle başlar.
const TR_DAYS = ['Pt', 'Sa', 'Ça', 'Pe', 'Cu', 'Ct', 'Pz'];

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

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
 * @param {object} options
 * @param {string} options.value    başlangıç değeri (ISO)
 * @param {string} options.label    erişilebilirlik etiketi
 * @param {(iso: string) => void} options.onChange
 * @returns {{node: HTMLElement, get: () => string, set: (iso: string) => void}}
 */
export function dateField({ value = '', label = 'Tarih', onChange } = {}) {
  let current = value;
  let cursor = dateOf(current) || new Date();
  let open = false;

  const wrap = h('div', 'df');
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
    const todayIso = isoOf(new Date());

    for (let i = 0; i < lead; i += 1) grid.append(h('span', 'df-empty'));

    for (let day = 1; day <= days; day += 1) {
      const iso = `${cursor.getFullYear()}-${pad(cursor.getMonth() + 1)}-${pad(day)}`;
      const cell = h('button', 'df-day', String(day));
      cell.type = 'button';
      cell.classList.toggle('on', iso === current);
      cell.classList.toggle('today', iso === todayIso);
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
  today.addEventListener('click', () => commit(isoOf(new Date())));
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
