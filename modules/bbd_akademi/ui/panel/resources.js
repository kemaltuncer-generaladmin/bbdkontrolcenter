// BBD Akademi — Kaynaklar (F-A8, F-C3…F-C6).
//
// Altı sekme: Kütüphane (hazır kütüphane + kurum kaynakları tek aramada) ·
// Kurum kaynakları (ekle / düzenle / kaldır) · İçindekiler (ünite → bölüm,
// sayfa, soru, konu eşlemesi) · Paketler (otomatik atama kurallı) · Atamalar ·
// İlerleme (öğrenci × kaynak).
//
// KURUM KAYNAĞI LİSTESİ AYRI BİR UÇ DEĞİLDİR: sözleşmede `/v1/admin/resources`
// için liste ucu yok; kurumun kaynakları `catalog_searchLibrary?scope=tenant`
// ile, tek kaynağın ayrıntısı ve içindekileri `catalog_getLibraryResource`
// ile okunur (küresel ve kurum kaynağı aynı uçtan). Yazmalar `/v1/admin/resources*`.
//
// HAZIR KÜTÜPHANE SALT OKUNURDUR (`409 global_read_only`): içindekilerini
// değiştirmek isteyen kurum kaynağı "Kuruma kopyala" ile kendine alır
// (`source: library_copy`), sonra kopyayı düzenler.
//
// PAKETLER KM'DEKİ ÖĞRENCİ BİLGİSİNDEN BESLENİR (kullanıcı kararı): kural =
// sınıf seviyesi / alan / hedef sınav; bunlar KM öğrenci kaydından Akademi'ye
// aynalanır ve kurala uyan YENİ öğrenciye dizin senkronunda paket atanır.
// Var olan öğrenciler için "Şimdi uygula".
//
// ISBN ile ekleme önce `admin_lookupIsbn` sorar: kaynak hazır kütüphanede mi,
// kurumda zaten var mı, bbdstore'da satılıyor mu — üçü yan yana gösterilir ve
// form eşleşmeden doldurulur. bbdstore kataloğu salt okunur (ürün bağlamak için).

import { button, blockedButton, h, loadStyles, menuButton } from '../../ui-kit/kit.js';
import { alertBox, badge, card, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { AkademiError } from './api.js';
import { storeSyncCard } from './book-picker.js';
import {
  actionButton, confirmAction, empty, errorBox, formDialog, kv, loader, money,
  multiChips, num, openDrawer, optionsOf, pagedList, problem, runAction, searchBox, sectionHead,
  selectBox, toolbar,
} from './common.js';
import {
  CURRICULUM_EXAMS, GRADE_OPTIONS, KINDS, TRACKS, clean, contentText, gradeValue,
  levelText, resourceCell, resourcePicker, scopeBadge, stackCell, subjectsText, tocPreview,
} from './resources-common.js';
import { tocTab } from './resources-toc.js';
import { packageDrawer, packagesTab } from './resources-packages.js';
import { assignDialog, assignmentsTab, progressTab } from './resources-assign.js';
import { batchesDialog, bulkAssignWizard } from './resources-bulk.js';
import { T as BULK } from './resources-bulk-logic.js';

export function section(app) {
  loadStyles(import.meta.url, 'resources.css');
  const node = h('div', 'ak-stack ak-res');
  let started = false;

  // Kaynak ekleme: üç yol tek düğmede (ana eylem). Yetki yoksa gerekçeli kapalı düğme.
  const add = app.can('admin_createResource')
    ? menuButton('Kaynak ekle', [
      { label: 'ISBN ya da bbdstore ile bul', onClick: () => addByLookup() },
      { label: 'Hazır kütüphaneden kopyala', onClick: () => addFromLibrary() },
      { label: 'Elle gir', onClick: () => addManual() },
    ], { variant: 'primary' })
    : blockedButton('Kaynak ekle', 'Bu işlem için yetkiniz yok (yöneticinizden izin isteyin).',
      { variant: 'primary' });
  // Akademi README §14 madde 116: BBD Store kataloğunun tamamından ücretsiz toplu atama (üç adım).
  const bulk = actionButton(app, 'admin_bulkAssignResources', BULK.open, { onClick: () => ctx.bulkAssign() });
  node.append(sectionHead('Kaynaklar', {
    subtitle: 'Hazır kütüphane, kurum kaynakları ve içindekiler; paketlerle seviye/alan kuralına göre atama; '
      + "BBD Store'daki bütün kitaplardan öğrencilere ücretsiz toplu atama.",
    actions: [bulk, add],
  }));

  // ------------------------------------------------------------- sekmeler
  const bodies = {};
  const tabs = tabBar([
    { key: 'library', label: 'Kütüphane' },
    { key: 'tenant', label: 'Kurum kaynakları' },
    { key: 'toc', label: 'İçindekiler' },
    { key: 'packages', label: 'Paketler' },
    { key: 'assignments', label: 'Atamalar' },
    { key: 'progress', label: 'İlerleme' },
  ], 'library', (key) => showTab(key));
  const tabSlot = h('div');
  node.append(tabs.node, tabSlot);

  const ctx = {
    openResource: (id) => resourceDrawer(app, id, ctx),
    /** `data` verilirse (ör. oluşturma yanıtı) ikinci kez okunmaz. */
    openToc: (id, data = null) => { go('toc'); bodies.toc.open(id, data); },
    openPackage: (id) => packageDrawer(app, id, ctx),
    copyToTenant: (resource) => copyToTenant(resource),
    editResource: (resource) => editResource(resource),
    deleteResource: (resource) => deleteResource(resource),
    assign: (options) => assignDialog(app, { ...options, onDone: () => ctx.changed('assignments') }),
    /** BBD Store'dan toplu ata (`resources`: Kütüphane'de seçilenler önceden seçili gelir). */
    bulkAssign: (options = {}) => bulkAssignWizard(app, { ...options, onDone: () => ctx.changed('assignments'),
      onShowBatch: (batchId) => go('assignments', { batchId }) }),
    showBatches: () => batchesDialog(app, { onShowBatch: (batchId) => go('assignments', { batchId }),
      onChanged: () => ctx.changed('assignment-removed') }),
    showAssignments: (filter) => go('assignments', filter),
    showProgress: (filter) => go('progress', filter),
    /**
     * Yazmadan sonra etkilenen sekmeleri tazeler (açılmamış sekme ilk açılışta
     * zaten çeker). Paket listesi atanan öğrenci sayısını taşıdığı için atama
     * değişince de tazelenir.
     */
    changed(what = 'resources') {
      if (what === 'resources') {
        bodies.library?.reload();
        bodies.tenant?.reload();
        return;
      }
      app.api.invalidate('/v1/admin/resource-packages');
      bodies.packages?.reload();
      if (what !== 'assignment-removed') bodies.assignments?.reload();
      bodies.progress?.stale();
    },
  };

  const builders = {
    library: () => resourceListTab(app, ctx, { tenantOnly: false }),
    tenant: () => resourceListTab(app, ctx, { tenantOnly: true }),
    toc: () => tocTab(app, ctx),
    packages: () => packagesTab(app, ctx),
    assignments: () => assignmentsTab(app, ctx),
    progress: () => progressTab(app, ctx),
  };

  /** `filter` verilirse sekme o süzgeçle (tek istekle) açılır. */
  function showTab(key, filter = null) {
    const fresh = !bodies[key];
    if (fresh) {
      bodies[key] = builders[key]();
      tabSlot.append(bodies[key].node);
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    if (filter && bodies[key].filter) bodies[key].filter(filter);
    else if (fresh) bodies[key].reload?.();
    else bodies[key].shown?.();
  }

  /** Sekmeyi programla aç (sekme şeridi + gövde birlikte). */
  function go(key, filter = null) {
    tabs.select(key, false);
    showTab(key, filter);
  }

  // ------------------------------------------------------------ ekleme yolları
  async function addByLookup() {
    const picked = await findDialog(app);
    if (!picked) return;
    const { choice, lookup } = picked;
    if (choice === 'open_tenant') { ctx.openResource(lookup.tenantResource.id); return; }
    if (choice === 'copy_library') {
      await copyToTenant(lookup.library, { storeProductId: lookup.store?.productId || null });
      return;
    }
    const store = lookup.store;
    const initial = choice === 'from_store' && store ? {
      title: store.title, publisher: store.publisher || '', isbn: lookup.isbn || store.isbn || '',
      storeProductId: store.productId, coverUrl: store.coverUrl || '', storeProduct: store,
    } : { isbn: lookup.isbn || '' };
    const created = await resourceForm(app, { mode: choice === 'from_store' ? 'isbn' : 'manual', initial });
    if (created) afterCreate(created, 'Kaynak eklendi. Şimdi içindekileri girin.');
  }

  async function addFromLibrary() {
    const resource = await pickLibraryDialog(app);
    if (resource) await copyToTenant(resource);
  }

  async function addManual() {
    const created = await resourceForm(app, { mode: 'manual', initial: {} });
    if (created) afterCreate(created, 'Kaynak eklendi. Şimdi içindekileri girin.');
  }

  async function copyToTenant(resource, extra = {}) {
    const created = await resourceForm(app, { mode: 'library_copy', initial: {
      ...formValueOf(resource),
      storeProductId: extra.storeProductId || resource.storeProductId || null,
      copyFromResourceId: resource.id,
    } });
    if (created) afterCreate(created, 'Kopya oluşturuldu; içindekileri artık kurumunuza göre düzenleyebilirsiniz.');
  }

  function afterCreate(created, message) {
    app.toast(message, 'good');
    ctx.changed('resources');
    ctx.openToc(created.id, Array.isArray(created.toc) ? created : null);
  }

  async function editResource(resource) {
    let detail = resource;
    if (detail.description === undefined) {
      detail = await runAction(app, () => app.api.get(`/v1/catalog/library/${resource.id}`));
      if (!detail) return;
    }
    const saved = await resourceForm(app, { mode: 'edit', resource: detail, initial: formValueOf(detail) });
    if (saved) {
      app.toast('Kaynak güncellendi.', 'good');
      ctx.changed('resources');
    }
  }

  async function deleteResource(resource) {
    const yes = await confirmAction(app, {
      title: `“${resource.title}” kaldırılsın mı?`,
      description: 'Hiç atanmamış kaynak silinir. Atanmış ya da öğrenci ilerlemesi olan kaynak silinmez, '
        + 'arşivlenir: öğrencilerin rafında ve geçmişinde kalır, yeni atamaya kapanır.',
      confirmLabel: 'Kaldır',
      danger: true,
    });
    if (!yes) return false;
    const done = await runAction(app, () => app.api.del(`/v1/admin/resources/${resource.id}`),
      { success: 'Kaynak kaldırıldı.' });
    if (done === undefined) return false;
    ctx.changed('resources');
    return true;
  }

  return {
    node,
    shown(payload) {
      const wanted = payload?.tab && builders[payload.tab] ? payload.tab : null;
      // "Bu konuyu kapsayan kaynaklar" (ör. Müfredat bölümünden): {topicId, topicName?}.
      const topic = payload?.topicId ? { topicId: payload.topicId, topicName: payload.topicName } : null;
      const first = !started;
      started = true;
      if (topic) go('library', topic);
      else if (wanted) go(wanted);
      else if (payload?.packageId) go('packages');
      else if (first) go('library');
      if (payload?.resourceId) {
        if (payload.tab === 'toc') ctx.openToc(payload.resourceId);
        else ctx.openResource(payload.resourceId);
      }
      if (payload?.packageId) ctx.openPackage(payload.packageId);
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// ------------------------------------------------------------- kaynak listesi

/** bbdstore'dan eşitlenen kitap: rozet + fiyat / stok (Akademi README §14 madde 98). */
function storeCell(row) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(badge('bbdstore', 'info'));
  const store = row.store;
  if (store) {
    cell.append(h('small', 'ak-muted', store.status === 'removed' ? 'Mağazadan kalktı'
      : `${money(store.priceMinor)}${store.inStock ? '' : ' · stokta yok'}`));
  }
  return cell;
}

/**
 * Kütüphane (kapsam süzgeçli) ve Kurum kaynakları (yalnız `tenant`, CRUD
 * düğmeli) aynı listeyi kullanır: `GET /v1/catalog/library`, imleçli.
 */
function resourceListTab(app, ctx, { tenantOnly }) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', isbn: '', scope: tenantOnly ? 'tenant' : 'all', kind: '', subject: '',
    gradeLevel: '', exam: '', track: '', publisher: '', topicId: '' };

  const search = searchBox({ placeholder: 'Ad, yayınevi ya da ISBN', onSearch: (text) => {
    const digits = text.replace(/[\s-]/g, '');
    if (/^\d{10}(\d{3})?$/.test(digits)) { filters.isbn = digits; filters.q = ''; }
    else { filters.q = text; filters.isbn = ''; }
    reload();
  } });
  const publisher = searchBox({ placeholder: 'Yayınevi', onSearch: (text) => { filters.publisher = text; reload(); } });
  publisher.node.classList.add('ak-res-publisher');
  const pick = (key) => (value) => { filters[key] = value; reload(); };
  const scopeSelect = tenantOnly ? null : selectBox([
    { value: 'all', label: 'Tüm kitaplar' }, { value: 'global', label: 'Yalnız hazır kütüphane' },
    { value: 'tenant', label: 'Yalnız kurum kaynakları' },
    // Akademi README §14 madde 98: bbdstore'dan saatlik eşitlenen kitaplar.
    { value: 'store', label: 'Yalnız bbdstore kitapları' }], 'all', pick('scope'), { label: 'Kapsam' });
  const kindSelect = selectBox(optionsOf(KINDS, { empty: 'Tüm türler' }), '', pick('kind'), { label: 'Tür' });
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', pick('subject'), { label: 'Ders' });
  const gradeSelect = selectBox([{ value: '', label: 'Tüm seviyeler' }, ...GRADE_OPTIONS], '', pick('gradeLevel'),
    { label: 'Seviye' });
  const examSelect = selectBox(optionsOf(CURRICULUM_EXAMS, { empty: 'Tüm sınavlar' }), '', pick('exam'), { label: 'Sınav' });
  const trackSelect = selectBox(optionsOf(TRACKS, { empty: 'Tüm alanlar' }), '', pick('track'), { label: 'Alan' });
  app.lookups.subjects().then((items) => subjectSelect.options([{ value: '', label: 'Tüm dersler' },
    ...items.map((item) => ({ value: item.id, label: item.name }))])).catch(() => {});

  // Konu süzgeci yalnız derin bağlantıyla gelir ("bu konuyu kapsayan kaynaklar"); çipten kaldırılır.
  const topicSlot = h('span');
  const paintTopic = (name) => {
    topicSlot.replaceChildren();
    if (!filters.topicId) return;
    const chip = h('button', 'kit-chip on', `Konu: ${name || filters.topicId} ✕`);
    chip.type = 'button';
    chip.title = 'Konu süzgecini kaldır';
    chip.addEventListener('click', () => { filters.topicId = ''; paintTopic(); reload(); });
    topicSlot.append(chip);
  };
  // Mağaza eşitlemesi (yalnız Kütüphane sekmesinde): son eşitleme, kitap sayısı, "Şimdi eşitle".
  const sync = tenantOnly ? null : storeSyncCard(app);
  if (sync) node.append(sync.node);
  node.append(toolbar(search.node, scopeSelect?.node, kindSelect.node, subjectSelect.node),
    toolbar(gradeSelect.node, examSelect.node, trackSelect.node, publisher.node, topicSlot));
  if (tenantOnly) {
    node.append(h('div', 'ak-muted', 'Kurumun eklediği ve hazır kütüphaneden kopyaladığı kaynaklar. '
      + 'Yeni kaynak için sağ üstteki “Kaynak ekle”yi kullanın.'));
  }

  const open = (row) => ctx.openResource(row.id);
  const columns = [
    { key: 'title', label: 'Kaynak', width: 'minmax(0, 2.4fr)', cell: (row) => resourceCell(row, open) },
    { key: 'kind', label: 'Tür', width: '120px', cell: (row) => KINDS[row.kind] || row.kind },
    { key: 'level', label: 'Seviye / sınav', width: 'minmax(0, 1.3fr)', cell: (row) => levelText(row) },
    { key: 'subjects', label: 'Ders', width: 'minmax(0, 1.1fr)', cell: (row) => subjectsText(row) },
    { key: 'content', label: 'İçerik', width: '130px', cell: (row) => stackCell(`${num(row.sectionCount)} bölüm`,
      `${num(row.questionCount)} soru`) },
  ];
  if (!tenantOnly) {
    columns.push({ key: 'scope', label: 'Kapsam', width: '120px', cell: (row) => (row.origin === 'store'
      ? storeCell(row) : scopeBadge(row.scope)) });
  }
  columns.push({ key: 'actions', label: '', width: tenantOnly ? '250px' : '170px', cell: (row) => {
    const actions = h('div', 'ak-row-actions');
    if (row.scope === 'global') {
      actions.append(actionButton(app, 'admin_createResource', 'Kuruma kopyala', { variant: 'ghost',
        onClick: () => ctx.copyToTenant(row) }));
    } else {
      actions.append(button('İçindekiler', { variant: 'ghost', onClick: () => ctx.openToc(row.id) }));
      if (tenantOnly) {
        actions.append(
          actionButton(app, 'admin_updateResource', 'Düzenle', { variant: 'ghost', onClick: () => ctx.editResource(row) }),
          actionButton(app, 'admin_deleteResource', 'Kaldır', { variant: 'ghost', onClick: () => ctx.deleteResource(row) }),
        );
      }
    }
    return actions;
  } });

  // Seçilenleri ata (README §14 madde 116): Kütüphane'de çoklu seçim → toplu atama sihirbazının "Kime" adımı.
  const assignSelected = actionButton(app, 'admin_bulkAssignResources', 'Seçilenleri ata', { variant: 'primary',
    onClick: () => {
      const keys = new Set(list.table.selection());
      const chosen = list.rows().filter((row) => keys.has(row.id));
      if (chosen.length) ctx.bulkAssign({ resources: chosen.map((row) => ({ id: row.id, title: row.title })) });
    } });
  assignSelected.hidden = true;
  const paintSelected = (keys) => {
    assignSelected.hidden = !keys.length;
    assignSelected.textContent = keys.length ? `Seçilenleri ata (${num(keys.length)})` : 'Seçilenleri ata';
  };
  node.append(toolbar(h('span', 'kit-spacer'), assignSelected));
  const list = pagedList({
    app,
    path: '/v1/catalog/library',
    query: () => clean(filters),
    columns,
    selectable: app.can('admin_bulkAssignResources'),
    onSelect: (keys) => paintSelected(keys),
    onLoaded: () => paintSelected(list.table.selection()),
    empty: tenantOnly
      ? { title: 'Kurum kaynağı yok', text: 'ISBN ile, elle ya da hazır kütüphaneden kopyalayarak kaynak ekleyin.' }
      : { title: 'Bu süzgece uyan kaynak yok', text: 'Aramayı ya da süzgeçleri genişletin.' },
  });
  node.append(list.node);

  function reload() { return list.reload(); }
  return {
    node,
    reload,
    /** {topicId, topicName?} süzgeciyle aç. */
    filter(next = {}) {
      filters.topicId = next.topicId || '';
      paintTopic(next.topicName);
      return reload();
    },
    destroy() { search.destroy(); publisher.destroy(); list.destroy(); },
  };
}

// ------------------------------------------------------------- kaynak ayrıntısı

/** Kaynak çekmecesi: künye, bbdstore ürünü, içindekiler önizlemesi, konu kapsamı. */
function resourceDrawer(app, resourceId, ctx) {
  const panel = openDrawer(app, { title: 'Kaynak', subtitle: 'Yükleniyor…' });
  panel.node.classList.add('ak-res-drawer');
  const view = loader(() => app.api.get(`/v1/catalog/library/${resourceId}`), (resource) => {
    panel.setTitle(resource.title);
    return render(resource);
  }, { rows: 6, columns: 4 });
  panel.body.append(view.node);
  view.reload();

  function render(resource) {
    const nodes = [];
    const tenant = resource.scope === 'tenant';
    const tools = toolbar();
    if (tenant) {
      tools.append(
        actionButton(app, 'admin_putResourceToc', 'İçindekileri düzenle', { variant: 'primary',
          onClick: () => { panel.close(); ctx.openToc(resource.id); } }),
        actionButton(app, 'admin_updateResource', 'Düzenle', { onClick: async () => {
          await ctx.editResource(resource);
          view.reload();
        } }),
      );
    } else {
      tools.append(actionButton(app, 'admin_createResource', 'Kuruma kopyala', { variant: 'primary',
        onClick: () => { panel.close(); ctx.copyToTenant(resource); } }));
    }
    tools.append(
      actionButton(app, 'admin_createResourceAssignments', 'Ata', {
        onClick: () => ctx.assign({ resources: [resource] }) }),
      button('Atamaları', { variant: 'ghost', onClick: () => {
        panel.close();
        ctx.showAssignments({ resourceId: resource.id, resourceTitle: resource.title });
      } }),
      button('İlerleme', { variant: 'ghost', onClick: () => {
        panel.close();
        ctx.showProgress({ resourceId: resource.id, resourceTitle: resource.title });
      } }),
    );
    if (tenant) {
      tools.append(h('span', 'kit-spacer'), actionButton(app, 'admin_deleteResource', 'Kaldır', { variant: 'ghost',
        onClick: async () => { if (await ctx.deleteResource(resource)) panel.close(); } }));
    }
    nodes.push(tools);
    if (!tenant) {
      nodes.push(alertBox('Hazır kütüphane kaynağı salt okunurdur. İçindekileri kurumunuza göre değiştirmek için '
        + '“Kuruma kopyala” ile kopyasını alın; kopya serbestçe düzenlenir.', 'info'));
    }

    const storeSlot = h('span', 'ak-muted', resource.storeProductId ? 'bbdstore bilgisi alınıyor…' : 'Bağlı ürün yok');
    const units = resource.toc || [];
    nodes.push(card('', kv([
      ['Kapsam', scopeBadge(resource.scope)],
      ['Tür', KINDS[resource.kind] || resource.kind],
      ['Yayınevi', resource.publisher],
      ['Yıl', resource.year],
      ['ISBN', resource.isbn],
      ['Seviye / sınav', levelText(resource)],
      ['Ders', subjectsText(resource)],
      ['İçerik', `${num(units.length)} ünite · ${contentText(resource)}`],
      ['bbdstore', storeSlot],
      resource.description ? ['Açıklama', resource.description] : null,
    ])));
    if (resource.storeProductId) fillStore(app, resource.storeProductId, storeSlot);

    nodes.push(card('İçindekiler', tocPreview(resource),
      units.length ? `${num(units.length)} ünite` : ''));
    nodes.push(card('Konu kapsamı', coverageTable(resource),
      (resource.topicCoverage || []).length ? `${num(resource.topicCoverage.length)} konu` : ''));
    return nodes;
  }
  return panel;
}

/** bbdstore ürünü (tek istek, çekmece açılınca). Kapalı özellik/erişim sorunu sessiz geçmez. */
async function fillStore(app, productId, slot) {
  try {
    const product = await app.api.get(`/v1/catalog/books/${productId}`);
    slot.replaceWith(productLine(product));
  } catch (failure) {
    slot.textContent = `Ürün bilgisi alınamadı: ${problem(failure)}`;
  }
}

function productLine(product) {
  const node = h('div', 'ak-res-product');
  node.append(h('b', '', product.title),
    h('span', 'ak-muted', [product.author, product.publisher, product.isbn ? `ISBN ${product.isbn}` : '']
      .filter(Boolean).join(' · ')),
    h('span', 'ak-res-product-price', money(product.priceMinor)),
    badge(product.inStock ? 'Stokta' : 'Stokta yok', product.inStock ? 'good' : 'warn'));
  return node;
}

function coverageTable(resource) {
  const rows = [...(resource.topicCoverage || [])].sort((a, b) => (b.questionCount || 0) - (a.questionCount || 0));
  const table = dataTable({
    dense: true,
    columns: [
      { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => row.topic?.name || '—' },
      { key: 'sections', label: 'Bölüm', width: '70px', align: 'num', cell: (row) => num((row.sectionIds || []).length) },
      { key: 'questions', label: 'Soru', width: '70px', align: 'num', cell: (row) => num(row.questionCount) },
    ],
    rows,
    empty: empty('Konu eşlemesi yok', 'Bölümlere konu eşlenince öğrenci ve öğretmen “bu konuyu kapsayan kaynaklar”ı görür.'),
  });
  return table.node;
}

// ------------------------------------------------------------- kaynak formu

/** Kaynak özeti / ayrıntısı → form değeri. */
function formValueOf(resource) {
  return {
    title: resource.title || '',
    kind: resource.kind || '',
    publisher: resource.publisher || '',
    year: resource.year ?? null,
    isbn: resource.isbn || '',
    gradeLevels: (resource.gradeLevels || []).map(String),
    exams: [...(resource.exams || [])],
    tracks: [...(resource.tracks || [])],
    subjectIds: (resource.subjects || []).map((subject) => subject.id),
    coverUrl: resource.coverUrl || '',
    description: resource.description || '',
    storeProductId: resource.storeProductId || null,
  };
}

const MODE_TEXT = {
  manual: ['Yeni kaynak (elle)', 'Kitabın künyesini girin; içindekileri kaydettikten sonra girersiniz.'],
  isbn: ['Yeni kaynak (bbdstore eşleşmesi)', 'Bilgiler bbdstore ürününden dolduruldu; tür ve seviyeyi kontrol edin.'],
  library_copy: ['Hazır kaynağı kuruma kopyala', 'Kaynak içindekileri ve konu eşlemesiyle birlikte kopyalanır. '
    + 'Kopya kurumunuzundur: adı, künyesi ve içindekileri serbestçe düzenlenir; hazır kütüphanedeki asıl kayıt değişmez.'],
  edit: ['Kaynağı düzenle', ''],
};

/**
 * Kaynak oluştur / düzenle. `mode`: manual | isbn | library_copy | edit.
 * @returns {Promise<object|null>} kaydedilen kaynak (ayrıntı) ya da null
 */
async function resourceForm(app, { mode, initial = {}, resource = null }) {
  const subjects = await app.lookups.subjects().catch(() => []);
  const [title, description] = MODE_TEXT[mode] || MODE_TEXT.manual;
  // ÖZEL ALAN DEĞERLERİ KAPANIŞTA TUTULUR, `value` içine KONMAZ: formDialog aynı
  // `value`yu formGrid'e de verir ve her ızgara değişikliğinde formGrid'in
  // taslağı (özel alanların İLK değeriyle) canlı taslağın üstüne yazılır.
  const extra = {
    gradeLevels: [...(initial.gradeLevels || [])],
    exams: [...(initial.exams || [])],
    tracks: [...(initial.tracks || [])],
    subjectIds: [...(initial.subjectIds || [])],
    storeProductId: initial.storeProductId || null,
  };
  const value = {
    title: initial.title || '', kind: initial.kind || '', publisher: initial.publisher || '',
    year: initial.year ?? null, isbn: initial.isbn || '', coverUrl: initial.coverUrl || '',
    description: initial.description || '',
  };
  const chips = (key, options) => (_draft, set) => multiChips(options, extra[key], (list) => {
    extra[key] = list;
    set(list);
  }).node;
  const LEVEL_LABEL = { ortaokul: 'Ortaokul', lise: 'Lise', exam: 'Sınav', custom: 'Kuruma özel' };
  const subjectChips = (_draft, set) => {
    const wrap = h('div', 'ak-res-subjects');
    if (!subjects.length) { wrap.append(h('span', 'ak-muted', 'Ders listesi alınamadı.')); return wrap; }
    const chosen = new Set(extra.subjectIds);
    const groups = new Map();
    for (const subject of subjects) {
      const key = subject.custom ? 'custom' : subject.level || 'custom';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(subject);
    }
    for (const [level, items] of groups) {
      const row = h('div', 'ak-res-subject-row');
      row.append(h('span', 'ak-res-subject-level', LEVEL_LABEL[level] || level),
        multiChips(items.map((item) => ({ value: item.id, label: item.shortName || item.name })),
          items.filter((item) => chosen.has(item.id)).map((item) => item.id), (list) => {
            for (const item of items) chosen.delete(item.id);
            for (const id of list) chosen.add(id);
            extra.subjectIds = [...chosen];
            set(extra.subjectIds);
          }).node);
      wrap.append(row);
    }
    return wrap;
  };

  const saved = await formDialog(app, {
    title: mode === 'edit' && resource ? `“${resource.title}” — düzenle` : title,
    description,
    wide: true,
    value,
    fields: [
      { key: 'title', label: 'Kaynak adı', required: true, maxLength: 160, wide: true },
      { key: 'kind', label: 'Tür', type: 'select', required: true,
        options: optionsOf(KINDS, { empty: 'Tür seçin' }) },
      { key: 'publisher', label: 'Yayınevi', maxLength: 80 },
      { key: 'year', label: 'Basım yılı', type: 'number', min: 1990, max: 2100 },
      { key: 'isbn', label: 'ISBN', maxLength: 17, placeholder: '978…',
        validate: (raw) => (raw && !/^\d{10}(\d{3})?$/.test(String(raw).replace(/[\s-]/g, ''))
          ? 'ISBN 10 ya da 13 haneli olmalı (tire ve boşluk yok sayılır).' : null) },
      { key: 'gradeLevels', label: 'Seviye', type: 'custom', render: chips('gradeLevels', GRADE_OPTIONS) },
      { key: 'exams', label: 'Sınav', type: 'custom', render: chips('exams', optionsOf(CURRICULUM_EXAMS)),
        hint: 'LGS ortaokul, TYT/AYT lise ve mezun kaynakları içindir.' },
      { key: 'tracks', label: 'Alan', type: 'custom', render: chips('tracks', optionsOf(TRACKS)),
        hint: 'Yalnız alana özel lise/mezun kaynaklarında seçin; boş = bütün alanlar.' },
      { key: 'subjectIds', label: 'Dersler', type: 'custom', render: subjectChips },
      { key: 'storeProductId', label: 'bbdstore ürünü', type: 'custom',
        render: (draft, set) => storeLinkField(app, {
          get: () => extra.storeProductId,
          set: (productId) => { extra.storeProductId = productId; set(productId); },
          query: () => draft.isbn || draft.title || '',
          known: initial.storeProduct || null,
        }),
        hint: 'Bağlı ürün, öğrencinin uygulamadan kitap talebinde kullanılır (fiyat ve stok bbdstore’dan).' },
      { key: 'coverUrl', label: 'Kapak görseli adresi', maxLength: 500, wide: true, placeholder: 'https://…',
        validate: (raw) => (raw && !/^https?:\/\/\S+$/.test(String(raw).trim()) ? 'Adres http(s):// ile başlamalı.' : null) },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 1000, wide: true },
    ],
    submitLabel: mode === 'edit' ? 'Kaydet' : mode === 'library_copy' ? 'Kuruma kopyala' : 'Kaynağı ekle',
    onSubmit: (draft, { key }) => {
      const body = {
        isbn: draft.isbn ? String(draft.isbn).replace(/[\s-]/g, '') : null,
        storeProductId: extra.storeProductId || null,
        title: String(draft.title || '').trim(),
        publisher: String(draft.publisher || '').trim() || null,
        kind: draft.kind,
        year: draft.year ? Number(draft.year) : null,
        gradeLevels: extra.gradeLevels.map(gradeValue).filter((item) => item !== null),
        exams: extra.exams,
        tracks: extra.tracks,
        subjectIds: extra.subjectIds,
        coverUrl: String(draft.coverUrl || '').trim() || null,
        description: String(draft.description || '').trim() || null,
      };
      if (mode === 'edit') return app.api.patch(`/v1/admin/resources/${resource.id}`, body);
      body.source = mode === 'library_copy' ? 'library_copy' : mode === 'isbn' ? 'isbn' : 'manual';
      if (mode === 'library_copy') body.copyFromResourceId = initial.copyFromResourceId;
      return app.api.post('/v1/admin/resources', body, { key });
    },
  });
  return saved && saved !== true ? saved : null;
}

/** Formdaki bbdstore bağlantısı: göster / ara-bağla / kaldır. */
function storeLinkField(app, { get, set, query, known }) {
  const wrap = h('div', 'ak-res-store');
  const info = h('div', 'ak-res-store-info');
  const clear = button('Bağlantıyı kaldır', { variant: 'ghost', onClick: () => { set(null); paint(null); } });
  const find = button('bbdstore’da ara', { onClick: async () => {
    const product = await storeSearchDialog(app, { q: query() });
    if (!product) return;
    set(product.productId);
    paint(product);
  } });
  function paint(product) {
    info.replaceChildren();
    if (product) info.append(productLine(product));
    else if (get()) info.append(h('span', 'ak-muted', 'Ürün bilgisi alınıyor…'));
    else info.append(h('span', 'ak-muted', 'Bağlı ürün yok.'));
    clear.hidden = !get();
  }
  paint(known);
  const linked = get();
  if (!known && linked) {
    app.api.get(`/v1/catalog/books/${linked}`).then((product) => paint(product))
      .catch((failure) => info.replaceChildren(h('span', 'ak-muted', `Bağlı ürün: ${linked} `
        + `(bilgi alınamadı: ${problem(failure)})`)));
  }
  const actions = h('div', 'ak-inline');
  actions.append(find, clear);
  wrap.append(info, actions);
  return wrap;
}

// ------------------------------------------------------------- ISBN / bbdstore

/**
 * Kaynak bul: ISBN yazılırsa `admin_lookupIsbn`, ad yazılırsa bbdstore araması →
 * seçilen ürünün ISBN'i ile eşleşme. Üç kaynak (hazır kütüphane / kurum /
 * bbdstore) yan yana gösterilir; kullanıcı ne yapılacağını seçer.
 * @returns {Promise<{choice, lookup}|null>}
 */
function findDialog(app) {
  let state = null;                 // {choice, lookup}
  const finder = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    const input = h('input', 'kit-input');
    input.type = 'text';
    input.placeholder = 'ISBN (978…) ya da kitap adı';
    input.setAttribute('aria-label', 'ISBN ya da kitap adı');
    const results = h('div', 'ak-stack');
    const go = button('Sorgula', { variant: 'primary', onClick: () => run() });
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); run(); } });
    const row = h('div', 'ak-res-find-bar');
    row.append(input, go);
    wrap.append(row, results);

    async function run() {
      const text = input.value.trim();
      const digits = text.replace(/[\s-]/g, '');
      state = null;
      set(null);
      if (!text) return;
      if (/^\d{10}(\d{3})?$/.test(digits)) { await lookup(digits); return; }
      if (text.length < 2) return;
      results.replaceChildren(h('div', 'ak-muted', 'bbdstore’da aranıyor…'));
      const list = storeResults(app, text, async (product) => {
        if (product.isbn) await lookup(String(product.isbn).replace(/[\s-]/g, ''), product);
        else showMatches({ isbn: '', library: null, tenantResource: null, store: product });
      });
      results.replaceChildren(h('div', 'ak-muted', 'Ürünü seçin; ISBN’i varsa kütüphane ve kurum kayıtlarıyla da eşleştirilir.'),
        list.node);
    }

    async function lookup(isbn, product = null) {
      go.disabled = true;
      results.replaceChildren(h('div', 'ak-muted', 'Eşleşmeler aranıyor…'));
      try {
        const found = await app.api.get('/v1/admin/resources/isbn-lookup', { isbn });
        showMatches({ ...found, store: found.store || product });
      } catch (failure) {
        results.replaceChildren(errorBox(failure));
      } finally {
        go.disabled = false;
      }
    }

    function showMatches(found) {
      const tenant = found.tenantResource || (found.library?.scope === 'tenant' ? found.library : null);
      const library = found.library?.scope === 'global' ? found.library : null;
      const lookupResult = { ...found, tenantResource: tenant, library };
      const grid = h('div', 'ak-res-matches');
      grid.append(
        matchCard('Kurum kaynakları', tenant, (item) => [KINDS[item.kind], item.publisher, contentText(item)],
          'Bu ISBN kurumda kayıtlı değil.'),
        matchCard('Hazır kütüphane', library, (item) => [KINDS[item.kind], item.publisher, contentText(item)],
          'Hazır kütüphanede yok.'),
        matchCard('bbdstore', found.store, (item) => [item.author, item.publisher, money(item.priceMinor),
          item.inStock ? 'Stokta' : 'Stokta yok'], 'bbdstore’da satılmıyor.'),
      );
      const choices = [];
      if (tenant) choices.push(['open_tenant', `Kurumdaki “${tenant.title}” kaydını aç (yeniden eklenmez)`]);
      if (library && !tenant) choices.push(['copy_library', 'Hazır kaynağı kuruma kopyala — içindekiler ve konu eşlemesi hazır gelir']);
      if (found.store && !tenant) choices.push(['from_store', 'bbdstore bilgileriyle yeni kaynak oluştur (içindekileri siz girersiniz)']);
      if (!tenant) choices.push(['manual', 'Bilgileri elle gir']);
      const radios = h('div', 'ak-res-choices');
      const name = `ak-res-choice-${Date.now()}`;
      for (const [index, [choice, label]] of choices.entries()) {
        const line = h('label', 'ak-res-choice');
        const radio = h('input');
        radio.type = 'radio';
        radio.name = name;
        radio.value = choice;
        radio.checked = index === 0;
        radio.addEventListener('change', () => { state = { choice, lookup: lookupResult }; set(state); });
        line.append(radio, h('span', '', label));
        radios.append(line);
      }
      state = { choice: choices[0][0], lookup: lookupResult };
      set(state);
      results.replaceChildren(grid, h('div', 'kit-field-label', 'Ne yapılsın?'), radios);
    }
    setTimeout(() => input.focus(), 0);
    return wrap;
  };

  return formDialog(app, {
    title: 'Kaynak bul: ISBN ya da bbdstore',
    description: 'ISBN ile hazır kütüphane, kurum kaynakları ve bbdstore aynı anda sorulur. '
      + 'ISBN bilinmiyorsa kitap adıyla bbdstore’da arayın.',
    wide: true,
    fields: [{ key: 'finder', label: 'Arama', type: 'custom', render: finder,
      validate: () => (state ? null : 'Önce sorgulayın ve bir seçenek belirleyin.') }],
    submitLabel: 'Devam et',
    onSubmit: () => {
      if (!state) throw new AkademiError({ message: 'Önce sorgulayın.' });
      return state;
    },
  }).then((result) => (result && result !== true ? result : null));
}

function matchCard(title, item, metaOf, emptyText) {
  const box = h('div', `ak-res-match${item ? ' found' : ''}`);
  box.append(h('div', 'ak-res-match-title', title));
  if (item) {
    box.append(badge('Eşleşti', 'good'), h('b', '', item.title),
      h('span', 'ak-muted', metaOf(item).filter(Boolean).join(' · ')));
  } else {
    box.append(badge('Eşleşme yok', 'dim'), h('span', 'ak-muted', emptyText));
  }
  return box;
}

/**
 * bbdstore ürün listesi (sözleşme sayfalıdır: `page` + `hasMore`).
 * Salt okunur; `onPick(product)` seçimi bildirir.
 */
function storeResults(app, text, onPick) {
  const node = h('div', 'ak-res-store-list');
  const rows = h('div');
  const more = button('Daha fazla', { variant: 'ghost', onClick: () => load() });
  more.hidden = true;
  node.append(rows, more);
  let page = 0;
  let busy = false;
  async function load() {
    if (busy) return;
    busy = true;
    more.disabled = true;
    try {
      const digits = text.replace(/[\s-]/g, '');
      const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
      const data = await app.api.get('/v1/catalog/books', { ...query, page: page + 1, limit: 20 });
      page += 1;
      const items = data?.items || [];
      if (page === 1 && !items.length) rows.append(h('div', 'ak-muted', 'bbdstore’da eşleşen kitap yok.'));
      for (const product of items) {
        const row = h('div', 'ak-res-store-row');
        const info = h('div', 'ak-cell-stack');
        info.append(h('b', '', product.title), h('small', '', [product.author, product.publisher,
          product.isbn ? `ISBN ${product.isbn}` : ''].filter(Boolean).join(' · ')));
        const pick = button('Seç', { variant: 'ghost', onClick: () => onPick(product) });
        row.append(info, h('span', 'ak-res-product-price', money(product.priceMinor)),
          badge(product.inStock ? 'Stokta' : 'Stokta yok', product.inStock ? 'good' : 'warn'), pick);
        rows.append(row);
      }
      more.hidden = !data?.hasMore;
    } catch (failure) {
      rows.append(errorBox(failure));
      more.hidden = true;
    } finally {
      busy = false;
      more.disabled = false;
    }
  }
  load();
  return { node };
}

/**
 * bbdstore'da ara ve ürün seç (formun içinden açılır). Kendi penceresidir:
 * Esc yalnız bunu kapatır, alttaki formu değil.
 * @returns {Promise<object|null>} ürün
 */
function storeSearchDialog(app, { q = '' } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const box = h('div', 'kit-dialog ak-dialog wide');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    const slot = h('div', 'ak-res-store-slot');
    let search = null;
    const close = (product) => {
      window.removeEventListener('keydown', onKey, true);
      search?.destroy();
      overlay.remove();
      resolve(product);
    };
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      close(null);
    };
    const show = (text) => {
      if (text.length < 2) { slot.replaceChildren(h('div', 'ak-muted', 'En az iki harf ya da ISBN yazın.')); return; }
      slot.replaceChildren(storeResults(app, text, (product) => close(product)).node);
    };
    search = searchBox({ placeholder: 'Kitap adı, yazar, yayınevi ya da ISBN', value: q, onSearch: show });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }));
    box.append(h('h3', 'kit-dialog-title', 'bbdstore kataloğu'),
      h('p', 'kit-dialog-text', 'Salt okunur: ürün yalnız kaynağa bağlanır, mağazada değişiklik yapılmaz.'),
      search.node, slot, actions);
    overlay.append(box);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    window.addEventListener('keydown', onKey, true);
    app.root.append(overlay);
    search.node.focus();
    show(q.trim());
  });
}

/** Hazır kütüphaneden kaynak seç ("Kaynak ekle → Hazır kütüphaneden kopyala"). */
function pickLibraryDialog(app) {
  let chosen = null;
  return formDialog(app, {
    title: 'Hazır kütüphaneden kopyala',
    description: 'Kopyalamak istediğiniz kaynağı arayın. Kopya içindekileriyle gelir ve kurumunuza göre düzenlenir.',
    wide: true,
    fields: [{ key: 'resource', label: 'Hazır kaynak', type: 'custom', required: true, render: (draft, set) => {
      const wrap = h('div', 'ak-stack');
      const picked = h('div', 'ak-muted', 'Kaynak seçilmedi.');
      const picker = resourcePicker(app, { scope: 'global', onPick: (item) => {
        chosen = item;
        set(item.id);
        picked.className = 'ak-res-picked';
        picked.replaceChildren(resourceCell(item), h('span', 'ak-muted', `${KINDS[item.kind] || ''} · ${levelText(item)} · `
          + `${contentText(item)}`));
      } });
      wrap.append(picker.node, picked);
      return wrap;
    } }],
    submitLabel: 'Devam et',
    onSubmit: () => chosen,
  }).then((result) => (result && result !== true ? result : null));
}

