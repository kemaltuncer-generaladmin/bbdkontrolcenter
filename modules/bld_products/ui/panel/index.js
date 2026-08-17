// Ürün Yönetimi paneli — BLD ürün kataloğunun Kontrol Merkezi'nden yönetimi.
//
// NE YAPAR: sunucu tarafında sayfalanmış ürün listesi (küçük resim sütunuyla);
// satırdan açılan çekmecede sekmeli düzenleyici (künye · görsel · durum);
// kategori ağacı ve kategori düzenleme; gün bazlı "bugün tükendi" işareti;
// bu ekrandan yapılan yazma denemelerinin yerel dökümü.
//
// ÜRÜN KATALOĞU İLE GÜNLÜK MENÜ AYRI EKRANLARDIR. Burada bir ürün doğar,
// fiyatlanır, görsellenir ve satıştan kalkar; HANGİ GÜN satılacağı Günlük Menü
// ekranının işidir. İkisini birleştirmek "bugünkü menüden çıkar" ile
// "kataloğdan kaldır" arasındaki farkı görünmez kılardı.
//
// NE YAPMAZ:
//  · TAM LİSTEYİ ÇEKİP İSTEMCİDE SÜZMEZ. Sayfalama sözleşmenin kendi biçimidir
//    (`00-genel.md` §5) ve arama sunucuda ad ile açıklamada birlikte çalışır;
//    "hepsini indir sonra filtrele" paylaşılan 3000/saat kovasını yakardı.
//  · YOKLAMAZ. Ürün kataloğu haftalarca değişmez ve `00-genel.md` §2'deki
//    yoklama bütçesi tablosunda bu ekran YOKTUR. Tazeleme düğmeye bağlıdır.
//  · KAYIT SİLMEZ. Ürün satıştan kalkar (`menu_status = 0`), kategori gizlenir
//    (`status = false`). Gerçek silme geçmiş siparişlerin ürün bağını koparır.
//  · SEÇENEK DÜZENLEMEZ. `options` bu turda SALT OKUNURDUR (sözleşme kararı);
//    ekran gösterir, düğme çizmez ve nedenini yazar.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · PAKET ÜRÜNÜNÜN FİYATI BURADA DEĞİL. "Günün Menüsü" ürününün kendi fiyatı
//    0,00'dır; gerçek fiyat o günün paket fiyatıdır. Fiyat alanı kapalı çizilir
//    ve nedeni yanında yazar — yazmak günün menüsünü yanlış tutara satardı.
//  · SATIŞTAN KALDIRMA KÜNYE FORMUNDA YOKTUR. Ayrı yetki ister
//    (`bld_products.retire`) ve kendi düğmesinden, gerekçeli onayla yapılır.
//    Forma bir "durum" kutusu koymak o yetkiyi süs hâline getirirdi; backend
//    de `PATCH status: false` isteğini bu yüzden reddeder.
//  · "BUGÜN TÜKENDİ" İLE "SATIŞTAN KALDIR" AYRI ŞEYLERDİR. İlki bugüne özeldir
//    ve ertesi gün kendiliğinden düşer; normalde işareti mutfak kasası koyar.
//    İkincisi kalıcıdır. İkisi ayrı sekmede ve ayrı cümlelerle durur.
//  · TÜKENDİ GEREKÇESİ MUTFAKTA GÖRÜNÜR (`veykemtu_menu_soldout.reason`).
//    Gerekçe kutusunun ipucu bunu söyler; iç yazışma için ayrı bir not alanı var.
//  · GÖRSEL BASE64 GİDER, multipart DEĞİL. İmza ham gövdeyi hashliyor ve
//    gövdeyi yeniden kodlayan herhangi bir vekil imzayı bozar; arıza sahada
//    "sır yanlış" gibi görünürdü.
//  · KATEGORİ LİSTESİ TAM LİSTEDİR. Seçiciden çıkardığınız kategori üründen
//    de kalkar; fark gönderilmez, pivot tablo listeye eşitlenir.
//  · KATEGORİ SİLİNMEZ. Altındaki ürünleri kategorisiz bırakır ve site
//    menüsünü sessizce boşaltırdı; gizlemek `status = false` yazmaktır.
//  · KURU PROVA ARAYÜZDE YOKTUR. Bu ekrandan yapılan her yazma GERÇEKTİR.
//    Yanıttaki `dry_run` yine de okunur — bir kurulum provayı ayardan geri
//    açarsa ekran "yapıldı" DEMEMELİ.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_products/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_products/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, button, clip, confirmWithReason, h, loadStyles, money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { imageField } from '../../ui-kit/imagefield.js';
import { createPicker } from '../../ui-kit/picker.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';

const BASE = '/api/bld_products';

/** Gerekçe sınırları — sunucu da denetliyor (§3), bunlar erken geri bildirim. */
const REASON_MIN = 10;
const REASON_MAX = 500;

/** Ürün/kategori adı — sözleşme "2-128 karakter" diyor. */
const NAME_MAX = 128;

// ---------------------------------------------------------------- sözlükler

/**
 * Yerel deneme izinin eylem adları. Sunucudaki `veykemtu_control_audit`
 * karşılıkları AYNI adı taşır; iki defteri yan yana koyabilmek, "istek gitti
 * mi" sorusunun tek cevabıdır.
 */
const ACTION_LABELS = {
  'product.create': 'Ürün açıldı',
  'product.update': 'Ürün güncellendi',
  'product.delete': 'Satıştan kaldırıldı',
  'product.image': 'Görsel yüklendi',
  'product.image.delete': 'Görsel kaldırıldı',
  'product.sold_out': 'Tükendi işareti kondu',
  'product.sold_out.clear': 'Tükendi işareti kaldırıldı',
  'category.create': 'Kategori açıldı',
  'category.update': 'Kategori güncellendi',
};

/**
 * Yerel izin sonuç sütunu. `denendi` EN ÖNEMLİSİDİR: istek gönderilirken
 * bağlantı koptuysa satır burada kalır ve sunucunun defteri onu HİÇ bilmez.
 * Renk tek başına konuşmaz — her rozetin içinde yazı var.
 */
const RESULT_LABELS = {
  ok: 'Uygulandı',
  dry_run: 'Kuru prova',
  denendi: 'Sonucu bilinmiyor',
  hata: 'Hata',
  engellendi: 'Engellendi',
};
const RESULT_TONES = {
  ok: 'good', dry_run: 'info', denendi: 'warn', hata: 'bad', engellendi: 'dim',
};

/** Geçit hata kodu → kullanıcıya ne söylemeli. Kod yoksa ham metin yazılır. */
const CODE_HINTS = {
  control_endpoint_missing: 'Bu uç BLD sunucusuna henüz dağıtılmamış. Sunucu '
    + 'eklentisi güncellenince ekran kendiliğinden çalışır.',
  read_only: 'Geçitte acil fren açık: yazma istekleri BLD\'ye hiç gönderilmiyor. '
    + 'Kontrol Merkezi ayarındaki `read_only` kapatılmalı.',
  conflict: 'Kayıt aradan değişmiş ya da bağlı bir kayıt engelliyor. Listeyi '
    + 'tazeleyip yeniden deneyin.',
  config_missing: 'BLD sunucusunun adresi ya da imza sırrı tanımlı değil; istek '
    + 'hiç gönderilmedi.',
  unauthorized: 'BLD imzayı kabul etmedi: sır yanlış, saat kaymış ya da istek '
    + 'tekrar oynatıldı.',
  rate_limited: 'BLD hız sınırına takıldı. Birkaç dakika sonra yeniden deneyin.',
};

const EMPTY_STATE = {
  tab: 'products',
  page: 1,
  perPage: 25,
  sort: 'name',
  direction: 'asc',
  products: [],
  meta: { page: 1, per_page: 25, total: 0, last_page: 1 },
  categories: [],
  counts: {},
  imageRules: {},
  connected: true,
  error: '',
  code: '',
  // Bağlantı gözleri — istek başına ayrı. Ekranın okuduğu `connected/error/code`
  // bunlardan türetilir; gerekçesi `noteLink()` başlığında.
  link: {},
};

let api = null;
let toast = null;
let state = { ...EMPTY_STATE };
let busy = false;
const nodes = {};
/** Çekmece kapatıcıları ve bileşen `destroy()` çağrıları. */
const closers = [];

// ------------------------------------------------------------------ ağ

/**
 * Sunucu iki türlü hata döndürebilir: HTTP durumu (kabuk `api()` fırlatır) ve
 * gövdedeki `{ok: false, error}`. İkincisi bir istisna DEĞİLDİR ama çağıranın
 * onu da fark etmesi gerekir; bu sarmalayıcı ikisini tek yola indirger.
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
 * Yazma gövdesi. `dryRun` GÖNDERİLMEZ.
 *
 * Kuru prova arayüzden bilerek çıkarıldı: ekranın tepesinde duran bir "gerçek
 * mi değil mi" şalteri, yazmanın iki gerçek kapısını (ayrı izin ve gerekçe)
 * zayıflatan üçüncü bir kip yaratıyordu. Sunucudaki `dry_run` parametresi
 * duruyor (sözleşme §4 additive) ama buradan hiç kullanılmıyor.
 */
function writeBody(fields) {
  return { ...fields };
}

/** Hata metnini kullanıcıya çevirir: kod biliniyorsa NE YAPMASI gerektiğini yazar. */
function explain(failure) {
  const code = failure?.code || '';
  const hint = CODE_HINTS[code];
  const message = String(failure?.message || failure || 'İşlem başarısız.');
  return hint ? `${message} ${hint}` : message;
}

/** Uzun işlemler sırasında ikinci tıklamayı yutar; şeritte ne olduğunu yazar. */
async function withBusy(label, task) {
  if (busy) {
    toast('Önceki işlem sürüyor; bitmesini bekleyin.', 'warn');
    return;
  }
  busy = true;
  nodes.status?.set(label);
  try {
    await task();
  } catch (failure) {
    toast(explain(failure), 'bad');
  } finally {
    busy = false;
    nodes.status?.set(statusText(), !state.connected);
  }
}

/**
 * Yazma yanıtını duyurur. `dry_run` YANITTAN okunur, isteğe yazdığımızdan
 * değil: bir kurulum provayı geçidin ayarından geri açarsa ekran "yapıldı"
 * DEMEMELİ.
 */
function announce(result, message) {
  if (result?.dry_run) {
    toast(`${message} — ama KURU PROVA: BLD'de hiçbir şey değişmedi.`, 'warn');
    return false;
  }
  toast(message, 'good');
  return true;
}

// ------------------------------------------------------------- yardımcı

/**
 * Bağlantı hatasının kullanıcıya söylediği cümle.
 *
 * Ham hata metni tek başına "ne yapmalıyım" sorusunu cevaplamıyor; geçidin
 * `code` alanı biliniyorsa yapılacak iş de yazılır (`control_endpoint_missing`
 * "bekle", `read_only` "acil freni kapat" demektir ve ikisi ayrı iştir).
 */
function connectionNote() {
  const hint = CODE_HINTS[state.code];
  const message = state.error || 'sebep bilinmiyor';
  return hint ? `${message} ${hint}` : message;
}

/** Bağlantı durumunu ve kayıt sayısını tek satırda anlatır. */
function statusText() {
  if (!state.connected) {
    return `BLD'ye ulaşılamıyor — ${state.error || 'sebep bilinmiyor'}`;
  }
  const total = state.meta?.total ?? 0;
  return `Bağlı · ${num(total)} ürün · ${num(state.categories.length)} kategori`;
}

/**
 * Küçük resim kutusu. KIRIK BAĞLANTI SESSİZ KALMAZ.
 *
 * Deseni `store_products` panelinden gelir: görsel yoksa kutu "—" ile ve
 * neden önemli olduğunu söyleyen bir ipucuyla çizilir; adres var ama dosya
 * açılmıyorsa kutu "!" der. İkisi AYRI şeydir — biri "hiç yüklenmemiş",
 * öteki "yüklenmiş ama kaybolmuş"tur ve düzeltmeleri de ayrıdır.
 */
function thumb(row) {
  const box = h('span', 'bp-thumb');
  if (!row.image_url) {
    box.classList.add('none');
    box.title = 'Görsel yok — görselsiz ürün menü kartında boş bir kare bırakır.';
    box.textContent = '—';
    return box;
  }
  const image = h('img');
  image.loading = 'lazy';
  image.src = row.image_url;
  image.alt = '';
  image.addEventListener('error', () => {
    box.classList.add('none');
    box.title = 'Görsel açılmıyor — dosya silinmiş ya da adresi değişmiş olabilir. '
      + 'Ürünü açıp görseli yeniden yükleyin.';
    box.replaceChildren(document.createTextNode('!'));
  });
  box.append(image);
  return box;
}

/** Ürün adı hücresi: ad + durum rozetleri. Renk tek başına anlam taşımaz. */
function nameCell(row) {
  const box = h('span', 'bp-name');
  const title = h('b');
  clip(title, row.name || '(adsız)', 46);
  box.append(title);

  const marks = h('span', 'bp-marks');
  if (!row.status) marks.append(badge('satıştan kaldırılmış', 'dim'));
  if (row.sold_out_today) marks.append(badge('bugün tükendi', 'warn'));
  if (row.is_package_product) marks.append(badge('paket ürünü', 'info'));
  if (!row.category_ids.length) marks.append(badge('kategorisiz', 'warn'));
  if (marks.childNodes.length) box.append(marks);
  return box;
}

/** Ürünün kategorileri — adları kategori ağacından çözülür. */
function categoryNames(row) {
  const names = row.category_ids
    .map((id) => state.categories.find((item) => item.category_id === id)?.name)
    .filter(Boolean);
  if (!names.length) {
    const box = h('span', 'bp-dim', 'kategorisiz');
    box.title = 'Kategorisiz ürün sitede görünmez ama günlük menüde kullanılabilir.';
    return box;
  }
  const box = h('span');
  clip(box, names.join(', '), 40);
  return box;
}

/** Fiyat hücresi. Paket ürününde tutar yerine nereye bakılacağı yazar. */
function priceCell(row) {
  const box = h('span', 'bp-price');
  if (row.price_locked) {
    box.append(badge('günün menüsünde', 'info'));
    box.title = 'Paket ürününün fiyatı o günün paket fiyatıdır; katalogda 0,00 durur.';
    return box;
  }
  box.append(h('b', undefined, money(row.price_kurus)));
  if (row.price_kurus === 0) {
    box.append(badge('ücretsiz', 'dim'));
    box.title = 'Sıfır fiyat geçerlidir: paket bileşeni olarak satılan ekmek, ayran.';
  }
  return box;
}

/** Kategori seçicisinin beslendiği liste. Gizli kategoriler AYRI işaretlenir. */
function categoryItems() {
  return state.categories.map((row) => ({
    id: String(row.category_id),
    name: `${'— '.repeat(row.depth)}${row.name}`,
    group: row.status ? 'Görünür' : 'Gizli',
    meta: `${num(row.menu_count)} ürün`,
  }));
}

// ------------------------------------------------------------------ veri

/**
 * BAĞLANTI DURUMU ÜÇ OKUMADAN GELİR VE BİRLEŞTİRİLİR.
 *
 * Kategoriler, özet ve liste aynı `connected/error/code` alanlarına yazıyordu.
 * Sıralı çalışırken "son yazan" belliydi (hep `refreshProducts`) ve önündeki
 * hatayı sessizce siliyordu. Paralelde son yazan YARIŞA bağlı olur: aynı arıza
 * bir açılışta "kategoriler okunamadı", ötekinde "liste okunamadı" derdi ve
 * kullanıcı hatayı yanlış yerde arardı.
 *
 * Bu yüzden her okuma yalnız KENDİ gözünü yazar; ekranın gördüğü tek durum
 * gözlerden SABİT bir sırayla türetilir — kategoriler → özet → liste, yani eski
 * sıralı akışın sırası. Mesaj artık bitiş sırasına değil, verinin mantıksal
 * sırasına göre seçilir ve iki koşuda aynı çıkar.
 *
 * BAĞLANTI YAPIŞKAN, HATA İLK GÖZDEN: bir göz bile kopuksa ekran "bağlı"
 * demez; gösterilen cümle sıradaki ilk arızalı gözündür. Tek başına yenilenen
 * bir okuma da doğru çalışır — yalnız kendi gözünü tazeler, ötekilerin süren
 * arızasını silmez.
 */
const LINK_ORDER = ['categories', 'overview', 'products'];

function noteLink(source, connected, error = '', code = '') {
  // Göz kümesi KOPYALANARAK değişir: `state` EMPTY_STATE'ten sığ kopyalanıyor
  // ve iç sözlüğü yerinde değiştirmek şablonu kirletirdi.
  state.link = { ...state.link, [source]: { connected, error, code } };
  const eyes = LINK_ORDER.map((name) => state.link[name]).filter(Boolean);
  const broken = eyes.find((eye) => !eye.connected || eye.error);
  state.connected = !eyes.some((eye) => !eye.connected);
  state.error = broken?.error || '';
  state.code = broken?.code || '';
}

async function refreshOverview() {
  try {
    const payload = await api(`${BASE}/overview`);
    state.counts = payload?.counts || {};
    state.imageRules = payload?.filters?.image || {};
    noteLink('overview', payload?.connected !== false,
             payload?.error || '', payload?.code || '');
  } catch (failure) {
    noteLink('overview', false, explain(failure));
  }
}

async function refreshCategories() {
  try {
    const payload = await api(`${BASE}/categories`);
    state.categories = payload?.items || [];
    noteLink('categories', payload?.connected !== false,
             payload?.error || '', payload?.code || '');
  } catch (failure) {
    state.categories = [];
    noteLink('categories', false, explain(failure));
  }
}

async function refreshProducts() {
  const values = nodes.filters?.values() || {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.category) params.set('category_id', values.category);
  if (values.status) params.set('status', values.status);
  // ÜÇ DEĞERLİ: süzgeç kapalıyken alan HİÇ GÖNDERİLMEZ. `false` göndermek
  // "yalnız tükenmemişler" demek olurdu ve liste sessizce süzülürdü.
  if (values.soldOut) params.set('sold_out', 'true');
  params.set('sort', state.sort);
  params.set('direction', state.direction);
  params.set('page', String(state.page));
  params.set('per_page', String(state.perPage));

  try {
    const payload = await api(`${BASE}/products?${params.toString()}`);
    state.products = payload?.items || [];
    state.meta = payload?.meta || EMPTY_STATE.meta;
    noteLink('products', payload?.connected !== false,
             payload?.error || '', payload?.code || '');
  } catch (failure) {
    state.products = [];
    noteLink('products', false, explain(failure));
  }
}

// ------------------------------------------------------------- ürün sekmesi

function showProducts() {
  nodes.body.replaceChildren(skeletonRows(8, 6));
  withBusy('Katalog okunuyor…', async () => {
    // ÜÇÜ BİRDEN. Aralarında bağ yok: hiçbiri ötekinin sonucunu okumuyor,
    // sıralı beklemek yalnız üç uzak turu arka arkaya eklemek demekti (ölçümde
    // tur başına ~0,5-1,2 sn). Üçü de kendi `try/catch`ini taşıdığı için
    // `Promise.all` reject etmez ve bir isteğin patlaması ötekini düşürmez (K7);
    // ortak `connected/error` alanları `noteLink()` ile birleştirilir.
    await Promise.all([refreshCategories(), refreshOverview(), refreshProducts()]);
    paintProducts();
  });
}

function paintProducts() {
  const view = h('div', 'bp-tabview');

  // --- sayaçlar ---------------------------------------------------------
  const counts = state.counts || {};
  const tiles = [
    { label: 'Katalogdaki ürün', value: num(counts.total ?? 0),
      title: 'Satışta olan ve olmayan bütün ürünler.' },
    { label: 'Satıştan kaldırılmış', value: num(counts.inactive ?? 0), tone: 'muted',
      title: 'Kaydı duruyor, müşteri göremiyor. Ürünü açıp yeniden satışa alabilirsiniz.' },
    { label: 'Bugün tükendi', value: num(counts.sold_out ?? 0), tone: 'warn',
      title: 'Bugüne özel işaret; ertesi gün kendiliğinden düşer.' },
    { label: 'Kategori', value: num(counts.categories ?? 0),
      title: 'Site menüsünü bu ağaç çiziyor.' },
  ];
  if ((counts.categories_hidden ?? 0) > 0) {
    tiles.push({ label: 'Gizli kategori', value: num(counts.categories_hidden),
      tone: 'muted', title: 'Kategori duruyor ama sitede görünmüyor.' });
  }
  view.append(kpiRow(tiles));

  if (!state.connected) {
    view.append(alertBox(`BLD sunucusuna ulaşılamıyor: ${connectionNote()}`
      + ' Aşağıdaki liste boş; katalogda ürün olmadığı anlamına GELMEZ.', 'bad'));
  }

  // --- tablo ------------------------------------------------------------
  const table = dataTable({
    columns: [
      { key: 'image', label: 'Görsel', width: '76px', cell: thumb },
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2.2fr)', sortable: true,
        cell: nameCell },
      { key: 'categories', label: 'Kategori', width: 'minmax(0, 1.4fr)',
        cell: categoryNames },
      { key: 'price', label: 'Fiyat', width: '160px', align: 'num', sortable: true,
        cell: priceCell },
      { key: 'priority', label: 'Sıra', width: '80px', align: 'num', sortable: true,
        cell: (row) => num(row.priority),
        title: 'Küçük olan menüde önce görünür.' },
      { key: 'updated', label: 'Son değişiklik', width: '150px', sortable: true,
        cell: (row) => {
          const box = h('span', 'bp-dim', ago(row.updated_at) || '—');
          box.title = stampIso(row.updated_at) || '';
          return box;
        } },
    ],
    rows: state.products,
    rowKey: (row) => String(row.menu_id),
    sort: { key: state.sort, dir: state.direction },
    // SIRALAMA SUNUCUDADIR: liste sayfalı ve yalnız görünen sayfayı sıralamak
    // "en pahalı ürün" sorusuna yanlış cevap verirdi.
    onSort: (key, dir) => {
      const map = { name: 'name', price: 'price', priority: 'priority', updated: 'updated' };
      if (!map[key]) return;
      state.sort = map[key];
      state.direction = dir;
      state.page = 1;
      savePrefs({ sort: state.sort, direction: state.direction });
      withBusy('Sıralanıyor…', async () => { await refreshProducts(); paintProducts(); });
    },
    onRow: (row) => openProduct(row.menu_id),
    empty: emptyState({
      title: state.connected ? 'Bu süzgece uyan ürün yok' : 'Liste okunamadı',
      text: state.connected
        ? 'Süzgeci temizleyip yeniden bakın; katalogda satıştan kaldırılmış ürünler '
          + 'de listelenir.'
        : 'Bağlantı gelince "Yenile" ile tekrar deneyin.',
      actions: state.connected
        ? [button('Süzgeci temizle', { onClick: () => nodes.filters?.reset() })]
        : [],
    }),
  });
  nodes.table = table;

  const strip = pager({
    total: state.meta.total || 0,
    page: state.meta.page || 1,
    size: state.meta.per_page || state.perPage,
    onChange: ({ page, size }) => {
      state.page = page;
      state.perPage = size;
      savePrefs({ page_size: size });
      withBusy('Sayfa yükleniyor…', async () => { await refreshProducts(); paintProducts(); });
    },
  });

  view.append(card('Ürünler', table.node), strip.node);
  nodes.body.replaceChildren(view);
  nodes.status.set(statusText(), !state.connected);

  // Kategori süzgeci ancak kategoriler geldikten sonra dolabilir.
  nodes.filters?.options('category', [
    { value: '', label: 'Tüm kategoriler' },
    ...state.categories.map((row) => ({
      value: String(row.category_id),
      label: `${'— '.repeat(row.depth)}${row.name}`,
    })),
  ]);
}

/** Ekran tercihi YEREL tabloya yazılır; BLD'yi etkilemez, gerekçe istemez. */
function savePrefs(values) {
  api(`${BASE}/prefs`, { method: 'PUT', body: { values } }).catch(() => {
    /* tercih yazılamadı: ekran çalışmaya devam eder, varsayılan geçerlidir */
  });
}

// ------------------------------------------------------------ ürün çekmecesi

async function openProduct(menuId) {
  await withBusy('Ürün açılıyor…', async () => {
    const payload = await api(`${BASE}/products/${menuId}`);
    if (!payload?.ok) {
      toast(payload?.error || 'Ürün okunamadı.', 'bad');
      return;
    }
    drawProductDrawer(payload.product, payload.options_read_only !== false);
  });
}

function drawProductDrawer(product, optionsReadOnly) {
  const box = drawer(nodes.root, {
    title: product.name || '(adsız ürün)',
    subtitle: `#${product.menu_id} · ${product.status ? 'satışta' : 'satıştan kaldırılmış'}`
      + (product.sold_out_today ? ' · bugün tükendi' : ''),
    onClose: () => {
      form?.destroy();
      images?.destroy();
      const index = closers.indexOf(box.close);
      if (index >= 0) closers.splice(index, 1);
    },
  });
  closers.push(box.close);

  let form = null;
  let images = null;
  let picker = null;

  const pane = h('div', 'bp-pane');
  const tabs = tabBar([
    { key: 'card', label: 'Künye' },
    { key: 'image', label: 'Görsel' },
    { key: 'state', label: 'Durum' },
  ], 'card', (key) => paint(key));

  box.body.append(tabs.node, pane);

  function paint(key) {
    // Sekme değişince önceki bileşenler bırakılır: `formGrid` tarih alanı,
    // `imageField` nesne URL'i tutuyor ve ikisi de kendiliğinden gitmez.
    form?.destroy();
    images?.destroy();
    form = null;
    images = null;
    picker = null;
    pane.replaceChildren(({ card: cardTab, image: imageTab, state: stateTab }[key]
      || cardTab)());
  }

  // ------------------------------------------------------------ künye
  function cardTab() {
    const wrap = h('div');

    if (product.price_locked) {
      wrap.append(hintBox('Bu ürün günün menüsünün paket kalemidir: fiyatı o günün '
        + 'paket fiyatında tanımlıdır ve buradan yazılamaz. Tutarı değiştirmek için '
        + 'Günlük Menü ekranındaki paket fiyatını düzenleyin.'));
    }

    form = formGrid({
      fields: [
        { key: 'name', label: 'Ürün adı', type: 'text', required: true,
          maxLength: NAME_MAX, wide: true,
          hint: 'Aynı adda ikinci bir ürün açılabilir — "Tavuk Sote" iki farklı '
            + 'tarifle iki ürün olabilir.' },
        { key: 'description', label: 'Açıklama', type: 'textarea', wide: true,
          hint: 'Menü kartında ürün adının altında görünür.' },
        { key: 'price_kurus', label: 'Fiyat', type: 'money', min: 0,
          readOnly: product.price_locked,
          hint: product.price_locked
            ? 'Paket ürününde kapalı.'
            : 'Sıfır geçerlidir: paket bileşeni olarak satılan ekmek, ayran.' },
        { key: 'minimum_qty', label: 'En az adet', type: 'number', min: 0,
          hint: 'Müşteri bu üründen en az kaç tane sipariş edebilir.' },
        { key: 'priority', label: 'Sıra numarası', type: 'number',
          hint: 'Küçük olan menüde önce görünür.' },
      ],
      value: {
        name: product.name,
        description: product.description,
        price_kurus: product.price_kurus,
        minimum_qty: product.minimum_qty,
        priority: product.priority,
      },
    });
    wrap.append(form.node);

    // --- kategoriler (TAM LİSTE) ---
    picker = createPicker({
      items: categoryItems(),
      groupLabel: 'Görünürlük',
      placeholder: 'Kategori ara',
      onChange: () => { /* seçim kaydetme anında okunur */ },
    });
    picker.select(product.category_ids.map(String));
    wrap.append(card('Kategoriler', picker.node,
      'Kaydettiğinizde bu liste ürünün TAM kategori listesi olur: seçimden '
      + 'çıkardığınız kategori üründen de kalkar. Kategorisiz ürün sitede '
      + 'görünmez ama günlük menüde kullanılabilir.'));

    if (product.options.length) {
      wrap.append(card('Seçenekler', optionList(product.options),
        optionsReadOnly
          ? 'Seçenekler bu ekrandan düzenlenmez (sözleşme kararı); değişiklik '
            + 'TastyIgniter yönetim panelinden yapılır. Kimlikler sipariş '
            + 'revizyonunda aynen kullanılıyor.'
          : ''));
    }

    const actions = h('div', 'bp-actions');
    actions.append(button('Değişiklikleri kaydet', {
      variant: 'primary',
      onClick: () => saveProduct(),
    }));
    wrap.append(actions);
    return wrap;
  }

  function optionList(options) {
    const list = h('div', 'bp-options');
    for (const option of options) {
      const row = h('div', 'bp-option');
      row.append(h('b', undefined, option.name || `#${option.id}`));
      if (option.required) row.append(badge('zorunlu', 'info'));
      row.append(h('span', 'bp-dim', option.type || ''));
      for (const value of option.values) {
        const line = h('div', 'bp-option-value');
        line.append(h('span', undefined, value.name || `#${value.id}`));
        line.append(h('span', 'bp-dim',
          value.price_delta_kurus ? `+${money(value.price_delta_kurus)}` : 'ek ücret yok'));
        row.append(line);
      }
      list.append(row);
    }
    return list;
  }

  async function saveProduct() {
    const errors = form.errors();
    if (errors.length) {
      form.showErrors();
      toast(errors[0].message, 'bad');
      return;
    }
    const fields = form.patch();
    const chosen = picker.selection().map(Number).sort((a, b) => a - b);
    const before = [...product.category_ids].sort((a, b) => a - b);
    if (JSON.stringify(chosen) !== JSON.stringify(before)) {
      // TAM LİSTE gönderilir; fark göndermek, iki kategoriden birini
      // kaldırmanın adını gerektirirdi.
      fields.category_ids = chosen;
    }
    if (!Object.keys(fields).length) {
      toast('Değişen alan yok.', 'warn');
      return;
    }

    const reason = await askReason({
      title: 'Ürün güncellenecek',
      description: `“${product.name}” için ${Object.keys(fields).length} alan `
        + 'değişiyor. Gerekçe BLD denetim kaydına yazılır.',
      confirmLabel: 'Kaydet',
      danger: false,
    });
    if (!reason) return;

    await withBusy('Ürün kaydediliyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}`, {
        method: 'PATCH', body: writeBody({ reason, fields }),
      });
      if (announce(result, 'Ürün güncellendi.')) {
        box.close();
        await reloadList();
      }
    });
  }

  // ------------------------------------------------------------ görsel
  function imageTab() {
    const wrap = h('div');

    const current = h('div', 'bp-current');
    if (product.image_url) {
      const picture = h('img', 'bp-current-img');
      picture.src = product.image_url;
      picture.alt = '';
      picture.addEventListener('error', () => {
        current.replaceChildren(alertBox('Kayıtlı görselin adresi var ama dosya '
          + 'açılmıyor — silinmiş ya da taşınmış olabilir. Yenisini yükleyin.', 'warn'));
      });
      current.append(picture);
      current.append(button('Görseli kaldır', {
        variant: 'danger',
        title: 'Ürünün görsel bağını kaldırır; ürün kaydı SİLİNMEZ.',
        onClick: () => removeImage(),
      }));
    } else {
      current.append(hintBox('Bu üründe görsel yok. Menü kartında ilk dört kalemin '
        + 'görseli 2×2 ızgarada diziliyor; boş kalan kutu kartı eksik gösterir.'));
    }
    wrap.append(card('Şu anki görsel', current));

    const rules = state.imageRules || {};
    images = imageField({
      // Kurallar SUNUCUDAN gelir: sınırı panelde tekrar yazmak, sözleşme
      // değiştiğinde ekranın yanlış cümle kurması demekti.
      rules: { accept: rules.accept || [], maxBytes: rules.max_bytes || 0 },
      multiple: false,
      reorder: false,
      limit: 1,
      label: 'Görsel seç',
      dropText: 'Görseli buraya sürükleyip bırakın',
      emptyText: 'Henüz görsel seçilmedi.',
    });
    const uploader = h('div');
    const send = h('div', 'bp-actions');
    send.append(button('Görseli yükle', {
      variant: 'primary',
      onClick: () => uploadImage(),
    }));
    uploader.append(images.node, send);
    wrap.append(card('Yeni görsel', uploader,
      'Dosya JSON gövdesinin içinde base64 olarak gider; multipart imzayı bozardı. '
      + 'Tür dosya adından değil İÇERİKTEN okunur — uzantı değiştirmek yardımcı olmaz.'));
    return wrap;
  }

  async function uploadImage() {
    if (!images || images.count() === 0) {
      toast('Önce bir görsel seçin.', 'warn');
      return;
    }
    const [file] = await images.payload();
    const reason = await askReason({
      title: 'Ürün görseli yüklenecek',
      description: `“${product.name}” ürününün görseli “${file.filename}” ile `
        + 'değiştirilecek. Eski görsel yerini yenisine bırakır.',
      confirmLabel: 'Yükle',
      danger: false,
    });
    if (!reason) return;

    await withBusy('Görsel yükleniyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}/image`, {
        method: 'PUT',
        // İçerik `data:` URI olarak gider; base64 çözme, boyut ve İÇERİKTEN
        // tür okuma geçidin işidir (`upload.py`) ve tek yerde durur.
        body: writeBody({ reason, filename: file.filename, content: file.content }),
      });
      if (announce(result, 'Görsel yüklendi.')) {
        box.close();
        await reloadList();
      }
    });
  }

  async function removeImage() {
    const reason = await askReason({
      title: 'Görsel kaldırılacak',
      description: `“${product.name}” ürününün görseli kaldırılacak. Ürün kaydı `
        + 'SİLİNMEZ, yalnız görsel bağı kalkar.',
      confirmLabel: 'Görseli kaldır',
    });
    if (!reason) return;
    await withBusy('Görsel kaldırılıyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}/image`, {
        method: 'DELETE', body: writeBody({ reason }),
      });
      if (announce(result, 'Görsel kaldırıldı.')) {
        box.close();
        await reloadList();
      }
    });
  }

  // ------------------------------------------------------------- durum
  function stateTab() {
    const wrap = h('div');

    // --- bugüne özel tükendi ---
    const today = h('div', 'bp-block');
    if (product.sold_out_today) {
      today.append(alertBox(`Bugün tükendi olarak işaretli${product.sold_out_reason
        ? `: “${product.sold_out_reason}”` : ''}. Bu işaret YARIN kendiliğinden `
        + 'düşer.', 'warn'));
      today.append(button('Tükendi işaretini kaldır', {
        onClick: () => clearSoldOut(),
      }));
    } else {
      today.append(h('p', 'bp-text', 'Ürün bugün satışta. "Bugün tükendi" işareti '
        + 'yalnız bugünü kapatır ve yarın kendiliğinden düşer; normalde bu işareti '
        + 'mutfak kasası koyar. Buradan konması, kasa çöktüğünde ya da yönetici '
        + 'sahada değilken ürünü satıştan çekmenin tek yolu olduğu içindir.'));
      const note = h('textarea', 'kit-textarea');
      note.placeholder = 'İç not (isteğe bağlı) — yalnız denetim kaydına yazılır';
      note.maxLength = 500;
      today.append(note);
      today.append(button('Bugün için tükendi işaretle', {
        variant: 'primary',
        onClick: () => markSoldOut(note.value),
      }));
    }
    wrap.append(card('Bugün', today,
      'Gün bazlıdır ve ertesi gün kendiliğinden düşer. Kalıcı kaldırma aşağıdadır.'));

    // --- kalıcı ---
    const permanent = h('div', 'bp-block');
    if (product.status) {
      permanent.append(h('p', 'bp-text', 'Ürünü satıştan kaldırmak kaydı SİLMEZ: '
        + 'geçmiş siparişlerdeki bağ korunur ve ürün istendiğinde yeniden satışa '
        + 'açılabilir. Ürün yayınlanmış bir günlük menüde kullanılıyorsa sunucu '
        + 'engeller ve hangi günler olduğunu söyler; önce menüden çıkarmanız gerekir.'));
      permanent.append(button('Satıştan kaldır', {
        variant: 'danger',
        title: 'Ayrı yetki ister (bld_products.retire) ve gerekçe alınır.',
        onClick: () => retire(),
      }));
    } else {
      permanent.append(alertBox('Bu ürün satıştan kaldırılmış: kaydı duruyor ama '
        + 'müşteri göremiyor.', 'info'));
      permanent.append(button('Yeniden satışa aç', {
        variant: 'primary',
        onClick: () => reopen(),
      }));
    }
    wrap.append(card('Katalog durumu', permanent));
    return wrap;
  }

  async function markSoldOut(note) {
    const reason = await askReason({
      title: 'Bugün için tükendi işaretlenecek',
      description: 'Gerekçe MUTFAK EKRANINDA da görünür — "neden yok" sorusunun '
        + 'cevabı orada okunuyor. İşaret yarın kendiliğinden düşer.',
      confirmLabel: 'Tükendi işaretle',
    });
    if (!reason) return;
    await withBusy('İşaret konuyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}/sold-out`, {
        method: 'POST', body: writeBody({ reason, note: note || '' }),
      });
      if (announce(result, 'Ürün bugünlük satıştan çekildi.')) {
        box.close();
        await reloadList();
      }
    });
  }

  async function clearSoldOut() {
    const reason = await askReason({
      title: 'Tükendi işareti kaldırılacak',
      description: 'Ürün bugün yeniden satışa açılır.',
      confirmLabel: 'İşareti kaldır',
      danger: false,
    });
    if (!reason) return;
    await withBusy('İşaret kaldırılıyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}/sold-out`, {
        method: 'DELETE', body: writeBody({ reason }),
      });
      if (announce(result, 'Ürün yeniden satışta.')) {
        box.close();
        await reloadList();
      }
    });
  }

  async function retire() {
    const reason = await askReason({
      title: 'Ürün satıştan kaldırılacak',
      description: `“${product.name}” siteden ve sipariş yolundan düşer. Kayıt `
        + 'SİLİNMEZ; istendiğinde yeniden satışa açılabilir.',
      confirmLabel: 'Satıştan kaldır',
    });
    if (!reason) return;
    await withBusy('Ürün satıştan kaldırılıyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}/retire`, {
        method: 'POST', body: writeBody({ reason }),
      });
      if (announce(result, 'Ürün satıştan kaldırıldı.')) {
        box.close();
        await reloadList();
      }
    });
  }

  async function reopen() {
    const reason = await askReason({
      title: 'Ürün yeniden satışa açılacak',
      description: `“${product.name}” tekrar sitede ve sipariş yolunda görünür.`,
      confirmLabel: 'Satışa aç',
      danger: false,
    });
    if (!reason) return;
    await withBusy('Ürün satışa açılıyor…', async () => {
      const result = await call(`${BASE}/products/${product.menu_id}`, {
        method: 'PATCH', body: writeBody({ reason, fields: { status: true } }),
      });
      if (announce(result, 'Ürün yeniden satışta.')) {
        box.close();
        await reloadList();
      }
    });
  }

  paint('card');
}

/**
 * Gerekçe kutusu. Alt sınır sunucudakiyle AYNI (10): burada 3 istemek,
 * kullanıcının yazdığı gerekçenin sunucuda reddedilmesi demekti.
 */
function askReason({ title, description, confirmLabel, danger = true }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN}, en çok ${REASON_MAX} karakter)`,
  });
}

async function reloadList() {
  // Yazma sonrası tazeleme de PARALEL: sayaçlar ile liste birbirinden bağımsız
  // okunuyor ve ikisi de kendi hatasını yutuyor.
  await Promise.all([refreshOverview(), refreshProducts()]);
  if (state.tab === 'products') paintProducts();
}

// --------------------------------------------------------------- yeni ürün

async function openNewProduct() {
  const box = drawer(nodes.root, {
    title: 'Yeni ürün',
    subtitle: 'Katalog kaydı açılır; hangi gün satılacağı Günlük Menü ekranının işidir.',
    onClose: () => {
      form?.destroy();
      const index = closers.indexOf(box.close);
      if (index >= 0) closers.splice(index, 1);
    },
  });
  closers.push(box.close);

  const form = formGrid({
    fields: [
      { key: 'name', label: 'Ürün adı', type: 'text', required: true,
        maxLength: NAME_MAX, wide: true,
        hint: 'Aynı adda ikinci bir ürün açılabilir; ekran uyarır, sunucu engellemez.' },
      { key: 'description', label: 'Açıklama', type: 'textarea', wide: true },
      { key: 'price_kurus', label: 'Fiyat', type: 'money', required: true, min: 0,
        hint: 'Sıfır geçerlidir: paket bileşeni olarak satılan ekmek, ayran.' },
      { key: 'minimum_qty', label: 'En az adet', type: 'number', min: 0 },
      { key: 'priority', label: 'Sıra numarası', type: 'number',
        hint: 'Küçük olan menüde önce görünür.' },
      { key: 'status', label: 'Açılışta satışta olsun', type: 'checkbox',
        hint: 'Kapalı bırakırsanız ürün kayıtlı ama satış dışı doğar.' },
    ],
    value: { minimum_qty: 1, priority: 0, status: true, price_kurus: 0 },
  });

  const picker = createPicker({
    items: categoryItems(),
    groupLabel: 'Görünürlük',
    placeholder: 'Kategori ara',
  });

  const actions = h('div', 'bp-actions');
  actions.append(button('Ürünü aç', { variant: 'primary', onClick: () => save() }));

  box.body.append(
    form.node,
    card('Kategoriler', picker.node,
      'Boş bırakılabilir: kategorisiz ürün sitede görünmez ama günlük menüde '
      + 'kullanılabilir.'),
    actions,
  );

  async function save() {
    const errors = form.errors();
    if (errors.length) {
      form.showErrors();
      toast(errors[0].message, 'bad');
      return;
    }
    const draft = form.draft();
    const name = String(draft.name || '').trim();
    const twin = state.products.find(
      (row) => row.name.toLocaleLowerCase('tr') === name.toLocaleLowerCase('tr'));
    if (twin) {
      toast(`Bu sayfada aynı adda bir ürün var (#${twin.menu_id}). Engellenmiyor — `
        + 'iki farklı tarif aynı adı taşıyabilir.', 'warn');
    }

    const reason = await askReason({
      title: 'Yeni ürün açılacak',
      description: `“${name}” kataloğa eklenecek.`,
      confirmLabel: 'Ürünü aç',
      danger: false,
    });
    if (!reason) return;

    await withBusy('Ürün açılıyor…', async () => {
      const result = await call(`${BASE}/products`, {
        method: 'POST',
        body: writeBody({
          reason,
          name,
          description: draft.description || null,
          price_kurus: Number(draft.price_kurus || 0),
          minimum_qty: Number(draft.minimum_qty ?? 1),
          priority: Number(draft.priority || 0),
          status: Boolean(draft.status),
          category_ids: picker.selection().map(Number),
        }),
      });
      if (announce(result, 'Ürün kataloğa eklendi.')) {
        box.close();
        await reloadList();
      }
    });
  }
}

// ----------------------------------------------------------- kategori sekmesi

function showCategories() {
  nodes.body.replaceChildren(skeletonRows(6, 4));
  withBusy('Kategoriler okunuyor…', async () => {
    await refreshCategories();
    paintCategories();
  });
}

function paintCategories() {
  const view = h('div', 'bp-tabview');

  if (!state.connected) {
    view.append(alertBox(`Kategoriler okunamadı: ${connectionNote()}`, 'bad'));
  }

  view.append(hintBox('Kategori SİLİNMEZ. Silmek altındaki ürünleri kategorisiz '
    + 'bırakır ve site menüsünü sessizce boşaltırdı; gizlemek için kategoriyi açıp '
    + '"Sitede görünsün" kutusunu kapatın.'));

  // Düğmeler bu sekmenin İÇİNDEDİR: üstteki süzgeç şeridi yalnız ürün
  // sekmesinde çiziliyor ve kategori açma düğmesini oraya koymak, kullanıcıyı
  // düğmeyi bulmak için başka bir sekmeye göndermek olurdu.
  const tools = h('div', 'bp-actions');
  tools.append(
    button('Yeni kategori', { variant: 'primary', onClick: () => openCategory(null) }),
    button('Yenile', { onClick: () => showCategories() }),
  );
  view.append(tools);

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kategori', width: 'minmax(0, 2fr)',
        cell: (row) => {
          const box = h('span', 'bp-name');
          const title = h('b', undefined, `${'— '.repeat(row.depth)}${row.name}`);
          box.append(title);
          if (!row.status) box.append(badge('gizli', 'dim'));
          return box;
        } },
      { key: 'menu_count', label: 'Ürün', width: '90px', align: 'num',
        cell: (row) => num(row.menu_count) },
      { key: 'priority', label: 'Sıra', width: '80px', align: 'num',
        cell: (row) => num(row.priority),
        title: 'Küçük olan sitede önce görünür.' },
      { key: 'slug', label: 'Site adresi', width: 'minmax(0, 1fr)',
        cell: (row) => {
          const box = h('span', 'bp-dim');
          clip(box, row.slug || '—', 32);
          box.title = 'Adres addan üretilir; elle yazılmaz.';
          return box;
        } },
    ],
    rows: state.categories,
    rowKey: (row) => String(row.category_id),
    onRow: (row) => openCategory(row),
    empty: emptyState({
      title: 'Kategori yok',
      text: 'Site menüsü kategori ağacından çiziliyor; en az bir kategori olmadan '
        + 'ürünler sitede görünmez.',
    }),
  });

  view.append(card('Kategori ağacı', table.node));
  nodes.body.replaceChildren(view);
  nodes.status.set(statusText(), !state.connected);
}

function openCategory(row) {
  const isNew = !row;
  const box = drawer(nodes.root, {
    title: isNew ? 'Yeni kategori' : row.name,
    subtitle: isNew ? 'Site menüsünde yeni bir başlık açar.'
      : `#${row.category_id} · ${num(row.menu_count)} ürün`,
    onClose: () => {
      form?.destroy();
      const index = closers.indexOf(box.close);
      if (index >= 0) closers.splice(index, 1);
    },
  });
  closers.push(box.close);

  // Üst kategori seçenekleri: KENDİSİ VE ALT AĞACI listelenmez. Sunucu da
  // döngüyü `422` ile reddediyor; kutuda hiç göstermemek, kullanıcıyı
  // reddedilecek bir seçime hiç götürmemektir.
  const banned = new Set();
  if (!isNew) {
    const walk = (id) => {
      banned.add(id);
      state.categories
        .filter((item) => item.parent_id === id)
        .forEach((item) => walk(item.category_id));
    };
    walk(row.category_id);
  }
  const parents = [
    { value: '', label: 'Üst kategori yok (kök)' },
    ...state.categories
      .filter((item) => !banned.has(item.category_id))
      .map((item) => ({ value: String(item.category_id),
        label: `${'— '.repeat(item.depth)}${item.name}` })),
  ];

  const form = formGrid({
    fields: [
      { key: 'name', label: 'Kategori adı', type: 'text', required: true,
        maxLength: NAME_MAX, wide: true },
      { key: 'description', label: 'Açıklama', type: 'textarea', wide: true },
      { key: 'parent_id', label: 'Üst kategori', type: 'select', options: parents },
      { key: 'priority', label: 'Sıra numarası', type: 'number',
        hint: 'Küçük olan sitede önce görünür.' },
      { key: 'status', label: 'Sitede görünsün', type: 'checkbox',
        hint: 'Kapatmak kategoriyi gizler; kayıt ve altındaki ürünler durur.' },
    ],
    value: isNew
      ? { priority: 0, status: true, parent_id: '' }
      : {
        name: row.name,
        description: row.description,
        parent_id: row.parent_id ? String(row.parent_id) : '',
        priority: row.priority,
        status: row.status,
      },
  });

  const actions = h('div', 'bp-actions');
  actions.append(button(isNew ? 'Kategoriyi aç' : 'Değişiklikleri kaydet', {
    variant: 'primary', onClick: () => save(),
  }));
  box.body.append(form.node, actions);

  if (!isNew) {
    box.body.append(hintBox('Bu kategori silinemez. Sitede görünmesin istiyorsanız '
      + '"Sitede görünsün" kutusunu kapatın; ürünler kategoride kalır.'));
  }

  async function save() {
    const errors = form.errors();
    if (errors.length) {
      form.showErrors();
      toast(errors[0].message, 'bad');
      return;
    }
    const draft = form.draft();
    const parentId = draft.parent_id ? Number(draft.parent_id) : null;

    if (isNew) {
      const reason = await askReason({
        title: 'Yeni kategori açılacak',
        description: `“${draft.name}” site menüsüne eklenecek. Adres (slug) addan `
          + 'üretilir.',
        confirmLabel: 'Kategoriyi aç',
        danger: false,
      });
      if (!reason) return;
      await withBusy('Kategori açılıyor…', async () => {
        const result = await call(`${BASE}/categories`, {
          method: 'POST',
          body: writeBody({
            reason,
            name: String(draft.name).trim(),
            description: draft.description || null,
            parent_id: parentId,
            priority: Number(draft.priority || 0),
            status: Boolean(draft.status),
          }),
        });
        if (announce(result, 'Kategori açıldı.')) {
          box.close();
          await refreshCategories();
          paintCategories();
        }
      });
      return;
    }

    const fields = form.patch();
    if ('parent_id' in fields) fields.parent_id = parentId;
    if ('status' in fields) fields.status = Boolean(fields.status);
    if (!Object.keys(fields).length) {
      toast('Değişen alan yok.', 'warn');
      return;
    }
    const reason = await askReason({
      title: 'Kategori güncellenecek',
      description: `“${row.name}” için ${Object.keys(fields).length} alan değişiyor.`,
      confirmLabel: 'Kaydet',
      danger: false,
    });
    if (!reason) return;
    await withBusy('Kategori kaydediliyor…', async () => {
      const result = await call(`${BASE}/categories/${row.category_id}`, {
        method: 'PATCH', body: writeBody({ reason, fields }),
      });
      if (announce(result, 'Kategori güncellendi.')) {
        box.close();
        await refreshCategories();
        paintCategories();
      }
    });
  }
}

// ------------------------------------------------------------ denetim sekmesi

function showAudit() {
  nodes.body.replaceChildren(skeletonRows(8, 5));
  withBusy('Deneme kaydı okunuyor…', async () => {
    const payload = await api(`${BASE}/audit`);
    paintAudit(payload?.items || [], payload?.error || '');
  });
}

function paintAudit(rows, error) {
  const view = h('div', 'bp-tabview');

  view.append(hintBox('Bu liste Kontrol Merkezi\'nin KENDİ defteridir ve BLD\'nin '
    + 'denetim kaydının yerine geçmez. Fark şu: BLD yalnız SUNUCUYA ULAŞAN isteği '
    + 'bilir. Ağ koptuysa, geçit acil freni kapattıysa ya da imza reddedildiyse '
    + '"kim neyi denedi" sorusunun cevabı yalnız burada kalır — o satırlar '
    + '"Sonucu bilinmiyor" ile işaretlidir.'));

  if (error) view.append(alertBox(`Deneme kaydı okunamadı: ${error}`, 'bad'));

  const table = dataTable({
    dense: true,
    columns: [
      { key: 'created_at', label: 'Ne zaman', width: '150px',
        cell: (row) => {
          const box = h('span', 'bp-dim', ago(row.created_at) || '—');
          box.title = stampIso(row.created_at) || '';
          return box;
        } },
      { key: 'action', label: 'İşlem', width: 'minmax(0, 1.2fr)',
        cell: (row) => h('span', undefined, ACTION_LABELS[row.action] || row.action) },
      { key: 'target', label: 'Kayıt', width: '120px',
        cell: (row) => h('span', 'bp-dim',
          row.target_id ? `${row.target_type} #${row.target_id}` : '—') },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)',
        cell: (row) => h('span', undefined, row.actor || '—') },
      { key: 'result', label: 'Sonuç', width: '160px',
        cell: (row) => badge(RESULT_LABELS[row.result] || row.result,
          RESULT_TONES[row.result] || '') },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)',
        cell: (row) => {
          const box = h('span', 'bp-dim');
          clip(box, row.reason || '—', 60);
          return box;
        } },
    ],
    rows,
    rowKey: (row) => String(row.id),
    empty: emptyState({
      title: 'Henüz deneme yok',
      text: 'Bu ekrandan bir yazma yapıldığında satır burada belirir.',
    }),
  });

  view.append(card('Son denemeler', table.node));
  nodes.body.replaceChildren(view);
}

// ------------------------------------------------------------------- mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel bp');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'products', label: 'Ürünler' },
    { key: 'categories', label: 'Kategoriler' },
    { key: 'audit', label: 'Deneme kaydı' },
  ], 'products', (key) => showTab(key));

  // Süzgeç şeridi sekmeyle birlikte YOK EDİLMEZ: `filterBar` global dinleyici
  // tutuyor ve her sekme geçişinde yenisini kurmak onları biriktirirdi.
  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '280px',
        placeholder: 'Ürün adı ya da açıklama (en az 2 karakter)' },
      { kind: 'select', key: 'category', label: 'Kategori',
        options: [{ value: '', label: 'Tüm kategoriler' }] },
      { kind: 'select', key: 'status', label: 'Durum', value: 'all',
        // VARSAYILAN "Hepsi": yönetimin ilk sorusu çoğu zaman "bu ürün nerede"
        // biçiminde gelir ve cevabı "satıştan kaldırılmış"tır. `active`
        // varsayılanı o ürünü gizler ve kaybolmuş gösterirdi.
        options: [
          { value: 'all', label: 'Hepsi' },
          { value: 'active', label: 'Satışta' },
          { value: 'inactive', label: 'Satıştan kaldırılmış' },
        ] },
      { kind: 'toggle', key: 'soldOut', label: 'Yalnız bugün tükenenler' },
    ],
    onChange: () => {
      // Süzme SUNUCUDADIR: liste sayfalı ve arama ad ile açıklamada birlikte
      // çalışıyor; istemcide süzmek yalnız GÖRÜNEN sayfayı süzerdi.
      state.page = 1;
      withBusy('Süzülüyor…', async () => { await refreshProducts(); paintProducts(); });
    },
    actions: [
      button('Yenile', {
        title: 'Bu ekran kendiliğinden yoklamaz; katalog haftalarca değişmez.',
        onClick: () => showTab(state.tab),
      }),
      button('Yeni ürün', { variant: 'primary', onClick: () => openNewProduct() }),
    ],
  });

  nodes.status = statusLine();
  nodes.body = h('div', 'bp-body');

  const bar = h('div', 'bp-topbar');
  bar.append(nodes.tabs.node);

  view.append(bar, nodes.filters.node, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    // Süzgeç şeridi YALNIZ ürün sekmesinde anlamlı; öteki sekmelerde
    // gizlenir ki kullanıcı işe yaramayan bir kutuya yazmasın. `hidden`
    // özniteliği YETMEZ: kit şeride `display: flex` veriyor ve o kuralı yener.
    nodes.filters.node.style.display = key === 'products' ? '' : 'none';
    ({
      products: showProducts,
      categories: showCategories,
      audit: showAudit,
    }[key] || showProducts)();
  }

  root.replaceChildren(view);

  // Açılışta tercih okunur: sayfa boyutu ve süzgeç kullanıcının bıraktığı
  // hâlde gelsin. Okunamazsa varsayılan geçerlidir ve ekran yine açılır (K7).
  api(`${BASE}/prefs`).then((prefs) => {
    if (prefs?.page_size) state.perPage = prefs.page_size;
    if (prefs?.sort) state.sort = prefs.sort;
    if (prefs?.direction) state.direction = prefs.direction;
    if (prefs?.status_filter) nodes.filters.set('status', prefs.status_filter);
  }).catch(() => { /* tercih yoksa varsayılan */ }).finally(() => {
    showTab('products');
  });

  return () => {
    nodes.filters?.destroy();      // arama ve açılır kutular global dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
