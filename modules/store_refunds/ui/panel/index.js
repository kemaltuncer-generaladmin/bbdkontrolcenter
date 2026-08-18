// İadeler paneli — bir iadenin tamamı tek çekmecede biter.
//
// NE YAPAR: kredi notları (para) ile iade taleplerini (süreç) tek listede
// birleştirir; durum çipleri ve KPI (bekleyen tutar · bu ay iade · iade oranı)
// üstte durur; satır açılınca çekmecede sipariş kalemleri adet adet seçilir,
// İADE TUTARI SATIR SATIR gösterilir (ürün + indirim + KDV + kargo), gerekçeyle
// onaylanır, ardından POS iadesi ayrı bir adım olarak çağrılır.
//
// NE YAPMAZ:
//  · Tutarı istemcide hesaplamaz. Hesap tek yerde (backend `calc.py`) durur;
//    burada ikinci bir aritmetik olsaydı iki sayı arasında sessiz fark çıkardı.
//    Ekran seçim yapılırken YAKLAŞIK bir toplam gösterir ve öyle etiketler;
//    kesin tutar `Hesapla` ile gelir.
//  · Her tuşta mağazaya gitmez. Hesap ucu siparişi tazeler ve mağazanın kendi
//    önizlemesini sorar; bunu her değişiklikte yapmak dakikada 55 istek
//    kovasını tüketir ve ekranı kilitler.
//  · İade faturası KESMEZ. Fatura Fatura ekranının işidir; iki ekranın aynı
//    belgeyi kesmesi çift numara üretir.
//  · Kayıt SİLMEZ. Reddedilen talep de kayıttır ve listede kalır (ADR 0012).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Kredi notu ile talep AYNI olayın iki kaydıdır; liste onları tek satırda
//    birleştirir, yoksa bekleyen tutar iki kez sayılır.
//  · Faturalanmamış kalem iade EDİLEMEZ; adet kutusu 0'da kilitlidir.
//  · Kargo bedeli kendiliğinden iade edilmez — kutu her iadede tek tek işaretlenir.
//  · Mağazanın hesabı bizimkinden farklı çıkabilir; uygulanan MAĞAZANINKİDİR ve
//    fark ekranda kırmızı yazar.
//  · `/api/admin/bbd/*` uçları YAYINDA (ölçüm 2026-08-16); bir dönem 404
//    dönüyorlardı ve bu satır "hâlâ yazılıyor" diyordu. Uç yine de yoksa
//    (geri çekilirse) o bölüm KAPALI görünür ve "uç hazır olunca açılacak"
//    der; ekranın geri kalanı çalışır. Bölümün açık/kapalı olduğunu bu dosya
//    KARAR VERMEZ — sunucudan gelen `sources.*.available` çizer; buraya
//    gömülü bir "yayında değil" varsayımı ekranda yanlış durum çizerdi.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_refunds/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_refunds/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  blockedButton, button, clip, confirmWithReason, csvBlob, h, loadStyles, money, moneyInput,
  num, parseMoney, percent, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine,
} from '../../ui-kit/layout.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_refunds';

const STATUSES = [
  { key: 'requested', label: 'Talep', tone: 'info' },
  { key: 'approved', label: 'Onaylandı', tone: 'info' },
  { key: 'awaiting', label: 'Ürün bekleniyor', tone: 'warn' },
  { key: 'received', label: 'Ürün geldi', tone: 'warn' },
  { key: 'refunded', label: 'İade edildi', tone: 'good' },
  { key: 'rejected', label: 'Reddedildi', tone: 'dim' },
  { key: 'other', label: 'Bilinmiyor', tone: 'dim' },
];

const REASONS = [
  { value: 'disliked', label: 'Beğenmedi' },
  { value: 'defective', label: 'Ayıplı' },
  { value: 'wrong_item', label: 'Yanlış ürün' },
  { value: 'late', label: 'Geç teslim' },
  { value: 'withdrawal', label: 'Cayma hakkı' },
  { value: 'other', label: 'Diğer' },
];

const METHODS = [
  { value: 'pos', label: 'POS iadesi' },
  { value: 'transfer', label: 'Havale' },
  { value: 'credit', label: 'Mağaza kredisi' },
  { value: 'exchange', label: 'Değişim' },
  { value: 'unknown', label: 'Belirtilmedi' },
];

//: Süreç ucundan yazılabilen durumlar. `refunded` BİLEREK yok: para gerçekten
//: geri verilmeden "iade edildi" yazmak mutabakatta kapatılamayan kayıt bırakır.
const FLOW = [
  { key: 'approved', label: 'Onayla' },
  { key: 'awaiting', label: 'Ürün bekleniyor' },
  { key: 'received', label: 'Ürün geldi' },
  { key: 'rejected', label: 'Reddet' },
];

const FILTER_SPEC = {
  q: { kind: 'search', fields: ['number', 'orderNumber', 'customer', 'email', 'tracking', 'itemNames'] },
  status: { kind: 'equals', field: 'status' },
  reason: { kind: 'equals', field: 'reason' },
  method: { kind: 'equals', field: 'method' },
  carrier: { kind: 'equals', field: 'carrier' },
  amount: { kind: 'numRange', field: 'amount' },
  withdrawal: { kind: 'toggle', test: (row) => Boolean(row.flags?.withinWithdrawal) },
  posFailed: { kind: 'toggle', test: (row) => Boolean(row.flags?.posFailed) },
  late: { kind: 'toggle', test: (row) => Boolean(row.flags?.itemLate) },
};

const EMPTY_STATE = {
  items: [], filtered: [], summary: null, warnings: [], sources: {},
  settings: { freeReturnDays: 14, returnWaitDays: 7, shippingDefault: false, pageSize: 50 },
  range: { from: '', to: '' }, connected: false, error: '', truncated: false,
  // Durum süzgeci çiplerdedir, filtre şeridinde DEĞİL: aynı süzgeci iki yerde
  // tutmak, birinden temizleyip diğerinde asılı kalmasına yol açıyordu.
  chip: null, page: 1, size: 50, loaded: false,
  // Para çıktıktan sonra oluşan ve YENİLEMEYLE KAYBOLMAMASI gereken uyarı.
  notice: '',
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};

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

/** Yıkıcı ve para etkileyen her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function statusOf(key) {
  return STATUSES.find((item) => item.key === key) || STATUSES[STATUSES.length - 1];
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const window = `${state.range.from} – ${state.range.to}`;
  const shown = state.filtered.length;
  const all = state.items.length;
  const scope = shown === all ? `${num(all)} kayıt` : `${num(shown)} / ${num(all)} kayıt`;
  return `Bağlı · ${window} · ${scope}`;
}

// -------------------------------------------------------------------- veri

function currentRange() {
  const values = nodes.filters ? nodes.filters.values() : {};
  return values.range || {};
}

async function refresh() {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('İadeler alınıyor…');
  const range = currentRange();
  const query = new URLSearchParams();
  if (range.start) query.set('dateFrom', range.start);
  if (range.end) query.set('dateTo', range.end);

  let payload;
  try {
    // Bu uç, süzgece göre mağazanın TAMAMINI sayfa sayfa tarayabiliyor.
    // Kabuğun 60 saniyelik varsayılanı taramayı ortasında kesip hatayı
    // "çekirdeğe bağlanılamadı" diye gösteriyordu; süre açıkça istenir.
    payload = await api(`${BASE}/refunds?${query.toString()}`,
      { timeoutSeconds: 300 });
  } catch (error) {
    state = { ...state, connected: false, error: error.message, items: [], filtered: [] };
    renderAll();
    nodes.status?.set(statusText(), true);
    return;
  }

  state = {
    ...state,
    items: payload.items || [],
    summary: payload.summary || null,
    warnings: payload.warnings || [],
    sources: payload.sources || {},
    settings: payload.settings || state.settings,
    range: payload.range || state.range,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    truncated: Boolean(payload.truncated),
    page: 1,
    size: (payload.settings && payload.settings.pageSize) || state.size,
    loaded: true,
  };
  fillCarriers();
  applyView();
}

/** Taşıyıcı listesi VERİDEN gelir: sabit liste yazmak, yeni taşıyıcı
 *  eklendiğinde satırları süzgeç dışında bırakırdı. */
function fillCarriers() {
  const seen = [...new Set(state.items.map((row) => row.carrier).filter(Boolean))].sort();
  nodes.filters.options('carrier', [
    { value: '', label: 'Tümü — taşıyıcı' },
    ...seen.map((name) => ({ value: name, label: name })),
  ]);
}

/** Süzme İSTEMCİDE: ölçek "yüzler"; sunucuya gitmek gereksiz gecikme olurdu. */
function applyView() {
  const values = { ...(nodes.filters ? nodes.filters.values() : {}), status: state.chip || '' };
  state.filtered = applyFilters(state.items, values, FILTER_SPEC);
  const pages = Math.max(1, Math.ceil(state.filtered.length / state.size));
  if (state.page > pages) state.page = pages;
  renderAll();
}

function renderAll() {
  renderKpi();
  renderChips();
  renderTable();
  nodes.pager.update({ total: state.filtered.length, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
}

// ------------------------------------------------------------------- çizim

function renderKpi() {
  if (!nodes.kpi) return;
  const summary = state.summary;
  nodes.kpi.replaceChildren();
  if (state.notice) nodes.kpi.append(alertBox(state.notice, 'bad'));
  if (!summary) return;

  const rate = summary.refundRate;
  nodes.kpi.append(kpiRow([
    {
      label: 'Bekleyen tutar',
      value: money(summary.pendingAmount),
      tone: summary.pendingCount ? 'warn' : '',
      title: `${num(summary.pendingCount)} açık kayıt — parası henüz çıkmadı`,
    },
    {
      label: 'Bu ay iade',
      value: money(summary.monthAmount),
      title: `${summary.monthStart} – ${summary.monthEnd} · ${num(summary.monthCount)} iade`,
    },
    {
      label: 'İade oranı',
      value: rate === null || rate === undefined ? '—' : percent(rate),
      tone: rate !== null && rate !== undefined && rate >= 10 ? 'bad' : '',
      title: summary.rateNote || `Bu ayın iadesi / bu ayın satışı (${money(summary.salesTotal)})`,
    },
  ]));

  if (summary.rateNote) nodes.kpi.append(hintBox(summary.rateNote));
  const requests = state.sources.requests;
  if (requests && requests.available === false) {
    nodes.kpi.append(alertBox(
      `İade talepleri okunamadı — ${requests.error} Liste yalnız kesilmiş kredi notlarını `
      + 'gösteriyor; açık talepler eksik olabilir.', requests.missing ? 'info' : 'warn'));
  }
  for (const warning of state.warnings) {
    if (requests && requests.available === false && warning.includes('İade talepleri')) continue;
    nodes.kpi.append(alertBox(warning, 'warn'));
  }
  if (state.truncated) {
    nodes.kpi.append(alertBox('Liste tavana dayandı; tarih aralığını daraltın.', 'warn'));
  }
}

function renderChips() {
  const counts = {};
  for (const row of state.items) counts[row.status] = (counts[row.status] || 0) + 1;
  nodes.chips.counts(counts);
}

function amountCell(row) {
  const box = h('span', 'rf-amount');
  box.append(h('b', undefined, money(row.amount)));
  // Talep aşamasında tutar TAHMİNDİR; kesin rakam kalem seçilince hesaplanır.
  if (row.source === 'request') box.append(h('span', 'rf-sub', 'beklenen'));
  return box;
}

function statusCell(row) {
  const box = h('span', 'rf-status');
  const info = statusOf(row.status);
  // Renk tek başına anlam taşımaz: rozetin içinde her zaman yazı var.
  box.append(badge(row.statusLabel || info.label, info.tone));
  const marks = row.flags || {};
  if (marks.posFailed) box.append(badge('POS hatası', 'bad'));
  if (marks.itemLate) box.append(badge(`${num(marks.waitingDays)} gündür yolda`, 'warn'));
  if (marks.withinWithdrawal) box.append(badge('cayma süresinde', 'info'));
  return box;
}

function shippingCell(row) {
  if (!row.carrier && !row.tracking) return '—';
  const box = h('span', 'rf-ship');
  if (row.carrier) box.append(h('b', undefined, row.carrier));
  if (row.tracking) box.append(h('code', 'rf-code', row.tracking));
  return box;
}

const COLUMNS = [
  { key: 'number', label: 'İade no', width: '110px', className: 'mono' },
  { key: 'orderNumber', label: 'Sipariş', width: '110px', className: 'mono' },
  { key: 'date', label: 'Tarih', width: '100px', sortable: true },
  {
    key: 'customer',
    label: 'Müşteri',
    width: 'minmax(0, 1.8fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'rf-name');
      box.append(clip(h('b'), row.customer || '(isimsiz)', 32));
      if (row.itemNames) box.append(clip(h('span', 'rf-sub'), row.itemNames, 44));
      return box;
    },
  },
  // "Adet", "kalem" DEĞİL: kredi notu listesi kalemleri vermiyor (yalnız
  // detayda gelirler), sayı iade edilen ADETtir (`totalQty`).
  { key: 'itemCount', label: 'Adet', width: '70px', align: 'num',
    title: 'İade edilen adet',
    cell: (row) => (row.itemCount ? num(row.itemCount) : '—') },
  { key: 'reasonLabel', label: 'Sebep', width: '110px' },
  { key: 'amount', label: 'Tutar', width: '130px', align: 'num', sortable: true,
    cell: amountCell },
  { key: 'methodLabel', label: 'Yöntem', width: '110px' },
  { key: 'carrier', label: 'Kargo', width: '140px', cell: shippingCell },
  { key: 'status', label: 'Durum', width: 'minmax(0, 1.4fr)', cell: statusCell },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.items.length === 0) {
    return emptyState({
      title: 'Bu aralıkta iade yok',
      text: `${state.range.from} – ${state.range.to} arasında ne kredi notu ne de açık `
        + 'talep var. Aralığı genişletebilirsiniz.',
    });
  }
  const actions = [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })];
  if (state.chip) {
    actions.unshift(button('Durum çipini kaldır', {
      onClick: () => { nodes.chips.set(null); state.chip = null; applyView(); },
    }));
  }
  return emptyState({
    title: 'Bu filtreye uyan iade yok',
    text: `${num(state.items.length)} kayıt yüklü. Süzgeçleri gevşetin ya da temizleyin.`,
    actions,
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  const start = (state.page - 1) * state.size;
  const rows = state.filtered.slice(start, start + state.size);

  nodes.table = dataTable({
    columns: COLUMNS,
    rows,
    empty: emptyNode(),
    rowKey: (row) => row.key,
    onRow: (row) => openRefund(row),
  });
  wrap.replaceChildren(nodes.table.node);
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['İade no', 'Sipariş', 'Tarih', 'Müşteri', 'Adet', 'Sebep', 'Tutar',
    'Yöntem', 'Taşıyıcı', 'Takip no', 'Durum'];
  const rows = state.filtered.map((row) => [
    row.number, row.orderNumber, row.date, row.customer, row.itemCount,
    row.reasonLabel || '', money(row.amount), row.methodLabel, row.carrier,
    row.tracking, row.statusLabel,
  ]);
  const written = csvBlob(headers, rows, `iadeler-${todayIso()}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Dosya yazılıyor…', async () => {
    const range = currentRange();
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { dateFrom: range.start || '', dateTo: range.end || '' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ================================================================ çekmece

async function openRefund(row) {
  const box = drawer(nodes.root, {
    title: 'Sipariş yükleniyor…',
    subtitle: `${row.number} · ${row.orderNumber || `#${row.orderId}`}`,
    // Çekmece `document`'e Escape dinleyicisi bağlar ve yalnız `close()` onu
    // söker. Panel çekmece açıkken değiştirilirse dinleyici asılı kalırdı.
    onClose: () => { nodes.drawer = null; },
  });
  nodes.drawer = box;
  box.body.append(skeletonRows(6, 4));

  if (!row.orderId) {
    box.body.replaceChildren(emptyState({
      title: 'Sipariş bağlantısı yok',
      text: 'Bu kayıt bir siparişe bağlı değil; kalem seçimi ve iade hesabı yapılamaz.',
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  let payload;
  try {
    payload = await call(`${BASE}/orders/${row.orderId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Sipariş okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const order = payload.order;
  box.setTitle(`${order.orderNumber || `#${order.orderId}`} · ${order.customer}`);
  box.body.replaceChildren();

  const head = h('div', 'rf-head');
  const info = statusOf(row.status);
  head.append(
    badge(row.statusLabel || info.label, info.tone),
    badge(row.methodLabel, 'info'),
    h('span', 'rf-sub', `Sipariş ${money(order.grandTotal)}`),
    h('span', 'rf-sub', `Daha önce iade edilen ${money(order.totalRefunded)}`),
  );
  if (row.reasonLabel) head.append(badge(row.reasonLabel, ''));
  box.body.append(head);

  if (payload.warnings?.length) {
    box.body.append(alertBox(`Bazı parçalar okunamadı — ${payload.warnings.join(' · ')}`, 'warn'));
  }
  if (payload.basisNotice) box.body.append(alertBox(payload.basisNotice, 'warn'));

  box.body.append(
    refundSection(row, payload, box),
    processSection(row),
    posSection(row, payload),
    shippingSection(row, payload),
    notifySection(row),
    historySection(payload),
  );
  auditSection(box.body, row.orderId);
}

// ---------------------------------------------------- kalem seçimi + hesap

function refundSection(row, payload, box) {
  const order = payload.order;
  const wanted = new Map();          // kalemId → adet

  const rough = h('div', 'rf-rough');
  const result = h('div', 'rf-result');

  const shippingBox = h('input', 'kit-check');
  shippingBox.type = 'checkbox';
  // Geçmiş iadeler okunamadıysa "kargo daha önce verilmiş mi" bilinmiyor:
  // kutu KENDİLİĞİNDEN işaretlenmez, personel bilerek işaretler.
  shippingBox.checked = Boolean(payload.shippingDefault) && payload.shippingVerified !== false
    && order.shippingAvailable > 0;
  shippingBox.disabled = order.shippingAvailable <= 0;

  const extra = h('input', 'kit-input');
  extra.type = 'text';
  extra.placeholder = '0,00';
  extra.style.width = '110px';

  const fee = h('input', 'kit-input');
  fee.type = 'text';
  fee.placeholder = '0,00';
  fee.style.width = '110px';

  function paintRough() {
    // YAKLAŞIK: indirim ve KDV payları burada hesaplanmaz. İkinci bir aritmetik
    // yazmak, iki sayı arasında sessiz fark üretirdi; kesin tutar `Hesapla`'dan
    // gelir ve etiket bunu açıkça söyler.
    let units = 0;
    let approx = 0;
    for (const line of order.lines) {
      const qty = wanted.get(line.id) || 0;
      units += qty;
      approx += qty * line.unitPrice;
    }
    rough.replaceChildren();
    if (!units) {
      rough.append(h('span', 'rf-sub', 'Kalem seçilmedi.'));
      return;
    }
    rough.append(
      h('b', undefined, `${num(units)} adet seçildi`),
      h('span', 'rf-sub', ` · yaklaşık ${money(approx)} (KDV ve indirim payı hariç)`),
    );
  }

  const table = dataTable({
    columns: [
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2.2fr)' },
      { key: 'unitPrice', label: 'Birim', width: '110px', align: 'num',
        cell: (line) => money(line.unitPrice) },
      { key: 'qtyOrdered', label: 'Sipariş', width: '80px', align: 'num',
        cell: (line) => num(line.qtyOrdered) },
      { key: 'qtyRefunded', label: 'İade edilen', width: '96px', align: 'num',
        cell: (line) => num(line.qtyRefunded) },
      {
        key: 'refundable',
        label: 'İade edilecek',
        width: '150px',
        align: 'num',
        cell: (line) => qtyPicker(line),
      },
    ],
    rows: order.lines,
    dense: true,
    rowKey: (line) => String(line.id),
    empty: emptyState({ title: 'Kalem yok', text: 'Bu siparişte iade edilecek kalem yok.' }),
  });

  function qtyPicker(line) {
    const wrap = h('div', 'rf-qty');
    if (line.refundable <= 0) {
      // Faturalanmamış ya da tamamı iade edilmiş kalem: kutu YOK, sebebi yazılı.
      wrap.append(h('span', 'rf-sub',
        line.qtyInvoiced === 0 ? 'faturalanmadı' : 'tamamı iade edildi'));
      return wrap;
    }
    const input = h('input', 'kit-input');
    input.type = 'number';
    input.min = '0';
    input.max = String(line.refundable);
    input.value = '0';
    input.style.width = '70px';
    input.setAttribute('aria-label', `${line.name} iade adedi`);
    input.addEventListener('input', () => {
      const value = Math.max(0, Math.min(line.refundable, Number(input.value) || 0));
      if (String(value) !== input.value) input.value = String(value);
      wanted.set(line.id, value);
      result.replaceChildren();
      paintRough();
    });
    wrap.append(input, h('span', 'rf-sub', `/ ${num(line.refundable)}`));
    return wrap;
  }

  const controls = h('div', 'rf-controls');
  const shipLabel = h('label', 'rf-check');
  shipLabel.append(shippingBox, h('span', undefined,
    order.shippingAvailable > 0
      ? `Kargo bedelini de iade et (${money(order.shippingAvailable)})`
      : 'Kargo bedeli zaten iade edilmiş'));
  shippingBox.addEventListener('change', () => result.replaceChildren());
  controls.append(
    shipLabel,
    labelled('Ek iade', extra),
    labelled('Kesinti', fee),
    button('Hesapla', { variant: 'primary', onClick: () => runCalc() }),
  );

  async function runCalc() {
    const quantities = {};
    for (const [id, qty] of wanted) if (qty > 0) quantities[String(id)] = qty;
    const extraKurus = extra.value.trim() === '' ? 0 : parseMoney(extra.value);
    const feeKurus = fee.value.trim() === '' ? 0 : parseMoney(fee.value);
    if (extraKurus === null || feeKurus === null) {
      toast('Ek iade / kesinti tutarını 25,00 gibi yazın.', 'bad');
      return;
    }
    result.replaceChildren(skeletonRows(4, 3));
    let payloadCalc;
    try {
      payloadCalc = await call(`${BASE}/calculate`, {
        method: 'POST',
        body: {
          orderId: order.orderId,
          quantities,
          refundShipping: shippingBox.checked,
          adjustmentRefund: extraKurus,
          adjustmentFee: feeKurus,
        },
      });
    } catch (error) {
      result.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    paintResult(payloadCalc);
  }

  function paintResult(data) {
    const detail = data.calc;
    result.replaceChildren();

    const lines = h('div', 'rf-lines');
    for (const line of detail.lines) {
      lines.append(breakdownRow(
        `${line.sku} · ${line.name}`,
        `${num(line.qty)} × ${money(line.unitPrice)}`,
        line.total,
      ));
    }
    lines.append(breakdownRow('Ürün tutarı', '', detail.base, 'sum'));
    if (detail.discount) lines.append(breakdownRow('İndirim payı', 'düşülür', -detail.discount));
    lines.append(breakdownRow('KDV payı', 'eklenir', detail.tax));
    // Kargo hariçken "iade edilebilir" rakamı ÇEKMECENİN doğrulanmış değeridir:
    // hesap ucu, kutu işaretli değilken geçmiş iadeleri sormaz (istek kovası)
    // ve oradaki rakam daha önce iade edilmiş kargoyu düşmemiş olabilir.
    lines.append(breakdownRow(
      'Kargo iadesi',
      detail.shippingIncluded
        ? 'dahil'
        : `HARİÇ (iade edilebilir ${money(order.shippingAvailable)})`,
      detail.shipping,
    ));
    if (detail.adjustmentRefund) lines.append(breakdownRow('Ek iade', '', detail.adjustmentRefund));
    if (detail.adjustmentFee) lines.append(breakdownRow('Kesinti', 'düşülür', -detail.adjustmentFee));
    lines.append(breakdownRow('İADE TOPLAMI', `${num(detail.unitCount)} adet`, detail.total,
      'total'));
    result.append(card('İade tutarı — satır satır', lines));

    if (detail.clamped?.length) {
      result.append(alertBox(
        `Şu kalemlerde istenen adet iade edilebilir adedi aşıyordu ve düşürüldü: `
        + `${detail.clamped.join(', ')}.`, 'warn'));
    }
    // Sunucunun doğrulayamadığı şeyler (ör. daha önce iade edilmiş kargo)
    // burada yazar: para ekranında "sanırım" susarak geçilmez.
    for (const warning of data.warnings || []) result.append(alertBox(warning, 'warn'));
    const verdict = data.store;
    result.append(alertBox(verdict.message,
      verdict.matches === true ? 'good' : verdict.matches === false ? 'bad' : 'warn'));
    if (verdict.matches === false) {
      result.append(alertBox(
        `Mağaza ${money(verdict.storeTotal)} hesapladı, biz ${money(detail.total)}. `
        + `Fark ${money(Math.abs(verdict.diff))}.`, 'bad'));
    }

    const actions = h('div', 'rf-actions');
    actions.append(button('Onayla ve iadeyi oluştur', {
      variant: 'danger',
      title: 'Kredi notu oluşur; para hareketi denetim kaydına yazılır',
      onClick: () => approve(data),
    }));
    result.append(actions, hintBox(
      'Onay, EKRANDA GÖRDÜĞÜNÜZ hesabı gönderir; tutar yeniden hesaplanmaz. '
      + 'Parayı karta geri vermek ayrı bir adımdır (POS iadesi bölümü).'));
  }

  async function approve(data) {
    const reason = await askReason({
      title: 'İadeyi onayla',
      description: `${data.order.number || `#${data.orderId}`} · ${money(data.calc.total)} iade `
        + `edilecek (${num(data.calc.unitCount)} adet`
        + `${data.calc.shippingIncluded ? ', kargo dahil' : ', kargo hariç'}). `
        + 'Kredi notu oluşur ve geri alınamaz.',
      confirmLabel: 'İadeyi oluştur',
    });
    if (!reason) return;
    await withBusy('İade oluşturuluyor…', async () => {
      const done = await call(`${BASE}/approve`, {
        method: 'POST', body: { token: data.token, reason, dryRun: false },
      });
      toast(done.dryRun ? 'Kuru prova: istek gönderilmedi.' : `${money(done.total)} iade oluştu.`,
        done.dryRun ? 'warn' : 'good');
      if (done.notice) toast(done.notice, 'warn');
      // Para çıktıktan SONRA oluşan yerel kayıt hatası: kullanıcı aynı hesabı
      // ikinci kez onaylamasın diye toast değil, kalıcı uyarı olarak durur.
      if (done.warning) {
        toast(done.warning, 'bad');
        state.notice = done.warning;      // `refresh()` KPI'ı yeniden çizer, uyarı kalır
      }
      box.close();
      refresh();
    });
  }

  paintRough();
  const body = h('div', 'rf-pane');
  body.append(table.node, rough, controls, result);
  return card('İade edilecek kalemler', body,
    'Faturalanmamış kalem iade edilemez — parası hiç alınmamıştır');
}

function breakdownRow(label, note, amount, kind = '') {
  const line = h('div', `rf-line${kind ? ` ${kind}` : ''}`);
  line.append(h('span', 'rf-line-label', label));
  line.append(h('span', 'rf-sub', note || ''));
  const value = h('b', 'rf-line-value');
  value.textContent = money(amount);
  if (amount < 0) value.classList.add('minus');
  line.append(value);
  return line;
}

function labelled(label, control) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), control);
  return wrap;
}

// ------------------------------------------------------------------ süreç

function processSection(row) {
  const body = h('div', 'rf-pane');
  if (!row.requestId) {
    body.append(hintBox(
      'Bu kayıt bir iade talebine bağlı değil (doğrudan kesilmiş kredi notu). '
      + 'Süreç adımları talep kaydı olan iadelerde görünür.'));
    return card('Talep süreci', body);
  }

  const actions = h('div', 'rf-actions');
  for (const step of FLOW) {
    actions.append(button(step.label, {
      variant: step.key === 'rejected' ? 'danger' : '',
      onClick: async () => {
        const reason = await askReason({
          title: `Talebi "${step.label}" durumuna al`,
          description: `${row.number} talebinin durumu değişecek ve müşteri panelinde `
            + 'görünecek. Gerekçe denetim kaydına yazılır.',
          confirmLabel: step.label,
        });
        if (!reason) return;
        await withBusy('Durum yazılıyor…', async () => {
          const done = await call(`${BASE}/requests/${row.requestId}/status`, {
            method: 'POST', body: { status: step.key, reason, dryRun: false },
          });
          toast(`Durum: ${done.statusLabel}`, 'good');
          refresh();
        });
      },
    }));
  }
  body.append(actions, hintBox(
    '"İade edildi" buradan yazılmaz: o durum ancak para gerçekten geri verildiğinde, '
    + 'yukarıdaki onay adımıyla oluşur.'));
  return card('Talep süreci', body);
}

// ------------------------------------------------------------------- POS

function posSection(row, payload) {
  const body = h('div', 'rf-pane');
  const pos = payload.payments;

  if (!pos.available) {
    body.append(alertBox(
      `POS işlemleri okunamadı — ${pos.error}`, 'info'));
    return card('POS iadesi', body);
  }
  if (!pos.items.length) {
    body.append(hintBox('Bu siparişte sanal POS işlemi yok; para havaleyle ya da kapıda '
      + 'alınmış olabilir. İade yolunu muhasebe belirler.'));
    return card('POS iadesi', body);
  }

  const amount = h('input', 'kit-input');
  amount.type = 'text';
  amount.style.width = '130px';
  amount.placeholder = '0,00';

  let chosen = pos.items[0];
  amount.value = moneyInput(chosen.refundable);

  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'bank', label: 'Banka / POS', width: 'minmax(0, 1.2fr)' },
      { key: 'card', label: 'Kart', width: '130px', className: 'mono' },
      { key: 'status', label: 'Sonuç', width: '110px' },
      { key: 'amount', label: 'Tutar', width: '120px', align: 'num',
        cell: (item) => money(item.amount) },
      { key: 'refundable', label: 'İade edilebilir', width: '130px', align: 'num',
        cell: (item) => money(item.refundable) },
    ],
    rows: pos.items,
    dense: true,
    rowKey: (item) => String(item.id),
    onRow: (item) => {
      chosen = item;
      amount.value = moneyInput(item.refundable);
      toast(`${item.bank || 'POS'} işlemi seçildi.`, 'good');
    },
  });

  // PARAYI KARTA GERİ GÖNDERME ADIMI BU EKRANDAN YAPILMIYOR.
  //
  // Düğme daha önce tıklanınca geçitten gelen "bu uç bilerek yok" metnini
  // gösteriyordu — yani her tıklama aynı cümleyi tekrar ediyordu. Şimdi
  // kapalı çiziliyor ve nedeni yanında duruyor. Tutar kutusu da kapalı: dolu
  // bir kutu, işin buradan yapılabileceğini söyler.
  //
  // LİSTE KALDI. Hangi karta ne çekildiği gerçek bir bilgidir ve iadeyi
  // panelden yapacak kişinin ihtiyacı olan tam da budur.
  const blockedReason = pos.refundReason
    || 'Sanal POS iadesi Kontrol Merkezi\'den yapılmıyor: bu adım parayı müşterinin '
      + 'kartına geri gönderir ve geri alınamaz.';
  amount.disabled = true;
  amount.title = blockedReason;

  const actions = h('div', 'rf-actions');
  actions.append(labelled('İade tutarı', amount),
    blockedButton('POS iadesi yap', blockedReason, { variant: 'danger' }));

  body.append(table.node, actions, alertBox(blockedReason, 'warn'), hintBox(
    'POS iadesi kredi notundan AYRIDIR: biri muhasebe kaydı, diğeri banka hareketi. '
    + 'Tek düğmede birleştirmek, banka reddettiğinde "iade edildi" yazan bir kayıt bırakırdı.'));
  return card('POS iadesi', body);
}

// ----------------------------------------------------------------- kargo

function shippingSection(row, payload) {
  const body = h('div', 'rf-pane');
  const box = payload.shipments;

  if (!box.available) {
    body.append(alertBox(`Gönderiler okunamadı — ${box.error}`, 'info'));
    return card('Ürünü geri getir', body);
  }
  if (!box.items.length) {
    body.append(hintBox('Bu siparişin kargo kaydı yok; geri gönderim etiketi üretilemez.'));
    return card('Ürünü geri getir', body);
  }

  const list = h('div', 'rf-pane');
  for (const item of box.items) {
    const line = h('div', 'rf-line');
    line.append(
      h('b', 'rf-line-label', item.carrier || 'Taşıyıcı'),
      h('code', 'rf-code', item.tracking || '—'),
      h('span', 'rf-sub', item.status || ''),
      button('İade gönderisi aç', {
        onClick: async () => {
          const reason = await askReason({
            title: 'İade gönderisi aç',
            description: `${item.carrier || 'Taşıyıcı'} üzerinden müşteriden geri alım `
              + 'kaydı açılır ve yeni bir takip numarası üretilir. Ücret mağazaya yansır.',
            confirmLabel: 'Geri gönderim başlat',
          });
          if (!reason) return;
          await withBusy('İade gönderisi açılıyor…', async () => {
            const done = await call(`${BASE}/orders/${row.orderId}/return-shipment`, {
              method: 'POST', body: { shipmentId: item.id, reason, dryRun: false },
            });
            toast(done.tracking ? `Takip no: ${done.tracking}` : 'İade gönderisi açıldı.',
              'good');
            refresh();
          });
        },
      }),
    );
    list.append(line);
  }
  body.append(list);
  return card('Ürünü geri getir', body);
}

// -------------------------------------------------------------- bildirim

function notifySection(row) {
  const body = h('div', 'rf-pane');
  const area = h('textarea', 'kit-textarea');
  area.rows = 3;
  area.maxLength = 2000;
  area.placeholder = 'Müşteriye gidecek metin — iade durumu, tutar, ne zaman hesabına geçer…';

  const actions = h('div', 'rf-actions');
  actions.append(button('Müşteriye gönder', {
    variant: 'primary',
    onClick: async () => {
      const message = area.value.trim();
      if (message.length < 10) { toast('Mesaj en az 10 karakter olmalı.', 'bad'); return; }
      const reason = await askReason({
        title: 'Müşteriye bildir',
        description: 'Metin siparişe not olarak düşer ve müşteriye e-posta gönderilir.',
        confirmLabel: 'Gönder',
      });
      if (!reason) return;
      await withBusy('Bildirim gönderiliyor…', async () => {
        await call(`${BASE}/orders/${row.orderId}/notify`, {
          method: 'POST', body: { message, reason, dryRun: false },
        });
        toast('Müşteriye gönderildi.', 'good');
        area.value = '';
      });
    },
  }));

  body.append(area, actions, hintBox(
    'Metin siparişin notlarına yazılır ve müşteriye e-posta olarak gider. Şablonlu '
    + 'bildirimler Bildirimler ekranındadır; aynı metnin iki yerde yaşamaması için '
    + 'burada şablon tutulmaz.'));
  return card('Müşteri bildirimi', body);
}

// ---------------------------------------------------------------- geçmiş

function historySection(payload) {
  const body = h('div', 'rf-pane');
  if (!payload.history.length) {
    body.append(hintBox('Bu siparişte daha önce kesilmiş kredi notu yok.'));
    return card('Bu siparişin önceki iadeleri', body);
  }
  const table = dataTable({
    columns: [
      { key: 'number', label: 'İade no', width: '120px', className: 'mono' },
      { key: 'date', label: 'Tarih', width: '110px' },
      { key: 'itemCount', label: 'Kalem', width: '80px', align: 'num' },
      { key: 'amount', label: 'Tutar', width: '130px', align: 'num',
        cell: (item) => money(item.amount) },
    ],
    rows: payload.history,
    dense: true,
    rowKey: (item) => item.key,
  });
  body.append(table.node);
  return card('Bu siparişin önceki iadeleri', body);
}

async function auditSection(host, orderId) {
  const body = h('div', 'rf-pane');
  const box = card('Bu ekrandan yapılan işlemler', body);
  host.append(box);
  body.append(skeletonRows(3, 3));

  let result;
  try {
    result = await call(`${BASE}/audit?orderId=${orderId}&limit=50`);
  } catch (error) {
    body.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  body.replaceChildren();
  if (!result.items.length) {
    body.append(hintBox('Bu siparişe Kontrol Merkezi üzerinden henüz dokunulmadı.'));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'action', label: 'İşlem', width: '140px' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (item) => `${item.createdAt}-${item.action}`,
  });
  body.append(table.node, hintBox(
    'Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydında "neden iade '
    + 'edildi" alanı yok; ağ koparsa ne yapmaya çalıştığımız yalnız burada kalır.'));
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel rf');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  let lastRange = '';

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'İade no, sipariş no, müşteri, takip no',
        width: '280px' },
      { kind: 'dateRange', key: 'range', label: 'Tarih', start: todayIso(-89), end: todayIso() },
      { kind: 'select', key: 'reason', label: 'Sebep', options: [
        { value: '', label: 'Tümü — sebep' }, ...REASONS] },
      { kind: 'select', key: 'method', label: 'Yöntem', options: [
        { value: '', label: 'Tümü — yöntem' }, ...METHODS] },
      { kind: 'select', key: 'carrier', label: 'Taşıyıcı', options: [
        { value: '', label: 'Tümü — taşıyıcı' }] },
      { kind: 'numRange', key: 'amount', label: 'Tutar', money: true },
      { kind: 'toggle', key: 'withdrawal', label: 'Cayma süresi içinde' },
      { kind: 'toggle', key: 'posFailed', label: 'POS iadesi başarısız' },
      { kind: 'toggle', key: 'late', label: 'Ürün gelmedi' },
    ],
    onChange: (values) => {
      // Tarih aralığı SUNUCU penceresidir (yeniden çekilir); gerisi istemcide
      // süzülür. İkisini ayırmamak her tuşta mağazaya gitmek olurdu.
      const stamp = `${values.range?.start || ''}|${values.range?.end || ''}`;
      if (stamp !== lastRange) {
        lastRange = stamp;
        if (state.loaded) { refresh(); return; }
      }
      if (state.loaded) applyView();
    },
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('⤓ Görünen', { title: 'Ekrandaki listeyi CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Aralığın tamamını rapor klasörüne yaz', onClick: exportAll }),
      button('İade icmali', {
        onClick: () => {
          const range = currentRange();
          report.run('summary', { dateFrom: range.start || '', dateTo: range.end || '' });
        },
      }),
    ],
  });
  lastRange = `${nodes.filters.values().range?.start || ''}|${nodes.filters.values().range?.end || ''}`;

  nodes.chips = chipRow(
    STATUSES.map((item) => ({ key: item.key, label: item.label, count: 0 })),
    null,
    (key) => {
      state.chip = key;
      state.page = 1;
      applyView();
    },
  );

  nodes.status = statusLine();
  nodes.kpi = h('div', 'rf-kpi');
  nodes.tableWrap = h('div', 'rf-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => {
      state.page = page;
      state.size = size;
      renderTable();
    },
  });

  nodes.body = h('div', 'rf-body');
  nodes.body.append(nodes.kpi, nodes.tableWrap, nodes.pager.node);
  view.append(nodes.filters.node, nodes.chips.node, nodes.status.node, nodes.body);

  root.replaceChildren(view);
  nodes.status.set('İadeler alınıyor…');
  refresh();

  return () => {
    nodes.filters?.destroy();     // tarih alanları ve arama debounce'u global dinleyici tutar
    nodes.drawer?.close();        // açık çekmecenin Escape dinleyicisi document'te
    nodes.drawer = null;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
