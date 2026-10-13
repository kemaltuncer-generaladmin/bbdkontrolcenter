// BBD Akademi — Okuma yönetimi (F-A19; öğrenci tarafı F-S19).
//
// Sekmeler: Okuma listeleri · Hazır listeler · Okuma ligi · Rapor. Sekme,
// okuma işlemine yetki yoksa hiç çizilmez; yazma düğmeleri `actionButton`
// ile gerekçeli kapanır (asıl kapı vekildedir, K9). Liste, lig ve kapak işleri
// yalnız KM'dendir (`reading_lists.manage` → KM `bbd_akademi.learning_setup`).
//
// Liste düzenleyici (katalog araması, elle kalem, kapak yükleme, kalem
// kuralları) ayrı dosyadadır: `reading-editor.js`.
//
// LİG: açma/kapama kiracı bayrağıdır (`reading_league`, Ayarlar → Özellikler);
// burada kapsam ve dönemler tanımlanır, bayrak kapalıyken de hazırlanabilir.
//
// RAPOR: öğrenci × ay (en çok 12 ay), ön hesaplı özetten; imleçli sayfalanır.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, hintBox, skeletonRows, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { AkademiError } from './api.js';
import {
  GRADE_LEVELS, actionButton, confirmAction, dateInput, empty, errorBox, fmtDate, fmtDateTime,
  gradeText, kv, loader, multiChips, num, openDrawer, pagedList, problem, runAction, sectionHead,
  selectBox, staticList, todayIst, toolbar,
} from './common.js';
import { editList, entryFromCatalog, gradesText } from './reading-editor.js';

/** Sekme kaydı: anahtar → {etiket, okuma işlemi}. Sıra ekrandaki sıradır. */
const TABS = [
  { key: 'lists', label: 'Okuma listeleri', read: 'admin_listReadingLists' },
  { key: 'catalog', label: 'Hazır listeler', read: 'catalog_listReadingLists' },
  { key: 'league', label: 'Okuma ligi', read: 'admin_getReadingLeagueSettings' },
  { key: 'report', label: 'Rapor', read: 'admin_getReadingReport' },
];

/**
 * Liste türü (sözleşme `ReadingListKind`, zorunlu). `preset` sınıf düzeyi
 * hazır şablondur: öğrenci/veliye hiç dönmez, personel de varsayılan olarak
 * almaz — "hazır listeden oluştur" akışı onu `kind=preset` ile ister.
 */
const LIST_KINDS = { official: ['Resmî liste', 'info'], curated: ['Öneri listesi', 'good'],
  preset: ['Hazır şablon', 'warn'], institution: ['Kurum listesi', 'dim'] };
/** Hazır listeler sekmesindeki tür seçimi; ilk seçenek "hazır listeden oluştur" akışıdır. */
const CATALOG_KINDS = { preset: 'Hazır şablonlar (sınıf düzeyi)', official: 'Resmî listeler (ör. MEB 100 Temel Eser)',
  curated: 'Öneri listeleri (LGS / YKS)' };
const LEAGUE_SCOPES = { class_group: 'Sınıf ligi', institution: 'Kurum ligi' };
const PERIOD_KINDS = { monthly: 'Aylık (her takvim ayı bir dönem)', custom: 'Özel dönemler' };
const REPORT_SORT = { name: 'Ada göre', pages_desc: 'En çok sayfa', books_desc: 'En çok kitap' };
const MONTHS_SHORT = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
const MONTHS_LONG = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos',
  'Eylül', 'Ekim', 'Kasım', 'Aralık'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const gradeOptions = (emptyLabel) => [{ value: '', label: emptyLabel },
  ...GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) }))];
const rangeText = (from, to) => (from || to ? `${fmtDate(from)} – ${fmtDate(to)}` : 'Süresiz');
const isFeatureOff = (failure) => failure instanceof AkademiError && failure.code === 'feature_disabled';

/** 'YYYY-MM' ± ay. */
function addMonths(month, delta) {
  const [year, index] = month.split('-').map(Number);
  const date = new Date(Date.UTC(year, index - 1 + delta, 1));
  return date.toISOString().slice(0, 7);
}
function monthsBetween(from, to) {
  const [fy, fm] = from.split('-').map(Number);
  const [ty, tm] = to.split('-').map(Number);
  return (ty - fy) * 12 + (tm - fm) + 1;
}
const monthShort = (month) => {
  const [year, index] = String(month).split('-').map(Number);
  return `${MONTHS_SHORT[index - 1] || '?'} ${year}`;
};
const monthLong = (month) => {
  const [year, index] = String(month).split('-').map(Number);
  return `${MONTHS_LONG[index - 1] || '?'} ${year}`;
};

export function section(app) {
  loadStyles(import.meta.url, 'reading.css');
  const node = h('div', 'ak-stack ak-rd');
  node.append(sectionHead('Okuma', {
    subtitle: 'Kurum okuma listeleri, hazır katalog listeleri, okuma ligi ve öğrenci × ay okuma raporu.' }));

  // Okuma ligi kurumda kapalıysa sekmesi hiç çizilmez (Ayarlar → Özellikler).
  const visible = TABS.filter((tab) => app.can(tab.read)
    && (tab.key !== 'league' || app.enabled('reading_league')));
  if (!visible.length) {
    node.append(empty('Görüntülenecek okuma ekranı yok', 'Okuma sekmelerinden hiçbirine yetkiniz yok.'));
    return { node, shown() {}, destroy() {} };
  }

  const bodies = {};
  const builders = {
    lists: () => listsTab(app, { openList, editList: (current, options) => edit(current, options),
      fromCatalog: (kind) => goTab('catalog', { kind }) }),
    catalog: () => catalogTab(app, { openCatalog, copyList }),
    league: () => leagueTab(app),
    report: () => reportTab(app),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key,
    (key) => showTab(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  function showTab(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  function goTab(key, payload = null) {
    if (!visible.some((tab) => tab.key === key)) return;
    tabs.select(key, false);
    showTab(key);
    if (payload?.kind) bodies[key].setKind?.(payload.kind);
  }

  /** Kurum listesi değişti: liste sekmesi ve katalog araması tazelenir. */
  function changed() {
    app.api.invalidate('/v1/admin/reading/lists');
    app.api.invalidate('/v1/catalog/reading');
    bodies.lists?.reload();
  }

  function openList(readingListId) {
    listDrawer(app, readingListId, { onChange: changed, onEdit: (list) => edit(list) });
  }

  function openCatalog(readingListId) {
    catalogDrawer(app, readingListId, { onCopy: copyList });
  }

  async function edit(current, { prefill = null } = {}) {
    const saved = await editList(app, current, { prefill });
    if (saved) {
      changed();
      if (!current) goTab('lists');
    }
    return saved;
  }

  /** Hazır listeden kurum listesi oluştur: kalemler katalog kimliğiyle gelir, hedef seçilir. */
  async function copyList(readingListId) {
    let source;
    try {
      source = await app.api.getCached(`/v1/catalog/reading-lists/${readingListId}`, undefined,
        { ttlMs: 10 * 60_000 });
    } catch (failure) {
      app.toast(problem(failure, 'Hazır liste okunamadı.'), 'bad');
      return;
    }
    await edit(null, { prefill: {
      title: source.title,
      description: source.description || '',
      gradeLevels: source.gradeLevels || [],
      startDate: source.startDate || '',
      endDate: source.endDate || '',
      items: (source.items || []).map(entryFromCatalog),
    } });
  }

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
      } else if (wanted) {
        goTab(wanted);
      }
      const listId = payload?.readingListId;
      if (listId) {
        if (UUID.test(listId) && app.can('admin_getReadingList')) openList(listId);
        else openCatalog(listId);
      }
    },
    destroy() {
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

/** Kapalı özellik → sebebini söyleyen durum + (yetki varsa) Ayarlar'a geçiş. */
function featureOffBox(app, text) {
  const box = h('div', 'ak-stack');
  box.append(alertBox(text, 'warn'));
  if (app.has('settings')) {
    box.append(toolbar(button('Özellik ayarlarına git', { variant: 'ghost',
      onClick: () => app.go('settings', { tab: 'features' }) })));
  }
  return box;
}

// ------------------------------------------------------------ kurum listeleri

function listsTab(app, { openList, editList, fromCatalog }) {
  const node = h('div', 'ak-stack');
  const filters = { gradeLevel: '' };
  const grade = selectBox(gradeOptions('Tüm seviyeler'), '', (value) => {
    filters.gradeLevel = value;
    list.reload();
  }, { label: 'Seviye' });
  const add = actionButton(app, 'admin_createReadingList', 'Yeni liste', { variant: 'primary',
    onClick: () => editList(null) });
  const fromPreset = app.can('catalog_listReadingLists')
    ? actionButton(app, 'admin_createReadingList', 'Hazır listeden oluştur', { variant: 'ghost',
      onClick: () => fromCatalog('preset') }) : null;
  node.append(toolbar(grade.node,
    h('span', 'ak-muted', 'Seviye bazlı listeler öğrencinin uygulamasında okuma rafına eklenebilir.'),
    h('span', 'kit-spacer'), fromPreset, add));

  const list = pagedList({
    app,
    path: '/v1/admin/reading/lists',
    query: () => ({ gradeLevel: filters.gradeLevel || undefined }),
    columns: [
      { key: 'title', label: 'Liste', width: 'minmax(0, 2.2fr)', cell: (row) => titleCell(row) },
      { key: 'kind', label: 'Tür', width: '130px', cell: (row) => kindBadge(row.kind) },
      { key: 'grades', label: 'Seviye', width: 'minmax(0, 1.2fr)', cell: (row) => gradesText(row.gradeLevels) },
      { key: 'books', label: 'Kitap', align: 'num', width: '80px', cell: (row) => num(row.bookCount) },
      { key: 'range', label: 'Okuma dönemi', width: '190px', cell: (row) => rangeText(row.startDate, row.endDate) },
      { key: 'updated', label: 'Güncellendi', width: '140px', cell: (row) => fmtDateTime(row.updatedAt) },
    ],
    onRow: (row) => openList(row.id),
    empty: { title: 'Henüz kurum okuma listesi yok',
      text: '“Yeni liste” ile oluşturun ya da “Hazır listeden oluştur” ile bir şablondan başlayın.' },
  });
  node.append(list.node);
  return { node, reload: () => list.reload(), destroy() { list.destroy(); } };
}

const kindBadge = (kind) => (LIST_KINDS[kind] ? badge(LIST_KINDS[kind][0], LIST_KINDS[kind][1]) : '—');

function titleCell(row) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('b', '', row.title || '—'));
  if (row.description) cell.append(h('small', '', row.description));
  return cell;
}

function listDrawer(app, readingListId, { onChange, onEdit }) {
  const panel = openDrawer(app, { title: 'Okuma listesi', subtitle: 'Kurum listesi' });
  panel.node.classList.add('ak-rd-drawer');
  const view = loader(() => Promise.all([
    app.api.getCached(`/v1/admin/reading/lists/${readingListId}`, undefined, { ttlMs: 15_000 }),
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
  ]), ([list, classes, groups]) => {
    panel.setTitle(list.title || 'Okuma listesi');
    const names = (ids, items) => (ids || []).map((id) => items.find((item) => item.id === id)?.name || 'Bilinmeyen')
      .join(', ');
    const tools = toolbar(
      actionButton(app, 'admin_updateReadingList', 'Düzenle', { variant: 'primary',
        onClick: async () => {
          const saved = await onEdit(list);
          if (saved) view.reload();
        } }),
      actionButton(app, 'admin_deleteReadingList', 'Sil', { variant: 'ghost',
        onClick: () => remove(list) }),
    );
    const info = card('', kv([
      ['Seviye', gradesText(list.gradeLevels)],
      ['Hedef sınıflar', names(list.classGroupIds, classes) || 'Seviyedeki tüm sınıflar'],
      ['Çalışma grupları', names(list.studentGroupIds, groups) || '—'],
      ['Okuma dönemi', rangeText(list.startDate, list.endDate)],
      ['Ulaşılan öğrenci', num(list.studentReach)],
      ['Açıklama', list.description || '—'],
      ['Oluşturuldu', fmtDateTime(list.createdAt)],
      ['Güncellendi', `${fmtDateTime(list.updatedAt)} · sürüm ${num(list.version)}`],
    ]));
    const items = dataTable({
      columns: bookColumns(),
      rows: list.items || [],
      dense: true,
      empty: empty('Listede kitap yok', 'Düzenle ile kitap ekleyin.'),
    });
    return [tools, info, card(`Kitaplar (${num((list.items || []).length)})`, items.node)];
  }, { rows: 6, columns: 4 });
  panel.body.append(view.node);
  view.reload();

  async function remove(list) {
    const ok = await confirmAction(app, {
      title: `“${list.title}” listesini sil`,
      description: 'Liste öğrencilerin uygulamasından kalkar. Öğrencilerin rafına eklenmiş kitaplar ve '
        + 'okuma kayıtları silinmez.',
      confirmLabel: 'Listeyi sil',
      danger: true,
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/reading/lists/${list.id}`),
      { success: 'Okuma listesi silindi.' });
    if (done !== undefined) {
      panel.close();
      onChange?.();
    }
  }
  return panel;
}

function bookColumns() {
  return [
    { key: 'title', label: 'Kitap', width: 'minmax(0, 2fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', '', row.title || '—'), h('small', '', row.author || 'Yazar belirtilmemiş'));
      return cell;
    } },
    { key: 'pages', label: 'Sayfa', align: 'num', width: '80px', cell: (row) => (row.pageCount ? num(row.pageCount) : '—') },
    { key: 'isbn', label: 'ISBN', width: '130px', cell: (row) => row.isbn || '—' },
    { key: 'grades', label: 'Seviye', width: 'minmax(0, 1fr)', cell: (row) => ((row.gradeLevels || []).length
      ? gradesText(row.gradeLevels) : '—') },
    { key: 'cover', label: 'Kapak', width: '80px', cell: (row) => (row.coverUrl ? badge('Var', 'good') : badge('Yok', 'dim')) },
  ];
}

// --------------------------------------------------------------- hazır listeler

function catalogTab(app, { openCatalog, copyList }) {
  const node = h('div', 'ak-stack');
  const filters = { gradeLevel: '', kind: 'preset' };
  const grade = selectBox(gradeOptions('Tüm seviyeler'), '', (value) => {
    filters.gradeLevel = value;
    list.reload();
  }, { label: 'Seviye' });
  const kind = selectBox(Object.entries(CATALOG_KINDS).map(([value, label]) => ({ value, label })), filters.kind,
    (value) => { filters.kind = value; list.reload(); }, { label: 'Tür' });
  node.append(toolbar(kind.node, grade.node, h('span', 'ak-muted',
    'Platformun hazır listeleri. Bir listeden kurum listesi oluşturup hedef sınıfları seçebilirsiniz; '
    + 'hazır şablonlar öğrenci ve veliye ayrıca gösterilmez.')));

  // `kind` her istekte açıkça gönderilir: `preset` yalnız istenince döner (sözleşme §14.13).
  const list = staticList({
    load: () => app.api.getCached('/v1/catalog/reading-lists',
      { source: 'platform', kind: filters.kind, gradeLevel: filters.gradeLevel || undefined }, { ttlMs: 10 * 60_000 }),
    columns: [
      { key: 'title', label: 'Liste', width: 'minmax(0, 2.2fr)', cell: (row) => titleCell(row) },
      { key: 'kind', label: 'Tür', width: '130px', cell: (row) => kindBadge(row.kind) },
      { key: 'grades', label: 'Seviye', width: 'minmax(0, 1.2fr)', cell: (row) => gradesText(row.gradeLevels) },
      { key: 'books', label: 'Kitap', align: 'num', width: '80px', cell: (row) => num(row.bookCount) },
      { key: 'updated', label: 'Güncellendi', width: '120px', cell: (row) => fmtDate(row.updatedAt) },
      { key: 'actions', label: '', width: '210px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(
          button('İncele', { variant: 'ghost', onClick: () => openCatalog(row.id) }),
          actionButton(app, 'admin_createReadingList', 'Bu listeden oluştur', { variant: 'ghost',
            onClick: () => copyList(row.id) }),
        );
        return actions;
      } },
    ],
    // Satırda düğme var: satır tıklaması yok (kitte `onRow` satırı <button> yapar).
    empty: { title: 'Bu süzgece uyan hazır liste yok', text: 'Başka bir tür ya da seviye seçin.' },
  });
  node.append(list.node);
  return {
    node,
    reload: () => list.reload(),
    setKind(value) {
      if (!CATALOG_KINDS[value] || value === filters.kind) return;
      filters.kind = value;
      kind.set(value);
      list.reload();
    },
  };
}

function catalogDrawer(app, readingListId, { onCopy }) {
  const panel = openDrawer(app, { title: 'Hazır liste', subtitle: 'Platform kataloğu' });
  panel.node.classList.add('ak-rd-drawer');
  const view = loader(() => app.api.getCached(`/v1/catalog/reading-lists/${readingListId}`, undefined,
    { ttlMs: 10 * 60_000 }), (list) => {
    panel.setTitle(list.title || 'Hazır liste');
    const nodes = [];
    if (list.source === 'platform') {
      nodes.push(toolbar(actionButton(app, 'admin_createReadingList', 'Bu listeden oluştur', { variant: 'primary',
        onClick: () => { panel.close(); onCopy(list.id); } })));
    }
    nodes.push(card('', kv([
      ['Kaynak', list.source === 'platform' ? 'Platform kataloğu' : 'Kurum listesi'],
      ['Tür', kindBadge(list.kind)],
      ['Seviye', gradesText(list.gradeLevels)],
      ['Okuma dönemi', rangeText(list.startDate, list.endDate)],
      ['Açıklama', list.description || '—'],
      ['Güncellendi', fmtDateTime(list.updatedAt)],
    ])));
    const table = dataTable({ columns: bookColumns(), rows: list.items || [], dense: true,
      empty: empty('Listede kitap yok') });
    nodes.push(card(`Kitaplar (${num((list.items || []).length)})`, table.node));
    return nodes;
  }, { rows: 6, columns: 4 });
  panel.body.append(view.node);
  view.reload();
  return panel;
}

// ------------------------------------------------------------------ okuma ligi

function leagueTab(app) {
  const node = h('div', 'ak-stack');
  const dispose = [];
  const cleanup = () => { for (const fn of dispose.splice(0)) fn(); };

  const view = loader(async () => {
    try {
      return await app.api.getCached('/v1/admin/reading/league-settings', undefined, { ttlMs: 30_000 });
    } catch (failure) {
      if (isFeatureOff(failure)) return { featureOff: true };
      throw failure;
    }
  }, (settings) => {
    cleanup();
    if (settings.featureOff) {
      return featureOffBox(app, 'Okuma ligi kurumda kapalı. '
        + 'Özellik açılınca kapsam ve dönemler buradan tanımlanır.');
    }
    return leagueForm(settings);
  }, { rows: 4, columns: 3 });
  node.append(view.node);

  function leagueForm(settings) {
    const state = {
      scopes: [...(settings.scopes || [])],
      periodKind: settings.periodKind || 'monthly',
      minParticipants: settings.minParticipants ?? 5,
      periods: (settings.periods || []).map((period) => ({ ...period })),
    };
    const nodes = [];
    if (settings.enabled) {
      nodes.push(alertBox('Okuma ligi açık: öğrenciler seçilen kapsamda dönemlik sayfa sıralamasını görür.', 'good'));
    } else {
      nodes.push(featureOffBox(app, 'Okuma ligi şu an kapalı. Ayarları şimdiden '
        + 'hazırlayabilirsiniz; öğrenciler sıralamayı özellik açılınca görür.'));
    }

    const scopes = multiChips(Object.entries(LEAGUE_SCOPES).map(([value, label]) => ({ value, label })),
      state.scopes, (list) => { state.scopes = list; });
    const kind = selectBox(Object.entries(PERIOD_KINDS).map(([value, label]) => ({ value, label })),
      state.periodKind, (next) => { state.periodKind = next; paintPeriods(); }, { label: 'Dönem türü' });
    const minimum = h('input', 'kit-input ak-rd-min');
    minimum.type = 'text';
    minimum.inputMode = 'numeric';
    minimum.maxLength = 2;
    minimum.value = String(state.minParticipants);
    minimum.setAttribute('aria-label', 'En az katılımcı');

    const periodsSlot = h('div', 'ak-stack');
    let periodDisposers = [];
    dispose.push(() => { for (const fn of periodDisposers.splice(0)) fn(); });
    function paintPeriods() {
      for (const fn of periodDisposers.splice(0)) fn();
      periodsSlot.replaceChildren();
      if (state.periodKind !== 'custom') {
        periodsSlot.append(hintBox('Her takvim ayı ayrı bir dönemdir; ay başında sıralama sıfırlanır.'));
        return;
      }
      if (!state.periods.length) periodsSlot.append(h('div', 'ak-muted', 'Henüz dönem yok. “Dönem ekle” ile başlayın.'));
      state.periods.forEach((period, index) => {
        const row = h('div', 'ak-rd-period');
        const name = h('input', 'kit-input');
        name.type = 'text';
        name.maxLength = 60;
        name.placeholder = 'Dönem adı (ör. Ekim okuma ayı)';
        name.value = period.name || '';
        name.setAttribute('aria-label', 'Dönem adı');
        name.addEventListener('input', () => { period.name = name.value; });
        const from = dateInput(period.from || '', (iso) => { period.from = iso; }, 'Dönem başlangıcı');
        const to = dateInput(period.to || '', (iso) => { period.to = iso; }, 'Dönem bitişi');
        periodDisposers.push(() => from.destroy(), () => to.destroy());
        const drop = button('Kaldır', { variant: 'ghost', onClick: () => {
          state.periods.splice(index, 1);
          paintPeriods();
        } });
        row.append(name, from.node, h('span', 'ak-muted', '–'), to.node, drop);
        periodsSlot.append(row);
      });
      periodsSlot.append(toolbar(button('Dönem ekle', { variant: 'ghost', onClick: () => {
        const last = state.periods[state.periods.length - 1];
        state.periods.push({ id: null, name: '', from: last?.to || todayIst(), to: '' });
        paintPeriods();
      } })));
    }
    paintPeriods();

    const form = h('div', 'ak-stack ak-card-pad');
    const line = (label, control, hint = '') => {
      const wrap = h('div', 'ak-rd-line');
      const text = h('div', 'ak-cell-stack');
      text.append(h('b', '', label));
      if (hint) text.append(h('small', '', hint));
      wrap.append(text, control);
      return wrap;
    };
    const save = actionButton(app, 'admin_putReadingLeagueSettings', 'Ayarları kaydet', { variant: 'primary',
      onClick: () => submit() });
    form.append(
      line('Kapsam', scopes.node, 'Sınıf ligi sınıf içinde, kurum ligi bütün kurumda sıralar.'),
      line('Dönem türü', kind.node),
      periodsSlot,
      line('En az katılımcı', minimum, 'Bundan az katılımcı olan ligde sıralama gösterilmez (3–50).'),
      toolbar(save, h('span', 'ak-muted', settings.updatedAt ? `Son değişiklik: ${fmtDateTime(settings.updatedAt)}` : '')),
    );
    nodes.push(card('Lig ayarları', form));

    async function submit() {
      const min = Number(minimum.value.trim());
      const problems = [];
      if (!state.scopes.length) problems.push('En az bir kapsam seçin.');
      if (!Number.isInteger(min) || min < 3 || min > 50) problems.push('En az katılımcı 3–50 arasında olmalı.');
      const periods = state.periodKind === 'custom' ? state.periods.map((period) => ({
        id: period.id || null, name: String(period.name || '').trim(), from: period.from, to: period.to })) : [];
      if (state.periodKind === 'custom') {
        if (!periods.length) problems.push('Özel dönem türünde en az bir dönem tanımlayın.');
        periods.forEach((period, index) => {
          if (!period.name) problems.push(`${index + 1}. dönemin adı boş.`);
          if (!period.from || !period.to) problems.push(`${index + 1}. dönemin tarihleri eksik.`);
          else if (period.to < period.from) problems.push(`${index + 1}. dönemin bitişi başlangıcından önce.`);
        });
        const sorted = periods.filter((period) => period.from && period.to)
          .sort((a, b) => a.from.localeCompare(b.from));
        for (let index = 1; index < sorted.length; index += 1) {
          if (sorted[index].from <= sorted[index - 1].to) {
            problems.push(`“${sorted[index - 1].name || 'Adsız'}” ile “${sorted[index].name || 'Adsız'}” dönemleri çakışıyor.`);
          }
        }
      }
      if (problems.length) {
        app.toast(problems[0], 'bad');
        return;
      }
      const result = await runAction(app, () => app.api.put('/v1/admin/reading/league-settings', {
        scopes: state.scopes,
        periodKind: state.periodKind,
        periods,
        minParticipants: min,
      }), { success: 'Okuma ligi ayarları kaydedildi.', button: save });
      if (result) {
        app.api.invalidate('/v1/admin/reading/league-settings');
        view.reload();
      }
    }
    return nodes;
  }

  return { node, reload: () => view.reload(), destroy: cleanup };
}

// ------------------------------------------------------------------- rapor

function reportTab(app) {
  const node = h('div', 'ak-stack');
  const current = todayIst().slice(0, 7);
  const state = { classGroupId: '', studentGroupId: '', fromMonth: addMonths(current, -2), toMonth: current, sort: 'name' };
  const monthOptions = Array.from({ length: 24 }, (_, index) => {
    const month = addMonths(current, -index);
    return { value: month, label: monthLong(month) };
  });

  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    state.classGroupId = value; reload();
  }, { label: 'Sınıf' });
  const groupSelect = selectBox([{ value: '', label: 'Tüm gruplar' }], '', (value) => {
    state.studentGroupId = value; reload();
  }, { label: 'Çalışma grubu' });
  app.lookups.options('classGroups', { empty: 'Tüm sınıflar' }).then((list) => classSelect.options(list)).catch(() => {});
  app.lookups.options('studentGroups', { empty: 'Tüm gruplar' }).then((list) => groupSelect.options(list)).catch(() => {});
  const fromSelect = selectBox(monthOptions, state.fromMonth, (value) => { state.fromMonth = value; reload(); },
    { label: 'Başlangıç ayı' });
  const toSelect = selectBox(monthOptions, state.toMonth, (value) => { state.toMonth = value; reload(); },
    { label: 'Bitiş ayı' });
  const sortSelect = selectBox(Object.entries(REPORT_SORT).map(([value, label]) => ({ value, label })),
    state.sort, (value) => { state.sort = value; reload(); }, { label: 'Sıralama' });
  node.append(toolbar(classSelect.node, groupSelect.node, h('span', 'ak-muted', 'Aylar'), fromSelect.node,
    h('span', 'ak-muted', '–'), toSelect.node, sortSelect.node));

  const meta = h('div', 'ak-muted ak-rd-meta');
  const slot = h('div', 'ak-rd-report');
  const status = h('span', 'ak-list-count');
  const more = button('Daha fazla', { variant: 'ghost', onClick: () => loadPage() });
  const foot = h('div', 'ak-list-foot');
  foot.append(status, h('span', 'kit-spacer'), more);
  const sentinel = h('div', 'ak-sentinel');
  node.append(meta, slot, foot, sentinel);

  let rows = [];
  let cursor = null;
  let done = true;
  let busy = false;
  let token = 0;
  let table = null;
  const observer = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((list) => {
      if (list.some((entry) => entry.isIntersecting) && !done && !busy && rows.length) loadPage();
    }) : null;
  observer?.observe(sentinel);

  function paintFoot() {
    more.hidden = done;
    more.disabled = busy;
    status.textContent = rows.length ? `${num(rows.length)} öğrenci${done ? '' : '+'}` : '';
  }

  function buildTable(months) {
    const columns = [
      { key: 'student', label: 'Öğrenci', width: 'minmax(180px, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.student?.displayName || '—'),
          h('small', '', [row.student?.externalRef, row.student?.classGroup?.name].filter(Boolean).join(' · ')));
        return cell;
      } },
      ...months.map((month) => ({ key: `m-${month}`, label: monthShort(month), align: 'num', width: '96px',
        cell: (row) => monthCell(row.byMonth[month]) })),
      { key: 'pages', label: 'Toplam sayfa', align: 'num', width: '110px', cell: (row) => num(row.totalPages) },
      { key: 'books', label: 'Biten kitap', align: 'num', width: '100px', cell: (row) => num(row.totalBooks) },
    ];
    table = dataTable({
      columns,
      rows: [],
      onRow: (row) => row.student?.id && app.go('students', { studentId: row.student.id, tab: 'learning' }),
      empty: empty('Bu süzgece uyan okuma kaydı yok', 'Ay aralığını ya da sınıfı değiştirin.'),
    });
    table.node.style.minWidth = `${180 + months.length * 96 + 210}px`;
    slot.replaceChildren(table.node);
  }

  function monthCell(cell) {
    if (!cell || (!cell.pages && !cell.booksFinished && !cell.minutes)) return h('span', 'ak-muted', '—');
    const node = h('div', 'ak-cell-stack ak-rd-month');
    node.append(h('span', '', `${num(cell.pages)} s`),
      h('small', '', [cell.booksFinished ? `${num(cell.booksFinished)} kitap` : '', cell.minutes ? `${num(cell.minutes)} dk` : '']
        .filter(Boolean).join(' · ')));
    node.title = `${num(cell.pages)} sayfa · ${num(cell.minutes)} dakika · ${num(cell.booksFinished)} kitap bitti`;
    return node;
  }

  async function loadPage() {
    if (done || busy) return;
    busy = true;
    paintFoot();
    const mine = token;
    try {
      const data = await app.api.get('/v1/admin/reading/report', {
        classGroupId: state.classGroupId || undefined,
        studentGroupId: state.studentGroupId || undefined,
        fromMonth: state.fromMonth,
        toMonth: state.toMonth,
        sort: state.sort,
        limit: 50,
        cursor: cursor || undefined,
      });
      if (mine !== token) return;
      if (!table) buildTable(data?.months || []);
      meta.textContent = data?.computedAt
        ? `${monthLong(state.fromMonth)} – ${monthLong(state.toMonth)} · özet ${fmtDateTime(data.computedAt)} itibarıyla`
        : '';
      const page = (data?.items || []).map((row) => ({ ...row,
        byMonth: Object.fromEntries((row.cells || []).map((cell) => [cell.month, cell])) }));
      rows = rows.concat(page);
      table.update({ rows });
      cursor = data?.nextCursor || null;
      done = !cursor;
    } catch (failure) {
      if (mine !== token) return;
      done = true;
      slot.replaceChildren(errorBox(failure, () => reload()));
    } finally {
      if (mine === token) busy = false;
      paintFoot();
    }
  }

  function reload() {
    token += 1;
    rows = [];
    cursor = null;
    table = null;
    busy = false;
    if (state.fromMonth > state.toMonth) {
      done = true;
      slot.replaceChildren(alertBox('Başlangıç ayı bitiş ayından sonra olamaz.', 'warn'));
      meta.textContent = '';
      paintFoot();
      return Promise.resolve();
    }
    if (monthsBetween(state.fromMonth, state.toMonth) > 12) {
      done = true;
      slot.replaceChildren(alertBox('Rapor en çok 12 ay kapsayabilir; aralığı daraltın.', 'warn'));
      meta.textContent = '';
      paintFoot();
      return Promise.resolve();
    }
    done = false;
    slot.replaceChildren(skeletonRows(6, 5));
    return loadPage();
  }

  return { node, reload, destroy() { observer?.disconnect(); token += 1; } };
}
