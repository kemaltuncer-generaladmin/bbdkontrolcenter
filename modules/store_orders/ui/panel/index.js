// Siparişler paneli — mağazanın günlük çalışma ekranı.
//
// NE YAPAR: on beş süzgeç ve sayaçlı durum çipleriyle sipariş listesi; süzgecin
// mini toplamı (adet · ciro · ortalama sepet); satırdan tam yükseklik çekmecede
// sekmeli sipariş künyesi; toplu kargoya verme / fatura kesme (ÖNCE ATLANANLAR
// LİSTESİ, sonra gerekçeli onay); etiket indirme; sipariş listesi, sipariş fişi
// ve kargo manifestosu PDF'i.
//
// NE YAPMAZ:
//  · Sipariş DURUMUNU serbestçe değiştirmez. Mağazanın admin API'sinde sipariş
//    durumunu yazan bir uç yok; durum fatura/kargo/iptal eylemlerinin SONUCU
//    olarak değişir. "Durum değiştir" düğmesi bu yüzden kapalı durur ve neden
//    kapalı olduğunu yazar — sahte bir düğme koymak, basan kişiye hiçbir şey
//    olmadığını göstermezdi.
//  · Kargo etiketi SATIN ALMAZ. Para harcayan iş Kargo Yönetimi ekranınındır;
//    burada yalnız alınmış etiketin PDF'i indirilir.
//  · Sipariş SİLMEZ. İptal edilen sipariş listede ve raporlarda kalır.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Mağaza yalnız birkaç süzgeci uyguluyor. Ödeme yöntemi, kargo firması,
//    şehir, kupon ve anahtar süzgeçler seçilince liste TARANIR; durum satırı
//    kaç kaydın tarandığını yazar ve tavan yakalanırsa uyarır.
//  · Ödeme durumu Bagisto'da bir alan değildir; faturalanan tutardan çıkarılır.
//    Liste ucu faturalanan tutarı hiç göndermiyor: o satırlar "Bilinmiyor"
//    görünür. Detay istendiğinde (süzgeç ya da çekmece) gerçek durum gelir —
//    tahsil edilmiş siparişi "Ödenmedi" diye göstermektense söylemek doğrudur.
//  · Liste ucu SIĞ satır veriyor: fatura, gönderi, not ve ara toplam yalnız
//    detayda var. Bunlara dayanan bir süzgeç seçilince sunucu kümeyi
//    detaylandırır; tavan aşılırsa durum satırı "eksik uygulandı" der.
//  · Sipariş numarası `incrementId`dir; eylemler iç kimlikle gider.
//  · Kısmi fatura ve kısmi kargo olağandır — rozetler üç durumludur.
//  · Kargo/Fatura sekmeleri ilgili modülün yeteneğinden beslenir; yetenek yoksa
//    sekme HİÇ AÇILMAZ ve özet sekmesi kısa dökümü gösterir.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_orders/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_orders/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, copyText, csvBlob, h, loadStyles,
  money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_orders';

const CHIPS = [
  { key: 'pending', label: 'Bekleyen', count: 0 },
  { key: 'processing', label: 'Hazırlanıyor', count: 0 },
  { key: 'shipped', label: 'Kargoda', count: 0 },
  { key: 'today', label: 'Bugün gelen', count: 0 },
  { key: 'late', label: 'Geciken', count: 0 },
  { key: 'canceled', label: 'İptal', count: 0 },
];

const FULFIL_TONES = { none: 'bad', partial: 'warn', full: 'good' };

/** Ayarlar sekmesinde yeniden adlandırılabilen durum kodları. */
const STATUS_KEYS = [
  ['pending', 'Bekliyor'],
  ['pending_payment', 'Ödeme bekliyor'],
  ['processing', 'Hazırlanıyor'],
  ['completed', 'Tamamlandı'],
  ['canceled', 'İptal'],
  ['closed', 'Kapandı'],
  ['fraud', 'Sahte şüphesi'],
];

const EMPTY_STATE = {
  items: [], total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', scanned: false, truncated: false, partial: false, scannedCount: 0,
  summary: { count: 0, liveCount: 0, revenue: 0, average: 0, canceled: 0 },
  reference: { statuses: [], paymentStates: [], dateFields: [], channels: [], customerGroups: [] },
  selection: [], chip: null, loaded: false,
};

let api = null;
let capability = null;
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

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const parts = [`Bağlı · ${num(state.total)} sipariş`];
  parts.push(`sayfa ${state.page}/${Math.max(1, state.pages)}`);
  if (state.scanned) {
    // Taranan küme açıkça söylenir: kullanıcı süzgecin mağazada mı yoksa
    // burada mı uygulandığını bilmeli.
    parts.push(`${num(state.scannedCount)} kayıt tarandı`);
  }
  if (state.partial) {
    parts.push('detay tavanı aşıldı — bazı süzgeçler eksik uygulandı');
  }
  return parts.join(' · ');
}

function short(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—';
}

// -------------------------------------------------------------------- veri

function currentFilters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  const total = values.total || {};
  return {
    q: values.q || '',
    status: values.status || '',
    paymentState: values.paymentState || '',
    paymentMethod: values.paymentMethod || '',
    carrier: values.carrier || '',
    channel: values.channel || '',
    customerGroup: values.customerGroup || '',
    dateField: values.dateField || 'created',
    dateFrom: range.start || '',
    dateTo: range.end || '',
    totalMin: total.min ?? null,
    totalMax: total.max ?? null,
    city: values.city || '',
    coupon: values.coupon || '',
    noInvoice: Boolean(values.noInvoice),
    noShipment: Boolean(values.noShipment),
    hasNote: Boolean(values.hasNote),
    risky: Boolean(values.risky),
    partialRefund: Boolean(values.partialRefund),
    chip: state.chip || '',
  };
}

function queryString(extra = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...currentFilters(), ...extra })) {
    if (value === null || value === undefined || value === '' || value === false) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refresh({ page = state.page, size = state.size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('Siparişler alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/orders?${queryString({ page, size })}`);
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
    scanned: Boolean(payload.scanned),
    truncated: Boolean(payload.truncated),
    partial: Boolean(payload.partial),
    scannedCount: payload.scannedCount || 0,
    summary: payload.summary || EMPTY_STATE.summary,
    selection: [],
    loaded: true,
  };
  renderTable();
  renderTotals();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
  loadOverview();
}

/** Çip sayaçları ve süzgecin TAMAMININ toplamı ayrı uçtan gelir (tarama ister). */
async function loadOverview() {
  let payload;
  try {
    payload = await api(`${BASE}/overview?${queryString({ page: '', size: '' })}`);
  } catch {
    return;                    // sayaçsız da çalışır (K7)
  }
  if (!payload.connected) return;
  nodes.chips.counts(payload.counts || {});
  state.summary = payload.summary || state.summary;
  state.truncated = Boolean(payload.truncated);
  state.partial = state.partial || Boolean(payload.partial);
  renderTotals();
  nodes.status?.set(statusText(), !state.connected);
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
    statuses: payload.statuses || [],
    paymentStates: payload.paymentStates || [],
    dateFields: payload.dateFields || [],
    channels: payload.channels || [],
    customerGroups: payload.customerGroups || [],
  };
  const withAll = (label, items) => [{ value: '', label }, ...items];
  nodes.filters.options('status', withAll('Tümü — durum',
    state.reference.statuses.map((item) => ({ value: item.value, label: item.label }))));
  nodes.filters.options('paymentState', withAll('Tümü — ödeme',
    state.reference.paymentStates.map((item) => ({ value: item.value, label: item.label }))));
  nodes.filters.options('channel', withAll('Tümü — kanal',
    state.reference.channels.map((item) => ({ value: item.name, label: item.name }))));
  nodes.filters.options('customerGroup', withAll('Tümü — müşteri grubu',
    state.reference.customerGroups.map((item) => ({ value: item.name, label: item.name }))));
  nodes.filters.options('dateField',
    state.reference.dateFields.map((item) => ({ value: item.value, label: item.label })));
  if (payload.stale) {
    toast('Referans listeler bayat kopyadan geldi; mağaza yanıt vermedi.', 'warn');
  }
}

// ------------------------------------------------------------------- çizim

function renderTotals() {
  if (!nodes.totals) return;
  const sum = state.summary || EMPTY_STATE.summary;
  nodes.totals.replaceChildren(kpiRow([
    { label: 'Sipariş', value: num(sum.count) },
    { label: 'Ciro', value: money(sum.revenue), title: 'İptal ve kapalı siparişler hariç' },
    { label: 'Ortalama sepet', value: money(sum.average) },
    { label: 'İptal', value: num(sum.canceled), tone: 'muted' },
  ]));
  if (state.truncated) {
    nodes.totals.append(alertBox(
      'Tarama tavanına ulaşıldı; sayaçlar ve toplamlar EKSİK olabilir. Süzgeci '
      + 'daraltın ya da modül ayarındaki tarama tavanını yükseltin.', 'warn'));
  }
  if (state.partial) {
    // Sessiz yanlış liste yok: hangi süzgecin neden eksik uygulandığı yazılır.
    nodes.totals.append(alertBox(
      'Fatura, gönderi, not ve ödeme durumu mağazanın liste ucunda YOKTUR; bu '
      + 'süzgeçler için siparişler tek tek okunur ve detay tavanı aşıldı. Listede '
      + 'eksik satır olabilir — süzgeci daraltın ya da modül ayarındaki '
      + '`detail_cap` değerini yükseltin.', 'warn'));
  }
}

function trackCell(row) {
  const box = h('span', 'so-track');
  if (!row.track) {
    box.textContent = '—';
    return box;
  }
  box.append(h('code', 'so-code', row.track));
  box.append(button('kopyala', {
    variant: 'ghost',
    title: 'Takip numarasını panoya kopyala',
    onClick: async (event) => {
      event.stopPropagation();          // satır tıklaması çekmeceyi açmasın
      const done = await copyText(row.track);
      toast(done ? 'Takip no kopyalandı.' : 'Pano kullanılamadı.', done ? 'good' : 'warn');
    },
  }));
  return box;
}

/**
 * Yalnız DETAYDA bulunan bir tutar.
 *
 * Mağazanın liste ucu ara toplam, KDV, kargo ve indirim taşımıyor; o satırlar
 * sunucudan 0 gelir. `₺0,00` yazmak "bu siparişte KDV yok" demek olurdu —
 * bilgi yoksa `—` yazılır ve neden yazıldığı ipucunda durur.
 */
function detailMoney(row, value) {
  if (!row.detailed) {
    const cell = h('span', 'so-sub', '—');
    cell.title = 'Bu tutar yalnız sipariş detayında var; satıra tıklayın.';
    return cell;
  }
  return money(value);
}

function fulfilCell(row, key) {
  const box = h('span', 'so-fulfil');
  if (!row.detailed) {
    // Sığ satırda fatura/gönderi bilgisi YOKTUR. "Yok" yazmak, faturası
    // kesilmiş siparişi kesilmemiş göstermek olurdu.
    const cell = badge('Bilinmiyor', '');
    cell.title = 'Fatura ve gönderi kaydı yalnız sipariş detayında var; satıra tıklayın.';
    box.append(cell);
    return box;
  }
  const label = key === 'invoice' ? row.invoiceLabel : row.shipmentLabel;
  const value = key === 'invoice' ? row.invoiceState : row.shipmentState;
  const count = key === 'invoice' ? row.invoiceCount : row.shipmentCount;
  // Renk tek başına anlam taşımaz: rozetin yanında adet yazar.
  box.append(badge(label, FULFIL_TONES[value] || ''));
  if (count) box.append(h('span', 'so-sub', `${num(count)} kayıt`));
  return box;
}

const COLUMNS = [
  {
    key: 'orderNo',
    label: 'Sipariş',
    width: 'minmax(0, 1.3fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'so-cellstack');
      box.append(h('b', 'so-code', row.orderNo),
        h('span', 'so-sub', short(row.createdAt)));
      return box;
    },
  },
  {
    key: 'customer',
    label: 'Müşteri',
    width: 'minmax(0, 2fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'so-cellstack');
      box.append(clip(h('b'), row.customer, 30));
      const line = h('span', 'so-sub');
      line.textContent = row.customerGroup ? `${row.customerGroup} · ` : '';
      line.append(document.createTextNode(row.city || row.email || ''));
      box.append(line);
      return box;
    },
  },
  { key: 'itemCount', label: 'Kalem', width: '62px', align: 'num' },
  { key: 'subTotal', label: 'Ara toplam', width: '104px', align: 'num',
    cell: (row) => detailMoney(row, row.subTotal) },
  { key: 'shipping', label: 'Kargo', width: '92px', align: 'num',
    cell: (row) => detailMoney(row, row.shipping) },
  { key: 'discount', label: 'İndirim', width: '92px', align: 'num',
    cell: (row) => (row.detailed && !row.discount ? '—' : detailMoney(row, row.discount)) },
  { key: 'tax', label: 'KDV', width: '92px', align: 'num',
    cell: (row) => detailMoney(row, row.tax) },
  { key: 'grandTotal', label: 'Toplam', width: '110px', align: 'num', sortable: true,
    cell: (row) => h('b', 'so-total', money(row.grandTotal)) },
  { key: 'paymentLabel', label: 'Ödeme', width: '104px',
    cell: (row) => badge(row.paymentLabel, row.paymentTone) },
  {
    key: 'statusLabel',
    label: 'Durum',
    width: '124px',
    cell: (row) => {
      const box = h('span', 'so-cellstack');
      box.append(badge(row.statusLabel, row.statusTone));
      if (row.late) box.append(h('span', 'so-late', `${num(row.lateDays)} gündür bekliyor`));
      return box;
    },
  },
  { key: 'track', label: 'Takip no', width: '150px', cell: trackCell },
  { key: 'invoiceState', label: 'Fatura', width: '108px',
    cell: (row) => fulfilCell(row, 'invoice') },
  { key: 'shipmentState', label: 'Kargo durumu', width: '116px',
    cell: (row) => fulfilCell(row, 'ship') },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.chip) {
    const chip = CHIPS.find((item) => item.key === state.chip);
    return emptyState({
      title: `“${chip ? chip.label : state.chip}” çipinde sipariş yok`,
      text: 'Bu başlıkta bekleyen iş kalmadı.',
      actions: [button('Çipi kaldır', {
        onClick: () => { nodes.chips.set(null); applyChip(null); },
      })],
    });
  }
  return emptyState({
    title: 'Filtreye uyan sipariş yok',
    text: 'Süzgeçleri gevşetin ya da temizleyin; temizlenince tüm siparişler görünür.',
    actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  nodes.table = dataTable({
    columns: COLUMNS,
    rows: state.items,
    selectable: true,
    empty: emptyNode(),
    onRow: (row) => openOrder(row.id),
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
  bar.append(h('b', undefined, `${num(count)} sipariş seçildi`));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Kargoya ver', { onClick: () => batchDialog('ship') }),
    button('Fatura kes', { onClick: () => batchDialog('invoice') }),
    button('Etiket indir', { onClick: () => downloadLabels() }),
    button('Kargo manifestosu', {
      onClick: () => report.run('manifest', { orderIds: state.selection.map(Number) }),
    }),
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

function applyChip(key) {
  state.chip = key;
  refresh({ page: 1 });
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['Sipariş no', 'Tarih', 'Müşteri', 'E-posta', 'Durum', 'Ödeme',
    'Kalem', 'Toplam', 'Kargo firması', 'Takip no'];
  const rows = state.items.map((row) => [
    row.orderNo, row.createdAt, row.customer, row.email, row.statusLabel, row.paymentLabel,
    row.itemCount, money(row.grandTotal), row.carrier, row.track,
  ]);
  toast(`${num(csvBlob(headers, rows, `siparisler-sayfa-${state.page}`))} satır indirildi.`,
    'good');
  if (state.items.some((row) => !row.detailed)) {
    // Sessiz eksik dosya yok: bu CSV mağazanın liste ucundan geliyor ve o uç
    // kargo firması, takip no ve ödeme durumu taşımıyor.
    toast('Bu dosya ekrandaki sayfadan üretildi: kargo firması, takip no ve ödeme '
      + 'durumu boş. Tam döküm için “⤓ Tümü”.', 'warn');
  }
}

async function exportAll() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm kayıtları dışa aktar',
    description: 'Süzgece uyan siparişler mağazadan sayfa sayfa çekilir ve rapor '
      + 'klasörüne CSV olarak yazılır.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Siparişler taranıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { filters: currentFilters() },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) toast('Tarama tavanına dayandı; dosya eksik olabilir.', 'warn');
  });
}

async function downloadLabels() {
  await withBusy('Etiketler indiriliyor…', async () => {
    const result = await api(`${BASE}/labels`, {
      method: 'POST', body: { orderIds: state.selection.map(Number) },
    });
    const files = result.files || [];
    const good = files.filter((item) => !item.error);
    const bad = files.filter((item) => item.error);
    if (good.length) {
      toast(`${num(good.length)} etiket rapor klasörüne yazıldı.`, 'good');
      nodes.status.set(`Son etiket: ${good[good.length - 1].path}`);
    }
    if (bad.length) {
      // Sessiz atlama yok: hangi siparişin etiketinin NEDEN alınamadığı yazılır.
      toast(`${num(bad.length)} sipariş: ${bad[0].error}`, 'warn');
    }
  });
}

// ================================================================ çekmece

async function openOrder(orderId) {
  // Çekmecenin kendi kaynakları (formGrid → tarih alanları global dinleyici
  // tutar) çekmece kapanınca bırakılır; panel cleanup'ına bırakılırsa her
  // açılışta bir tane daha birikir.
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Sipariş yükleniyor…', subtitle: `#${orderId}`, onClose: dropForms,
  });
  closers.push(dropForms);
  box.body.append(skeletonRows(6, 3));

  let payload;
  try {
    payload = await call(`${BASE}/orders/${orderId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Sipariş okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const order = payload.order;
  box.setTitle(`${order.orderNo} · ${order.customer}`);
  box.body.replaceChildren();

  // Kargo, Fatura ve mağaza denetimi BAŞKA modüllerin yeteneğidir (K3).
  // Yetenek yoksa sekme HİÇ açılmaz; özet sekmesi kısa dökümü gösterir.
  const shipmentCap = capability?.('store.shipment.byOrder') || null;
  const invoiceCap = capability?.('store.invoice.byOrder') || null;
  const auditCap = capability?.('store.audit.for') || null;

  const tabs = [
    { key: 'summary', label: 'Özet' },
    { key: 'items', label: 'Kalemler' },
    { key: 'payment', label: 'Ödeme' },
  ];
  if (shipmentCap) tabs.push({ key: 'shipping', label: 'Kargo' });
  if (invoiceCap) tabs.push({ key: 'invoice', label: 'Fatura' });
  tabs.push({ key: 'returns', label: 'İade' }, { key: 'notes', label: 'Notlar' },
    { key: 'history', label: 'İşlem geçmişi' });

  const bar = tabBar(tabs, 'summary', (key) => paint(key));
  bar.badge('items', payload.items.length || undefined);
  bar.badge('returns', payload.returns.length || undefined);

  const head = h('div', 'so-drawer-head');
  head.append(
    badge(order.statusLabel, order.statusTone),
    badge(order.paymentLabel, order.paymentTone),
    h('code', 'so-code', order.orderNo),
    h('span', 'so-sub', `${short(order.createdAt)} · ${order.channel || 'kanal yok'}`),
  );
  const pane = h('div', 'so-pane');
  box.body.append(head, bar.node, pane);

  if (payload.warnings && payload.warnings.length) {
    box.body.insertBefore(
      alertBox(`Bazı parçalar okunamadı — ${payload.warnings.join(' · ')}`, 'warn'), pane);
  }

  const context = { payload, forms, shipmentCap, invoiceCap, auditCap };

  function paint(key) {
    dropForms();
    pane.replaceChildren();
    ({
      summary: paintSummary, items: paintItems, payment: paintPayment,
      shipping: paintShipping, invoice: paintInvoice, returns: paintReturns,
      notes: paintNotes, history: paintHistory,
    }[key] || paintSummary)(pane, context);
  }
  paint('summary');
}

function addressCard(title, address) {
  const body = h('div', 'so-address');
  body.append(h('b', undefined, address.name || '—'));
  if (address.company) body.append(h('span', 'so-sub', address.company));
  body.append(h('span', undefined, address.line || 'Adres yok'));
  if (address.phone) body.append(h('span', 'so-sub', address.phone));
  return card(title, body);
}

function paintSummary(pane, { payload, shipmentCap, invoiceCap }) {
  const order = payload.order;
  const amounts = payload.money;

  const totals = h('div', 'so-totals-table');
  for (const [label, value, strong] of [
    ['Ara toplam', amounts.subTotal, false],
    ['Kargo', amounts.shipping, false],
    ['İndirim', amounts.discount, false],
    ['KDV', amounts.tax, false],
    ['Genel toplam', amounts.grandTotal, true],
    ['Faturalanan', amounts.invoiced, false],
    ['İade edilen', amounts.refunded, false],
  ]) {
    const line = h('div', 'so-total-row');
    line.append(h('span', strong ? 'so-strong' : '', label),
      h('span', strong ? 'so-strong' : '', money(value)));
    totals.append(line);
  }

  const facts = h('div', 'so-facts');
  for (const [label, value] of [
    ['Müşteri', order.customer],
    ['E-posta', order.email || '—'],
    ['Telefon', order.phone || '—'],
    ['Müşteri grubu', order.customerGroup || '—'],
    ['Kanal', order.channel || '—'],
    ['Ödeme yöntemi', order.paymentMethod || '—'],
    ['Kupon', order.coupon || '—'],
    ['Kalem / adet', `${num(order.itemCount)} / ${num(order.quantity)}`],
  ]) {
    const line = h('div', 'so-fact');
    line.append(h('span', 'so-sub', label), h('b', undefined, String(value)));
    facts.append(line);
  }

  // Kargo/Fatura sekmesi kapalıysa bilgi KAYBOLMAZ: kısa dökümü burada durur.
  const closed = [];
  if (!shipmentCap) closed.push(['Kargo', payload.shipments, 'Kargo Yönetimi']);
  if (!invoiceCap) closed.push(['Fatura', payload.invoices, 'Fatura']);
  const digest = h('div', 'so-digest');
  for (const [label, rows] of closed) {
    const line = h('div', 'so-fact');
    line.append(h('span', 'so-sub', label),
      h('b', undefined, rows.length ? `${num(rows.length)} kayıt` : 'yok'));
    digest.append(line);
  }
  if (closed.length) {
    digest.append(hintBox(
      `${closed.map((item) => item[0]).join(' ve ')} sekmesi kapalı: veriyi veren ekran `
      + `(${closed.map((item) => item[2]).join(', ')}) bu kurulumda yok. Ekran çalışmaya `
      + 'devam eder; o ekran açılınca sekme kendiliğinden görünür.'));
  }

  pane.append(
    card('Sipariş', facts),
    addressCard('Fatura adresi', payload.billing),
    addressCard('Teslimat adresi', payload.shipping),
    card('Toplam dökümü', totals),
  );
  if (closed.length) pane.append(card('Kargo ve fatura özeti', digest));
  pane.append(actionBar(payload));
}

function actionBar(payload) {
  const order = payload.order;
  const row = h('div', 'so-actions');
  row.append(
    button('Fiş yazdır', { onClick: () => report.run('slip', { orderId: order.id }) }),
    button('Fatura kes', {
      variant: 'primary',
      disabled: order.invoiceState === 'full',
      title: order.invoiceState === 'full' ? 'Siparişin tamamı zaten faturalanmış' : '',
      onClick: () => invoiceOrder(order, payload.items),
    }),
    button('Kargoya ver', {
      disabled: order.invoiceState === 'none' || order.shipmentState === 'full',
      title: order.invoiceState === 'none'
        ? 'Önce fatura kesilmeli' : 'Bagisto gönderi kaydı açar; etiket satın almaz',
      onClick: () => shipOrder(order, payload.items),
    }),
    // Ölü düğme bırakılmıyor: kapalı olduğu ve NEDEN kapalı olduğu hem
    // etikette hem de altındaki uyarıda yazılı. İpucu (title) tek başına
    // yetmez — dokunmatikte ve klavyeyle hiç görünmez.
    button('Durum değiştir (kapalı)', {
      disabled: true,
      title: 'Mağazanın admin API\'sinde sipariş durumunu yazan bir uç yok. Durum '
        + 'fatura/kargo/iptal eylemlerinin sonucu olarak değişir; uç yayınlanınca '
        + 'bu düğme açılacak.',
    }),
    button('İptal et', {
      variant: 'danger',
      disabled: Boolean(payload.cancelBlock),
      title: payload.cancelBlock || 'Geri alınamaz; ayrı izin ve gerekçe ister',
      onClick: () => cancelOrder(order),
    }),
  );
  const box = h('div', 'so-actionbox');
  box.append(row);
  if (payload.cancelBlock) box.append(alertBox(payload.cancelBlock, 'info'));
  box.append(alertBox(
    '“Durum değiştir” HAZIR DEĞİL: mağazanın admin API\'sinde sipariş durumunu '
    + 'doğrudan yazan bir uç yok. Durum, fatura kesme / kargoya verme / iptal '
    + 'eylemlerinin sonucu olarak değişir. Uç yayınlandığında düğme açılacak.', 'info'));
  box.append(hintBox(
    'Bu ekran siparişin kendisini yönetir. Etiket satın alma Kargo Yönetimi\'nin, '
    + 'para iadesi İadeler\'in, fatura PDF\'i Fatura ekranının işidir; buradan yalnız '
    + 'tetiklenir.'));
  return box;
}

// ------------------------------------------------------------- kalemler

function paintItems(pane, { payload }) {
  const order = payload.order;
  const rows = payload.items;
  if (!rows.length) {
    pane.append(emptyState({ title: 'Kalem yok', text: 'Siparişte kalem kaydı bulunamadı.' }));
    return;
  }

  const table = dataTable({
    columns: [
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1.1fr)', className: 'mono' },
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2.2fr)' },
      { key: 'quantity', label: 'Adet', width: '64px', align: 'num' },
      { key: 'unitPrice', label: 'Birim', width: '96px', align: 'num',
        cell: (row) => money(row.unitPrice) },
      { key: 'discount', label: 'İndirim', width: '96px', align: 'num',
        cell: (row) => (row.discount ? money(row.discount) : '—') },
      { key: 'tax', label: 'KDV', width: '90px', align: 'num', cell: (row) => money(row.tax) },
      { key: 'total', label: 'Tutar', width: '104px', align: 'num',
        cell: (row) => money(row.total) },
      {
        key: 'invoiced',
        label: 'Fatura / Kargo',
        width: '128px',
        // Kısmi fatura ve kısmi kargo olağandır; iki sayı da açıkça yazılır.
        cell: (row) => `${num(row.invoiced)} / ${num(row.shipped)}`,
      },
    ],
    rows,
    dense: true,
  });

  const partial = h('div', 'so-actions');
  partial.append(
    button('Seçili kalemleri faturala', {
      onClick: () => invoiceOrder(order, rows, { partial: true }),
    }),
    button('Seçili kalemleri kargoya ver', {
      onClick: () => shipOrder(order, rows, { partial: true }),
    }),
  );
  pane.append(table.node, partial, hintBox(
    'Kalem iptali ve kısmi iade İadeler ekranının işidir: para hareketi orada, ayrı '
    + 'izinle ve ayrı onayla yapılır. Buradan yalnız kısmi FATURA ve kısmi KARGO '
    + 'yapılabilir.'));
}

/** Kalem adedi soran küçük diyalog. Kısmi fatura/kargo bunu kullanır. */
function askQuantities(items, { title, hint, cap }) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const dialog = h('div', 'kit-dialog so-qty');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.append(h('h3', 'kit-dialog-title', title));
    dialog.append(h('p', 'kit-dialog-text', hint));

    const inputs = new Map();
    const list = h('div', 'so-qty-list');
    for (const item of items) {
      const remaining = Math.max(0, cap(item));
      const line = h('label', 'so-qty-row');
      const input = h('input', 'kit-input');
      input.type = 'number';
      input.min = '0';
      input.max = String(remaining);
      input.value = String(remaining);
      input.disabled = remaining === 0;
      line.append(h('span', undefined, `${item.sku} · ${item.name}`),
        h('span', 'so-sub', `en çok ${num(remaining)}`), input);
      inputs.set(String(item.id), input);
      list.append(line);
    }
    dialog.append(list);

    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    const onKey = (event) => { if (event.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    closers.push(() => document.removeEventListener('keydown', onKey));

    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }), button('Devam', {
      variant: 'primary',
      onClick: () => {
        const out = {};
        for (const [id, input] of inputs) {
          const value = Number(input.value || 0);
          if (value > 0) out[id] = value;
        }
        close(Object.keys(out).length ? out : null);
      },
    }));
    dialog.append(actions);
    overlay.append(dialog);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    nodes.root.append(overlay);
  });
}

async function invoiceOrder(order, items, { partial = false } = {}) {
  let quantities = {};
  if (partial) {
    quantities = await askQuantities(items, {
      title: 'Kısmi fatura',
      hint: 'Faturalanacak adetleri yazın. Sıfır bırakılan kalem faturaya girmez.',
      cap: (item) => item.quantity - item.invoiced,
    });
    if (!quantities) return;
  }
  const reason = await askReason({
    title: 'Fatura kes',
    description: `${order.orderNo} · ${partial ? 'seçilen kalemler' : 'siparişin tamamı'} `
      + 'faturalanacak. Fatura mağazada kesilir ve GERİ ALINAMAZ.',
    confirmLabel: 'Fatura kes',
  });
  if (!reason) return;
  await withBusy('Fatura kesiliyor…', async () => {
    const result = await call(`${BASE}/orders/${order.id}/invoice`, {
      method: 'POST', body: { items: quantities, reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Fatura kesildi.',
      result.dryRun ? 'warn' : 'good');
    refresh();
  });
}

async function shipOrder(order, items, { partial = false } = {}) {
  const quantities = await askQuantities(items, {
    title: partial ? 'Kısmi kargo' : 'Kargoya ver',
    hint: 'Kargoya verilecek adetleri yazın. Yalnız FATURALANMIŞ kalem kargolanabilir.',
    cap: (item) => item.invoiced - item.shipped,
  });
  if (!quantities) return;

  const track = window.prompt('Takip numarası (boş bırakılabilir)', '');
  if (track === null) return;
  const carrier = window.prompt('Kargo firması (boş bırakılabilir)', order.carrier || '');
  if (carrier === null) return;

  const reason = await askReason({
    title: 'Kargoya ver',
    description: `${order.orderNo} için mağazada GÖNDERİ KAYDI açılır. Etiket SATIN `
      + 'ALINMAZ — o iş Kargo Yönetimi ekranınındır ve para harcar.',
    confirmLabel: 'Gönderiyi kaydet',
  });
  if (!reason) return;
  await withBusy('Gönderi kaydediliyor…', async () => {
    const result = await call(`${BASE}/orders/${order.id}/ship`, {
      method: 'POST',
      body: { items: quantities, carrier, track, sourceId: 1, reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Gönderi kaydedildi.',
      result.dryRun ? 'warn' : 'good');
    refresh();
  });
}

async function cancelOrder(order) {
  const reason = await askReason({
    title: 'Siparişi iptal et',
    description: `${order.orderNo} · ${money(order.grandTotal)}. İPTAL GERİ ALINAMAZ: `
      + 'stok geri döner, sipariş kapanır ve müşteri bilgilendirilir. Zaten tahsil '
      + 'edilmiş tutar için ayrıca İadeler ekranından iade açılmalıdır.',
    confirmLabel: 'Siparişi iptal et',
  });
  if (!reason) return;
  await withBusy('Sipariş iptal ediliyor…', async () => {
    const result = await call(`${BASE}/orders/${order.id}/cancel`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.'
      : `${result.orderNo} iptal edildi.`, result.dryRun ? 'warn' : 'good');
    refresh();
  });
}

// -------------------------------------------------------------- ödeme

function paintPayment(pane, { payload }) {
  const order = payload.order;
  const facts = h('div', 'so-facts');
  for (const [label, value] of [
    ['Yöntem', order.paymentMethod || '—'],
    ['Durum', order.paymentLabel],
    ['Faturalanan', money(payload.money.invoiced)],
    ['İade edilen', money(payload.money.refunded)],
    ['Kupon', order.coupon || '—'],
  ]) {
    const line = h('div', 'so-fact');
    line.append(h('span', 'so-sub', label), h('b', undefined, String(value)));
    facts.append(line);
  }
  pane.append(card('Ödeme', facts));

  if (!payload.transactions.length) {
    pane.append(emptyState({
      title: 'POS işlemi kaydı yok',
      text: 'Bu sipariş için sanal POS işlemi bulunamadı. Havale/kapıda ödeme '
        + 'siparişlerinde bu normaldir.',
    }));
  } else {
    pane.append(dataTable({
      columns: [
        { key: 'createdAt', label: 'Zaman', width: '160px', cell: (row) => short(row.createdAt) },
        { key: 'type', label: 'Tür', width: '120px' },
        { key: 'status', label: 'Sonuç', width: '120px' },
        { key: 'amount', label: 'Tutar', width: '120px', align: 'num',
          cell: (row) => money(row.amount) },
      ],
      rows: payload.transactions,
      dense: true,
    }).node);
  }
  pane.append(hintBox(
    'Ödeme durumu Bagisto\'da bir alan DEĞİLDİR: faturalanan tutardan çıkarılır. '
    + '3D sonucu ve taksit ayrıntısı Sanal POS ekranındadır.'));
}

// ------------------------------------------- yetenekten beslenen sekmeler

/**
 * Başka modülün yeteneğini çizer.
 *
 * SÖZLEŞME (`store.shipment.byOrder` · `store.invoice.byOrder` · `store.audit.for`):
 *     async (orderId) => {ok, error?, title?, node?, columns?, rows?}
 * `node` verilirse olduğu gibi eklenir; yoksa `columns`/`rows` ile tablo çizilir.
 * Yetenek yoksa bu sekme HİÇ açılmaz — çağrı buraya gelmez.
 */
async function paintCapability(pane, provider, { orderId, empty }) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await provider(orderId);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message || 'Yetenek yanıt vermedi.', 'bad'));
    return;
  }
  pane.replaceChildren();
  if (result && result.ok === false) {
    pane.append(alertBox(result.error || 'Veri alınamadı.', 'warn'));
    return;
  }
  if (result?.node instanceof Node) {
    pane.append(result.node);
    return;
  }
  const rows = result?.rows || [];
  if (!rows.length) {
    pane.append(emptyState({ title: empty.title, text: empty.text }));
    return;
  }
  const columns = result.columns
    || Object.keys(rows[0]).map((key) => ({ key, label: key }));
  pane.append(dataTable({ columns, rows, dense: true }).node);
}

function paintShipping(pane, { payload, shipmentCap }) {
  paintCapability(pane, shipmentCap, {
    orderId: payload.order.id,
    empty: {
      title: 'Gönderi yok',
      text: 'Bu sipariş henüz kargoya verilmedi. Özet sekmesinden “Kargoya ver”.',
    },
  });
}

function paintInvoice(pane, { payload, invoiceCap }) {
  paintCapability(pane, invoiceCap, {
    orderId: payload.order.id,
    empty: {
      title: 'Fatura yok',
      text: 'Bu siparişin faturası kesilmemiş. Özet sekmesinden “Fatura kes”.',
    },
  });
}

// --------------------------------------------------------------- iade

function paintReturns(pane, { payload }) {
  if (payload.refunds.length) {
    pane.append(card('Mağaza iadeleri', dataTable({
      columns: [
        { key: 'no', label: 'İade no', width: '140px' },
        { key: 'createdAt', label: 'Zaman', width: '170px',
          cell: (row) => short(row.createdAt) },
        { key: 'total', label: 'Tutar', width: '120px', align: 'num',
          cell: (row) => money(row.total) },
      ],
      rows: payload.refunds,
      dense: true,
    }).node));
  }

  if (!payload.returnsAvailable) {
    pane.append(alertBox(
      'İade/değişim talepleri (RMA) mağazadaki BBD paketinden gelir; o uç henüz '
      + 'yayında değil. Uç yayınlanınca talepler burada listelenecek.', 'info'));
  } else if (!payload.returns.length) {
    pane.append(emptyState({
      title: 'İade talebi yok',
      text: 'Müşteri bu sipariş için iade/değişim talebi açmadı.',
    }));
  } else {
    pane.append(card('İade talepleri', dataTable({
      columns: [
        { key: 'createdAt', label: 'Zaman', width: '170px',
          cell: (row) => short(row.createdAt) },
        { key: 'status', label: 'Durum', width: '130px' },
        { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
      ],
      rows: payload.returns,
      dense: true,
    }).node));
  }

  pane.append(hintBox(
    'Para iadesi bu ekrandan YAPILMAZ: iade tutarı hesabı ve para hareketi İadeler '
    + 'ekranının işidir, ayrı izin ister.'));
}

// -------------------------------------------------------------- notlar

async function paintNotes(pane, { payload, forms }) {
  const order = payload.order;
  const list = h('div', 'so-notes');
  list.append(skeletonRows(3, 2));

  const form = formGrid({
    fields: [
      { key: 'comment', label: 'Not', type: 'textarea', wide: true, maxLength: 2000,
        required: true, placeholder: 'Siparişle ilgili not…' },
      { key: 'notify', label: 'Müşteriye e-posta gönder', type: 'checkbox',
        hint: 'AÇIKSA müşteriye posta gider ve geri alınamaz. İç not yazıyorsanız kapalı '
          + 'bırakın.' },
    ],
    value: { comment: '', notify: false },
  });
  forms.push(form);

  const send = button('Notu ekle', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Notu yazın.', 'bad'); return; }
      const draft = form.draft();
      const reason = await askReason({
        title: 'Sipariş notu ekle',
        description: draft.notify
          ? 'Not MÜŞTERİYE E-POSTA olarak gider ve geri alınamaz.'
          : 'Not yalnız yönetim tarafında görünür.',
        confirmLabel: 'Notu ekle',
      });
      if (!reason) return;
      await withBusy('Not ekleniyor…', async () => {
        await call(`${BASE}/orders/${order.id}/comments`, {
          method: 'POST',
          body: { comment: draft.comment, notify: Boolean(draft.notify), reason,
            dryRun: false },
        });
        toast('Not eklendi.', 'good');
        form.reset({ comment: '', notify: false });
        await fill();
      });
    },
  });

  const actions = h('div', 'so-actions');
  actions.append(send);
  pane.append(card('Yazışma', list), card('Yeni not', form.node), actions);

  async function fill() {
    let result;
    try {
      result = await call(`${BASE}/orders/${order.id}/comments`);
    } catch (error) {
      list.replaceChildren(alertBox(error.message, 'warn'));
      return;
    }
    list.replaceChildren();
    if (!result.connected) {
      list.append(alertBox(`Notlar okunamadı — ${result.error}`, 'warn'));
      return;
    }
    if (!result.items.length) {
      list.append(emptyState({ title: 'Not yok', text: 'Bu siparişe henüz not yazılmadı.' }));
      return;
    }
    for (const item of result.items) {
      const note = h('div', 'so-note');
      note.append(h('div', 'so-sub',
        `${short(item.createdAt)}${item.notified ? ' · müşteriye gönderildi' : ' · iç not'}`));
      note.append(h('div', undefined, item.comment));
      list.append(note);
    }
  }
  await fill();
}

// ------------------------------------------------------- işlem geçmişi

async function paintHistory(pane, { payload, auditCap }) {
  const orderId = payload.order.id;
  const local = h('div');
  local.append(skeletonRows(4, 3));
  pane.append(card('Kontrol Merkezi işlemleri', local,
    'Gerekçeleriyle — bu iz yalnız burada tutulur'));

  // Mağazanın KENDİ denetim kaydı (UDİT) ayrı bir modülün yeteneğidir. Yoksa
  // bu sekme yine çalışır: bizim yerel izimiz her koşulda buradadır.
  if (auditCap) {
    const remote = h('div');
    pane.append(card('Mağaza denetim kaydı', remote, 'store.audit.for'));
    paintCapability(remote, auditCap, {
      orderId,
      empty: { title: 'Mağaza kaydı yok', text: 'Mağaza bu sipariş için kayıt tutmamış.' },
    });
  } else {
    pane.append(hintBox(
      'Mağazanın kendi denetim kaydı (UDİT) burada gösterilmiyor: o veriyi veren ekran '
      + 'bu kurulumda yok. Aşağıdaki iz Kontrol Merkezi üzerinden yapılan işlemlerin '
      + 'izidir ve gerekçeyi tutar.'));
  }

  let result;
  try {
    result = await call(`${BASE}/audit?orderId=${orderId}&limit=50`);
  } catch (error) {
    local.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  local.replaceChildren();
  if (!result.items.length) {
    local.append(emptyState({
      title: 'Bu siparişe bu ekrandan dokunulmadı',
      text: 'Kontrol Merkezi üzerinden yapılan her yazma gerekçesiyle burada listelenir.',
    }));
    return;
  }
  local.append(dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '160px',
        cell: (row) => stampIso(row.createdAt) },
      { key: 'action', label: 'İşlem', width: '130px' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '100px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}`,
  }).node);
}

// ============================================================= toplu işlem

function batchDialog(kind) {
  const overlay = h('div', 'kit-overlay');
  const dialog = h('div', 'kit-dialog so-batch');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');

  const title = kind === 'ship' ? 'Toplu kargoya verme' : 'Toplu fatura kesme';
  dialog.append(h('h3', 'kit-dialog-title', title));
  dialog.append(h('p', 'kit-dialog-text',
    `${num(state.selection.length)} sipariş seçili. Hangi siparişin atlanacağı ve NEDEN `
    + 'atlandığı gösterilmeden hiçbir şey uygulanmaz.'));

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const result = h('div', 'so-batch-result');
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Vazgeç', { onClick: close }),
    button('Önizle', { variant: 'primary', onClick: () => runPreview() }));

  dialog.append(result, actions);
  overlay.append(dialog);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);

  async function runPreview() {
    result.replaceChildren(skeletonRows(4, 4));
    let preview;
    try {
      preview = await call(`${BASE}/batch/preview`, {
        method: 'POST', body: { kind, orderIds: state.selection.map(Number) },
      });
    } catch (error) {
      result.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    paintPreview(preview);
  }

  function paintPreview(preview) {
    const summary = preview.summary;
    result.replaceChildren(kpiRow([
      { label: 'Sipariş', value: num(summary.total) },
      { label: 'Uygulanacak', value: num(summary.ready), tone: 'good' },
      { label: 'Atlanacak', value: num(summary.skipped), tone: 'muted' },
      { label: 'Tutar', value: money(summary.amount) },
    ]));
    result.append(dataTable({
      columns: [
        { key: 'orderNo', label: 'Sipariş', width: '130px', className: 'mono' },
        { key: 'customer', label: 'Müşteri', width: 'minmax(0, 1.6fr)' },
        { key: 'statusLabel', label: 'Durum', width: '120px' },
        { key: 'total', label: 'Toplam', width: '110px', align: 'num',
          cell: (row) => money(row.total) },
        {
          key: 'note',
          label: 'Sonuç',
          width: 'minmax(0, 1.8fr)',
          className: 'wrap',
          cell: (row) => (row.skipped ? badge(row.note, 'warn') : badge('Uygulanacak', 'good')),
        },
      ],
      rows: preview.rows,
      dense: true,
      rowKey: (row) => String(row.id),
    }).node);

    if (preview.missing && preview.missing.length) {
      result.append(alertBox(
        `${preview.missing.length} sipariş okunamadı ve önizlemeye girmedi.`, 'warn'));
    }

    actions.replaceChildren(
      button('Vazgeç', { onClick: close }),
      button(`${num(summary.ready)} siparişe uygula`, {
        variant: 'danger',
        disabled: summary.ready === 0,
        onClick: () => apply(preview, summary),
      }),
    );
  }

  async function apply(preview, summary) {
    const reason = await askReason({
      title,
      description: `${num(summary.ready)} siparişe uygulanacak. Önizlediğiniz liste neyse `
        + 'o uygulanır; yeniden hesaplanmaz. Sıra sıra yazılır ve YİNELENMEZ.',
      confirmLabel: 'Uygula',
    });
    if (!reason) return;
    await withBusy('Uygulanıyor…', async () => {
      const applied = await api(`${BASE}/batch/apply`, {
        method: 'POST', body: { token: preview.token, reason, dryRun: false },
      });
      const failed = applied.failed || 0;
      toast(`${num(applied.applied)} sipariş işlendi`
        + (failed ? ` · ${num(failed)} başarısız` : ''), failed ? 'warn' : 'good');
      if (failed) {
        // Hangi siparişin NEDEN başarısız olduğu kaybolmaz.
        result.replaceChildren(dataTable({
          columns: [
            { key: 'orderNo', label: 'Sipariş', width: '130px', className: 'mono' },
            { key: 'ok', label: 'Sonuç', width: '110px',
              cell: (row) => badge(row.ok ? 'Tamam' : 'Hata', row.ok ? 'good' : 'bad') },
            { key: 'error', label: 'Açıklama', width: 'minmax(0, 2fr)', className: 'wrap' },
          ],
          rows: applied.results || [],
          dense: true,
          rowKey: (row) => String(row.id),
        }).node);
        actions.replaceChildren(button('Kapat', { onClick: close }));
      } else {
        close();
      }
      nodes.table?.clearSelection();
      state.selection = [];
      refresh();
    });
  }
}

// ================================================================ ayarlar

let settingsForms = [];

async function renderSettings(host) {
  settingsForms.forEach((form) => form.destroy());
  settingsForms = [];
  host.replaceChildren(skeletonRows(5, 2));
  let payload;
  try {
    payload = await call(`${BASE}/settings`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  const local = payload.local;
  const generalForm = formGrid({
    fields: [
      { key: 'orderNoFormat', label: 'Sipariş no biçimi', type: 'text', maxLength: 40,
        hint: '{no} sipariş numarası · {id} iç kimlik · {year} yıl. Örnek: BBD-{year}-{no}' },
      { key: 'cancelWindowHours', label: 'İptal süresi (saat)', type: 'number', min: 0,
        max: 8760,
        hint: '0 = sınırsız. Bundan eski sipariş bu ekrandan iptal EDİLEMEZ; kural '
          + 'sunucuda da uygulanır.' },
      { key: 'lateDays', label: 'Gecikme eşiği (gün)', type: 'number', min: 1, max: 90,
        hint: 'Bu kadar gündür kargolanmayan sipariş “Geciken” çipine düşer.' },
    ],
    value: {
      orderNoFormat: local.orderNoFormat,
      cancelWindowHours: local.cancelWindowHours,
      lateDays: local.lateDays,
    },
  });
  settingsForms.push(generalForm);

  const nameForm = formGrid({
    fields: STATUS_KEYS.map(([key, fallback]) => ({
      key, label: fallback, type: 'text', maxLength: 40, placeholder: fallback,
    })),
    value: Object.fromEntries(
      STATUS_KEYS.map(([key]) => [key, local.statusNames?.[key] || ''])),
  });
  settingsForms.push(nameForm);

  const storeBox = h('div');
  if (!payload.storeAvailable) {
    storeBox.append(alertBox(
      `Mağaza sipariş ayarları okunamadı — ${payload.error}. Yukarıdaki tercihler yine `
      + 'kaydedilebilir; onlar yereldir.', 'warn'));
  } else if (!payload.store.length) {
    storeBox.append(emptyState({
      title: 'Mağaza ayarı bulunamadı',
      text: `“${payload.storeSlug}” bölümü boş döndü. Bölüm adı modül ayarından `
        + 'değiştirilebilir.',
    }));
  } else {
    storeBox.append(dataTable({
      columns: [
        { key: 'key', label: 'Anahtar', width: 'minmax(0, 2fr)', className: 'mono wrap' },
        { key: 'value', label: 'Değer', width: 'minmax(0, 1fr)' },
      ],
      rows: payload.store,
      dense: true,
      rowKey: (row) => row.key,
    }).node);
  }

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!generalForm.valid()) {
        generalForm.showErrors();
        toast('Alanları düzeltin.', 'bad');
        return;
      }
      const draft = generalForm.draft();
      const names = {};
      const written = nameForm.draft();
      for (const [key] of STATUS_KEYS) {
        if (String(written[key] || '').trim()) names[key] = String(written[key]).trim();
      }
      const reason = await askReason({
        title: 'Ekran tercihlerini kaydet',
        description: 'Bu tercihler YALNIZ Kontrol Merkezi\'ni etkiler; mağazaya hiçbir '
          + 'şey gönderilmez. Gerekçe denetim kaydına yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Ayarlar kaydediliyor…', async () => {
        const result = await call(`${BASE}/settings`, {
          method: 'POST',
          body: {
            statusNames: names,
            orderNoFormat: draft.orderNoFormat,
            cancelWindowHours: Number(draft.cancelWindowHours || 0),
            lateDays: Number(draft.lateDays || 3),
            reason,
          },
        });
        toast(`Kaydedildi: ${result.changed.join(', ') || 'değişiklik yok'}`, 'good');
        state.loaded = false;         // liste yeni adlarla yeniden çizilsin
      });
    },
  });

  const actions = h('div', 'so-actions');
  actions.append(save);

  host.append(
    card('Sipariş akışı', generalForm.node, 'Yalnız Kontrol Merkezi\'ni etkiler'),
    card('Durum adları', nameForm.node, 'Boş bırakılan durum varsayılan adıyla görünür'),
    actions,
    card('Mağazanın sipariş ayarları', storeBox, 'Salt okunur'),
    hintBox(
      'Mağaza ayarları buradan YAZILMAZ: anahtar adları Bagisto sürümüne göre '
      + 'değişiyor ve bulunmayan bir anahtara yazmak `core_config` içinde etkisiz bir '
      + 'satır açar — kullanıcı ayarı değiştirdiğini sanır. Ayrı bir “Mağaza Ayarları” '
      + 'ekranı da yoktur: her ayar onu kullanan ekranda durur.'),
  );
}

// ================================================================== mount

/**
 * `store.order.card` — Müşteriler ve Talepler ekranları bir siparişin künyesini
 * buradan okur (K3: o modüller bu modülü import etmez).
 */
export function capabilities(ctx) {
  return {
    'store.order.card': async (orderId) => {
      try {
        return await ctx.api(`${BASE}/orders/${Number(orderId)}`);
      } catch (error) {
        return { ok: false, error: error.message || 'Sipariş okunamadı.' };
      }
    },
  };
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'kit-panel so');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar([
    { key: 'list', label: 'Siparişler' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'list', (key) => showView(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '250px',
        placeholder: 'Sipariş no, müşteri, e-posta, telefon, takip no' },
      { kind: 'select', key: 'status', label: 'Durum',
        options: [{ value: '', label: 'Tümü — durum' }] },
      { kind: 'select', key: 'paymentState', label: 'Ödeme',
        options: [{ value: '', label: 'Tümü — ödeme' }] },
      { kind: 'select', key: 'channel', label: 'Kanal',
        options: [{ value: '', label: 'Tümü — kanal' }] },
      { kind: 'select', key: 'customerGroup', label: 'Grup',
        options: [{ value: '', label: 'Tümü — müşteri grubu' }] },
      { kind: 'select', key: 'dateField', label: 'Tarih alanı',
        options: [{ value: 'created', label: 'Sipariş tarihi' }] },
      // Varsayılan aralık BOŞ: hazır bir "son 7 gün" süzgeci, ekranı ilk açan
      // kişiden eski siparişleri sessizce gizlerdi.
      { kind: 'dateRange', key: 'range', label: 'Aralık', start: '', end: '' },
      { kind: 'numRange', key: 'total', label: 'Tutar', money: true },
      { kind: 'search', key: 'paymentMethod', width: '140px', placeholder: 'Ödeme yöntemi' },
      { kind: 'search', key: 'carrier', width: '130px', placeholder: 'Kargo firması' },
      { kind: 'search', key: 'city', width: '110px', placeholder: 'Şehir' },
      { kind: 'search', key: 'coupon', width: '110px', placeholder: 'Kupon kodu' },
      { kind: 'toggle', key: 'noInvoice', label: 'Faturası kesilmemiş' },
      { kind: 'toggle', key: 'noShipment', label: 'Kargoya verilmemiş' },
      { kind: 'toggle', key: 'hasNote', label: 'Not içeren' },
      { kind: 'toggle', key: 'risky', label: 'Riskli' },
      { kind: 'toggle', key: 'partialRefund', label: 'Kısmi iadeli' },
    ],
    onChange: () => refresh({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Süzgece uyanları rapor klasörüne yaz', onClick: exportAll }),
      button('Liste raporu', { onClick: () => report.run('list', { filters: currentFilters() }) }),
    ],
  });

  nodes.chips = chipRow(CHIPS, null, (key) => applyChip(key));
  nodes.status = statusLine();
  nodes.totals = h('div', 'so-totals');
  nodes.selbar = h('div', 'so-selbar');
  nodes.tableWrap = h('div', 'so-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size }),
  });
  // Tek gövde kabı; sekme değişince İÇERİĞİ değişir. İki ayrı kap tutup
  // `replaceChild` yapmak, kaydırma konumunu ve düğüm kimliklerini kaybettiriyor.
  nodes.body = h('div', 'so-body');
  const listView = [nodes.totals, nodes.selbar, nodes.tableWrap, nodes.pager.node];
  nodes.body.append(...listView);

  view.append(tabs.node, nodes.filters.node, nodes.chips.node, nodes.status.node, nodes.body);

  function showView(key) {
    const settings = key === 'settings';
    nodes.filters.node.hidden = settings;
    nodes.chips.node.hidden = settings;
    nodes.status.node.hidden = settings;
    if (settings) {
      nodes.body.replaceChildren();
      renderSettings(nodes.body);
    } else {
      nodes.body.replaceChildren(...listView);
      if (!state.loaded) refresh();
    }
  }

  root.replaceChildren(view);
  nodes.status.set('Siparişler alınıyor…');
  // Referans listeler ÖNCE gelir: süzgeç açılırları dolmadan liste çekmek,
  // kullanıcının seçtiği kanalı kaybettiriyordu.
  loadReference().then(() => refresh());

  return () => {
    nodes.filters?.destroy();          // arama alanı ve takvim global dinleyici tutar
    settingsForms.forEach((form) => form.destroy());
    settingsForms = [];
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
