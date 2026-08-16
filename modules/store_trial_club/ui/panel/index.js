// Deneme Kulübü paneli — denemeler, kontenjan, katılımcılar, sonuç yükleme.
//
// NE YAPAR: KPI şeridi + dört sekme (Denemeler · Katılımcılar · Sonuçlar ·
// Ayarlar); her denemede dolu/toplam kontenjan çubuğu; kurumdan gelen sonuç
// CSV'sini yükleyip ÖNCE EŞLEŞTİRME ÖNİZLEMESİ gösterir, sonra gerekçeli onayla
// yazar; yoklama çizelgesi ve kişi başı sonuç karnesi PDF üretip yazdırır;
// seçili kitleye toplu bildirim gönderir (önce kaç kişi/kaç SMS önizlemesi).
//
// NE YAPMAZ:
//  · Katılımcı EKLEMEZ/ÇIKARMAZ. Düğmeler kapalı ve nedeni yazılı; sessizce
//    404 alıp patlamıyoruz. İKİ DÜĞMENİN NEDENİ AYNI DEĞİL: "Katılımcı ekle"
//    için mağazada uç hiç yok; "Kaydı iptal et" için uç 2026-08-16 itibarıyla
//    YAYINDA ama geçitte (store.api) karşılığı yok. Metinler `features()`ten
//    gelir, burada sabitlenmez — eskiyip yalan söylemesin.
//  · Sonucu "yükle" ile "yayınla" AYNI ŞEY DEĞİLDİR. Yükleme katılımcıya
//    görünmez; yayınlama ayrı izin ve ayrı onay ister.
//  · Kimseyi silmez. Kayıt iptali de geçit metodu gelince pasifleştirme
//    olacak (mağaza tarafı zaten böyle davranıyor: sipariş "iptal" durumuna
//    çekiliyor, satır silinmiyor).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Aynı ada sahip iki katılımcı varsa satır "belirsiz" işaretlenir ve
//    HİÇBİRİ seçilmez — kardeşlerin sonucunu birbirine yazmamak için.
//  · CSV virgülle ayrılmışsa Türkçe ondalık (12,5) bölünmüş olabilir; ekran
//    bunu uyarı olarak söyler.
//  · Kontenjan kayıtlı sayısının altına çekilemez; backend de reddeder.
//  · Telefonu bozuk katılımcı "gönderildi" sayılmaz, önizlemede ayrı görünür.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_trial_club/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_trial_club/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  blockedButton, button, clip, confirmWithReason, csvBlob, h, loadStyles, money, num,
  toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, progress, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_trial_club';

const CAPACITY_TONES = {
  empty: 'dim', filling: '', near: 'warn', full: 'bad', over: 'bad', unlimited: 'info',
};
const CAPACITY_LABELS = {
  empty: 'Boş', filling: 'Doluyor', near: 'Dolmak üzere', full: 'Dolu',
  over: 'Kontenjan aşıldı', unlimited: 'Sınırsız',
};
const REGISTRATION_LABELS = {
  not_started: 'Kayıt başlamadı', open: 'Kayıt açık', closed: 'Kayıt kapandı', full: 'Kontenjan dolu',
};
const RESULT_LABELS = { none: 'Bekliyor', pending: 'Sonuç yüklenmedi', published: 'Yayınlandı' };
const RESULT_TONES = { none: 'dim', pending: 'warn', published: 'good' };

const MATCH_LABELS = {
  matched: 'Eşleşti', unmatched: 'Bulunamadı', ambiguous: 'Belirsiz',
  duplicate: 'Mükerrer', empty: 'Sonuç boş',
};
const MATCH_TONES = {
  matched: 'good', unmatched: 'bad', ambiguous: 'warn', duplicate: 'warn', empty: 'dim',
};

const EMPTY_STATE = {
  exams: [], members: [], results: [], stats: {},
  total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', tab: 'exams', examId: 0,
  features: {}, settings: null, loaded: false, examsTruncated: false,
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];        // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

// ------------------------------------------------------------------ araçlar

/** Sunucu yanıtı `{ok:false, error}` de dönebilir; ikisi de tek yerde okunur. */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) throw new Error(result.error);
  return result;
}

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.status?.set(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    nodes.status?.set(error.message || 'İşlem başarısız.', true);
    return null;
  } finally {
    busy = false;
  }
}

/** Yıkıcı ve para harcayan her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function feature(key) {
  return state.features?.[key] || { available: false, reason: 'Durum bilinmiyor.' };
}

/** Kapalı bir özelliğin düğmesi: tıklanamaz ama NEDENİ `title`'da yazar. */
function pendingButton(label, key) {
  const info = feature(key);
  return button(label, { disabled: true, title: info.reason });
}

function dropFilters() {
  nodes.filters?.destroy();
  nodes.filters = null;
}

function dropForms() {
  (nodes.forms || []).forEach((form) => form.destroy());
  nodes.forms = [];
}

/** Boş/çözülemeyen sayı `null` döner: "değiştirme" ile "sıfır yap" ayrı şeyler. */
function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function examName(examId) {
  return state.exams.find((row) => row.id === Number(examId))?.name || `#${examId}`;
}

// -------------------------------------------------------------------- veri

async function loadOverview() {
  let payload;
  try {
    payload = await api(`${BASE}/overview`);
  } catch (error) {
    nodes.kpi.replaceChildren(alertBox(`KPI okunamadı — ${error.message}`, 'warn'));
    return;
  }
  state.features = payload.features || {};
  const tiles = payload.tiles || {};
  const show = (value) => (value === null || value === undefined ? '—' : num(value));
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Deneme', value: show(tiles.exams) },
    { label: 'Kaydı açık', value: show(tiles.openExams), tone: 'good' },
    { label: 'Yaklaşan', value: show(tiles.upcoming) },
    { label: 'Kontenjan', value: show(tiles.seats) },
    {
      label: 'Dolu',
      value: show(tiles.taken),
      tone: (tiles.fillPercent || 0) >= 90 ? 'warn' : '',
      title: tiles.fillPercent === null || tiles.fillPercent === undefined
        ? 'Kontenjanı sınırlı deneme yok.'
        : `Doluluk %${tiles.fillPercent}`,
    },
    { label: 'Katılımcı', value: show(tiles.members) },
    { label: 'Sonucu bekleyen', value: show(tiles.pendingResults), tone: 'warn' },
  ]));
  if (!payload.connected) {
    nodes.kpi.append(alertBox(`Mağazaya ulaşılamadı — ${payload.error}`, 'warn'));
  }
}

/** Yerel tercihler. Ayarlar sekmesi AÇILMADAN da gerekir: bildirim kanalı,
 *  toplu şablon ve tek kişilik hatırlatma şablonu buradan geliyor. Yalnız
 *  sekme açılınca çekildiğinde ayarlanan varsayılanlar hiç uygulanmıyordu —
 *  kullanıcı değeri yazıyor, form boş açılıyordu. */
async function loadSettings() {
  const payload = await call(`${BASE}/settings`);
  state.settings = payload;
  state.features = payload.features || state.features;
  return payload;
}

async function loadExams() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.status) params.set('status', values.status);
  if (values.results) params.set('results', values.results);

  let payload;
  try {
    payload = await api(`${BASE}/exams?${params.toString()}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, exams: [] };
    return;
  }
  state.exams = payload.items || [];
  state.connected = Boolean(payload.connected);
  state.error = payload.error || '';
  state.features = payload.features || state.features;
  state.examsTruncated = Boolean(payload.truncated);
  state.loaded = true;
}

async function loadMembers({ page = state.page, size = state.size } = {}) {
  const values = nodes.filters ? nodes.filters.values() : {};
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  for (const [key, name] of [['q', 'q'], ['exam', 'examId'], ['payment', 'payment'],
    ['attendance', 'attendance'], ['city', 'city'], ['grade', 'grade']]) {
    if (values[key]) params.set(name, String(values[key]));
  }
  let payload;
  try {
    payload = await api(`${BASE}/members?${params.toString()}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, members: [], total: 0 };
    return;
  }
  state.members = payload.items || [];
  state.total = payload.total || 0;
  state.page = payload.page || page;
  state.size = payload.size || size;
  state.pages = payload.pages || 0;
  state.connected = Boolean(payload.connected);
  state.error = payload.error || '';
}

async function loadResults(examId, { page = 1, size = state.size } = {}) {
  let payload;
  try {
    payload = await api(`${BASE}/exams/${examId}/results?page=${page}&size=${size}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, results: [], stats: {} };
    return;
  }
  state.results = payload.items || [];
  state.stats = payload.stats || {};
  state.total = payload.total || 0;
  state.page = payload.page || page;
  state.pages = payload.pages || 0;
  state.connected = Boolean(payload.connected);
  state.error = payload.error || '';
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  if (state.tab === 'exams') return `Bağlı · ${num(state.exams.length)} deneme`;
  if (state.tab === 'members') {
    return `Bağlı · ${num(state.total)} katılımcı · sayfa ${state.page}/${Math.max(1, state.pages)}`;
  }
  if (state.tab === 'results') {
    return state.examId
      ? `Bağlı · ${examName(state.examId)} · ${num(state.total)} sonuç`
      : 'Sonuçları görmek için bir deneme seçin.';
  }
  return 'Bağlı';
}

// ================================================================ Denemeler

function capacityCell(row) {
  const box = h('div', 'tc-cap');
  const view = row.capacity;
  if (view.state === 'unlimited') {
    // Renk tek başına anlam taşımaz: rozetin yanında sayı durur.
    box.append(h('b', undefined, num(view.enrolled)), badge('Sınırsız', 'info'));
    return box;
  }
  const bar = progress();
  bar.percent(Math.min(100, view.percent || 0), `${num(view.enrolled)}/${num(view.capacity)}`);
  bar.node.classList.add(`tone-${view.state}`);
  box.append(bar.node, badge(CAPACITY_LABELS[view.state] || view.state,
    CAPACITY_TONES[view.state] || ''));
  return box;
}

const EXAM_COLUMNS = [
  {
    key: 'name',
    label: 'Deneme',
    width: 'minmax(0, 2.4fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'tc-name');
      box.append(clip(h('b'), row.name, 44),
        h('span', 'tc-sub', [row.grade, row.venue].filter(Boolean).join(' · ') || 'sınıf belirtilmemiş'));
      return box;
    },
  },
  {
    key: 'examDate',
    label: 'Tarih',
    width: '130px',
    sortable: true,
    cell: (row) => (row.examDate ? `${row.examDate}${row.examTime ? ` ${row.examTime}` : ''}` : '—'),
  },
  { key: 'capacity', label: 'Kontenjan', width: '190px', cell: capacityCell },
  {
    key: 'feeKurus',
    label: 'Ücret',
    width: '110px',
    align: 'num',
    cell: (row) => (row.feeKurus === null || row.feeKurus === undefined
      ? '—' : (row.feeKurus === 0 ? 'Ücretsiz' : money(row.feeKurus))),
  },
  {
    key: 'registrationState',
    label: 'Kayıt',
    width: '140px',
    cell: (row) => {
      const box = h('span', 'tc-stack');
      box.append(badge(REGISTRATION_LABELS[row.registrationState] || '—',
        row.registrationState === 'open' ? 'good' : 'dim'));
      if (row.registrationEnd) box.append(h('span', 'tc-sub', `son ${row.registrationEnd}`));
      return box;
    },
  },
  {
    key: 'resultsState',
    label: 'Sonuç',
    width: '130px',
    cell: (row) => badge(RESULT_LABELS[row.resultsState] || '—',
      RESULT_TONES[row.resultsState] || ''),
  },
];

function paintExams(host) {
  dropFilters();
  host.replaceChildren();

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Deneme adı ya da sınıf ara', width: '250px' },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Tümü' },
        { value: 'open', label: 'Kayıt açık' },
        { value: 'closed', label: 'Kayıt kapandı' },
        { value: 'full', label: 'Kontenjan dolu' },
        { value: 'done', label: 'Tamamlandı' },
        { value: 'cancelled', label: 'İptal' },
      ] },
      { kind: 'select', key: 'results', label: 'Sonuç', options: [
        { value: '', label: 'Tümü' },
        { value: 'pending', label: 'Sonuç yüklenmedi' },
        { value: 'published', label: 'Yayınlandı' },
        { value: 'none', label: 'Bekliyor' },
      ] },
      { kind: 'dateRange', key: 'range', label: 'Sınav tarihi' },
    ],
    onChange: () => refreshExams(),
    actions: [
      button('Yenile', { onClick: () => { refreshExams(); loadOverview(); } }),
      // YENİ DENEME AÇILAMAZ: mağazada kulüp TEK bir üyelik ürünü ve yazma
      // ucu hangi fiille çağrılırsa çağrılsın aynı ürünü günceller.
      blockedButton('Yeni deneme', feature('newExam').reason, { variant: 'primary' }),
    ],
  });

  const table = h('div', 'tc-table');
  host.append(nodes.filters.node, table);
  host.append(hintBox('Deneme listesi tek sayfadan gelir: yılda onlarca deneme açılıyor, '
    + 'binlerce değil. Sınav tarihi süzgeci bu yüzden sayfada uygulanır.'));
  nodes.examTable = table;
  // Liste bellekte varsa hemen çizilir; ilk açılışta çekilir. Sekme her
  // değiştiğinde yeniden çekmek mağazaya boşuna istek atıyordu.
  if (state.loaded) renderExamTable();
  else refreshExams();
}

function renderExamTable() {
  const host = nodes.examTable;
  if (!host) return;
  const values = nodes.filters ? nodes.filters.values() : {};
  const rows = applyFilters(state.exams, values, {
    range: { kind: 'dateRange', field: 'examDate' },
  });

  host.replaceChildren();
  if (state.examsTruncated) {
    // Geçidin deneme ucu sayfa parametresi almıyor ve sunucu 50'de kırpıyor;
    // eksik listeyi sessizce göstermek "o deneme yok" demek olurdu.
    host.append(alertBox('Mağaza deneme listesini 50 kayıtta kırptı; bu ekranda tümü '
      + 'görünmüyor. Eski denemeler için mağaza yönetim ekranını kullanın.', 'warn'));
  }
  const table = dataTable({
    columns: EXAM_COLUMNS,
    rows,
    onRow: (row) => openExam(row),
    empty: state.connected
      ? emptyState({
        title: 'Bu süzgeçte deneme yok',
        text: `${num(state.exams.length)} deneme yüklendi. Süzgeçleri gevşetin ya da yeni bir `
          + 'deneme açın.',
        actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() }),
          blockedButton('Yeni deneme', feature('newExam').reason, { variant: 'primary' })],
      })
      : emptyState({
        title: 'Mağazaya ulaşılamadı',
        text: state.error || 'Bağlantı kurulamadı.',
        actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshExams() })],
      }),
  });
  host.append(table.node);
}

async function refreshExams() {
  nodes.examTable?.replaceChildren(skeletonRows(6, 6));
  nodes.status?.set('Denemeler alınıyor…');
  await loadExams();
  renderExamTable();
  nodes.status?.set(statusText(), !state.connected);
}

// ------------------------------------------------------------ deneme çekmecesi

function openExam(row) {
  const forms = [];
  const drop = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: row.name,
    subtitle: `${row.examDate || 'tarihsiz'} · ${row.capacity.label}`,
    onClose: drop,
  });
  closers.push(drop);

  const head = h('div', 'tc-drawer-head');
  head.append(
    badge(row.statusLabel, row.status === 'cancelled' ? 'bad' : 'info'),
    badge(REGISTRATION_LABELS[row.registrationState] || '—',
      row.registrationState === 'open' ? 'good' : 'dim'),
    badge(RESULT_LABELS[row.resultsState] || '—', RESULT_TONES[row.resultsState] || ''),
  );

  const pane = h('div', 'tc-pane');
  const tabs = tabBar([
    { key: 'general', label: 'Deneme' },
    { key: 'capacity', label: 'Kontenjan' },
    { key: 'notify', label: 'Bildirim' },
    { key: 'history', label: 'Geçmiş' },
  ], 'general', (key) => paint(key));
  box.body.append(head, tabs.node, pane);

  function paint(key) {
    drop();
    pane.replaceChildren();
    if (key === 'general') paintExamGeneral(pane, row, forms, box);
    else if (key === 'capacity') paintCapacity(pane, row, forms);
    else if (key === 'notify') paintNotify(pane, row, forms);
    else paintHistory(pane, row.id);
  }
  paint('general');
}

/**
 * Künyenin YAZILABİLİR alanları.
 *
 * Deneme Kulübü bu mağazada bir TABLO SATIRI DEĞİL, sabit SKU'lu TEK BİR sanal
 * üründür; künyesi o ürünün fiyatı ile durumudur. "Deneme adı", "sınav
 * tarihi", "yer", "kontenjan" alanlarının mağazada karşılığı yok ve
 * gönderilirlerse mağaza isteğin TAMAMINI reddediyor — yani ücret de
 * yazılmıyor.
 *
 * Bu yüzden o alanlar SALT OKUNUR (`static`) çizilir: değer görünmeye devam
 * eder (listeden geliyor), ama kullanıcıdan değiştirmesi istenmez.
 */
function examFields({ readOnlyExtras = true } = {}) {
  const closed = (key, label, feature) => ({
    key, label, type: 'static',
    hint: readOnlyExtras ? feature : '',
  });
  return [
    { key: 'feeKurus', label: 'Üyelik bedeli', type: 'money', required: true,
      hint: 'Vitrindeki üyelik fiyatı. Mağazanın gerçekten sakladığı iki alandan biri.' },
    { key: 'isOpen', label: 'Üyelik satışa açık', type: 'checkbox',
      hint: 'Kapalıyken vitrinde üye olunamaz. İkinci yazılabilir alan budur.' },
    closed('name', 'Deneme adı', feature('name').reason),
    closed('examDate', 'Sınav tarihi', feature('schedule').reason),
    closed('examTime', 'Saat', ''),
    closed('grade', 'Sınıf / seviye', ''),
    closed('venue', 'Yer', ''),
    closed('capacity', 'Kontenjan', feature('capacity').reason),
    closed('registrationStart', 'Kayıt başlangıcı', ''),
    closed('registrationEnd', 'Kayıt bitişi', ''),
  ];
}

function paintExamGeneral(pane, row, forms, box) {
  const form = formGrid({
    fields: examFields(),
    value: {
      name: row.name, examDate: row.examDate, examTime: row.examTime, grade: row.grade,
      venue: row.venue, capacity: row.capacity.capacity, feeKurus: row.feeKurus,
      isOpen: row.status !== 'closed' && row.registrationState !== 'closed',
      registrationStart: row.registrationStart, registrationEnd: row.registrationEnd,
    },
  });
  forms.push(form);

  const actions = h('div', 'tc-actions');
  actions.append(
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
        const saved = await saveExam(row.id, form.draft());
        if (saved) { box.close(); refreshExams(); }
      },
    }),
    button('Sonuçlara git', {
      onClick: () => { box.close(); nodes.tabs.select('results'); selectResultExam(row.id); },
    }),
  );

  pane.append(form.node, actions,
    alertBox(feature('capacity').reason, 'info'),
    hintBox('Bu ekrandan üyelik bedeli ve üyeliğin satışa açık olup olmadığı kaydedilir. '
      + 'Geri kalan alanlar mağazada bir kayda karşılık gelmediği için salt okunur: '
      + 'gönderilseler mağaza isteğin tamamını reddeder ve üyelik bedeli de yazılmazdı.'));
}

/** Yalnız VAR OLAN künyeyi düzenler; yeni kayıt açılamaz (bkz. `newExam`). */
function openExamForm(existing) {
  const forms = [];
  const drop = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Deneme künyesi',
    subtitle: 'Üyelik bedeli ve satış durumu — mağazanın sakladığı iki alan',
    onClose: drop,
  });
  closers.push(drop);

  const form = formGrid({ fields: examFields(), value: existing || {} });
  forms.push(form);
  const actions = h('div', 'tc-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const saved = await saveExam(existing?.id || null, form.draft());
      if (saved) { box.close(); refreshExams(); loadOverview(); }
    },
  }));
  box.body.append(form.node, actions);
}

async function saveExam(examId, draft) {
  const reason = await askReason({
    title: 'Deneme künyesini kaydet',
    description: `Üyelik bedeli ${money(draft.feeKurus || 0)} · satış `
      + `${draft.isOpen ? 'AÇIK' : 'KAPALI'} olacak. Vitrindeki fiyat ve üyelik durumu `
      + 'değişir; gerekçe denetim kaydına yazılır ve mağazaya başlıkla gider.',
    confirmLabel: 'Kaydet',
  });
  if (!reason) return null;
  return withBusy('Kaydediliyor…', async () => {
    // GÖVDEYE YALNIZ MAĞAZANIN SAKLADIĞI İKİ ALAN KONUR. Salt okunur alanları
    // göndermek, mağazanın isteği tümüyle reddetmesi demekti — ücret de
    // yazılmazdı. Sunucu da aynı denetimi yapıyor (K9).
    const body = {
      feeKurus: draft.feeKurus === null || draft.feeKurus === undefined
        ? null : Number(draft.feeKurus),
      isOpen: Boolean(draft.isOpen),
      reason,
      dryRun: false,
    };
    const result = examId
      ? await call(`${BASE}/exams/${examId}`, { method: 'PUT', body })
      : await call(`${BASE}/exams`, { method: 'POST', body });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Kaydedildi.',
      result.dryRun ? 'warn' : 'good');
    return result;
  });
}

function paintCapacity(pane, row, forms) {
  const view = row.capacity;
  const bar = progress();
  bar.percent(view.state === 'unlimited' ? 0 : Math.min(100, view.percent || 0),
    view.state === 'unlimited' ? `${num(view.enrolled)} kayıt · sınırsız` : view.label);
  bar.node.classList.add(`tone-${view.state}`);

  // KONTENJAN YAZILAMAZ — ve bu bir arıza değil, mağazanın hâli.
  //
  // Bu depoda kontenjan diye bir KAYIT yok: üyelik ürünü stok takibi kapalı
  // kaydediliyor ("Kontenjan Sınırlı" yalnızca vitrin rozeti). Giriş kutusu
  // açık bırakılsaydı, yazılan sayı 503 ile geri dönerdi — ya da daha kötüsü,
  // hiçbir şeyin okumadığı bir yere yazılıp kontenjan dolduktan sonra da satış
  // sürerdi. Doluluk çubuğu KALIYOR: kayıt sayısı gerçek bir bilgidir.
  const actions = h('div', 'tc-actions');
  actions.append(blockedButton('Kontenjanı değiştir', feature('capacity').reason,
    { variant: 'primary' }));

  pane.append(
    card('Doluluk', bar.node, view.remaining === null ? 'sınırsız'
      : `${num(view.remaining)} yer kaldı`),
    actions,
    alertBox(feature('capacity').reason, 'warn'),
    card('Katılımcı işlemleri', (() => {
      const box = h('div', 'tc-actions');
      box.append(pendingButton('Katılımcı ekle', 'addMember'));
      box.append(pendingButton('Kaydı iptal et', 'removeMember'));
      return box;
    })(), 'mağaza ucu bekleniyor'),
    alertBox(feature('addMember').reason, 'info'),
  );
}

// ============================================================== Katılımcılar

const MEMBER_COLUMNS = [
  {
    key: 'name',
    label: 'Katılımcı',
    width: 'minmax(0, 2.2fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'tc-name');
      box.append(clip(h('b'), row.name, 40),
        h('span', 'tc-sub', [row.code, row.grade].filter(Boolean).join(' · ') || '—'));
      return box;
    },
  },
  {
    key: 'email',
    label: 'İletişim',
    width: 'minmax(0, 1.8fr)',
    cell: (row) => {
      const box = h('span', 'tc-name');
      box.append(h('span', undefined, row.email || '—'),
        h('span', 'tc-sub', row.phoneMasked || '—'));
      return box;
    },
  },
  { key: 'examName', label: 'Deneme', width: 'minmax(0, 1.4fr)' },
  {
    key: 'city',
    label: 'Şehir',
    width: '130px',
    cell: (row) => [row.city, row.district].filter(Boolean).join(' / ') || '—',
  },
  {
    key: 'payment',
    label: 'Ödeme',
    width: '120px',
    cell: (row) => badge(row.paymentLabel,
      { paid: 'good', pending: 'warn', failed: 'bad', refunded: 'dim', free: 'info' }[row.payment] || ''),
  },
  {
    key: 'attendance',
    label: 'Katılım',
    width: '120px',
    cell: (row) => badge(row.attendanceLabel,
      { attended: 'good', absent: 'bad', unknown: 'dim' }[row.attendance] || ''),
  },
  {
    key: 'net',
    label: 'Net / Sıra',
    width: '120px',
    align: 'num',
    cell: (row) => {
      if (row.net === null && row.rank === null) return '—';
      const box = h('span', 'tc-stack right');
      box.append(h('b', undefined, row.net === null ? '—' : String(row.net)));
      box.append(h('span', 'tc-sub', row.rank ? `${row.rank}. sıra` : 'sıra yok'));
      return box;
    },
  },
];

function paintMembers(host) {
  dropFilters();
  host.replaceChildren();

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ad, e-posta, telefon ara', width: '250px' },
      { kind: 'select', key: 'exam', label: 'Deneme', options: examOptions() },
      { kind: 'select', key: 'payment', label: 'Ödeme', options: [
        { value: '', label: 'Tümü' },
        { value: 'paid', label: 'Ödendi' },
        { value: 'pending', label: 'Bekliyor' },
        { value: 'free', label: 'Ücretsiz' },
      ] },
      { kind: 'select', key: 'attendance', label: 'Katılım', options: [
        { value: '', label: 'Tümü' },
        { value: 'attended', label: 'Katıldı' },
        { value: 'absent', label: 'Katılmadı' },
      ] },
      { kind: 'search', key: 'city', placeholder: 'Şehir', width: '130px' },
      { kind: 'search', key: 'grade', placeholder: 'Sınıf', width: '110px' },
    ],
    onChange: () => refreshMembers({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => refreshMembers() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Seçili denemenin tüm katılımcılarını rapor klasörüne yaz',
        onClick: exportAllMembers }),
    ],
  });

  const table = h('div', 'tc-table');
  nodes.memberTable = table;
  nodes.pager = pager({
    total: state.total, page: state.page, size: state.size,
    onChange: ({ page, size }) => refreshMembers({ page, size }),
  });
  host.append(nodes.filters.node, table, nodes.pager.node);
  refreshMembers({ page: 1 });
}

function renderMemberTable() {
  const host = nodes.memberTable;
  if (!host) return;
  host.replaceChildren();
  const table = dataTable({
    columns: MEMBER_COLUMNS,
    rows: state.members,
    onRow: (row) => openMember(row),
    empty: state.connected
      ? emptyState({
        title: 'Bu süzgeçte katılımcı yok',
        text: 'Süzgeçleri gevşetin ya da başka bir deneme seçin.',
        actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
      })
      : emptyState({
        title: 'Mağazaya ulaşılamadı',
        text: state.error || 'Bağlantı kurulamadı.',
        actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshMembers() })],
      }),
  });
  host.append(table.node);
}

async function refreshMembers({ page = state.page, size = state.size } = {}) {
  nodes.memberTable?.replaceChildren(skeletonRows(8, 7));
  nodes.status?.set('Katılımcılar alınıyor…');
  await loadMembers({ page, size });
  renderMemberTable();
  nodes.pager?.update({ total: state.total, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
}

function openMember(row) {
  const box = drawer(nodes.root, { title: row.name, subtitle: row.examName || '—' });
  const info = h('div', 'tc-pane');
  info.append(kpiRow([
    { label: 'Ödeme', value: row.paymentLabel },
    { label: 'Katılım', value: row.attendanceLabel },
    { label: 'Net', value: row.net === null ? '—' : String(row.net) },
    { label: 'Sıra', value: row.rank ? `${row.rank}.` : '—' },
  ]));

  const details = h('div', 'tc-detail');
  for (const [label, value] of [
    ['Kayıt no', row.code], ['E-posta', row.email], ['Telefon', row.phone],
    ['Sınıf', row.grade], ['Şehir', [row.city, row.district].filter(Boolean).join(' / ')],
    ['Kayıt tarihi', row.registeredAt], ['Ödenen', row.paidKurus === null ? '' : money(row.paidKurus)],
  ]) {
    const line = h('div', 'tc-detail-row');
    line.append(h('span', 'tc-sub', label), h('b', undefined, value || '—'));
    details.append(line);
  }

  const actions = h('div', 'tc-actions');
  if (feature('notifyOne').available) {
    actions.append(button('Hatırlatma gönder', { onClick: () => notifyOne(row) }));
  }
  actions.append(pendingButton('Kaydı iptal et', 'removeMember'));

  info.append(details, actions);
  if (!feature('notifyOne').available) {
    info.append(alertBox(feature('notifyOne').reason, 'info'));
  }
  // Bu ipucu bir dönem "mağaza ucu yayınlandığında" diyordu; uç yayınlandı,
  // bekleyen halka geçit metodu. Gerekçenin AYRINTISI düğmenin `title`ında
  // (features().removeMember.reason) durur — burada tek yerde iki metin
  // tutmayalım diye özet geçiyoruz.
  info.append(hintBox('Katılımcı kaydı bu ekrandan SİLİNMEZ; iptal (pasifleştirme) '
    + 'olarak gelecek, geçmiş sınav ve ödeme kayıtları öksüz kalmasın diye. '
    + 'Düğmenin neden kapalı olduğu üstüne gelince yazar.'));
  box.body.append(info);
}

async function notifyOne(row) {
  const reason = await askReason({
    title: 'Hatırlatma gönder',
    description: `${row.name} kişisine ${row.phoneMasked || row.email} üzerinden tek bir `
      + 'bildirim gider. SMS ise ücretlidir.',
    confirmLabel: 'Gönder',
  });
  if (!reason) return;
  await withBusy('Gönderiliyor…', async () => {
    await call(`${BASE}/members/${row.id}/notify`, {
      method: 'POST',
      // Bu yol Bildirimler modülünün şablon KİMLİĞİNİ ister (`store:12`);
      // toplu gönderimin mağaza anahtarı burada geçerli değildir.
      body: { template: state.settings?.local?.notifyDirectTemplate || '',
        channel: row.phoneMasked && row.phone ? 'sms' : 'email',
        reason, dryRun: false },
    });
    toast('Bildirim gönderildi.', 'good');
  });
}

function exportVisible() {
  const headers = ['Kayıt no', 'Ad Soyad', 'E-posta', 'Telefon', 'Deneme', 'Ödeme', 'Katılım',
    'Net', 'Sıra'];
  const rows = state.members.map((row) => [row.code, row.name, row.email, row.phone,
    row.examName, row.paymentLabel, row.attendanceLabel, row.net, row.rank]);
  const written = csvBlob(headers, rows, `deneme-katilimcilari-sayfa-${state.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAllMembers() {
  const examId = Number(nodes.filters?.values()?.exam || 0);
  if (!examId) { toast('Önce bir deneme seçin: tüm kulübün dökümü çok büyük.', 'warn'); return; }
  await withBusy('Katılımcılar taranıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { examId, scope: 'members' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ================================================================= Sonuçlar

function examOptions() {
  return [{ value: '', label: 'Tümü — deneme' },
    ...state.exams.map((row) => ({ value: row.id, label: `${row.name} · ${row.examDate || '—'}` }))];
}

function paintResults(host) {
  dropFilters();
  host.replaceChildren();

  // Deneme seçici KİTİN filtre şerididir: elle `<select>` kurmak aynı şeyin
  // ikinci bir kopyasını ve ayrı bir görünümü doğurur (ADR 0011).
  nodes.filters = filterBar({
    fields: [
      { kind: 'select', key: 'exam', label: 'Deneme', value: String(state.examId || ''),
        options: [{ value: '', label: 'Deneme seçin' },
          ...state.exams.map((row) => (
            { value: row.id, label: `${row.name} · ${row.examDate || '—'}` }))] },
    ],
    onChange: (values) => selectResultExam(Number(values.exam) || 0),
    actions: [
      button('Sonuç CSV yükle', { variant: 'primary', onClick: () => pickFile() }),
      button('Sonuçları yayınla', { variant: 'danger', onClick: () => publishResults() }),
      button('Yoklama çizelgesi', { onClick: () => runReport('attendance') }),
      button('Sonuç karneleri', { onClick: () => runReport('cards') }),
      button('Sonuç listesi', { onClick: () => runReport('results') }),
      button('⤓ CSV', { onClick: () => exportResults() }),
    ],
  });

  const stats = h('div', 'tc-stats');
  const table = h('div', 'tc-table');
  nodes.resultStats = stats;
  nodes.resultTable = table;
  host.append(nodes.filters.node, stats, table);
  host.append(hintBox('Sonuç YÜKLEMEK ile YAYINLAMAK aynı şey değildir: yüklenen sonuç '
    + 'katılımcıya görünmez, yayınlanan görünür ve mobil uygulamada bildirim üretir.'));
  if (state.examId) refreshResults();
  else renderResults();
}

function selectResultExam(examId) {
  state.examId = Number(examId) || 0;
  // `set` olay tetiklemez: başka sekmeden gelindiğinde sonsuz döngü olmasın.
  if (state.tab === 'results') nodes.filters?.set('exam', String(state.examId || ''));
  if (!state.examId) { renderResults(); return; }
  refreshResults();
}

async function refreshResults() {
  if (!state.examId) return;
  nodes.resultTable?.replaceChildren(skeletonRows(8, 6));
  nodes.status?.set('Sonuçlar alınıyor…');
  await loadResults(state.examId);
  renderResults();
  nodes.status?.set(statusText(), !state.connected);
}

const RESULT_COLUMNS = [
  { key: 'rank', label: 'Sıra', width: '70px', align: 'num',
    cell: (row) => (row.rank ? `${row.rank}.` : '—') },
  {
    key: 'name',
    label: 'Katılımcı',
    width: 'minmax(0, 2.2fr)',
    cell: (row) => {
      const box = h('span', 'tc-name');
      box.append(clip(h('b'), row.name, 40),
        h('span', 'tc-sub', [row.code, row.grade].filter(Boolean).join(' · ') || '—'));
      return box;
    },
  },
  { key: 'attendance', label: 'Katılım', width: '110px',
    cell: (row) => badge(row.attendanceLabel,
      { attended: 'good', absent: 'bad', unknown: 'dim' }[row.attendance] || '') },
  { key: 'correct', label: 'Doğru', width: '90px', align: 'num' },
  { key: 'wrong', label: 'Yanlış', width: '90px', align: 'num' },
  { key: 'blank', label: 'Boş', width: '80px', align: 'num' },
  { key: 'net', label: 'Net', width: '90px', align: 'num' },
  { key: 'score', label: 'Puan', width: '100px', align: 'num' },
];

function renderResults() {
  const host = nodes.resultTable;
  if (!host) return;
  const stats = nodes.resultStats;
  stats.replaceChildren();
  host.replaceChildren();

  if (!state.examId) {
    host.append(emptyState({
      title: 'Deneme seçilmedi',
      text: 'Sonuçlar denemeye göre tutulur; yukarıdan bir deneme seçin.',
    }));
    return;
  }

  const numbers = state.stats || {};
  stats.append(kpiRow([
    { label: 'Sonuç', value: num(numbers.count || 0) },
    { label: 'Katılan', value: num(numbers.attended || 0) },
    { label: 'Net ortalaması', value: numbers.netAverage === null || numbers.netAverage === undefined
      ? '—' : String(numbers.netAverage) },
    { label: 'En yüksek net', value: numbers.netBest === null || numbers.netBest === undefined
      ? '—' : String(numbers.netBest) },
    { label: 'Puan ortalaması', value: numbers.scoreAverage === null || numbers.scoreAverage === undefined
      ? '—' : String(numbers.scoreAverage) },
  ]));

  const table = dataTable({
    columns: RESULT_COLUMNS,
    rows: state.results,
    dense: true,
    rowKey: (row) => String(row.memberId || row.id),
    onRow: (row) => runReport('cards', { memberId: row.memberId || row.id }),
    empty: state.connected
      ? emptyState({
        title: 'Bu denemenin sonucu yüklenmemiş',
        text: 'Kurumdan gelen CSV dosyasını yükleyin; önce eşleştirme önizlemesi gösterilir.',
        actions: [button('Sonuç CSV yükle', { variant: 'primary', onClick: () => pickFile() })],
      })
      : emptyState({
        title: 'Mağazaya ulaşılamadı',
        text: state.error || 'Bağlantı kurulamadı.',
        actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshResults() })],
      }),
  });
  host.append(table.node);
  if (state.results.length) {
    host.append(h('div', 'tc-sub', 'Satıra tıklamak o katılımcının sonuç karnesini üretir.'));
  }
}

async function exportResults() {
  if (!state.examId) { toast('Önce bir deneme seçin.', 'warn'); return; }
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { examId: state.examId, scope: 'results' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

function runReport(kind, extra = {}) {
  if (!state.examId) { toast('Önce bir deneme seçin.', 'warn'); return; }
  report.run(kind, { examId: state.examId, memberId: 0, ...extra });
}

async function publishResults() {
  if (!state.examId) { toast('Önce bir deneme seçin.', 'warn'); return; }
  const reason = await askReason({
    title: 'Sonuçları yayınla',
    description: `${examName(state.examId)} denemesinin sonuçları katılımcılara GÖRÜNÜR olur `
      + 've mobil uygulamada bildirim üretir. Geri alma tek tuşla değildir.',
    confirmLabel: 'Yayınla',
  });
  if (!reason) return;
  await withBusy('Yayınlanıyor…', async () => {
    const result = await call(`${BASE}/exams/${state.examId}/results/publish`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast('Sonuçlar yayınlandı.', 'good');
    if (result.notice) toast(result.notice, 'warn');
    refreshExams();
    loadOverview();
  });
}

// ------------------------------------------------------- CSV → önizleme → onay

function pickFile() {
  if (!state.examId) { toast('Önce bir deneme seçin.', 'warn'); return; }
  const input = h('input');
  input.type = 'file';
  input.accept = '.csv,text/csv,text/plain';
  input.style.display = 'none';
  input.addEventListener('change', () => {
    const file = input.files && input.files[0];
    input.remove();
    if (!file) return;
    const reader = new FileReader();
    reader.addEventListener('error', () => toast('Dosya okunamadı.', 'bad'));
    reader.addEventListener('load', () => {
      uploadPreview(String(reader.result || ''), file.name);
    });
    // Kurumdan gelen dosyalar Windows'ta genelde UTF-8 kaydediliyor; yanlış
    // kodlamada Türkçe harfler bozulur ama eşleştirme `fold` üzerinden
    // yapıldığı için satır yine de tanınır.
    reader.readAsText(file, 'utf-8');
  });
  nodes.root.append(input);
  input.click();
}

async function uploadPreview(content, filename) {
  const preview = await withBusy('Dosya eşleştiriliyor…', async () => call(
    `${BASE}/exams/${state.examId}/results/preview`, {
      method: 'POST', body: { content, filename },
    }));
  if (preview) openMatchDialog(preview, filename);
}

function openMatchDialog(preview, filename) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog tc-match');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const summary = preview.summary;
  box.append(h('h3', 'kit-dialog-title', 'Eşleştirme önizlemesi'));
  box.append(h('p', 'kit-dialog-text',
    `${filename || 'dosya'} · ${num(summary.total)} satır okundu, `
    + `${num(summary.memberCount)} kayıtlı katılımcıyla eşleştirildi. `
    + 'Hiçbir şey yazılmadı.'));

  const body = h('div', 'tc-match-body');
  body.append(kpiRow([
    { label: 'Satır', value: num(summary.total) },
    { label: 'Eşleşti', value: num(summary.matched), tone: 'good' },
    { label: 'Bulunamadı', value: num(summary.unmatched), tone: summary.unmatched ? 'bad' : '' },
    { label: 'Belirsiz', value: num(summary.ambiguous), tone: summary.ambiguous ? 'warn' : '' },
    { label: 'Mükerrer', value: num(summary.duplicate), tone: summary.duplicate ? 'warn' : '' },
    { label: 'Yazılacak', value: num(summary.writable) },
  ]));

  for (const note of preview.warnings || []) body.append(alertBox(note, 'warn'));

  const table = dataTable({
    columns: [
      { key: 'line', label: 'Satır', width: '70px', align: 'num' },
      { key: 'csvName', label: 'Dosyadaki', width: 'minmax(0, 1.6fr)' },
      { key: 'memberName', label: 'Katılımcı', width: 'minmax(0, 1.6fr)',
        cell: (row) => (row.memberName || '—') },
      { key: 'matchedBy', label: 'Nasıl', width: '100px',
        cell: (row) => (row.matchedBy || '—') },
      { key: 'net', label: 'Net', width: '80px', align: 'num' },
      { key: 'score', label: 'Puan', width: '90px', align: 'num' },
      { key: 'state', label: 'Durum', width: '120px',
        cell: (row) => badge(MATCH_LABELS[row.state] || row.state, MATCH_TONES[row.state] || '') },
      { key: 'note', label: 'Açıklama', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: preview.matches,
    dense: true,
    rowKey: (row) => String(row.line),
  });
  body.append(table.node);

  if ((preview.missingMembers || []).length) {
    body.append(alertBox(
      `${preview.missingMembers.length} katılımcının dosyada karşılığı yok: `
      + `${preview.missingMembers.slice(0, 8).map((item) => item.name).join(', ')}`
      + `${preview.missingMembers.length > 8 ? '…' : ''}. Sınava girmemiş olabilirler ya da `
      + 'dosya eksik gelmiştir.', 'warn'));
  }

  const actions = h('div', 'kit-dialog-actions');
  actions.append(
    button('Vazgeç', { onClick: close }),
    button('Eşleştirmeyi CSV al', {
      onClick: () => {
        csvBlob(['Satır', 'Dosyadaki', 'Katılımcı', 'Nasıl', 'Net', 'Puan', 'Durum', 'Açıklama'],
          preview.matches.map((row) => [row.line, row.csvName, row.memberName, row.matchedBy,
            row.net, row.score, MATCH_LABELS[row.state] || row.state, row.note]),
          'deneme-eslestirme');
        toast('Eşleştirme tablosu indirildi.', 'good');
      },
    }),
    button(`${num(summary.writable)} sonucu yaz`, {
      variant: 'danger',
      disabled: !preview.applicable,
      onClick: async () => {
        const reason = await askReason({
          title: 'Sonuçları yaz',
          description: `${num(summary.writable)} katılımcının sonucu yazılacak. Önizlediğiniz `
            + 'tablo neyse o yazılır; yeniden eşleştirilmez. Yazma YAYINLAMA DEĞİLDİR.',
          confirmLabel: 'Yaz',
        });
        if (!reason) return;
        await withBusy('Sonuçlar yazılıyor…', async () => {
          const result = await call(`${BASE}/results/apply`, {
            method: 'POST', body: { token: preview.token, reason, dryRun: false },
          });
          toast(`${num(result.written)} sonuç yazıldı.`, 'good');
          if (result.notice) toast(result.notice, 'warn');
          // Yazma başarılı ama jeton kapatılamadıysa aynı dosyayı ikinci kez
          // uygulamak sonuçları İKİ KEZ yazar; bunu sessiz geçmiyoruz.
          if (result.warning) toast(result.warning, 'bad');
          close();
          refreshResults();
        });
      },
    }),
  );

  box.append(body, actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);
}

// ================================================================= bildirim

function paintNotify(pane, row, forms) {
  const form = formGrid({
    fields: [
      { key: 'audience', label: 'Kime', type: 'select', options: [
        { value: 'all', label: 'Tüm katılımcılar' },
        { value: 'unpaid', label: 'Ödemesi bekleyenler' },
        { value: 'attended', label: 'Sınava katılanlar' },
        { value: 'absent', label: 'Sınava katılmayanlar' },
      ] },
      { key: 'channel', label: 'Kanal', type: 'select', options: [
        { value: 'sms', label: 'SMS (ücretli)' },
        { value: 'email', label: 'E-posta' },
        { value: 'push', label: 'Mobil bildirim' },
      ] },
      { key: 'template', label: 'Şablon anahtarı', type: 'text', maxLength: 64,
        hint: 'Boş bırakılırsa yalnız aşağıdaki metin gönderilir.' },
      { key: 'message', label: 'Mesaj', type: 'textarea', maxLength: 1200, wide: true },
    ],
    value: {
      audience: 'all',
      channel: state.settings?.local?.notifyChannel || 'sms',
      template: state.settings?.local?.notifyTemplate || '',
      message: '',
    },
  });
  forms.push(form);

  const result = h('div', 'tc-notify-result');
  const actions = h('div', 'tc-actions');
  actions.append(
    button('Önizle', {
      onClick: async () => {
        const draft = form.draft();
        result.replaceChildren(skeletonRows(2, 3));
        let preview;
        try {
          preview = await call(`${BASE}/exams/${row.id}/notify/preview`, {
            method: 'POST',
            body: { audience: draft.audience, channel: draft.channel,
              message: draft.message || '' },
          });
        } catch (error) {
          result.replaceChildren(alertBox(error.message, 'bad'));
          return;
        }
        paintNotifyPreview(result, preview, row, form);
      },
    }),
  );

  pane.append(form.node, actions, result,
    hintBox('Toplu bildirim mağazaya TEK istekte gider ve alıcı listesi açıkça taşınır: '
      + 'önizlediğiniz kitle ile gönderilen kitle aynıdır. Yüz kişiye tek tek çağrı '
      + 'atmak hız sınırını tüketirdi.'));
}

function paintNotifyPreview(host, preview, row, form) {
  host.replaceChildren();
  host.append(kpiRow([
    { label: 'Kitle', value: preview.audienceLabel },
    { label: 'Kişi', value: num(preview.total) },
    { label: 'Ulaşılabilir', value: num(preview.reachable), tone: 'good' },
    { label: 'Ulaşılamayan', value: num(preview.unreachableCount),
      tone: preview.unreachableCount ? 'warn' : '' },
    ...(preview.smsParts ? [{ label: 'SMS parçası', value: num(preview.smsParts) },
      { label: 'Toplam SMS', value: num(preview.estimatedSms), tone: 'warn' }] : []),
  ]));

  if (preview.unreachableCount) {
    host.append(alertBox(
      `${num(preview.unreachableCount)} kişiye ulaşılamıyor (numara/adres eksik ya da geçersiz); `
      + 'bunlar gönderildi sayılmaz.', 'warn'));
  }
  if (preview.smsUnicode) {
    host.append(alertBox('Mesajda GSM alfabesine sığmayan karakter var; SMS Unicode olarak '
      + 'gider ve parça başına 70 karakter sayılır — maliyet artar.', 'warn'));
  }
  if (preview.capped) {
    host.append(alertBox(`Alıcı sayısı ${num(preview.reachable)}, sınır ${num(preview.limit)}. `
      + 'Kitleyi daraltın ya da modül ayarından sınırı bilerek yükseltin.', 'bad'));
  }
  if (preview.truncated) {
    host.append(alertBox('Katılımcı listesi tavana dayandı; kitle eksik olabilir.', 'warn'));
  }

  const send = h('div', 'tc-actions');
  send.append(button(`${num(preview.reachable)} kişiye gönder`, {
    variant: 'danger',
    disabled: preview.capped || preview.reachable === 0,
    onClick: async () => {
      const draft = form.draft();
      const reason = await askReason({
        title: 'Toplu bildirim gönder',
        description: `${preview.audienceLabel} · ${num(preview.reachable)} kişi · `
          + `${draft.channel.toUpperCase()}`
          + `${preview.estimatedSms ? ` · yaklaşık ${num(preview.estimatedSms)} SMS` : ''}. `
          + 'SMS ücretlidir ve geri alınamaz.',
        confirmLabel: 'Gönder',
      });
      if (!reason) return;
      await withBusy('Gönderiliyor…', async () => {
        const result = await call(`${BASE}/exams/${row.id}/notify`, {
          method: 'POST',
          body: { audience: draft.audience, channel: draft.channel,
            template: draft.template || '', message: draft.message || '',
            reason, dryRun: false },
        });
        toast(`${num(result.sent)} kişiye gönderildi`
          + (result.skipped ? ` · ${num(result.skipped)} atlandı` : ''),
        result.skipped ? 'warn' : 'good');
      });
    },
  }));
  host.append(send);
}

// ================================================================== geçmiş

async function paintHistory(pane, examId) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await call(`${BASE}/audit?examId=${examId}&limit=50`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Bu denemeye bu ekrandan dokunulmadı',
      text: 'Kontrol Merkezi üzerinden yapılan her yazma gerekçesiyle burada listelenir.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'action', label: 'İşlem', width: '150px' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}`,
  });
  pane.append(table.node,
    hintBox('Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydı gerekçe alanı '
      + 'taşımıyor; ağ koparsa "ne yapmaya çalıştık" bilgisi yalnız burada kalır.'));
}

// ================================================================== ayarlar

async function paintSettings(host) {
  dropFilters();
  dropForms();
  host.replaceChildren(skeletonRows(4, 2));
  let payload;
  try {
    payload = await loadSettings();
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  const form = formGrid({
    fields: [
      { key: 'nearFullPercent', label: 'Kontenjan uyarı eşiği (%)', type: 'number', min: 50,
        max: 100, hint: 'Bu yüzdeden sonra çubuk "dolmak üzere" boyanır.' },
      { key: 'attendanceSpareRows', label: 'Yoklama boş satırı', type: 'number', min: 0, max: 50,
        hint: 'Sınav günü gelen son dakika kayıtları elle yazılabilsin diye.' },
      { key: 'notifyChannel', label: 'Varsayılan bildirim kanalı', type: 'select', options: [
        { value: 'sms', label: 'SMS' }, { value: 'email', label: 'E-posta' },
        { value: 'push', label: 'Mobil bildirim' },
      ] },
      { key: 'notifyTemplate', label: 'Toplu bildirim şablon anahtarı', type: 'text',
        maxLength: 64, hint: 'Mağaza tarafındaki şablon anahtarı; toplu gönderimde kullanılır.' },
      { key: 'notifyDirectTemplate', label: 'Tek kişilik hatırlatma şablonu', type: 'text',
        maxLength: 64, wide: true,
        hint: 'Bildirimler ekranındaki şablon KİMLİĞİ (örn. store:12). Toplu gönderimin '
          + 'anahtarı burada çalışmaz; boşsa satırdaki hatırlatma düğmesi çalışmaz.' },
    ],
    value: payload.local,
  });
  nodes.forms = [form];

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const draft = form.draft();
      await withBusy('Ayarlar kaydediliyor…', async () => {
        const result = await call(`${BASE}/settings`, {
          method: 'POST',
          body: {
            // Boş sayı alanı `null` gider ("değiştirme"); `Number('')` sıfır
            // üretiyor ve sunucu şeması 0'ı reddedip anlaşılmaz 422 veriyordu.
            nearFullPercent: numberOrNull(draft.nearFullPercent),
            attendanceSpareRows: numberOrNull(draft.attendanceSpareRows),
            notifyChannel: draft.notifyChannel || '',
            notifyTemplate: draft.notifyTemplate || '',
            notifyDirectTemplate: draft.notifyDirectTemplate || '',
          },
        });
        toast(`Kaydedildi: ${result.changed.join(', ') || 'değişiklik yok'}`, 'good');
        refreshExams();
      });
    },
  });

  const limits = h('div', 'tc-detail');
  for (const [label, value] of [
    ['Sonuç dosyasından okunacak en çok satır', num(payload.limits.maxUploadRows)],
    ['Tek toplu bildirimde en çok alıcı', num(payload.limits.maxRecipients)],
  ]) {
    const line = h('div', 'tc-detail-row');
    line.append(h('span', 'tc-sub', label), h('b', undefined, value));
    limits.append(line);
  }

  const pending = h('div', 'tc-detail');
  for (const [key, info] of Object.entries(payload.features || {})) {
    const line = h('div', 'tc-detail-row');
    line.append(badge(info.available ? 'Açık' : 'Kapalı', info.available ? 'good' : 'dim'),
      h('span', undefined, `${key} — ${info.reason || 'çalışıyor'}`));
    pending.append(line);
  }

  const actions = h('div', 'tc-actions');
  actions.append(save);

  host.append(
    card('Bu ekranın tercihi', form.node, 'Yalnız Kontrol Merkezi’ni etkiler'),
    actions,
    card('Sınırlar', limits, 'modül ayarından değişir'),
    card('Özellik durumu', pending, 'kapalı olanın nedeni yazılıdır'),
    hintBox('Ayrı bir “Mağaza Ayarları” ekranı yoktur: her ayar onu kullanan ekranda durur. '
      + 'Deneme Kulübü ayarlarının hiçbiri mağaza vitrinini etkilemez; hepsi yerel tercihtir.'),
  );
}

// ==================================================================== mount

function showTab(key) {
  state.tab = key;
  dropForms();
  nodes.body.replaceChildren();
  if (key === 'exams') paintExams(nodes.body);
  else if (key === 'members') paintMembers(nodes.body);
  else if (key === 'results') paintResults(nodes.body);
  else paintSettings(nodes.body);
  nodes.status.node.hidden = key === 'settings';
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel tc');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  nodes.forms = [];
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  nodes.kpi = h('div', 'tc-kpi');
  nodes.status = statusLine();
  nodes.body = h('div', 'tc-body');
  nodes.tabs = tabBar([
    { key: 'exams', label: 'Denemeler' },
    { key: 'members', label: 'Katılımcılar' },
    { key: 'results', label: 'Sonuçlar' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'exams', (key) => showTab(key));

  view.append(nodes.kpi, nodes.tabs.node, nodes.status.node, nodes.body);
  root.replaceChildren(view);
  nodes.status.set('Deneme Kulübü yükleniyor…');

  // Deneme listesi ÖNCE gelir: katılımcı ve sonuç sekmelerindeki deneme
  // seçicileri onunla dolar; sonra çekmek seçimi kaybettiriyordu.
  loadOverview();
  // Tercihler açılışta okunur; Ayarlar sekmesi hiç açılmasa bile bildirim
  // formları varsayılan kanal ve şablonla dolsun. Hata YUTULUR: tercih
  // okunamazsa ekran modül ayarındaki varsayılanlarla çalışmaya devam eder.
  loadSettings().catch(() => {});
  loadExams().then(() => showTab('exams'));

  return () => {
    dropFilters();
    dropForms();
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
