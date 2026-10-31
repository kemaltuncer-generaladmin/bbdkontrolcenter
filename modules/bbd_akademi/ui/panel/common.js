// BBD Akademi — bölümlerin paylaştığı biçim, liste, pencere ve giriş yardımcıları.
//
// Uç adresi ve veri durumu BURADA YOKTUR; onlar `app` nesnesindedir
// (index.js). Her yardımcı ya düğüm ya da `{node, …}` denetleyici döndürür —
// denetleyiciyi DOM'a verirken `.node` yazılır (bkz. test_panel_dugum_sozlesmesi).
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_akademi/common.js  →  shell/ui-kit/
// Kaynak modules/bbd_akademi/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import {
  blockedButton, button, confirmSimple, confirmWithPin, confirmWithReason, debounce, dialogA11y, h,
  money, moneyInput, num, parseMoney, percent,
} from '../../ui-kit/kit.js';
import { alertBox, badge, drawer, emptyState, progress, skeletonRows } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { formGrid } from '../../ui-kit/form.js';
import { dateField } from '../../ui-kit/datefield.js';
import { createPicker } from '../../ui-kit/picker.js';
import { AkademiError, openBlob, problem, saveBlob } from './api.js';

export { money, moneyInput, num, parseMoney, percent, problem };

export const TZ = 'Europe/Istanbul';

// ------------------------------------------------------------------ tarih

/** 'YYYY-MM-DD' → '12.10.2026'. */
export function fmtDate(iso) {
  if (!iso) return '—';
  const [year, month, day] = String(iso).slice(0, 10).split('-');
  return day && month && year ? `${day}.${month}.${year}` : String(iso);
}

/** Zaman damgası → '12.10.2026 14:05' (Europe/Istanbul). */
export function fmtDateTime(value) {
  if (!value) return '—';
  const date = new Date(String(value).replace(/(\.\d{3})\d+/, '$1'));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('tr-TR', { timeZone: TZ, day: '2-digit', month: '2-digit',
    year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** Zaman damgası → '14:05'. 'HH:MM' gelirse olduğu gibi. */
export function fmtTime(value) {
  if (!value) return '—';
  if (/^\d{2}:\d{2}/.test(String(value))) return String(value).slice(0, 5);
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString('tr-TR', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
}

/** Europe/Istanbul'da bugün (+/- gün) → 'YYYY-MM-DD'. Bilgisayarın saat dilimi değil. */
export function todayIst(offsetDays = 0) {
  const now = new Date(Date.now() + offsetDays * 86_400_000);
  return now.toLocaleDateString('sv-SE', { timeZone: TZ });
}

export function addDays(iso, days) {
  const [year, month, day] = String(iso).split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

/** Haftanın pazartesisi (ISO gün). */
export function weekStart(iso = todayIst()) {
  const [year, month, day] = String(iso).split('-').map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay() || 7;
  return addDays(iso, 1 - weekday);
}

/** Akademi `DayOfWeek` anahtarları: [anahtar, kısa, uzun]. */
export const WEEKDAYS = [
  ['mon', 'Pzt', 'Pazartesi'], ['tue', 'Sal', 'Salı'], ['wed', 'Çar', 'Çarşamba'],
  ['thu', 'Per', 'Perşembe'], ['fri', 'Cum', 'Cuma'], ['sat', 'Cmt', 'Cumartesi'],
  ['sun', 'Paz', 'Pazar'],
];
export const DAY_LABEL = Object.fromEntries(WEEKDAYS.map(([key, , long]) => [key, long]));

/** 'YYYY-MM-DD' ya da yerel tarih+saat → ofsetli ISO ('…T09:30:00+03:00'). */
export function istanbulStamp(isoDate, clock = '00:00') {
  return `${isoDate}T${clock.length === 5 ? `${clock}:00` : clock}+03:00`;
}

// ------------------------------------------------------------ etiketler

export const GRADE_LEVELS = [5, 6, 7, 8, 9, 10, 11, 12, 'graduate'];
export function gradeText(level) {
  if (level === null || level === undefined || level === '') return '—';
  return level === 'graduate' ? 'Mezun' : `${level}. sınıf`;
}
export const TRACKS = { SAY: 'Sayısal', EA: 'Eşit ağırlık', SOZ: 'Sözel', DIL: 'Dil' };
export const EXAM_TARGETS = { LGS: 'LGS', YKS: 'YKS' };
export const CURRICULUM_EXAMS = { LGS: 'LGS', TYT: 'TYT', AYT: 'AYT' };

/** `[metin, ton]` haritasından rozet. Bilinmeyen değer kendi adıyla, sönük. */
export function statusBadge(map, key) {
  const [text, tone] = map?.[key] || [key ? String(key) : '—', 'dim'];
  return badge(text, tone);
}

export function labelOf(map, key, fallback = '—') {
  const value = map?.[key];
  if (Array.isArray(value)) return value[0];
  return value || (key ? String(key) : fallback);
}

export function personName(ref) {
  if (!ref) return '—';
  if (typeof ref === 'string') return ref;
  return ref.displayName || ref.name || ref.fullName || '—';
}

/** {a: 1, b: 2} → seçenek listesi [{value, label}]. */
export function optionsOf(map, { empty } = {}) {
  const list = Object.entries(map).map(([value, label]) => ({ value,
    label: Array.isArray(label) ? label[0] : label }));
  return empty !== undefined ? [{ value: '', label: empty }, ...list] : list;
}

// -------------------------------------------------------------- yerleşim

/** Bölüm başlığı: başlık + alt yazı + sağda eylemler. */
export function sectionHead(title, { subtitle = '', actions = [] } = {}) {
  const node = h('div', 'ak-head');
  const text = h('div', 'ak-head-text');
  text.append(h('h2', 'ak-title', title));
  if (subtitle) text.append(h('div', 'ak-sub', subtitle));
  const tools = h('div', 'ak-tools');
  tools.append(...actions.filter(Boolean));
  node.append(text, tools);
  return node;
}

/** Yatay araç şeridi (süzgeçler + eylemler). */
export function toolbar(...nodes) {
  const node = h('div', 'ak-toolbar');
  node.append(...nodes.filter(Boolean));
  return node;
}

/** Anahtar–değer listesi: [[etiket, değer|düğüm], …]. */
export function kv(rows) {
  const node = h('dl', 'ak-kv');
  for (const [label, value] of rows.filter(Boolean)) {
    node.append(h('dt', '', label));
    const cell = h('dd');
    if (value instanceof Node) cell.append(value);
    else cell.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
    node.append(cell);
  }
  return node;
}

// ------------------------------------------------- kurum finansı kilidi (iş 83)

let financeUnlock = null;

/** Kilit penceresini açan işlev (`index.js` → `app.financeLock.ensure`); açılırsa true döner. */
export function setFinanceUnlock(fn) {
  financeUnlock = typeof fn === 'function' ? fn : null;
}

/** Kurum finansı kilitli (vekil `finance_access_required`)? Pencere açılır; kod girilirse true. */
export async function unlockFinance(error) {
  if (error?.code !== 'finance_access_required' || !financeUnlock) return false;
  return Boolean(await financeUnlock());
}

/** Hata kutusu + "Tekrar dene". */
export function errorBox(error, retry) {
  if (error?.code === 'finance_access_required') {
    // Kilit bir HATA değildir: sakin bir not + kilidi açan düğme; açılınca içerik kendiliğinden yüklenir.
    const lockedNode = h('div', 'ak-off');
    lockedNode.append(alertBox('Kurum finansı kilitli. Görmek için onay kodu gerekir.', 'info'));
    lockedNode.append(button('Finansın kilidini aç', { variant: 'ghost',
      onClick: async () => { if (await unlockFinance(error)) retry?.(); } }));
    return lockedNode;
  }
  if (error?.code === 'feature_disabled') {
    // Kapalı özellik bir HATA değildir: sakin bir not, "tekrar dene" yok.
    const off = h('div', 'ak-off');
    off.append(alertBox('Bu özellik kurumda kapalı. Yönetici Ayarlar → Özellikler\'den açabilir.', 'info'));
    return off;
  }
  const node = h('div', 'ak-error');
  node.append(alertBox(problem(error, 'Bilgiler alınamadı.'), 'bad'));
  if (retry) node.append(button('Tekrar dene', { variant: 'ghost', onClick: retry }));
  return node;
}

/**
 * Yükle → çiz. İlk yüklemede iskelet, hatada sebep + tekrar dene.
 *   const view = loader(async () => app.api.get(...), (data) => node);
 *   parent.append(view.node); view.reload();
 */
export function loader(load, render, { rows = 5, columns = 4 } = {}) {
  const node = h('div', 'ak-loader');
  let token = 0;
  async function reload() {
    const mine = ++token;
    if (!node.childElementCount) node.replaceChildren(skeletonRows(rows, columns));
    try {
      const data = await load();
      if (mine !== token) return;
      const content = await render(data);
      node.replaceChildren(...(Array.isArray(content) ? content : [content]).filter(Boolean));
    } catch (failure) {
      if (mine !== token) return;
      node.replaceChildren(errorBox(failure, reload));
    }
  }
  return { node, reload };
}

// ---------------------------------------------------------------- liste

/**
 * İmleçli liste (sözleşme §4: `{items, nextCursor}`). İlk sayfada iskelet;
 * aşağı kaydırınca ya da "Daha fazla" ile sonraki sayfa. Satır başına ek
 * istek ATILMAZ — gösterilen her şey liste yükünden gelir.
 *
 * @param {object} spec
 *   app, path, query (nesne ya da () => nesne), columns (dataTable sütunları),
 *   limit=50, empty: {title, text, actions}, onRow, dense, selectable, rowKey,
 *   map(items) → items (isteğe bağlı dönüşüm), onLoaded(allRows)
 */
export function pagedList(spec) {
  const { app, path, columns, limit = 50 } = spec;
  const node = h('div', 'ak-list');
  const tableSlot = h('div');
  const foot = h('div', 'ak-list-foot');
  const more = button('Daha fazla', { variant: 'ghost', onClick: () => loadMore() });
  const status = h('span', 'ak-list-count');
  foot.append(status, h('span', 'kit-spacer'), more);
  node.append(tableSlot, foot);

  let rows = [];
  let iterator = null;
  let busy = false;
  let token = 0;

  const table = dataTable({
    columns,
    rows: [],
    dense: spec.dense,
    selectable: spec.selectable,
    rowKey: spec.rowKey,
    onRow: spec.onRow,
    onSelect: spec.onSelect,
    empty: emptyState(spec.empty || { title: 'Kayıt yok' }),
  });

  // Görünür olunca sonraki sayfa (sonsuz kaydırma). Gözlemci yoksa düğme yeter.
  const sentinel = h('div', 'ak-sentinel');
  node.append(sentinel);
  const observer = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && iterator && !iterator.done && !busy
        && rows.length) loadMore();
    })
    : null;
  observer?.observe(sentinel);

  function paintFoot() {
    more.hidden = !iterator || iterator.done;
    more.disabled = busy;
    status.textContent = rows.length ? `${num(rows.length)} kayıt${iterator?.done ? '' : '+'}` : '';
  }

  async function loadMore() {
    if (!iterator || iterator.done || busy) return;
    busy = true;
    paintFoot();
    const mine = token;
    try {
      const page = await iterator.next();
      if (mine !== token) return;
      const mapped = spec.map ? spec.map(page) : page;
      rows = rows.concat(mapped);
      table.update({ rows });
      if (tableSlot.firstChild !== table.node) tableSlot.replaceChildren(table.node);
      spec.onLoaded?.(rows);
    } catch (failure) {
      if (mine !== token) return;
      tableSlot.replaceChildren(errorBox(failure, () => reload()));
    } finally {
      if (mine === token) busy = false;
      paintFoot();
    }
  }

  function reload(query) {
    token += 1;
    busy = false;
    rows = [];
    const resolved = query || (typeof spec.query === 'function' ? spec.query() : spec.query) || {};
    iterator = app.api.pages(path, resolved, { limit });
    tableSlot.replaceChildren(skeletonRows(6, Math.min(columns.length, 6)));
    paintFoot();
    return loadMore();
  }

  return {
    node,
    table,
    reload,
    rows: () => rows,
    /** Tek satırı yerinde güncelle (yazmadan sonra listeyi baştan çekmeden). */
    replace(match, next) {
      rows = rows.map((row) => (match(row) ? next : row));
      table.update({ rows });
    },
    remove(match) {
      rows = rows.filter((row) => !match(row));
      table.update({ rows });
    },
    destroy() { observer?.disconnect(); token += 1; },
  };
}

/** Sayfasız liste (`{items}`) için düz tablo + iskelet + hata. */
export function staticList(spec) {
  const table = dataTable({ columns: spec.columns, rows: [], dense: spec.dense,
    selectable: spec.selectable, rowKey: spec.rowKey, onRow: spec.onRow, onSelect: spec.onSelect,
    empty: emptyState(spec.empty || { title: 'Kayıt yok' }) });
  const view = loader(spec.load, (data) => {
    const items = Array.isArray(data) ? data : (data?.items || []);
    const mapped = spec.map ? spec.map(items) : items;
    table.update({ rows: mapped });
    spec.onLoaded?.(mapped, data);
    return table.node;
  });
  return { node: view.node, table, reload: view.reload };
}

// --------------------------------------------------------------- girişler

/** Debounce'lu arama kutusu (varsayılan 250 ms). */
export function searchBox({ placeholder = 'Ara', onSearch, delay = 250, value = '', label = '' } = {}) {
  const input = h('input', 'kit-input ak-search');
  input.type = 'search';
  input.placeholder = placeholder;
  // Yer tutucu etiket değildir (yazınca kaybolur): ekran okuyucu adı ayrıca verilir.
  input.setAttribute('aria-label', label || placeholder);
  input.value = value;
  const fire = debounce(() => onSearch?.(input.value.trim()), delay);
  input.addEventListener('input', fire);
  return { node: input, get value() { return input.value.trim(); }, destroy: () => fire.cancel() };
}

/** Açılır kutu: options [{value, label}]. */
export function selectBox(options, value, onChange, { label = '' } = {}) {
  const select = h('select', 'kit-select');
  if (label) select.setAttribute('aria-label', label);
  const fill = (list) => {
    select.replaceChildren();
    for (const option of list) {
      const item = h('option', '', option.label);
      item.value = String(option.value ?? '');
      select.append(item);
    }
  };
  fill(options);
  select.value = String(value ?? '');
  select.addEventListener('change', () => onChange?.(select.value));
  return {
    node: select,
    get value() { return select.value; },
    set(next) { select.value = String(next ?? ''); },
    options(list) { const keep = select.value; fill(list); select.value = keep; },
  };
}

/** Çoklu seçim çipleri: options [{value, label}]. */
export function multiChips(options, selected = [], onChange) {
  const node = h('div', 'ak-chips');
  const chosen = new Set((selected || []).map(String));
  const paint = () => {
    node.replaceChildren();
    for (const option of options) {
      const chip = h('button', `kit-chip${chosen.has(String(option.value)) ? ' on' : ''}`, option.label);
      chip.type = 'button';
      chip.setAttribute('aria-pressed', chosen.has(String(option.value)) ? 'true' : 'false');
      chip.addEventListener('click', () => {
        const key = String(option.value);
        if (chosen.has(key)) chosen.delete(key); else chosen.add(key);
        paint();
        onChange?.([...chosen]);
      });
      node.append(chip);
    }
  };
  paint();
  return { node, get value() { return [...chosen]; },
    set(list) { chosen.clear(); (list || []).forEach((item) => chosen.add(String(item))); paint(); } };
}

/** Tarih alanı (kitin `dateField`i — `<input type=date>` YASAK). */
export function dateInput(value, onChange, label = 'Tarih') {
  return dateField({ value, label, onChange });
}

/** Saat alanı 'HH:MM' (elle yazılır, terk edilince biçime oturur). */
export function clockInput(value, onChange, label = 'Saat') {
  const input = h('input', 'kit-input ak-clock');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.maxLength = 5;
  input.placeholder = 'SS:DD';
  input.value = value || '';
  input.setAttribute('aria-label', label);
  input.addEventListener('blur', () => {
    const match = input.value.trim().replace('.', ':').match(/^(\d{1,2}):?(\d{2})$/);
    if (match && Number(match[1]) < 24 && Number(match[2]) < 60) {
      input.value = `${match[1].padStart(2, '0')}:${match[2]}`;
      input.classList.remove('bad');
      onChange?.(input.value);
    } else if (input.value.trim()) {
      input.classList.add('bad');
    }
  });
  return { node: input, get value() { return input.value.trim(); } };
}

/**
 * Dosya seç / sürükle-bırak. Tür ve boyut ön denetimi UX'tir; asıl kapı
 * vekil ve Akademi'dedir.
 */
export function fileDrop({ accept = [], maxMb = 25, label = 'Dosya seçin ya da buraya bırakın',
  onFile } = {}) {
  const node = h('label', 'ak-drop');
  const input = h('input');
  input.type = 'file';
  if (accept.length) input.accept = accept.map((ext) => `.${ext}`).join(',');
  input.hidden = true;
  const text = h('span', 'ak-drop-text', label);
  const hint = h('span', 'ak-drop-hint',
    `${accept.length ? accept.map((ext) => ext.toUpperCase()).join(', ') : 'Dosya'} · en çok ${maxMb} MB`);
  const error = h('span', 'ak-drop-error');
  node.append(input, text, hint, error);

  const take = (file) => {
    error.textContent = '';
    if (!file) return;
    const ext = String(file.name).split('.').pop().toLowerCase();
    if (accept.length && !accept.includes(ext)) {
      error.textContent = `Bu dosya türü kabul edilmiyor (.${ext}).`;
      return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      error.textContent = `Dosya ${maxMb} MB'tan büyük.`;
      return;
    }
    text.textContent = `${file.name} (${num(Math.ceil(file.size / 1024))} KB)`;
    onFile?.(file);
  };
  input.addEventListener('change', () => take(input.files?.[0]));
  node.addEventListener('dragover', (event) => { event.preventDefault(); node.classList.add('over'); });
  node.addEventListener('dragleave', () => node.classList.remove('over'));
  node.addEventListener('drop', (event) => {
    event.preventDefault();
    node.classList.remove('over');
    take(event.dataTransfer?.files?.[0]);
  });
  return { node, clear() { input.value = ''; text.textContent = label; error.textContent = ''; } };
}

/** Yükleme ilerlemesi (okuma %0–100, sonra "gönderiliyor"). */
export function uploadProgress() {
  const bar = progress(['Dosya okunuyor', 'Akademi\'ye gönderiliyor', 'Tamamlandı']);
  return {
    node: bar.node,
    reading(percentValue) { bar.percent(Math.round(percentValue * 0.6), `Dosya okunuyor · %${percentValue}`); },
    sending() { bar.percent(75, 'Akademi\'ye gönderiliyor…'); },
    done(text = 'Tamamlandı') { bar.done(text); },
  };
}

// --------------------------------------------------------------- pencereler

/**
 * Form penceresi. `fields` kitin `formGrid` alanlarıdır; ayrıca
 * `{key, label, type: 'custom', render(draft, set) → Node | {node, update(draft)}, wide?}`
 * desteklenir. `draft` CANLI nesnedir; `update` verilirse formGrid alanı her
 * değiştiğinde çağrılır.
 * `onSubmit(draft, {key})` sonucu döndürür; `key` pencere boyunca SABİT
 * Idempotency-Key'dir (yeniden denemede ikinci kayıt açılmaz).
 * Akademi `validation_error` dönerse alan hataları ilgili kutunun altına yazılır.
 *
 * @returns {Promise<any|null>} onSubmit sonucu ya da vazgeçildiyse null
 */
export function formDialog(app, { title, description = '', fields = [], value = {},
  submitLabel = 'Kaydet', wide = false, danger = false, onSubmit } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', `kit-dialog ak-dialog${wide ? ' wide' : ''}`);
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.append(h('h3', 'kit-dialog-title', title));
    if (description) card.append(h('p', 'kit-dialog-text', description));

    const live = fields.filter(Boolean);
    const gridFields = live.filter((field) => field.type !== 'custom');
    const customs = live.filter((field) => field.type === 'custom');
    // TEK, CANLI taslak nesnesi: özel alanlar onu yakalar ve yazar; formGrid
    // değişiklikleri aynı nesneye işlenir ve özel alanların `update(draft)`'ı
    // çağrılır (ör. "kapsam" seçimi değişince hedef seçicisi yeniden çizilir).
    const draft = { ...value };
    const reactive = [];
    // formGrid kendi taslağını `value`nun TAMAMIYLA kurar ve her değişiklikte
    // hepsini geri verir; yalnız KENDİ alanları alınır — yoksa `value` içinde
    // başlangıç değeri verilmiş özel alanlar (seçili sınıflar, öğrenciler…)
    // her tuş vuruşunda başlangıç değerine dönerdi.
    const gridKeys = gridFields.map((field) => field.key);
    const grid = formGrid({ fields: gridFields, value, onChange: (next) => {
      for (const fieldKey of gridKeys) if (fieldKey in next) draft[fieldKey] = next[fieldKey];
      for (const control of reactive) control.update?.(draft);
    } });
    card.append(grid.node);

    const customErrors = new Map();
    for (const field of customs) {
      const wrap = h('div', `kit-field${field.wide !== false ? ' kit-field-wide' : ''}`);
      const label = h('span', 'kit-field-label', field.label || '');
      if (field.required) label.append(h('span', 'req', '*'));
      const set = (raw) => { draft[field.key] = raw; };
      // Üçüncü bağımsız değişken: özel alanın BAŞKA alanları doldurması (ör. şablon seçici notu, eylem maddelerini
      // ve görünürlüğü forma yerleştirir — Akademi README §14 madde 115). `get` güncel taslağı verir.
      const control = field.render(draft, set, {
        setField: (fieldKey, raw) => {
          draft[fieldKey] = raw;
          if (gridKeys.includes(fieldKey)) grid.set(fieldKey, raw);
        },
        get: () => ({ ...draft, ...grid.draft() }),
      });
      if (!(control instanceof Node)) reactive.push(control);
      const error = h('span', 'kit-field-error');
      customErrors.set(field.key, error);
      wrap.append(label, control instanceof Node ? control : control.node);
      if (field.hint) wrap.append(h('span', 'kit-field-hint', field.hint));
      wrap.append(error);
      card.append(wrap);
    }

    const error = h('div', 'kit-dialog-error');
    card.append(error);
    const key = (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).slice(0, 100);

    // Odak tuzağı + başlık bağı + kapanınca odağın açan düğmeye dönüşü (kit, WCAG 2.1.2/2.4.3).
    let release = () => {};
    const close = (result) => {
      document.removeEventListener('keydown', onKey);
      grid.destroy();
      overlay.remove();
      release();
      resolve(result);
    };
    const go = button(submitLabel, { variant: danger ? 'danger' : 'primary', onClick: () => submit() });
    async function submit() {
      grid.showErrors();
      error.textContent = '';
      customErrors.forEach((slot) => { slot.textContent = ''; });
      for (const field of customs) {
        const raw = draft[field.key];
        const emptyValue = raw === undefined || raw === null || raw === ''
          || (Array.isArray(raw) && raw.length === 0);
        const message = field.required && emptyValue ? `${field.label} zorunlu.`
          : field.validate?.(raw, draft);
        if (message) customErrors.get(field.key).textContent = message;
      }
      const invalid = !grid.valid() || [...customErrors.values()].some((slot) => slot.textContent);
      if (invalid) {
        error.textContent = 'İşaretli alanları düzeltin.';
        return;
      }
      go.disabled = true;
      go.textContent = 'Kaydediliyor…';
      try {
        const result = await onSubmit({ ...draft, ...grid.draft(), ...pick(draft, customs) },
          { key: `km-${key}` });
        close(result ?? true);
      } catch (failure) {
        go.disabled = false;
        go.textContent = submitLabel;
        error.textContent = problem(failure);
        if (failure instanceof AkademiError && Array.isArray(failure.details)) {
          for (const item of failure.details) {
            const fieldKey = String(item?.field || '').split('.').pop();
            const slot = customErrors.get(fieldKey);
            if (slot) slot.textContent = item.message || '';
          }
        }
      }
    }
    const onKey = (event) => { if (event.key === 'Escape') close(null); };
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }), go);
    card.append(actions);
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    card.querySelector('input, select, textarea')?.focus();
  });
}

function pick(draft, customs) {
  const out = {};
  for (const field of customs) out[field.key] = draft[field.key];
  return out;
}

/** Gerekçesiz onay. */
export function confirmAction(app, { title, description = '', confirmLabel = 'Evet', danger = false }) {
  return confirmSimple(app.root, { title, description, confirmLabel, danger });
}

/** Gerekçeli onay (gerekçe Akademi denetim kaydına gider). */
export function reasonDialog(app, { title, description = '', confirmLabel = 'Onayla',
  minLength = 3, presets = [], danger = true }) {
  return confirmWithReason(app.root, { title, description, confirmLabel, minLength, presets, danger });
}

/**
 * PIN teyidi → tek kullanımlık belirteç. Yanlış PIN'de pencere tekrar açılır.
 * @returns {Promise<string|null>}
 */
export async function pinStepUp(app, operationId, { title = 'PIN teyidi',
  description = 'Bu işlem geri alınamaz. Devam etmek için kendi PIN\'inizi girin.',
  confirmLabel = 'Onayla' } = {}) {
  let note = description;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const pin = await confirmWithPin(app.root, { title, description: note, confirmLabel }); // eslint-disable-line no-await-in-loop
    if (!pin) return null;
    try {
      return await app.api.stepUp(operationId, pin); // eslint-disable-line no-await-in-loop
    } catch (failure) {
      note = `${problem(failure, 'PIN doğrulanamadı.')} Tekrar deneyin.`;
    }
  }
  app.toast('PIN üç kez doğrulanamadı; işlem yapılmadı.', 'bad');
  return null;
}

/** Sağdan detay çekmecesi (overlay panel köküne eklenir). */
export function openDrawer(app, { title, subtitle = '', actions = [], onClose } = {}) {
  return drawer(app.root, { title, subtitle, actions, onClose });
}

// --------------------------------------------------------------- yetki

/**
 * İşlem düğmesi: kullanıcı yapamıyorsa GEREKÇELİ kapalı düğme (`blockedButton`).
 * Asıl kapı vekildedir; bu yalnız arayüzün dürüst olması içindir.
 */
export function actionButton(app, operationId, label, { variant = '', onClick, title = '' } = {}) {
  if (app.api.featureOff?.(operationId)) {
    // Kurumda kapalı özellik: düğme hiç görünmez (yönetici Ayarlar → Özellikler'den açar).
    const hidden = button(label, { variant, disabled: true });
    hidden.hidden = true;
    return hidden;
  }
  if (!app.api.can(operationId)) {
    return blockedButton(label, 'Bu işlem için yetkiniz yok (yöneticinizden izin isteyin).',
      { variant });
  }
  return button(label, { variant, title, onClick });
}

/**
 * Yazma işlemini çalıştırır: düğme meşgul, başarıda bildirim, hatada sebep.
 *   await runAction(app, () => app.api.post(...), {success: 'Kaydedildi'})
 */
export async function runAction(app, fn, { success = '', button: control = null } = {}) {
  if (control) control.disabled = true;
  try {
    const result = await fn();
    if (success) app.toast(success, 'good');
    return result;
  } catch (failure) {
    // Kurum finansı kilitli: kod penceresi açılır; açılırsa işlem bir kez daha (aynı anahtarla) denenir.
    if (await unlockFinance(failure)) {
      try {
        const result = await fn();
        if (success) app.toast(success, 'good');
        return result;
      } catch (again) {
        app.toast(problem(again), 'bad');
        return undefined;
      } finally {
        if (control) control.disabled = false;
      }
    }
    app.toast(problem(failure), 'bad');
    return undefined;
  } finally {
    if (control) control.disabled = false;
  }
}

// --------------------------------------------------------------- seçiciler

/**
 * Öğrenci seçici: yazdıkça (250 ms) `admin_listStudents?q=` arar.
 * `onPick(student)` — liste yükündeki öğrenci nesnesi.
 */
export function studentPicker(app, { onPick, placeholder = 'Öğrenci ara (ad, okul no)',
  query = {} } = {}) {
  const node = h('div', 'ak-picker');
  const results = h('div', 'ak-picker-list');
  results.hidden = true;
  let token = 0;
  const box = searchBox({ placeholder, onSearch: async (text) => {
    const mine = ++token;
    if (text.length < 2) { results.hidden = true; return; }
    try {
      const data = await app.api.get('/v1/admin/students', { ...query, q: text, limit: 12 });
      if (mine !== token) return;
      results.replaceChildren();
      const items = data?.items || [];
      if (!items.length) results.append(h('div', 'ak-picker-empty', 'Eşleşen öğrenci yok.'));
      for (const student of items) {
        const row = h('button', 'ak-picker-row');
        row.type = 'button';
        row.append(h('b', '', student.displayName || student.fullName || student.id),
          h('span', '', [student.classGroup?.name, gradeText(student.gradeLevel)]
            .filter((part) => part && part !== '—').join(' · ')));
        row.addEventListener('click', () => {
          results.hidden = true;
          onPick?.(student);
        });
        results.append(row);
      }
      results.hidden = false;
    } catch (failure) {
      results.replaceChildren(h('div', 'ak-picker-empty', problem(failure)));
      results.hidden = false;
    }
  } });
  node.append(box.node, results);
  return { node, destroy: box.destroy, focus: () => box.node.focus() };
}

/** Akademi öğrencisi → ortak seçicinin satırı (`{id, name, meta}`). */
export function studentItem(student) {
  const grade = gradeText(student.gradeLevel);
  return {
    id: student.id,
    name: student.displayName || student.fullName || student.name || student.id,
    meta: [student.classGroup?.name, grade && grade !== '—' ? grade : '',
      student.schoolNo ? `no ${student.schoolNo}` : ''].filter(Boolean).join(' · '),
  };
}

/**
 * TOPLU İŞLEMLERİN ÖĞRENCİ SEÇİCİSİ (13.10.2026 — "toplu işlemlerde öğrenci
 * seçilemiyor"). Eski "yaz · tek tek seç" açılır kutusu (`studentPicker`) toplu
 * işte yetmiyordu: en az 2 harf istiyor, sınıf süzgeci / tümünü seç / sayı
 * yoktu ve pencerenin içinde kesiliyordu. Bu seçici KM'nin ortak kitindeki
 * seçicidir (`ui-kit/picker.js`, KM Öğrenci Yönetimi → Toplu işlemler ile AYNI):
 * liste hemen gelir; arama ve sınıf süzgeci SUNUCUDA (`admin_listStudents`,
 * `q` / `classGroupId`, sayfa 50, imleçli "Daha fazla yükle"); görünenleri seç,
 * yalnız seçilenler, seçili sayısı, klavye (↑/↓, Boşluk), yükleniyor / hata /
 * boş. Seçim sayfalar ve aramalar arasında korunur.
 *
 * @param {object} app
 * @param {object} options
 *   classes: [{id, name}] — sınıf süzgeci
 *   value: başlangıç seçimi [{id, displayName}]
 *   onChange(students[{id, displayName}])
 *   placeholder, label
 * @returns {{node, value(): string[], items(): object[], size(): number, destroy()}}
 */
export function studentSelect(app, { classes = [], value = [], onChange, label = 'Öğrenciler',
  placeholder = 'Öğrenci ara (ad, okul no)', query = {} } = {}) {
  const toStudent = (item) => ({ id: item.id, displayName: item.name });
  const picker = createPicker({
    placeholder,
    label,
    groupLabel: 'Sınıf',
    groups: classes.map((item) => ({ value: item.id, label: item.name })),
    limit: 50,
    source: async ({ query: text, group, cursor }) => {
      const data = await app.api.get('/v1/admin/students', { ...query, q: text || undefined,
        classGroupId: group || undefined, cursor: cursor || undefined, limit: 50 });
      return { items: (data?.items || []).map(studentItem), next: data?.nextCursor || null };
    },
    onChange: () => onChange?.(picker.selectedItems().map(toStudent)),
  });
  picker.select((value || []).map((item) => ({ id: item.id, name: item.displayName || item.fullName || item.id })));
  picker.node.classList.add('ak-stu-select');
  return {
    node: picker.node,
    value: () => picker.selection(),
    items: () => picker.selectedItems().map(toStudent),
    size: () => picker.size(),
    focus: () => picker.focus(),
    destroy: () => picker.destroy(),
  };
}

// --------------------------------------------------------------- dışa aktarım

/**
 * Akademi dışa aktarım işi (`202 {id, status, url}`) → bitince dosyayı indir.
 * Ortak iş kaydı `GET /v1/admin/exports/{exportId}` ile yoklanır (artan
 * aralıkla, en çok ~3 dk); `done` olunca kısa ömürlü bağlantı köprüden
 * indirilir. PDF yeni pencerede açılır (yazdırma), XLSX/CSV/ZIP diske iner.
 *
 *   await exportJob(app, () => app.api.post(`/v1/admin/exams/${id}/exports`, body),
 *     { name: 'deneme-sonuclari' });
 *
 * @returns {Promise<object|null>} son iş kaydı ya da hata/vazgeçişte null
 */
export async function exportJob(app, start, { name = 'akademi-disa-aktarim',
  label = 'Dosya hazırlanıyor…' } = {}) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog ak-dialog');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  const bar = progress(['Sıraya alındı', 'Hazırlanıyor', 'İndiriliyor']);
  // "Arka planda bırak" yalnız pencereyi kapatır; yoklama sürer ve dosya
  // hazır olunca yine iner (bildirimle).
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Arka planda bırak', { variant: 'ghost', onClick: () => overlay.remove() }));
  box.append(h('h3', 'kit-dialog-title', label), bar.node, actions);
  overlay.append(box);
  app.root.append(overlay);
  try {
    let job = await start();
    bar.step(0);
    const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
    let delay = 800;
    const deadline = Date.now() + 180_000;
    while (job && ['queued', 'running'].includes(job.status) && Date.now() < deadline) {
      await wait(delay); // eslint-disable-line no-await-in-loop
      delay = Math.min(delay * 1.5, 5000);
      job = await app.api.get(`/v1/admin/exports/${job.id}`); // eslint-disable-line no-await-in-loop
      bar.step(job?.status === 'running' ? 1 : 0);
    }
    if (!job || job.status !== 'done' || !job.url) {
      throw new AkademiError({ code: 'export_failed', message: job?.error
        || (job && ['queued', 'running'].includes(job.status)
          ? 'Dosya hâlâ hazırlanıyor; biraz sonra tekrar deneyin.' : 'Dışa aktarım başarısız.') });
    }
    bar.step(2);
    const ext = job.format || 'xlsx';
    const file = await app.api.fetchFile(job.url, `${name}.${ext}`);
    if (ext === 'pdf') openBlob(file); else saveBlob(file);
    bar.done('İndirildi');
    app.toast('Dosya hazır.', 'good');
    return job;
  } catch (failure) {
    app.toast(problem(failure, 'Dışa aktarım başarısız.'), 'bad');
    return null;
  } finally {
    overlay.remove();
  }
}

/** Para alanı yardımcıları: kuruş ↔ metin. */
export function moneyText(kurus) { return kurus === null || kurus === undefined ? '' : moneyInput(kurus); }

/** Sayı → "12,5" (net gibi ondalıklılar). */
export function net(value) { return value === null || value === undefined ? '—' : num(value, 2); }

/** Boş durum kısayolu. */
export function empty(title, text = '', actions = []) {
  return emptyState({ title, text, actions });
}
