// Ana Ekran Görselleri paneli — vitrinin ilk ekranını buradan kurarsınız.
//
// NE YAPAR: üstte ana sayfanın ÖLÇEKLİ TEMSİLİ (duyuru şeridi · slider ·
// bannerlar · koleksiyonlar), altında dört sekme ve her sekmede o şeridin slot
// listesi. Sıra sürükle-bırak İLE VE `Ctrl+↑/↓` ile değişir. Slot düzenleyici
// çekmecede açılır; görsel dosyadan seçilir ya da üstüne bırakılır, tarayıcıda
// base64'e çevrilip gövdeyle gider ve ölçüsü sunucuda ölçülür.
//
// NE YAPMAZ:
//  · Slot SİLMEZ. Yayından kaldırılan slot listede kalır; tıklama geçmişi ve
//    "geçen kampanyada ne asmıştık" bilgisi silinmez (ADR 0012).
//  · Yayına ALMAZ — o ayrı izindir (`store_home_media.publish`) ve ayrı
//    düğmedir. Düzenleyicinin "Kaydet"i slotu vitrine düşürmez.
//  · Görseli KIRPMAZ/BÜYÜTMEZ. Bulanık görseli sessizce büyütmek onu daha da
//    bozardı; ekran ölçüyü ve hangi kenardan ne kadar kırpılacağını söyler,
//    kararı kullanıcı verir ve karar denetim kaydına yazılır.
//  · Süzgeç açıkken sıra değiştirtmez: sıra tüm şeridi ilgilendirir, süzülmüş
//    bir alt kümeyi sıralamak kalan slotları rastgele yerlere atardı.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Mağazanın sıra ucu GLOBAL liste ister; panel yalnız şeridin sırasını
//    gönderir, sunucu onu global sıraya oturtur.
//  · Tauri'de dosya sistemi/dialog eklentisi YOK. Bu yüzden gizli
//    `<input type=file>` + `FileReader` + base64 gövde kullanılır.
//  · `<input type="date">` WebKitGTK'da bozuk; tarih alanları kitin
//    `dateField`'ından gelir (formGrid `type: 'date'`).
//  · Alt metni ZORUNLU: ekran okuyucu ve arama motoru banner'ı yalnız ondan
//    okur. Boşsa sunucu da reddeder (K9).
//  · Görsel önizlemesini SABİT bir çerçeveye sığdırmak, uyarmaya çalıştığımız
//    kırpmayı gizler. Bu yüzden iki kare çizilir: vitrindeki (kırpılmış) hâli
//    ve dosyanın gerçek oranı. Ölçü sunucudan gelir, tarayıcıdan değil.
//  · Yükleme ucu (`POST /image/upload`) mağaza tarafında HENÜZ YOK. Geçit
//    "uç henüz yayında değil" derse ekran bunu hata değil "hazır, bekliyor"
//    diye anlatır; görsel o zamana kadar slot kaydıyla birlikte gider (K7).
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_home_media/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_home_media/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  bytes as formatBytes, button, clip, confirmSimple, confirmWithReason, csvBlob, debounce, h,
  loadStyles, num, toaster,
} from '../../ui-kit/kit.js';
import { filterBar } from '../../ui-kit/filters.js';
import { applyChoiceFilter, choiceField, choiceValues } from '../../ui-kit/choice.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_home_media';

// ŞERİT ADLARI İŞ DİLİNDE — "Slider" / "Banner" DEĞİL.
//
// Bu ekranı kullanan kişi yazılım bilmiyor. "Slider" ve "banner" onun
// sözlüğünde yok; ekranda okuduğu kelimeyi vitrinde göreceği şeye
// bağlayamıyor. Ad artık kutunun MÜŞTERİDE NEREYE DÜŞTÜĞÜNÜ söyler; teknik
// anahtar (`slider`, `banner`) kodda ve mağazaya giden gövdede aynen kalır.
//
// `one` = tekil ad ("3 slot" değil "3 kayan görsel"), `what` = sekmenin
// altında duran tek cümlelik "burası ne işe yarar" açıklaması. Aynı üçlü
// backend'de de var (slots.py AREA_LABELS/AREA_ONE/AREA_WHAT) ve sözleşme
// testi ikisinin ayrışmasını yakalar.
const AREAS = [
  { key: 'slider', label: 'Ana ekran kayan görseller', one: 'kayan görsel',
    what: 'Ana sayfanın en üstünde sırayla dönen büyük görseller. Müşterinin siteye '
      + 'girer girmez gördüğü yer burasıdır.' },
  { key: 'banner', label: 'Tanıtım görselleri', one: 'tanıtım görseli',
    what: 'Sayfanın ortasında ve altında duran sabit kampanya görselleri.' },
  { key: 'collection', label: 'Öne çıkan ürün grupları', one: 'ürün grubu',
    what: '“Yeni gelenler”, “Çok satanlar” gibi ana sayfadaki ürün şeritleri.' },
  { key: 'announcement', label: 'Üst duyuru yazısı', one: 'duyuru',
    what: 'Sayfanın en üstündeki ince şeritte duran yazı — kargo, kampanya ya da tatil '
      + 'duyurusu. Görsel değil, metindir.' },
];

const STATE_TONES = { published: 'good', scheduled: 'info', expired: 'warn', draft: 'dim' };
const STATE_LABELS = {
  published: 'Yayında', scheduled: 'Tarihi gelmedi', expired: 'Süresi doldu',
  draft: 'Hazırlıkta',
};
// Rozetin YANINDA duran cümle. "Hazırlıkta" bir durum adıdır; kullanıcının
// sorduğu soru ise "müşteri şu an görüyor mu". Cevap yazıyla verilir.
const STATE_WHAT = {
  published: 'Müşteri şu anda görüyor.',
  scheduled: 'Başlangıç tarihi gelmedi; o gün kendiliğinden yayına girer.',
  expired: 'Bitiş tarihi geçti; müşteri artık görmüyor.',
  draft: 'Müşteri görmüyor. Göstermek için “Yayına al” demeniz gerekir.',
};
const SIZE_TONES = { ok: 'good', blurry: 'bad', ratio: 'warn', unknown: 'warn', none: 'dim' };

const LINK_KINDS = [
  { value: 'url', label: 'Adresi ben yazacağım' },
  { value: 'product', label: 'Bir ürün sayfası' },
  { value: 'category', label: 'Bir kategori sayfası' },
  { value: 'cms', label: 'Site içi bilgi sayfası (Hakkımızda, İletişim…)' },
];

// ENGELLER — NEDEN + SIRADAKİ ADIM, tek yerde.
//
// Desen `store_shipping/backend/geliver.py` içindeki `BLOCKER_ACTIONS`'tan
// gelir ve aynı kuralı taşır: BİR İŞ YAPILAMIYORSA EKRAN İKİ ŞEY SÖYLER —
// neden yapılamadığı, ve kullanıcının ŞİMDİ ne yapacağı. "Salt okunur",
// "uç yayında değil", "sıra değiştirilemez" gibi tek cümlelik ret metinleri
// kullanıcıyı ekranda bırakıyordu: doğru ama işe yaramaz.
//
// `why` kullanıcının SUÇLU OLMADIĞINI da söyler; `next` her zaman bir
// eylemle başlar. Metin burada durur ki üç ayrı yerde üç ayrı cümleye
// dönüşmesin.
const BLOCKERS = {
  READ_ONLY: {
    why: 'Şu an yalnız bakabilirsiniz, değiştiremezsiniz. Ana sayfa listesi mağazadan '
      + 'okunamadı; ekranda gördüğünüz görüntü sitenin temasından çıkarılmış bir özet.',
    next: 'Sıradaki adım: “Yenile” deyin. Bağlantı düzelir düzelmez düzenleme '
      + 'kendiliğinden açılır. Sürerse mağazaya bakan kişiye haber verin.',
  },
  FILTERED_ORDER: {
    why: 'Arama ya da süzgeç açıkken sıra değiştirilemez. Sıra bütün şeridi ilgilendirir; '
      + 'yalnız görünen birkaç satırı sıralamak, görünmeyenleri rastgele yerlere atardı.',
    next: 'Sıradaki adım: “Filtreyi temizle” deyin, sonra sürükleyerek sıralayın.',
  },
  OFFLINE: {
    why: 'Mağazaya ulaşılamadı; ekran şu an mağazadaki gerçek durumu göremiyor.',
    next: 'Sıradaki adım: internet bağlantısını kontrol edip “Tekrar dene” deyin.',
  },
  NEEDS_IMAGE: {
    why: 'Bu bölüm görselsiz kaydedilemez: görselsiz bir tanıtım kutusu ana sayfada boş '
      + 'bir çerçeve olarak çizilir.',
    next: 'Sıradaki adım: “Dosya seç” ile bir görsel seçin ya da görseli kutunun üstüne '
      + 'sürükleyin.',
  },
  NO_FILE_PICKED: {
    why: 'Henüz bir dosya seçilmedi; gönderilecek görsel yok.',
    next: 'Sıradaki adım: “Dosya seç” ile bir görsel seçin.',
  },
  AREA_TEXT_ONLY: {
    why: 'Üstteki duyuru yazısı yalnız metin gösterir; oraya görsel konmaz.',
    next: 'Sıradaki adım: görsel asmak istiyorsanız “Tanıtım görselleri” sekmesine geçin.',
  },
};

/** Engelin iki cümlesini ekranda tek kutuda gösterir (neden + sıradaki adım). */
function blockerBox(key, tone = 'warn') {
  const item = BLOCKERS[key];
  const box = h('div', `kit-alert ${tone} hm-blocker`);
  box.append(h('div', 'hm-blocker-why', item.why));
  box.append(h('div', 'hm-blocker-next', item.next));
  return box;
}

/** Kapalı düğmenin nedenini fare ipucuna VE ekran okuyucuya yazar. */
function blockedReason(node, key) {
  const item = BLOCKERS[key];
  const text = `${item.why} ${item.next}`;
  node.title = text;
  node.setAttribute('aria-label', `${node.textContent} — kapalı: ${text}`);
  node.dataset.blocked = '1';
  return node;
}

const EMPTY_STATE = {
  items: [], preview: { slider: [], banner: [], collection: [], announcement: [] },
  summary: {}, counts: {}, connected: false, error: '', readOnly: false, source: '',
  recommended: {}, maxImageBytes: 2000000, allowedTypes: ['image/png', 'image/jpeg'],
  notice: '', reference: { channels: [], locales: [], categories: [], pages: [] },
  area: 'slider', order: [], baseOrder: [], device: 'desktop', loaded: false,
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];          // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

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
    // "İşlem başarısız" hiçbir şey anlatmıyordu. Sunucunun cevabı VARSA
    // olduğu gibi gösterilir; yoksa en azından ne yapılacağı yazılır.
    const message = error.message
      || 'Bu işlem tamamlanamadı. “Yenile” deyip yeniden deneyin; sürerse mağazaya '
        + 'bakan kişiye haber verin.';
    toast(message, 'bad');
    nodes.status?.set(message, true);
    return null;
  } finally {
    busy = false;
  }
}

/** Yıkıcı ve vitrini etkileyen her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Neden değiştiriyorsunuz? (en az 10 karakter) — "Eylül kampanyası '
      + 'başladı" gibi. Bu not kayda geçer.',
  });
}

/** Ekran okuyucuya "3. sıraya taşındı" gibi anlık bilgi verir. */
function announce(message) {
  if (nodes.live) nodes.live.textContent = message;
}

/** Açık sekmenin tekil adı — "3 slot" yerine "3 kayan görsel" demek için. */
function areaOne(key = state.area) {
  return AREAS.find((item) => item.key === key)?.one || 'öğe';
}

function areaLabel(key = state.area) {
  return AREAS.find((item) => item.key === key)?.label || '';
}

/** "Burası ne işe yarar" tek cümlesi — sekmenin ve kartın altında durur. */
function areaWhat(key = state.area) {
  return AREAS.find((item) => item.key === key)?.what || '';
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const stats = state.summary || {};
  const parts = [`Mağazaya bağlı · ana ekranda ${num(stats.total || 0)} görsel/duyuru`,
    `${num(stats.published || 0)} tanesini müşteri görüyor`];
  if (stats.missingAlt) {
    parts.push(`${num(stats.missingAlt)} tanesinin görsel açıklaması eksik`);
  }
  if (state.source === 'themes') parts.push('şu an yalnız bakılabiliyor');
  return parts.join(' · ');
}

// -------------------------------------------------------------------- veri

function currentFilters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  return {
    q: values.q || '',
    status: values.status || '',
    device: values.device || '',
    placement: values.placement || '',
    channel: values.channel || '',
    start: range.start || '',
    end: range.end || '',
  };
}

/** Süzgeç açıkken sıra değiştirilemez — sıra TÜM şeridi ilgilendirir. */
function orderEditable() {
  if (state.readOnly || !state.connected) return false;
  return Object.values(currentFilters()).every((value) => !value);
}

function queryString() {
  const params = new URLSearchParams();
  params.set('area', state.area);
  for (const [key, value] of Object.entries(currentFilters())) {
    if (value) params.set(key, String(value));
  }
  return params.toString();
}

async function refresh() {
  nodes.listWrap?.replaceChildren(skeletonRows(5, 4));
  nodes.status?.set('Ana sayfada ne olduğu okunuyor…');
  let payload;
  try {
    payload = await api(`${BASE}/slots?${queryString()}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, items: [] };
    renderAll();
    nodes.status?.set(statusText(), true);
    return;
  }
  const order = (payload.preview?.[state.area] || []).map((row) => row.id);
  state = {
    ...state,
    items: payload.items || [],
    preview: payload.preview || EMPTY_STATE.preview,
    summary: payload.summary || {},
    counts: payload.counts || {},
    connected: Boolean(payload.connected),
    error: payload.error || '',
    readOnly: Boolean(payload.readOnly),
    source: payload.source || '',
    recommended: payload.recommended || {},
    maxImageBytes: payload.maxImageBytes || EMPTY_STATE.maxImageBytes,
    allowedTypes: payload.allowedTypes || EMPTY_STATE.allowedTypes,
    notice: payload.notice || '',
    order,
    baseOrder: order,
    loaded: true,
  };
  renderAll();
  nodes.status?.set(statusText(), !state.connected);
}

async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    return;              // referans gelmezse süzgeçler boş kalır ama ekran çalışır (K7)
  }
  state.reference = {
    channels: payload.channels || [],
    locales: payload.locales || [],
    categories: payload.categories || [],
    pages: payload.pages || [],
  };
  // TEK KANALLI MAĞAZADA KUTU ÇİZİLMEZ (`choice.js`). Karar VERİDEN çıkar;
  // ikinci kanal açılırsa süzgeç kendiliğinden geri gelir.
  applyChoiceFilter(nodes.filters, 'channel', state.reference.channels,
                    { allLabel: 'Hepsi — mağaza' });
  if (payload.warnings?.length) {
    toast('Bazı listeler mağazadan gelmedi; seçim kutuları eksik olabilir. Ekranın geri '
      + `kalanı çalışıyor. (${payload.warnings.join(' · ')})`, 'warn');
  }
}

// ================================================================ önizleme

/**
 * Ana sayfanın ölçekli temsili.
 *
 * GERÇEK SAYFA DEĞİLDİR ve öyle olduğunu iddia etmez: `iframe` ile canlı
 * sayfayı çekmek CSP'de `frame-src` olmadığı için WebKitGTK'da öngörülemez
 * davranıyor. Bunun yerine şeritler önerilen ORANLARINDA çizilir — hangi
 * görselin nereye düştüğü ve neyin eksik olduğu buradan görünür.
 *
 * Yalnız YAYINDA olan slotlar çizilir: müşterinin gördüğü budur.
 */
function renderPreview() {
  const host = nodes.preview;
  if (!host) return;
  host.replaceChildren();

  const head = h('div', 'hm-preview-head');
  head.append(h('b', undefined, 'Müşteri ana sayfada bunu görüyor'));
  const toggle = h('div', 'hm-device');
  for (const [key, label] of [['desktop', 'Bilgisayarda'], ['mobile', 'Telefonda']]) {
    const item = button(label, {
      variant: state.device === key ? 'primary' : 'ghost',
      onClick: () => { state.device = key; renderPreview(); },
    });
    item.setAttribute('aria-pressed', state.device === key ? 'true' : 'false');
    toggle.append(item);
  }
  head.append(h('span', 'kit-spacer'), h('span', 'hm-sub',
    state.device === 'desktop'
      ? 'Küçültülmüş temsil — gerçek sayfa bunun iki katı genişlikte.'
      : 'Telefon ekranı kadar; gerçek genişliğe yakın.'), toggle);
  host.append(head);

  if (!state.connected) {
    host.append(alertBox(`Mağazaya ulaşılamadı — ${state.error}`, 'bad'));
    return;
  }

  const stage = h('div', `hm-stage ${state.device}`);
  let drawn = 0;
  let hidden = 0;

  const visible = (row) => {
    if (row.state !== 'published') return false;
    if (state.device === 'mobile' && row.device === 'desktop') return false;
    if (state.device === 'desktop' && row.device === 'mobile') return false;
    return true;
  };

  for (const area of AREAS) {
    const rows = (state.preview[area.key] || []);
    const live = rows.filter(visible);
    hidden += rows.length - live.length;
    drawn += live.length;
    stage.append(previewBand(area, live));
  }

  host.append(stage);
  const note = h('div', 'hm-sub');
  note.textContent = hidden
    ? `Müşterinin şu an gördüğü ${num(drawn)} kutu çizildi. ${num(hidden)} tanesi bu `
      + 'görünümde çıkmıyor: ya hazırlıkta, ya tarihi gelmedi, ya süresi doldu, ya da '
      + 'yalnız öbür ekranda gösteriliyor.'
    : `Müşterinin şu an gördüğü ${num(drawn)} kutu çizildi.`;
  host.append(note);
}

function previewBand(area, rows) {
  const band = h('div', `hm-band hm-band-${area.key}`);
  band.setAttribute('aria-label', area.label);

  if (!rows.length) {
    const empty = h('div', 'hm-band-empty');
    empty.textContent = `${area.label}: boş — müşteri burada hiçbir şey görmüyor`;
    band.append(empty);
    return band;
  }

  if (area.key === 'announcement') {
    // Duyuru şeridi metindir; görsel kutusu çizmek yanıltıcı olurdu.
    band.append(h('div', 'hm-strip', rows.map((row) => row.title).join('  ·  ')));
    return band;
  }

  const limit = area.key === 'slider' ? 1 : (area.key === 'banner' ? 3 : 4);
  for (const row of rows.slice(0, limit)) {
    band.append(previewTile(row, area));
  }
  if (rows.length > limit) {
    const more = h('div', 'hm-tile hm-tile-more');
    more.textContent = `+${num(rows.length - limit)}`;
    more.title = area.key === 'slider'
      ? 'Görseller sırayla döner; burada yalnız ilki çizildi.'
      : 'Bu satıra sığmayanlar. Vitrinde hepsi görünür.';
    band.append(more);
  }
  return band;
}

function previewTile(row, area) {
  // Tıklanabilir: önizlemede gördüğünüz kutu, düzenleyicisini açar. Slotu
  // listeden aramak zorunda kalmamak bu ekranın bütün amacı.
  const tile = h('button', 'hm-tile');
  tile.type = 'button';
  tile.title = `${row.title} — değiştirmek için tıklayın`;
  tile.setAttribute('aria-label', `${area.label}: ${row.title}`);
  tile.addEventListener('click', () => openEditor(row));

  if (row.imageUrl) {
    const image = h('img');
    image.loading = 'lazy';
    image.src = row.imageUrl;
    image.alt = row.altText || '';
    image.addEventListener('error', () => {
      tile.classList.add('broken');
      tile.replaceChildren(h('span', 'hm-tile-text', 'görsel açılmıyor'));
    });
    tile.append(image);
  } else {
    tile.classList.add('broken');
    tile.append(h('span', 'hm-tile-text', 'görsel yok'));
  }
  if (row.sizeState === 'blurry' || row.sizeState === 'ratio') {
    tile.append(h('span', 'hm-tile-flag',
      row.sizeState === 'blurry' ? 'bulanık çıkıyor' : 'kenarları kesiliyor'));
  }
  return tile;
}

// ==================================================================== liste

function areaRows() {
  const all = state.preview[state.area] || [];
  if (!orderEditable()) return state.items.filter((row) => row.area === state.area);
  const byId = new Map(all.map((row) => [row.id, row]));
  return state.order.map((id) => byId.get(id)).filter(Boolean);
}

function renderKpi() {
  if (!nodes.kpi) return;
  const stats = state.summary || {};
  // KUTU BAŞLIKLARI SORUYU CEVAPLAR. "Slot / Yayında / Taslak" bir durum
  // listesiydi; kullanıcının sorduğu soru ise "müşteri ne görüyor". Başlıklar
  // artık o soruyla aynı dili konuşuyor, `title` ipucu da tek cümleyle NEDEN
  // önemli olduğunu söylüyor.
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Toplam', value: num(stats.total || 0),
      title: 'Ana ekranda tanımlı bütün görseller ve duyurular.' },
    { label: 'Müşteri görüyor', value: num(stats.published || 0), tone: 'good',
      title: 'Şu anda ana sayfada çizilenler.' },
    { label: 'Tarihi gelmedi', value: num(stats.scheduled || 0), tone: 'info',
      title: 'Başlangıç tarihi ileri; o gün kendiliğinden yayına girer.' },
    { label: 'Süresi doldu', value: num(stats.expired || 0), tone: 'warn',
      title: 'Bitiş tarihi geçti; müşteri artık görmüyor.' },
    { label: 'Hazırlıkta', value: num(stats.draft || 0), tone: 'muted',
      title: 'Kaydedildi ama yayına alınmadı; müşteri görmüyor.' },
    { label: 'Açıklaması eksik', value: num(stats.missingAlt || 0), tone: 'bad',
      title: 'Görsel açılmadığında okunacak yazı yok; Google da bu görseli tanıyamaz.' },
    { label: 'Ölçüsü tutmuyor', value: num(stats.lowRes || 0), tone: 'warn',
      title: 'Bulanık çıkan ya da kenarları kesilen görseller.' },
    // "0 tıklama" ile "tıklama ölçülmüyor" aynı şey değil: uç bu alanı
    // taşımıyorsa sıfır yazıp ölçüm varmış gibi göstermeyiz.
    { label: 'Tıklama', value: stats.clicksKnown ? num(stats.clicks || 0) : '—',
      title: stats.clicksKnown
        ? 'Müşterilerin bu görsellere kaç kez tıkladığı.'
        : 'Mağaza tıklama sayısı tutmuyor; bu yüzden çizgi görünüyor.' },
  ]));
}

function renderList() {
  const host = nodes.listWrap;
  if (!host) return;
  host.replaceChildren();

  if (state.readOnly) {
    // GEREKÇE DÜZELTİLDİ (2026-08-16). Burada eskiden "BBD slot ucu henüz
    // yayında değil" yazıyordu; slot ucu artık canlıda çalışıyor, dolayısıyla
    // bu kutuyu gören kullanıcının derdi eksik paket değil, O ANKİ okuma
    // hatası. Eski metin geçici bir arızayı "beklemekten başka çare yok" gibi
    // gösteriyordu. Kutu (ve arkasındaki salt okunur dal) duruyor — yalnız
    // artık gerçek nedeni yazıyor.
    const box = blockerBox('READ_ONLY');
    if (state.error) box.append(h('div', 'hm-blocker-detail', `Mağazanın verdiği cevap: ${state.error}`));
    host.append(box);
  }
  if (!orderEditable() && state.connected && !state.readOnly) {
    host.append(blockerBox('FILTERED_ORDER', 'info'));
  }

  const rows = areaRows();
  if (!rows.length) {
    host.append(emptyNode());
    renderOrderBar();
    return;
  }

  const list = h('div', 'hm-list');
  list.setAttribute('role', 'list');
  rows.forEach((row, index) => list.append(slotRow(row, index, rows.length)));
  host.append(list);
  renderOrderBar();
}

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: `${BLOCKERS.OFFLINE.why} ${BLOCKERS.OFFLINE.next}`
        + (state.error ? ` (Mağazanın verdiği cevap: ${state.error})` : ''),
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (!orderEditable()) {
    return emptyState({
      title: 'Aramanıza uyan bir şey yok',
      text: `Bu bölümde kayıt var ama arama ya da süzgeçlere uyan yok. `
        + 'Süzgeçleri temizleyip yeniden bakın.',
      actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
    });
  }
  return emptyState({
    title: `${areaLabel()} boş`,
    text: state.area === 'slider'
      ? 'Ana sayfanın en üstünde hiç görsel yok; müşteri siteye girdiğinde boş bir alan '
        + 'görüyor. Buraya bir kampanya görseli asın.'
      : `Burada hiç ${areaOne()} yok; müşteri bu bölümü hiç görmüyor.`,
    actions: [button(state.area === 'announcement' ? 'Duyuru yaz' : 'Görsel ekle',
      { variant: 'primary', onClick: () => openEditor(null) })],
  });
}

/** Tek slot satırı: sürükle-bırak + `Ctrl+↑/↓` ile taşınır. */
function slotRow(row, index, total) {
  const item = h('div', `hm-slot${row.state === 'draft' ? ' passive' : ''}`);
  item.setAttribute('role', 'listitem');
  item.tabIndex = 0;
  item.dataset.id = String(row.id);
  const movable = orderEditable();
  item.draggable = movable;
  item.setAttribute('aria-label',
    `${index + 1}. ${areaOne(row.area)}: ${row.title} — `
    + `${STATE_LABELS[row.state] || row.state}. ${STATE_WHAT[row.state] || ''}`
    + (movable ? ' Sırasını değiştirmek için Ctrl ile yukarı/aşağı ok tuşu.' : ''));

  const handle = h('span', 'hm-handle', movable ? '⋮⋮' : '·');
  handle.title = movable
    ? 'Sürükleyerek ya da Ctrl+↑/↓ ile yukarı-aşağı taşıyın. Üstteki, vitrinde de '
      + 'en başta görünür.'
    : `${BLOCKERS.FILTERED_ORDER.why} ${BLOCKERS.FILTERED_ORDER.next}`;
  handle.setAttribute('aria-hidden', 'true');

  const thumb = h('span', 'hm-thumb');
  if (row.imageUrl) {
    const image = h('img');
    image.loading = 'lazy';
    image.src = row.imageUrl;
    image.alt = '';
    image.addEventListener('error', () => {
      thumb.classList.add('none');
      thumb.replaceChildren(document.createTextNode('!'));
      thumb.title = 'Görsel açılmıyor — dosya silinmiş ya da adresi değişmiş olabilir. '
        + 'Düzenleyip yeniden yükleyin.';
    });
    thumb.append(image);
  } else {
    thumb.classList.add('none');
    thumb.textContent = row.area === 'announcement' ? 'T' : '—';
    thumb.title = row.area === 'announcement'
      ? 'Duyurular yazıdır; görseli olmaz.'
      : 'Görsel seçilmemiş — vitrinde boş bir çerçeve çizilir.';
  }

  const main = h('div', 'hm-slot-main');
  main.append(clip(h('b'), row.title || '(başlıksız)', 52));
  const meta = h('div', 'hm-sub');
  meta.textContent = [
    row.link ? `tıklayınca → ${row.link}` : 'tıklanmıyor (gideceği yer yazılmamış)',
    row.placementLabel,
    row.deviceLabel,
    row.startsAt || row.endsAt
      ? `${row.startsAt || 'hemen'} → ${row.endsAt || 'süresiz'}`
      : 'her zaman görünür',
  ].join(' · ');
  main.append(meta);
  if (row.issues?.length) {
    // "Eksik: …" listesi ARTIK SONUCU SÖYLER (backend `issues_of`): "oran
    // farklı" değil "kenarları kesilir". Kullanıcının kararı sonuca göre
    // değişiyor, teknik tespite göre değil.
    main.append(h('div', 'hm-issues', `Düzeltilecek: ${row.issues.join(' · ')}`));
  }

  const right = h('div', 'hm-slot-right');
  // Renk tek başına anlam taşımaz: rozetin içinde yazı, yanında sayı var.
  // `stateBadge` — `state` DEĞİL: modül düzeyindeki `state` gölgelenirse
  // aşağıdaki `state.connected` denetimi rozeti sorgular ve sessizce yanlış
  // çalışır.
  const stateBadge = badge(STATE_LABELS[row.state] || row.state, STATE_TONES[row.state] || '');
  stateBadge.title = STATE_WHAT[row.state] || '';
  right.append(stateBadge);
  right.append(h('span', 'hm-sub', row.clicksKnown
    ? `${num(row.clicks || 0)} kez tıklandı` : 'tıklama sayılmıyor'));

  const tools = h('div', 'hm-slot-tools');
  tools.append(
    button('Değiştir', {
      title: 'Görselini, yazısını, tarihini ve tıklanınca gideceği yeri düzenleyin',
      onClick: () => openEditor(row),
    }),
    row.status
      ? button('Yayından kaldır', {
        variant: 'danger',
        title: 'Müşteri görmez olur. SİLİNMEZ; istediğinizde geri açarsınız.',
        onClick: () => togglePublish(row, false),
      })
      : button('Yayına al', {
        variant: 'primary',
        title: 'Müşteri ana sayfada görmeye başlar',
        onClick: () => togglePublish(row, true),
      }),
  );
  if (movable) {
    tools.append(
      button('↑', { variant: 'ghost', title: 'Bir üst sıraya taşı (Ctrl+↑)',
        onClick: () => moveSlot(index, -1) }),
      button('↓', { variant: 'ghost', title: 'Bir alt sıraya taşı (Ctrl+↓)',
        onClick: () => moveSlot(index, 1) }),
    );
  } else if (state.connected && !state.readOnly) {
    // DÜĞMELER GİZLENMEZ, NEDENİ SÖYLENİR. Ok tuşlarının sessizce yok olması
    // "bu ekranda böyle bir şey yok" gibi okunuyordu; oysa yalnız süzgeç
    // açık ve kapatınca geri geliyor.
    tools.append(
      blockedReason(button('↑', { variant: 'ghost', disabled: true }), 'FILTERED_ORDER'),
      blockedReason(button('↓', { variant: 'ghost', disabled: true }), 'FILTERED_ORDER'),
    );
  }

  item.append(handle, thumb, main, right, tools);

  // KLAVYE: sürükle-bırak tek yol OLAMAZ. Ctrl+ok ile taşıma, fare
  // kullanamayan personel için tek erişim yolu (ve en hızlısı).
  item.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target === item) { openEditor(row); return; }
    if (!movable || !event.ctrlKey) return;
    const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
    if (!step) return;
    event.preventDefault();
    if (moveSlot(index, step)) {
      announce(`${row.title}, ${index + 1 + step}. sıraya taşındı (${total} kayıt içinde). `
        + 'Kalıcı olması için “Sırayı kaydet” demeniz gerekir.');
      focusSlot(row.id);
    }
  });

  if (movable) {
    item.addEventListener('dragstart', (event) => {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(row.id));
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => item.classList.remove('dragging'));
    item.addEventListener('dragover', (event) => {
      event.preventDefault();
      item.classList.add('over');
    });
    item.addEventListener('dragleave', () => item.classList.remove('over'));
    item.addEventListener('drop', (event) => {
      event.preventDefault();
      item.classList.remove('over');
      const dragged = Number(event.dataTransfer.getData('text/plain'));
      dropSlot(dragged, row.id);
    });
  }
  return item;
}

function focusSlot(id) {
  const target = nodes.listWrap?.querySelector(`.hm-slot[data-id="${id}"]`);
  target?.focus();
}

/** Taşıma kuralı tek yerde: klavye de sürükleme de bunu çağırır. */
function moveSlot(index, step) {
  const order = [...state.order];
  const target = index + step;
  if (target < 0 || target >= order.length) return false;
  [order[index], order[target]] = [order[target], order[index]];
  state.order = order;
  renderList();
  return true;
}

function dropSlot(draggedId, targetId) {
  if (draggedId === targetId) return;
  const order = state.order.filter((id) => id !== draggedId);
  const at = order.indexOf(targetId);
  if (at < 0) return;
  order.splice(at, 0, draggedId);
  state.order = order;
  renderList();
  announce('Taşındı. Kalıcı olması için “Sırayı kaydet” demeniz gerekir.');
}

function orderDirty() {
  return state.order.join(',') !== state.baseOrder.join(',');
}

function renderOrderBar() {
  const bar = nodes.orderbar;
  if (!bar) return;
  bar.replaceChildren();
  if (!orderDirty()) {
    bar.classList.remove('on');
    return;
  }
  bar.classList.add('on');
  bar.append(h('b', undefined,
    'Sırayı değiştirdiniz ama HENÜZ KAYDEDİLMEDİ. Müşteri hâlâ eski sırayı görüyor.'));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Sırayı kaydet', {
      variant: 'primary',
      title: 'Yeni sırayı mağazaya yazar; müşteri bundan sonra bu sırayla görür',
      onClick: saveOrder,
    }),
    button('Değişiklikleri geri al', {
      variant: 'ghost',
      title: 'Ekrandaki sırayı, kaydedilmiş hâline döndürür',
      onClick: () => { state.order = [...state.baseOrder]; renderList(); },
    }),
  );
}

async function saveOrder() {
  const label = AREAS.find((item) => item.key === state.area)?.label || '';
  const reason = await askReason({
    title: 'Yeni sırayı kaydet',
    description: `“${label}” bölümünün sırası değişecek. Listede en üstteki, ana sayfada da `
      + 'en başta görünür. Diğer bölümler etkilenmez.',
    confirmLabel: 'Sırayı kaydet',
  });
  if (!reason) return;
  await withBusy('Sıra kaydediliyor…', async () => {
    const result = await call(`${BASE}/reorder`, {
      method: 'POST',
      body: { area: state.area, order: state.order, reason, dryRun: false },
    });
    toast('Yeni sıra kaydedildi.', 'good');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

async function togglePublish(row, published) {
  const reason = await askReason({
    title: published ? 'Müşteriye göster' : 'Müşteriden kaldır',
    description: published
      ? `“${row.title}” ana sayfada görünmeye başlayacak. Başlangıç/bitiş tarihi `
        + 'yazdıysanız yalnız o tarihler arasında görünür.'
      : `“${row.title}” ana sayfadan kalkacak, müşteri artık görmeyecek. SİLİNMEZ: `
        + 'listede kalır, görseli ve tıklanma sayısı durur; istediğiniz gün tek düğmeyle '
        + 'geri açarsınız.',
    confirmLabel: published ? 'Yayına al' : 'Yayından kaldır',
  });
  if (!reason) return;
  await withBusy('Kaydediliyor…', async () => {
    const result = await call(`${BASE}/slots/${row.id}/status`, {
      method: 'POST', body: { published, reason, dryRun: false },
    });
    toast(published
      ? 'Yayına alındı; müşteri ana sayfada görecek.'
      : 'Yayından kaldırıldı; müşteri artık görmüyor. Kayıt listede duruyor.', 'good');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

// ================================================================ düzenleyici

/**
 * Slot düzenleyici. `row` yoksa yeni slot açılır (TASLAK olarak kaydedilir;
 * yayına almak ayrı düğme ve ayrı izindir).
 */
function openEditor(row) {
  const forms = [];
  const cleaners = [];
  const dropAll = () => {
    forms.forEach((form) => form.destroy());
    cleaners.forEach((fn) => fn());
    forms.length = 0;
    cleaners.length = 0;
  };
  const area = row?.area || state.area;
  const one = areaOne(area);
  const box = drawer(nodes.root, {
    title: row ? row.title || '(başlık yazılmamış)' : `Yeni ${one}`,
    subtitle: `${areaLabel(area)}`
      + (row ? ` · kayıt no ${row.id}` : ' · kaydettiğinizde müşteri HENÜZ görmez'),
    onClose: dropAll,
  });
  closers.push(dropAll);

  if (state.readOnly) {
    // Aynı düzeltme (2026-08-16): neden "uç yayınlanmadı" değil, "liste
    // okunamadı". Backend de aynı gerekçeyi döndürür (service.save).
    box.body.append(blockerBox('READ_ONLY'));
  }

  // Görsel taslağı çekmece kapanana kadar burada durur; kaydedilene dek
  // mağazaya gitmez.
  const picked = { data: '', name: '', bytes: 0, verdict: null, acknowledged: false };

  const form = formGrid({
    fields: [
      { key: 'title', label: 'Bu görsele ne ad verelim?', type: 'text', required: true,
        maxLength: 160, wide: true,
        hint: 'MÜŞTERİ BU ADI GÖRMEZ — bu ad yalnız sizin listede tanımanız için. '
          + 'Örnek: “Eylül kırtasiye kampanyası”.' },
      { key: 'altText',
        label: area === 'announcement' ? 'Kısa açıklama' : 'Görselde ne var? (kısa açıklama)',
        type: 'text', required: area !== 'announcement', maxLength: 200, wide: true,
        hint: 'Görsel açılmadığında müşterinin okuduğu, Google’ın da gördüğü yazı budur. '
          + 'Dosya adı yazmayın; ne olduğunu yazın: “Sırt çantası kampanyası, %30 indirim”.' },
      { key: 'linkKind', label: 'Tıklayınca nereye gitsin?', type: 'select', options: LINK_KINDS,
        hint: 'Müşteri bu görsele tıkladığında açılacak sayfayı seçin.' },
      { key: 'link', label: 'Gideceği sayfanın adresi', type: 'text', maxLength: 400, wide: true,
        hint: 'Kendi sitemizde kalacaksa `/` ile başlar (`/kampanya`), başka bir siteye '
          + 'gidecekse `https://` ile. Boş bırakırsanız görsel tıklanmaz.' },
      { key: 'placement', label: 'Sayfanın neresinde dursun?', type: 'select', options: [
        { value: 'slider', label: 'En üstteki kayan bölüm' },
        { value: 'top', label: 'Sayfanın üstü' },
        { value: 'middle', label: 'Sayfanın ortası' },
        { value: 'bottom', label: 'Sayfanın altı' },
        { value: 'side', label: 'Yan sütun' },
      ], hint: 'Yukarı çıktıkça daha çok kişi görür.' },
      { key: 'device', label: 'Hangi ekranda görünsün?', type: 'select', options: [
        { value: 'all', label: 'Her ekranda' },
        { value: 'desktop', label: 'Yalnız bilgisayarda' },
        { value: 'mobile', label: 'Yalnız telefonda' },
      ], hint: 'Emin değilseniz “Her ekranda” bırakın — müşterilerin çoğu telefondan '
        + 'giriyor.' },
      // KANAL ALANI TEK SEÇENEKTE SORULMAZ ama DEĞER KAYBOLMAZ: `choiceField`
      // `null` döndürdüğünde `formGrid` alanı atlar, `choiceValues` da tek
      // seçeneği taslağa koyar ve slot o kanalla kaydedilir. Süzgeçten farkı
      // bilinçli: yazarken kanal boş bırakılırsa mağaza kendi varsayılanına
      // düşer ve hangi varsayılan olduğu uçtan uca aynı değil.
      choiceField({ key: 'channel', label: 'Hangi mağaza', options: state.reference.channels }),
      { key: 'startsAt', label: 'Ne zaman başlasın?', type: 'date',
        hint: 'Boş bırakırsanız yayına aldığınız an başlar.' },
      { key: 'endsAt', label: 'Ne zaman bitsin?', type: 'date',
        hint: 'Boş bırakırsanız siz kaldırana kadar durur. Kampanya bitişini buraya '
          + 'yazarsanız o gün kendiliğinden kalkar.' },
    ],
    value: {
      title: row?.title || '',
      altText: row?.altText || '',
      linkKind: row?.linkKind || 'url',
      link: row?.link || '',
      placement: row?.placement || (area === 'slider' ? 'slider' : 'top'),
      device: row?.device || 'all',
      // Kayıtta kanal varsa O kalır; yoksa tek seçenek kendiliğinden dolar.
      // Alan çizilmese de taslakta durduğu için `{...draft}` ile gövdeye gider.
      channel: row?.channel || choiceValues({ channel: state.reference.channels }).channel || '',
      startsAt: row?.startsAt || '',
      endsAt: row?.endsAt || '',
    },
    onChange: () => paintTarget(),
  });
  forms.push(form);

  // ------------------------------------------------------------- hedef seçici
  const target = h('div', 'hm-target');
  const searchInput = h('input', 'kit-input');
  searchInput.type = 'search';
  searchInput.placeholder = 'Ürün adının bir parçasını yazın';
  const results = h('div', 'hm-target-results');
  const search = debounce(async () => {
    const query = searchInput.value.trim();
    results.replaceChildren();
    if (query.length < 2) return;
    let payload;
    try {
      payload = await api(`${BASE}/link-search?q=${encodeURIComponent(query)}`);
    } catch (error) {
      results.append(alertBox(error.message, 'warn'));
      return;
    }
    if (!payload.items.length) {
      results.append(h('div', 'hm-sub',
        'Bu adda ürün bulunamadı. Adın daha kısa bir parçasını yazmayı deneyin.'));
      return;
    }
    for (const item of payload.items) {
      results.append(button(`${item.name} (${item.sku})`, {
        variant: 'ghost',
        onClick: () => { form.set('link', item.url); paintTarget(); },
      }));
    }
  }, 400);
  searchInput.addEventListener('input', search);
  cleaners.push(() => search.cancel());

  function paintTarget() {
    const kind = form.draft().linkKind;
    target.replaceChildren();
    if (kind === 'product') {
      target.append(h('div', 'hm-sub',
        'Ürünü aratıp seçin; adresi yukarıdaki adres kutusuna kendiliğinden yazılır.'),
      searchInput, results);
    } else if (kind === 'category' || kind === 'cms') {
      const list = kind === 'category'
        ? state.reference.categories.map((item) => ({ value: item.url, label: item.label }))
        : state.reference.pages.map((item) => ({ value: item.url, label: item.title }));
      const select = h('select', 'kit-select');
      select.setAttribute('aria-label',
        kind === 'category' ? 'Kategori seçin' : 'Bilgi sayfası seçin');
      for (const option of [{ value: '', label: 'Listeden seçin…' }, ...list]) {
        const node = h('option', undefined, option.label);
        node.value = option.value;
        select.append(node);
      }
      select.addEventListener('change', () => {
        if (select.value) form.set('link', select.value);
      });
      target.append(select);
      if (!list.length) {
        target.append(h('div', 'hm-sub',
          'Liste mağazadan gelmedi. “Adresi ben yazacağım” seçip adresi elle yazabilirsiniz.'));
      }
    } else {
      target.append(h('div', 'hm-sub',
        'Kendi sitemizde bir sayfaya gidecekse `/kampanya` gibi yazın; başka bir siteye '
        + 'gidecekse `https://…` diye tam adresini yazın.'));
    }
  }
  paintTarget();

  // ------------------------------------------------------------------ görsel
  const imageBox = imagePane(area, row, picked);

  // ----------------------------------------------------------------- eylemler
  const actions = h('div', 'hm-actions');
  const save = button(row ? 'Kaydet' : `${one.charAt(0).toLocaleUpperCase('tr')}${one.slice(1)} oluştur`, {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) {
        form.showErrors();
        toast('Kırmızı işaretli alanları doldurun; hangisi olduğu alanın altında yazıyor.',
          'bad');
        return;
      }
      const draft = form.draft();
      if (area !== 'announcement' && !row?.imageUrl && !picked.data) {
        toast(`${BLOCKERS.NEEDS_IMAGE.why} ${BLOCKERS.NEEDS_IMAGE.next}`, 'bad');
        return;
      }
      const patch = row ? form.patch() : { ...draft, area };
      if (row) patch.area = area;
      if (!Object.keys(patch).length && !picked.data) {
        toast('Hiçbir şeyi değiştirmediniz; kaydedilecek bir şey yok.', 'warn');
        return;
      }
      const reason = await askReason({
        title: row ? 'Değişiklikleri kaydet' : `Yeni ${one} oluştur`,
        description: row
          ? `“${row.title}” için ${form.dirty().length} alan değişti`
            + `${picked.data ? ' ve görsel değişiyor' : ''}. Neden değiştirdiğinizi yazın; `
            + 'ileride "bunu kim, niye yaptı" sorusunun cevabı bu not olacak.'
          : 'ÖNEMLİ: kaydettiğinizde müşteri HENÜZ görmez. Görünmesi için ayrıca '
            + '“Yayına al” demeniz gerekir — böylece yarım kalan bir çalışma yanlışlıkla '
            + 'vitrine düşmez.',
        confirmLabel: row ? 'Kaydet' : 'Oluştur',
      });
      if (!reason) return;
      await withBusy('Kaydediliyor…', async () => {
        const path = row ? `${BASE}/slots/${row.id}` : `${BASE}/slots`;
        const result = await call(path, {
          method: row ? 'PUT' : 'POST',
          body: { patch, image: picked.data || '', reason, dryRun: false },
        });
        toast(row
          ? 'Kaydedildi.'
          : 'Oluşturuldu. Müşterinin görmesi için şimdi “Yayına al” deyin.', 'good');
        // Ölçü ve kırpma TEK toast'ta: iki ayrı bildirim üst üste binip
        // birbirini yiyordu. "Uygun" durumunda hiç uyarı çıkmaz.
        if (result.sizeNote && result.sizeState !== 'ok') {
          toast([result.sizeNote, result.cropNote].filter(Boolean).join(' '), 'warn');
        }
        if (result.notice) toast(result.notice, 'warn');
        box.close();
        await refresh();
      });
    },
  });
  // KAPALI DÜĞME NEDENİNİ SÖYLER. Eskiden yalnız `disabled = true` vardı:
  // düğme soluklaşıyor, kullanıcı tıklıyor, hiçbir şey olmuyordu.
  if (state.readOnly) {
    save.disabled = true;
    blockedReason(save, 'READ_ONLY');
  }
  actions.append(save, button('Vazgeç', {
    variant: 'ghost',
    title: 'Hiçbir şey kaydetmeden kapat',
    onClick: box.close,
  }));
  if (row) {
    actions.append(row.status
      ? button('Yayından kaldır', {
        variant: 'danger',
        title: 'Müşteri görmez olur; kayıt silinmez',
        onClick: async () => { box.close(); await togglePublish(row, false); },
      })
      : button('Yayına al', {
        variant: 'primary',
        title: 'Müşteri ana sayfada görmeye başlar',
        onClick: async () => { box.close(); await togglePublish(row, true); },
      }));
  }

  box.body.append(
    imageBox,
    card('Bilgiler', form.node, areaWhat(area)),
    card('Tıklayınca açılacak sayfa', target),
    actions,
    hintBox('Buradan hiçbir şey SİLİNMEZ. “Yayından kaldır” dediğinizde kayıt listede '
      + 'kalır, görseli ve tıklanma sayısı durur; istediğiniz gün geri açarsınız. '
      + '“Kaydet” yayın durumunu değiştirmez: kaydetmek ile müşteriye göstermek ayrı '
      + 'iki iştir.'),
  );
}

/**
 * Görsel alanı: gizli `<input type=file>` + sürükle-bırak + `FileReader`.
 *
 * Tauri'de dosya sistemi/dialog eklentisi YOK; dosya tarayıcıda okunup base64
 * olarak gövdeyle taşınır. Seçilen görselin ölçüsü SUNUCUDA ölçülür
 * (`/image/check`): tarayıcının bildirdiği ölçüye güvenilmez ve karar
 * "Önerilen 1920x640; yüklenen 1200x400 — mobilde bulanık." cümlesiyle
 * kutunun altında durur.
 *
 * ÖNİZLEME İKİ KARE ÇİZER — bilerek. Solda görselin VİTRİNDEKİ hâli: önerilen
 * orana `cover` ile oturmuş, yani kırpılmış. Sağda dosyanın GERÇEK oranı. Tek
 * kare göstermek tam da uyarmaya çalıştığımız kırpmayı gizlerdi: kullanıcı
 * ekranda düzgün duran bir görsel görür, vitrinde kenarları kesik olanı bulurdu.
 *
 * İKİ YOL VAR ve ikisi de açıktır:
 *  · “Görseli yükle” — dosyayı mağazanın görsel ucuna gönderir
 *    (`POST /image/upload`). Uç henüz yayında değil; ekran bunu hata değil
 *    "hazır, bekliyor" diye anlatır (K7).
 *  · “Kaydet” — görsel slot gövdesiyle birlikte gider. Bugün çalışan yol budur.
 */
function imagePane(area, row, picked) {
  const wrap = h('div', 'hm-image');
  const recommended = state.recommended[area] || '';
  const wantsImage = recommended && recommended !== '0x0';

  const frame = h('div', 'hm-drop');
  const preview = h('div', 'hm-drop-preview');
  const note = h('div', 'hm-size-note');
  const meta = h('div', 'hm-sub');
  const outcome = h('div', 'hm-upload-note');   // yükleme sonucu / "uç bekleniyor"

  const input = h('input', 'hm-file');
  input.type = 'file';
  input.accept = state.allowedTypes.join(',');
  input.id = `hm-file-${area}-${row?.id || 'yeni'}`;
  // Görsel olarak gizli ama KLAVYEYLE ULAŞILIR: `display:none` verilseydi
  // sekme tuşuyla erişilemez ve dosya seçmenin klavye yolu kalmazdı.
  const label = h('label', 'kit-btn kit-btn-primary hm-file-label', 'Dosya seç');
  label.setAttribute('for', input.id);
  label.title = 'Bilgisayarınızdan bir görsel dosyası seçin (PNG ya da JPG)';

  const upload = button('Görseli şimdi yükle', {
    title: 'Dosyayı mağazaya hemen gönderir. Kaydetmeden de yükleyebilirsiniz.',
    onClick: () => uploadPicked(),
  });

  /**
   * Yükleme düğmesi kapalıysa NEDENİNİ söyler.
   *
   * Üç ayrı neden vardı ve üçü de aynı soluk düğmeye çıkıyordu: kullanıcı
   * tıklıyor, hiçbir şey olmuyor, hangi eksiği kapatacağını bilmiyordu.
   */
  function refreshTools() {
    const blocked = state.readOnly ? 'READ_ONLY'
      : (!wantsImage ? 'AREA_TEXT_ONLY' : (!picked.data ? 'NO_FILE_PICKED' : ''));
    upload.disabled = Boolean(blocked);
    if (blocked) {
      blockedReason(upload, blocked);
    } else {
      upload.removeAttribute('data-blocked');
      upload.removeAttribute('aria-label');
      upload.title = 'Dosyayı mağazaya hemen gönderir. Kaydetmeden de yükleyebilirsiniz.';
    }
  }

  function paint() {
    preview.replaceChildren();
    const source = picked.data || row?.imageUrl || '';
    if (!source) {
      preview.append(h('span', 'hm-drop-text', wantsImage
        ? `Görseli buraya sürükleyip bırakın ya da “Dosya seç” deyin. Bu bölüme `
          + `${recommended} piksel ölçüsünde bir görsel yakışır.`
        : 'Burası görsel istemez; yazı yeterli.'));
      return;
    }
    // Ölçü SUNUCUDAN gelir. Taze seçimde `/image/check` yanıtından, var olan
    // slotta satırın kendisinden; tarayıcının `naturalWidth` değeri kullanılmaz.
    const fresh = picked.data ? picked.verdict : null;
    const width = fresh ? fresh.width : (row?.imageWidth || 0);
    const height = fresh ? fresh.height : (row?.imageHeight || 0);

    if (wantsImage) {
      const shop = h('figure', 'hm-ratio-cell');
      const viewport = h('div', 'hm-ratio-frame');
      viewport.style.aspectRatio = recommended.replace(/x/i, ' / ');
      const shown = h('img');
      shown.src = source;
      shown.alt = '';
      viewport.append(shown);
      shop.append(viewport, h('figcaption', 'hm-sub',
        `MÜŞTERİ BÖYLE GÖRECEK (${recommended} piksel). Kenarları kesildiyse burada `
        + 'görürsünüz.'));
      preview.append(shop);
    }

    const real = h('figure', 'hm-ratio-cell');
    const image = h('img', 'hm-ratio-true');
    image.src = source;
    image.alt = '';
    const box = fresh?.previewBox;
    if (box?.width) {
      // Kutu sunucunun ölçtüğü en/boydan gelir; panel oran hesabı yapmaz.
      image.style.width = `${box.width}px`;
      image.style.height = `${box.height}px`;
    } else if (width > 0 && height > 0) {
      image.style.aspectRatio = `${width} / ${height}`;
    }
    const ratio = fresh?.aspect
      ? `SEÇTİĞİNİZ DOSYA (en-boy ${fresh.aspect})` : 'SEÇTİĞİNİZ DOSYA';
    real.append(image, h('figcaption', 'hm-sub', width
      ? `${ratio} · ${num(width)}×${num(height)} piksel`
      : 'Dosyanın ölçüsü okunamadı; nasıl görüneceğini gösteremiyoruz.'));
    preview.append(real);
  }

  function showVerdict(verdict) {
    note.replaceChildren();
    if (!verdict) {
      note.append(h('span', 'hm-sub', wantsImage
        ? `Bu bölüm için en uygun ölçü: ${recommended} piksel.`
        : 'Burası görsel istemez.'));
      return;
    }
    note.append(badge(
      { ok: 'Ölçü uygun', blurry: 'Bulanık çıkar', ratio: 'Kenarları kesilir',
        unknown: 'Ölçü okunamadı', none: 'Görsel gerekmiyor' }[verdict.sizeState]
        || verdict.sizeState,
      SIZE_TONES[verdict.sizeState] || '',
    ));
    // ZORUNLU ALT METİN: karar her zaman yazıyla da durur, renge bırakılmaz.
    note.append(h('span', 'hm-size-text', verdict.sizeNote));
    // Kırpma ayrı cümledir: "oran farklı" bir tespit, "soldan ve sağdan %25
    // kırpılacak" ise kullanıcının afişi yeniden kesmesini sağlayan bilgi.
    if (verdict.cropNote) note.append(h('span', 'hm-crop-text', verdict.cropNote));
  }

  async function accept(file) {
    if (!file) return;
    if (!state.allowedTypes.includes(file.type)) {
      const kinds = state.allowedTypes
        .map((item) => String(item).split('/')[1]?.toUpperCase()).filter(Boolean).join(' ya da ');
      toast(`Bu dosya türü kullanılamaz. Yalnız ${kinds} dosyası yükleyebilirsiniz — `
        + 'PDF, Word ya da başka bir dosya olmaz.', 'bad');
      return;
    }
    if (file.size > state.maxImageBytes) {
      toast(`Dosya çok büyük: ${formatBytes(file.size)}. En fazla `
        + `${formatBytes(state.maxImageBytes)} olabilir. Görseli küçültüp yeniden deneyin.`,
      'bad');
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => toast('Dosya okunamadı; bozuk olabilir. Başka bir dosya deneyin.',
      'bad');
    reader.onload = async () => {
      picked.data = String(reader.result || '');
      picked.name = file.name;
      picked.bytes = file.size;
      picked.acknowledged = false;
      outcome.replaceChildren();
      meta.textContent = `${file.name} · ${formatBytes(file.size)}`;
      paint();
      refreshTools();
      try {
        const verdict = await call(`${BASE}/image/check`, {
          method: 'POST', body: { data: picked.data, area },
        });
        picked.verdict = verdict;
        showVerdict(verdict);
        paint();                       // ölçü geldi: gerçek oranlı kare yeniden çizilir
        // ORAN DENETİMİ ZORUNLU: hem bulanıklık hem kırpma sesli uyarı verir.
        // Eskiden yalnız bulanıklık uyarırdı ve "oran farklı" sessizce geçerdi.
        if (verdict.needsConfirm) toast(verdict.sizeNote, 'warn');
      } catch (error) {
        picked.data = '';
        picked.verdict = null;
        note.replaceChildren(alertBox(error.message, 'bad'));
        paint();
        refreshTools();
      }
    };
    reader.readAsDataURL(file);
  }

  /** Dosyayı mağazanın görsel ucuna gönderir. Slot kaydından AYRI yoldur. */
  async function uploadPicked() {
    if (!picked.data) {
      toast(`${BLOCKERS.NO_FILE_PICKED.why} ${BLOCKERS.NO_FILE_PICKED.next}`, 'bad');
      return;
    }
    if (picked.verdict?.needsConfirm && !picked.acknowledged) {
      // Onay burada KOLAYLIK, kapı değil: sunucu `acknowledged` bayrağı
      // olmadan yüklemeyi zaten reddediyor (K9).
      const go = await confirmSimple(nodes.root, {
        title: 'Görselin ölçüsü tam tutmuyor',
        description: `${picked.verdict.sizeNote} ${picked.verdict.cropNote || ''} `.trim()
          + ' Yine de kullanılsın mı? İsterseniz vazgeçip görseli doğru ölçüde '
          + 'hazırlatabilirsiniz.',
        confirmLabel: 'Yine de kullan',
        danger: true,
      });
      if (!go) return;
      picked.acknowledged = true;
    }
    const reason = await askReason({
      title: 'Görseli mağazaya yükle',
      description: 'Dosya mağazaya gönderilir. Bu görselin başlığı, tarihi ve tıklanınca '
        + 'gideceği yer DEĞİŞMEZ — onlar için “Kaydet” demeniz gerekir.',
      confirmLabel: 'Yükle',
    });
    if (!reason) return;
    await withBusy('Görsel yükleniyor…', async () => {
      // `call` KULLANILMAZ: burada `ok:false` her zaman hata değil. Uç henüz
      // yayında değilse `pending` gelir ve ekran onu anlatır, patlamaz (K7).
      const result = await api(`${BASE}/image/upload`, {
        method: 'POST',
        body: {
          data: picked.data, area, slotId: row?.id || 0, filename: picked.name,
          acknowledged: Boolean(picked.acknowledged), reason, dryRun: false,
        },
      });
      outcome.replaceChildren();
      if (result.pending) {
        outcome.append(alertBox(result.error, 'info'));
        toast('Bu yol mağaza tarafında henüz açılmadı. Endişelenmeyin: görseli “Kaydet” '
          + 'ile gönderebilirsiniz, sonuç aynı.', 'warn');
        return;
      }
      if (result.ok === false) {
        outcome.append(alertBox(result.error, 'bad'));
        if (result.needsConfirm) picked.acknowledged = false;
        toast(result.error, 'bad');
        return;
      }
      outcome.append(alertBox(`Görsel mağazaya yüklendi: ${result.file}`, 'good'));
      toast('Görsel yüklendi.', 'good');
      if (result.notice) toast(result.notice, 'warn');
    });
  }

  input.addEventListener('change', () => accept(input.files?.[0]));
  frame.addEventListener('dragover', (event) => {
    event.preventDefault();
    frame.classList.add('over');
  });
  frame.addEventListener('dragleave', () => frame.classList.remove('over'));
  frame.addEventListener('drop', (event) => {
    event.preventDefault();
    frame.classList.remove('over');
    accept(event.dataTransfer?.files?.[0]);
  });

  const tools = h('div', 'hm-image-tools');
  // Sıra önemli: gizli girdi ETİKETTEN ÖNCE gelir, yoksa odak halkasını
  // etikete taşıyan kardeş seçici (.hm-file:focus-visible + .hm-file-label)
  // eşleşmez ve klavye kullanıcısı nereye bastığını göremez.
  tools.append(input, label, upload, button('Seçtiğim dosyayı bırak', {
    variant: 'ghost',
    title: 'Seçtiğiniz dosyayı iptal eder; mağazadaki görsel değişmez',
    onClick: () => {
      picked.data = '';
      picked.verdict = null;
      picked.acknowledged = false;
      meta.textContent = '';
      outcome.replaceChildren();
      paint();
      showVerdict(null);
      refreshTools();
    },
  }));

  frame.append(preview);
  paint();
  refreshTools();
  showVerdict(row && row.imageUrl
    ? { sizeState: row.sizeState, sizeNote: row.sizeNote } : null);
  wrap.append(card('Görsel', frame,
    wantsImage ? `En uygun ölçü: ${recommended} piksel` : 'Burası görsel istemez'),
  tools, meta, note, outcome);
  return wrap;
}

// -------------------------------------------------------------------- CSV

function exportVisible() {
  const rows = areaRows();
  const written = csvBlob(
    ['Başlık', 'Görsel açıklaması', 'Tıklayınca gideceği yer', 'Sayfadaki yeri',
      'Hangi ekranda', 'Başlangıç', 'Bitiş', 'Durum', 'Tıklama', 'Düzeltilecek'],
    rows.map((row) => [row.title, row.altText, row.link, row.placementLabel, row.deviceLabel,
      row.startsAt, row.endsAt, row.stateLabel,
      row.clicksKnown ? row.clicks : '', (row.issues || []).join(', ')]),
    `ana-sayfa-${state.area}`,
  );
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Dosya hazırlanıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: {} });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya kaydedildi: ${result.path}`);
  });
}

// ------------------------------------------------------------------- çizim

function renderAll() {
  renderPreview();
  renderKpi();
  renderList();
  // Sekmenin altında "burası ne işe yarar" tek cümlesi. Sekme adı ne kadar
  // iyi olursa olsun, müşterinin bunu NEREDE göreceğini söylemiyor.
  if (nodes.areaWhat) nodes.areaWhat.textContent = areaWhat();
  nodes.tabs?.badge('slider', state.counts.slider || undefined);
  nodes.tabs?.badge('banner', state.counts.banner || undefined);
  nodes.tabs?.badge('collection', state.counts.collection || undefined);
  nodes.tabs?.badge('announcement', state.counts.announcement || undefined);
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel hm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  // `reverting` bayrağı ZORUNLU: `select()` geri alırken `onChange` bir kez
  // daha çağrılır ve bayrak olmasaydı onay kutusu sonsuz döngüye girerdi.
  let reverting = false;
  nodes.tabs = tabBar(AREAS, 'slider', (key) => {
    if (reverting) { reverting = false; return; }
    if (orderDirty()
      && !window.confirm('Sırayı değiştirdiniz ama kaydetmediniz. Başka sekmeye geçerseniz '
        + 'bu değişiklik kaybolur. Yine de geçilsin mi?')) {
      reverting = true;
      nodes.tabs.select(state.area);
      return;
    }
    state.area = key;
    refresh();
  });

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Başlıkta ya da adreste ara', width: '240px' },
      // SÜZGEÇLERDE VARSAYILAN "Tümü": ekran hiçbir şeyi gizlemeden açılır.
      // Kullanıcı daraltmayı kendi seçer; ekran kendiliğinden daraltmaz.
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Hepsi' },
        { value: 'published', label: 'Müşteri görüyor' },
        { value: 'scheduled', label: 'Tarihi gelmedi' },
        { value: 'expired', label: 'Süresi doldu' },
        { value: 'draft', label: 'Hazırlıkta' },
      ] },
      { kind: 'select', key: 'placement', label: 'Sayfadaki yeri', options: [
        { value: '', label: 'Hepsi' },
        { value: 'slider', label: 'En üstteki kayan bölüm' },
        { value: 'top', label: 'Sayfanın üstü' },
        { value: 'middle', label: 'Sayfanın ortası' },
        { value: 'bottom', label: 'Sayfanın altı' },
        { value: 'side', label: 'Yan sütun' },
      ] },
      { kind: 'select', key: 'device', label: 'Hangi ekranda', options: [
        { value: '', label: 'Hepsi' },
        { value: 'desktop', label: 'Bilgisayarda' },
        { value: 'mobile', label: 'Telefonda' },
      ] },
      // Başlangıçta GİZLİ: kanal listesi `reference` isteğiyle geliyor ve
      // kutunun çizilip çizilmeyeceğine ancak o zaman karar verilebiliyor.
      { kind: 'select', key: 'channel', label: 'Hangi mağaza', hidden: true, options: [] },
      { kind: 'dateRange', key: 'range', label: 'Şu tarihler arasında görünenler' },
    ],
    onChange: () => refresh(),
    actions: [
      button('Yeni ekle', {
        variant: 'primary',
        title: 'Açık sekmeye yeni bir görsel ya da duyuru ekler',
        onClick: () => openEditor(null),
      }),
      button('Yenile', {
        title: 'Mağazadaki güncel durumu yeniden okur',
        onClick: () => refresh(),
      }),
      button('⤓ Ekrandakiler', {
        title: 'Şu an listede görünen satırları Excel dosyası olarak indirir',
        onClick: exportVisible,
      }),
      button('⤓ Hepsi', {
        title: 'Bütün bölümlerdeki her şeyi rapor klasörüne yazar',
        onClick: exportAll,
      }),
      button('Yazdırılabilir rapor', {
        title: 'Ana sayfada ne olduğunu gösteren, yazdırılabilir PDF hazırlar',
        onClick: () => report.run('layout', { area: '' }),
      }),
    ],
  });

  nodes.areaWhat = h('div', 'hm-areawhat');
  nodes.preview = h('div', 'hm-preview');
  nodes.kpi = h('div', 'hm-kpi');
  nodes.orderbar = h('div', 'hm-orderbar');
  nodes.listWrap = h('div', 'hm-listwrap');
  nodes.status = statusLine();
  // Klavyeyle taşımanın sesli karşılığı: ekran okuyucu "3. sıraya taşındı" der.
  nodes.live = h('div', 'hm-live');
  nodes.live.setAttribute('role', 'status');
  nodes.live.setAttribute('aria-live', 'polite');

  nodes.body = h('div', 'hm-body');
  nodes.body.append(nodes.preview, nodes.kpi, nodes.orderbar, nodes.listWrap);

  view.append(nodes.tabs.node, nodes.areaWhat, nodes.filters.node, nodes.status.node,
    nodes.live, nodes.body);
  root.replaceChildren(view);

  nodes.areaWhat.textContent = areaWhat();
  nodes.status.set('Ana sayfada ne olduğu okunuyor…');
  // Referans listeler ÖNCE gelir: kanal süzgeci dolmadan liste çekmek,
  // kullanıcının seçtiği kanalı kaybettiriyordu.
  loadReference().then(() => refresh());

  return () => {
    nodes.filters?.destroy();          // arama alanı ve tarih aralığı dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
