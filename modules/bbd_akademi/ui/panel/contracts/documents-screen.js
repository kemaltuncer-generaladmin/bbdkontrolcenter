// BBD Akademi — "Sözleşmeler" ekranı (Finans ve Kantin → Sözleşmeler; F-SZ, Akademi README §14 madde 139).
//
// KULLANICI (28.10.2026): "ayrıca sözleşmeler vs km de olsun ana kumanda km bizim" + "isteğe bağlı şu şu şu
// sözleşmeleri çıkar diyeceğiz. şak o sözleşmeler toplu pdf gelecek yazdır diyeceğiz yazdıracak".
//
// Sekmeler (okuma iznine göre çizilir; derin bağlantı `app.go('contracts', {tab})`):
//   · Sözleşmeler (A1): süzgeçli liste, çoklu seçim → "Belge çıkar" (A17/A18 → TEK PDF → önizle → Yazdır);
//     "Gecikenlere Yasal Uyarı" tek tık. Satır → öğrencinin Sözleşmeler sekmesi (80-km-sozlesme).
//   · Sözleşmesi olmayanlar (A2): "Kayıt ve ödeme planı" → sihirbaz (80-km-sihirbaz, pencere olayıyla).
//   · Hizmet kataloğu (A19) · Madde şablonları (A20–A22) · Sözleşme ayarları (`/v1/admin/settings` → `contracts.*`).
//
// TEK KAYNAK AKADEMİ: KM kopya tutmaz, ekran Akademi API'sinden okur/yazar (izin listeli vekil).
// ANLIK: Akademi SSE'si entegrasyon anahtarını kabul etmez. KM'de sözleşmeyi değiştiren ekran
// `CONTRACT_CHANGED_EVENT` yayınlar (liste hemen tazelenir); web/mobilden gelen değişiklikler için ekran görünürken
// ilk sayfa 20 sn'de bir sessizce yoklanır ve yalnız iz değişince yeniden çizilir (seçim korunsun diye).

import { button, h, loadStyles, pollLoop } from '../../../ui-kit/kit.js';
import { badge, tabBar } from '../../../ui-kit/layout.js';
import {
  actionButton, empty, fmtDate, money, pagedList, sectionHead, searchBox, selectBox, statusBadge, toolbar,
} from '../common.js';
import { openBuildDialog, overdueLegalNotice, truncatedNote } from './documents-build.js';
import {
  CONTRACT_CHANGED_EVENT, CONTRACT_STATUS, LIST_CHIPS, OPEN_WIZARD_EVENT, RESOLVE_MAX, SIGNATURE_SOURCES,
  SIGNATURE_STATES, academicYearOptions, contractNumber, documentsFilter, listFingerprint, listQuery, studentName,
} from './documents-logic.js';

const LIST = '/v1/admin/contracts';
const POLL_MS = 20_000;

const TABS = [
  { key: 'list', label: 'Sözleşmeler', read: 'admin_listContracts' },
  { key: 'missing', label: 'Sözleşmesi olmayanlar', read: 'admin_listStudentsWithoutContract' },
  { key: 'services', label: 'Hizmet kataloğu', read: 'admin_listContractServices' },
  { key: 'templates', label: 'Madde şablonları', read: 'admin_listContractTemplates' },
  { key: 'settings', label: 'Sözleşme ayarları', read: 'admin_getSettings' },
];

const studentCell = (student) => {
  const node = h('div', 'ak-cell-stack');
  node.append(h('b', '', studentName(student)),
    h('small', '', [student?.classGroup?.name, student?.externalRef || student?.schoolNo].filter(Boolean).join(' · ')));
  return node;
};

function signatureCell(row) {
  const node = h('div', 'ak-cell-stack');
  const state = row.status && row.status !== 'active' ? null : row.signature?.state;
  node.append(state ? statusBadge(SIGNATURE_STATES, state) : statusBadge(CONTRACT_STATUS, row.status));
  const detail = row.signature?.signedOn
    ? [fmtDate(row.signature.signedOn), SIGNATURE_SOURCES[row.signature.source] || ''].filter(Boolean).join(' · ')
    : '';
  if (detail) node.append(h('small', '', detail));
  return node;
}

/** Bir öğrencinin sözleşme sekmesini aç (80-km-sozlesme: öğrenci kartı → Sözleşmeler). */
function openStudent(app, studentId, contractId = null) {
  if (!studentId) return;
  app.go('students', { studentId, tab: 'contracts', contractId });
}

/** Sihirbazı aç (80-km-sihirbaz `OPEN_WIZARD_EVENT`'i dinler). */
export function openWizard(studentId, planId = null) {
  window.dispatchEvent(new CustomEvent(OPEN_WIZARD_EVENT, { detail: { studentId, planId: planId || null } }));
}

export function section(app) {
  loadStyles(import.meta.url, 'documents-screen.css');
  const node = h('div', 'ak-stack ak-szb');
  const visible = TABS.filter((tab) => app.can(tab.read));
  const legal = actionButton(app, 'admin_buildContractDocuments', 'Gecikenlere Yasal Uyarı', {
    title: 'Gecikmiş taksiti olan bütün öğrencilerin yasal uyarı belgesi — tek PDF, önizlemeden yazdırın.',
    onClick: () => overdueLegalNotice(app, { opener: legal }),
  });
  node.append(sectionHead('Sözleşmeler', {
    subtitle: 'Kayıt sözleşmeleri, MEB sözleşmesi, senetler, ödeme planı ve yasal uyarı. Seçtiklerinizi tek PDF '
      + 'olarak çıkarıp yazdırın; katalog, madde şablonları ve ayarlar da burada.',
    actions: visible.some((tab) => tab.key === 'list') ? [legal] : [],
  }));
  if (!visible.length) {
    node.append(empty('Sözleşmeleri görme yetkiniz yok', 'Kurum finansı izni gerekir (yöneticinizden isteyin).'));
    return { node, shown() {}, destroy() {} };
  }

  const bodies = {};
  let active = null;
  const builders = {
    list: () => listTab(app),
    missing: () => missingTab(app),
    services: () => import('./services-catalog.js').then((m) => m.servicesTab(app)),
    templates: () => import('./templates-editor.js').then((m) => m.templatesTab(app)),
    settings: () => import('./settings-contracts.js').then((m) => m.contractSettingsTab(app)),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key, (key) => showTab(key));
  const slot = h('div', 'ak-szb-slot');
  node.append(tabs.node, slot);

  async function showTab(key) {
    active = key;
    if (!bodies[key]) {
      const pending = h('div');
      bodies[key] = { node: pending, loading: true };
      slot.append(pending);
      try {
        const body = await builders[key]();
        pending.replaceWith(body.node);
        bodies[key] = body;
        body.reload?.();
      } catch (failure) {
        console.error('[bbd_akademi] sözleşme sekmesi açılamadı', failure);
        pending.replaceChildren(empty('Sekme açılamadı', String(failure?.message || failure)));
        bodies[key] = { node: pending };
      }
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== active;
    bodies[active]?.shown?.();
  }

  // Sözleşme değişti (KM'nin başka ekranı ya da sihirbaz): açık listeler tazelensin.
  const onChanged = () => {
    for (const body of Object.values(bodies)) body.changed?.();
  };
  window.addEventListener(CONTRACT_CHANGED_EVENT, onChanged);
  const poll = pollLoop({ every: POLL_MS, run: async () => {
    if (!node.isConnected || node.offsetParent === null || node.querySelector('.kit-overlay')
      || app.root.querySelector('.kit-overlay, .kit-drawer')) return;
    await bodies[active]?.poll?.();
  } });

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.tab && visible.some((tab) => tab.key === payload.tab) ? payload.tab : null;
      if (!started) {
        started = true;
        const first = wanted || visible[0].key;
        tabs.select(first, false);
        showTab(first);
        return;
      }
      if (wanted && wanted !== active) {
        tabs.select(wanted, false);
        showTab(wanted);
      } else {
        bodies[active]?.poll?.();
      }
    },
    destroy() {
      poll.stop();
      window.removeEventListener(CONTRACT_CHANGED_EVENT, onChanged);
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ------------------------------------------------------------------ liste (A1)

function listTab(app) {
  const node = h('div', 'ak-stack');
  const filters = { chip: 'all', classId: '', academicYear: '', q: '', overdueOnly: false };
  let selected = [];

  const chips = h('div', 'ak-chips ak-szb-chips');
  chips.setAttribute('role', 'group');
  chips.setAttribute('aria-label', 'İmza durumu');
  const chipButtons = new Map();
  for (const chip of LIST_CHIPS) {
    const item = h('button', 'kit-chip', chip.label);
    item.type = 'button';
    item.addEventListener('click', () => { filters.chip = chip.key; paintChips(); reload(); });
    chipButtons.set(chip.key, item);
    chips.append(item);
  }
  const overdue = h('button', 'kit-chip ak-szb-overdue', 'Gecikmesi olanlar');
  overdue.type = 'button';
  overdue.title = 'Yalnız gecikmiş taksiti olan sözleşmeler';
  overdue.addEventListener('click', () => { filters.overdueOnly = !filters.overdueOnly; paintChips(); reload(); });
  chips.append(overdue);
  function paintChips() {
    for (const [key, item] of chipButtons) {
      item.classList.toggle('on', key === filters.chip);
      item.setAttribute('aria-pressed', key === filters.chip ? 'true' : 'false');
    }
    overdue.classList.toggle('on', filters.overdueOnly);
    overdue.setAttribute('aria-pressed', filters.overdueOnly ? 'true' : 'false');
  }
  paintChips();

  const classSelect = selectBox([{ value: '', label: 'Bütün sınıflar' }], '', (value) => {
    filters.classId = value;
    reload();
  }, { label: 'Sınıf' });
  app.lookups.classGroups().then((items) => classSelect.options([{ value: '', label: 'Bütün sınıflar' },
    ...items.map((item) => ({ value: item.id, label: item.name }))])).catch(() => {});
  const years = selectBox([{ value: '', label: 'Bütün eğitim yılları' },
    ...academicYearOptions().map((year) => ({ value: year, label: year }))], '', (value) => {
    filters.academicYear = value;
    reload();
  }, { label: 'Eğitim yılı' });
  const search = searchBox({ placeholder: 'Ara: öğrenci, veli, sözleşme no', label: 'Sözleşme ara',
    onSearch: (text) => { filters.q = text; reload(); } });

  const selection = h('div', 'ak-szb-selection');
  const selectionText = h('span', 'ak-szb-selection-text');
  const build = actionButton(app, 'admin_buildContractDocuments', 'Belge çıkar', { variant: 'primary',
    title: 'Seçili sözleşmelerin (seçim yoksa süzgece uyanların) belgelerini tek PDF olarak hazırlar.',
    onClick: () => startBuild() });
  build.dataset.command = 'print';     // Ctrl/⌘+P bu ekranda "Belge çıkar"ı açar (ADR 0032)
  const clear = button('Seçimi temizle', { variant: 'ghost', onClick: () => {
    list.table.clearSelection();
    selected = [];
    paintSelection();
  } });
  selection.append(selectionText, h('span', 'kit-spacer'), clear, build);
  function paintSelection() {
    selectionText.textContent = selected.length
      ? `${selected.length} sözleşme seçili`
      : 'Belge çıkarmak için sözleşmeleri seçin ya da süzgecin tamamını basın.';
    clear.hidden = !selected.length;
    selection.classList.toggle('on', selected.length > 0);
  }
  paintSelection();

  node.append(toolbar(chips), toolbar(classSelect.node, years.node, search.node), selection);

  const list = pagedList({
    app,
    path: LIST,
    limit: 50,
    query: () => listQuery(filters),
    selectable: app.can('admin_buildContractDocuments'),
    rowKey: (row) => String(row.id),
    onSelect: (ids) => { selected = ids; paintSelection(); },
    onRow: (row) => openStudent(app, row.student?.id, row.id),
    columns: [
      { key: 'number', label: 'No', width: '110px', cell: (row) => h('span', 'ak-szb-no', contractNumber(row)) },
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => studentCell(row.student) },
      { key: 'payer', label: 'Veli (ödeme sorumlusu)', width: 'minmax(0, 1fr)',
        cell: (row) => row.payerGuardian?.name || '—' },
      { key: 'academicYear', label: 'Eğitim yılı', width: '100px', cell: (row) => row.academicYear || '—' },
      { key: 'total', label: 'Tutar', width: '120px', align: 'num', cell: (row) => money(row.totalMinor ?? 0) },
      { key: 'signature', label: 'İmza', width: '170px', cell: (row) => signatureCell(row) },
      { key: 'date', label: 'Tarih', width: '96px', cell: (row) => fmtDate(row.contractDate) },
    ],
    empty: { title: 'Sözleşme yok',
      text: 'Süzgeci değiştirin ya da "Sözleşmesi olmayanlar" sekmesinden "Kayıt ve ödeme planı" ile oluşturun.' },
  });
  node.append(list.node);

  function reload() {
    selected = [];
    paintSelection();
    return list.reload();
  }

  /** "Süzgecin tamamı" → A17 hedefi: Akademi'nin toplu süzgeci ya da (ekranda fazlası varsa) kimlik listesi. */
  async function resolveFilter() {
    const filter = documentsFilter(filters);
    if (filter) return { filter };
    const rows = await app.api.all(LIST, listQuery(filters), { limit: 100, max: RESOLVE_MAX });
    const note = truncatedNote(rows.length);
    if (note) app.toast(note, 'warn');
    return { contractIds: rows.map((row) => String(row.id)) };
  }

  function filterLabel() {
    const parts = [];
    const chip = LIST_CHIPS.find((item) => item.key === filters.chip);
    if (chip && chip.key !== 'all') parts.push(chip.label);
    if (filters.overdueOnly) parts.push('Gecikmesi olanlar');
    if (filters.classId) parts.push(classSelect.node.selectedOptions?.[0]?.textContent || 'Sınıf');
    if (filters.academicYear) parts.push(filters.academicYear);
    if (filters.q) parts.push(`“${filters.q}”`);
    return parts.length ? `Süzgeç: ${parts.join(' · ')}` : '';
  }

  function startBuild() {
    const rows = list.rows();
    openBuildDialog(app, { selectedIds: selected, filterLabel: filterLabel(),
      filterCount: rows.length, resolveFilter, opener: build });
  }

  async function poll() {
    const rows = list.rows();
    if (!rows.length && !list.node.querySelector('.kit-table, table')) return;
    const fresh = await app.api.get(LIST, { ...listQuery(filters), limit: 50 });
    if (listFingerprint(fresh?.items) !== listFingerprint(rows)) await reload();
  }

  return {
    node,
    reload,
    poll,
    changed: () => reload(),
    destroy() { list.destroy(); search.destroy(); },
  };
}

// --------------------------------------------------------- sözleşmesi olmayanlar (A2)

function missingTab(app) {
  const node = h('div', 'ak-stack');
  const canCreate = app.can('admin_createContract');
  node.append(h('p', 'ak-muted ak-szb-lead', 'Etkin öğrencilerden sözleşmesi olmayanlar. Mevcut öğrencilerin basılı '
    + 'imzalı sözleşmesini girerken sihirbazda "Basılı imzalı sözleşmesi var" işaretli gelir; yeniden imza gerekmez.'));
  const list = pagedList({
    app,
    path: `${LIST}/missing`,
    limit: 50,
    query: () => ({}),
    // Satır tıklanmaz (içinde düğme var — iç içe düğme olmaz); öğrenci kartı "Aç" ile.
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (row) => studentCell(row.student) },
      { key: 'plan', label: 'Ödeme planı', width: '130px',
        cell: (row) => (row.planId ? badge('Planı var', 'info') : badge('Plan yok', 'dim')) },
      { key: 'open', label: '', width: '90px', cell: (row) => button('Kartı aç', { variant: 'ghost',
        title: 'Öğrencinin kartında Sözleşmeler sekmesi', onClick: () => openStudent(app, row.student?.id) }) },
      { key: 'action', label: '', width: '210px', cell: (row) => {
        if (!canCreate) return '';
        return button(row.planId ? 'Sözleşme oluştur' : 'Kayıt ve ödeme planı', {
          variant: 'primary',
          title: row.planId ? 'Mevcut ödeme planından sözleşme oluşturur.'
            : 'Hizmetler, ödeme planı ve sözleşme tek ekranda; onaylayınca sözleşme kendiliğinden çıkar.',
          onClick: () => openWizard(row.student?.id, row.planId) });
      } },
    ],
    empty: { title: 'Bütün etkin öğrencilerin sözleşmesi var',
      text: 'Yeni öğrenci kaydedildiğinde "Kayıt ve ödeme planı" adımıyla sözleşmesi kendiliğinden çıkar.' },
  });
  node.append(list.node);
  return {
    node,
    reload: () => list.reload(),
    changed: () => list.reload(),
    destroy() { list.destroy(); },
  };
}
