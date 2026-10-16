// Siparişler paneli — mağazanın günlük çalışma ekranı.
//
// NE YAPAR: arama + tarih ve sayaçlı durum çipleriyle sipariş listesi (gerisi
// "Filtreler" arkasında); yedi sütunluk tablo ve satırın ana eylemi ("Kargoya
// ver" / "Fatura kes"); satırdan çekmecede sipariş künyesi, en sık üç eylem
// tepede; toplu kargoya verme / fatura kesme (ÖNCE ATLANANLAR LİSTESİ, sonra
// gerekçeli onay); etiket indirme; "Dışa aktar" menüsünde Excel ve PDF.
//
// SADE EKRAN (docs/tasarim-sade-magaza.md — bağlayıcı): görünür süzgeç iki,
// ikincil eylemler tek menüde, varsayılan görünümde açıklama kutusu yok,
// uyarılar yalnız eyleme dönük ve tek satır, tablo en çok yedi sütun, teknik
// dil ("tarama", "uç", "geçit") ekranda geçmez — gerekçesi koddaki yorumdadır.
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
//  · Ödeme durumu Bagisto'da bir alan değildir. Liste ucu ödeme YÖNTEMİNİ
//    veriyor, DURUMUNU değil; durum iki ayrı listeden çözülür: fatura kaydının
//    `state` alanı ve sanal POS denemesinin `state` alanı (ikisi de TEK sayfa
//    isteğiyle çekilir, sipariş başına istek atılmaz). Banka yanıtı gelmemiş
//    deneme "Belirsiz" görünür ve AYRI RENKTEDİR: "Ödenmedi" yazmak, parası
//    çekilmiş müşteriden ikinci kez tahsilat istemeye yol açardı.
//  · Toplu kargoya verme yalnız UYGUN siparişleri alır; uygun olmayanın nedeni
//    satırda yazılıdır ve önce KURU PROVA çalışır.
//  · Liste ucu SIĞ satır veriyor. Fatura, gönderi ve iade durumu sunucunun
//    ORTAK TARAMASINDAN gelir (liste uçları, sipariş başına istek yok); tarama
//    arka planda koşar, durum satırı yaşını yazar ("tarama 2 dakika önce") ve
//    tarama sürüyorsa ekran bir kez daha sorar. Yalnız not ve müşteri grubu
//    süzgeci sipariş detayı ister; o okuma da arka planda yürür ve bitmeden
//    durum satırı "eksik uygulandı" der.
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
  ago, button, clip, confirmSimple, confirmWithReason, copyText, csvBlob, h, loadStyles,
  menuButton, money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { applyChoiceFilter } from '../../ui-kit/choice.js';
import { RANGE_PRESETS } from '../../ui-kit/datefield.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { stepper } from '../../ui-kit/flow.js';
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
  // Ortak taramanın durumu: hazır mı, sürüyor mu, ne zamandan. `pending` = not/
  // müşteri grubu detayı arka planda okunuyor. `warnings` = okunamayan yan liste.
  ready: true, pending: false, warnings: [],
  scan: { state: 'empty', running: false, at: '', error: '' },
  overviewKey: null,
  // `summary` süzgecin TAMAMINA mı ait (sayaç ucu / taranmış liste), yoksa
  // yalnız sunucunun verdiği SAYFAYA mı? Ciro yalnız ilkinde yazılır.
  summaryFull: false,
  summary: { count: 0, liveCount: 0, revenue: 0, average: 0, canceled: 0 },
  reference: {
    statuses: [], paymentStates: [], dateFields: [], channels: [], customerGroups: [],
    carriers: [],
  },
  // Ödeme kanıtının durumu: hangi kaynak okundu, kaç satır bankaya sorulmalı.
  evidence: {
    invoiceOk: false, posOk: false, complete: false, warnings: [], uncertain: 0,
    invoiceRead: 0, posRead: 0, invoiceFloorDay: '', posFloorDay: '',
  },
  selection: [], chip: null, loaded: false,
};

let api = null;
let capability = null;
// Panelden panele gezinme. Kabuk taşır, panel hedefi bilir (K3): kargoya
// verme bu ekranın işi değil, kullanıcıyı asıl evine göndermek onun işi.
let openPanel = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

// Tarama sürerken ekranı BİR KEZ daha sorar. Sürekli yoklanmaz: tarama
// bitmişse ikinci istek hazır sonucu alır; süresiz yoklamak mağazanın istek
// payını (geçitte dakikada 55, bütün ekranların ortak payı) boşuna yerdi.
const SCAN_POLL_MS = 15_000;
let scanTimer = null;

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

/**
 * Verinin ne zamandan olduğu — "güncellendi 2 dakika önce".
 *
 * Sayaçlar ve süzgeçli liste sunucunun arka plandaki ORTAK TARAMASINDAN gelir
 * (bkz. backend/service.py). Kullanıcıya "tarama" denmez; söylenen tek şey
 * verinin yaşıdır ve tazelenirken bunun söylenmesidir.
 */
function freshText() {
  const scan = state.scan || {};
  if (!state.ready || scan.running) return 'güncelleniyor…';
  return scan.at ? `güncellendi ${ago(scan.at)}` : '';
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const parts = [`${num(state.total)} sipariş`];
  // KPI şeridi YOK (docs/tasarim-sade-magaza.md §6): sipariş sayısı, ciro ve
  // ortalama sepet bu satırda, durum dağılımı çiplerde yazıyor. Ciro süzgecin
  // TAMAMINA aittir; elde yalnız sayfanın toplamı varsa hiç yazılmaz — 29
  // sayfalık süzgecin cirosu ilk sayfanınki sanılırdı. İptaller ciroya girmez.
  const sum = state.summary || EMPTY_STATE.summary;
  if (state.summaryFull && sum.liveCount) {
    parts.push(`ciro ${money(sum.revenue)}`, `ort. sepet ${money(sum.average)}`);
  }
  const fresh = freshText();
  if (fresh) parts.push(fresh);
  if (state.pending) parts.push('not/grup bilgisi okunuyor');
  if (state.scan?.error) parts.push('son güncelleme yapılamadı');
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

/**
 * Listeyi çeker; GEREKİYORSA sayaç şeridini de.
 *
 * `paging` — yalnız sayfa/boyut değişti. Sayaçlar ve süzgecin toplamı sayfaya
 * bağlı DEĞİL; eskiden her sayfa değişiminde `/overview` yeniden istenir ve
 * sunucu siparişlerin tamamını yeniden tarardı. Artık süzgeç/çip aynıysa
 * sayaçlar yerinde kalır.
 * `fresh` — "Yenile": ortak tarama tazelik süresini beklemeden yenilenir.
 */
async function refresh({ page = state.page, size = state.size, paging = false,
  fresh = false } = {}) {
  if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
  // Süzgeç/çip aynı ve yalnız sayfa değiştiyse sayaç şeridine ait alanlar
  // (süzgecin TAMAMININ toplamı, kanıt şeridi, tavan) yerinde KALIR: sunucu
  // sayfalı listede o alanlar yalnız SAYFANINKİDİR ve üstüne yazılsaydı
  // 29 sayfalık süzgecin cirosu ilk sayfanınki gibi görünürdü.
  const key = queryString({ page: '', size: '' });
  const keep = paging && key === state.overviewKey;
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('Siparişler alınıyor…');
  let payload;
  try {
    // Sunucu taramayı beklemez (en çok birkaç saniye); süre yine de geniş
    // istenir: geçidin dakikalık payı başka ekranlarca doluysa istek sırada
    // bekleyebilir ve kabuğun 60 saniyelik varsayılanı onu kesip "çekirdeğe
    // bağlanılamadı" gösterirdi.
    payload = await api(`${BASE}/orders?${queryString({
      page, size, refresh: fresh ? 1 : '',
    })}`, { timeoutSeconds: 300 });
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
    truncated: Boolean(payload.truncated) || (keep && state.truncated),
    partial: Boolean(payload.partial) || (keep && state.partial),
    scannedCount: payload.scannedCount || 0,
    summary: keep ? state.summary : (payload.summary || EMPTY_STATE.summary),
    summaryFull: keep ? state.summaryFull : Boolean(payload.scanned),
    evidence: keep ? state.evidence : (payload.evidence || EMPTY_STATE.evidence),
    ready: payload.ready !== false,
    pending: Boolean(payload.pending) || (keep && state.pending),
    warnings: payload.warnings?.length ? payload.warnings : (keep ? state.warnings : []),
    // Sunucu sayfalı listede tarama okunmaz; önceki sayaç taramasının
    // yaşı ekranda kalır.
    scan: payload.scanned ? (payload.scan || EMPTY_STATE.scan) : state.scan,
    selection: [],
    loaded: true,
  };
  renderTable();
  renderTotals();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
  if (!keep) {
    // Taranmış liste aynı istekte taramayı zaten tazeledi; sayaç ucu ikinci
    // bir tazeleme başlatmasın.
    await loadOverview({ fresh: fresh && !state.scanned });
  }
  scheduleScanFollowUp();
}

/**
 * Çip sayaçları ve süzgecin TAMAMININ toplamı. Sunucu bunları liste ucuyla
 * AYNI ortak taramadan okur — mağazaya ikinci tarama gitmez.
 */
async function loadOverview({ fresh = false } = {}) {
  const key = queryString({ page: '', size: '' });
  let payload;
  try {
    payload = await api(`${BASE}/overview?${queryString({
      page: '', size: '', refresh: fresh ? 1 : '',
    })}`, { timeoutSeconds: 300 });
  } catch {
    return;                    // sayaçsız da çalışır (K7)
  }
  if (!payload.connected) return;
  state.scan = payload.scan || state.scan;
  if (payload.ready === false) {
    // Tarama bitmeden SIFIR YAZILMAZ: "Bekleyen 0" bekleyen iş yok demektir.
    nodes.chips.counts({});
    nodes.status?.set(statusText(), !state.connected);
    return;
  }
  state.overviewKey = key;
  // `null` sayaç (gönderi listesi okunamadı) boş kalır; sıfır uydurulmaz.
  nodes.chips.counts(payload.counts || {});
  state.summary = payload.summary || state.summary;
  state.summaryFull = true;
  state.truncated = Boolean(payload.truncated);
  state.partial = state.partial || Boolean(payload.partial);
  state.pending = state.pending || Boolean(payload.pending);
  state.warnings = payload.warnings?.length ? payload.warnings : state.warnings;
  // Sayaç ucu SÜZGECİN TAMAMINA bakar: "kaç sipariş bankaya sorulmalı"
  // sorusunun doğru cevabı sayfanın değil, süzgecin tamamınınkidir.
  state.evidence = payload.evidence || state.evidence;
  renderTotals();
  nodes.status?.set(statusText(), !state.connected);
}

// Tarama ya da detay okuması sürüyorsa ekran BİR KEZ daha sorar (bkz.
// SCAN_POLL_MS). Bitmemişse sonraki yanıt zamanlayıcıyı yeniden kurar.
function scheduleScanFollowUp() {
  if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
  if (state.ready && !state.pending && !state.scan?.running) return;
  scanTimer = setTimeout(() => {
    scanTimer = null;
    if (!state.loaded || busy) return;      // ekran kapandı ya da iş sürüyor
    refresh();
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
    statuses: payload.statuses || [],
    paymentStates: payload.paymentStates || [],
    dateFields: payload.dateFields || [],
    channels: payload.channels || [],
    customerGroups: payload.customerGroups || [],
    // Toplu kargonun taşıyıcı listesi. Gelmezse seçici SERBEST METİN olarak
    // çalışır; boş bir açılır kutu göstermek işi durdururdu.
    carriers: payload.carriers || [],
  };
  const withAll = (label, items) => [{ value: '', label }, ...items];
  nodes.filters.options('status', withAll('Tümü — durum',
    state.reference.statuses.map((item) => ({ value: item.value, label: item.label }))));
  nodes.filters.options('paymentState', withAll('Tümü — ödeme',
    state.reference.paymentStates.map((item) => ({ value: item.value, label: item.label }))));
  // TEK SEÇENEKLİ SÜZGEÇ ÇİZİLMEZ (choice.js): mağazada tek kanal var, "Kanal"
  // kutusu hiçbir satırı elemeyen bir soruydu. Değer kanalın ADIdır — satır
  // `channel` alanında adı taşır; kod göndermek listeyi boşaltırdı.
  applyChoiceFilter(nodes.filters, 'channel',
    state.reference.channels.map((item) => ({ value: item.name, label: item.name })),
    { allLabel: 'Tümü — kanal' });
  applyChoiceFilter(nodes.filters, 'customerGroup',
    state.reference.customerGroups.map((item) => ({ value: item.name, label: item.name })),
    { allLabel: 'Tümü — müşteri grubu' });
  nodes.filters.options('dateField',
    state.reference.dateFields.map((item) => ({ value: item.value, label: item.label })));
  if (payload.stale) {
    toast('Referans listeler bayat kopyadan geldi; mağaza yanıt vermedi.', 'warn');
  }
}

// ------------------------------------------------------------------- çizim

/**
 * Uyarı şeridi — YALNIZ eyleme dönen durumlar, her biri TEK SATIR
 * (docs/tasarim-sade-magaza.md §3). Ayrıntı satırın `title`ında durur.
 *
 * Bilgi kutuları kalktı: "tarama sürüyor" durum satırında ("güncelleniyor…")
 * ve boş tabloda yazar; "kanıt tek sayfada bitmedi" satırın ödeme rozetinin
 * ipucundadır. KPI şeridi de kalktı (bkz. `statusText`).
 */
function renderTotals() {
  if (!nodes.totals) return;
  nodes.totals.replaceChildren();
  if (!state.connected) return;
  const line = (text, detail = '') => {
    const box = alertBox(text, 'warn');
    if (detail) box.title = detail;
    nodes.totals.append(box);
  };
  const info = { ...EMPTY_STATE.evidence, ...(state.evidence || {}) };
  if (info.uncertain) {
    // Para havada: "Ödenmedi" yazmak müşteriden ikinci kez tahsilat istetirdi.
    // Bu siparişler toplu kargoya da girmez (`shipBlock`).
    line(`${num(info.uncertain)} siparişte ödeme belirsiz — kargolamadan önce bankadan `
      + 'teyit edin.', 'Bankadan kesin yanıt gelmemiş bir kart ödemesi var; para çekilmiş '
      + 'olabilir. Kesin sonuç ve provizyon iptali mağaza/banka tarafındadır.');
  }
  if (!info.invoiceOk || !info.posOk) {
    line('Ödeme bilgisinin bir kısmı okunamadı; “Ödeme” sütunu eksik olabilir — Yenile.',
      (info.warnings || []).join(' · '));
  }
  if (state.warnings?.length) {
    // Yan liste okunamadı: o bilgiye dayanan sayaçlar BOŞ (sıfır değil).
    line('Kargo/fatura bilgisi tam okunamadı; bazı sayaçlar boş — Yenile.',
      state.warnings.join(' · '));
  }
  if (state.truncated) {
    line('Liste çok büyük; sayılar eksik olabilir — tarih aralığını daraltın.');
  }
  if (state.partial && !state.pending) {
    line('Not/grup süzgeci bazı siparişlerde uygulanamadı — süzgeci daraltın.');
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

/** Kargoya verme ekranı (tek ev: Kargo Yönetimi). Panel hedefi bilir (K3). */
function openShipping(row) {
  openPanel?.('store_shipping', { orderId: row.id, orderNumber: row.orderNo || row.incrementId });
}

const SHIP_WORDS = { none: 'Kargolanmadı', partial: 'Kısmen kargolandı', full: 'Kargolandı' };

/**
 * "Kargo" sütunu: kargo hâli + satırın ANA EYLEMİ (docs/tasarim-sade-magaza.md §5).
 *
 * Eylem siparişin SIRADAKİ işidir: faturası yoksa "Fatura kes", faturalıysa
 * "Kargoya ver" (Kargo Yönetimi'nde açılır — kargoya vermenin tek evi orası).
 * Ödeme belirsiz/iade edilmişse düğme yerine NEDEN yazar; renk tek başına
 * anlam taşımaz. Hâl bilinmiyorsa "Kargolanmadı" UYDURULMAZ, yalnız eylem
 * durur — Kargo Yönetimi gönderiyi yine kendisi doğrular.
 */
function shipCell(row) {
  const box = h('span', 'so-cellstack so-ship');
  if (['canceled', 'closed'].includes(row.status)) {
    box.append(h('span', 'so-sub', '—'));
    return box;
  }
  if (row.shipKnown && row.shipmentState !== 'none') {
    box.append(badge(SHIP_WORDS[row.shipmentState] || row.shipmentLabel,
      FULFIL_TONES[row.shipmentState] || ''));
  }
  if (row.track) box.append(trackCell(row));
  else if (row.carrier) box.append(clip(h('span', 'so-sub'), row.carrier, 26));
  if (row.shipKnown && row.shipmentState === 'full') return box;

  const needsInvoice = row.invoiceKnown
    ? row.invoiceState === 'none' : row.paymentState === 'unpaid';
  const stop = (event) => event?.stopPropagation?.();   // satır tıklaması çekmeceyi açmasın
  if (needsInvoice) {
    box.append(button('Fatura kes', {
      title: 'Siparişin tamamını faturalar; gerekçe sorulur',
      onClick: (event) => { stop(event); invoiceOrder(row, []); },
    }));
  } else if (row.shipBlock) {
    box.append(clip(h('span', 'so-why'), row.shipBlock, 34));
  } else {
    box.append(button('Kargoya ver', {
      title: 'Kargo Yönetimi’nde açılır: teklif, etiket ve takip no orada',
      onClick: (event) => { stop(event); openShipping(row); },
    }));
  }
  return box;
}

/**
 * Ödeme durumu — İKİ KAYNAKTAN çözülür (fatura kaydı + sanal POS denemesi) ve
 * hangi kaynaktan geldiği satırın ipucunda yazar.
 *
 * BELİRSİZ SATIR AYRI RENKTEDİR ve yazıyla da söylenir: renk tek başına anlam
 * taşımaz. "Belirsiz" ne "Ödendi" ne "Ödenmedi" demektir — banka yanıtı
 * gelmemiştir ve para çekilmiş olabilir.
 */
function paymentCell(row) {
  const box = h('span', 'so-pay');
  const chip = badge(row.paymentLabel, row.paymentTone);
  if (row.paymentAttention) chip.classList.add('so-pay-uncertain');
  const why = [row.paymentNote, row.paymentSource ? `Kaynak: ${row.paymentSource}.` : '']
    .filter(Boolean).join(' ');
  if (why) chip.title = why;
  box.append(chip);
  if (row.paymentAttention) {
    const call = h('span', 'so-why', 'bankadan teyit edin');
    call.title = why;
    box.append(call);
  }
  return box;
}

// YEDİ SÜTUN, SİPARİŞ NO ÖNDE (docs/tasarim-sade-magaza.md §5). Ara toplam,
// kargo, indirim, KDV, kalem sayısı ve fatura hâli çekmecededir: on dört
// sütun 1440 px'te bile yatay kayıyordu ve sipariş no ekran dışında kalıyordu.
const COLUMNS = [
  { key: 'orderNo', label: 'Sipariş no', width: '118px', sortable: true,
    cell: (row) => h('b', 'so-code', row.orderNo) },
  { key: 'createdAt', label: 'Tarih', width: '124px', sortable: true,
    cell: (row) => short(row.createdAt) },
  {
    key: 'customer',
    label: 'Müşteri',
    // Canlıda ad alanları kimi siparişte boş; satır e-postaya düşer. Kırpma
    // sınırı e-posta boyuna göre — "kemal.tu…" kimin siparişi olduğunu söylemez.
    width: 'minmax(200px, 2fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'so-cellstack');
      box.append(clip(h('b'), row.customer, 46));
      box.append(h('span', 'so-sub', row.city || row.email || ''));
      return box;
    },
  },
  { key: 'grandTotal', label: 'Tutar', width: '112px', align: 'num', sortable: true,
    cell: (row) => h('b', 'so-total', money(row.grandTotal)) },
  { key: 'paymentLabel', label: 'Ödeme', width: '150px', className: 'wrap',
    cell: paymentCell },
  {
    key: 'statusLabel',
    label: 'Durum',
    width: '128px',
    cell: (row) => {
      const box = h('span', 'so-cellstack');
      box.append(badge(row.statusLabel, row.statusTone));
      if (row.late) box.append(h('span', 'so-late', `${num(row.lateDays)} gündür bekliyor`));
      return box;
    },
  },
  { key: 'shipmentState', label: 'Kargo', width: 'minmax(170px, 1.3fr)', className: 'wrap',
    cell: shipCell },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (!state.ready) {
    // "Tarama daha bitmedi" ile "süzgece uyan sipariş yok" AYRI şeylerdir.
    return emptyState({
      title: 'Siparişler yükleniyor',
      text: 'Liste birkaç saniye içinde kendiliğinden dolar.',
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
    text: 'Süzgeçleri gevşetin ya da temizleyin.',
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

/** Seçili kimliklerin SATIRLARI. Toplu iş kararları satırın kendisinden çıkar. */
function selectedRows() {
  const chosen = new Set(state.selection.map(String));
  return state.items.filter((row) => chosen.has(String(row.id)));
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
  const rows = selectedRows();
  // Kargoya verilebilenler ve verilemeyenler AYRI: düğme yalnız uygun olanları
  // götürür, ötekiler sessizce listeye karışmaz.
  const ready = rows.filter((row) => !row.shipBlock);
  const blocked = rows.filter((row) => row.shipBlock);

  bar.classList.add('on');
  bar.append(h('b', undefined, `${num(count)} sipariş seçildi`));
  bar.append(h('span', 'kit-spacer'));
  // En çok üç düğme + "Diğer" menüsü (docs/tasarim-sade-magaza.md §2).
  //
  // TOPLU KARGO BURADA YAPILMAZ, TEK EVE DEVREDİLİR. Eskiden bu düğme mağazaya
  // doğrudan yazıyor ve tek onayda N siparişi "gönderilmiş" işaretliyordu —
  // paket yola çıkmıyordu (Bagisto kendi kaydını açıyor, etiket alınmıyordu).
  // Seçim Kargo Yönetimi sihirbazına aktarılır ve orada sipariş sipariş
  // yürür: her gönderinin desisi ayrı ölçülür, taşıyıcısı ayrı onaylanır.
  bar.append(
    button(`Kargoya ver${ready.length === count ? '' : ` (${num(ready.length)})`}`, {
      variant: 'primary',
      disabled: ready.length === 0,
      title: ready.length
        ? `${num(ready.length)} sipariş Kargo Yönetimi’nde sırayla kargoya verilir.`
        : 'Seçilenlerin hiçbiri kargoya verilebilir durumda değil — nedenler aşağıda.',
      onClick: () => openPanel?.('store_shipping', { orderIds: ready.map((row) => row.id) }),
    }),
    button('Fatura kes', { onClick: () => batchDialog('invoice', rows.map((row) => row.id)) }),
    menuButton('Diğer', [
      { label: 'Etiket indir', title: 'Alınmış kargo etiketlerini rapor klasörüne indirir',
        onClick: () => downloadLabels() },
      { label: 'Kargo manifestosu — PDF', title: 'Teslim tutanağı: sipariş, şehir, takip no',
        onClick: () => report.run('manifest', { orderIds: state.selection.map(Number) }) },
    ]),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => {
        nodes.table.clearSelection();
        state.selection = [];
        renderSelectionBar();
      },
    }),
  );

  if (blocked.length) {
    // NEDEN gruplanarak yazılır: "3 sipariş kargoya verilemez" tek başına
    // kullanıcıyı satır satır aramaya bırakırdı.
    const groups = new Map();
    for (const row of blocked) groups.set(row.shipBlock, (groups.get(row.shipBlock) || 0) + 1);
    const line = h('div', 'so-selwhy');
    line.append(h('b', undefined,
      `${num(blocked.length)} seçili sipariş kargoya verilemez —`));
    for (const [why, howMany] of groups) line.append(badge(`${num(howMany)} × ${why}`, 'warn'));
    bar.append(line);
  }
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
  if (state.items.some((row) => !row.shipKnown)) {
    // Sessiz eksik dosya yok: bu görünümde gönderi kaydı okunmadı, takip no
    // boş kalır. Ödeme durumu ayrı kaynaktan çözüldüğü için dolu gelir.
    toast('Takip no bu dosyada boş olabilir; tam liste için Dışa aktar → Tümü.', 'warn');
  }
}

async function exportAll() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm kayıtları dışa aktar',
    description: 'Süzgece uyan bütün siparişler rapor klasörüne Excel (CSV) dosyası '
      + 'olarak yazılır.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Dosya hazırlanıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { filters: currentFilters() },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) {
      toast('Liste çok büyük; dosya eksik olabilir — tarih aralığını daraltın.', 'warn');
    }
  });
}

async function downloadLabels(orderIds = state.selection) {
  await withBusy('Etiketler indiriliyor…', async () => {
    const result = await api(`${BASE}/labels`, {
      method: 'POST', body: { orderIds: orderIds.map(Number) },
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

  // EN ÇOK BEŞ SEKME (kullanıcı kararı; tasarim-sade-magaza.md). Akraba
  // bölümler birleşti: ödeme + iade, kargo + fatura, notlar + işlem geçmişi.
  const tabs = [
    { key: 'summary', label: 'Özet' },
    { key: 'items', label: 'Kalemler' },
    { key: 'payment', label: 'Ödeme ve iade' },
  ];
  if (shipmentCap || invoiceCap) tabs.push({ key: 'shipping', label: 'Kargo ve fatura' });
  tabs.push({ key: 'notes', label: 'Notlar ve geçmiş' });

  const bar = tabBar(tabs, 'summary', (key) => paint(key));
  bar.badge('items', payload.items.length || undefined);
  bar.badge('payment', payload.returns.length || undefined);

  const head = h('div', 'so-drawer-head');
  const payChip = badge(order.paymentLabel, order.paymentTone);
  if (order.paymentAttention) payChip.classList.add('so-pay-uncertain');
  if (order.paymentNote) payChip.title = order.paymentNote;
  head.append(
    badge(order.statusLabel, order.statusTone),
    payChip,
    h('span', 'so-sub', short(order.createdAt)),
  );
  const pane = h('div', 'so-pane');
  // EN SIK ÜÇ EYLEM EN ÜSTTE, sekmelerden önce: işi yapan kişi aramadan bulsun.
  box.body.append(head, actionBar(payload), bar.node, pane);

  if (payload.warnings && payload.warnings.length) {
    // Tek satır; ayrıntı (hangi parça, neden) ipucunda.
    const warn = alertBox('Bazı bilgiler okunamadı; görünen kısım doğrudur.', 'warn');
    warn.title = payload.warnings.join(' · ');
    box.body.insertBefore(warn, bar.node);
  }

  const context = { payload, forms, shipmentCap, invoiceCap, auditCap };

  function paint(key) {
    dropForms();
    pane.replaceChildren();
    ({
      summary: paintSummary, items: paintItems, payment: paintPaymentTab,
      shipping: paintShippingTab, notes: paintNotesTab,
    }[key] || paintSummary)(pane, context);
  }
  paint('summary');
}

function paintPaymentTab(pane, context) {
  paintPayment(pane, context);
  paintReturns(pane, context);
}

/** Kargo ve Fatura başka modüllerin yeteneğidir (K3); yalnız var olan çizilir. */
function paintShippingTab(pane, { payload, shipmentCap, invoiceCap }) {
  if (shipmentCap) {
    const box = h('div');
    pane.append(card('Kargo', box));
    paintShipping(box, { payload, shipmentCap });
  }
  if (invoiceCap) {
    const box = h('div');
    pane.append(card('Fatura', box));
    paintInvoice(box, { payload, invoiceCap });
  }
}

function paintNotesTab(pane, context) {
  paintNotes(pane, context);
  paintHistory(pane, context);
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

  pane.append(
    journeyCard(payload, shipmentCap),
    card('Sipariş', facts),
    addressCard('Fatura adresi', payload.billing),
    addressCard('Teslimat adresi', payload.shipping),
    card('Toplam dökümü', totals),
  );
  if (closed.length) pane.append(card('Kargo ve fatura özeti', digest));
}

/** İki kayıt kümesinin EN ESKİ zaman damgası. ISO metni takvim sırasında dizilir. */
function firstStamp(rows) {
  return (rows || []).map((item) => item.createdAt).filter(Boolean).sort()[0] || '';
}

/**
 * Siparişin aşama şeridi — kitin `stepper`'ı (ADR 0011).
 *
 * NEDEN ÜÇ ADIM VE "TESLİM EDİLDİ" YOK. Bu çekmecenin künye ucu Bagisto'nun
 * KENDİ kayıtlarını taşıyor: fatura ve gönderi satırları var, taşıyıcının
 * teslim bildirimi YOK (`shipment_rows` yalnız firma, takip no, adet ve tarih
 * veriyor). Dördüncü bir "Teslim edildi" adımı çizmek, ekranın bilmediği bir
 * şeyi "bekliyor" diye göstermesi olurdu: teslim edilmiş bir sipariş de teslim
 * edilmemiş gibi görünürdü. Teslim durumu Kargo sekmesindedir ve oradaki şeridi
 * `store.shipment.byOrder` yeteneği çizer — bu panel taşıyıcıya kendi sormaz.
 *
 * NEDEN "FATURALANDI" İKİNCİ SIRADA. Bu kurulumda kargoya verme ödeme kanıtı
 * ister (`shipBlock`); olağan sıra alındı → faturalandı → kargoya verildi.
 * Sıra bozulursa şerit BUNU DA GÖSTERİR: `activeIndex` yalnız kesintisiz
 * tamamlanan başlangıcı işaretler, atlanmış bir adım "tamamlandı" görünmez ama
 * gerçekleşmiş adım da kendi yazısını kaybetmez.
 *
 * KISMİ HÂL YALAN SÖYLETMEZ. `invoiceState`/`shipmentState` üç durumludur
 * (TUZAK: kısmi fatura ve kısmi kargo olağandır); "tamamlandı" ile "bekliyor"
 * arasında üçüncü bir cümle gerekiyordu ve kit `state` alanıyla onu veriyor.
 */
function journeyCard(payload, shipmentCap) {
  const order = payload.order;
  const durdu = ['canceled', 'closed'].includes(order.status);
  // Her adım kendi hâlini YAZIYLA söyler; ton yalnız o yazının yanında durur.
  // Zincir sınıfı (`activeIndex`) ile karıştırılmaz: sırası gelmeden
  // gerçekleşmiş adım işaretlenir ama zinciri tamamlanmış göstermez.
  const asama = (kind, count, partialText) => {
    if (kind === 'full') return { state: 'tamamlandı', tone: 'good' };
    if (kind === 'partial') {
      return { state: `${partialText} (${num(count)} kayıt)`, tone: 'warn' };
    }
    return { state: durdu ? 'gerçekleşmedi' : 'bekliyor', tone: durdu ? 'dim' : '' };
  };

  const steps = [
    { label: 'Sipariş alındı', at: short(order.createdAt), state: 'tamamlandı', tone: 'good' },
    { label: 'Faturalandı', at: short(firstStamp(payload.invoices)),
      ...asama(order.invoiceState, order.invoiceCount, 'kısmen faturalandı') },
    { label: 'Kargoya verildi', at: short(firstStamp(payload.shipments)),
      ...asama(order.shipmentState, order.shipmentCount, 'kısmen kargolandı') },
  ];

  // KESİNTİSİZ ÖNEK. `stepper` `index <= activeIndex` olan her adımı tamamlanmış
  // boyar; faturasız kargolanmış bir siparişte 2 vermek, kesilmemiş faturayı
  // kesilmiş göstermek olurdu.
  let done = 0;
  if (order.invoiceState === 'full') done = 1;
  if (done === 1 && order.shipmentState === 'full') done = 2;

  // İptal/kapalı siparişte kalan adımlar "gerçekleşmedi" yazar; ayrıca bir
  // uyarı kutusu çizilmez (tasarim-sade-magaza.md §3).
  const box = h('div', 'so-journey');
  box.append(stepper(steps, done));
  // İpucu VAR OLAN bir yeri göstermeli: kargo yeteneği yoksa "Kargo sekmesi"
  // diye olmayan bir sekmeye yollamak, arayan kişiyi boşuna dolaştırır.
  return card('Siparişin yolculuğu', box, shipmentCap
    ? 'teslim bilgisi: Kargo ve fatura sekmesi'
    : 'teslim bilgisi bu kurulumda okunamıyor');
}

/**
 * Çekmecenin tepesindeki eylemler: Fatura kes · Kargoya ver (ya da Etiket
 * indir) · Durum değiştir; seyrek olanlar "Diğer" menüsünde. Tek mavi düğme:
 * siparişin SIRADAKİ işi (faturası yoksa fatura, varsa kargo).
 *
 * KARGOYA VERME BURADA YAPILMAZ, Kargo Yönetimi'nde açılır. KULLANICININ
 * KURALI: "farklı yerde 'kargoya ver' olmasın." Bu ekranın eski kargo düğmeleri
 * Bagisto'nun kendi gönderi kaydını açıyordu: paket yola ÇIKMIYOR, etiket
 * alınmıyor, müşteriye mesaj gitmiyordu. Gerçek zincir (gönderi aç → teklif →
 * etiket SATIN AL → takip no → SMS) yalnız orada koşar.
 *
 * Durum değiştirme AÇIK (04.09.2026): geçilebilecek durumlar SUNUCUDAN gelir
 * (`payload.statusTargets`); matrisi burada kopyalamak, ikisi ayrıştığında
 * olmayan bir seçeneği sunup 422 aldırırdı. Normalde durum TÜRETİLİR ve sonraki
 * fatura/gönderi elle konan değeri ezebilir — bu, düğmenin ipucunda ve onay
 * penceresinde yazar.
 *
 * İptal geri alınamaz; ayrı izin, süre penceresi ve gerekçe ister. Yıkıcı
 * eylem olduğu için ön sırada değil, menüdedir (tasarim-sade-magaza.md §5).
 */
function actionBar(payload) {
  const order = payload.order;
  const targets = payload.statusTargets || [];
  const closedOrder = ['canceled', 'closed'].includes(order.status);
  const invoiced = order.invoiceState === 'full';
  const shipped = order.shipmentState && order.shipmentState !== 'none';
  const row = h('div', 'so-actions');
  row.append(
    button('Fatura kes', {
      variant: invoiced || closedOrder ? '' : 'primary',
      disabled: invoiced || closedOrder,
      title: invoiced ? 'Siparişin tamamı zaten faturalanmış'
        : 'Siparişin tamamını faturalar; kısmi fatura Kalemler sekmesinde',
      onClick: () => invoiceOrder(order, payload.items),
    }),
    shipped
      ? button('Etiket indir', {
        title: 'Alınmış kargo etiketini rapor klasörüne indirir',
        onClick: () => downloadLabels([order.id]),
      })
      : button('Kargoya ver', {
        variant: invoiced && !closedOrder ? 'primary' : '',
        disabled: closedOrder,
        title: 'Kargo Yönetimi’nde açılır: teklif, etiket ve takip no orada',
        onClick: () => openShipping(order),
      }),
    button('Durum değiştir', {
      disabled: !targets.length,
      title: targets.length
        ? 'Durumu elle değiştirir; sonraki fatura ya da gönderi bu değeri değiştirebilir'
        : `“${order.statusLabel || order.status}” son durumdur; değiştirilemez.`,
      onClick: () => changeStatus(order, targets),
    }),
    menuButton('Diğer', [
      { label: 'Sipariş fişi — PDF', onClick: () => report.run('slip', { orderId: order.id }) },
      shipped ? { label: 'Kargo Yönetimi’nde aç', onClick: () => openShipping(order) } : null,
      { label: 'Siparişi iptal et',
        disabled: Boolean(payload.cancelBlock),
        title: payload.cancelBlock || 'Geri alınamaz; gerekçe ister',
        onClick: () => cancelOrder(order) },
    ]),
  );
  const box = h('div', 'so-actionbox');
  box.append(row);
  // Toplu kargo bu siparişi ALMAZ — nedeni tek satır (ödeme belirsiz, fatura
  // yok…). Tek sipariş yine Kargo Yönetimi'nden gönderilebilir: kararı veren
  // kişi burada siparişin tamamını görüyor.
  if (order.shipBlock && !closedOrder) box.append(alertBox(order.shipBlock, 'warn'));
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
    button('Kısmi fatura kes', {
      title: 'Faturalanacak adetleri siz seçersiniz',
      onClick: () => invoiceOrder(order, rows, { partial: true }),
    }),
    // "Seçili kalemleri kargoya ver" KALDIRILDI ve yerine bir şey konmadı.
    // İki ayrı nedenle sahte bir olanaktı: (1) `shipOrder`'ın aynısını
    // çağırıyordu ve `partial` bayrağı kargoda YALNIZ pencere başlığını
    // değiştiriyordu — davranış farkı sıfırdı; (2) bu tablo `selectable`
    // almıyor, yani "Seçili" diye bir şey hiç yoktu. Kısmi kargo artık
    // Kargo Yönetimi sihirbazının işidir: orada kalan adet hesaplanır,
    // desi ölçülür ve etiket gerçekten satın alınır.
  );
  // Kalem iptali ve kısmi iade İade Yönetimi'nin işidir (para hareketi, ayrı
  // izin); burada yalnız kısmi FATURA vardır.
  pane.append(table.node, partial);
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

// `shipOrder()` KALDIRILDI. Yaptığı iş şuydu: kullanıcıya adet sorup,
// `window.prompt` ile takip numarası ve kargo firması isteyip, gerekçe alıp
// `POST /orders/{id}/ship` çağırmak.
//
// ÜÇ AYRI NEDENLE YANLIŞTI:
//  1. TAKİP NUMARASI ZİNCİRİN ÇIKTISIDIR, GİRDİSİ DEĞİL. Kullanıcıya sormak,
//     taşıyıcının üreteceği numarayı elle uydurmasını istemekti; alan çoğu
//     zaman boş bırakılıyordu.
//  2. GÖVDE MAĞAZANIN SÖZLEŞMESİNE UYMUYORDU (`{"shipment": {…}}` sarmalı);
//     canlı işlemci `source` ve `items`ı gövdenin KÖKÜNDEN okuduğu için ikisi
//     de boş kalıyor ve istek reddediliyordu.
//  3. PAKET YOLA ÇIKMIYORDU. Açılan şey Bagisto'nun kendi gönderi kaydıydı;
//     etiket satın alınmıyor, taşıyıcıya hiç gidilmiyordu — ama ekran
//     "gönderi kaydedildi" diyordu.
//
// Kargoya vermenin tek evi Kargo Yönetimi'dir.

async function cancelOrder(order) {
  const reason = await askReason({
    title: 'Siparişi iptal et',
    description: `${order.orderNo} · ${money(order.grandTotal)}. Geri alınamaz; tahsil `
      + 'edilmiş para için İade Yönetimi’nden iade açın.',
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

/**
 * Durumu elle çekme — TEK PENCERE, iki alan.
 *
 * Hedef listesi SUNUCUDAN gelir; burada matris kopyalanmaz. Gerekçe zorunlu
 * (uçta da en az 10 karakter) çünkü bu, mağazanın kendi türetmesinin üstüne
 * konan bir istisnadır ve iki ay sonra "bu sipariş neden elle kapatılmış"
 * sorusunun tek cevabı o metin olacak.
 */
async function changeStatus(order, targets) {
  if (!targets.length) return;

  const secim = await new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    const select = h('select', 'kit-input');
    for (const target of targets) {
      const option = h('option', undefined, target.label);
      option.value = target.value;
      select.append(option);
    }

    const close = (value) => { overlay.remove(); resolve(value); };
    card.append(
      h('h3', 'kit-dialog-title', `Durum değiştir — ${order.orderNo}`),
      h('p', 'kit-dialog-text',
        `Şu an: ${order.statusLabel || order.status}. Sonraki fatura ya da gönderi `
        + 'bu değeri değiştirebilir.'),
      select,
      h('div', 'kit-dialog-actions'),
    );
    card.lastChild.append(
      button('Vazgeç', { onClick: () => close(null) }),
      button('Devam', { variant: 'primary', onClick: () => close(select.value) }),
    );
    overlay.append(card);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close(null);
    });
    document.body.append(overlay);
    select.focus();
  });
  if (!secim) return;

  const hedef = targets.find((item) => item.value === secim);
  const reason = await askReason({
    title: `Durumu “${hedef?.label || secim}” yap`,
    description: `${order.orderNo} · ${order.statusLabel || order.status} → `
      + `${hedef?.label || secim}. Gerekçe işlem geçmişine yazılır.`,
    confirmLabel: 'Durumu değiştir',
  });
  if (!reason) return;

  await withBusy('Durum değiştiriliyor…', async () => {
    const result = await call(`${BASE}/orders/${order.id}/status`, {
      method: 'POST', body: { status: secim, reason, dryRun: false },
    });
    toast(result.dryRun
      ? 'Kuru prova: istek gönderilmedi.'
      : `${result.orderNo} → ${result.statusLabel || secim}`,
    result.dryRun ? 'warn' : 'good');
    // EZİLEBİLİRLİK SESSİZ GEÇİLMEZ: kullanıcı "kaydettim ama geri döndü"
    // ile karşılaşmadan önce nedenini duymalı. Kararı SUNUCU veriyor —
    // koşullu bir cevap ve burada tahmin etmek yanlış söz vermek olurdu.
    if (!result.dryRun && result.mayBeOverwritten) {
      toast('Bu durum, sonraki bir fatura ya da gönderi kaydında yeniden '
        + 'hesaplanabilir.', 'warn');
    }
    // YAN ETKİLER SUNUCUNUN CÜMLELERİDİR: stok, müşteri bildirimi, "kargoya
    // verilmedi" alarmı. Kendi özetimizi yazmak, sunucu kuralı değiştiğinde
    // ekranı sessizce yanlış yapardı.
    for (const line of result.sideEffects || []) toast(line, 'info');
    refresh();
  });
}

// -------------------------------------------------------------- ödeme

function paintPayment(pane, { payload }) {
  const order = payload.order;
  const evidence = order.paymentEvidence || {};
  const facts = h('div', 'so-facts');
  for (const [label, value] of [
    ['Yöntem', order.paymentMethod || '—'],
    ['Durum', order.paymentLabel],
    ['Bilgi kaynağı', order.paymentSource || '—'],
    ['Fatura kaydı', evidence.invoiceCount
      ? `${num(evidence.invoiceCount)} kayıt · ${(evidence.invoiceStates || []).join(', ')}`
      : 'yok'],
    ['POS denemesi', evidence.attemptCount
      ? `${num(evidence.attemptCount)} kayıt · ${(evidence.attemptStates || []).join(', ')}`
      : 'yok'],
    ['Faturalanan', money(payload.money.invoiced)],
    ['İade edilen', money(payload.money.refunded)],
    ['Kupon', order.coupon || '—'],
  ]) {
    const line = h('div', 'so-fact');
    line.append(h('span', 'so-sub', label), h('b', undefined, String(value)));
    facts.append(line);
  }
  // Ödeme durumunun gerekçesi (hangi kayıttan, neden) kartın ipucundadır;
  // ekranda yalnız eyleme dönen tek satır durur.
  const paymentCard = card('Ödeme', facts);
  if (order.paymentNote) paymentCard.title = order.paymentNote;
  pane.append(paymentCard);
  if (order.paymentAttention) {
    const warn = alertBox('Ödeme belirsiz — kargolamadan önce bankadan teyit edin.', 'warn');
    warn.title = order.paymentNote || '';
    pane.append(warn);
  }

  if (!payload.transactions.length) {
    pane.append(emptyState({
      title: 'Kart işlemi yok',
      text: 'Havale ya da kapıda ödemede bu normaldir.',
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
    pane.replaceChildren(alertBox(error.message || 'Bilgi alınamadı.', 'bad'));
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
      // METİN BAYATLAMIŞTI: "Özet sekmesinden 'Kargoya ver'" diyordu ve o
      // düğme bu ekrandan kaldırılmıştı (gövde sarmalı yüzünden paket yola
      // çıkmadan müşteriye SMS tetikliyordu). Var olmayan bir düğmeyi tarif
      // eden boş durum, kullanıcıyı ekranda olmayan bir şeyi aramaya yollar.
      text: 'Henüz kargoya verilmedi — üstteki “Kargoya ver” ile başlatın.',
    },
  });
}

function paintInvoice(pane, { payload, invoiceCap }) {
  paintCapability(pane, invoiceCap, {
    orderId: payload.order.id,
    empty: {
      title: 'Fatura yok',
      text: 'Faturası kesilmemiş — üstteki “Fatura kes” ile kesin.',
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

  // Bu kutu bir dönem "o uç henüz yayında değil" diyordu; uç 2026-08-16
  // itibarıyla CANLIDA ÇALIŞIYOR ve gerçek talepleri döndürüyor. Metin artık
  // yayın durumu hakkında hüküm vermiyor: dal ayakta (K7 — uç geri çekilirse
  // künye çökmemeli) ama kullanıcıya "yayınlanmadı" diye yanlış bir gerekçe
  // göstermiyor. Gerçek sebep künyenin uyarı listesinde zaten yazılı.
  if (!payload.returnsAvailable) {
    pane.append(alertBox('İade talepleri şu an okunamadı.', 'warn'));
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
  // Para iadesi bu ekrandan YAPILMAZ: tutar hesabı ve para hareketi İade
  // Yönetimi'nin işidir, ayrı izin ister.
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
        hint: 'Açıksa müşteriye e-posta gider (geri alınamaz).' },
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
  pane.append(card('İşlem geçmişi', local, 'gerekçeleriyle'));

  // Mağazanın KENDİ denetim kaydı (UDİT) ayrı bir modülün yeteneğidir. Yoksa
  // bu sekme yine çalışır: bizim yerel izimiz her koşulda buradadır.
  if (auditCap) {
    const remote = h('div');
    pane.append(card('Mağaza kaydı', remote));
    paintCapability(remote, auditCap, {
      orderId,
      empty: { title: 'Mağaza kaydı yok', text: 'Mağaza bu sipariş için kayıt tutmamış.' },
    });
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
      title: 'Henüz işlem yok',
      text: 'Bu ekrandan yapılan her işlem gerekçesiyle burada görünür.',
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

/**
 * Toplu gönderinin TAŞIYICISI.
 *
 * Liste `store.api` üzerinden Kargo yeteneğinden gelir; gelmezse serbest metin
 * kalır. Boş bırakılabilir: Bagisto gönderi kaydı taşıyıcısız da açılır ve
 * taşıyıcı sonradan Kargo Yönetimi'nden atanır. TAKİP NUMARASI burada YOKTUR —
 * her gönderinin numarası ayrıdır, tek numara hepsini aynı pakete yazmak olurdu.
 */
function carrierPicker() {
  const box = h('div', 'so-carrier');
  const list = state.reference.carriers || [];
  const select = h('select', 'kit-input');
  const choices = [{ value: '', label: 'Taşıyıcı seçilmedi' },
    ...list.map((entry) => ({ value: entry.name || entry.code,
      label: entry.name || entry.code }))];
  for (const choice of choices) {
    const option = h('option', undefined, choice.label);
    option.value = choice.value;
    select.append(option);
  }
  const free = h('input', 'kit-input');
  free.type = 'text';
  free.maxLength = 64;
  free.placeholder = list.length ? 'ya da elle yazın' : 'taşıyıcı adı (liste gelmedi)';

  box.append(h('span', 'so-sub', 'Taşıyıcı'), select, free);
  if (!list.length) {
    box.append(h('span', 'so-sub',
      'Taşıyıcı listesi mağazadan gelmedi; ad elle yazılabilir.'));
  }
  return { node: box, value: () => (free.value.trim() || select.value || '') };
}

function batchDialog(kind, orderIds) {
  const ids = (orderIds || state.selection).map(Number).filter(Boolean);
  const overlay = h('div', 'kit-overlay');
  const dialog = h('div', 'kit-dialog so-batch');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');

  const title = kind === 'ship' ? 'Toplu kargoya verme' : 'Toplu fatura kesme';
  dialog.append(h('h3', 'kit-dialog-title', title));
  // Sıra: önizleme (kim atlanacak, neden) → KURU PROVA (mağazaya hiçbir şey
  // yazılmaz) → gerekçeli tek onay. Kuru prova görülmeden gerçek işlem
  // yapılmaz; kural sunucuda da uygulanır (K9).
  dialog.append(h('p', 'kit-dialog-text',
    `${num(ids.length)} sipariş seçili. Önce önizleme, sonra kuru prova, sonra onay.`));

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const carrier = kind === 'ship' ? carrierPicker() : null;
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
        method: 'POST', body: { kind, orderIds: ids },
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
      { label: 'Adet', value: num(summary.quantity) },
      { label: 'Tutar', value: money(summary.amount) },
    ]));
    result.append(previewTable(preview.rows));

    if (preview.missing && preview.missing.length) {
      result.append(alertBox(
        `${preview.missing.length} sipariş okunamadı ve önizlemeye girmedi.`, 'warn'));
    }
    if (kind === 'ship') {
      // Desi uydurulmaz: hesabı ürün ölçülerine dayanır ve sahibi Kargo
      // Yönetimi ekranıdır; sipariş gövdesi o ölçüyü taşımaz.
      result.append(card('Gönderi bilgisi', carrier.node,
        'taşıyıcı onayda değiştirilemez'));
    }

    actions.replaceChildren(
      button('Vazgeç', { onClick: close }),
      button(`Kuru prova (${num(summary.ready)} sipariş)`, {
        variant: 'primary',
        disabled: summary.ready === 0,
        title: 'Mağazaya hiçbir şey yazılmaz; ne olacağı satır satır gösterilir.',
        onClick: () => run(preview, summary, { dryRun: true }),
      }),
    );
  }

  function previewTable(rows) {
    return dataTable({
      columns: [
        { key: 'orderNo', label: 'Sipariş', width: '130px', className: 'mono' },
        { key: 'customer', label: 'Müşteri', width: 'minmax(0, 1.4fr)' },
        { key: 'statusLabel', label: 'Durum', width: '110px' },
        { key: 'paymentLabel', label: 'Ödeme', width: '150px', className: 'wrap' },
        { key: 'quantity', label: 'Adet', width: '70px', align: 'num' },
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
      rows,
      dense: true,
      rowKey: (row) => String(row.id),
    }).node;
  }

  function resultTable(rows) {
    return dataTable({
      columns: [
        { key: 'orderNo', label: 'Sipariş', width: '130px', className: 'mono' },
        { key: 'customer', label: 'Müşteri', width: 'minmax(0, 1.2fr)' },
        { key: 'ok', label: 'Sonuç', width: '110px',
          cell: (row) => badge(row.ok ? 'Tamam' : 'Hata', row.ok ? 'good' : 'bad') },
        // MÜŞTERİ SMS'İ SATIR SATIR. "Yedi sipariş kargolandı" demek yetmez:
        // hangisinin müşterisine haber verilemediği ve NEDEN verilemediği
        // (numara yok, takip kodu yok) aynı tabloda görünmeli.
        { key: 'smsSent', label: 'Müşteri SMS’i', width: '130px',
          cell: (row) => (row.smsNote
            ? badge(row.smsSent ? 'Gitti' : 'Gitmedi', row.smsSent ? 'good' : 'warn')
            : '—') },
        { key: 'error', label: 'Açıklama', width: 'minmax(0, 2fr)', className: 'wrap',
          cell: (row) => [row.error, row.smsNote].filter(Boolean).join(' · ') || '—' },
      ],
      rows,
      dense: true,
      rowKey: (row) => String(row.id),
    }).node;
  }

  /** Kuru prova ve gerçek uygulama AYNI uçtan geçer; fark tek bayrakta. */
  async function run(preview, summary, { dryRun }) {
    const chosen = carrier ? carrier.value() : '';
    let reason = 'Toplu islem kuru provasi (yazma yok)';
    if (!dryRun) {
      reason = await askReason({
        title,
        description: `${num(summary.ready)} siparişe uygulanacak · toplam `
          + `${num(summary.quantity)} adet · ${money(summary.amount)}`
          + (kind === 'ship'
            ? ` · taşıyıcı: ${chosen || 'seçilmedi (gönderi kaydı taşıyıcısız açılır)'}`
            : '')
          + '. Kuru provada gördüğünüz liste neyse O uygulanır; yeniden hesaplanmaz. '
          + 'Sıra sıra yazılır ve YİNELENMEZ.',
        confirmLabel: kind === 'ship' ? 'Kargoya ver' : 'Fatura kes',
      });
      if (!reason) return;
    }

    await withBusy(dryRun ? 'Kuru prova çalışıyor…' : 'Uygulanıyor…', async () => {
      const outcome = await api(`${BASE}/batch/apply`, {
        method: 'POST',
        body: { token: preview.token, reason, dryRun, carrier: chosen },
      });
      if (outcome.ok === false && outcome.error) {
        result.append(alertBox(outcome.error, 'bad'));
        toast(outcome.error, 'bad');
        return;
      }
      paintOutcome(preview, summary, outcome, { dryRun, chosen });
    });
  }

  function paintOutcome(preview, summary, outcome, { dryRun, chosen }) {
    const failed = outcome.failed || 0;
    const applied = outcome.applied || 0;
    // KISMİ BAŞARI GİZLENMEZ: başaranlar da başaramayanlar da tek tabloda.
    result.replaceChildren(kpiRow([
      { label: dryRun ? 'Provada başarılı' : 'İşlendi', value: num(applied), tone: 'good' },
      { label: 'Başarısız', value: num(failed), tone: failed ? 'bad' : 'muted' },
      { label: 'Taşıyıcı', value: chosen || '—' },
      { label: 'Tutar', value: money(summary.amount) },
    ]));
    result.append(resultTable(outcome.results || []));

    if (dryRun) {
      result.append(alertBox(
        'Kuru prova: henüz hiçbir şey yazılmadı. Onaylarsanız aynı liste uygulanır'
        + `${failed ? '; başarısız satırlar atlanır' : ''}.`, 'info'));
      actions.replaceChildren(
        button('Vazgeç', { onClick: close }),
        button(`${num(applied)} siparişe gerçekten uygula`, {
          variant: 'danger',
          disabled: applied === 0,
          onClick: () => run(preview, summary, { dryRun: false }),
        }),
      );
      return;
    }

    toast(`${num(applied)} sipariş işlendi`
      + (failed ? ` · ${num(failed)} başarısız (nedenleri listede)` : ''),
    failed ? 'warn' : 'good');
    if (failed) {
      // Başarılılar yeniden denenmemeli: ikinci gönderi ikinci kargo demektir.
      result.append(alertBox(
        `${num(failed)} sipariş işlenemedi — yalnız onları seçip yeniden deneyin.`, 'warn'));
    }
    actions.replaceChildren(button('Kapat', { onClick: close }));
    nodes.table?.clearSelection();
    state.selection = [];
    refresh();
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
        hint: '0 = sınırsız. Bundan eski sipariş bu ekrandan iptal edilemez.' },
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
    card('Müşteri aşama SMS\'i', await stageSmsBox(), 'metin Bildirimler ekranında'),
    // Mağaza ayarları buradan YAZILMAZ: anahtar adları Bagisto sürümüne göre
    // değişiyor ve bulunmayan anahtara yazmak `core_config` içinde etkisiz bir
    // satır açar — kullanıcı ayarı değiştirdiğini sanır.
    card('Mağazanın sipariş ayarları', storeBox, 'salt okunur'),
  );
}

// ======================================================= müşteri aşama SMS'i
//
// METİN BURADA DÜZENLENMEZ. Şablon, tek segment kuralı, üç katmanlı fren ve
// "aynı siparişe ikinci SMS gitmez" kaydı Bildirimler ekranındadır. Bu kutu
// yalnız TETİKLEYİCİYİ gösterir: hangi aşamalar açık, tarama penceresi kaç gün
// ve elle tarama.

const SWEEP_LABELS = { order_placed: 'Yeni siparişler', delivered: 'Teslim edilenler' };

async function stageSmsBox() {
  const box = h('div');
  let state_;
  try {
    state_ = await call(`${BASE}/stage-sms`);
  } catch (error) {
    box.append(alertBox(error.message, 'warn'));
    return box;
  }

  if (!state_.available) {
    // SESSİZ GEÇİLMEZ: müşteriye neden haber gitmediği ekranda yazar.
    box.append(alertBox(state_.error || 'Müşteri SMS\'i kapalı.', 'warn'));
    return box;
  }

  const open = state_.enabled || [];
  box.append(h('div', 'so-sub', open.length
    ? `Açık aşamalar: ${open.map((key) => SWEEP_LABELS[key] || key).join(', ')}`
    : 'Hiçbir aşama açık değil. Aşamalar Bildirimler → Müşteri SMS’i sekmesinden açılır.'));
  // Gün penceresi olmadan ilk kontrol tüm geçmişi "yeni" sayardı.
  box.append(h('div', 'so-sub', state_.lookbackDays
    ? `“Siparişiniz alındı” SMS'i son ${state_.lookbackDays} günün siparişlerine gider.`
    : '“Siparişiniz alındı” SMS’inde gün sınırı yok.'));
  if (state_.dryRun) {
    // Üç frenin bu ekrandaki katmanı: `stage_sms_dry_run` modül ayarı.
    const warn = alertBox('Deneme kipi açık: bu ekrandan gerçek SMS çıkmaz.', 'warn');
    warn.title = 'Modül ayarı stage_sms_dry_run. Diğer iki fren Bildirimler ve platform '
      + 'ayarındadır.';
    box.append(warn);
  }

  const note = h('div', 'so-sub', '');
  const actions = h('div', 'so-actions');
  for (const stage of ['order_placed', 'delivered']) {
    actions.append(button(`${SWEEP_LABELS[stage]}: SMS'leri şimdi gönder`, {
      title: 'Aynı işi on dakikada bir otomatik görev de yapar; aynı siparişe ikinci '
        + 'SMS gitmez.',
      onClick: () => runSweep(stage, note),
    }));
  }
  box.append(actions, note);
  return box;
}

async function runSweep(stage, note) {
  note.textContent = 'Gönderiliyor…';
  note.classList.remove('bad');
  const reason = await askReason({
    title: `${SWEEP_LABELS[stage]}: SMS gönder`,
    description: 'Açık aşamalar için henüz haber verilmemiş müşterilere SMS gider.',
    confirmLabel: 'Gönder',
  });
  if (!reason) {
    note.textContent = '';
    return;
  }
  await withBusy('SMS\'ler gönderiliyor…', async () => {
    // `dryRun: false` = YALNIZ bu ekranın freni açılır. Modül ayarı ve platform
    // ayarı kapanmadan tek bir gerçek SMS çıkmaz; sonuç satırı ne olduğunu yazar.
    const result = await call(`${BASE}/stage-sms/sweep`, {
      method: 'POST', body: { stage, dryRun: false },
    });
    const failed = (result.results || []).filter((row) => !row.sent);
    note.textContent = result.skipped
      ? result.note
      : `${result.note} · denenen ${result.tried ?? 0}, gönderilen ${result.sent ?? 0}`
        + (failed.length ? ` · gönderilemeyen ${failed.length}` : '');
    note.classList.toggle('bad', Boolean(result.skipped) || failed.length > 0);
    if (failed.length) {
      // GÖNDERİLEMEYENLER SAYI OLARAK GEÇİŞTİRİLMEZ: hangi sipariş, neden.
      toast(failed.map((row) => `${row.orderNo || row.orderId}: ${row.note}`).join(' · '),
        'warn');
    }
    if (result.truncated) toast('Kalanlar bir sonraki turda gönderilecek.', 'info');
  });
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

/** Koyu temada kâğıt da koyulaşır: ekran yalnız kitin jetonlarını kullanır (ADR 0032, 63-duzeltici). */
export const darkReady = true;

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  capability = ctx.capability;
  openPanel = ctx.open;

  const view = h('div', 'kit-panel so');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar([
    { key: 'list', label: 'Siparişler' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'list', (key) => showView(key));

  // SADE KİP (docs/tasarim-sade-magaza.md §1-2): görünür süzgeç yalnız arama ve
  // tarih; Durum (çipler zaten durumu söylüyor), Ödeme, Grup, Tarih alanı,
  // Tutar, Ödeme yöntemi, Kargo firması, Şehir, Kupon ve beş anahtar
  // "Filtreler" arkasında katlanır — dolu olanı düğme kendisi sayar. Tek
  // seçenekli Kanal/Grup kutusu hiç çizilmez (`loadReference`).
  nodes.filters = filterBar({
    compact: true,
    fields: [
      { kind: 'search', key: 'q', width: '260px', primary: true,
        placeholder: 'Sipariş no, müşteri, e-posta, takip no' },
      // Varsayılan aralık BOŞ: hazır bir "son 7 gün" süzgeci, ekranı ilk açan
      // kişiden eski siparişleri sessizce gizlerdi. Üç hazır aralık yeter.
      { kind: 'dateRange', key: 'range', label: 'Tarih', start: '', end: '', primary: true,
        presets: RANGE_PRESETS.filter((preset) => ['today', 'week', 'month']
          .includes(preset.key)) },
      { kind: 'select', key: 'status', label: 'Durum',
        options: [{ value: '', label: 'Tümü — durum' }] },
      { kind: 'select', key: 'paymentState', label: 'Ödeme',
        options: [{ value: '', label: 'Tümü — ödeme' }] },
      { kind: 'select', key: 'channel', label: 'Kanal', hidden: true,
        options: [{ value: '', label: 'Tümü — kanal' }] },
      { kind: 'select', key: 'customerGroup', label: 'Grup', hidden: true,
        options: [{ value: '', label: 'Tümü — müşteri grubu' }] },
      { kind: 'select', key: 'dateField', label: 'Tarih alanı',
        options: [{ value: 'created', label: 'Sipariş tarihi' }] },
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
      button('Yenile', {
        variant: 'ghost',
        title: 'Listeyi ve sayaçları mağazadan yeniden okur',
        onClick: () => refresh({ fresh: true }),
      }),
      menuButton('Dışa aktar', [
        { label: 'Görünen — Excel',
          title: 'Ekrandaki sayfayı Excel ile açılan CSV olarak indirir',
          onClick: exportVisible },
        { label: 'Tümü — Excel',
          title: 'Süzgece uyan bütün siparişleri rapor klasörüne yazar',
          onClick: exportAll },
        { label: 'Liste raporu — PDF',
          title: 'Süzgece uyan siparişlerin yazdırılabilir listesi',
          onClick: () => report.run('list', { filters: currentFilters() }) },
      ], { title: 'Excel ya da yazdırılabilir PDF olarak indir' }),
    ],
  });

  nodes.chips = chipRow(CHIPS, null, (key) => applyChip(key));
  nodes.chips.counts({});        // sayaç gelene kadar BOŞ — "0" uydurulmaz
  nodes.status = statusLine();
  nodes.totals = h('div', 'so-totals');
  nodes.selbar = h('div', 'so-selbar');
  nodes.tableWrap = h('div', 'so-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size, paging: true }),
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

  // DERİN BAĞLANTI. Kabuk `ctx.payload`'ı geçiriyor ama bu panel okumuyordu:
  // Kargo ekranındaki "Siparişler'de aç" düğmesi listeyi açıyor, ARANAN
  // siparişi açmıyordu ve kullanıcı aramayı baştan yapıyordu. Süzgeç alanı
  // sipariş no, müşteri, e-posta ve takip no üzerinde çalıştığı için hangi
  // künye gelirse gelsin tek alan yeter.
  const hedef = ctx.payload || {};
  const anahtar = String(hedef.orderNumber || hedef.trackingNo || hedef.orderId || '').trim();
  if (anahtar) nodes.filters.set('q', anahtar);

  // Referans listeler ÖNCE gelir: süzgeç açılırları dolmadan liste çekmek,
  // kullanıcının seçtiği kanalı kaybettiriyordu.
  loadReference().then(() => refresh());

  return () => {
    if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
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
