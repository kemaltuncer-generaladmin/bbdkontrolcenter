// Satış Ayarları paneli — satışı açan, kapatan ve kurallarını belirleyen ekran.
//
// NE YAPAR: satış şalteri (durdur / aç, süreli ya da süresiz, müşteriye ayrı
// mesaj); sabah kesim saati, ileri gün sınırı; minimum sepet tutarı ve teslimat
// ücreti; ödeme yöntemleri; yoğunluk anahtarı ve müşteriye görünen metni;
// hazırlık / teslimat / yoğunluk dakikaları; günün menüsü rejimi ve otomatik
// fatura belgesi; kapalı günler (takvim + liste); bugünün ve yarının hızlı stok
// tavanları; bu ekranın yerel denetim izi.
//
// NE YAPMAZ:
//  · GÜN BAZLI KESİM SAATİNİ değiştirmez. Buradaki `order_cutoff` GENEL
//    varsayılandır; bir servis gününe özel saat Günlük Menü ekranının işidir
//    (`DailyMenuDay.cutoff_time`) ve doluysa buradakini EZER. Sunucudaki
//    birleştirme kuralı tektir: `gün ?? ayar`.
//  · SATIŞ ŞALTERİNİ ayar formundan çevirmez. `ordering_enabled` bu ekranın
//    ayar ucundan yazılamaz; kendi düğmeleri ve kendi izni vardır. Şalteri
//    gerekçesiz ve süresiz çevirmek, tam da durdurmanın en sık hatasını
//    (açmayı unutmak) üretirdi.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//  · GÜNÜN MENÜSÜNÜ kurmaz, kalem eklemez. Stok şeridi yalnız TAVAN yazar.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · KURU PROVA AÇIK BİR EYLEMDİR, ÖRTÜK BİR KİP DEĞİL. "Önizle" düğmesi
//    sunucudan eski/yeni tablosunu ister ve ekran hiçbir şeyin yazılmadığını
//    YAZAR; "Kaydet" her zaman gerçek yazmadır. Bu ekranda örtük bir kuru
//    prova, başarıdan ayırt edilemeyen bir sessiz no-op demekti.
//  · YOĞUNLUK YARIŞI. `busy` anahtarını MUTFAK EKRANI da değiştiriyor. Panel
//    yalnız DEĞİŞEN alanları gönderir (`formGrid.patch()`) ve okuma anındaki
//    taban çizgisinin jetonunu geri yollar; sunucu tarafındaki servis, yazılan
//    alanların aradan değişip değişmediğine ona bakarak karar verir. Ekran
//    ayrıca arka planda yokluyor ve mutfak anahtarı çevirdiğinde uyarıyor.
//  · BOŞ KESİM SAATİ GEÇERLİDİR ve "kesim yok" demektir. Sunucu bunu sipariş
//    alımını hiç kapatmamak diye okur; siparişler de o günün içinde anında
//    mutfağa düşer.
//  · SİPARİŞİN MUTFAĞA DÜŞTÜĞÜ AN BİR AYAR DEĞİL, TÜRETİLMİŞ BİLGİDİR.
//    Eskiden `subscription_release_time` diye ayrı bir kutu vardı; 17.08.2026'da
//    sunucudan kaldırıldı ve buradan da kalktı. Cevap artık kesim saatinden
//    okunur ve ekranda kesim saatinin yanındaki ipucu kutusu bunu yazar. Ayrı
//    bir kutu iki doğruluk kaynağı demekti: kesimi 09:00'a çekip düşme saatini
//    07:00'de unutan yönetici, mutfağa satış hâlâ açıkken eksik bir listeyi tam
//    diye gösterirdi.
//  · `busy` SATIŞI KESMEZ, yalnız müşteriye "hazırlanması uzun sürebilir"
//    uyarısı gösterir. Satışı kesen şey durdurmadır ve ayrı izin ister.
//  · STOK YAZMASI TAM LİSTEDİR. Ekrandaki bütün kalemler birlikte gider;
//    eksik gönderim o kalemin tavanını sunucuda sessizce kaldırırdı.
//  · KAPALI GÜN SİLME GERÇEK SİLMEDİR (sözleşme). Kit'in "silme yok,
//    pasifleştirme var" kuralı burada uygulanamıyor çünkü sözleşme
//    pasifleştirme alanı tanımlamıyor; kuralın koruduğu geri izlenebilirlik
//    gerekçeli onay ve iki denetim satırıyla karşılanıyor.
//  · PARA HER YERDE KURUŞ. Gösterimde `money()`, girişte `moneyInput()`.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_sales_settings/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_sales_settings/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, confirmWithReason, h, loadStyles, money, num,
  pollLoop, stampIso, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { formGrid } from '../../ui-kit/form.js';
import { dateField } from '../../ui-kit/datefield.js';
import { monthCalendar } from '../../ui-kit/calendar.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';

const BASE = '/api/bld_sales_settings';

/** Gerekçe sınırları — sunucu da denetliyor (00-genel.md §3), bu erken geri bildirim. */
const REASON_MIN = 10;
const REASON_MAX = 500;

/** `HH:mm` — sözleşmedeki kalıbın birebir aynısı. */
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/**
 * Sunucu tazeleme aralığını bildirmezse kullanılacak yedek (ms).
 *
 * Asıl değer `GET /sales` yanıtındaki `poll_seconds`'tan gelir: aralığı ayar
 * dosyasında bir, ekranda başka bir sayı olarak tutmak iki farklı gerçek
 * üretirdi.
 */
const FALLBACK_POLL_MS = 60000;

/** Ödeme yöntemleri — `account` YOKTUR, cari hesap kaldırıldı (iş kararı 1). */
const PAYMENT_LABELS = { online: 'Online ödeme', cash: 'Kapıda nakit' };

/**
 * Alanların Türkçe adları; değişiklik tablosu ve çakışma cümlesi bunu okur.
 *
 * `subscription_release_time` BURADA YOKTUR ve olmayacak: ayar 17.08.2026'da
 * sunucudan kaldırıldı. Etiketi burada bırakmak, bir daha hiç gelmeyecek bir
 * alanın adını fark tablosunda diri tutardı.
 */
const FIELD_LABELS = {
  order_cutoff: 'Sabah kesim saati',
  max_lookahead_days: 'İleri gün sınırı',
  min_order_total_kurus: 'Minimum sepet tutarı',
  delivery_fee_kurus: 'Teslimat ücreti',
  payment_methods: 'Ödeme yöntemleri',
  busy: 'Yoğunluk anahtarı',
  busy_message: 'Yoğunluk mesajı',
  prep_minutes: 'Hazırlık süresi',
  delivery_minutes: 'Teslimat süresi',
  busy_extra_minutes: 'Yoğunlukta ek süre',
  daily_menu_enabled: 'Günün menüsü rejimi',
  auto_invoice: 'Otomatik fatura belgesi',
  // Formdan YAZILAMAZ ama değiştiğinde yöneticinin bilmesi gerekir: başka bir
  // yönetici satışı durdurmuş olabilir ve ekranın tepesindeki "Açık" yazısı
  // artık doğru değildir.
  ordering_enabled: 'Satış şalteri',
  paused_until: 'Durdurma bitişi',
};

/** Para alanları — gösterimi `money()` ile yapılır. */
const MONEY_FIELDS = new Set(['min_order_total_kurus', 'delivery_fee_kurus']);

const EMPTY_STATE = {
  tab: 'settings',
  link: { connected: true, error: '' },

  // `sales` = formun ÜSTÜNE YAZDIĞI taban (kullanıcının gördüğü hâl).
  // `live`  = arka plan yoklamasının bildiği en taze hâl.
  // `baseline` = jetonun işaret ettiği 12 yazılabilir alan; fark buna göre.
  sales: {},
  live: {},
  baseline: {},
  meta: {},
  limits: {},
  writableFields: [],
  token: '',
  pollMs: FALLBACK_POLL_MS,
  readAt: '',
  salesLoaded: false,
  salesError: '',
  // Mutfak / başka bir yönetici aradan değiştirdiyse: [{field, label, from, to}]
  drift: [],

  closed: [],
  closedLoaded: false,
  closedError: '',
  closedStale: false,

  stock: [],
  stockLoaded: false,
  stockError: '',
  // Kullanıcının ekranda yaptığı tavan düzenlemeleri: iso → {total, items:{id:val}}
  stockEdits: {},

  audit: [],
  auditLoaded: false,
  auditError: '',

  changes: [],       // son önizlemenin / kaydın değişiklik tablosu
  changesTitle: '',
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};

/**
 * SEKME İÇİ kaynak bırakıcılar — her çizimde önce çalıştırılıp temizlenir.
 *
 * `formGrid`, `dateField` ve `monthCalendar` global dinleyici ya da nesne
 * tutuyor ve bu ekran kendini sık yeniden çiziyor (her kayıttan sonra). Tek
 * bir listeye biriktirseydik, panel açık kaldıkça aynı sekmenin onlarca ölü
 * takvimi bellekte kalırdı.
 */
const sectionClosers = [];
let live = null;           // pollLoop kolu
let livePeriod = 0;        // kurulu döngünün aralığı (ms)

function releaseSection() {
  while (sectionClosers.length) {
    const release = sectionClosers.pop();
    try { release(); } catch { /* kapanışta hata yutulur */ }
  }
}

// ------------------------------------------------------------------ araçlar

/**
 * Sunucu iki türlü hata döndürebilir: HTTP durumu (kabuk `api()` fırlatır) ve
 * gövdedeki `{ok:false, error}`. İkincisi burada tek yerde okunur.
 */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) {
    const raw = result.error;
    const message = typeof raw === 'string' ? raw : (raw.message || 'İşlem başarısız.');
    const error = new Error(message);
    error.code = typeof raw === 'string' ? '' : (raw.code || '');
    error.payload = result;
    throw error;
  }
  return result;
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * Geçit ya da BLD sunucusu düştüğünde okuma uçları
 * `{ok:true, connected:false, error:"…"}` döndürür. Bu bir HATA DEĞİL, bir
 * DURUMDUR: ayar gerçekten yok değil, ŞU AN OKUNAMIYOR. Sessizce boş bir form
 * çizmek yanlış olurdu — yönetici "ayar boş" ile "sunucuya ulaşılamıyor"u
 * ayırt edemez ve boş sandığı alanı doldurup kaydetmeye çalışır.
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = {
      connected: false,
      error: payload.error || 'BLD sunucusuna ulaşılamıyor.',
    };
    return false;
  }
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

/** Bağlantı kopukken çizilen uyarı; bağlantı varken `null`. */
function linkAlert({ stale = false, what = 'Ayarlar', auto = false } = {}) {
  if (state.link.connected) return null;
  const tail = auto
    ? 'Tazeleme sürüyor; bağlantı geri geldiğinde ekran KENDİLİĞİNDEN düzelir.'
    : 'Bağlantı geri geldiğinde “Yenile” ile tazeleyin.';
  return alertBox(
    `BLD sunucusuna ULAŞILAMIYOR — ${state.link.error} `
    + (stale
      ? `${what} son başarılı okumadan kalma ve BAYAT: satış şalteri, yoğunluk `
        + 'anahtarı ve tavanlar burada GÖRÜNDÜĞÜ gibi olmayabilir. '
      : `${what} okunamadı. `)
    + tail, 'bad');
}

/** Yazma düğmesinin kapalı olma nedeni; yazılabiliyorsa boş dize. */
function linkBlock() {
  if (state.link.connected) return '';
  return `BLD sunucusuna ulaşılamıyor (${state.link.error}) — ulaşılamayan bir `
    + 'sunucuya gönderilen ayar yazması yöneticiye "kaydedildi" hissi verirdi. '
    + 'Bağlantı gelince düğme kendiliğinden açılır.';
}

/**
 * Yazma düğmesi. Bağlantı yoksa ya da `blocked` doluysa KAPALI çizilir ve
 * nedenini söyler — sessizce çalışmayan düğme bırakılmaz (kit README).
 */
function writeButton(label, { variant = '', title = '', onClick, blocked = '' } = {}) {
  const why = blocked || linkBlock();
  if (why) return blockedButton(label, why, { variant });
  return button(label, { variant, title, onClick });
}

/**
 * Yerel gün + saatten ISO 8601 UTC ANI üretir.
 *
 * `toISOString()` YASAĞI GÜN DİZELERİ İÇİNDİR (kit kuralı 6): bir günü UTC'ye
 * çevirmek tarihi kaydırır. Burada üretilen şey bir GÜN değil bir ANDIR —
 * yönetici "bugün 18:00'e kadar" diyor ve o duvar saatinin UTC karşılığı tam
 * olarak budur (`00-genel.md` §6: anlar UTC, gün içi saatler yereldir).
 */
function utcInstant(iso, time) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || '') || !TIME_RE.test(time || '')) return '';
  const [year, month, day] = iso.split('-').map(Number);
  const [hour, minute] = time.split(':').map(Number);
  const local = new Date(year, month - 1, day, hour, minute, 0, 0);
  if (Number.isNaN(local.getTime())) return '';
  return local.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** Değeri değişiklik tablosunda okunur kılar. */
function showValue(field, value) {
  if (field === 'ordering_enabled') return value ? 'Satış açık' : 'SATIŞ DURDURULDU';
  if (value === null || value === undefined || value === '') return '—';
  if (field === 'payment_methods') {
    const list = Array.isArray(value) ? value : [value];
    return list.map((item) => PAYMENT_LABELS[item] || item).join(' + ') || '—';
  }
  if (MONEY_FIELDS.has(field)) return money(value);
  if (typeof value === 'boolean') return value ? 'Açık' : 'Kapalı';
  if (field.endsWith('_minutes')) return `${num(value)} dk`;
  if (field === 'max_lookahead_days') return `${num(value)} gün`;
  return String(value);
}

/** Sunucunun bildirdiği varsayılan; alan boşaltılırsa bu geçerli olur. */
function defaultHint(field) {
  const defaults = state.meta?.defaults || {};
  if (!(field in defaults)) return '';
  return `Boş bırakılırsa sunucu varsayılanı geçerli: ${showValue(field, defaults[field])}`;
}

/** Ekranın yazılabilir alan listesi — sunucudan gelir, panele gömülmez. */
function writableFields() {
  const list = state.writableFields;
  return Array.isArray(list) && list.length ? list : Object.keys(FIELD_LABELS);
}

// ==================================================================== okumalar

/**
 * FORMU KURAN okuma. Taban çizgisi jetonunu ve o andaki değerleri alır.
 *
 * Yalnız kullanıcı isteğiyle çağrılır (açılış, "Formu tazele", kayıttan
 * sonra): formu altından değiştiren bir okuma, yarısını doldurduğu kutuları
 * silmek olurdu.
 */
async function loadSales() {
  try {
    const payload = await call(`${BASE}/sales`);
    if (!linkOk(payload)) {
      state.salesError = '';
      state.salesLoaded = true;
      return;
    }
    state.sales = payload.data || {};
    state.live = payload.data || {};
    state.meta = payload.meta || {};
    state.limits = payload.limits || {};
    state.writableFields = payload.writable_fields || [];
    state.token = payload.baseline_token || '';
    // Formun AÇILDIĞI andaki hâl. Yoklama farkı buna göre hesaplanır; iki
    // yoklama arasındaki farka göre değil — iki tur önce olan bir değişiklik
    // aksi hâlde ekrandan sessizce silinirdi. Kopyadır: `state.sales` ile aynı
    // nesneyi paylaşsalardı, biri güncellendiğinde fark her zaman boş çıkardı.
    state.baseline = { ...(payload.data || {}) };
    state.pollMs = Math.max(15000, (Number(payload.poll_seconds) || 60) * 1000);
    state.readAt = new Date().toISOString();
    state.salesLoaded = true;
    state.salesError = '';
    state.drift = [];
  } catch (error) {
    state.salesError = error.message;
    state.salesLoaded = true;
  }
}

/**
 * YOKLAMA okuması. Formu ELLEMEZ ve YENİ JETON ÜRETMEZ (`baseline=false`).
 *
 * İşi tek: yönetici formda çalışırken mutfak yoğunluk anahtarını çevirdiyse
 * ya da başka bir yönetici bir alanı değiştirdiyse bunu YAZMAK. Sunucu zaten
 * yazmayı reddedecek (taban çizgisi karşılaştırması), ama reddi "Kaydet"
 * düğmesine bastıktan sonra öğrenmek geç olurdu.
 *
 * Yeni jeton üretseydi tabanı sessizce "şu an"a çeker ve yarış denetimini
 * işlevsiz bırakırdı — tam da engellemek için var olduğu şeyi.
 */
async function probeSales() {
  try {
    const payload = await call(`${BASE}/sales?baseline=0`);
    if (!linkOk(payload)) return;
    state.live = payload.data || {};
    state.readAt = new Date().toISOString();
    state.drift = driftBetween(state.baseline || {}, state.live);
  } catch (error) {
    // Yoklama hatası formu bozmaz; durum satırı zaten bağlantıyı yazıyor.
    state.link = { connected: false, error: error.message };
  }
}

/**
 * Yoklamanın izlediği alanlar: yazılabilir 12'si + satış şalteri.
 *
 * Şalter bu formdan YAZILAMAZ ama başka bir yönetici onu çevirmiş olabilir ve
 * ekranın tepesindeki "Satış açık" yazısı o an yanlış olur. Yalnız yazılabilir
 * alanlara bakmak, bu ekrandaki en önemli değişikliği görmezden gelmek olurdu.
 */
function watchedFields() {
  return [...writableFields(), 'ordering_enabled', 'paused_until'];
}

/** İki ayar görüntüsü arasındaki fark — izlenen alanlar. */
function driftBetween(before, after) {
  const rows = [];
  for (const field of watchedFields()) {
    const left = JSON.stringify(before[field] ?? null);
    const right = JSON.stringify(after[field] ?? null);
    if (left === right) continue;
    rows.push({ field, label: FIELD_LABELS[field] || field,
      from: before[field], to: after[field] });
  }
  return rows;
}

async function loadClosedDays() {
  try {
    const payload = await call(`${BASE}/closed-days`);
    if (!linkOk(payload)) {
      state.closedStale = state.closed.length > 0;
      state.closedLoaded = true;
      return;
    }
    state.closed = payload.items || [];
    state.closedStale = false;
    state.closedError = '';
    state.closedLoaded = true;
  } catch (error) {
    state.closedError = error.message;
    state.closedLoaded = true;
  }
}

async function loadStock() {
  try {
    const payload = await call(`${BASE}/stock`);
    state.stock = payload?.days || [];
    state.stockError = '';
    state.stockLoaded = true;
    if (payload && payload.connected === false) {
      state.link = { connected: false, error: payload.error || 'BLD sunucusuna ulaşılamıyor.' };
    }
  } catch (error) {
    state.stockError = error.message;
    state.stockLoaded = true;
  }
}

async function loadAudit() {
  try {
    const payload = await call(`${BASE}/audit?limit=200`);
    state.audit = payload?.items || [];
    state.auditError = payload?.error || '';
    state.auditLoaded = true;
  } catch (error) {
    state.auditError = error.message;
    state.auditLoaded = true;
  }
}

// ============================================================== ayarlar sekmesi

/**
 * Ayar formu DÖRT BÖLÜMDÜR ve her bölüm kendi `formGrid`'ini taşır.
 *
 * Tek bir dev ızgara da çizilebilirdi ama on iki alanın hangisinin neyi
 * etkilediği kaybolurdu: kesim saati ile teslimat ücreti aynı kutuda yan yana
 * durunca, "yoğunluk" anahtarının satışı KESMEDİĞİ de görünmez oluyor. Kirli
 * alan takibi bölüm başına yapılır ve kaydederken birleştirilir.
 */
function buildForms() {
  const data = state.sales || {};
  const limits = state.limits || {};
  const methods = Array.isArray(data.payment_methods) ? data.payment_methods : [];
  const shared = {
    ...data,
    pay_online: methods.includes('online'),
    pay_cash: methods.includes('cash'),
  };

  // ZORUNLULUK SEÇENEĞİ YOKTUR ve bilerek: bu formdaki tek saat alanı kesim
  // saatidir ve BOŞ BIRAKILABİLİR ("kesim yok"). `required: true` ile çizilen
  // bir saat alanı, sunucudan hiç gelmeyen bir anahtara denk düştüğü an bütün
  // formu doğrulamadan geçmez yapıyordu — tek bir alan değil, ekranın tamamı
  // kaydedilemez oluyordu (17.08.2026, `subscription_release_time`).
  const timeField = (key, label, { hint = '' } = {}) => ({
    key,
    label,
    type: 'text',
    placeholder: 'ss:dd',
    maxLength: 5,
    hint,
    // `<input type="date">` YASAK ve zaten yanlış araç olurdu: bu bir GÜN
    // değil, gün içi bir SAAT (Europe/Istanbul). `dateField()` de tarih
    // seçtirir; saat için doğru alan denetimli bir metin kutusudur.
    validate: (value) => {
      const text = String(value ?? '').trim();
      if (!text) return null;
      return TIME_RE.test(text) ? null : '08:00 gibi HH:mm biçiminde yazın.';
    },
  });

  const forms = {};

  forms.rules = formGrid({
    value: shared,
    onChange: () => paintDirty(),
    fields: [
      timeField('order_cutoff', 'Sabah kesim saati', {
        hint: 'Boş bırakılırsa kesim saati yoktur. Bir servis gününe özel saat '
          + 'girildiyse (Günlük Menü ekranı) o gün için BURASI GEÇERSİZDİR.',
      }),
      {
        key: 'max_lookahead_days',
        label: 'İleri gün sınırı',
        type: 'number',
        min: 0,
        max: Number(limits.max_lookahead_days) || 7,
        hint: `0–${Number(limits.max_lookahead_days) || 7} gün. Sıfır geçerlidir ve `
          + '“yalnız bugüne sipariş” demektir.',
      },
    ],
  });

  forms.money = formGrid({
    value: shared,
    onChange: () => paintDirty(),
    fields: [
      {
        key: 'min_order_total_kurus',
        label: 'Minimum sepet tutarı',
        type: 'money',
        min: 0,
        hint: 'Bu tutarın altındaki sepet siparişe dönüşmez.',
      },
      {
        key: 'delivery_fee_kurus',
        label: 'Teslimat ücreti',
        type: 'money',
        min: 0,
        hint: 'Gel-al siparişte uygulanmaz.',
      },
      {
        key: 'pay_online',
        label: PAYMENT_LABELS.online,
        type: 'checkbox',
        hint: 'En az bir ödeme yöntemi açık olmalı.',
      },
      {
        key: 'pay_cash',
        label: PAYMENT_LABELS.cash,
        type: 'checkbox',
        // Cari hesap Faz 0'da kaldırıldı; listede üçüncü bir kutu YOKTUR ve
        // olmayacak. Sözleşme `account` değerini 422 ile reddediyor.
        hint: 'Cari hesap kaldırıldı; üçüncü bir ödeme yöntemi yoktur.',
      },
    ],
  });

  forms.busy = formGrid({
    value: shared,
    onChange: () => paintDirty(),
    fields: [
      {
        key: 'busy',
        label: 'Yoğunluk uyarısı açık',
        type: 'checkbox',
        hint: 'SATIŞI KESMEZ — yalnız müşteriye “hazırlanması uzun sürebilir” yazar. '
          + 'Satışı kesmek için aşağıdaki “Satışı durdur” düğmesi kullanılır.',
      },
      {
        key: 'busy_message',
        label: 'Müşteriye görünen yoğunluk metni',
        type: 'textarea',
        wide: true,
        maxLength: Number(state.limits?.busy_message_max) || 500,
        hint: defaultHint('busy_message') || 'Boş bırakılırsa sunucunun varsayılan metni gösterilir.',
      },
      {
        key: 'prep_minutes',
        label: 'Hazırlık süresi (dk)',
        type: 'number',
        min: Number(limits.minute_min) || 1,
        max: Number(limits.minute_max) || 480,
        hint: defaultHint('prep_minutes'),
      },
      {
        key: 'delivery_minutes',
        label: 'Teslimat süresi (dk)',
        type: 'number',
        min: Number(limits.minute_min) || 1,
        max: Number(limits.minute_max) || 480,
        hint: defaultHint('delivery_minutes'),
      },
      {
        key: 'busy_extra_minutes',
        label: 'Yoğunlukta ek süre (dk)',
        type: 'number',
        min: Number(limits.minute_min) || 1,
        max: Number(limits.minute_max) || 480,
        hint: defaultHint('busy_extra_minutes'),
      },
    ],
  });

  forms.regime = formGrid({
    value: {
      ...shared,
      display_is_open: data.is_open ? 'Çalışma saati içinde' : 'Çalışma saati dışında',
      display_package: data.daily_package_menu_id ?? null,
    },
    onChange: () => paintDirty(),
    fields: [
      {
        key: 'daily_menu_enabled',
        label: 'Günün menüsü rejimi açık',
        type: 'checkbox',
      },
      {
        key: 'auto_invoice',
        label: 'Teslimde fatura belgesi otomatik üretilsin',
        type: 'checkbox',
        hint: 'Fatura belgesinin mali değeri yoktur; müşteriye verilen bir dökümdür.',
      },
      {
        key: 'display_is_open',
        label: 'Şu an çalışma saati',
        type: 'static',
        // SALT OKUNUR ve yazılamaz: çalışma saatlerinden TÜRETİLİR. Alanı
        // düzenlenebilir çizmek, gönderildiğinde 422 alan bir kutu olurdu.
        hint: 'Çalışma saatlerinden türetilir; buradan yazılamaz.',
      },
      {
        key: 'display_package',
        label: 'Günün menüsü paket ürünü',
        type: 'static',
        hint: 'Göç tarafından yazılır ve buradan DEĞİŞTİRİLEMEZ: yanlış bir kimlik '
          + 'günün menüsünü sıfır liraya sattırırdı.',
      },
    ],
  });

  for (const grid of Object.values(forms)) sectionClosers.push(() => grid.destroy());
  return forms;
}

/**
 * Bölümlerin kirli alanlarını TEK gövdede birleştirir.
 *
 * Yalnız sunucunun yazılabilir dediği alanlar geçer: ekrandaki iki gösterim
 * alanı (`display_*`) ve ödeme kutularının ham hâli gövdeye GİRMEZ. Süzgeç
 * sunucudan gelen listeye bakar, panele gömülü bir listeye değil.
 */
function collectPatch() {
  const merged = {};
  for (const grid of Object.values(nodes.forms || {})) Object.assign(merged, grid.patch());

  if ('pay_online' in merged || 'pay_cash' in merged) {
    const draft = nodes.forms.money.draft();
    const methods = [];
    if (draft.pay_online) methods.push('online');
    if (draft.pay_cash) methods.push('cash');
    merged.payment_methods = methods;
  }

  const allowed = new Set(writableFields());
  const body = {};
  for (const [key, value] of Object.entries(merged)) {
    if (allowed.has(key)) body[key] = value;
  }
  return body;
}

/** Gönderilecek gövdeden okunur değişiklik tablosu üretir (istemci tarafı). */
function plannedChanges(body) {
  const data = state.sales || {};
  const rows = [];
  for (const [field, value] of Object.entries(body)) {
    rows.push({ field, label: FIELD_LABELS[field] || field, from: data[field], to: value });
  }
  return rows;
}

function formsValid() {
  let ok = true;
  for (const grid of Object.values(nodes.forms || {})) {
    if (!grid.valid()) { grid.showErrors(); ok = false; }
  }
  const draft = nodes.forms?.money?.draft() || {};
  if (!draft.pay_online && !draft.pay_cash) {
    toast('En az bir ödeme yöntemi açık olmalı — hiçbiri olmayan bir satış kanalı, '
      + 'sipariş formunun son adımında çuvallar.', 'bad');
    ok = false;
  }
  return ok;
}

/** Kirli alan sayısını düğmelerin yanına yazar. */
function paintDirty() {
  if (!nodes.dirtyLine) return;
  const body = collectPatch();
  const count = Object.keys(body).length;
  nodes.dirtyLine.textContent = count
    ? `${count} alan değişti; kaydedilmedi.`
    : 'Değişen alan yok.';
  nodes.dirtyLine.classList.toggle('on', count > 0);
}

function showSettings() {
  const body = nodes.body;
  // Önceki çizimin takvimi / tarih alanı / formları GERÇEKTEN bırakılır:
  // hepsi global dinleyici tutuyor ve bu ekran her kayıttan sonra yeniden
  // çiziliyor.
  releaseSection();
  body.replaceChildren();
  // Eski çizimin düğümlerine yazmayı önler: `paintDirty()` form kurulurken de
  // çağrılabiliyor ve o an ekranda olmayan bir sayaca yazmak, kullanıcının
  // gördüğü sayıyı sessizce yanlış bırakırdı.
  nodes.forms = null;
  nodes.dirtyLine = null;
  nodes.driftBox = null;
  nodes.changesBox = null;

  if (!state.salesLoaded) {
    body.append(skeletonRows(8, 2));
    refreshSales();
    return;
  }

  const warning = linkAlert({ stale: Object.keys(state.sales).length > 0, auto: true });
  if (warning) body.append(warning);
  if (state.salesError) {
    body.append(emptyState({
      title: 'Satış ayarları okunamadı',
      text: state.salesError,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshSales() })],
    }));
    return;
  }

  const data = state.sales || {};
  const paused = data.ordering_enabled === false;

  body.append(kpiRow([
    {
      label: 'Satış',
      value: paused ? 'DURDURULDU' : 'Açık',
      tone: paused ? 'bad' : 'good',
      title: paused
        ? 'Müşteri sipariş veremiyor.'
        : 'Müşteri sipariş verebiliyor (çalışma saatleri ve kesim saati geçerli).',
    },
    { label: 'Sabah kesim saati', value: data.order_cutoff || 'yok',
      title: 'Bir servis gününe özel saat girildiyse o gün için bu değer geçersizdir.' },
    { label: 'İleri gün sınırı', value: `${num(data.max_lookahead_days ?? 0)} gün` },
    {
      label: 'Yoğunluk',
      value: data.busy ? 'Açık' : 'Kapalı',
      tone: data.busy ? 'warn' : '',
      title: 'Yoğunluk uyarısı satışı KESMEZ; yalnız müşteriye uyarı gösterir.',
    },
    { label: 'Ödeme', value: (data.payment_methods || []).length
      ? (data.payment_methods || []).map((item) => PAYMENT_LABELS[item] || item).join(' + ')
      : 'yok', tone: (data.payment_methods || []).length ? '' : 'bad' },
  ]));

  if (paused) {
    const until = data.paused_until
      ? `${stampIso(data.paused_until)} (${ago(data.paused_until)})`
      : 'SÜRESİZ — elle açılana kadar';
    body.append(alertBox(
      `Satış durduruldu. Bitiş: ${until}. `
      + (data.pause_reason
        ? `Müşteriye gösterilen mesaj: “${data.pause_reason}”`
        : 'Müşteriye özel mesaj girilmemiş; istemciler kendi genel metnini gösteriyor.'),
      'bad'));
  }

  // ---------------------------------------------------------- satış şalteri
  nodes.pauseDate = dateField({
    value: '',
    label: 'Durdurma bitiş günü',
    onChange: () => {},
  });
  sectionClosers.push(() => nodes.pauseDate.destroy());

  const pauseTime = h('input', 'kit-input bss-time');
  pauseTime.type = 'text';
  pauseTime.placeholder = 'ss:dd';
  pauseTime.maxLength = 5;

  const pauseMessage = h('textarea', 'kit-textarea');
  pauseMessage.maxLength = Number(state.limits?.customer_message_max) || 300;
  pauseMessage.placeholder = 'Müşteriye gösterilecek mesaj (isteğe bağlı)';

  const switchBox = h('div', 'bss-switch');
  const switchFields = h('div', 'bss-switch-fields');
  switchFields.append(
    labelled('Durdurma bitiş günü', nodes.pauseDate.node),
    labelled('Bitiş saati', pauseTime),
    labelled('Müşteriye gösterilecek mesaj', pauseMessage, true),
  );
  const switchActions = h('div', 'bss-actions');
  if (paused) {
    switchActions.append(writeButton('Satışı yeniden aç', {
      variant: 'primary',
      onClick: () => doResume(),
    }));
    switchActions.append(writeButton('Durdurmayı güncelle', {
      title: 'Bitiş saatini ve müşteri mesajını değiştirir; süreyi uzatmak olağan bir eylemdir.',
      onClick: () => doPause(pauseTime, pauseMessage),
    }));
  } else {
    switchActions.append(writeButton('Satışı durdur', {
      variant: 'danger',
      onClick: () => doPause(pauseTime, pauseMessage),
    }));
  }
  switchBox.append(switchFields, switchActions);

  body.append(card('Satış şalteri', switchBox,
    'Yoğunluk anahtarıyla karıştırmayın: bu satışı GERÇEKTEN keser.'));
  body.append(hintBox(
    'Bitiş günü ve saati boş bırakılırsa durdurma SÜRESİZDİR ve satış elle açılana '
    + 'kadar kapalı kalır. Süre dolduğunda satış kendiliğinden açılır — arka planda '
    + 'bir iş yoktur, sunucu okuma anında karşılaştırır. Buradaki gerekçe DENETİM '
    + 'kaydına gider ve müşteriye GÖSTERİLMEZ; müşterinin göreceği metin yukarıdaki '
    + 'kutudur.'));

  // -------------------------------------------------------------- ayar formu
  nodes.forms = buildForms();

  // İpucu kesim saatinin YANINDA, aynı kartın içinde duruyor: "sipariş mutfağa
  // ne zaman düşer" sorusunun cevabı artık bir AYAR DEĞİL, bu alandan TÜRETİLEN
  // bilgi. Ayrı bir yere koysaydık yönetici kesim saatini değiştirirken düşme
  // anını da değiştirdiğini görmezdi.
  const rulesBox = h('div', 'bss-rules');
  rulesBox.append(nodes.forms.rules.node, hintBox(
    'SİPARİŞ MUTFAĞA NE ZAMAN DÜŞER: kesim saatinde. Ayrı bir “serbest bırakma '
    + 'saati” ayarı YOKTUR — 17.08.2026’da kaldırıldı, çünkü ikinci bir saat iki '
    + 'doğruluk kaynağı demekti (kesimi 09:00’a çekip düşme saatini 07:00’de '
    + 'unutan yönetici, satış hâlâ açıkken mutfağa eksik bir listeyi tam diye '
    + 'gösterirdi). Kural kanaldan bağımsızdır — web, mobil, panel ve abonelik '
    + 'üretimi aynı yoldan geçer: servis günü BUGÜN ise sipariş ANINDA görünür; '
    + 'ileri bir güne verilmişse O GÜNÜN kesim anında görünür; kesim saati '
    + 'tanımsızsa (bu alan boş ve güne özel saat de yoksa) yine anında görünür. '
    + 'Bir servis gününe özel saat girildiyse (Günlük Menü ekranı) o gün için '
    + 'geçerli olan odur.'));
  body.append(
    card('Sipariş kuralları', rulesBox),
    card('Tutarlar ve ödeme', nodes.forms.money.node),
    card('Yoğunluk ve süreler', nodes.forms.busy.node,
      'Yoğunluk uyarısı satışı kesmez.'),
    card('Rejim', nodes.forms.regime.node),
  );

  nodes.driftBox = h('div', 'bss-drift');
  paintDrift();
  body.append(nodes.driftBox);

  nodes.dirtyLine = h('div', 'bss-dirty', 'Değişen alan yok.');
  const actions = h('div', 'bss-actions');
  actions.append(
    writeButton('Önizle', {
      title: 'Sunucu eski ve yeni değeri yan yana verir. HİÇBİR ŞEY YAZILMAZ.',
      onClick: () => doSaveSettings({ preview: true }),
    }),
    writeButton('Kaydet', {
      variant: 'primary',
      title: 'Değişen alanları yazar. Bu gerçek bir yazmadır.',
      onClick: () => doSaveSettings({ preview: false }),
    }),
    button('Formu tazele', {
      title: 'Sunucudaki güncel hâli okur; kaydedilmemiş değişiklikler kaybolur.',
      onClick: () => refreshSales(),
    }),
  );
  const foot = h('div', 'bss-foot');
  foot.append(nodes.dirtyLine, actions);
  body.append(foot);

  nodes.changesBox = h('div', 'bss-changes');
  paintChanges();
  body.append(nodes.changesBox);

  body.append(hintBox(
    'Yalnız DEĞİŞEN alanlar gönderilir. Dokunulmayan bir anahtar gövdeye hiç '
    + 'girmez — bu, mutfak ekranının aradan çevirdiği yoğunluk anahtarını geri '
    + 'çevirmemek içindir. Yazılan bir alan siz formu açtıktan sonra sunucuda '
    + 'değiştiyse kayıt YAPILMAZ ve hangi alanın değiştiği burada yazar.'));

  paintDirty();
  nodes.status.set(statusText());
}

/** Etiketli tek alan — `formGrid` dışındaki serbest kutular için. */
function labelled(text, control, wide = false) {
  const wrap = h('label', `kit-field${wide ? ' kit-field-wide' : ''}`);
  wrap.append(h('span', 'kit-field-label', text));
  wrap.append(control);
  return wrap;
}

function paintDrift() {
  const box = nodes.driftBox;
  if (!box) return;
  box.replaceChildren();
  if (!state.drift.length) return;
  const list = state.drift
    .map((row) => `${row.label}: ${showValue(row.field, row.from)} → `
      + `${showValue(row.field, row.to)}`)
    .join(' · ');
  const switched = state.drift.some((row) => row.field === 'ordering_enabled');
  box.append(alertBox(
    `Bu değerler siz ekranı açtıktan sonra SUNUCUDA değişti: ${list}. `
    + (switched
      ? 'SATIŞ ŞALTERİ başka bir yerden çevrildi; ekranın üstündeki durum artık '
        + 'eski okumadan kalma. '
      : '')
    + 'Formdaki değerler eski okumadan kalma olabilir. Değişen bir alanı yazmaya '
    + 'çalışırsanız kayıt reddedilir; “Formu tazele” ile güncel hâli alın '
    + '(kaydedilmemiş değişiklikleriniz kaybolur).', 'warn'));
}

function paintChanges() {
  const box = nodes.changesBox;
  if (!box) return;
  box.replaceChildren();
  if (!state.changes.length) return;
  const table = dataTable({
    columns: [
      { key: 'label', label: 'Alan', width: 'minmax(0, 1.4fr)' },
      { key: 'from', label: 'Önceki', cell: (row) => showValue(row.field, row.from) },
      { key: 'to', label: 'Yeni', cell: (row) => showValue(row.field, row.to) },
    ],
    rows: state.changes,
    dense: true,
  });
  box.append(card(state.changesTitle || 'Değişiklikler', table.node));
}

// ============================================================ yazma eylemleri

/**
 * Gerekçeli onay. Kuru prova ile gerçek yazma AYNI kapıdan geçer ama başlık ve
 * düğme metni farklıdır: kullanıcının hangisini yaptığını ekrandan okuması
 * gerekir, kutuyu ezberlemesi değil.
 */
function askReason({ title, description, confirmLabel, danger = false }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN}, en çok ${REASON_MAX} karakter)`,
  });
}

async function doSaveSettings({ preview }) {
  if (busy) return;
  if (!formsValid()) return;

  const body = collectPatch();
  const rows = plannedChanges(body);
  if (!rows.length) {
    toast('Değişen alan yok; gönderilecek bir şey de yok.', 'warn');
    return;
  }

  const summary = rows
    .map((row) => `${row.label}: ${showValue(row.field, row.from)} → `
      + `${showValue(row.field, row.to)}`)
    .join('\n');

  const reason = await askReason({
    title: preview ? 'Önizleme — hiçbir şey yazılmaz' : 'Satış ayarlarını yaz',
    description: preview
      ? `Sunucu bu değişiklikleri DENER ve sonucu döndürür, ama HİÇBİR AYAR `
        + `YAZILMAZ:\n${summary}`
      : `Şu alanlar sunucuya YAZILACAK:\n${summary}`,
    confirmLabel: preview ? 'Önizle' : 'Kaydet',
    danger: false,
  });
  if (!reason) return;

  busy = true;
  nodes.status.set(preview ? 'Önizleme isteniyor…' : 'Ayarlar yazılıyor…');
  try {
    const result = await call(`${BASE}/sales`, {
      method: 'PUT',
      body: { reason, preview, token: state.token, settings: body },
    });
    state.changes = rows;
    state.changesTitle = preview
      ? 'Önizleme — bu değişiklikler YAZILMADI'
      : 'Yazılan değişiklikler';
    if (preview) {
      toast('Önizleme alındı. Hiçbir şey yazılmadı; kaydetmek için “Kaydet”.', 'good');
    } else {
      const changed = result?.changed || [];
      toast(changed.length
        ? `${changed.length} alan yazıldı.`
        : 'İstek tamamlandı.', 'good');
      if (result?.warning) toast(result.warning, 'warn');
      if (result?.note) toast(result.note, 'warn');
      state.drift = [];
      await loadSales();
    }
    showSettings();
  } catch (error) {
    // Çakışma bir HATA DEĞİL, bir DURUMDUR: sunucu yazmadı ve nedenini söyledi.
    // Ekran bunu kırmızı bir kutuda değil, ne yapılacağını anlatan bir uyarıda
    // göstermeli — yönetici düğmeye yeniden basmayı değil, tazelemeyi yapmalı.
    const payload = error.payload || {};
    if (payload.conflict) {
      state.drift = (payload.conflicts || []).map((row) => ({
        field: row.field, label: row.label, from: row.baseline, to: row.current,
      }));
      // FORM YENİDEN ÇİZİLMEZ ve `state.sales` DEĞİŞTİRİLMEZ. Yöneticinin
      // yazdığı değerler duruyor; kararı o vermeli. Formu buradan tazelemek
      // hem yazdıklarını silerdi hem de jetonu eskisiyle bırakıp yeni bir
      // ret üretirdi — kullanıcı ne yaptığını anlamadan iki kez reddedilirdi.
      state.live = payload.data || state.live;
      paintDrift();
      toast(error.message, 'warn');
      nodes.status.set(statusText(), true);
    } else {
      toast(error.message, 'bad');
      nodes.status.set(error.message, true);
    }
  } finally {
    busy = false;
  }
}

async function doPause(timeInput, messageInput) {
  if (busy) return;
  const day = nodes.pauseDate.get();
  const time = String(timeInput.value || '').trim();
  let until = null;

  if (day && !time) {
    toast('Bitiş günü girdiniz ama saat boş. Süresiz durdurmak için ikisini de boş '
      + 'bırakın.', 'bad');
    return;
  }
  if (!day && time) {
    toast('Bitiş saati girdiniz ama gün boş.', 'bad');
    return;
  }
  if (day && time) {
    until = utcInstant(day, time);
    if (!until) {
      toast('Bitiş saatini 18:00 gibi HH:mm biçiminde yazın.', 'bad');
      return;
    }
  }

  const message = String(messageInput.value || '').trim();
  const when = until
    ? `${stampIso(until)}'e kadar`
    : 'SÜRESİZ (elle açılana kadar)';

  const reason = await askReason({
    title: 'Satışı durdur',
    description: `Satış ${when} DURDURULACAK ve müşteri sipariş veremeyecek.\n`
      + (message
        ? `Müşteriye gösterilecek mesaj: “${message}”`
        : 'Müşteriye özel mesaj girilmedi; istemciler kendi genel metnini gösterir.')
      + '\n\nBuraya yazacağınız gerekçe DENETİM kaydına gider ve müşteriye '
      + 'GÖSTERİLMEZ.',
    confirmLabel: 'Satışı durdur',
    danger: true,
  });
  if (!reason) return;

  busy = true;
  nodes.status.set('Satış durduruluyor…');
  try {
    await call(`${BASE}/ordering/pause`, {
      method: 'POST',
      body: { reason, preview: false, until, customer_message: message || null },
    });
    toast('Satış durduruldu.', 'good');
    await loadSales();
    showSettings();
  } catch (error) {
    toast(error.message, 'bad');
    nodes.status.set(error.message, true);
  } finally {
    busy = false;
  }
}

async function doResume() {
  if (busy) return;
  const reason = await askReason({
    title: 'Satışı yeniden aç',
    description: 'Satış açılacak ve durdurma izleri (bitiş saati, müşteri mesajı) '
      + 'temizlenecek. Müşteri hemen sipariş verebilir.',
    confirmLabel: 'Satışı aç',
    danger: false,
  });
  if (!reason) return;

  busy = true;
  nodes.status.set('Satış açılıyor…');
  try {
    await call(`${BASE}/ordering/resume`, {
      method: 'POST',
      body: { reason, preview: false },
    });
    toast('Satış açıldı.', 'good');
    await loadSales();
    showSettings();
  } catch (error) {
    toast(error.message, 'bad');
    nodes.status.set(error.message, true);
  } finally {
    busy = false;
  }
}

// ========================================================== kapalı gün sekmesi

function showClosedDays() {
  const body = nodes.body;
  // Önceki çizimin takvimi / tarih alanı / formları GERÇEKTEN bırakılır:
  // hepsi global dinleyici tutuyor ve bu ekran her kayıttan sonra yeniden
  // çiziliyor.
  releaseSection();
  body.replaceChildren();

  if (!state.closedLoaded) {
    body.append(skeletonRows(6, 3));
    refreshClosedDays();
    return;
  }

  const warning = linkAlert({ stale: state.closedStale, what: 'Kapalı gün listesi' });
  if (warning) body.append(warning);

  const holidays = {};
  for (const row of state.closed) holidays[row.date] = row.description || 'Kapalı gün';

  nodes.calendar = monthCalendar({
    selected: todayIso(),
    onPick: (iso) => {
      nodes.closedDate?.set(iso);
      nodes.closedPicked?.replaceChildren(document.createTextNode(
        holidays[iso] ? `${iso} kapalı: ${holidays[iso]}` : `${iso} seçildi.`));
    },
    // Sağ tık: tatil işaretle / kaldır. Akışı kesmeden aynı gerekçeli onaydan
    // geçer — kısayol izin ya da onay ATLAMAZ.
    onToggleHoliday: (iso, isHoliday) => {
      if (isHoliday) doRemoveClosedDay(iso);
      else doAddClosedDay(iso, '');
    },
    renderBadge: () => null,
    dayTitle: (iso) => (holidays[iso] ? `Kapalı: ${holidays[iso]}` : ''),
    legend: [{ text: 'sağ tık: kapalı gün ekle / kaldır', tone: '' }],
  });
  nodes.calendar.update({ holidays });
  sectionClosers.push(() => nodes.calendar.destroy());

  nodes.closedDate = dateField({
    value: todayIso(),
    label: 'Kapatılacak gün',
    onChange: () => {},
  });
  sectionClosers.push(() => nodes.closedDate.destroy());

  const description = h('input', 'kit-input');
  description.type = 'text';
  description.maxLength = Number(state.limits?.closed_day_description_max) || 160;
  description.placeholder = 'Örn. 30 Ağustos Zafer Bayramı';

  const addBox = h('div', 'bss-add');
  addBox.append(
    labelled('Kapatılacak gün', nodes.closedDate.node),
    labelled('Açıklama', description, true),
  );
  const addActions = h('div', 'bss-actions');
  addActions.append(writeButton('Kapalı gün ekle', {
    variant: 'primary',
    onClick: () => doAddClosedDay(nodes.closedDate.get(), description.value),
  }));
  addBox.append(addActions);

  nodes.closedPicked = h('div', 'bss-sub');

  const table = dataTable({
    columns: [
      { key: 'date', label: 'Tarih', width: '140px', sortable: true },
      { key: 'description', label: 'Açıklama',
        cell: (row) => row.description || '—' },
      {
        key: 'state',
        label: 'Durum',
        width: '150px',
        cell: (row) => badge(row.date < todayIso() ? 'Geçmiş' : 'Gelecek',
          row.date < todayIso() ? 'dim' : 'info'),
      },
      {
        key: 'act',
        label: '',
        width: '140px',
        cell: (row) => writeButton('Kaldır', {
          variant: 'danger',
          title: 'Kapalı gün kaydı SİLİNİR ve o gün yeniden satışa açılır.',
          onClick: () => doRemoveClosedDay(row.date),
        }),
      },
    ],
    rows: state.closed,
    dense: true,
    empty: state.closedError
      ? emptyState({
        title: 'Kapalı günler okunamadı',
        text: state.closedError,
        actions: [button('Tekrar dene',
          { variant: 'primary', onClick: () => refreshClosedDays() })],
      })
      : emptyState({
        title: 'Kapalı gün yok',
        text: state.link.connected
          ? 'Önümüzdeki bir yılda kapalı gün kaydı bulunmuyor. Resmî tatilleri '
            + 'buradan girin: abonelik üretimi o günleri atlar ve günlük menü '
            + 'takvimi sipariş almaz.'
          : 'BLD sunucusuna ulaşılamıyor; boş liste “kapalı gün yok” anlamına GELMEZ.',
      }),
  });

  body.append(
    card('Takvim', nodes.calendar.node,
      'Gün seçmek aşağıdaki formu doldurur; sağ tık ekler ya da kaldırır.'),
    nodes.closedPicked,
    card('Kapalı gün ekle', addBox),
    card('Kapalı günler', table.node),
    hintBox(
      'Kapalı günler GLOBAL’dir: bütün vitrinler için geçerlidir. Abonelik üretimi bu '
      + 'günleri atlar ve günlük menü takvimi o gün sipariş almaz. Geçmiş bir gün de '
      + 'işaretlenebilir (rapor tutarlılığı); o güne ait sipariş varsa sunucu uyarı '
      + 'döndürür ama engel çıkarmaz. Kaldırma GERÇEK SİLMEDİR — kapalı gün bir belge '
      + 'değil bir kuraldır; kaydın tarihçesi denetim izinde kalır.'),
  );
  nodes.status.set(`${num(state.closed.length)} kapalı gün · ${statusText()}`,
    Boolean(state.closedError) || !state.link.connected);
}

async function doAddClosedDay(iso, description) {
  if (busy) return;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || '')) {
    toast('Önce kapatılacak günü seçin.', 'bad');
    return;
  }
  const past = iso < todayIso();
  const reason = await askReason({
    title: 'Kapalı gün ekle',
    description: `${iso} bütün vitrinlerde KAPALI olacak: o gün sipariş alınmaz ve `
      + 'abonelik üretimi o günü atlar.'
      + (past ? '\n\nBu GEÇMİŞ bir gün. Kayıt yapılır; o güne ait sipariş varsa '
        + 'sunucu uyarı döndürür.' : ''),
    confirmLabel: 'Kapalı gün ekle',
    danger: true,
  });
  if (!reason) return;

  busy = true;
  nodes.status.set('Kapalı gün ekleniyor…');
  try {
    const result = await call(`${BASE}/closed-days`, {
      method: 'POST',
      body: { reason, preview: false, date: iso, description: description || null },
    });
    for (const item of result?.warnings || []) {
      toast(item.message || JSON.stringify(item), 'warn');
    }
    toast(`${iso} kapalı gün olarak eklendi.`, 'good');
    await loadClosedDays();
    showClosedDays();
  } catch (error) {
    toast(error.message, 'bad');
    nodes.status.set(error.message, true);
  } finally {
    busy = false;
  }
}

async function doRemoveClosedDay(iso) {
  if (busy) return;
  const reason = await askReason({
    title: 'Kapalı günü kaldır',
    description: `${iso} yeniden satışa AÇILACAK ve abonelik üretimi o günü işleyecek. `
      + 'Kayıt gerçekten silinir; tarihçesi yalnız denetim izinde kalır.',
    confirmLabel: 'Kaldır',
    danger: true,
  });
  if (!reason) return;

  busy = true;
  nodes.status.set('Kapalı gün kaldırılıyor…');
  try {
    await call(`${BASE}/closed-days/${iso}`, {
      method: 'DELETE',
      body: { reason, preview: false },
    });
    toast(`${iso} kapalı günlerden kaldırıldı.`, 'good');
    await loadClosedDays();
    showClosedDays();
  } catch (error) {
    toast(error.message, 'bad');
    nodes.status.set(error.message, true);
  } finally {
    busy = false;
  }
}

// ================================================================ stok şeridi

/** Ekranda düzenlenen tavanlar; sunucu okuması onları EZMEZ. */
function editsFor(iso) {
  if (!state.stockEdits[iso]) state.stockEdits[iso] = { total: undefined, items: {} };
  return state.stockEdits[iso];
}

/** Tavan girişi. Boş = SINIRSIZ; sıfırdan farklıdır. */
function capacityInput(value, onWrite) {
  const input = h('input', 'kit-input bss-cap');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.placeholder = 'sınırsız';
  input.value = value === null || value === undefined ? '' : String(value);
  input.addEventListener('input', () => {
    const text = input.value.trim();
    if (text === '') { onWrite(null); input.classList.remove('bad'); return; }
    const parsed = Number(text.replace(',', '.'));
    const bad = !Number.isInteger(parsed) || parsed < 0;
    input.classList.toggle('bad', bad);
    onWrite(bad ? undefined : parsed);
  });
  return input;
}

function showStock() {
  const body = nodes.body;
  // Önceki çizimin takvimi / tarih alanı / formları GERÇEKTEN bırakılır:
  // hepsi global dinleyici tutuyor ve bu ekran her kayıttan sonra yeniden
  // çiziliyor.
  releaseSection();
  body.replaceChildren();

  if (!state.stockLoaded) {
    body.append(skeletonRows(6, 4));
    refreshStock();
    return;
  }

  if (state.stockError) {
    body.append(emptyState({
      title: 'Stok okunamadı',
      text: state.stockError,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshStock() })],
    }));
    return;
  }

  body.append(hintBox(
    'Bu şerit yalnız TAVAN yazar; menü kalemlerini Günlük Menü ekranı kurar. Boş bir '
    + 'tavan SINIRSIZ demektir ve sıfırdan farklıdır (sıfır “hiç satılmasın”). '
    + '“Satılan” rezerve edilmiş porsiyondur, teslim edilmiş değil — abonelikler onu '
    + 'önceden tutar ve satırda ayrı gösterilir. Tavanı satılmışın altına çekmek '
    + 'serbesttir (malzeme biter) ama sunucu bunu uyarı olarak söyler.'));

  for (const day of state.stock) body.append(stockCard(day));

  nodes.status.set(`${num(state.stock.length)} gün · ${statusText()}`,
    !state.link.connected);
}

function stockCard(day) {
  const iso = day.date;
  const edits = editsFor(iso);

  if (day.connected === false) {
    const why = day.code === 'not_found'
      ? `${iso} için henüz bir günlük menü kurulmamış. Bu bir hata değildir — menü `
        + 'Günlük Menü ekranından kurulunca tavanlar burada görünür.'
      : `Stok okunamadı: ${day.error}`;
    return card(`${iso}`, emptyState({ title: 'Stok yok', text: why }));
  }

  const totalValue = edits.total === undefined ? day.day.capacity : edits.total;
  const totalInput = capacityInput(totalValue, (value) => { edits.total = value; });

  const head = h('div', 'bss-stock-head');
  head.append(labelled('Gün tavanı (porsiyon)', totalInput));
  head.append(h('div', 'bss-sub',
    `Satılan ${num(day.day.sold)} (sipariş ${num(day.day.sold_orders)} + abonelik `
    + `${num(day.day.sold_subscriptions)}) · Kalan `
    + `${day.day.remaining === null || day.day.remaining === undefined
      ? 'sınırsız' : num(day.day.remaining)}`
    + (day.day.full ? ' · GÜN TAVANI DOLDU' : '')));

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kalem', width: 'minmax(0, 2fr)' },
      { key: 'sold', label: 'Satılan', width: '110px', align: 'num',
        cell: (row) => num(row.sold) },
      { key: 'sub', label: 'Aboneliğe ayrılan', width: '150px', align: 'num',
        title: 'Abonelikler stoku ÖNCE rezerve eder; bu sayı satılanın içindedir.',
        cell: (row) => num(row.sold_subscriptions) },
      { key: 'remaining', label: 'Kalan', width: '110px', align: 'num',
        cell: (row) => (row.remaining === null || row.remaining === undefined
          ? 'sınırsız' : num(row.remaining)) },
      {
        key: 'state',
        label: 'Durum',
        width: '150px',
        // Renk TEK BAŞINA anlam taşımaz: her rozetin içinde yazı var.
        cell: (row) => {
          if (row.sold_out) return badge('Mutfak tükendi dedi', 'bad');
          if (row.full) return badge('Tavan doldu', 'warn');
          return badge('Satışta', 'good');
        },
      },
      {
        key: 'capacity',
        label: 'Tavan',
        width: '130px',
        cell: (row) => capacityInput(
          edits.items[row.item_id] === undefined ? row.capacity : edits.items[row.item_id],
          (value) => { edits.items[row.item_id] = value; },
        ),
      },
    ],
    rows: day.items,
    dense: true,
    empty: emptyState({
      title: 'Kalem yok',
      text: `${iso} menüsünde kalem bulunmuyor.`,
    }),
  });

  const actions = h('div', 'bss-actions');
  actions.append(
    writeButton('Önizle', {
      title: 'Sunucu uyarıları hesaplar (kaç sipariş tavanın altında kalıyor) ama '
        + 'HİÇBİR ŞEY YAZILMAZ.',
      onClick: () => doSaveStock(day, { preview: true }),
    }),
    writeButton('Tavanları yaz', {
      variant: 'primary',
      onClick: () => doSaveStock(day, { preview: false }),
    }),
    button('Tazele', { onClick: () => { state.stockEdits[iso] = undefined; refreshStock(); } }),
  );

  const box = h('div', 'bss-stock');
  box.append(head, table.node, actions);
  return card(`${iso} — hızlı stok`, box,
    day.blocking?.day ? 'Gün tavanı dolu: yeni sipariş alınmıyor.' : '');
}

async function doSaveStock(day, { preview }) {
  if (busy) return;
  const iso = day.date;
  const edits = editsFor(iso);

  // TAM LİSTE: ekrandaki bütün kalemler gider. Eksik gönderim, o kalemin
  // tavanını sunucuda sessizce kaldırırdı.
  const items = day.items.map((row) => ({
    item_id: row.item_id,
    capacity: edits.items[row.item_id] === undefined ? row.capacity : edits.items[row.item_id],
  }));
  if (items.some((row) => row.capacity === undefined)) {
    toast('Tavan alanlarından biri sayı değil. Sınırsız için alanı boş bırakın.', 'bad');
    return;
  }
  const total = edits.total === undefined ? day.day.capacity : edits.total;
  if (total === undefined) {
    toast('Gün tavanı sayı değil. Sınırsız için alanı boş bırakın.', 'bad');
    return;
  }

  const rows = [];
  if (total !== day.day.capacity) {
    rows.push(`Gün tavanı: ${day.day.capacity ?? 'sınırsız'} → ${total ?? 'sınırsız'}`);
  }
  for (const row of items) {
    const before = day.items.find((item) => item.item_id === row.item_id);
    if (before && before.capacity !== row.capacity) {
      rows.push(`${before.name}: ${before.capacity ?? 'sınırsız'} → `
        + `${row.capacity ?? 'sınırsız'}`);
    }
  }
  if (!rows.length) {
    toast('Değişen tavan yok.', 'warn');
    return;
  }

  const reason = await askReason({
    title: preview ? `${iso} — önizleme, hiçbir şey yazılmaz` : `${iso} tavanlarını yaz`,
    description: `${rows.join('\n')}\n\nTavan tablosu TAM olarak gönderilir; ekranda `
      + 'görünmeyen bir kalem varsa yazma reddedilir.',
    confirmLabel: preview ? 'Önizle' : 'Yaz',
    danger: false,
  });
  if (!reason) return;

  busy = true;
  nodes.status.set(preview ? 'Önizleme isteniyor…' : 'Tavanlar yazılıyor…');
  try {
    const result = await call(`${BASE}/stock/${iso}`, {
      method: 'PUT',
      body: { reason, preview, capacity_total: total, items },
    });
    for (const item of result?.warnings || []) {
      const name = day.items.find((row) => row.item_id === item.item_id)?.name
        || `Kalem ${item.item_id}`;
      toast(`${name}: tavan ${num(item.capacity)}, satılan ${num(item.sold)} — `
        + 'tavan satılmışın altında.', 'warn');
    }
    if (preview) {
      toast('Önizleme alındı. Hiçbir tavan yazılmadı.', 'good');
    } else {
      toast(`${iso} tavanları yazıldı.`, 'good');
      state.stockEdits[iso] = undefined;
      await loadStock();
    }
    showStock();
  } catch (error) {
    toast(error.message, 'bad');
    nodes.status.set(error.message, true);
  } finally {
    busy = false;
  }
}

// ============================================================== denetim sekmesi

function showAudit() {
  const body = nodes.body;
  // Önceki çizimin takvimi / tarih alanı / formları GERÇEKTEN bırakılır:
  // hepsi global dinleyici tutuyor ve bu ekran her kayıttan sonra yeniden
  // çiziliyor.
  releaseSection();
  body.replaceChildren();

  if (!state.auditLoaded) {
    body.append(skeletonRows(8, 5));
    refreshAudit();
    return;
  }

  body.append(hintBox(
    'Bu, KONTROL MERKEZİ’nin kendi izidir ve sunucuya ULAŞMAYAN denemeleri de taşır: '
    + 'ağ koparsa “kim neyi denedi” sorusunun tek cevabı burasıdır. Sunucunun kendi '
    + 'denetim izi ayrı bir ekrandan okunur. Satırlar SİLİNMEZ.'));

  const table = dataTable({
    columns: [
      { key: 'created_at', label: 'Zaman', width: '170px',
        cell: (row) => stampIso(row.created_at) },
      { key: 'action', label: 'İşlem', width: 'minmax(0, 1.2fr)' },
      { key: 'target_id', label: 'Hedef', width: '130px',
        cell: (row) => row.target_id || '—' },
      { key: 'actor', label: 'Kim', width: '150px' },
      {
        key: 'result',
        label: 'Sonuç',
        width: '130px',
        cell: (row) => {
          const tone = { ok: 'good', denendi: 'info', onizleme: 'dim',
            engellendi: 'warn', hata: 'bad' }[row.result] || '';
          return badge(row.result || '—', tone);
        },
      },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)',
        cell: (row) => row.reason || '—' },
    ],
    rows: state.audit,
    dense: true,
    empty: emptyState({
      title: 'Kayıt yok',
      text: state.auditError || 'Bu ekrandan henüz yazma denenmemiş.',
    }),
  });

  body.append(card('Yerel denetim izi', table.node));
  nodes.status.set(`${num(state.audit.length)} kayıt · ${statusText()}`);
}

// ================================================================== tazeleme

function statusText() {
  if (!state.link.connected) return `BLD’ye ulaşılamıyor — ${state.link.error}`;
  // EN TAZE bilinen hâl. Form `state.sales`'ten çizilir (kullanıcının gördüğü
  // ve üstüne yazdığı taban), ama durum satırı şalterin ŞU ANKİ hâlini
  // söylemeli: "satış açık" yazan bir şeridin altında kapalı bir dükkân
  // olmamalı.
  const data = state.live || state.sales || {};
  const parts = [];
  parts.push(data.ordering_enabled === false ? 'Satış DURDURULDU' : 'Satış açık');
  if (data.location_name) parts.push(data.location_name);
  if (state.readAt) parts.push(`son okuma ${ago(state.readAt)}`);
  return parts.join(' · ');
}

async function refreshSales() {
  nodes.status.set('Satış ayarları alınıyor…');
  state.drift = [];
  await loadSales();
  if (state.tab !== 'settings') return;
  startLive();          // sunucunun bildirdiği aralık artık biliniyor
  showSettings();
}

async function refreshClosedDays() {
  nodes.status.set('Kapalı günler alınıyor…');
  await loadClosedDays();
  if (state.tab !== 'closed') return;
  showClosedDays();
}

async function refreshStock() {
  nodes.status.set('Stok alınıyor…');
  await loadStock();
  if (state.tab !== 'stock') return;
  showStock();
}

async function refreshAudit() {
  nodes.status.set('Denetim izi alınıyor…');
  await loadAudit();
  if (state.tab !== 'audit') return;
  showAudit();
}

/**
 * Canlı tazeleme YALNIZ ayarlar sekmesinde çalışır.
 *
 * Gerekçesi tek bir şey: `busy` anahtarını mutfak ekranı da çeviriyor ve
 * yönetici bunu KAYDET düğmesine basmadan önce görmeli. Kapalı sekme için
 * istek üretmek, paylaşılan 3000/saat bütçesini boşuna yakar; `pollLoop`
 * ayrıca sekme gizliyken duraklar.
 */
function startLive() {
  const period = state.pollMs || FALLBACK_POLL_MS;
  // İlk kurulumda ayarlar henüz okunmamış olur ve yedek aralık geçerlidir;
  // sunucu kendi aralığını bildirdiğinde döngü YENİDEN kurulur. Aksi hâlde
  // ayar dosyasındaki değer ekranda hiç geçerli olmazdı.
  if (live && period === livePeriod) return;
  stopLive();
  livePeriod = period;
  live = pollLoop({
    every: period,
    run: async () => {
      // Kullanıcı yazarken formu ALTINDAN DEĞİŞTİRMEYİZ: yalnız okuruz ve
      // farkı uyarı olarak yazarız. Formu tazelemek, yarısını doldurduğu
      // kutuları silmek olurdu.
      await probeSales();
      if (state.tab !== 'settings') return;
      paintDrift();
      nodes.status.set(statusText(), !state.link.connected);
    },
  });
}

function stopLive() {
  live?.stop();
  live = null;
  livePeriod = 0;
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE, stockEdits: {} };

  const view = h('div', 'kit-panel bss');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'settings', label: 'Ayarlar' },
    { key: 'closed', label: 'Kapalı günler' },
    { key: 'stock', label: 'Hızlı stok' },
    { key: 'audit', label: 'Denetim izi' },
  ], 'settings', (key) => showTab(key));

  nodes.status = statusLine();
  nodes.body = h('div', 'bss-body');

  const bar = h('div', 'bss-topbar');
  bar.append(nodes.tabs.node);
  bar.append(button('Yenile', {
    title: 'Açık sekmeyi sunucudan tazeler.',
    onClick: () => ({
      settings: refreshSales,
      closed: refreshClosedDays,
      stock: refreshStock,
      audit: refreshAudit,
    }[state.tab] || refreshSales)(),
  }));

  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    if (key === 'settings') startLive(); else stopLive();
    ({
      settings: showSettings,
      closed: showClosedDays,
      stock: showStock,
      audit: showAudit,
    }[key] || showSettings)();
  }

  root.replaceChildren(view);
  showTab('settings');

  return () => {
    // `stop()` hem zamanlayıcıyı hem `visibilitychange` dinleyicisini bırakır;
    // `releaseSection()` takvimi, tarih alanlarını ve form ızgaralarını.
    stopLive();
    releaseSection();
    root.replaceChildren();
    state = { ...EMPTY_STATE, stockEdits: {} };
    busy = false;
  };
}
