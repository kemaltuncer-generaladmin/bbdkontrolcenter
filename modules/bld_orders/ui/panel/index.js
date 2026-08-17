// Sipariş Yönetimi paneli — gelen siparişlerin merkezden yönetimi.
//
// NE YAPAR: süzülen ve sayfalanan sipariş listesi (geçmişe bakar, fiyat
// taşır); sipariş ayrıntısı çekmecesi (aşama şeridi, kalemler, ödeme, geri
// alma penceresi); tam kalem listesiyle revizyon; durum ilerletme; gerekçeli
// iptal (iade + SMS + stok iadesi); revizyon geçmişi; fatura künyesi;
// muhasebe için CSV dışa aktarım ve bu ekranın kendi yazma izi.
//
// NE YAPMAZ:
//  · DURUM MAKİNESİNİ YENİDEN UYGULAMAZ. Geçiş matrisi ve 120 saniyelik tek
//    adım geri alma penceresi `OrderStatusTransition`'da, yani SUNUCUDADIR.
//    Bu ekranda hangi geçişin geçerli olduğuna dair TEK BİR İDDİA YOKTUR:
//    yedi durumun hepsi hedef olarak sunulur, kararı sunucu verir ve reddi
//    ekranda yazılır. `bld_kds` matrisin bir kopyasını taşıyor ve "olmayacak
//    düğmeyi çizmeyelim" diyor; buradaki karar farklı çünkü iki kopya
//    sessizce ayrışır ve ayrıştığında ekran, sunucunun KABUL EDECEĞİ bir
//    geçişi hiç sormadan reddeder — hatayı görmenin bir yolu bile kalmaz.
//  · GERİ ALMAYI TAHMİN ETMEZ. Pencere yalnız sunucu `can_undo` gönderirse
//    çizilir; geri sayım yerelde işler ama TABANI sunucunun `server_time`
//    değeridir. Sözleşme bu alanı saymıyor, bu yüzden yokluğu ekranda AÇIKÇA
//    yazılır ve düğme hiç çizilmez.
//  · FATURA ÜRETMEZ. Var olan belgenin künyesine bakar; üretim `bld_invoices`
//    alanının işi.
//  · KAYIT SİLMEZ. İptal edilen sipariş listede kalır.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · REVİZYON TAM LİSTEDİR, kalem farkı değil. Gönderilen liste siparişin YENİ
//    hâlidir; "değişmeyeni göndermeye gerek yok" demek siparişi BOŞALTIRDI.
//    Boş liste reddedilir ve siparişi kapatmanın yolu iptaldir.
//  · BİLEŞEN SATIRLARI GÖRÜNMEZ (B-19). Günün menüsü bir paket satırıdır;
//    personel onu TEK BİRİM olarak düzenler, sunucu bileşenleri yeniden açar.
//  · SEÇENEK KİMLİĞİ KORUNUR. Sunucu `option_value_ids` veriyorsa olduğu gibi
//    geri gönderilir — personel adede dokunurken "ekstra peynir" düşmez.
//    Seçenekler bu turda DÜZENLENMEZ, yalnız korunur.
//  · İPTAL DURUM DEĞİŞTİRME DEĞİLDİR. Ayrı uç, ayrı izin (`bld_orders.cancel`)
//    ve ayrı sonuç: iade tutarı, SMS ve STOK İADESİ. Sonuç ekranda yazılır;
//    yazılmazsa yönetici "neden birden 12 yer açıldı" diye sorar.
//  · SAYFA SAYAÇLARI YALNIZ O SAYFAYI SAYAR. Toplam `meta.total`'dedir ve
//    ikisi ayrı yazılır; karıştırılırsa yönetici yanlış karar verir.
//  · PARA HER YERDE KURUŞ. Gösterimde `money()`; hiçbir yerde bölme yok.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_orders/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_orders/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, confirmWithReason, h, loadStyles, money, num,
  pollLoop, stampIso, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { stepper, timeline } from '../../ui-kit/flow.js';

const BASE = '/api/bld_orders';

/** Gerekçe sınırları — sunucu da denetliyor (sözleşme §3), bu erken geri bildirim. */
const REASON_MIN = 10;
const REASON_MAX = 160;

/** Geri sayım şeridinin tik aralığı. Ağa çıkmaz, yalnız yazıyı tazeler. */
const TICK_MS = 1000;

// ------------------------------------------------------------------ durum

const EMPTY_STATE = {
  tab: 'orders',
  // BAĞLANTI: `ok:true` ile gelen `connected:false` (K7). Ayrı tutulur çünkü
  // "kayıt yok" ile "sunucuya ulaşılamıyor" aynı ekranda aynı görünmemeli.
  link: { connected: true, error: '' },
  contract: null,
  prefs: null,
  limits: null,
  rows: [],
  meta: {},
  counts: {},
  loaded: false,
  error: '',
  stale: false,
  page: 1,
  size: 25,
  serverTime: '',
  statusChip: null,
  filters: {},
  order: null,
  exportResult: null,
  audit: [],
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};

// ------------------------------------------------------------------ araçlar

/**
 * Sunucu iki türlü hata döndürebilir: HTTP durumu (kabuk `api()` fırlatır) ve
 * gövdedeki `{ok:false, error}`. İkincisi burada tek yerde okunur.
 */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) {
    const error = new Error(String(result.error));
    error.code = result.code || '';
    throw error;
  }
  return result;
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * Geçit ya da BLD sunucusu düştüğünde okuma uçları
 * `{ok:true, connected:false, error:"…"}` döndürür. Bu bir HATA DEĞİL, bir
 * DURUMDUR: veri gerçekten yok değil, ŞU AN OKUNAMIYOR. Sessizce boş liste
 * çizmek, yöneticiye "bugün hiç sipariş gelmemiş" dedirtirdi.
 *
 * @returns {boolean} veri güvenilir mi
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = {
      connected: false,
      error: payload.error || 'BLD sunucusuna ulaşılamıyor.',
    };
    return false;
  }
  // `connected` HİÇ YOKSA (ör. ağa çıkmayan `/overview`) durum DEĞİŞMEZ:
  // taşımayan bir yanıt, bilinen bir kopukluğu "düzeldi" saymamalı.
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

/** Bağlantı kopukken çizilen uyarı; bağlantı varken `null`. */
function linkAlert({ stale = false, what = 'Liste' } = {}) {
  if (state.link.connected) return null;
  return alertBox(
    `BLD sunucusuna ULAŞILAMIYOR — ${state.link.error} `
    + (stale
      ? `${what} son başarılı okumadan kalma ve BAYAT: yeni siparişler, durum `
        + 'değişiklikleri ve iptaller burada GÖRÜNMÜYOR olabilir. '
      : `${what} okunamadı. `)
    + 'Bağlantı geri geldiğinde ekran kendiliğinden düzelir.', 'bad');
}

/** Yazma düğmesinin kapalı olma nedeni; yazılabiliyorsa boş dize. */
function linkBlock() {
  if (state.link.connected) return '';
  return `BLD sunucusuna ulaşılamıyor (${state.link.error}) — ulaşılamayan bir `
    + 'sunucuya gönderilen işlem yöneticiye "gitti" hissi verirdi. Bağlantı '
    + 'gelince düğme kendiliğinden açılır.';
}

/**
 * Yazma düğmesi. Bağlantı yoksa ya da `blocked` doluysa KAPALI çizilir ve
 * nedenini söyler — sessizce çalışmayan düğme bırakılmaz (kit `blockedButton`).
 */
function writeButton(label, { variant = '', title = '', onClick, blocked = '' } = {}) {
  const why = blocked || linkBlock();
  if (why) return blockedButton(label, why, { variant });
  return button(label, { variant, title, onClick });
}

/** Aynı anda tek yazma. İki kez tıklanan bir iptal, iki iade denemesi olurdu. */
async function withBusy(message, work) {
  if (busy) return;
  busy = true;
  nodes.status?.set(message);
  try {
    await work();
  } catch (failure) {
    toast(failure.message || 'İşlem başarısız.', 'bad');
    nodes.status?.set(failure.message || 'İşlem başarısız.', true);
  } finally {
    busy = false;
  }
}

/**
 * Gerekçeli onay. Kitin kutusu 255 karaktere kadar yazdırıyor; sunucu sütunu
 * 160. Farkı burada yakalamak, kullanıcıya 422 yerine kendi cümlesini
 * göstermek demektir.
 */
async function askReason({ title, description, confirmLabel, danger = true }) {
  const reason = await confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (${REASON_MIN}-${REASON_MAX} karakter, denetim kaydına yazılır)`,
  });
  if (!reason) return null;
  if (reason.length > REASON_MAX) {
    toast(`Gerekçe en çok ${REASON_MAX} karakter olabilir; ${reason.length} yazıldı.`, 'bad');
    return null;
  }
  return reason;
}

/** Durum sözlüğünden tek kayıt. Sözleşme yerelde (`/overview`) tutulur. */
function statusInfo(code) {
  const found = (state.contract?.statuses || []).find((item) => item.code === code);
  return found || { code, label: code || '—', tone: 'dim', in_chain: false };
}

function statusBadge(row) {
  return badge(row.status_label || statusInfo(row.status).label, row.status_tone || 'dim');
}

/** Kuruş → "12.345,67 ₺". Bölme YOK; biçimlendirme kitin işi. */
function priceText(kurus) {
  return kurus === null || kurus === undefined ? '—' : money(kurus);
}

// =================================================================== veri

async function loadOverview() {
  try {
    const payload = await call(`${BASE}/overview`);
    linkOk(payload);
    state.contract = payload.contract;
    state.prefs = payload.prefs;
    state.limits = payload.limits;
    state.size = payload.prefs?.page_size || payload.limits?.page_size || 25;
  } catch (failure) {
    // Sözleşme ucu ağa çıkmıyor; buraya düşmek çekirdeğin kendisiyle ilgili
    // bir sorundur ve ekranın geri kalanı yine de çizilmeli.
    state.error = failure.message;
  }
}

function serverFilters() {
  const values = state.filters || {};
  const range = values.range || {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (range.start) params.set('date_from', range.start);
  if (range.end) params.set('date_to', range.end);
  if (values.delivery_type) params.set('delivery_type', values.delivery_type);
  if (values.source) params.set('source', values.source);
  if (state.statusChip) params.set('status', state.statusChip);
  params.set('page', String(state.page));
  params.set('per_page', String(state.size));
  return params.toString();
}

async function loadOrders() {
  try {
    const payload = await call(`${BASE}/orders?${serverFilters()}`);
    if (!linkOk(payload)) {
      // Eski satırlar EKRANDA KALIR ve "bayat" diye işaretlenir: boş bir tablo,
      // kopukluğu "sipariş yok" gibi gösterirdi.
      state.stale = state.rows.length > 0;
      state.error = payload.error || '';
      return;
    }
    state.rows = payload.items || [];
    state.meta = payload.meta || {};
    state.counts = payload.page_counts || {};
    state.serverTime = payload.server_time || '';
    state.loaded = true;
    state.stale = false;
    state.error = '';
  } catch (failure) {
    state.error = failure.message;
    state.stale = state.rows.length > 0;
  }
}

async function refreshOrders() {
  await loadOrders();
  if (state.tab === 'orders') paintOrders();
}

/**
 * SUNUCUYA GİDEN SÜZGEÇLERİN İMZASI. Yalnız bu imza değiştiğinde istek atılır.
 *
 * Ödeme durumu imzada YOKTUR: o süzgeç sunucuda karşılığı olmadığı için
 * istemcide çalışıyor ve onun değişmesi yeni bir istek gerektirmez. Ayrım
 * ölçülmüş bir maliyettir — her tuş vuruşunda liste çekmek, paylaşılan
 * 3000/saat kovasını tek ekranda bitirirdi.
 */
let lastSignature = '';

function serverSignature() {
  const values = state.filters || {};
  const range = values.range || {};
  return JSON.stringify([values.q || '', range.start || '', range.end || '',
    values.delivery_type || '', values.source || '', state.statusChip || '',
    state.page, state.size]);
}

/** Süzgeç değişince çağrılır: gerekiyorsa çeker, gerekmiyorsa yalnız çizer. */
async function syncList({ force = false } = {}) {
  const signature = serverSignature();
  if (!force && signature === lastSignature) {
    paintOrders();
    return;
  }
  lastSignature = signature;
  await refreshOrders();
}

// ============================================================ 1. Sipariş listesi

/**
 * İSTEMCİDE SÜZÜLEN alanlar. Sunucu süzgeci ödeme durumunu tanımıyor
 * (`orders.md` sorgu tablosunda yok) ve uydurulmadı: sayfadaki satırlar
 * üzerinde süzmek, olmayan bir parametreyi göndermekten dürüsttür — ama
 * ekranın bunu SÖYLEMESİ gerekir, yoksa "ödenmemiş 3 sipariş var" sanılır.
 */
const CLIENT_FILTERS = {
  payment_status: { kind: 'equals', field: 'payment_status' },
};

const ORDER_COLUMNS = [
  {
    key: 'order_number',
    label: 'Sipariş',
    width: 'minmax(0, 1.1fr)',
    sortable: true,
    cell: (row) => {
      const box = h('div', 'bo-cell');
      box.append(h('b', undefined, row.order_number || `#${row.id}`));
      if (row.from_subscription) box.append(badge('Abonelik', 'info'));
      return box;
    },
  },
  {
    key: 'service_date',
    label: 'Servis günü',
    width: '120px',
    sortable: true,
    cell: (row) => h('span', undefined, row.service_date || '—'),
  },
  {
    key: 'status',
    label: 'Durum',
    width: '130px',
    sortable: true,
    cell: (row) => statusBadge(row),
  },
  {
    key: 'customer_name',
    label: 'Müşteri',
    width: 'minmax(0, 1.4fr)',
    sortable: true,
    cell: (row) => {
      const box = h('div', 'bo-cell');
      box.append(h('span', undefined, row.customer_name || '—'));
      if (row.customer_phone) box.append(h('small', 'bo-dim', row.customer_phone));
      return box;
    },
  },
  {
    key: 'delivery_type',
    label: 'Teslimat',
    width: '120px',
    cell: (row) => h('span', undefined, row.delivery_type_label || '—'),
  },
  {
    key: 'item_count',
    label: 'Kalem',
    width: '80px',
    align: 'num',
    sortable: true,
    cell: (row) => h('span', undefined, num(row.item_count || 0)),
  },
  {
    key: 'total_kurus',
    label: 'Tutar',
    width: '120px',
    align: 'num',
    sortable: true,
    cell: (row) => h('span', undefined, priceText(row.total_kurus)),
  },
  {
    key: 'payment_status',
    label: 'Ödeme',
    width: '130px',
    cell: (row) => {
      const box = h('div', 'bo-cell');
      box.append(badge(row.payment_status_label || '—', row.payment_status_tone || 'dim'));
      box.append(h('small', 'bo-dim', row.payment_method_label || '—'));
      return box;
    },
  },
  {
    key: 'updated_at',
    label: 'Güncellendi',
    width: '140px',
    sortable: true,
    cell: (row) => {
      const node = h('span', undefined, ago(row.updated_at));
      node.title = stampIso(row.updated_at);
      return node;
    },
  },
];

function visibleRows() {
  return applyFilters(state.rows, state.filters || {}, CLIENT_FILTERS);
}

function showOrders() {
  nodes.body.replaceChildren();
  const wrap = h('div', 'bo-stack');
  wrap.append(nodes.orderFilters.node, nodes.chipSlot);
  nodes.orderTableSlot = h('div');
  wrap.append(nodes.orderTableSlot, nodes.pager.node);
  // SIRALAMA SAYFA İÇİDİR ve bu yazılır: sözleşmede sıralama parametresi yok
  // (`orders.md` sorgu tablosu) ve uydurulmadı. Söylenmeseydi "en pahalı
  // sipariş" diye 25 kaydın en pahalısına bakılırdı.
  wrap.append(h('small', 'bo-dim',
    'Sütun başlığına tıklamak YALNIZ açık sayfayı sıralar; sunucu tarafında '
    + 'sıralama parametresi yok. Tüm kayıtlar için aralığı daraltın ya da CSV '
    + 'dışa aktarımı kullanın.'));
  nodes.body.append(wrap);
  paintOrders();
}

/**
 * Durum çipleri sözleşmeden çizilir (`/overview`), panelde sabit liste tutulmaz.
 * Şerit AÇILIŞTA BOŞ durur ve sözleşme geldiğinde dolar: kodları burada
 * tekrarlamak, sunucudaki liste değiştiğinde ekranın yanlış çip göstermesi
 * olurdu.
 */
function buildChips() {
  const chips = chipRow(
    (state.contract?.statuses || []).map((item) => ({
      key: item.code, label: item.label, count: 0,
    })),
    null,
    (key) => { state.statusChip = key; state.page = 1; syncList(); },
  );
  nodes.statusChips = chips;
  nodes.chipSlot.replaceChildren(chips.node);
  nodes.chipSlot.append(h('small', 'bo-dim',
    'Çiplerdeki sayı AÇIK SAYFAYI sayar, toplamı değil; toplam durum satırında.'));
  if (nodes.rangeButton) {
    nodes.rangeButton.textContent = `Son ${num(state.prefs?.range_days || 7)} gün`;
  }
}

function paintOrders() {
  if (!nodes.orderTableSlot) return;
  nodes.orderTableSlot.replaceChildren();

  const warning = linkAlert({ stale: state.stale, what: 'Sipariş listesi' });
  if (warning) nodes.orderTableSlot.append(warning);
  if (state.error && state.link.connected) {
    nodes.orderTableSlot.append(alertBox(state.error, 'bad'));
  }

  if (!state.loaded && !state.error) {
    // Boş beyaz alan yerine tablonun ŞEKLİ: ekran bozuk sanılmasın.
    nodes.orderTableSlot.append(skeletonRows(8, 9));
    nodes.status.set('Siparişler alınıyor…');
    return;
  }

  const rows = visibleRows();
  const filtered = rows.length !== state.rows.length;
  const empty = emptyState({
    title: state.rows.length === 0 ? 'Bu süzgeçe uyan sipariş yok'
      : 'Sayfadaki kayıtlar bu ödeme durumuna uymuyor',
    text: state.rows.length === 0
      ? 'Tarih aralığını genişletin ya da durum çipini kaldırın. Süzgeç '
        + 'verilmezse sunucu son 7 günü döndürür.'
      : 'Ödeme durumu süzgeci YALNIZ AÇIK SAYFADA çalışır; sunucu bu süzgeci '
        + 'tanımıyor. Diğer sayfalarda uyan kayıt olabilir.',
    actions: [button('Süzgeci temizle', { onClick: () => clearFilters() })],
  });

  nodes.orderTable = dataTable({
    columns: ORDER_COLUMNS,
    rows,
    empty,
    onRow: (row) => openOrder(row),
  });
  nodes.orderTableSlot.append(nodes.orderTable.node);

  if (filtered) {
    nodes.orderTableSlot.append(hintBox(
      `Sayfadaki ${num(state.rows.length)} kaydın ${num(rows.length)} tanesi `
      + 'gösteriliyor: ödeme durumu süzgeci sunucuda değil, YALNIZ AÇIK SAYFADA '
      + 'çalışır.'));
  }

  const total = Number(state.meta.total || 0);
  nodes.pager.update({ total, page: Number(state.meta.page || state.page), size: state.size });
  nodes.statusChips?.counts(state.counts);
  nodes.status.set(statusText(), Boolean(state.error) || !state.link.connected);
}

function statusText() {
  const total = Number(state.meta.total || 0);
  const parts = [];
  parts.push(state.link.connected ? 'Bağlı' : `KOPUK — ${state.link.error}`);
  parts.push(`sayfada ${num(state.rows.length)} · toplam ${num(total)} sipariş`);
  if (state.serverTime) parts.push(`sunucu saati ${stampIso(state.serverTime)}`);
  if (state.stale) parts.push('LİSTE BAYAT');
  return parts.join(' · ');
}

/**
 * "Son N gün" hazır aralığı. N kullanıcının tercihinden gelir.
 *
 * ARALIK AÇILIŞTA BOŞ BIRAKILIR ve bu bilinçlidir: süzgeç verilmezse sunucu
 * kendi varsayılanını (son 7 gün) uyguluyor, dolu bir `from`/`to` ise SERVİS
 * GÜNÜNÜ süzüyor. İkisi aynı şey değil; açılışta tarih kutularını doldurmak,
 * sunucunun varsayılanını sessizce başka bir süzgeçle değiştirmek olurdu.
 * Düğme, kullanıcı BİLEREK bastığında o değişimi yapar.
 */
function applyRangePreset() {
  const days = Math.max(1, Number(state.prefs?.range_days || 7));
  nodes.orderFilters.set('range', { start: todayIso(-(days - 1)), end: todayIso() });
  state.filters = nodes.orderFilters.values();
  state.page = 1;
  syncList();
}

function clearFilters() {
  state.statusChip = null;
  state.page = 1;
  nodes.statusChips?.set(null);
  // `reset()` kendi `onChange`ini tetikler ve orada `syncList()` çağrılır;
  // buradan ikinci bir istek atmak, aynı listeyi iki kez çekmek olurdu.
  nodes.orderFilters.reset();
}

// =========================================================== 2. Sipariş çekmecesi

async function openOrder(row) {
  state.order = { id: row.id, row, detail: null, undo: null, revisions: [], invoice: null,
    error: '', draft: null };

  const box = drawer(nodes.root, {
    title: row.order_number || `#${row.id}`,
    subtitle: `${row.service_date || '—'} · ${row.customer_name || '—'}`,
    onClose: () => closeOrder(),
  });
  nodes.drawer = box;
  box.body.append(skeletonRows(6, 2));

  await loadOrderDetail(row.id);
  paintDrawer();
  startTicker();
}

function closeOrder() {
  stopTicker();
  nodes.drawer = null;
  state.order = null;
}

/**
 * Çekmecenin üç okuması. ÜÇÜ AYNI ANDA gider.
 *
 * Aralarında bağ yok — hiçbiri ötekinin sonucunu kullanmıyor — ve ayrı ayrı
 * beklemek üç uzak turu arka arkaya eklemekten başka bir şey yapmıyordu.
 * Üçü kendi `try/catch`ini taşıdığı için `Promise.all` reject etmez ve biri
 * patladığında çekmece yine çizilir (K7).
 *
 * ORTAK ALANA TEK YAZAN VAR: `state.link` yalnız ayrıntı okumasında
 * (`linkOk`), `state.order.error` yine yalnız orada güncelleniyor; geçmiş ve
 * fatura kendi alanlarına yazıyor. Bu yüzden burada birleştirilecek bir yarış
 * yok — sıralı hâlde de tek yazan oydu.
 */
async function loadOrderDetail(orderId) {
  const detail = async () => {
    try {
      const payload = await call(`${BASE}/orders/${orderId}`);
      linkOk(payload);
      state.order.detail = payload.order || null;
      state.order.undo = payload.undo || null;
      state.order.serverTime = payload.server_time || '';
      // Geri sayımın tabanı sunucunun saatidir; istemcinin saati kaymış olabilir
      // (`00-genel.md` §6). Aradaki farkı bir kez ölçüp yerelde ilerletiyoruz.
      state.order.skewMs = payload.server_time
        ? Date.parse(payload.server_time) - Date.now() : 0;
      state.order.draft = (payload.order?.items || []).map((item) => ({ ...item }));
    } catch (failure) {
      state.order.error = failure.message;
    }
  };

  const revisions = async () => {
    try {
      const payload = await call(`${BASE}/orders/${orderId}/revisions`);
      state.order.revisions = payload.items || [];
    } catch {
      // Geçmiş okunamadı: ayrıntı ekranı yine çizilir (K7). Boş bir geçmiş
      // "hiç revizyon yok" demek DEĞİLDİR ve bölümde yazılı.
      state.order.revisions = null;
    }
  };

  const invoice = async () => {
    try {
      state.order.invoice = await call(`${BASE}/orders/${orderId}/invoice`);
    } catch (failure) {
      state.order.invoice = { missing: false, error: failure.message };
    }
  };

  await Promise.all([detail(), revisions(), invoice()]);
}

function paintDrawer() {
  if (!nodes.drawer || !state.order) return;
  const body = nodes.drawer.body;
  body.replaceChildren();

  if (state.order.error) {
    body.append(alertBox(state.order.error, 'bad'));
    body.append(button('Yeniden dene', {
      onClick: async () => { await loadOrderDetail(state.order.id); paintDrawer(); },
    }));
    return;
  }
  const order = state.order.detail;
  if (!order) { body.append(skeletonRows(6, 2)); return; }

  // İPTAL SONUCU EN ÜSTTE ve çekmece yeniden çizilse bile KALIR: iade tutarı,
  // SMS ve stok iadesi bir kez gösterilip kaybolsaydı yönetici "neden birden
  // 12 yer açıldı" sorusunu ekranda cevaplayamazdı.
  if (state.order.cancelReport) body.append(cancelReportCard(state.order.cancelReport));

  body.append(summaryCard(order));
  body.append(stageCard(order));
  body.append(undoCard());
  body.append(itemsCard(order));
  body.append(statusCard(order));
  body.append(cancelCard(order));
  body.append(revisionsCard());
  body.append(invoiceCard());
}

function summaryCard(order) {
  const totals = order.totals || {};
  const payment = order.payment || {};
  const tiles = [
    { label: 'Toplam', value: priceText(totals.total_kurus ?? state.order.row.total_kurus) },
    { label: 'Kalem', value: num((order.items || []).length) },
    {
      label: 'Ödeme',
      value: order.payment_status_label
        || statusPaymentLabel(payment.status) || '—',
      title: `Yöntem: ${order.payment_method_label || payment.method || '—'}`,
    },
    { label: 'Revizyon', value: `#${num(order.revision_no || 0)}` },
  ];
  const box = h('div', 'bo-stack');
  box.append(kpiRow(tiles));
  if (order.customer_note) {
    box.append(hintBox(`Müşteri notu: ${order.customer_note}`));
  }
  if (order.delivery_type_label) {
    box.append(h('div', 'bo-dim', `Teslimat: ${order.delivery_type_label}`
      + (order.requested_at ? ` · İstenen an: ${stampIso(order.requested_at)}` : '')));
  }
  return card('Özet', box);
}

function statusPaymentLabel(code) {
  const found = (state.contract?.payment_statuses || []).find((item) => item.code === code);
  return found?.label || code || '';
}

/**
 * Aşama şeridi. `iptal` ZİNCİRDE DEĞİL: iptal bir aşama değil, zincirin
 * dışına çıkıştır; şeride konsaydı "yedinci adıma yaklaşıyoruz" gibi okunurdu.
 * İptal edilmiş sipariş şeridin ÜSTÜNDE ayrı bir kutuyla söylenir.
 */
function stageCard(order) {
  const chain = state.contract?.chain || [];
  const index = chain.indexOf(order.status);
  const steps = chain.map((code) => ({ label: statusInfo(code).label }));

  const box = h('div', 'bo-stack');
  if (order.status === 'iptal') {
    box.append(alertBox('Bu sipariş İPTAL EDİLDİ. Aşama şeridi son bilinen '
      + 'ilerlemeyi gösterir; iptal zincirin bir adımı değildir.', 'bad'));
  }
  box.append(stepper(steps, index));
  if (order.editable === false && order.not_editable_label) {
    box.append(alertBox(order.not_editable_label, 'warn'));
  } else if (order.editable !== true) {
    box.append(alertBox('Sunucu bu siparişin düzenlenebilir olup olmadığını '
      + 'söylemedi; revizyon kapalı. Bilinmeyen bir kilidi açık saymak, '
      + 'reddedilecek bir isteği "gönderildi" göstermek olurdu.', 'warn'));
  }
  return card('Aşama', box);
}

/**
 * GERİ ALMA — sunucunun `can_undo` alanı yüzeye çıkar, geri sayım YERELDE
 * işler. Alan gelmiyorsa düğme HİÇ ÇİZİLMEZ ve nedeni yazılır: hesaplanmış bir
 * pencere, dolmuş bir geri almayı açık gösterir ve personel 422 yerken kendini
 * yavaş sanardı.
 */
function undoCard() {
  const undo = state.order?.undo;
  const box = h('div', 'bo-stack');
  // Önceki çekmecenin şeridine tutunulmaz: DOM'dan düşmüş bir düğümü
  // tazelemek, geri sayımı hiç görünmeyen bir yerde işletmek olurdu.
  nodes.undoLine = null;
  if (!undo || undo.known === false) {
    box.append(hintBox(undo?.reason
      || 'Geri alma penceresi bilinmiyor.'));
    box.append(h('div', 'bo-dim',
      `Sunucudaki pencere ${undo?.window_seconds || 120} saniyedir ve kararı `
      + 'sunucu verir. Yanlış geçiş, izin verilen bir sonraki adımla düzeltilir.'));
    return card('Geri alma', box);
  }

  nodes.undoLine = h('div', 'bo-undo');
  box.append(nodes.undoLine);
  paintUndoLine();
  return card('Geri alma', box);
}

function paintUndoLine() {
  const undo = state.order?.undo;
  if (!nodes.undoLine || !undo) return;
  nodes.undoLine.replaceChildren();

  const left = remainingSeconds();
  if (!undo.can_undo || left <= 0) {
    nodes.undoLine.append(badge('Kapalı', 'dim'));
    nodes.undoLine.append(h('span', undefined,
      'Geri alma penceresi kapandı. Bundan sonrası ileri adımlarla düzeltilir.'));
    return;
  }
  nodes.undoLine.append(badge(`${left} sn`, 'warn'));
  nodes.undoLine.append(h('span', undefined,
    `Sunucu bu sipariş için tek adım geri almaya izin veriyor; pencere `
    + `${stampIso(undo.until)} anında kapanıyor. Geri alma da bir DURUM `
    + 'geçişidir: aşağıdaki listeden önceki durumu gerekçesiyle seçin.'));
}

/** Kalan saniye. Taban sunucu saati; fark açılışta bir kez ölçüldü. */
function remainingSeconds() {
  const undo = state.order?.undo;
  if (!undo?.until) return 0;
  const now = Date.now() + (state.order.skewMs || 0);
  const left = Math.floor((Date.parse(undo.until) - now) / 1000);
  return Math.max(0, Math.min(undo.window_seconds || 120, left));
}

function startTicker() {
  stopTicker();
  // `pollLoop` sekme gizliyken durur ve üst üste binmez. AĞA ÇIKMAZ: yalnız
  // ekrandaki yazıyı tazeler.
  nodes.ticker = pollLoop({ every: TICK_MS, run: () => paintUndoLine() });
}

function stopTicker() {
  nodes.ticker?.stop();
  nodes.ticker = null;
}

// ------------------------------------------------------------ kalemler

/**
 * KALEM DÜZENLEME. Yalnız ADET değişir; seçenekler korunur ve bu turda
 * düzenlenmez. Gönderilen liste siparişin TAM hâlidir — ekranda görünen ne ise
 * o gider.
 */
function itemsCard(order) {
  const box = h('div', 'bo-stack');
  const items = state.order.draft || [];

  if (items.length === 0) {
    box.append(emptyState({
      title: 'Kalem yok',
      text: 'Sunucu bu sipariş için kalem döndürmedi. Revizyon yazmak boş bir '
        + 'liste göndermek olurdu ve reddedilir.',
    }));
    return card('Kalemler', box);
  }

  const list = h('div', 'bo-items');
  items.forEach((item, index) => {
    const line = h('div', 'bo-item');
    const info = h('div', 'bo-item-info');
    info.append(h('b', undefined, item.name || `Ürün #${item.menu_id}`));
    const meta = [];
    if (Array.isArray(item.option_value_ids) && item.option_value_ids.length) {
      meta.push(`${item.option_value_ids.length} seçenek korunuyor`);
    }
    if (item.note) meta.push(`Not: ${item.note}`);
    if (meta.length) info.append(h('small', 'bo-dim', meta.join(' · ')));
    line.append(info);

    const input = h('input', 'bo-qty');
    input.type = 'number';
    input.min = '1';
    input.max = '999';
    input.value = String(item.quantity ?? 1);
    input.setAttribute('aria-label', `${item.name || item.menu_id} adedi`);
    input.disabled = order.editable !== true;
    input.addEventListener('change', () => {
      const value = Math.max(1, Math.min(999, Number(input.value) || 1));
      input.value = String(value);
      state.order.draft[index].quantity = value;
      paintRevisionActions(order);
    });
    line.append(input);
    list.append(line);
  });
  box.append(list);

  box.append(hintBox(
    'Revizyon siparişin TAM kalem listesini gönderir, farkı değil: ekranda '
    + 'görünen liste siparişin yeni hâli olur. Günün menüsü TEK BİRİM olarak '
    + 'düzenlenir; bileşenlerini sunucu yeniden açar. Seçenekler olduğu gibi '
    + 'geri gönderilir.'));

  nodes.revisionActions = h('div', 'bo-actions');
  box.append(nodes.revisionActions);
  paintRevisionActions(order);
  return card('Kalemler', box);
}

function changedItems(order) {
  const before = order.items || [];
  return (state.order.draft || []).filter((item, index) =>
    Number(item.quantity) !== Number(before[index]?.quantity));
}

function paintRevisionActions(order) {
  if (!nodes.revisionActions) return;
  nodes.revisionActions.replaceChildren();
  const changed = changedItems(order);

  if (order.editable !== true) {
    nodes.revisionActions.append(blockedButton('Revizyonu yaz',
      order.not_editable_label
      || 'Sunucu bu siparişin düzenlenebilir olduğunu söylemedi.',
      { variant: 'primary' }));
    return;
  }
  if (changed.length === 0) {
    nodes.revisionActions.append(blockedButton('Revizyonu yaz',
      'Hiçbir adet değişmedi. Aynı listeyi göndermek, denetim izine bir '
      + 'revizyon satırı yazıp hiçbir şeyi değiştirmemek olurdu.',
      { variant: 'primary' }));
    return;
  }
  nodes.revisionActions.append(h('span', 'bo-dim',
    `${num(changed.length)} kalemin adedi değişti.`));
  nodes.revisionActions.append(writeButton('Revizyonu yaz', {
    variant: 'primary',
    onClick: () => submitRevision(order),
  }));
  nodes.revisionActions.append(button('Değişiklikleri geri al', {
    onClick: () => {
      state.order.draft = (order.items || []).map((item) => ({ ...item }));
      paintDrawer();
    },
  }));
}

async function submitRevision(order) {
  const changed = changedItems(order);
  const reason = await askReason({
    title: 'Revizyon yaz',
    description: `${order.order_number || `#${order.id}`} siparişinin `
      + `${num(changed.length)} kaleminin adedi değişiyor. Gönderilen liste `
      + 'siparişin YENİ hâlidir. Sunucu farkı hesaplar; iade ya da ek ücret '
      + 'doğabilir ve revizyon geçmişine yazılır.',
    confirmLabel: 'Revizyonu yaz',
    danger: false,
  });
  if (!reason) return;

  await withBusy('Revizyon yazılıyor…', async () => {
    const result = await call(`${BASE}/orders/${state.order.id}/revisions`, {
      method: 'POST',
      body: { reason, items: state.order.draft },
    });
    announce(result, 'Revizyon yazıldı.');
    if (result.revision) {
      const revision = result.revision;
      const parts = [];
      if (revision.refund_kurus) parts.push(`iade ${money(revision.refund_kurus)}`);
      if (revision.extra_charge_kurus) {
        parts.push(`ek ücret ${money(revision.extra_charge_kurus)}`);
      }
      if (parts.length) toast(`Revizyon #${revision.revision_no}: ${parts.join(' · ')}`);
    }
    await loadOrderDetail(state.order.id);
    paintDrawer();
    await refreshOrders();
  });
}

// ------------------------------------------------------------ durum

/**
 * DURUM İLERLETME. Yedi durumun hepsi sunulur ve kararı SUNUCU verir; bu ekran
 * geçiş matrisinin kopyasını taşımaz. `iptal` burada YOKTUR — ayrı uç, ayrı
 * izin, ayrı sonuç (iade + SMS + stok iadesi).
 */
function statusCard(order) {
  const box = h('div', 'bo-stack');
  box.append(hintBox(
    'İzin verilen geçişleri SUNUCU belirler (`OrderStatusTransition`). Bu ekran '
    + 'matrisin kopyasını tutmaz: iki kopya sessizce ayrışır ve ayrıştığında '
    + 'ekran, sunucunun kabul edeceği bir geçişi hiç sormadan reddederdi. '
    + 'Reddedilen bir geçiş burada nedeniyle birlikte yazılır.'));

  const row = h('div', 'bo-actions');
  for (const item of state.contract?.statuses || []) {
    if (item.code === 'iptal') continue;
    if (item.code === order.status) {
      row.append(badge(`Şu an: ${item.label}`, item.tone));
      continue;
    }
    row.append(writeButton(item.label, {
      title: `Siparişi “${item.label}” durumuna geçir`,
      onClick: () => submitStatus(order, item),
    }));
  }
  box.append(row);
  return card('Durum', box);
}

async function submitStatus(order, target) {
  const reason = await askReason({
    title: `Durumu “${target.label}” yap`,
    description: `${order.order_number || `#${order.id}`} siparişi `
      + `“${statusInfo(order.status).label}” → “${target.label}”. Gerekçe, `
      + 'siparişin kendi durum geçmişine de yorum olarak düşer. Geri alma '
      + 'penceresi kısadır ve kararı sunucu verir.',
    confirmLabel: `“${target.label}” yap`,
    danger: false,
  });
  if (!reason) return;

  await withBusy('Durum yazılıyor…', async () => {
    const result = await call(`${BASE}/orders/${state.order.id}/status`, {
      method: 'POST',
      body: { reason, status: target.code },
    });
    announce(result, `Durum “${target.label}” oldu.`);
    await loadOrderDetail(state.order.id);
    paintDrawer();
    await refreshOrders();
  });
}

// ------------------------------------------------------------ iptal

/**
 * İPTAL. Ayrı izin (`bld_orders.cancel`) ister; izin yoksa uç 403 döner ve
 * ekran bunu SÖYLER (K9 — görünürlük sunucuda süzülür, panel izin denetlemez).
 */
function cancelCard(order) {
  const box = h('div', 'bo-stack');
  if (order.status === 'iptal') {
    box.append(alertBox('Sipariş zaten iptal edilmiş.', 'dim'));
    return card('İptal', box);
  }

  box.append(alertBox(
    'İPTAL DURUM DEĞİŞTİRMEK DEĞİLDİR: ödenmiş siparişte iade kaydı üretir, '
    + 'müşteriye iptal SMS\'i gönderir ve porsiyonları gün toplamı ile ürün '
    + 'tavanından DÜŞÜRÜR — o kadar sipariş yeniden alınabilir hâle gelir. '
    + 'Teslim edilmiş sipariş iptal edilemez. Abonelikten üretilmiş bir '
    + 'siparişin iptali ABONELİĞİ DURDURMAZ, yalnız o günün siparişini düşürür.',
    'warn'));

  const refund = checkbox('İade kaydı üret', true,
    'Ödeme “Ödendi” ise iade kaydı oluşur. Kapatmak SERBESTTİR: para elden '
    + 'iade edilmiş olabilir ve sistemin ikinci kez iade üretmemesi gerekir; '
    + 'sunucu bu durumda uyarı döndürür.');
  const notify = checkbox('Müşteriye iptal SMS\'i gönder', true,
    'Kapatmak, müşterinin iptalden haberi olmaması demektir.');
  box.append(refund.node, notify.node);

  const row = h('div', 'bo-actions');
  row.append(writeButton('Siparişi iptal et', {
    variant: 'danger',
    onClick: () => submitCancel(order, refund.value, notify.value),
  }));
  box.append(row);
  return card('İptal', box);
}

function checkbox(label, initial, hint) {
  const node = h('label', 'bo-check');
  const input = h('input');
  input.type = 'checkbox';
  input.checked = Boolean(initial);
  node.append(input, h('span', undefined, label));
  if (hint) node.append(h('small', 'bo-dim', hint));
  return { node, get value() { return input.checked; } };
}

async function submitCancel(order, refund, notify) {
  const reason = await askReason({
    title: 'Siparişi iptal et',
    description: `${order.order_number || `#${order.id}`} iptal edilecek. `
      + `İade kaydı: ${refund ? 'üretilecek' : 'ÜRETİLMEYECEK'} · `
      + `SMS: ${notify ? 'gönderilecek' : 'GÖNDERİLMEYECEK'}. `
      + 'Porsiyonlar gün toplamı ve ürün tavanından düşer. Bu işlem ayrı bir '
      + 'yetki ister ve denetim kaydına iki satır yazar.',
    confirmLabel: 'İptal et',
  });
  if (!reason) return;

  await withBusy('İptal gönderiliyor…', async () => {
    const result = await call(`${BASE}/orders/${state.order.id}/cancel`, {
      method: 'POST',
      body: { reason, refund, notify_customer: notify },
    });
    announce(result, 'Sipariş iptal edildi.');
    const report = result?.data || null;
    await loadOrderDetail(state.order.id);
    // Rapor TAZE OKUMADAN SONRA yazılır: `loadOrderDetail` durumu sıfırlıyor
    // ve önce yazılsaydı yeniden çizimde silinirdi.
    state.order.cancelReport = report;
    paintDrawer();
    await refreshOrders();
  });
}

/** İptalin sonucu EKRANDA yazılır; yoksa "neden 12 yer açıldı" sorulur. */
function cancelReportCard(data) {
  const box = h('div', 'bo-stack');
  const lines = [
    `İade: ${data.refund_created ? money(data.refund_kurus) : 'üretilmedi'}`,
    `SMS: ${data.sms_sent ? 'gönderildi' : 'gönderilmedi'}`,
    `Stok iadesi: gün toplamından ${num(data.stock_released?.day || 0)} porsiyon`,
  ];
  for (const item of data.stock_released?.items || []) {
    lines.push(`  · ürün #${item.menu_id}: ${num(item.quantity)} porsiyon`);
  }
  for (const line of lines) box.append(h('div', undefined, line));
  for (const warning of data.warnings || []) box.append(alertBox(warning, 'warn'));
  return card('İptal sonucu', box);
}

// ------------------------------------------------------------ geçmiş

function revisionsCard() {
  const rows = state.order?.revisions;
  if (rows === null) {
    return card('Revizyon geçmişi',
      alertBox('Revizyon geçmişi okunamadı. Bu, "hiç revizyon yok" DEMEK '
        + 'DEĞİLDİR; bağlantı gelince yeniden deneyin.', 'warn'));
  }
  const events = (rows || []).map((row) => ({
    title: `Revizyon #${row.revision_no} — ${row.origin_label}`,
    at: stampIso(row.created_at),
    detail: [
      row.reason,
      row.refund_kurus ? `iade ${money(row.refund_kurus)}` : '',
      row.extra_charge_kurus ? `ek ücret ${money(row.extra_charge_kurus)}` : '',
      row.note || '',
    ].filter(Boolean).join(' · '),
    tone: row.origin === 'control' ? 'info' : 'dim',
  }));
  return card('Revizyon geçmişi',
    timeline(events, { emptyText: 'Bu siparişte revizyon yok.' }),
    'Merkezden mi kasadan mı yazıldığı satırda yazılı');
}

function invoiceCard() {
  const payload = state.order?.invoice;
  const box = h('div', 'bo-stack');
  if (!payload || payload.error) {
    box.append(alertBox(payload?.error || 'Fatura künyesi okunamadı.', 'warn'));
    return card('Fatura', box);
  }
  if (payload.missing) {
    box.append(hintBox('Bu siparişe ait fatura belgesi oluşturulmamış. Belge '
      + 'üretimi bu ekranın işi değildir (Fatura modülü).'));
    return card('Fatura', box);
  }
  const invoice = payload.invoice || {};
  box.append(h('div', undefined, `Belge: ${invoice.invoice_no || '—'}`));
  box.append(h('div', undefined, `Durum: ${invoice.status || '—'}`));
  box.append(h('div', undefined, `Tutar: ${priceText(invoice.total_kurus)}`));
  box.append(h('div', 'bo-dim', `Düzenlenme: ${stampIso(invoice.issued_at)}`));
  return card('Fatura', box);
}

/**
 * Yazma sonucunu bildirir. KURU PROVA "yapıldı" DEMEZ: bir kurulum provayı
 * ayardan geri açarsa ekran yanlış bilgi vermemeli.
 */
function announce(result, message) {
  if (result?.dry_run) {
    toast('KURU PROVA: sunucu denetimleri koştu ama HİÇBİR ŞEY YAZILMADI.', 'warn');
    return;
  }
  toast(message, 'good');
}

// ============================================================ 3. Dışa aktarım

function showExport() {
  nodes.body.replaceChildren();
  const box = h('div', 'bo-stack');

  box.append(hintBox(
    'CSV, EKRANDAKİ SÜZGEÇLERLE üretilir: soldaki listede ne görünüyorsa aynı '
    + 'küme dışa aktarılır (sayfalama hariç — dosya tüm sayfaları taşır). '
    + 'Dosya UTF-8 BOM ile başlar (Excel Türkçe karakterleri bozmasın diye), '
    + 'para sütunları KURUŞ tam sayıdır ve dosya yalnız size okunur biçimde '
    + '(0600) masaüstündeki rapor klasörüne yazılır.'));

  const limit = h('label', 'bo-field');
  limit.append(h('span', undefined, 'Satır tavanı'));
  const input = h('input', 'bo-num');
  input.type = 'number';
  input.min = '100';
  input.max = '20000';
  input.value = String(state.limits?.export_max_rows || 5000);
  limit.append(input);
  limit.append(h('small', 'bo-dim',
    'Tavan aşılırsa sunucu satırları KESER ve hata vermez — kesilmiş bir dosya '
    + 'hiç dosya olmamasından iyidir. Kesilme aşağıda yazılır.'));
  box.append(limit);

  const row = h('div', 'bo-actions');
  row.append(writeButton('CSV üret', {
    variant: 'primary',
    onClick: () => runExport(Number(input.value) || 0),
  }));
  box.append(row);

  nodes.exportResult = h('div', 'bo-stack');
  box.append(nodes.exportResult);
  paintExportResult();

  nodes.body.append(card('Dışa aktarım', box));
}

async function runExport(maxRows) {
  const values = state.filters || {};
  const range = values.range || {};
  await withBusy('CSV üretiliyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: {
        q: values.q || '',
        date_from: range.start || '',
        date_to: range.end || '',
        delivery_type: values.delivery_type || '',
        source: values.source || '',
        status: state.statusChip || '',
        max_rows: maxRows,
      },
    });
    state.exportResult = result;
    paintExportResult();
    toast('CSV yazıldı.', 'good');
  });
}

function paintExportResult() {
  if (!nodes.exportResult) return;
  nodes.exportResult.replaceChildren();
  const result = state.exportResult;
  if (!result) return;

  nodes.exportResult.append(alertBox(
    `Dosya yazıldı: ${result.path}`, 'good'));
  nodes.exportResult.append(h('div', undefined,
    `${num(result.total_rows ?? 0)} satır · ${num(result.bytes || 0)} bayt`));
  if (result.truncated) {
    nodes.exportResult.append(alertBox(
      `Satır tavanı (${num(result.max_rows)}) doldu ve dosya KESİLDİ. Eksik bir `
      + 'listeyi tam sanmayın: aralığı daraltın ya da tavanı yükseltin.', 'warn'));
  }
}

// ============================================================ 4. Yerel iz

async function showAudit() {
  nodes.body.replaceChildren();
  const box = h('div', 'bo-stack');
  box.append(hintBox(
    'Bu, BU EKRANDAN yapılan yazma DENEMELERİNİN yerel kaydıdır — sunucunun '
    + 'denetim izi değil. Ağ koparsa ya da istek yarıda kalırsa "kim neyi '
    + 'denedi" sorusunun cevabı yalnız burada kalır: uzak kayıt yalnız '
    + 'sunucuya ULAŞAN isteği bilir. Satır silinmez.'));
  nodes.auditSlot = h('div');
  box.append(nodes.auditSlot);
  nodes.body.append(card('Yerel iz', box));

  nodes.auditSlot.append(skeletonRows(6, 5));
  try {
    const payload = await call(`${BASE}/audit?limit=200`);
    state.audit = payload.items || [];
    state.auditLoaded = true;
  } catch (failure) {
    state.audit = [];
    nodes.auditSlot.replaceChildren(alertBox(failure.message, 'bad'));
    return;
  }
  paintAudit();
}

const AUDIT_TONES = {
  ok: 'good', dry_run: 'info', denendi: 'dim', engellendi: 'warn', hata: 'bad',
};

function paintAudit() {
  if (!nodes.auditSlot) return;
  nodes.auditSlot.replaceChildren();
  const table = dataTable({
    columns: [
      { key: 'created_at', label: 'An', width: '160px',
        cell: (row) => h('span', undefined, stampIso(row.created_at)) },
      { key: 'action', label: 'Eylem', width: '140px' },
      { key: 'order_id', label: 'Sipariş', width: '100px', align: 'num',
        cell: (row) => h('span', undefined, row.order_id ? `#${row.order_id}` : '—') },
      { key: 'actor', label: 'Aktör', width: 'minmax(0, 1fr)' },
      { key: 'result', label: 'Sonuç', width: '120px',
        cell: (row) => badge(row.result, AUDIT_TONES[row.result] || 'dim') },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
    ],
    rows: state.audit,
    dense: true,
    empty: emptyState({ title: 'Bu ekrandan henüz yazma denenmedi' }),
  });
  nodes.auditSlot.append(table.node);
}

// ============================================================ 5. Ekran ayarı

/**
 * GÖRÜNTÜLEME TERCİHİ — BLD'ye hiçbir şey gitmez, gerekçe istenmez.
 *
 * Sayfa boyutunu değiştirmek bir iş yazması değildir; burada gerekçe kutusu
 * açmak, gerçek yazmalardaki gerekçe alışkanlığını törenselleştirirdi.
 */
function showPrefs() {
  nodes.body.replaceChildren();
  const box = h('div', 'bo-stack');
  box.append(hintBox(
    'Bu ayarlar YALNIZ bu ekranı etkiler ve Kontrol Merkezi\'nin kendi '
    + 'deposunda durur; BLD sunucusuna gönderilmez. Yoklama aralığını '
    + 'kısaltmak paylaşılan sunucu bütçesini (saatte 3000 istek, tüm BLD '
    + 'ekranları için) hızlandırılmış biçimde yakar ve ikinci bir yöneticinin '
    + 'ekranını "çok istek" hatasına düşürür.'));

  const prefs = state.prefs || {};
  const size = numberField('Sayfa başına kayıt', prefs.page_size ?? 25, 5, 100,
    'Sunucu tavanı 100.');
  const days = numberField('“Son N gün” düğmesinin aralığı', prefs.range_days ?? 7, 1, 90,
    'Süzgeç şeridindeki hazır aralık düğmesi bu kadar gün geriye gider.');
  const poll = numberField('Yoklama aralığı (saniye)', prefs.poll_seconds ?? 15, 5, 300,
    'Sekme gizliyken yoklama zaten durur.');
  const auto = checkbox('Liste kendiliğinden tazelensin', prefs.auto_refresh !== false,
    'Kapatılırsa liste yalnız “Yenile” ile güncellenir; ekranda BAYAT veri '
    + 'durabilir ve bu durum satırında yazar.');
  box.append(size.node, days.node, poll.node, auto.node);

  const row = h('div', 'bo-actions');
  row.append(button('Kaydet', {
    variant: 'primary',
    onClick: () => savePrefs({
      page_size: size.value, range_days: days.value,
      poll_seconds: poll.value, auto_refresh: auto.value,
    }),
  }));
  box.append(row);
  nodes.body.append(card('Ekran ayarı', box));
}

function numberField(label, initial, min, max, hint) {
  const node = h('label', 'bo-field');
  node.append(h('span', undefined, label));
  const input = h('input', 'bo-num');
  input.type = 'number';
  input.min = String(min);
  input.max = String(max);
  input.value = String(initial);
  node.append(input);
  if (hint) node.append(h('small', 'bo-dim', hint));
  return {
    node,
    get value() {
      return Math.max(min, Math.min(max, Number(input.value) || min));
    },
  };
}

async function savePrefs(values) {
  await withBusy('Tercih kaydediliyor…', async () => {
    const result = await call(`${BASE}/prefs`, { method: 'PUT', body: values });
    state.prefs = result.prefs || state.prefs;
    state.size = state.prefs.page_size;
    toast('Ekran ayarı kaydedildi.', 'good');
    // Yoklama döngüsü YENİDEN KURULUR: aralık değiştiği hâlde eski döngü
    // dönmeye devam etseydi, ayar kaydedildiğini söyleyip hiçbir şey
    // değiştirmemiş olurdu.
    nodes.restartLive?.();
  });
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel bo');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'orders', label: 'Siparişler' },
    { key: 'export', label: 'Dışa aktarım' },
    { key: 'audit', label: 'Yerel iz' },
    { key: 'prefs', label: 'Ekran ayarı' },
  ], 'orders', (key) => showTab(key));

  // Süzgeç şeridi sekmeyle birlikte YOK EDİLMEZ: `filterBar` global dinleyici
  // tutuyor (takvim) ve her sekme geçişinde yenisini kurmak onları biriktirirdi.
  nodes.orderFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px',
        placeholder: 'Sipariş no, müşteri adı, telefon' },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // `dateRange` kitin kendi takvimini kullanır.
      { kind: 'dateRange', key: 'range', label: 'Servis günü', start: '', end: '' },
      { kind: 'select', key: 'delivery_type', label: 'Teslimat',
        options: [{ value: '', label: 'Hepsi' },
          { value: 'delivery', label: 'Adrese teslim' },
          { value: 'pickup', label: 'Gel-al' }] },
      { kind: 'select', key: 'source', label: 'Kaynak',
        options: [{ value: '', label: 'Hepsi' },
          { value: 'manual', label: 'Elle verilen' },
          { value: 'subscription', label: 'Abonelikten' }] },
      // SUNUCUDA KARŞILIĞI YOK: yalnız açık sayfada süzer ve ekran bunu yazar.
      { kind: 'select', key: 'payment_status', label: 'Ödeme (sayfada)',
        options: [{ value: '', label: 'Hepsi' },
          { value: 'pending', label: 'Bekliyor' },
          { value: 'paid', label: 'Ödendi' },
          { value: 'failed', label: 'Başarısız' },
          { value: 'refunded', label: 'İade edildi' }] },
    ],
    onChange: (values) => {
      state.filters = values;
      // Sunucuya giden bir süzgeç değiştiyse sayfa BAŞA döner: aksi hâlde
      // 6. sayfada duran kullanıcı boş bir tablo görür ve "kayıt yok" sanır.
      state.page = 1;
      syncList();
    },
    actions: [
      // Etiket tercih geldikten sonra tazelenir (`buildChips`): "Son 7 gün"
      // yazan bir düğmenin 14 gün getirmesi, ekranın yalan söylemesi olurdu.
      nodes.rangeButton = button('Son günler', {
        title: 'Servis günü aralığını tercihteki kadar geriye çeker',
        onClick: () => applyRangePreset(),
      }),
      button('Yenile', { onClick: () => syncList({ force: true }) }),
    ],
  });
  state.filters = nodes.orderFilters.values();

  // Çipler sözleşme geldikten sonra doldurulur; yuva şimdi kurulur ki sekme
  // çizimi veriyi beklemek zorunda kalmasın.
  nodes.chipSlot = h('div', 'bo-chips');

  nodes.pager = pager({
    total: 0, page: 1, size: state.size,
    onChange: ({ page, size }) => {
      state.page = page;
      state.size = size;
      syncList();
    },
  });

  nodes.status = statusLine();
  nodes.body = h('div', 'bo-body');

  const bar = h('div', 'bo-topbar');
  bar.append(nodes.tabs.node);
  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    // CANLI VERİ YALNIZ SİPARİŞLER SEKMESİNDE: kapalı sekme için istek üretmek,
    // paylaşılan 3000/saat kovasını boşuna yakar.
    if (key === 'orders') startLive(); else stopLive();
    ({
      orders: showOrders,
      export: showExport,
      audit: showAudit,
      prefs: showPrefs,
    }[key] || showOrders)();
  }

  function startLive() {
    if (nodes.live) return;
    // TERCİH GELMEDEN DÖNGÜ KURULMAZ. Sekme açılışı sözleşme okumasından önce
    // gerçekleşiyor; burada varsayılanla başlamak, "otomatik tazeleme kapalı"
    // diyen bir kullanıcının ekranını yine de yoklatırdı.
    if (!state.prefs) return;
    // Kullanıcı otomatik tazelemeyi kapattıysa hiç kurulmaz: kapalı bir
    // şalterin arkasında dönen bir döngü, ayarın yalan söylemesi olurdu.
    if (state.prefs.auto_refresh === false) return;
    const every = Math.max(5, Number(state.prefs?.poll_seconds || 15)) * 1000;
    // `pollLoop` sekme gizliyken durur ve üst üste binmez.
    nodes.live = pollLoop({ every, run: () => syncList({ force: true }) });
  }

  function stopLive() {
    nodes.live?.stop();
    nodes.live = null;
  }

  // Tercih kaydedildiğinde döngü yeniden kurulur. Kapanış (`nodes.restartLive`)
  // modül kapsamındaki `savePrefs`in mount içindeki iki fonksiyona ulaşmasının
  // tek yolu; ikisini modül kapsamına taşımak, `nodes.live` durumunu panel
  // kapandıktan sonra da canlı tutardı.
  nodes.restartLive = () => {
    if (state.tab !== 'orders') return;
    stopLive();
    startLive();
  };

  root.replaceChildren(view);
  showTab('orders');

  // Açılış: önce sözleşme (ağa çıkmaz), sonra liste. Sıra önemli — çipler ve
  // etiketler sözleşmeden çizilir ve sözleşme gelmeden çizilen bir çip şeridi,
  // sunucudaki kod listesinden ayrışmış olurdu.
  (async () => {
    await loadOverview();
    buildChips();
    nodes.pager.update({ size: state.size, page: 1, total: 0 });
    startLive();
    await syncList({ force: true });
  })();

  // TEMİZLİK GERÇEK KAYNAK BIRAKIR (kit kuralı 4): iki `pollLoop` hem
  // zamanlayıcıyı hem `visibilitychange` dinleyicisini, `filterBar` ise takvim
  // ve arama için tuttuğu global dinleyicileri bırakır. Bırakılmazsa panel her
  // açılışta bir tane daha birikir ve kapalı bir ekran yoklamaya devam eder.
  return () => {
    stopLive();
    stopTicker();
    nodes.orderFilters?.destroy();
    nodes.restartLive = null;
    lastSignature = '';
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
