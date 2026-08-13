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
//  · Ürün SİLMEZ. Siparişi olan ürün silinirse geçmiş siparişlerin kalemleri
//    öksüz kalır; her yerde pasifleştirme vardır (ADR 0012).
//  · Barkod etiketi basmaz — gerçek barkod çizimi rapor üretecinde yok, sahte
//    barkod basmaktansa hiç basmamak doğrudur.
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
const STOCK_LABELS = { in: 'Stokta', low: 'Kritik', out: 'Tükendi', off: 'Takip kapalı' };

const CHIPS = [
  { key: 'out_of_stock', label: 'Tükendi' },
  { key: 'low_stock', label: 'Kritik stok' },
  { key: 'no_image', label: 'Görselsiz' },
  { key: 'seo_missing', label: 'SEO eksik' },
  { key: 'passive', label: 'Pasif' },
];

const EMPTY_STATE = {
  items: [], total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', threshold: 5, categoryFilter: null, source: 'products',
  reference: { categories: [], families: [], types: [], sources: [] },
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

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const pages = Math.max(1, state.pages);
  const scope = state.chip ? ` · süzgeç: ${CHIPS.find((c) => c.key === state.chip)?.label}` : '';
  return `Bağlı · ${num(state.total)} ürün · sayfa ${state.page}/${pages}${scope}`;
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
  nodes.status?.set('Ürünler alınıyor…');
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
    toast('Referans listeler bayat kopyadan geldi; mağaza yanıt vermedi.', 'warn');
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
      'Katalog sağlığı okunamadı; mağaza tarafındaki BBD paketi yayınlanınca '
      + 'tükenen/görselsiz/SEO eksik sayıları burada görünecek.',
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
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Ürün', value: pick('total', 'products') },
    { label: 'Pasif', value: pick('inactive', 'passive'), tone: 'muted' },
    { label: 'Tükendi', value: pick('out_of_stock', 'outOfStock'), tone: 'bad' },
    { label: 'Kritik stok', value: pick('low_stock', 'lowStock'), tone: 'warn' },
    { label: 'Görselsiz', value: pick('no_image', 'missing_images'), tone: 'warn' },
    { label: 'SEO eksik', value: pick('seo_missing', 'missing_seo'), tone: 'muted' },
  ]));
}

function thumb(row) {
  const box = h('span', 'sp-thumb');
  if (!row.imageUrl) {
    box.classList.add('none');
    box.title = 'Görsel yok';
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
    box.title = 'Görsel bağlantısı açılmıyor';
    box.replaceChildren(document.createTextNode('!'));
  });
  box.append(image);
  return box;
}

function priceCell(row) {
  const box = h('span', 'sp-price');
  box.append(h('b', row.specialState === 'active' ? 'sp-strike' : '', money(row.price)));
  if (row.specialPrice && row.specialState === 'active') {
    box.append(h('span', 'sp-special', money(row.specialPrice)));
  } else if (row.specialPrice && row.specialState === 'scheduled') {
    box.append(badge('indirim bekliyor', 'info'));
  } else if (row.specialPrice && row.specialState === 'expired') {
    box.append(badge('indirim bitti', 'dim'));
  }
  return box;
}

function stockCell(row) {
  const box = h('span', 'sp-stock');
  // Renk tek başına anlam taşımaz: sayının yanında her zaman yazı durur.
  box.append(h('b', undefined, `${row.stockExact ? '' : '~'}${num(row.stock)}`));
  box.append(badge(STOCK_LABELS[row.stockState] || row.stockState,
    STOCK_TONES[row.stockState] || ''));
  if (!row.stockExact) box.title = 'Vitrin değeri; kesin sayı ürün açılınca gelir.';
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
        h('span', 'sp-sub', row.categories || 'kategorisiz'));
      return box;
    },
  },
  { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono', sortable: true },
  { key: 'typeLabel', label: 'Tip', width: '92px' },
  { key: 'price', label: 'Fiyat', width: '150px', align: 'num', sortable: true, cell: priceCell },
  { key: 'stock', label: 'Stok', width: '132px', align: 'num', cell: stockCell },
  {
    key: 'status',
    label: 'Durum',
    width: '84px',
    cell: (row) => badge(row.status ? 'Aktif' : 'Pasif', row.status ? 'good' : 'dim'),
  },
  {
    key: 'updatedAt',
    label: 'Güncelleme',
    width: '132px',
    cell: (row) => (row.updatedAt ? row.updatedAt.replace('T', ' ').slice(0, 16) : '—'),
  },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.chip) {
    return emptyState({
      title: 'Bu bulguda ürün yok',
      text: 'Seçili çipe uyan ürün bulunamadı — katalog bu başlıkta temiz.',
      actions: [button('Çipi kaldır', { onClick: () => { nodes.chips.set(null); applyChip(null); } })],
    });
  }
  return emptyState({
    title: 'Bu filtreye uyan ürün yok',
    text: `${num(state.total)} kayıt döndü. Süzgeçleri gevşetin ya da temizleyin.`,
    actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  if (state.categoryFilter === false) {
    wrap.append(alertBox(
      'Kategori süzgeci mağaza tarafında uygulanmadı; liste SÜZÜLMEMİŞTİR. '
      + 'Sayfada süzmek 1.419 ürünlük kataloğu temsil etmeyeceği için yapılmadı.',
      'warn',
    ));
  }
  if (state.source !== 'products') {
    wrap.append(alertBox(
      'Bu liste katalog sağlığı bulgularından geliyor; satırlar ürün kaydının '
      + 'tamamını taşımayabilir (eksik alanlar “—” görünür).', 'info',
    ));
  }

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
  bar.append(h('b', undefined, `${num(count)} ürün seçildi`));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Fiyat güncelle', { onClick: () => bulkDialog('price') }),
    button('Stok ayarla', { onClick: () => bulkDialog('stock') }),
    button('Kategori ata', { onClick: () => bulkDialog('category') }),
    button('Aktif yap', { onClick: () => bulkDialog('status', { active: true }) }),
    button('Pasifleştir', { variant: 'danger', onClick: () => bulkDialog('status', { active: false }) }),
    button('Seçimi bırak', { variant: 'ghost', onClick: () => { nodes.table.clearSelection(); state.selection = []; renderSelectionBar(); } }),
  );
}

function applyChip(key) {
  state.chip = key;
  refresh({ page: 1 });
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['SKU', 'Ad', 'Tip', 'Kategori', 'Fiyat', 'İndirimli', 'Stok', 'Durum'];
  const rows = state.items.map((row) => [
    row.sku, row.name, row.typeLabel, row.categories,
    money(row.price), row.specialPrice ? money(row.specialPrice) : '',
    row.stock, row.status ? 'Aktif' : 'Pasif',
  ]);
  const written = csvBlob(headers, rows, `urunler-sayfa-${state.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm kayıtları dışa aktar',
    description: `${num(state.total)} ürün mağazadan sayfa sayfa çekilir ve rapor `
      + 'klasörüne CSV olarak yazılır. Birkaç dakika sürebilir.',
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
    if (result.truncated) toast('Katalog tavana dayandı; dosya eksik olabilir.', 'warn');
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
      title: 'Ürün okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const product = payload.product;
  box.setTitle(product.name);
  box.body.replaceChildren();

  const tabs = tabBar([
    { key: 'general', label: 'Genel' },
    { key: 'price', label: 'Fiyat' },
    { key: 'stock', label: 'Stok' },
    { key: 'images', label: 'Görseller' },
    { key: 'variants', label: 'Varyantlar' },
    { key: 'categories', label: 'Kategoriler' },
    { key: 'seo', label: 'SEO' },
    { key: 'history', label: 'Geçmiş' },
  ], 'general', (key) => paint(key));
  tabs.badge('variants', payload.variants.length || undefined);
  tabs.badge('images', payload.images.length || undefined);

  const head = h('div', 'sp-drawer-head');
  head.append(
    badge(product.status ? 'Aktif' : 'Pasif', product.status ? 'good' : 'dim'),
    badge(product.typeLabel, 'info'),
    h('code', 'sp-sku', product.sku),
    h('span', 'sp-sub', `Öznitelik ailesi: ${product.familyName || '—'} (salt gösterilir)`),
  );
  const pane = h('div', 'sp-pane');
  box.body.append(head, tabs.node, pane);

  if (payload.warnings && payload.warnings.length) {
    box.body.insertBefore(alertBox(`Bazı parçalar okunamadı — ${payload.warnings.join(' · ')}`,
      'warn'), pane);
  }

  function paint(key) {
    dropForms();
    pane.replaceChildren();
    const painter = {
      general: paintGeneral, price: paintPrice, stock: paintStock, images: paintImages,
      variants: paintVariants, categories: paintCategories, seo: paintSeo,
      history: paintHistory,
    }[key];
    painter?.(pane, payload, forms, box);
  }
  paint('general');
}

/** Tek üründe kaydetme: kirli alanlar + gerekçe → OKU-DEĞİŞTİR-YAZ. */
async function saveProduct(productId, patch, { title, description }) {
  if (!Object.keys(patch).length) {
    toast('Değişen alan yok.', 'warn');
    return null;
  }
  const reason = await askReason({ title, description, confirmLabel: 'Kaydet' });
  if (!reason) return null;
  return withBusy('Kaydediliyor…', async () => {
    const result = await call(`${BASE}/products/${productId}`, {
      method: 'PUT',
      body: { patch, reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Kaydedildi.',
      result.dryRun ? 'warn' : 'good');
    if (result.notice) toast(result.notice, 'warn');
    return result;
  });
}

function paintGeneral(pane, payload, forms) {
  const product = payload.product;
  const form = formGrid({
    fields: [
      { key: 'name', label: 'Ürün adı', type: 'text', required: true, maxLength: 180, wide: true },
      { key: 'urlKey', label: 'URL anahtarı', type: 'text', maxLength: 180,
        hint: 'Değişirse eski vitrin bağlantıları kırılır. Kaydetmeden önce benzersizlik yoklanır.' },
      { key: 'status', label: 'Vitrinde görünsün', type: 'checkbox',
        hint: 'Kapatmak silmek değildir; ürün siparişlerde ve raporlarda kalır.' },
      { key: 'shortDescription', label: 'Kısa açıklama', type: 'richtext', wide: true,
        maxLength: 500, placeholder: 'Listede ve ürün kartının üstünde görünen özet.',
        hint: 'Vitrinde ürün adının hemen altında çıkar. Kısa tutun; uzun anlatım '
          + 'aşağıdaki açıklamaya yazılır.' },
      { key: 'description', label: 'Açıklama', type: 'richtext', wide: true, maxLength: 8000,
        placeholder: 'Ürünün ayrıntılı anlatımı. Başlık, liste, renk ve kalın yazı '
          + 'araç çubuğundan uygulanır.',
        hint: 'Biçim araç çubuğundan verilir — HTML yazmanız gerekmez. "Kaynak" '
          + 'düğmesi üretilen HTML\'i gösterir.' },
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
      verdict.replaceChildren(alertBox(error.message, 'warn'));
    }
  }, 500);
  closers.push(() => checkKey.cancel());
  form.node.addEventListener('input', checkKey);

  const actions = h('div', 'sp-actions');
  actions.append(
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
        const result = await saveProduct(product.id, form.patch(), {
          title: 'Ürünü güncelle',
          description: `${product.sku} · ${form.dirty().length} alan değişti. Gerekçe denetim `
            + 'kaydına yazılır ve mağazaya başlıkla gider.',
        });
        if (result) form.reset(form.draft());
      },
    }),
    button('SKU değiştir', {
      variant: 'danger',
      title: 'product_flat yeniden yazılır ve eski URL\'ler kırılır',
      onClick: () => changeSku(product),
    }),
    button('Kopyala', { onClick: () => copyProduct(product) }),
  );

  pane.append(form.node, verdict, actions,
    hintBox('Kaydetme OKU-DEĞİŞTİR-YAZ yapar: ürün taze okunur, dokunmadığınız alanlar '
      + 'mağazadaki güncel değeriyle geri gönderilir. Kısmi gönderim bazı alanları '
      + 'boşaltıyordu.'));
}

async function changeSku(product) {
  const input = window.prompt(`Yeni SKU (şu an: ${product.sku})`, product.sku);
  if (!input || input.trim() === product.sku) return;
  const reason = await askReason({
    title: 'SKU değiştir',
    description: `${product.sku} → ${input.trim()}. Mağaza ürünün düz tablosunu (product_flat) `
      + 'yeniden yazar, eski SKU ile paylaşılmış vitrin bağlantıları kırılır ve '
      + 'arama dizini yenilenene kadar ürün eski adıyla bulunabilir.',
    confirmLabel: 'SKU\'yu değiştir',
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

function paintPrice(pane, payload, forms) {
  const product = payload.product;
  const price = payload.price;
  const priceless = ['configurable', 'bundle', 'grouped'].includes(product.type);

  if (priceless) {
    pane.append(alertBox(
      `${product.typeLabel} ürünün fiyatı KENDİSİNDE DEĞİL varyantlarındadır. Buradan `
      + 'yazmak vitrinde görünmeyen ama raporlara giren hayalet fiyat üretirdi; alanlar '
      + 'kapalıdır. Fiyatı Varyantlar sekmesinden düzenleyin.', 'warn'));
  }

  const form = formGrid({
    fields: [
      { key: 'price', label: 'Liste fiyatı', type: 'money', readOnly: priceless },
      { key: 'cost', label: 'Maliyet', type: 'money', readOnly: priceless,
        hint: 'Kâr marjı bundan hesaplanır; boşsa marj gösterilmez.' },
      { key: 'specialPrice', label: 'İndirimli fiyat', type: 'money', readOnly: priceless },
      { key: 'specialFrom', label: 'İndirim başlangıcı', type: 'date' },
      { key: 'specialTo', label: 'İndirim bitişi', type: 'date' },
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
    margin.append(h('span', 'sp-sub', 'Kâr marjı: '));
    if (!listed || cost === null) {
      margin.append(h('b', undefined, '—'));
      margin.append(h('span', 'sp-sub', ' (maliyet girilmedi)'));
      return;
    }
    const ratio = ((listed - cost) * 100) / listed;
    margin.append(h('b', ratio < 0 ? 'sp-bad' : '', percent(Math.round(ratio * 10) / 10)));
    margin.append(h('span', 'sp-sub', ` · birim kâr ${money(listed - cost)}`));
  }
  paintMargin();

  const specialNote = {
    none: 'İndirim tanımlı değil.',
    active: 'İndirim BUGÜN geçerli — müşteri indirimli fiyatı ödüyor.',
    scheduled: 'İndirim ileri tarihli; henüz uygulanmıyor.',
    expired: 'İndirim penceresi kapandı; liste fiyatı geçerli.',
  }[price.specialState];

  const groups = h('div', 'sp-group-prices');
  groups.append(h('div', 'sp-sub', 'Müşteri grubu fiyatları'));
  if (!price.groupPrices.length) {
    groups.append(h('div', 'sp-sub', 'Tanımlı grup fiyatı yok.'));
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
        'Bu üründe hiç görsel yok. Görselsiz ürün vitrinde tıklanmıyor ve katalog '
        + 'sağlığında “Görselsiz” bulgusunda çıkar. Aşağıdan görsel ekleyin.', 'warn'));
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
      if (done) refresh();          // liste küçük resmi ve “Görselsiz” çipi değişti
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
    description: 'Görsel mağazadan silinir ve geri alınamaz. Ürün ve siparişler etkilenmez.',
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
      title: 'Varyant yok',
      text: 'Bu ürün tek kalem satılıyor. Varyant tanımı mağaza yönetiminden yapılır.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1.2fr)', className: 'mono' },
      { key: 'name', label: 'Varyant', width: 'minmax(0, 2fr)' },
      { key: 'price', label: 'Fiyat', width: '120px', align: 'num', cell: priceCell },
      { key: 'stock', label: 'Stok', width: '120px', align: 'num', cell: stockCell },
      { key: 'status', label: 'Durum', width: '80px',
        cell: (row) => badge(row.status ? 'Aktif' : 'Pasif', row.status ? 'good' : 'dim') },
    ],
    rows,
    dense: true,
    onRow: (row) => openProduct(row.id),
  });
  pane.append(table.node,
    hintBox('Varyantlı ürünün fiyatı buradadır. Üst ürüne fiyat yazmak vitrinde '
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

function paintSeo(pane, payload, forms) {
  const product = payload.product;
  const seo = payload.seo;
  const form = formGrid({
    fields: [
      { key: 'metaTitle', label: 'Meta başlık', type: 'text', maxLength: 120, wide: true },
      { key: 'metaDescription', label: 'Meta açıklama', type: 'textarea', maxLength: 320,
        wide: true },
      { key: 'metaKeywords', label: 'Anahtar kelimeler', type: 'text', maxLength: 240,
        wide: true },
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
      h('div', 'sp-serp-text', description.slice(0, 160) || 'Meta açıklama boş — arama '
        + 'motoru sayfa metninden rastgele bir parça gösterir.'),
      h('div', 'sp-sub',
        `Başlık ${title.length}/60 · açıklama ${description.length}/160 karakter`),
    );
  }
  paintPreview();

  const actions = h('div', 'sp-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      const result = await saveProduct(product.id, form.patch(), {
        title: 'SEO alanlarını güncelle',
        description: `${product.sku} · ${form.dirty().length} alan değişti.`,
      });
      if (result) form.reset(form.draft());
    },
  }));

  pane.append(form.node, card('Arama sonucu önizlemesi', preview), actions);
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
      title: 'Bu ürüne bu ekrandan dokunulmadı',
      text: 'Kontrol Merkezi üzerinden yapılan her yazma gerekçesiyle burada listelenir.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'action', label: 'İşlem', width: '150px' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
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
    price: 'Toplu fiyat güncelleme',
    stock: 'Toplu stok ayarlama',
    category: 'Toplu kategori işlemi',
    status: options.active ? 'Toplu aktifleştirme' : 'Toplu pasifleştirme',
  };
  box.append(h('h3', 'kit-dialog-title', titles[kind]));
  box.append(h('p', 'kit-dialog-text',
    `${num(state.selection.length)} ürün seçili. Fark tablosu görülmeden hiçbir şey `
    + 'uygulanmaz.'));

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(() => document.removeEventListener('keydown', onKey));

  const controls = h('div', 'sp-bulk-controls');
  const params = { kind, mode: '', amount: 0, rounding: 'none', categoryId: 0, active: options.active };

  if (kind === 'price') {
    const mode = select([
      { value: 'percent', label: 'Yüzde değiştir (%)' },
      { value: 'amount', label: 'Tutar ekle/çıkar (₺)' },
      { value: 'set', label: 'Sabit fiyat ata (₺)' },
    ]);
    const amount = h('input', 'kit-input');
    amount.type = 'text';
    amount.placeholder = '-10 · 25,00 · 199,90';
    const rounding = select([
      { value: 'none', label: 'Yuvarlama yok' },
      { value: 'penny99', label: 'x,99 ile bitir' },
      { value: 'whole', label: 'Tam liraya' },
      { value: 'half', label: '50 kuruşa' },
    ]);
    controls.append(labelled('Kip', mode), labelled('Değer', amount),
      labelled('Yuvarlama', rounding));
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
      { value: 'set', label: 'Adedi şuna ayarla' },
      { value: 'add', label: 'Adede ekle/çıkar' },
    ]);
    const amount = h('input', 'kit-input');
    amount.type = 'number';
    amount.value = '0';
    controls.append(labelled('Kip', mode), labelled('Adet', amount));
    params.read = () => {
      params.mode = mode.value;
      params.amount = Number(amount.value);
      return !Number.isNaN(params.amount);
    };
  } else if (kind === 'category') {
    const mode = select([
      { value: 'add', label: 'Kategoriye ekle' },
      { value: 'remove', label: 'Kategoriden çıkar' },
    ]);
    const target = select([
      { value: '', label: 'Kategori seçin' },
      ...state.reference.categories.map((item) => ({ value: item.id, label: item.label })),
    ]);
    controls.append(labelled('İşlem', mode), labelled('Kategori', target));
    params.read = () => {
      params.mode = mode.value;
      params.categoryId = Number(target.value) || 0;
      return params.categoryId > 0;
    };
  } else {
    params.read = () => true;
    controls.append(h('div', 'sp-sub', options.active
      ? 'Seçili ürünler vitrinde görünür hâle gelir.'
      : 'Seçili ürünler vitrinden kalkar. SİLİNMEZ: siparişlerde ve raporlarda kalır.'));
  }

  const result = h('div', 'sp-bulk-result');
  const actions = h('div', 'kit-dialog-actions');
  const previewBtn = button('Önizle', { variant: 'primary', onClick: () => runPreview() });
  actions.append(button('Vazgeç', { onClick: close }), previewBtn);

  box.append(controls, result, actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);

  async function runPreview() {
    if (!params.read()) { toast('Değer geçersiz.', 'bad'); return; }
    result.replaceChildren(skeletonRows(4, 4));
    let preview;
    try {
      preview = await call(`${BASE}/bulk/preview`, {
        method: 'POST',
        body: {
          kind, productIds: state.selection.map(Number), mode: params.mode,
          amount: params.amount, rounding: params.rounding, categoryId: params.categoryId,
          active: Boolean(params.active),
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
      { label: 'Satır', value: num(summary.total) },
      { label: 'Değişecek', value: num(summary.changed) },
      { label: 'Atlanan', value: num(summary.skipped), tone: 'muted' },
      { label: 'Artan', value: num(summary.up), tone: 'good' },
      { label: 'Azalan', value: num(summary.down), tone: 'bad' },
    ]));

    const asMoney = kind === 'price';
    const table = dataTable({
      columns: [
        { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
        { key: 'before', label: 'Önce', width: '110px', align: 'num',
          cell: (row) => (asMoney ? money(row.before) : num(row.before)) },
        { key: 'after', label: 'Sonra', width: '110px', align: 'num',
          cell: (row) => (asMoney ? money(row.after) : num(row.after)) },
        { key: 'delta', label: 'Fark', width: '110px', align: 'num',
          cell: (row) => {
            const cell = h('span', row.delta < 0 ? 'sp-bad' : 'sp-good');
            cell.textContent = row.skipped ? '—'
              : `${row.delta > 0 ? '+' : ''}${asMoney ? money(row.delta) : num(row.delta)}`;
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
      result.append(alertBox(`${preview.missing.length} ürün okunamadı ve önizlemeye `
        + 'girmedi.', 'warn'));
    }
    result.append(alertBox(preview.note, preview.applicable ? 'info' : 'warn'));

    actions.replaceChildren(
      button('Vazgeç', { onClick: close }),
      button('Fark tablosunu CSV al', {
        onClick: () => {
          csvBlob(['SKU', 'Ürün', 'Önce', 'Sonra', 'Fark', 'Not'],
            preview.rows.map((row) => [row.sku, row.name, row.before, row.after, row.delta,
              row.note || '']),
            `toplu-${kind}-farki`);
          toast('Fark tablosu indirildi.', 'good');
        },
      }),
      button(`${num(summary.changed)} ürüne uygula`, {
        variant: 'danger',
        disabled: !preview.applicable || summary.changed === 0,
        onClick: () => apply(preview),
      }),
    );
  }

  async function apply(preview) {
    const reason = await askReason({
      title: titles[kind],
      description: `${num(preview.summary.changed)} ürün değişecek. Önizlediğiniz fark `
        + 'tablosu neyse o uygulanır; yeniden hesaplanmaz.',
      confirmLabel: 'Uygula',
    });
    if (!reason) return;
    await withBusy('Uygulanıyor…', async () => {
      const applied = await call(`${BASE}/bulk/apply`, {
        method: 'POST', body: { token: preview.token, reason, dryRun: false },
      });
      toast(`${num(applied.applied)} ürün güncellendi`
        + (applied.failed ? ` · ${num(applied.failed)} başarısız` : ''),
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

function labelled(label, control) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), control);
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
      key: 'lowStockThreshold', label: 'Kritik stok eşiği', type: 'number', min: 0, max: 9999,
      hint: 'Bu sayının altındaki stok “Kritik” boyanır. YALNIZ bu ekranı etkiler; '
        + 'vitrine ve siparişe karışmaz.',
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
      `Mağaza ayarları okunamadı — ${payload.error}. Kritik eşiği yine de `
      + 'kaydedebilirsiniz; o yerel bir tercihtir.', 'warn'));
  } else {
    if (found('outOfStock')) {
      storeFields.push({
        key: 'outOfStock', label: 'Tükenen ürün vitrinde görünsün', type: 'checkbox',
        hint: 'Açık: ürün listede kalır ama satın alınamaz. Kapalı: vitrinden tamamen gizlenir.',
      });
    }
    if (found('backOrder')) {
      storeFields.push({
        key: 'backOrder', label: 'Arka sipariş (stok yokken satış)', type: 'checkbox',
        hint: 'Açıkken müşteri stokta olmayan ürünü sipariş edebilir ve teslimat bekler.',
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
  { key: 'configurable', label: 'Varyant üretiminde kullanılsın', type: 'checkbox',
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
      { kind: 'search', key: 'q', placeholder: 'Ad, SKU veya barkod ara', width: '260px' },
      { kind: 'select', key: 'category', label: 'Kategori', options: [{ value: '', label: 'Tümü — kategori' }] },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Tümü' }, { value: '1', label: 'Aktif' }, { value: '0', label: 'Pasif' },
      ] },
      { kind: 'select', key: 'type', label: 'Tip', options: [{ value: '', label: 'Tümü — tip' }] },
      // 'Aile' süzgeci KALDIRILDI: tek satıcılı mağazada her ürün aynı ailede,
      // dolayısıyla süzgeç hiçbir şeyi elemiyor — yalnız şeridi kalabalıklaştırıyordu.
      { kind: 'numRange', key: 'price', label: 'Fiyat', money: true },
    ],
    onChange: () => refresh({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => { refresh(); loadHealth(); } }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm kayıtları rapor klasörüne yaz', onClick: exportAll }),
      // TUZAK: `reportChain` gövdeyi `{kind, ...params}` olarak kuruyor.
      // `currentFilters()` içinde de `kind` var (ürün tipi) ve rapor türünü
      // eziyordu; rapor parametreleri açıkça verilir.
      button('Stok raporu', { onClick: () => report.run('stock', reportParams()) }),
      button('Fiyat listesi', { onClick: () => report.run('pricelist', reportParams()) }),
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
  nodes.status.set('Ürünler alınıyor…');
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
