// Bildirimler paneli — müşteri uygulamasında ve sitede gösterilen UYGULAMA-İÇİ
// duyuruların hazırlanması, yayını ve ölçümü.
//
// NE YAPAR: duyuru listesi ve süzgeçleri; taslak yazma ve düzenleme; müşterinin
// göreceği kartın CANLI ÖNİZLEMESİ; gerekçeli onayla yayın ve arşiv; görülme /
// kapatılma istatistiği; bu ekrandan yapılan yazma denemelerinin yerel izi.
//
// PUSH BİLDİRİMİ YOKTUR. Bu bir eksiklik değil, bir iş kararıdır (BLD kararı 11):
// duyuru İTTİRİLMEZ, müşteri uygulamayı açtığında görür. Ekranda "push",
// "gönder", "ilet" gibi bir dil KULLANILMAZ — yöneticiye telefonların titreyeceği
// hissini veren tek kelime, gerçekleşmeyecek bir vaattir. Acil duyuru SMS'tir ve
// başka bir ekranın işidir.
//
// NE YAPMAZ:
//  · KAYIT SİLMEZ. Arşiv YUMUŞAKTIR: satır durur, `status = archived` olur ve
//    istatistik çalışmaya devam eder. Gerçek silme ucu sözleşmede yoktur.
//  · YAYINDAN KALDIRMAZ. `POST /{id}/unpublish` bilerek tanımlanmadı; yolu
//    bitiş anını geçmişe çekmek ya da arşivlemektir. Üçüncü bir yol, "duyuru
//    neden görünmüyor" sorusunun üç ayrı cevabı olması demekti.
//  · `live` DEĞERİNİ HESAPLAMAZ. Sunucuda hesaplanır ve olduğu gibi gösterilir;
//    istemcide hesaplansaydı saati kaymış bir panelde duyuru bir gün erken
//    "bitmiş" görünürdü. Ekranın türettiği tek şey, o değerin AÇIKLAMASIDIR
//    ("yayında ama henüz başlamadı" / "süresi doldu").
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · GÖVDE DÜZ METİNDİR, HTML DEĞİL (sözleşme §Şema). Duyuru üç istemcide
//    birden çiziliyor (Next.js, Flutter müşteri, ileride başkaları) ve HTML'i
//    üçünde tutarlı çizmek imkânsız. Bu yüzden burada zengin metin
//    düzenleyicisi YOKTUR ve önizleme metni `textContent` ile yazılır: satır
//    sonları korunur, etiketler ETİKET OLARAK GÖRÜNÜR ve kimse yazdığı
//    `<b>`nin müşteride kalınlaşacağını sanmaz.
//  · KİTLESİ "herkes" OLAN DUYURU ÖLÇÜLEMEZ. Giriş yapmamış ziyaretçinin
//    kimliği yok, okunma kaydı yazılamıyor. Ekran bu durumda "0 görülme"
//    yazmaz, "ölçülemez" yazar: sıfır "kimse görmedi" demektir ve çalışan bir
//    duyuruyu başarısız gösterirdi.
//  · YAYINLADIM AMA GÖRÜNMÜYOR. Yayın yanıtındaki `live_from` doluysa duyuru
//    yayındadır ama penceresi henüz açılmamıştır; ekran bunu açıkça yazar —
//    aksi hâlde hiçbir şey görmeyen yönetici düğmeye ikinci kez basardı.
//  · PENCERE GÜN OLARAK SEÇİLİR, AN OLARAK YAZILIR. Ayrıntı ve gerekçesi
//    aşağıda `gunBaslangici` yorumunda.
//  · KAPATILAMAZ DUYURU YALNIZ "KRİTİK" OLABİLİR. Kapatılamayan bir
//    bilgilendirme, müşteri uygulamasını kullanılamaz hâle getirir.
//  · KAPSAM DEĞİŞTİRMEK GEÇMİŞİ SİLMEZ ama kimin göreceğini değiştirir; sunucu
//    uyarı döndürür ve ekran onu YUTMAZ.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_notifications/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_notifications/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, clip, confirmWithReason, h, loadStyles, num,
  percent, pollLoop, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { barChart } from '../../ui-kit/charts.js';

const BASE = '/api/bld_notifications';

/** Gerekçe sınırları — sunucu da denetliyor (`00-genel.md` §3), bu erken uyarı. */
const REASON_MIN = 10;
const REASON_MAX = 500;

/** Sözleşmedeki alan sınırları. Sunucudan da geliyor; burası yedek değer. */
const LIMITS = {
  title_max: 160, body_max: 2000, action_label_max: 60, action_url_max: 255,
};

/**
 * Düzey sözlüğü. RENK TEK BAŞINA ANLAM TAŞIMAZ (kit kuralı 7): rozetin içinde
 * her yerde yazı var ve önizleme kartı düzeyi ayrıca kelimeyle söyler.
 */
const LEVELS = [
  { value: 'info', label: 'Bilgi', tone: 'info' },
  { value: 'warning', label: 'Uyarı', tone: 'warn' },
  { value: 'critical', label: 'Kritik', tone: 'bad' },
];

const AUDIENCES = [
  {
    value: 'all',
    label: 'Herkes',
    help: 'Giriş yapmamış ziyaretçi dâhil herkes görür (site dâhil). Kimlik '
      + 'olmadığı için OKUNMA KAYDI YAZILAMAZ: bu duyuru istatistik üretmez.',
  },
  {
    value: 'customers',
    label: 'Giriş yapmış müşteriler',
    help: 'Uygulamaya giriş yapmış müşteriler görür; görülme ve kapatılma sayılır.',
  },
  {
    value: 'subscribers',
    label: 'Aktif aboneler',
    help: 'Yalnız aktif aboneliği olan müşteriler görür; görülme ve kapatılma sayılır.',
  },
];

const STATUSES = [
  { value: 'draft', label: 'Taslak', tone: 'dim' },
  { value: 'published', label: 'Yayında', tone: 'good' },
  { value: 'archived', label: 'Arşiv', tone: '' },
];

/**
 * `visibility` — sunucudan gelen `live` değerinin AÇIKLAMASI.
 *
 * Üç ayrı durum aynı `live: false` değerinden çıkıyor: taslak, henüz
 * başlamamış ve süresi dolmuş. Yalnız `live`e bakan bir ekran üçüne de
 * "görünmüyor" derdi ve yönetici hangisini düzelteceğini bilemezdi.
 */
const VISIBILITY = {
  live: { label: 'Şu an görünüyor', tone: 'good' },
  scheduled: { label: 'Yayında, henüz başlamadı', tone: 'info' },
  expired: { label: 'Süresi doldu', tone: 'dim' },
  hidden: { label: 'Yayında ama sunucu göstermiyor', tone: 'warn' },
  draft: { label: 'Taslak — yayında değil', tone: 'dim' },
  archived: { label: 'Arşivde — görünmüyor', tone: '' },
};

/** Yerel denetim izinin `result` sözlüğü. */
const RESULTS = {
  denendi: { label: 'Denendi', tone: 'warn' },
  ok: { label: 'Yazıldı', tone: 'good' },
  dry_run: { label: 'Kuru prova', tone: 'warn' },
  engellendi: { label: 'Engellendi', tone: 'bad' },
  hata: { label: 'Hata', tone: 'bad' },
};

const ACTIONS = {
  'notification.create': 'Taslak oluşturma',
  'notification.update': 'Düzenleme',
  'notification.publish': 'Yayınlama',
  'notification.archive': 'Arşivleme',
};

const EMPTY_STATE = {
  tab: 'list',
  link: { connected: true, error: '' },
  items: [],
  meta: { page: 1, per_page: 25, total: 0, last_page: 1, live_count: null },
  settings: { page_size: 25, refresh_seconds: 120, ending_soon_hours: 48 },
  filters: { q: '', status: '', audience: '', level: '', live: '' },
  page: 1,
  loaded: false,
  listError: '',
  serverTime: '',
  drawerOpen: false,
  audit: [],
  auditLoaded: false,
};

let api = null;
let toast = null;
let state = { ...EMPTY_STATE };
let busy = false;
const nodes = {};

// ================================================================= altyapı

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
 * DURUMDUR: duyuru gerçekten yok değil, ŞU AN OKUNAMIYOR. Sessizce boş liste
 * çizmek yanlış olurdu — yönetici "hiç duyuru yok" ile "sunucuya ulaşılamıyor"u
 * ayırt edemez ve olmayan bir duyuruyu ikinci kez yazardı.
 *
 * @returns {boolean} veri güvenilir mi
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = { connected: false, error: payload.error || 'BLD sunucusuna ulaşılamıyor.' };
    return false;
  }
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

/** Bağlantı kopukken çizilen uyarı; bağlantı varken `null`. */
function linkAlert({ stale = false, what = 'Duyuru listesi' } = {}) {
  if (state.link.connected) return null;
  return alertBox(
    `BLD sunucusuna ULAŞILAMIYOR — ${state.link.error} `
    + (stale
      ? `${what} son başarılı okumadan kalma ve BAYAT: yeni duyurular, yayın `
        + 'durumu ve görülme sayıları burada GÖRÜNMÜYOR olabilir. '
      : `${what} okunamadı. `)
    + 'Bağlantı geri geldiğinde "Yenile" ile tazeleyin.', 'bad');
}

/** Yazma düğmesinin kapalı olma nedeni; yazılabiliyorsa boş dize. */
function linkBlock() {
  if (state.link.connected) return '';
  return `BLD sunucusuna ulaşılamıyor (${state.link.error}) — ulaşılamayan bir `
    + 'sunucuya gönderilen duyuru yöneticiye "yayınlandı" hissi verirdi. '
    + 'Bağlantı gelince düğme kendiliğinden açılır.';
}

/** Yazma düğmesi. Kapalıysa NEDENİNİ söyler (kit README §blockedButton). */
function writeButton(label, { variant = '', title = '', onClick, blocked = '' } = {}) {
  const why = blocked || linkBlock();
  if (why) return blockedButton(label, why, { variant });
  return button(label, { variant, title, onClick });
}

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.status?.set(label);
  try {
    const result = await work();
    nodes.status?.set(statusText());
    return result;
  } catch (error) {
    nodes.status?.set(`Hata: ${error.message}`, true);
    toast(error.message, 'bad');
    return null;
  } finally {
    busy = false;
  }
}

/** Gerekçeli onay. Gerekçe backend'e gider ve denetim kaydına yazılır (ADR 0012). */
function askReason({ title, description, confirmLabel, danger = true }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
}

/**
 * Yazma sonucunu bildirir ve GERÇEKTEN yazılıp yazılmadığını döndürür.
 *
 * Panel kuru prova İSTEMEZ (şalter yoktur) ama yanıttaki bayrak YİNE DE
 * okunur: bir kurulum `dry_run_default` ayarını açık bırakırsa ekran
 * "yayınlandı" DEMEMELİ. Sessiz kalan bir prova, duyurunun hiç yayınlanmadığını
 * ancak müşteri şikâyetiyle öğretirdi.
 */
function announce(result, doneText) {
  if (result?.dry_run) {
    toast('KURU PROVA AÇIK — hiçbir şey yazılmadı. Bu ekranda kuru prova şalteri '
      + 'yoktur; ayar modülde açık bırakılmış (modules.bld_notifications.'
      + 'dry_run_default ya da modules.bld_api.dry_run_default).', 'warn');
    return false;
  }
  toast(doneText, 'good');
  showWarnings(result?.warnings);
  return true;
}

/**
 * Sunucunun uyarıları — `audience_changed_after_publish` gibi.
 *
 * Uyarı bir hata DEĞİLDİR: yazma başarılı olmuştur. Yutulursa yönetici,
 * kapsamı daralttığında kaç müşterinin duyuruyu artık göremeyeceğini hiç
 * öğrenemez; bu yüzden ayrı ve kalıcı bir kutuda gösterilir.
 */
function showWarnings(warnings) {
  if (!Array.isArray(warnings) || warnings.length === 0) return;
  // Kutu MOUNT'TA kurulur ve listeyle birlikte yeniden çizilmez: uyarı yazma
  // işleminden hemen sonra geliyor ve arkasından liste tazeleniyor. Her çizimde
  // yeniden kurulsaydı uyarı, okunmadan silinirdi.
  nodes.warnBox?.replaceChildren(...warnings.map((item) => alertBox(
    item.note || item.message || `Uyarı: ${item.code || 'bilinmeyen'}`, 'warn')));
}

// ============================================================ gün ↔ an dönüşümü
//
// SÖZLEŞME AN İSTER, YÖNETİCİ GÜN DÜŞÜNÜR. `starts_at` / `ends_at` alanları
// ISO 8601 UTC anlarıdır; yönetici ise "20 Ağustos'ta başlasın, 30 Ağustos'ta
// bitsin" diye konuşur. Ekran bu ikisini şöyle bağlar:
//
//   başlangıç günü G      → `G T00:00:00Z`
//   son görüneceği gün S  → `(S+1) T00:00:00Z`   (yani pencere S gününü kapsar)
//
// İkinci satır sözleşmenin kendi örneğiyle birebir aynı: 30 Ağustos kapanış
// duyurusunun `ends_at` değeri `2026-08-31T00:00:00Z`. "Bitiş" alanına 31
// yazdırmak yöneticiyi her seferinde bir gün eksik ya da fazla yazmaya
// itiyordu; alan "son görüneceği gün" diye soruluyor ve +1 kaydırma burada,
// tek yerde yapılıyor.
//
// SAAT DİLİMİ ÇEVİRİSİ YAPILMAZ. `00-genel.md` §6 anları UTC'de sabitliyor ve
// gün içi saatleri yaz saati yüzünden çevirmemeyi açıkça söylüyor. Yerel gün
// başlangıcına oturtmak için sabit bir "+3" uygulamak, o uyarıyı görmezden
// gelmek olurdu. Pratik sonuç: pencere İstanbul saatiyle 03:00'te açılır ve
// kapanır; ekran bunu yazar, gizlemez.

const DAY_TAIL = 'T00:00:00Z';

function dayShift(iso, delta) {
  const [year, month, day] = String(iso).split('-').map(Number);
  if (!year || !month || !day) return '';
  const moved = new Date(Date.UTC(year, month - 1, day + delta));
  const pad = (value) => String(value).padStart(2, '0');
  return `${moved.getUTCFullYear()}-${pad(moved.getUTCMonth() + 1)}-${pad(moved.getUTCDate())}`;
}

/** "Gün başlangıcına oturuyor mu" — oturmuyorsa gün alanı kullanılamaz. */
const isDayStart = (instant) => Boolean(instant) && String(instant).endsWith(DAY_TAIL);

const dayOfInstant = (instant) => (isDayStart(instant) ? String(instant).slice(0, 10) : '');

/** Başlangıç anı ← gün. */
const startInstant = (day) => (day ? `${day}${DAY_TAIL}` : '');

/** Bitiş anı ← SON GÖRÜNECEĞİ gün (dâhil). */
const endInstant = (day) => (day ? `${dayShift(day, 1)}${DAY_TAIL}` : '');

/** Son görüneceği gün ← bitiş anı. */
const endDay = (instant) => (isDayStart(instant) ? dayShift(String(instant).slice(0, 10), -1) : '');

/** Gün → "20 Ağu 2026". Uzun ad tablo hücresine sığmıyor. */
function shortDay(iso) {
  if (!iso) return '';
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('tr-TR', { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Pencerenin insan diliyle karşılığı. Boş uçlar AÇIKÇA söylenir. */
function windowText(row) {
  const from = dayOfInstant(row.starts_at);
  const to = endDay(row.ends_at);
  const left = from ? shortDay(from) : (row.published_at ? 'yayından itibaren' : 'yayınla birlikte');
  const right = to ? shortDay(to) : 'süresiz';
  if (!row.starts_at && !row.ends_at) return 'Yayınlandığı andan itibaren, süresiz';
  return `${left} → ${right}`;
}

// ================================================================= çizimler

const levelInfo = (key) => LEVELS.find((item) => item.value === key) || LEVELS[0];
const audienceInfo = (key) => AUDIENCES.find((item) => item.value === key) || AUDIENCES[1];
const statusInfo = (key) => STATUSES.find((item) => item.value === key) || STATUSES[0];

function statusText() {
  const parts = [];
  parts.push(state.link.connected ? 'Bağlı' : `BAĞLANTI YOK — ${state.link.error}`);
  if (state.loaded) {
    parts.push(`${num(state.meta.total)} duyuru (süzgeçli)`);
    parts.push(state.meta.live_count === null
      ? 'şu an görünen: sunucu bildirmedi'
      : `şu an görünen: ${num(state.meta.live_count)}`);
  }
  return parts.join(' · ');
}

/**
 * MÜŞTERİNİN GÖRECEĞİ KART — canlı önizleme.
 *
 * Yöneticinin yazdığı metnin müşteride nasıl duracağını göstermenin tek dürüst
 * yolu, onu aynen çizmek. İki ayrıntı bilerek böyle:
 *
 *  1. GÖVDE `textContent` İLE YAZILIR, `innerHTML` ile değil (kit kuralı 11).
 *     Gövde sözleşmede DÜZ METİN; `<b>` yazan yönetici müşteride kalın yazı
 *     değil, `<b>` harflerini görecek ve önizleme bunu ona ŞİMDİ söyler.
 *  2. KAPATMA DÜĞMESİ yalnız `dismissible` ise çizilir. Kapatılamayan bir
 *     duyurunun kartında da düğme görünseydi, yönetici kararının sonucunu
 *     göremezdi.
 */
function previewCard(draft) {
  const level = levelInfo(draft.level);
  const box = h('div', `bn-preview bn-level-${draft.level || 'info'}`);

  const head = h('div', 'bn-preview-head');
  head.append(badge(level.label, level.tone));
  head.append(h('span', 'kit-spacer'));
  if (draft.dismissible !== false) {
    const close = h('span', 'bn-preview-close', '✕');
    close.title = 'Müşteri duyuruyu kapatabilir';
    head.append(close);
  } else {
    head.append(h('span', 'bn-preview-locked', 'kapatılamaz'));
  }
  box.append(head);

  box.append(h('div', 'bn-preview-title', draft.title || 'Başlık yazılmadı'));

  const body = h('div', 'bn-preview-body');
  const lines = String(draft.body || '').split('\n');
  if (!draft.body) {
    body.append(h('span', 'bn-dim', 'Gövde yazılmadı.'));
  } else {
    // Satır sonu `\n` sözleşmede destekli; her satır AYRI düğüm olur ve metin
    // `textContent` ile yazılır — HTML asla yorumlanmaz.
    for (const line of lines) body.append(h('div', 'bn-preview-line', line));
  }
  box.append(body);

  if (draft.action_label && draft.action_url) {
    const action = h('span', 'bn-preview-action', draft.action_label);
    action.title = `Adres: ${draft.action_url}`;
    box.append(action);
  }
  return box;
}

// ============================================================== liste sekmesi

const LIST_COLUMNS = [
  {
    key: 'title',
    label: 'Duyuru',
    width: 'minmax(0, 2.2fr)',
    cell: (row) => {
      const box = h('div', 'bn-cellstack');
      box.append(clip(h('b'), row.title, 52));
      box.append(clip(h('span', 'bn-sub'), String(row.body || '').replace(/\n/g, ' '), 78));
      return box;
    },
  },
  {
    key: 'status',
    label: 'Durum',
    width: '190px',
    cell: (row) => {
      const box = h('div', 'bn-cellstack');
      const info = statusInfo(row.status);
      const view = VISIBILITY[row.visibility] || VISIBILITY.draft;
      box.append(badge(info.label, info.tone));
      box.append(h('span', 'bn-sub', view.label));
      return box;
    },
  },
  {
    key: 'level',
    label: 'Düzey',
    width: '92px',
    cell: (row) => {
      const info = levelInfo(row.level);
      return badge(info.label, info.tone);
    },
  },
  {
    key: 'audience',
    label: 'Kitle',
    width: '150px',
    cell: (row) => {
      const info = audienceInfo(row.audience);
      const box = h('div', 'bn-cellstack');
      box.append(h('span', undefined, info.label));
      if (!row.trackable) box.append(h('span', 'bn-sub', 'ölçülemez'));
      return box;
    },
  },
  {
    key: 'window',
    label: 'Gösterim penceresi',
    width: 'minmax(0, 1.2fr)',
    cell: (row) => {
      const box = h('div', 'bn-cellstack');
      box.append(h('span', undefined, windowText(row)));
      if (row.visibility === 'live' && endsSoon(row)) {
        box.append(h('span', 'bn-warn', `${Math.max(0, Math.round(row.ends_in_hours))} saat sonra bitiyor`));
      }
      return box;
    },
  },
  {
    key: 'seen_count',
    label: 'Görülme',
    width: '96px',
    align: 'num',
    cell: (row) => (row.trackable ? num(row.seen_count) : 'ölçülemez'),
  },
  {
    key: 'updated_at',
    label: 'Güncelleme',
    width: '150px',
    cell: (row) => whenCell(row.updated_at),
  },
];

/** Mutlak zaman + göreli zaman birlikte: biri ötekini tamamlar, değiştirmez. */
function whenCell(iso) {
  if (!iso) return h('span', 'bn-sub', '—');
  const box = h('span', 'bn-when');
  const relative = h('b', undefined, ago(iso));
  relative.title = stampIso(iso);
  box.append(relative, h('span', 'bn-sub', stampIso(iso)));
  return box;
}

function endsSoon(row) {
  const limit = Number(state.settings.ending_soon_hours || 48);
  return typeof row.ends_in_hours === 'number' && row.ends_in_hours >= 0
    && row.ends_in_hours <= limit;
}

async function loadList({ silent = false } = {}) {
  if (!silent) nodes.status?.set('Duyurular alınıyor…');
  const params = new URLSearchParams();
  const { q, status, audience, level, live } = state.filters;
  if (q) params.set('q', q);
  if (status) params.set('status', status);
  if (audience) params.set('audience', audience);
  if (level) params.set('level', level);
  // ÜÇ DEĞERLİ: boş = süzgeç yok, 'true' = şu an görünen, 'false' = görünmeyen.
  if (live) params.set('live', live);
  params.set('page', String(state.page));

  let payload = null;
  try {
    payload = await call(`${BASE}/notices?${params.toString()}`);
  } catch (error) {
    state.listError = error.message;
    state.loaded = true;
    return;
  }
  state.listError = '';
  linkOk(payload);
  state.items = Array.isArray(payload?.items) ? payload.items : [];
  state.meta = payload?.meta || EMPTY_STATE.meta;
  state.settings = payload?.settings || state.settings;
  state.serverTime = payload?.server_time || '';
  state.loaded = true;
}

function paintList() {
  const wrap = nodes.body;
  wrap.replaceChildren();

  const kpis = kpiRow([
    {
      label: 'Şu an görünen',
      value: state.meta.live_count === null ? '—' : num(state.meta.live_count),
      tone: state.meta.live_count ? 'good' : '',
      title: state.meta.live_count === null
        ? 'Sunucu bu sayıyı bildirmedi. Boş bırakıldı; sıfır yazmak "hiçbiri '
          + 'görünmüyor" demek olurdu ve bu ölçülmemiş bir iddia olurdu.'
        : 'Yayında OLAN ve gösterim penceresi ŞU AN açık olan duyuru sayısı. '
          + 'Süzgeçten bağımsızdır.',
    },
    {
      label: 'Süzgece uyan',
      value: num(state.meta.total),
      title: 'Seçili süzgeçlere uyan toplam duyuru sayısı.',
    },
  ]);
  wrap.append(kpis);

  const warn = linkAlert({ stale: state.items.length > 0 });
  if (warn) wrap.append(warn);
  if (state.listError) wrap.append(alertBox(`Liste okunamadı — ${state.listError}`, 'bad'));

  wrap.append(nodes.warnBox, nodes.filters.node);

  let empty;
  if (!state.loaded) {
    empty = skeletonRows(6, LIST_COLUMNS.length);
  } else if (state.listError || !state.link.connected) {
    empty = emptyState({
      title: 'Duyurular okunamıyor',
      text: 'BLD sunucusuna ulaşılamıyor. Boş liste "duyuru yok" anlamına GELMEZ; '
        + 'yayında duran duyurular müşteride görünmeye devam ediyor olabilir.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshList() })],
    });
  } else if (hasFilter()) {
    empty = emptyState({
      title: 'Bu süzgece uyan duyuru yok',
      text: 'Süzgeçleri temizleyip tekrar bakın.',
      actions: [button('Süzgeci temizle', { onClick: () => clearFilters() })],
    });
  } else {
    empty = emptyState({
      title: 'Henüz duyuru yok',
      text: 'Bakım bildirimi, tatil duyurusu ya da yeni hizmet tanıtımı burada '
        + 'hazırlanır. Duyuru müşteriye İTTİRİLMEZ: uygulamayı açtığında görür.',
      actions: [writeButton('Yeni duyuru', { variant: 'primary', onClick: () => openEditor(null) })],
    });
  }

  nodes.table = dataTable({
    columns: LIST_COLUMNS,
    rows: state.items,
    empty,
    onRow: (row) => openEditor(row),
  });
  wrap.append(nodes.table.node);

  nodes.pager = pager({
    total: state.meta.total,
    page: state.meta.page,
    size: state.meta.per_page,
    sizes: [state.meta.per_page],
    onChange: ({ page }) => {
      state.page = page;
      refreshList();
    },
  });
  wrap.append(nodes.pager.node);

  wrap.append(hintBox(
    'Duyuru PUSH DEĞİLDİR: müşterinin telefonu titremez, uygulamayı açtığında '
    + 'görür. Acil bir şey duyurulacaksa SMS kullanılır. Gövde düz metindir; '
    + 'HTML etiketleri müşteride etiket olarak görünür.'));

  nodes.status?.set(statusText(), !state.link.connected || Boolean(state.listError));
}

const hasFilter = () => Object.values(state.filters).some((value) => Boolean(value));

/** `reset()` süzgeç şeridinin kendi `onChange`'ini tetikler; liste ORADAN tazelenir. */
function clearFilters() {
  nodes.filters?.reset?.();
}

async function refreshList({ silent = false } = {}) {
  await withBusy('Duyurular alınıyor…', async () => {
    await loadList({ silent });
    if (state.tab === 'list') {
      paintList();
      // Yoklama aralığı MODÜL AYARINDAN gelir ve ilk yanıtla birlikte öğrenilir;
      // döngü açılışta yedek değerle kuruluyor. Ayar başkaysa döngü yeniden
      // kurulur — yoksa panel, kurulumun seçtiği aralığı hiç uygulamazdı.
      if (nodes.poll && nodes.pollEvery !== pollInterval()) startPolling();
    }
  });
}

// ============================================================== düzenleyici

/**
 * Duyuru çekmecesi. `row` boşsa YENİ TASLAK.
 *
 * Çekmece KENDİ KENDİNE TAZELENMEZ: açık bir formun altından veri değiştirmek,
 * yazılmakta olan metni kaybettirirdi. Yoklama da çekmece açıkken durur.
 */
function openEditor(row) {
  const editing = Boolean(row && row.id);
  const record = row || {};

  const draft = {
    title: record.title || '',
    body: record.body || '',
    level: record.level || 'info',
    audience: record.audience || 'customers',
    startDay: dayOfInstant(record.starts_at),
    endDay: endDay(record.ends_at),
    dismissible: record.dismissible !== false,
    action_label: record.action_label || '',
    action_url: record.action_url || '',
  };

  // PENCERESİ GÜN BAŞINA OTURMAYAN KAYIT. Sunucudaki an gün başlangıcı değilse
  // (başka bir araçla ya da elle yazılmışsa) gün alanı onu TEMSİL EDEMEZ.
  // Sessizce yuvarlamak, yöneticinin dokunmadığı bir pencereyi değiştirmek
  // olurdu; bu yüzden alan boş bırakılır ve durum açıkça yazılır.
  const oddWindow = (record.starts_at && !isDayStart(record.starts_at))
    || (record.ends_at && !isDayStart(record.ends_at));

  state.drawerOpen = true;
  const box = drawer(nodes.root, {
    title: editing ? 'Duyuruyu düzenle' : 'Yeni duyuru',
    subtitle: editing
      ? `#${record.id} · ${statusInfo(record.status).label}`
      : 'Kayıt TASLAK olarak doğar; yayın ayrı bir adımdır.',
    onClose: () => {
      // `formGrid.destroy()` tarih alanlarının GLOBAL dinleyicilerini bırakır;
      // çağrılmazsa kapanmış çekmecenin takvimi `document` üzerinde dinlemeye
      // devam eder (kit kuralı 4).
      form.destroy();
      state.drawerOpen = false;
      nodes.editor = null;
    },
  });
  // AÇIK ÇEKMECE TEK: paneli kapatan temizlik onu kapatabilsin diye tutulur.
  // Her açılışta listeye eklemek, oturum boyunca ölü kapanış biriktirirdi.
  nodes.editor = box;

  const preview = h('div', 'bn-previewbox');
  const audienceNote = h('div', 'bn-note');
  const actions = h('div', 'bn-actions');
  const messages = h('div', 'bn-alertbox');

  // İkisi de FONKSİYON BİLDİRİMİ (hoisted): `formGrid` çağrısındaki `onChange`
  // onları adıyla anıyor ve `form` değişkeni o an henüz atanmamış oluyor.
  // Çağrılmaları ise ilk kullanıcı etkileşiminde, yani atamadan SONRA.
  function repaint() {
    const current = form.draft();
    preview.replaceChildren(previewCard(current));
    audienceNote.textContent = audienceInfo(current.audience).help;
  }

  const form = formGrid({
    value: draft,
    // Takvimden seçilen gün `input` olayı ÜRETMEZ (değer programla yazılıyor);
    // düğme şeridini DOM olayına bağlamak, tarih değişince kilidi güncellemezdi.
    onChange: () => { repaint(); rebuildActions(); },
    fields: [
      {
        key: 'title',
        label: 'Başlık',
        type: 'text',
        required: true,
        maxLength: LIMITS.title_max,
        wide: true,
        hint: `En çok ${LIMITS.title_max} karakter.`,
      },
      {
        key: 'body',
        label: 'Gövde',
        type: 'textarea',
        required: true,
        maxLength: LIMITS.body_max,
        wide: true,
        hint: 'DÜZ METİN. Satır sonu korunur; kalın/renkli yazı, bağlantı ve '
          + 'HTML etiketi YOKTUR — duyuru üç ayrı istemcide çiziliyor.',
        validate: (value) => (String(value || '').trim().length < 2
          ? 'Gövde en az 2 karakter olmalı.' : null),
      },
      {
        key: 'level',
        label: 'Düzey',
        type: 'select',
        options: LEVELS.map((item) => ({ value: item.value, label: item.label })),
        hint: 'Rozetin rengini ve önceliğini belirler.',
      },
      {
        key: 'audience',
        label: 'Hedef kitle',
        type: 'select',
        options: AUDIENCES.map((item) => ({ value: item.value, label: item.label })),
      },
      {
        key: 'startDay',
        label: 'İlk görüneceği gün',
        type: 'date',
        hint: 'Boş = yayınlandığı anda görünmeye başlar.',
      },
      {
        key: 'endDay',
        label: 'Son görüneceği gün (dâhil)',
        type: 'date',
        hint: 'Boş = süresiz. Seçilen günün sonuna kadar görünür.',
        validate: (value, current) => {
          if (!value || !current.startDay) return null;
          return value < current.startDay
            ? 'Son gün, ilk günden önce olamaz.' : null;
        },
      },
      {
        key: 'dismissible',
        label: 'Müşteri kapatabilsin',
        type: 'checkbox',
        hint: 'Kapalıysa duyuru ekranda kalır. YALNIZ "Kritik" düzeyle birlikte '
          + 'kullanılabilir: kapatılamayan bir bilgilendirme, uygulamayı '
          + 'kullanılamaz hâle getirir.',
        validate: (value, current) => (value === false && current.level !== 'critical'
          ? 'Kapatılamayan duyuru yalnız "Kritik" düzeyiyle kullanılabilir.' : null),
      },
      {
        key: 'action_label',
        label: 'Düğme yazısı',
        type: 'text',
        maxLength: LIMITS.action_label_max,
        hint: 'Boş bırakılırsa düğme çizilmez.',
        validate: (value, current) => (value && !current.action_url
          ? 'Adressiz bir etiket tıklanamaz: düğme adresini de yazın.' : null),
      },
      {
        key: 'action_url',
        label: 'Düğme adresi',
        type: 'text',
        maxLength: LIMITS.action_url_max,
        placeholder: 'https://… ya da /abonelik',
        hint: 'YALNIZ https:// ya da uygulama-içi yol (/ ile başlar).',
        validate: (value, current) => {
          if (!value) return current.action_label
            ? 'Etiketsiz bir düğme çizilemez: ya adresi ya da yazıyı boşaltın.' : null;
          return urlProblem(value);
        },
      },
    ],
  });

  function rebuildActions() {
    actions.replaceChildren();
    const status = record.status || 'draft';

    actions.append(writeButton(editing ? 'Değişiklikleri kaydet' : 'Taslağı oluştur', {
      variant: 'primary',
      onClick: () => saveNotice({ form, record, editing, box }),
    }));

    if (editing && status !== 'published') {
      actions.append(writeButton('Yayınla', {
        variant: 'danger',
        title: 'Duyuru bütün hedef kitleye açılır; geri alma ucu yoktur.',
        onClick: () => publishNotice(record, box),
        blocked: form.dirty().length
          ? 'Önce değişiklikleri kaydedin: kaydedilmemiş metin yayınlanmaz ve '
            + 'yayınlanan duyuru ekranda gördüğünüzden başka olurdu.'
          : '',
      }));
    }
    if (editing && status === 'published') {
      actions.append(blockedButton('Yayınla',
        'Bu duyuru zaten yayında. Yayından kaldırmanın yolu bitiş gününü geçmişe '
        + 'çekmek ya da arşivlemektir; üçüncü bir yol yoktur.'));
    }
    if (editing && status !== 'archived') {
      actions.append(writeButton('Arşivle', {
        variant: 'danger',
        title: 'Duyuru ANINDA görünmez olur; kayıt ve istatistik durur.',
        onClick: () => archiveNotice(record, box),
      }));
    }
  }

  // ---- çekmece gövdesi
  box.body.append(messages);
  if (oddWindow) {
    messages.append(alertBox(
      'Bu duyurunun gösterim penceresi gün başlangıcına oturmuyor '
      + `(${record.starts_at || 'başlangıç yok'} → ${record.ends_at || 'bitiş yok'}). `
      + 'Gün alanları boş bırakıldı: bir gün seçerseniz o an DEĞİŞİR. '
      + 'Dokunmazsanız pencere olduğu gibi kalır.', 'warn'));
  }
  if (record.status === 'published') {
    messages.append(hintBox(
      'Yayınlanmış duyuru düzenlenebilir ve bu bilinçlidir: yazım hatası '
      + 'düzeltmek, tarihi uzatmak gerçek ihtiyaçlar. Kapsamı (hedef kitle) '
      + 'değiştirirseniz duyuruyu daha önce görmüş bazı müşteriler kapsam dışında '
      + 'kalır; görülme kayıtları SİLİNMEZ.'));
  }

  box.body.append(card('Müşterinin göreceği kart', preview,
    'Uygulama açıldığında bu kart gösterilir. Gövde düz metindir: yazdığınız '
    + 'HTML etiketleri müşteride de etiket olarak görünür.'));
  box.body.append(card('Hedef kitle', audienceNote));
  box.body.append(card('Duyuru', form.node));
  box.body.append(actions);

  if (editing) {
    const statsBox = h('div', 'bn-statsbox');
    statsBox.append(skeletonRows(2, 4));
    box.body.append(card('Görülme istatistiği', statsBox,
      'Sayılar sunucuda tutulur; kitlesi "Herkes" olan duyuru ölçülemez.'));
    loadStats(record.id).then((payload) => {
      // Çekmece bu arada kapanmış olabilir; kapalı bir düğüme çizmek zararsız
      // ama gereksiz. Kök hâlâ belgede mi diye bakmak yeterli.
      if (statsBox.isConnected) paintStats(statsBox, payload, record);
    });
  }

  repaint();
  rebuildActions();
}

/** Adres denetimi — backend'deki kuralın AYNISI (K9: çift kapı, erken uyarı). */
function urlProblem(value) {
  const url = String(value || '').trim();
  if (!url) return null;
  if (/\s/.test(url)) return 'Düğme adresinde boşluk olamaz.';
  if (url.startsWith('//')) {
    return 'Adres `//` ile başlayamaz: bu uygulama-içi bir yol değil, başka bir '
      + 'siteye giden mutlak adrestir.';
  }
  if (url.startsWith('/')) return null;
  if (url.startsWith('https://')) return null;
  return 'Adres https:// ile ya da uygulama-içi yol olarak / ile başlamalı. '
    + 'http://, javascript: ve data: kabul edilmez.';
}

function draftBody(form) {
  const draft = form.draft();
  return {
    title: String(draft.title || '').trim(),
    body: String(draft.body || ''),
    level: draft.level || 'info',
    audience: draft.audience || 'customers',
    starts_at: startInstant(draft.startDay) || null,
    ends_at: endInstant(draft.endDay) || null,
    action_label: String(draft.action_label || '').trim() || null,
    action_url: String(draft.action_url || '').trim() || null,
    dismissible: draft.dismissible !== false,
  };
}

async function saveNotice({ form, record, editing, box }) {
  form.showErrors();
  const errors = form.errors();
  if (errors.length) {
    toast(errors[0].message, 'bad');
    form.focus(errors[0].key);
    return;
  }
  const fields = draftBody(form);

  const reason = await askReason({
    title: editing ? 'Duyuruyu güncelle' : 'Taslağı oluştur',
    description: editing
      ? `“${fields.title}” kaydı güncellenecek. Gerekçe denetim kaydına yazılır.`
      : `“${fields.title}” TASLAK olarak kaydedilecek; müşteriye henüz gitmez.`,
    confirmLabel: editing ? 'Güncelle' : 'Taslağı oluştur',
    danger: false,
  });
  if (!reason) return;

  await withBusy(editing ? 'Duyuru güncelleniyor…' : 'Taslak oluşturuluyor…', async () => {
    let result;
    if (editing) {
      // KISMİ YAZMA — ama eş alanlar BİRLİKTE gider. `starts_at`/`ends_at` ve
      // `action_label`/`action_url` çiftlerinin kuralı ikisinin BİRLİKTE hâli
      // hakkındadır ve sunucuda tek duyuru okuyan bir uç yok; yalnız birini
      // göndermek, kuralı doğrulanamaz bırakırdı. Değişmemiş eşi göndermenin
      // bedeli yok: aynı değer yazmak bir değişiklik değildir.
      const dirty = new Set(form.dirty());
      const body = { reason };
      const put = (key) => { body[key] = fields[key]; };
      if (dirty.has('title')) put('title');
      if (dirty.has('body')) put('body');
      if (dirty.has('level') || dirty.has('dismissible')) { put('level'); put('dismissible'); }
      if (dirty.has('audience')) put('audience');
      if (dirty.has('startDay') || dirty.has('endDay')) { put('starts_at'); put('ends_at'); }
      if (dirty.has('action_label') || dirty.has('action_url')) {
        put('action_label'); put('action_url');
      }
      if (Object.keys(body).length === 1) {
        toast('Değişen bir alan yok.', 'warn');
        return;
      }
      result = await call(`${BASE}/notices/${record.id}`, { method: 'PATCH', body });
    } else {
      result = await call(`${BASE}/notices`, { method: 'POST', body: { ...fields, reason } });
    }
    if (announce(result, editing ? 'Duyuru güncellendi.' : 'Taslak oluşturuldu.')) {
      box.close();
      await refreshList({ silent: true });
    }
  });
}

async function publishNotice(record, box) {
  const audience = audienceInfo(record.audience);
  const window = windowText(record);
  const reason = await askReason({
    title: 'Duyuruyu yayınla',
    description: `“${record.title}” yayına alınacak ve ${audience.label.toLowerCase()} `
      + `için açılacak (${window}). Yayından kaldırmanın ayrı bir ucu YOKTUR: `
      + 'bitiş gününü geçmişe çekmek ya da arşivlemek gerekir. Gerekçe denetim '
      + 'kaydına yazılır.',
    confirmLabel: 'Yayınla',
  });
  if (!reason) return;

  await withBusy('Duyuru yayınlanıyor…', async () => {
    const result = await call(`${BASE}/notices/${record.id}/publish`,
      { method: 'POST', body: { reason } });
    if (!announce(result, 'Duyuru yayınlandı.')) return;

    const info = result?.publish || {};
    if (info.live_from) {
      // "YAYINLADIM AMA GÖRÜNMÜYOR" — düğmeye ikinci kez basılmasının tek
      // sebebi bu cümlenin yazılmamasıdır.
      toast(`Yayınlandı ama HENÜZ GÖRÜNMÜYOR: gösterim penceresi `
        + `${stampIso(info.live_from)} tarihinde açılıyor.`, 'warn');
    }
    if (typeof info.estimated_audience === 'number') {
      toast(`Tahmini kitle: ${num(info.estimated_audience)} kişi.`, 'good');
    }
    box.close();
    await refreshList({ silent: true });
  });
}

async function archiveNotice(record, box) {
  const reason = await askReason({
    title: 'Duyuruyu arşivle',
    description: `“${record.title}” ANINDA görünmez olur; bitiş günü beklenmez. `
      + 'Kayıt SİLİNMEZ ve görülme istatistiği çalışmaya devam eder — bir '
      + 'duyurunun kaç kişiye ulaştığı sonradan sorulan bir sorudur.',
    confirmLabel: 'Arşivle',
  });
  if (!reason) return;

  await withBusy('Duyuru arşivleniyor…', async () => {
    const result = await call(`${BASE}/notices/${record.id}/archive`,
      { method: 'POST', body: { reason } });
    if (announce(result, 'Duyuru arşivlendi.')) {
      box.close();
      await refreshList({ silent: true });
    }
  });
}

// ================================================================ istatistik

async function loadStats(id) {
  try {
    return await call(`${BASE}/notices/${id}/stats`);
  } catch (error) {
    return { ok: false, connected: false, error: error.message, data: {} };
  }
}

/**
 * İstatistik bloğu.
 *
 * `trackable: false` HATA DEĞİLDİR: kitlesi "Herkes" olan duyuru ölçülemez ve
 * sunucu sayıları `null` döndürür. Ekran bu durumda SIFIR YAZMAZ — sıfır
 * "kimse görmedi" demektir ve çalışan bir duyuruyu başarısız gösterirdi.
 */
function paintStats(box, payload, record) {
  box.replaceChildren();
  const data = payload?.data || {};

  if (payload?.connected === false) {
    box.append(alertBox(`İstatistik okunamadı — ${payload.error || 'bağlantı yok'}`, 'bad'));
    return;
  }
  if (record.status === 'draft') {
    box.append(hintBox('Taslak duyuru hiç gösterilmedi; ölçecek bir şey yok.'));
    return;
  }
  if (data.trackable === false) {
    box.append(alertBox(
      'Bu duyuru ÖLÇÜLEMEZ: kitlesi "Herkes" ve giriş yapmamış ziyaretçinin '
      + 'kimliği olmadığı için okunma kaydı yazılamıyor. Sayıların boş olması '
      + '"kimse görmedi" demek DEĞİLDİR.', 'info'));
    return;
  }

  const size = data.audience_size;
  box.append(kpiRow([
    { label: 'Kitle büyüklüğü', value: size === null || size === undefined ? '—' : num(size),
      title: 'ŞU ANKİ kitle büyüklüğü, yayın anındaki değil: müşteri sayısı '
        + 'artıyor ve donmuş bir payda oranı zamanla yanlış gösterirdi.' },
    { label: 'Görülme', value: data.seen_count === null || data.seen_count === undefined
      ? '—' : num(data.seen_count) },
    { label: 'Kapatılma', value: data.dismissed_count === null
      || data.dismissed_count === undefined ? '—' : num(data.dismissed_count),
      title: record.dismissible === false
        ? 'Bu duyuru kapatılamıyor; sayı anlamlı değildir.' : '' },
    { label: 'Görülme oranı', value: data.seen_rate === null || data.seen_rate === undefined
      ? '—' : percent(Number(data.seen_rate) * 100),
      title: data.seen_rate === null
        ? 'Kitle büyüklüğü sıfır olduğu için oran hesaplanamadı.' : '' },
  ]));

  if (data.first_seen_at || data.last_seen_at) {
    const line = h('div', 'bn-note');
    line.append(h('span', undefined, `İlk görülme: ${stampIso(data.first_seen_at) || '—'}`));
    line.append(h('span', undefined, ` · Son görülme: ${stampIso(data.last_seen_at) || '—'}`));
    box.append(line);
  }

  if (Array.isArray(data.daily) && data.daily.length) {
    box.append(barChart(data.daily.map((item) => ({
      label: shortDay(item.date),
      value: Number(item.seen || 0),
      display: num(item.seen),
    })), { max: 30 }));
    box.append(h('div', 'bn-sub', 'Günlük görülme — sunucu en fazla 90 gün taşır.'));
  } else if (data.daily === null) {
    box.append(h('div', 'bn-sub', 'Günlük döküm ölçülemiyor.'));
  }
}

// ============================================================ denetim sekmesi

const AUDIT_COLUMNS = [
  { key: 'created_at', label: 'Zaman', width: '160px', cell: (row) => whenCell(row.created_at) },
  { key: 'action', label: 'İşlem', width: '150px',
    cell: (row) => ACTIONS[row.action] || row.action },
  { key: 'notification_id', label: 'Duyuru', width: '90px', align: 'num',
    cell: (row) => (row.notification_id ? `#${row.notification_id}` : '—') },
  { key: 'result', label: 'Sonuç', width: '120px',
    cell: (row) => {
      const info = RESULTS[row.result] || { label: row.result || '—', tone: '' };
      return badge(info.label, info.tone);
    } },
  { key: 'actor', label: 'Kim', width: '150px' },
  { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.6fr)',
    cell: (row) => clip(h('span'), row.reason, 90) },
];

async function loadAudit() {
  try {
    const payload = await call(`${BASE}/audit`);
    state.audit = Array.isArray(payload?.items) ? payload.items : [];
  } catch (error) {
    state.audit = [];
    toast(error.message, 'bad');
  }
  state.auditLoaded = true;
}

function paintAudit() {
  const wrap = nodes.body;
  wrap.replaceChildren();
  wrap.append(hintBox(
    'Bu liste, BU EKRANDAN yapılan yazma DENEMELERİDİR ve yereldir. Sunucunun '
    + 'kendi denetim izi ayrıdır ve yalnız kendisine ULAŞAN isteği bilir: ağ '
    + 'koparsa "kim neyi denedi" sorusunun cevabı yalnız burada kalır. '
    + '"Denendi" satırının yanında "Yazıldı" yoksa isteğin sonucu bilinmiyor '
    + 'demektir.'));

  nodes.auditTable = dataTable({
    columns: AUDIT_COLUMNS,
    rows: state.audit,
    dense: true,
    empty: emptyState({
      title: 'Yerel iz boş',
      text: 'Bu ekrandan henüz bir yazma denemesi yapılmadı.',
    }),
  });
  wrap.append(nodes.auditTable.node);
  nodes.status?.set(`${num(state.audit.length)} yerel iz satırı`, false);
}

// ================================================================== yoklama

/** Yoklama aralığı (ms). Alt sınır sunucu bütçesini korur (`00-genel.md` §2). */
const pollInterval = () => Math.max(30, Number(state.settings.refresh_seconds || 120)) * 1000;

function startPolling() {
  stopPolling();
  const every = pollInterval();
  nodes.pollEvery = every;
  nodes.poll = pollLoop({
    every,
    // ÇEKMECE AÇIKKEN TAZELEME YOK: açık bir formun altından liste değiştirmek
    // yazılmakta olan metni kaybettirmez ama kullanıcıyı şaşırtır; üstelik
    // paylaşılan hız kovasından boşuna pay yer.
    run: async () => {
      if (busy || state.drawerOpen || state.tab !== 'list') return;
      await loadList({ silent: true });
      if (state.tab === 'list' && !state.drawerOpen) paintList();
    },
  });
}

function stopPolling() {
  // `pollLoop.stop()` hem zamanlayıcıyı hem `visibilitychange` dinleyicisini
  // bırakır (kit kuralı 4); yalnız zamanlayıcıyı temizlemek yetmez.
  nodes.poll?.stop();
  nodes.poll = null;
  nodes.pollEvery = 0;
}

// ==================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE, filters: { ...EMPTY_STATE.filters } };

  const view = h('div', 'kit-panel bn');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'list', label: 'Duyurular' },
    { key: 'audit', label: 'Denetim izi' },
  ], 'list', (key) => showTab(key));

  // Süzgeç şeridi sekmeyle birlikte yok edilmez: `filterBar` global dinleyici
  // tutuyor ve her sekme geçişinde yenisini kurmak onları biriktirirdi.
  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px', placeholder: 'Başlık ya da gövde' },
      { kind: 'select', key: 'status', label: 'Durum',
        options: [{ value: '', label: 'Tümü' },
          ...STATUSES.map((item) => ({ value: item.value, label: item.label }))] },
      { kind: 'select', key: 'audience', label: 'Kitle',
        options: [{ value: '', label: 'Tümü' },
          ...AUDIENCES.map((item) => ({ value: item.value, label: item.label }))] },
      { kind: 'select', key: 'level', label: 'Düzey',
        options: [{ value: '', label: 'Tümü' },
          ...LEVELS.map((item) => ({ value: item.value, label: item.label }))] },
      // ÜÇ DEĞERLİ SÜZGEÇ, ANAHTAR DEĞİL: "şu an görünmeyenler" gerçek bir
      // sorudur (yayında sanılıp görünmeyen duyuruyu bulmanın tek yolu) ve
      // açık/kapalı bir anahtar onu "süzgeç yok" ile aynı yere düşürürdü.
      { kind: 'select', key: 'live', label: 'Görünürlük',
        options: [{ value: '', label: 'Tümü' },
          { value: 'true', label: 'Şu an görünen' },
          { value: 'false', label: 'Şu an görünmeyen' }] },
    ],
    onChange: (values) => {
      state.filters = {
        q: values.q || '', status: values.status || '', audience: values.audience || '',
        level: values.level || '', live: values.live || '',
      };
      state.page = 1;
      refreshList();
    },
    // Şeritteki düğmeler BİR KEZ kurulur; "Yeni duyuru" burada `writeButton`
    // DEĞİL çünkü bağlantı sonradan koparsa kapanmayan, koptuktan sonra
    // gelirse açılmayan bir düğme kalırdı. Yazma kapısı çekmecenin içindedir
    // ve orada her çizimde tazelenir.
    actions: [
      button('Yenile', { onClick: () => refreshList() }),
      button('Yeni duyuru', { variant: 'primary', onClick: () => openEditor(null) }),
    ],
  });

  nodes.status = statusLine();
  nodes.body = h('div', 'bn-body');
  // Sunucu uyarılarının KALICI kabı. Liste her çizildiğinde yeniden kurulsaydı,
  // kapsam değişikliği uyarısı yazmadan hemen sonraki tazelemede silinirdi.
  nodes.warnBox = h('div', 'bn-alertbox');

  const bar = h('div', 'bn-topbar');
  bar.append(nodes.tabs.node);
  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    if (key === 'list') {
      startPolling();
      paintList();
      if (!state.loaded) refreshList();
    } else {
      // CANLI VERİ YALNIZ LİSTE SEKMESİNDE: kapalı sekme için istek üretmek,
      // paylaşılan `bld-control-panel` bütçesini boşuna yakar.
      stopPolling();
      paintAudit();
      if (!state.auditLoaded) {
        withBusy('Yerel iz alınıyor…', async () => { await loadAudit(); paintAudit(); });
      }
    }
  }

  root.replaceChildren(view);
  showTab('list');

  return () => {
    stopPolling();
    // Açık çekmece kendi `onClose`'unu koşar ve `formGrid.destroy()` orada
    // çağrılır: tarih alanlarının `document` üzerindeki dinleyicileri ancak
    // böyle bırakılır (kit kuralı 4).
    try { nodes.editor?.close(); } catch { /* kapanışta hata yutulur */ }
    nodes.editor = null;
    nodes.filters?.destroy();     // arama ve açılırlar global dinleyici tutar
    root.replaceChildren();
    state = { ...EMPTY_STATE, filters: { ...EMPTY_STATE.filters } };
    busy = false;
  };
}
