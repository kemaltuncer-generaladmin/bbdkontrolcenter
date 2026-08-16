// Site İçeriği paneli — kurumsal sitenin (Next.js) beslendiği metinlerin yönetimi.
//
// NE YAPAR: yedi kurumsal içerik anahtarı (marka, iletişim, kurumsal metinler,
// SSS, sektörler, menü çözümleri, kalite zinciri), hizmet sayfaları ve bilgi
// merkezi yazıları. Gövdeler zengin metin düzenleyicisiyle yazılır, beyaz
// listeden geçirilerek önizlenir ve kaydedildikten sonra site yeniden
// çizdirilir.
//
// EKRANIN TEK BÜYÜK VAADİ: "KAYDETTİM AMA SİTEDE YOK" DENMESİN.
// Site ISR ile önbellekleniyor; yazma başarılı olsa bile önbellek tazelenmezse
// sayfa eski hâliyle durur. Sunucu bunu bilerek bir hata saymıyor (içerik
// gerçekten yazıldı) ve 200 döndürüyor — o yüzden SÖYLEMEK EKRANIN İŞİ. Her
// kaydetmeden sonra üstteki şeritte üç hâlden biri yazar:
//
//     Site tazelendi                    · yazıldı ve yayında
//     Site TAZELENMEDİ — [Yeniden dene] · yazıldı, önbellek boşalmadı
//     Sonuç bildirilmedi — [Tazele]     · yazıldı, sunucu sonucu söylemedi
//     Tazeleme istenmedi — [Tazele]     · yönetici bilerek kapattı (toplu çizim)
//
// NE YAPMAZ:
//  · KAYIT SİLMEZ (bir istisnayla). Yayından çıkarmak `is_published = false`
//    ile olur ve `bld_cms.manage` yeter. GERÇEK silme ayrı bir izne
//    (`bld_cms.delete`) bağlıdır, gerekçe ister ve geri alınamaz — sözleşme
//    yumuşak silme sunmuyor.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//  · YOKLAMA YAPMAZ. İçeriği yalnız bu ekran değiştiriyor; saniyede bir
//    tazelemenin kazandıracağı bir şey yok ve açık bir düzenleyicinin altından
//    veri değiştirmek, yazılmakta olan metni kaybettirirdi. Paylaşılan saatlik
//    bütçe (3000/saat/IP) gerçekten yoklayan ekranlara ayrılmıştır.
//  · İKİNCİ BİR BEYAZ LİSTE TUTMAZ. İzin verilen etiketler okuma yanıtıyla
//    iniyor (`screen.editor.allowed_tags`); panelde kopyası tutulsaydı
//    sunucudaki liste değiştiğinde ekran yanlış cümleyi göstermeye devam
//    ederdi (kit kuralı 10 — `store_cms` bunu denedi ve iki liste ayrıştı).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · İÇERİK DEĞERİ ŞEMASIZDIR. Sunucu `value` içeriğini doğrulamıyor; yalnız
//    geçerli JSON olduğunu ve 256 KB'ı aşmadığını denetliyor. Bu yüzden alan
//    formu VERİDEN türetilir: hangi alanlar varsa onlar çizilir. Boş bir
//    anahtarda sözleşmenin ÖRNEK gövdesindeki alanlar önerilir (yalnız
//    `brand`, `contact`, `faq` için — örnekte geçmeyen anahtara alan adı
//    uydurmak, siteyi okumayan bir alan doldurmak olurdu). Form yetmediğinde
//    çıkış yolu "Ham JSON" sekmesidir.
//  · DEĞER TAM DEĞERDİR, BİRLEŞTİRİLMEZ. Formdan çıkarılan bir alan sunucudan
//    da silinir; ekran bunu kaydetmeden önce yazar.
//  · `slug` DEĞİŞİMİ ADRESİ KIRAR. Uyarı ONAY KUTUSUNDA gösterilir, yazma
//    bittikten sonra değil: kaydettikten sonra öğrenen yönetici kırılan
//    bağlantıları geri getiremez.
//  · GÖVDE KAYITTA TEMİZLENİR. Kaydetme yanıtı sunucunun temizlediği hâli
//    döndürür ve ekran ONU gösterir — gönderdiğini geri okumayan bir editör,
//    yapıştırmanın kaybolduğunu fark ettirmez.
//  · SATIR İÇİ GÖRSEL DÜĞMESİ, YÜKLEME UCU YOKKEN HİÇ ÇİZİLMEZ. Sözleşmede
//    (docs/control/cms.md) görsel yükleme ucu yok; basınca hiçbir şey olmayan
//    bir düğme bırakmak yerine düzenleyicinin altına ne yapılacağı yazılır.
//    Uç eklendiğinde düğme kendiliğinden gelir (`screen.image_upload`).
//  · OKUMA SÜRESİ BOŞ BIRAKILABİLİR: boş "sen hesapla" demektir. Sıfır yazmak
//    "bu yazı okunmuyor" anlamına gelen bir sayı üretirdi; ekran hesaplanan
//    değeri ayrı bir ipucuyla gösterir.
//  · GEÇMİŞ SEKMESİ YERELDİR ve BLD düşse bile okunur. Bir yedek değildir:
//    eski sürüm düzenleyiciye GETİRİLİR, yönetici bakar ve kendi gerekçesiyle
//    normal bir yazma olarak kaydeder. Tek düğmeyle geri yazmak, aradaki
//    bütün değişiklikleri de görünmez biçimde silerdi.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_cms/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_cms/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, button, clip, confirmWithReason, h, loadStyles, num, stampIso, todayIso,
  toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { renderHtml } from '../../ui-kit/richtext.js';

const BASE = '/api/bld_cms';

/** Gerekçe alt sınırı — sunucu da denetliyor (00-genel.md §3), bu erken uyarı. */
const REASON_MIN = 10;

/** Panel uçlarında gerekçe üst sınırı (00-genel.md §3). */
const REASON_MAX = 500;

/** Yayın süzgecinin seçenekleri — sözleşmedeki üç değer. */
const PUBLISHED_OPTIONS = [
  { value: 'all', label: 'Hepsi' },
  { value: 'true', label: 'Yayında' },
  { value: 'false', label: 'Taslak' },
];

/** Hizmetin dizi alanları: etiket ve ipucu birlikte durur. */
const SERVICE_LISTS = [
  { key: 'audience', label: 'Kimler için', hint: 'Bu hizmeti kimlerin aldığı.' },
  { key: 'how_it_works', label: 'Nasıl işler', hint: 'Sırayla adımlar.' },
  { key: 'benefits', label: 'Ne kazandırır', hint: 'Müşterinin eline geçen.' },
  { key: 'quote_needs', label: 'Teklif için gerekenler',
    hint: 'Teklif verebilmek için sorulan bilgiler.' },
];

/** Yerel geçmiş satırlarının okunur karşılıkları. */
const TARGET_LABELS = {
  site_content: 'Kurumsal metin',
  site_service: 'Hizmet',
  site_post: 'Yazı',
};

const ACTION_LABELS = {
  'cms.content.update': 'yazıldı',
  'cms.service.create': 'açıldı',
  'cms.service.update': 'güncellendi',
  'cms.service.delete': 'silindi',
  'cms.post.create': 'açıldı',
  'cms.post.update': 'güncellendi',
  'cms.post.delete': 'silindi',
};

const SLUG_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

let api = null;
let toast = null;

const EMPTY_STATE = {
  tab: 'content',
  link: { connected: true, error: '' },
  screen: null,
  content: [],
  services: [],
  posts: [],
  postsMeta: { page: 1, per_page: 25, total: 0, last_page: 1, categories: [] },
  revisions: [],
  revalidate: null,        // son yazmanın tazeleme sonucu
  historyKey: '',          // "bu kaydın geçmişi" ile gelindiyse süzülen slug/anahtar
  pending: null,           // geçmişten düzenleyiciye getirilen değer
  busy: false,
};

let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];        // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

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
    error.code = typeof raw === 'string' ? (result.code || '') : (raw.code || '');
    throw error;
  }
  return result;
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * Geçit ya da BLD düştüğünde okuma uçları `{ok:true, connected:false, error}`
 * döndürür. Bu bir HATA DEĞİL, bir DURUMDUR: içerik yok değil, ŞU AN
 * OKUNAMIYOR. Sessizce boş liste çizmek yanlış olurdu — yönetici "hiç yazı
 * yok" ile "sunucuya ulaşılamıyor"u ayırt edemez ve var olan yazıları yeniden
 * yazmaya kalkardı.
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = { connected: false,
                   error: payload.error || 'BLD sunucusuna ulaşılamıyor.' };
    return false;
  }
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

/** Ekran sözleşmesi her okuma yanıtıyla iner; en sonuncusu geçerlidir. */
function takeScreen(payload) {
  if (payload && payload.screen) state.screen = payload.screen;
}

function limits() {
  return (state.screen && state.screen.limits) || {};
}

function statusText() {
  if (!state.link.connected) return `BLD'ye ulaşılamıyor — ${state.link.error}`;
  const parts = [];
  if (state.content.length) {
    parts.push(`${state.content.filter((row) => row.filled).length} / `
      + `${state.content.length} içerik anahtarı dolu`);
  }
  if (state.services.length) parts.push(`${num(state.services.length)} hizmet`);
  if (state.postsMeta.total) parts.push(`${num(state.postsMeta.total)} yazı`);
  return parts.join(' · ') || 'Hazır.';
}

function setStatus() {
  nodes.status?.set(statusText(), !state.link.connected);
}

/**
 * Kaydetme sonrası şerit — ekranın tek büyük vaadi burada duruyor.
 *
 * Üç hâl birbirinden AYRI yazılır. "İstendi ve olmadı" ile "hiç istenmedi"
 * aynı cümleyle anlatılsaydı, yönetici sitenin neden eski göründüğünü bulamaz
 * ve aynı kaydı ikinci kez yazardı.
 */
function paintBanner() {
  const box = nodes.banner;
  box.replaceChildren();
  if (!state.revalidate) return;

  const { status, note, at } = state.revalidate;
  if (status === 'ok') {
    box.append(alertBox(`${note} (${ago(at)})`, 'good'));
    return;
  }
  // `failed` KIRMIZI, `skipped` ve `unknown` SARI: ilkinde sunucu "olmadı"
  // dedi, ötekilerde ya hiç istenmedi ya da sonuç bilinmiyor. Üçünü de aynı
  // renge boyamak, gerçekten kırılan durumu sıradanlaştırırdı.
  const wrap = alertBox('', status === 'failed' ? 'bad' : 'warn');
  const line = h('div', 'bsi-bannerrow');
  line.append(
    h('span', undefined, note),
    button(status === 'failed' ? 'Yeniden dene' : 'Siteyi şimdi tazele',
           { variant: 'primary', onClick: () => askRevalidate() }),
  );
  wrap.append(line);
  box.append(wrap);
}

/** Yazma yanıtındaki tazeleme bloğunu şeride taşır ve uyarıları duyurur. */
function announce(result, message) {
  if (result && result.revalidate) {
    state.revalidate = { ...result.revalidate, at: new Date().toISOString() };
    paintBanner();
  }
  for (const warning of (result && result.warnings) || []) {
    const text = warning.note || warning.message
      || (warning.code === 'revalidate_failed'
        ? 'Site yeniden çizdirilemedi.' : warning.code);
    if (text) toast(text, 'warn');
  }
  if (result && result.sanitized_note) toast(result.sanitized_note, 'warn');
  if (result && result.dry_run) {
    // Panel `dryRun` GÖNDERMİYOR; bu dal, bir kurulumun geçit ayarından kuru
    // provayı geri açması içindir. Ekran o zaman "yapıldı" DEMEMELİ.
    toast('KURU PROVA: sunucuda hiçbir şey değişmedi.', 'warn');
    return false;
  }
  if (message) toast(message, 'good');
  return true;
}

/** İşlem sürerken ikinci isteği engeller; hata metnini tek yerde gösterir. */
async function withBusy(label, work) {
  if (state.busy) return;
  state.busy = true;
  nodes.status?.set(label);
  try {
    await work();
  } catch (failure) {
    const extra = failure.code === 'control_endpoint_missing'
      ? ' Bu uç sunucuya henüz dağıtılmamış olabilir.' : '';
    toast((failure.message || 'İşlem başarısız.') + extra, 'bad');
  } finally {
    state.busy = false;
    setStatus();
  }
}

/**
 * Gerekçe kutusu — her yazma gövdesinde zorunlu (00-genel.md §3).
 *
 * ÇEKMECEDE DURUR, AYRI BİR PENCEREDE DEĞİL: yönetici metni yazarken neden
 * yazdığını da orada söyler. Yıkıcı işlem (gerçek silme) bunun yerine
 * `confirmWithReason` kullanır — orada gerekçe, geri alınamaz bir işlemin
 * önündeki son duraktır ve ekranın geri kalanını kapatması gerekir.
 */
function reasonBox() {
  const wrap = h('label', 'kit-field kit-field-wide bsi-reason');
  wrap.append(h('span', 'kit-field-label', 'Gerekçe'));
  const input = h('input', 'kit-input');
  input.type = 'text';
  input.maxLength = REASON_MAX;
  input.placeholder = `Neden değişti? (en az ${REASON_MIN} karakter)`;
  wrap.append(input, h('span', 'kit-field-hint',
    `Denetim kaydına bu metin yazılır; sunucu da en az ${REASON_MIN} karakter `
    + 'istiyor ve arayüzde gizlemek yetkilendirme değildir.'));
  return {
    node: wrap,
    /** Geçerliyse gerekçeyi, değilse boş metin döndürür (ve uyarır). */
    read() {
      const value = input.value.trim();
      if (value.length < REASON_MIN) {
        input.classList.add('bad');
        input.focus();
        toast(`Gerekçe en az ${REASON_MIN} karakter olmalı.`, 'warn');
        return '';
      }
      input.classList.remove('bad');
      return value;
    },
  };
}

/** Tazeleme onay kutusu — varsayılanı sunucu sözleşmesi söyler. */
function revalidateBox() {
  const wrap = h('label', 'kit-field kit-field-wide bsi-check');
  const input = h('input', 'kit-check');
  input.type = 'checkbox';
  input.checked = (state.screen && state.screen.revalidate_default) !== false;
  const line = h('span', 'bsi-checkline');
  line.append(input, h('span', undefined, 'Kaydettikten sonra siteyi tazele'));
  wrap.append(line, h('span', 'kit-field-hint',
    'Kapatırsanız değişiklik yayında görünmez; birkaç kaydı art arda yapıp '
    + 'sonunda bir kez tazelemek için kullanın.'));
  return { node: wrap, read: () => input.checked };
}

/** Beyaz listeden geçirilmiş önizleme — `innerHTML` ASLA (kit kuralı 11). */
function previewCard(getHtml) {
  const box = h('div', 'bsi-preview');
  const repaint = () => box.replaceChildren(renderHtml(getHtml() || ''));
  const tags = ((state.screen && state.screen.editor
                 && state.screen.editor.allowed_tags) || []).join(' ');
  repaint();
  return {
    node: card('Önizleme', box,
      tags
        ? 'iframe kullanılmaz — etiketler beyaz listeden geçirilerek çizilir. '
          + `İzin verilenler: ${tags}`
        : 'iframe kullanılmaz — etiketler beyaz listeden geçirilerek çizilir.'),
    repaint,
  };
}

/**
 * Satır içi görsel yükleyici — YALNIZ uç varsa verilir.
 *
 * `richText` bu geri çağrıyı almadığında görsel düğmesini HİÇ ÇİZMİYOR; kitin
 * kuralı bir düğmenin ya çalışması ya da hiç çizilmemesidir. Bugün sözleşmede
 * yükleme ucu yok, o yüzden burası `undefined` dönüyor ve düzenleyicinin
 * altına ne yapılacağı yazılıyor (`imageNotice`).
 */
function imageUploader(getReason) {
  const support = (state.screen && state.screen.image_upload) || {};
  if (!support.available) return undefined;
  return async (file) => {
    const reason = getReason();
    if (!reason) throw new Error('Görsel yüklemek için önce gerekçe yazın.');
    const result = await call(`${BASE}/images`, {
      method: 'POST',
      body: { content: file.content, filename: file.filename, reason },
    });
    return result.url || '';
  };
}

function imageNotice() {
  const support = (state.screen && state.screen.image_upload) || {};
  if (support.available) return null;
  return hintBox(support.reason || 'Satır içi görsel yükleme ucu henüz yok.');
}

function imageRules() {
  return ((state.screen && state.screen.image_upload) || {}).rules || {};
}

/**
 * Kaydın yerel geçmişine götüren bağlantı — her çekmecenin dibinde durur.
 *
 * Çekmece KAPATILIR: açık kalsaydı geçmiş listesinin üstünde durur ve
 * kullanıcı bir şey olmadı sanırdı.
 */
function historyLink(targetType, targetKey, close) {
  return button('Bu kaydın geçmişi', {
    variant: 'ghost',
    title: 'Yerel düzenleme geçmişini açar (BLD düşse bile okunur)',
    onClick: () => {
      state.historyKey = targetKey;
      nodes.historyFilters.set('target_type', targetType);
      close?.();
      // `select` sekme değişimini bildirir ve `showHistory` kendiliğinden
      // çalışır; ikinci kez çağırmak aynı isteği iki kez atardı.
      nodes.tabs.select('history');
    },
  });
}

// ================================================================= okumalar

async function refreshContent({ silent = false } = {}) {
  if (!silent) nodes.body.replaceChildren(skeletonRows(7, 5));
  const payload = await call(`${BASE}/content`);
  takeScreen(payload);
  linkOk(payload);
  state.content = payload.items || [];
  setStatus();
}

async function refreshServices({ silent = false } = {}) {
  if (!silent) nodes.body.replaceChildren(skeletonRows(6, 6));
  const published = nodes.serviceFilters.values().published || 'all';
  const payload = await call(
    `${BASE}/services?published=${encodeURIComponent(published)}`);
  takeScreen(payload);
  linkOk(payload);
  state.services = payload.items || [];
  setStatus();
}

function postQuery() {
  const values = nodes.postFilters.values();
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.category) params.set('category', values.category);
  params.set('published', values.published || 'all');
  params.set('page', String(state.postsMeta.page || 1));
  params.set('per_page', String(state.postsMeta.per_page || 25));
  return params.toString();
}

async function refreshPosts({ silent = false } = {}) {
  if (!silent) nodes.body.replaceChildren(skeletonRows(8, 6));
  const payload = await call(`${BASE}/posts?${postQuery()}`);
  takeScreen(payload);
  linkOk(payload);
  state.posts = payload.items || [];
  state.postsMeta = { ...state.postsMeta, ...(payload.meta || {}) };
  // Kategori açılır kutusu SUNUCUDAN dolar: kategori ayrı bir tablo değil,
  // serbest bir metin alanı ve liste mevcut yazılardan damıtılıyor. Panel
  // kendi listesini tutsaydı yönetici her seferinde yeni bir kategori
  // uydurur ve site altı ayda on kategoriye çıkardı.
  nodes.postFilters.options('category', [
    { value: '', label: 'Tüm kategoriler' },
    ...(state.postsMeta.categories || []).map((item) => ({ value: item, label: item })),
  ]);
  setStatus();
}

async function refreshRevisions({ silent = false } = {}) {
  if (!silent) nodes.body.replaceChildren(skeletonRows(8, 6));
  const values = nodes.historyFilters.values();
  const params = new URLSearchParams();
  if (values.target_type) params.set('target_type', values.target_type);
  if (state.historyKey) params.set('target_key', state.historyKey);
  const payload = await call(`${BASE}/revisions?${params.toString()}`);
  state.revisions = payload.items || [];
}

// ============================================================ içerik sekmesi

function showContent() {
  withBusy('İçerik okunuyor…', async () => {
    await refreshContent();
    paintContent();
    // Geçmişten getirilen değer varsa çekmece kendiliğinden açılır: kullanıcı
    // "getir" dedi, ikinci bir tıklama istemek o niyeti unutmak olurdu.
    if (state.pending) {
      const { key, value } = state.pending;
      state.pending = null;
      openContent(key, value);
    }
  });
}

function paintContent() {
  const pane = h('div', 'bsi-pane');

  if (!state.link.connected) {
    pane.append(alertBox(`BLD'ye ulaşılamıyor: ${state.link.error} `
      + 'Aşağıdaki liste boş; bu "içerik yok" demek DEĞİLDİR.', 'bad'));
  }

  const table = dataTable({
    columns: [
      { key: 'label', label: 'Bölüm', width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const box = h('div', 'bsi-stack');
          box.append(h('b', undefined, row.label), h('small', undefined, row.hint));
          return box;
        } },
      { key: 'shape', label: 'Şekil', width: '110px',
        cell: (row) => badge(row.shape === 'array' ? 'Liste' : 'Nesne', 'dim') },
      { key: 'count', label: 'İçerik', width: '170px',
        cell: (row) => {
          if (!row.filled) return badge('Boş', 'warn');
          const label = row.shape === 'array'
            ? `${num(row.count)} satır` : `${num(row.count)} alan`;
          return h('span', undefined,
            `${label} · ${num(Math.round(row.bytes / 1024))} KB`);
        } },
      { key: 'updated_at', label: 'Son güncelleme', width: '180px',
        cell: (row) => h('span', row.updated_at ? undefined : 'bsi-dim',
          row.updated_at ? stampIso(row.updated_at) : 'hiç yazılmadı') },
    ],
    rows: state.content,
    rowKey: (row) => row.key,
    empty: emptyState({
      title: 'İçerik okunamadı',
      text: 'Sunucuya ulaşılamıyor olabilir; liste boş olduğu için değil.',
      actions: [button('Yeniden dene', { onClick: () => showContent() })],
    }),
    onRow: (row) => openContent(row.key),
  });

  pane.append(
    hintBox('Bu bölümdeki metinler sitenin sabit sayfalarını besler. Satıra '
      + 'tıklayarak açın. Değer TAM DEĞERDİR: formdan çıkardığınız bir alan '
      + 'sunucudan da silinir, birleştirme yapılmaz.'),
    table.node,
  );
  nodes.body.replaceChildren(pane);
}

/**
 * Şekilsiz JSON için değer kutusu.
 *
 * Sunucu içeriği doğrulamıyor; hangi alanların olduğunu ancak VERİ söyler.
 * Bu yüzden kutu tipini değerin KENDİSİ belirler: metin, sayı, evet/hayır ya
 * da (iç içe yapı için) JSON kutusu. Uydurma bir şema dayatmak, sitenin
 * okuduğu bir alanı ekranın yazamaz hâle getirirdi.
 */
function valueControl(initial) {
  if (typeof initial === 'boolean') {
    const input = h('input', 'kit-check');
    input.type = 'checkbox';
    input.checked = initial;
    return { node: input, read: () => input.checked };
  }
  if (typeof initial === 'number') {
    const input = h('input', 'kit-input');
    input.type = 'number';
    input.value = String(initial);
    return { node: input, read: () => Number(input.value) };
  }
  if (initial && typeof initial === 'object') {
    const input = h('textarea', 'kit-textarea bsi-json');
    input.value = JSON.stringify(initial, null, 2);
    return {
      node: input,
      read: () => {
        try {
          return JSON.parse(input.value);
        } catch {
          // Bozuk JSON SESSİZCE atılmaz: `null` yazmak, iç içe bir bloğu tek
          // yazım hatasıyla silmek olurdu.
          throw new Error('İç içe alanlardan biri geçerli JSON değil.');
        }
      },
    };
  }
  const text = initial === null || initial === undefined ? '' : String(initial);
  const long = text.length > 80 || text.includes('\n');
  const input = h(long ? 'textarea' : 'input', long ? 'kit-textarea' : 'kit-input');
  if (!long) input.type = 'text';
  input.value = text;
  return { node: input, read: () => input.value };
}

/**
 * Nesne anahtarı için alan listesi.
 *
 * Alan ADI da düzenlenebilir: site yeni bir alan okumaya başladığında
 * yöneticinin onu ekleyebilmesi gerekir ve sunucu bir ad listesi tutmuyor.
 */
function objectEditor(value, seed) {
  const rows = [];
  const list = h('div', 'bsi-rows');
  const source = { ...(value && typeof value === 'object' && !Array.isArray(value)
    ? value : {}) };
  // TOHUM YALNIZ BOŞ ANAHTARDA: dolu bir nesneye örnekteki alanları eklemek,
  // yöneticinin bilerek sildiği bir alanı geri getirmek olurdu.
  if (!Object.keys(source).length) for (const key of seed || []) source[key] = '';

  const addRow = (key, raw) => {
    const row = h('div', 'bsi-row');
    const name = h('input', 'kit-input bsi-key');
    name.type = 'text';
    name.value = key;
    name.placeholder = 'alan adı';
    const control = valueControl(raw);
    const entry = { name, control };
    row.append(name, control.node, button('Çıkar', {
      variant: 'ghost',
      title: 'Bu alanı değerden çıkarır; kaydedince sunucudan da silinir',
      onClick: () => {
        row.remove();
        const index = rows.indexOf(entry);
        if (index >= 0) rows.splice(index, 1);
      },
    }));
    rows.push(entry);
    list.append(row);
  };

  for (const [key, raw] of Object.entries(source)) addRow(key, raw);

  const actions = h('div', 'bsi-rowactions');
  actions.append(button('Alan ekle', { onClick: () => addRow('', '') }));

  const wrap = h('div');
  wrap.append(list, actions);
  return {
    node: wrap,
    read() {
      const out = {};
      for (const entry of rows) {
        const key = entry.name.value.trim();
        if (!key) continue;      // adsız alan yazılmaz
        out[key] = entry.control.read();
      }
      return out;
    },
  };
}

/** Dizi anahtarı için satır listesi (sıra korunur, satır taşınabilir). */
function arrayEditor(value, seed) {
  const list = h('div', 'bsi-rows');
  let rows = [];
  const source = Array.isArray(value) ? value : [];

  const shape = () => {
    const first = source[0];
    if (first && typeof first === 'object' && !Array.isArray(first)) {
      return Object.fromEntries(Object.keys(first).map((key) => [key, '']));
    }
    if (seed && seed.length) return Object.fromEntries(seed.map((key) => [key, '']));
    return '';
  };

  const paint = () => {
    list.replaceChildren();
    rows.forEach((entry, index) => {
      const row = h('div', 'bsi-listrow');
      const head = h('div', 'bsi-listhead');
      head.append(h('b', undefined, `${index + 1}.`), h('span', 'kit-spacer'));
      head.append(
        button('▲', { variant: 'ghost', title: 'Yukarı taşı',
                      onClick: () => rebuild(index, index - 1) }),
        button('▼', { variant: 'ghost', title: 'Aşağı taşı',
                      onClick: () => rebuild(index, index + 1) }),
        button('Çıkar', { variant: 'ghost', title: 'Bu satırı listeden çıkarır',
                          onClick: () => rebuild(index, -1) }),
      );
      row.append(head, entry.control.node);
      list.append(row);
    });
  };

  /**
   * Satırları DEĞERLERİYLE yeniden kurar.
   *
   * Düğümleri taşımak daha ucuz olurdu ama açık bir metin kutusundaki imleci
   * ve seçimi kaybettirir; okunan değerle yeniden kurmak, kullanıcının yazdığı
   * her şeyi korur.
   */
  const rebuild = (from, to) => {
    let values;
    try {
      values = rows.map((entry) => entry.control.read());
    } catch (failure) {
      toast(failure.message, 'warn');
      return;
    }
    if (to === -1) values.splice(from, 1);
    else if (to >= 0 && to < values.length) {
      [values[from], values[to]] = [values[to], values[from]];
    } else if (from === -1) values.push(shape());
    rows = values.map((raw) => ({ control: valueControl(raw) }));
    paint();
  };

  rows = source.map((item) => ({ control: valueControl(item) }));
  if (!rows.length) rows = [{ control: valueControl(shape()) }];
  paint();

  const actions = h('div', 'bsi-rowactions');
  actions.append(button('Satır ekle', { onClick: () => rebuild(-1, rows.length) }));

  const wrap = h('div');
  wrap.append(list, actions);
  return {
    node: wrap,
    read: () => rows.map((entry) => entry.control.read())
      .filter((item) => !(typeof item === 'string' && !item.trim())),
  };
}

/**
 * İçerik anahtarı çekmecesi.
 *
 * @param {string} key      — yedi sabit anahtardan biri
 * @param {*}      [preset] — geçmişten getirilen değer; verilirse form bununla
 *                            doldurulur ve ekran bunun eski bir sürüm olduğunu
 *                            SÖYLER (sessizce yüklemek, yöneticiye şu anki
 *                            değeri gösterdiğini sandırırdı)
 */
function openContent(key, preset) {
  const row = state.content.find((item) => item.key === key);
  if (!row) return;

  let draft = preset === undefined ? row.value : preset;
  let mode = 'form';
  let editor = null;

  const editorBox = h('div', 'bsi-editorbox');
  const rawBox = h('textarea', 'kit-textarea bsi-json bsi-raw');
  rawBox.hidden = true;
  const counter = h('div', 'bsi-counter');
  const problem = h('div', 'kit-dialog-error');

  const sizeOf = (value) => {
    try {
      return new TextEncoder().encode(JSON.stringify(value)).length;
    } catch {
      return -1;
    }
  };

  const paintCounter = (value) => {
    const bytes = sizeOf(value);
    const max = limits().content_bytes || 0;
    counter.textContent = bytes < 0
      ? 'Değer JSON\'a çevrilemiyor.'
      : `${num(Math.round(bytes / 1024))} KB / ${num(Math.round(max / 1024))} KB`;
    counter.classList.toggle('bad', bytes < 0 || (max > 0 && bytes > max));
  };

  const buildForm = () => {
    editor = row.shape === 'array'
      ? arrayEditor(draft, row.seed) : objectEditor(draft, row.seed);
    editorBox.replaceChildren(editor.node);
  };

  const readCurrent = () => {
    if (mode === 'raw') {
      try {
        return JSON.parse(rawBox.value || 'null');
      } catch {
        throw new Error('Ham JSON geçerli değil; kaydetmeden önce düzeltin.');
      }
    }
    return editor.read();
  };

  const toggleMode = (next) => {
    try {
      draft = readCurrent();
    } catch (failure) {
      problem.textContent = failure.message;
      return;
    }
    problem.textContent = '';
    mode = next;
    if (mode === 'raw') {
      rawBox.value = JSON.stringify(draft, null, 2);
      rawBox.hidden = false;
      editorBox.hidden = true;
    } else {
      buildForm();
      rawBox.hidden = true;
      editorBox.hidden = false;
    }
    paintCounter(draft);
  };

  buildForm();
  paintCounter(draft);

  const modes = tabBar([
    { key: 'form', label: 'Alanlar' },
    { key: 'raw', label: 'Ham JSON' },
  ], 'form', (next) => toggleMode(next));

  const reason = reasonBox();
  const fresh = revalidateBox();

  const box = drawer(nodes.root, {
    title: row.label,
    subtitle: row.updated_at
      ? `Son güncelleme: ${stampIso(row.updated_at)}`
      : 'Bu anahtar hiç yazılmadı.',
  });
  closers.push(() => box.close());

  const save = button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      let value;
      try {
        value = readCurrent();
      } catch (failure) {
        problem.textContent = failure.message;
        return;
      }
      problem.textContent = '';
      const text = reason.read();
      if (!text) return;
      await withBusy('İçerik yazılıyor…', async () => {
        const result = await call(`${BASE}/content/${encodeURIComponent(row.key)}`, {
          method: 'PUT',
          body: { value, reason: text, revalidate: fresh.read() },
        });
        if (result.changed === false) {
          toast(result.note || 'Değer zaten bu; sunucuya istek gönderilmedi.', 'warn');
          return;
        }
        announce(result, `“${row.label}” kaydedildi.`);
        box.close();
        await refreshContent({ silent: true });
        paintContent();
      });
    },
  });

  const actions = h('div', 'bsi-actions');
  actions.append(save, historyLink('site_content', row.key, () => box.close()));

  if (preset !== undefined) {
    box.body.append(alertBox('Formda ESKİ bir sürüm duruyor. Kaydetmediğiniz '
      + 'sürece sunucuda hiçbir şey değişmez; kaydederseniz aradaki bütün '
      + 'değişiklikler bu değerin altında kalır.', 'warn'));
  }
  box.body.append(
    hintBox(`${row.hint} Değer TAM DEĞERDİR: formdan çıkardığınız alan `
      + 'sunucudan da silinir, birleştirme yapılmaz.'),
    modes.node, editorBox, rawBox, counter, problem,
    reason.node, fresh.node, actions,
  );
}

// =========================================================== hizmet sekmesi

function showServices() {
  withBusy('Hizmetler okunuyor…', async () => {
    await refreshServices();
    paintServices();
  });
}

function paintServices() {
  const pane = h('div', 'bsi-pane');
  if (!state.link.connected) {
    pane.append(alertBox(`BLD'ye ulaşılamıyor: ${state.link.error}`, 'bad'));
  }

  const rows = applyFilters(state.services, nodes.serviceFilters.values(), {
    q: { kind: 'search', fields: ['title', 'slug', 'summary', 'body_text'] },
  });

  const table = dataTable({
    columns: [
      { key: 'sort_order', label: 'Sıra', width: '70px', align: 'num', sortable: true },
      { key: 'title', label: 'Hizmet', width: 'minmax(0, 1.4fr)', sortable: true,
        cell: (row) => {
          const box = h('div', 'bsi-stack');
          box.append(h('b', undefined, row.title));
          const small = h('small');
          clip(small, row.summary || '—', 70);
          box.append(small);
          return box;
        } },
      { key: 'slug', label: 'Adres', width: 'minmax(0, 1fr)',
        cell: (row) => h('code', 'bsi-slug', `/${row.slug}`) },
      { key: 'is_published', label: 'Durum', width: '110px',
        cell: (row) => badge(row.is_published ? 'Yayında' : 'Taslak',
                             row.is_published ? 'good' : 'warn') },
      { key: 'updated_at', label: 'Güncellendi', width: '160px', sortable: true,
        cell: (row) => h('span', undefined, stampIso(row.updated_at)) },
    ],
    rows,
    empty: emptyState({
      title: 'Bu süzgece uyan hizmet yok',
      text: 'Arama kutusunu temizleyip yeniden bakın ya da yeni bir hizmet açın.',
      actions: [button('Yeni hizmet', { variant: 'primary',
                                        onClick: () => openService(null) })],
    }),
    onRow: (row) => openService(row.id),
  });

  pane.append(nodes.serviceFilters.node, table.node);
  nodes.body.replaceChildren(pane);
}

/** Düz metin listesi editörü — hizmetin dört dizi alanı bunu kullanır. */
function listEditor(spec, items) {
  const max = limits().list_items || 20;
  const maxChars = limits().list_item_chars || 300;
  const list = h('div', 'bsi-rows');
  let values = [...(items || [])];

  const paint = () => {
    list.replaceChildren();
    values.forEach((value, index) => {
      const row = h('div', 'bsi-row');
      const input = h('input', 'kit-input');
      input.type = 'text';
      input.maxLength = maxChars;
      input.value = value;
      input.addEventListener('input', () => { values[index] = input.value; });
      row.append(input,
        button('▲', { variant: 'ghost', title: 'Yukarı taşı',
                      onClick: () => swap(index, -1) }),
        button('▼', { variant: 'ghost', title: 'Aşağı taşı',
                      onClick: () => swap(index, 1) }),
        button('Çıkar', { variant: 'ghost',
                          onClick: () => { values.splice(index, 1); paint(); } }));
      list.append(row);
    });
  };

  const swap = (index, step) => {
    const target = index + step;
    if (target < 0 || target >= values.length) return;
    [values[index], values[target]] = [values[target], values[index]];
    paint();
  };

  paint();

  const actions = h('div', 'bsi-rowactions');
  actions.append(button('Satır ekle', {
    onClick: () => {
      if (values.length >= max) {
        // Sunucu da 422 verirdi; burada söylemek, yazdığı satırı gönderdikten
        // sonra kaybeden bir kullanıcıdan iyidir.
        toast(`“${spec.label}” en çok ${max} satır alabilir.`, 'warn');
        return;
      }
      values.push('');
      paint();
    },
  }));

  const wrap = h('div');
  wrap.append(list, actions);
  return {
    node: card(spec.label, wrap,
      `${spec.hint} En çok ${max} satır, her biri ${maxChars} karakter.`),
    read: () => values.map((item) => String(item).trim()).filter(Boolean),
  };
}

async function confirmSlugChange(before, after) {
  return confirmWithReason(nodes.root, {
    title: 'Adres değişiyor',
    description: `“/${before}” → “/${after}”. Eski adrese verilen bağlantılar `
      + 'kırılacak ve arama motorlarındaki sıra sıfırlanacak. Bunu yazma '
      + 'bittikten sonra öğrenen biri, kırılan bağlantıları geri getiremez.',
    confirmLabel: 'Adresi değiştir',
    minLength: REASON_MIN,
    placeholder: `Adres neden değişiyor? (en az ${REASON_MIN} karakter)`,
  });
}

function openService(serviceId) {
  const creating = serviceId === null;
  const row = creating
    ? { id: 0, slug: '', title: '', summary: '', intro: '', icon: '', body_html: '',
        audience: [], how_it_works: [], benefits: [], quote_needs: [],
        menu_planning: '', sort_order: 0, is_published: false }
    : state.services.find((item) => item.id === serviceId);
  if (!row) return;

  const forms = [];
  const box = drawer(nodes.root, {
    title: creating ? 'Yeni hizmet' : row.title,
    subtitle: creating
      ? 'Sayfa TASLAK açılır; yayına almak ayrı bir seçim.'
      : `Adres: /${row.slug}`,
    onClose: () => forms.forEach((form) => form.destroy()),
  });
  closers.push(() => box.close());

  const reason = reasonBox();
  const fresh = revalidateBox();
  // Önizleme formdan SONRA kurulur ama form ona ihtiyaç duyar; bağ tembel
  // kurulur, yoksa `form` henüz tanımlı olmadan çağrılırdı.
  let preview = null;

  const form = formGrid({
    fields: [
      { key: 'title', label: 'Başlık', type: 'text', required: true, wide: true,
        maxLength: (limits().service || {}).title || 160 },
      { key: 'slug', label: 'Adres parçası', type: 'text', required: true,
        maxLength: 96,
        hint: 'Küçük harf, rakam ve tek tire. Değiştirmek eski bağlantıları kırar.',
        validate: (value) => (SLUG_RE.test(String(value || ''))
          ? null : 'Yalnız küçük harf, rakam ve tek tire (ör. kurumsal-catering).') },
      { key: 'icon', label: 'İkon adı', type: 'text', maxLength: 48,
        hint: 'Lucide ikon adı (ör. Building2). Bilinmeyen ad sitede sessizce '
          + 'varsayılana düşer, boş kutu görünmez — liste sitede yaşıyor ve '
          + 'sunucu onu doğrulamıyor.' },
      { key: 'sort_order', label: 'Sıra', type: 'number', min: 0, max: 9999,
        hint: 'Sitede küçükten büyüğe dizilir.' },
      { key: 'is_published', label: 'Yayında', type: 'checkbox',
        hint: 'Kapalıyken sayfa sitede görünmez ama kaydı durur.' },
      { key: 'summary', label: 'Kart metni', type: 'textarea', wide: true,
        maxLength: (limits().service || {}).summary || 400,
        hint: 'Hizmet kartında görünen kısa özet.' },
      { key: 'intro', label: 'Giriş', type: 'textarea', wide: true,
        hint: 'Sayfanın başındaki paragraf.' },
      { key: 'menu_planning', label: 'Menü planlama', type: 'textarea', wide: true,
        hint: 'Menünün nasıl belirlendiği.' },
      { key: 'body_html', label: 'Sayfa gövdesi', type: 'richtext', wide: true,
        // Görsel düğmesi YALNIZ yükleme ucu varken çizilir (bkz. imageUploader).
        onInsertImage: imageUploader(() => reason.read()),
        imageRules: imageRules(),
        hint: 'Biçim araç çubuğundan uygulanır; HTML yazmak gerekmez. '
          + 'Kaydettiğinizde sunucu izin listesi dışındaki etiketleri temizler '
          + 've ekran temizlenmiş hâli gösterir.' },
    ],
    value: { ...row },
    onChange: () => preview?.repaint(),
  });
  forms.push(form);
  preview = previewCard(() => form.draft().body_html);

  const lists = SERVICE_LISTS.map((spec) => listEditor(spec, row[spec.key]));

  const save = button(creating ? 'Hizmeti aç' : 'Kaydet', {
    variant: 'primary',
    onClick: async () => {
      form.showErrors();
      if (!form.valid()) {
        toast('Kırmızı alanları düzeltin.', 'warn');
        return;
      }
      const text = reason.read();
      if (!text) return;

      const listValues = Object.fromEntries(
        SERVICE_LISTS.map((spec, index) => [spec.key, lists[index].read()]));

      let body;
      if (creating) {
        body = { ...form.draft(), ...listValues };
      } else {
        // KISMİ GÜNCELLEME: yalnız değişen alanlar gönderilir. Tam gövde
        // göndermek, dokunulmamış bir alanı aradan başkasının yazdığı değerin
        // üstüne yazmak olurdu.
        body = { ...form.patch() };
        for (const spec of SERVICE_LISTS) {
          if (JSON.stringify(listValues[spec.key]) !== JSON.stringify(row[spec.key])) {
            body[spec.key] = listValues[spec.key];
          }
        }
        if (!Object.keys(body).length) {
          toast('Değişen alan yok.', 'warn');
          return;
        }
        if (body.slug && body.slug !== row.slug
            && !await confirmSlugChange(row.slug, body.slug)) return;
      }

      await withBusy(creating ? 'Hizmet açılıyor…' : 'Hizmet yazılıyor…', async () => {
        const path = creating ? `${BASE}/services` : `${BASE}/services/${row.id}`;
        const result = await call(path, {
          method: creating ? 'POST' : 'PATCH',
          body: { fields: body, reason: text, revalidate: fresh.read() },
        });
        announce(result, creating ? 'Hizmet açıldı.' : 'Hizmet kaydedildi.');
        box.close();
        await refreshServices({ silent: true });
        paintServices();
      });
    },
  });

  const actions = h('div', 'bsi-actions');
  actions.append(save);
  if (!creating) {
    actions.append(deleteButton('service', row, () => box.close()),
                   historyLink('site_service', row.slug, () => box.close()));
  }

  const notice = imageNotice();
  box.body.append(form.node);
  if (notice) box.body.append(notice);
  box.body.append(preview.node, ...lists.map((item) => item.node),
                  reason.node, fresh.node, actions);
}

/**
 * YIKICI silme düğmesi.
 *
 * Gerçekten silmek `bld_cms.delete` ister ve `confirmWithReason`dan geçer.
 * Gerekçe hem denetim kaydına yazılır hem de kullanıcıyı bir saniye durdurur —
 * geri alınamaz bir işlemde tek koruma budur (ADR 0012; PIN istenmez).
 * Yetkisi olmayan kullanıcıda sunucu 403 döner ve ekran nedenini yazar.
 *
 * Silme başarılıysa ÇEKMECE KAPANIR: açık kalsaydı artık var olmayan bir
 * kaydın formunu gösterir ve üstündeki "Kaydet" düğmesi silinmiş bir kimliğe
 * yazmaya çalışırdı.
 */
function deleteButton(kind, row, close) {
  const isService = kind === 'service';
  return button('Kalıcı olarak sil', {
    variant: 'danger',
    title: 'Kayıt geri gelmez. Sitede görünmesin istiyorsanız yayından çıkarın.',
    onClick: async () => {
      const reason = await confirmWithReason(nodes.root, {
        title: isService ? 'Hizmet siliniyor' : 'Yazı siliniyor',
        description: `“${row.title}” KALICI olarak silinecek ve geri gelmez. `
          + `“/${row.slug}” adresine verilen bütün bağlantılar kırılır. `
          + 'Yalnız sitede görünmesin istiyorsanız bunun yerine yayından çıkarın '
          + '(kayıt durur, sayfa görünmez).',
        confirmLabel: 'Kalıcı olarak sil',
        minLength: REASON_MIN,
        placeholder: `Neden siliniyor? (en az ${REASON_MIN} karakter)`,
      });
      if (!reason) return;
      await withBusy('Siliniyor…', async () => {
        const path = isService ? `${BASE}/services/${row.id}` : `${BASE}/posts/${row.id}`;
        const result = await call(path, { method: 'DELETE', body: { reason } });
        announce(result, `“${row.title}” silindi.`);
        close?.();
        if (isService) {
          await refreshServices({ silent: true });
          paintServices();
        } else {
          await refreshPosts({ silent: true });
          paintPosts();
        }
      });
    },
  });
}

// ============================================================== yazı sekmesi

function showPosts() {
  withBusy('Yazılar okunuyor…', async () => {
    await refreshPosts();
    paintPosts();
  });
}

function paintPosts() {
  const pane = h('div', 'bsi-pane');
  if (!state.link.connected) {
    pane.append(alertBox(`BLD'ye ulaşılamıyor: ${state.link.error}`, 'bad'));
  }

  const yayinda = state.posts.filter((row) => row.is_published).length;
  pane.append(kpiRow([
    { label: 'Toplam yazı', value: num(state.postsMeta.total || 0),
      title: 'Süzgeçlere uyan bütün yazılar (sunucudan)' },
    { label: 'Bu sayfada yayında',
      value: `${num(yayinda)} / ${num(state.posts.length)}` },
    { label: 'Kategori', value: num((state.postsMeta.categories || []).length),
      title: 'Mevcut yazılardan damıtıldı; ayrı bir kategori tablosu yok' },
  ]));

  const table = dataTable({
    columns: [
      { key: 'title', label: 'Yazı', width: 'minmax(0, 1.6fr)',
        cell: (row) => {
          const box = h('div', 'bsi-stack');
          box.append(h('b', undefined, row.title));
          const small = h('small');
          clip(small, row.description || `/${row.slug}`, 80);
          box.append(small);
          return box;
        } },
      { key: 'category', label: 'Kategori', width: '160px',
        cell: (row) => (row.category
          ? badge(row.category, 'dim') : h('span', 'bsi-dim', 'yok')) },
      { key: 'published_at', label: 'Yayın tarihi', width: '130px' },
      { key: 'reading', label: 'Okuma', width: '120px',
        cell: (row) => {
          // "Hesaplandı" ipucu, yöneticinin kendi yazdığı sanılan bir sayıyı
          // ayırt edebilmesi içindir.
          const box = h('span', row.reading_estimated ? 'bsi-dim' : undefined,
            `${num(row.reading_minutes_effective)} dk`);
          box.title = row.reading_estimated
            ? 'Gövdeden hesaplandı; elle girilmedi.' : 'Elle girildi.';
          return box;
        } },
      { key: 'is_published', label: 'Durum', width: '110px',
        cell: (row) => badge(row.is_published ? 'Yayında' : 'Taslak',
                             row.is_published ? 'good' : 'warn') },
    ],
    rows: state.posts,
    empty: emptyState({
      title: 'Bu süzgece uyan yazı yok',
      text: 'Arama ya da kategori süzgecini temizleyip yeniden bakın.',
      actions: [button('Yeni yazı', { variant: 'primary',
                                      onClick: () => openPost(null) })],
    }),
    onRow: (row) => openPost(row.id),
  });

  const paging = pager({
    total: state.postsMeta.total || 0,
    page: state.postsMeta.page || 1,
    size: state.postsMeta.per_page || 25,
    onChange: ({ page, size }) => {
      state.postsMeta = { ...state.postsMeta, page, per_page: size };
      showPosts();
    },
  });

  pane.append(nodes.postFilters.node, table.node, paging.node);
  nodes.body.replaceChildren(pane);
}

function openPost(postId) {
  const creating = postId === null;
  const row = creating
    ? { id: 0, slug: '', title: '', description: '', category: '', body_html: '',
        published_at: todayIso(), reading_minutes: null,
        reading_minutes_effective: 0, reading_estimated: true, is_published: false }
    : state.posts.find((item) => item.id === postId);
  if (!row) return;

  const forms = [];
  const box = drawer(nodes.root, {
    title: creating ? 'Yeni yazı' : row.title,
    subtitle: creating
      ? 'Bilgi merkezine yeni bir yazı. Gövde boş bırakılamaz.'
      : `Adres: /${row.slug} · ${row.reading_minutes_effective} dk `
        + `(${row.reading_estimated ? 'hesaplandı' : 'elle girildi'})`,
    onClose: () => forms.forEach((form) => form.destroy()),
  });
  closers.push(() => box.close());

  const reason = reasonBox();
  const fresh = revalidateBox();
  let preview = null;

  const form = formGrid({
    fields: [
      { key: 'title', label: 'Başlık', type: 'text', required: true, wide: true,
        maxLength: (limits().post || {}).title || 200 },
      { key: 'slug', label: 'Adres parçası', type: 'text', required: true,
        maxLength: 96,
        hint: 'Küçük harf, rakam ve tek tire. Değiştirmek eski bağlantıları kırar.',
        validate: (value) => (SLUG_RE.test(String(value || ''))
          ? null : 'Yalnız küçük harf, rakam ve tek tire.') },
      { key: 'category', label: 'Kategori', type: 'text',
        maxLength: (limits().post || {}).category || 64,
        hint: 'Serbest metin — ayrı bir kategori tablosu yok. Mevcut olanlar: '
          + ((state.postsMeta.categories || []).join(', ') || 'henüz yok') },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // kit kendi takvimini kullanıyor.
      { key: 'published_at', label: 'Yayın tarihi', type: 'date',
        hint: 'Bir TARİHTİR, an değil: yayın günü sizin kararınız ve geçmişe ya '
          + 'da ileriye tarihlenebilir.' },
      { key: 'reading_minutes', label: 'Okuma süresi (dk)', type: 'number',
        min: 1, max: 240,
        hint: 'BOŞ BIRAKIN, sunucu gövdeden hesaplasın. Sıfır yazmayın — o, '
          + '"bu yazı okunmuyor" anlamına gelen bir sayı olurdu.' },
      { key: 'is_published', label: 'Yayında', type: 'checkbox',
        hint: 'Kapalıyken yazı sitede görünmez ama kaydı durur.' },
      { key: 'description', label: 'Açıklama', type: 'textarea', wide: true,
        maxLength: (limits().post || {}).description || 400,
        hint: 'Liste kartında ve arama sonucunda görünen metin.' },
      { key: 'body_html', label: 'Gövde', type: 'richtext', wide: true, required: true,
        onInsertImage: imageUploader(() => reason.read()),
        imageRules: imageRules(),
        hint: 'Zorunlu ve boş olamaz: başlığı olan boş bir sayfa üretirdi.' },
    ],
    value: { ...row },
    onChange: () => preview?.repaint(),
  });
  forms.push(form);
  preview = previewCard(() => form.draft().body_html);

  const save = button(creating ? 'Yazıyı aç' : 'Kaydet', {
    variant: 'primary',
    onClick: async () => {
      form.showErrors();
      if (!form.valid()) {
        toast('Kırmızı alanları düzeltin.', 'warn');
        return;
      }
      const text = reason.read();
      if (!text) return;

      const body = creating ? { ...form.draft() } : { ...form.patch() };
      if (!creating && !Object.keys(body).length) {
        toast('Değişen alan yok.', 'warn');
        return;
      }
      if (!creating && body.slug && body.slug !== row.slug
          && !await confirmSlugChange(row.slug, body.slug)) return;

      await withBusy(creating ? 'Yazı açılıyor…' : 'Yazı yazılıyor…', async () => {
        const path = creating ? `${BASE}/posts` : `${BASE}/posts/${row.id}`;
        const result = await call(path, {
          method: creating ? 'POST' : 'PATCH',
          body: { fields: body, reason: text, revalidate: fresh.read() },
        });
        announce(result, creating ? 'Yazı açıldı.' : 'Yazı kaydedildi.');
        box.close();
        await refreshPosts({ silent: true });
        paintPosts();
      });
    },
  });

  const actions = h('div', 'bsi-actions');
  actions.append(save);
  if (!creating) {
    actions.append(deleteButton('post', row, () => box.close()),
                   historyLink('site_post', row.slug, () => box.close()));
  }

  const notice = imageNotice();
  box.body.append(form.node);
  if (notice) box.body.append(notice);
  box.body.append(preview.node, reason.node, fresh.node, actions);
}

// =========================================================== geçmiş sekmesi

function showHistory() {
  withBusy('Geçmiş okunuyor…', async () => {
    await refreshRevisions();
    paintHistory();
  });
}

function paintHistory() {
  const pane = h('div', 'bsi-pane');

  if (state.historyKey) {
    const wrap = alertBox('', 'info');
    const line = h('div', 'bsi-bannerrow');
    line.append(
      h('span', undefined, `Yalnız “${state.historyKey}” kaydının geçmişi `
        + 'gösteriliyor.'),
      button('Süzgeci kaldır', {
        onClick: () => { state.historyKey = ''; showHistory(); },
      }),
    );
    wrap.append(line);
    pane.append(wrap);
  }

  const table = dataTable({
    columns: [
      { key: 'created_at', label: 'Ne zaman', width: '170px',
        cell: (row) => h('span', undefined, stampIso(row.created_at)) },
      { key: 'target_type', label: 'Tür', width: '140px',
        cell: (row) => badge(TARGET_LABELS[row.target_type] || row.target_type, 'dim') },
      { key: 'title', label: 'Kayıt', width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const box = h('div', 'bsi-stack');
          box.append(h('b', undefined, row.title || row.target_key));
          box.append(h('small', undefined, row.target_key));
          return box;
        } },
      { key: 'action', label: 'Eylem', width: '120px',
        cell: (row) => h('span', undefined, ACTION_LABELS[row.action] || row.action) },
      { key: 'actor', label: 'Kim', width: '150px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const box = h('span');
          clip(box, row.reason || '—', 70);
          return box;
        } },
      { key: 'truncated', label: '', width: '150px',
        cell: (row) => (row.truncated
          // Kırpılmış sürümün gövdesi SAKLANMADI ve ekran bunu satırın
          // üstünde söyler; açılınca yarım metin gösteren bir satır, geri
          // getirilebilir sanılırdı.
          ? badge('gövde saklanmadı', 'warn') : h('span', 'bsi-dim', 'açmak için tıkla')) },
    ],
    rows: state.revisions,
    empty: emptyState({
      title: 'Henüz kayıt yok',
      text: 'Bu ekrandan yapılan her başarılı yazma buraya bir satır bırakır.',
    }),
    onRow: (row) => (row.truncated
      ? toast('Bu sürümün gövdesi saklanmadı (sınırı aşıyordu).', 'warn')
      : openRevision(row.id)),
  });

  pane.append(
    hintBox('Bu liste YEREL tutulur ve BLD düşse bile okunur. Bir YEDEK '
      + 'DEĞİLDİR: eski sürümü açıp düzenleyiciye getirebilir, sonra kendi '
      + 'gerekçenizle normal bir kayıt olarak yazabilirsiniz. Sunucuda '
      + 'karşılığı yoktur — orada içeriğin yalnız son hâli durur.'),
    nodes.historyFilters.node,
    table.node,
  );
  nodes.body.replaceChildren(pane);
}

function openRevision(revisionId) {
  withBusy('Sürüm okunuyor…', async () => {
    const payload = await call(`${BASE}/revisions/${revisionId}`);
    const row = payload.data || {};
    const box = drawer(nodes.root, {
      title: row.title || row.target_key,
      subtitle: `${stampIso(row.created_at)} · ${row.actor || 'bilinmiyor'}`,
    });
    closers.push(() => box.close());

    const dump = (label, value, hint) => {
      const pre = h('pre', 'bsi-dump');
      pre.textContent = value === null || value === undefined
        ? '(kayıt yok)' : JSON.stringify(value, null, 2);
      return card(label, pre, hint);
    };

    box.body.append(
      hintBox(`Gerekçe: ${row.reason || '—'}`
        + (row.audit_id
          ? ` · Sunucudaki denetim satırı: #${row.audit_id}` : '')),
      dump('Önceki hâl', row.before_json,
           'Yazmadan hemen önce sunucudan TAZE okunan değer.'),
      dump('Yazılan hâl', row.after_json,
           'Silme satırlarında bu bölüm boştur — silinen kaydın "sonrası" yoktur.'),
    );

    // Düzenleyiciye getirme YALNIZ içerik anahtarında var: hizmet ve yazı
    // kayıtlarının kimliği silinince yok oluyor ve "eski hâli getir", var
    // olmayan bir kaydı güncellemeye çalışmak olurdu.
    if (payload.restorable && row.target_type === 'site_content'
        && row.before_json !== null && row.before_json !== undefined) {
      box.body.append(button('Önceki hâli düzenleyiciye getir', {
        variant: 'primary',
        title: 'Değeri forma yükler; kaydetmek ayrı bir adımdır',
        onClick: () => {
          // SESSİZCE GERİ YAZILMAZ. Tek düğmeyle geri yazmak, aradaki bütün
          // değişiklikleri de görünmez biçimde silerdi; değer forma gelir,
          // yönetici bakar ve kendi gerekçesiyle kaydeder.
          state.pending = { key: row.target_key, value: row.before_json };
          box.close();
          nodes.tabs.select('content');
        },
      }));
    }
  });
}

// ========================================================== toplu tazeleme

async function askRevalidate() {
  const reason = await confirmWithReason(nodes.root, {
    title: 'Site yeniden çizdirilsin mi?',
    description: 'Next.js önbelleği boşaltılır ve sayfalar depodaki hâlleriyle '
      + 'yeniden çizilir. HİÇBİR KAYIT DEĞİŞMEZ. Çizdirme başarısız olursa '
      + 'sonucu üstteki şeritte yazar.',
    confirmLabel: 'Siteyi tazele',
    danger: false,
    minLength: REASON_MIN,
    placeholder: `Neden tazeleniyor? (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;
  await withBusy('Site tazeleniyor…', async () => {
    const result = await call(`${BASE}/revalidate`, {
      method: 'POST', body: { reason, paths: null },
    });
    announce(result,
      result.revalidate && result.revalidate.status === 'ok' ? 'Site tazelendi.' : '');
  });
}

// =================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel bsi');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'content', label: 'Kurumsal metinler' },
    { key: 'services', label: 'Hizmetler' },
    { key: 'posts', label: 'Yazılar' },
    { key: 'history', label: 'Geçmiş' },
  ], 'content', (key) => showTab(key));

  // Süzgeçler sekmeyle birlikte yok edilmez: `filterBar` global dinleyici
  // tutuyor ve her sekme geçişinde yenisini kurmak onları biriktirirdi.
  nodes.serviceFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px',
        placeholder: 'Başlık, adres, metin' },
      { kind: 'select', key: 'published', label: 'Durum', value: 'all',
        options: PUBLISHED_OPTIONS },
    ],
    onChange: (values) => {
      // Hizmet listesi SAYFALANMIYOR (onlarca kayıt), bu yüzden arama
      // istemcide yapılır ve her tuşta istek atmaz. Yalnız sunucuya giden
      // süzgeç değiştiğinde yeniden çekilir.
      if (values.published !== nodes.lastServicePublished) {
        nodes.lastServicePublished = values.published;
        showServices();
        return;
      }
      paintServices();
    },
    actions: [
      button('Yenile', { onClick: () => showServices() }),
      button('Yeni hizmet', { variant: 'primary', onClick: () => openService(null) }),
    ],
  });
  nodes.lastServicePublished = 'all';

  nodes.postFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px', placeholder: 'Başlık, metin' },
      { kind: 'select', key: 'category', label: 'Kategori', value: '',
        options: [{ value: '', label: 'Tüm kategoriler' }] },
      { kind: 'select', key: 'published', label: 'Durum', value: 'all',
        options: PUBLISHED_OPTIONS },
    ],
    onChange: () => {
      // Yazı araması SUNUCUDA yapılır (liste sayfalı): istemcide süzmek yalnız
      // açık sayfadaki 25 satırda arardı ve "böyle bir yazı yok" yalanını
      // söylerdi. Süzgeç değişince ilk sayfaya dönülür.
      state.postsMeta = { ...state.postsMeta, page: 1 };
      showPosts();
    },
    actions: [
      button('Yenile', { onClick: () => showPosts() }),
      button('Yeni yazı', { variant: 'primary', onClick: () => openPost(null) }),
    ],
  });

  nodes.historyFilters = filterBar({
    fields: [
      { kind: 'select', key: 'target_type', label: 'Tür', value: '',
        options: [
          { value: '', label: 'Hepsi' },
          { value: 'site_content', label: 'Kurumsal metin' },
          { value: 'site_service', label: 'Hizmet' },
          { value: 'site_post', label: 'Yazı' },
        ] },
    ],
    onChange: () => showHistory(),
    actions: [button('Yenile', { onClick: () => showHistory() })],
  });

  nodes.status = statusLine();
  nodes.banner = h('div', 'bsi-bannerbox');
  nodes.body = h('div', 'bsi-body');

  const bar = h('div', 'bsi-topbar');
  bar.append(nodes.tabs.node, h('span', 'kit-spacer'),
             button('Siteyi yeniden çizdir', { onClick: () => askRevalidate() }));

  view.append(bar, nodes.status.node, nodes.banner, nodes.body);

  function showTab(key) {
    state.tab = key;
    if (key !== 'history') state.historyKey = '';
    ({
      content: showContent,
      services: showServices,
      posts: showPosts,
      history: showHistory,
    }[key] || showContent)();
  }

  root.replaceChildren(view);
  showTab('content');

  return () => {
    // Süzgeçler ve tarih alanları global dinleyici tutar; çekmeceler `document`
    // üzerinde `keydown` bırakır ve zengin metin düzenleyicisi açık bir görsel
    // penceresi bırakabilir (`formGrid.destroy()` onu da kapatır).
    nodes.serviceFilters?.destroy();
    nodes.postFilters?.destroy();
    nodes.historyFilters?.destroy();
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
  };
}
