// BBD Akademi — Kaynaklar › "BBD Store'dan toplu ata" (Akademi README §14 madde 116).
//
// KULLANICI: "toplu kaynak atama yapabilelim öğrencilere ücretsiz bbdstoreda olan tüm
// kaynaklardan". Merkezi eşitlenen BÜTÜN katalogdan (~1.311 kitap) seçip sınıf / grup /
// öğrenciye ÜCRETSİZ atar: sipariş, borç, ödeme kaydı doğmaz (A12 — para modüllerine dokunmaz).
//
// Üç kısa adım (sihirbaz, "karışık olmasın"): Kitaplar → Kime → Önizle ve ata.
//   1. Katalog: arama (Türkçe harf duyarsız) + ders / sınıf / sınav / yayınevi / tür süzgeçleri,
//      "Yalnız stokta", "Yalnız ücretsiz (N)"; satıra tıklayarak çoklu seçim ya da
//      "Süzülenlerin tümünü seç (N)" (sunucu süzgeci kendisi çözer; tek tek kaldırılanlar düşülür).
//   2. Hedef: ortak "Kime?" seçicisi (sınıf / grup / öğrenci ya da süzgeç) + hedef bitiş + bildirim;
//      seçilen sınıfların ortak seviyesi varsa "Bu seviyenin kitaplarını göster" önerisi.
//   3. Önizleme (`admin_previewBulkResourceAssignment`, yazmasız): kaç kitap × kaç öğrenci, zaten
//      rafında olanlar, mağazadan kalkmış ürün uyarısı, arka plan işi mi. "Ücretsiz ata" →
//      `admin_bulkAssignResources`; büyük atama `202` + `admin_getBulkJob` ile ilerleme.
// Sonuç penceresinden ve "Toplu atamalar" listesinden toplu geri alma (`batchId`).

import { button, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, kpiRow } from '../../../ui-kit/layout.js';
import { confirmAction, errorBox, fmtDateTime, num, searchBox, selectBox } from '../common.js';
import { bulkTargetField } from '../shared/bulk-target.js';
import { loadTargets } from './resources-common.js';
import { followBulkJob, jobProgressText, wizard } from '../shared/wizard.js';
import {
  EMPTY_FILTERS, KIND_LABELS, MAX_RESOURCES, T, catalogQuery, commonGrade, fmt, hasFilters, itemMeta,
  previewLines, resultText, sameFilters, selection, selectionText,
} from './resources-bulk-logic.js';

const PAGE = 50;

/**
 * Toplu atama sihirbazı. `resources`: önceden seçili kaynaklar (Kütüphane'den "Seçilenleri ata").
 * @returns {Promise<object|null>} biten iş (BulkJob) ya da null
 */
export async function bulkAssignWizard(app, { resources = [], onDone, onShowBatch } = {}) {
  loadStyles(import.meta.url, 'resources.css');
  const [classes, groups] = await loadTargets(app);
  const sel = selection({ resources });
  let filters = { ...EMPTY_FILTERS };
  let target = null;
  const options = { targetDate: '', notify: true };
  let preview = null;

  const body = () => ({
    ...sel.body(),
    target: target?.value({ bundle: true }) || {},
    targetDate: options.targetDate || null,
    notify: options.notify,
  });

  // ======================================================= 1. kitaplar
  let catalogStep = null;
  const stepBooks = (ctl) => {
    catalogStep = catalogPicker(app, sel, {
      get filters() { return filters; },
      setFilters(next) { filters = next; },
      changed: () => { ctl.clearError(); ctl.note(selectionText(sel)); },
    });
    return { node: catalogStep.node, enter: () => { catalogStep.paintSelection(); ctl.note(selectionText(sel)); },
      destroy: () => catalogStep.destroy() };
  };

  // ======================================================= 2. kime
  const stepTarget = (ctl) => {
    const node = h('div', 'ak-stack');
    target = bulkTargetField(app, { classes, groups, modes: ['pick', 'filter'], onChange: () => {
      ctl.clearError();
      paintHint();
      ctl.note(target.summary());
    } });
    const hint = h('div', 'ak-tk-hint');
    hint.setAttribute('aria-live', 'polite');
    const paintHint = () => {
      hint.replaceChildren();
      const grade = target.mode === 'pick' ? commonGrade(classes, target.value().classGroupIds) : '';
      if (!grade || filters.gradeLevel === grade) return;
      const label = grade === 'graduate' ? 'Mezun' : `${grade}. sınıf`;
      hint.append(h('span', 'ak-muted', `Seçilen sınıflar ${label}.`), button(`${label} kitaplarını göster`, {
        variant: 'ghost', onClick: () => { filters = { ...filters, gradeLevel: grade }; catalogStep?.reload(); ctl.go(0); },
      }));
    };
    const date = h('input', 'kit-input');
    date.type = 'date';
    date.setAttribute('aria-label', T.targetDate);
    date.addEventListener('change', () => { options.targetDate = date.value; });
    const notifyBox = h('label', 'ak-check');
    const notify = h('input');
    notify.type = 'checkbox';
    notify.checked = true;
    notify.addEventListener('change', () => { options.notify = notify.checked; });
    notifyBox.append(notify, h('span', '', T.notify));
    const dateField = h('label', 'kit-field');
    dateField.append(h('span', 'kit-field-label', T.targetDate), date);
    node.append(h('div', 'ak-muted', 'Sınıf ya da grup seçilirse atama gruba yapılır: sınıfa sonradan katılan öğrenci de '
      + 'görür. Kitap öğrencinin rafına “Kurum atadı” olarak düşer.'), target.node, hint, dateField, notifyBox);
    return { node, enter: () => { paintHint(); ctl.note(target.summary()); }, destroy: () => target.destroy() };
  };

  // ======================================================= 3. önizle
  const stepPreview = (ctl) => {
    const node = h('div', 'ak-stack ak-tk-preview');
    node.setAttribute('aria-live', 'polite');
    const load = async () => {
      preview = null;
      if (!app.can('admin_previewBulkResourceAssignment')) {
        node.replaceChildren(alertBox('Önizleme için yetkiniz yok (yöneticinizden izin isteyin).', 'warn'));
        return;
      }
      node.replaceChildren(h('div', 'ak-muted', 'Önizleme hazırlanıyor…'));
      try {
        preview = await app.api.post('/v1/admin/resource-assignments/bulk/preview', body());
        node.replaceChildren(kpiRow([
          { label: 'Kitap', value: num(preview.resourceCount) },
          { label: 'Öğrenci', value: num(preview.studentCount) },
          { label: 'Rafa yeni düşecek', value: num(preview.newCount) },
          { label: 'Zaten rafında', value: num(preview.alreadyCount) },
        ]));
        const list = h('ul', 'ak-tk-lines');
        for (const line of previewLines(preview)) list.append(h('li', '', line));
        node.append(list);
        if (!preview.newCount) node.append(alertBox('Seçilen kitapların hepsi bu öğrencilerin rafında zaten var.', 'warn'));
        ctl.note(`${fmt(preview.resourceCount)} kitap → ${fmt(preview.studentCount)} öğrenci · ücretsiz`);
      } catch (failure) {
        node.replaceChildren(errorBox(failure, () => load()));
      }
    };
    return { node, enter: () => load() };
  };

  const job = await wizard(app, {
    title: T.title,
    finishLabel: T.finish,
    start: resources.length ? 1 : 0,
    steps: [
      { key: 'books', label: T.steps[0], render: stepBooks, validate: () => {
        if (sel.empty()) return T.noBooks;
        if (sel.count() > MAX_RESOURCES) return `Bir seferde en çok ${fmt(MAX_RESOURCES)} kitap atanabilir; süzgeci daraltın.`;
        return null;
      } },
      { key: 'target', label: T.steps[1], render: stepTarget, validate: () => target?.problem() || null },
      { key: 'preview', label: T.steps[2], render: stepPreview, validate: () => (preview ? null
        : 'Önizleme yüklenmeden atama yapılamaz.') },
    ],
    onFinish: async ({ key, progress }) => {
      const started = await app.api.post('/v1/admin/resource-assignments/bulk', body(), { key });
      const done = await followBulkJob(app, started, { onProgress: (current) => {
        if (current && ['queued', 'running'].includes(current.status)) progress(jobProgressText(current, 'atama'));
      } });
      if (done && ['queued', 'running'].includes(done.status)) {
        app.toast('Atama arka planda sürüyor; birkaç dakika sonra “Toplu atamalar”dan bakın.', 'warn');
      }
      return done || started;
    },
  });
  if (!job) return null;
  onDone?.(job);
  if (job.resources) await resultDialog(app, job.resources, { onShowBatch, onUndone: () => onDone?.(job) });
  return job;
}

// ------------------------------------------------------------------ katalog adımı

/** Katalog tarayıcısı: süzgeçler + sonuç listesi (çoklu seçim) + "tümünü seç". */
function catalogPicker(app, sel, state) {
  const node = h('div', 'ak-stack ak-tk');
  const info = h('div', 'ak-muted ak-tk-info');
  const filterRow = h('div', 'ak-tk-filters');
  const head = h('div', 'ak-tk-head');
  const list = h('div', 'ak-tk-list');
  list.setAttribute('role', 'group');
  list.setAttribute('aria-label', 'Katalog sonuçları');
  const more = button(T.more, { variant: 'ghost', onClick: () => load(page + 1) });
  more.hidden = true;
  const selected = h('div', 'ak-tk-selected');
  selected.setAttribute('aria-live', 'polite');
  let facets = null;
  let items = [];
  let total = 0;
  let page = 1;
  let token = 0;

  const search = searchBox({ placeholder: T.search, label: T.search, delay: 300, onSearch: (text) => {
    state.setFilters({ ...state.filters, q: text });
    load(1);
  } });
  node.append(info, search.node, filterRow, head, list, more, selected);

  const select = (label, values, key, labelOf) => {
    const box = selectBox([{ value: '', label: `${label}: ${T.any}` },
      ...(values || []).map((item) => ({ value: item.value, label: `${labelOf ? labelOf(item) : item.label} (${fmt(item.count)})` }))],
    state.filters[key] || '', (value) => { state.setFilters({ ...state.filters, [key]: value }); load(1); }, { label });
    return box.node;
  };
  const chip = (label, key) => {
    const node = h('button', `kit-chip${state.filters[key] ? ' on' : ''}`, label);
    node.type = 'button';
    node.setAttribute('aria-pressed', state.filters[key] ? 'true' : 'false');
    node.addEventListener('click', () => { state.setFilters({ ...state.filters, [key]: !state.filters[key] }); load(1); });
    return node;
  };

  function paintFilters() {
    filterRow.replaceChildren();
    if (!facets) return;
    filterRow.append(
      select(T.subject, facets.subjects, 'subject'),
      select(T.grade, facets.gradeLevels, 'gradeLevel'),
      select(T.exam, facets.exams, 'exam'),
      select(T.publisher, facets.publishers, 'publisher'),
      select(T.kind, facets.kinds, 'kind', (item) => KIND_LABELS[item.value] || item.label),
      chip(T.inStock, 'inStock'),
      chip(`${T.free} (${fmt(facets.freeCount)})`, 'free'),
    );
    if (hasFilters(state.filters)) {
      filterRow.append(button(T.clear, { variant: 'ghost', onClick: () => {
        state.setFilters({ ...EMPTY_FILTERS });
        search.node.value = '';
        load(1);
      } }));
    }
  }

  function paintHead() {
    head.replaceChildren(h('span', 'ak-tk-total', `${fmt(total)} kitap`));
    if (!total) return;
    const allOn = Boolean(sel.all) && sameFilters(sel.all.filters, state.filters);
    head.append(button(allOn ? T.unselectAll : T.selectAll(total), { variant: allOn ? 'ghost' : '', onClick: () => {
      if (allOn) sel.clearAll(); else sel.selectAll(state.filters, total);
      paintRows();
      paintHead();
      paintSelection();
      state.changed();
    } }));
  }

  function row(item) {
    const on = sel.isOn(item, state.filters);
    const node = h('label', `ak-tk-row${on ? ' on' : ''}`);
    const box = h('input');
    box.type = 'checkbox';
    box.checked = on;
    box.addEventListener('change', () => {
      sel.toggle(item, state.filters);
      node.classList.toggle('on', sel.isOn(item, state.filters));
      paintSelection();
      state.changed();
    });
    const text = h('span', 'ak-tk-text');
    text.append(h('span', 'ak-tk-title', item.title), h('span', 'ak-tk-meta', itemMeta(item) || '—'));
    const badges = h('span', 'ak-tk-badges');
    if (item.free) badges.append(badge(T.freeBadge, 'good'));
    if (item.status === 'removed') badges.append(badge(T.removed, 'bad'));
    else if (!item.inStock) badges.append(badge(T.outOfStock, 'warn'));
    node.append(box, text, badges);
    return node;
  }

  function paintRows() {
    list.replaceChildren();
    if (!items.length) {
      list.append(h('div', 'ak-empty-mini', `${T.emptyTitle}. ${T.emptyText}`));
      return;
    }
    for (const item of items) list.append(row(item));
  }

  function paintSelection() {
    selected.replaceChildren(h('span', '', selectionText(sel)));
    if (!sel.empty()) {
      selected.append(button(T.clearSelection, { variant: 'ghost', onClick: () => {
        sel.clear();
        paintRows();
        paintHead();
        paintSelection();
        state.changed();
      } }));
    }
    for (const item of sel.resources.values()) {
      const chipNode = h('button', 'kit-chip on', `${item.title} ✕`);
      chipNode.type = 'button';
      chipNode.title = 'Seçimden çıkar';
      chipNode.addEventListener('click', () => { sel.removeResource(item.id); paintSelection(); state.changed(); });
      selected.append(chipNode);
    }
  }

  async function load(nextPage) {
    const mine = ++token;
    if (nextPage === 1) {
      list.replaceChildren(h('div', 'ak-muted', 'Yükleniyor…'));
      paintFilters();
    }
    more.disabled = true;
    try {
      const data = await app.api.get('/v1/admin/store-catalog', catalogQuery(state.filters, { page: nextPage, limit: PAGE }));
      if (mine !== token) return;
      page = nextPage;
      items = nextPage === 1 ? (data?.items || []) : items.concat(data?.items || []);
      total = data?.totalCount || 0;
      more.hidden = !data?.hasMore;
      paintHead();
      paintRows();
    } catch (failure) {
      if (mine !== token) return;
      list.replaceChildren(errorBox(failure, () => load(nextPage)));
    } finally {
      more.disabled = false;
    }
  }

  async function loadFacets() {
    try {
      facets = await app.api.get('/v1/admin/store-catalog/facets');
      info.textContent = facets?.syncedAt
        ? `BBD Store kataloğu: ${fmt(facets.totalCount)} kitap · ${fmt(facets.freeCount)} ücretsiz · son eşitleme `
          + `${fmtDateTime(facets.syncedAt)}. ${T.freeNote}`
        : T.neverSynced;
      paintFilters();
    } catch (failure) {
      info.replaceChildren(errorBox(failure, () => loadFacets()));
    }
  }

  loadFacets();
  load(1);
  paintSelection();
  return {
    node,
    paintSelection,
    reload: () => load(1),
    destroy: () => { token += 1; search.destroy(); },
  };
}

// ------------------------------------------------------------------ sonuç ve geri alma

/** Sonuç penceresi: özet + "Atamaları gör" + "Geri al". */
function resultDialog(app, result, { onShowBatch, onUndone } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    const text = h('p', 'kit-dialog-text', resultText(result));
    text.setAttribute('role', 'status');
    const actions = h('div', 'kit-dialog-actions');
    let release = () => {};
    const close = () => { document.removeEventListener('keydown', onKey); overlay.remove(); release(); resolve(); };
    const onKey = (event) => { if (event.key === 'Escape') close(); };
    if (result.createdCount) {
      actions.append(button(T.undo, { variant: 'ghost', onClick: async () => {
        const removed = await undoBatch(app, result.batchId);
        if (removed !== null) { onUndone?.(); close(); }
      } }));
      if (onShowBatch) actions.append(button(T.showAssignments, { onClick: () => { close(); onShowBatch(result.batchId); } }));
    }
    actions.append(button('Tamam', { variant: 'primary', onClick: () => close() }));
    card.append(h('h3', 'kit-dialog-title', 'Toplu atama tamamlandı'), text,
      h('p', 'ak-muted', T.freeNote), actions);
    overlay.append(card);
    app.root.append(overlay);
    document.addEventListener('keydown', onKey);
    release = dialogA11y(card);
  });
}

/**
 * Toplu atamayı geri alır (önce sayar, onay ister). İlerlemesi olan öğrencide kitap rafında kalır.
 * @returns {Promise<number|null>} kaldırılan atama sayısı; vazgeçildiyse null
 */
export async function undoBatch(app, batchId) {
  try {
    const counted = await app.api.post('/v1/admin/resource-assignments/bulk-delete', { batchId, dryRun: true });
    if (!counted?.removedCount) { app.toast('Bu toplu atamanın kalkacak ataması kalmadı.', 'warn'); return null; }
    const ok = await confirmAction(app, {
      title: 'Toplu atamayı geri al',
      description: `${fmt(counted.removedCount)} atama kaldırılacak. Kitaba çalışmaya başlamış öğrencinin çözümleri `
        + 'korunur ve kitap onun rafında kalır.',
      confirmLabel: T.undo,
      danger: true,
    });
    if (!ok) return null;
    const done = await app.api.post('/v1/admin/resource-assignments/bulk-delete', { batchId });
    app.toast(`${fmt(done?.removedCount)} atama geri alındı.`, 'good');
    return done?.removedCount ?? 0;
  } catch (failure) {
    app.toast(failure?.message || 'Geri alınamadı.', 'bad');
    return null;
  }
}

/** "Toplu atamalar" penceresi: son toplu atamalar, durum, atamaları gör, geri al. */
export function batchesDialog(app, { onShowBatch, onChanged } = {}) {
  loadStyles(import.meta.url, 'resources.css');
  const overlay = h('div', 'kit-overlay');
  const card = h('div', 'kit-dialog ak-dialog wide');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  const body = h('div', 'ak-stack ak-tk-batches');
  const actions = h('div', 'kit-dialog-actions');
  let release = () => {};
  const close = () => { document.removeEventListener('keydown', onKey); overlay.remove(); release(); };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  actions.append(button('Kapat', { onClick: () => close() }));
  card.append(h('h3', 'kit-dialog-title', T.batches),
    h('p', 'kit-dialog-text', "BBD Store'dan yapılan son toplu atamalar. Geri alınca o atamanın bütün kayıtları kalkar."),
    body, actions);
  overlay.append(card);
  app.root.append(overlay);
  document.addEventListener('keydown', onKey);
  release = dialogA11y(card);

  const STATUS = { queued: 'Sırada', running: 'Sürüyor', succeeded: 'Tamamlandı', failed: 'Başarısız' };
  async function load() {
    body.replaceChildren(h('div', 'ak-muted', 'Yükleniyor…'));
    try {
      const data = await app.api.get('/v1/admin/resource-assignments/batches', { limit: 20 });
      const items = data?.items || [];
      body.replaceChildren();
      if (!items.length) { body.append(h('div', 'ak-muted', 'Henüz toplu atama yapılmadı.')); return; }
      for (const item of items) {
        const line = h('div', 'ak-tk-batch');
        const text = h('div', 'ak-tk-text');
        text.append(h('span', 'ak-tk-title', `${fmt(item.resourceCount)} kitap · ${fmt(item.studentCount)} öğrenci`
          + ` · ${fmt(item.createdCount)} atama`), h('span', 'ak-tk-meta', `${fmtDateTime(item.assignedAt)} · ${item.assignedBy || '—'}`));
        const state = item.active ? badge(STATUS[item.status] || item.status, item.status === 'failed' ? 'bad' : 'good')
          : badge(T.undone, '');
        const tools = h('div', 'ak-row-actions');
        if (item.active && onShowBatch) {
          tools.append(button(T.showAssignments, { variant: 'ghost', onClick: () => { close(); onShowBatch(item.batchId); } }));
        }
        if (item.active && app.can('admin_bulkDeleteResourceAssignments')) {
          tools.append(button(T.undo, { variant: 'ghost', onClick: async () => {
            const removed = await undoBatch(app, item.batchId);
            if (removed !== null) { onChanged?.(); load(); }
          } }));
        }
        line.append(text, state, tools);
        body.append(line);
      }
    } catch (failure) {
      body.replaceChildren(errorBox(failure, () => load()));
    }
  }
  load();
  return { close };
}
