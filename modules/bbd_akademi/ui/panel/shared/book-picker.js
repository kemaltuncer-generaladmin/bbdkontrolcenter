// BBD Akademi — ORTAK KİTAP SEÇİCİSİ ("şak şak seç", Akademi README §14 madde 98).
//
// React `BookPicker` ve Flutter `showBookPicker` ile aynı davranış ve metinler: arama
// (Türkçe harf duyarsız, 300 ms), süzgeçler (sınıf, sınav, ders, yayınevi, seri, yalnız
// satışta — `catalog_getLibraryFacets`), kapaklı ızgara, tek / çoklu seçim, son
// kullanılanlar ve hedefin kitapları ("Bu öğrencinin kitapları", `admin_listTargetBooks`)
// önce. Katalog: hazır kütüphane + kurum kaynakları + bbdstore'dan saatlik eşitlenen
// bütün kitaplar (`catalog_searchLibrary`, imleçli).
//
//   const items = await bookPicker(app, { multiple: false, target: { studentIds: [id] }, subject });
//   // iptal → null; seçim → [ResourceSummary, …]
//
// Bağlanış: görev formu `task-form.js` → `pickResourceField`, ödev içeriği
// `homework-content.js` → kaynak alanı, Kaynaklar ekranı → eşitleme kartı (`storeSyncCard`).

import { button, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { badge } from '../../../ui-kit/layout.js';
import { problem } from '../api.js';
import { fmtDateTime } from '../common.js';
import {
  EMPTY_FILTERS, T, bookMeta, count, gradeLabel, hasFilters, lastSync, libraryQuery, onShelf, pickLabel, readRecent,
  rememberRecent, storeBadge, syncFailed,
} from './book-picker-logic.js';

const PAGE = 30;

function cover(book) {
  if (book.coverUrl) {
    const img = h('img', 'ak-bp-cover');
    img.src = book.coverUrl;
    img.alt = '';
    img.loading = 'lazy';
    img.addEventListener('error', () => img.replaceWith(placeholder(book)));
    return img;
  }
  return placeholder(book);
}

function placeholder(book) {
  const subject = book.subjects?.[0];
  return h('span', 'ak-bp-cover ph', subject?.shortName || (subject?.name || 'Kitap').slice(0, 3).toLocaleUpperCase('tr-TR'));
}

/** Kitap kartı (seçicide ve Kaynaklar listesinde). */
export function bookCard(book, { on = false, note = '', onClick } = {}) {
  const card = h('button', `ak-bp-card${on ? ' on' : ''}`);
  card.type = 'button';
  const text = h('span', 'ak-bp-text');
  const meta = bookMeta(book);
  text.append(h('span', 'ak-bp-title', book.title));
  if (meta) text.append(h('span', 'ak-bp-meta', meta));
  const badges = h('span', 'ak-bp-badges');
  if (note) badges.append(badge(note, 'info'));
  const store = storeBadge(book);
  if (store) badges.append(badge(store, 'warn'));
  if (badges.childNodes.length) text.append(badges);
  card.append(cover(book), text);
  if (on) card.append(h('span', 'ak-bp-tick', '✓'));
  card.setAttribute('aria-pressed', on ? 'true' : 'false');
  card.setAttribute('aria-label', [book.title, meta, note, store].filter(Boolean).join(', '));
  card.addEventListener('click', () => onClick?.(book));
  return card;
}

function select(label, list, value, onChange, labelOf) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label));
  const box = h('select', 'kit-select');
  box.setAttribute('aria-label', label);
  const options = [{ value: '', label: T.any }, ...(list || []).map((item) => ({
    value: item.value, label: `${labelOf ? labelOf(item.value) : item.label} (${item.count})` }))];
  for (const option of options) {
    const node = h('option', '', option.label);
    node.value = option.value;
    box.append(node);
  }
  box.value = value || '';
  box.addEventListener('change', () => onChange(box.value));
  wrap.append(box);
  return wrap;
}

/**
 * Seçici penceresi. `target`: {studentIds, classGroupIds}; `subject`: başlangıç ders süzgeci.
 * @returns {Promise<object[]|null>}
 */
export function bookPicker(app, { multiple = false, target = {}, subject = '', title = T.title, initial = [] } = {}) {
  loadStyles(import.meta.url, 'book-picker.css');
  return new Promise((resolve) => {
    const studentIds = target.studentIds || [];
    const classGroupIds = target.classGroupIds || [];
    const hasTarget = studentIds.length + classGroupIds.length > 0;
    const picked = new Map(initial.map((item) => [item.id, item]));
    let filters = { ...EMPTY_FILTERS, subject: subject || '' };
    let facets = null;
    let targetBooks = [];
    let items = [];
    let cursor = null;
    let token = 0;
    const recent = readRecent();

    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-bp-dialog');
    card.setAttribute('role', 'dialog');
    const head = h('div', 'ak-bp-head');
    head.append(h('h3', 'kit-dialog-title', title));
    const input = h('input', 'kit-input');
    input.type = 'search';
    input.placeholder = T.search;
    input.setAttribute('aria-label', T.search);
    input.setAttribute('autocomplete', 'off');
    const filterRow = h('div', 'ak-bp-filters');
    const tools = h('div', 'ak-bp-tools');
    head.append(input, filterRow, tools);
    const body = h('div', 'ak-bp-body');
    const actions = h('div', 'kit-dialog-actions ak-bp-actions');
    const go = button(pickLabel(picked.size), { variant: 'primary', onClick: () => done([...picked.values()]) });
    actions.append(button('Vazgeç', { onClick: () => done(null) }));
    if (multiple) actions.append(go);
    card.append(head, body, actions);
    overlay.append(card);

    let release = () => {};
    const onKey = (event) => { if (event.key === 'Escape') done(null); };
    function done(result) {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      if (result && result.length) rememberRecent(result);
      resolve(result && result.length ? result : null);
    }

    function choose(book) {
      if (!multiple) { done([book]); return; }
      if (picked.has(book.id)) picked.delete(book.id); else picked.set(book.id, book);
      go.textContent = pickLabel(picked.size);
      go.disabled = picked.size === 0;
      paint();
    }

    function paintFilters() {
      filterRow.replaceChildren();
      const set = (key) => (value) => { filters = { ...filters, [key]: value }; reload(); };
      filterRow.append(select(T.grade, facets?.gradeLevels, filters.gradeLevel, set('gradeLevel'), gradeLabel),
        select(T.exam, facets?.exams, filters.exam, set('exam')),
        select(T.subject, facets?.subjects, filters.subject, set('subject')),
        select(T.publisher, facets?.publishers, filters.publisher, set('publisher')));
      if (facets?.series?.length) filterRow.append(select(T.series, facets.series, filters.series, set('series')));
      tools.replaceChildren();
      const stock = h('label', 'ak-inline');
      const box = h('input', 'kit-check');
      box.type = 'checkbox';
      box.checked = filters.inStock;
      box.addEventListener('change', () => { filters = { ...filters, inStock: box.checked }; reload(); });
      stock.append(box, h('span', '', T.inStock));
      tools.append(stock);
      if (hasFilters(filters)) {
        tools.append(button(T.clear, { variant: 'ghost', onClick: () => { filters = { ...EMPTY_FILTERS }; input.value = ''; paintFilters(); reload(); } }));
      }
      if (facets?.syncedAt) tools.append(h('span', '', `Mağaza kataloğu ${fmtDateTime(facets.syncedAt)} güncellendi`));
    }

    function grid(list, noteOf) {
      const g = h('div', 'ak-bp-grid');
      for (const book of list) g.append(bookCard(book, { on: picked.has(book.id), note: noteOf?.(book) || '', onClick: choose }));
      return g;
    }

    function paint(state = '') {
      body.replaceChildren();
      const top = !filters.q && !filters.gradeLevel && filters.subject === (subject || '');
      const tb = targetBooks.filter((x) => !filters.subject || (x.resource.subjects || []).some((s) => s.id === filters.subject));
      if (top && hasTarget && tb.length) {
        const label = !studentIds.length ? T.targetClass : studentIds.length > 1 ? T.targetMany : T.targetOne;
        body.append(h('div', 'ak-bp-h', label));
        const notes = new Map(tb.map((x) => [x.resource.id, onShelf(x.studentCount, x.targetCount)]));
        body.append(grid(tb.map((x) => x.resource), (book) => notes.get(book.id)));
      }
      if (top && recent.length) {
        body.append(h('div', 'ak-bp-h', T.recent));
        body.append(grid(recent.slice(0, 6)));
      }
      const head2 = h('div', 'ak-bp-h', T.all);
      if (facets && !hasFilters(filters)) head2.append(h('small', '', ` · ${count(facets.totalCount)}`));
      body.append(head2);
      if (state === 'loading' && !items.length) { body.append(h('p', 'ak-muted', 'Yükleniyor…')); return; }
      if (state && state !== 'loading') { body.append(h('p', 'ak-muted', state)); return; }
      if (!items.length) {
        body.append(h('p', 'ak-muted', `${T.emptyTitle}. ${T.emptyMessage}`));
        return;
      }
      body.append(grid(items));
      if (cursor) body.append(button(T.more, { variant: 'ghost', onClick: () => load(true) }));
    }

    async function load(more = false) {
      const mine = ++token;
      if (!more) { items = []; cursor = null; paint('loading'); }
      try {
        const data = await app.api.get('/v1/catalog/library', { ...libraryQuery(filters), limit: PAGE, cursor: more ? cursor : undefined });
        if (mine !== token) return;
        items = more ? [...items, ...(data?.items || [])] : (data?.items || []);
        cursor = data?.nextCursor || null;
        paint();
      } catch (failure) {
        if (mine === token) paint(problem(failure, 'Kitaplar yüklenemedi.'));
      }
    }
    const reload = () => load(false);

    let timer = null;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { filters = { ...filters, q: input.value.trim() }; paintFilters(); reload(); }, 300);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); body.querySelector('.ak-bp-card')?.click(); }
    });

    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) done(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    go.disabled = picked.size === 0;
    paintFilters();
    reload();
    input.focus();

    app.api.getCached('/v1/catalog/library/facets', { scope: 'all' }, { ttlMs: 10 * 60_000 })
      .then((data) => { facets = data; paintFilters(); paint(); }).catch(() => {});
    if (hasTarget && app.can('admin_listTargetBooks')) {
      app.api.get('/v1/admin/catalog/target-books', {
        studentId: studentIds.length ? studentIds.join(',') : undefined,
        classGroupId: classGroupIds.length ? classGroupIds.join(',') : undefined,
      }).then((data) => { targetBooks = data?.items || []; paint(); }).catch(() => {});
    }
  });
}

/** Kaynaklar ekranındaki eşitleme kartı: son eşitleme, kitap sayısı, hata, "Şimdi eşitle". */
export function storeSyncCard(app) {
  loadStyles(import.meta.url, 'book-picker.css');
  const node = h('div', 'ak-bp-sync');
  node.hidden = true;
  if (!app.can('admin_getStoreSync')) return { node, destroy() {} };
  async function refresh() {
    try {
      const data = await app.api.get('/v1/admin/catalog/store-sync');
      node.replaceChildren(h('b', '', T.syncTitle),
        h('span', 'ak-muted', data?.lastSuccessAt ? lastSync(fmtDateTime(data.lastSuccessAt), data.productCount) : T.never));
      if (data?.status === 'failed' && data.error) node.append(badge(syncFailed(data.error), 'warn'));
      if (app.can('admin_runStoreSync')) {
        node.append(button(T.syncNow, { variant: 'ghost', onClick: async (event) => {
          const btn = event?.currentTarget;
          if (btn) btn.disabled = true;
          try {
            await app.api.post('/v1/admin/catalog/store-sync', {});
            app.toast?.(T.syncQueued, 'good');
          } catch (failure) {
            app.toast?.(problem(failure), 'bad');
          } finally {
            if (btn) btn.disabled = false;
          }
        } }));
      }
      node.hidden = false;
    } catch {
      node.hidden = true;
    }
  }
  refresh();
  return { node, refresh, destroy() {} };
}
