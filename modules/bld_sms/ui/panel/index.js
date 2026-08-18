// SMS Paneli — BLD'nin müşteriye giden SMS metinleri, tetikleyicileri ve kaydı.
//
// NE YAPAR: on sabit şablonun metnini düzenler; her bildirimi tek tek açıp
// kapatır; karakter/segment sayacını ve canlı önizlemeyi gösterir; tek numaraya
// [DENEME] SMS'i yollar; toplu duyuruyu kuru provadan geçirerek gönderir;
// gönderim kaydını süzer ve maliyeti (segment toplamı) yazar.
//
// NE YAPMAZ:
//  · SMS GÖNDERMEZ — gönderilmesini ister. Mesajı BLD sunucusu kendi
//    sağlayıcısıyla yollar. Kontrol Merkezi'nin kendi Netgsm şeridi bu yolun
//    parçası DEĞİLDİR ve ekranda ayrı bir satırda yazar; karıştırılırsa
//    yönetici yanlış ayarı düzeltmeye çalışır.
//  · ŞABLON ADI DEĞİŞTİRMEZ. `title` sistemin kendi sözlüğüdür ve uç onu
//    kabul etmez; alan salt okunur çizilir.
//  · KAYIT SİLMEZ. Gönderim kaydı silinemez, silme ucu yoktur; şablonlar
//    kapatılır, silinmez.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//  · YOKLAMA YAPMAZ. Bu ekranda saniyesi değişen bir şey yok ve paylaşılan
//    3000/saat bütçesi on dört panelin ortak kovası; tazeleme elle yapılır.
//    TEK İSTİSNA alıcı tahminidir ve o da her açılışta yeniden okunur.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · İKİ SEGMENT ÖLÇÜSÜ VAR VE İKİSİ DE DOĞRU. Sözleşme "Türkçe karakter varsa
//    UCS-2, tek segment 70" diyor; platformun SMS şeridi ise `ğ ı ş` için
//    Netgsm'in Türkçe kaydırma tablosunu kullanıyor ve 160'ta kalıyor. Aradaki
//    fark PARADIR. Ekran ikisini `measureBar` ile yan yana çizer ve
//    "faturalanan" işaretini SÖZLEŞMENİN ölçüsüne koyar.
//  · CANLI SAYAÇ YERELDİR (`POST /measure`): ağa çıkmaz, denetim satırı
//    yazmaz. Sunucudaki önizleme AYRI bir düğmedir, gerekçe ister ve gerçeği
//    o söyler.
//  · BİR BİLDİRİMİ AÇMAK gerekçeli onaydan geçer. Açık doğan ya da yanlışlıkla
//    açılan bir şablon, tek dağıtımı binlerce SMS'e çevirir.
//  · TOPLU DUYURUDA KURU PROVA ZORUNLU İLK ADIMDIR. Gönder düğmesi prova
//    yapılmadan hiç açılmaz; provanın jetonu tek kullanımlıktır ve taslak
//    değişince düşer.
//  · SAĞLAYICI KURULU DEĞİLSE hiçbir şey gitmez, yalnız günlüğe yazılır.
//    Ekran bunu en üstte söyler; aksi hâlde "SMS gitti" diyen bir ekran hiçbir
//    şey göndermemiş olur.
//  · GÖNDERİM KAYDINDA TELEFON MASKELİ ve gövde kırpık gelir; ekran ikisini de
//    geri açmaz.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_sms/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_sms/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, confirmSimple, confirmWithReason, copyText,
  csvBlob, debounce, h, loadStyles, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { measureBar } from '../../ui-kit/flow.js';
import { formGrid } from '../../ui-kit/form.js';

const BASE = '/api/bld_sms';

/**
 * Gerekçe alt sınırı — sunucu da denetliyor (00-genel.md §3); bu erken bildirim.
 * Üst sınır (500) burada TEKRARLANMAZ: kitin gerekçe kutusu zaten 255 karakterde
 * duruyor ve iki sayıyı iki yerde tutmak, biri değişince ötekini unutmaktır.
 */
const REASON_MIN = 10;

/** Şablon gövdesinin sınırı (sözleşme). */
const BODY_MAX = 500;

/** Canlı sayacın bekleme süresi. Uç yereldir ama her tuşta çağırmak gereksiz. */
const MEASURE_MS = 220;

/**
 * Netgsm gönderici adının sınırı — BBD Kantin panelindekiyle AYNI sayı.
 * İki ekranın farklı sınır göstermesi, birinde kabul edilen bir başlığın
 * ötekinde reddedilmesi demekti.
 */
const HEADER_MAX = 11;

/**
 * BLD'nin gönderici adı. Netgsm hesabı BBD Kantin ile ORTAKTIR; iki sistemi
 * ayıran tek ayar budur ve bu yüzden ekran beklenen değeri yazılı gösterir.
 * Bir DAYATMA değil, bir hatırlatmadır: onaylı ad gün gelip değişirse panelden
 * yazılabilmeli.
 */
const EXPECTED_HEADER = 'BLEZZETDNYM';

/** Netgsm'in "mesaj başlığı sistemde tanımlı değil" kodu. */
const HEADER_ERROR_CODE = '40';

/** Eksik sağlayıcı alanlarının okunur adları. */
const MISSING_LABELS = {
  username: 'Netgsm kullanıcı kodu',
  password: 'Netgsm parolası',
  header: 'gönderici başlığı',
};

/** Başlığın nereden geldiği — yönetici çalışan bir ayarı bozmasın diye ayrı. */
const HEADER_SOURCES = {
  setting: 'Bu ekrandan yazılmış ayar',
  env: 'BLD sunucusunun ortam değişkeni (NETGSM_HEADER)',
  none: 'Hiçbir yerde tanımlı değil — SMS GİTMEZ',
};

/**
 * Gönderim kaydının durum sözlüğü. Renk TEK BAŞINA anlam taşımaz: her rozetin
 * içinde yazı var.
 */
const STATUSES = [
  { key: 'sent', label: 'Gitti', tone: 'good' },
  { key: 'failed', label: 'Gitmedi', tone: 'bad' },
];

/** Gönderimin bağlamı — otomatik bildirim, deneme, toplu duyuru. */
const CONTEXTS = [
  { key: 'auto', label: 'Otomatik', tone: 'info' },
  { key: 'test', label: 'Deneme', tone: 'dim' },
  { key: 'announcement', label: 'Duyuru', tone: 'warn' },
];

const EMPTY_STATE = {
  tab: 'templates',
  templates: [],
  groups: [],
  audiences: [],
  sender: { driver: '', configured: false, header: '', header_source: '', missing: [] },
  netgsm: null,
  netgsmLoaded: false,
  platformLane: { available: false, note: '' },
  segmentWarn: 2,
  templatesLoaded: false,
  announcement: null,
  announcementDry: null,
  logRows: [],
  logMeta: {},
  logPage: 1,
  logSize: 25,
  history: [],
  confirmThreshold: 100,
  dryTtl: 15,
  link: { connected: true, error: '' },
};

let api = null;
let toast = () => {};
let state = { ...EMPTY_STATE };
let busy = false;

const nodes = {};

/** Panel ömrü boyunca yaşayan kaynak bırakıcılar (süzgeç şeridi, açık çekmece). */
const closers = [];

/**
 * SEKME ÖMÜRLÜ bırakıcılar. Sekme her çizildiğinde `formGrid` ve `debounce`
 * yeniden kuruluyor; hepsini panel ömrüne yazmak, ekranı açıp kapatan bir
 * kullanıcıda yüzlerce ölü dinleyici biriktirirdi. Çizimden önce boşaltılır.
 */
const tabClosers = [];

/** Bırakıcıyı listeye yazar ve GERİ ALMA işlevini döndürür. */
function keep(list, fn) {
  list.push(fn);
  return () => {
    const index = list.indexOf(fn);
    if (index >= 0) list.splice(index, 1);
  };
}

function disposeTab() {
  tabClosers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
  tabClosers.length = 0;
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
 * çizmek "şablon yok" demek olurdu ve yönetici olmayan bir sorunu arardı.
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = { connected: false, error: String(payload.error || '') };
    return false;
  }
  state.link = { connected: true, error: '' };
  return true;
}

/** Yazma gövdesi. `dryRun` HER ZAMAN açıkça gönderilir — atlanmaz. */
function writeBody(fields, dryRun = false) {
  return { ...fields, dryRun: Boolean(dryRun) };
}

/**
 * Yazma sonucunu okur ve kullanıcıya doğru cümleyi söyler.
 *
 * SORDUĞUMUZ DEĞİL, CEVAP OKUNUR: bir kurulum geçidin varsayılanını geri açarsa
 * gerçek sanılan bir yazma provaya düşebilir ve ekran "kaydedildi" DEMEMELİDİR.
 */
function announce(result, doneText) {
  if (result?.dry_run) {
    toast('Sunucu bunu KURU PROVA olarak işledi — hiçbir şey yazılmadı.', 'warn');
    return false;
  }
  toast(doneText, 'good');
  return true;
}

async function withBusy(label, work) {
  if (busy) return;
  busy = true;
  nodes.status?.set(label);
  try {
    await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
  } finally {
    busy = false;
    nodes.status?.set(statusText());
  }
}

function statusText() {
  if (!state.link.connected) return `BLD sunucusuna ulaşılamıyor — ${state.link.error}`;
  if (!state.templatesLoaded) return 'Şablonlar alınıyor…';
  const acik = state.templates.filter((row) => row.enabled).length;
  return `Bağlı · ${state.templates.length} şablon · ${acik} açık`;
}

function templateOf(key) {
  return state.templates.find((row) => row.key === key) || null;
}

function groupLabel(key) {
  return state.groups.find((item) => item.key === key)?.label || 'Diğer';
}

function toneOf(list, key) {
  return list.find((item) => item.key === key)?.tone || '';
}

function labelOf(list, key) {
  return list.find((item) => item.key === key)?.label || key || '—';
}

// ------------------------------------------------------------------ okuma

async function loadTemplates() {
  const payload = await call(`${BASE}/templates`);
  state.templatesLoaded = true;
  if (!linkOk(payload)) {
    state.templates = [];
    state.groups = payload?.groups || [];
    return;
  }
  state.templates = payload.data || [];
  state.groups = payload.groups || [];
  // Süzgeç şeridi panel açılır açılmaz çiziliyor; şablon listesi ancak burada
  // biliniyor. Kutu önce boş kurulur, seçenekleri veriden sonra dolar.
  nodes.logFilters?.options('template_key', [
    { value: '', label: 'Tümü' },
    ...state.templates.map((row) => ({ value: row.key, label: row.title })),
  ]);
  state.audiences = payload.audiences || [];
  state.sender = payload.sender
    || { driver: '', configured: false, header: '', header_source: '', missing: [] };
  state.platformLane = payload.platform_lane || { available: false, note: '' };
  state.segmentWarn = Number(payload.segment_warn) || 2;
}

async function loadAnnouncement() {
  const payload = await call(`${BASE}/announcement`);
  if (!linkOk(payload)) {
    state.announcement = null;
    state.announcementDry = null;
    return;
  }
  state.announcement = payload.data || null;
  state.announcementDry = payload.dry_run || null;
  state.audiences = payload.audiences || state.audiences;
  state.confirmThreshold = Number(payload.confirm_threshold) || 100;
  state.dryTtl = Number(payload.ttl_minutes) || 15;
}

async function loadLog() {
  const values = nodes.logFilters?.values?.() || {};
  const params = new URLSearchParams();
  if (values.q) params.set('phone', values.q);
  if (values.template_key) params.set('template_key', values.template_key);
  if (values.status) params.set('status', values.status);
  if (values.context) params.set('context', values.context);
  if (values.range?.start) params.set('date_from', values.range.start);
  if (values.range?.end) params.set('date_to', values.range.end);
  params.set('page', String(state.logPage));
  params.set('per_page', String(state.logSize));

  const payload = await call(`${BASE}/log?${params.toString()}`);
  if (!linkOk(payload)) {
    state.logRows = [];
    state.logMeta = {};
    return;
  }
  state.logRows = payload.data || [];
  state.logMeta = payload.meta || {};
}

async function loadHistory() {
  const payload = await call(`${BASE}/history?limit=100`);
  state.history = payload?.data || [];
}

async function loadNetgsm() {
  const payload = await call(`${BASE}/netgsm`);
  state.netgsmLoaded = true;
  if (!linkOk(payload)) {
    state.netgsm = null;
    return;
  }
  state.netgsm = payload.data || null;
}

/** YEREL ölçüm — ağa çıkmaz, denetim satırı yazmaz. */
async function measureText(body, key, sample, allowed) {
  const payload = await call(`${BASE}/measure`, {
    method: 'POST', body: { body, key: key || '', sample: sample || {}, allowed },
  });
  return payload?.data || null;
}

// ------------------------------------------------------------------ ortak

/**
 * Netgsm `40` hatasının AÇIK METİNLİ karşılığı.
 *
 * Sağlayıcının ham cümlesi ("Mesaj başlığı sistemde tanımlı değil") sorunu
 * söylüyor ama ÇÖZÜMÜ söylemiyor ve hangi başlığın reddedildiğini de yazmıyor.
 * Oysa bu hatanın tek sebebi vardır: gönderdiğimiz ad Netgsm panelinde onaylı
 * değil. Aynı hata sipariş, abonelik ve fatura bildirimlerinde de aynen
 * tekrarlanır — tek bir yanlış harf bütün SMS trafiğini sessizce düşürür.
 *
 * `provider_code` alanına bakar, hata METNİNDE ARAMA YAPMAZ: cümle
 * düzeltildiği gün metin araması sessizce boşa düşerdi.
 */
function headerRejected(data) {
  if (String(data?.provider_code || '') !== HEADER_ERROR_CODE) return null;
  const gonderilen = String(data.header || state.sender.header || '').trim();
  const box = h('div');
  box.append(alertBox(
    'BAŞLIK NETGSM\'DE TANIMLI DEĞİL (hata 40). Gönderici adı Netgsm panelinde '
    + 'onaylanmış olmalı; onaysız bir başlıkla tek mesaj bile ulaşmaz ve aynı '
    + 'hata sipariş, abonelik ve fatura bildirimlerinde de tekrarlanır.', 'bad'));
  box.append(hintBox(
    `Gönderilen başlık: "${gonderilen || '(boş)'}" · Beklenen: "${EXPECTED_HEADER}". `
    + 'Ya Netgsm panelinden bu adı onaylatın ya da "Netgsm ayarları" '
    + 'sekmesinden onaylı adı yazın.'));
  return box;
}

/** Sağlayıcı şeridi — en üstte durur ve iki ayrı gerçeği ayrı satırda yazar. */
function senderNotice() {
  const wrap = h('div', 'bs-notice');
  if (!state.sender.configured) {
    // EKSİK ALANIN ADI YAZILIR. "Sağlayıcı kurulu değil" cümlesi tek başına
    // yöneticiyi yanlış yere yolluyordu: eksik olan parolayken başlığı
    // düzeltmeye çalışıyor ve hiçbir şey değişmiyordu.
    const eksik = (state.sender.missing || []).map((key) => MISSING_LABELS[key] || key);
    wrap.append(alertBox(
      'BLD sunucusunda SMS sağlayıcısı KURULU DEĞİL. Bu ekrandan çıkan mesajlar '
      + 'kimseye gitmez, yalnız sunucunun günlüğüne yazılır. Sırrı tanımlanana '
      + 'kadar "gönderildi" yazan her satır aslında "yazıldı" demektir.'
      + (eksik.length ? ` Eksik olan: ${eksik.join(', ')}.` : ''), 'bad'));
    if (eksik.length === 1 && (state.sender.missing || [])[0] === 'header') {
      wrap.append(hintBox(
        'Yalnız gönderici başlığı eksik ve o BU EKRANDAN yazılabilir: '
        + '"Netgsm ayarları" sekmesi. Kullanıcı adı ile parola ise BLD\'nin '
        + 'ortam değişkenlerinde durur ve buradan değiştirilemez.'));
    }
  } else {
    wrap.append(alertBox(
      `SMS'i gönderen taraf BLD sunucusudur (sağlayıcı: ${state.sender.driver || 'bilinmiyor'}).`
      + ` Gönderici adı: "${state.sender.header || '—'}".`,
      'info'));
    // BAŞLIK BEKLENENDEN FARKLIYSA SESSİZ KALINMAZ: gönderim yine yapılır ama
    // müşterinin telefonunda başka bir ad görünür ve Netgsm'de onaylı değilse
    // hiç ulaşmaz.
    if (state.sender.header && state.sender.header !== EXPECTED_HEADER) {
      wrap.append(alertBox(
        `Gönderici adı "${state.sender.header}" — BLD için beklenen ad `
        + `"${EXPECTED_HEADER}". Bilerek değiştirildiyse sorun yok; değilse `
        + 'mesajlar müşteriye tanımadığı bir addan gider ya da hiç ulaşmaz.', 'warn'));
    }
  }
  // İKİ ŞERİT KARIŞTIRILMASIN. Kontrol Merkezi'nin kendi Netgsm ayarı bu
  // ekrandaki gönderimleri ETKİLEMEZ; ayrı yazılmazsa yanlış ayar düzeltilir.
  if (state.platformLane.available) {
    wrap.append(hintBox(state.platformLane.note
      || 'Kontrol Merkezi\'nin kendi SMS şeridi ayrıdır ve buradaki gönderimlerde '
      + 'kullanılmaz.'));
  }
  return wrap;
}

/** İki ölçüyü yan yana çizer; "faturalanan" işareti sözleşmenin ölçüsündedir. */
function measureView(measure, { title = 'Uzunluk ve segment' } = {}) {
  if (!measure) return h('div');
  const billed = measure.billed || {};
  const provider = measure.provider || {};
  const box = h('div', 'bs-measure');
  box.append(h('div', 'bs-card-title', title));

  const satir = h('div', 'bs-measure-line');
  satir.append(h('span', undefined, `${num(measure.length)} karakter`));
  satir.append(badge(billed.has_turkish_chars ? 'Türkçe karakter var' : 'Yalnız GSM-7',
    billed.has_turkish_chars ? 'warn' : 'good'));
  if ((Number(billed.segments) || 0) >= state.segmentWarn) {
    satir.append(badge(`${num(billed.segments)} segment — maliyet ${num(billed.segments)} kat`,
      'warn'));
  }
  if (Number(billed.segments) > 0) {
    satir.append(h('span', 'bs-dim',
      `${num(billed.remaining)} karakter sonra yeni segment başlar`));
  }
  box.append(satir);

  box.append(measureBar([
    {
      label: 'Sözleşmenin ölçüsü (UCS-2/GSM-7)',
      value: Number(billed.segments) || 0,
      text: `${num(billed.segments)} segment · ${num(billed.units)} birim`,
      governs: true,
      tone: (Number(billed.segments) || 0) >= state.segmentWarn ? 'warn' : '',
    },
    {
      label: 'Netgsm Türkçe tablosu ile',
      value: Number(provider.segments) || 0,
      text: `${num(provider.segments)} segment · ${num(provider.units)} birim`,
    },
  ], {
    note: 'İki hesap da doğrudur ve aradaki fark paradır: sözleşme Türkçe harf '
      + 'gördüğünde mesajı UCS-2 sayar (tek segment 70 karakter), Netgsm\'in '
      + 'Türkçe tablosu ise 160\'ta kalır. Faturayı gönderen taraf sözleşmenin '
      + 'saydığı sayıyı kullanır.',
  }));

  const offending = measure.offending || [];
  if (offending.length) {
    box.append(h('div', 'bs-dim',
      `Pahalılaştıran karakterler: ${offending.join(' ')}`));
  }
  return box;
}

/** "Sadeleştir" önerisi — yalnız KAZANÇ VARSA çizilir. */
function simplifyHint(measure, onApply) {
  const gain = Number(measure?.simplified?.gain) || 0;
  if (!gain) return null;
  const row = h('div', 'bs-simplify');
  row.append(h('span', undefined,
    `Türkçe harfleri sadeleştirirsen ${num(gain)} segment düşer.`));
  row.append(button('Sadeleştir', {
    title: 'Metni ASCII karşılıklarına çevirir. Karar sizindir; metin '
      + 'kendiliğinden değişmez.',
    onClick: () => onApply(measure.simplified.text),
  }));
  return row;
}

// ============================================================== ŞABLONLAR

function paintTemplates() {
  const body = nodes.body;
  disposeTab();
  body.replaceChildren();

  if (!state.templatesLoaded) {
    body.append(skeletonRows(8, 6));
    return;
  }
  if (!state.link.connected) {
    body.append(alertBox(
      `Şablonlar okunamadı: ${state.link.error}. Sunucu geri geldiğinde `
      + '[Yenile] ile tazeleyin — bu ekran boş değil, kapalı.', 'bad'));
    return;
  }

  body.append(senderNotice());

  const uyarilan = state.templates.filter((row) => row.unconfirmed_enabled);
  if (uyarilan.length) {
    body.append(alertBox(
      `${uyarilan.length} şablon sunucuda AÇIK ama bu ekrandan hiç açılmadı. `
      + 'Açık doğan bir bildirim, tek dağıtımı binlerce SMS\'e çevirir; '
      + 'Tetikleyiciler sekmesinden tek tek gözden geçirin.', 'warn'));
  }

  const table = dataTable({
    columns: [
      { key: 'title', label: 'Şablon', width: 'minmax(0, 2fr)',
        cell: (row) => {
          const box = h('div', 'bs-cell');
          box.append(h('b', undefined, row.title));
          box.append(h('span', 'bs-dim', row.key));
          return box;
        } },
      { key: 'group', label: 'Öbek', width: '150px',
        cell: (row) => groupLabel(row.group) },
      { key: 'sender_code', label: 'Nereden gider', width: 'minmax(0, 1.4fr)',
        title: 'O metni gerçekten gönderen kod. Boşsa şablon sunucuda tanımlı '
          + 'ama hiçbir yerden gönderilmiyor.',
        cell: (row) => {
          // "Düzenlenebilir ama hiç gönderilmiyor" en sinsi hâl: yönetici
          // metni özenle yazar, açar ve tek bir mesaj çıkmaz. Sütun bunu
          // satırın kendisinde söyler.
          if (row.dispatch === 'dead') return badge('şu an gönderilmiyor', 'bad');
          if (row.dispatch === 'unknown') return h('span', 'bs-dim', 'bilinmiyor');
          return h('span', 'bs-dim', row.sender_code || '—');
        } },
      { key: 'enabled', label: 'Durum', width: '150px',
        cell: (row) => {
          const box = h('div', 'bs-cell-row');
          box.append(badge(row.enabled ? 'Açık' : 'Kapalı', row.enabled ? 'good' : 'dim'));
          if (row.unconfirmed_enabled) box.append(badge('onaylanmadı', 'warn'));
          return box;
        } },
      { key: 'length', label: 'Uzunluk', width: '100px', align: 'num',
        cell: (row) => num(row.length) },
      { key: 'segments', label: 'Segment', width: '110px', align: 'num',
        title: 'Sunucunun saydığı, faturalanan segment sayısı.',
        cell: (row) => {
          const box = h('div', 'bs-cell-row');
          box.append(h('span', undefined, num(row.segments)));
          if (Number(row.segments) >= state.segmentWarn) box.append(badge('pahalı', 'warn'));
          return box;
        } },
      { key: 'updated_at', label: 'Güncellendi', width: '170px',
        cell: (row) => {
          const span = h('span', undefined, ago(row.updated_at));
          span.title = stampIso(row.updated_at);
          return span;
        } },
    ],
    rows: state.templates,
    rowKey: (row) => row.key,
    onRow: (row) => openTemplate(row.key),
    empty: emptyState({
      title: 'Şablon yok',
      text: 'Sunucu hiç şablon döndürmedi. Anahtarlar sabittir ve buradan '
        + 'eklenemez; sunucu tarafındaki tanımı denetleyin.',
    }),
  });
  nodes.templateTable = table;
  body.append(card('Şablonlar', table.node,
    'Satıra tıklayınca metin düzenleyici açılır.'));
}

function openTemplate(key) {
  const row = templateOf(key);
  if (!row) return;

  // ÇEKMECE PANEL ÖMRÜNE YAZILIR, sekmeye değil: sekme yeniden çizilse bile
  // açık çekmece `nodes.root` üstünde durmaya devam eder. Kapanışta kayıt
  // DÜŞÜRÜLÜR; aksi hâlde açılıp kapanan her çekmece listede birikirdi.
  const layer = drawer(nodes.root, {
    title: row.title,
    subtitle: `${row.key} · ${groupLabel(row.group)}`,
    onClose: () => release(),
  });

  const bosalt = () => {
    form.destroy();
    refreshPreview.cancel();
  };
  const drop = keep(closers, () => { layer.close(); bosalt(); });
  const release = () => { drop(); bosalt(); };

  const form = formGrid({
    fields: [
      // ŞABLONUN ADI SİSTEMİN SÖZLÜĞÜDÜR: uç `title` kabul etmiyor, alan da
      // salt okunur çizilir. Düzenlenebilir görünüp kaydedilmemesi, yöneticiye
      // "değiştirdim" dedirtirdi.
      { key: 'title', label: 'Ad (değiştirilemez)', type: 'static', wide: true,
        hint: 'Şablonun adı sistemin kendi sözlüğüdür ve panelden yazılamaz.' },
      { key: 'body', label: 'Metin', type: 'textarea', wide: true, required: true,
        maxLength: BODY_MAX,
        hint: `En çok ${BODY_MAX} karakter. Değişkenler {süslü_parantez} ile yazılır.` },
    ],
    value: { title: row.title, body: row.body },
    onChange: () => refreshPreview(),
  });

  const chips = h('div', 'bs-chips');
  for (const name of row.variables || []) {
    chips.append(button(`{${name}}`, {
      variant: 'ghost',
      title: 'Metnin sonuna ekler.',
      onClick: () => {
        form.set('body', `${form.draft().body || ''}{${name}}`);
        refreshPreview();
      },
    }));
  }
  if (!chips.childElementCount) {
    chips.append(h('span', 'bs-dim', 'Bu şablonun değişkeni yok.'));
  }

  const previewBox = h('div', 'bs-preview');
  const measureBox = h('div');
  const warnBox = h('div');

  const refreshPreview = debounce(async () => {
    const draft = form.draft();
    try {
      const measure = await measureText(draft.body || '', row.key, row.sample,
        row.variables || []);
      if (!measure) return;
      previewBox.replaceChildren(
        h('div', 'bs-card-title', 'Önizleme (örnek verilerle)'),
        h('div', 'bs-rendered', measure.rendered || ''),
      );
      measureBox.replaceChildren(measureView(measure.rendered_measure));
      const oneri = simplifyHint(measure.rendered_measure,
        (text) => { form.set('body', text); refreshPreview(); });
      if (oneri) measureBox.append(oneri);

      warnBox.replaceChildren();
      if (measure.unknown_variables?.length) {
        // SUNUCU BUNU 422 İLE REDDEDER. Kaydetmeden önce söylemek, ham bir
        // doğrulama hatasını ekrana basmaktan iyidir.
        warnBox.append(alertBox(
          'Şablonun tanımadığı değişken var: '
          + measure.unknown_variables.map((name) => `{${name}}`).join(', ')
          + '. Sunucu bu metni KAYDETMEZ.', 'bad'));
      }
      if (measure.unresolved_variables?.length) {
        warnBox.append(hintBox(
          'Örnek veride karşılığı olmayan değişkenler önizlemede olduğu gibi '
          + 'bırakıldı: '
          + measure.unresolved_variables.map((name) => `{${name}}`).join(', ')
          + '. Gerçek gönderimde bunları sunucu doldurur.'));
      }
    } catch (error) {
      previewBox.replaceChildren(alertBox(`Ölçüm yapılamadı: ${error.message}`, 'bad'));
    }
  }, MEASURE_MS);

  const actions = h('div', 'bs-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: () => saveTemplate(row.key, form, layer),
  }));
  actions.append(button('Sunucuda doğrula', {
    title: 'Metni SUNUCUDA işler ve gerçek uzunluğu/segmenti söyler. Hiçbir SMS '
      + 'göndermez ama bir denetim satırı yazar, bu yüzden gerekçe ister.',
    onClick: () => serverPreview(row.key, form),
  }));
  actions.append(button('Deneme gönder', {
    title: 'Tek numaraya [DENEME] ön ekli bir SMS yollar.',
    onClick: () => openTestDialog(row),
  }));
  actions.append(row.enabled
    ? button('Bildirimi kapat', {
      variant: 'danger',
      onClick: () => toggleTemplate(row, false, layer),
    })
    : button('Bildirimi aç', {
      variant: 'primary',
      title: 'Açık bir şablon müşterilere mesaj göndermeye başlar.',
      onClick: () => toggleTemplate(row, true, layer),
    }));

  const info = h('div', 'bs-info');
  info.append(h('p', undefined, row.about || ''));
  if (row.local?.drifted) {
    info.append(alertBox(
      'Sunucudaki metin, bu ekrandan en son yazdığımız metinle aynı değil. '
      + 'Başka bir yerden değiştirilmiş olabilir; kaydetmeden önce okuyun — '
      + 'kaydetmek o değişikliği geri alır.', 'warn'));
  }
  if (row.unconfirmed_enabled) {
    info.append(alertBox(
      'Bu bildirim sunucuda AÇIK ama Kontrol Merkezi\'nden hiç açılmadı. '
      + 'Bilerek açık olduğundan emin değilseniz kapatın.', 'warn'));
  }
  if (row.local?.changed_at) {
    info.append(h('div', 'bs-dim',
      `Son durum değişikliği: ${stampIso(row.local.changed_at)} — `
      + `${row.local.changed_by || 'bilinmiyor'} · ${row.local.reason || ''}`));
  }

  layer.body.append(
    info,
    form.node,
    card('Değişkenler', chips,
      'Tanınmayan bir değişken kaydedilmez; sunucu 422 verir.'),
    warnBox,
    previewBox,
    measureBox,
    actions,
  );
  refreshPreview();
}

async function saveTemplate(key, form, layer) {
  const row = templateOf(key);
  const draft = form.draft();
  const body = String(draft.body || '');
  if (!body.trim()) {
    toast('Şablon metni boş olamaz.', 'bad');
    return;
  }
  if (body === row.body) {
    toast('Metin değişmedi.', 'warn');
    return;
  }

  const reason = await confirmWithReason(nodes.root, {
    title: 'Şablon metnini kaydet',
    description: `“${row.title}” metni değişecek. Bu metin müşteriye gidiyor; `
      + 'gerekçe denetim kaydına yazılır.',
    confirmLabel: 'Kaydet',
    danger: false,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;

  await withBusy('Şablon yazılıyor…', async () => {
    const result = await call(`${BASE}/templates/${key}`, {
      method: 'PATCH', body: writeBody({ body, reason }, false),
    });
    if (announce(result, 'Şablon metni kaydedildi.')) {
      layer.close();
      await loadTemplates();
      paintCurrentTab();
    }
  });
}

async function toggleTemplate(row, next, layer) {
  const reason = await confirmWithReason(nodes.root, {
    title: next ? 'Bildirimi aç' : 'Bildirimi kapat',
    description: next
      ? `“${row.title}” açıldığında bu olay her gerçekleştiğinde müşteriye SMS `
        + 'gider ve her segment faturalanır. Açmak bilinçli bir karardır.'
      : `“${row.title}” kapatıldığında bu bildirim için gönderim denenmez ve `
        + 'gönderim kaydına satır yazılmaz.',
    confirmLabel: next ? 'Aç' : 'Kapat',
    danger: next,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;

  await withBusy(next ? 'Bildirim açılıyor…' : 'Bildirim kapatılıyor…', async () => {
    const result = await call(`${BASE}/templates/${row.key}`, {
      method: 'PATCH', body: writeBody({ enabled: next, reason }, false),
    });
    if (announce(result, next ? 'Bildirim açıldı.' : 'Bildirim kapatıldı.')) {
      layer?.close();
      await loadTemplates();
      paintCurrentTab();
    }
  });
}

async function serverPreview(key, form) {
  const reason = await confirmWithReason(nodes.root, {
    title: 'Sunucuda doğrula',
    description: 'Metin SUNUCUDA işlenir ve gerçek uzunluk/segment döner. '
      + 'Hiçbir SMS gönderilmez, ama bir denetim satırı yazılır.',
    confirmLabel: 'Doğrula',
    danger: false,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;

  await withBusy('Sunucuda işleniyor…', async () => {
    const result = await call(`${BASE}/templates/${key}/preview`, {
      method: 'POST',
      body: writeBody({ body: String(form.draft().body || ''), reason }, false),
    });
    const data = result?.data || {};
    toast(`Sunucu: ${num(data.length)} karakter, ${num(data.segments)} segment `
      + `(${data.encoding || '—'}).`, 'good');
  });
}

// ------------------------------------------------------------- deneme SMS

function openTestDialog(row) {
  const layer = drawer(nodes.root, {
    title: 'Deneme SMS\'i',
    subtitle: row ? row.title : 'Serbest metin',
    onClose: () => release(),
  });
  const drop = keep(closers, () => { layer.close(); form.destroy(); });
  const release = () => { drop(); form.destroy(); };

  const form = formGrid({
    fields: [
      { key: 'phone', label: 'Cep numarası', type: 'phone', required: true,
        hint: '10 hane, 5 ile başlar. Numara denetim kaydına MASKELİ yazılır.' },
      row ? null : { key: 'body', label: 'Metin', type: 'textarea', wide: true,
        required: true, maxLength: BODY_MAX },
    ],
    value: { phone: '', body: '' },
  });

  const info = hintBox(
    'Metnin başına sunucu [DENEME] ekler ve bu kaldırılamaz: deneme SMS\'inin '
    + 'gerçek bir bildirimden ayırt edilememesi, yanlış numaraya giden bir '
    + 'mesajın müşteride panik yaratması demekti.');

  const actions = h('div', 'bs-actions');
  const gonder = async (dryRun) => {
    const draft = form.draft();
    if (!form.valid()) {
      form.showErrors();
      toast('Formda eksik var.', 'bad');
      return;
    }
    const reason = await confirmWithReason(nodes.root, {
      title: dryRun ? 'Kuru prova' : 'Deneme SMS\'i gönder',
      description: dryRun
        ? 'Hiçbir şey gönderilmez; sunucu yalnız işlenmiş metni döndürür.'
        : 'Bu numaraya gerçek bir SMS gider ve bir segment ücreti doğar.',
      confirmLabel: dryRun ? 'Prova' : 'Gönder',
      danger: !dryRun,
      minLength: REASON_MIN,
      placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
    });
    if (!reason) return;

    await withBusy(dryRun ? 'Prova çalıştırılıyor…' : 'Deneme gönderiliyor…', async () => {
      const result = await call(`${BASE}/send-test`, {
        method: 'POST',
        body: writeBody({
          phone: String(draft.phone || ''),
          template_key: row ? row.key : '',
          body: row ? '' : String(draft.body || ''),
          reason,
        }, dryRun),
      });
      const data = result?.data || {};
      const kutu = h('div');
      kutu.append(h('div', 'bs-card-title',
        result?.dry_run ? 'Kuru prova sonucu' : 'Gönderim sonucu'));
      kutu.append(h('div', 'bs-rendered', data.rendered || ''));
      if (!result?.dry_run) {
        // SAĞLAYICI HATASI İSTEK HATASI DEĞİL: uç `ok:true` döner ve durum
        // gövdededir. İkisini ayırmayan bir ekran "sunucu bozuldu" der.
        if (data.status === 'sent') {
          kutu.append(alertBox(`Gitti · ${num(data.segments)} segment.`, 'good'));
        } else {
          kutu.append(alertBox(`Gitmedi: ${data.error || 'sağlayıcı hata verdi'}.`, 'bad'));
          // `40` HATASI AÇIK METİNLE ANLATILIR. Sağlayıcının bu kodu tek bir
          // şey demek: gönderdiğimiz başlık Netgsm panelinde ONAYLI DEĞİL.
          // Ham cümle ("Mesaj başlığı sistemde tanımlı değil") yöneticiye ne
          // yapacağını söylemiyor ve hata sipariş/abonelik bildirimlerinde de
          // aynen tekrarlanıyor — yani tek bir yanlış harf bütün SMS trafiğini
          // sessizce düşürüyor.
          const hatali = headerRejected(data);
          if (hatali) kutu.append(hatali);
        }
      }
      layer.body.append(kutu);
    });
  };

  actions.append(button('Kuru prova', { onClick: () => gonder(true) }));
  actions.append(button('Gönder', { variant: 'danger', onClick: () => gonder(false) }));

  layer.body.append(info, form.node, actions);
}

// ========================================================= NETGSM AYARLARI
//
// BU SEKME BBD KANTİN'İN "Netgsm ayarları" SEKMESİNİN KARDEŞİDİR ama BİR
// ALANI EKSİKTİR VE BU BİLİNÇLİDİR: kullanıcı kodu ve parola BLD'de ortam
// değişkeninde yaşıyor ve hiçbir uçtan geri dönmüyor (K8 ile aynı gerekçe —
// sır her veritabanı yedeğine girerdi). Kantinde ikisi ayar satırında
// tutuluyor; BLD'de tutulmuyor ve bu ekran onları YAZIYORMUŞ gibi yapmaz.
// Yapsaydı yönetici parolayı buraya girer, hiçbir yere yazılmaz ve "kaydettim
// ama çalışmıyor" hâli doğardı.
//
// BAŞLIK İSE BURADAN YAZILIR ÇÜNKÜ BİR SIR DEĞİL: müşterinin telefonunda
// görünen addır ve BLD'de `BLEZZETDNYM` olmalıdır. Yalnız ortam değişkeninde
// durduğu sürece yanlış bir başlık sessiz bir arızaydı — Netgsm `40` döner,
// istek 200 kalır, kayıt `failed` olur ve kimse fark etmez.

function paintNetgsm() {
  const body = nodes.body;
  disposeTab();
  body.replaceChildren();

  if (!state.netgsmLoaded) {
    body.append(skeletonRows(4, 2));
    return;
  }
  if (!state.netgsm) {
    body.append(alertBox(
      `Netgsm ayarı okunamadı: ${state.link.error}. Sunucu geri geldiğinde `
      + '[Yenile] ile tazeleyin.', 'bad'));
    return;
  }

  const cfg = state.netgsm;
  const eksik = (cfg.missing || []).map((key) => MISSING_LABELS[key] || key);

  if (eksik.length) {
    body.append(alertBox(
      `Netgsm yapılandırması eksik — HİÇBİR SMS GİTMEZ. Eksik olan: ${eksik.join(', ')}. `
      + 'Bu hâlde gönderimler yalnız BLD sunucusunun günlüğüne yazılır ve panel '
      + '"gönderildi" dese bile müşteriye hiçbir şey ulaşmaz.', 'bad'));
  } else {
    body.append(alertBox(
      `Netgsm kurulu · gönderici adı "${cfg.header}" · sağlayıcı `
      + `${cfg.driver || 'bilinmiyor'}.`, 'good'));
  }

  // ── Sırlar: okunur ama YAZILAMAZ ────────────────────────────────────────
  const sirlar = h('div', 'bs-cell');
  sirlar.append(hintBox(
    'Netgsm hesabı BBD Kantin ile ORTAKTIR: aynı abonelik, aynı kredi havuzu, '
    + 'tek fatura. BLD\'yi ayıran tek ayar gönderici adıdır.'));
  const durum = h('div', 'bs-cell-row');
  durum.append(badge(
    cfg.username_configured ? 'Kullanıcı kodu tanımlı' : 'Kullanıcı kodu BOŞ',
    cfg.username_configured ? 'good' : 'bad'));
  durum.append(badge(
    cfg.password_configured ? 'Parola tanımlı' : 'Parola BOŞ',
    cfg.password_configured ? 'good' : 'bad'));
  sirlar.append(durum);
  sirlar.append(hintBox(
    'Kullanıcı kodu ve parola BURADAN DEĞİŞTİRİLEMEZ ve bu bilinçli: sır '
    + 'veritabanına yazılsaydı her yedeğe girerdi, yedekler ise sırlardan çok '
    + 'daha kolay dolaşıyor. Değiştirmek için Coolify\'daki NETGSM_USERNAME / '
    + 'NETGSM_PASSWORD değişkenleri güncellenir ve uygulama yeniden başlatılır.'));
  body.append(card('Hesap bilgileri (BLD ortam değişkenleri)', sirlar));

  // ── Başlık: buradan yazılır ─────────────────────────────────────────────
  const kutu = h('div', 'bs-cell');

  const form = formGrid({
    fields: [
      { key: 'header', label: 'Gönderici adı', required: false,
        maxLength: HEADER_MAX,
        hint: `En çok ${HEADER_MAX} karakter; yalnız harf, rakam ve boşluk. `
          + `BLD için beklenen değer "${EXPECTED_HEADER}". Netgsm panelinde `
          + 'ONAYLI olmalı — onaysız bir başlıkta sağlayıcı 40 döner ve tek '
          + 'mesaj bile ulaşmaz.' },
    ],
    // ORTAMDAN GELEN DEĞER KUTUYA YAZILMAZ. Yazılsaydı "kaydet"e basmak onu
    // ayara kopyalar ve ortam değişkeni sessizce gölgelenirdi; oysa kullanıcı
    // hiçbir şey değiştirmediğini sanıyordu.
    value: { header: cfg.stored_header || '' },
  });
  keep(tabClosers, () => form.destroy());

  kutu.append(form.node);

  // KAYNAK AYRI YAZILIR. Ayar kutusu boş ama ortam dolu olabilir; ikisini tek
  // satıra indirmek, yöneticinin "demek ki başlık yok" deyip ÇALIŞAN bir
  // yapılandırmayı bozmasına yol açardı.
  const kaynak = h('div', 'bs-cell');
  kaynak.append(h('div', undefined,
    `Yürürlükteki başlık: ${cfg.header ? `"${cfg.header}"` : '(yok)'}`));
  kaynak.append(h('span', 'bs-dim',
    `Kaynak: ${HEADER_SOURCES[cfg.source] || cfg.source || '—'}`));
  if (cfg.env_header) {
    kaynak.append(h('span', 'bs-dim',
      `Ortam değişkenindeki değer: "${cfg.env_header}" — yukarıdaki kutu `
      + 'boşaltılırsa bu değer yeniden yürürlüğe girer.'));
  }
  kutu.append(kaynak);

  if (cfg.header && cfg.header !== EXPECTED_HEADER) {
    kutu.append(alertBox(
      `Yürürlükteki ad "${cfg.header}" — BLD için beklenen "${EXPECTED_HEADER}". `
      + 'Bilerek değiştirildiyse sorun yok; değilse mesajlar müşteriye '
      + 'tanımadığı bir addan gider ya da hiç ulaşmaz.', 'warn'));
  }

  const eylemler = h('div', 'bs-actions');
  const yaz = async (dryRun) => {
    const draft = form.draft();
    const header = String(draft.header || '').trim();
    if (!form.valid()) {
      form.showErrors();
      toast('Formda eksik var.', 'bad');
      return;
    }
    const bosaltiliyor = header === '' && Boolean(cfg.stored_header);
    const reason = await confirmWithReason(nodes.root, {
      title: dryRun ? 'Kuru prova' : 'Gönderici başlığını yaz',
      description: bosaltiliyor
        ? 'Ayar SİLİNECEK ve başlık BLD ortam değişkenine geri dönecek '
          + `("${cfg.env_header || 'tanımsız'}").`
        : `BLD'den giden HER SMS "${header}" adıyla gidecek. Ad Netgsm `
          + 'panelinde onaylı değilse tek mesaj bile ulaşmaz.',
      confirmLabel: dryRun ? 'Prova' : 'Yaz',
      danger: !dryRun,
      minLength: REASON_MIN,
      placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
    });
    if (!reason) return;

    await withBusy(dryRun ? 'Prova çalıştırılıyor…' : 'Başlık yazılıyor…', async () => {
      const result = await call(`${BASE}/netgsm`, {
        method: 'PUT', body: writeBody({ header, reason }, dryRun),
      });
      if (!result?.ok) { toast(result?.error || 'Yazılamadı.', 'bad'); return; }
      if (!announce(result, bosaltiliyor ? 'Ayar silindi.' : 'Gönderici başlığı yazıldı.')) return;
      // YENİ BAŞLIK BİR SONRAKİ İSTEKTE YÜRÜRLÜĞE GİRER: BLD tarafında
      // gönderici istek başına çözülen bir tekil. Söylenmezse yönetici hemen
      // deneme SMS'i gönderip ESKİ başlıkla 40 alır ve kaydın işlemediğini
      // sanır.
      if ((result.warnings || []).includes('netgsm_header_applies_next_request')) {
        toast('Yeni başlık bir sonraki gönderimden itibaren geçerli.', 'warn');
      }
      state.templatesLoaded = false;
      await loadNetgsm();
      await loadTemplates();
      paintNetgsm();
    });
  };

  eylemler.append(button('Kuru prova', { onClick: () => yaz(true) }));
  eylemler.append(button('Yaz', { variant: 'danger', onClick: () => yaz(false) }));
  kutu.append(eylemler);
  body.append(card('Gönderici başlığı', kutu));

  body.append(hintBox(
    `Netgsm'in 40 hatası ("Mesaj başlığı sistemde tanımlı değil") tek bir şey `
    + 'demektir: buradaki ad sağlayıcının panelinde onaylı değil. Deneme SMS\'i '
    + 'o hatayı alırsa ekran bunu açık metinle yazar.'));
}

// =========================================================== TETİKLEYİCİLER

function paintTriggers() {
  const body = nodes.body;
  disposeTab();
  body.replaceChildren();

  if (!state.templatesLoaded) {
    body.append(skeletonRows(6, 3));
    return;
  }
  if (!state.link.connected) {
    body.append(alertBox(
      `Tetikleyiciler okunamadı: ${state.link.error}.`, 'bad'));
    return;
  }

  body.append(senderNotice());
  body.append(hintBox(
    'Her bildirim ayrı ayrı açılıp kapanır. Kapalı bir şablon için gönderim '
    + 'DENENMEZ ve gönderim kaydına satır yazılmaz. Açmak gerekçeli onaydan '
    + 'geçer: açık doğan bir bildirim, tek dağıtımı binlerce SMS\'e çevirir.'));

  for (const group of state.groups) {
    const rows = state.templates.filter((row) => row.group === group.key);
    if (!rows.length) continue;

    const list = h('div', 'bs-triggers');
    for (const row of rows) {
      const satir = h('div', 'bs-trigger');
      const sol = h('div', 'bs-trigger-main');
      const bas = h('div', 'bs-cell-row');
      bas.append(h('b', undefined, row.title));
      bas.append(badge(row.enabled ? 'Açık' : 'Kapalı', row.enabled ? 'good' : 'dim'));
      if (row.unconfirmed_enabled) bas.append(badge('bu ekrandan açılmadı', 'warn'));
      sol.append(bas);
      sol.append(h('div', 'bs-dim', row.about || ''));
      sol.append(h('div', 'bs-dim',
        `${num(row.length)} karakter · ${num(row.segments)} segment`));

      const sag = h('div', 'bs-cell-row');
      sag.append(button('Metni düzenle', { variant: 'ghost',
        onClick: () => openTemplate(row.key) }));
      sag.append(row.enabled
        ? button('Kapat', { variant: 'danger',
          onClick: () => toggleTemplate(row, false, null) })
        : button('Aç', { variant: 'primary',
          onClick: () => toggleTemplate(row, true, null) }));

      satir.append(sol, h('span', 'kit-spacer'), sag);
      list.append(satir);
    }
    body.append(card(group.label, list, group.note));
  }

  body.append(announcementCard());
}

// ------------------------------------------------------------- toplu duyuru

function announcementCard() {
  const box = h('div', 'bs-announce');

  if (!state.announcement) {
    box.append(alertBox(
      'Duyuru taslağı okunamadı. [Yenile] ile tekrar deneyin.', 'warn'));
    return card('Toplu duyuru', box);
  }

  const draft = state.announcement;
  const estimate = draft.estimate || {};

  box.append(kpiRow([
    { label: 'Kitle', value: labelOf(state.audiences, draft.audience) },
    { label: 'Alıcı (şu an)', value: num(estimate.recipients),
      title: 'Her okumada yeniden hesaplanır; donmuş bir tahmin, sandığınızdan '
        + 'fazla SMS göndermek demektir.' },
    { label: 'Segment (toplam)', value: num(estimate.segments),
      tone: 'warn', title: 'Gönderimin toplam maliyeti.' },
    { label: 'Son gönderim', value: draft.last_run_at ? ago(draft.last_run_at) : '—',
      title: stampIso(draft.last_run_at) },
  ]));

  const form = formGrid({
    fields: [
      { key: 'body', label: 'Duyuru metni', type: 'textarea', wide: true,
        required: true, maxLength: BODY_MAX,
        hint: 'Yalnız {customer_name} değişkeni kullanılabilir.' },
      { key: 'audience', label: 'Kitle', type: 'select',
        options: state.audiences.map((item) => ({ value: item.key, label: item.label })),
        hint: 'En geniş kitle listenin sonundadır ve ek onay ister.' },
    ],
    value: { body: draft.body || '', audience: draft.audience || 'active_customers' },
    onChange: () => refreshMeasure(),
  });
  keep(tabClosers, () => form.destroy());

  const measureBox = h('div');
  const refreshMeasure = debounce(async () => {
    try {
      const measure = await measureText(String(form.draft().body || ''), 'announcement',
        { customer_name: 'Mehmet Kaya' }, ['customer_name']);
      if (!measure) return;
      measureBox.replaceChildren(measureView(measure.rendered_measure));
      const oneri = simplifyHint(measure.rendered_measure,
        (text) => { form.set('body', text); refreshMeasure(); });
      if (oneri) measureBox.append(oneri);
    } catch (error) {
      measureBox.replaceChildren(alertBox(`Ölçüm yapılamadı: ${error.message}`, 'bad'));
    }
  }, MEASURE_MS);
  keep(tabClosers, () => refreshMeasure.cancel());
  refreshMeasure();

  const dryBox = h('div', 'bs-dry');
  paintDryRun(dryBox, form);

  const actions = h('div', 'bs-actions');
  actions.append(button('Taslağı kaydet', {
    variant: 'primary',
    title: 'Yalnız taslağı yazar. GÖNDERMEZ.',
    onClick: () => saveAnnouncement(form),
  }));
  actions.append(button('Kuru prova', {
    title: 'Kaç kişiye gideceğini ve metnin işlenmiş hâlini sunucudan sorar. '
      + 'Hiçbir SMS gönderilmez.',
    onClick: () => runAnnouncement(true, form, dryBox),
  }));

  box.append(
    hintBox('Metni yazmak ile göndermek AYRI eylemlerdir ve ayrı gerekçe ister. '
      + 'Gönderim düğmesi ancak kuru provadan sonra açılır; prova jetonu tek '
      + 'kullanımlıktır ve taslak değişince düşer. Duyuru KUYRUĞA ALINMAZ, akış '
      + 'hâlinde gider — yarıda kesilirse bir kısmı gönderilmiş olur.'),
    form.node,
    measureBox,
    actions,
    dryBox,
  );
  return card('Toplu duyuru', box,
    'Zamanlayıcı yoktur; duyuru elle çalıştırılır.');
}

function paintDryRun(box, form) {
  box.replaceChildren();
  const dry = state.announcementDry;
  if (!dry) {
    box.append(hintBox(
      'Henüz kuru prova yapılmadı. Gönderim düğmesi kapalı — kaç kişiye '
      + 'gideceğini görmeden yüzlerce SMS gönderilmez.'));
    box.append(blockedButton('Duyuruyu gönder',
      'Önce kuru prova çalıştırın: kaç alıcı ve hangi metin, gönderimden ÖNCE '
      + 'görülmelidir.', { variant: 'danger' }));
    return;
  }

  box.append(h('div', 'bs-card-title', 'Kuru prova sonucu'));
  box.append(kpiRow([
    { label: 'Alıcı', value: num(dry.recipients) },
    { label: 'Segment', value: num(dry.segments), tone: 'warn' },
    { label: 'Kitle', value: labelOf(state.audiences, dry.audience) },
    { label: 'Provanın yaşı', value: ago(dry.created_at), title: stampIso(dry.created_at) },
  ]));
  if (dry.sample_rendered) {
    box.append(h('div', 'bs-rendered', dry.sample_rendered));
  }
  box.append(hintBox(
    `Prova ${state.dryTtl || 15} dakika geçerlidir ve tek kullanımlıktır. Alıcı `
    + 'sayısı arada değişirse sunucu gönderimi reddeder ve prova yenilenir.'));
  box.append(button('Duyuruyu gönder', {
    variant: 'danger',
    onClick: () => runAnnouncement(false, form, box),
  }));
}

async function saveAnnouncement(form) {
  const draft = form.draft();
  if (!String(draft.body || '').trim()) {
    toast('Duyuru metni boş olamaz.', 'bad');
    return;
  }
  const reason = await confirmWithReason(nodes.root, {
    title: 'Duyuru taslağını kaydet',
    description: 'Yalnız taslak yazılır, HİÇBİR MESAJ GÖNDERİLMEZ. Bekleyen bir '
      + 'kuru prova varsa düşer: prova eski metne aitti.',
    confirmLabel: 'Kaydet',
    danger: false,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;

  await withBusy('Taslak yazılıyor…', async () => {
    const result = await call(`${BASE}/announcement`, {
      method: 'PUT',
      body: writeBody({ body: String(draft.body), audience: String(draft.audience), reason },
        false),
    });
    if (announce(result, 'Duyuru taslağı kaydedildi. Gönderim ayrı bir adımdır.')) {
      await loadAnnouncement();
      paintCurrentTab();
    }
  });
}

async function runAnnouncement(dryRun, form, box) {
  const dry = state.announcementDry;
  const alici = Number(dry?.recipients) || 0;
  const kitle = form ? String(form.draft().audience || '') : (dry?.audience || '');

  if (!dryRun) {
    // EN GENİŞ KİTLE VE BÜYÜK GÖNDERİM AYRICA SORULUR. İki yıl önce bir kez
    // sipariş vermiş birine duyuru göndermek, spam şikâyeti ve numara kaybıdır.
    if (kitle === 'all_customers' || alici >= (state.confirmThreshold || 100)) {
      const devam = await confirmSimple(nodes.root, {
        title: 'Emin misiniz?',
        description: `${num(alici)} kişiye SMS gidecek`
          + (kitle === 'all_customers'
            ? ' ve kitle BÜTÜN MÜŞTERİLER — uzun süredir sipariş vermeyenler dâhil.'
            : '.')
          + ' Gönderilen mesaj geri alınamaz.',
        confirmLabel: 'Devam',
        danger: true,
      });
      if (!devam) return;
    }
  }

  const reason = await confirmWithReason(nodes.root, {
    title: dryRun ? 'Kuru prova' : 'Toplu duyuruyu gönder',
    description: dryRun
      ? 'Hiçbir SMS gönderilmez. Sunucu kaç alıcı olduğunu ve işlenmiş metni döner.'
      : `${num(alici)} müşteriye SMS gidecek. Geri alınamaz ve her segment `
        + 'faturalanır.',
    confirmLabel: dryRun ? 'Prova' : 'Gönder',
    danger: !dryRun,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;

  await withBusy(dryRun ? 'Prova çalıştırılıyor…' : 'Duyuru gönderiliyor…', async () => {
    const result = await call(`${BASE}/announcement/run`, {
      method: 'POST',
      body: writeBody({
        reason,
        confirm_recipients: dryRun ? 0 : alici,
        token: dryRun ? '' : String(dry?.token || ''),
      }, dryRun),
    });

    if (dryRun) {
      state.announcementDry = { ...(result?.data || {}), created_at: new Date().toISOString() };
      toast(`Prova hazır: ${num(result?.data?.recipients)} alıcı, `
        + `${num(result?.data?.segments)} segment.`, 'good');
      paintDryRun(box, form);
      return;
    }

    const data = result?.data || {};
    toast(`Duyuru gönderildi: ${num(data.sent)} gitti, ${num(data.failed)} gitmedi `
      + `(${num(data.segments)} segment).`, data.failed ? 'warn' : 'good');
    state.announcementDry = null;
    await loadAnnouncement();
    paintCurrentTab();
  });
}

// ================================================================== GEÇMİŞ

function paintHistory() {
  const body = nodes.body;
  disposeTab();
  body.replaceChildren();

  if (!state.link.connected) {
    body.append(alertBox(`Gönderim kaydı okunamadı: ${state.link.error}.`, 'bad'));
  }

  const meta = state.logMeta || {};
  body.append(kpiRow([
    { label: 'Kayıt (süzgeçli)', value: num(meta.total) },
    { label: 'Gitti', value: num(meta.sent_count), tone: 'good' },
    { label: 'Gitmedi', value: num(meta.failed_count), tone: meta.failed_count ? 'bad' : '' },
    { label: 'Segment toplamı', value: num(meta.segment_total), tone: 'warn',
      title: 'Süzgeçlenmiş kümenin toplam maliyeti.' },
  ]));

  body.append(nodes.logFilters.node);

  const table = dataTable({
    dense: true,
    columns: [
      { key: 'sent_at', label: 'Zaman', width: '160px',
        cell: (row) => {
          const span = h('span', undefined, stampIso(row.sent_at));
          span.title = ago(row.sent_at);
          return span;
        } },
      { key: 'phone', label: 'Numara', width: '130px',
        title: 'Maskeli gelir; tam numara müşteri kartındadır ve orası denetlenir.' },
      { key: 'template_key', label: 'Şablon', width: '160px',
        cell: (row) => row.template_key || '—' },
      { key: 'context', label: 'Bağlam', width: '120px',
        cell: (row) => badge(labelOf(CONTEXTS, row.context), toneOf(CONTEXTS, row.context)) },
      { key: 'status', label: 'Durum', width: '130px',
        cell: (row) => {
          const box = h('div', 'bs-cell-row');
          box.append(badge(labelOf(STATUSES, row.status), toneOf(STATUSES, row.status)));
          if (row.error) {
            const uyari = h('span', 'bs-dim', row.error);
            uyari.title = row.error;
            box.append(uyari);
          }
          return box;
        } },
      { key: 'segments', label: 'Segment', width: '100px', align: 'num',
        cell: (row) => num(row.segments) },
      { key: 'body', label: 'Metin (kırpık)', width: 'minmax(0, 2fr)' },
    ],
    rows: state.logRows,
    empty: emptyState({
      title: 'Bu süzgece uyan gönderim yok',
      text: 'Süzgeci genişletin ya da tarih aralığını değiştirin.',
      actions: [button('Süzgeci temizle', { onClick: () => resetLogFilters() })],
    }),
  });

  const sayfa = pager({
    total: Number(meta.total) || 0,
    page: Number(meta.page) || state.logPage,
    size: Number(meta.per_page) || state.logSize,
    onChange: ({ page, size }) => {
      state.logPage = page;
      state.logSize = size;
      refreshLog();
    },
  });

  const kutu = h('div');
  kutu.append(table.node, sayfa.node);
  body.append(card('Gönderim kaydı', kutu,
    'Kayıt silinemez; silme ucu yoktur.'));

  body.append(card('Bu ekrandan yapılan denemeler', localHistoryTable(),
    'BLD\'nin denetim izinden ayrıdır: burada sunucuya ULAŞMAYAN denemeler de var.'));
}

function localHistoryTable() {
  const table = dataTable({
    dense: true,
    columns: [
      { key: 'created_at', label: 'Zaman', width: '160px',
        cell: (row) => stampIso(row.created_at) },
      { key: 'action', label: 'Eylem', width: '190px' },
      { key: 'target_key', label: 'Hedef', width: '160px',
        cell: (row) => row.target_key || row.target_type || '—' },
      { key: 'result', label: 'Sonuç', width: '130px',
        cell: (row) => badge(row.result, {
          ok: 'good', dry_run: 'info', denendi: 'dim', engellendi: 'warn', hata: 'bad',
        }[row.result] || '') },
      { key: 'actor', label: 'Kim', width: '150px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
    ],
    rows: state.history,
    empty: emptyState({ title: 'Henüz deneme yok',
      text: 'Bu ekrandan bir yazma yapıldığında satır burada belirir.' }),
  });
  return table.node;
}

function resetLogFilters() {
  nodes.logFilters?.reset?.();
  state.logPage = 1;
  refreshLog();
}

async function refreshLog() {
  await withBusy('Gönderim kaydı alınıyor…', async () => {
    await loadLog();
    await loadHistory();
    if (state.tab === 'history') paintHistory();
  });
}

// ================================================================== mount

function paintCurrentTab() {
  ({
    templates: paintTemplates,
    triggers: paintTriggers,
    netgsm: paintNetgsm,
    history: paintHistory,
  }[state.tab] || paintTemplates)();
  nodes.status?.set(statusText());
}

async function showTab(key) {
  state.tab = key;
  if (key === 'templates' || key === 'triggers') {
    if (!state.templatesLoaded) {
      paintCurrentTab();
      await withBusy('Şablonlar alınıyor…', loadTemplates);
    }
    // ALICI TAHMİNİ HER AÇILIŞTA TAZELENİR: sayı sürekli değişiyor ve donmuş
    // bir tahmin, yöneticinin sandığından fazla SMS göndermesi demekti.
    if (key === 'triggers') await withBusy('Duyuru taslağı alınıyor…', loadAnnouncement);
  } else if (key === 'netgsm') {
    // HER AÇILIŞTA TAZE OKUNUR: başlık BLD'de konsoldan ya da ortam
    // değişkeninden de değişmiş olabilir ve bayat bir değer, yöneticinin
    // ekranda gördüğü adla gerçekte gönderilen adın ayrışması demekti.
    paintCurrentTab();
    await withBusy('Netgsm ayarı alınıyor…', loadNetgsm);
  } else if (key === 'history') {
    await refreshLog();
    return;
  }
  paintCurrentTab();
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel bs');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'templates', label: 'Şablonlar' },
    { key: 'triggers', label: 'Tetikleyiciler' },
    // ŞABLONDAN AYRI BİR SEKME: metni düzeltmekle bütün gönderimlerin görünen
    // adını değiştirmek aynı iş değil ve ikincisi tek satırda bütün SMS
    // trafiğini düşürebilir.
    { key: 'netgsm', label: 'Netgsm ayarları' },
    { key: 'history', label: 'Geçmiş' },
  ], 'templates', (key) => { showTab(key); });

  // Süzgeç şeridi sekmeyle birlikte yok edilmez: `filterBar` global dinleyici
  // tutuyor (takvim) ve her sekme geçişinde yenisini kurmak onları biriktirirdi.
  nodes.logFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '200px', placeholder: 'Numara (maskeli eşleşir)' },
      { kind: 'select', key: 'template_key', label: 'Şablon',
        options: [{ value: '', label: 'Tümü' }] },
      { kind: 'select', key: 'status', label: 'Durum',
        options: [{ value: '', label: 'Tümü' },
          ...STATUSES.map((item) => ({ value: item.key, label: item.label }))] },
      { kind: 'select', key: 'context', label: 'Bağlam',
        options: [{ value: '', label: 'Tümü' },
          ...CONTEXTS.map((item) => ({ value: item.key, label: item.label }))] },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // dateRange kitin kendi takvimini kullanır.
      { kind: 'dateRange', key: 'range', label: 'Tarih', start: '', end: '' },
    ],
    onChange: () => { state.logPage = 1; refreshLog(); },
    actions: [
      button('Yenile', { onClick: () => refreshLog() }),
      button('CSV', {
        title: 'Ekranda görünen sayfayı Excel için indirir. Numara maskeli kalır.',
        onClick: () => {
          const sayi = csvBlob(
            ['Zaman', 'Numara', 'Şablon', 'Bağlam', 'Durum', 'Segment', 'Metin'],
            state.logRows.map((row) => [row.sent_at, row.phone, row.template_key,
              row.context, row.status, row.segments, row.body]),
            'bld-sms-gonderim');
          toast(`${sayi} satır indirildi.`, 'good');
        },
      }),
    ],
  });
  keep(closers, () => nodes.logFilters.destroy());

  nodes.status = statusLine();
  nodes.body = h('div', 'bs-body');

  const bar = h('div', 'bs-topbar');
  bar.append(nodes.tabs.node, h('span', 'kit-spacer'));
  bar.append(button('Yenile', {
    onClick: () => withBusy('Tazeleniyor…', async () => {
      state.templatesLoaded = false;
      await loadTemplates();
      if (state.tab === 'triggers') await loadAnnouncement();
      if (state.tab === 'netgsm') await loadNetgsm();
      paintCurrentTab();
    }),
  }));
  bar.append(button('Anahtarı kopyala', {
    variant: 'ghost',
    title: 'Seçili sekmedeki şablon anahtarlarını panoya kopyalar.',
    onClick: async () => {
      const ok = await copyText(state.templates.map((row) => row.key).join('\n'));
      toast(ok ? 'Anahtarlar kopyalandı.' : 'Pano kullanılamadı.', ok ? 'good' : 'warn');
    },
  }));

  view.append(bar, nodes.status.node, nodes.body);
  root.replaceChildren(view);
  showTab('templates');

  return () => {
    disposeTab();
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
