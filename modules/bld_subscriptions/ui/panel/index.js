// Abonelikler paneli — talepten aktif aboneliğe, sözleşmeden dönem borcuna.
//
// NE YAPAR: siteden gelen teklif taleplerinin kuyruğu (maskeli liste, maskesiz
// kayıt, iç not, aboneliğe çevirme); abonelik kuralı (servis günleri, adet,
// anlaşılan birim fiyat, teslimat noktaları); durum eylemleri (aktifleştir,
// duraklat, devam ettir, iptal); servis takvimi ve üretim defteri; tek-gün
// istisnaları; elle üretim ve siparişi KDS'e erken düşürme; imzalı sözleşme
// gönderimi; 30 günlük peşin dönem borcu ve tahsilat kaydı.
//
// NE YAPMAZ:
//  · OTP AKIŞI KURMAZ. Sözleşme SMS'ini ve kod doğrulamasını BLD yürütür; bu
//    ekran yalnız TETİKLER (K3). Burada kod kutusu, "kodu doğrula" düğmesi ya
//    da SMS gövdesi YOKTUR ve olmayacaktır — ikinci bir OTP uygulaması, iki
//    yerde ayrışabilen bir güvenlik akışı demekti. Ekran müşterinin onayını
//    BEKLEDİĞİNİ söyler, onun yerine onaylamaz.
//  · ÜRETİM TAKVİMİNİ HESAPLAMAZ. Hangi günün sipariş üreteceğini sunucu
//    söyler (`upcomingServiceDays()` — gece işinin kullandığı metodun ta
//    kendisi). `service_days` listesinden takvim türetmek, kapalı günleri,
//    duraklamaları ve istisnaları ekranda TEKRAR uygulamak olurdu ve
//    ayrışmanın fark edileceği yer mutfak olurdu.
//  · GECİKMİŞ BORCU HESAPLAMAZ. `overdue` sunucudan gelir; saati kaymış bir
//    panelde borç bir gün erken kırmızıya dönerdi.
//  · MÜŞTERİ ARAMAZ. Müşteri kimliği elle yazılır: müşteri okumaları KVKK
//    gereği sunucuda tek tek denetleniyor (`customers.md` §9) ve bu ekrandan
//    açılan bir arama kutusu, denetim izini abonelik açan herkesin her
//    denemesiyle doldurup içindeki gerçek erişimi görünmez kılardı. Müşteri
//    kartı `bld_customers` ekranının işi.
//  · KAYIT SİLMEZ. Tek istisna tek-gün istisnasıdır ve sözleşme orada bilerek
//    gerçek silme diyor: istisna bir belge değil, bir KURALDIR.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · TEK EYLEM, TEK EV. Sözleşme gönderme/iptal YALNIZ "Sözleşmeler"
//    sekmesinde; dönem borcu ve tahsilat YALNIZ "Ödemeler" sekmesinde durur.
//    Abonelik çekmecesi ikisini de OKUR ama düğmesini açmaz — aynı işi iki
//    yerden yapabilmek, "hangisinden yaptım" sorusunu doğurur.
//  · ABONELİK `pending` DOĞAR. Fiyatı ve imzalı sözleşmesi tamamlanmadan
//    aktifleşmez; şerit hangi adımda durduğunu ve SIRADAKİ ADIMI yazar.
//  · KURAL DEĞİŞİKLİĞİ ÜRETİLMİŞ SİPARİŞİ DEĞİŞTİRMEZ. Sunucu bunu `warnings`
//    ile söyler ve ekran o uyarıyı OLDUĞU GİBİ yazar; yutulsaydı yönetici
//    yarının siparişini değiştirdiğini sanırdı.
//  · DURAKLATMA İPTAL DEĞİLDİR. Aralık zorunludur; süresiz durdurmak
//    isteyenin yolu iptaldir ve iptalin geri dönüşü yoktur.
//  · ÖDEMEYİ GERİ ALMA UCU YOK. Yanlış işaretlenen tahsilat yeni bir dönem
//    kaydıyla düzeltilir; ekran bunu tahsilat kutusunda yazar.
//  · PARA HER YERDE KURUŞ. Gösterimde `money()`, girişte `parseMoney`;
//    hiçbir yerde bölme yok.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_subscriptions/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_subscriptions/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, blockedButton, confirmWithPin, confirmWithReason, h, loadStyles, money, num,
  stampIso, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { stepper, timeline } from '../../ui-kit/flow.js';
import { formGrid } from '../../ui-kit/form.js';

const BASE = '/api/bld_subscriptions';

/** Gerekçe sınırları — sunucu da denetliyor (`00-genel.md` §3). 160 DEĞİL: o
 *  daralma sipariş revizyonu sütunundan geliyor ve buraya uygulanmaz. */
const REASON_MIN = 10;
const REASON_MAX = 500;

// ------------------------------------------------------------------ durum

const EMPTY_STATE = {
  tab: 'requests',
  //: Şube listesi — abonelik formundaki seçicinin kaynağı. BOŞ KALIRSA FORM
  //: AÇILMAZ: `location_id` sunucuda zorunlu ve sıfır göndermek, panelin uzun
  //: süre yaptığı ve her denemeyi 422'ye çeviren şeydi.
  locations: { items: [], defaultId: 0, loaded: false, error: '' },
  // BAĞLANTI: `ok:true` ile gelen `connected:false` (K7). Ayrı tutulur çünkü
  // "kayıt yok" ile "sunucuya ulaşılamıyor" aynı ekranda aynı görünmemeli.
  // `missing` üçüncü bir hâldir: uç sunucuya HENÜZ DAĞITILMADI ve bu arıza
  // değil; kırmızı bir kutu, personelin olmayan bir sorunu bildirmesi olurdu.
  link: { connected: true, error: '', missing: false },
  contract: null,
  prefs: null,
  limits: null,
  error: '',
  requests: { rows: [], meta: {}, page: 1, loaded: false, error: '' },
  pending: { rows: [], loaded: false },
  list: { rows: [], meta: {}, page: 1, loaded: false, error: '', status: 'active' },
  picker: { rows: [], meta: {}, q: '', page: 1, loaded: false, selected: null },
  contracts: { items: [], openId: 0, signedId: 0, loaded: false, error: '' },
  payments: { items: [], meta: {}, loaded: false, error: '' },
  drawer: null,
  request: null,
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
/** Sekme değişince yok edilecek kit bileşenleri (takvim/global dinleyici tutanlar). */
let disposables = [];

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
 * Sessizce boş liste çizmek, yöneticiye "hiç abonelik yok" dedirtirdi.
 * `missing_endpoint` ayrı taşınır: sunucu tarafı paralel yazılıyor ve
 * dağıtılmamış bir uç BEKLENEN durumdur.
 *
 * @returns {boolean} veri güvenilir mi
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = {
      connected: false,
      error: payload.error || 'BLD sunucusuna ulaşılamıyor.',
      missing: Boolean(payload.missing_endpoint),
    };
    return false;
  }
  // `connected` HİÇ YOKSA (ör. ağa çıkmayan `/overview`) durum DEĞİŞMEZ:
  // taşımayan bir yanıt, bilinen bir kopukluğu "düzeldi" saymamalı.
  if (payload && payload.connected === true) {
    state.link = { connected: true, error: '', missing: false };
  }
  return true;
}

/** Bağlantı kopukken çizilen uyarı; bağlantı varken `null`. */
function linkAlert(what = 'Liste') {
  if (state.link.connected) return null;
  if (state.link.missing) {
    // ZARİFÇE BOZULMA: uç henüz yok, ekran ayakta. Sarı, kırmızı değil.
    return alertBox(
      `Bu bölümün sunucu ucu HENÜZ DAĞITILMADI (${state.link.error}). Sunucu `
      + 'tarafı paralel yazılıyor; ekranın geri kalanı çalışmaya devam eder ve '
      + 'uç yayına alındığında burası kendiliğinden dolar. Yeniden denemenin '
      + 'faydası yok.', 'warn');
  }
  return alertBox(
    `BLD sunucusuna ULAŞILAMIYOR — ${state.link.error} ${what} okunamadı. `
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

/** Aynı anda tek yazma. İki kez tıklanan bir tahsilat, iki denetim satırı olurdu. */
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
 * Gerekçeli onay. Kitin kutusu 255 karaktere kadar yazdırıyor; sunucu sınırı
 * 500. Kısa taraf burada yakalanır ve kullanıcı 422 yerine kendi cümlesini
 * görür.
 */
async function askReason({ title, description, confirmLabel, danger = true }) {
  const reason = await confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter, denetim kaydına yazılır)`,
  });
  if (!reason) return null;
  if (reason.length > REASON_MAX) {
    toast(`Gerekçe en çok ${REASON_MAX} karakter olabilir; ${reason.length} yazıldı.`,
      'bad');
    return null;
  }
  return reason;
}

/** Kuruş → "12.345,67 ₺". Bölme YOK; biçimlendirme kitin işi. */
function priceText(kurus) {
  return kurus === null || kurus === undefined ? '—' : money(kurus);
}

/**
 * Yazma sonucunu bildirir. KURU PROVA "yapıldı" DEMEZ: bir kurulum provayı
 * ayardan geri açarsa ekran yanlış bilgi vermemeli.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * UYARILARI DA BURADAN GÖSTERİYOR — VE BU, KAPININ TEK YERE TAŞINMASIDIR (I3).
 *
 * `showWarnings()` ayrı bir çağrıydı ve on üç yazma akışının YALNIZ DÖRDÜNDE
 * çağrılıyordu. Kalan dokuzunda sunucunun söyledikleri sessizce düşüyordu:
 * "fatura kesilmedi", "notunuz kaydedilmedi", "iptal sonrasına üretilmiş
 * siparişler duruyor" gibi cümleler hiç görünmüyordu. Yeni bir yazma akışı
 * eklendiğinde de listeye eklenmesi gereken ikinci bir çağrıydı — yani
 * unutulmaya açıktı.
 *
 * Artık her yazma sonucu buradan geçiyor ve uyarılar oradan çıkıyor. Uyarılar
 * KURU PROVADA DA gösteriliyor: prova "bu yazma şu uyarıyı üretecek" demektir
 * ve tam da o yüzden yapılıyor.
 * ═══════════════════════════════════════════════════════════════════════════
 */
function announce(result, message) {
  showWarnings(result);
  if (result?.dry_run) {
    toast('KURU PROVA: sunucu denetimleri koştu ama HİÇBİR ŞEY YAZILMADI.', 'warn');
    return;
  }
  toast(message, 'good');
}

/**
 * Sunucu uyarıları. YUTULMAZ: "kural değişti ama üretilmiş siparişler
 * etkilenmedi" cümlesini görmeyen yönetici, yarının siparişini değiştirdiğini
 * sanır ve mutfağa yanlış adet gider.
 */
const WARNING_TEXT = {
  generated_orders_unaffected: (warning) =>
    `Bu kural değişikliği ZATEN ÜRETİLMİŞ siparişleri DEĞİŞTİRMEDİ `
    + `(${(warning.dates || []).join(', ')} · sipariş `
    + `${(warning.order_ids || []).map((id) => `#${id}`).join(', ')}). `
    + 'Onları düzeltmek Sipariş Yönetimi ekranındaki revizyonun işidir.',
  generated_orders_in_range: (warning) =>
    `Duraklatma aralığında ZATEN ÜRETİLMİŞ siparişler var ve OTOMATİK İPTAL `
    + `EDİLMEDİ (${(warning.dates || []).join(', ')} · sipariş `
    + `${(warning.order_ids || []).map((id) => `#${id}`).join(', ')}). `
    + 'Her birine tek tek karar verin; iptalleri Sipariş Yönetimi ekranından yapılır.',
  /*
   * ─────────────────────────────────────────────────────────────────────
   * AŞAĞIDAKİLER SONRADAN EKLENDİ (I3) — SÖZLÜK BEŞ KODU TANIMIYORDU.
   *
   * Tanınmayan kod `Sunucu uyarısı: invoice_not_created` diye ham hâliyle
   * geçiyordu; yani yönetici ne olduğunu ne de ne yapması gerektiğini
   * öğreniyordu. En pahalısı `invoice_not_created` idi: fatura onay kutusu
   * işaretlenmiş, belge kesilmemiş ve ekran bunu İNGİLİZCE bir kodla
   * söylüyordu.
   * ─────────────────────────────────────────────────────────────────────
   */
  generated_orders_after_cancel: (warning) =>
    'İPTAL TARİHİNDEN SONRAYA ÜRETİLMİŞ siparişler var ve OTOMATİK '
    + `DÜŞÜRÜLMEDİ (${(warning.dates || []).join(', ')} · sipariş `
    + `${(warning.order_ids || []).map((id) => `#${id}`).join(', ')}). `
    + 'Abonelik iptal edildi ama o siparişler duruyor; düşürmek (ve iade) '
    + 'Sipariş Yönetimi ekranının ayrı yetkisini ister.',
  period_end_derived: (warning) =>
    `Yazdığınız dönem bitişi KULLANILMADI; sunucu ${warning.period_end || '—'} `
    + 'gününü yazdı. Bu, sunucudaki göçün henüz uygulanmadığını gösterir — '
    + 'dönem uzunluğu şimdilik 30 güne sabitleniyor.',
  due_date_derived: (warning) =>
    `Yazdığınız son ödeme günü KULLANILMADI; sunucu ${warning.due_date || '—'} `
    + '(dönem başı) yazdı. Sunucudaki göç uygulanınca ayrı bir ödeme günü '
    + 'girilebilecek.',
  note_not_stored: () =>
    'Yazdığınız NOT KAYDEDİLMEDİ: sunucudaki göç henüz uygulanmamış. Notu '
    + 'gerekçe alanına yazarsanız denetim izinde kalır.',
  invoice_not_created: (warning) =>
    'FATURA BELGESİ ÜRETİLMEDİ. Tahsilat kaydedildi ama belge kesilemedi'
    + `${warning.reason ? ` (${warning.reason})` : ''}. Belgeyi Fatura `
    + 'ekranından elle kesebilirsiniz; tahsilatı tekrar işaretlemeyin.',
  invoice_already_issued: (warning) =>
    `Bu dönemin belgesi ZATEN KESİLMİŞ (${warning.invoice_no || `#${warning.invoice_id}`}). `
    + 'Yenisi üretilmedi; ikinci bir belge aynı hizmeti iki kez faturalardı.',
  pause_scheduled: (warning) =>
    `Duraklatma İLERİ TARİHLİ (${warning.starts_on || '—'}): abonelik o güne `
    + 'kadar ÜRETMEYE DEVAM EDER ve durumu "Aktif" görünür. Aralıktaki günler '
    + 'yine de üretilmez.',
  netgsm_header_applies_next_request: () =>
    'Yeni gönderici başlığı bir sonraki gönderimden itibaren geçerli.',
};

function showWarnings(result) {
  for (const warning of result?.warnings || []) {
    const write = WARNING_TEXT[warning.code];
    toast(write ? write(warning) : `Sunucu uyarısı: ${warning.code}`, 'warn');
  }
}

/** Sekme yıkılırken kit bileşenlerini bırakır (kit kuralı 4). */
function track(component) {
  if (component?.destroy) disposables.push(component);
  return component;
}

function releaseAll() {
  for (const component of disposables) {
    try { component.destroy(); } catch { /* bırakma başarısızsa panel yine kapansın */ }
  }
  disposables = [];
}

// =================================================================== sözlükler

function options(list, { all = '' } = {}) {
  const items = (list || []).map((item) => ({ value: String(item.code), label: item.label }));
  return all ? [{ value: '', label: all }, ...items] : items;
}

function labelOf(list, code) {
  return (list || []).find((item) => String(item.code) === String(code))?.label || code || '—';
}

/**
 * Hafta günü seçici. `<select multiple>` KULLANILMAZ: beş günü seçmek için
 * Ctrl basılı tutmak gerekirdi ve seçili olmayan gün ile hiç seçilmemiş liste
 * ekranda aynı görünürdü. Çipler seçili olanı YAZIYLA da söyler.
 */
function weekdayPicker(initial = []) {
  const chosen = new Set((initial || []).map(Number));
  const node = h('div', 'bsu-days');
  const summary = h('small', 'bsu-dim');
  const buttons = new Map();

  const paint = () => {
    for (const [day, chip] of buttons) chip.classList.toggle('on', chosen.has(day));
    const days = [...chosen].sort((a, b) => a - b);
    summary.textContent = days.length
      ? `Seçili: ${days.map((day) => labelOf(state.contract?.weekdays, day)).join(', ')}`
      : 'Hiç gün seçilmedi — günsüz bir kural hiçbir şey üretmez.';
  };

  for (const item of state.contract?.weekdays || []) {
    const chip = h('button', 'bsu-day', item.short);
    chip.type = 'button';
    chip.title = item.label;
    chip.addEventListener('click', () => {
      if (chosen.has(item.code)) chosen.delete(item.code); else chosen.add(item.code);
      paint();
    });
    buttons.set(item.code, chip);
    node.append(chip);
  }

  const wrap = h('div', 'bsu-stack');
  wrap.append(h('span', 'bsu-label', 'Servis günleri'), node, summary);
  paint();
  return { node: wrap, value: () => [...chosen].sort((a, b) => a - b) };
}

// =================================================================== 1. Talepler

const REQUEST_COLUMNS = [
  {
    key: 'created_at',
    label: 'Geldi',
    width: '150px',
    sortable: true,
    cell: (row) => {
      const node = h('span', undefined, stampIso(row.created_at));
      node.title = row.created_at;
      return node;
    },
  },
  {
    key: 'organization',
    label: 'Kurum / kişi',
    width: 'minmax(0, 1.6fr)',
    sortable: true,
    cell: (row) => {
      const box = h('div', 'bsu-cell');
      box.append(h('b', undefined, row.organization || row.full_name || '—'));
      // MASKE SUNUCUDA UYGULANIR ve burada AÇILMAZ; arayacak kişi kaydı açar.
      box.append(h('small', 'bsu-dim', `${row.full_name} · ${row.telephone || '—'}`));
      return box;
    },
  },
  { key: 'location', label: 'Yer', width: 'minmax(0, 1fr)' },
  {
    key: 'headcount',
    label: 'Kişi',
    width: '80px',
    align: 'num',
    sortable: true,
    cell: (row) => h('span', undefined, num(row.headcount || 0)),
  },
  { key: 'start_date', label: 'Başlangıç', width: '110px', sortable: true },
  {
    key: 'status',
    label: 'Durum',
    width: '130px',
    cell: (row) => {
      const box = h('div', 'bsu-cell');
      box.append(badge(row.status_label, row.status_tone));
      if (row.converted) {
        box.append(h('small', 'bsu-dim', `Abonelik #${row.converted_subscription_id}`));
      }
      return box;
    },
  },
];

function showRequests() {
  nodes.body.replaceChildren();
  const wrap = h('div', 'bsu-stack');

  const filters = track(filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '240px', placeholder: 'Kurum, ad, telefon' },
      { kind: 'select', key: 'status', label: 'Durum',
        options: options(state.contract?.request_statuses, { all: 'Hepsi' }) },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // `dateRange` kitin kendi takvimini kullanır.
      { kind: 'dateRange', key: 'range', label: 'Geliş', start: '', end: '' },
    ],
    onChange: () => { state.requests.page = 1; loadRequests().then(paintRequests); },
    actions: [button('Yenile', { onClick: () => loadRequests().then(paintRequests) })],
  }));
  nodes.requestFilters = filters;
  wrap.append(filters.node);

  nodes.requestSlot = h('div');
  wrap.append(nodes.requestSlot);

  nodes.requestPager = pager({
    total: 0, page: 1, size: state.prefs?.page_size || 25,
    onChange: ({ page }) => { state.requests.page = page; loadRequests().then(paintRequests); },
  });
  wrap.append(nodes.requestPager.node);

  // AKIŞIN İKİNCİ YARISI AYNI SEKMEDE: talep aboneliğe çevrildiğinde kayıt
  // `pending` doğar ve fiyat/sözleşme bekler. Onu başka bir sekmeye koymak,
  // "çevirdim, sonra ne oldu" sorusunu ekranda cevapsız bırakırdı.
  nodes.pendingSlot = h('div');
  wrap.append(nodes.pendingSlot);

  nodes.body.append(wrap);
  paintRequests();
  paintPending();
}

async function loadRequests() {
  const values = nodes.requestFilters?.values() || {};
  const range = values.range || {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.status) params.set('status', values.status);
  if (range.start) params.set('date_from', range.start);
  if (range.end) params.set('date_to', range.end);
  params.set('page', String(state.requests.page));
  params.set('per_page', String(state.prefs?.page_size || 25));

  try {
    const payload = await call(`${BASE}/requests?${params}`);
    if (!linkOk(payload)) {
      state.requests.error = payload.error || '';
      return;
    }
    state.requests.rows = payload.items || [];
    state.requests.meta = payload.meta || {};
    state.requests.loaded = true;
    state.requests.error = '';
  } catch (failure) {
    state.requests.error = failure.message;
  }
}

function paintRequests() {
  if (!nodes.requestSlot) return;
  nodes.requestSlot.replaceChildren();

  const warning = linkAlert('Talep listesi');
  if (warning) nodes.requestSlot.append(warning);
  if (state.requests.error && state.link.connected) {
    nodes.requestSlot.append(alertBox(state.requests.error, 'bad'));
  }
  if (!state.requests.loaded && !state.requests.error) {
    nodes.requestSlot.append(skeletonRows(6, 6));
    return;
  }

  const table = dataTable({
    columns: REQUEST_COLUMNS,
    rows: state.requests.rows,
    empty: emptyState({
      title: 'Bu süzgeçe uyan talep yok',
      text: 'Siteden gelen "Teklif Al" kayıtları burada birikir. Süzgeci '
        + 'genişletin ya da tarih aralığını kaldırın.',
    }),
    onRow: (row) => openRequest(row),
  });
  nodes.requestSlot.append(table.node);
  nodes.requestPager.update({
    total: Number(state.requests.meta.total || 0),
    page: Number(state.requests.meta.page || state.requests.page),
    size: Number(state.requests.meta.per_page || 25),
  });
  nodes.status.set(statusText(), !state.link.connected);
}

/**
 * Şube listesi — abonelik formundaki seçicinin kaynağı.
 *
 * KİMLİK EKRANA GÖMÜLMEZ: kurulumdan kuruluma değişir ve yanlış bir sabit
 * siparişleri başka bir mutfağa yollar. Liste okunamazsa form AÇILMAZ ve
 * sebebi yazılır; sessizce sıfır göndermek, her denemeyi 422'ye çeviren eski
 * arızayı geri getirmek olurdu.
 */
async function loadLocations() {
  try {
    const payload = await call(`${BASE}/locations`);
    state.locations.items = payload?.items || [];
    state.locations.defaultId = Number(payload?.default_location_id) || 0;
    state.locations.error = payload?.connected === false ? (payload.error || '') : '';
  } catch (failure) {
    state.locations.items = [];
    state.locations.error = failure.message;
  }
  state.locations.loaded = true;
}

/** Şube seçeneği listesi — kapalı vitrinler işaretli ama LİSTEDE. */
function locationOptions() {
  return state.locations.items.map((row) => ({
    value: String(row.id),
    label: row.enabled ? row.name : `${row.name} (kapalı)`,
  }));
}

async function loadPending() {
  try {
    const payload = await call(`${BASE}/subscriptions?status=pending&per_page=100`);
    if (!linkOk(payload)) return;
    state.pending.rows = payload.items || [];
    state.pending.loaded = true;
  } catch {
    // Bekleyen abonelikler okunamadı: talep kuyruğu yine çizilir (K7).
    state.pending.loaded = false;
  }
}

function paintPending() {
  if (!nodes.pendingSlot) return;
  nodes.pendingSlot.replaceChildren();
  const box = h('div', 'bsu-stack');

  box.append(hintBox(
    'Aşağıdakiler AÇILMIŞ ama HENÜZ AKTİF OLMAYAN aboneliklerdir. Abonelik '
    + 'bilerek `pending` doğar: fiyatı ve İMZALI SÖZLEŞMESİ tamamlanmadan '
    + 'sipariş üretmemeli. Her satır akışta nerede durduğunu ve sıradaki adımı '
    + 'yazar; OTP adımı SUNUCUDA yürür ve buradan tetiklenmez.'));

  if (!state.pending.loaded) {
    box.append(skeletonRows(3, 4));
  } else if (state.pending.rows.length === 0) {
    box.append(emptyState({ title: 'Fiyat ya da sözleşme bekleyen abonelik yok' }));
  } else {
    const table = dataTable({
      columns: [
        { key: 'customer_label', label: 'Müşteri', width: 'minmax(0, 1.4fr)' },
        { key: 'start_date', label: 'Başlangıç', width: '110px' },
        { key: 'service_days_label', label: 'Günler', width: 'minmax(0, 1fr)' },
        {
          key: 'agreed_unit_price_kurus',
          label: 'Birim fiyat',
          width: '130px',
          align: 'num',
          cell: (row) => (row.needs_price
            ? badge('Fiyat girilmedi', 'warn')
            : h('span', undefined, priceText(row.agreed_unit_price_kurus))),
        },
        {
          key: 'contract_status',
          label: 'Sözleşme',
          width: '140px',
          cell: (row) => badge(row.contract_status_label, row.contract_status_tone),
        },
      ],
      rows: state.pending.rows,
      dense: true,
      onRow: (row) => openSubscription(row),
    });
    box.append(table.node);
  }
  nodes.pendingSlot.append(card('Fiyat ve sözleşme bekleyenler', box));
}

// ------------------------------------------------------------ talep çekmecesi

async function openRequest(row) {
  state.request = { id: row.id, row, detail: null, error: '' };
  const box = drawer(nodes.root, {
    title: row.organization || row.full_name || `Talep #${row.id}`,
    subtitle: `Geldi: ${stampIso(row.created_at)}`,
    onClose: () => { state.request = null; nodes.requestDrawer = null; },
  });
  nodes.requestDrawer = box;
  box.body.append(skeletonRows(5, 2));

  try {
    const payload = await call(`${BASE}/requests/${row.id}`);
    linkOk(payload);
    state.request.detail = payload.request || null;
  } catch (failure) {
    state.request.error = failure.message;
  }
  paintRequestDrawer();
}

function paintRequestDrawer() {
  if (!nodes.requestDrawer || !state.request) return;
  const body = nodes.requestDrawer.body;
  body.replaceChildren();

  if (state.request.error) {
    body.append(alertBox(state.request.error, 'bad'));
    return;
  }
  const record = state.request.detail;
  if (!record) { body.append(skeletonRows(5, 2)); return; }

  body.append(requestSummaryCard(record));
  body.append(requestNoteCard(record));
  body.append(convertCard(record));
}

function requestSummaryCard(record) {
  const box = h('div', 'bsu-stack');
  // KVKK ONAYI HER ZAMAN DOLUDUR (onaysız kayıt hiç oluşmaz). Boşsa ekran
  // bunu SÖYLER; sessizce "—" yazmak olağan olmayanı olağan göstermek olurdu.
  if (record.kvkk_missing) {
    box.append(alertBox('Bu kayıtta KVKK onay damgası YOK. Sözleşmeye göre '
      + 'onaysız talep hiç oluşmaz; kaydı işleme almadan önce nereden geldiğini '
      + 'doğrulayın.', 'bad'));
  } else {
    box.append(h('div', 'bsu-dim', `KVKK onayı: ${stampIso(record.kvkk_accepted_at)}`));
  }

  for (const [label, value] of [
    ['Ad soyad', record.full_name],
    ['Telefon', record.telephone],
    ['E-posta', record.email],
    ['Kurum', record.organization],
    ['Hizmet türü', record.service_type],
    ['Kişi sayısı', num(record.headcount || 0)],
    ['Sıklık', record.frequency],
    ['İstenen başlangıç', record.start_date],
    ['Yer', record.location],
    ['Menü tercihi', record.menu_preference],
    ['Mutfak notu', record.kitchen_note],
    ['Mesaj', record.message],
  ]) {
    if (!value) continue;
    const line = h('div', 'bsu-kv');
    line.append(h('span', 'bsu-dim', label), h('span', undefined, String(value)));
    box.append(line);
  }
  box.append(hintBox('Ziyaretçinin yazdığı içerik DEĞİŞTİRİLEMEZ. Bir kaydın '
    + 'içeriğini düzeltebilen panel, o kaydın delil değerini yok eder; '
    + 'yazılabilen yalnız durum ve iç nottur.'));
  return card('Talep', box);
}

function requestNoteCard(record) {
  const box = h('div', 'bsu-stack');
  const form = track(formGrid({
    fields: [
      { key: 'status', label: 'Durum', type: 'select',
        options: options(state.contract?.request_statuses) },
      { key: 'admin_note', label: 'İç not', type: 'textarea', wide: true,
        maxLength: 2000,
        hint: 'Yalnız personel görür; müşteriye gitmez.' },
    ],
    value: { status: record.status, admin_note: record.admin_note || '' },
  }));
  box.append(form.node);

  const row = h('div', 'bsu-actions');
  row.append(writeButton('Kaydet', {
    variant: 'primary',
    onClick: () => submitRequestPatch(form),
  }));
  box.append(row);
  return card('Durum ve iç not', box);
}

async function submitRequestPatch(form) {
  const draft = form.draft();
  const reason = await askReason({
    title: 'Talep kaydını güncelle',
    description: 'Durum ve iç not yazılıyor. Ziyaretçinin yazdığı içerik '
      + 'değişmez. Gerekçe denetim kaydına düşer.',
    confirmLabel: 'Kaydet',
    danger: false,
  });
  if (!reason) return;

  await withBusy('Talep güncelleniyor…', async () => {
    const result = await call(`${BASE}/requests/${state.request.id}`, {
      method: 'PATCH',
      body: { reason, status: draft.status, admin_note: draft.admin_note || '' },
    });
    announce(result, 'Talep güncellendi.');
    state.request.detail = result.request || state.request.detail;
    paintRequestDrawer();
    await loadRequests();
    paintRequests();
  });
}

/**
 * TALEBİ ABONELİĞE ÇEVİR. Talep SİLİNMEZ: `kapandi` olur ve
 * `converted_subscription_id` dolar. Abonelik `pending` doğar.
 *
 * MÜŞTERİ KİMLİĞİ ELLE YAZILIR ve bu uç MÜŞTERİ YARATMAZ: hesap açmak parola
 * ve e-posta doğrulaması gerektirir, ikisi de bu sözleşmenin dışında. Burada
 * bir müşteri arama kutusu açmak, KVKK gereği tek tek denetlenen müşteri
 * okumalarını abonelik açan herkesin her denemesiyle çoğaltırdı.
 */
function convertCard(record) {
  const box = h('div', 'bsu-stack');
  if (record.converted) {
    box.append(alertBox(
      `Bu talep zaten abonelik #${record.converted_subscription_id} olarak `
      + 'açılmış. İkinci kez çevirmek sunucuda reddedilir; aboneliği listeden '
      + 'açın.', 'info'));
    return card('Aboneliğe çevir', box);
  }

  box.append(hintBox(
    'MÜŞTERİ KAYDI ÖNCE AÇILMIŞ OLMALI. Bu uç müşteri yaratmaz — hesap açmak '
    + 'parola ve e-posta doğrulaması ister. Kimliği TastyIgniter yönetiminden '
    + 'ya da Müşteriler ekranından öğrenip buraya yazın.'));

  /*
   * ŞUBE LİSTESİ OKUNAMADIYSA FORM HİÇ ÇİZİLMEZ (I1).
   *
   * `location_id` sunucuda zorunlu. Seçenek yokken formu açmak, kullanıcıya
   * dolduracağı ama HER SEFERİNDE 422 alacağı bir form göstermek olurdu —
   * panelin uzun süre yaptığı tam olarak buydu.
   */
  if (!state.locations.items.length) {
    box.append(alertBox(
      'Şube listesi okunamadı; abonelik açılamaz. BLD zorunlu olarak bir şube '
      + `istiyor.${state.locations.error ? ` Sebep: ${state.locations.error}` : ''} `
      + 'Bağlantı geri geldiğinde [Yenile] ile tazeleyin.', 'bad'));
    return card('Aboneliğe çevir', box);
  }

  const days = weekdayPicker([1, 2, 3, 4, 5]);
  const form = track(formGrid({
    fields: [
      { key: 'customer_id', label: 'Müşteri kimliği', type: 'number', required: true,
        hint: 'Sayı. Yanlış kimlik başka bir kuruma abonelik açar.' },
      /*
       * ŞUBE SEÇİCİ — BU ALAN UZUN SÜRE HİÇ YOKTU (I1).
       *
       * Sunucu `location_id` alanını zorunlu tutuyor; panel sabit sıfır
       * gönderiyor ve alan gövdeye hiç konmuyordu. Sonuç: HER dönüşüm
       * denemesi 422 alıyor ve abonelik Kontrol Merkezi'nden HİÇ
       * AÇILAMIYORDU.
       */
      { key: 'location_id', label: 'Şube', type: 'select', required: true,
        options: locationOptions(),
        hint: 'Siparişler bu şubenin mutfağında üretilir. Yanlış şube, yemeğin '
          + 'başka bir mutfaktan çıkması demektir.' },
      { key: 'start_date', label: 'Başlangıç günü', type: 'date', required: true },
      { key: 'end_date', label: 'Bitiş günü (boş = süresiz)', type: 'date' },
      { key: 'delivery_type', label: 'Teslimat', type: 'select',
        options: options(state.contract?.delivery_types) },
      { key: 'delivery_time_from', label: 'Teslim saati (başlangıç)', type: 'text',
        placeholder: '11:30', maxLength: 5 },
      { key: 'delivery_time_to', label: 'Teslim saati (bitiş)', type: 'text',
        placeholder: '12:30', maxLength: 5 },
      { key: 'menu_mode', label: 'Menü kipi', type: 'select',
        options: options(state.contract?.menu_modes),
        hint: 'Günün menüsü seçiliyken kalem listesi gönderilemez; menü o günün '
          + 'yayınlanmış menüsünden gelir.' },
      { key: 'default_quantity', label: 'Günlük porsiyon', type: 'number',
        required: true },
      { key: 'agreed_unit_price_kurus', label: 'Anlaşılan birim fiyat', type: 'money',
        hint: 'Kuruş olarak saklanır. Boş bırakılabilir ama FİYATSIZ ABONELİK '
          + 'AKTİFLEŞTİRİLEMEZ.' },
      { key: 'address_id', label: 'Teslimat adresi kimliği', type: 'number',
        hint: 'Müşterinin adres defterinden. Adrese teslimde zorunlu.' },
      { key: 'point_quantity', label: 'Bu adrese porsiyon', type: 'number' },
    ],
    value: {
      customer_id: null,
      // Tek vitrinli kurulumda fazladan bir tıklamayı kaldırır; birden çok
      // şube varsa yönetici yine bilinçli olarak seçer.
      location_id: state.locations.defaultId
        ? String(state.locations.defaultId) : '',
      start_date: record.start_date || todayIso(1),
      end_date: '',
      delivery_type: 'delivery',
      delivery_time_from: '',
      delivery_time_to: '',
      menu_mode: 'daily_menu',
      default_quantity: record.headcount || null,
      agreed_unit_price_kurus: null,
      address_id: null,
      point_quantity: record.headcount || null,
    },
  }));
  box.append(form.node, days.node);

  box.append(hintBox(
    'ÖDEME KİPİ SORULMAZ: tek geçerli değer 30 günlük peşindir. Cari hesap '
    + 'tamamen kaldırıldı ve başka bir değer sunucuya hiç gönderilmez.'));

  const row = h('div', 'bsu-actions');
  // KURU PROVANIN ASIL FAYDASI BURADA: sunucu kuralın gerçekten hangi günleri
  // üreteceğini (`first_service_dates`) kaydetmeden gösterir.
  row.append(writeButton('Önce prova et', {
    title: 'Hiçbir şey yazmadan sunucunun ne yapacağını gösterir',
    onClick: () => submitConvert(form, days, true),
  }));
  row.append(writeButton('Aboneliğe çevir', {
    variant: 'primary',
    onClick: () => submitConvert(form, days, false),
  }));
  box.append(row);

  nodes.convertResult = h('div', 'bsu-stack');
  box.append(nodes.convertResult);
  return card('Aboneliğe çevir', box);
}

function subscriptionBlock(form, days) {
  const draft = form.draft();
  const block = {
    // ŞUBE GÖVDEYE KOŞULSUZ KONUR: sunucu alanı `required` tutuyor ve
    // koşullu bir anahtar, eksikliği 422'ye çeviriyordu.
    location_id: Number(draft.location_id) || 0,
    start_date: draft.start_date || '',
    end_date: draft.end_date || '',
    delivery_type: draft.delivery_type || 'delivery',
    delivery_time_from: draft.delivery_time_from || '',
    delivery_time_to: draft.delivery_time_to || '',
    service_days: days.value(),
    menu_mode: draft.menu_mode || 'daily_menu',
    default_quantity: Number(draft.default_quantity) || 0,
    agreed_unit_price_kurus: draft.agreed_unit_price_kurus ?? null,
    lines: [],
    delivery_points: [],
  };
  if (draft.address_id) {
    block.delivery_points = [{
      address_id: Number(draft.address_id),
      quantity: Number(draft.point_quantity) || block.default_quantity,
      note: null,
    }];
  }
  return { block, customerId: Number(draft.customer_id) || 0 };
}

async function submitConvert(form, days, dryRun) {
  const { block, customerId } = subscriptionBlock(form, days);
  const reason = await askReason({
    title: dryRun ? 'Dönüşümü prova et' : 'Talebi aboneliğe çevir',
    description: dryRun
      ? 'Sunucu denetimleri koşar ve kuralın ilk üç üretim gününü gösterir; '
        + 'HİÇBİR ŞEY YAZILMAZ.'
      : `Müşteri #${customerId} için abonelik açılacak ve talep "kapandı" olacak. `
        + 'Abonelik BEKLEYEN doğar: sözleşme imzalanmadan sipariş üretmez.',
    confirmLabel: dryRun ? 'Prova et' : 'Çevir',
    danger: false,
  });
  if (!reason) return;

  await withBusy(dryRun ? 'Prova koşuluyor…' : 'Abonelik açılıyor…', async () => {
    const result = await call(`${BASE}/requests/${state.request.id}/convert`, {
      method: 'POST',
      body: { reason, customer_id: customerId, subscription: block, dryRun },
    });
    paintConvertResult(result);
    announce(result, `Abonelik #${result.subscription_id} açıldı (bekliyor).`);
    if (!dryRun) {
      await Promise.all([loadRequests(), loadPending()]);
      paintRequests();
      paintPending();
      const payload = await call(`${BASE}/requests/${state.request.id}`);
      state.request.detail = payload.request;
      paintRequestDrawer();
    }
  });
}

/**
 * KURU PROVANIN ASIL FAYDASI: `first_service_dates`. Yönetici kuralın gerçekten
 * hangi günleri ürettiğini KAYDETMEDEN görür — servis günü seçimindeki bir
 * hata (ör. cumartesi işaretlemek) ancak burada ya da mutfakta fark edilirdi.
 */
function paintConvertResult(result) {
  if (!nodes.convertResult) return;
  nodes.convertResult.replaceChildren();
  if (!result?.would) return;

  const box = h('div', 'bsu-stack');
  box.append(h('b', undefined, 'Prova sonucu — hiçbir şey yazılmadı'));
  const dates = result.would.first_service_dates || [];
  box.append(h('div', undefined, `İlk üretim günleri: ${dates.join(', ') || '—'}`));
  if (result.would.monthly_estimate_kurus !== undefined) {
    box.append(h('div', undefined,
      `Aylık tahmini tutar: ${priceText(result.would.monthly_estimate_kurus)}`));
  }
  nodes.convertResult.append(card('Prova', box));
}

// ============================================== 2-3. Aktif / Duraklatılmış

const SUB_COLUMNS = [
  {
    key: 'customer_label',
    label: 'Müşteri',
    width: 'minmax(0, 1.6fr)',
    sortable: true,
    cell: (row) => {
      const box = h('div', 'bsu-cell');
      box.append(h('b', undefined, row.customer_label));
      box.append(h('small', 'bsu-dim', `#${row.id} · ${row.menu_mode_label}`));
      return box;
    },
  },
  {
    key: 'status',
    label: 'Durum',
    width: '120px',
    cell: (row) => badge(row.status_label, row.status_tone),
  },
  { key: 'service_days_label', label: 'Servis günleri', width: 'minmax(0, 1.1fr)' },
  {
    key: 'default_quantity',
    label: 'Porsiyon',
    width: '90px',
    align: 'num',
    sortable: true,
    cell: (row) => h('span', undefined, num(row.default_quantity)),
  },
  {
    key: 'agreed_unit_price_kurus',
    label: 'Birim fiyat',
    width: '120px',
    align: 'num',
    sortable: true,
    cell: (row) => (row.needs_price
      ? badge('Girilmedi', 'warn')
      : h('span', undefined, priceText(row.agreed_unit_price_kurus))),
  },
  {
    key: 'contract_status',
    label: 'Sözleşme',
    width: '130px',
    cell: (row) => badge(row.contract_status_label, row.contract_status_tone),
  },
  { key: 'next_service_date', label: 'Sonraki servis', width: '120px' },
  {
    key: 'unpaid_total_kurus',
    label: 'Ödenmemiş',
    width: '140px',
    align: 'num',
    sortable: true,
    cell: (row) => {
      if (!row.unpaid_periods) return h('span', 'bsu-dim', '—');
      const box = h('div', 'bsu-cell');
      // ÖDENMEMİŞ DÖNEM LİSTEDEN GELİR: satır başına ayrı bir ödeme çağrısı
      // dokuz abonelikte dokuz istek demekti.
      box.append(badge(`${num(row.unpaid_periods)} dönem`, 'bad'));
      box.append(h('small', 'bsu-dim', money(row.unpaid_total_kurus)));
      return box;
    },
  },
];

/**
 * Sekme açıklamaları — her durum kümesi için bir cümle.
 *
 * `waiting` ve `cancelled` SONRADAN EKLENDİ (I1/I3): ilki, sözleşme ya da
 * ödeme bekleyen abonelikleri hiçbir sekmede göstermeyen boşluğu kapatıyor;
 * ikincisi, iptal edilmiş aboneliklerin ekranın HİÇBİR YERİNDE
 * listelenmemesini. İkisi de "veri kayboldu" gibi görünen, aslında yalnız
 * sorgulanmayan kümelerdi.
 */
const LIST_HINTS = {
  active: 'Aktif abonelikler her gece kendi servis günleri için sipariş üretir ve '
    + 'siparişler mutfağa 07:00\'de düşer. Satıra tıklayınca kural, takvim, '
    + 'üretim defteri ve fiyat geçmişi açılır.',
  paused: 'Duraklatılmış abonelikler aralık boyunca sipariş ÜRETMEZ ama iptal '
    + 'edilmiş değildir: aralık bitince ya da "Devam ettir" ile aynı fiyatla '
    + 'sürer. Aralıktaki üretilmiş siparişler otomatik iptal edilmez.',
  waiting: 'FİYAT, SÖZLEŞME YA DA ÖDEME BEKLEYENLER. Sözleşmesi imzalanmış ama '
    + 'ilk dönem ödemesi gelmemiş abonelikler de buradadır ve sipariş '
    + 'ÜRETMEZLER. Bu küme uzun süre hiçbir sekmede görünmüyordu.',
  cancelled: 'İPTAL EDİLMİŞ abonelikler — SALT OKUNUR. Geri açılamazlar; '
    + 'yeniden başlatmak yeni bir abonelik açmaktır. Liste, "bu müşteri neden '
    + 'yemek almıyor" sorusunun cevabı için duruyor.',
};

/** Sekme → sunucuya gidecek durum süzgeci (virgüllü liste kabul edilir). */
const LIST_STATUSES = {
  active: 'active',
  paused: 'paused',
  // ÜÇ DURUM TEK SEKMEDE: yönetici için üçü de "henüz üretmiyor, birinin bir
  // şey yapması bekleniyor" demek. Ayrı sekmeler, çoğu gün boş üç sekme
  // olurdu.
  waiting: 'pending,awaiting_contract,awaiting_payment',
  cancelled: 'cancelled',
};

function showList(status) {
  state.list.status = status;
  state.list.loaded = false;
  nodes.body.replaceChildren();
  const wrap = h('div', 'bsu-stack');

  const filters = track(filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '240px', placeholder: 'Kurum, ad, telefon' },
      { kind: 'select', key: 'service_day', label: 'Servis günü',
        options: [{ value: '', label: 'Hepsi' },
          ...(state.contract?.weekdays || []).map((item) => ({
            value: String(item.code), label: item.label,
          }))] },
    ],
    onChange: () => { state.list.page = 1; loadList().then(paintList); },
    actions: [button('Yenile', { onClick: () => loadList().then(paintList) })],
  }));
  nodes.listFilters = filters;
  wrap.append(filters.node);

  wrap.append(hintBox(LIST_HINTS[status] || LIST_HINTS.active));

  nodes.listSlot = h('div');
  wrap.append(nodes.listSlot);
  nodes.listPager = pager({
    total: 0, page: 1, size: state.prefs?.page_size || 25,
    onChange: ({ page }) => { state.list.page = page; loadList().then(paintList); },
  });
  wrap.append(nodes.listPager.node);
  nodes.body.append(wrap);

  loadList().then(paintList);
}

async function loadList() {
  const values = nodes.listFilters?.values() || {};
  const params = new URLSearchParams();
  params.set('status', LIST_STATUSES[state.list.status] || state.list.status);
  if (values.q) params.set('q', values.q);
  if (values.service_day) params.set('service_day', values.service_day);
  params.set('page', String(state.list.page));
  params.set('per_page', String(state.prefs?.page_size || 25));

  try {
    const payload = await call(`${BASE}/subscriptions?${params}`);
    if (!linkOk(payload)) { state.list.error = payload.error || ''; return; }
    state.list.rows = payload.items || [];
    state.list.meta = payload.meta || {};
    state.list.loaded = true;
    state.list.error = '';
  } catch (failure) {
    state.list.error = failure.message;
  }
}

function paintList() {
  if (!nodes.listSlot) return;
  nodes.listSlot.replaceChildren();

  const warning = linkAlert('Abonelik listesi');
  if (warning) nodes.listSlot.append(warning);
  if (state.list.error && state.link.connected) {
    nodes.listSlot.append(alertBox(state.list.error, 'bad'));
  }
  if (!state.list.loaded && !state.list.error) {
    nodes.listSlot.append(skeletonRows(6, 8));
    return;
  }

  nodes.listSlot.append(dataTable({
    columns: SUB_COLUMNS,
    rows: state.list.rows,
    empty: emptyState({
      title: state.list.status === 'active'
        ? 'Aktif abonelik yok' : 'Duraklatılmış abonelik yok',
      text: 'Yeni abonelikler "Talepler" sekmesinde açılır ve sözleşme '
        + 'imzalanana kadar bekleyen olarak durur.',
    }),
    onRow: (row) => openSubscription(row),
  }));
  nodes.listPager.update({
    total: Number(state.list.meta.total || 0),
    page: Number(state.list.meta.page || state.list.page),
    size: Number(state.list.meta.per_page || 25),
  });
  nodes.status.set(statusText(), !state.link.connected);
}

// ========================================================= abonelik çekmecesi

async function openSubscription(row) {
  state.drawer = {
    id: row.id, row, detail: null, calendar: [], runs: [], audit: [],
    error: '', loaded: false,
  };
  const box = drawer(nodes.root, {
    title: row.customer_label || `Abonelik #${row.id}`,
    subtitle: `${row.status_label} · ${row.service_days_label}`,
    onClose: () => {
      state.drawer = null;
      // DOM'dan düşmüş panellere tutunulmaz: kapanan bir çekmecenin duraklatma
      // kutusuna referans tutmak, bir sonraki çekmecede `remove()` çağrısının
      // görünmeyen bir düğüme gitmesi olurdu.
      nodes.subDrawer = null;
      nodes.pausePanel = null;
      nodes.cancelPanel = null;
    },
  });
  nodes.subDrawer = box;
  box.body.append(skeletonRows(8, 2));

  await loadSubscription(row.id);
  paintSubscription();
}

async function loadSubscription(id) {
  try {
    const payload = await call(`${BASE}/subscriptions/${id}`);
    linkOk(payload);
    state.drawer.detail = payload.subscription || null;
    state.drawer.loaded = true;
  } catch (failure) {
    state.drawer.error = failure.message;
  }
  // Üç okuma AYRI AYRI karşılanır: takvim düşerse kural kartı yine çizilmeli
  // (K7). Boş bir takvim "üretim yok" DEMEK DEĞİLDİR ve bölümde yazılı.
  try {
    const payload = await call(`${BASE}/subscriptions/${id}/calendar`);
    state.drawer.calendar = payload.items || [];
    state.drawer.calendarOk = payload.connected !== false;
  } catch {
    state.drawer.calendarOk = false;
  }
  try {
    const payload = await call(`${BASE}/subscriptions/${id}/runs?per_page=50`);
    state.drawer.runs = payload.items || [];
    state.drawer.runsOk = payload.connected !== false;
  } catch {
    state.drawer.runsOk = false;
  }
  try {
    const payload = await call(
      // ÜÇ HEDEF BİRDEN (I3): fiyat taşıyan yazmalar `subscription`,
      // `quote_request` ve `subscription_payment` hedeflerine dağılıyor.
      // Yalnız `subscription` sorgulanınca kart İLK ANLAŞMAYI hiç
      // göstermiyordu — yani modülün en çok reklam edilen özelliği,
      // cevaplaması gereken ilk soruyu cevaplayamıyordu.
      `${BASE}/audit?target_type=subscription,subscription_payment`
      + `&target_id=${id}&limit=200`);
    state.drawer.audit = payload.items || [];
  } catch {
    state.drawer.audit = [];
  }
}

function paintSubscription() {
  if (!nodes.subDrawer || !state.drawer) return;
  const body = nodes.subDrawer.body;
  body.replaceChildren();

  if (state.drawer.error) {
    body.append(alertBox(state.drawer.error, 'bad'));
    body.append(button('Yeniden dene', {
      onClick: async () => { await loadSubscription(state.drawer.id); paintSubscription(); },
    }));
    return;
  }
  const record = state.drawer.detail;
  if (!record) { body.append(skeletonRows(8, 2)); return; }

  body.append(summaryCard(record));
  body.append(flowCard(record));
  body.append(ruleCard(record));
  body.append(statusCard(record));
  body.append(calendarCard(record));
  body.append(exceptionsCard(record));
  body.append(runsCard());
  body.append(priceHistoryCard());
  body.append(elsewhereCard(record));
}

function summaryCard(record) {
  const box = h('div', 'bsu-stack');
  box.append(kpiRow([
    { label: 'Birim fiyat', value: priceText(record.agreed_unit_price_kurus) },
    { label: 'Günlük porsiyon', value: num(record.default_quantity) },
    { label: 'Servis günleri', value: record.service_days_label },
    {
      label: 'Ödenmemiş',
      value: record.unpaid_periods
        ? `${num(record.unpaid_periods)} dönem` : 'yok',
      title: record.unpaid_periods ? money(record.unpaid_total_kurus) : '',
    },
  ]));
  const lines = [
    `Başlangıç: ${record.start_date || '—'}`,
    `Bitiş: ${record.end_date || 'süresiz'}`,
    `Teslimat: ${record.delivery_type_label}`
      + (record.delivery_time_from
        ? ` · ${record.delivery_time_from}–${record.delivery_time_to}` : ''),
    `Menü kipi: ${record.menu_mode_label}`,
    `Ödeme kipi: ${record.payment_mode_label}`,
    `Teslimat noktası: ${num((record.delivery_points || []).length)}`,
  ];
  for (const line of lines) box.append(h('div', 'bsu-dim', line));
  return card('Özet', box);
}

/**
 * AKIŞ ŞERİDİ. Adımların hepsi SUNUCUDAN GELEN alanlardan türetilir (fiyat,
 * sözleşme durumu, abonelik durumu); ekran hiçbirini tahmin etmez.
 *
 * OTP adımı bir BEKLEME adımıdır: müşteri kodu SUNUCUNUN sayfasında girer.
 * Buraya bir "kodu doğrula" düğmesi koymak, ikinci bir OTP uygulaması
 * yazmak olurdu (K3).
 */
function flowCard(record) {
  const flow = record.flow || { steps: [], index: -1, next_hint: '' };
  const box = h('div', 'bsu-stack');
  box.append(stepper(flow.steps.map((step) => ({ label: step.label })), flow.index));
  box.append(alertBox(`SIRADAKİ ADIM: ${flow.next_hint}`, 'info'));
  box.append(hintBox(
    'Sözleşme SMS\'i ve OTP onayı SUNUCU tarafı akışlardır. Kontrol Merkezi '
    + 'yalnız sözleşmeyi oluşturur ve bağlantıyı gönderir; kodu müşteri kendi '
    + 'telefonundan, sunucunun imza sayfasında girer. Bu ekranda kod kutusu '
    + 'yoktur ve olmayacaktır.'));
  return card('Akış', box);
}

function ruleCard(record) {
  const box = h('div', 'bsu-stack');
  if (record.status === 'cancelled') {
    box.append(alertBox('Bu abonelik İPTAL EDİLDİ. Kural değiştirilemez; iptal '
      + 'geri dönüşsüzdür ve yeniden başlatmak YENİ BİR ABONELİK açmaktır.',
    'bad'));
    return card('Kural', box);
  }

  const days = weekdayPicker(record.service_days);
  const form = track(formGrid({
    fields: [
      { key: 'end_date', label: 'Bitiş günü (boş = süresiz)', type: 'date' },
      { key: 'delivery_type', label: 'Teslimat', type: 'select',
        options: options(state.contract?.delivery_types) },
      { key: 'delivery_time_from', label: 'Teslim saati (başlangıç)', type: 'text',
        maxLength: 5, placeholder: '11:30' },
      { key: 'delivery_time_to', label: 'Teslim saati (bitiş)', type: 'text',
        maxLength: 5, placeholder: '12:30' },
      { key: 'menu_mode', label: 'Menü kipi', type: 'select',
        options: options(state.contract?.menu_modes) },
      { key: 'default_quantity', label: 'Günlük porsiyon', type: 'number' },
      { key: 'agreed_unit_price_kurus', label: 'Anlaşılan birim fiyat', type: 'money',
        hint: 'Kuruş. Değişiklik denetim izine AYRI BİR SÜTUN olarak yazılır: '
          + '"fiyatı kim, ne zaman, neden anlaştı" aşağıda okunur.' },
    ],
    value: {
      end_date: record.end_date || '',
      delivery_type: record.delivery_type,
      delivery_time_from: record.delivery_time_from,
      delivery_time_to: record.delivery_time_to,
      menu_mode: record.menu_mode,
      default_quantity: record.default_quantity,
      agreed_unit_price_kurus: record.agreed_unit_price_kurus,
    },
  }));
  box.append(form.node, days.node);
  box.append(hintBox(
    'MÜŞTERİ, KONUM VE BAŞLANGIÇ GÜNÜ değiştirilemez: müşteriyi değiştirmek '
    + 'yeni abonelik açmaktır. Kural değişikliği ZATEN ÜRETİLMİŞ SİPARİŞLERİ '
    + 'ETKİLEMEZ; sunucu hangi siparişlerin etkilenmediğini söyler ve o '
    + 'siparişleri düzeltmek Sipariş Yönetimi ekranındaki revizyonun işidir.'));

  const row = h('div', 'bsu-actions');
  row.append(writeButton('Kuralı kaydet', {
    variant: 'primary',
    onClick: () => submitRule(record, form, days),
  }));
  box.append(row);
  return card('Kural', box);
}

async function submitRule(record, form, days) {
  const draft = form.draft();
  const changes = {};
  const before = {
    end_date: record.end_date || '',
    delivery_type: record.delivery_type,
    delivery_time_from: record.delivery_time_from,
    delivery_time_to: record.delivery_time_to,
    menu_mode: record.menu_mode,
    default_quantity: record.default_quantity,
    agreed_unit_price_kurus: record.agreed_unit_price_kurus,
  };
  for (const key of Object.keys(before)) {
    if (String(draft[key] ?? '') !== String(before[key] ?? '')) changes[key] = draft[key];
  }
  const chosen = days.value();
  if (chosen.join(',') !== (record.service_days || []).join(',')) {
    changes.service_days = chosen;
  }
  if (Object.keys(changes).length === 0) {
    toast('Değişen bir alan yok; aynı kuralı yeniden yazmak denetim izine boş '
      + 'bir satır eklerdi.', 'warn');
    return;
  }

  const reason = await askReason({
    title: 'Abonelik kuralını güncelle',
    description: `Değişen alanlar: ${Object.keys(changes).join(', ')}. `
      + 'Kural değişikliği ZATEN ÜRETİLMİŞ siparişleri değiştirmez.',
    confirmLabel: 'Kaydet',
    danger: false,
  });
  if (!reason) return;

  await withBusy('Kural yazılıyor…', async () => {
    const result = await call(`${BASE}/subscriptions/${state.drawer.id}`, {
      method: 'PATCH',
      body: { reason, ...changes },
    });
    announce(result, 'Kural güncellendi.');
    await refreshDrawer();
  });
}

/**
 * DURUM EYLEMLERİ. Dört kapı, dört ayrı sonuç:
 *   aktifleştir — fiyat ve İMZALI SÖZLEŞME şartını sunucu denetler
 *   duraklat    — ARALIK ZORUNLU; süresiz durdurmak iptaldir
 *   devam ettir — duraklamayı bugün kapatır, satırı silmez
 *   iptal       — GERİ DÖNÜŞSÜZ; üretilmiş siparişlere dokunmaz
 */
function statusCard(record) {
  const box = h('div', 'bsu-stack');
  const row = h('div', 'bsu-actions');

  if (record.status === 'cancelled') {
    box.append(alertBox('İptal edilmiş bir aboneliğin durumu değiştirilemez.', 'dim'));
    return card('Durum', box);
  }

  if (record.status !== 'active') {
    const blocked = record.needs_price
      ? 'Anlaşılan birim fiyat girilmeden aktifleştirilemez: fiyatsız bir '
        + 'abonelik sipariş üretir ve o siparişin tutarı sıfır olurdu.'
      : record.contract_status !== 'signed'
        ? 'İmzalı sözleşme olmadan aktifleştirilemez. Sözleşmeler sekmesinden '
          + 'imza bağlantısını gönderin; onayı müşteri kendi telefonundan verir.'
        : '';
    row.append(writeButton('Aktifleştir', {
      variant: 'primary', blocked,
      onClick: () => submitStatus('activate', {
        title: 'Aboneliği aktifleştir',
        description: 'Aktif abonelik her gece servis günleri için sipariş '
          + 'üretmeye başlar ve siparişler mutfağa 07:00\'de düşer.',
        confirmLabel: 'Aktifleştir', danger: false,
      }),
    }));
  }

  if (record.status === 'active') {
    row.append(writeButton('Duraklat…', { onClick: () => openPause() }));
  }
  if (record.status === 'paused') {
    row.append(writeButton('Devam ettir', {
      onClick: () => submitStatus('resume', {
        title: 'Duraklamayı bitir',
        description: 'Açık duraklama BUGÜN itibarıyla kapanır ve abonelik aynı '
          + 'fiyatla üretmeye devam eder. Duraklama satırı SİLİNMEZ: "ne zaman '
          + 'duraklatıldı, ne zaman devam edildi" sorusunun cevabı kalmalı.',
        confirmLabel: 'Devam ettir', danger: false,
      }),
    }));
  }
  row.append(writeButton('İptal et…', {
    variant: 'danger',
    onClick: () => openCancel(record),
  }));
  box.append(row);

  if ((record.pauses || []).length) {
    box.append(timeline((record.pauses || []).map((pause) => ({
      title: `${pause.start_date} → ${pause.end_date}`,
      at: pause.start_date,
      detail: pause.reason || 'Gerekçe yazılmamış',
      tone: 'info',
    })), { emptyText: 'Duraklatma geçmişi yok.' }));
  }
  return card('Durum', box);
}

async function submitStatus(action, ask) {
  const reason = await askReason(ask);
  if (!reason) return;
  await withBusy('Durum yazılıyor…', async () => {
    const result = await call(`${BASE}/subscriptions/${state.drawer.id}/${action}`, {
      method: 'POST',
      body: { reason },
    });
    announce(result, 'Durum güncellendi.');
    await refreshDrawer();
    await reloadActiveTab();
  });
}

function openPause() {
  const box = h('div', 'bsu-stack');
  box.append(alertBox(
    'DURAKLATMA İPTAL DEĞİLDİR: aralık boyunca üretim durur, sonra AYNI '
    + 'FİYATLA devam eder. Bitiş günü ZORUNLUDUR — süresiz duraklatma, iptalin '
    + 'adı konmamış hâlidir ve iptalin kendi düğmesi var. Aralıktaki ZATEN '
    + 'ÜRETİLMİŞ siparişler otomatik iptal edilmez; sunucu onları listeler ve '
    + 'her birine siz karar verirsiniz.', 'warn'));

  const form = track(formGrid({
    fields: [
      { key: 'start_date', label: 'Duraklatma başlangıcı', type: 'date', required: true,
        hint: 'Bugünden geriye alınamaz: geçmiş bir günü duraklatmak üretilmiş '
          + 'siparişi silmez, yalnız raporu bozar. İLERİ TARİH SEÇİLİRSE '
          + 'abonelik o güne kadar üretmeye devam eder.' },
      { key: 'end_date', label: 'Duraklatma bitişi', type: 'date', required: true },
      { key: 'pause_reason', label: 'Duraklama etiketi', type: 'text', maxLength: 255,
        placeholder: 'Kurum tatili',
        hint: 'Kaydın kendisine yazılır; denetim gerekçesinden ayrıdır.' },
    ],
    /*
     * VARSAYILAN ARTIK BUGÜN, YARIN DEĞİL (I2).
     *
     * Yarın varsayılanı, en sık yapılan hareketi en pahalı hataya
     * bağlıyordu: sunucu durumu hemen `paused` yapıyor ve `runsOnDate()`
     * ilk kontrolü `status !== active` olduğu için BUGÜNÜN üretimi de
     * sessizce kesiliyordu. Sunucu tarafı düzeltildi (durum yalnız pencere
     * gerçekten yürürlükteyken değişiyor) ama varsayılanın da "duraklat"
     * denince akla gelen günü göstermesi gerekiyor: bugün.
     */
    value: { start_date: todayIso(), end_date: '', pause_reason: '' },
  }));
  box.append(form.node);

  const actions = h('div', 'bsu-actions');
  actions.append(writeButton('Duraklat', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      if (!draft.start_date || !draft.end_date) {
        toast('Başlangıç ve bitiş günü zorunlu.', 'bad');
        return;
      }
      const reason = await askReason({
        title: 'Aboneliği duraklat',
        description: `${draft.start_date} → ${draft.end_date} aralığında üretim `
          + 'durur. Abonelik iptal edilmez.',
        confirmLabel: 'Duraklat', danger: false,
      });
      if (!reason) return;
      await withBusy('Duraklatılıyor…', async () => {
        const result = await call(`${BASE}/subscriptions/${state.drawer.id}/pause`, {
          method: 'POST',
          body: { reason, start_date: draft.start_date, end_date: draft.end_date,
            pause_reason: draft.pause_reason || '' },
        });
        announce(result, 'Abonelik duraklatıldı.');
        nodes.pausePanel?.remove();
        await refreshDrawer();
        await reloadActiveTab();
      });
    },
  }));
  box.append(actions);

  nodes.pausePanel?.remove();
  nodes.pausePanel = card('Duraklatma aralığı', box);
  nodes.subDrawer.body.prepend(nodes.pausePanel);
}

function openCancel(record) {
  const box = h('div', 'bsu-stack');
  box.append(alertBox(
    'İPTALİN GERİ DÖNÜŞÜ YOKTUR. Yeniden başlatmak yeni bir abonelik açmaktır: '
    + 'iptal edilmiş bir kuralı canlandırmak, iptal tarihinden sonraki günlerin '
    + 'hangi kurala tabi olduğunu belirsiz kılardı. İptal ÜRETİLMİŞ SİPARİŞLERİ '
    + 'DÜŞÜRMEZ ve para hareketi üretmez; o siparişleri iptal etmek (ve iade) '
    + 'Sipariş Yönetimi ekranının ayrı yetkisini ister.', 'bad'));

  const form = track(formGrid({
    fields: [
      { key: 'effective_date', label: 'Geçerlilik günü', type: 'date', required: true,
        hint: 'Bitiş günü buraya yazılır. Bugünden geriye alınamaz.' },
    ],
    value: { effective_date: todayIso() },
  }));
  box.append(form.node);

  const actions = h('div', 'bsu-actions');
  actions.append(writeButton('İptal et', {
    variant: 'danger',
    onClick: async () => {
      const draft = form.draft();
      if (!draft.effective_date) { toast('Geçerlilik günü zorunlu.', 'bad'); return; }
      const reason = await askReason({
        title: 'Aboneliği iptal et',
        description: `${record.customer_label} aboneliği ${draft.effective_date} `
          + 'itibarıyla sona erecek. GERİ DÖNÜŞÜ YOKTUR.',
        confirmLabel: 'İptal et',
      });
      if (!reason) return;
      // İKİNCİ KAPI: PIN. Bu, abonelik alanındaki tek GERİ DÖNÜŞSÜZ işlem;
      // iptal edilmiş abonelik aktifleştirilemez, ödemeyle geri açılamaz.
      const pin = await confirmWithPin(nodes.root, {
        title: 'PIN ile onayla',
        description: `${record.customer_label} aboneliği KALICI OLARAK iptal `
          + 'edilecek. Yeniden başlatmak yeni bir abonelik açmak demektir.',
        confirmLabel: 'Aboneliği iptal et',
      });
      if (!pin) return;
      await withBusy('İptal gönderiliyor…', async () => {
        const result = await call(`${BASE}/subscriptions/${state.drawer.id}/cancel`, {
          method: 'POST',
          body: { reason, pin, effective_date: draft.effective_date },
        });
        announce(result, 'Abonelik iptal edildi.');
        nodes.cancelPanel?.remove();
        await refreshDrawer();
        await reloadActiveTab();
      });
    },
  }));
  box.append(actions);

  nodes.cancelPanel?.remove();
  nodes.cancelPanel = card('Abonelik iptali', box);
  nodes.subDrawer.body.prepend(nodes.cancelPanel);
}

/**
 * SERVİS TAKVİMİ. Kaynağı `upcomingServiceDays()` — gece işinin kullandığı
 * metodun ta kendisi. Yalnız ÜRETİM YAPILACAK günler döner: hafta sonu menü
 * olmadığı için beş günlük bir abonelikte cumartesi/pazar hiç görünmez.
 * Kapalı günler GÖRÜNÜR ve nedeni yazılır — "o gün neden üretim yok"
 * sorusunun cevabı listede olmalı.
 */
function calendarCard(record) {
  const box = h('div', 'bsu-stack');
  if (state.drawer.calendarOk === false) {
    box.append(alertBox('Servis takvimi okunamadı. Bu, "üretim yok" DEMEK '
      + 'DEĞİLDİR; bağlantı gelince yeniden deneyin.', 'warn'));
    return card('Servis takvimi', box);
  }

  const rows = state.drawer.calendar || [];
  box.append(dataTable({
    columns: [
      { key: 'date', label: 'Gün', width: '110px' },
      { key: 'weekday_label', label: 'Hafta günü', width: '110px' },
      { key: 'quantity', label: 'Porsiyon', width: '90px', align: 'num',
        cell: (row) => h('span', undefined, num(row.quantity)) },
      {
        key: 'closed',
        label: 'Durum',
        width: '160px',
        cell: (row) => {
          if (row.closed) return badge('Kapalı gün', 'dim');
          if (row.exception) {
            return badge(row.exception.skip ? 'Atlanacak' : row.exception.label, 'warn');
          }
          return badge('Normal', 'good');
        },
      },
      {
        key: 'release_state',
        label: 'Sipariş',
        width: '190px',
        cell: (row) => {
          const box2 = h('div', 'bsu-cell');
          if (row.release_state === 'released') {
            box2.append(badge(`Mutfakta · #${row.order_id}`, 'good'));
            box2.append(h('small', 'bsu-dim', stampIso(row.released_at)));
          } else if (row.release_state === 'waiting') {
            // ÜÇÜNCÜ HÂL: üretildi ama 07:00'ı bekliyor. İkiye indirmek
            // "mutfak bunu görüyor mu" sorusunu cevapsız bırakırdı.
            box2.append(badge(`Üretildi, 07:00 bekliyor · #${row.order_id}`, 'info'));
          } else {
            box2.append(badge('Üretilmedi', 'dim'));
          }
          return box2;
        },
      },
      { key: 'note', label: 'Not', width: 'minmax(0, 1fr)' },
      {
        key: 'actions',
        label: '',
        width: '210px',
        cell: (row) => {
          const box2 = h('div', 'bsu-actions');
          if (row.closed) return h('span', 'bsu-dim', '—');
          if (row.release_state === 'waiting') {
            box2.append(writeButton('KDS\'e düşür', {
              title: 'Siparişi 07:00\'ı beklemeden mutfağa açar',
              onClick: () => submitRelease(row.order_id),
            }));
          } else if (row.release_state === 'none') {
            box2.append(writeButton('Şimdi üret', {
              title: 'Gece işini beklemeden bu günün siparişini üretir',
              onClick: () => submitGenerate(row.date),
            }));
          }
          return box2;
        },
      },
    ],
    rows,
    dense: true,
    empty: emptyState({
      title: 'Önümüzdeki pencerede üretim günü yok',
      text: 'Yalnız ÜRETİM YAPILACAK günler listelenir; servis günü olmayan '
        + 'günler hiç görünmez. Abonelik duraklatılmış ya da bitmiş olabilir.',
    }),
  }).node);

  box.append(hintBox(
    'Elle üretim en fazla 7 gün ileri gidebilir ve o günün stok tavanına '
    + 'takılabilir: abonelikler stoku ÖNCE REZERVE eder, elle üretim o '
    + 'rezervasyonun dışında kalan bir taleptir. Tavan doluysa sunucu reddeder '
    + 've nedenini söyler.'));
  void record;
  return card('Servis takvimi', box);
}

async function submitGenerate(date) {
  const release = await confirmWithReason(nodes.root, {
    title: `${date} için sipariş üret`,
    description: 'Gece işi beklenmeden üretim yapılır; kural gece işiyle '
      + 'AYNIDIR. Sipariş normal serbest bırakma saatinde (07:00) mutfağa '
      + 'düşer. Gerekçe zorunludur.',
    confirmLabel: 'Üret',
    danger: false,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!release) return;

  await withBusy('Üretim yapılıyor…', async () => {
    const result = await call(`${BASE}/subscriptions/${state.drawer.id}/generate`, {
      method: 'POST',
      body: { reason: release, service_date: date, release_now: false },
    });
    announce(result, `${num((result.created || []).length)} sipariş üretildi.`);
    // ÜRETİLMEYENLER DE YAZILIR: "neden sipariş yok" sorusunun cevabı ekranda
    // durmalı, yoksa sessiz bir başarısızlık gibi görünür.
    for (const skipped of result.skipped || []) {
      toast(`Üretilmedi: ${JSON.stringify(skipped)}`, 'warn');
    }
    await refreshDrawer();
  });
}

async function submitRelease(orderId) {
  const reason = await askReason({
    title: `Sipariş #${orderId} mutfağa düşsün`,
    description: 'Abonelik siparişleri mutfağa 07:00\'de düşer: gece üretilen '
      + 'kırk sipariş, sabah işbaşı yapan mutfağın panosunu doldurup o an gelen '
      + 'GERÇEK bir siparişi görünmez kılardı. Bu düğme o siparişi erken açar. '
      + 'Denetim kaydı SİPARİŞE yazılır.',
    confirmLabel: 'Mutfağa düşür',
    danger: false,
  });
  if (!reason) return;
  await withBusy('Sipariş açılıyor…', async () => {
    const result = await call(`${BASE}/orders/${orderId}/release`, {
      method: 'POST',
      body: { reason },
    });
    announce(result, 'Sipariş mutfağa düşürüldü.');
    await refreshDrawer();
  });
}

/**
 * TEK-GÜN İSTİSNASI: "yarın 20 değil 12" ya da "yarın atla". Kural değişikliği
 * DEĞİLDİR. Aynı gün için ikinci istisna ÜZERİNE YAZILIR ve çakışma hatası
 * verilmez — yönetici aynı güne iki kez karar verebilir, son karar geçerlidir
 * ve denetim izinde ikisi de görünür.
 */
function exceptionsCard(record) {
  const box = h('div', 'bsu-stack');
  const rows = record.exceptions || [];

  if (rows.length) {
    box.append(dataTable({
      columns: [
        { key: 'service_date', label: 'Gün', width: '110px' },
        { key: 'label', label: 'Karar', width: '150px',
          cell: (row) => badge(row.label, row.skip ? 'warn' : 'info') },
        { key: 'note', label: 'Not', width: 'minmax(0, 1fr)' },
        {
          key: 'actions',
          label: '',
          width: '120px',
          cell: (row) => writeButton('Kaldır', {
            title: 'İstisnayı siler ve gün normal adede döner',
            onClick: () => submitExceptionDelete(row.service_date),
          }),
        },
      ],
      rows,
      dense: true,
    }).node);
  } else {
    box.append(hintBox('Tanımlı istisna yok; her servis günü varsayılan adetle '
      + 'üretilir.'));
  }

  const form = track(formGrid({
    fields: [
      { key: 'service_date', label: 'Servis günü', type: 'date', required: true,
        hint: 'Aboneliğin servis günlerinden biri olmalı ve geçmişte olamaz.' },
      { key: 'skip', label: 'Bu günü atla', type: 'checkbox',
        hint: 'İşaretlenirse adet yazılamaz: "atla ama 12 yap" tutarsız olurdu.' },
      { key: 'quantity_override', label: 'O güne özel porsiyon', type: 'number' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 255 },
    ],
    value: { service_date: todayIso(1), skip: false, quantity_override: null, note: '' },
  }));
  box.append(form.node);

  const actions = h('div', 'bsu-actions');
  actions.append(writeButton('İstisna yaz', {
    variant: 'primary',
    onClick: () => submitException(form),
  }));
  box.append(actions);
  box.append(hintBox(
    'O gün için sipariş ZATEN ÜRETİLDİYSE istisna yazılamaz: üretilmiş bir '
    + 'siparişi değiştirmenin yolu Sipariş Yönetimi ekranındaki revizyondur.'));
  return card('Tek-gün istisnaları', box);
}

async function submitException(form) {
  const draft = form.draft();
  if (!draft.service_date) { toast('Servis günü zorunlu.', 'bad'); return; }
  if (draft.skip && draft.quantity_override) {
    toast('«Atla» seçiliyken adet yazılamaz: «atla ama 12 yap» tutarsız olurdu.',
      'bad');
    return;
  }
  if (!draft.skip && !draft.quantity_override) {
    toast('Ya günü atlayın ya da adet yazın; ikisi de boş bir istisna hiçbir '
      + 'şeyi değiştirmez.', 'bad');
    return;
  }
  const reason = await askReason({
    title: 'Tek-gün istisnası yaz',
    description: `${draft.service_date} için `
      + (draft.skip ? 'üretim ATLANACAK.' : `${draft.quantity_override} porsiyon.`)
      + ' Aynı gün için önceki bir istisna varsa ÜZERİNE YAZILIR.',
    confirmLabel: 'Yaz',
    danger: false,
  });
  if (!reason) return;

  await withBusy('İstisna yazılıyor…', async () => {
    const result = await call(`${BASE}/subscriptions/${state.drawer.id}/exceptions`, {
      method: 'POST',
      body: {
        reason,
        service_date: draft.service_date,
        skip: Boolean(draft.skip),
        quantity_override: draft.skip ? null : Number(draft.quantity_override) || null,
        note: draft.note || '',
      },
    });
    announce(result, 'İstisna yazıldı.');
    await refreshDrawer();
  });
}

async function submitExceptionDelete(date) {
  const reason = await askReason({
    title: `${date} istisnasını kaldır`,
    description: 'İstisna GERÇEKTEN SİLİNİR — bir belge değil, bir kuraldır ve '
      + 'pasifleştirilmiş bir kural, uygulanıp uygulanmadığı belirsiz bir kayıt '
      + 'olurdu. Gün varsayılan adede döner. Denetim izi kalır.',
    confirmLabel: 'Kaldır',
  });
  if (!reason) return;
  await withBusy('İstisna kaldırılıyor…', async () => {
    const result = await call(
      `${BASE}/subscriptions/${state.drawer.id}/exceptions/${date}/delete`,
      { method: 'POST', body: { reason } });
    announce(result, 'İstisna kaldırıldı.');
    await refreshDrawer();
  });
}

function runsCard() {
  const box = h('div', 'bsu-stack');
  if (state.drawer.runsOk === false) {
    box.append(alertBox('Üretim defteri okunamadı. Bu, "hiç üretim yapılmadı" '
      + 'DEMEK DEĞİLDİR.', 'warn'));
    return card('Üretim defteri', box);
  }
  box.append(hintBox(
    'Defter bir İDEMPOTENCY kaydıdır: bir (abonelik × nokta × gün) en fazla bir '
    + 'sipariş üretir ve bu güvence koddaki bir kontrol değil, VERİTABANI '
    + 'KISITIDIR. Siparişsiz bir satır BAŞARISIZLIK DEĞİLDİR: üretim denendi ama '
    + 'sipariş oluşmadı (kapalı gün, menü yayınlanmamış, stok dolu) ve satır '
    + 'yazıldı ki gece işi ertesi koşuda aynı günü yeniden denemesin.'));
  box.append(dataTable({
    columns: [
      { key: 'service_date', label: 'Gün', width: '110px' },
      {
        key: 'produced',
        label: 'Sonuç',
        width: '190px',
        cell: (row) => badge(row.outcome_label, row.outcome_tone),
      },
      { key: 'order_number', label: 'Sipariş', width: '130px',
        cell: (row) => h('span', undefined, row.order_number || '—') },
      { key: 'order_status', label: 'Sipariş durumu', width: '140px',
        cell: (row) => h('span', undefined, row.order_status || '—') },
      { key: 'quantity', label: 'Porsiyon', width: '90px', align: 'num',
        cell: (row) => h('span', undefined, num(row.quantity)) },
      { key: 'released_at', label: 'Mutfağa düştü', width: '150px',
        cell: (row) => h('span', undefined,
          row.released_at ? stampIso(row.released_at) : 'bekliyor') },
    ],
    rows: state.drawer.runs || [],
    dense: true,
    empty: emptyState({ title: 'Bu abonelik için henüz üretim yapılmadı' }),
  }).node);
  return card('Üretim defteri', box);
}

const AUDIT_TONES = {
  ok: 'good', dry_run: 'info', denendi: 'dim', hata: 'bad',
};

/**
 * FİYAT GEÇMİŞİ VE YEREL İZ. Bu, BU EKRANDAN yapılan yazma DENEMELERİNİN
 * kaydıdır — sunucunun denetim izi değil. Ağ koparsa ya da istek yarıda
 * kalırsa "kim neyi denedi" sorusunun cevabı yalnız burada kalır: uzak kayıt
 * yalnız sunucuya ULAŞAN isteği bilir.
 */
function priceHistoryCard() {
  const box = h('div', 'bsu-stack');
  const rows = state.drawer.audit || [];
  const priced = rows.filter((row) => row.price_kurus !== null
    && row.price_kurus !== undefined && row.result !== 'denendi');

  box.append(hintBox(
    'Fiyat satırları ayrı bir sütunda tutulur: "fiyatı kim, ne zaman, neden '
    + 'anlaştı" bu ekranın en çok sorulan sorusudur ve serbest bir not '
    + 'içinden aranamaz. Satırlar SİLİNMEZ.'));

  if (priced.length) {
    box.append(timeline(priced.map((row) => ({
      title: `${money(row.price_kurus)} — ${row.actor || 'bilinmiyor'}`,
      at: stampIso(row.created_at),
      detail: `${row.action} · ${row.reason}`,
      tone: row.result === 'ok' ? 'good' : 'dim',
    })), { emptyText: 'Fiyat kaydı yok.' }));
  } else {
    box.append(hintBox('Bu abonelik için bu ekrandan henüz fiyat yazılmadı.'));
  }

  box.append(dataTable({
    columns: [
      { key: 'created_at', label: 'An', width: '160px',
        cell: (row) => h('span', undefined, stampIso(row.created_at)) },
      { key: 'action', label: 'Eylem', width: '210px' },
      { key: 'actor', label: 'Aktör', width: 'minmax(0, 1fr)' },
      { key: 'result', label: 'Sonuç', width: '110px',
        cell: (row) => badge(row.result, AUDIT_TONES[row.result] || 'dim') },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
    ],
    rows,
    dense: true,
    empty: emptyState({ title: 'Bu abonelikte bu ekrandan yazma denenmedi' }),
  }).node);
  return card('Fiyat geçmişi ve yerel iz', box);
}

/**
 * SÖZLEŞME VE ÖDEME BURADA OKUNUR, YAZILMAZ (tek eylem, tek ev).
 *
 * Sözleşme göndermek ve dönem borcu açmak kendi sekmelerinde durur. Aynı işi
 * iki yerden yapabilmek, "hangisinden yaptım" sorusunu doğurur ve iki ekran
 * arasındaki küçük fark (ör. biri `send_sms` soruyor, öbürü sormuyor) zamanla
 * ayrışır.
 */
function elsewhereCard(record) {
  const box = h('div', 'bsu-stack');
  const line = h('div', 'bsu-kv');
  line.append(h('span', 'bsu-dim', 'Sözleşme'),
    badge(record.contract_status_label, record.contract_status_tone));
  box.append(line);
  if (record.contract?.signed_at) {
    box.append(h('div', 'bsu-dim',
      `İmzalandı: ${stampIso(record.contract.signed_at)} · OTP doğrulaması: `
      + `${stampIso(record.contract.otp_verified_at)}`));
  }

  const money2 = h('div', 'bsu-kv');
  money2.append(h('span', 'bsu-dim', 'Ödenmemiş dönem'),
    record.unpaid_periods
      ? badge(`${num(record.unpaid_periods)} dönem · ${money(record.unpaid_total_kurus)}`,
        'bad')
      : h('span', undefined, 'yok'));
  box.append(money2);

  box.append(hintBox(
    'Sözleşme göndermek/iptal etmek "Sözleşmeler" sekmesinde, dönem borcu açmak '
    + 've tahsilat işaretlemek "Ödemeler" sekmesindedir. Bir iş eylemi yalnız '
    + 'bir ekranda durur; buraya kısayol konsaydı iki ekran zamanla ayrışırdı.'));
  return card('Sözleşme ve ödeme (okuma)', box);
}

async function refreshDrawer() {
  if (!state.drawer) return;
  await loadSubscription(state.drawer.id);
  paintSubscription();
}

async function reloadActiveTab() {
  if (state.tab === 'active' || state.tab === 'paused') {
    await loadList();
    paintList();
  } else if (state.tab === 'requests') {
    await loadPending();
    paintPending();
  }
}

// ============================================================ 4. Sözleşmeler

/**
 * Sözleşme ve ödeme uçları ABONELİK BAŞINADIR (`/{id}/contracts`,
 * `/{id}/payments`); genel bir liste ucu YOKTUR ve uydurulmadı. Bu yüzden her
 * iki sekme de önce bir abonelik seçtirir. Seçim tablosu abonelik listesinden
 * gelir ve sözleşme durumu ile ödenmemiş dönem ZATEN o listede taşınır — yani
 * "kimde eksik var" sorusu TEK İSTEKLE cevaplanır, seçim yalnız ayrıntı için.
 */
function showPicker(kind) {
  nodes.body.replaceChildren();
  // SEÇİM SEKMELER ARASINDA TAŞINMAZ. "Sözleşmeler"de seçili duran abonelik
  // "Ödemeler"e geçince de seçili görünseydi, ekran o aboneliğin ödemelerini
  // İSTENMEDEN yükler ve kullanıcı baktığı satırın hangisi olduğunu tablodan
  // değil hafızasından okumak zorunda kalırdı.
  state.picker.selected = null;
  const wrap = h('div', 'bsu-stack');

  wrap.append(hintBox(kind === 'contracts'
    ? 'Sözleşme uçları abonelik başınadır; aşağıdaki tablo hangi abonelikte '
      + 'sözleşmenin hangi aşamada olduğunu TEK İSTEKLE gösterir. Ayrıntı ve '
      + 'gönderim için bir satır seçin.'
    : 'Ödeme uçları abonelik başınadır; aşağıdaki tablo kimin kaç dönem borcu '
      + 'olduğunu TEK İSTEKLE gösterir. Dönem listesi ve tahsilat için bir '
      + 'satır seçin.'));

  // ARAMA ŞERİDİ: yüzden fazla abonelikte listeyi gözle taramak mümkün değil
  // ve eski ekran zaten ilk 100'ü gösterip duruyordu.
  const pickerFilters = track(filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px', placeholder: 'Kurum, ad, telefon' },
    ],
    onChange: (values) => {
      state.picker.q = values.q || '';
      state.picker.page = 1;
      loadPicker().then(() => paintPicker(kind));
    },
    actions: [button('Yenile', {
      onClick: () => loadPicker().then(() => paintPicker(kind)),
    })],
  }));
  wrap.append(pickerFilters.node);

  nodes.pickerSlot = h('div');
  nodes.detailSlot = h('div', 'bsu-stack');
  wrap.append(nodes.pickerSlot, nodes.detailSlot);
  nodes.pickerPager = pager({
    total: 0, page: 1, size: state.prefs?.page_size || 25,
    onChange: ({ page }) => {
      state.picker.page = page;
      loadPicker().then(() => paintPicker(kind));
    },
  });
  wrap.append(nodes.pickerPager.node);
  nodes.body.append(wrap);

  if (state.picker.loaded) paintPicker(kind);
  else loadPicker().then(() => paintPicker(kind));
}

/**
 * Sözleşme/ödeme sekmelerinin abonelik seçicisi.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ARAMA VE SAYFALAMA SONRADAN EKLENDİ (I3).
 *
 * Sabit `per_page=100` tek sayfaydı: 101. abonelikten sonrası seçilemiyordu
 * ve ekran bunu SÖYLEMİYORDU — liste sessizce kesiliyor, aranan müşteri
 * "yok" gibi görünüyordu. Yüz abonelik uzak bir sayı değil; kurumsal
 * müşterili bir catering şirketinde bir yılın işi.
 *
 * İPTAL EDİLMİŞLER SEÇİCİDE YOK ve bu bilinçli: iptal edilmiş bir aboneliğe
 * sözleşme gönderilemez, borç açılamaz. Onları görmek için "İptal edilmiş"
 * sekmesi var.
 * ═══════════════════════════════════════════════════════════════════════════
 */
async function loadPicker() {
  const params = new URLSearchParams();
  params.set('status', 'pending,awaiting_contract,awaiting_payment,active,paused');
  if (state.picker.q) params.set('q', state.picker.q);
  params.set('page', String(state.picker.page || 1));
  params.set('per_page', String(state.prefs?.page_size || 25));
  try {
    const payload = await call(`${BASE}/subscriptions?${params.toString()}`);
    if (!linkOk(payload)) return;
    state.picker.rows = payload.items || [];
    state.picker.meta = payload.meta || {};
    state.picker.loaded = true;
  } catch {
    state.picker.loaded = false;
  }
}

function paintPicker(kind) {
  if (!nodes.pickerSlot) return;
  nodes.pickerSlot.replaceChildren();
  nodes.pickerPager?.update({
    total: Number(state.picker.meta.total || 0),
    page: Number(state.picker.meta.page || state.picker.page || 1),
    size: Number(state.picker.meta.per_page || state.prefs?.page_size || 25),
  });

  const warning = linkAlert('Abonelik listesi');
  if (warning) nodes.pickerSlot.append(warning);
  if (!state.picker.loaded) {
    nodes.pickerSlot.append(skeletonRows(5, 4));
    return;
  }

  const columns = [
    { key: 'customer_label', label: 'Müşteri', width: 'minmax(0, 1.6fr)' },
    { key: 'status', label: 'Durum', width: '120px',
      cell: (row) => badge(row.status_label, row.status_tone) },
  ];
  if (kind === 'contracts') {
    columns.push({
      key: 'contract_status', label: 'Sözleşme', width: '160px',
      cell: (row) => badge(row.contract_status_label, row.contract_status_tone),
    });
  } else {
    columns.push({
      key: 'unpaid_total_kurus', label: 'Ödenmemiş', width: '170px', align: 'num',
      cell: (row) => (row.unpaid_periods
        ? badge(`${num(row.unpaid_periods)} dönem · ${money(row.unpaid_total_kurus)}`,
          'bad')
        : h('span', 'bsu-dim', '—')),
    });
  }

  nodes.pickerSlot.append(card('Abonelik seçin', dataTable({
    columns,
    rows: state.picker.rows,
    dense: true,
    empty: emptyState({ title: 'Abonelik yok' }),
    onRow: (row) => {
      state.picker.selected = row;
      if (kind === 'contracts') loadContracts(row.id).then(() => paintContracts(row));
      else loadPayments(row.id).then(() => paintPayments(row));
    },
  }).node));

  const selected = state.picker.selected;
  if (!selected) {
    nodes.detailSlot.replaceChildren(emptyState({
      title: 'Yukarıdan bir abonelik seçin',
      text: kind === 'contracts'
        ? 'Sözleşmeler abonelik başına listelenir.'
        : 'Dönem ödemeleri abonelik başına listelenir.',
    }));
  }
}

async function loadContracts(id) {
  state.contracts = { items: [], openId: 0, signedId: 0, loaded: false, error: '' };
  try {
    const payload = await call(`${BASE}/subscriptions/${id}/contracts`);
    if (!linkOk(payload)) { state.contracts.error = payload.error || ''; return; }
    state.contracts = {
      items: payload.items || [],
      openId: payload.open_contract_id || 0,
      signedId: payload.signed_contract_id || 0,
      loaded: true,
      error: '',
    };
  } catch (failure) {
    state.contracts.error = failure.message;
  }
}

function paintContracts(subscription) {
  if (!nodes.detailSlot) return;
  nodes.detailSlot.replaceChildren();

  const warning = linkAlert('Sözleşme listesi');
  if (warning) nodes.detailSlot.append(warning);
  if (state.contracts.error && state.link.connected) {
    nodes.detailSlot.append(alertBox(state.contracts.error, 'bad'));
  }

  nodes.detailSlot.append(card(`${subscription.customer_label} — sözleşmeler`,
    contractsBody(subscription)));
  nodes.detailSlot.append(card('Yeni sözleşme gönder', newContractBody(subscription)));
}

function contractsBody(subscription) {
  const box = h('div', 'bsu-stack');
  box.append(hintBox(
    'Sözleşme bir PDF değil, TEK KULLANIMLIK bir bağlantıdır: müşteri açar, '
    + 'metni okur, telefonuna gelen kodu girer ve onaylar. Kodun doğrulanması '
    + 'SUNUCUDA olur — Kontrol Merkezi yalnız bağlantıyı oluşturur ve '
    + 'gönderir. İmzalanmış sözleşme İPTAL EDİLEMEZ; yeni koşullar yeni bir '
    + 'sözleşme gerektirir.'));

  box.append(dataTable({
    columns: [
      { key: 'status', label: 'Durum', width: '130px',
        cell: (row) => badge(row.status_label, row.status_tone) },
      { key: 'sent_to_phone', label: 'Gönderildi', width: '150px',
        cell: (row) => h('span', undefined, row.sent_to_phone || '—') },
      { key: 'sent_at', label: 'Gönderim', width: '150px',
        cell: (row) => h('span', undefined, row.sent_at ? stampIso(row.sent_at) : '—') },
      { key: 'expires_at', label: 'Son geçerlilik', width: '150px',
        cell: (row) => h('span', undefined,
          row.expires_at ? stampIso(row.expires_at) : '—') },
      { key: 'signed_at', label: 'İmza', width: '150px',
        cell: (row) => h('span', undefined,
          row.signed_at ? stampIso(row.signed_at) : 'bekliyor') },
      {
        key: 'terms',
        label: 'İmzalanan koşullar',
        width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const terms = row.terms_snapshot || {};
          if (!Object.keys(terms).length) return h('span', 'bsu-dim', '—');
          // İMZALANDIĞI ANDAKİ KOŞULLAR: abonelik sonradan değişse bile
          // sözleşme değişmez ve "neyi imzaladı" sorusunun cevabı budur.
          const node = h('div', 'bsu-cell');
          node.append(h('span', undefined,
            `${priceText(terms.agreed_unit_price_kurus)} × `
            + `${num(terms.default_quantity || 0)}`));
          node.append(h('small', 'bsu-dim',
            `${(terms.service_days || []).length} gün · ${terms.start_date || '—'}`));
          return node;
        },
      },
      {
        key: 'actions',
        label: '',
        width: '230px',
        cell: (row) => {
          const box2 = h('div', 'bsu-actions');
          if (row.terminal) {
            box2.append(badge('İmzalı — değiştirilemez', 'good'));
            return box2;
          }
          if (!row.open) {
            box2.append(h('span', 'bsu-dim', '—'));
            return box2;
          }
          box2.append(writeButton('Yeniden gönder', {
            title: 'AYNI bağlantıyı yeniden gönderir; süreye dokunmaz, '
              + 'müşterinin elindeki SMS çalışmaya devam eder',
            onClick: () => submitResend(row, subscription, false),
          }));
          // AYRI DÜĞME, AYRI SONUÇ. Tek düğmede birleştirilseydi "yeniden
          // gönder"e basan herkes müşterinin elindeki bağlantıyı öldürürdü —
          // eski ekranın sessizce yaptığı tam olarak buydu.
          box2.append(writeButton('Yenile + gönder', {
            variant: 'danger',
            title: 'YENİ bağlantı üretir; müşterinin elindeki eski SMS geçersiz olur',
            onClick: () => submitResend(row, subscription, true),
          }));
          box2.append(writeButton('İptal', {
            variant: 'danger',
            onClick: () => submitContractCancel(row, subscription),
          }));
          return box2;
        },
      },
    ],
    rows: state.contracts.items,
    dense: true,
    empty: emptyState({
      title: 'Bu abonelikte sözleşme yok',
      text: 'Abonelik imzalı sözleşme olmadan aktifleştirilemez.',
    }),
  }).node);
  return box;
}

function newContractBody(subscription) {
  const box = h('div', 'bsu-stack');
  if (state.contracts.openId) {
    box.append(alertBox(
      `Bu abonelikte AÇIK bir sözleşme var (#${state.contracts.openId}). İki `
      + 'geçerli bağlantı, hangisinin imzalandığını belirsiz kılardı; yenisini '
      + 'göndermek için önce onu iptal edin.', 'warn'));
    return box;
  }

  const form = track(formGrid({
    fields: [
      { key: 'phone', label: 'Telefon (boş = müşterinin kayıtlısı)', type: 'phone',
        hint: 'Bağlantı bu numaraya SMS ile gider.' },
      { key: 'expires_in_days', label: 'Bağlantı ömrü (gün)', type: 'number',
        hint: 'Sunucu sınırı 1–30. Süresiz bir imza bağlantısı, bir yıl sonra '
          + 'ele geçtiğinde hâlâ geçerli olurdu.' },
      { key: 'send_sms', label: 'SMS ile gönder', type: 'checkbox',
        hint: 'Kapatılırsa kayıt "hazırlandı" kalır ve bağlantı EKRANDA gösterilir '
          + '(elden iletmek için). Açıkken bağlantı ekrana HİÇ düşmez: zaten '
          + 'müşterinin telefonunda ve ikinci bir yerde göstermek onu '
          + 'sızdırılabilir kılardı.' },
    ],
    value: {
      phone: '',
      expires_in_days: state.prefs?.expires_in_days || 7,
      send_sms: true,
    },
  }));
  box.append(form.node);

  const actions = h('div', 'bsu-actions');
  actions.append(writeButton('Sözleşme oluştur ve gönder', {
    variant: 'primary',
    onClick: () => submitContract(form, subscription),
  }));
  box.append(actions);

  nodes.contractResult = h('div', 'bsu-stack');
  box.append(nodes.contractResult);
  return box;
}

async function submitContract(form, subscription) {
  const draft = form.draft();
  const reason = await askReason({
    title: 'Sözleşme gönder',
    description: `${subscription.customer_label} için imza bağlantısı `
      + (draft.send_sms ? 'SMS ile gönderilecek.' : 'oluşturulacak (SMS gitmeyecek).')
      + ' Onayı müşteri kendi telefonundan, sunucunun imza sayfasında verir.',
    confirmLabel: 'Gönder',
    danger: false,
  });
  if (!reason) return;

  await withBusy('Sözleşme gönderiliyor…', async () => {
    const result = await call(`${BASE}/subscriptions/${subscription.id}/contracts`, {
      method: 'POST',
      body: {
        reason,
        phone: draft.phone || '',
        expires_in_days: Number(draft.expires_in_days) || 0,
        send_sms: Boolean(draft.send_sms),
      },
    });
    announce(result, result.sms_sent ? 'Sözleşme SMS ile gönderildi.'
      : 'Sözleşme oluşturuldu (SMS gönderilmedi).');
    await loadContracts(subscription.id);
    paintContracts(subscription);
    // BAĞLANTI YALNIZ `send_sms=false` iken döner ve YALNIZ EKRANDA gösterilir;
    // hiçbir yere yazılmaz.
    if (result.sign_url && nodes.contractResult) {
      nodes.contractResult.replaceChildren(alertBox(
        `İmza bağlantısı (elden iletin, bir yere kaydetmeyin): ${result.sign_url}`,
        'info'));
    }
  });
}

/**
 * Bağlantıyı yeniden gönderir.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * `renew` VARSAYILAN OLARAK KAPALI — ESKİ EKRAN TERSİNİ YAPIYORDU (I2).
 *
 * Buradaki metin "yeni token ÜRETİLMEZ, eski SMS çalışmaya devam eder"
 * diyordu ama gövde HER SEFERİNDE bir gün sayısı gönderiyordu. Belirteç bitiş
 * anını da imzalıyor: süre tazelendiği anda müşterinin elindeki bağlantı
 * ÖLÜYORDU. Yani ekran tam tersini vaat ediyor, müşteri de eski SMS'e
 * tıkladığında geçersiz bir sayfa buluyordu.
 *
 * Artık iki ayrı düğme var ve ikisi de ne yaptığını yazıyor.
 * ═══════════════════════════════════════════════════════════════════════════
 */
async function submitResend(row, subscription, renew = false) {
  const reason = await askReason({
    title: renew ? 'Yeni bağlantı üret ve gönder' : 'Bağlantıyı yeniden gönder',
    description: renew
      ? 'YENİ BİR BAĞLANTI ÜRETİLİR VE ESKİSİ ÖLÜR. Müşterinin elindeki SMS '
        + `artık çalışmayacak; süre ${state.prefs?.expires_in_days || 7} güne `
        + 'tazelenir. Yalnız eski bağlantı süresi dolduysa ya da sızdığından '
        + 'şüpheleniyorsanız kullanın.'
      : 'AYNI bağlantı yeniden gönderilir; süreye DOKUNULMAZ ve yeni bir token '
        + 'ÜRETİLMEZ. Müşterinin elindeki eski SMS çalışmaya devam eder, yani '
        + '"hangi linke tıklayacağım" sorusu doğmaz.',
    confirmLabel: renew ? 'Yenile ve gönder' : 'Yeniden gönder',
    danger: renew,
  });
  if (!reason) return;
  await withBusy('Yeniden gönderiliyor…', async () => {
    const result = await call(`${BASE}/contracts/${row.id}/resend`, {
      method: 'POST',
      body: {
        reason,
        renew,
        // YALNIZ YENİLERKEN ANLAMLI: `renew=false` iken sunucu bu alana hiç
        // bakmıyor ve süre korunuyor.
        expires_in_days: renew ? (state.prefs?.expires_in_days || 0) : 0,
      },
    });
    announce(result, 'Bağlantı yeniden gönderildi.');
    // SUNUCU CEVAP OKUNUR, İSTEK DEĞİL: süresi dolmuş bir bağlantıda sunucu
    // süreyi zorunlu olarak tazeliyor ve `renews_link` bunu söylüyor. Ekran
    // "eski link çalışmaya devam ediyor" derken sunucu onu öldürmüş olabilir.
    if (result?.renews_link && !result?.dry_run) {
      toast('YENİ BAĞLANTI ÜRETİLDİ: müşterinin elindeki eski SMS artık '
        + 'geçersiz. Yeni mesajın ulaştığını doğrulayın.', 'warn');
    }
    await loadContracts(subscription.id);
    paintContracts(subscription);
  });
}

async function submitContractCancel(row, subscription) {
  const reason = await askReason({
    title: 'Sözleşmeyi iptal et',
    description: 'Bağlantı geçersiz olur ve yeni bir sözleşme oluşturulabilir. '
      + 'İMZALANMIŞ bir sözleşme iptal edilemez: imzalanmışı iptal edilmiş '
      + 'göstermek, imzanın kendisini geçersiz kılmaktır.',
    confirmLabel: 'İptal et',
  });
  if (!reason) return;
  await withBusy('Sözleşme iptal ediliyor…', async () => {
    const result = await call(`${BASE}/contracts/${row.id}/cancel`, {
      method: 'POST',
      body: { reason },
    });
    announce(result, 'Sözleşme iptal edildi.');
    await loadContracts(subscription.id);
    paintContracts(subscription);
  });
}

// ============================================================== 5. Ödemeler

async function loadPayments(id) {
  state.payments = { items: [], meta: {}, loaded: false, error: '' };
  try {
    const payload = await call(`${BASE}/subscriptions/${id}/payments`);
    if (!linkOk(payload)) { state.payments.error = payload.error || ''; return; }
    state.payments = {
      items: payload.items || [], meta: payload.meta || {}, loaded: true, error: '',
    };
  } catch (failure) {
    state.payments.error = failure.message;
  }
}

function paintPayments(subscription) {
  if (!nodes.detailSlot) return;
  nodes.detailSlot.replaceChildren();

  const warning = linkAlert('Ödeme listesi');
  if (warning) nodes.detailSlot.append(warning);
  if (state.payments.error && state.link.connected) {
    nodes.detailSlot.append(alertBox(state.payments.error, 'bad'));
  }

  const box = h('div', 'bsu-stack');
  const meta = state.payments.meta || {};
  box.append(kpiRow([
    { label: 'Toplam', value: priceText(meta.total_kurus) },
    { label: 'Tahsil edilen', value: priceText(meta.paid_kurus) },
    { label: 'Bekleyen', value: priceText(meta.pending_kurus) },
    { label: 'Gecikmiş', value: priceText(meta.overdue_kurus) },
  ]));
  box.append(hintBox(
    'Toplamlar SUNUCUDAN gelir; satırların toplamını almak, geçersiz kayıtların '
    + 'nasıl sayıldığını tahmin etmek olurdu. "Gecikmiş" de sunucuda hesaplanır: '
    + 'istemcide hesaplansaydı saati kaymış bir panelde borç bir gün erken '
    + 'kırmızıya dönerdi.'));

  box.append(dataTable({
    columns: [
      { key: 'period_label', label: 'Dönem', width: '200px' },
      { key: 'due_date', label: 'Son ödeme', width: '120px' },
      { key: 'amount_kurus', label: 'Tutar', width: '130px', align: 'num',
        cell: (row) => h('span', undefined, money(row.amount_kurus)) },
      {
        key: 'status',
        label: 'Durum',
        width: '170px',
        cell: (row) => {
          const cell = h('div', 'bsu-cell');
          cell.append(badge(row.status_label, row.status_tone));
          // GECİKMİŞ DÖNEM AYRI BİR ROZETLE UYARIR: rozet tek başına anlam
          // taşımasın diye gün sayısı da yazılır.
          if (row.overdue) {
            cell.append(badge(`${num(row.overdue_days)} gün gecikti`, 'bad'));
          }
          return cell;
        },
      },
      { key: 'method_label', label: 'Yöntem', width: '110px' },
      { key: 'paid_at', label: 'Tahsilat', width: '150px',
        cell: (row) => h('span', undefined, row.paid_at ? stampIso(row.paid_at) : '—') },
      { key: 'invoice_id', label: 'Fatura', width: '100px',
        cell: (row) => h('span', undefined, row.invoice_id ? `#${row.invoice_id}` : '—') },
      {
        key: 'actions',
        label: '',
        width: '170px',
        cell: (row) => (row.payable
          ? writeButton('Tahsil edildi…', {
            onClick: () => openMarkPaid(row, subscription),
          })
          : h('span', 'bsu-dim', '—')),
      },
    ],
    rows: state.payments.items,
    dense: true,
    empty: emptyState({
      title: 'Bu abonelikte dönem borcu açılmamış',
      text: 'Abonelik 30 günlük peşin ödemelidir; dönem borcunu aşağıdan açın.',
    }),
  }).node);

  nodes.detailSlot.append(card(`${subscription.customer_label} — dönem ödemeleri`, box));
  nodes.detailSlot.append(card('Yeni dönem borcu', newPaymentBody(subscription)));
  nodes.markPaidSlot = h('div', 'bsu-stack');
  nodes.detailSlot.append(nodes.markPaidSlot);
}

function newPaymentBody(subscription) {
  const box = h('div', 'bsu-stack');
  box.append(hintBox(
    'TUTARI BOŞ BIRAKIN, SUNUCU HESAPLASIN: dönemdeki üretilmiş ve iptal '
    + 'edilmemiş siparişlerin toplamını hesaplar. Elle yazmak serbesttir ama '
    + 'varsayılan hesaplanmış olmalı — yönetici her ay çarpma yapmamalı. '
    + '"Önce prova et" hesabı GERÇEKTEN yapar ve kaç siparişten geldiğini '
    + 'söyler, hiçbir satır yazmadan.'));

  const form = track(formGrid({
    fields: [
      { key: 'period_start', label: 'Dönem başlangıcı', type: 'date', required: true },
      { key: 'period_end', label: 'Dönem bitişi', type: 'date', required: true,
        hint: 'Aralık en çok 62 gün olabilir.' },
      { key: 'due_date', label: 'Son ödeme günü', type: 'date', required: true,
        hint: 'Dönem başlangıcından önce olamaz.' },
      { key: 'amount_kurus', label: 'Tutar (boş = hesaplat)', type: 'money' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 255 },
    ],
    value: {
      period_start: todayIso(),
      period_end: todayIso(29),
      due_date: todayIso(5),
      amount_kurus: null,
      note: '',
    },
  }));
  box.append(form.node);

  const actions = h('div', 'bsu-actions');
  actions.append(writeButton('Önce prova et', {
    onClick: () => submitPayment(form, subscription, true),
  }));
  actions.append(writeButton('Dönem borcunu aç', {
    variant: 'primary',
    onClick: () => submitPayment(form, subscription, false),
  }));
  box.append(actions);

  nodes.paymentResult = h('div', 'bsu-stack');
  box.append(nodes.paymentResult);
  return box;
}

async function submitPayment(form, subscription, dryRun) {
  const draft = form.draft();
  if (!draft.period_start || !draft.period_end || !draft.due_date) {
    toast('Dönem başlangıcı, bitişi ve son ödeme günü zorunlu.', 'bad');
    return;
  }
  const reason = await askReason({
    title: dryRun ? 'Dönem borcunu prova et' : 'Dönem borcu aç',
    description: dryRun
      ? 'Sunucu tutarı HESAPLAR ve kaç siparişten geldiğini söyler; hiçbir '
        + 'satır yazılmaz.'
      : `${draft.period_start} → ${draft.period_end} dönemi borçlandırılacak. `
        + 'Aynı dönem ikinci kez açılamaz.',
    confirmLabel: dryRun ? 'Prova et' : 'Borcu aç',
    danger: false,
  });
  if (!reason) return;

  await withBusy(dryRun ? 'Hesaplanıyor…' : 'Dönem borcu açılıyor…', async () => {
    const result = await call(`${BASE}/subscriptions/${subscription.id}/payments`, {
      method: 'POST',
      body: {
        reason,
        period_start: draft.period_start,
        period_end: draft.period_end,
        due_date: draft.due_date,
        amount_kurus: draft.amount_kurus ?? null,
        note: draft.note || '',
        dryRun,
      },
    });
    if (nodes.paymentResult) {
      nodes.paymentResult.replaceChildren();
      const body = result.would || result;
      const source = body.amount_source === 'manual' ? 'elle yazıldı' : 'hesaplandı';
      nodes.paymentResult.append(alertBox(
        `${dryRun ? 'PROVA — hiçbir şey yazılmadı. ' : ''}`
        + `Tutar: ${money(body.amount_kurus ?? result.amount_kurus ?? 0)} (${source})`
        + ` · ${num(body.order_count ?? result.order_count ?? 0)} siparişten.`,
        dryRun ? 'info' : 'good'));
    }
    announce(result, 'Dönem borcu açıldı.');
    if (!dryRun) {
      await loadPayments(subscription.id);
      paintPayments(subscription);
      await loadPicker();
    }
  });
}

/**
 * TAHSİLAT. GERİ ALMA UCU YOKTUR ve bu ekranda yazılı: yanlış işaretlenen bir
 * tahsilat yeni bir dönem kaydıyla düzeltilir, para defterinde silme yoktur.
 */
function openMarkPaid(row, subscription) {
  const box = h('div', 'bsu-stack');
  box.append(alertBox(
    'BU İŞLEMİN GERİ ALMA UCU YOKTUR. Yanlış işaretlenen bir tahsilat ancak yeni '
    + 'bir dönem kaydıyla düzeltilir; para defterinde silme yoktur. Zaten tahsil '
    + 'edilmiş bir dönemi ikinci kez işaretlemek sunucuda reddedilir — tutar iki '
    + 'kez sayılmasın diye.', 'warn'));

  const form = track(formGrid({
    fields: [
      { key: 'method', label: 'Ödeme yöntemi', type: 'select', required: true,
        options: options(state.contract?.payment_methods) },
      { key: 'reference', label: 'Referans', type: 'text', maxLength: 120,
        hint: 'Havale açıklaması, dekont numarası…' },
      { key: 'create_invoice', label: 'Fatura belgesi de üret', type: 'checkbox',
        hint: 'Varsayılan kapalı: fatura yazdırılabilir bir belgedir ve her '
          + 'tahsilatta üretmek gereksiz. Belgenin mali değeri yoktur.' },
    ],
    value: { method: 'online', reference: '', create_invoice: false },
  }));
  box.append(form.node);
  box.append(h('div', 'bsu-dim',
    'Tahsilat anı boş bırakılır ve SUNUCU şimdiyi yazar; ileri bir an '
    + 'reddedilir.'));

  const actions = h('div', 'bsu-actions');
  actions.append(writeButton('Tahsil edildi işaretle', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      const reason = await askReason({
        title: 'Tahsilatı kaydet',
        description: `${row.period_label} dönemi (${money(row.amount_kurus)}) tahsil `
          + 'edildi olarak işaretlenecek. GERİ ALMA UCU YOKTUR.',
        confirmLabel: 'İşaretle',
      });
      if (!reason) return;
      /*
       * İKİNCİ KAPI: PIN (I4).
       *
       * Gerekçe "neden yapıldı" sorusunu denetim kaydına yazar; PIN
       * "klavyenin başındaki kişi gerçekten o mu" sorusunu sorar. Bu bir
       * PARA HAREKETİ ve geri alma ucu yok — açık bırakılmış bir oturumda
       * gerekçe yazmak kimseyi durdurmaz. Asıl kapı sunucudadır
       * (`confirm_pin`, K9); buradaki yalnız kullanıcıya sorar.
       */
      const pin = await confirmWithPin(nodes.root, {
        title: 'PIN ile onayla',
        description: `${money(row.amount_kurus)} tutarındaki tahsilat kaydedilecek. `
          + 'Bu bir para hareketidir ve GERİ ALMA UCU YOKTUR.',
        confirmLabel: 'Tahsilatı kaydet',
      });
      if (!pin) return;
      await withBusy('Tahsilat yazılıyor…', async () => {
        const result = await call(`${BASE}/payments/${row.id}/mark-paid`, {
          method: 'POST',
          body: {
            reason,
            pin,
            method: draft.method,
            reference: draft.reference || '',
            create_invoice: Boolean(draft.create_invoice),
            subscription_id: subscription.id,
          },
        });
        // FATURA GERÇEKTEN KESİLDİYSE NUMARASI YAZILIR; kesilmediyse sebebi
        // `warnings` içinde geliyor ve `announce()` onu ayrı bir satırda
        // gösteriyor. Eskiden ikisi de görünmüyordu: `invoice_id` daima
        // `null` dönüyor ve uyarı `data` içine gömülü olduğu için hiçbir
        // ekrana ulaşmıyordu.
        announce(result, result.invoice_no
          ? `Tahsilat kaydedildi · fatura ${result.invoice_no}`
          : 'Tahsilat kaydedildi.');
        nodes.markPaidSlot?.replaceChildren();
        await loadPayments(subscription.id);
        paintPayments(subscription);
        await loadPicker();
      });
    },
  }));
  box.append(actions);

  nodes.markPaidSlot?.replaceChildren(card(`Tahsilat — ${row.period_label}`, box));
}

// ================================================================== durum satırı

function statusText() {
  const parts = [];
  if (state.link.missing) parts.push('UÇ DAĞITILMADI');
  else parts.push(state.link.connected ? 'Bağlı' : `KOPUK — ${state.link.error}`);

  if (state.tab === 'requests') {
    parts.push(`${num(state.requests.rows.length)} talep sayfada`
      + ` · toplam ${num(state.requests.meta.total || 0)}`);
    parts.push(`${num(state.pending.rows.length)} abonelik fiyat/sözleşme bekliyor`);
  } else if (LIST_STATUSES[state.tab]) {
    parts.push(`sayfada ${num(state.list.rows.length)}`
      + ` · toplam ${num(state.list.meta.total || 0)}`);
  }
  return parts.join(' · ');
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };
  disposables = [];

  const view = h('div', 'kit-panel bsu');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    // TALEPLER İLK SEKME: akış orada başlıyor ve kuyruk bekleyen iştir.
    { key: 'requests', label: 'Talepler' },
    { key: 'waiting', label: 'Bekleyenler' },
    { key: 'active', label: 'Aktif' },
    { key: 'paused', label: 'Duraklatılmış' },
    { key: 'cancelled', label: 'İptal edilmiş' },
    { key: 'contracts', label: 'Sözleşmeler' },
    { key: 'payments', label: 'Ödemeler' },
  ], 'requests', (key) => showTab(key));

  nodes.status = statusLine();
  nodes.body = h('div', 'bsu-body');

  const bar = h('div', 'bsu-topbar');
  bar.append(nodes.tabs.node);
  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    // SEKME DEĞİŞİNCE KİT BİLEŞENLERİ BIRAKILIR: `filterBar` ve `dateField`
    // `document` üzerinde global dinleyici tutuyor ve bırakılmazsa her sekme
    // geçişinde bir tane daha birikir — kapalı bir takvim `mousedown`
    // dinlemeye devam ederdi (kit kuralı 4).
    releaseAll();
    state.tab = key;
    ({
      requests: () => { showRequests(); loadRequests().then(paintRequests);
        loadPending().then(paintPending); },
      waiting: () => showList('waiting'),
      active: () => showList('active'),
      paused: () => showList('paused'),
      cancelled: () => showList('cancelled'),
      contracts: () => showPicker('contracts'),
      payments: () => showPicker('payments'),
    }[key] || (() => showRequests()))();
  }

  root.replaceChildren(view);

  // AÇILIŞ SIRASI ÖNEMLİ: önce sözleşme (ağa çıkmaz), sonra sekme. Hafta günü
  // çipleri, durum seçenekleri ve etiketler sözleşmeden çizilir; sözleşme
  // gelmeden çizilen bir seçim kutusu, sunucudaki kod listesinden ayrışmış
  // olurdu.
  (async () => {
    try {
      const payload = await call(`${BASE}/overview`);
      linkOk(payload);
      state.contract = payload.contract;
      state.prefs = payload.prefs;
      state.limits = payload.limits;
      /*
       * ŞUBE LİSTESİ AÇILIŞTA OKUNUR VE ÖNBELLEKLİDİR (geçit L1).
       *
       * Formun içinden okunsaydı her talep açılışında bir istek daha giderdi;
       * abonelik açma formu ise kuyruktaki her talebe tıklandığında çiziliyor.
       * Liste bu panelden yazılmıyor ve saatte bir değişmiyor.
       */
      await loadLocations();
    } catch (failure) {
      // Sözleşme ucu ağa çıkmıyor; buraya düşmek çekirdeğin kendisiyle ilgili
      // bir sorundur ve ekranın geri kalanı yine de çizilmeli.
      state.error = failure.message;
      nodes.body.append(alertBox(failure.message, 'bad'));
    }
    showTab('requests');
  })();

  // TEMİZLİK GERÇEK KAYNAK BIRAKIR (kit kuralı 4): `filterBar` takvim ve arama
  // için, `formGrid` içindeki tarih alanları da kendi takvimleri için global
  // dinleyici tutuyor. Bırakılmazsa panel her açılışta bir tane daha birikir.
  //
  // YOKLAMA DÖNGÜSÜ YOK ve bu bilinçli: abonelik saatler-günler ölçeğinde
  // değişir. 15 saniyede bir yoklayan bir ekran, paylaşılan `bld-control-panel`
  // kovasını (3000/saat/IP, TÜM BLD ekranları için) hiçbir şey öğrenmeden yakar
  // ve ikinci bir yöneticinin ekranını "çok istek" hatasına düşürürdü.
  return () => {
    releaseAll();
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
