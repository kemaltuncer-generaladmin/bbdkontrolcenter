// BBD Akademi — Kaynaklar › Atamalar ve İlerleme (F-C5, F-C6).
//
// Atama: kaynak(lar) ya da paket → sınıf / çalışma grubu / öğrenci (aynı
// istekte birlikte). Atama kaldırılırken öntanımlı olarak ilerlemesi olan
// öğrencinin rafında kalır (`keepOnShelf`), çözümler kaybolmaz.
//
// İlerleme: öğrenci × kaynak tablosu, imleçli (satırlar sayfa sayfa gelir,
// sütunlar sayfalarla birleşir). Hücre tamamlanma yüzdesini ve bitirilen /
// toplam bölümü YAZIYLA taşır; renk yalnız eşlik eder. "Geride" (atamadaki
// hedef tarihe göre) hem çizgiyle hem "geride" yazısıyla işaretlenir. Hücre
// başına ek istek atılmaz: ayrıntı hücrenin ipucundadır.

import { button, h } from '../../ui-kit/kit.js';
import { skeletonRows } from '../../ui-kit/layout.js';
import {
  actionButton, empty, errorBox, fmtDate, fmtDateTime, formDialog, num, pagedList, selectBox,
  studentPicker, toolbar,
} from './common.js';
import { bulkTargetField } from './shared/bulk-target.js';
import { T as BULK, resultText } from './resources-bulk-logic.js';
import { followBulkJob } from './shared/wizard.js';

/** `admin_createResourceAssignments` tek istekte en çok kaynak; fazlası toplu atama ucuna gider. */
const SINGLE_MAX = 50;
import {
  KINDS, TARGET_KINDS, clean, loadPackages, loadTargets, resourceCell, resourceListField, resourcePicker,
  stackCell, targetsField,
} from './resources-common.js';

// ------------------------------------------------------------------ atamalar

export function assignmentsTab(app, ctx) {
  const node = h('div', 'ak-stack');
  const filters = { resourceId: '', packageId: '', classGroupId: '', studentGroupId: '', studentId: '', batchId: '' };
  const reloadSoon = () => list.reload();

  const resourceFilter = pickedFilter('Kaynak', (done) => resourcePicker(app, { placeholder: 'Kaynağa göre süz',
    onPick: (item) => done(item.id, item.title) }), (id) => { filters.resourceId = id; reloadSoon(); });
  const studentFilter = pickedFilter('Öğrenci', (done) => studentPicker(app, { placeholder: 'Öğrenciye göre süz',
    onPick: (student) => done(student.id, student.displayName || student.id) }),
  (id) => { filters.studentId = id; reloadSoon(); });
  const packageSelect = selectBox([{ value: '', label: 'Tüm paketler' }], '', (value) => {
    filters.packageId = value; reloadSoon();
  }, { label: 'Paket' });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reloadSoon();
  }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm gruplar' }], '', (value) => {
    filters.studentGroupId = value; reloadSoon();
  }, { label: 'Çalışma grubu' });
  fillSelects(app, { classSelect, groupSelect });
  const add = actionButton(app, 'admin_createResourceAssignments', 'Yeni atama', { variant: 'primary',
    onClick: () => ctx.assign({}) });
  // README §14 madde 116: BBD Store kataloğundan ücretsiz toplu atama + geçmiş / geri alma.
  const bulkAssign = actionButton(app, 'admin_bulkAssignResources', BULK.open, { onClick: () => ctx.bulkAssign?.() });
  const batches = actionButton(app, 'admin_listResourceAssignmentBatches', BULK.batches, {
    onClick: () => ctx.showBatches?.() });
  // "Toplu atama" süzgeci yalnız derin bağlantıyla gelir (sonuç penceresi / Toplu atamalar); çipten kaldırılır.
  const batchSlot = h('span');
  const paintBatch = () => {
    batchSlot.replaceChildren();
    if (!filters.batchId) return;
    const chip = h('button', 'kit-chip on', 'Toplu atama ✕');
    chip.type = 'button';
    chip.title = 'Toplu atama süzgecini kaldır';
    chip.addEventListener('click', () => { filters.batchId = ''; paintBatch(); reloadSoon(); });
    batchSlot.append(chip);
  };
  // Toplu kaldırma (0.5.4): seçili atamalar ya da kaynak(lar) × hedef; tek işlem, önce sayılır.
  const removeSelected = actionButton(app, 'admin_bulkDeleteResourceAssignments', 'Seçilenleri kaldır', {
    onClick: () => bulkRemove({ assignmentIds: list.table.selection() }) });
  removeSelected.hidden = true;
  const bulk = actionButton(app, 'admin_bulkDeleteResourceAssignments', 'Toplu kaldır', {
    onClick: () => bulkRemove({}) });
  node.append(toolbar(resourceFilter.node, packageSelect.node, classSelect.node, groupSelect.node,
    studentFilter.node, batchSlot, h('span', 'kit-spacer'), removeSelected, bulk, batches, bulkAssign, add));

  let list;
  list = pagedList({
    app,
    path: '/v1/admin/resource-assignments',
    query: () => clean(filters),
    selectable: true,
    onSelect: (keys) => paintSelected(keys),
    onLoaded: () => paintSelected(list.table.selection()),
    columns: [
      { key: 'resource', label: 'Kaynak', width: 'minmax(0, 2.2fr)', cell: (row) => {
        const cell = resourceCell(row.resource, (resource) => ctx.openResource(resource.id));
        if (row.package?.name) cell.append(h('small', '', `Paket: ${row.package.name}`));
        return cell;
      } },
      { key: 'target', label: 'Hedef', width: 'minmax(0, 1.4fr)', cell: (row) => stackCell(row.target?.label || '—',
        [TARGET_KINDS[row.target?.kind] || row.target?.kind || '', row.batchId ? 'Toplu atama' : '']
          .filter(Boolean).join(' · ')) },
      { key: 'students', label: 'Öğrenci', width: '80px', align: 'num', cell: (row) => num(row.studentCount) },
      { key: 'targetDate', label: 'Hedef bitiş', width: '100px', cell: (row) => fmtDate(row.targetDate) },
      { key: 'assigned', label: 'Atama', width: '150px', cell: (row) => stackCell(fmtDateTime(row.assignedAt),
        row.assignedBy || '') },
      { key: 'actions', label: '', width: '100px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_deleteResourceAssignment', 'Kaldır', { variant: 'ghost',
          onClick: () => removeAssignment(row) }));
        return actions;
      } },
    ],
    empty: { title: 'Bu süzgece uyan atama yok', text: '“Yeni atama” ile kaynak ya da paket atayın.' },
  });
  node.append(list.node);

  async function removeAssignment(row) {
    const what = row.resource?.title || 'Kaynak';
    const done = await formDialog(app, {
      title: 'Atamayı kaldır',
      description: `“${what}” → ${row.target?.label || ''} (${num(row.studentCount)} öğrenci). `
        + 'Kaldırılınca kaynak bu öğrencilerin atanmış listesinden çıkar.',
      value: { keepOnShelf: true },
      fields: [{ key: 'keepOnShelf', label: 'İlerlemesi olan öğrencinin rafında kalsın (çözümler korunur)',
        type: 'checkbox', wide: true }],
      submitLabel: 'Atamayı kaldır',
      danger: true,
      onSubmit: (draft) => app.api.del(`/v1/admin/resource-assignments/${row.id}`,
        { query: { keepOnShelf: Boolean(draft.keepOnShelf) } }),
    });
    if (done === null) return;
    app.toast('Atama kaldırıldı.', 'good');
    list.remove((item) => item.id === row.id);
    app.api.invalidate('/v1/admin/resource-packages');
    ctx.changed('assignment-removed');
  }

  function paintSelected(keys) {
    removeSelected.hidden = !keys.length;
    removeSelected.textContent = keys.length ? `Seçilenleri kaldır (${num(keys.length)})` : 'Seçilenleri kaldır';
  }

  async function bulkRemove(scope) {
    const done = await bulkRemoveDialog(app, scope);
    if (!done) return;
    list.table.clearSelection();
    paintSelected([]);
    list.reload();
    app.api.invalidate('/v1/admin/resource-packages');
    ctx.changed('assignment-removed');
  }

  async function refreshPackages() {
    const packages = await loadPackages(app).catch(() => []);
    packageSelect.options([{ value: '', label: 'Tüm paketler' },
      ...packages.map((item) => ({ value: item.id, label: item.name }))]);
  }

  return {
    node,
    reload() { refreshPackages(); return list.reload(); },
    /** Başka yerden süzgeçli açılış: {resourceId, resourceTitle} | {packageId} | {studentId, studentName}. */
    filter(next = {}) {
      Object.assign(filters, { resourceId: '', packageId: '', classGroupId: '', studentGroupId: '', studentId: '',
        batchId: '' });
      filters.batchId = next.batchId || '';
      paintBatch();
      filters.resourceId = next.resourceId || '';
      filters.packageId = next.packageId || '';
      filters.classGroupId = next.classGroupId || '';
      filters.studentId = next.studentId || '';
      resourceFilter.set(filters.resourceId, next.resourceTitle);
      studentFilter.set(filters.studentId, next.studentName);
      classSelect.set(filters.classGroupId);
      groupSelect.set('');
      refreshPackages().then(() => packageSelect.set(filters.packageId));
      packageSelect.set(filters.packageId);
      return list.reload();
    },
    destroy() { list.destroy(); resourceFilter.destroy(); studentFilter.destroy(); },
  };
}

/**
 * Toplu kaynak ataması kaldırma (`admin_bulkDeleteResourceAssignments`, tek
 * işlem). `scope.assignmentIds` verilirse yalnız o atamalar; yoksa seçilen
 * kaynak(lar) × hedef (sınıf / grup / öğrenci ya da süzgeç). Öğrencinin kendi
 * rafına eklediği kaynaklara dokunulmaz. "Önce say" `dryRun` ile sayar.
 * @returns {Promise<object|null>}
 */
async function bulkRemoveDialog(app, scope = {}) {
  const byIds = Array.isArray(scope.assignmentIds) && scope.assignmentIds.length > 0;
  const [classes, groups] = byIds ? [[], []] : await loadTargets(app);
  let resources = [];
  let target = null;
  let picker = null;
  const previewSlot = h('div', 'ak-stack');
  previewSlot.setAttribute('aria-live', 'polite');
  previewSlot.append(h('div', 'ak-muted', '“Önce say” hiçbir şey silmeden kaç atamanın kalkacağını gösterir.'));
  const body = (dryRun) => (byIds ? { assignmentIds: scope.assignmentIds.slice(0, 500), dryRun }
    : { resourceIds: resources.map((item) => item.id), target: target.value({ bundle: true }), dryRun });
  const missing = () => {
    if (byIds) return null;
    if (!resources.length) return 'En az bir kaynak seçin.';
    return target?.problem() || null;
  };
  const result = await formDialog(app, {
    title: byIds ? `${num(scope.assignmentIds.length)} atamayı kaldır` : 'Toplu kaynak ataması kaldır',
    description: 'Kaynak, seçilen öğrencilerin atanmış listesinden çıkar. Öğrencinin kendi rafına eklediği '
      + 'kaynaklara dokunulmaz.',
    wide: true,
    danger: true,
    fields: [
      byIds ? null : { key: 'resources', label: 'Kaldırılacak kaynaklar', type: 'custom', render: () => {
        picker = resourceListField(app, { onChange: (list) => { resources = list; } });
        return picker.node;
      }, validate: () => (resources.length ? null : 'En az bir kaynak seçin.') },
      byIds ? null : { key: 'target', label: 'Kimden', type: 'custom', render: () => {
        target = bulkTargetField(app, { classes, groups, modes: ['pick', 'filter'], onChange: () => {} });
        return target.node;
      }, validate: () => target?.problem() || null },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: () => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önce say', { onClick: async () => {
          const reason = missing();
          if (reason) { previewSlot.replaceChildren(h('div', 'ak-muted', reason)); return; }
          go.disabled = true;
          try {
            const counts = await app.api.post('/v1/admin/resource-assignments/bulk-delete', body(true));
            previewSlot.replaceChildren(h('div', '', `Önizleme: ${num(counts.removedCount)} atama kalkacak `
              + '(henüz bir şey silinmedi).'));
          } catch (failure) {
            previewSlot.replaceChildren(errorBox(failure));
          } finally {
            go.disabled = false;
          }
        } });
        wrap.append(go, previewSlot);
        return wrap;
      } },
    ].filter(Boolean),
    submitLabel: 'Kaldır',
    onSubmit: (_draft, { key }) => {
      const reason = missing();
      if (reason) throw new Error(reason);
      return app.api.post('/v1/admin/resource-assignments/bulk-delete', body(false), { key });
    },
  });
  picker?.destroy?.();
  target?.destroy();
  if (!result || result === true) return null;
  app.toast(`${num(result.removedCount)} kaynak ataması kaldırıldı.`, 'good');
  return result;
}

/** Sınıf ve grup açılırlarını önbellekli listelerden doldurur. */
function fillSelects(app, { classSelect, groupSelect }) {
  loadTargets(app).then(([classes, groups]) => {
    classSelect?.options([{ value: '', label: 'Tüm sınıflar' }, ...classes.map((item) => ({ value: item.id, label: item.name }))]);
    groupSelect?.options([{ value: '', label: 'Tüm gruplar' }, ...groups.map((item) => ({ value: item.id, label: item.name }))]);
  });
}

/**
 * Seçiciyle süzgeç: önce arama kutusu, seçilince "Kaynak: X ✕" çipi.
 * `makePicker(done)` → {node, destroy}; `done(id, label)` seçimi bildirir.
 */
function pickedFilter(prefix, makePicker, onChange) {
  const node = h('div', 'ak-res-filter');
  let picker = null;
  const showPicker = () => {
    picker?.destroy?.();
    picker = makePicker((id, text) => { showChip(id, text); onChange(id); });
    node.replaceChildren(picker.node);
  };
  const showChip = (id, text) => {
    picker?.destroy?.();
    picker = null;
    const chip = h('button', 'kit-chip on', `${prefix}: ${text || 'seçili'} ✕`);
    chip.type = 'button';
    chip.title = 'Süzgeci kaldır';
    chip.addEventListener('click', () => { showPicker(); onChange(''); });
    node.replaceChildren(chip);
  };
  showPicker();
  return {
    node,
    set(id, text) { if (id) showChip(id, text); else showPicker(); },
    destroy() { picker?.destroy?.(); },
  };
}

/**
 * Kaynak / paket ata. `resources` ön seçili kaynaklar, `packageId` ön seçili paket.
 * @returns {Promise<object|null>}
 */
export async function assignDialog(app, { resources = [], packageId = null, onDone } = {}) {
  const [[classes, groups], packages] = await Promise.all([loadTargets(app), loadPackages(app).catch(() => [])]);
  let chosen = [...resources];
  let selectedPackage = packageId || '';
  let targets = { classGroupIds: [], studentGroupIds: [], studentIds: [] };

  const subjectField = (draft, set) => {
    const wrap = h('div');
    const resourceBox = resourceListField(app, { initial: chosen, onChange: (list) => {
      chosen = list;
      set(list.length ? list.map((item) => item.id) : null);
    } });
    const packageBox = h('select', 'kit-select');
    packageBox.setAttribute('aria-label', 'Kaynak paketi');
    for (const option of [{ value: '', label: packages.length ? 'Paket seçin' : 'Tanımlı paket yok' },
      ...packages.map((item) => ({ value: item.id, label: `${item.name} (${num((item.resources || []).length)} kaynak)` }))]) {
      const node = h('option', '', option.label);
      node.value = option.value;
      packageBox.append(node);
    }
    packageBox.value = selectedPackage;
    packageBox.addEventListener('change', () => { selectedPackage = packageBox.value; set(selectedPackage || null); });
    wrap.append(resourceBox.node, packageBox);
    const update = () => {
      const pkg = draft.what === 'package';
      resourceBox.node.hidden = pkg;
      packageBox.hidden = !pkg;
    };
    update();
    return { node: wrap, update };
  };

  const result = await formDialog(app, {
    title: 'Kaynak ata',
    description: 'Atanan kaynak öğrencinin rafına düşer; ilerlemesi İlerleme sekmesinde izlenir. '
      + 'Sınıf, grup ve öğrenci aynı atamada birlikte seçilebilir.',
    wide: true,
    value: { what: packageId ? 'package' : 'resources', targetDate: '' },
    fields: [
      { key: 'what', label: 'Ne atanacak', type: 'select', options: [
        { value: 'resources', label: 'Kaynak(lar)' }, { value: 'package', label: 'Kaynak paketi' }] },
      { key: 'targetDate', label: 'Hedef bitiş günü (isteğe bağlı)', type: 'date' },
      { key: 'subject', label: 'Kaynak / paket', type: 'custom', render: subjectField,
        validate: (_raw, draft) => {
          if (draft.what === 'package') return selectedPackage ? null : 'Bir paket seçin.';
          return chosen.length ? null : 'En az bir kaynak seçin.';
        } },
      { key: 'targets', label: 'Kime', type: 'custom', render: (draft, set) => targetsField(app, { classes, groups,
        onChange: (value) => { targets = value; set(value); } }).node,
      hint: '“Geride” hesabı hedef bitiş gününe göre yapılır.',
      validate: () => (targets.classGroupIds.length + targets.studentGroupIds.length + targets.studentIds.length
        ? null : 'En az bir sınıf, çalışma grubu ya da öğrenci seçin.') },
    ],
    submitLabel: 'Ata',
    onSubmit: async (draft, { key }) => {
      if (draft.what !== 'package' && chosen.length > SINGLE_MAX) {
        // 50'den çok kaynak: toplu atama ucu (README §14 madde 116; en çok 1.500, büyüğü arka planda).
        const job = await app.api.post('/v1/admin/resource-assignments/bulk', {
          resourceIds: chosen.map((item) => item.id),
          target: { classGroupIds: targets.classGroupIds, studentGroupIds: targets.studentGroupIds,
            studentIds: targets.studentIds },
          targetDate: draft.targetDate || null,
        }, { key });
        return { bulk: await followBulkJob(app, job) };
      }
      return app.api.post('/v1/admin/resource-assignments', {
        resourceIds: draft.what === 'package' ? [] : chosen.map((item) => item.id),
        packageId: draft.what === 'package' ? selectedPackage : null,
        classGroupIds: targets.classGroupIds,
        studentGroupIds: targets.studentGroupIds,
        studentIds: targets.studentIds,
        targetDate: draft.targetDate || null,
      }, { key });
    },
  });
  if (!result || result === true) return null;
  if (result.bulk) {
    app.toast(resultText(result.bulk.resources), 'good');
    onDone?.(result);
    return result;
  }
  const items = result.items || [];
  const students = items.reduce((acc, item) => acc + (item.studentCount || 0), 0);
  app.toast(`${num(items.length)} atama yapıldı${students ? ` (${num(students)} öğrenci)` : ''}.`, 'good');
  onDone?.(result);
  return result;
}

// ------------------------------------------------------------------ ilerleme

const LEGEND = [['h0', '%0'], ['h1', '%1–24'], ['h2', '%25–49'], ['h3', '%50–74'], ['h4', '%75–99'], ['h5', '%100']];

export function progressTab(app, ctx) {
  const node = h('div', 'ak-stack');
  const filters = { classGroupId: '', studentGroupId: '', packageId: '', resourceId: '', behindOnly: false };
  let columns = new Map();        // kaynak kimliği → özet (sayfalarla birleşir)
  let rows = [];
  let cursor = null;
  let done = true;
  let busy = false;
  let token = 0;
  let stale = false;
  let grid = null;
  let paintedColumns = 0;
  let paintedRows = 0;

  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reload();
  }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm gruplar' }], '', (value) => {
    filters.studentGroupId = value; reload();
  }, { label: 'Çalışma grubu' });
  const packageSelect = selectBox([{ value: '', label: 'Tüm paketler' }], '', (value) => {
    filters.packageId = value; reload();
  }, { label: 'Paket' });
  fillSelects(app, { classSelect, groupSelect });
  const resourceFilter = pickedFilter('Kaynak', (pick) => resourcePicker(app, { placeholder: 'Kaynağa göre süz',
    onPick: (item) => pick(item.id, item.title) }), (id) => { filters.resourceId = id; reload(); });
  const behind = h('button', 'kit-chip', 'Yalnız geride kalanlar');
  behind.type = 'button';
  behind.setAttribute('aria-pressed', 'false');
  behind.addEventListener('click', () => {
    filters.behindOnly = !filters.behindOnly;
    paintBehind();
    reload();
  });
  const paintBehind = () => {
    behind.classList.toggle('on', filters.behindOnly);
    behind.setAttribute('aria-pressed', filters.behindOnly ? 'true' : 'false');
  };
  node.append(toolbar(classSelect.node, groupSelect.node, packageSelect.node, resourceFilter.node, behind));

  const slot = h('div', 'ak-res-matrix-wrap');
  const status = h('span', 'ak-list-count');
  const more = button('Daha fazla', { variant: 'ghost', onClick: () => loadMore() });
  const foot = h('div', 'ak-list-foot');
  foot.append(status, h('span', 'kit-spacer'), more);
  const sentinel = h('div', 'ak-sentinel');
  node.append(slot, foot, sentinel, legend());
  const observer = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !done && !busy && rows.length) loadMore();
    })
    : null;
  observer?.observe(sentinel);

  function paintFoot() {
    more.hidden = done;
    more.disabled = busy;
    status.textContent = rows.length ? `${num(rows.length)} öğrenci${done ? '' : '+'} · ${num(columns.size)} kaynak` : '';
  }

  async function refreshPackages() {
    const packages = await loadPackages(app).catch(() => []);
    packageSelect.options([{ value: '', label: 'Tüm paketler' },
      ...packages.map((item) => ({ value: item.id, label: item.name }))]);
  }

  function reload() {
    token += 1;
    stale = false;
    columns = new Map();
    rows = [];
    cursor = null;
    done = false;
    busy = false;
    grid = null;
    slot.replaceChildren(skeletonRows(8, 6));
    paintFoot();
    return loadMore();
  }

  async function loadMore() {
    if (done || busy) return;
    busy = true;
    paintFoot();
    const mine = token;
    try {
      const data = await app.api.get('/v1/admin/resource-progress',
        clean({ ...filters, cursor: cursor || undefined, limit: 50 }));
      if (mine !== token) return;
      for (const resource of data?.resources || []) if (!columns.has(resource.id)) columns.set(resource.id, resource);
      rows = rows.concat(data?.rows || []);
      cursor = data?.nextCursor || null;
      done = !cursor;
      paint();
    } catch (failure) {
      if (mine !== token) return;
      done = true;
      slot.replaceChildren(errorBox(failure, () => reload()));
    } finally {
      if (mine === token) busy = false;
      paintFoot();
    }
  }

  /** Sütunlar değişmediyse yalnız yeni satırlar eklenir; değiştiyse tablo baştan çizilir. */
  function paint() {
    if (!rows.length) {
      grid = null;
      slot.replaceChildren(empty(filters.behindOnly ? 'Geride kalan öğrenci yok' : 'Gösterilecek ilerleme yok',
        filters.behindOnly ? 'Hedef tarihine göre geride kalan öğrenci bulunmuyor.'
          : 'Bu süzgece uyan öğrencinin atanmış kaynağı yok. Atamalar sekmesinden kaynak atayın.'));
      return;
    }
    const resources = [...columns.values()];
    if (!grid || resources.length !== paintedColumns) {
      grid = h('div', 'ak-res-matrix');
      grid.setAttribute('role', 'table');
      grid.style.gridTemplateColumns = `minmax(190px, 240px) 72px repeat(${resources.length}, minmax(86px, 108px))`;
      grid.append(headCell('Öğrenci', 'corner'), headCell('Ortalama', 'avg'));
      for (const resource of resources) grid.append(resourceHead(resource));
      paintedColumns = resources.length;
      paintedRows = 0;
      slot.replaceChildren(grid);
    }
    for (const row of rows.slice(paintedRows)) appendRow(row, resources);
    paintedRows = rows.length;
  }

  function headCell(text, extra) {
    const cell = h('div', `ak-res-mh ${extra}`, text);
    cell.setAttribute('role', 'columnheader');
    return cell;
  }

  function resourceHead(resource) {
    const cell = h('button', 'ak-res-mh ak-res-mh-resource');
    cell.type = 'button';
    cell.setAttribute('role', 'columnheader');
    cell.title = `${resource.title} — ${KINDS[resource.kind] || ''} · ${num(resource.sectionCount)} bölüm. Ayrıntı için tıklayın.`;
    cell.append(h('span', 'ak-res-mh-title', resource.title), h('small', '', `${num(resource.sectionCount)} bölüm`));
    cell.addEventListener('click', () => ctx.openResource(resource.id));
    return cell;
  }

  function appendRow(row, resources) {
    const student = row.student || {};
    const name = h('div', 'ak-res-mstudent');
    name.setAttribute('role', 'rowheader');
    const link = h('button', 'ak-res-link', student.displayName || '—');
    link.type = 'button';
    link.title = 'Öğrenci kartını aç';
    link.addEventListener('click', () => app.go('students', { studentId: student.id }));
    name.append(link, h('small', '', [student.classGroup?.name, student.externalRef].filter(Boolean).join(' · ')));
    const byResource = new Map((row.cells || []).map((cell) => [cell.resourceId, cell.progress]));
    const present = resources.map((resource) => byResource.get(resource.id)).filter(Boolean);
    const behindCount = present.filter((item) => item.behind).length;
    const average = present.length
      ? Math.round(present.reduce((acc, item) => acc + (Number(item.percent) || 0), 0) / present.length) : null;
    const avg = h('div', 'ak-res-mavg');
    avg.append(h('b', '', average === null ? '—' : `%${average}`));
    if (behindCount) avg.append(h('small', 'ak-res-behind', `${num(behindCount)} geride`));
    grid.append(name, avg);
    for (const resource of resources) grid.append(heatCell(byResource.get(resource.id) ?? null, resource));
  }

  function legend() {
    const box = h('div', 'ak-res-legend');
    box.append(h('span', 'ak-muted', 'Hücre: tamamlanma yüzdesi ve bitirilen/toplam bölüm.'));
    for (const [tone, text] of LEGEND) {
      const item = h('span', 'ak-res-legend-item');
      item.append(h('span', `ak-res-swatch ${tone}`), h('span', '', text));
      box.append(item);
    }
    const late = h('span', 'ak-res-legend-item');
    late.append(h('span', 'ak-res-swatch behind'), h('span', '', '“geride”: hedef tarihe göre geride'));
    const none = h('span', 'ak-res-legend-item');
    none.append(h('span', 'ak-res-swatch none'), h('span', '', '—: rafında yok'));
    box.append(late, none);
    return box;
  }

  return {
    node,
    reload() { refreshPackages(); return reload(); },
    shown() { if (stale) reload(); },
    stale() { stale = true; if (!node.hidden && node.isConnected) reload(); },
    /** {classGroupId} | {packageId} | {resourceId, resourceTitle} süzgeciyle aç. */
    filter(next = {}) {
      filters.classGroupId = next.classGroupId || '';
      filters.studentGroupId = next.studentGroupId || '';
      filters.packageId = next.packageId || '';
      filters.resourceId = next.resourceId || '';
      filters.behindOnly = Boolean(next.behindOnly);
      classSelect.set(filters.classGroupId);
      groupSelect.set(filters.studentGroupId);
      resourceFilter.set(filters.resourceId, next.resourceTitle);
      refreshPackages().then(() => packageSelect.set(filters.packageId));
      packageSelect.set(filters.packageId);
      paintBehind();
      return reload();
    },
    destroy() { observer?.disconnect(); token += 1; resourceFilter.destroy(); },
  };
}

/** Isı hücresi: yüzde + bölüm yazıyla; renk eşlik eder. Ayrıntı ipucunda. */
function heatCell(progress, resource) {
  const cell = h('div', 'ak-res-cell');
  cell.setAttribute('role', 'cell');
  if (!progress) {
    cell.classList.add('none');
    cell.append(h('span', '', '—'));
    cell.title = `${resource.title}: öğrencinin rafında yok.`;
    return cell;
  }
  const pct = Math.max(0, Math.min(100, Math.round(Number(progress.percent) || 0)));
  const level = pct >= 100 ? 5 : pct >= 75 ? 4 : pct >= 50 ? 3 : pct >= 25 ? 2 : pct > 0 ? 1 : 0;
  cell.classList.add(`h${level}`);
  if (progress.behind) cell.classList.add('behind');
  cell.append(h('b', '', `%${pct}`), h('small', '', `${num(progress.sectionsDone)}/${num(progress.sectionsTotal)} bölüm`));
  if (progress.behind) cell.append(h('span', 'ak-res-behind', 'geride'));
  const accuracy = progress.accuracyPercent === null || progress.accuracyPercent === undefined
    ? '—' : `%${Math.round(progress.accuracyPercent)}`;
  cell.title = [
    resource.title,
    `Bölüm: ${num(progress.sectionsDone)}/${num(progress.sectionsTotal)}`,
    `Soru: ${num(progress.questionsSolved)}/${num(progress.questionsTotal)}`,
    `Doğruluk: ${accuracy}`,
    `Son çalışma: ${progress.lastStudiedAt ? fmtDateTime(progress.lastStudiedAt) : 'hiç'}`,
    progress.behind ? 'Hedef tarihe göre geride' : '',
  ].filter(Boolean).join('\n');
  return cell;
}

