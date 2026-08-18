// Müşteriler paneli — müşteri listesi, RFM segmenti, yorum moderasyonu, KVKK.
//
// NE YAPAR: sunucu tarafında sayfalanmış müşteri listesi + mini KPI + segment
// çipleri; satırdan tam yükseklik çekmecede sekizli künye (özet · siparişler ·
// adresler · iadeler · YORUMLAR · izin geçmişi · notlar · işlem geçmişi);
// ayrı bir üst sekmede tüm mağazanın yorum moderasyon kuyruğu (puan süzgeci,
// onayla/reddet/spam, mağaza yanıtı, bekleyen sayacı); KVKK veri paketi ve
// anonimleştirme; segment raporu, yorum özeti ve müşteri karnesi PDF.
//
// NE YAPMAZ:
//  · Müşteri SİLMEZ. Silinen müşterinin siparişi, faturası ve iadesi öksüz
//    kalır; mali kayıt geçmişe dönük bozulur. Her yerde pasifleştirme vardır.
//  · Yorum SİLMEZ. Reddedilen yorum vitrinde görünmez ama kaydı durur.
//  · Kişisel veriyi ekranda TOPLAMAZ. KVKK veri paketini mağaza üretir; biz
//    yalnız bağlantıyı gösteririz.
//  · Şifre sıfırlama bağlantısı GÖNDEREMEZ ve bülten aboneliğini tek tıkla
//    açıp kapatamaz — mağaza geçidinde bu uçlar henüz yok. Düğmeler kapalı
//    duruyor ve nedenini yazıyor; sessizce patlamıyorlar.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Bagisto müşteri ucu sipariş sayısına/harcamaya göre SÜZDÜRMEZ. Segment,
//    harcama ve tarih süzgeçleri seçilince nüfusun tamamı bir kez taranır;
//    şerit "tarama görünümü" olduğunu ve taramanın zamanını söyler.
//  · Mağazanın müşteri kaydında sipariş sayısı, harcama ve SON SİPARİŞ TARİHİ
//    yoktur (liste ucu hiçbirini, detay ucu yalnız ilk ikisini verir); üçü de
//    siparişlerden toplulaştırılır. Toplulaştırma yapılamazsa segment
//    "Bilinmiyor" kalır, rozet ve şerit bunu yazar. Sıfır UYDURULMAZ.
//  · Mağaza sipariş ucunda `customer_id` süzgecini UYGULAMIYOR (canlıda
//    doğrulandı); müşterinin siparişleri kimliğe göre burada süzülür ve
//    çekmece bunu söyler.
//  · Mağaza yorum durumları üçtür; SPAM YOKTUR. "Spam" yorumu reddeder ve
//    burada spam olarak etiketler — karar kaybolmaz, ekran farkı söyler.
//  · Puan süzgeci çoklu seçilirse mağaza tek puan kabul ettiği için sayfa
//    üzerinde süzülür; şerit bunu yazar.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_customers/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_customers/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, button, clip, confirmSimple, confirmWithReason, csvBlob, h, loadStyles,
  money, num, percent, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { sparkline, stackedBar } from '../../ui-kit/charts.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_customers';

const SEGMENTS = [
  { key: 'champion', label: 'Şampiyon' },
  { key: 'loyal', label: 'Sadık' },
  { key: 'new', label: 'Yeni' },
  { key: 'at_risk', label: 'Riskli' },
  { key: 'sleeping', label: 'Uykuda' },
  { key: 'lost', label: 'Kayıp' },
  { key: 'none', label: 'Hiç sipariş vermemiş' },
];

const SEGMENT_TONES = {
  champion: 'good', loyal: 'good', new: 'info', at_risk: 'warn',
  sleeping: 'dim', lost: 'bad', none: 'dim', unknown: 'dim',
};

const SEGMENT_LABELS = Object.fromEntries(SEGMENTS.map((item) => [item.key, item.label]));
SEGMENT_LABELS.unknown = 'Bilinmiyor';

const REVIEW_TONES = { pending: 'warn', approved: 'good', disapproved: 'bad' };
const REVIEW_LABELS = { pending: 'Bekliyor', approved: 'Onaylı', disapproved: 'Reddedildi' };

const EMPTY_STATE = {
  items: [], total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', source: 'customers', scannedAt: '', truncated: false,
  unknownSegments: 0, statsError: '', statsTruncated: false,
  scan: { state: 'empty', running: false, at: '' },
  selection: [], segment: null, loaded: false,
  reference: { groups: [], channels: [], segments: [] },
  reviews: {
    items: [], total: 0, page: 1, size: 50, pages: 0, ratings: [], status: '',
    unanswered: false, q: '', selection: [], summary: null, loaded: false,
  },
};

let api = null;
let openPanel = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE, reviews: { ...EMPTY_STATE.reviews } };
//: Tarama sürüyorken ekranın kendini tazeleyeceği an. Tek bir zamanlayıcı
//: tutulur; kapanışta iptal edilir, yoksa panel kapandıktan sonra istek atar.
let scanTimer = null;
const SCAN_POLL_MS = 15_000;

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

/** Yıkıcı ve kişisel veriye dokunan her işlem buradan geçer. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function segmentBadge(row) {
  const key = row.segment || 'unknown';
  const chip = badge(SEGMENT_LABELS[key] || key, SEGMENT_TONES[key] || '');
  if (key === 'unknown') {
    chip.title = 'Mağaza bu müşterinin sipariş toplamını vermedi; segment hesaplanamadı. '
      + 'Sıfır varsayılmadı.';
  }
  return chip;
}

// Tarama (nüfus + sipariş toplulaştırması) mağazayı sayfa sayfa gezdiği için
// dakikalar sürebiliyor ve İSTEĞİN İÇİNDE koşmuyor. Liste hemen gelir, taramaya
// bağlı sütunlar sonradan dolar. Durum satırı bunu SÖYLER; sessiz boş sütun,
// kullanıcıya "bu müşteri hiç alışveriş yapmamış" diye okunurdu.
function scanNote() {
  const scan = state.scan || {};
  if (scan.state === 'running') return ' · segment taraması sürüyor';
  if (scan.state === 'error') return ' · segment taraması yapılamadı';
  return '';
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const pages = Math.max(1, state.pages);
  if (state.source === 'segment') {
    const when = state.scannedAt ? ago(state.scannedAt) : 'az önce';
    return `Tarama görünümü · ${num(state.total)} müşteri · sayfa ${state.page}/${pages} `
      + `· tarama ${when}` + (state.truncated ? ' · TAVANA DAYANDI' : '') + scanNote();
  }
  return `Bağlı · ${num(state.total)} müşteri · sayfa ${state.page}/${pages}` + scanNote();
}

// -------------------------------------------------------------------- veri

function currentFilters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const registered = values.registered || {};
  const lastOrder = values.lastOrder || {};
  const orders = values.orders || {};
  const spend = values.spend || {};
  return {
    q: values.q || '',
    city: values.city || '',
    groupId: values.group || 0,
    status: values.status || '',
    newsletter: values.newsletter ? '1' : '',
    segment: state.segment || '',
    registeredFrom: registered.start || '',
    registeredTo: registered.end || '',
    lastOrderFrom: lastOrder.start || '',
    lastOrderTo: lastOrder.end || '',
    ordersMin: orders.min ?? null,
    ordersMax: orders.max ?? null,
    spendMin: spend.min ?? null,
    spendMax: spend.max ?? null,
  };
}

function queryString(extra = {}) {
  const merged = { ...currentFilters(), ...extra };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(merged)) {
    // SIFIR ATILMAZ: "en çok 0 sipariş" (hiç sipariş vermemiş) geçerli bir
    // süzgeçtir ve atılırsa kullanıcı süzdüğünü sanarak tüm listeyi görür.
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refresh({ page = state.page, size = state.size, refreshScan = false } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 7));
  nodes.status?.set('Müşteriler alınıyor…');
  const query = queryString({ page, size, refresh: refreshScan ? '1' : '' });
  let payload;
  try {
    payload = await api(`${BASE}/customers?${query}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, items: [], total: 0 };
    renderTable();
    nodes.status?.set(statusText(), true);
    return;
  }
  state = {
    ...state,
    items: payload.items || [],
    total: payload.total || 0,
    page: payload.page || page,
    size: payload.size || size,
    pages: payload.pages || 0,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    source: payload.source || 'customers',
    scannedAt: payload.scannedAt || '',
    truncated: Boolean(payload.truncated),
    unknownSegments: payload.unknownSegments || 0,
    statsError: payload.statsError || '',
    statsTruncated: Boolean(payload.statsTruncated),
    scan: payload.scan || { state: 'empty', running: false, at: '' },
    selection: [],
    loaded: true,
  };
  renderTable();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
  scheduleScanFollowUp();
}

// Tarama bitince ekranı BİR KEZ tazeler. Sürekli yoklamıyoruz: tarama bitmişse
// ikinci istek zaten hazır sonucu alır, bitmemişse yeni bir tarama başlatmaz —
// ama süresiz yoklamak mağazanın istek bütçesini boşuna yerdi.
function scheduleScanFollowUp() {
  if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
  if (!state.scan?.running) return;
  scanTimer = setTimeout(() => {
    scanTimer = null;
    if (!state.loaded) return;          // ekran kapandı
    refresh();
    loadOverview();
  }, SCAN_POLL_MS);
}

async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    // Referans listeler gelmezse süzgeçler boş kalır ama ekran çalışır (K7).
    return;
  }
  state.reference = {
    groups: payload.groups || [],
    channels: payload.channels || [],
    segments: payload.segments || [],
  };
  nodes.filters.options('group', [
    { value: '', label: 'Tümü — grup' },
    ...state.reference.groups.map((item) => ({ value: item.id, label: item.name })),
  ]);
  if (payload.stale) {
    toast('Referans listeler bayat kopyadan geldi; mağaza yanıt vermedi.', 'warn');
  }
}

async function loadOverview({ refreshScan = false } = {}) {
  try {
    renderKpi(await api(`${BASE}/overview${refreshScan ? '?refresh=1' : ''}`));
  } catch {
    renderKpi(null);
  }
}

// ------------------------------------------------------------------- çizim

function renderKpi(payload) {
  if (!nodes.kpi) return;
  if (!payload || !payload.connected) {
    nodes.kpi.replaceChildren(hintBox(
      'Mini KPI ve segment sayaçları müşteri nüfusunun taranmasını gerektiriyor '
      + `ve tarama şu an yapılamadı${payload?.error ? ` — ${payload.error}` : ''}. `
      + 'Liste yine çalışıyor.',
    ));
    nodes.chips.counts({});
    return;
  }
  const kpi = payload.kpi || {};
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Müşteri', value: num(kpi.total || 0) },
    { label: `Yeni (${kpi.newDays || 30}g)`, value: num(kpi.new || 0), tone: 'good' },
    {
      label: 'Tekrar eden',
      value: kpi.repeatRate === null || kpi.repeatRate === undefined
        ? '—' : percent(kpi.repeatRate),
      title: 'Payda: en az bir sipariş vermiş müşteri. Hiç sipariş vermemişleri paydaya '
        + 'koymak oranı sahte biçimde düşürürdü.',
    },
    {
      label: 'Ortalama YBD',
      value: kpi.avgLifetime === null || kpi.avgLifetime === undefined
        ? '—' : money(kpi.avgLifetime),
      title: 'Yaşam boyu değer: müşteri başına toplam harcama ortalaması.',
    },
  ]));
  nodes.chips.counts(payload.segments || {});
  if (payload.truncated) {
    nodes.kpi.append(alertBox(
      'Tarama tavana dayandı; KPI ve segment sayıları nüfusun tamamını temsil etmiyor. '
      + 'Tavan modül ayarından yükseltilebilir (segment_scan_cap).', 'warn'));
  }
}

const COLUMNS = [
  {
    key: 'name',
    label: 'Müşteri',
    width: 'minmax(0, 2.4fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'cu-name');
      box.append(clip(h('b'), row.name, 34),
        h('span', 'cu-sub', row.email || row.phone || 'iletişim yok'));
      return box;
    },
  },
  { key: 'groupName', label: 'Grup', width: '110px' },
  {
    key: 'segment',
    label: 'Segment',
    width: '140px',
    cell: segmentBadge,
  },
  {
    key: 'orders',
    label: 'Sipariş',
    width: '82px',
    align: 'num',
    cell: (row) => (row.orders === null || row.orders === undefined ? '—' : num(row.orders)),
  },
  {
    key: 'spend',
    label: 'Toplam harcama',
    width: '132px',
    align: 'num',
    sortable: true,
    cell: (row) => {
      if (row.spend === null || row.spend === undefined) return '—';
      if (!row.spendPartial) return money(row.spend);
      // Tutarı okunamayan sipariş toplama girmedi; rakamın eksik olduğunu
      // yazıyla söylemek, sessizce düşük göstermekten iyidir.
      const box = h('span', 'cu-when');
      box.append(h('b', undefined, money(row.spend)), h('span', 'cu-sub', 'eksik'));
      return box;
    },
  },
  {
    key: 'avgBasket',
    label: 'Ort. sepet',
    width: '112px',
    align: 'num',
    cell: (row) => (row.avgBasket === null || row.avgBasket === undefined
      ? '—' : money(row.avgBasket)),
  },
  {
    key: 'lastOrderAt',
    label: 'Son sipariş',
    width: '112px',
    cell: (row) => {
      if (!row.lastOrderAt) return '—';
      const box = h('span', 'cu-when');
      box.append(h('b', undefined, row.lastOrderAt));
      if (row.recencyDays !== null && row.recencyDays !== undefined) {
        box.append(h('span', 'cu-sub', `${num(row.recencyDays)} gün önce`));
      }
      return box;
    },
  },
  { key: 'createdAt', label: 'Kayıt', width: '100px' },
  { key: 'city', label: 'Şehir', width: '110px' },
  {
    key: 'status',
    label: 'Durum',
    width: '104px',
    cell: (row) => {
      const box = h('span', 'cu-flags');
      box.append(badge(row.status ? 'Aktif' : 'Pasif', row.status ? 'good' : 'dim'));
      if (row.verified === false) box.append(badge('doğrulanmadı', 'warn'));
      return box;
    },
  },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.segment) {
    return emptyState({
      title: 'Bu segmentte müşteri yok',
      text: `“${SEGMENT_LABELS[state.segment]}” eşiklerine uyan müşteri bulunamadı. `
        + 'Eşikler modül ayarından değiştirilebilir.',
      actions: [button('Segmenti kaldır', {
        onClick: () => { nodes.chips.set(null); applySegment(null); },
      })],
    });
  }
  return emptyState({
    title: 'Bu filtreye uyan müşteri yok',
    text: 'Süzgeçleri gevşetin ya da temizleyin.',
    actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  if (state.source === 'segment') {
    wrap.append(alertBox(
      'Segment, harcama ve tarih süzgeçlerini mağazanın müşteri ucu kabul etmiyor; '
      + 'bu liste nüfusun bir kez taranmış kopyasından süzülüyor. Tarama bellekte tutulur, '
      + 'diske yazılmaz. Satır açıldığında künye her zaman CANLI okunur.', 'info'));
  }
  if (state.statsError) {
    wrap.append(alertBox(
      `Sipariş toplamları okunamadı — ${state.statsError}. Mağazanın müşteri kaydında `
      + 'sipariş sayısı, harcama ve son sipariş tarihi YOKTUR; bu üçü siparişlerden '
      + 'sayılır. Sayılamadığı için segment, harcama ve “son sipariş” sütunları boş; '
      + 'sıfır varsayılmadı.', 'warn'));
  } else if (state.statsTruncated) {
    wrap.append(alertBox(
      'Sipariş taraması tavana dayandı; eksik listeyle “bu müşterinin siparişi yok” '
      + 'demek yanlış olacağından sipariş ve harcama sütunları doldurulmadı.', 'warn'));
  } else if (state.unknownSegments) {
    wrap.append(alertBox(
      `${num(state.unknownSegments)} müşterinin sipariş toplamı okunamadı; segmentleri `
      + '“Bilinmiyor” gösteriliyor. Sıfır varsayılmadı — varsayılsaydı sipariş vermiş '
      + 'müşteriler “hiç sipariş vermemiş” listesine düşerdi.', 'warn'));
  }

  nodes.table = dataTable({
    columns: COLUMNS,
    rows: state.items,
    selectable: true,
    empty: emptyNode(),
    onRow: (row) => openCustomer(row.id),
    onSelect: (ids) => {
      state.selection = ids;
      renderSelectionBar();
    },
  });
  wrap.append(nodes.table.node);
  renderSelectionBar();
}

function renderSelectionBar() {
  const bar = nodes.selbar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.selection.length;
  if (!count) {
    bar.classList.remove('on');
    return;
  }
  bar.classList.add('on');
  bar.append(h('b', undefined, `${num(count)} müşteri seçildi`));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Aktif yap', { onClick: () => bulkStatus(true) }),
    button('Pasifleştir', { variant: 'danger', onClick: () => bulkStatus(false) }),
    button('Seçimi CSV al', { onClick: exportSelection }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => {
        nodes.table.clearSelection();
        state.selection = [];
        renderSelectionBar();
      },
    }),
  );
}

async function bulkStatus(active) {
  const ids = state.selection.map(Number);
  const reason = await askReason({
    title: active ? 'Seçili müşterileri aktifleştir' : 'Seçili müşterileri pasifleştir',
    description: `${num(ids.length)} müşteri etkilenecek. `
      + (active
        ? 'Müşteriler yeniden giriş yapabilir.'
        : 'Pasif müşteri giriş yapamaz. SİLİNMEZ: siparişleri, faturaları ve iadeleri '
          + 'olduğu gibi kalır.'),
    confirmLabel: active ? 'Aktifleştir' : 'Pasifleştir',
  });
  if (!reason) return;
  await withBusy('Uygulanıyor…', async () => {
    const result = await call(`${BASE}/customers/status`, {
      method: 'POST', body: { customerIds: ids, active, reason, dryRun: false },
    });
    toast(`${num(result.count)} müşteri güncellendi.`, 'good');
    if (result.notice) toast(result.notice, 'warn');
    nodes.table?.clearSelection();
    state.selection = [];
    refresh();
    loadOverview({ refreshScan: true });
  });
}

function applySegment(key) {
  state.segment = key;
  refresh({ page: 1 });
}

// ------------------------------------------------------------------- CSV

function rowsToCsv(rows, filename) {
  const headers = ['Ad Soyad', 'E-posta', 'Telefon', 'Grup', 'Segment', 'Sipariş',
    'Toplam harcama', 'Ort. sepet', 'Son sipariş', 'Kayıt', 'Şehir', 'Durum'];
  const table = rows.map((row) => [
    row.name, row.email, row.phone, row.groupName,
    SEGMENT_LABELS[row.segment] || row.segment,
    row.orders ?? '—',
    row.spend === null || row.spend === undefined ? '—' : money(row.spend),
    row.avgBasket === null || row.avgBasket === undefined ? '—' : money(row.avgBasket),
    row.lastOrderAt || '—', row.createdAt || '—', row.city || '—',
    row.status ? 'Aktif' : 'Pasif',
  ]);
  const written = csvBlob(headers, table, filename);
  toast(`${num(written)} satır indirildi. Dosya kişisel veri taşır.`, 'good');
}

function exportVisible() {
  rowsToCsv(state.items, `musteriler-sayfa-${state.page}`);
}

function exportSelection() {
  const chosen = new Set(state.selection.map(String));
  rowsToCsv(state.items.filter((row) => chosen.has(String(row.id))), 'musteriler-secim');
}

async function exportAll() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm müşterileri dışa aktar',
    description: 'Müşteri nüfusu mağazadan sayfa sayfa çekilir ve rapor klasörüne CSV '
      + 'olarak yazılır. DOSYA KİŞİSEL VERİ TAŞIR: ad, e-posta ve telefon içerir, '
      + '0600 izinle yazılır ve paylaşımı KVKK sorumluluğundadır.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Müşteriler taranıyor…', async () => {
    const filters = currentFilters();
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: {
        q: filters.q, segment: filters.segment, groupId: Number(filters.groupId) || 0,
        status: filters.status, city: filters.city,
      },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) toast('Tarama tavana dayandı; dosya eksik olabilir.', 'warn');
  });
}

// ================================================================ çekmece

async function openCustomer(customerId) {
  // Çekmecenin kendi kaynakları (formGrid → tarih alanı global dinleyici tutar)
  // çekmece kapanınca bırakılır; panel cleanup'ına bırakılırsa her açılışta bir
  // tane daha birikir.
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Müşteri yükleniyor…', subtitle: `#${customerId}`, onClose: dropForms,
  });
  closers.push(dropForms);
  box.body.append(skeletonRows(6, 3));

  let payload;
  try {
    payload = await call(`${BASE}/customers/${customerId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Müşteri okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const customer = payload.customer;
  box.setTitle(customer.name);
  box.body.replaceChildren();

  const tabs = tabBar([
    { key: 'summary', label: 'Özet' },
    { key: 'orders', label: 'Siparişler' },
    { key: 'addresses', label: 'Adresler' },
    { key: 'returns', label: 'İadeler' },
    { key: 'reviews', label: 'Yorumlar' },
    { key: 'consents', label: 'İzin geçmişi' },
    { key: 'notes', label: 'Notlar' },
    { key: 'history', label: 'İşlem geçmişi' },
  ], 'summary', (key) => paint(key));
  tabs.badge('orders', payload.orders.length || undefined);

  const head = h('div', 'cu-drawer-head');
  head.append(
    badge(customer.status ? 'Aktif' : 'Pasif', customer.status ? 'good' : 'dim'),
    segmentBadge(customer),
    badge(customer.groupName || 'Grup', 'info'),
    h('code', 'cu-mail', customer.email || '—'),
  );
  const pane = h('div', 'cu-pane');
  box.body.append(head, tabs.node, pane);

  if (payload.warnings && payload.warnings.length) {
    box.body.insertBefore(
      alertBox(`Bazı parçalar okunamadı — ${payload.warnings.join(' · ')}`, 'warn'), pane);
  }

  function paint(key) {
    dropForms();
    pane.replaceChildren();
    const painter = {
      summary: paintSummary, orders: paintOrders, addresses: paintAddresses,
      returns: paintReturns, reviews: paintCustomerReviews, consents: paintConsents,
      notes: paintNotes, history: paintHistory,
    }[key];
    painter?.(pane, payload, forms, box);
  }
  paint('summary');
}

function paintSummary(pane, payload, forms, box) {
  const customer = payload.customer;

  const tiles = kpiRow([
    { label: 'Segment', value: payload.segmentLabel },
    {
      label: 'Sipariş',
      value: customer.orders === null || customer.orders === undefined
        ? '—' : num(customer.orders),
    },
    {
      label: 'Toplam harcama',
      value: customer.spend === null || customer.spend === undefined
        ? '—' : money(customer.spend),
    },
    {
      label: 'Ortalama sepet',
      value: customer.avgBasket === null || customer.avgBasket === undefined
        ? '—' : money(customer.avgBasket),
    },
  ]);

  // Sparkline TEK BAŞINA okunmaz; yanında toplam ve aylar yazılı durur.
  const spark = h('div', 'cu-spark');
  const values = (payload.spark || []).map((item) => item.total);
  spark.append(h('span', 'cu-sub', 'Son 6 ay harcaması: '));
  spark.append(sparkline(values, { width: 180, height: 34 }));
  spark.append(h('b', undefined, money(values.reduce((acc, value) => acc + value, 0))));
  const months = h('div', 'cu-months');
  for (const item of payload.spark || []) {
    const cell = h('span', 'cu-month');
    cell.append(h('span', 'cu-sub', item.month), h('b', undefined, money(item.total)));
    months.append(cell);
  }

  const form = formGrid({
    fields: [
      { key: 'firstName', label: 'Ad', type: 'text', maxLength: 60 },
      { key: 'lastName', label: 'Soyad', type: 'text', maxLength: 60 },
      { key: 'email', label: 'E-posta', type: 'email', maxLength: 120, wide: true },
      { key: 'phone', label: 'Telefon', type: 'phone' },
      {
        key: 'groupId',
        label: 'Müşteri grubu',
        type: 'select',
        options: state.reference.groups.map((item) => ({ value: item.id, label: item.name })),
      },
      {
        key: 'newsletter',
        label: 'Bülten aboneliği',
        type: 'checkbox',
        hint: 'Değişiklik izin geçmişine gerekçesiyle yazılır (KVKK).',
      },
    ],
    value: {
      firstName: (customer.name || '').split(' ').slice(0, -1).join(' ') || customer.name,
      lastName: (customer.name || '').split(' ').slice(-1)[0] || '',
      email: customer.email,
      phone: customer.phone,
      groupId: customer.groupId,
      newsletter: customer.newsletter === true,
    },
  });
  forms.push(form);

  const actions = h('div', 'cu-actions');
  actions.append(
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
        const patch = form.patch();
        if (!Object.keys(patch).length) { toast('Değişen alan yok.', 'warn'); return; }
        const reason = await askReason({
          title: 'Müşteri künyesini güncelle',
          description: `${customer.name} · ${form.dirty().length} alan değişti. Kayıt taze `
            + 'okunur ve dokunmadığınız alanlar mağazadaki güncel değeriyle geri gönderilir.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        const result = await withBusy('Kaydediliyor…', () => call(
          `${BASE}/customers/${customer.id}`,
          { method: 'PUT', body: { patch, reason, dryRun: false } },
        ));
        if (!result) return;
        toast('Kaydedildi.', 'good');
        form.reset(form.draft());
        refresh();
      },
    }),
    button(customer.status ? 'Pasifleştir' : 'Aktifleştir', {
      variant: customer.status ? 'danger' : '',
      onClick: async () => {
        state.selection = [String(customer.id)];
        await bulkStatus(!customer.status);
        box.close();
      },
    }),
    button('Müşteri karnesi PDF', {
      onClick: () => report.run('card', { customerId: customer.id }),
    }),
    button('Şifre sıfırlama bağlantısı — hazır değil', {
      disabled: true,
      title: 'Mağaza geçidinde şifre sıfırlama ucu henüz yok; uç hazır olunca açılacak.',
    }),
  );
  // KAPALI DÜĞMENİN NEDENİ EKRANDA YAZILI OLMALI: `title` ipucu kapalı
  // düğmede pek çok tarayıcıda hiç görünmez ve düğme sessiz ölü düğmeye
  // dönüşür — kullanıcı tıklar, hiçbir şey olmaz, kimse nedenini bilmez.
  actions.append(h('div', 'cu-sub',
    'Şifre sıfırlama bağlantısı gönderilemiyor: mağaza geçidinde (store_api) bu uç '
    + 'yok. Müşteri vitrindeki “şifremi unuttum” akışını kullanmalı.'));

  pane.append(
    tiles,
    card('Harcama eğilimi', spark, 'Aylık toplamlar'),
    months,
    card('Künye', form.node),
    actions,
    hintBox('Segment MUTLAK eşiklerle hesaplanır (ayardan değiştirilebilir): son sipariş '
      + 'kaç gün önce, kaç sipariş, ne kadar harcama. Nüfusa göre değişen dilim '
      + 'kullanılsaydı aynı müşteri iki dönemde iki farklı segmentte görünürdü.'),
    payload.customer.computed
      ? hintBox('Sipariş sayısı ve harcama mağazanın müşteri kaydında yoktu; siparişler '
        + 'listesinden hesaplandı.')
      : h('span'),
  );
}

function paintOrders(pane, payload) {
  if (payload.ordersUnverifiable) {
    pane.append(alertBox(
      'Sipariş satırlarının hangi müşteriye ait olduğu doğrulanamadı; başkasının '
      + 'siparişini bu müşteriye ait gibi göstermemek için liste boş bırakıldı.', 'bad'));
    return;
  }
  if (payload.ordersLocalFiltered) {
    pane.append(alertBox(
      'Mağaza sipariş süzgecini uygulamıyor (tüm siparişleri döndürüyor); liste bu '
      + 'müşterinin kimliğine göre burada süzüldü. Sayılar bu süzülmüş listeye göredir.',
      'info'));
  }
  if (!payload.orders.length) {
    pane.append(emptyState({
      title: 'Bu müşterinin siparişi yok',
      text: 'Kayıt açılmış ama alışveriş yapılmamış. Bülten ve kampanya listelerinde bu '
        + 'grup ayrı ele alınır.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'increment', label: 'Sipariş', width: 'minmax(0, 1.2fr)', className: 'mono' },
      { key: 'createdAt', label: 'Tarih', width: '150px' },
      { key: 'statusLabel', label: 'Durum', width: '140px' },
      {
        key: 'total',
        label: 'Tutar',
        width: '120px',
        align: 'num',
        cell: (row) => money(row.total),
      },
    ],
    rows: payload.orders,
    dense: true,
    onRow: (row) => openPanel?.('store_orders', { orderId: row.id }),
  });
  pane.append(table.node,
    hintBox('Satıra tıklamak Siparişler ekranını o siparişle açar; sipariş ayrıntısı '
      + 'burada tekrarlanmaz.'));
}

async function paintAddresses(pane, payload) {
  pane.append(skeletonRows(3, 3));
  let result;
  try {
    result = await call(`${BASE}/customers/${payload.customer.id}/addresses`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Kayıtlı adres yok',
      text: 'Müşteri henüz adres girmemiş; ilk siparişte adres oluşur.',
    }));
    return;
  }
  const list = h('div', 'cu-cards');
  for (const item of result.items) {
    const box = h('div', 'cu-card');
    const title = h('div', 'cu-card-head');
    title.append(h('b', undefined, item.name || item.title));
    if (item.default) title.append(badge('varsayılan', 'info'));
    box.append(title);
    box.append(h('div', undefined, item.line || '—'));
    box.append(h('div', 'cu-sub',
      [item.district, item.city, item.postcode, item.country].filter(Boolean).join(' · ')));
    if (item.phone) box.append(h('div', 'cu-sub', `Tel: ${item.phone}`));
    if (item.vatId) box.append(h('div', 'cu-sub', `VKN/TCKN: ${item.vatId}`));
    list.append(box);
  }
  pane.append(list, hintBox('Adres düzenleme mağaza tarafındadır: adres siparişe bağlı '
    + 've düzeltmesi müşterinin onayını gerektirir. Burada salt gösterilir.'));
}

async function paintReturns(pane, payload) {
  pane.append(skeletonRows(3, 4));
  let result;
  try {
    result = await call(`${BASE}/customers/${payload.customer.id}/returns`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  // UÇ CANLIDA YAYINDA (2026-08-16 doğrulandı): GET /api/admin/bbd/return-requests
  // 200 + gerçek talepler dönüyor. Bir dönem bu uç dağıtılmamıştı ve burada
  // "henüz yayında değil" yazıyordu; artık öyle değil, o metin okuyanı
  // mağazada olmayan bir eksiklik aramaya gönderiyordu. DAL DURUYOR (K7): uç
  // geri çekilir ya da düşerse sekme boş kalmaz, nedenini söyler.
  if (!result.available) {
    pane.append(alertBox(
      `İade/talep listesi okunamadı — ${result.error}. Mağazanın talep ucu şu an `
      + 'yanıt vermiyor; bağlantı düzelince bu sekme kendiliğinden dolacak.', 'warn'));
    return;
  }
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'İade veya talep yok',
      text: 'Bu müşteri hiç iade/değişim talebi açmamış.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'id', label: 'Talep', width: '90px', className: 'mono' },
      { key: 'createdAt', label: 'Açılış', width: '150px' },
      { key: 'kind', label: 'Tür', width: '120px' },
      { key: 'subject', label: 'Konu', width: 'minmax(0, 2fr)', className: 'wrap' },
      { key: 'status', label: 'Durum', width: '120px' },
    ],
    rows: result.items,
    dense: true,
    onRow: (row) => openPanel?.('store_requests', { requestId: row.id }),
  });
  pane.append(table.node);
}

async function paintCustomerReviews(pane, payload) {
  pane.append(skeletonRows(3, 4));
  let result;
  try {
    result = await call(`${BASE}/reviews?customerId=${payload.customer.id}&size=100`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (result.error) pane.append(alertBox(result.error, 'warn'));
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Bu müşteri yorum yazmamış',
      text: 'Tüm mağazanın yorum kuyruğu üstteki “Yorumlar” sekmesindedir.',
      actions: [button('Yorum kuyruğuna git', { onClick: () => nodes.tabs.select('reviews') })],
    }));
    return;
  }
  pane.append(reviewList(result.items));
}

async function paintConsents(pane, payload) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await call(`${BASE}/customers/${payload.customer.id}/consents`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();

  const current = h('div', 'cu-consents');
  for (const item of result.current || []) {
    const line = h('div', 'cu-consent');
    line.append(h('b', undefined, item.label));
    if (item.state === 'unknown') {
      line.append(badge('bilinmiyor', 'dim'));
      line.title = 'Mağaza bu izin için bir değer tutmuyor. "Onay yok" ile "kayıt yok" '
        + 'aynı şey değildir; uydurulmadı.';
    } else {
      line.append(badge(item.state === 'on' ? 'var' : 'yok',
        item.state === 'on' ? 'good' : 'dim'));
    }
    line.append(h('span', 'cu-sub', item.source));
    current.append(line);
  }

  const history = (result.history || []).length
    ? dataTable({
      columns: [
        { key: 'createdAt', label: 'Zaman', width: '150px' },
        { key: 'label', label: 'İzin', width: '160px' },
        { key: 'before', label: 'Önce', width: '90px' },
        { key: 'after', label: 'Sonra', width: '90px' },
        { key: 'actor', label: 'Kim', width: '130px' },
        { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
      ],
      rows: result.history,
      dense: true,
      rowKey: (row) => `${row.createdAt}-${row.kind}`,
    }).node
    : emptyState({
      title: 'İzin değişikliği kaydı yok',
      text: 'Bu müşterinin izinleri Kontrol Merkezi üzerinden hiç değiştirilmemiş.',
    });

  const requests = h('div');
  // KVKK UCU CANLIDA YAYINDA (2026-08-16 doğrulandı): GET
  // /api/admin/customers/gdpr-requests 200 dönüyor (bugün liste boş, ama uç
  // var). Bir dönem yedek metin "uç yayında değil" diyordu; bu artık yanlış
  // teşhis — hata metni gelmediğinde sebebi uydurmak yerine sebebin
  // bilinmediği söylenir. DAL DURUYOR (K7).
  if (!result.requestsAvailable) {
    requests.append(alertBox(
      `KVKK talep listesi okunamadı — ${result.requestsError || 'sebep bildirilmedi'}. `
      + 'Anonimleştirme talebe bağlı olduğu için o düğme de kapalı kalır.', 'warn'));
  } else if (!(result.requests || []).length) {
    requests.append(hintBox('Açık KVKK talebi yok.'));
  } else {
    requests.append(dataTable({
      columns: [
        { key: 'createdAt', label: 'Tarih', width: '150px' },
        { key: 'kind', label: 'Tür', width: '130px' },
        { key: 'status', label: 'Durum', width: '120px' },
        { key: 'message', label: 'Açıklama', width: 'minmax(0, 2fr)', className: 'wrap' },
      ],
      rows: result.requests,
      dense: true,
    }).node);
  }

  const actions = h('div', 'cu-actions');
  actions.append(
    button('KVKK veri paketi iste', {
      onClick: () => gdprExport(payload.customer),
    }),
    button('Anonimleştir', {
      variant: 'danger',
      title: 'GERİ ALINAMAZ. Ayrı izin gerektirir (store_customers.anonymize).',
      onClick: () => anonymize(payload.customer),
    }),
    button('Bülten aboneliğini değiştir — hazır değil', {
      disabled: true,
      title: 'Tek tıkla abonelik değişimi için mağaza geçidinde ayrı bir uç yok.',
    }),
  );
  actions.append(h('div', 'cu-sub',
    'Bülten aboneliğinin tek tıkla değiştirilmesi için mağaza geçidinde ayrı bir uç yok. '
    + 'Değişiklik Özet sekmesindeki “Bülten aboneliği” alanından yapılır; oradan yapılan '
    + 'değişiklik gerekçesiyle birlikte bu geçmişe düşer.'));

  pane.append(
    card('Şu anki izinler', current, 'Kaynak: müşteri kaydı'),
    card('Değişiklik geçmişi', history, 'Yalnız bu ekrandan yapılanlar'),
    card('KVKK talepleri', requests),
    actions,
    hintBox('Mağaza izin DEĞİŞİKLİĞİ geçmişi tutmuyor, yalnız o anki değeri var. '
      + '“Ne zaman, kim, hangi gerekçeyle” bilgisi bu modül açıldığı günden itibaren '
      + 'burada birikir; müşterinin vitrinden kendi yaptığı değişiklikler buraya düşmez.'),
  );
}

async function gdprExport(customer) {
  const reason = await askReason({
    title: 'KVKK veri paketi iste',
    description: `${customer.name} için mağaza kişisel veri paketini üretir. Paket ad, `
      + 'e-posta, adres ve sipariş geçmişini içerir; yalnız talebi yapan müşteriye, '
      + 'kimliği doğrulanarak teslim edilir.',
    confirmLabel: 'Paketi iste',
  });
  if (!reason) return;
  await withBusy('Veri paketi isteniyor…', async () => {
    const result = await call(`${BASE}/customers/${customer.id}/gdpr-export`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast(result.message, 'good');
    if (result.url) nodes.status.set(`Veri paketi: ${result.url}`);
    toast(result.notice, 'warn');
  });
}

async function anonymize(customer) {
  const reason = await askReason({
    title: 'KVKK anonimleştirme',
    description: `${customer.name} · GERİ ALINAMAZ. Ad, e-posta ve telefon kalıcı olarak `
      + 'silinir; siparişler ve faturalar kalır ama müşteriye bağlanamaz. İşlem mağazanın '
      + 'KVKK talebi üzerinden yürür: müşterinin açık talebi yoksa yapılmaz.',
    confirmLabel: 'Anonimleştir',
  });
  if (!reason) return;
  await withBusy('Anonimleştiriliyor…', async () => {
    const result = await call(`${BASE}/customers/${customer.id}/anonymize`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast('Anonimleştirme uygulandı.', 'good');
    toast(result.notice, 'warn');
    refresh();
  });
}

async function paintNotes(pane, payload, forms) {
  pane.append(skeletonRows(3, 2));
  let result;
  try {
    result = await call(`${BASE}/customers/${payload.customer.id}/notes`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();

  const form = formGrid({
    fields: [{
      key: 'note', label: 'Yeni not', type: 'textarea', wide: true, maxLength: 2000,
      hint: 'Not mağazaya yazılır ve müşteri temsilcileri de görür. Müşteri görmez.',
    }],
    value: { note: '' },
  });
  forms.push(form);

  const actions = h('div', 'cu-actions');
  actions.append(button('Not ekle', {
    variant: 'primary',
    onClick: async () => {
      const note = (form.draft().note || '').trim();
      if (note.length < 3) { toast('Not en az 3 karakter olmalı.', 'bad'); return; }
      const reason = await askReason({
        title: 'Müşteri notu ekle',
        description: 'Not mağazadaki müşteri kaydına yazılır ve silinemez.',
        confirmLabel: 'Ekle',
      });
      if (!reason) return;
      const done = await withBusy('Not ekleniyor…', () => call(
        `${BASE}/customers/${payload.customer.id}/notes`,
        { method: 'POST', body: { note, reason, dryRun: false } },
      ));
      if (!done) return;
      toast('Not eklendi.', 'good');
      form.set('note', '');
    },
  }));

  const list = result.items.length
    ? dataTable({
      columns: [
        { key: 'createdAt', label: 'Zaman', width: '150px' },
        { key: 'actor', label: 'Kim', width: '140px' },
        { key: 'note', label: 'Not', width: 'minmax(0, 3fr)', className: 'wrap' },
      ],
      rows: result.items,
      dense: true,
      rowKey: (row) => String(row.id),
    }).node
    : emptyState({ title: 'Not yok', text: 'Bu müşteriye henüz not yazılmamış.' });

  pane.append(card('Not ekle', form.node), actions, card('Notlar', list));
}

async function paintHistory(pane, payload) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await call(`${BASE}/audit?customerId=${payload.customer.id}&limit=100`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Bu müşteriye bu ekrandan dokunulmadı',
      text: 'Kontrol Merkezi üzerinden yapılan her yazma gerekçesiyle burada listelenir.',
    }));
    return;
  }
  pane.append(dataTable({
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
  }).node,
  hintBox('Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydı gerekçe alanı '
    + 'taşımıyor; ağ koparsa “ne yapmaya çalıştık” bilgisi yalnız burada kalır.'));
}

// ============================================================ yorum kuyruğu

function starText(rating) {
  return `${'★'.repeat(rating)}${'☆'.repeat(5 - rating)} ${rating}`;
}

/** Yorum kartları — hem kuyrukta hem müşteri çekmecesinde AYNI, moderasyon
 *  düğmeleriyle birlikte. Çekmecede düğmeleri kısmak, "müşteriye bakarken
 *  gördüğüm yorumu buradan işleyemiyorum" demek olurdu. */
function reviewList(items) {
  const box = h('div', 'cu-reviews');
  for (const item of items) {
    const cell = h('div', 'cu-review');
    const head = h('div', 'cu-review-head');
    // Renk tek başına anlam taşımaz: yıldızın yanında sayı da yazılı.
    head.append(h('span', 'cu-stars', starText(item.rating)));
    head.append(badge(REVIEW_LABELS[item.status] || item.status,
      REVIEW_TONES[item.status] || ''));
    if (item.spam) head.append(badge('spam işaretli', 'bad'));
    if (item.verifiedBuyer) head.append(badge('doğrulanmış alıcı', 'info'));
    if (!item.hasReply) head.append(badge('yanıtsız', 'warn'));
    head.append(h('span', 'kit-spacer'));
    head.append(h('span', 'cu-sub', item.createdAt));
    cell.append(head);

    if (item.title) cell.append(h('div', 'cu-review-title', item.title));
    cell.append(h('div', 'cu-review-body', item.comment || '(metin yok)'));
    const meta = h('div', 'cu-sub');
    meta.append(document.createTextNode(`${item.customerName} · ${item.productName}`));
    cell.append(meta);
    if (item.reply) {
      cell.append(h('div', 'cu-reply', `Mağaza yanıtı: ${item.reply}`));
    }

    const tools = h('div', 'cu-review-tools');
    tools.append(
      button('Onayla', { onClick: () => moderate([item.id], 'approve') }),
      button('Reddet', { onClick: () => moderate([item.id], 'reject') }),
      button('Spam', {
        variant: 'danger',
        title: 'Mağazada spam durumu yok: yorum reddedilir ve burada spam etiketlenir.',
        onClick: () => moderate([item.id], 'spam'),
      }),
      button('Yanıt yaz', { variant: 'primary', onClick: () => replyTo(item) }),
      button('Ürüne git', {
        variant: 'ghost',
        disabled: !item.productId,
        onClick: () => openPanel?.('store_products', { productId: item.productId }),
      }),
    );
    cell.append(tools);
    box.append(cell);
  }
  return box;
}

async function moderate(ids, action) {
  const titles = {
    approve: 'Yorumu onayla', reject: 'Yorumu reddet', spam: 'Yorumu spam işaretle',
    pending: 'Yorumu beklemeye al',
  };
  const descriptions = {
    approve: 'Yorum vitrinde yayınlanır.',
    reject: 'Yorum vitrinden kalkar. SİLİNMEZ: kaydı ve puanı durur.',
    spam: 'Mağazada spam durumu yoktur: yorum REDDEDİLİR ve burada spam olarak '
      + 'etiketlenir. Böylece vitrin doğru olur, kararınız da kaybolmaz.',
    pending: 'Yorum yeniden onay kuyruğuna girer.',
  };
  const reason = await askReason({
    title: titles[action],
    description: `${num(ids.length)} yorum · ${descriptions[action]}`,
    confirmLabel: 'Uygula',
  });
  if (!reason) return;
  await withBusy('Uygulanıyor…', async () => {
    const result = await call(`${BASE}/reviews/status`, {
      method: 'POST', body: { reviewIds: ids, action, reason, dryRun: false },
    });
    toast(`${num(result.count)} yorum güncellendi.`, 'good');
    if (result.notice) toast(result.notice, 'warn');
    refreshReviews();
  });
}

async function replyTo(item) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog cu-reply-box');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title', 'Mağaza yanıtı yaz'));
  box.append(h('p', 'kit-dialog-text',
    `${item.customerName} · ${starText(item.rating)} · ${item.productName}. `
    + 'Yanıt VİTRİNDE yayınlanır ve herkes görür.'));
  const area = h('textarea', 'kit-textarea');
  area.maxLength = 2000;
  area.value = item.reply || '';
  box.append(area);

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const actions = h('div', 'kit-dialog-actions');
  actions.append(
    button('Vazgeç', { onClick: close }),
    button('Yanıtla', {
      variant: 'primary',
      onClick: async () => {
        const body = area.value.trim();
        if (body.length < 5) { toast('Yanıt en az 5 karakter olmalı.', 'bad'); return; }
        const reason = await askReason({
          title: 'Mağaza yanıtını yayınla',
          description: 'Yanıt vitrinde yayınlanır; gerekçe denetim kaydına yazılır.',
          confirmLabel: 'Yayınla',
        });
        if (!reason) return;
        const done = await withBusy('Yanıt gönderiliyor…', () => call(
          `${BASE}/reviews/${item.id}/reply`,
          { method: 'POST', body: { body, reason, dryRun: false } },
        ));
        if (!done) return;
        toast('Yanıt yayınlandı.', 'good');
        close();
        refreshReviews();
      },
    }),
  );
  box.append(actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);
  area.focus();
}

function renderRatingPicker() {
  const box = h('div', 'cu-stars-picker');
  box.append(h('span', 'kit-filter-label', 'Puan'));
  for (let star = 5; star >= 1; star -= 1) {
    const chip = h('button', 'kit-chip', `${star} ★`);
    chip.type = 'button';
    chip.setAttribute('aria-pressed', 'false');
    chip.addEventListener('click', () => {
      const chosen = new Set(state.reviews.ratings);
      if (chosen.has(star)) chosen.delete(star);
      else chosen.add(star);
      state.reviews.ratings = [...chosen].sort();
      chip.classList.toggle('on', chosen.has(star));
      chip.setAttribute('aria-pressed', chosen.has(star) ? 'true' : 'false');
      refreshReviews({ page: 1 });
    });
    box.append(chip);
  }
  return box;
}

async function refreshReviews({ page = state.reviews.page, size = state.reviews.size } = {}) {
  if (!nodes.reviewBody) return;
  nodes.reviewBody.replaceChildren(skeletonRows(5, 3));
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('size', String(size));
  if (state.reviews.q) params.set('q', state.reviews.q);
  if (state.reviews.status) params.set('status', state.reviews.status);
  if (state.reviews.ratings.length) params.set('ratings', state.reviews.ratings.join(','));
  if (state.reviews.unanswered) params.set('unanswered', '1');

  let payload;
  try {
    payload = await api(`${BASE}/reviews?${params.toString()}`);
  } catch (error) {
    nodes.reviewBody.replaceChildren(emptyState({
      title: 'Yorumlar okunamadı',
      text: error.message,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshReviews() })],
    }));
    return;
  }

  state.reviews = {
    ...state.reviews,
    items: payload.items || [],
    total: payload.total || 0,
    page: payload.page || page,
    size: payload.size || size,
    pages: payload.pages || 0,
    summary: payload.summary || null,
    loaded: true,
  };
  nodes.tabs.badge('reviews', payload.pending || undefined);
  renderReviewBody(payload);
}

function renderReviewBody(payload) {
  const body = nodes.reviewBody;
  body.replaceChildren();
  const summary = payload.summary || {};

  body.append(kpiRow([
    { label: 'Bekleyen', value: num(payload.pending || 0), tone: 'warn' },
    { label: 'Sayfada', value: num(summary.total || 0) },
    { label: 'Yanıtsız', value: num(summary.unanswered || 0), tone: 'warn' },
    {
      label: 'Ortalama puan',
      value: summary.average === null || summary.average === undefined
        ? '—' : String(summary.average),
    },
  ]));

  if (summary.breakdown && summary.total) {
    const chart = h('div', 'cu-breakdown');
    chart.append(h('div', 'cu-sub', 'Puan dağılımı (sayılar çubuğun üstünde)'));
    chart.append(stackedBar(summary.breakdown, { width: 680 }));
    body.append(card('Puan dağılımı', chart));
  }

  if (payload.error) body.append(alertBox(payload.error, 'warn'));
  if (payload.clientFiltered) {
    body.append(alertBox(
      'Mağaza yorum ucu tek puan süzgeci kabul ediyor; çoklu puan, yanıtsız ve metin '
      + 'araması BU SAYFA üzerinde süzüldü. Sayfa dışındaki yorumlar bu süzgeçten '
      + 'geçmedi — daraltmak için durum süzgecini ve sayfa boyutunu kullanın.', 'info'));
  }

  if (!state.reviews.items.length) {
    body.append(emptyState({
      title: 'Onay bekleyen yorum yok',
      text: 'Kuyruk temiz — bu süzgeçte işlenmemiş yorum kalmamış.',
      actions: [button('Süzgeci temizle', {
        onClick: () => {
          state.reviews = { ...state.reviews, q: '', status: '', ratings: [], unanswered: false };
          nodes.reviewFilters.reset();
        },
      })],
    }));
    return;
  }

  body.append(reviewList(state.reviews.items));
  nodes.reviewPager.update({
    total: state.reviews.total, page: state.reviews.page, size: state.reviews.size,
  });
  body.append(nodes.reviewPager.node);
}

// ================================================================ ayarlar

let settingsForms = [];

async function renderSettings(host) {
  settingsForms.forEach((form) => form.destroy());
  settingsForms = [];
  host.replaceChildren(skeletonRows(4, 2));
  let payload;
  try {
    payload = await call(`${BASE}/settings`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  if (!payload.storeAvailable) {
    host.append(alertBox(
      `Mağaza ayarları okunamadı — ${payload.error}. Ayar yazılamaz; liste ve yorum `
      + 'sekmeleri çalışmaya devam eder.', 'bad'));
    return;
  }

  const store = payload.store || {};
  const found = (slot) => store[slot]?.found;
  const truthy = (slot) => Boolean(Number(store[slot]?.value ?? 0));
  const fields = [];

  if (found('newsletterDefault')) {
    fields.push({
      key: 'newsletterDefault', label: 'Yeni kayıtta bülten aboneliği', type: 'checkbox',
      hint: 'Açıkken yeni kaydolan müşteri bültene abone başlar. KVKK açısından açık rıza '
        + 'gerektiren bir varsayılandır; kapatmak daha güvenli taraftır.',
    });
  }
  if (found('emailVerification')) {
    fields.push({
      key: 'emailVerification', label: 'E-posta doğrulama iste', type: 'checkbox',
      hint: 'Açıkken müşteri e-postasını doğrulamadan giriş yapamaz.',
    });
  }
  if (found('defaultGroup')) {
    fields.push({
      key: 'defaultGroup', label: 'Varsayılan müşteri grubu', type: 'select',
      // Değer grup KODUDUR (`general`), kimlik DEĞİL: mağaza ayarı kodla
      // tutuyor ve kimlik yazmak ayarı sessizce bozardı.
      options: (payload.groups || [])
        .filter((item) => item.code)
        .map((item) => ({ value: item.code, label: `${item.name} (${item.code})` })),
      hint: 'Yeni kaydolan müşteri bu gruba düşer; grup fiyatlarını etkiler. Ayar grup '
        + 'kodunu tutar.',
    });
  }
  if (found('gdprEnabled')) {
    fields.push({
      key: 'gdprEnabled', label: 'KVKK modülü açık', type: 'checkbox',
      hint: 'Açıkken müşteri vitrinden veri paketi ve silme talebi açabilir.',
    });
  }
  if (found('gdprText')) {
    fields.push({
      key: 'gdprText', label: 'KVKK aydınlatma metni', type: 'textarea', wide: true,
      maxLength: 20000,
      hint: 'Kayıt formunda gösterilir. Mesafeli satış ve iade koşulları bu metinde değil, '
        + 'CMS ekranındaki yasal sayfalarda durur.',
    });
  }

  const missing = Object.keys(store).filter((slot) => !store[slot]?.found);
  if (missing.length) {
    host.append(alertBox(
      'Şu ayarların anahtarı mağaza yapılandırmasında bulunamadı: '
      + `${missing.map((slot) => store[slot]?.label || slot).join(', ')}. `
      + 'Bulunmayan anahtara yazmak hiçbir şeyi değiştirmez ve “kaydettim” yanılgısı '
      + `üretir; bu yüzden alan hiç gösterilmiyor. Anahtar adları modül ayarından `
      + `düzeltilebilir (bölüm: ${payload.slug} · ${payload.gdprSlug}).`, 'warn'));
  }

  if (!fields.length) {
    host.append(emptyState({
      title: 'Yazılabilir ayar bulunamadı',
      text: 'Mağaza yapılandırmasında bu ekranın tanıdığı anahtarların hiçbiri yok.',
    }));
    return;
  }

  const form = formGrid({
    fields,
    value: {
      newsletterDefault: truthy('newsletterDefault'),
      emailVerification: truthy('emailVerification'),
      defaultGroup: String(store.defaultGroup?.value ?? ''),
      gdprEnabled: truthy('gdprEnabled'),
      gdprText: String(store.gdprText?.value ?? ''),
    },
  });
  settingsForms.push(form);

  const actions = h('div', 'cu-actions');
  actions.append(button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      const body = {};
      for (const field of fields) {
        if (field.key === 'defaultGroup') body.defaultGroup = String(draft.defaultGroup ?? '');
        else if (field.key === 'gdprText') body.gdprText = String(draft.gdprText ?? '');
        else body[field.key] = Boolean(draft[field.key]);
      }
      const reason = await askReason({
        title: 'Müşteri ayarlarını kaydet',
        description: 'Bu ayarlar vitrini doğrudan etkiler: kayıt zorunluluğu, doğrulama ve '
          + 'KVKK metni. Gerekçe denetim kaydına yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Ayarlar kaydediliyor…', async () => {
        const result = await call(`${BASE}/settings`, {
          method: 'POST', body: { ...body, reason, dryRun: false },
        });
        toast(`Kaydedildi: ${result.changed.join(', ') || 'değişiklik yok'}`, 'good');
        if (result.skipped.length) {
          toast(`Yazılamayan ayar: ${result.skipped.join(', ')} (anahtar bulunamadı).`, 'warn');
        }
      });
    },
  }));

  host.append(
    card('Müşteri kaydı ve KVKK', form.node, 'Vitrini doğrudan etkiler'),
    actions,
    hintBox('Ayrı bir “Mağaza Ayarları” ekranı yoktur: her ayar onu kullanan ekranda durur. '
      + 'Müşteri kaydı ve KVKK burada, vergi store_tax’ta, kargo ücreti store_shipping’de, '
      + 'yasal metinler store_cms’te.'),
  );
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  openPanel = ctx.open;

  const view = h('div', 'kit-panel cu');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  nodes.tabs = tabBar([
    { key: 'list', label: 'Müşteriler' },
    { key: 'reviews', label: 'Yorumlar' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'list', (key) => showView(key));

  // ------------------------------------------------------------ müşteriler
  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ad, e-posta veya telefon ara', width: '240px' },
      { kind: 'select', key: 'group', label: 'Grup', options: [{ value: '', label: 'Tümü — grup' }] },
      {
        kind: 'select',
        key: 'status',
        label: 'Durum',
        options: [
          { value: '', label: 'Tümü' },
          { value: '1', label: 'Aktif' },
          { value: '0', label: 'Pasif' },
          { value: 'unverified', label: 'E-posta doğrulanmamış' },
        ],
      },
      { kind: 'search', key: 'city', placeholder: 'Şehir', width: '120px' },
      { kind: 'dateRange', key: 'registered', label: 'Kayıt' },
      { kind: 'dateRange', key: 'lastOrder', label: 'Son sipariş' },
      { kind: 'numRange', key: 'orders', label: 'Sipariş' },
      { kind: 'numRange', key: 'spend', label: 'Harcama', money: true },
      { kind: 'toggle', key: 'newsletter', label: 'Bülten aboneleri' },
    ],
    onChange: () => refresh({ page: 1 }),
    actions: [
      button('Yenile', {
        onClick: () => { refresh({ refreshScan: true }); loadOverview({ refreshScan: true }); },
      }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm kayıtları rapor klasörüne yaz', onClick: exportAll }),
      button('Segment raporu', { onClick: () => report.run('segments', {}) }),
    ],
  });

  // `count: 0` ZORUNLU: chipRow sayaç yuvasını yalnız `count` verilmişse kurar;
  // null geçmek `counts()` çağrısını sessizce etkisiz bırakıyordu.
  nodes.chips = chipRow(
    SEGMENTS.map((item) => ({ ...item, count: 0 })), null, (key) => applySegment(key));
  nodes.status = statusLine();
  nodes.kpi = h('div', 'cu-kpi');
  nodes.selbar = h('div', 'cu-selbar');
  nodes.tableWrap = h('div', 'cu-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size }),
  });

  // ---------------------------------------------------------------- yorum
  nodes.reviewFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Yorum, ürün veya müşteri ara', width: '240px' },
      {
        kind: 'select',
        key: 'status',
        label: 'Durum',
        options: [
          { value: '', label: 'Tümü' },
          { value: 'pending', label: 'Bekleyen' },
          { value: 'approved', label: 'Onaylı' },
          { value: 'disapproved', label: 'Reddedilen' },
        ],
      },
      { kind: 'toggle', key: 'unanswered', label: 'Yanıtlanmamış' },
    ],
    onChange: (values) => {
      state.reviews = {
        ...state.reviews,
        q: values.q || '',
        status: values.status || '',
        unanswered: Boolean(values.unanswered),
      };
      refreshReviews({ page: 1 });
    },
    actions: [
      button('Yenile', { onClick: () => refreshReviews() }),
      button('Yorum özeti PDF', { onClick: () => report.run('reviews', {}) }),
    ],
  });
  nodes.reviewStars = renderRatingPicker();
  nodes.reviewBody = h('div', 'cu-review-body-wrap');
  nodes.reviewPager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refreshReviews({ page, size }),
  });

  // Tek gövde kabı; sekme değişince İÇERİĞİ değişir. İki ayrı kap tutup
  // `replaceChild` yapmak kaydırma konumunu kaybettiriyor.
  nodes.body = h('div', 'cu-body');
  const listView = [nodes.kpi, nodes.selbar, nodes.tableWrap, nodes.pager.node];
  const reviewView = [nodes.reviewStars, nodes.reviewBody];
  nodes.body.append(...listView);

  view.append(nodes.tabs.node, nodes.filters.node, nodes.reviewFilters.node,
    nodes.chips.node, nodes.status.node, nodes.body);
  nodes.reviewFilters.node.hidden = true;

  function showView(key) {
    nodes.filters.node.hidden = key !== 'list';
    nodes.chips.node.hidden = key !== 'list';
    nodes.status.node.hidden = key !== 'list';
    nodes.reviewFilters.node.hidden = key !== 'reviews';
    if (key === 'settings') {
      nodes.body.replaceChildren();
      renderSettings(nodes.body);
    } else if (key === 'reviews') {
      nodes.body.replaceChildren(...reviewView);
      if (!state.reviews.loaded) refreshReviews();
    } else {
      nodes.body.replaceChildren(...listView);
      if (!state.loaded) refresh();
    }
  }

  root.replaceChildren(view);
  nodes.status.set('Müşteriler alınıyor…');
  // Referans listeler ÖNCE gelir: grup açılırı dolmadan liste çekmek,
  // kullanıcının seçtiği grubu kaybettiriyordu.
  // Liste ÖNCE gelir, KPI arkasından. İkisi aynı arka plan taramasını okuyor;
  // eş zamanlı çağırmak mağazaya iki ayrı tam tarama başlatıyordu (arıza).
  loadReference().then(() => refresh()).then(() => loadOverview());

  return () => {
    if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
    nodes.filters?.destroy();          // arama alanı bekleyen debounce tutar
    nodes.reviewFilters?.destroy();
    settingsForms.forEach((form) => form.destroy());
    settingsForms = [];
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE, reviews: { ...EMPTY_STATE.reviews } };
    busy = false;
  };
}
