// Kargo paneli — kargoya hazır siparişten teslim edilmiş gönderiye kadar tek ekran.
//
// NE YAPAR: ÜÇ sekme (docs/tasarim-sade-magaza.md §4).
//  · `Kargoya hazır` ödemesi alınmış ama kargolanmamış siparişleri listeler.
//    Satırda «Kargoya ver» (+ gerekirse «📏 Ölçü») durur; seçim yapılırsa üst
//    şeritte TOPLU kargoya verme çıkar; sihirbaz ÜÇ BÜYÜK DÜĞMEDİR (kargoya ver
//    · etiketim var · test) ve ölçü formu katlanmış durur.
//  · `Gönderiler` takip, hareket geçmişi, toplu etiket PDF'i, teslimat
//    manifestosu ve senkron. `Performans` (teslim süresi, kargo kâr/zararı)
//    günlük iş değildir; bu sekmenin İKİNCİL görünümüdür ("📊 Performans").
//  · `Ayarlar` ayda bir açılan dört bölüm: taşıyıcılar (+ ekran tercihleri),
//    ücretlendirme (desi matrisi), bölgeler ve Geliver kurulumu.
//
// SADE GÖRÜNÜM. Varsayılan görünümde bilgi kutusu yoktur; "mağaza şu süzgeci
// sunmuyor", "sayfa içinde" gibi iç ayrıntılar bu dosyanın yorumlarında durur.
// Kalan uyarılar yalnız eyleme dönüşenlerdir ve tek satırdır.
//
// «KARGOYA VER» — İKİ TIK: düğme + tek onay penceresi. Onaydan sonra gönderi
// açılır, teklif alınır, MÜŞTERİNİN ödediği firmadan etiket SATIN ALINIR,
// takip numarası siparişe yazılır ve iki belge (KARGO ETİKETİ + FATURA) bu
// cihazın yazıcısına gider. Onay penceresi ölçünün NEREDEN geldiğini (girilen
// ölçü ya da siparişin mağazadaki desi kaydı), faturalanan birimi ve desi
// tarifesinden TAHMİNİ tutarı söyler; kesin tutarı Geliver belirler ve sonuç
// çekmecesinde yazar. Gerekçe boşsa otomatik yazılır.
//
// Satırdaki düğme ÖLÇÜ GÖNDERMEZ: mağaza siparişin kendi desisini (checkout'ta
// müşteriye uygulanan) kullanır. Sipariş LİSTESİ ucu kalem ölçüsü vermediği
// için panelin kendi tahmini çoğu satırda boştur; bu bir engel DEĞİLDİR ve
// yanında «📏 Ölçü» düğmesi koliyi ölçüp girmeyi tek tıka indirir.
//
// NE YAPMAZ:
//  · ETİKET ÇİZMEZ. Barkod taşıyıcının kendi numaralandırmasıdır; kendi
//    çizdiğimiz barkod şubede okunmazsa gönderi elde kalır. Etiketin kendisi
//    her zaman taşıyıcıdan gelir, biz yalnız kâğıda yerleştiririz.
//  · ZİNCİRİN ÜRETTİĞİ TAKİP NUMARASINI SORMAZ. "Kargoya ver" sonrası ekranda
//    görünen numara siparişe YAZILMIŞ olandır; kullanıcıya yazdırılsaydı
//    tahmin olurdu. TEK İSTİSNA `🏷️ Etiketim var`: orada zincir hiç çalışmadı,
//    numara kullanıcının elindeki etiketin üstünde yazıyor ve tek kaynağı odur.
//  · GERÇEK ETİKET SATIN ALIMINDAN ÖNCE AÇIK ÖZET ONAYI ALIR.
//  · SİHİRBAZDA TASLAK AÇARKEN PARA HARCAMAZ. "Taslak aç" ile "Etiketi satın
//    al" orada bilerek iki ayrı adımdır: ilki ücretsiz ve düzeltilebilir.
//    İkisi de katlanır `Ayrıntı` bölmesindedir; günlük iş oraya girmez.
//  · TESLİM FİŞİNİ OTOMATİK BASMAZ. Kullanıcı "fiş yok" dedi; düğmesi
//    sihirbazda duruyor ve elle basılıyor.
//  · GÖNDERİ SİLMEZ. İptal ve iade vardır; ikisi de gerekçe ister.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Desi ≠ ağırlık. Faturalanan, ikisinin büyüğünün tavana yuvarlanmışıdır;
//    ekran hem ham ölçüyü hem faturalanacak desiyi gösterir.
//  · Ücret dökümü TAHMİNDİR; kesin tutar taşıyıcı teklifinden gelir. İkisi
//    ayrı gösterilir, tahmin gerçek fiyat gibi sunulmaz.
//  · `Geciken` / `Adres sorunu` / `Tahsil edilmemiş` süzgeçleri SAYFA İÇİNDE
//    çalışır (taşıyıcıda böyle bir alan yok); ekran bunu yazar.
//  · Gönderiler sekmesinde arama, taşıyıcı ve durum da SAYFA İÇİNDE çalışır:
//    mağazanın gönderi ucu yalnız sipariş süzgecini okuyor. Şehir, tarih,
//    ödeme tipi ve desi süzgeçleri bu yüzden KALDIRILDI — mağaza onları
//    sessizce yok sayıyordu ve ekran süzülmüş sanılan tam listeyi gösteriyordu.
//  · Bölge eşlemesi YEREL: ücret dökümünü ve uyarıyı etkiler, vitrini değil.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_shipping/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_shipping/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, copyText, csvBlob, h, loadStyles,
  menuButton, money, num, percent, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { barChart, groupedBar, stackedBar } from '../../ui-kit/charts.js';
import { measureBar, stepper, timeline } from '../../ui-kit/flow.js';
import { reportChain } from '../../ui-kit/report.js';
// ETİKET VE FATURA CİHAZDA BASILIR — gerekçe `ui-kit/printing.js` başlığında.
import { printDocument, printerReady } from '../../ui-kit/printing.js';

const BASE = '/api/store_shipping';

const STATUS_LABELS = {
  created: 'Etiket oluşturuldu',
  picked_up: 'Teslim alındı',
  in_transit: 'Yolda',
  at_branch: 'Şubede',
  out_for_delivery: 'Dağıtımda',
  delivered: 'Teslim edildi',
  undelivered: 'Teslim edilemedi',
  returning: 'İade dönüyor',
  returned: 'İade teslim',
  cancelled: 'İptal',
};

const STATUS_TONES = {
  created: 'info', picked_up: 'info', in_transit: 'info', at_branch: 'info',
  out_for_delivery: 'warn', delivered: 'good', undelivered: 'bad',
  returning: 'warn', returned: 'dim', cancelled: 'dim',
};

const FLAG_LABELS = {
  late: 'Geciken', address: 'Adres sorunu', cod: 'Tahsil edilmemiş',
  unknown: 'Bilinmeyen durum',
};

const FLAG_CHIPS = [
  { key: 'late', label: 'Geciken' },
  { key: 'address', label: 'Adres sorunu' },
  { key: 'cod', label: 'Kapıda ödeme tahsil edilmemiş' },
];

const PAYERS = [
  { value: 'sender', label: 'Gönderici öder' },
  { value: 'receiver', label: 'Alıcı öder (kapıda)' },
];

// EN ÇOK ÜÇ SEKME (tasarım §4). Eskiden yedi sekme vardı; dördü ayda bir
// açılan kurulum ekranıydı ve her gün kargo çıkaran personeli canlı mod
// anahtarının yanından geçiriyordu. Onlar artık `Ayarlar`ın alt bölümleri.
const TABS = [
  { key: 'ready', label: 'Kargoya hazır' },
  { key: 'shipments', label: 'Gönderiler' },
  { key: 'settings', label: 'Ayarlar' },
];

/** `Ayarlar` sekmesinin alt bölümleri — kullanım sıklığına göre sıralı. */
const SETTINGS_SECTIONS = [
  { key: 'carriers', label: 'Taşıyıcılar' },
  { key: 'rates', label: 'Ücretlendirme' },
  { key: 'zones', label: 'Bölgeler' },
  { key: 'geliver', label: 'Geliver' },
];

/**
 * "Kargoya ver" onay penceresinin HAZIR GEREKÇELERİ (tasarım §7).
 *
 * Gerekçe bu akışta ZORUNLU DEĞİL (boşsa sunucu `dispatch_reason` ile
 * otomatik metin yazar). Eskiden listenin üstünde kalıcı bir "Gerekçe" şeridi
 * duruyordu; şimdi isteyen onay penceresinde bir çiple ya da yazarak verir.
 */
const DISPATCH_CHIPS = [
  'Günlük kargo çıkışı',
  'Müşteri acele istedi',
  'Koli depoda ölçüldü',
];

/**
 * Gerekçe isteyen pencerelerin HAZIR GEREKÇELERİ (tasarım §7). Her biri
 * kapının alt sınırını (10; para harcayan işte 20 karakter) zaten geçer —
 * çipe basıp onaylamak, kapıyı atlatmak değil doldurmaktır.
 */
const REASON_CHIPS = {
  sync: ['Müşteri kargosunu sordu', 'Durum günlerdir değişmedi'],
  notify: ['Müşteri kargosunu sordu', 'Takip numarası iletiliyor'],
  cancel: ['Müşteri siparişten vazgeçti', 'Adres yanlış girilmiş', 'Yanlış ürün paketlendi'],
  retour: ['Müşteri ürünü iade ediyor', 'Hasarlı ürün geri alınıyor'],
  purchase: ['Taşıyıcı teklifi seçilerek etiket alındı'],
  settings: ['Ekran tercihi güncellendi'],
  carrierTest: ['Bağlantı kontrol ediliyor'],
  rates: ['Kargo firması fiyat güncelledi', 'Kampanya için ücret değişti'],
  priceList: ['Güncel fiyat listesi alınıyor'],
  zone: ['Uzak bölgeye ek ücret', 'Bu bölgeye teslimat yok'],
  geliver: ['Geliver kurulum ayarı güncellendi'],
  webhook: ['Gönderi durumu bildirimleri açılıyor'],
};

/** Sınama satırlarının tonu. `unknown` yeşile DÖNMEZ (bkz. `check_lines`). */
const CHECK_TONES = { ok: 'good', fail: 'bad', unknown: 'dim' };
const CHECK_MARKS = { ok: '✓', fail: '✕', unknown: '—' };

/**
 * Başlangıç durumu FONKSİYONDUR, sabit değil.
 *
 * Tek bir sabit nesneyi `{...EMPTY}` ile kopyalamak İÇ nesneleri paylaştırır:
 * `state.shipments.flag = 'late'` sabitin kendisini değiştirir ve panel
 * kapanıp yeniden açıldığında süzgeç üzerinde kalırdı.
 */
function freshState() {
  return {
    tab: 'ready',
    ready: { items: [], blocked: [], total: 0, shown: 0, page: 1, size: 50, pages: 0,
      connected: false, error: '', loaded: false },
    shipments: { items: [], total: 0, shown: 0, page: 1, size: 50, pages: 0, connected: false,
      error: '', totals: {}, flag: '', idleLimit: 3, loaded: false, orderId: 0,
      pageFiltered: false },
    carriers: [],
    rates: null,
    zones: [],
    settings: null,
    geliver: null,
    selection: [],
    readySelection: [],
    settingsSection: 'carriers',
  };
}

let api = null;
let toast = null;
let report = null;
let openPanel = null;
let busy = false;
let state = freshState();

const nodes = {};
const closers = [];        // cleanup'ta çağrılacak GERÇEK kaynak bırakıcılar

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

/**
 * GEREKÇE PENCERESİ — kitin `confirmWithReason`ı (hazır çipler, tasarım §7).
 *
 * `minLength: 0` notu İSTEĞE BAĞLI yapar ("Kargoya ver": boşsa sunucu otomatik
 * metin yazar) ve odak onay düğmesinde başlar. Asıl kapı backend'dedir —
 * gerekçe uzunluğu orada da doğrulanır (K9).
 *
 * @returns {Promise<{reason: string}|null>}
 */
async function reasonDialog({ title, lines = [], confirmLabel = 'Onayla', danger = true,
  chips = [], minLength = 0 }) {
  const reason = await confirmWithReason(nodes.root, {
    title,
    description: lines.filter(Boolean).join('\n'),
    confirmLabel,
    danger,
    minLength,
    presets: chips,
    placeholder: `Gerekçe (en az ${minLength} karakter) — kayda yazılır`,
  });
  return reason === null ? null : { reason };
}

/** Yıkıcı işlemin arayüz kapısı; asıl kapı backend'de (K9). Gerekçe ya da `null`. */
async function askReason({ title, description, confirmLabel = 'Onayla', minLength = 10,
  chips = [] }) {
  const answer = await reasonDialog({ title, lines: [description], confirmLabel, chips,
    minLength });
  return answer ? answer.reason : null;
}

/** PARA HARCAYAN işlemde gerekçe 20 karakter; backend de 20 istiyor. */
function askPurchaseReason(options) {
  return askReason({ ...options, minLength: 20 });
}

/**
 * "Kargoya ver" onayı — kısa özet + İSTEĞE BAĞLI not (hazır çiplerle).
 *
 * Not boş bırakılabilir; boşsa sunucu denetim defterine otomatik metin yazar
 * (`dispatch_reason`). Eskiden not listenin üstünde KALICI bir "Gerekçe"
 * şeridiydi. İKİ TIK: düğme + Enter (onay düğmesi odakta açılır).
 *
 * @returns {Promise<{reason: string}|null>}
 */
function confirmDispatch({ title, lines, confirmLabel, danger = true }) {
  return reasonDialog({ title, lines, confirmLabel, danger, chips: DISPATCH_CHIPS,
    minLength: 0 });
}

/** Takip numarası — tıklanınca panoya kopyalanır (depoda en çok yapılan iş). */
function track(row) {
  const box = h('span', 'sh-track');
  if (!row.trackingNo) {
    box.append(h('i', 'sh-sub', 'etiket yok'));
    return box;
  }
  const code = h('code', 'sh-code', row.trackingNo);
  code.title = 'Takip numarasını kopyalamak için tıklayın';
  code.addEventListener('click', async (event) => {
    // Satır tıklaması çekmeceyi açıyor; kopyalama onu tetiklememeli.
    event.stopPropagation();
    await copyText(row.trackingNo);
    toast('Takip numarası kopyalandı.', 'good');
  });
  box.append(code);
  return box;
}

function statusCell(row) {
  const box = h('span', 'sh-status');
  box.append(badge(STATUS_LABELS[row.status] || row.statusLabel,
    STATUS_TONES[row.status] || 'warn'));
  for (const flag of row.flags || []) {
    if (FLAG_LABELS[flag]) box.append(badge(FLAG_LABELS[flag], flag === 'cod' ? 'warn' : 'bad'));
  }
  return box;
}

/**
 * Ölçü karşılaştırması — kitin `measureBar`'ı (ADR 0011).
 *
 * NEDEN ÇEKMECEDE AYRI BİR KART. Künyede "Faturalanan desi 5" ile "Ölçülen
 * 2,4 desi / 5,0 kg" alt alta iki satırdı; hangisinin ücreti belirlediği
 * okunmuyordu ve personel küçük olana bakıp ücreti yanlış bekliyordu. Taşıyıcı
 * DESİ ile AĞIRLIĞIN büyüğünü alır ve tam sayıya YUKARI yuvarlar; faturalanan
 * satır bu yüzden ayrıca işaretli ve yazıyla söylenir.
 *
 * ÖLÇÜ YOKSA ÇUBUK ÇİZİLMEZ. Üçü de sıfırken `measureBar` üç boş çubuk ve üç
 * "0" gösterirdi — "ölçü sıfır" ile "taşıyıcı ölçü bildirmedi" farklı şeyler
 * ve ikincisi sıfır gibi görünmemeli.
 */
function measureView(row) {
  const desi = Number(row.desi) || 0;
  const weight = Number(row.weight) || 0;
  const units = Number(row.units) || 0;
  if (!desi && !weight && !units) {
    return emptyState({
      title: 'Ölçü bilgisi yok',
      text: 'Taşıyıcı bu gönderi için desi ve ağırlık bildirmedi.',
    });
  }
  // Desi ile kilogram farklı birimlerdir; burada YAN YANA konmaları doğrudur,
  // çünkü taşıyıcının kuralı tam olarak iki SAYIYI karşılaştırmaktır.
  return measureBar([
    { label: 'Ölçülen hacim', value: desi, text: `${num(desi, 1)} desi` },
    { label: 'Ölçülen ağırlık', value: weight, text: `${num(weight, 1)} kg` },
    { label: 'Faturalanan', value: units, text: `${num(units)} desi`, governs: true },
  ], {
    note: 'Desi ile kg’ın büyüğü, yukarı yuvarlanarak faturalanır.',
  });
}

// ==================================================================== veri

async function loadSettings() {
  try {
    state.settings = await api(`${BASE}/settings`);
  } catch {
    state.settings = null;         // ayar okunamadı; varsayılanlar iş görür (K7)
  }
}

async function loadReady({ page = state.ready.page, size = state.ready.size } = {}) {
  nodes.readyHost?.replaceChildren(skeletonRows(8, 7));
  const q = nodes.readyFilters ? (nodes.readyFilters.values().q || '') : '';
  const query = new URLSearchParams({ q, page: String(page), size: String(size) });
  let payload;
  try {
    payload = await api(`${BASE}/ready?${query}`);
  } catch (error) {
    // `blocked` de sıfırlanır: eski çağrının "bekleyen siparişler" kartı
    // bağlantı koptuktan sonra da ekranda kalırdı.
    state.ready = { ...state.ready, connected: false, error: error.message, items: [],
      blocked: [], total: 0, shown: 0, loaded: true };
    renderReady();
    return;
  }
  state.ready = { ...state.ready, ...payload, loaded: true };
  state.readySelection = [];
  renderReady();
}

/**
 * Gönderi listesinin sorgusu. MAĞAZAYA GİDEN TEK SÜZGEÇ `orderId`dir; arama,
 * taşıyıcı, durum ve işaret sunucuda ÇEKİLEN SAYFANIN içinde uygulanır.
 */
function shipmentQuery({ page, size } = {}) {
  const values = nodes.shipFilters ? nodes.shipFilters.values() : {};
  const params = new URLSearchParams();
  const put = (key, value) => {
    if (value === null || value === undefined || value === '' || value === 0) return;
    params.set(key, String(value));
  };
  put('q', values.q);
  put('carrier', values.carrier);
  put('status', values.status);
  put('flag', state.shipments.flag);
  put('orderId', state.shipments.orderId);
  put('page', page ?? state.shipments.page);
  put('size', size ?? state.shipments.size);
  return params.toString();
}

async function loadShipments({ page, size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('Gönderiler alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/shipments?${shipmentQuery({ page, size })}`);
  } catch (error) {
    state.shipments = { ...state.shipments, connected: false, error: error.message,
      items: [], total: 0, loaded: true };
    renderShipTable();
    nodes.status?.set(shipStatusText(), true);
    return;
  }
  state.shipments = { ...state.shipments, ...payload, loaded: true };
  state.selection = [];
  renderShipTable();
  nodes.pager?.update({ total: payload.total || 0, page: payload.page || 1,
    size: payload.size || 50 });
  nodes.status?.set(shipStatusText(), !payload.connected);
}

function shipStatusText() {
  const data = state.shipments;
  // "Mağazaya ulaşılamadı" DENMEZ: en sık sebep mağazanın düşmesi değil,
  // BBD kargo ucunun henüz yayında olmaması. Geçidin metni olduğu gibi
  // gösterilir, üstüne yanlış bir teşhis yazılmaz.
  if (!data.connected) return `Gönderi listesi okunamadı — ${data.error}`;
  const parts = [`Bağlı · ${num(data.total)} gönderi · sayfa ${data.page}/${Math.max(1, data.pages)}`];
  if (data.orderId) parts.push(`yalnız #${data.orderId} numaralı siparişin gönderileri`);
  if (data.pageFiltered) parts.push(`süzgeç bu sayfada ${num(data.shown)} kayıt buldu`);
  return parts.join(' · ');
}

// ================================================================ sekme 1

/**
 * Bu kurulum TEST yolunda mı (`provider: bagisto`)?
 *
 * Kurulumun kararıdır, ekranın değil: test kurulumunda «Kargoya ver» Geliver'a
 * hiç çıkmaz. Ekran bunu GİZLEMEZ — düğmenin adı "(TEST)" olur ve onay
 * penceresi söyler. Ayrı bir uyarı kutusu çizilmez (tasarım §3). Eskiden
 * sihirbazdaki "Kargo yolu" seçimi kırmızı «PARA HARCAR» düğmesini sessizce
 * test gönderisine çeviriyordu; o seçim kaldırıldı.
 */
function testInstall() {
  return state.settings?.provider === 'bagisto';
}

/**
 * "Kargoya hazır" tablosu — EN ÇOK YEDİ SÜTUN (tasarım §5).
 *
 * KALDIRILAN SÜTUNLAR ve nedenleri:
 *  · "Otomatik desi": sipariş LİSTESİ ucu kalem ölçüsü vermiyor; canlıda her
 *    satırda boştu. Ölçü eksikse satırdaki «📏 Ölçü» düğmesi bunu söyler.
 *  · "Kalan": liste ucu kargolanan adedi vermiyor (canlı doğrulandı); sütun
 *    sipariş edilen adedi "kalan" diye gösteriyordu. YALNIZ gerçek bilgi
 *    taşıdığında (kısmen kargolanmış ve adedi bilinen satır varsa) çizilir.
 *  · "Bölge": yerel eşleme; "teslimat yok" işareti müşteri satırına rozet
 *    olarak iner, gerisi sihirbazda.
 *
 * KALDIRILAN KUTULAR (tasarım §3) — bilgi burada, ekranda değil:
 *  · Mağaza "gönderisi olmayan sipariş" süzgeci sunmuyor; durum süzgeci
 *    sunucuda, kalan adet kontrolü sayfa içinde yapılıyor (`total` ≠ `shown`).
 *  · Liste ucu faturalanan/kargolanan adedi vermiyor (`verified: false`);
 *    sihirbaz siparişi tek tek okuyup yeniden denetler.
 */
function renderReady() {
  const host = nodes.readyHost;
  if (!host) return;
  host.replaceChildren();
  const data = state.ready;

  if (!data.connected) {
    host.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: data.error || 'Siparişler okunamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadReady() })],
    }));
    return;
  }

  const bar = h('div', 'sh-selbar');
  nodes.readyBar = bar;

  // "Kalan" yalnız GERÇEK bilgi taşıdığında: adedi bilinen ve kısmen
  // kargolanmış en az bir satır varsa.
  const partial = data.items.some((row) => row.shipmentKnown && Number(row.shipped) > 0);

  const table = dataTable({
    columns: [
      { key: 'orderNumber', label: 'Sipariş', width: '100px', className: 'mono' },
      {
        key: 'customer',
        label: 'Müşteri',
        width: 'minmax(0, 1.6fr)',
        cell: (row) => {
          const box = h('span', 'sh-name');
          // AD BOŞ OLABİLİR: canlıda 18 siparişin 4'ünde müşteri adı yok.
          // `displayName` bu boşluğu e-postayla doldurur.
          const yer = `${row.city} / ${row.district}`.replace(/^ \/ | \/ $/, '');
          box.append(clip(h('b'), row.displayName || row.customer || `Sipariş ${row.orderNumber}`, 34));
          const sub = h('span', 'sh-sub', yer);
          if (!row.delivers) sub.append(' ', badge('teslimat yok', 'bad'));
          if (yer || !row.delivers) box.append(sub);
          return box;
        },
      },
      // KARGO FİRMASI: sipariş listesi ucu bunu HİÇ vermiyor, `bbd/orders`
      // veriyor ve servis tek istekle eşliyor.
      { key: 'carrierTitle', label: 'Kargo firması', width: '150px',
        cell: (row) => (row.carrierTitle
          ? clip(h('span'), row.carrierTitle, 22)
          : h('span', 'sh-sub', '—')) },
      { key: 'total', label: 'Tutar', width: '104px', align: 'num',
        cell: (row) => money(row.total) },
      partial && { key: 'pending', label: 'Kalan', width: '80px', align: 'num',
        cell: (row) => `${num(row.pending)} / ${num(row.ordered)}` },
      { key: 'waitingDays', label: 'Bekliyor', width: '90px', align: 'num',
        cell: (row) => (row.waitingDays === null ? '—' : `${num(row.waitingDays)} gün`) },
      // ANA EYLEM SATIRDA (tasarım §5): «Kargoya ver» + gerekirse «📏 Ölçü».
      { key: 'dispatch', label: '', width: '220px', cell: dispatchCell },
    ].filter(Boolean),
    rows: data.items,
    selectable: true,
    // SEÇİM KİMLİĞİ SİPARİŞTİR. Satırın `id` alanı yok; kitin varsayılanı
    // (`row.id`) her satıra AYNI boş kimliği veriyordu ve tek satır seçmek
    // hepsini seçiyordu — toplu kargoya verme sayfanın tamamına yayılırdı.
    rowKey: (row) => String(row.orderId),
    empty: emptyState({
      title: 'Kargo bekleyen sipariş yok',
      actions: [button('Yenile', { onClick: () => loadReady() })],
    }),
    onSelect: (ids) => {
      state.readySelection = ids;
      renderReadyBar(table);
    },
  });
  nodes.readyTable = table;

  host.append(bar, table.node);
  if (data.blocked && data.blocked.length) host.append(blockedList(data.blocked));
  renderReadyBar(table);
}

/**
 * Kargoya verilen siparişi listeden YERELDE düşürür — listeyi yeniden çekmez.
 *
 * Eskiden her "kargoya ver"den sonra liste baştan istenirdi (iki istek); toplu
 * işte bu, sipariş başına iki istek daha demekti ve geçidin dakikalık payını
 * kargo çıkışının ortasında bitiriyordu. Sipariş mağazada artık "kargolandı";
 * yerelde düşürmek doğrudur. Yeni gelen siparişler için «Yenile» durur.
 */
function dropReadyRow(orderId) {
  const before = state.ready.items.length;
  state.ready.items = state.ready.items.filter((item) => item.orderId !== orderId);
  if (state.ready.items.length !== before) {
    state.ready.shown = Math.max(0, (Number(state.ready.shown) || 0) - 1);
  }
  // Tablo yeniden kurulur ve seçim kutuları boşalır; seçim durumu da boşalır
  // ki şerit "N sipariş seçildi" deyip boş bir seçimle iş yaptırmasın.
  state.readySelection = [];
  renderReady();
}

/** Hazır OLMAYAN siparişler — tek satırlık katlanır bölme, kart değil. */
function blockedList(rows) {
  const box = h('details', 'sh-blocked');
  box.append(h('summary', 'sh-sub', `${num(rows.length)} sipariş henüz kargoya hazır değil`));
  const list = h('div', 'sh-list');
  for (const row of rows.slice(0, 30)) {
    const line = h('div', 'sh-list-row');
    line.append(h('code', 'sh-code', row.orderNumber),
      h('span', undefined, row.customer || '—'),
      h('span', 'kit-spacer'),
      badge(row.blocked, 'dim'));
    list.append(line);
  }
  box.append(list);
  return box;
}

function renderReadyBar(table) {
  const bar = nodes.readyBar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.readySelection.length;
  if (!count) { bar.classList.remove('on'); return; }
  bar.classList.add('on');

  // PANELDE ÖLÇÜSÜ OLMAYAN SİPARİŞ DE GİDER. Eskiden yalnız ölçüsü tam
  // satırlar kuyruğa giriyordu; sipariş listesi kalem ölçüsü vermediği için
  // canlıda hiçbir satır "tam" değildi ve toplu düğme hiç çıkmıyordu. Ölçü
  // gönderilmeyen siparişte mağaza kendi desi kaydını (checkout'ta müşteriye
  // uygulanan) kullanır; onay penceresi bunu yazar.
  const rows = table.selectedRows();
  bar.append(h('b', undefined, `${num(count)} sipariş seçildi`), h('span', 'kit-spacer'));

  // EKRANIN TEK BİRİNCİL DÜĞMESİ (tasarım §2): toplu «Kargoya ver».
  if (rows.length > 1) {
    bar.append(button(`🚚 ${num(rows.length)} siparişi kargoya ver`, {
      variant: testInstall() ? 'primary' : 'danger',
      title: 'Tek onaydan sonra sırayla: her biri için etiket satın alınır, etiket ve '
        + 'fatura basılır.',
      onClick: () => dispatchQueue(rows),
    }));
  }
  bar.append(
    button(rows.length > 1 ? 'Tek tek geç' : 'Sihirbazı aç', {
      onClick: () => openWizard(rows),
    }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => { table.clearSelection(); state.readySelection = []; renderReadyBar(table); },
    }),
  );
}

/**
 * TOPLU KARGOYA VERME — TEK onay, sonra sırayla.
 *
 * ONAY BİR KEZ ALINIR: pencere sipariş sayısını ve desi tarifesinden tahmini
 * toplamı gösterir; isteğe bağlı not bütün siparişlere yazılır. Sonrasında
 * sipariş başına soru SORULMAZ — otuz siparişte otuz pencere, kargo çıkışını
 * durdururdu.
 *
 * NEDEN SIRAYLA, PARALEL DEĞİL: her gönderi taşıyıcıya çıkıyor ve geçidin hız
 * kovası dakikada 55 istek. Paralel göndermek kovayı boşaltır, ortadaki
 * siparişler 429 alır ve kullanıcı hangisinin gittiğini bilemez.
 *
 * BİR SİPARİŞİN PATLAMASI SIRAYI DURDURMAZ (K7): hata sayılır, sıra devam
 * eder ve sonunda tek çekmecede hangisinin gittiği (takip numarasıyla) ve
 * hangisinin neden gitmediği yazılır.
 *
 * Her adım kendi belgesini kendi basar (`dispatchOrder` içinde); toplu iş
 * sonunda tek seferde basmak, ilk etiketi son paketin üstüne yapıştırma
 * riskini getirirdi.
 */
async function dispatchQueue(rows) {
  if (busy) return;
  const estimate = await estimateFor(rows.map((row) => ({
    orderId: row.orderId, carrier: row.carrierCode || row.carrierKey || '',
    units: Number(row.measures?.units) || 0,
  })));
  const numbers = rows.slice(0, 8).map((row) => row.orderNumber).join(', ')
    + (rows.length > 8 ? ` … +${rows.length - 8}` : '');
  const answer = await confirmDispatch({
    title: testInstall() ? `${rows.length} TEST gönderisi aç` : `${rows.length} siparişi kargoya ver`,
    lines: [
      numbers,
      estimateLine(estimate, rows.length),
      testInstall()
        ? 'TEST kurulumu: para harcanmaz.'
        : `${rows.length} etiket satın alınır; geri alınamaz.`,
    ],
    confirmLabel: testInstall() ? 'Hepsini test et' : 'Hepsini kargoya ver',
    danger: !testInstall(),
  });
  if (!answer) return;

  const sonuclar = [];
  for (const [index, row] of rows.entries()) {
    nodes.status?.set(`${index + 1}/${rows.length} · ${row.orderNumber} kargoya veriliyor…`);
    // `dispatchOrder` kendi `withBusy`'sini kuruyor; kuyruk onun bitmesini
    // bekler ve sıradakine geçer. Onay zaten alındı, çekmece en sonda tek.
    const result = await dispatchOrder(row, null, {},
      { confirmed: true, quiet: true, reason: answer.reason });
    sonuclar.push({ row, result });
  }
  const gitti = sonuclar.filter((item) => item.result && item.result.ok !== false);
  const kaldi = sonuclar.length - gitti.length;
  toast(kaldi
    ? `${num(gitti.length)} sipariş kargoya verildi · ${num(kaldi)} tanesi olmadı`
    : `${num(gitti.length)} sipariş kargoya verildi.`,
  kaldi ? 'warn' : 'good');
  nodes.readyTable?.clearSelection();
  state.readySelection = [];
  if (nodes.readyTable) renderReadyBar(nodes.readyTable);
  showQueueResult(sonuclar);
}

/** Toplu işin sonucu — her sipariş tek satır: takip numarası ya da neden. */
function showQueueResult(sonuclar) {
  const box = drawer(nodes.root, {
    title: 'Toplu kargoya verme sonucu',
    subtitle: `${num(sonuclar.length)} sipariş`,
  });
  const list = h('div', 'sh-list');
  for (const { row, result } of sonuclar) {
    const line = h('div', 'sh-list-row');
    line.append(h('code', 'sh-code', row.orderNumber),
      h('span', undefined, row.displayName || row.customer || '—'), h('span', 'kit-spacer'));
    if (result && result.ok !== false) {
      line.append(result.trackingNo
        ? h('code', 'sh-code', result.trackingNo)
        : badge('takip no gelmedi', 'warn'));
      if (result.fee !== null && result.fee !== undefined) {
        line.append(h('span', 'sh-sub', money(result.fee)));
      }
      if ((result.documents || []).some((doc) => doc.printError)) {
        line.append(badge('yazdırılamadı', 'warn'));
      }
    } else {
      line.append(badge(result?.error || 'gönderilemedi', 'bad'));
    }
    list.append(line);
  }
  box.body.append(list);
}

// ------------------------------------------------ KARGOYA VER (tek tık)

/**
 * Satırdaki düğmeler: «Kargoya ver» ve gerekirse «📏 Ölçü».
 *
 * «Kargoya ver» HER SATIRDA çalışır: ölçü göndermez, mağaza siparişin kendi
 * desisini kullanır ve onay penceresi bunu yazar. Eskiden panelin tahmini
 * yoksa düğme "Sipariş desisi yok" deyip duruyordu — sipariş listesi kalem
 * ölçüsü vermediği için canlıda HER satırda; ve nasıl düzeltileceğini
 * söylemiyordu.
 *
 * «📏 Ölçü» panelin tahmini eksikse çıkar ve sihirbazı ÖLÇÜ ALANLARI açık,
 * imleç "Uzunluk"ta olarak açar: koliyi ölçen kişi üç sayıyı yazar,
 * «Kargoya ver»e basar, onaylar.
 *
 * TEST YOLU KAYBOLMADI, günlük yoldan çıktı: sihirbazdaki ayrı «🧪 Test
 * gönderisi» düğmesi. Koruma arayüzde değil: `store_shipping.purchase` izni
 * (K9 — düğmeyi gizlemek yetkilendirme değildir) ve denetim defteri.
 */
function dispatchCell(row) {
  const box = h('span', 'sh-dispatch');
  const go = button(testInstall() ? 'Kargoya ver (TEST)' : 'Kargoya ver', {
    variant: testInstall() ? '' : 'danger',
    title: testInstall()
      ? 'TEST kurulumu: Geliver’a çıkılmaz, para harcanmaz.'
      : 'Etiket satın alınır, takip numarası siparişe yazılır, etiket ve fatura basılır.',
    onClick: () => dispatchOrder(row, go),
  });
  box.append(go);
  if (!row.measures?.complete) {
    box.append(button('📏 Ölçü', {
      variant: 'ghost',
      title: 'Koliyi ölçüp girmek için. Girilmezse siparişin kayıtlı desisi kullanılır.',
      onClick: () => openWizard([row], 0, { measure: true }),
    }));
  }
  return box;
}

/**
 * TEST YOLU — Geliver'a HİÇ uğramaz.
 *
 * Bagisto'nun kendi gönderi kaydını açar; kalan bütün kalemler kargolanmış
 * sayıldığı için sipariş "tamamlandı"ya taşınır. Takip numarasını biz üretiriz
 * ve `TEST-` ile başlar — gören herkes (müşteri dâhil) bunun deneme olduğunu
 * anlar. Para harcanmaz, taşıyıcıya çıkılmaz. Gerekçe GÖNDERİLMEZ: sunucu
 * yolu da yazan otomatik bir metin yazar (`draft_reason`).
 */
async function testShipOrder(row, trigger) {
  const label = trigger?.textContent;
  if (trigger) { trigger.disabled = true; trigger.textContent = 'Gönderiliyor…'; }
  const result = await withBusy(`${row.orderNumber} test gönderisi açılıyor…`, () => call(
    `${BASE}/orders/${row.orderId}/shipments`, {
      method: 'POST',
      body: {
        provider: 'bagisto',
        reason: '',
        // Kuru prova DEĞİL: test yolu zaten para harcamıyor, provada bırakmak
        // "sipariş tamamlandı" beklentisini karşılamazdı.
        dryRun: false,
      },
    }));
  if (trigger) { trigger.disabled = false; trigger.textContent = label; }
  if (!result) return null;

  toast(result.ok === false
    ? `Test gönderisi açılamadı: ${result.error}`
    : `Test gönderisi açıldı · takip no ${result.trackNumber || '—'}`,
  result.ok === false ? 'bad' : 'good');
  if (result.ok !== false && !result.dryRun) dropReadyRow(row.orderId);
  return result;
}

/**
 * Gönderilecek ölçünün ÖZETİ — onay penceresi bunu tek satırda yazar.
 *
 * `extra` yalnız GÖNDERİLECEK alanları taşır (sihirbazda dokunulan ölçüler);
 * boşsa mağaza siparişin kendi desisini (checkout'ta müşteriye uygulanan)
 * kullanır ve pencere bunu öyle söyler. Panelin tahmini o durumda yalnız
 * bilgi olarak yazılır — gönderilmeyen bir sayıyı "uygulanacak ölçü" diye
 * göstermek, eski onay penceresinin yalanıydı.
 *
 * Ölçü gönderildiğinde açık taslak başka ölçüyle açılmışsa sunucu onu
 * ücretsiz iptal edip yeni ölçüyle açar (`_prepare_draft`); pencere bunu
 * anlatmaz, sonuç uyarısı söyler.
 */
function measureSummary(row, extra) {
  const length = Number(extra.length) || 0;
  const width = Number(extra.width) || 0;
  const height = Number(extra.height) || 0;
  const count = [length, width, height].filter((value) => value > 0).length;
  if (count > 0 && count < 3) {
    return { ok: false, error: 'En, boy ve yükseklik alanlarının üçünü de doldurun.' };
  }
  const divisor = Number(state.ready.divisor) || 3000;
  const physical = count === 3;
  const volume = physical ? Math.round(((length * width * height) / divisor) * 100) / 100 : 0;
  const desi = physical ? volume : Number(extra.desi) || 0;
  const weight = Number(extra.weight) || 0;
  if (!physical && desi <= 0 && weight <= 0) {
    const guess = Number(row.measures?.units) || 0;
    return { ok: true, units: guess, text: 'Desi: siparişte kayıtlı olan' };
  }
  const units = Math.max(1, Math.ceil(Math.max(desi, weight) - 1e-9));
  return {
    ok: true, units,
    text: (physical ? `Ölçü: ${length}×${width}×${height} cm` : `Desi: ${desi}`)
      + (weight ? ` · ${weight} kg` : '') + ` → ${units} desi`,
  };
}

/** Desi tarifesinden TAHMİN — tek istek, matris sunucu belleğinde. Hata akışı durdurmaz. */
async function estimateFor(items) {
  try {
    return await call(`${BASE}/dispatch/estimate`, { method: 'POST', body: { items } });
  } catch {
    return null;
  }
}

/**
 * Tahmin satırı. Desi tarifesine göredir; kesin tutarı Geliver belirler ve
 * sonuç çekmecesinde yazar. Tahmin yoksa satır HİÇ yazılmaz — "hesaplanamadı"
 * diye bir satır karar vermeye yardım etmez.
 */
function estimateLine(estimate, count) {
  if (!estimate || estimate.connected === false || !estimate.known) return '';
  const eksik = count - estimate.known;
  return `Tahmini ücret: ${money(estimate.total)}${eksik > 0 ? ` (${eksik} sipariş hariç)` : ''}`;
}

/**
 * "Kargoya ver" — İKİ TIK: düğme + tek onay penceresi.
 *
 * `extra` sihirbazdan gelen DÜZELTMELERİ taşır (yalnız dokunulan alanlar);
 * satırdan tıklandığında boştur ve sunucu müşterinin ödediği firmayı ve
 * siparişin desisini kendisi bulur. `confirmed` toplu işten gelir: onay orada
 * bir kez alındı ve notu `reason`la gelir. `quiet` sonuç çekmecesini açmaz
 * (toplu iş kendi özetini gösterir).
 */
async function dispatchOrder(row, trigger, extra = {},
                             { confirmed = false, quiet = false, reason = '' } = {}) {
  const sent = measureSummary(row, extra);
  if (!sent.ok) {
    toast(sent.error, 'warn');
    return null;
  }
  let note = reason;
  if (!confirmed) {
    const estimate = await estimateFor([{
      orderId: row.orderId, carrier: extra.carrier || row.carrierCode || row.carrierKey || '',
      units: sent.units,
    }]);
    const firma = extra.carrier
      ? `${extra.carrier} — müşterinin ödediği firma değilse mağaza reddeder`
      : (row.carrierTitle || 'siparişteki firma');
    const answer = await confirmDispatch({
      title: testInstall() ? 'TEST gönderisi aç' : 'Kargoya ver',
      lines: [
        `${row.orderNumber} · ${row.displayName || row.customer || ''} · ${firma}`,
        Number(extra.packages) > 1 ? `${Number(extra.packages)} paket` : '',
        sent.text,
        estimateLine(estimate, 1),
        testInstall()
          ? 'TEST kurulumu: para harcanmaz.'
          : 'Etiket satın alınır; geri alınamaz.',
      ],
      confirmLabel: testInstall() ? 'Test gönderisi aç' : 'Etiketi satın al',
      danger: !testInstall(),
    });
    if (!answer) return null;
    note = answer.reason;
  }

  const label = trigger?.textContent;
  if (trigger) { trigger.disabled = true; trigger.textContent = 'Gönderiliyor…'; }
  const result = await withBusy(`${row.orderNumber} kargoya veriliyor…`, async () => {
    try {
      return await call(`${BASE}/orders/${row.orderId}/dispatch`, {
        method: 'POST',
        // dryRun AÇIKÇA false: bu uçta amaç gerçekten göndermek. `provider`
        // GÖNDERİLMEZ: yol kurulumun ayarıdır (bkz. `testInstall`). Not boşsa
        // sunucu denetim defterine otomatik metin yazar.
        body: { reason: note, dryRun: false, ...extra },
      });
    } catch (error) {
      // Toplu işte hata NEDENİYLE döner: sonuç çekmecesi her siparişin neden
      // gitmediğini yazar. Tek siparişte bildirim olarak gösterilir.
      if (quiet) return { ok: false, error: error.message || 'Gönderilemedi.' };
      throw error;
    }
  });
  if (trigger) { trigger.disabled = false; trigger.textContent = label; }
  if (!result || result.ok === false) return result;

  // OTOMATİK BASIMI BURADA YAPIYORUZ. Sunucu yalnız KARAR veriyor
  // (`autoPrintPaths`); kâğıt kullanıcının masasındaki yazıcıdan çıkıyor.
  // Basım düşse bile gönderi açılmıştır ve akış durmaz (K7): satırlar
  // "Yazdırılamadı" der, dosya yolu ve "Tekrar yazdır" yerinde kalır.
  if ((result.autoPrintPaths || []).length) {
    await withBusy('Etiket ve fatura yazıcıya gönderiliyor…',
      () => autoPrintDocuments(result));
  }

  if (!quiet) {
    toast(result.dryRun
      ? 'Kuru prova: mağazaya gerçek istek gitmedi.'
      : `Kargoya verildi${result.trackingNo ? ` · ${result.trackingNo}` : ''}`,
    result.dryRun ? 'warn' : 'good');
    showDispatchResult(row, result);
  }
  if (!result.dryRun) dropReadyRow(row.orderId);
  return result;
}

/**
 * "Kargoya ver" sonrası etiketi ve faturayı BU CİHAZIN yazıcısına basar.
 *
 * Sonucu `result.documents` üzerinde işaretler; çekmece bu işaretlere bakarak
 * "Yazdırıldı" / "Yazdırılamadı" rozetlerini çiziyor. Yazıcı yoksa akış
 * DURMAZ, satırda sebep yazar ve dosya yolu görünür kalır — gönderi zaten
 * açılmıştır, kâğıt sonradan da alınır.
 */
async function autoPrintDocuments(result) {
  const paths = result.autoPrintPaths || [];
  if (!paths.length) return;

  const ready = await printerReady();
  if (!ready.ready) {
    // Sunucu "bas" dedi, cihazda yazıcı yok: sebep TEK yerde söylenir.
    result.printSkipped = result.printSkipped
      || `Belgeler basılamadı: ${ready.error}`;
    return;
  }

  const printed = [];
  for (const doc of result.documents || []) {
    if (!paths.includes(doc.path)) continue;
    try {
      await printDocument(api, doc.path, { copies: 1 });
      doc.printed = true;
      printed.push(doc.kind);
    } catch (error) {
      doc.printError = error.message || 'Yazdırılamadı.';
    }
  }
  result.printed = printed;
}

/**
 * Sonuç çekmecesi. TAKİP NUMARASI EKRANDA GÖRÜNÜR ve tıklanınca kopyalanır —
 * kimse onu elle girmez, zincirin çıktısıdır ve siparişe yazılmıştır.
 * Etiketin GERÇEK ücreti (gönderi künyesindeki tutar) da buradadır; ücret
 * okunamadıysa "0" değil "okunamadı" yazar. Her uyarı TEK SATIRDIR.
 */
function showDispatchResult(row, result) {
  const box = drawer(nodes.root, {
    title: result.dryRun ? 'Kuru prova sonucu' : 'Kargoya verildi',
    subtitle: `Sipariş ${row.orderNumber} · ${row.displayName || row.customer || ''}`.trim(),
  });

  const head = h('div', 'sh-dispatch-head');
  if (result.trackingNo) {
    const code = h('code', 'sh-code sh-track-big', result.trackingNo);
    code.title = 'Kopyalamak için tıklayın';
    code.addEventListener('click', async () => {
      await copyText(result.trackingNo);
      toast('Takip numarası kopyalandı.', 'good');
    });
    head.append(code);
  } else {
    head.append(badge('Takip numarası gelmedi — Gönderiler’den bakın', 'warn'));
  }
  if (result.purchased && !result.dryRun) {
    head.append(badge(result.fee === null || result.fee === undefined
      ? 'ücret okunamadı' : money(result.fee), result.fee === null ? 'warn' : 'dim'));
  } else if (!result.purchased) {
    head.append(badge('Etiket satın alınmadı', 'warn'));
  }
  if (result.dryRun) head.append(badge('KURU PROVA — para harcanmadı', 'warn'));
  if (result.trackingUrl) {
    head.append(button('Takip bağlantısını kopyala', {
      variant: 'ghost',
      onClick: async () => {
        await copyText(result.trackingUrl);
        toast('Bağlantı kopyalandı.', 'good');
      },
    }));
  }
  box.body.append(head);

  for (const line of result.warnings || []) box.body.append(alertBox(line, 'warn'));

  if (result.labelReady === false && result.shipmentId) {
    box.body.append(button('Etiketi yeniden al ve yazdır', {
      variant: 'primary',
      // Biçim BOŞ GEÇİLEBİLİR: ayar okunamadıysa sunucu kendi çözer
      // (`label_format_on` — tercih, yoksa ayar).
      onClick: () => report.run('labels', {
        shipmentIds: [result.shipmentId], format: state.settings?.labelFormat,
      }),
    }));
  }

  box.body.append(card('Belgeler', documentList(result)));
  if (result.printSkipped) box.body.append(h('div', 'sh-sub', result.printSkipped));
}

/** Belge satırları: basıldı mı, basılamadıysa dosya nerede, tekrar yazdır. */
function documentList(result) {
  const list = h('div', 'sh-list');
  const rows = result.documents || [];
  if (!rows.length) {
    list.append(h('div', 'sh-sub', 'Bu akışta belge üretilmedi.'));
    return list;
  }
  for (const doc of rows) {
    const line = h('div', 'sh-list-row');
    line.append(h('b', undefined, doc.label));
    if (doc.printed) line.append(badge('Yazdırıldı', 'good'));
    else if (doc.error) line.append(badge('Üretilemedi', 'bad'));
    else line.append(badge('Yazdırılmadı', 'warn'));
    line.append(h('span', 'kit-spacer'));

    if (doc.error) {
      line.append(h('span', 'sh-sub', doc.error));
    } else {
      // YAZICI YOKSA İŞ DURMAZ (K7): dosya nerede, açıkça yazılır.
      if (doc.printError) line.append(h('span', 'sh-sub', `Yazdırılamadı: ${doc.printError}`));
      line.append(h('code', 'sh-code', doc.path));
      line.append(button('Tekrar yazdır', {
        // ÇİFT BASIM KORUMASI otomatik basımı kapsar, bunu değil: kasıtlı
        // tekrar ile kazara ikinci basım ayrı şeylerdir.
        onClick: async () => {
          const printer = await withBusy('Yazıcıya gönderiliyor…',
            () => printDocument(api, doc.path, { copies: 1 }));
          if (printer) toast(`${printer} yazıcısına gönderildi.`, 'good');
        },
      }));
    }
    list.append(line);
  }
  return list;
}

// ------------------------------------------------------------- sihirbaz
//
// İKİ YOL, ÜÇ DÜĞME, TEK EKRAN. Sihirbaz eskiden dört kartlı bir formdu:
// ölçü ve taşıyıcı (sekiz alan) → ücret dökümü → "Taslak oluştur" (gerekçe
// penceresi) → teklif listesi → "Etiketi satın al" (yirmi karakterlik ikinci
// gerekçe penceresi). Kullanıcının kararı: "tık tık tık otomasyonlu çalışsın."
//
//   🚚 Kargoya ver      GERÇEK — zincirin tamamı, PARA HARCAR
//   🏷️ Etiketim var     elde barkod varken takip numarasını yazar — ücretsiz
//   🧪 Test gönderisi   AYRI ve İKİNCİL — Geliver'a hiç uğramaz, ücretsiz
//
// "KARGO YOLU" SEÇİMİ KALDIRILDI. Formun başında Geliver/Bagisto seçimi
// duruyordu ve "Bagisto" seçiliyken kırmızı «Kargoya ver / PARA HARCAR»
// düğmesi SESSİZCE test gönderisi açıyordu; «Test gönderisi» düğmesi ise
// seçime hiç bakmıyordu. Artık kırmızı düğme her zaman gerçek yoldur (yol
// kurulumun ayarıdır — bkz. `testInstall`), test ise kendi düğmesidir.
//
// TAŞIYICI VARSAYILANI MÜŞTERİNİN FİRMASIDIR. Eskiden ayardaki varsayılan
// taşıyıcı ya da listenin ilk firması seçili geliyor ve "Kargoya ver" onu
// gönderiyordu; mağaza müşterinin ödediğinden farklı firmayı reddediyor
// (`DispatchManager::assertRequestedCarrier`) ve sihirbazdan gönderim çoğu
// siparişte 409 ile düşüyordu. Şimdi "Siparişteki firma" seçilidir ve hiçbir
// firma GÖNDERİLMEZ — satırdaki düğmeyle aynı davranış.
//
// ÖLÇÜ FORMU DURUYOR ama KAPALI; «📏 Ölçü» ile açık gelir. Yalnız DOKUNULAN
// ölçü gönderilir: dokunulmadıysa mağaza siparişin kendi desisini kullanır.
//
// TEKLİF LİSTESİ DE DURUYOR, aynı katlanır bölmede: "önce fiyatları göreyim"
// diyen taslak açıp teklifleri okuyabilir. Normal iş akışında görünmez.

const MEASURE_FIELDS = ['desi', 'weight', 'length', 'width', 'height'];

/**
 * Formdan "kargoya ver" gövdesine — YALNIZ değişen alanlar.
 *
 * ÖLÇÜYE DOKUNULDUYSA ÖLÇÜLERİN HEPSİ BİRLİKTE GİDER. Sunucu açık taslağın
 * hangi ölçüyle açıldığını karşılaştırır (`_prepare_draft`); "Taslak aç"
 * hepsini gönderiyor, "Kargoya ver" yalnız değişeni gönderseydi aynı ölçü
 * farklı görünür ve taslak boşuna iptal edilirdi.
 *
 * Taşıyıcı yalnız SİPARİŞTEKİNDEN FARKLI seçildiyse gider; mağaza o zaman
 * reddeder ve onay penceresi bunu önceden söyler.
 */
function wizardExtra(form, row) {
  const draft = form.draft();
  const dirty = new Set(form.dirty());
  const extra = {};
  if (draft.carrier && draft.carrier !== row.carrierKey) extra.carrier = draft.carrier;
  if (MEASURE_FIELDS.some((key) => dirty.has(key))) {
    for (const key of MEASURE_FIELDS) {
      if (Number(draft[key]) > 0) extra[key] = Number(draft[key]);
    }
  }
  if (Number(draft.packages) > 1) extra.packages = Number(draft.packages);
  if (draft.payer === 'receiver') extra.payer = 'receiver';
  if (Number(draft.cod) > 0) extra.cod = Number(draft.cod);
  if (draft.note) extra.note = draft.note;
  return extra;
}

/**
 * Ücret dökümü ve taslak için taşıyıcı KODU. Seçim "siparişteki firma"
 * ise siparişin kendi firması, o da okunamadıysa ekran tercihindeki
 * varsayılan taşıyıcı — desi matrisinden doğru satırı bulmak için.
 */
function quoteCarrier(form, row) {
  return form.draft().carrier || row.carrierKey || state.settings?.defaultCarrier || '';
}

/**
 * Büyük seçim düğmesi: başlık + altında ne olacağını söyleyen tek satır.
 *
 * Düğmenin ADI ne yapacağını, alt satırı BEDELİNİ söyler. Eski ekranda bu
 * bilgi `title` özniteliğindeydi ve yalnız fareyi üstünde bekleten görürdü.
 */
function bigChoice({ icon, label, note, variant = '', onClick, disabled = false }) {
  const box = h('button', `sh-big ${variant}`.trim());
  box.type = 'button';
  box.disabled = disabled;
  box.append(
    h('span', 'sh-big-icon', icon),
    h('span', 'sh-big-label', label),
    h('span', 'sh-big-note', note),
  );
  if (onClick) box.addEventListener('click', onClick);
  return box;
}

/**
 * Gönderi sihirbazı — tek çekmece, üç düğme.
 *
 * Birden çok sipariş seçildiyse sırayla geçilir. Toplu gönderim ayrı düğmede
 * ve listenin üstündedir ({@see renderReadyBar}); burası tek siparişe bakar.
 * `measure` satırdaki «📏 Ölçü»den gelir: ölçü bölmesi açık, imleç
 * "Uzunluk"ta açılır.
 */
function openWizard(rows, index = 0, { measure = false } = {}) {
  const row = rows[index];
  if (!row) return;

  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  // Sipariş listesi adresi vermiyor: yer bilgisi boşsa satır "· / " diye
  // görünmesin, hiç yazılmasın.
  const where = [row.city, row.district].filter(Boolean).join(' / ');
  const box = drawer(nodes.root, {
    title: `Kargoya ver — ${row.customer || row.orderNumber}`,
    subtitle: `${index + 1}/${rows.length} · sipariş ${row.orderNumber}`
      + (where ? ` · ${where}` : ''),
    onClose: dropForms,
  });
  closers.push(dropForms);

  const carriers = (state.carriers || []).filter((item) => item.active);
  const carrierOptions = carriers.map((item) => ({ value: item.code, label: item.label }));

  const form = formGrid({
    fields: [
      { key: 'carrier', label: 'Taşıyıcı', type: 'select',
        options: [
          { value: '', label: `Siparişteki firma — ${row.carrierTitle || 'mağaza belirler'}` },
          ...carrierOptions,
        ],
        hint: 'Mağaza müşterinin ödediğinden farklı firmayı reddeder.' },
      { key: 'packages', label: 'Paket sayısı', type: 'number', min: 1, max: 99,
        required: true },
      { key: 'desi', label: 'Desi', type: 'number', min: 0, max: 9999,
        hint: 'Ölçüye dokunmazsanız siparişin kayıtlı desisi kullanılır.' },
      { key: 'weight', label: 'Ağırlık (kg)', type: 'number', min: 0, max: 9999 },
      { key: 'length', label: 'Uzunluk (cm)', type: 'number', min: 0, max: 9999 },
      { key: 'width', label: 'Genişlik (cm)', type: 'number', min: 0, max: 9999 },
      { key: 'height', label: 'Yükseklik (cm)', type: 'number', min: 0, max: 9999 },
      { key: 'payer', label: 'Ödeme tipi', type: 'select', options: PAYERS },
      { key: 'cod', label: 'Kapıda tahsil edilecek', type: 'money', min: 0,
        hint: 'Alıcı ödemeli gönderide taşıyıcının tahsil edeceği tutar.' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 255, wide: true },
    ],
    value: {
      carrier: '',
      packages: 1,
      desi: row.measures.desi || 0,
      weight: row.measures.weight || 0,
      length: 0,
      width: 0,
      height: 0,
      payer: 'sender',
      cod: 0,
      note: '',
    },
    onChange: () => refreshQuote(),
  });
  forms.push(form);

  const quoteBox = h('div', 'sh-quote');
  const offerBox = h('div', 'sh-offers');
  // BELGE KUTUSU AYRI: `showOffers` teklif kutusunu her çağrıda
  // `replaceChildren` ile siliyor; yazdırma düğmesi orada dursaydı
  // teklifler gelir gelmez kaybolurdu.
  const docBox = h('div', 'sh-docs');

  // ÖZET ŞERİDİ — formu açmadan görülmesi gereken üç şey: ne kadar mal,
  // ne kadar ölçü, müşteri hangi firmaya para ödedi.
  const summary = h('div', 'sh-summary');
  const tahmin = Number(row.measures.units) > 0;
  summary.append(
    badge(`${num(row.pending)} / ${num(row.ordered)} kalem`, 'dim'),
    badge(tahmin ? `${num(row.measures.units)} desi` : 'desi: mağaza kaydı',
      row.measures.complete ? 'info' : 'warn'),
    badge(row.measures.complete ? `${num(row.measures.weight, 1)} kg` : 'ölçü eksik',
      row.measures.complete ? 'dim' : 'warn'),
    badge(row.carrierTitle || 'firma seçilmemiş', 'info'),
    h('span', 'kit-spacer'),
    h('b', 'sh-summary-total', money(row.total)),
  );
  box.body.append(summary);

  // Bölge işareti bir uyarıdır, engel değil — tek satır.
  if (!row.delivers) {
    box.body.append(alertBox('Bu bölge "teslimat yapılmıyor" olarak işaretli.', 'warn'));
  }

  // ------------------------------------------------------ üç büyük düğme

  const choices = h('div', 'sh-choices');

  const goButton = bigChoice({
    icon: '🚚',
    label: testInstall() ? 'Kargoya ver (TEST kurulumu)' : 'Kargoya ver',
    note: testInstall()
      ? 'Bu kurulum TEST yolunda: Geliver’a çıkılmaz, para harcanmaz'
      : 'GERÇEK: etiket SATIN ALINIR · takip no siparişe yazılır · etiket ve fatura basılır',
    variant: testInstall() ? '' : 'danger',
    onClick: async () => {
      const sent = await dispatchOrder(row, null, wizardExtra(form, row));
      if (sent) box.close();
    },
  });

  const manualButton = bigChoice({
    icon: '🏷️',
    label: 'Etiketim var — takip no gir',
    note: 'Etiket dışarıdan alındı · para harcanmaz · numara siparişe yazılır',
    onClick: () => manualPanel.classList.toggle('on'),
  });

  const testButton = bigChoice({
    icon: '🧪',
    label: 'Test gönderisi — GERÇEK DEĞİL',
    note: 'Geliver’e hiç uğranmaz · kargo yola çıkmaz · TEST- önekli numara · para harcanmaz',
    variant: 'test',
    onClick: async () => {
      const sent = await testShipOrder(row, null);
      if (sent) box.close();
    },
  });

  choices.append(goButton, manualButton, testButton);

  // ------------------------------------------- elle takip girişi (katlanır)
  //
  // Düğmenin ALTINDA açılır, ayrı bir pencere değil: numara elde bir kâğıtta
  // duruyor ve tek iş onu yazmak. Pencere açmak, tek alanlık bir işi iki
  // tıka çıkarırdı.

  const manualPanel = h('div', 'sh-manual');
  const manualInput = h('input', 'kit-input sh-manual-input');
  manualInput.type = 'text';
  manualInput.maxLength = 64;
  manualInput.placeholder = 'Etiketin üstündeki barkod numarası — örn. GLV31161794989';
  const manualCarrier = h('select', 'kit-input sh-manual-carrier');
  // "Etiketteki firma" MÜŞTERİNİN ÖDEDİĞİ firma olmayabilir: elde kalan bir
  // etiket başka firmadan alınmış olabilir. Boş seçenek "siparişteki firma"
  // demektir ve sunucu onu yazar.
  for (const option of [{ value: '', label: `Siparişteki firma (${row.carrierTitle || '—'})` },
    ...carrierOptions]) {
    const node = h('option', undefined, option.label);
    node.value = option.value;
    manualCarrier.append(node);
  }
  const manualSave = button('Kaydet', {
    variant: 'primary',
    onClick: () => saveManual(),
  });
  // ENTER = KAYDET. Numara elle yazılıyor; yazıp Enter'a basmak, fareyi alıp
  // düğmeye götürmekten hızlıdır ve akış zaten "tık tık tık" olsun diye
  // sadeleştirildi.
  manualInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); saveManual(); }
  });
  manualPanel.append(
    h('span', 'sh-sub', 'Takip numarası'),
    manualInput, manualCarrier, manualSave,
  );

  async function saveManual() {
    const track = manualInput.value.trim();
    if (!track) { manualInput.focus(); toast('Takip numarası boş.', 'warn'); return; }
    const result = await withBusy(`${row.orderNumber} · takip numarası yazılıyor…`,
      () => api(`${BASE}/orders/${row.orderId}/manual-shipment`, {
        method: 'POST',
        body: {
          trackingNo: track,
          carrier: manualCarrier.value || '',
          note: form.draft().note || '',
          // Boş gerekçe: sunucu numarayı da içeren otomatik metin yazar.
          reason: '',
          dryRun: false,
        },
      }));
    if (!result) return;
    // MÜKERRER AYRI BİR CEVAPTIR, hata değil: numara zaten yazılıysa
    // kullanıcının yapacak bir şeyi yok ve "hata" demek onu tekrar denemeye
    // iter. `call` yerine ham `api` çağrılmasının sebebi budur — `ok:false`
    // burada okunabilir bir sonuçtur.
    if (result.ok === false) {
      toast(result.error, result.already ? 'warn' : 'bad');
      if (result.already) loadReady();
      return;
    }
    toast(`Takip numarası siparişe yazıldı · ${result.trackNumber} · ${result.carrier}`,
      'good');
    for (const line of result.warnings || []) toast(line, 'warn');
    box.close();
    if (!result.dryRun) dropReadyRow(row.orderId);
  }

  // --------------------------------------------------- ayrıntı (katlanır)

  let quoteTimer = null;
  function refreshQuote() {
    window.clearTimeout(quoteTimer);
    quoteTimer = window.setTimeout(async () => {
      const draft = form.draft();
      let payload;
      try {
        // Desi matrisi ve sipariş sunucu belleğinden gelir; her alan
        // değişiminde mağazaya istek ÇIKMAZ.
        payload = await call(`${BASE}/quote`, {
          method: 'POST',
          body: {
            orderId: row.orderId,
            carrier: quoteCarrier(form, row),
            desi: Number(draft.desi) || 0,
            weight: Number(draft.weight) || 0,
            length: Number(draft.length) || 0,
            width: Number(draft.width) || 0,
            height: Number(draft.height) || 0,
            payer: draft.payer || 'sender',
            cod: Number(draft.cod) || 0,
          },
        });
      } catch (error) {
        quoteBox.replaceChildren(alertBox(`Ücret hesaplanamadı: ${error.message}`, 'warn'));
        return;
      }
      quoteBox.replaceChildren(quoteView(payload));
    }, 220);
  }
  closers.push(() => window.clearTimeout(quoteTimer));

  const draftButton = button('Taslak aç ve teklifleri getir', {
    title: 'PARA HARCAMAZ: Geliver’de gönderi kaydı açılır ve taşıyıcı fiyatları listelenir. '
      + 'Etiket ayrıca satın alınır.',
    onClick: async () => {
      const draft = form.draft();
      if (!form.valid()) { form.showErrors(); toast('Eksik alan var.', 'warn'); return; }
      const dimensionCount = [draft.length, draft.width, draft.height]
        .filter((value) => Number(value) > 0).length;
      if ((dimensionCount > 0 && dimensionCount < 3)
        || (dimensionCount === 0 && Number(draft.desi) <= 0)) {
        toast('Koli ölçülerinin üçünü girin veya desiyi elle belirtin.', 'warn');
        return;
      }
      const result = await withBusy('Taslak açılıyor…', () => call(
        `${BASE}/orders/${row.orderId}/shipments`, {
          method: 'POST',
          body: {
            // TASLAK HER ZAMAN GELİVER'DİR: teklif ve etiket orada. Test
            // gönderisinin kendi düğmesi var.
            provider: 'geliver',
            carrier: quoteCarrier(form, row), packages: Number(draft.packages) || 1,
            desi: Number(draft.desi) || 0, weight: Number(draft.weight) || 0,
            length: Number(draft.length) || 0, width: Number(draft.width) || 0,
            height: Number(draft.height) || 0,
            payer: draft.payer, cod: Number(draft.cod) || 0, note: draft.note || '',
            // GEREKÇE PENCERESİ KALKTI: taslak açmak PARA HARCAMAZ ve sunucu
            // boş gerekçeye otomatik metin yazıyor (`draft_reason`). Pencere,
            // ücretsiz bir adımın önünde duran tek engeldi.
            reason: '', dryRun: false,
          },
        }));
      if (!result) return;
      toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Taslak açıldı.',
        result.dryRun ? 'warn' : 'good');
      for (const line of result.warnings || []) toast(line, 'warn');
      // Kullanıcı "fatura + kargoya teslim fişi tek A4 çıksın, katlayıp kargo
      // cebine koyacağız" dedi. Etiket satın alınmadan da basılabilmeli,
      // çünkü paket hazırlanırken fişe ihtiyaç var.
      docBox.replaceChildren(button('Teslim fişi + fatura yazdır (A4)', {
        onClick: () => report.run('handover', {
          orderId: row.orderId,
          trackNumber: result.trackNumber || '',
          carrier: quoteCarrier(form, row),
        }),
      }));
      if (result.shipmentId) await showOffers(result.shipmentId);
    },
  });

  async function showOffers(shipmentId) {
    offerBox.replaceChildren(skeletonRows(3, 4));
    let payload;
    try {
      payload = await call(`${BASE}/shipments/${shipmentId}/offers`);
    } catch (error) {
      offerBox.replaceChildren(alertBox(`Teklifler alınamadı: ${error.message}`, 'bad'));
      return;
    }
    offerBox.replaceChildren();
    if (!payload.items.length) {
      offerBox.append(emptyState({
        title: 'Taşıyıcı teklifi gelmedi',
        text: 'Ölçüyü ya da adresi düzeltip yeniden deneyin.',
      }));
      return;
    }
    // Teklif fiyatları TAŞIYICININ kesin tutarıdır; yukarıdaki döküm bizim
    // tahminimiz. Fark varsa taşıyıcının fiyatı geçerlidir — kutu yok,
    // satırlar kesin tutarı zaten taşıyor.
    for (const offer of payload.items) {
      const line = h('div', 'sh-offer');
      line.append(
        h('b', undefined, offer.carrierLabel),
        h('span', 'sh-sub', offer.service || '—'),
        h('span', 'kit-spacer'),
        h('b', 'sh-price', money(offer.price)),
        h('span', 'sh-sub', offer.promise || ''),
        button('Etiketi satın al', {
          variant: 'danger',
          title: 'PARA HARCAR — ayrı izin ve en az 20 karakter gerekçe ister',
          onClick: () => purchase(shipmentId, offer),
        }),
      );
      offerBox.append(line);
    }
  }

  async function purchase(shipmentId, offer) {
    // BU PENCERE KALDI ve kalmalı: ADR 0012'nin üç kapısından biri
    // (`store_shipping.purchase` + 20 karakter gerekçe + `dryRun`) ve gerekçe
    // sunucuda da doğrulanıyor — kaldırmak 422 üretirdi, sadeleştirme değil.
    // "Kargoya ver" bu pencereyi görmüyor çünkü orada gerekçe zorunlu değil.
    const reason = await askPurchaseReason({
      chips: REASON_CHIPS.purchase,
      title: 'Etiketi satın al — PARA HARCAR',
      description: `${offer.carrierLabel} · ${money(offer.price)}. Bu işlem taşıyıcıdan `
        + 'etiket satın alır ve GERİ ALINAMAZ. Gerekçe denetim kaydına yazılır; iki ay '
        + 'sonra "bu tutar neden harcandı" sorusunun cevabı bu metin olacak.',
      confirmLabel: 'Satın al',
    });
    if (!reason) return;
    const result = await withBusy('Etiket satın alınıyor…', () => call(
      `${BASE}/shipments/${shipmentId}/purchase`, {
        method: 'POST',
        body: { offerId: offer.id, reason, dryRun: false },
      }));
    if (!result) return;
    toast(result.dryRun
      ? 'Kuru prova: istek gönderilmedi, etiket alınmadı.'
      : `Etiket alındı · takip no ${result.trackingNo} · ${money(result.fee)}`,
    result.dryRun ? 'warn' : 'good');
    box.close();
    if (!result.dryRun) dropReadyRow(row.orderId);
  }

  // Ayrıntı bölmesi: ölçü formu, ücret dökümü, taslak/teklif/belgeler.
  // ÖLÇÜ EKSİKSE YA DA «📏 Ölçü»DEN GELİNDİYSE KENDİLİĞİNDEN AÇILIR —
  // sadeleştirme, yanlış desiyi gizlemek değildir.
  const details = h('details', 'sh-details');
  const summaryNode = h('summary', 'sh-details-summary',
    'Ölçü, taşıyıcı ve fiyat teklifleri');
  details.append(summaryNode);
  // TEK SATIRLIK yönlendirme; uzun açıklama yok (tasarım §3). Ölçü
  // eksikliğinin nedeni (liste ucu kalem ölçüsü vermiyor) koddadır.
  if (measure) {
    details.open = true;
    details.append(h('div', 'sh-sub',
      'Koliyi ölçüp uzunluk, genişlik ve yüksekliği girin, sonra «Kargoya ver».'));
  } else if (!row.measures.complete && Number(row.measures.units) > 0) {
    details.open = true;
    details.append(alertBox(
      `Ölçüsü eksik ürün var (${row.measures.missing.join(', ')}) — koliyi ölçüp girin.`,
      'warn'));
  }
  details.append(
    card('Ölçü ve taşıyıcı', form.node),
    card('Ücret dökümü (tahmin)', quoteBox, 'Kesin tutarı taşıyıcı belirler'),
    card('Fiyat teklifleri', (() => {
      const wrap = h('div', 'sh-detail-stack');
      wrap.append(draftButton, docBox, offerBox);
      return wrap;
    })(), 'Taslak açmak para harcamaz'),
  );

  box.body.append(choices, manualPanel, details);

  if (index + 1 < rows.length) {
    const next = h('div', 'sh-actions');
    next.append(button('Bu siparişi atla · sonraki →', {
      onClick: () => { box.close(); openWizard(rows, index + 1); },
    }));
    box.body.append(next);
  }

  if (measure) form.focus('length');
  else goButton.focus();
  // Döküm yalnız bölme AÇIKKEN istenir: kapalı bölmede kimse okumuyor ve
  // her sihirbaz açılışı bir istek harcıyordu.
  if (details.open) refreshQuote();
  details.addEventListener('toggle', () => { if (details.open) refreshQuote(); });
}


function quoteView(payload) {
  const box = h('div', 'sh-quote-lines');
  if (payload.ok === false) return alertBox(payload.error || 'Ölçü bilgileri geçersiz.', 'warn');
  const quote = payload.quote || {};
  for (const line of quote.lines || []) {
    const row = h('div', 'sh-quote-row');
    row.append(h('span', undefined, line.label), h('span', 'kit-spacer'),
      h('b', undefined, money(line.amount)));
    if (line.note) row.append(h('span', 'sh-sub', line.note));
    box.append(row);
  }
  const total = h('div', 'sh-quote-total');
  total.append(h('b', undefined, `Toplam: ${money(quote.total)}`), h('span', 'kit-spacer'),
    badge(`${num(payload.units)} desi`, 'info'), badge(quote.payerLabel || '', 'dim'));
  box.append(total);
  if (payload.measuredDesi > 0) {
    const actual = `${num(payload.measuredDesi, 2)} desi`;
    box.append(alertBox(payload.desiMismatch
      ? `Girilen fiziksel ölçüler ${actual} hesaplıyor; fiyat teklifi ve gönderi bu ölçüye `
        + 'göre hazırlanır (ayrı desi alanı göz ardı edilir).'
      : `Fiyat teklifi fiziksel ölçülerden hesaplanan ${actual} değerini kullanıyor.`,
    payload.desiMismatch ? 'warn' : 'good'));
  }
  if (quote.tierFound === false) {
    box.append(alertBox(
      'Bu desi için taşıyıcı sözleşmesinde kademe tanımlı değil; taban ücret 0 sayıldı. '
      + 'Ücretlendirme sekmesinden matrisi tamamlayın.', 'warn'));
  }
  if (payload.zone && payload.zone.found && !payload.zone.delivers) {
    box.append(alertBox('Bu bölgeye teslimat yapılmıyor olarak işaretli.', 'warn'));
  }
  return box;
}

// ================================================================ sekme 2

// EN ÇOK YEDİ SÜTUN (tasarım §5). "Desi" ve "Hareketsiz" çekmeceye indi:
// desi ölçü kartında, hareketsiz gün künyede ve "Geciken" rozetinde duruyor.
const SHIP_COLUMNS = [
  { key: 'trackingNo', label: 'Takip no', width: 'minmax(0, 1.2fr)',
    cell: (row) => track(row) },
  { key: 'orderNumber', label: 'Sipariş', width: '96px', className: 'mono' },
  {
    key: 'customer',
    label: 'Müşteri',
    width: 'minmax(0, 1.5fr)',
    cell: (row) => {
      const box = h('span', 'sh-name');
      box.append(clip(h('b'), row.customer || '—', 30),
        h('span', 'sh-sub', `${row.city} / ${row.district}`.replace(/^ \/ | \/ $/, '')));
      return box;
    },
  },
  { key: 'carrierLabel', label: 'Taşıyıcı', width: '120px' },
  { key: 'fee', label: 'Ücret', width: '100px', align: 'num',
    cell: (row) => money(row.fee) },
  { key: 'createdAt', label: 'Tarih', width: '100px',
    cell: (row) => (row.createdAt ? row.createdAt.slice(0, 10) : '—') },
  { key: 'status', label: 'Durum', width: 'minmax(0, 1.4fr)', cell: statusCell },
];

function shipEmpty() {
  const data = state.shipments;
  if (!data.connected) {
    return emptyState({
      title: 'Gönderiler okunamadı',
      text: data.error || '',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadShipments() })],
    });
  }
  // Arama/taşıyıcı/durum/işaret ÇEKİLEN SAYFANIN içinde uygulanır (mağaza ucu
  // okumuyor); eşleşen başka sayfada olabilir. Kutu yerine tek cümle.
  if (data.pageFiltered) {
    return emptyState({
      title: 'Bu sayfada eşleşen gönderi yok',
      text: 'Sonraki sayfalara bakın ya da süzgeci temizleyin.',
      actions: [button('Süzgeci temizle', { onClick: () => clearShipFilters() })],
    });
  }
  if (data.orderId) {
    return emptyState({
      title: 'Bu siparişin gönderisi yok',
      actions: [button('Tüm gönderiler', { onClick: () => showOrderShipments(0) })],
    });
  }
  return emptyState({ title: 'Henüz gönderi yok' });
}

function renderShipTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  const data = state.shipments;
  // SAYFA İÇİ SÜZGEÇ KUTUSU YOK (tasarım §3): durum satırı "süzgeç bu sayfada
  // N kayıt buldu" der, boş sayfada boş durum ne yapılacağını söyler.
  if (data.orderId) {
    const line = h('div', 'sh-actions');
    line.append(h('span', 'sh-sub', `Yalnız #${data.orderId} siparişinin gönderileri`),
      button('Tümünü göster', { variant: 'ghost', onClick: () => showOrderShipments(0) }));
    wrap.append(line);
  }
  nodes.shipTable = dataTable({
    columns: SHIP_COLUMNS,
    rows: data.items,
    selectable: true,
    empty: shipEmpty(),
    onRow: (row) => openShipment(row.id),
    onSelect: (ids) => { state.selection = ids; renderShipBar(); },
  });
  wrap.append(nodes.shipTable.node);
  renderKpi();
  renderShipBar();
}

/** EN ÇOK DÖRT KUTU (tasarım §6). Tahsilat işareti çipte duruyor. */
function renderKpi() {
  if (!nodes.kpi) return;
  const counts = state.shipments.totals || {};
  if (!state.shipments.connected) { nodes.kpi.replaceChildren(); return; }
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Yolda', value: num(counts.inTransit ?? 0), tone: 'info' },
    { label: 'Teslim', value: num(counts.delivered ?? 0), tone: 'good' },
    { label: 'Geciken', value: num(counts.late ?? 0), tone: 'warn',
      title: `${state.shipments.idleLimit} gündür hareket görmeyen gönderiler` },
    { label: 'Teslim edilemedi', value: num(counts.undelivered ?? 0), tone: 'bad' },
  ]));
}

/**
 * Seçim şeridi — yalnız seçimle ilgili iki eylem. Etiket PDF'i ve manifesto
 * «Dışa aktar» menüsündedir (tasarım §2: ikincil eylemler tek menüde).
 */
function renderShipBar() {
  const bar = nodes.shipBar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.selection.length;
  if (!count) { bar.classList.remove('on'); return; }
  bar.classList.add('on');
  bar.append(
    h('b', undefined, `${num(count)} gönderi seçildi`),
    h('span', 'kit-spacer'),
    button('Durumu tazele', { onClick: () => syncSelected() }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => { nodes.shipTable.clearSelection(); state.selection = []; renderShipBar(); },
    }),
  );
}

/** Seçim gerektiren menü öğesi seçim yoksa ne yapılacağını söyler. */
function withSelection(work) {
  const ids = selectedIds();
  if (!ids.length) {
    toast('Önce listeden gönderi seçin.', 'warn');
    return;
  }
  work(ids);
}

/** Seçilenlerin etiketleri — biçim ekran tercihinden (sunucu da aynını çözer). */
function printSelectedLabels() {
  if (state.settings?.labelLibrary?.ready === false) {
    toast(state.settings.labelLibrary.message || 'Toplu etiket bu kurulumda kapalı.', 'warn');
    return;
  }
  withSelection((ids) => report.run('labels', {
    shipmentIds: ids, format: state.settings?.labelFormat,
  }));
}

/**
 * Seçilenlerin teslimat manifestosu. SAYFA DA GİDER: sunucu satırları bu
 * sayfadan TEK istekle okur; eskiden her gönderi ayrı bir istekti.
 */
function printManifest() {
  withSelection((ids) => report.run('manifest', {
    shipmentIds: ids, page: state.shipments.page, size: state.shipments.size,
  }));
}

function selectedIds() {
  return state.selection.map((id) => Number(id)).filter(Boolean);
}

async function syncSelected() {
  const reason = await askReason({
    chips: REASON_CHIPS.sync,
    title: 'Taşıyıcıdan durum tazele',
    description: `${num(state.selection.length)} gönderi için taşıyıcıya sorulur. `
      + 'Yazma değildir ama taşıyıcı kotasını kullanır.',
    confirmLabel: 'Senkronla',
  });
  if (!reason) return;
  const result = await withBusy('Taşıyıcıdan durum alınıyor…', () => call(
    `${BASE}/shipments/sync`, {
      method: 'POST',
      body: { shipmentIds: selectedIds(), reason, dryRun: false },
    }));
  if (!result) return;
  toast(`${num(result.done)} gönderi tazelendi`
    + (result.failed.length ? ` · ${num(result.failed.length)} tanesi alınamadı` : ''),
  result.failed.length ? 'warn' : 'good');
  loadShipments();
}

function exportVisible() {
  const headers = ['Takip no', 'Sipariş', 'Müşteri', 'Şehir', 'Taşıyıcı', 'Desi', 'Ücret',
    'Ödeme', 'Kapıda ödeme', 'Oluşturma', 'Durum'];
  const rows = state.shipments.items.map((row) => [
    row.trackingNo, row.orderNumber, row.customer, `${row.city}/${row.district}`,
    row.carrierLabel, row.units, money(row.fee), row.payerLabel,
    row.cod ? money(row.cod) : '', row.createdAt, row.statusLabel,
  ]);
  const written = csvBlob(headers, rows, `kargo-sayfa-${state.shipments.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  const values = nodes.shipFilters ? nodes.shipFilters.values() : {};
  // Süzgeçler TÜM liste üzerinde uygulanır (sunucu tam tarama yapar).
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm gönderileri dışa aktar',
    description: 'Süzgece uyan bütün gönderiler rapor klasörüne yazılır. Birkaç dakika '
      + 'sürebilir.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Gönderiler taranıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: { q: values.q || '', carrier: values.carrier || '', status: values.status || '' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) toast('Liste tavana dayandı; dosya eksik olabilir.', 'warn');
  });
}

function applyFlag(key) {
  state.shipments.flag = key || '';
  loadShipments({ page: 1 });
}

/** Arama/taşıyıcı/durum ve işaret süzgeçlerini sıfırlar. */
function clearShipFilters() {
  state.shipments.flag = '';
  nodes.flagChips?.set?.(null);
  nodes.shipFilters.reset();
}

/**
 * Yalnız bir siparişin gönderileri — MAĞAZADA SÜZÜLÜR (`orderId`), sayfa içinde
 * değil. Başka ekrandan "Kargo Yönetimi'nde aç" ile gelindiğinde kullanılır.
 * `0` süzgeci kaldırır.
 */
function showOrderShipments(orderId) {
  state.shipments.orderId = Number(orderId) || 0;
  loadShipments({ page: 1 });
}

// ------------------------------------------------------- gönderi çekmecesi

async function openShipment(shipmentId) {
  const box = drawer(nodes.root, { title: 'Gönderi yükleniyor…', subtitle: `#${shipmentId}` });
  box.body.append(skeletonRows(5, 3));

  let payload;
  try {
    payload = await call(`${BASE}/shipments/${shipmentId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Gönderi okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const row = payload.shipment;
  box.setTitle(row.customer || row.trackingNo || `#${shipmentId}`);
  box.body.replaceChildren();

  const head = h('div', 'sh-drawer-head');
  head.append(statusCell(row), track(row),
    badge(row.carrierLabel, 'info'),
    h('span', 'sh-sub', `${row.city} / ${row.district} · ${row.payerLabel}`));
  box.body.append(head);

  if (row.error) box.body.append(alertBox(`Taşıyıcı hatası: ${row.error}`, 'bad'));
  // Bilinmeyen durum kodu "yolda" VARSAYILMAZ; tek satırda söylenir.
  if (!row.known) {
    box.body.append(alertBox(
      `Bilinmeyen durum "${row.statusLabel}" — taşıyıcıdan doğrulayın.`, 'warn'));
  }

  const facts = h('div', 'sh-facts');
  const fact = (label, value) => {
    const cell = h('div', 'sh-fact');
    cell.append(h('span', 'sh-sub', label), h('b', undefined, value));
    return cell;
  };
  facts.append(
    fact('Sipariş', row.orderNumber || '—'),
    fact('Paket', num(row.packages)),
    fact('Ücret', money(row.fee)),
    fact('Kapıda ödeme', row.cod ? `${money(row.cod)}${row.codCollected ? ' (tahsil edildi)' : ' (tahsil edilmedi)'}` : '—'),
    fact('Oluşturma', row.createdAt || '—'),
    fact('Son hareket', `${row.lastMoveAt || '—'}`),
    fact('Hareketsiz', row.idleDays === null ? '—' : `${num(row.idleDays)} gün`),
    fact('Desi', `${num(row.units)} desi`),
  );
  box.body.append(card('Künye', facts));
  box.body.append(card('Ölçü', measureView(row)));

  // KİTTEN GELİR (ADR 0011). Burada elle yazılmış bir zaman çizelgesi vardı
  // (`sh-timeline` / `sh-move`); kitin `timeline()`'ı aynı işi yapıyor, üstelik
  // bekleyen adımı ve tonu da biliyor. İkinci kopya kitteki düzeltmeleri
  // almıyordu.
  //
  // SIRA ÇEVRİLİR. `movement_rows` en yeniyi ÜSTE koyuyor — bir liste için
  // doğrudur, bir YOLCULUK için değildir: `timeline` eskiden yeniye okutur ve
  // "nereye kadar geldi" sorusunu son satıra bakarak yanıtlatır. Kaynak dizi
  // KOPYALANIR; `reverse()` yerinde çalışır ve `payload.movements`'ı bozarsa
  // aynı çekmece ikinci kez çizildiğinde sıra ters dönerdi.
  const moves = [...(payload.movements || [])].reverse().map((move) => ({
    title: move.statusLabel,
    at: (move.at || '').replace('T', ' '),
    detail: [move.location, move.note].filter(Boolean).join(' · '),
    tone: STATUS_TONES[move.status] || '',
  }));
  box.body.append(card('Hareket geçmişi', timeline(moves, {
    emptyText: 'Taşıyıcı henüz hareket bildirmedi.',
  })));

  // DRAWER SADE: iki görünür eylem, gerisi «Diğer» menüsünde (tasarım §2,
  // §5 — yıkıcı ve seyrek eylemler görünür satırda durmaz).
  const reprint = () => report.run('labels', {
    shipmentIds: [row.id],
    // Biçim BOŞ GEÇİLEBİLİR: ayar okunamadıysa sunucu kendi çözer
    // (`label_format_on` — tercih, yoksa ayar). Buraya elle bir varsayılan
    // yazmak dördüncü bir kopya olurdu.
    format: state.settings?.labelFormat,
  });
  const refreshStatus = async () => {
    const reason = await askReason({
      chips: REASON_CHIPS.sync,
      title: 'Taşıyıcıdan durum tazele',
      description: 'Taşıyıcıya bu gönderi sorulur.',
      confirmLabel: 'Tazele',
    });
    if (!reason) return;
    await withBusy('Tazeleniyor…', () => call(`${BASE}/shipments/sync`, {
      method: 'POST', body: { shipmentIds: [row.id], reason, dryRun: false },
    }));
    box.close();
    openShipment(row.id);
  };
  const notify = async () => {
    const reason = await askReason({
      chips: REASON_CHIPS.notify,
      title: 'Müşteriye kargo bildirimi',
      description: 'Takip numarası ve güncel durum müşteriye gönderilir.',
      confirmLabel: 'Gönder',
    });
    if (!reason) return;
    // ŞABLON GÖNDERİLMEZ: sunucu ekran tercihinden çözer.
    const result = await withBusy('Bildirim gönderiliyor…', () => call(
      `${BASE}/shipments/${row.id}/notify`, { method: 'POST', body: { reason, dryRun: false } }));
    if (result) toast('Bildirim gönderildi.', 'good');
  };
  const retour = async () => {
    const reason = await askPurchaseReason({
      chips: REASON_CHIPS.retour,
      title: 'İade gönderisi aç — PARA HARCAR',
      description: 'Taşıyıcıdan iade etiketi alınır ve faturalanır.',
      confirmLabel: 'İade aç',
    });
    if (!reason) return;
    const result = await withBusy('İade gönderisi açılıyor…', () => call(
      `${BASE}/shipments/${row.id}/return`, { method: 'POST', body: { reason, dryRun: false } }));
    if (result) { toast('İade gönderisi açıldı.', 'good'); box.close(); loadShipments(); }
  };
  const cancel = async () => {
    const reason = await askReason({
      chips: REASON_CHIPS.cancel,
      title: 'Gönderiyi iptal et',
      description: 'Taşıyıcı paketi teslim aldıysa iptali reddedebilir.',
      confirmLabel: 'İptal et',
    });
    if (!reason) return;
    const result = await withBusy('İptal ediliyor…', () => call(
      `${BASE}/shipments/${row.id}/cancel`, { method: 'POST', body: { reason, dryRun: false } }));
    if (result) { toast('İptal isteği gönderildi.', 'good'); box.close(); loadShipments(); }
  };

  const actions = h('div', 'sh-actions');
  actions.append(
    button('Etiketi yeniden bas', { onClick: reprint }),
    button('Siparişi aç', {
      variant: 'ghost',
      disabled: !row.orderId,
      // NUMARA DA GİDER: karşı panel süzgeci metin arıyor ve iç kimlik
      // (`orderId`) çoğu kurulumda görünen numaraya eşit değil.
      onClick: () => openPanel?.('store_orders',
        { orderId: row.orderId, orderNumber: row.orderNumber }),
    }),
    menuButton('Diğer', [
      row.trackingUrl && {
        label: 'Takip bağlantısını kopyala',
        onClick: async () => { await copyText(row.trackingUrl); toast('Bağlantı kopyalandı.', 'good'); },
      },
      { label: 'Durumu tazele', onClick: refreshStatus },
      payload.notify && { label: 'Müşteriye bildir', onClick: notify },
      { label: 'İade gönderisi aç (para harcar)', onClick: retour },
      { label: 'Gönderiyi iptal et', onClick: cancel },
    ]),
  );
  box.body.append(actions);
}

// ================================================================ ayarlar

/**
 * `Ayarlar` sekmesi — dört alt bölüm, üstte tek çip şeridi.
 *
 * Eskiden dördü ayrı üst sekmeydi (tasarım §4: en çok üç sekme). Çip şeridi
 * tek seçimlidir; seçili çipe yeniden basmak bölümü KAPATMAZ (kitin çip
 * şeridi seçimi kaldırır; burada seçim geri konur).
 */
function renderSettings(host) {
  const body = h('div', 'sh-pane');
  const chips = chipRow(SETTINGS_SECTIONS, state.settingsSection, (key) => {
    if (!key) { chips.set(state.settingsSection); return; }
    state.settingsSection = key;
    paint();
  });
  host.append(chips.node, body);

  function paint() {
    const key = state.settingsSection;
    if (key === 'rates') renderRates(body);
    else if (key === 'zones') renderZones(body);
    else if (key === 'geliver') renderGeliver(body);
    else renderCarriers(body);
  }
  paint();
}

// ================================================================ sekme 3

let settingsForm = null;

/**
 * Ekran tercihleri: varsayılan taşıyıcı, etiket biçimi, gecikme eşiği.
 *
 * Taşıyıcılar sekmesinin altındadır çünkü üçünün de karşılığı burada
 * görünür. VİTRİNİ ETKİLEMEZ — mağaza ayarı değil, bu panelin davranışıdır;
 * kart başlığı bunu yazar ki "kaydettim, müşteri de görecek" sanılmasın.
 */
function settingsCard(host) {
  settingsForm?.destroy();
  const data = state.settings || {};
  const carrierOptions = [
    { value: '', label: 'Seçilmemiş' },
    ...(state.carriers || []).map((item) => ({ value: item.code, label: item.label })),
  ];
  settingsForm = formGrid({
    fields: [
      // "KARGOYA VER" BUNU GÖNDERMEZ: etiket her zaman müşterinin ödediği
      // firmadan alınır ve mağaza başka firmayı reddeder. Eskiden sihirbaz bu
      // tercihi seçili getirip gönderiyordu ve gönderim 409 ile düşüyordu.
      { key: 'defaultCarrier', label: 'Varsayılan taşıyıcı', type: 'select',
        options: carrierOptions,
        hint: 'Yalnız siparişin firması okunamadığında ücret tahmini ve taslak için '
          + 'kullanılır. «Kargoya ver» her zaman müşterinin ödediği firmayı kullanır.' },
      { key: 'labelFormat', label: 'Etiket biçimi', type: 'select',
        options: (data.formats || []).map((item) => ({ value: item.value,
          label: item.label })) },
      { key: 'idleDays', label: 'Gecikme eşiği (gün)', type: 'number', min: 1, max: 30,
        hint: 'Kaç gün hareketsiz kalan gönderi "Geciken" sayılır.' },
      // Kapatılırsa belgeler YİNE üretilir ve rapor klasörüne yazılır; yalnız
      // yazıcıya gitmez. "Üretme" ile "basma" ayrı şeyler.
      { key: 'autoPrint', label: '"Kargoya ver" sonrası etiket ve faturayı yazdır',
        type: 'checkbox',
        hint: 'Kapalıyken belgeler yine üretilir, yalnız yazıcıya gönderilmez. '
          + 'Kargoya teslim fişi bu akışa hiç girmez.' },
      // ŞABLON KİMLİĞİ, ŞABLON ADI DEĞİL. Bir dönem burada hiç alan yoktu ve
      // "Müşteriye bildir" düğmesi sabit `shipment_status` gönderiyordu;
      // yetenek o biçimi tanımıyor ve düğme canlıda hiç çalışmıyordu.
      { key: 'notifyTemplate', label: 'Müşteri bildirim şablonu', type: 'text',
        maxLength: 64, wide: true,
        hint: 'Bildirimler ekranındaki şablon kimliği — store:12 ya da local:7. '
          + 'Boşken "Müşteriye bildir" düğmesi çalışmaz ve nedenini söyler.' },
    ],
    value: {
      defaultCarrier: data.defaultCarrier || '',
      labelFormat: data.labelFormat || 'thermal-100x150',
      idleDays: data.idleDays || 3,
      autoPrint: data.autoPrint !== false,
      notifyTemplate: data.notifyTemplate || '',
    },
  });

  const save = button('Tercihleri kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!settingsForm.valid()) { settingsForm.showErrors(); return; }
      const draft = settingsForm.draft();
      const reason = await askReason({
        chips: REASON_CHIPS.settings,
        title: 'Ekran tercihlerini kaydet',
        description: 'Bu tercihler yalnız Kontrol Merkezi’nin kargo ekranını etkiler; '
          + 'vitrindeki kargo ücreti ve taşıyıcı seçimi değişmez.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Tercihler kaydediliyor…', () => call(
        `${BASE}/settings`, {
          method: 'POST',
          body: { defaultCarrier: draft.defaultCarrier || '',
            labelFormat: draft.labelFormat || '',
            idleDays: Number(draft.idleDays) || null,
            autoPrint: Boolean(draft.autoPrint),
            notifyTemplate: (draft.notifyTemplate || '').trim(),
            reason },
        }));
      if (!result) return;
      toast(result.changed?.length ? `Kaydedildi: ${result.changed.join(', ')}.`
        : 'Değişiklik yok.', 'good');
      await loadSettings();
      renderCarriers(host);
    },
  });

  const box = h('div');
  box.append(settingsForm.node, save);
  if (data.labelLibrary && data.labelLibrary.ready === false) {
    box.append(alertBox(data.labelLibrary.message, 'warn'));
  }
  return card('Ekran tercihleri', box,
    'Yalnız bu paneli etkiler — vitrine yansımaz');
}

async function renderCarriers(host) {
  host.replaceChildren(skeletonRows(4, 3));
  let payload;
  try {
    payload = await api(`${BASE}/carriers`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.carriers = payload.items || [];
  host.replaceChildren();

  // Taşıyıcı listesi gelmese bile ekran tercihleri düzenlenebilir: onlar
  // yerel tabloda durur, mağazaya bağlı değildir (K7).
  if (!payload.connected) {
    host.append(alertBox(
      `Taşıyıcı tanımları okunamadı — ${payload.error}. Gönderi sihirbazı taşıyıcı `
      + 'listesi olmadan çalışmaz.', 'bad'), settingsCard(host));
    return;
  }
  if (!state.carriers.length) {
    host.append(emptyState({
      title: 'Tanımlı taşıyıcı yok',
      text: 'Mağaza tarafında henüz taşıyıcı tanımlanmamış. Tanım yapılmadan gönderi '
        + 'oluşturulamaz.',
    }), settingsCard(host));
    return;
  }

  // API kimlik bilgileri kasada durur ve buraya MASKELİ gelir; tam değeri
  // hiçbir ekran göstermez, değişiklik mağaza tarafında yapılır. Kutu yok
  // (tasarım §3); maske kendini anlatıyor.

  for (const carrier of state.carriers) {
    const body = h('div', 'sh-carrier');
    const line = h('div', 'sh-carrier-head');
    line.append(
      badge(carrier.active ? 'Aktif' : 'Pasif', carrier.active ? 'good' : 'dim'),
      carrier.default ? badge('Varsayılan', 'info') : h('span'),
      h('span', 'sh-sub', `Müşteri no: ${carrier.customerNo || '—'}`),
      h('span', 'sh-sub', `API anahtarı: ${carrier.apiKey || '—'}`),
    );
    body.append(line);

    const test = h('div', 'sh-carrier-test');
    test.append(
      button('Bağlantıyı sına', {
        onClick: async () => {
          const reason = await askReason({
            chips: REASON_CHIPS.carrierTest,
            title: `${carrier.label} bağlantısını sına`,
            description: 'Taşıyıcıya kimlik doğrulama isteği gider; gönderi oluşmaz.',
            confirmLabel: 'Sına',
          });
          if (!reason) return;
          const result = await withBusy('Sınanıyor…', () => call(
            `${BASE}/carriers/${carrier.code}/test`, {
              method: 'POST', body: { reason, dryRun: false },
            }));
          if (result) {
            toast(`${carrier.label}: ${result.message || 'bağlantı kuruldu'}`, 'good');
            renderCarriers(host);
          }
        },
      }),
      h('span', 'sh-sub', carrier.testedAt
        ? `Son sınama: ${carrier.testedAt.replace('T', ' ')} · `
          + (carrier.testedOk ? 'başarılı' : 'başarısız')
          + (carrier.testAgeDays !== null ? ` (${num(carrier.testAgeDays)} gün önce)` : '')
        : 'Hiç sınanmamış'),
    );
    body.append(test);

    body.append(tierTable(carrier.tiers, carrier.problems));
    host.append(card(carrier.label, body, `${carrier.tiers.length} desi kademesi`));
  }
  host.append(settingsCard(host));
}

function tierTable(tiers, problems) {
  const box = h('div', 'sh-tiers');
  if (!tiers.length) {
    box.append(alertBox('Bu taşıyıcıda desi kademesi tanımlı değil; ücret hesaplanamaz.',
      'warn'));
    return box;
  }
  for (const tier of tiers) {
    const line = h('div', 'sh-tier');
    line.append(
      h('span', undefined, `${num(tier.min, 1)} – ${tier.max ? num(tier.max, 1) : '∞'} desi`),
      h('span', 'kit-spacer'),
      h('b', undefined, money(tier.price)),
    );
    box.append(line);
  }
  for (const problem of problems || []) box.append(alertBox(problem, 'warn'));
  return box;
}

// ================================================================ sekme 4

let rateForms = [];

async function renderRates(host) {
  host.replaceChildren(skeletonRows(4, 3));
  rateForms.forEach((form) => form.destroy());
  rateForms = [];

  let payload;
  try {
    payload = await api(`${BASE}/rates`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.rates = payload;
  host.replaceChildren();

  if (!payload.connected) {
    // İKİ AYRI DURUM, İKİ AYRI CÜMLE. "Uç yayında değil" beklenecek bir şeydir;
    // "okunamadı" ise tekrar denenecek bir arıza. Aynı metne sığdırmak,
    // personeli olmayan bir arızayı kovalarken bırakırdı. Her iki hâlde de
    // matris ve iki yazma düğmesi HİÇ ÇİZİLMEZ: okunamayan bir tarifenin
    // üstüne yazmak mevcut ücretleri silerdi.
    host.append(alertBox(payload.endpointPending
      ? 'Ücretlendirme mağazada henüz açık değil.'
      : `Ücretlendirme okunamadı — ${payload.error} Tekrar deneyin.`,
    payload.endpointPending ? 'warn' : 'bad'));
    return;
  }

  // BU BÖLÜM VİTRİNİ DOĞRUDAN ETKİLER: matris müşterinin ödeyeceği kargo
  // ücretini belirler. Kademelerde boşluk kalırsa kaydetme reddedilir — açıkta
  // kalan bir desi aralığı vitrinde "ücret hesaplanamadı" hatasına dönüşür.
  // Bunu kaydetme onayı söyler; açıklama kutusu yok (tasarım §3).

  const generalForm = formGrid({
    fields: [
      { key: 'freeThreshold', label: 'Ücretsiz kargo eşiği', type: 'money', min: 0,
        hint: 'Sepet bu tutarı geçerse gönderici ödemeli kargo ücretsizdir. 0 = kapalı.' },
      { key: 'codFee', label: 'Kapıda ödeme hizmet bedeli', type: 'money', min: 0 },
      { key: 'promise', label: 'Teslimat süresi vaadi', type: 'text', maxLength: 255,
        wide: true, placeholder: 'ör. 2-3 iş günü' },
    ],
    value: { freeThreshold: payload.freeThreshold, codFee: payload.codFee,
      promise: payload.promise },
  });
  rateForms.push(generalForm);
  host.append(card('Genel kurallar', generalForm.node));

  const editors = [];
  for (const carrier of payload.carriers) {
    const editor = tierEditor(carrier);
    editors.push(editor);
    host.append(card(`${carrier.label} · desi ücret matrisi`, editor.node,
      'Ücretler kuruş değil, ekranda TL olarak yazılır'));
  }

  const actions = h('div', 'sh-actions');
  const problemBox = h('div', 'sh-problems');
  actions.append(
    button('Ücretlendirmeyi kaydet', {
      variant: 'primary',
      onClick: async () => {
        const draft = generalForm.draft();
        const body = {
          carriers: editors.map((editor) => editor.value()),
          freeThreshold: Number(draft.freeThreshold) || 0,
          codFee: Number(draft.codFee) || 0,
          promise: draft.promise || '',
        };
        const reason = await askReason({
          chips: REASON_CHIPS.rates,
          title: 'Kargo ücretlendirmesini kaydet',
          description: 'Bu değişiklik VİTRİNDEKİ kargo ücretini değiştirir ve her yeni '
            + 'siparişe uygulanır.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        problemBox.replaceChildren();
        let result;
        try {
          result = await api(`${BASE}/rates`, {
            method: 'POST', body: { ...body, reason, dryRun: false },
          });
        } catch (error) {
          toast(error.message, 'bad');
          return;
        }
        if (!result.ok) {
          toast(result.error, 'bad');
          for (const line of result.problems || []) problemBox.append(alertBox(line, 'bad'));
          return;
        }
        toast('Ücretlendirme kaydedildi.', 'good');
        renderRates(host);
      },
    }),
    button('Taşıyıcı fiyat listesini tazele', {
      onClick: async () => {
        const reason = await askReason({
          chips: REASON_CHIPS.priceList,
          title: 'Fiyat listesini tazele',
          description: 'Taşıyıcının güncel sözleşme fiyatları mağazaya yeniden çekilir.',
          confirmLabel: 'Tazele',
        });
        if (!reason) return;
        const result = await withBusy('Fiyat listesi çekiliyor…', () => call(
          `${BASE}/rates/refresh`, { method: 'POST', body: { reason, dryRun: false } }));
        if (result) { toast('Fiyat listesi tazelendi.', 'good'); renderRates(host); }
      },
    }),
  );
  host.append(actions, problemBox);
}

/** Bir taşıyıcının desi matrisi: satır ekle/çıkar, değer yaz. */
function tierEditor(carrier) {
  const node = h('div', 'sh-tier-editor');
  const rows = carrier.tiers.length
    ? carrier.tiers.map((tier) => ({ ...tier }))
    : [{ min: 0, max: 0, price: 0, label: '' }];

  const list = h('div');
  const paint = () => {
    list.replaceChildren();
    rows.forEach((tier, index) => {
      const line = h('div', 'sh-tier-row');
      const numField = (value, onInput, placeholder) => {
        const input = h('input', 'kit-input');
        input.type = 'text';
        input.inputMode = 'decimal';
        input.value = value;
        input.placeholder = placeholder;
        input.addEventListener('input', () => onInput(input.value));
        return input;
      };
      line.append(
        h('span', 'sh-sub', 'desi'),
        numField(String(tier.min ?? 0), (raw) => { tier.min = Number(raw.replace(',', '.')) || 0; }, 'en az'),
        h('span', 'sh-sub', '–'),
        numField(tier.max ? String(tier.max) : '', (raw) => { tier.max = Number(raw.replace(',', '.')) || 0; }, '∞'),
        h('span', 'sh-sub', 'ücret ₺'),
        numField((tier.price / 100).toFixed(2).replace('.', ','),
          (raw) => { tier.price = Math.round((Number(raw.replace(/\./g, '').replace(',', '.')) || 0) * 100); },
          '0,00'),
        button('Sil', {
          variant: 'ghost',
          title: 'Kademeyi listeden çıkarır; mağazada ancak kaydedince silinir',
          onClick: () => { rows.splice(index, 1); paint(); },
        }),
      );
      list.append(line);
    });
  };
  paint();

  const add = button('Kademe ekle', {
    onClick: () => {
      const last = rows[rows.length - 1];
      rows.push({ min: last ? (last.max || last.min + 1) : 0, max: 0, price: 0, label: '' });
      paint();
    },
  });
  node.append(list, add);
  return {
    node,
    value: () => ({ code: carrier.code, tiers: rows.map((tier) => ({
      min: Number(tier.min) || 0, max: Number(tier.max) || 0,
      price: Number(tier.price) || 0, label: tier.label || '',
    })) }),
  };
}

// ================================================================ sekme 5

let zoneForm = null;

async function renderZones(host) {
  host.replaceChildren(skeletonRows(5, 4));
  zoneForm?.destroy();
  zoneForm = null;

  let payload;
  try {
    payload = await api(`${BASE}/zones`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.zones = payload.items || [];
  host.replaceChildren();

  // Bölge eşlemesi Kontrol Merkezi'nde durur: ücret dökümünü ve sihirbaz
  // uyarısını etkiler, VİTRİNİ etkilemez (kart alt başlığı bunu söyler).

  zoneForm = formGrid({
    fields: [
      { key: 'city', label: 'İl', type: 'text', required: true, maxLength: 64 },
      { key: 'district', label: 'İlçe', type: 'text', maxLength: 64,
        hint: 'Boş bırakılırsa ilin tamamı için geçerlidir. İlçe satırı ili ezer.' },
      { key: 'zone', label: 'Bölge adı', type: 'text', maxLength: 64 },
      { key: 'surcharge', label: 'Bölgesel ek ücret', type: 'money', min: 0 },
      { key: 'delivers', label: 'Bu bölgeye teslimat yapılıyor', type: 'checkbox' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 255, wide: true },
    ],
    value: { city: '', district: '', zone: '', surcharge: 0, delivers: true, note: '' },
  });

  const save = button('Bölgeyi kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!zoneForm.valid()) { zoneForm.showErrors(); return; }
      const draft = zoneForm.draft();
      const reason = await askReason({
        chips: REASON_CHIPS.zone,
        title: 'Bölge tanımını kaydet',
        description: `${draft.city} ${draft.district || '(tüm il)'} için bölge, ek ücret ve `
          + 'teslimat durumu yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Bölge kaydediliyor…', () => call(`${BASE}/zones`, {
        method: 'POST',
        body: { city: draft.city, district: draft.district || '', zone: draft.zone || '',
          surcharge: Number(draft.surcharge) || 0, delivers: Boolean(draft.delivers),
          note: draft.note || '', reason },
      }));
      if (result) { toast('Bölge kaydedildi.', 'good'); renderZones(host); }
    },
  });

  const table = dataTable({
    columns: [
      { key: 'city', label: 'İl', width: 'minmax(0, 1fr)' },
      { key: 'district', label: 'İlçe', width: 'minmax(0, 1fr)' },
      { key: 'zone', label: 'Bölge', width: 'minmax(0, 1fr)' },
      { key: 'surcharge', label: 'Ek ücret', width: '110px', align: 'num',
        cell: (row) => money(row.surcharge) },
      { key: 'delivers', label: 'Teslimat', width: '120px',
        cell: (row) => badge(row.delivers ? 'Yapılıyor' : 'Yapılmıyor',
          row.delivers ? 'good' : 'bad') },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)' },
    ],
    rows: state.zones,
    empty: emptyState({
      title: 'Bölge tanımı yok',
      text: 'Hiç bölge tanımlanmamış; her yere aynı ücret uygulanıyor. Uzak ilçelere ek '
        + 'ücret uygulamak için aşağıdaki formu kullanın.',
    }),
    onRow: (row) => zoneForm.reset({ ...row }),
  });

  const formBox = h('div');
  formBox.append(zoneForm.node, save);
  host.append(card('Tanımlı bölgeler', table.node,
    'Yalnız bu paneli etkiler, vitrine yansımaz · satıra tıklayınca form dolar'));
  host.append(card('Bölge ekle / güncelle', formBox));
}

// ================================================================ sekme 6

/**
 * Gönderi durum dağılımı — kitin `stackedBar`'ı.
 *
 * SAYILAR ZATEN VARDI, PAY YOKTU. Aynı rakamlar yukarıdaki KPI kutularında
 * duruyor; yedi kutuya dağılmış sayı "yolda olanlar teslim edilenin kaç katı"
 * sorusunu cevaplamıyor. Tek çubuk bütünü ve içindeki payı birlikte gösterir;
 * her parçanın SAYISI da altındaki açıklama şeridinde yazar.
 *
 * "GECİKEN" ÇUBUĞA GİRMEZ — bilerek. `late` bir DURUM değil, yoldaki bir
 * gönderiye takılan bayraktır; parçalardan biri yapılsaydı aynı gönderi hem
 * "Yolda" hem "Geciken" olarak iki kez sayılır ve toplam gerçek gönderi
 * sayısını aşardı. Geciken sayısı kendi KPI kutusunda ve "Dikkat gerektirenler"
 * listesinde duruyor.
 *
 * İPTAL ARTIKTAN HESAPLANIR: `totals` iptal edilen gönderiyi dört durumun
 * hiçbirine yazmıyor. Toplamdan düşmezsek çubuk, gerçekte var olan gönderilerin
 * tamamını gösterdiğini iddia ederdi.
 */
function statusSpread(counts) {
  const bilinen = ['delivered', 'inTransit', 'undelivered', 'returning']
    .reduce((total, key) => total + (Number(counts[key]) || 0), 0);
  const iptal = Math.max(0, (Number(counts.total) || 0) - bilinen);
  return stackedBar([
    { label: 'Teslim edildi', value: counts.delivered },
    { label: 'Yolda', value: counts.inTransit },
    { label: 'Teslim edilemedi', value: counts.undelivered },
    { label: 'İade', value: counts.returning },
    { label: 'İptal', value: iptal },
  ]);
}

/**
 * Performans — `Gönderiler`in İKİNCİL görünümü (tasarım §4: en çok üç sekme;
 * günlük iş değil). Üstte «← Gönderiler» geri döner.
 *
 * Hesap sunucuda 5 dakika BELLEKTE tutulur: görünüm her açıldığında gönderi
 * geçmişi baştan taranıyordu. «Yenile» belleği atlar; ekran yalnız hesabın
 * saatini yazar, "bellek" gibi iç ayrıntıyı yazmaz (tasarım §3).
 */
async function renderPerformance(host, { fresh = false } = {}) {
  host.replaceChildren(skeletonRows(6, 5));
  let payload;
  try {
    payload = await api(`${BASE}/performance${fresh ? '?fresh=true' : ''}`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  if (!payload.connected) {
    host.append(emptyState({
      title: 'Performans hesaplanamadı',
      text: payload.error || 'Mağazaya ulaşılamadı.',
      actions: [button('Tekrar dene', { variant: 'primary',
        onClick: () => renderPerformance(host, { fresh: true }) })],
    }));
    return;
  }

  const age = h('div', 'sh-actions');
  age.append(
    h('span', 'sh-sub', `Son ${payload.days} gün · ${stampIso(payload.at || '')}`),
    h('span', 'kit-spacer'),
    button('Yenile', { variant: 'ghost', onClick: () => renderPerformance(host, { fresh: true }) }),
    button('Performans raporu (PDF)', {
      variant: 'ghost',
      onClick: () => report.run('performance', { days: payload.days }),
    }),
  );
  host.append(age);

  // EN ÇOK DÖRT KUTU (tasarım §6). Maliyet/tahsilat kırılımı taşıyıcı
  // tablosunda ve "Fark" kutusunun ipucunda duruyor.
  const counts = payload.totals || {};
  const cash = payload.money || {};
  host.append(kpiRow([
    { label: `Gönderi (${payload.days} gün)`, value: num(counts.total) },
    { label: 'Teslim oranı', value: percent(counts.deliveredRate) },
    { label: 'Geciken', value: num(counts.late), tone: 'warn' },
    { label: 'Kargo kâr/zarar', value: money(cash.margin),
      tone: cash.margin < 0 ? 'bad' : 'good',
      title: `Maliyet ${money(cash.cost)} · tahsil edilen ${money(cash.collected)}` },
  ]));

  // Uyarılar TEK SATIR (tasarım §3).
  if (cash.missingCollected) {
    host.append(alertBox(
      `${num(cash.missingCollected)} gönderide tahsil edilen kargo bedeli okunamadı — `
      + 'kâr/zarar eksik.', 'warn'));
  }
  if (cash.codOutstanding) {
    host.append(alertBox(
      `Tahsil edilmemiş kapıda ödeme: ${money(cash.codOutstanding)}.`, 'warn'));
  }

  const rows = payload.carriers || [];
  if (!rows.length) {
    host.append(emptyState({
      title: 'Bu pencerede gönderi yok',
      text: `Son ${payload.days} günde kargo hareketi görünmüyor.`,
    }));
    return;
  }

  host.append(card('Gönderiler şu an nerede', statusSpread(counts),
    `son ${payload.days} günde açılan ${num(counts.total)} gönderi`));

  // Renk tek başına anlam taşımaz: grafiğin altında her taşıyıcının sayıları
  // tabloda da durur.
  host.append(card('Taşıyıcı karşılaştırması',
    groupedBar(rows.map((row) => ({
      label: row.label,
      values: [row.avgDays ?? 0, row.medianDays ?? 0],
    })), { series: ['Ortalama gün', 'Ortanca gün'], format: (value) => value.toFixed(1) }),
    'Teslim edilmiş gönderilerden'));

  host.append(card('Teslim süresi dağılımı',
    barChart((payload.delays || []).map((item) => ({
      label: item.label, value: item.count, display: num(item.count),
    })))));

  const table = dataTable({
    columns: [
      { key: 'label', label: 'Taşıyıcı', width: 'minmax(0, 1.4fr)' },
      { key: 'count', label: 'Gönderi', width: '90px', align: 'num',
        cell: (row) => num(row.count) },
      { key: 'delivered', label: 'Teslim', width: '90px', align: 'num',
        cell: (row) => num(row.delivered) },
      { key: 'failRate', label: 'Teslim edilemeyen', width: '150px', align: 'num',
        cell: (row) => (row.failRate === null
          ? '—'
          : `${num(row.undelivered)} · ${percent(row.failRate)}`) },
      { key: 'avgDays', label: 'Ort. gün', width: '96px', align: 'num',
        cell: (row) => (row.avgDays === null ? '—' : num(row.avgDays, 1)) },
      { key: 'cost', label: 'Maliyet', width: '116px', align: 'num',
        cell: (row) => money(row.cost) },
      { key: 'collected', label: 'Tahsil', width: '116px', align: 'num',
        cell: (row) => money(row.collected) },
      { key: 'margin', label: 'Fark', width: '116px', align: 'num',
        cell: (row) => {
          const cell = h('b', row.margin < 0 ? 'sh-bad' : 'sh-good', money(row.margin));
          return cell;
        } },
    ],
    rows,
    rowKey: (row) => row.carrier,
  });
  host.append(card('Taşıyıcı tablosu', table.node));

  if ((payload.late || []).length) {
    const late = h('div', 'sh-list');
    for (const row of payload.late) {
      const line = h('div', 'sh-list-row');
      line.append(track(row), h('span', undefined, row.customer || '—'),
        h('span', 'sh-sub', row.carrierLabel), h('span', 'kit-spacer'),
        badge(`${num(row.idleDays)} gündür hareketsiz`, 'bad'));
      line.tabIndex = 0;
      line.style.cursor = 'pointer';
      line.addEventListener('click', () => openShipment(row.id));
      line.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') openShipment(row.id);
      });
      late.append(line);
    }
    host.append(card('Dikkat gerektirenler', late,
      `${num(payload.late.length)} gönderi uzun süredir hareketsiz`));
  }

  if (payload.truncated) {
    host.append(alertBox('Liste çok uzun; rakamlar eksik olabilir.', 'warn'));
  }
}

// ================================================================ sekme 7
//
// GELİVER KURULUMU — "Bagisto admin paneline bir daha girmemek" sekmesi.
//
// NE YAPAR: API tokenını yazar (geri OKUMAZ), "kurulu"/"canlı"/"test"
// anahtarlarını çevirir, gönderici adres kimliğini ayarlar, bağlantıyı sınar
// ve webhook'u Geliver hesabına kaydeder.
//
// NE YAPMAZ:
//  · TOKENI GÖSTERMEZ. Mağaza tokenı hiçbir gövdede döndürmüyor; ekran yalnız
//    son dört karakterlik maskeyi görür. Kutu BOŞ bırakılırsa mevcut token
//    KORUNUR — boşu yazmak mağazanın kargo kimliğini silmek olurdu.
//  · CANLIYA GEÇİŞİ KENDİ KENDİNE ONAYLAMAZ. "Canlı" anahtarı açıkken kaydet
//    denince kararı SUNUCU verir: Geliver'a sorar, geçemezse reddeder ve
//    nedenleri söyler. Ekran o nedenleri olduğu gibi basar; kendi tahminini
//    sunucunun cevabının yerine koymaz.
//  · CHECKOUT BAŞLIĞINI, FİYAT AYARLAMASINI VE FİRMA SEÇİMİNİ değiştirmez —
//    mağaza bu üçünü bu uçtan reddediyor ve gerekçesini söylüyor; ekran o
//    gerekçeleri listeler ki "neden burada yok" sorusu cevapsız kalmasın.

let geliverForm = null;

/** Sunucunun son reddi — kaydet düğmesinin ALTINDA kalıcı olarak durur.
 *
 * Toast yetmez: ön denetim reddi üç ayrı neden içerebilir ve her birinin
 * kendi "sıradaki adım"ı var. Kaybolan bir bildirimin içine yazılırsa
 * operatör okumaya yetişemez ve aynı düğmeye yeniden basar.
 */
let geliverProblems = null;

async function renderGeliver(host) {
  host.replaceChildren(skeletonRows(5, 3));
  geliverForm?.destroy();
  geliverForm = null;

  let payload;
  try {
    payload = await api(`${BASE}/geliver`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.geliver = payload;
  host.replaceChildren();

  if (!payload.connected) {
    host.append(alertBox(
      `Geliver ayarları okunamadı — ${payload.error}. Ayar yazma açılmadı: mevcut değerleri `
      + 'bilmeden yazmak, dokunmadığınız anahtarları da ezerdi.', 'bad'));
    host.append(button('Yeniden dene', { onClick: () => renderGeliver(host) }));
    return;
  }

  geliverProblems = h('div', 'sh-gl-problems');

  host.append(statusCard(payload));
  host.append(checksCard(host, payload));
  host.append(settingsFormCard(host, payload));
  host.append(geliverProblems);
  host.append(webhookCard(host, payload));
  host.append(refusedCard(payload));
}

/** "Müşteri şu an ne görüyor" — ekranın ilk bakışta okunan tek cümlesi. */
function statusCard(payload) {
  const status = payload.status || {};
  const token = payload.token || {};
  const box = h('div', 'sh-gl-status');
  box.append(
    badge(status.visible ? 'Müşteriye AÇIK' : 'Müşteriye kapalı',
      status.tone || 'dim'),
    h('span', 'sh-sub', status.label || ''),
  );
  const line = h('div', 'sh-gl-line');
  line.append(
    h('span', 'sh-sub', `API tokenı: ${token.hasToken ? token.mask || 'girilmiş' : 'GİRİLMEMİŞ'}`),
    h('span', 'sh-sub', `Test modu: ${payload.settings?.testMode ? 'AÇIK — gönderiler sahtedir'
      : 'kapalı'}`),
  );
  box.append(line);

  const blockers = payload.blockers || [];
  if (blockers.length) {
    // ENGELLER KAYDETMEDEN ÖNCE GÖRÜNÜR. Kullanıcıyı önce reddedilecek bir
    // isteğe göndermek kötü bir öğretmendir.
    const list = h('div', 'sh-gl-blockers');
    for (const item of blockers) {
      const row = h('div', 'sh-gl-blocker');
      row.append(h('b', undefined, item.message));
      if (item.action) row.append(h('span', 'sh-sub', item.action));
      list.append(row);
    }
    box.append(alertBox('Canlıya geçiş şu an mümkün değil:', 'warn'), list);
  }
  return card('Durum', box, 'Üçü birden açık olmadan müşteri Geliver fiyatını görmez');
}

/** Geliver'a sorulan iki sorunun cevabı + "şimdi dene" düğmesi. */
function checksCard(host, payload) {
  const box = h('div', 'sh-gl-checks');
  for (const check of payload.checks || []) {
    const row = h('div', 'sh-gl-check');
    row.append(
      badge(`${CHECK_MARKS[check.state] || '—'} ${check.label}`,
        CHECK_TONES[check.state] || 'dim'),
      h('span', 'sh-sub', check.detail || ''),
    );
    box.append(row);
  }
  box.append(button('Bağlantıyı sına', {
    title: 'Geliver’a yalnız OKUMA çağrısı gider; gönderi, etiket ya da iade oluşmaz.',
    onClick: async () => {
      const result = await withBusy('Sınanıyor…', () => call(`${BASE}/geliver/test`));
      if (!result) return;
      const failed = (result.checks || []).filter((item) => item.state !== 'ok');
      toast(failed.length ? `${failed.length} denetim geçmedi.` : 'Bağlantı kuruldu.',
        failed.length ? 'warn' : 'good');
      renderGeliver(host);
    },
  }));
  return card('Bağlantı denetimi', box,
    // Sınama mağazanın önbelleğini atlar ve o anki gerçeği sorar.
    'Geliver’a şu an sorulur');
}

function settingsFormCard(host, payload) {
  const data = payload.settings || {};
  const token = payload.token || {};
  geliverForm = formGrid({
    fields: [
      { key: 'apiToken', label: 'API tokenı', type: 'text', wide: true, maxLength: 512,
        placeholder: token.hasToken
          ? `Kayıtlı: ${token.mask || '****'} — değiştirmeyecekseniz BOŞ bırakın`
          : 'Geliver panelindeki tokenı yapıştırın',
        hint: 'Kaydedilen token bir daha OKUNAMAZ; ekran yalnız son dört karakterini '
          + 'gösterir. Boş bırakmak mevcut tokenı SİLMEZ — silmenin yolu yenisiyle '
          + 'değiştirmek ya da entegrasyonu kapatmaktır.' },
      { key: 'active', label: 'Entegrasyon kurulu', type: 'checkbox',
        hint: 'Kapatmak kargoyu kaldırmaz: checkout mağazanın kendi desi tablosundaki '
          + 'ücretlere döner.' },
      { key: 'goLive', label: 'Müşteriye açık (canlı)', type: 'checkbox',
        hint: 'Açmak checkout’ta Geliver’ın CANLI fiyatını gösterir ve sipariş ekranından '
          + 'gerçek gönderi açılmasını başlatır. Sunucu önce denetler; geçemezse reddeder.' },
      { key: 'testMode', label: 'Test modu', type: 'checkbox',
        hint: 'Açıkken oluşturulan gönderiler SAHTEDİR; gerçek kargo çağrılmaz ve etiket '
          + 'taşınmaz.' },
      { key: 'senderAddressId', label: 'Gönderici adres kimliği', type: 'text', maxLength: 64,
        hint: 'Boş = hesabın varsayılan çıkış adresi. Buraya adresin kendisi değil, '
          + 'Geliver’daki kimliği yazılır.' },
      { key: 'sourceIdentifier', label: 'Mağaza adresi (salt gösterilir)', type: 'static' },
      { key: 'title', label: 'Checkout başlığı (salt gösterilir)', type: 'static' },
    ],
    value: {
      apiToken: '',
      active: Boolean(data.active),
      goLive: Boolean(data.goLive),
      testMode: Boolean(data.testMode),
      senderAddressId: data.senderAddressId || '',
      sourceIdentifier: data.sourceIdentifier || '—',
      title: data.title || '—',
    },
  });

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: () => saveGeliver(host, payload, false),
  });
  const preview = button('Önce prova et', {
    title: 'Hiçbir şey yazılmaz; yalnız ne gideceği gösterilir.',
    onClick: () => saveGeliver(host, payload, true),
  });

  const box = h('div');
  const actions = h('div', 'sh-actions');
  actions.append(preview, save);
  box.append(geliverForm.node, actions);
  return card('Ayarlar', box, 'Gerekçe en az 20 karakter — canlıya geçiş para hareketi başlatır');
}

async function saveGeliver(host, payload, dryRun) {
  if (!geliverForm.valid()) { geliverForm.showErrors(); return; }
  const draft = geliverForm.draft();
  const body = {
    apiToken: String(draft.apiToken || '').trim(),
    active: Boolean(draft.active),
    goLive: Boolean(draft.goLive),
    testMode: Boolean(draft.testMode),
    senderAddressId: String(draft.senderAddressId || '').trim(),
  };
  const opening = body.goLive && !payload.settings?.goLive;
  const reason = await askPurchaseReason({
    chips: REASON_CHIPS.geliver,
    title: dryRun ? 'Geliver ayarlarını prova et' : 'Geliver ayarlarını kaydet',
    description: opening
      ? 'CANLIYA ALIYORSUNUZ: checkout Geliver’ın gerçek fiyatını göstermeye, sipariş '
        + 'ekranı gerçek gönderi açmaya başlar. Sunucu önce token, erişim ve gönderici '
        + 'adresi denetler; geçemezse istek reddedilir ve neden söylenir.'
      : 'Kargo entegrasyonunun kurulum ayarları değişecek. Gerekçe denetim kaydına yazılır.',
    confirmLabel: dryRun ? 'Prova et' : 'Kaydet',
  });
  if (!reason) return;

  // `call` DEĞİL, ham `api`: `call` `ok:false` yanıtını istisnaya çevirir ve
  // ön denetim engelleri (gövdedeki `blockers`) yolda kaybolurdu. Bu ekranın
  // asıl işi tam olarak o engelleri göstermek.
  const result = await withBusy(dryRun ? 'Prova ediliyor…' : 'Kaydediliyor…', () => api(
    `${BASE}/geliver`, { method: 'POST', body: { ...body, reason, dryRun } }));
  if (!result) return;

  if (result.ok === false) {
    // SUNUCUNUN REDDİ OLDUĞU GİBİ GÖSTERİLİR ve EKRANDA KALIR. Ön denetim
    // engelleri hata metnine sığmıyor; ayrı satırlar hâlinde gelirler ve her
    // biri ne yapılacağını söyler.
    toast(result.error, 'bad');
    geliverProblems?.replaceChildren(alertBox(result.error, 'bad'));
    for (const item of result.blockers || []) {
      const row = h('div', 'sh-gl-blocker');
      row.append(h('b', undefined, item.message));
      if (item.action) row.append(h('span', 'sh-sub', item.action));
      geliverProblems?.append(row);
    }
    for (const problem of result.problems || []) {
      geliverProblems?.append(alertBox(problem, 'warn'));
    }
    return;
  }
  geliverProblems?.replaceChildren();

  if (result.dryRun) {
    toast(`Prova: ${(result.labels || []).join(' · ') || 'değişen alan yok'}. Hiçbir şey yazılmadı.`,
      'warn');
    return;
  }
  toast(`Kaydedildi: ${(result.labels || []).join(' · ')}.`, 'good');
  renderGeliver(host);
}

function webhookCard(host, payload) {
  const hook = payload.webhook || {};
  const box = h('div');
  box.append(alertBox(hook.message || 'Webhook durumu bilinmiyor.',
    { ok: 'good', missing: 'bad', unknown: 'warn' }[hook.state] || 'info'));
  if (hook.url) box.append(h('code', 'sh-gl-url', hook.url));

  box.append(button('Webhook’u kaydet', {
    variant: hook.state === 'missing' ? 'primary' : '',
    title: 'Geliver hesabında bir webhook kaydı açar; gönderi durumu bundan sonra '
      + 'kendiliğinden güncellenir.',
    onClick: async () => {
      const reason = await askReason({
        chips: REASON_CHIPS.webhook,
        title: 'Webhook’u Geliver hesabına kaydet',
        description: 'Geliver hesabınızda bir kayıt açılır. Gönderi durumu bundan sonra '
          + 'taşıyıcıdan kendiliğinden gelir; bugün her gönderi elle senkron bekliyor.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Webhook kaydediliyor…', () => call(
        `${BASE}/geliver/webhook`, { method: 'POST', body: { reason, dryRun: false } }));
      if (!result) return;
      toast('Webhook kaydedildi.', 'good');
      renderGeliver(host);
    },
  }));
  return card('Webhook', box, 'Gönderi durumunun kendiliğinden güncellenmesini sağlar');
}

/** Bu uçtan DEĞİŞTİRİLEMEYEN alanlar — sessizce yok olmazlar. */
function refusedCard(payload) {
  const refused = payload.refused || {};
  const keys = Object.keys(refused);
  if (!keys.length) return h('span');
  const box = h('div', 'sh-gl-refused');
  for (const key of keys) {
    const row = h('div');
    row.append(h('code', undefined, key), h('span', 'sh-sub', ` — ${refused[key]}`));
    box.append(row);
  }
  return card('Buradan değiştirilemeyenler', box,
    'Mağaza panelinden düzenlenir; gerekçeleri mağazanın kendi cevabıdır');
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  openPanel = ctx.open;

  const view = h('div', 'kit-panel sh');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar(TABS, 'ready', (key) => showTab(key));
  nodes.status = statusLine();
  nodes.kpi = h('div', 'sh-kpi');
  nodes.shipBar = h('div', 'sh-selbar');
  nodes.tableWrap = h('div', 'sh-table');
  nodes.body = h('div', 'sh-body');

  // SADE SÜZGEÇ (tasarım §1): tek arama kutusu + hayalet «Yenile».
  nodes.readyFilters = filterBar({
    compact: true,
    fields: [{ kind: 'search', key: 'q', placeholder: 'Müşteri ara', width: '260px',
      primary: true }],
    onChange: () => loadReady({ page: 1 }),
    actions: [button('Yenile', { variant: 'ghost', onClick: () => loadReady() })],
  });

  // ÜÇ SÜZGEÇ, ÜÇÜ DE ÇALIŞIR; görünür olan İKİ (arama + durum), taşıyıcı
  // "Filtreler"in arkasında. Şehir, tarih aralığı, ödeme tipi ve desi aralığı
  // KALDIRILDI: mağazanın gönderi ucu hiçbirini okumuyordu ve ekran süzülmüş
  // sanılan tam listeyi gösteriyordu. Kalan üçü sunucuda, çekilen sayfanın
  // içinde uygulanır; durum satırı bunu yazar.
  nodes.shipFilters = filterBar({
    compact: true,
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Takip no, sipariş, müşteri', width: '260px',
        primary: true },
      { kind: 'select', key: 'status', label: 'Durum', primary: true, options: [
        { value: '', label: 'Tümü' },
        ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
      ] },
      { kind: 'select', key: 'carrier', label: 'Taşıyıcı',
        options: [{ value: '', label: 'Tümü' }] },
    ],
    onChange: () => loadShipments({ page: 1 }),
    actions: [
      button('Yenile', { variant: 'ghost', onClick: () => loadShipments() }),
      button('📊 Performans', {
        variant: 'ghost',
        title: 'Teslim süreleri ve kargo kâr/zararı',
        onClick: () => showTab('performance'),
      }),
      // İKİNCİL EYLEMLER TEK MENÜDE (tasarım §2).
      menuButton('Dışa aktar', [
        { label: 'Görünen sayfa — Excel', onClick: exportVisible },
        { label: 'Tüm gönderiler — Excel', onClick: exportAll },
        { label: 'Seçilenlerin etiketleri — PDF', onClick: printSelectedLabels },
        { label: 'Seçilenlerin manifestosu — PDF',
          title: 'Şoföre imzalatılan teslimat listesi', onClick: printManifest },
      ]),
    ],
  });

  nodes.flagChips = chipRow(FLAG_CHIPS, null, (key) => applyFlag(key));
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => loadShipments({ page, size }),
  });

  const shipView = [nodes.shipFilters.node, nodes.flagChips.node, nodes.status.node,
    nodes.kpi, nodes.shipBar, nodes.tableWrap, nodes.pager.node];

  view.append(tabs.node, nodes.body);

  /**
   * Sekme gövdesi. `performance` sekme çubuğunda YOKTUR: `Gönderiler`in
   * ikincil görünümüdür ve sekme çubuğu `Gönderiler`de kalır.
   */
  function showTab(key) {
    state.tab = key;
    nodes.body.replaceChildren();
    if (key === 'ready') {
      nodes.readyHost = h('div', 'sh-pane');
      nodes.body.append(nodes.readyFilters.node, nodes.readyHost);
      loadReady();
      return;
    }
    if (key === 'shipments') {
      for (const node of shipView) nodes.body.append(node);
      if (!state.shipments.loaded) loadShipments();
      else renderShipTable();
      return;
    }
    const host = h('div', 'sh-pane');
    if (key === 'performance') {
      const back = h('div', 'sh-actions');
      back.append(button('← Gönderiler', { variant: 'ghost', onClick: () => showTab('shipments') }));
      nodes.body.append(back, host);
      renderPerformance(host);
      return;
    }
    nodes.body.append(host);
    renderSettings(host);
  }

  /** Programla sekme değiştirir — sekme çubuğu da aynı yeri gösterir. */
  function goTab(key) {
    tabs.select(key, false);
    showTab(key);
  }

  root.replaceChildren(view);
  // Taşıyıcılar ÖNCE okunur: gönderi sihirbazı taşıyıcı listesi olmadan
  // açılamıyor ve kullanıcı ilk seçtiği siparişte boş bir açılır liste görürdü.
  loadSettings()
    .then(() => api(`${BASE}/carriers`).catch(() => null))
    .then((payload) => {
      state.carriers = payload?.items || [];
      nodes.shipFilters.options('carrier', [
        { value: '', label: 'Tümü' },
        ...state.carriers.map((item) => ({ value: item.code, label: item.label })),
      ]);
    })
    // Hazırlık ne olursa olsun ekran AÇILIR (K7): taşıyıcı listesi gelmezse
    // sihirbaz kısıtlı çalışır, ama panel boş kalmaz.
    .catch(() => null)
    // DERİN BAĞLANTI. Kabuk `ctx.payload`'ı yıllardır geçiriyordu ve iki panel
    // de okumuyordu: "Kargo Yönetimi'nde aç" ekranı açıyor ama SİPARİŞİ
    // açmıyordu; kullanıcı aramayı baştan yapıyordu. Sipariş numarasıyla
    // gelen bir çağrı artık doğru sekmeyi açar ve süzgeci doldurur.
    .then(() => {
      const target = ctx.payload || {};
      // GÖNDERİ KİMLİĞİ GELDİYSE GÖNDERİ AÇILIR; SİPARİŞ GELDİYSE MAĞAZADA
      // SÜZÜLÜR. Eskiden her künye arama kutusuna yazılıyordu ve arama
      // mağazaya hiç ulaşmadığı için ekran "bulunamadı" ya da ilgisiz bir
      // sayfa gösteriyordu.
      if (Number(target.shipmentId)) {
        state.shipments.orderId = Number(target.orderId) || 0;
        goTab('shipments');
        openShipment(Number(target.shipmentId));
        return;
      }
      if (Number(target.orderId)) {
        state.shipments.orderId = Number(target.orderId);
        goTab('shipments');
        return;
      }
      const anahtar = String(target.trackingNo || target.orderNumber || '').trim();
      if (!anahtar) { showTab('ready'); return; }
      // Takip no / sipariş no: arama kutusu bu sayfanın içinde arar.
      goTab('shipments');
      nodes.shipFilters.set?.('q', anahtar);
      loadShipments({ page: 1 });
    });

  return () => {
    nodes.readyFilters?.destroy();      // arama alanı bekleyen debounce tutar
    nodes.shipFilters?.destroy();       // tarih aralığı global dinleyici tutar
    zoneForm?.destroy();
    zoneForm = null;
    settingsForm?.destroy();
    settingsForm = null;
    geliverForm?.destroy();
    geliverForm = null;
    geliverProblems = null;
    rateForms.forEach((form) => form.destroy());
    rateForms = [];
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = freshState();
    busy = false;
  };
}

// ═══════════════════════════════════════════════ paylaşılan yetenek: yüzey

/**
 * Bir siparişin gönderisini BAŞKA ekranlara gösterir — `store.shipment.byOrder`.
 *
 * NEDEN BU DIŞA VURUM ŞARTTI. Manifest bu yeteneği ilan ediyor ve backend
 * sağlıyordu, ama panel `capabilities()` dışa vurmadığı için kabuk onu HİÇ
 * çözemiyordu (ui-kernel.js: `typeof module.capabilities !== 'function' → {}`).
 * Sonuç: Siparişler ekranının Kargo sekmesi hiç açılmıyor ve kullanıcıya
 * "veriyi veren ekran bu kurulumda yok" deniyordu — oysa ekran iki dosya
 * ötede duruyordu. Doğru yolu deneyen kişi duvara çarpıp kendi kopyasını
 * yazıyordu; üç ayrı "kargoya ver" düğmesinin kökeni budur.
 *
 * SALT OKUMA — BİLEREK. Buradan gönderi açılmaz, etiket alınmaz, iptal
 * edilmez. Kargoya vermenin tek evi bu panelin kendisidir; başka ekranda
 * düğme bulunmaz, yalnız DURUM görünür. Yüzeyin dar olması bunu kaza eseri
 * bozmayı da imkânsız kılar (K3).
 */
export function capabilities(ctx) {
  return {
    'store.shipment.byOrder': async (orderId) => {
      let payload;
      try {
        payload = await ctx.api(`${BASE}/orders/${Number(orderId)}/shipments`);
      } catch (error) {
        return { ok: false, error: error.message || 'Gönderi bilgisi okunamadı.' };
      }
      // BAĞLANTI YOKSA "GÖNDERİ YOK" DENMEZ. İkisi çok farklı: biri "bu
      // sipariş kargoya verilmedi", diğeri "bilmiyoruz". İkincisini
      // birincisi gibi göstermek, kargolanmış bir siparişi kargosuz sanmaya
      // ve ikinci kez göndermeye yol açar.
      if (payload?.connected === false) {
        return { ok: false, error: payload.error || 'Kargo bilgisine şu an ulaşılamıyor.' };
      }
      const items = payload?.items || [];
      if (!items.length) return { rows: [] };
      // STİL BURADA YÜKLENİR, DOSYA TEPESİNDE DEĞİL. Kart başka panelin
      // içine çiziliyor ve oranın `panel.css`'i bizimkini taşımıyor. Tepede
      // yüklemek ise kabuk yetenek çözümü sırasında HİÇ AÇILMAYAN panelleri
      // de import ettiği için kullanılmayan stilleri head'e sızdırırdı
      // (kit.js:399-401). Burası kartın gerçekten çizildiği tek an.
      loadStyles(import.meta.url);
      return { node: shipmentCards(items, ctx) };
    },
  };
}

/** Mutlu yol — aşama şeridi bu sırayı çizer. */
const JOURNEY = ['created', 'picked_up', 'in_transit', 'out_for_delivery', 'delivered'];

/** Mutlu yoldan çıkmış durumlar: şeritte değil, ayrı uyarı olarak gösterilir. */
const OFF_TRACK = {
  undelivered: ['Teslim edilemedi', 'bad'],
  returning: ['İade dönüyor', 'warn'],
  returned: ['İade teslim edildi', 'warn'],
  cancelled: ['İptal edildi', 'dim'],
};

function shipmentCards(items, ctx) {
  const box = h('div', 'sh-cap');
  for (const row of items) box.append(shipmentCard(row, ctx));
  return box;
}

function shipmentCard(row, ctx) {
  const body = h('div', 'sh-cap-body');

  // ── künye: takip numarası kopyalanabilir, çünkü aranan şey odur
  const head = h('div', 'sh-cap-head');
  const track = h('span', 'sh-cap-track', row.trackingNo || 'takip numarası yok');
  head.append(track);
  if (row.carrierLabel) head.append(badge(row.carrierLabel, 'info'));
  if (row.trackingNo) {
    head.append(button('Kopyala', {
      title: 'Takip numarasını panoya kopyalar',
      onClick: () => copyText(row.trackingNo),
    }));
  }
  body.append(head);

  // ── TAKİP BAĞLANTISI. Veri hep vardı (`by_order` → `trackingUrl`) ama bu
  // kart onu hiç okumuyordu: personel numarayı kopyalayıp firmanın sitesini
  // kendisi buluyordu. Bağlantı YOKSA SESSİZ KALINMAZ — aynı boşluk
  // "kargoya verildi" SMS'ini de düşürüyor (zorunlu değişken), ve sebebi
  // ancak müşteri arayınca anlaşılıyordu.
  if (row.trackingUrl) {
    const link = h('a', 'sh-cap-link', 'Kargomu taşıyıcının sitesinde takip et');
    link.href = row.trackingUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    body.append(link);
  } else if (row.trackingNo) {
    body.append(h('div', 'sh-cap-nolink',
      'Taşıyıcı henüz takip bağlantısı vermedi. Bağlantı boşken müşteriye '
      + '“kargoya verildi” SMS’i de gitmez — mesajın zorunlu değişkenlerinden biri.'));
  }

  // ── mutlu yoldan çıktıysa ÖNCE o söylenir; şerit yanıltıcı olurdu
  const sapma = OFF_TRACK[row.status];
  if (sapma) {
    body.append(alertBox(
      `${sapma[0]}${row.error ? ` — ${row.error}` : ''}`,
      sapma[1] === 'dim' ? 'warn' : sapma[1],
    ));
  } else {
    const index = JOURNEY.indexOf(row.status);
    body.append(stepper(
      JOURNEY.map((code) => ({ label: STATUS_LABELS[code] || code })),
      index,
    ));
  }

  // ── tek satırlık olgu şeridi. Bilinmeyen alan UYDURULMAZ, hiç yazılmaz.
  const facts = [];
  if (row.city) facts.push(`${row.city}${row.district ? ` · ${row.district}` : ''}`);
  if (row.createdAt) facts.push(`açıldı ${stampIso(row.createdAt)}`);
  if (row.deliveredAt) facts.push(`teslim ${stampIso(row.deliveredAt)}`);
  else if (row.lastMoveAt) facts.push(`son hareket ${stampIso(row.lastMoveAt)}`);
  if (row.packages > 1) facts.push(`${row.packages} paket`);
  if (facts.length) body.append(h('div', 'sh-cap-facts', facts.join('  ·  ')));

  // ── dikkat çeken yanlar zaten YAZIYLA geliyor (shipping.flags)
  if (row.flags?.length) {
    const chips = h('div', 'sh-cap-flags');
    for (const flagText of row.flags) chips.append(badge(flagText, 'warn'));
    body.append(chips);
  }

  // ── TEK EYLEM: buraya gitmek. Kargoya verme, iptal, etiket YOK.
  body.append(h('div', 'sh-cap-go', ''));
  body.lastChild.append(button('Kargo Yönetimi’nde aç', {
    title: 'Bu siparişi kargo ekranında açar — gönderme, iptal ve etiket işlemleri orada',
    onClick: () => ctx.open?.('store_shipping', { orderId: row.orderId, shipmentId: row.id }),
  }));

  return card('', body);
}
