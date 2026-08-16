// Ürünler paneli — 1.419 ürünlük katalogda arama, düzenleme, toplu işlem ve rapor.
//
// NE YAPAR: sunucu tarafında sayfalanmış ürün listesi; satırdan tam yükseklik
// çekmecede sekmeli düzenleyici (künye · fiyat · stok · görsel · varyant ·
// kategori · SEO · geçmiş); seçimle toplu fiyat/stok/kategori/durum
// (ÖNCE FARK TABLOSU, sonra gerekçeli onay); stok raporu ve fiyat listesi PDF.
//
// NE YAPMAZ:
//  · Tam listeyi çekip istemcide süzmez. 1.419 ürün 29 sayfadır ve mağaza
//    dakikada 60 istek kabul eder; "hepsini indir sonra filtrele" ekranı
//    dakikalarca kilitler ve hız sınırını başka araçlara kapatır.
//  · Barkod etiketi basmaz — gerçek barkod çizimi rapor üretecinde yok, sahte
//    barkod basmaktansa hiç basmamak doğrudur.
//
// SİLME VAR VE GERÇEKTİR (pasifleştirme değil). Güvenli olduğu ÖLÇÜLDÜ:
// `order_items` ürünün adını, SKU'sunu, fiyatını ve toplamını kendi satırında
// saklıyor ve `products` tablosuna yabancı anahtar kısıtı yok — silme geçmişi
// bozmaz, kalem raporlarda kırmızı “silinmiş” ibaresiyle görünür. Silme
// önizlemesiz açılmaz: kaç siparişte geçtiği ve kaç adet satıldığı gösterilir,
// gerekçe (>=10 karakter) alınır, kısmi başarı tek tek raporlanır.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Stok `product.quantity` alanında DEĞİL envanter kaynaklarındadır. Liste
//    yaklaşık değeri `~` ile gösterir; kesin sayı ürün açılınca gelir.
//  · Fiyat tek alan değil: liste + indirimli (tarih penceresiyle) + müşteri
//    grubu fiyatları. Üçü birden gösterilir, biri gizlenmez.
//  · Varyantlı ürünün fiyatı varyantlarındadır; fiyat alanları kapalıdır.
//  · Öznitelik ailesi SALT GÖSTERİLİR.
//  · `status` değişikliği indeksleme ister — ekran "vitrine yansıması birkaç
//    dakika sürebilir" der, sessiz kalmaz.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_products/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_products/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, bytes, clip, confirmSimple, confirmWithReason, csvBlob, debounce, h, loadStyles,
  money, num, parseMoney, percent, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow, progress,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { createPicker } from '../../ui-kit/picker.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_products';

const STOCK_TONES = { in: 'good', low: 'warn', out: 'bad', off: 'dim' };
const STOCK_LABELS = {
  in: 'Stokta var', low: 'Stok azaldı', out: 'Tükendi', off: 'Stok takip edilmiyor',
};
// Rozetin YANINDAKİ cümle: durum adı tek başına "ne yapmam gerek" sorusunu
// cevaplamıyor.
const STOCK_WHAT = {
  in: 'Satılabilir durumda.',
  low: 'Azaldı; bitmeden sipariş vermeyi düşünün.',
  out: 'Bitti. Müşteri sipariş veremiyor.',
  off: 'Bu üründe stok sayısı tutulmuyor; her zaman satılabilir görünür.',
};

// ÇİP ADLARI SONUCU SÖYLER. "SEO eksik" doğru bir terimdi ve kullanıcının
// sözlüğünde yoktu; "Google’da zor bulunur" aynı şeyi söyler ama neden
// önemli olduğunu da anlatır.
const CHIPS = [
  { key: 'out_of_stock', label: 'Tükenenler' },
  { key: 'low_stock', label: 'Stoğu azalanlar' },
  { key: 'no_image', label: 'Fotoğrafı olmayanlar' },
  { key: 'seo_missing', label: 'Google’da zor bulunanlar' },
  { key: 'passive', label: 'Vitrinde olmayanlar' },
];

// ENGELLER — NEDEN + SIRADAKİ ADIM, tek yerde.
//
// Desen `store_shipping/backend/geliver.py` içindeki `BLOCKER_ACTIONS`'tan
// gelir. Bir iş yapılamıyorsa ekran iki şey söyler: neden yapılamadığı VE
// kullanıcının ŞİMDİ ne yapacağı. Tek cümlelik ret ("Varyantlı ürünün fiyatı
// varyantlarındadır") doğruydu ama kullanıcıyı ekranda bırakıyordu.
const BLOCKERS = {
  OFFLINE: {
    why: 'Mağazaya ulaşılamadı; ürün listesi okunamıyor.',
    next: 'Sıradaki adım: internet bağlantısını kontrol edip “Tekrar dene” deyin.',
  },
  PRICE_ON_VARIANTS: {
    why: 'Bu ürünün fiyatı kendisinde değil, seçeneklerinde (renk/beden gibi) duruyor. '
      + 'Buraya fiyat yazmak, vitrinde hiç görünmeyen ama raporlara giren hayalet bir '
      + 'fiyat üretirdi.',
    next: 'Sıradaki adım: yukarıdaki “Seçenekler” sekmesine geçip fiyatı orada düzenleyin.',
  },
  NO_SELECTION: {
    why: 'Hiçbir ürün işaretlemediniz; toplu işlem yapılacak ürün yok.',
    next: 'Sıradaki adım: listede satırların solundaki kutucukları işaretleyin.',
  },
  NO_BOOK_FIELDS: {
    why: 'Mağaza kataloğunda “sayfa sayısı” ve “desi” alanları bulunamadı; toplu yazma '
      + 'açılamıyor.',
    next: 'Sıradaki adım: mağaza yazılımına bakan kişiden bu iki alanı tanımlamasını '
      + 'isteyin.',
  },
  CATEGORY_NOT_FILTERED: {
    why: 'Kategori seçiminiz mağaza tarafında uygulanmadı; aşağıdaki liste SÜZÜLMEMİŞ '
      + 'hâlde, yani seçtiğiniz kategori dışındaki ürünleri de içeriyor.',
    next: 'Sıradaki adım: kategoriye göre çalışacaksanız “Yenile” deyip yeniden deneyin; '
      + 'sürerse arama kutusunu kullanın.',
  },
  PARTIAL_ROWS: {
    why: 'Bu liste, katalog denetiminin bulgularından geliyor; satırlar ürün kaydının '
      + 'tamamını taşımayabilir ve bazı alanlar “—” görünebilir.',
    next: 'Sıradaki adım: bir ürünün tam bilgisi için satırına tıklayıp açın.',
  },
};

/** Engelin iki cümlesini tek kutuda gösterir (neden + sıradaki adım). */
function blockerBox(key, tone = 'warn') {
  const item = BLOCKERS[key];
  const box = h('div', `kit-alert ${tone} sp-blocker`);
  box.append(h('div', 'sp-blocker-why', item.why));
  box.append(h('div', 'sp-blocker-next', item.next));
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
  items: [], total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', threshold: 5, categoryFilter: null, source: 'products',
  reference: { categories: [], families: [], types: [], sources: [], fields: {},
    bookFields: [], desiRules: null, imageRules: {} },
  selection: [], chip: null, loaded: false,
};

let api = null;
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
    // "İşlem başarısız" hiçbir şey anlatmıyordu. Sunucunun cevabı VARSA
    // olduğu gibi gösterilir; yoksa en azından sıradaki adım yazılır.
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

/** Yıkıcı ve para etkileyen her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Neden değiştiriyorsunuz? (en az 10 karakter) — "Yayınevi zam yaptı" '
      + 'gibi. Bu not kayda geçer.',
  });
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const pages = Math.max(1, state.pages);
  const scope = state.chip
    ? ` · yalnız “${CHIPS.find((c) => c.key === state.chip)?.label}” gösteriliyor` : '';
  return `Mağazaya bağlı · ${num(state.total)} ürün · ${state.page}. sayfa (toplam ${pages} `
    + `sayfa)${scope}`;
}

// -------------------------------------------------------------------- veri

function currentFilters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const price = values.price || {};
  return {
    q: values.q || '',
    kind: values.type || '',
    status: values.status || '',
    family: values.family || '',
    categoryId: values.category || 0,
    priceMin: price.min ?? null,
    priceMax: price.max ?? null,
  };
}

/** Rapor uçlarının kabul ettiği tek parametre kümesi. */
function reportParams() {
  const filters = currentFilters();
  return { categoryId: Number(filters.categoryId) || 0, type: filters.kind || '' };
}

function queryString(extra = {}) {
  const filters = { ...currentFilters(), ...extra };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === null || value === undefined || value === '' || value === 0) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refresh({ page = state.page, size = state.size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('Ürünler okunuyor…');
  const query = queryString({ page, size, chip: state.chip || '' });
  let payload;
  try {
    payload = await api(`${BASE}/products?${query}`);
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
    threshold: payload.threshold ?? state.threshold,
    categoryFilter: payload.categoryFilter,
    source: payload.source || 'products',
    selection: [],
    loaded: true,
  };
  renderTable();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
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
    categories: payload.categories || [],
    families: payload.families || [],
    types: payload.types || [],
    sources: payload.sources || [],
    // Tek seçenekli alan tarifi: hangi alan çizilecek, hangisi kendiliğinden
    // uygulanacak. Sayıyı sunucu mağazadan okuyup gönderiyor; panel "kanal
    // birdir" diye VARSAYMAZ.
    fields: payload.fields || {},
    // Kitap alanlarının GERÇEK nitelik kodları ve desi katsayıları. Katsayı
    // panelde SABİT DEĞİLDİR: aynı sayı mağazada da, geçitte de yaşıyor ve
    // üçünün ayrışması müşteriden alınan kargo ücretiyle beyan edilen desinin
    // tutmaması demek olurdu.
    bookFields: payload.bookFields || [],
    // ÜRÜN AÇMA FORMU AYRI LİSTE KULLANIR: mağaza künye değerlerini ürünün
    // ÖZNİTELİK AİLESİNE göre yazıyor ve ailede olmayan koda gönderilen değer
    // 200 ile kabul edilip hiçbir yere konmuyor. `bookFields` "katalogda ne
    // var" sorusunun cevabı (toplu yazma ekranı onu sorar); yeni ürünün
    // doğacağı ailede gerçekten yazılabilenler ise bu listede.
    bookFieldsOnCreate: payload.bookFieldsOnCreate || payload.bookFields || [],
    desiRules: payload.desiRules || null,
    // Görsel kuralları da SUNUCUDAN gelir. Ürün AÇMA formunda henüz ürün yok,
    // dolayısıyla `products/{id}/images` çağrılamıyor; sınırı panele yazmak
    // ise aynı 4 MB'ın iki yerde yaşaması demekti.
    imageRules: payload.imageRules || {},
  };
  nodes.filters.options('category', [
    { value: '', label: 'Tümü — kategori' },
    ...state.reference.categories.map((item) => ({ value: item.id, label: item.label })),
  ]);
  nodes.filters.options('type', [
    { value: '', label: 'Tümü — tip' },
    ...state.reference.types.map((item) => ({ value: item.value, label: item.label })),
  ]);
  // 'family' süzgeci artık şeritte yok (tek satıcı, tek aile). Referans listesi
  // yine okunur — düzenleyicideki "Öznitelik ailesi: …" satırı onu gösteriyor.
  if (payload.stale) {
    toast('Kategori ve tür listeleri mağazadan gelmedi; ekranda eski kopyaları '
      + 'gösteriliyor. Sıradaki adım: “Yenile” deyip yeniden deneyin.', 'warn');
  }
}

async function loadHealth() {
  try {
    renderKpi(await api(`${BASE}/health`));
  } catch {
    renderKpi(null);
  }
}

// ------------------------------------------------------------------- çizim

function renderKpi(payload) {
  if (!nodes.kpi) return;
  if (!payload || !payload.connected) {
    nodes.kpi.replaceChildren(hintBox(
      'Katalog özeti şu an okunamadı — bu sizin hatanız değil. Mağaza yazılımındaki ilgili '
      + 'bölüm yayınlandığında tükenen, fotoğrafsız ve Google’da zor bulunan ürün sayıları '
      + 'burada görünecek. Sıradaki adım: “Yenile” deyin; ekranın geri kalanı çalışıyor.',
    ));
    return;
  }
  const tiles = payload.tiles || {};
  const pick = (...names) => {
    for (const name of names) {
      if (tiles[name] !== undefined && tiles[name] !== null) return num(tiles[name]);
    }
    return '—';
  };
  // KUTU BAŞLIKLARI SORUYU CEVAPLAR, terim saymaz. `title` ipucu da tek
  // cümleyle NEDEN önemli olduğunu söyler.
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Toplam ürün', value: pick('total', 'products'),
      title: 'Katalogdaki bütün ürünler.' },
    { label: 'Vitrinde yok', value: pick('inactive', 'passive'), tone: 'muted',
      title: 'Müşteri bunları göremiyor; kayıt duruyor.' },
    { label: 'Tükendi', value: pick('out_of_stock', 'outOfStock'), tone: 'bad',
      title: 'Stoğu bitmiş; müşteri sipariş veremiyor.' },
    { label: 'Stoğu azaldı', value: pick('low_stock', 'lowStock'), tone: 'warn',
      title: 'Belirlediğiniz sınırın altına düşenler.' },
    { label: 'Fotoğrafsız', value: pick('no_image', 'missing_images'), tone: 'warn',
      title: 'Fotoğrafsız ürün vitrinde neredeyse hiç tıklanmıyor.' },
    { label: 'Google’da zor bulunur', value: pick('seo_missing', 'missing_seo'), tone: 'muted',
      title: 'Google’da görünecek başlık/açıklama yazılmamış; arama sonuçlarında geri '
        + 'sıralarda çıkar.' },
  ]));
}

function thumb(row) {
  const box = h('span', 'sp-thumb');
  if (!row.imageUrl) {
    box.classList.add('none');
    box.title = 'Fotoğraf yok — fotoğrafsız ürün vitrinde neredeyse hiç tıklanmıyor.';
    box.textContent = '—';
    return box;
  }
  const image = h('img');
  image.loading = 'lazy';
  image.src = row.imageUrl;
  image.alt = '';
  // Kırık bağlantı sessiz kalmaz: kutu "görsel açılmıyor" der.
  image.addEventListener('error', () => {
    box.classList.add('none');
    box.title = 'Fotoğraf açılmıyor — dosya silinmiş ya da adresi değişmiş olabilir. '
      + 'Ürünü açıp fotoğrafı yeniden yükleyin.';
    box.replaceChildren(document.createTextNode('!'));
  });
  box.append(image);
  return box;
}

function priceCell(row) {
  const box = h('span', 'sp-price');
  box.append(h('b', row.specialState === 'active' ? 'sp-strike' : '', money(row.price)));
  if (row.specialPrice && row.specialState === 'active') {
    const chip = h('span', 'sp-special', money(row.specialPrice));
    chip.title = 'İndirim bugün geçerli; müşteri bu tutarı ödüyor.';
    box.append(chip);
  } else if (row.specialPrice && row.specialState === 'scheduled') {
    box.append(badge('indirim ileri tarihli', 'info'));
  } else if (row.specialPrice && row.specialState === 'expired') {
    box.append(badge('indirim süresi doldu', 'dim'));
  }
  return box;
}

function stockCell(row) {
  const box = h('span', 'sp-stock');
  // Renk tek başına anlam taşımaz: sayının yanında her zaman yazı durur.
  box.append(h('b', undefined, `${row.stockExact ? '' : '~'}${num(row.stock)}`));
  const chip = badge(STOCK_LABELS[row.stockState] || row.stockState,
    STOCK_TONES[row.stockState] || '');
  chip.title = STOCK_WHAT[row.stockState] || '';
  box.append(chip);
  if (!row.stockExact) {
    box.title = 'Yaklaşık sayı (başındaki ~ bunun için). Kesin adet, ürüne tıklayıp '
      + 'açtığınızda görünür.';
  }
  return box;
}

const COLUMNS = [
  { key: 'imageUrl', label: '', width: '46px', cell: thumb },
  {
    key: 'name',
    label: 'Ürün',
    width: 'minmax(0, 2.6fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'sp-name');
      // Uzun ad kesilir ama tamamı `title`'a girer — bilgi kaybolmaz.
      box.append(clip(h('b'), row.name, 46),
        h('span', 'sp-sub', row.categories || 'hiçbir kategoride değil'));
      return box;
    },
  },
  { key: 'sku', label: 'Stok kodu', width: 'minmax(0, 1fr)', className: 'mono', sortable: true },
  { key: 'typeLabel', label: 'Tür', width: '92px' },
  { key: 'price', label: 'Fiyat', width: '150px', align: 'num', sortable: true, cell: priceCell },
  { key: 'stock', label: 'Stok', width: '132px', align: 'num', cell: stockCell },
  {
    key: 'status',
    label: 'Durum',
    width: '104px',
    cell: (row) => {
      const chip = badge(row.status ? 'Satışta' : 'Vitrinde yok', row.status ? 'good' : 'dim');
      chip.title = row.status
        ? 'Müşteri bu ürünü görüyor ve satın alabiliyor.'
        : 'Müşteri bu ürünü göremiyor. Silinmedi; istediğiniz gün geri alırsınız.';
      return chip;
    },
  },
  {
    key: 'updatedAt',
    label: 'Son değişiklik',
    width: '132px',
    cell: (row) => (row.updatedAt ? row.updatedAt.replace('T', ' ').slice(0, 16) : '—'),
  },
  {
    // Satır tıklaması çekmeceyi açıyor; silme düğmesi o tıklamayı YEMELİ,
    // yoksa "sil"e basan kullanıcının önce düzenleyicisi açılırdı.
    key: 'delete',
    label: '',
    width: '52px',
    cell: (row) => {
      const node = button('Sil', {
        variant: 'danger',
        title: `${row.sku} — bu ürünü katalogdan tamamen siler. GERİ ALINAMAZ. `
          + 'Yalnız vitrinden kaldırmak istiyorsanız ürünü açıp “Vitrinde görünsün” '
          + 'kutusunun işaretini kaldırın.',
        onClick: () => deleteDialog([row.id]),
      });
      node.classList.add('sp-rowbtn');
      node.addEventListener('click', (event) => event.stopPropagation());
      return node;
    },
  },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: `${BLOCKERS.OFFLINE.why} ${BLOCKERS.OFFLINE.next}`
        + (state.error ? ` (Mağazanın verdiği cevap: ${state.error})` : ''),
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.chip) {
    const label = CHIPS.find((item) => item.key === state.chip)?.label || '';
    return emptyState({
      title: 'İyi haber: burada hiç ürün yok',
      text: `“${label}” diye bir ürün bulunamadı — katalog bu konuda temiz.`,
      actions: [button('Bu süzgeci kaldır',
        { onClick: () => { nodes.chips.set(null); applyChip(null); } })],
    });
  }
  return emptyState({
    title: 'Aramanıza uyan ürün yok',
    text: 'Seçtiğiniz süzgeçlere uyan ürün bulunamadı. Arama kelimesini kısaltmayı ya da '
      + 'süzgeçleri temizlemeyi deneyin.',
    actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  if (state.categoryFilter === false) wrap.append(blockerBox('CATEGORY_NOT_FILTERED'));
  if (state.source !== 'products') wrap.append(blockerBox('PARTIAL_ROWS', 'info'));

  nodes.table = dataTable({
    columns: COLUMNS,
    rows: state.items,
    selectable: true,
    empty: emptyNode(),
    onRow: (row) => openProduct(row.id),
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
  bar.append(h('b', undefined, `${num(count)} ürün işaretlendi — aşağıdaki işlemler `
    + 'yalnız bunlara uygulanır'));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Fiyat değiştir', {
      title: 'Seçili ürünlerin fiyatını topluca değiştirir. Önce ne olacağını gösterir.',
      onClick: () => bulkDialog('price'),
    }),
    button('Stok gir', {
      title: 'Seçili ürünlerin stok adedini topluca değiştirir. Önce ne olacağını gösterir.',
      onClick: () => bulkDialog('stock'),
    }),
    button('Kategoriye ekle / çıkar', {
      title: 'Seçili ürünleri bir kategoriye ekler ya da o kategoriden çıkarır',
      onClick: () => bulkDialog('category'),
    }),
    // KİTAP ALANLARI TOPLU YAZILABİLEN İKİ ALANLA SINIRLI (sayfa sayısı ·
    // desi): ikisi de kargo ücretinin girdisi ve bir serinin 40 fasikülüne
    // aynı değeri yazmak gerçek bir iş. ISBN/yazar/yayınevi ürüne özgüdür;
    // toplu yazmak onları hatalı hâle getirmenin en hızlı yolu olurdu.
    button('Sayfa sayısı / desi yaz', {
      title: 'Seçili kitaplara sayfa sayısı ya da desi (kargo hacmi) yazar; önce ne '
        + 'olacağı gösterilir',
      onClick: () => bulkDialog('book'),
    }),
    button('Vitrine çıkar', {
      title: 'Seçili ürünleri müşteriye görünür yapar',
      onClick: () => bulkDialog('status', { active: true }),
    }),
    button('Vitrinden kaldır', {
      variant: 'danger',
      title: 'Müşteri görmez olur. SİLİNMEZ; istediğiniz gün geri alırsınız.',
      onClick: () => bulkDialog('status', { active: false }),
    }),
    // Pasifleştirmenin YANINDA durur, yerine değil: biri geri alınabilir,
    // öteki alınamaz ve ikisi ayrı izne bağlı. Aynı düğmeye toplamak
    // "vitrinden kaldır" diye basan personele kataloğu sildirirdi.
    button('Sil', {
      variant: 'danger',
      title: 'Seçili ürünleri katalogdan TAMAMEN siler — GERİ ALINAMAZ. Yalnız vitrinden '
        + 'kaldırmak istiyorsanız yandaki “Vitrinden kaldır” düğmesini kullanın.',
      onClick: () => deleteDialog(state.selection.map(Number)),
    }),
    button('İşaretleri kaldır', {
      variant: 'ghost',
      title: 'Seçimi iptal eder; hiçbir ürüne dokunulmaz',
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
  const headers = ['Stok kodu', 'Ürün adı', 'Tür', 'Kategori', 'Fiyat', 'İndirimli fiyat',
    'Stok', 'Durum'];
  const rows = state.items.map((row) => [
    row.sku, row.name, row.typeLabel, row.categories,
    money(row.price), row.specialPrice ? money(row.specialPrice) : '',
    row.stock, row.status ? 'Satışta' : 'Vitrinde yok',
  ]);
  const written = csvBlob(headers, rows, `urunler-sayfa-${state.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Bütün ürünleri dosyaya yaz',
    description: `${num(state.total)} ürün mağazadan tek tek okunup rapor klasörüne Excel `
      + 'dosyası olarak yazılacak. Birkaç dakika sürebilir; bu sırada başka bir şey '
      + 'yapmanız gerekmiyor.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Katalog taranıyor…', async () => {
    const filters = currentFilters();
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: { categoryId: Number(filters.categoryId) || 0, type: filters.kind || '' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) {
      toast('DİKKAT — dosya eksik olabilir: katalog tek seferde okunabilecek sınıra dayandı. '
        + 'Sıradaki adım: kategori süzgeciyle daraltıp parça parça indirin.', 'warn');
    }
  });
}

// ================================================================ çekmece

async function openProduct(productId) {
  // Çekmecenin kendi kaynakları (formGrid → tarih alanları global dinleyici
  // tutar) çekmece kapanınca bırakılır; panel cleanup'ına bırakılırsa her
  // açılışta bir tane daha birikir.
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Ürün yükleniyor…', subtitle: `#${productId}`, onClose: dropForms,
  });
  closers.push(dropForms);
  box.body.append(skeletonRows(6, 3));

  let payload;
  try {
    payload = await call(`${BASE}/products/${productId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Bu ürün açılamadı',
      text: `${error.message} Sıradaki adım: pencereyi kapatıp “Yenile” deyin ve yeniden `
        + 'deneyin.',
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const product = payload.product;
  box.setTitle(product.name);
  box.body.replaceChildren();

  // SEKME ADLARI İŞ DİLİNDE. "SEO" ve "Varyantlar" kullanıcının sözlüğünde
  // yoktu; ikisi de ne işe yaradığını söyleyen adla değişti.
  const tabs = tabBar([
    { key: 'general', label: 'Temel bilgiler' },
    { key: 'book', label: 'Kitap künyesi' },
    { key: 'price', label: 'Fiyat' },
    { key: 'stock', label: 'Stok' },
    { key: 'images', label: 'Fotoğraflar' },
    { key: 'variants', label: 'Seçenekler' },
    { key: 'categories', label: 'Kategoriler' },
    { key: 'seo', label: 'Google görünümü' },
    { key: 'history', label: 'Kim ne değiştirmiş' },
  ], 'general', (key) => paint(key));
  tabs.badge('variants', payload.variants.length || undefined);
  tabs.badge('images', payload.images.length || undefined);

  const head = h('div', 'sp-drawer-head');
  const statusChip = badge(product.status ? 'Satışta' : 'Vitrinde yok',
    product.status ? 'good' : 'dim');
  statusChip.title = product.status
    ? 'Müşteri bu ürünü görüyor ve satın alabiliyor.'
    : 'Müşteri bu ürünü göremiyor. Silinmedi.';
  const sku = h('code', 'sp-sku', product.sku);
  sku.title = 'Stok kodu (SKU) — ürünün mağazadaki tekil kodu.';
  // "Öznitelik ailesi" kullanıcının sözlüğünde yoktu ve zaten
  // DEĞİŞTİRİLEMEYEN bir bilgi. Ne olduğu söylenir, sonra bir daha
  // düşünülmesi gerekmez.
  const family = h('span', 'sp-sub',
    `Bilgi alanı grubu: ${product.familyName || '—'} (değiştirilemez)`);
  family.title = 'Bu ürünün hangi bilgi alanlarını (ISBN, yazar, sayfa sayısı…) '
    + 'taşıyacağını belirleyen grup. Mağazada bir kez kurulur, sonra dokunulmaz.';
  head.append(statusChip, badge(product.typeLabel, 'info'), sku, family);
  const pane = h('div', 'sp-pane');
  box.body.append(head, tabs.node, pane);

  if (payload.warnings && payload.warnings.length) {
    box.body.insertBefore(alertBox(
      'Bu ürünün bazı bilgileri mağazadan okunamadı; aşağıdaki sekmelerde eksik alanlar '
      + `olabilir. Sıradaki adım: pencereyi kapatıp yeniden açın. (${payload.warnings.join(' · ')})`,
      'warn'), pane);
  }

  function paint(key) {
    dropForms();
    pane.replaceChildren();
    const painter = {
      general: paintGeneral, book: paintBook, price: paintPrice, stock: paintStock,
      images: paintImages, variants: paintVariants, categories: paintCategories,
      seo: paintSeo, history: paintHistory,
    }[key];
    painter?.(pane, payload, forms, box);
  }
  paint('general');
}

/** Tek üründe kaydetme: kirli alanlar + gerekçe → OKU-DEĞİŞTİR-YAZ. */
async function saveProduct(productId, patch, { title, description }) {
  if (!Object.keys(patch).length) {
    toast('Hiçbir şeyi değiştirmediniz; kaydedilecek bir şey yok.', 'warn');
    return null;
  }
  const reason = await askReason({ title, description, confirmLabel: 'Kaydet' });
  if (!reason) return null;
  return withBusy('Kaydediliyor…', async () => {
    const result = await call(`${BASE}/products/${productId}`, {
      method: 'PUT',
      body: { patch, reason, dryRun: false },
    });
    toast(result.dryRun
      ? 'DENEME yapıldı: mağazaya hiçbir şey yazılmadı.'
      : 'Kaydedildi.', result.dryRun ? 'warn' : 'good');
    if (result.notice) toast(result.notice, 'warn');
    return result;
  });
}

function paintGeneral(pane, payload, forms, box) {
  const product = payload.product;
  const form = formGrid({
    fields: [
      { key: 'name', label: 'Ürün adı', type: 'text', required: true, maxLength: 180,
        wide: true,
        hint: 'Müşterinin vitrinde ve arama sonuçlarında göreceği ad.' },
      // "URL anahtarı" kullanıcının sözlüğünde yoktu. Alan adı artık ne
      // olduğunu söylüyor, ipucu da NEREYE etki ettiğini.
      { key: 'urlKey', label: 'Sayfa adresi', type: 'text', maxLength: 180,
        hint: 'Bu ürünün sitedeki adresinin son parçası: bbdstore.com.tr/BURASI. '
          + 'DEĞİŞTİRİRSENİZ eski adres çalışmaz — paylaşılmış bağlantılar ve Google '
          + 'sonuçları kırılır. Kaydetmeden önce “bu adres boşta mı” diye bakılır.' },
      { key: 'status', label: 'Vitrinde görünsün', type: 'checkbox',
        hint: 'İşareti kaldırmak SİLMEK DEĞİLDİR: ürün geçmiş siparişlerde ve raporlarda '
          + 'kalır, istediğiniz gün geri açarsınız.' },
      { key: 'shortDescription', label: 'Kısa açıklama', type: 'richtext', wide: true,
        maxLength: 500, placeholder: 'Listede ve ürün kartının üstünde görünen özet.',
        hint: 'Vitrinde ürün adının hemen altında çıkar. Bir iki cümle yeter; uzun '
          + 'anlatımı aşağıdaki kutuya yazın.' },
      { key: 'description', label: 'Uzun açıklama', type: 'richtext', wide: true,
        maxLength: 8000,
        placeholder: 'Ürünün ayrıntılı anlatımı. Başlık, liste, renk ve kalın yazı '
          + 'araç çubuğundan uygulanır.',
        hint: 'Word gibi yazın: kalın, başlık, madde işareti üstteki araç çubuğundan '
          + 'verilir. Kod yazmanız gerekmiyor.' },
    ],
    value: {
      name: product.name,
      urlKey: product.urlKey,
      status: product.status,
      shortDescription: payload.descriptions.short,
      description: payload.descriptions.long,
    },
  });
  forms.push(form);

  const verdict = h('div', 'sp-verdict');
  const checkKey = debounce(async () => {
    const value = form.draft().urlKey;
    if (!value || value === product.urlKey) { verdict.replaceChildren(); return; }
    try {
      const result = await call(`${BASE}/url-key?value=${encodeURIComponent(value)}`
        + `&productId=${product.id}`);
      verdict.replaceChildren(alertBox(result.message,
        { free: 'good', taken: 'bad', unknown: 'warn', empty: 'warn' }[result.state] || 'info'));
    } catch (error) {
      verdict.replaceChildren(alertBox(
        `Bu adresin boşta olup olmadığı kontrol edilemedi — ${error.message} Kaydetmeyi `
        + 'deneyebilirsiniz; adres doluysa mağaza size söyleyecek.', 'warn'));
    }
  }, 500);
  closers.push(() => checkKey.cancel());
  form.node.addEventListener('input', checkKey);

  const actions = h('div', 'sp-actions');
  actions.append(
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) {
          form.showErrors();
          toast('Kırmızı işaretli alanları doldurun; ne eksik olduğu alanın altında yazıyor.',
            'bad');
          return;
        }
        const result = await saveProduct(product.id, form.patch(), {
          title: 'Değişiklikleri kaydet',
          description: `“${product.name}” için ${form.dirty().length} alan değişti. Neden `
            + 'değiştirdiğinizi yazın; ileride “bunu kim, niye yaptı” sorusunun cevabı '
            + 'bu not olacak.',
        });
        if (result) form.reset(form.draft());
      },
    }),
    button('Stok kodunu değiştir', {
      variant: 'danger',
      title: 'Ürünün mağazadaki kodu değişir. Bu kodla kurulmuş eski bağlantılar ve '
        + 'dış listelerdeki eşleşmeler kırılabilir.',
      onClick: () => changeSku(product),
    }),
    button('Bu ürünün kopyasını oluştur', {
      title: 'Aynı bilgilerle yeni bir ürün açar; sonra farklarını düzenlersiniz',
      onClick: () => copyProduct(product),
    }),
    button('Ürünü sil', {
      variant: 'danger',
      title: 'Katalogdan TAMAMEN siler — GERİ ALINAMAZ. Önce neyin silineceği gösterilir. '
        + 'Yalnız vitrinden kaldırmak için yukarıdaki “Vitrinde görünsün” işaretini '
        + 'kaldırmanız yeterli.',
      onClick: () => deleteDialog([product.id], {
        // Ürün silindiyse açık duran düzenleyici artık olmayan bir ürünü
        // gösteriyor demektir; kapatılır.
        onDone: (outcome) => {
          if ((outcome.deleted || []).includes(product.id)) box?.close();
        },
      }),
    }),
  );

  pane.append(form.node, verdict, actions,
    hintBox('Kaydederken ürün önce mağazadan TAZE okunur; dokunmadığınız alanlar olduğu '
      + 'gibi bırakılır. Yani bu ekranda görmediğiniz bir bilgi, siz kaydettiniz diye '
      + 'silinmez.'));
}

// --------------------------------------------------------- kitap künyesi
//
// ANINDA DESİ. Sayfa sayısı yazılırken desi yeniden hesaplanır ve rakam
// SUNUCUDAKİYLE AYNI çıkar — katsayılar bu dosyaya yazılmaz, `reference`
// ucundan (`desiRules`) gelir. Aksi hâlde aynı sayı üç yerde yaşardı
// (mağazadaki PHP, geçitteki Python, buradaki JS) ve biri değiştiğinde
// diğerleri sessizce eski kalırdı. Ekranın gösterdiği desi ile müşteriden
// alınan kargo ücretinin ayrışması tam olarak kaçınılmak istenen şey.
//
// ÖNCELİK SIRASI EKRANDA DA GÖRÜNÜR: elle girilmiş `desi` bir ÖLÇÜMDÜR ve
// sayfadan yapılan hesabı EZER. Kutuya değer yazan personel, sayfa sayısını
// değiştirmesinin artık bir etkisi kalmadığını görmeli.

/** `rules` katsayılarıyla tek kitabın desisi — YUVARLANMAMIŞ. */
function desiFromPages(pages, rules) {
  const count = Math.max(0, Math.min(Number(pages) || 0, rules.maxPageCount));
  const thicknessCm = (count * rules.pageThicknessMm + rules.coverThicknessMm) / 10;
  return (thicknessCm * rules.footprintCm2) / rules.desiDivisor;
}

/** Sayı okuma — virgül ondalık ayracı kabul edilir, boş/geçersiz `null`. */
function readNumber(value) {
  const text = String(value ?? '').trim().replace(',', '.');
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Ekrandaki değerlerden desi dökümü — sunucudaki üç basamağın aynısı. */
function desiPreview(draft, rules, product) {
  if (['virtual', 'downloadable'].includes(product.type)) {
    return { source: 'Kargoya girmiyor', unit: 0, billed: 0, thickness: 0 };
  }
  const measured = readNumber(draft.desi);
  if (measured !== null && measured > 0) {
    return {
      source: 'Elle girilen desi (ÖLÇÜM — aşağıdaki hesabı ezer)',
      unit: measured,
      billed: Math.max(1, Math.ceil(measured)),
      thickness: (measured * rules.desiDivisor) / rules.footprintCm2,
    };
  }
  const pages = readNumber(draft.pageCount);
  if (pages !== null && pages > 0) {
    const unit = desiFromPages(pages, rules);
    return {
      source: `${num(pages)} sayfadan hesaplandı`,
      unit,
      billed: Math.max(1, Math.ceil(unit)),
      thickness: (Math.min(pages, rules.maxPageCount) * rules.pageThicknessMm
        + rules.coverThicknessMm) / 10,
    };
  }
  return {
    source: 'Varsayılan — ne desi ne sayfa sayısı var',
    unit: rules.defaultDesi,
    billed: Math.max(1, Math.ceil(rules.defaultDesi)),
    thickness: (rules.defaultDesi * rules.desiDivisor) / rules.footprintCm2,
  };
}

/**
 * Çözülemeyen alanların NEDEN yokluğunu yazan kutu — düzenleme sekmesi ile
 * ekleme formu AYNI kutuyu kullanır.
 *
 * ÇÖZÜLEMEYEN ALAN SESSİZCE YOK OLMAZ. "Yayınevi neden yok" sorusunun cevabı
 * ekranda durmalı; olmayan bir koda (ya da seçenekleri okunamamış bir seçim
 * alanına) yazmak, mağazanın isteği 200 ile kabul edip değeri hiçbir yere
 * koymaması demektir.
 */
function missingBox(missing) {
  const box = h('div', 'sp-book-missing');
  for (const item of missing) {
    box.append(h('div', 'sp-sub', `${item.label}: ${item.reason}`));
  }
  return box;
}

/**
 * Kitap künyesi alanının form tarifi — TEK KAYNAK.
 *
 * Hem düzenleme sekmesi hem ekleme formu bunu çağırır; alanın metin mi seçim
 * mi olduğu SUNUCUDAN gelen `spec.type` ile belirlenir, panelde sabit
 * değildir. `publisher` canlıda `select` ve değerini seçenek kimliğiyle
 * saklıyor — serbest metin kutusu çizmek, kaydedilmeyen bir değer yazdırmak
 * olurdu.
 */
function bookField(spec, rules) {
  if (spec.type === 'select') {
    return {
      key: spec.key,
      label: spec.label,
      type: 'select',
      // BOŞ SEÇENEK BAŞTA: künye eksik olabilir ve "bilinmiyor" demenin yolu
      // budur. Listenin ilk maddesini zorla seçtirmek, bilmeyeni uydurmaya
      // zorlar ve yanlış yayınevi yazardı.
      options: [{ value: '', label: '— seçilmedi —' },
        ...(spec.options || []).map((item) => ({ value: item.value, label: item.label }))],
      hint: bookHint(spec.key, rules),
    };
  }
  return {
    key: spec.key,
    label: spec.label,
    type: 'text',
    maxLength: spec.numeric ? 12 : 180,
    hint: bookHint(spec.key, rules),
  };
}

function paintBook(pane, payload, forms) {
  const product = payload.product;
  const info = payload.book || {};
  const rules = info.rules || state.reference.desiRules;
  const specs = info.fields || [];
  const available = specs.filter((item) => item.available);

  if (!rules) {
    pane.append(alertBox('Desi katsayıları okunamadı; anlık hesap kapalı. Ekranı yenileyin.',
      'warn'));
  }

  if (!available.length) {
    pane.append(emptyState({
      title: 'Katalogda kitap niteliği yok',
      text: 'Sayfa sayısı, ISBN, yayınevi, yazar, baskı yılı ve desi için tanımlı bir '
        + 'nitelik bulunamadı. Nitelikler sekmesinden açıldıktan sonra bu alanlar '
        + 'kendiliğinden görünür.',
    }));
  }

  const missing = specs.filter((item) => !item.available);
  if (missing.length) {
    pane.append(card('Katalogda bulunmayan alanlar', missingBox(missing),
      'Nitelik açılmadan yazılamaz — açılırsa alan kendiliğinden gelir'));
  }

  const readout = h('div', 'sp-desi');

  const paintDesi = (draft) => {
    readout.replaceChildren();
    if (!rules) return;
    const view = desiPreview(draft, rules, product);
    readout.append(
      badge(`${num(view.billed)} desi ücretlendirilir`, view.billed > 1 ? 'warn' : 'good'),
      h('span', 'sp-sub', `Birim desi ${num(view.unit, 3)} · yığın kalınlığı `
        + `${num(view.thickness, 2)} cm · ${view.source}`),
    );
  };

  let form = null;
  if (available.length) {
    form = formGrid({
      fields: available.map((item) => bookField(item, rules)),
      value: Object.fromEntries(available.map((item) => [item.key,
        (info.values || {})[item.key] || ''])),
      // ANINDA: her tuş vuruşunda yeniden hesaplanır. Gecikmeli (debounce)
      // yapmak yanlış olurdu — hesap yereldir, ağa çıkmaz ve beklemenin
      // hiçbir karşılığı yok.
      onChange: (draft) => paintDesi(draft),
    });
    forms.push(form);
    paintDesi(form.draft());
  } else {
    paintDesi(info.values || {});
  }

  const box = h('div');
  if (form) box.append(form.node);
  box.append(readout);
  pane.append(card('Kitap künyesi', box,
    'Sayfa sayısı yazıldıkça desi anında yeniden hesaplanır'));

  if (rules) {
    pane.append(hintBox(`Hesap: ${rules.formula}. ${rules.note}`));
  }

  if (form) {
    const actions = h('div', 'sp-actions');
    actions.append(button('Kitap künyesini kaydet', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
        const changed = form.dirty();
        if (!changed.length) { toast('Değişen alan yok.', 'warn'); return; }
        const draft = form.draft();
        const bookPatch = Object.fromEntries(changed.map((key) => [key, draft[key] ?? '']));
        const reason = await askReason({
          title: 'Kitap künyesini güncelle',
          description: `${product.sku} · ${changed.length} alan değişti. Sayfa sayısı ve `
            + 'desi KARGO ÜCRETİNİN girdisidir: değişiklik müşterinin checkout’ta ödeyeceği '
            + 'tutarı doğrudan etkiler.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        const result = await withBusy('Kaydediliyor…', () => call(
          `${BASE}/products/${product.id}`, {
            method: 'PUT', body: { patch: {}, book: bookPatch, reason, dryRun: false },
          }));
        if (!result) return;
        toast(result.dryRun
          ? 'DENEME yapıldı: mağazaya hiçbir şey yazılmadı.'
          : 'Kaydedildi.', result.dryRun ? 'warn' : 'good');
        if (result.desi) {
          toast(`Kargo hesabında kullanılacak desi: ${num(result.desi.billed)} `
            + `(${result.desi.sourceLabel})`, 'info');
        }
        form.reset(form.draft());
      },
    }));
    pane.append(actions);
  }
}

function bookHint(key, rules) {
  if (key === 'pageCount') {
    return 'Kargo desisinin ana girdisi. Sayı olmayan değer (örn. “Fasikül”) hesapta YOK '
      + `sayılır ve ürün varsayılan ${rules ? num(rules.defaultDesi, 1) : '1,0'} desiye çıkar.`;
  }
  if (key === 'desi') {
    return 'Elle ÖLÇÜM. Doldurulursa sayfa sayısından yapılan hesabı EZER — paketi eline '
      + 'alıp ölçen kişinin kararı modelden üstündür. Boş bırakmak doğru olanıdır.';
  }
  if (key === 'isbn') return '10 ya da 13 hane; tire ve boşluk sayılmaz.';
  if (key === 'publishYear') return 'Dört haneli yıl (örnek: 2024).';
  if (key === 'publisher') {
    return 'Katalogdaki yayınevi listesinden seçilir. Listede olmayan bir yayınevi '
      + 'önce Nitelikler sekmesinden seçenek olarak eklenir; buraya elle yazılamaz.';
  }
  if (key === 'author') return 'Serbest metin. Komisyon kitaplarında yayınevi adı yazılabilir.';
  return '';
}

async function changeSku(product) {
  const input = window.prompt(`Yeni stok kodu (şu an: ${product.sku})`, product.sku);
  if (!input || input.trim() === product.sku) return;
  const reason = await askReason({
    title: 'Stok kodunu değiştir',
    description: `Stok kodu ${product.sku} yerine ${input.trim()} olacak. DİKKAT: eski kodla `
      + 'kurulmuş bağlantılar ve dış listelerdeki eşleşmeler kırılabilir; mağaza arama '
      + 'listesini yenileyene kadar ürün bir süre eski koduyla da bulunabilir.',
    confirmLabel: 'Stok kodunu değiştir',
  });
  if (!reason) return;
  await withBusy('SKU değiştiriliyor…', async () => {
    const result = await call(`${BASE}/products/${product.id}/sku`, {
      method: 'POST', body: { sku: input.trim(), reason, dryRun: false },
    });
    toast(`SKU ${result.from} → ${result.to}`, 'good');
    toast(result.notice, 'warn');
    refresh();
  });
}

// ------------------------------------------------------------- yeni ürün
//
// EKRAN NE DOLDURDUYSA GÖSTERİR. Otomatik alanlar (url_key, üst kategoriler,
// SEO metinleri, stok, aile, durum) sunucuda `POST /products/plan` ile
// hesaplanır ve buraya işaretli olarak gelir; hepsinin üstüne yazılabilir.
// Arkada sessizce doldurup kullanıcıyı kaydetme anında şaşırtmak yasak.

const AUTO_LABELS = {
  urlKey: 'Sayfa adresi',
  metaTitle: 'Google’da görünecek başlık',
  metaDescription: 'Google’da görünecek açıklama',
  categoryIds: 'Üst kategoriler',
  attributeFamilyId: 'Bilgi alanı grubu',
  sourceId: 'Stok deposu',
  taxCategoryId: 'Vergi grubu',
  stock: 'Stok',
  status: 'Vitrinde görünme durumu',
};

// ------------------------------------------------- tek seçenekli alanlar
//
// Kanal · dil · para birimi · stok deposu · vergi kategorisi. Mağazada
// hepsinin TEK seçeneği var; tek seçenekli açılır kutu seçim değil, okunup
// geçilen bir satırdır. Alan gizlenir, değer kendiliğinden uygulanır.
//
// SERT KODLAMA YOK: karar sunucudan gelen SAYIDAN çıkar (`fields[key].state`).
// İkinci kanal açıldığı gün alan kendiliğinden geri gelir ve burada tek satır
// değişmez.
//
// İKİ TÜR ALAN VAR ve ayrımı sunucu söylüyor (`writable`):
//  · Yazılabilen (depo, vergi kategorisi) — ürünün kendi alanı. >1 seçenekte
//    GERÇEK bir form alanı olarak geri gelir.
//  · Yazılamayan (kanal, dil, para birimi) — ürünün alanı değil; kanal ve dil
//    her isteğe ayardan konuyor, para birimi kanalın özelliği. >1 seçenekte
//    UYARI olarak geri gelir: "mağazada iki kanal var, bu ekran hepsine
//    `default` yazıyor". Olmayan bir seçim kutusu çizip kullanıcının seçtiğini
//    yok saymak, sessiz yanlış veri demekti.

function fieldSpec(key) {
  return (state.reference.fields || {})[key] || { state: 'none', options: [], visible: false };
}

/** Alanı ancak SUNUCU "görünsün" dediyse form alanı olarak üretir. */
function choiceField(key, hint) {
  const spec = fieldSpec(key);
  if (!spec.visible) return [];
  return [{
    key,
    label: spec.label,
    type: 'select',
    options: spec.options.map((item) => ({ value: item.value, label: item.label })),
    hint,
  }];
}

/** Gizlenen alanları ve neden gizlendiklerini YAZAR — sessizce yok olmazlar. */
function choiceNotes() {
  const box = h('div', 'sp-choices');
  const single = [];
  for (const key of ['channel', 'locale', 'currency', 'sourceId', 'taxCategoryId']) {
    const spec = fieldSpec(key);
    if (spec.state === 'single' && spec.auto) single.push(`${spec.label}: ${spec.auto.label}`);
    if (spec.state === 'many' && !spec.writable) {
      box.append(alertBox(`Mağazada ${num(spec.count)} ${String(spec.label).toLowerCase()} var. `
        + 'Bu ekran hepsine ayardaki değerle yazıyor; seçim ekrandan yapılamaz — '
        + `ayarı (modules.store_products) güncelleyin.`, 'warn'));
    }
  }
  if (single.length) {
    box.append(h('div', 'sp-sub',
      `Tek seçenekli oldukları için sorulmayan alanlar — ${single.join(' · ')}`));
  }
  return box;
}

/** Sunucunun türettiği alanları panelde yazan alanlara çevirir. */
const AUTO_FIELDS = ['urlKey', 'metaTitle', 'metaDescription'];

// ------------------------------------------------------- katlanır bölüm
//
// `<details>` KULLANILIR, elle açılıp kapanan bir div değil: açık/kapalı
// durumu tarayıcının kendi işidir, klavyeyle (Enter/Space) açılır, ekran
// okuyucu "genişletilebilir" diye okur ve Ctrl+F ile sayfada arama yapan
// kullanıcı kapalı bölümün içini de bulabilir.

function collapsible(title, subtitle, content) {
  const node = h('details', 'sp-fold');
  const head = h('summary', 'sp-fold-head');
  const tag = h('span', 'sp-fold-tag');
  head.append(h('b', undefined, title));
  if (subtitle) head.append(h('span', 'sp-sub', subtitle));
  head.append(tag);
  const body = h('div', 'sp-fold-body');
  body.append(content);
  node.append(head, body);
  return {
    node,
    open: () => { node.open = true; },
    /**
     * Kapalı bölümün İÇİ BOŞ SANILMAZ. Kullanıcı alanları doldurup bölümü
     * kapatabilir; başlıkta "3 alan dolu" yazmazsa yazdığını görmeden kaydeder
     * ve ne gittiğini bilemez. Zorla açık tutmak yerine damga konur — kapatma
     * kullanıcının kararıdır, gizlenen bilgi değil.
     */
    mark: (text, tone) => {
      tag.replaceChildren();
      if (text) tag.append(badge(text, tone || 'info'));
    },
  };
}

// ------------------------------------------------- ürün açarken görseller
//
// GÖRSEL ZİNCİRİN SON ADIMIDIR VE BAŞKA TÜRLÜ OLAMAZ: mağazanın yükleme ucu
// ÜRÜN KİMLİĞİ istiyor (`POST /catalog/products/{id}/images`) ve kimlik ancak
// ürün doğunca oluşuyor. Bu yüzden dosyalar formda seçilir, incelenir ve
// önizlenir; mağazaya ürün açıldıktan SONRA giderler.
//
// SEÇİM ANINDA İNCELENİR, GÖNDERİM ANINDA OKUNUR: tür/boyut kararı dosya
// seçilir seçilmez verilir (reddedilen dosya listeye hiç girmez), ama base64
// içerik ancak "Ürünü aç" düğmesine basılınca üretilir. Altı dosyanın
// base64'ünü form açık dururken bellekte tutmanın anlamı yok.

function imagePicker({ rules, limit = 0 }) {
  /** @type {{file: File, url: string, report: object}[]} */
  let picked = [];
  const grid = h('div', 'sp-images sp-newimages');
  // GÜNLÜK PARTİLER ARASINDA BİRİKİR, SIFIRLANMAZ. Seçim listesi (`picked`)
  // birikimli: kullanıcı önce `kapak.png` + `arka.pdf` bırakıp PDF'in
  // reddedildiğini okuyor, sonra `ic1.png` bırakıyor. Günlük her partide
  // silinseydi PDF'in reddedildiğine dair tek iz ekrandan kaybolur, ızgarada
  // iki görsel durur ve kullanıcı üç dosya gönderdiğini sanırdı. (Görseller
  // sekmesinde günlük sıfırlanabilir — orada her seçim seç→yükle→bitir diye
  // KAPALI bir işlem, burada ise seçim birikiyor.)
  const log = h('div', 'sp-file-log');
  const node = h('div', 'sp-uploader');
  // SINIR BİLİNMİYORSA UYDURULMAZ AMA SUSULMAZ DA. `/reference` düşerse
  // kurallar boş gelir ve `inspectFile` içindeki denetimler (tür · boyut ·
  // dosya sayısı) sırayla kapanır — hepsi kuralın DOĞRULUK DEĞERİNE bağlı.
  // Böyle bir durumda ekranın sessizce "hazır" görünmesi, 50 MB'lık bir .mov
  // dosyasının "Kapak" etiketiyle ızgarada durması demekti.
  const unknownRules = !rules || !rules.maxBytes || !(rules.accept || []).length;

  const release = () => {
    // Nesne URL'leri ELDE BIRAKILMAZ: her önizleme bir bellek tutamağıdır ve
    // çekmece kapanınca serbest bırakılmazsa panel açıldıkça birikirler.
    picked.forEach((entry) => URL.revokeObjectURL(entry.url));
  };

  function paint() {
    grid.replaceChildren();
    if (!picked.length) {
      grid.append(h('div', 'sp-sub',
        'Henüz fotoğraf seçilmedi. Fotoğrafsız ürün vitrinde neredeyse hiç tıklanmıyor; '
        + 'en azından bir kapak fotoğrafı ekleyin.'));
      return;
    }
    picked.forEach((entry, index) => {
      const cell = h('div', `sp-image${index === 0 ? ' cover' : ''}`);
      const picture = h('img');
      picture.src = entry.url;
      picture.alt = '';
      cell.append(picture, h('span', 'sp-image-tag', index === 0 ? 'Kapak' : `#${index + 1}`));

      // Sürükle-bırak tek yol OLAMAZ: ok düğmeleriyle de taşınır (klavye).
      const move = (step) => {
        const target = index + step;
        if (target < 0 || target >= picked.length) return;
        const next = [...picked];
        [next[index], next[target]] = [next[target], next[index]];
        picked = next;
        paint();
      };
      const tools = h('div', 'sp-image-tools');
      tools.append(
        button('◀', { variant: 'ghost', title: 'Sola taşı', onClick: () => move(-1) }),
        button('▶', { variant: 'ghost', title: 'Sağa taşı', onClick: () => move(1) }),
        button('Çıkar', {
          variant: 'danger',
          title: 'Listeden çıkarır — mağazaya hiç gönderilmez',
          onClick: () => {
            URL.revokeObjectURL(entry.url);
            picked = picked.filter((item) => item !== entry);
            paint();
          },
        }),
      );
      cell.append(tools);
      cell.append(h('span', 'sp-sub',
        `${entry.file.name} · ${bytes(entry.file.size)}`
        + (entry.report.width ? ` · ${entry.report.width}×${entry.report.height}` : '')));
      grid.append(cell);
    });
  }

  const input = h('input', 'sp-file');
  input.type = 'file';
  input.multiple = true;
  input.accept = (rules.accept || []).join(',');
  input.id = `sp-newfile-${Math.random().toString(36).slice(2, 8)}`;
  // Görsel olarak gizli ama KLAVYEYLE ULAŞILIR (bkz. Görseller sekmesi).
  const label = h('label', 'kit-btn kit-btn-primary sp-file-label', 'Görsel seç');
  label.setAttribute('for', input.id);

  const drop = h('div', 'sp-drop');
  // SINIR BİLİNMİYORSA O CÜMLE HİÇ YAZILMAZ. `bytes(0)` "0 B" döndürüyor ve
  // "dosya başına en çok 0 B · önerilen en az 0×0" olgusal olarak YANLIŞ bir
  // cümledir — kullanıcı sınırı okuduğunu sanır, oysa panel sınırı bilmiyordur.
  const hint = unknownRules
    ? 'Görsel kuralları okunamadı; dosyalar ancak mağazada denetlenecek.'
    : `${(rules.accept || []).map((item) => item.replace('image/', '').toUpperCase()).join(' · ')}`
      + ` · dosya başına en çok ${bytes(rules.maxBytes)}`
      + (rules.minWidth ? ` · önerilen en az ${rules.minWidth}×${rules.minHeight}` : '')
      + (limit ? ` · ürün açarken en çok ${limit} dosya` : '');
  drop.append(
    h('div', 'sp-drop-text', 'Ürün görsellerini buraya sürükleyip bırakın'),
    h('div', 'sp-sub', hint),
  );
  if (unknownRules) {
    // Yeşil "Gönderilmeye hazır" izlenimi verilmez: seçilen dosya burada
    // denetlenmemiştir ve reddi ancak ürün açılırken mağazadan dönecektir.
    // Sunucu tarafı (`routes.py` + `images.inspect_upload`) reddi yine yapıyor,
    // yani veri kaybı yok — kaybolan şey erken uyarı.
    node.append(alertBox('Görsel kuralları okunamadı; dosyalar burada denetlenmeden '
      + 'listelenir ve ancak mağazada reddedilebilir. “Yenile” ile kuralları yeniden '
      + 'çekebilirsiniz.', 'warn'));
  }

  async function accept(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;

    // REDDEDİLEN DOSYA LİSTEYE HİÇ GİRMEZ ve sebebi kendi satırında yazar.
    // Denetim `inspectFile` ile yapılır — Görseller sekmesiyle AYNI kurallar,
    // ikinci bir kopya değil.
    for (const file of files) {
      const report = await inspectFile(file, rules);   // eslint-disable-line no-await-in-loop
      if (!report.ok) {
        log.append(fileLine(report, 'bad', report.error));
        continue;
      }
      if (limit && picked.length >= limit) {
        log.append(fileLine(report, 'bad',
          `Ürün açarken en çok ${limit} görsel gönderilebilir; bu dosya listeye alınmadı. `
          + 'Kalanları ürün açıldıktan sonra Görseller sekmesinden ekleyin — orada sayı '
          + 'sınırı yok.'));
        continue;
      }
      picked.push({ file, url: URL.createObjectURL(file), report });
      if (report.warnings.length) log.append(fileLine(report, 'warn', report.warnings.join(' ')));
    }
    input.value = '';                 // aynı dosya tekrar seçilebilsin
    paint();
  }

  input.addEventListener('change', () => accept(input.files));
  drop.addEventListener('dragover', (event) => {
    event.preventDefault();
    drop.classList.add('over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    accept(event.dataTransfer?.files);
  });

  const tools = h('div', 'sp-actions');
  tools.append(input, label);
  node.append(drop, tools, grid, log);
  paint();

  return {
    node,
    count: () => picked.length,
    /** Gövdeye girecek dosyalar — base64 BURADA üretilir, seçim anında değil. */
    async payload() {
      const out = [];
      for (const entry of picked) {
        // SIRAYLA okunur: altı dosyayı aynı anda belleğe açmak, tam da
        // kaçınılmak istenen 30 MB'lık tepe noktasını üretirdi.
        const content = await readAsDataUrl(entry.file);   // eslint-disable-line no-await-in-loop
        out.push({ filename: entry.file.name, mime: entry.file.type || '', content });
      }
      return out;
    },
    destroy: release,
  };
}

async function newProduct(done) {
  const forms = [];
  // Görsel önizlemelerinin nesne URL'leri de çekmece kapanınca bırakılır;
  // yükleyici aşağıda kuruluyor, tutamağı buraya yazıyor.
  let dropShots = () => {};
  const dropForms = () => {
    forms.forEach((form) => form.destroy());
    forms.length = 0;
    dropShots();
  };
  const box = drawer(nodes.root, {
    title: 'Yeni ürün',
    subtitle: 'Boş bıraktığınız alanları ekran doldurur',
    onClose: dropForms,
  });
  closers.push(dropForms);

  let plan = null;
  // Ekranın en son OTOMATİK yazdığı değerler. Kullanıcının kendi yazdığını
  // ayırt etmek için gerekiyor: form "kirli mi" bilgisi programla yazılan
  // değeri de kirli sayıyor ve otomatik değer bir daha tazelenmezdi.
  const applied = {};
  const typed = new Set();
  let applying = false;

  const sources = state.reference.sources || [];
  const autoBox = h('div', 'sp-auto');
  const warnBox = h('div', 'sp-auto-warn');
  let lastPicked = [];

  const picker = createPicker({
    items: (state.reference.categories || []).map((item) => ({
      id: item.id,
      name: item.label,
      group: item.depth === 0 ? 'Ana kategoriler' : 'Alt kategoriler',
    })),
    groupLabel: 'Düzey',
    placeholder: 'Kategori ara',
    onChange: (ids) => {
      // Seçim BÜYÜDÜYSE taslak yenilenir (yeni yaprağın üstleri eklensin).
      // KÜÇÜLDÜYSE yenilenmez: kullanıcının çıkardığı üst kategoriyi sunucu
      // yeniden ekler ve seçim geri alınamaz hâle gelirdi.
      const before = new Set(lastPicked);
      lastPicked = ids.map(String);
      if (ids.some((id) => !before.has(String(id)))) schedulePlan();
      else paintAuto();
    },
  });

  const form = formGrid({
    fields: [
      { key: 'sku', label: 'Stok kodu (SKU)', type: 'text', required: true, maxLength: 64,
        hint: 'Bu ürünü diğerlerinden ayıran tekil kod — barkod ya da kendi kodunuz. '
          + 'Sonradan değiştirmek eski bağlantıları kırabilir, baştan doğru yazın.' },
      { key: 'name', label: 'Ürün adı', type: 'text', required: true, maxLength: 180,
        wide: true,
        hint: 'Müşterinin göreceği ad. Sayfa adresi ve Google başlığı bundan üretilir.' },
      { key: 'price', label: 'Satış fiyatı', type: 'money',
        hint: 'Müşterinin ödeyeceği tutar. BOŞ BIRAKIRSANIZ fiyat hiç yazılmaz; 0 yazmak '
          + 'ise “bedava” demektir. İkisi aynı şey değil.' },
      { key: 'stock', label: 'Kaç adet var?', type: 'number', min: 0,
        hint: 'Boş bırakırsanız 0 yazılır ve ürün “tükendi” olarak doğar.' },
      // Depo ve vergi kategorisi YALNIZ birden çok seçenek varken çizilir.
      // Karar sunucudan gelir; "mağazada tek depo var" burada varsayılmaz.
      ...choiceField('sourceId', 'Stok hangi depoya yazılsın'),
      ...choiceField('taxCategoryId', 'Vergi grubu'),
      { key: 'urlKey', label: 'Sayfa adresi', type: 'text', maxLength: 180, wide: true,
        hint: 'Ürünün sitedeki adresinin son parçası: bbdstore.com.tr/BURASI. Boş '
          + 'bırakın, ürün adından kendiliğinden üretilsin.' },
      { key: 'shortDescription', label: 'Kısa açıklama', type: 'richtext', wide: true,
        maxLength: 500, placeholder: 'Listede ve ürün kartının üstünde görünen özet.',
        hint: 'Bir iki cümle. Google açıklamasını boş bırakırsanız oraya da bu metin '
          + 'konur.' },
      { key: 'metaTitle', label: 'Google’da görünecek başlık', type: 'text', maxLength: 120,
        wide: true,
        hint: 'Boş bırakın, ürün adından üretilsin.' },
      { key: 'metaDescription', label: 'Google’da görünecek açıklama', type: 'textarea',
        maxLength: 320, wide: true,
        hint: 'Boş bırakın, kısa açıklamadan üretilsin.' },
      // VARSAYILAN KAPALI ve bu bilinçli: yeni ürün önce kontrol edilir,
      // sonra vitrine çıkar. Yarım kalan bir kayıt yanlışlıkla satışa
      // düşmesin.
      { key: 'active', label: 'Ürün hemen satışa çıksın', type: 'checkbox',
        hint: 'İŞARETLEMEMENİZ ÖNERİLİR. İşaretlemezseniz ürün “vitrinde yok” olarak '
          + 'açılır; fiyatını, stoğunu ve fotoğrafını kontrol ettikten sonra listeden '
          + 'vitrine çıkarırsınız.' },
    ],
    // Gizlenen alanın değeri BOŞ gider ve sunucu kendi çözer: panelin
    // gizlediği bir alana değer uydurmak, sunucunun kararını istemciden
    // dayatmak olurdu. Alan görünürse ilk seçenek ön dolu gelir.
    value: { sku: '', name: '', price: null, stock: null,
      sourceId: fieldSpec('sourceId').visible ? (sources[0]?.id ?? '') : '',
      taxCategoryId: '',
      urlKey: '', shortDescription: '', metaTitle: '', metaDescription: '', active: false },
    onChange: (draft) => {
      if (applying) return;
      for (const key of AUTO_FIELDS) {
        // Otomatik alanın üstüne yazıldıysa bir daha ezilmez; boşaltılırsa
        // yeniden otomatiğe döner (kullanıcı "sen doldur" demiş olur).
        const value = String(draft[key] ?? '');
        if (value && value !== String(applied[key] ?? '')) typed.add(key);
        if (!value) typed.delete(key);
      }
      schedulePlan();
    },
  });
  forms.push(form);

  // ------------------------------------------------- gelişmiş alanlar (künye)
  //
  // KATLANIR VE KAPALI BAŞLAR: form yeni ürün için boş açılıyor ve künyenin
  // dokuz alanını her seferinde göstermek asıl işi (SKU + ad + fiyat)
  // gürültüye boğuyordu.
  //
  // KULLANICI YAZDIĞINI GÖRMEDEN KAYDETMEZ — üç kural birlikte:
  //  · dolu alan varsa başlıkta "N alan dolu" damgası durur, bölüm kapalı
  //    olsa bile (`paintBookTag`),
  //  · sunucu taslakta bir künye hatası bulursa bölüm KENDİLİĞİNDEN açılır,
  //  · "Ürünü aç"a basıldığında alanlar geçersizse bölüm açılır ve hata
  //    gösterilir.
  // Zorla açık tutmak yerine damga konması bilinçli: kapatmak kullanıcının
  // kararıdır, gizlenen bilgi değil.
  //
  // ALANLAR PANELDE SABİT DEĞİL: hangi alanın çizileceği ve metin mi seçim mi
  // olduğu sunucudan gelen `bookFieldsOnCreate` ile belirlenir; düzenleme
  // sekmesiyle AYNI tarif, aynı `bookField()` üreteci ve aynı `bookHint()`
  // ipuçları. Liste HEDEF AİLEYE göre süzülmüş gelir: ailenin taşımadığı bir
  // alanı çizmek, kullanıcıya yazacağı yer yokken yazdırmak olurdu.
  const bookSpecs = state.reference.bookFieldsOnCreate || [];
  const bookOpen = bookSpecs.filter((item) => item.available);
  const bookGone = bookSpecs.filter((item) => !item.available);
  const bookBox = h('div');
  let bookForm = null;
  if (bookOpen.length) {
    bookForm = formGrid({
      fields: bookOpen.map((item) => bookField(item, state.reference.desiRules)),
      value: Object.fromEntries(bookOpen.map((item) => [item.key, ''])),
      // Dolu alan sayısı başlığa yazılır; bölüm kapatılsa da yazılanın izi
      // kalır. Ayrıca sayfa sayısı/desi değişince taslak tazelenir — sunucu
      // künyeyi de doğruluyor ve hata kaydet düğmesine bırakılmıyor.
      onChange: () => { paintBookTag(); schedulePlan(); },
    });
    forms.push(bookForm);
    bookBox.append(bookForm.node);
  } else {
    bookBox.append(h('div', 'sp-sub',
      'Mağaza kataloğunda kitap künyesi alanları (ISBN, yazar, yayınevi…) tanımlı değil, '
      + 'bu yüzden burada gösterilemiyor. Sıradaki adım: ürünü şimdi açın; künye '
      + 'alanları tanımlandığında ürünü açıp doldurabilirsiniz.'));
  }
  if (bookGone.length) {
    // ÇÖZÜLEMEYEN ALAN SESSİZCE YOK OLMAZ — düzenleme sekmesindeki kutunun
    // aynısı, aynı fonksiyondan.
    bookBox.append(h('div', 'sp-auto-head', 'Mağazada karşılığı olmayan alanlar'),
      missingBox(bookGone));
  }
  const bookFold = collapsible('Kitap künyesi (isteğe bağlı)',
    'ISBN, yazar, yayınevi, sayfa sayısı, desi… — sonradan da doldurabilirsiniz', bookBox);

  function paintBookTag() {
    const filled = Object.keys(bookDraft()).length;
    bookFold.mark(filled ? `${num(filled)} alan dolu` : '', 'good');
  }

  // ------------------------------------------------------------- görseller
  //
  // KATLANMAZ, HER ZAMAN GÖRÜNÜR. Görselsiz ürün vitrinde tıklanmıyor;
  // yükleyiciyi katlamak, en sık unutulan işi en görünmez yere koymak olurdu.
  // TAVAN SUNUCUDAN GELİR (`maxFilesOnCreate`), panelde sabit değil: aynı sayı
  // hem burada hem şemada yaşasaydı biri değiştiğinde diğeri sessizce eski
  // kalır ve kullanıcı anlamsız bir 422 görürdü. Sunucu söylemediyse panel
  // sınır UYDURMAZ; son sözü şema söyler.
  const shots = imagePicker({
    rules: state.reference.imageRules || {},
    limit: Number((state.reference.imageRules || {}).maxFilesOnCreate) || 0,
  });
  dropShots = () => shots.destroy();

  function bodyOf() {
    const draft = form.draft();
    const body = {
      sku: String(draft.sku || '').trim(),
      type: 'simple',
      name: String(draft.name || '').trim(),
      shortDescription: draft.shortDescription || '',
      categoryIds: picker.selection().map(Number),
      price: draft.price ?? null,
      stock: draft.stock === '' || draft.stock === null || draft.stock === undefined
        ? null : Number(draft.stock),
      sourceId: Number(draft.sourceId) || 0,
      taxCategoryId: Number(draft.taxCategoryId) || 0,
      // İşaretli değilse `null` gider: "seçilmedi" ile "pasif olsun" aynı şey
      // değil — sunucu `null` görünce varsayılanı uygular VE bunu söyler.
      status: draft.active ? true : null,
      // BOŞ KÜNYE ALANI GÖNDERİLMEZ: yeni üründe temizlenecek bir şey yok ve
      // boş alanı yamaya koymak mağazaya gereksiz bir yazma yaptırırdı.
      book: bookDraft(),
    };
    for (const key of AUTO_FIELDS) body[key] = typed.has(key) ? String(draft[key] || '') : '';
    return body;
  }

  function bookDraft() {
    if (!bookForm) return {};
    const draft = bookForm.draft();
    return Object.fromEntries(bookOpen
      .map((item) => [item.key, String(draft[item.key] ?? '').trim()])
      .filter(([, value]) => value !== ''));
  }

  function paintAuto() {
    autoBox.replaceChildren();
    warnBox.replaceChildren();
    if (!plan) return;
    const list = h('ul', 'sp-auto-list');
    for (const key of plan.auto || []) {
      const row = h('li', 'sp-auto-row');
      row.append(badge('otomatik dolduruldu', 'warn'),
        h('span', 'sp-auto-key', AUTO_LABELS[key] || key),
        h('span', 'sp-auto-note', plan.notes?.[key] || ''));
      list.append(row);
    }
    if ((plan.auto || []).length) {
      autoBox.append(h('div', 'sp-auto-head', 'Ekranın doldurduğu alanlar — hepsi değiştirilebilir'),
        list);
    }
    for (const warning of plan.warnings || []) warnBox.append(alertBox(warning, 'warn'));
    if (plan.connected === false) {
      warnBox.append(alertBox('Mağazaya ulaşılamadı; taslak doğrulanamadı.', 'bad'));
    }
    // KÜNYE HATASI KAYDET DÜĞMESİNE BIRAKILMAZ: sunucu taslakta da denetliyor
    // ve hatalı bir ISBN ürün açılmadan ÖNCE söylenir — açılmış bir ürünü
    // geri almanın yolu yok.
    for (const [key, message] of Object.entries(plan.bookErrors || {})) {
      const label = bookSpecs.find((item) => item.key === key)?.label || key;
      warnBox.append(alertBox(`${label}: ${message}`, 'bad'));
      bookFold.open();                       // hatanın olduğu bölüm kapalı kalmaz
    }
  }

  /** Taslağı sunucudan alır ve DOKUNULMAMIŞ alanları tazeler. */
  const schedulePlan = debounce(async () => {
    const body = bodyOf();
    if (!body.name && !body.sku) return;
    let result;
    try {
      result = await call(`${BASE}/products/plan`, { method: 'POST', body });
    } catch (error) {
      warnBox.replaceChildren(alertBox(error.message, 'warn'));
      return;
    }
    plan = result;
    applying = true;
    for (const key of AUTO_FIELDS) {
      // Elle yazılan alan korunur — TEK istisna: mağazada dolu olduğu için
      // numaralandırılan url_key. Kutuda `roman` yazarken `roman-2` yazmak,
      // gösterilenden başkasını kaydetmek olurdu.
      const forced = key === 'urlKey' && result.urlKeyCheck?.changed;
      if (typed.has(key) && !forced) continue;
      applied[key] = result.draft?.[key] ?? '';
      form.set(key, applied[key]);
    }
    applying = false;
    lastPicked = (result.draft?.categoryIds || []).map(String);
    picker.select(result.draft?.categoryIds || []);
    paintAuto();
  }, 450);
  closers.push(() => schedulePlan.cancel());

  const actions = h('div', 'sp-actions');
  actions.append(button('Ürünü aç', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) {
        form.showErrors();
        toast('Kırmızı işaretli alanları doldurun; ne eksik olduğu alanın altında yazıyor.',
          'bad');
        return;
      }
      if (bookForm && !bookForm.valid()) {
        bookForm.showErrors();
        bookFold.open();                    // hatalı alan katlanmış kalmasın
        toast('Kitap künyesinde düzeltilecek bir alan var; bölümü açtım, kırmızı satıra '
          + 'bakın.', 'bad');
        return;
      }
      const body = bodyOf();
      const auto = (plan?.auto || []).map((key) => AUTO_LABELS[key] || key).join(', ');
      const shotCount = shots.count();
      const reason = await askReason({
        title: 'Yeni ürünü oluştur',
        description: `“${body.name}” (${body.sku}) oluşturulacak. Ekranın sizin yerinize `
          + `doldurduğu alanlar: ${auto || 'yok'}. Önce ürün açılır, sonra adı, sayfa `
          + `adresi, Google metinleri, kategorileri, künyesi ve stoğu yazılır; `
          + `${shotCount
            ? `${num(shotCount)} fotoğraf EN SON yüklenir (fotoğraf yükleyebilmek için `
              + 'ürünün önce var olması gerekiyor). '
            : 'ürün FOTOĞRAFSIZ açılır — fotoğrafsız ürün vitrinde neredeyse hiç '
              + 'tıklanmaz. '}`
          + `Ürün ${body.status ? 'DOĞRUDAN SATIŞA ÇIKAR' : 'önce vitrinde görünmez; '
            + 'kontrol edip siz çıkaracaksınız'}.`,
        confirmLabel: 'Oluştur',
      });
      if (!reason) return;
      // Base64 içerik ANCAK BURADA üretilir: form açık dururken altı dosyanın
      // metnini bellekte tutmanın anlamı yok.
      const images = await withBusy('Fotoğraflar hazırlanıyor…', () => shots.payload());
      if (images === null) return;          // okuma patladı, `withBusy` söyledi
      // `call` YERİNE ham `api`: ürün açıldıktan sonra bir adım düşerse yanıt
      // `ok:false` gelir AMA kimlik taşır. `call` bunu istisnaya çevirip
      // kimliği yutar ve kullanıcı mağazada duran ürünü göremezdi.
      const result = await withBusy('Ürün açılıyor…', async () => api(`${BASE}/products`, {
        method: 'POST',
        // Kategori listesi taslakta ZATEN genişletildi ve kullanıcı gördü;
        // yazarken yeniden genişletmek, çıkardığı üst kategoriyi geri koyardı.
        body: { ...body, images, expandParents: false, reason, dryRun: false },
      }));
      if (!result) return;
      if (result.ok === false && !result.id) {
        toast(result.error
          || 'Ürün oluşturulamadı. Sıradaki adım: “Yenile” deyip yeniden deneyin.', 'bad');
        return;
      }
      if (result.ok === false) {
        // Ürün mağazada DURUYOR; yarım kaldığı söylenir ve düzenleyici açılır.
        toast(result.error, 'bad');
        toast('Ürün oluşturuldu AMA bazı bilgileri yazılamadı. Sıradaki adım: birazdan '
          + 'açılacak pencerede eksikleri tamamlayın.', 'warn');
      } else {
        toast('Ürün oluşturuldu.', 'good');
      }
      // GÖRSEL SONUCU DOSYA DOSYA SÖYLENİR: "2 görsel yüklenemedi" demek,
      // kullanıcıya hangisini küçülteceğini söylemiyordu.
      const shotStep = (result.steps || []).find((step) => step.step === 'images');
      if (shotStep && (shotStep.uploaded || []).length) {
        toast(`${num(shotStep.uploaded.length)} fotoğraf yüklendi.`, 'good');
      }
      for (const row of (shotStep?.failed || [])) {
        toast(`“${row.file}” yüklenemedi — ${row.error} Sıradaki adım: ürünü açıp `
          + 'Fotoğraflar sekmesinden yeniden deneyin.', 'bad');
      }
      // Yazma anında url_key kapılmış olabilir; ekranda gördüğünüzden başka
      // bir adresle doğduysa SÖYLENİR.
      const written = result.draft?.urlKey || '';
      if (written && plan?.draft?.urlKey && written !== plan.draft.urlKey) {
        toast('Sayfa adresi son anda değişti — aynı adres başka bir ürün tarafından '
          + `kapılmıştı. Yeni adres: ${written}`, 'warn');
      }
      for (const warning of result.warnings || []) toast(warning, 'warn');
      if (result.notice) toast(result.notice, 'warn');
      box.close();
      done?.();
      if (result.id) openProduct(result.id);
    },
  }));

  box.body.append(
    form.node,
    // Gizlenen tek seçenekli alanlar SESSİZ KALMAZ: hangi değerin
    // kendiliğinden uygulandığı yazar, ikinci seçenek açıldıysa uyarı çıkar.
    choiceNotes(),
    h('div', 'sp-auto-title', 'Kategoriler'),
    picker.node,
    // GÖRSEL KATLANMAZ: en sık unutulan iş en görünür yerde durur.
    card('Ürün görselleri', shots.node,
      'İlk görsel KAPAK olur · ürün açıldıktan sonra sırayla yüklenir'),
    // KÜNYE KATLANIR VE KAPALI BAŞLAR: dokuz alanı her ürün açılışında
    // göstermek asıl işi gürültüye boğuyordu.
    bookFold.node,
    autoBox, warnBox, actions,
    hintBox('Ürün İKİ AŞAMADA doğar: mağaza önce yalnız tip/aile/SKU kabul eder, geri kalanı '
      + 'ürün kimliği belli olunca yazılır. Görsel yükleme ucu da ürün kimliği istediği için '
      + 'görseller EN SON gider — dosyaları şimdi seçersiniz, ürün açılınca sırayla '
      + 'yüklenirler. Ekran bu turları sizin yerinize atar; bir adım düşerse hangisinin '
      + 'düştüğünü söyler ve ürün yarım kalmaz. Seçtiğiniz kategorinin ÜST kategorileri '
      + 'ağaca göre eklenir — vitrinde üst rafta da görünmesi için gerekir; istemediğinizi '
      + 'listeden çıkarabilirsiniz.'),
  );
  schedulePlan();
}

async function copyProduct(product) {
  const reason = await askReason({
    title: 'Ürünü kopyala',
    description: `${product.sku} kopyalanır; kopya PASİF açılır ve SKU'su mağaza tarafından `
      + 'türetilir.',
    confirmLabel: 'Kopyala',
  });
  if (!reason) return;
  await withBusy('Kopyalanıyor…', async () => {
    await call(`${BASE}/products/${product.id}/copy`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast('Kopya oluşturuldu.', 'good');
    refresh();
  });
}

// ================================================================== silme
//
// SİLME GERÇEK SİLMEDİR — pasifleştirme değil, geri alınamaz. Kullanıcının
// kararı: "siparişi gönderildiyse de ürün silinebilir; geçmiş raporda kırmızı
// 'silinmiş' ibaresi koyarız." Karar güvenli çünkü sipariş kalemi ürünün
// adını, SKU'sunu ve fiyatını KENDİ satırında saklıyor ve `order_items`
// tablosunun `products`'a yabancı anahtar kısıtı yok.
//
// EKRANIN GÖREVİ KULLANICIYI BİLGİLENDİRMEK: ne silinecek, kaç siparişte
// geçti, kaç adet satıldı. Bu üçü GÖRÜLMEDEN silme düğmesi açılmaz — sayı
// bilinmiyorsa "bilinmiyor" yazar, sıfır uydurulmaz.

/** Silinen satırları listeden ANINDA düşürür; yeniden yükleme beklenmez. */
function dropRows(ids) {
  const gone = new Set((ids || []).map(String));
  if (!gone.size) return;
  const before = state.items.length;
  state.items = state.items.filter((row) => !gone.has(String(row.id)));
  const removed = before - state.items.length;
  if (!removed) return;
  // Toplam da düşer: "1.419 ürün" yazarken 1.418 satır göstermek, kullanıcıya
  // silmenin gerçekleşmediğini düşündürüyordu.
  state.total = Math.max(0, state.total - removed);
  state.selection = state.selection.filter((id) => !gone.has(String(id)));
  nodes.table?.update({ rows: state.items, empty: emptyNode() });
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  renderSelectionBar();
  nodes.status?.set(statusText(), !state.connected);
}

/** Satış geçmişi hücresi. ÜÇ HÂL: sayı · bilinmiyor · uç yok. */
function salesCell(row) {
  const sales = row.sales || {};
  const box = h('span', 'sp-sales');
  if (sales.state !== 'known') {
    box.append(badge(sales.state === 'unavailable' ? 'okunamıyor' : 'bilinmiyor', 'warn'));
    box.title = sales.note
      || 'Bu ürünün daha önce satılıp satılmadığı okunamadı. Silmeden önce emin olmak '
        + 'için Siparişler ekranından arayın.';
    return box;
  }
  box.append(h('b', undefined, `${num(sales.orderCount)} sipariş`));
  box.append(h('span', 'sp-sub', `${num(sales.soldQty)} adet satıldı`));
  if (sales.lastOrderedAt) box.title = `Son sipariş: ${sales.lastOrderedAt.replace('T', ' ')}`;
  return box;
}

function deleteDialog(productIds, { onDone } = {}) {
  const ids = [...new Set((productIds || []).map(Number))].filter(Boolean);
  if (!ids.length) {
    toast(`${BLOCKERS.NO_SELECTION.why} ${BLOCKERS.NO_SELECTION.next}`, 'warn');
    return;
  }

  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog sp-bulk sp-delete');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title',
    ids.length === 1 ? 'Ürünü sil' : `${num(ids.length)} ürünü sil`));
  box.append(h('p', 'kit-dialog-text',
    'NELERİN SİLİNECEĞİ AŞAGIDA. Bu işlem GERİ ALINAMAZ. Geçmiş siparişleriniz bozulmaz: '
    + 'o siparişlerdeki satırlar yerinde kalır ve kırmızı “silinmiş” ibaresiyle görünür. '
    + 'Ürünü yalnız vitrinden kaldırmak istiyorsanız bu pencereyi kapatıp “Vitrinden '
    + 'kaldır” düğmesini kullanın — o geri alınabilir.'));

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const result = h('div', 'sp-bulk-result');
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Vazgeç', { onClick: close }));
  box.append(result, actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);

  loadPreview();

  async function loadPreview() {
    result.replaceChildren(skeletonRows(Math.min(6, ids.length), 5));
    let preview;
    try {
      preview = await call(`${BASE}/products/delete/preview`, {
        method: 'POST', body: { productIds: ids },
      });
    } catch (error) {
      result.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    paintPreview(preview);
  }

  function paintPreview(preview) {
    result.replaceChildren();
    const summary = preview.summary || {};
    result.append(kpiRow([
      { label: 'Silinecek', value: num(summary.total) },
      { label: 'Şu an satışta', value: num(summary.active),
        tone: summary.active ? 'bad' : 'muted',
        title: 'Bunları müşteri şu anda görüyor; silerseniz vitrinden kaybolurlar.' },
      { label: 'Stoğu olan', value: num(summary.withStock),
        tone: summary.withStock ? 'warn' : 'muted',
        title: 'Deponuzda hâlâ malı olan ürünler; silmeden önce iki kez düşünün.' },
      { label: 'Daha önce satılmış', value: num(summary.sold),
        tone: summary.sold ? 'warn' : 'muted',
        title: 'Geçmiş siparişlerde geçen ürünler. Siparişler bozulmaz ama ürün '
          + 'katalogdan gider.' },
      { label: 'Satışı bilinmeyen', value: num(summary.salesUnknown), tone: 'muted',
        title: 'Satılıp satılmadığı okunamayanlar.' },
    ]));

    const table = dataTable({
      columns: [
        { key: 'sku', label: 'Stok kodu', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
        { key: 'status', label: 'Durum', width: '110px',
          cell: (row) => badge(row.status ? 'Satışta' : 'Vitrinde yok',
            row.status ? 'good' : 'dim') },
        { key: 'stock', label: 'Stok', width: '80px', align: 'num',
          cell: (row) => `${row.stockExact ? '' : '~'}${num(row.stock)}` },
        { key: 'variantCount', label: 'Seçenek', width: '80px', align: 'num',
          cell: (row) => (row.variantCount ? num(row.variantCount) : '—') },
        { key: 'sales', label: 'Daha önce satıldı mı?', width: 'minmax(0, 1.4fr)',
          cell: salesCell },
      ],
      rows: preview.rows,
      dense: true,
      rowKey: (row) => String(row.id),
    });
    result.append(table.node);

    if (preview.missing && preview.missing.length) {
      result.append(alertBox(`${num(preview.missing.length)} ürün mağazadan okunamadı ve `
        + 'bu listeye girmedi. MERAK ETMEYİN: okunamayan ürün silinmez.', 'warn'));
    }
    for (const warning of preview.warnings || []) result.append(alertBox(warning, 'warn'));
    if (!preview.capable) {
      result.append(alertBox(preview.capabilityError, 'bad'));
    }

    actions.replaceChildren(
      button('Vazgeç', { onClick: close }),
      button(preview.rows.length === 1
        ? 'Evet, bu ürünü sil' : `Evet, ${num(preview.rows.length)} ürünü sil`, {
        variant: 'danger',
        disabled: !preview.capable,
        title: preview.capable
          ? 'GERİ ALINAMAZ. Emin değilseniz “Vazgeç” deyin.'
          : preview.capabilityError,
        onClick: () => run(preview),
      }),
    );
  }

  async function run(preview) {
    const sold = (preview.summary || {}).sold || 0;
    // Elli SKU'yu onay kutusuna sığdırmak metni okunmaz yapıyor; ilk beşi
    // yazılır, gerisi sayıyla söylenir. Tam liste zaten yukarıdaki tabloda.
    const skus = preview.rows.map((row) => row.sku);
    const named = skus.length > 5
      ? `${skus.slice(0, 5).join(', ')} ve ${num(skus.length - 5)} ürün daha`
      : skus.join(', ');
    const reason = await askReason({
      title: preview.rows.length === 1 ? 'Ürünü sil' : `${num(preview.rows.length)} ürünü sil`,
      description: `${named} katalogdan silinecek. `
        + (sold ? `Bunlardan ${num(sold)} tanesi daha önce satılmış; o siparişlerin `
          + 'satırları yerinde kalır ve “silinmiş” ibaresiyle görünür. ' : '')
        + 'BU İŞLEM GERİ ALINAMAZ — silinen ürün geri getirilemez.',
      confirmLabel: 'Sil',
    });
    if (!reason) return;
    // `call` YERİNE ham `api`: kısmi başarıda yanıt `ok:false` gelir AMA
    // silinenlerin listesini taşır. `call` bunu istisnaya çevirseydi silinen
    // ürünler ekranda durmaya devam eder ve kullanıcı ikinci kez silmeye
    // çalışırdı.
    const outcome = await withBusy('Siliniyor…', async () => api(`${BASE}/products/delete`, {
      method: 'POST', body: { productIds: ids, reason, dryRun: false },
    }));
    if (!outcome) return;

    // KURU PROVADA SATIR DÜŞMEZ: ürün mağazada duruyor, listeden kaldırmak
    // silinmiş gibi göstermek olurdu. Panel `dryRun: false` yolluyor ama
    // geçidin acil freni de kuru provaya düşürebiliyor.
    const removed = outcome.dryRun ? [] : [...(outcome.deleted || []),
      ...((outcome.missing || []).map((row) => row.id))];
    dropRows(removed);
    close();
    if (outcome.dryRun && (outcome.deleted || []).length) {
      toast('DENEME yapıldı: mağazaya hiçbir şey gönderilmedi, ürünler yerinde duruyor.',
        'warn');
    }

    if (!outcome.dryRun && outcome.deleted && outcome.deleted.length) {
      toast(`${num(outcome.deleted.length)} ürün silindi.`, 'good');
      // KPI şeridi katalog sayılarını gösteriyor; silme sonrası tazelenmezse
      // "1.419 ürün" yazmaya devam eder. Liste ZATEN anında düştü, bu yalnız
      // şeridi düzeltir ve arka planda olur.
      loadHealth();
    }
    for (const row of outcome.missing || []) {
      toast(`#${row.id} zaten yoktu; listeden düşürüldü.`, 'warn');
    }
    // KISMİ BAŞARI TEK TEK SÖYLENİR: "3 üründen 1'i silinemedi" demek,
    // hangisinin ve neden kaldığını gizlerdi.
    for (const row of outcome.failed || []) {
      toast(`“${row.sku || row.id}” silinemedi — ${row.error} Sıradaki adım: “Yenile” `
        + 'deyip bu ürünü tek başına silmeyi deneyin.', 'bad');
    }
    if (outcome.notice && (outcome.deleted || []).length) toast(outcome.notice, 'warn');
    onDone?.(outcome);
  }
}

function paintPrice(pane, payload, forms) {
  const product = payload.product;
  const price = payload.price;
  const priceless = ['configurable', 'bundle', 'grouped'].includes(product.type);

  if (priceless) pane.append(blockerBox('PRICE_ON_VARIANTS'));

  const form = formGrid({
    fields: [
      { key: 'price', label: 'Satış fiyatı', type: 'money', readOnly: priceless,
        hint: 'Müşterinin normalde ödeyeceği tutar (KDV dâhil).' },
      { key: 'cost', label: 'Alış fiyatı (size kaça mal oldu)', type: 'money',
        readOnly: priceless,
        hint: 'MÜŞTERİ BUNU GÖRMEZ. Kâr hesabı bundan çıkar; boş bırakırsanız kâr '
          + 'gösterilemez.' },
      { key: 'specialPrice', label: 'İndirimli fiyat', type: 'money', readOnly: priceless,
        hint: 'Kampanya fiyatı. Boş bırakırsanız indirim yok demektir.' },
      { key: 'specialFrom', label: 'İndirim ne zaman başlasın?', type: 'date',
        hint: 'Boş bırakırsanız hemen başlar.' },
      { key: 'specialTo', label: 'İndirim ne zaman bitsin?', type: 'date',
        hint: 'Boş bırakırsanız siz kaldırana kadar sürer.' },
    ],
    value: {
      price: price.price, cost: price.cost, specialPrice: price.specialPrice,
      specialFrom: price.specialFrom, specialTo: price.specialTo,
    },
    onChange: () => paintMargin(),
  });
  forms.push(form);

  const margin = h('div', 'sp-margin');
  function paintMargin() {
    const draft = form.draft();
    margin.replaceChildren();
    const listed = Number(draft.price || 0);
    const cost = draft.cost === null || draft.cost === undefined ? null : Number(draft.cost);
    margin.append(h('span', 'sp-sub', 'Bu üründen kazancınız: '));
    if (!listed || cost === null) {
      margin.append(h('b', undefined, '—'));
      margin.append(h('span', 'sp-sub',
        ' — hesaplanamıyor. Sıradaki adım: yukarıdaki “Alış fiyatı” kutusunu doldurun.'));
      return;
    }
    const ratio = ((listed - cost) * 100) / listed;
    margin.append(h('b', ratio < 0 ? 'sp-bad' : '', money(listed - cost)));
    margin.append(h('span', 'sp-sub',
      ` · satış fiyatının ${percent(Math.round(ratio * 10) / 10)} kadarı kâr`));
    if (ratio < 0) {
      margin.append(h('span', 'sp-sub',
        ' · DİKKAT: bu ürünü zararına satıyorsunuz.'));
    }
  }
  paintMargin();

  const specialNote = {
    none: 'Bu üründe indirim tanımlı değil.',
    active: 'İNDİRİM BUGÜN GEÇERLİ — müşteri indirimli fiyatı ödüyor.',
    scheduled: 'İndirim ileri tarihli; başlangıç günü gelmedi, müşteri normal fiyatı ödüyor.',
    expired: 'İndirimin süresi doldu; müşteri yeniden normal fiyatı ödüyor.',
  }[price.specialState];

  const groups = h('div', 'sp-group-prices');
  groups.append(h('div', 'sp-sub',
    'Belirli müşteri gruplarına özel fiyatlar (örneğin okullara toplu alım fiyatı)'));
  if (!price.groupPrices.length) {
    groups.append(h('div', 'sp-sub', 'Özel fiyat tanımlanmamış; herkes aynı fiyatı ödüyor.'));
  } else {
    for (const item of price.groupPrices) {
      const line = h('div', 'sp-group-row');
      line.append(
        h('b', undefined, item.groupName),
        h('span', 'sp-sub', `${item.qty}+ adet`),
        h('span', 'sp-sub', item.kind === 'discount' ? `%${item.raw} indirim` : money(item.value)),
      );
      groups.append(line);
    }
  }

  const actions = h('div', 'sp-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (priceless) { toast('Bu tipte fiyat buradan yazılmaz.', 'bad'); return; }
      const result = await saveProduct(product.id, form.patch(), {
        title: 'Fiyatı güncelle',
        description: `${product.sku} · liste ${money(price.price)} → `
          + `${money(form.draft().price)}. Gerekçe denetim kaydına yazılır.`,
      });
      if (result) form.reset(form.draft());
    },
  }));

  pane.append(
    card('Fiyat', form.node, specialNote),
    margin,
    groups,
    actions,
    hintBox('Fiyat tek alan değildir: liste fiyatı, tarih pencereli indirimli fiyat ve '
      + 'müşteri grubu fiyatları birlikte çalışır. Vitrinde müşterinin gördüğü tutar '
      + `bugün: ${money(price.effective)}.`),
  );
}

function paintStock(pane, payload, forms) {
  const product = payload.product;
  const rows = payload.inventories;
  if (!rows.length) {
    pane.append(emptyState({
      title: 'Envanter kaynağı yok',
      text: 'Bu ürün hiçbir depoya bağlı değil; stok yazılamaz. Mağazada envanter '
        + 'kaynağı tanımlanmalı.',
    }));
    return;
  }

  const fields = rows.map((row) => ({
    key: `src_${row.sourceId}`,
    label: row.sourceName,
    type: 'number',
    min: 0,
    hint: 'Mutlak adet — fark değil.',
  }));
  const value = {};
  for (const row of rows) value[`src_${row.sourceId}`] = row.quantity;

  const total = h('div', 'sp-total');
  const form = formGrid({
    fields, value, onChange: () => paintTotal(),
  });
  forms.push(form);

  function paintTotal() {
    const draft = form.draft();
    const sum = rows.reduce((acc, row) => acc + Number(draft[`src_${row.sourceId}`] || 0), 0);
    total.replaceChildren(
      h('span', 'sp-sub', 'Toplam: '),
      h('b', undefined, num(sum)),
      h('span', 'sp-sub', ` · kritik eşik ${num(payload.threshold)}`),
    );
  }
  paintTotal();

  const actions = h('div', 'sp-actions');
  actions.append(button('Stok yaz', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      const quantities = {};
      for (const row of rows) {
        const next = Number(draft[`src_${row.sourceId}`] ?? row.quantity);
        if (next !== row.quantity) quantities[String(row.sourceId)] = next;
      }
      if (!Object.keys(quantities).length) { toast('Değişen depo yok.', 'warn'); return; }
      const reason = await askReason({
        title: 'Stok yaz',
        description: `${product.sku} · ${Object.keys(quantities).length} depo güncellenecek. `
          + 'Değerler MUTLAKTIR: yazdığınız sayı deponun yeni adedi olur, mevcut adede '
          + 'eklenmez.',
        confirmLabel: 'Stoğu yaz',
      });
      if (!reason) return;
      await withBusy('Stok yazılıyor…', async () => {
        await call(`${BASE}/products/${product.id}/stock`, {
          method: 'POST', body: { quantities, reason, dryRun: false },
        });
        toast('Stok yazıldı.', 'good');
        refresh();
      });
    },
  }));

  pane.append(
    card('Depo bazlı stok', form.node),
    total,
    actions,
    hintBox('Stok ürünün üzerinde değil envanter kaynaklarında durur. Vitrindeki '
      + '“adet” alanı bunlardan TÜRETİLİR ve geç güncellenir; doğru sayı buradadır.'),
  );
}

// ------------------------------------------------------------ görsel yükleme
//
// KURAL: REDDEDİLEN DOSYA HİÇ GÖNDERİLMEZ. Sunucunun ürün görseli sınırı
// 4 MB'dır (AdminCatalogProductImageProcessor::MAX_BYTES) ve aşan dosyayı 422
// ile geri çevirir. 6 MB'lık bir dosyayı yine de göndermek: kullanıcıyı
// yüklemenin bitmesini beklerken tutar, hız kovasından (dakikada 60 istek) pay
// harcar ve karşılığında "Mağaza isteği doğrulayamadı" gibi hiçbir şey
// anlatmayan bir metin döndürür. Denetim burada, dosya seçilir seçilmez.
//
// Bu denetim YETKİLENDİRME DEĞİLDİR (K9): backend `images.inspect_upload` ile
// aynı kararları içerikten tekrar verir — uzantı yalan söyleyebilir, eski bir
// panel sürümü açık kalmış olabilir. Buradaki denetim HIZ içindir.
//
// ÇÖZÜNÜRLÜK ENGEL DEĞİL UYARIDIR: küçük görsel yüklenebilir, ama ne olacağı
// somut söylenir ("yüklenen 320×240 — listede bulanık görünür"), "görsel
// küçük" gibi kullanıcının ne yapacağını bilemeyeceği bir cümleyle değil.

/** `File` → `data:` URI. Tauri kabuğunda fs eklentisi yok; tek yol budur. */
function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`\`${file.name}\` okunamadı; dosya taşınmış ya da `
      + 'erişim kapanmış olabilir.'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(file);
  });
}

/** Ölçüyü tarayıcıya ölçtürür. Okunamazsa `null` — sıfır UYDURULMAZ. */
function measureImage(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const probe = new Image();
    const finish = (size) => { URL.revokeObjectURL(url); resolve(size); };
    probe.onload = () => finish({ width: probe.naturalWidth, height: probe.naturalHeight });
    probe.onerror = () => finish(null);
    probe.src = url;
  });
}

function ratioText(width, height) {
  const divisor = (function gcd(a, b) { return b ? gcd(b, a % b) : a; }(width, height)) || 1;
  const shortW = Math.round(width / divisor);
  const shortH = Math.round(height / divisor);
  if (shortW <= 20 && shortH <= 20) return `${shortW}:${shortH}`;
  return width >= height
    ? `${(width / height).toFixed(1).replace('.', ',')}:1`
    : `1:${(height / width).toFixed(1).replace('.', ',')}`;
}

/**
 * Tek dosyanın kararı: gönderilir mi, gönderilirse kullanıcı neyi bilmeli.
 * `{file, ok, error, warnings, width, height}` döner; ASLA istek atmaz.
 */
async function inspectFile(file, rules) {
  const accept = rules.accept || [];
  const maxBytes = Number(rules.maxBytes) || 0;
  const mime = String(file.type || '').toLowerCase();

  if (accept.length && !accept.includes(mime)) {
    const label = mime ? mime.replace('image/', '').toUpperCase() : 'tanınmayan tür';
    return { file, ok: false, warnings: [],
      error: `${label} kabul edilmiyor; kabul edilenler `
        + `${accept.map((item) => item.replace('image/', '').toUpperCase()).join(', ')}. `
        + 'Görseli bu biçimlerden birine çevirin — istek mağazaya gönderilmedi.' };
  }
  if (maxBytes && file.size > maxBytes) {
    return { file, ok: false, warnings: [],
      error: `Dosya ${bytes(file.size)}; bu ekranın sınırı ${bytes(maxBytes)} `
        + '(mağazanın kendi sınırı). Görseli küçültüp yeniden deneyin — istek mağazaya '
        + 'gönderilmedi.' };
  }
  if (!file.size) {
    return { file, ok: false, warnings: [], error: 'Dosya boş.' };
  }

  const size = await measureImage(file);
  if (!size) {
    return { file, ok: true, width: 0, height: 0,
      warnings: ['Görselin ölçüsü tarayıcıda okunamadı; dosya yarım inmiş olabilir. '
        + 'Yüklendikten sonra ürün sayfasında gözle doğrulayın.'] };
  }

  const { width, height } = size;
  const warnings = [];
  const minWidth = Number(rules.minWidth) || 0;
  const minHeight = Number(rules.minHeight) || 0;
  const maxRatio = Number(rules.maxRatio) || 0;
  if ((minWidth && width < minWidth) || (minHeight && height < minHeight)) {
    warnings.push(`Önerilen en az ${minWidth}×${minHeight}; yüklenen ${width}×${height} — `
      + 'listede ve ürün sayfasında bulanık görünür.');
  }
  const longSide = Math.max(width, height);
  const shortSide = Math.min(width, height);
  if (maxRatio && longSide > shortSide * maxRatio) {
    warnings.push(`Görsel ${ratioText(width, height)} oranında (${width}×${height}); vitrin `
      + 'ızgarası kareye yakın görsel bekler, kenarlardan kırpılır.');
  }
  return { file, ok: true, width, height, warnings };
}

/** Dosya başına tek satır: ad · boyut · ölçü · karar. Renk tek başına konuşmaz. */
function fileLine(report, tone, text) {
  const line = h('div', `sp-file-line ${tone}`);
  const size = report.width ? `${report.width}×${report.height}` : 'ölçü okunamadı';
  line.append(
    h('b', undefined, report.file.name),
    h('span', 'sp-sub', `${bytes(report.file.size)} · ${size}`),
    badge({ bad: 'Reddedildi', warn: 'Uyarılı', good: 'Yüklendi', dim: 'Bekliyor' }[tone] || tone,
      tone),
    h('span', 'sp-file-why', text),
  );
  return line;
}

function paintImages(pane, payload) {
  const product = payload.product;
  let images = [...payload.images];
  let rules = payload.imageRules || {};
  let order = images.map((item) => item.id);

  const list = h('div', 'sp-images');
  const empty = h('div');
  const log = h('div', 'sp-file-log');
  const bar = h('div', 'sp-progress');
  const reorderActions = h('div', 'sp-actions');

  function paintList() {
    list.replaceChildren();
    empty.replaceChildren();
    reorderActions.replaceChildren();
    if (!images.length) {
      empty.append(alertBox(
        'Bu ürünün hiç fotoğrafı yok. Fotoğrafsız ürün vitrinde neredeyse hiç tıklanmıyor. '
        + 'Sıradaki adım: aşağıdan en az bir fotoğraf ekleyin; ilk eklediğiniz kapak olur.',
        'warn'));
      return;
    }
    order.forEach((id, index) => {
      const item = images.find((image) => image.id === id);
      if (!item) return;
      const cell = h('div', `sp-image${index === 0 ? ' cover' : ''}`);
      const picture = h('img');
      picture.loading = 'lazy';
      picture.src = item.url;
      picture.alt = '';
      cell.append(picture);
      cell.append(h('span', 'sp-image-tag', index === 0 ? 'Kapak' : `#${index + 1}`));

      // Sürükle-bırak tek yol OLAMAZ: ok tuşlarıyla da taşınır (klavye erişimi).
      const move = (step) => {
        const target = index + step;
        if (target < 0 || target >= order.length) return;
        const next = [...order];
        [next[index], next[target]] = [next[target], next[index]];
        order = next;
        paintList();
      };
      const tools = h('div', 'sp-image-tools');
      tools.append(
        button('◀', { variant: 'ghost', title: 'Sola taşı', onClick: () => move(-1) }),
        button('▶', { variant: 'ghost', title: 'Sağa taşı', onClick: () => move(1) }),
        button('Kaldır', {
          variant: 'danger',
          onClick: () => removeImage(product, item, reload),
        }),
      );
      cell.append(tools);
      list.append(cell);
    });

    if (images.length > 1) {
      reorderActions.append(button('Sırayı kaydet', {
        variant: 'primary',
        onClick: async () => {
          const reason = await askReason({
            title: 'Görsel sırasını kaydet',
            description: 'Listenin ilk görseli KAPAK olur ve vitrinde/listede o görünür.',
            confirmLabel: 'Sırayı kaydet',
          });
          if (!reason) return;
          await withBusy('Sıra kaydediliyor…', async () => {
            await call(`${BASE}/products/${product.id}/images/reorder`, {
              method: 'POST', body: { order, reason, dryRun: false },
            });
            toast('Görsel sırası kaydedildi.', 'good');
          });
        },
      }));
    }
  }

  /** Yükleme/kaldırma sonrası TEK istekle tazeler — çekmecenin tamamı değil.
   *
   * Taze liste ÇEKMECENİN YÜKÜNE de yazılır. Yazılmasaydı sekme değiştirip
   * geri gelen kullanıcı, açılışta çekilmiş eski listeyi görürdü: yeni
   * yüklediği görsel kaybolmuş gibi görünür ve ikinci kez yüklerdi.
   */
  async function reload() {
    try {
      const fresh = await call(`${BASE}/products/${product.id}/images`);
      images = fresh.images || [];
      rules = fresh.rules || rules;
      order = images.map((item) => item.id);
      payload.images = images;
      payload.imageRules = rules;
      paintList();
    } catch (error) {
      log.append(alertBox(`Görsel listesi tazelenemedi: ${error.message} `
        + 'Çekmeceyi kapatıp yeniden açın.', 'warn'));
    }
  }

  // ------------------------------------------------------------- yükleyici
  const input = h('input', 'sp-file');
  input.type = 'file';
  input.multiple = true;                       // çoklu seçim; yükleme SIRAYLA
  input.accept = (rules.accept || []).join(',');
  input.id = `sp-file-${product.id}`;
  // Görsel olarak gizli ama KLAVYEYLE ULAŞILIR: `display:none` verilseydi sekme
  // tuşuyla erişilemez ve dosya seçmenin klavye yolu hiç kalmazdı.
  const label = h('label', 'kit-btn kit-btn-primary sp-file-label', 'Görsel ekle');
  label.setAttribute('for', input.id);

  const drop = h('div', 'sp-drop');
  drop.append(
    h('div', 'sp-drop-text', 'Görselleri buraya sürükleyip bırakın'),
    h('div', 'sp-sub',
      `${(rules.accept || []).map((item) => item.replace('image/', '').toUpperCase()).join(' · ')}`
      + ` · dosya başına en çok ${bytes(rules.maxBytes || 0)}`
      + ` · önerilen en az ${rules.minWidth || 0}×${rules.minHeight || 0}`),
  );

  const tools = h('div', 'sp-actions');
  // Sıra önemli: gizli girdi ETİKETTEN ÖNCE gelir, yoksa odak halkasını etikete
  // taşıyan kardeş seçici (.sp-file:focus-visible + .sp-file-label) eşleşmez ve
  // klavye kullanıcısı nereye bastığını göremez.
  tools.append(input, label);

  async function accept(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;
    if (busy) { toast('Önceki iş bitmeden yeni yükleme başlatılmaz.', 'warn'); return; }

    log.replaceChildren();
    bar.replaceChildren();

    // 1. ADIM — HİÇBİR İSTEK ATILMADAN önce hepsi tek tek incelenir ve
    // reddedilen dosyanın NEDEN reddedildiği kendi satırında yazılır.
    // Toplu "3 dosya reddedildi" mesajı kullanıcıya hangisini küçülteceğini
    // söylemiyordu.
    const reports = [];
    for (const file of files) {
      reports.push(await inspectFile(file, rules));      // eslint-disable-line no-await-in-loop
    }
    const accepted = reports.filter((item) => item.ok);
    for (const item of reports) {
      if (!item.ok) log.append(fileLine(item, 'bad', item.error));
      else if (item.warnings.length) log.append(fileLine(item, 'warn', item.warnings.join(' ')));
      else log.append(fileLine(item, 'dim', 'Gönderilmeye hazır.'));
    }
    if (!accepted.length) {
      toast(`${num(reports.length)} dosyanın hiçbiri gönderilmedi.`, 'bad');
      input.value = '';
      return;
    }

    const reason = await askReason({
      title: 'Görsel yükle',
      description: `${product.sku} · ${num(accepted.length)} dosya SIRAYLA yüklenecek`
        + (reports.length !== accepted.length
          ? ` (${num(reports.length - accepted.length)} dosya yukarıdaki sebeplerle hiç `
            + 'gönderilmiyor)' : '')
        + '. Yarıda kalırsa o ana kadar yüklenenler mağazada kalır; hangisinin gittiği '
        + 'aşağıda tek tek yazar.',
      confirmLabel: 'Yükle',
    });
    if (!reason) { input.value = ''; return; }

    const steps = accepted.map((item, index) =>
      `${index + 1}/${accepted.length} · ${item.file.name}`);
    const meter = progress(steps);
    bar.replaceChildren(meter.node);

    await withBusy('Görseller yükleniyor…', async () => {
      let done = 0;
      const failures = [];
      for (let index = 0; index < accepted.length; index += 1) {
        const entry = accepted[index];
        meter.step(index);
        try {
          /* eslint-disable no-await-in-loop */
          // SIRAYLA — paralel değil: mağaza dakikada 60 istek kabul ediyor ve
          // her yüklemenin kendi denetim satırı, kendi istek kimliği olmalı.
          const content = await readAsDataUrl(entry.file);
          const result = await call(`${BASE}/products/${product.id}/images`, {
            method: 'POST',
            body: {
              filename: entry.file.name, mime: entry.file.type || '', content,
              reason, dryRun: false,
            },
          });
          /* eslint-enable no-await-in-loop */
          done += 1;
          const notes = [...(result.warnings || [])];
          entry.line = fileLine(entry, notes.length ? 'warn' : 'good',
            notes.join(' ') || 'Mağazaya yüklendi.');
        } catch (error) {
          failures.push(entry.file.name);
          entry.line = fileLine(entry, 'bad', error.message);
        }
      }
      // Satırlar yeniden çizilir: "hazır" damgaları sonuçla değişir.
      log.replaceChildren(...reports.map((item) => item.line
        || fileLine(item, 'bad', item.error || 'Gönderilmedi.')));
      meter.done(`${num(done)}/${num(accepted.length)} dosya yüklendi`);
      toast(`${num(done)} görsel yüklendi`
        + (failures.length ? ` · ${num(failures.length)} başarısız` : ''),
      failures.length ? 'warn' : 'good');
      await reload();
      if (done) refresh();          // liste küçük resmi ve fotoğrafsız süzgeci değişti
    });
    input.value = '';               // aynı dosya tekrar seçilebilsin
  }

  input.addEventListener('change', () => accept(input.files));
  drop.addEventListener('dragover', (event) => {
    event.preventDefault();
    drop.classList.add('over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    accept(event.dataTransfer?.files);
  });

  const uploader = h('div', 'sp-uploader');
  uploader.append(drop, tools, bar, log);

  paintList();
  pane.append(
    empty, list, reorderActions,
    card('Görsel ekle', uploader),
    hintBox('Sunucunun sınırı dosya başına 4 MB\'dır; aşan dosya mağazaya HİÇ gönderilmez, '
      + 'sebebi burada tek tek yazar. Çözünürlük uyarısı yüklemeyi ENGELLEMEZ — küçük '
      + 'görsel yüklenir, yalnız listede ve ürün sayfasında bulanık görüneceği söylenir.'),
  );
}

async function removeImage(product, image, done) {
  const reason = await askReason({
    title: 'Görseli kaldır',
    description: 'Bu fotoğraf mağazadan silinir ve GERİ ALINAMAZ. Ürünün kendisi ve '
      + 'geçmiş siparişler etkilenmez.',
    confirmLabel: 'Görseli kaldır',
  });
  if (!reason) return;
  await withBusy('Görsel kaldırılıyor…', async () => {
    await call(`${BASE}/products/${product.id}/images/${image.id}/remove`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast('Görsel kaldırıldı.', 'good');
    await done?.();
    refresh();
  });
}

function paintVariants(pane, payload) {
  const rows = payload.variants;
  if (!rows.length) {
    pane.append(emptyState({
      title: 'Bu üründe seçenek yok',
      text: 'Bu ürün tek çeşit satılıyor — renk, beden gibi seçenekleri yok. Seçenekli '
        + 'ürünler (aynı kitabın ciltli/karton kapak gibi çeşitleri) mağaza yönetiminden '
        + 'tanımlanır.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'sku', label: 'Stok kodu', width: 'minmax(0, 1.2fr)', className: 'mono' },
      { key: 'name', label: 'Seçenek', width: 'minmax(0, 2fr)' },
      { key: 'price', label: 'Fiyat', width: '120px', align: 'num', cell: priceCell },
      { key: 'stock', label: 'Stok', width: '120px', align: 'num', cell: stockCell },
      { key: 'status', label: 'Durum', width: '110px',
        cell: (row) => badge(row.status ? 'Satışta' : 'Vitrinde yok',
          row.status ? 'good' : 'dim') },
    ],
    rows,
    dense: true,
    onRow: (row) => openProduct(row.id),
  });
  pane.append(table.node,
    hintBox('Bu ürünün fiyatı ve stoğu SEÇENEKLERİNDE tutulur — yukarıdaki satırlara '
      + 'tıklayıp tek tek düzenlersiniz. Ana ürüne fiyat yazmak, vitrinde hiç '
      + 'görünmeyen bir değer üretir.'));
}

function paintCategories(pane, payload) {
  const product = payload.product;
  const before = [...product.categoryIds].sort((a, b) => a - b).join(',');
  const picker = createPicker({
    items: state.reference.categories.map((item) => ({
      id: item.id,
      name: item.label,
      group: item.depth === 0 ? 'Ana kategoriler' : 'Alt kategoriler',
    })),
    groupLabel: 'Düzey',
    placeholder: 'Kategori ara',
  });
  picker.select(product.categoryIds);

  const actions = h('div', 'sp-actions');
  actions.append(button('Kategorileri kaydet', {
    variant: 'primary',
    onClick: async () => {
      const ids = picker.selection().map(Number);
      if (ids.slice().sort((a, b) => a - b).join(',') === before) {
        toast('Kategori seçimi değişmedi.', 'warn');
        return;
      }
      const reason = await askReason({
        title: 'Kategorileri kaydet',
        description: `${product.sku} · ${ids.length} kategori atanacak. Liste TAM gönderilir: `
          + 'seçimden çıkardığınız kategoriden ürün kalkar.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Kategoriler kaydediliyor…', async () => {
        await call(`${BASE}/products/${product.id}/categories`, {
          method: 'POST', body: { categoryIds: ids, reason, dryRun: false },
        });
        toast('Kategoriler kaydedildi.', 'good');
        refresh();
      });
    },
  }));

  pane.append(picker.node, actions,
    hintBox('Kategori listesi mağazaya TAM gönderilir; kısmi gönderim ürünün mevcut '
      + 'kategorilerini boşaltıyordu. Seçili olmayan her kategori kaldırılır.'));
}

// GOOGLE GÖRÜNÜMÜ — eski adı "SEO".
//
// "SEO", "meta başlık", "meta açıklama" kullanıcının sözlüğünde yoktu ve bu
// sekme tam da bu yüzden hiç doldurulmuyordu. Alanlar artık NEREDE göründüğünü
// söylüyor; altındaki önizleme de aynı şeyi gösteriyor.
function paintSeo(pane, payload, forms) {
  const product = payload.product;
  const seo = payload.seo;
  const form = formGrid({
    fields: [
      { key: 'metaTitle', label: 'Google’da görünecek başlık', type: 'text', maxLength: 120,
        wide: true,
        hint: 'Boş bırakırsanız ürün adı kullanılır. 60 karakteri geçen kısım Google’da '
          + '“…” ile kesilir.' },
      { key: 'metaDescription', label: 'Google’da görünecek açıklama', type: 'textarea',
        maxLength: 320, wide: true,
        hint: 'Arama sonucunda başlığın altındaki iki satır. Boş bırakırsanız Google '
          + 'sayfadan rastgele bir parça seçer — genelde kötü görünür.' },
      { key: 'metaKeywords', label: 'Müşteri bunu ararken hangi kelimeleri yazar?',
        type: 'text', maxLength: 240, wide: true,
        hint: 'Virgülle ayırın: “1. sınıf matematik, ilkokul kitabı”. Bugünkü arama '
          + 'motorları buna pek bakmıyor; boş bırakmanız da sorun değil.' },
    ],
    value: seo,
    onChange: () => paintPreview(),
  });
  forms.push(form);

  const preview = h('div', 'sp-serp');
  function paintPreview() {
    const draft = form.draft();
    const title = draft.metaTitle || product.name;
    const description = draft.metaDescription || '';
    preview.replaceChildren(
      h('div', 'sp-serp-url', `bbdstore.com.tr/${product.urlKey || 'urun'}`),
      h('div', 'sp-serp-title', title.slice(0, 60)),
      h('div', 'sp-serp-text', description.slice(0, 160)
        || 'Açıklama boş — Google burada sayfadan rastgele bir parça gösterir.'),
      h('div', 'sp-sub',
        `Başlık ${title.length}/60 karakter · açıklama ${description.length}/160 karakter. `
        + 'Sınırı aşan kısım Google’da görünmez.'),
    );
  }
  paintPreview();

  const actions = h('div', 'sp-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      const result = await saveProduct(product.id, form.patch(), {
        title: 'Google görünümünü kaydet',
        description: `“${product.name}” için ${form.dirty().length} alan değişti. Bu `
          + 'değişikliğin Google’a yansıması birkaç gün sürebilir.',
      });
      if (result) form.reset(form.draft());
    },
  }));

  pane.append(form.node,
    card('Google’da böyle görünecek', preview,
      'Müşteri arama yaptığında karşısına çıkacak kutunun temsili'),
    actions,
    hintBox('Bu sekme ürünün Google’da bulunmasını kolaylaştırır. Boş bırakırsanız ürün '
      + 'yine satılır ama arama sonuçlarında geri sıralarda çıkar. Değişikliklerin '
      + 'Google’a yansıması birkaç gün alabilir.'));
}

async function paintHistory(pane, payload) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await call(`${BASE}/audit?productId=${payload.product.id}&limit=50`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Bu ürüne bu ekrandan hiç dokunulmamış',
      text: 'Buradan yapılan her değişiklik; kimin, ne zaman ve neden yaptığıyla birlikte '
        + 'bu listeye yazılır.',
    }));
    return;
  }
  // SİLİNMİŞ ÜRÜN GEÇMİŞTE KALIR ve kırmızı ibaresiyle görünür: "bu ürüne ne
  // oldu" sorusunun cevabı, ürün katalogdan gittikten sonra da durmalı.
  // İbarenin metni sunucudan gelir (`deleted.py`) — üç ekran aynı kelimeyi
  // kullansın diye panelde sabit yazılmaz.
  const goneLabel = result.deletedLabel || 'silinmiş';
  if ((result.deletedIds || []).includes(payload.product.id)) {
    pane.append(alertBox(`Bu ürün bu ekrandan SİLİNDİ. Aşağıdaki iz ve geçmiş siparişlerin `
      + `kalemleri yerinde duruyor; kalemler raporlarda kırmızı “${goneLabel}” ibaresiyle `
      + 'görünür.', 'bad'));
  }

  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Ne zaman', width: '150px' },
      { key: 'action', label: 'Ne yapıldı', width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const cell = h('span', 'sp-audit-action');
          cell.append(h('b', undefined, row.action));
          if (row.productDeleted) cell.append(badge(goneLabel, 'bad'));
          return cell;
        } },
      { key: 'actor', label: 'Kim yaptı', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Neden yaptı', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}`,
  });
  pane.append(table.node,
    hintBox('Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydı gerekçe '
      + 'alanı taşımıyor; ağ koparsa “ne yapmaya çalıştık” bilgisi yalnız burada kalır.'));
}

// ============================================================= toplu işlem

function bulkDialog(kind, options = {}) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog sp-bulk');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');

  const titles = {
    price: 'Seçili ürünlerin fiyatını değiştir',
    stock: 'Seçili ürünlerin stoğunu değiştir',
    category: 'Seçili ürünleri kategoriye ekle / çıkar',
    book: 'Seçili kitaplara sayfa sayısı / desi yaz',
    status: options.active
      ? 'Seçili ürünleri vitrine çıkar' : 'Seçili ürünleri vitrinden kaldır',
  };
  box.append(h('h3', 'kit-dialog-title', titles[kind]));
  box.append(h('p', 'kit-dialog-text',
    `İşaretlediğiniz ${num(state.selection.length)} ürüne uygulanacak. ÖNCE NE OLACAĞINI `
    + 'GÖSTERİRİZ: “Önizle” dediğinizde her ürünün önceki ve sonraki değerini bir tabloda '
    + 'görürsünüz. Onaylamadan hiçbir şey değişmez.'));

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const controls = h('div', 'sp-bulk-controls');
  const params = { kind, mode: '', amount: 0, rounding: 'none', categoryId: 0,
    active: options.active, field: '', value: '' };
  // Bu kutuda yapılacak iş MÜMKÜN DEĞİLSE (mağazada gerekli alan yoksa)
  // önizleme düğmesi kapalı açılır ve nedenini söyler.
  let missingBookFields = false;

  if (kind === 'price') {
    // İLK SEÇENEK VARSAYILAN GELİR (tarayıcı davranışı) ve bilinçli olarak
    // "yüzde" seçildi: zam/indirim en sık yüzdeyle konuşulur.
    const mode = select([
      { value: 'percent', label: 'Yüzde zam/indirim yap' },
      { value: 'amount', label: 'Fiyata tutar ekle/çıkar' },
      { value: 'set', label: 'Hepsine aynı fiyatı yaz' },
    ]);
    const amount = h('input', 'kit-input');
    amount.type = 'text';
    amount.placeholder = 'Örnek: 10 (zam) · -10 (indirim) · 199,90 (sabit fiyat)';
    const rounding = select([
      { value: 'none', label: 'Yuvarlama yapma' },
      { value: 'penny99', label: '…,99 ile bitir (99,99 gibi)' },
      { value: 'whole', label: 'Tam liraya yuvarla (100 gibi)' },
      { value: 'half', label: '50 kuruşa yuvarla (99,50 gibi)' },
    ]);
    controls.append(
      labelled('Ne yapılsın?', mode),
      labelled('Ne kadar?', amount,
        'Yüzdede: 10 = %10 zam, -10 = %10 indirim. Tutarda: 25 = 25 TL ekle, -25 = çıkar.'),
      labelled('Sonuç yuvarlansın mı?', rounding,
        'Hesap sonrası fiyatı düzgün bir sayıya çeker.'));
    params.read = () => {
      params.mode = mode.value;
      params.rounding = rounding.value;
      params.amount = mode.value === 'percent'
        ? Number(String(amount.value).replace(',', '.'))
        : parseMoney(amount.value);
      return params.amount !== null && !Number.isNaN(params.amount);
    };
  } else if (kind === 'stock') {
    const mode = select([
      { value: 'set', label: 'Stoğu şu sayıya eşitle' },
      { value: 'add', label: 'Mevcut stoğa ekle / stoktan düş' },
    ]);
    const amount = h('input', 'kit-input');
    amount.type = 'number';
    amount.value = '0';
    controls.append(
      labelled('Ne yapılsın?', mode),
      labelled('Kaç adet?', amount,
        'Ekle/düş seçtiyseniz: 20 = 20 adet ekler, -20 = 20 adet düşer.'));
    params.read = () => {
      params.mode = mode.value;
      params.amount = Number(amount.value);
      return !Number.isNaN(params.amount);
    };
  } else if (kind === 'book') {
    const bookFields = (state.reference.bookFields || []).filter((item) => item.available
      && (item.key === 'pageCount' || item.key === 'desi'));
    const field = select(bookFields.map((item) => ({ value: item.key, label: item.label })));
    const mode = select([
      { value: 'set', label: 'Şu değeri yaz' },
      // BOŞALTMA AYRI BİR KİPTİR ve asıl kullanışlı olan budur: yanlışlıkla
      // girilmiş bir `desi` ölçümü sayfa hesabını EZMEYE devam eder ve onu
      // kaldırmanın tek yolu alanı boş yazmaktır.
      { value: 'clear', label: 'Alanı boşalt (yazılmış değeri sil)' },
    ]);
    const amount = h('input', 'kit-input');
    amount.type = 'text';
    amount.placeholder = 'Örnek: 176 (sayfa) · 0,45 (desi)';
    controls.append(
      labelled('Hangi alan?', field),
      labelled('Ne yapılsın?', mode),
      labelled('Değer', amount,
        'Sayfa sayısı tam sayıdır (176). Desi ondalıklı olabilir (0,45).'));
    if (!bookFields.length) {
      controls.append(blockerBox('NO_BOOK_FIELDS'));
      missingBookFields = true;
    }
    const sync = () => { amount.disabled = mode.value === 'clear'; };
    mode.addEventListener('change', sync);
    sync();
    params.read = () => {
      params.field = field.value;
      params.mode = mode.value;
      params.value = mode.value === 'clear' ? '' : String(amount.value).trim();
      return Boolean(params.field) && (mode.value === 'clear' || params.value !== '');
    };
  } else if (kind === 'category') {
    const mode = select([
      { value: 'add', label: 'Bu kategoriye ekle' },
      { value: 'remove', label: 'Bu kategoriden çıkar' },
    ]);
    const target = select([
      { value: '', label: 'Listeden bir kategori seçin…' },
      ...state.reference.categories.map((item) => ({ value: item.id, label: item.label })),
    ]);
    controls.append(
      labelled('Ne yapılsın?', mode),
      labelled('Hangi kategori?', target,
        'Ürün başka kategorilerde de kalmaya devam eder; yalnız bu kategoriye eklenir '
        + 'ya da bundan çıkarılır.'));
    params.read = () => {
      params.mode = mode.value;
      params.categoryId = Number(target.value) || 0;
      return params.categoryId > 0;
    };
  } else {
    params.read = () => true;
    controls.append(h('div', 'sp-sub', options.active
      ? 'İşaretlediğiniz ürünler müşteriye görünür hâle gelir ve satın alınabilir.'
      : 'İşaretlediğiniz ürünler müşteriye görünmez olur. SİLİNMEZ: geçmiş siparişlerde ve '
        + 'raporlarda kalır, istediğiniz gün geri açarsınız.'));
  }

  const result = h('div', 'sp-bulk-result');
  const actions = h('div', 'kit-dialog-actions');
  const previewBtn = button('Önce ne olacağını göster', {
    variant: 'primary',
    title: 'Hiçbir şey değiştirmeden, her ürünün önceki ve sonraki değerini listeler',
    disabled: missingBookFields,
    onClick: () => runPreview(),
  });
  // KAPALI DÜĞME NEDENİNİ SÖYLER. Eskiden düğme açık kalıyor, basınca
  // anlamsız bir sunucu hatası dönüyordu.
  if (missingBookFields) blockedReason(previewBtn, 'NO_BOOK_FIELDS');
  actions.append(button('Vazgeç', { onClick: close }), previewBtn);

  box.append(controls, result, actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);

  async function runPreview() {
    if (!params.read()) {
      // Hangi alanın eksik olduğu işlem türüne göre değişiyor; cümle her
      // durumda "ne yapmalıyım" sorusunu cevaplar.
      toast({
        price: 'Önce “Ne kadar?” kutusuna bir sayı yazın. Örnek: 10 (zam), -10 (indirim).',
        stock: 'Önce “Kaç adet?” kutusuna bir sayı yazın.',
        book: 'Önce alanı ve değeri seçin. “Alanı boşalt” seçtiyseniz değer yazmanız '
          + 'gerekmez.',
        category: 'Önce listeden bir kategori seçin.',
      }[kind] || 'Girdiğiniz değer kullanılamıyor; kontrol edip yeniden deneyin.', 'bad');
      return;
    }
    result.replaceChildren(skeletonRows(4, 4));
    let preview;
    try {
      preview = await call(`${BASE}/bulk/preview`, {
        method: 'POST',
        body: {
          kind, productIds: state.selection.map(Number), mode: params.mode,
          amount: params.amount, rounding: params.rounding, categoryId: params.categoryId,
          active: Boolean(params.active), field: params.field, value: params.value,
        },
      });
    } catch (error) {
      result.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    paintDiff(preview);
  }

  function paintDiff(preview) {
    result.replaceChildren();
    const summary = preview.summary;
    result.append(kpiRow([
      { label: 'İşaretlediğiniz', value: num(summary.total),
        title: 'Toplu işlem için seçtiğiniz ürün sayısı.' },
      { label: 'Değişecek', value: num(summary.changed),
        title: 'Onaylarsanız gerçekten değişecek ürün sayısı.' },
      { label: 'Dokunulmayacak', value: num(summary.skipped), tone: 'muted',
        title: 'Zaten istenen değerde olan ya da değiştirilemeyen ürünler.' },
      { label: 'Artanlar', value: num(summary.up), tone: 'good' },
      { label: 'Azalanlar', value: num(summary.down), tone: 'bad' },
    ]));

    const asMoney = kind === 'price';
    // KİTAP ALANI METİNDİR. `num()` ile basmak "0,45 desi"yi 0'a, boş değeri
    // de 0'a çevirirdi — ikisi de yanlış ve ikincisi tehlikeli: boş desi
    // "hesabı sayfadan yap" demektir, 0 desi ise geçersiz bir ölçüm.
    const asText = kind === 'book';
    const cellValue = (value) => {
      if (asText) return String(value ?? '—');
      return asMoney ? money(value) : num(value);
    };
    const table = dataTable({
      columns: [
        { key: 'sku', label: 'Stok kodu', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
        { key: 'before', label: 'Şu an', width: '110px', align: 'num',
          cell: (row) => cellValue(row.before) },
        { key: 'after', label: 'Onaylarsanız', width: '110px', align: 'num',
          cell: (row) => cellValue(row.after) },
        { key: 'delta', label: 'Fark', width: '110px', align: 'num',
          cell: (row) => {
            const cell = h('span', row.delta < 0 ? 'sp-bad' : 'sp-good');
            if (asText) cell.textContent = row.skipped ? '—' : 'değişecek';
            else {
              cell.textContent = row.skipped ? '—'
                : `${row.delta > 0 ? '+' : ''}${asMoney ? money(row.delta) : num(row.delta)}`;
            }
            if (row.note) cell.title = row.note;
            return cell;
          } },
      ],
      rows: preview.rows,
      dense: true,
      rowKey: (row) => String(row.id),
    });
    result.append(table.node);

    if (preview.missing && preview.missing.length) {
      result.append(alertBox(`${preview.missing.length} ürün mağazadan okunamadı; bu `
        + 'tabloya girmediler ve onaylasanız da değişmeyecekler. Sıradaki adım: '
        + 'pencereyi kapatıp “Yenile” deyin, sonra yeniden deneyin.', 'warn'));
    }
    result.append(alertBox(preview.note, preview.applicable ? 'info' : 'warn'));

    actions.replaceChildren(
      button('Vazgeç', { onClick: close }),
      button('Bu tabloyu Excel olarak indir', {
        title: 'Onaylamadan önce kontrol etmek ya da kayıt altına almak için',
        onClick: () => {
          csvBlob(['Stok kodu', 'Ürün', 'Şu an', 'Onaylarsanız', 'Fark', 'Not'],
            preview.rows.map((row) => [row.sku, row.name, row.before, row.after, row.delta,
              row.note || '']),
            `toplu-${kind}-farki`);
          toast('Tablo indirildi.', 'good');
        },
      }),
      // Uygulanamıyorsa NEDENİ düğmenin üstünde yazıyor (`preview.note`);
      // düğme de sessiz kalmasın diye ipucunu taşır.
      (() => {
        const node = button(`Onaylıyorum — ${num(summary.changed)} ürüne uygula`, {
          variant: 'danger',
          disabled: !preview.applicable || summary.changed === 0,
          onClick: () => apply(preview),
        });
        if (node.disabled) {
          node.title = summary.changed === 0
            ? 'Değişecek bir şey yok: seçtiğiniz ürünler zaten istediğiniz durumda.'
            : (preview.note || 'Bu işlem şu an uygulanamıyor.');
          node.setAttribute('aria-label', `${node.textContent} — kapalı: ${node.title}`);
        }
        return node;
      })(),
    );
  }

  async function apply(preview) {
    const reason = await askReason({
      title: titles[kind],
      description: `${num(preview.summary.changed)} ürün değişecek. AZ ÖNCE GÖRDÜĞÜNÜZ `
        + 'TABLO NEYSE O UYGULANIR — bu arada fiyatlar değişse bile hesap yeniden '
        + 'yapılmaz, sürpriz olmaz.',
      confirmLabel: 'Uygula',
    });
    if (!reason) return;
    await withBusy('Uygulanıyor…', async () => {
      const applied = await call(`${BASE}/bulk/apply`, {
        method: 'POST', body: { token: preview.token, reason, dryRun: false },
      });
      toast(`${num(applied.applied)} ürün güncellendi`
        + (applied.failed
          ? ` · ${num(applied.failed)} ürüne yazılamadı. Sıradaki adım: “Yenile” deyip o `
            + 'ürünleri tek tek kontrol edin.'
          : ''),
      applied.failed ? 'warn' : 'good');
      if (applied.notice) toast(applied.notice, 'warn');
      close();
      nodes.table?.clearSelection();
      state.selection = [];
      refresh();
    });
  }
}

function select(options) {
  const node = h('select', 'kit-select');
  for (const option of options) {
    const item = h('option', undefined, option.label);
    item.value = String(option.value);
    node.append(item);
  }
  return node;
}

/**
 * Etiketli alan — İPUCU ZORUNLU DEĞİL AMA MÜMKÜN.
 *
 * `hint` eklendi çünkü toplu işlem kutusundaki alanlar (“Kip”, “Değer”)
 * tek başına hiçbir şey anlatmıyordu: kullanıcı “-10” mu “%10” mu yazacağını
 * ekrandan öğrenemiyordu. `formGrid` bunu zaten yapıyor; burası elle kurulan
 * kutular için aynı davranışı verir.
 */
function labelled(label, control, hint) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), control);
  if (hint) wrap.append(h('span', 'kit-field-hint', hint));
  return wrap;
}

// ================================================================ ayarlar

let settingsForms = [];

async function renderSettings(host) {
  settingsForms.forEach((form) => form.destroy());
  settingsForms = [];
  host.replaceChildren(skeletonRows(4, 2));
  let payload;
  try {
    payload = await call(`${BASE}/settings`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  const localForm = formGrid({
    fields: [{
      key: 'lowStockThreshold', label: 'Stok kaça düşünce uyaralım?', type: 'number',
      min: 0, max: 9999,
      hint: 'Bu sayının altına düşen ürünler listede “Stok azaldı” diye işaretlenir. '
        + 'YALNIZ bu ekranı ilgilendirir; müşteriye ve siparişlere hiçbir etkisi yok.',
    }],
    value: { lowStockThreshold: payload.local.lowStockThreshold },
  });
  settingsForms.push(localForm);

  const storeBox = h('div');
  const storeFields = [];
  const found = (slot) => payload.store?.[slot]?.found;
  const truthy = (slot) => Boolean(Number(payload.store?.[slot]?.value ?? 0));

  if (!payload.storeAvailable) {
    storeBox.append(alertBox(
      `Mağazadaki ayarlar okunamadı — ${payload.error} Sıradaki adım: “Yenile” deyip `
      + 'yeniden deneyin. Yukarıdaki stok uyarı sınırını yine de kaydedebilirsiniz; o '
      + 'ayar bu bilgisayarda tutuluyor.', 'warn'));
  } else {
    if (found('outOfStock')) {
      storeFields.push({
        key: 'outOfStock', label: 'Stoğu biten ürünler vitrinde kalsın', type: 'checkbox',
        hint: 'İŞARETLİ: ürün listede görünmeye devam eder ama “tükendi” yazar ve satın '
          + 'alınamaz — müşteri ürünü tanır, stok gelince geri döner. '
          + 'İŞARETSİZ: ürün vitrinden tamamen kalkar.',
      });
    }
    if (found('backOrder')) {
      storeFields.push({
        key: 'backOrder', label: 'Stok yokken de sipariş alınsın', type: 'checkbox',
        hint: 'İŞARETLİ: müşteri stokta olmayan ürünü sipariş edebilir ve gelmesini '
          + 'bekler. Ürünü tedarik edebiliyorsanız işaretleyin; edemiyorsanız '
          + 'işaretlemeyin, yoksa teslim edemeyeceğiniz sipariş alırsınız.',
      });
    }
    const missing = ['outOfStock', 'backOrder'].filter((slot) => !found(slot));
    if (missing.length) {
      storeBox.append(alertBox(
        'Bu ayarların anahtarı mağaza yapılandırmasında bulunamadı; bulunmayan anahtara '
        + 'yazmak hiçbir şeyi değiştirmez ve “kaydettim” yanılgısı üretir. Anahtar adları '
        + `modül ayarından düzeltilebilir (slug: ${payload.storeSlug}).`, 'warn'));
    }
  }

  const storeForm = storeFields.length ? formGrid({
    fields: storeFields,
    value: { outOfStock: truthy('outOfStock'), backOrder: truthy('backOrder') },
  }) : null;
  if (storeForm) {
    settingsForms.push(storeForm);
    storeBox.append(storeForm.node);
  }

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      const body = { lowStockThreshold: Number(localForm.draft().lowStockThreshold) };
      if (storeForm) {
        const draft = storeForm.draft();
        if (found('outOfStock')) body.outOfStock = Boolean(draft.outOfStock);
        if (found('backOrder')) body.backOrder = Boolean(draft.backOrder);
      }
      const reason = await askReason({
        title: 'Stok ayarlarını kaydet',
        description: 'Mağaza ayarları vitrini doğrudan etkiler; gerekçe denetim kaydına '
          + 'yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Ayarlar kaydediliyor…', async () => {
        const result = await call(`${BASE}/settings`, {
          method: 'POST', body: { ...body, reason, dryRun: false },
        });
        toast(`Kaydedildi: ${result.changed.join(', ') || 'değişiklik yok'}`, 'good');
        if (result.skipped.length) {
          toast(`Yazılamayan ayar: ${result.skipped.join(', ')} (anahtar bulunamadı).`, 'warn');
        }
        state.threshold = body.lowStockThreshold;
      });
    },
  });

  const actions = h('div', 'sp-actions');
  actions.append(save);

  host.append(
    card('Bu ekranın tercihi', localForm.node, 'Yalnız Kontrol Merkezi’ni etkiler'),
    card('Mağaza ayarı', storeBox, 'Vitrini doğrudan etkiler'),
    actions,
    hintBox('Ayrı bir “Mağaza Ayarları” ekranı yoktur: her ayar onu kullanan ekranda '
      + 'durur. Stokla ilgili olanlar burada, vergi store_tax’ta, kargo ücreti '
      + 'store_shipping’de.'),
  );
}

// ====================================================== nitelikler · aileler
//
// NİTELİK KATALOĞUN ŞEMASIDIR, VERİSİ DEĞİL. Bir ürünü pasifleştirmek tek
// satırı etkiler; bir niteliği silmek o niteliğin BÜTÜN ürünlerdeki değerini
// götürür ve geri alınamaz. Bu yüzden bu sekmedeki her yıkıcı işlem, ürün
// tarafındakinden daha dar bir kapıdan geçer.
//
// EKRANDA KARŞILIĞI OLAN KURALLAR:
//  · KOD ve TİP oluşturulduktan sonra DEĞİŞMEZ. Alanlar `static` çizilir ve
//    NEDEN kilitli olduğu yanlarında yazar. Kilit yalnız burada değil
//    backend'de de var (K9); ekranda gizlemek yetkilendirme değildir.
//  · KULLANIMDAKİ NİTELİK SİLİNMEZ. Silme düğmesi backend'in verdiği karara
//    (`delete.allowed`) bağlıdır; kapalıysa yerinde PASİFLEŞTİRME durur ve
//    ikisinin farkı yazıyla anlatılır.
//  · Mağaza 409 dönerse ("ailede kullanılıyor") backend bunu ne yapılacağını
//    söyleyen bir cümleye çeviriyor; ekran o cümleyi olduğu gibi gösterir.
//  · AİLE DÜZENİ GÖNDERİLİRSE MAĞAZADAKİNİN YERİNE GEÇER. "Adı değiştir" ile
//    "düzeni kaydet" bu yüzden AYRI iki düğmedir: tek düğme olsaydı adı
//    düzeltmek isteyen kullanıcı ailenin gruplarını da yeniden yazardı.

const SCHEMA_SCOPES = [
  { value: '', label: 'Tümü' },
  { value: 'custom', label: 'Kullanıcı tanımlı' },
  { value: 'system', label: 'Sistem' },
  { value: 'required', label: 'Zorunlu' },
  { value: 'filterable', label: 'Süzgeçte' },
  { value: 'options', label: 'Seçenekli' },
  { value: 'unused', label: 'Kullanılmayan' },
];

/** Nitelik yazma formunun alanları — kod ve tip BURADA YOK, olamaz. */
const ATTRIBUTE_FIELDS = [
  { key: 'name', label: 'Görünen ad', type: 'text', required: true, maxLength: 180, wide: true },
  { key: 'required', label: 'Ürün kaydında zorunlu', type: 'checkbox',
    hint: 'Açıkken bu nitelik boş bırakılan ürün KAYDEDİLEMEZ.' },
  { key: 'unique', label: 'Değeri benzersiz olmalı', type: 'checkbox' },
  { key: 'filterable', label: 'Vitrin süzgecinde çıksın', type: 'checkbox' },
  { key: 'configurable', label: 'Ürün seçeneği üretmekte kullanılsın', type: 'checkbox',
    hint: 'Yalnız seçim tiplerinde anlamlıdır.' },
  { key: 'visibleOnFront', label: 'Ürün sayfasında görünsün', type: 'checkbox' },
  { key: 'comparable', label: 'Karşılaştırmada görünsün', type: 'checkbox' },
  { key: 'perLocale', label: 'Değer dile göre değişsin', type: 'checkbox' },
  { key: 'perChannel', label: 'Değer kanala göre değişsin', type: 'checkbox' },
  { key: 'position', label: 'Sıra', type: 'number', min: 0 },
];

//: Tip listesi nitelik listesiyle birlikte geliyor; "yeni nitelik" çekmecesi de
//: aynı listeyi kullansın diye burada tutulur — ayrı bir istek atmaya değmez.
const SCHEMA_TYPE_CACHE = [];

//: Hangi alt sekmede olunduğu. Sekme değişip geri dönülünce kullanıcı bıraktığı
//: yere döner; her dönüşte "Nitelikler"e atmak seçimi kaybettiriyordu.
let schemaState = { view: 'attributes' };

function usageCell(row) {
  const box = h('span', 'sp-usage');
  if (row.usageProducts === null || row.usageProducts === undefined) {
    const dash = h('b', 'sp-sub', '—');
    dash.title = 'Bu nitelik hiçbir ailede geçmiyor ya da aile düzeni okunamadı; '
      + 'ürün sayısı hesaplanmadı.';
    box.append(dash);
  } else {
    box.append(h('b', undefined, num(row.usageProducts)));
  }
  box.append(h('span', 'sp-sub', (row.usageFamilies || []).join(', ') || 'ailesiz'));
  return box;
}

const ATTRIBUTE_COLUMNS = [
  { key: 'code', label: 'Kod', width: 'minmax(0, 1.2fr)', className: 'mono', sortable: true },
  { key: 'name', label: 'Ad', width: 'minmax(0, 1.6fr)', sortable: true },
  { key: 'typeLabel', label: 'Tip', width: '110px' },
  {
    key: 'flags',
    label: 'Bayraklar',
    width: 'minmax(0, 1.2fr)',
    cell: (row) => {
      const box = h('span', 'sp-flags');
      if (row.required) box.append(badge('Zorunlu', 'warn'));
      if (row.filterable) box.append(badge('Süzgeç', 'info'));
      if (row.visibleOnFront) box.append(badge('Vitrin', 'good'));
      if (row.hasOptions) {
        box.append(badge(row.optionCount === null ? 'Seçenekli'
          : `${num(row.optionCount)} seçenek`, 'dim'));
      }
      if (!box.childNodes.length) box.append(h('span', 'sp-sub', '—'));
      return box;
    },
  },
  { key: 'usageProducts', label: 'Kullanım', width: '150px', align: 'num', cell: usageCell },
  {
    key: 'system',
    label: 'Kaynak',
    width: '96px',
    cell: (row) => badge(row.system ? 'Sistem' : 'Kullanıcı', row.system ? 'dim' : 'info'),
  },
];

function renderSchema(host) {
  host.replaceChildren();

  const inner = tabBar([
    { key: 'attributes', label: 'Nitelikler' },
    { key: 'families', label: 'Aileler' },
  ], schemaState.view, (key) => { schemaState.view = key; paint(); });

  const body = h('div', 'sp-schema-body');
  host.append(inner.node, body);

  // Bu iki liste FORM TUTMAZ: formlar yalnız çekmecelerde açılıyor ve her
  // çekmece kendi `destroy` çağrısını `onClose` ile birlikte `closers`a
  // yazıyor. Burada ayrıca bir form havuzu tutmak, hiç dolmayan bir listeyi
  // temizleyen ölü kod olurdu.
  function paint() {
    body.replaceChildren();
    if (schemaState.view === 'families') renderFamilyList(body);
    else renderAttributeList(body);
  }
  paint();
}

// ------------------------------------------------------------------ nitelik

function renderAttributeList(host) {
  const search = h('input', 'kit-input');
  search.type = 'search';
  search.placeholder = 'Kod ya da ad ara';
  const kindSelect = select([{ value: '', label: 'Tümü — tip' }]);
  const scopeSelect = select(SCHEMA_SCOPES);

  const notes = h('div', 'sp-schema-notes');
  const listBox = h('div');

  // Süzme SUNUCUDA yapılır: "kullanılmayan" çipi ağ isteklerinden gelen
  // kullanım hesabına dayanıyor ve o hesap panelde yok.
  const reload = async () => {
    listBox.replaceChildren(skeletonRows(8, 6));
    notes.replaceChildren();
    const query = new URLSearchParams();
    if (search.value.trim()) query.set('q', search.value.trim());
    if (kindSelect.value) query.set('kind', kindSelect.value);
    if (scopeSelect.value) query.set('scope', scopeSelect.value);

    let payload;
    try {
      payload = await call(`${BASE}/attributes?${query.toString()}`);
    } catch (error) {
      listBox.replaceChildren(emptyState({
        title: 'Nitelikler okunamadı',
        text: error.message,
        actions: [button('Tekrar dene', { variant: 'primary', onClick: () => reload() })],
      }));
      return;
    }

    if (kindSelect.options.length <= 1) {
      SCHEMA_TYPE_CACHE.length = 0;
      for (const item of payload.types || []) {
        SCHEMA_TYPE_CACHE.push(item);
        const option = h('option', undefined, item.label);
        option.value = item.value;
        kindSelect.append(option);
      }
    }

    if (!payload.connected) {
      notes.append(alertBox(`Mağazaya ulaşılamadı — ${payload.error}`, 'bad'));
    }
    for (const warning of payload.warnings || []) notes.append(alertBox(warning, 'warn'));
    if (payload.connected && !payload.familiesKnown) {
      notes.append(alertBox(
        'Ailelerin nitelik düzeni eksik okundu: bir niteliğin hangi ailede kullanıldığı '
        + 'BİLİNMİYOR. Silme bu yüzden kapalı — bilinmeyeni “kullanılmıyor” sayıp silmek '
        + 'geri alınamayan veri kaybıdır. Pasifleştirme açık.', 'warn'));
    }
    notes.append(hintBox(payload.usageNote || ''));

    listBox.replaceChildren(dataTable({
      columns: ATTRIBUTE_COLUMNS,
      rows: payload.items || [],
      empty: emptyState({
        title: 'Bu süzgece uyan nitelik yok',
        text: 'Süzgeci gevşetin. “Kullanılmayan” çipi yalnız kullanımı BİLİNEN ve sıfır '
          + 'olan nitelikleri gösterir; bilinmeyen kullanım bu çipe girmez.',
      }),
      rowKey: (row) => String(row.id),
      onRow: (row) => openAttribute(row.id, reload),
    }).node);
  };

  const debounced = debounce(() => reload(), 320);
  closers.push(() => debounced.cancel());
  search.addEventListener('input', debounced);
  kindSelect.addEventListener('change', () => reload());
  scopeSelect.addEventListener('change', () => reload());

  const bar = h('div', 'sp-schema-bar');
  bar.append(
    labelled('Ara', search), labelled('Tip', kindSelect), labelled('Kapsam', scopeSelect),
    h('span', 'kit-spacer'),
    button('Yenile', { onClick: () => reload() }),
    button('Yeni nitelik', { variant: 'primary', onClick: () => newAttribute(reload) }),
  );

  host.append(bar, notes, listBox);
  reload();
}

async function openAttribute(attributeId, done) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Nitelik yükleniyor…', subtitle: `#${attributeId}`, onClose: dropForms,
  });
  closers.push(dropForms);
  box.body.append(skeletonRows(6, 3));

  let payload;
  try {
    payload = await call(`${BASE}/attributes/${attributeId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Nitelik okunamadı', text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const row = payload.attribute;
  box.setTitle(row.name);
  box.body.replaceChildren();

  const head = h('div', 'sp-drawer-head');
  head.append(
    badge(row.system ? 'Sistem niteliği' : 'Kullanıcı tanımlı', row.system ? 'dim' : 'info'),
    badge(row.typeLabel, 'info'),
    h('code', 'sp-sku', row.code),
    h('span', 'sp-sub', row.usageProducts === null
      ? 'Kullanım bilinmiyor'
      : `${num(row.usageProducts)} ürünün ailesinde tanımlı`),
  );
  box.body.append(head);

  for (const warning of payload.warnings || []) box.body.append(alertBox(warning, 'warn'));

  // --- KİLİTLİ ALANLAR. `static` tipi bilinçli: readOnly bir metin kutusu
  // "denesem belki yazar" izlenimi verir; static hiç kutu çizmez.
  const locked = formGrid({
    fields: [
      { key: 'code', label: 'Kod', type: 'static',
        hint: 'Ürün değer tablolarında ANAHTAR; değişirse bu niteliğin bütün '
          + 'ürünlerdeki değeri öksüz kalır.' },
      { key: 'type', label: 'Tip', type: 'static',
        hint: 'Değerin hangi sütunda saklandığını belirler; değişirse mevcut '
          + 'değerler okunamaz hâle gelir.' },
    ],
    value: { code: row.code, type: `${row.typeLabel} (${row.type})` },
  });
  forms.push(locked);
  box.body.append(
    card('Değiştirilemez alanlar', locked.node, 'Oluşturulduktan sonra kilitli'),
    alertBox(payload.lockNotice, 'info'),
  );

  // --- DÜZENLENEBİLİR BAYRAKLAR
  const form = formGrid({ fields: ATTRIBUTE_FIELDS, value: row });
  forms.push(form);
  const saveRow = h('div', 'sp-actions');
  saveRow.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const patch = form.patch();
      if (!Object.keys(patch).length) { toast('Değişen alan yok.', 'warn'); return; }
      const reason = await askReason({
        title: 'Niteliği güncelle',
        description: `\`${row.code}\` · ${form.dirty().length} alan değişti. Kod ve tip `
          + 'gövdeye KONMAZ; yalnız işaretlediğiniz bayraklar gider.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Nitelik kaydediliyor…', async () => call(
        `${BASE}/attributes/${row.id}`,
        { method: 'PUT', body: { patch, reason, dryRun: false } },
      ));
      if (!result) return;
      toast(`Kaydedildi: ${result.fields.join(', ')}`, 'good');
      if (result.notice) toast(result.notice, 'warn');
      form.reset(form.draft());
      done?.();
    },
  }));
  box.body.append(card('Bayraklar', form.node), saveRow);

  // --- SEÇENEKLER (yalnız seçim tiplerinde)
  if (row.hasOptions) box.body.append(optionsCard(row, payload.options || [], box, done));

  // --- PASİFLEŞTİRME ve SİLME. İkisi YAN YANA durur ve farkı yazıyla anlatılır:
  // pasifleştirme geri alınabilir, silme alınamaz.
  const verdict = payload.delete || {};
  const danger = h('div', 'sp-danger');
  danger.append(h('div', 'sp-sub', payload.deactivate));
  const dangerActions = h('div', 'sp-actions');
  dangerActions.append(button('Pasifleştir', {
    title: 'Nitelik ve ürünlerdeki değerleri KALIR; yalnız bayraklar iner.',
    onClick: async () => {
      const reason = await askReason({
        title: 'Niteliği pasifleştir',
        description: payload.deactivate,
        confirmLabel: 'Pasifleştir',
      });
      if (!reason) return;
      const result = await withBusy('Pasifleştiriliyor…', async () => call(
        `${BASE}/attributes/${row.id}/deactivate`,
        { method: 'POST', body: { reason, dryRun: false } },
      ));
      if (!result) return;
      toast(result.notice, 'good');
      box.close();
      done?.();
    },
  }));
  dangerActions.append(button('Sil', {
    variant: 'danger',
    disabled: !verdict.allowed,
    title: verdict.allowed ? 'Nitelik ve değerleri KALICI olarak gider.' : verdict.reason,
    onClick: async () => {
      const go = await confirmSimple(nodes.root, {
        title: 'Niteliği sil',
        description: `\`${row.code}\` mağazadan silinir. ${verdict.reason} `
          + 'Bu işlem GERİ ALINAMAZ; pasifleştirme alınabilir.',
        confirmLabel: 'Anladım, devam',
      });
      if (!go) return;
      const reason = await askReason({
        title: 'Niteliği sil',
        description: 'Gerekçe denetim kaydına yazılır. Mağaza niteliği bir ailede görürse '
          + 'isteği reddeder ve sebebini burada okursunuz.',
        confirmLabel: 'Sil',
      });
      if (!reason) return;
      const result = await withBusy('Nitelik siliniyor…', async () => call(
        `${BASE}/attributes/${row.id}/delete`,
        { method: 'POST', body: { reason, dryRun: false } },
      ));
      if (!result) return;
      toast(`\`${result.code}\` silindi.`, 'good');
      box.close();
      done?.();
    },
  }));
  danger.append(dangerActions);
  if (!verdict.allowed) {
    danger.append(alertBox(`${verdict.reason} ${verdict.alternative || ''}`.trim(), 'warn'));
  }
  box.body.append(card('Pasifleştirme ve silme', danger, 'ADR 0012'));
}

/** Seçenek yönetimi. Seçeneği silmek O DEĞERİ TAŞIYAN ÜRÜNLERDEN de düşürür. */
function optionsCard(row, options, box, done) {
  const wrap = h('div', 'sp-options');
  const list = h('div', 'sp-options-list');

  const save = async (optionId, name, sortOrder) => {
    if (!String(name).trim()) { toast('Seçenek adı zorunlu.', 'bad'); return false; }
    const reason = await askReason({
      title: optionId ? 'Seçeneği güncelle' : 'Seçenek ekle',
      description: `\`${row.code}\` niteliğinin seçenek listesi değişecek.`,
      confirmLabel: 'Kaydet',
    });
    if (!reason) return false;
    const result = await withBusy('Seçenek kaydediliyor…', async () => call(
      `${BASE}/attributes/${row.id}/options`,
      { method: 'POST', body: { optionId: optionId || null, name: String(name).trim(),
        sortOrder: Number(sortOrder) || 0, reason, dryRun: false } },
    ));
    if (!result) return false;
    toast('Seçenek kaydedildi. Çekmeceyi kapatıp açın.', 'good');
    box.close();
    done?.();
    return true;
  };

  for (const option of options) {
    const line = h('div', 'sp-option-row');
    const name = h('input', 'kit-input');
    name.value = option.name;
    const order = h('input', 'kit-input sp-narrow');
    order.type = 'number';
    order.value = String(option.sortOrder);
    line.append(
      name, order,
      button('Kaydet', { onClick: () => save(option.id, name.value, order.value) }),
      button('Sil', {
        variant: 'danger',
        title: 'Bu değeri taşıyan ürünlerden de düşer.',
        onClick: async () => {
          const go = await confirmSimple(nodes.root, {
            title: 'Seçeneği sil',
            description: `\`${option.name}\` silinir ve bu değeri taşıyan ÜRÜNLERDEN de `
              + 'düşer. Kaç ürünün etkilendiği mağaza ucundan öğrenilemiyor — sayı '
              + 'BİLİNMİYOR, uydurulmuyor. Geri alınamaz.',
            confirmLabel: 'Anladım, devam',
          });
          if (!go) return;
          const reason = await askReason({
            title: 'Seçeneği sil', description: 'Gerekçe denetim kaydına yazılır.',
            confirmLabel: 'Sil',
          });
          if (!reason) return;
          const result = await withBusy('Seçenek siliniyor…', async () => call(
            `${BASE}/attributes/${row.id}/options/${option.id}/delete`,
            { method: 'POST', body: { reason, dryRun: false } },
          ));
          if (!result) return;
          toast('Seçenek silindi.', 'good');
          box.close();
          done?.();
        },
      }),
    );
    list.append(line);
  }
  if (!options.length) list.append(h('div', 'sp-sub', 'Tanımlı seçenek yok.'));

  const fresh = h('div', 'sp-option-row');
  const freshName = h('input', 'kit-input');
  freshName.placeholder = 'Yeni seçenek adı';
  const freshOrder = h('input', 'kit-input sp-narrow');
  freshOrder.type = 'number';
  freshOrder.value = '0';
  fresh.append(freshName, freshOrder, button('Seçenek ekle', {
    variant: 'primary',
    onClick: () => save(null, freshName.value, freshOrder.value),
  }));

  wrap.append(list, fresh, hintBox('Seçenekleri ayrı bir liste ucu vermiyor; nitelik '
    + 'detayının içinden okunur. Bu yüzden kaydetmeden sonra çekmece kapanır ve liste '
    + 'yeniden çekilir.'));
  return card('Seçenekler', wrap, `${num(options.length)} seçenek`);
}

/** Yeni nitelik — KOD ve TİP burada SON KEZ seçilir. */
async function newAttribute(done) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Yeni nitelik',
    subtitle: 'Kod ve tip sonradan değiştirilemez',
    onClose: dropForms,
  });
  // Panel çekmece açıkken kapatılırsa `onClose` HİÇ çalışmaz: kabuk
  // `root.replaceChildren()` yapıyor ve overlay sessizce gidiyor. Form global
  // dinleyici tutuyor; bırakma temizleyiciye de yazılır.
  closers.push(dropForms);

  const types = SCHEMA_TYPE_CACHE.length ? SCHEMA_TYPE_CACHE : [];
  const form = formGrid({
    fields: [
      { key: 'code', label: 'Kod', type: 'text', required: true, maxLength: 50,
        hint: 'Küçük harfle başlar; küçük harf, rakam ve alt çizgi. Örnek: `raf_kodu`. '
          + 'SONRADAN DEĞİŞTİRİLEMEZ.' },
      { key: 'type', label: 'Tip', type: 'select', required: true,
        options: types.length ? types
          : [{ value: 'text', label: 'Metin' }, { value: 'textarea', label: 'Uzun metin' },
            { value: 'select', label: 'Tek seçim' }, { value: 'boolean', label: 'Evet/Hayır' }],
        hint: 'Değerin hangi sütunda saklanacağını belirler. SONRADAN DEĞİŞTİRİLEMEZ.' },
      { key: 'name', label: 'Görünen ad', type: 'text', required: true, maxLength: 180,
        wide: true },
      { key: 'required', label: 'Ürün kaydında zorunlu', type: 'checkbox' },
      { key: 'filterable', label: 'Vitrin süzgecinde çıksın', type: 'checkbox' },
      { key: 'visibleOnFront', label: 'Ürün sayfasında görünsün', type: 'checkbox' },
    ],
    value: { code: '', type: 'text', name: '' },
  });
  forms.push(form);

  const actions = h('div', 'sp-actions');
  actions.append(button('Niteliği aç', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const draft = form.draft();
      const reason = await askReason({
        title: 'Yeni nitelik aç',
        description: `\`${draft.code}\` (${draft.type}) açılacak. KOD VE TİP BİR DAHA `
          + 'DEĞİŞTİRİLEMEZ — yanlış seçilirse yeni nitelik açıp değerleri taşımak gerekir.',
        confirmLabel: 'Aç',
      });
      if (!reason) return;
      const result = await withBusy('Nitelik açılıyor…', async () => call(`${BASE}/attributes`, {
        method: 'POST',
        body: {
          code: draft.code, type: draft.type, reason, dryRun: false,
          patch: {
            name: draft.name, required: Boolean(draft.required),
            filterable: Boolean(draft.filterable),
            visibleOnFront: Boolean(draft.visibleOnFront),
          },
        },
      }));
      if (!result) return;
      toast('Nitelik açıldı.', 'good');
      toast(result.notice, 'warn');
      box.close();
      done?.();
    },
  }));

  box.body.append(
    form.node, actions,
    hintBox('Yeni nitelik HİÇBİR AİLEDE değildir: ürün ekranında görünmesi için Aileler '
      + 'sekmesinden bir gruba eklenmelidir. Seçim tiplerinde seçenekler nitelik '
      + 'açıldıktan sonra eklenir.'),
  );
}

// -------------------------------------------------------------------- aile

async function renderFamilyList(host) {
  host.replaceChildren(skeletonRows(4, 5));
  let payload;
  try {
    payload = await call(`${BASE}/families`);
  } catch (error) {
    host.replaceChildren(emptyState({
      title: 'Aileler okunamadı', text: error.message,
      actions: [button('Tekrar dene', { variant: 'primary',
        onClick: () => renderFamilyList(host) })],
    }));
    return;
  }

  const reload = () => renderFamilyList(host);
  host.replaceChildren();

  const bar = h('div', 'sp-schema-bar');
  bar.append(
    h('span', 'kit-spacer'),
    button('Yenile', { onClick: reload }),
    button('Yeni aile', { variant: 'primary', onClick: () => newFamily(reload) }),
  );
  host.append(bar);

  if (!payload.connected) host.append(alertBox(`Mağazaya ulaşılamadı — ${payload.error}`, 'bad'));
  for (const warning of payload.warnings || []) host.append(alertBox(warning, 'warn'));

  const dash = (value) => (value === null || value === undefined ? '—' : num(value));
  host.append(dataTable({
    columns: [
      { key: 'name', label: 'Aile', width: 'minmax(0, 2fr)', sortable: true },
      { key: 'code', label: 'Kod', width: 'minmax(0, 1fr)', className: 'mono' },
      { key: 'groupCount', label: 'Grup', width: '90px', align: 'num',
        cell: (row) => dash(row.groupCount) },
      { key: 'attributeCount', label: 'Nitelik', width: '90px', align: 'num',
        cell: (row) => dash(row.attributeCount) },
      { key: 'productCount', label: 'Ürün', width: '110px', align: 'num',
        cell: (row) => dash(row.productCount) },
    ],
    rows: payload.items || [],
    rowKey: (row) => String(row.id),
    onRow: (row) => openFamily(row.id, reload),
  }).node);

  host.append(hintBox(payload.notice || ''));
  host.append(hintBox('Aile SİLİNMEZ: mağaza son aileyi ve ürünü olan aileyi zaten '
    + 'reddediyor (canlıda iki ailenin ikisinde de ürün var). Kullanılmayacak aile '
    + 'ürünlerini başka aileye taşıyıp boş bırakılır.'));
}

async function openFamily(familyId, done) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Aile yükleniyor…', subtitle: `#${familyId}`, onClose: dropForms,
  });
  closers.push(dropForms);
  box.body.append(skeletonRows(6, 3));

  let payload;
  try {
    payload = await call(`${BASE}/families/${familyId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Aile okunamadı', text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const family = payload.family;
  box.setTitle(family.name);
  box.body.replaceChildren();

  const head = h('div', 'sp-drawer-head');
  head.append(
    h('code', 'sp-sku', family.code),
    h('span', 'sp-sub', payload.productCount === null
      ? 'Ürün sayısı okunamadı'
      : `${num(payload.productCount)} ürün bu ailede`),
  );
  box.body.append(head);
  for (const warning of payload.warnings || []) box.body.append(alertBox(warning, 'warn'));

  const nameForm = formGrid({
    fields: [
      { key: 'code', label: 'Kod', type: 'static',
        hint: 'Aile kodu oluşturulduktan sonra değiştirilemez.' },
      { key: 'name', label: 'Aile adı', type: 'text', required: true, maxLength: 120 },
    ],
    value: { code: family.code, name: family.name },
  });
  forms.push(nameForm);

  const nameActions = h('div', 'sp-actions');
  nameActions.append(button('Yalnız adı kaydet', {
    variant: 'primary',
    title: 'Grup düzenine DOKUNMAZ: gövdeye `attribute_groups` konmaz.',
    onClick: async () => {
      if (!nameForm.valid()) { nameForm.showErrors(); return; }
      const reason = await askReason({
        title: 'Aile adını kaydet',
        description: 'Yalnız ad gönderilir; grup düzeni gövdeye KONMAZ ve mağazadaki '
          + 'düzen olduğu gibi kalır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Aile kaydediliyor…', async () => call(
        `${BASE}/families/${family.id}`,
        { method: 'PUT', body: { name: nameForm.draft().name, reason, dryRun: false } },
      ));
      if (!result) return;
      // `touchedGroups` backend'in "gövdeye `attribute_groups` koydum mu"
      // cevabıdır. Bu düğme grupları HİÇ göndermediği için beklenen `false`;
      // `true` gelirse gövde kurulumunda bir şey bozulmuş demektir ve
      // kullanıcı bunu sessizce öğrenmemeli.
      toast(result.touchedGroups
        ? 'Kaydedildi ama grup düzeni de gönderilmiş görünüyor — aileyi açıp düzeni '
          + 'doğrulayın.'
        : 'Ad kaydedildi; grup düzenine dokunulmadı.',
      result.touchedGroups ? 'warn' : 'good');
      done?.();
    },
  }));
  box.body.append(card('Künye', nameForm.node), nameActions);

  box.body.append(alertBox(payload.lockNotice, 'warn'));

  // --- GRUP DÜZENİ. Gönderilen liste mağazadakinin YERİNE geçtiği için düzen
  // ayrı bir düğmeyle ve ayrı bir onayla kaydedilir.
  const pool = (payload.pool || []).map((item) => ({
    id: item.id,
    name: `${item.code} — ${item.name}`,
    group: item.system ? 'Sistem' : 'Kullanıcı tanımlı',
    meta: item.typeLabel,
  }));
  const coreCodes = payload.coreCodes || [];
  const codeById = new Map((payload.pool || []).map((item) => [item.id, item.code]));

  const groups = family.groups.map((group) => ({
    code: group.code,
    name: group.name,
    column: group.column,
    position: group.position,
    attributeIds: group.attributes.map((item) => item.id),
  }));

  const groupsBox = h('div', 'sp-groups');
  const coreLine = h('div');
  const pickers = [];

  // Havuz olmadan çekirdek denetimi YAPILAMAZ: eşleme kimlik→kod havuzdan
  // geliyor ve havuz boşken her kod "eksik" görünürdü. Yanlış alarm vermek
  // yerine denetimin yapılamadığı söylenir; backend zaten aynı sebeple
  // (nitelik listesi okunamadı) düzeni kaydetmiyor.
  const poolReady = pool.length > 0;

  function paintCore() {
    if (!poolReady) {
      coreLine.replaceChildren(alertBox(
        'Atanabilir nitelik listesi okunamadı; hangi çekirdek niteliğin düzende olduğu '
        + 'DOĞRULANAMIYOR. Düzen kaydetme kapalı — doğrulanmadan kaydetmek ailenin '
        + 'gruplarını sessizce silebilir.', 'bad'));
      return;
    }
    const chosen = new Set();
    for (const picker of pickers) {
      for (const id of picker.selection()) chosen.add(codeById.get(Number(id)));
    }
    const missing = coreCodes.filter((code) => !chosen.has(code));
    coreLine.replaceChildren(missing.length
      ? alertBox(`Çekirdek nitelikler eksik: ${missing.join(', ')}. Bu düzen KAYDEDİLMEZ — `
        + 'ürün kaydı bu nitelikler olmadan açılmıyor ve sebebi ekranda görünmüyor.', 'bad')
      : hintBox(`Çekirdek nitelikler (${coreCodes.join(', ')}) düzende duruyor.`));
  }

  function paintGroups() {
    groupsBox.replaceChildren();
    pickers.length = 0;
    groups.forEach((group, index) => {
      const cell = h('div', 'sp-group');
      const nameInput = h('input', 'kit-input');
      nameInput.value = group.name;
      nameInput.addEventListener('input', () => { group.name = nameInput.value; });
      const columnInput = h('input', 'kit-input sp-narrow');
      columnInput.type = 'number';
      columnInput.min = '1';
      columnInput.max = '4';
      columnInput.value = String(group.column || 1);
      // Sunucu 1-4 arası kabul ediyor; sınırın dışına çıkan değer 422 döndürür.
      // Kullanıcıyı ağ turuna göndermek yerine burada sıkıştırılır.
      columnInput.addEventListener('input', () => {
        group.column = Math.max(1, Math.min(4, Number(columnInput.value) || 1));
      });

      const header = h('div', 'sp-group-head');
      header.append(
        labelled('Grup adı', nameInput), labelled('Sütun', columnInput),
        h('span', 'kit-spacer'),
        button('Grubu kaldır', {
          variant: 'danger',
          title: 'Yalnız bu ekrandan kaldırır; mağazaya ancak “Grup düzenini kaydet” '
            + 'derseniz gider.',
          onClick: () => { groups.splice(index, 1); paintGroups(); paintCore(); },
        }),
      );

      const picker = createPicker({
        items: pool,
        groupLabel: 'Kaynak',
        placeholder: 'Nitelik ara',
        onChange: (ids) => {
          group.attributeIds = ids.map(Number);
          paintCore();
        },
      });
      picker.select(group.attributeIds);
      pickers.push(picker);

      cell.append(header, picker.node);
      groupsBox.append(cell);
    });
    paintCore();
  }
  paintGroups();

  const layoutActions = h('div', 'sp-actions');
  layoutActions.append(
    button('Grup ekle', {
      onClick: () => {
        groups.push({ code: '', name: 'Yeni grup', column: 1, position: groups.length,
          attributeIds: [] });
        paintGroups();
      },
    }),
    button('Grup düzenini kaydet', {
      variant: 'danger',
      disabled: !poolReady,
      title: poolReady
        ? 'Gönderilen düzen mağazadakinin YERİNE geçer.'
        : 'Nitelik havuzu okunamadı; düzen doğrulanmadan kaydedilmez.',
      onClick: async () => {
        // Adsız grup şemada 422 üretir; hangi grubun adsız olduğunu ancak
        // ekran bilir, mağazanın hata metni "name alanı gerekli" der.
        const nameless = groups.findIndex((group) => !String(group.name || '').trim());
        if (nameless >= 0) {
          toast(`${nameless + 1}. grubun adı boş; adsız grup gönderilemez.`, 'bad');
          return;
        }
        if (!groups.length) {
          toast('Ailede en az bir grup olmalı; boş liste ailenin şemasını siler.', 'bad');
          return;
        }
        const go = await confirmSimple(nodes.root, {
          title: 'Grup düzenini kaydet',
          description: `${num(groups.length)} grup gönderilecek ve mağazadaki düzenin `
            + 'YERİNE geçecek: burada görünmeyen bir grup aileden düşer. Ailede '
            + `${payload.productCount === null ? 'bilinmeyen sayıda' : num(payload.productCount)}`
            + ' ürün var ve hepsi bu şemayı kullanıyor.',
          confirmLabel: 'Anladım, devam',
        });
        if (!go) return;
        const reason = await askReason({
          title: 'Grup düzenini kaydet',
          description: 'Gerekçe denetim kaydına yazılır. Çekirdek nitelik düşüren düzen '
            + 'backend tarafından reddedilir.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        const result = await withBusy('Aile düzeni kaydediliyor…', async () => call(
          `${BASE}/families/${family.id}`,
          {
            method: 'PUT',
            body: {
              name: nameForm.draft().name,
              groups: groups.map((group, index) => ({
                code: group.code, name: group.name, column: group.column,
                position: index, attributeIds: group.attributeIds,
              })),
              reason, dryRun: false,
            },
          },
        ));
        if (!result) return;
        toast('Aile düzeni kaydedildi.', 'good');
        if (result.notice) toast(result.notice, 'warn');
        box.close();
        done?.();
      },
    }),
  );

  box.body.append(card('Grup düzeni', groupsBox), coreLine, layoutActions);
}

async function newFamily(done) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Yeni aile',
    subtitle: 'Kod sonradan değiştirilemez',
    onClose: dropForms,
  });
  closers.push(dropForms);
  const form = formGrid({
    fields: [
      { key: 'code', label: 'Kod', type: 'text', required: true, maxLength: 50,
        hint: 'Küçük harf, rakam ve alt çizgi. SONRADAN DEĞİŞTİRİLEMEZ.' },
      { key: 'name', label: 'Aile adı', type: 'text', required: true, maxLength: 120,
        wide: true },
    ],
    value: { code: '', name: '' },
  });
  forms.push(form);

  const actions = h('div', 'sp-actions');
  actions.append(button('Aileyi aç', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); return; }
      const draft = form.draft();
      const reason = await askReason({
        title: 'Yeni aile aç',
        description: `\`${draft.code}\` açılacak. Grup düzeni bu adımda GÖNDERİLMEZ; `
          + 'aile açıldıktan sonra düzenlenir.',
        confirmLabel: 'Aç',
      });
      if (!reason) return;
      const result = await withBusy('Aile açılıyor…', async () => call(`${BASE}/families`, {
        method: 'POST',
        body: { code: draft.code, name: draft.name, reason, dryRun: false },
      }));
      if (!result) return;
      toast('Aile açıldı.', 'good');
      box.close();
      done?.();
    },
  }));

  box.body.append(form.node, actions,
    hintBox('Yeni aile GRUPSUZ açılır; ürün kaydı bu aileyle ancak çekirdek nitelikleri '
      + 'taşıyan bir grup eklendikten sonra açılabilir.'));
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel sp');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  // NİTELİK/AİLE SEKMESİ MENÜDE YOK — kod duruyor, girişi kapalı.
  //
  // Öznitelik ailesi Bagisto'da çok satıcılı / çok ürün tipli mağazalar için
  // var: farklı ürün grupları farklı alan setleri taşısın diye. Burada tek
  // satıcı ve tek ürün tipi (kitap) olduğu için aile bir kez kurulur ve bir
  // daha dokunulmaz; ürün açılırken de sorulmaz, backend sessizce çözer.
  //
  // Sekme SİLİNMEDİ çünkü niteliğin gerçek bir kullanımı olabilir (yayınevi,
  // sınıf düzeyi gibi vitrin süzgeci alanları eklemek). O gün gelirse tek
  // satır geri açar: aşağıdaki girdiyi listeye ekle.
  //   { key: 'schema', label: 'Nitelikler' },
  const tabs = tabBar([
    { key: 'list', label: 'Ürünler' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'list', (key) => showView(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ürün adı, stok kodu ya da barkod ara',
        width: '260px' },
      // SÜZGEÇLERDE VARSAYILAN "Hepsi": ekran hiçbir şeyi gizlemeden açılır.
      { kind: 'select', key: 'category', label: 'Kategori',
        options: [{ value: '', label: 'Hepsi — kategori farkı yok' }] },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Hepsi' },
        { value: '1', label: 'Satışta olanlar' },
        { value: '0', label: 'Vitrinde olmayanlar' },
      ] },
      { kind: 'select', key: 'type', label: 'Tür',
        options: [{ value: '', label: 'Hepsi — tür farkı yok' }] },
      // 'Aile' süzgeci KALDIRILDI: tek satıcılı mağazada her ürün aynı ailede,
      // dolayısıyla süzgeç hiçbir şeyi elemiyor — yalnız şeridi kalabalıklaştırıyordu.
      { kind: 'numRange', key: 'price', label: 'Fiyat', money: true },
    ],
    onChange: () => refresh({ page: 1 }),
    actions: [
      button('Yeni ürün ekle', {
        variant: 'primary',
        title: 'Sayfa adresi, üst kategoriler ve Google metinleri sizin yerinize doldurulur',
        onClick: () => newProduct(() => { refresh(); loadHealth(); }),
      }),
      // REFERANS DA YENİLENİR. `loadReference()` yalnız açılışta çağrılıyordu
      // ve hatası sessizce yutuluyor (K7): düştüğü oturumda kitap alanları,
      // desi katsayıları ve görsel sınırları BOŞ kalıyor, "Yenile" listeyi
      // tazelese bile o boşluk kalıcı oluyordu. Bir düğme "yenile" diyorsa
      // ekranın okuduğu her şeyi yenilemeli.
      button('Yenile', {
        title: 'Mağazadaki güncel fiyat, stok ve durumları yeniden okur',
        onClick: () => { loadReference().then(refresh); loadHealth(); },
      }),
      button('⤓ Ekrandakiler', {
        title: 'Şu an ekranda görünen sayfayı Excel dosyası olarak indirir',
        onClick: exportVisible,
      }),
      button('⤓ Hepsi', {
        title: 'Bütün ürünleri rapor klasörüne yazar; birkaç dakika sürebilir',
        onClick: exportAll,
      }),
      // TUZAK: `reportChain` gövdeyi `{kind, ...params}` olarak kuruyor.
      // `currentFilters()` içinde de `kind` var (ürün tipi) ve rapor türünü
      // eziyordu; rapor parametreleri açıkça verilir.
      button('Stok raporu', {
        title: 'Tükenen ve azalan ürünleri gösteren, yazdırılabilir PDF hazırlar',
        onClick: () => report.run('stock', reportParams()),
      }),
      button('Fiyat listesi', {
        title: 'Ürün ve fiyatlarını gösteren, yazdırılabilir PDF hazırlar',
        onClick: () => report.run('pricelist', reportParams()),
      }),
    ],
  });

  nodes.chips = chipRow(CHIPS, null, (key) => applyChip(key));
  nodes.status = statusLine();
  nodes.kpi = h('div', 'sp-kpi');
  nodes.selbar = h('div', 'sp-selbar');
  nodes.tableWrap = h('div', 'sp-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size }),
  });
  // Tek gövde kabı; sekme değişince İÇERİĞİ değişir. İki ayrı kap tutup
  // `replaceChild` yapmak, kaydırma konumunu ve düğüm kimliklerini kaybettiriyor.
  nodes.body = h('div', 'sp-body');
  const listView = [nodes.kpi, nodes.selbar, nodes.tableWrap, nodes.pager.node];
  nodes.body.append(...listView);

  view.append(tabs.node, nodes.filters.node, nodes.chips.node, nodes.status.node, nodes.body);

  function showView(key) {
    // Süzgeç şeridi, çipler ve durum satırı ÜRÜN listesine aittir; nitelik ve
    // ayar sekmesinde göstermek "bu süzgeç neyi süzüyor" sorusunu üretiyordu.
    const listing = key === 'list';
    nodes.filters.node.hidden = !listing;
    nodes.chips.node.hidden = !listing;
    nodes.status.node.hidden = !listing;
    if (key === 'settings') {
      nodes.body.replaceChildren();
      renderSettings(nodes.body);
    } else if (key === 'schema') {
      nodes.body.replaceChildren();
      renderSchema(nodes.body);
    } else {
      nodes.body.replaceChildren(...listView);
      if (!state.loaded) refresh();
    }
  }

  root.replaceChildren(view);
  nodes.status.set('Ürünler okunuyor…');
  // Referans listeler ÖNCE gelir: süzgeç açılırları dolmadan liste çekmek,
  // kullanıcının seçtiği kategoriyi kaybettiriyordu.
  loadReference().then(() => refresh());
  loadHealth();

  return () => {
    nodes.filters?.destroy();          // arama alanı bekleyen debounce tutar
    settingsForms.forEach((form) => form.destroy());
    settingsForms = [];
    schemaState = { view: 'attributes' };
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
