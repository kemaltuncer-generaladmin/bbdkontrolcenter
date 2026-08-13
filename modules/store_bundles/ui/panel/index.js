// Setler paneli — bir setin bileşenlerini kurar ve o setin KÂR EDİP ETMEDİĞİNİ söyler.
//
// NE YAPAR: solda set listesi (uyarı ve kâr sütunlu), sağda düzenleyici;
// bileşen tablosu (ürün seç → adet → bileşen indirimi → zorunlu/opsiyonel) ve
// her değişiklikte yenilenen CANLI HESAP KUTUSU:
//   bileşen toplamı → indirim → KDV → set fiyatı → KÂR/ZARAR.
//
// CANLIDAKİ DÜZEN. Mağazada "set" diye bir ürün tipi YOK. Setler, 42 numaralı
// “Setler” kategorisindeki normal ürünlerdir; hangi kalemlerden oluştukları
// çapraz satış bağlarıyla, vitrindeki yerleri ana sayfa carousel'iyle kurulmuş.
// O düzen ADET, BİLEŞEN İNDİRİMİ ve ZORUNLULUK bilgisini taşımaz — set künyesi
// bu yüzden Kontrol Merkezi'nde durur ve ekran bunu açıkça söyler.
//
// NE YAPMAZ:
//  · Set fiyatını mağazaya YAZMAZ. Set bir üründür; ürün fiyatı yazmak Bagisto'da
//    oku-değiştir-yaz ister (kısmi PUT alanları boşaltıyor) ve o kural Ürünler
//    ekranının işidir. Buradan fiyat düzenlemesi o ekrana yönlendirilir.
//  · Ana sayfa carousel'ini düzenlemez; yalnız "bu set ana sayfada" rozetini
//    çizmek için okur. Düzenleme store_home_media ekranındadır.
//  · Set SİLMEZ. Satılmış setin bileşen künyesi geçmişi okumak için gerekir;
//    vitrinden kaldırma vardır (ADR 0012).
//
// TASARIM SAPMASI (bilerek): plan, 340px'lik sol listede yedi sütun istiyordu.
// O genişlikte yedi sütun okunmuyor; listede karar için gereken dördü durur
// (set · fiyat · kâr · uyarı+durum), kalanı düzenleyicide ve CSV/PDF'te.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_bundles/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_bundles/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmWithReason, csvBlob, debounce, h, loadStyles, money, num,
  parseMoney, percent, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar, applyFilters } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, skeletonRows, splitView,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { createPicker } from '../../ui-kit/picker.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_bundles';

const VALIDITY = {
  always: 'Süresiz',
  active: 'Yürürlükte',
  scheduled: 'Başlamadı',
  expired: 'Süresi doldu',
};

const EMPTY_STATE = {
  items: [], connected: false, error: '', notice: '', source: '', skipped: 0,
  missed: 0, taxRate: 20, selected: 0,
};

let api = null;
let open = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

// Düzenleyicinin canlı taslağı. Sunucuya yalnız TANIM gönderilir; birim fiyat
// ve maliyet orada mağaza verisinden çözülür (istemciden tutar kabul edilmez).
let draft = null;

const nodes = {};
const closers = [];          // cleanup'ta çağrılacak gerçek kaynak bırakıcılar
let editorForm = null;

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
  const loss = state.items.filter((row) => row.flags.loss).length;
  const risk = state.items.filter((row) => row.warning).length;
  return `Bağlı · ${num(state.items.length)} set · ${num(loss)} zararına · ${num(risk)} uyarılı`;
}

function selectedRow() {
  return state.items.find((row) => row.id === state.selected) || null;
}

// --------------------------------------------------------------------- veri

async function refresh({ keepSelection = true } = {}) {
  nodes.listBody?.replaceChildren(skeletonRows(8, 3));
  nodes.status?.set('Setler alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/bundles`);
  } catch (error) {
    // LİSTE DÜŞTÜYSE DÜZENLEYİCİ DE DÜŞER. Yalnız listeyi yeniden çizmek,
    // sağda artık elimizde olmayan bir setin künyesini — Kaydet ve Vitrinden
    // kaldır düğmeleriyle birlikte — ekranda bırakıyordu; üstteki şerit de
    // eski kaynağı anlatmaya devam ediyordu.
    state = {
      ...state, connected: false, error: error.message, items: [],
      notice: '', source: '', skipped: 0, missed: 0, selected: 0,
    };
    renderNotice();
    renderList();
    renderEditor();
    nodes.status?.set(statusText(), true);
    return;
  }
  state = {
    ...state,
    items: payload.items || [],
    connected: Boolean(payload.connected),
    error: payload.error || '',
    notice: payload.notice || '',
    source: payload.source || '',
    skipped: payload.skipped || 0,
    missed: payload.missed || 0,
    taxRate: payload.taxRate ?? state.taxRate,
  };
  if (!keepSelection || !selectedRow()) state.selected = state.items[0]?.id || 0;
  renderNotice();
  renderList();
  renderEditor();
  nodes.status?.set(statusText(), !state.connected);
}

/** Hangi kaynaktan okunduğu ve tavana dayanılıp dayanılmadığı GİZLENMEZ. */
function renderNotice() {
  if (!nodes.notice) return;
  nodes.notice.replaceChildren();
  if (!state.notice) return;
  // `missed` = kategoride görünüp listeye GİRMEYEN set sayısı; yani liste
  // eksik. Sunucu bunu metne yazıyordu ama panel yalnız `skipped`e bakıp
  // kutuyu mavi (bilgi) çiziyordu: eksik liste sıradan bir açıklama gibi
  // görünüyordu.
  const tone = (state.skipped || state.missed) ? 'warn' : 'info';
  nodes.notice.append(alertBox(state.notice, tone));
}

// -------------------------------------------------------------------- liste

function visibleRows() {
  const values = nodes.filters ? nodes.filters.values() : {};
  // `statusKey` yalnız süzgeç için türetilir: `applyFilters` metin karşılaştırır,
  // satırdaki `status` ise boolean.
  const rows = state.items.map((row) => ({ ...row, statusKey: row.status ? '1' : '0' }));
  return applyFilters(rows, values, {
    q: { kind: 'search', fields: ['name', 'sku'] },
    status: { kind: 'equals', field: 'statusKey' },
    validity: { kind: 'equals', field: 'validity' },
    loss: { kind: 'toggle', test: (row) => row.flags.loss },
    outOfStock: { kind: 'toggle', test: (row) => row.flags.outOfStock },
    passive: { kind: 'toggle', test: (row) => row.flags.passive },
  });
}

function profitCell(row) {
  const box = h('span', 'sb-profit');
  const calc = row.calc;
  if (calc.profit === null || calc.profit === undefined) {
    box.append(h('b', 'sb-dim', '—'));
    // NEDEN GÖRÜNÜR YAZI. Bu bilgi yalnız `title` ipucundayken dokunmatikte
    // ve klavyeyle hiç açılmıyordu: kâr sütunu boş “—” diye okunuyor,
    // kullanıcı sayının neden yokluğunu öğrenemiyordu. Renk/boşluk tek başına
    // anlam taşımaz (kit kuralı 7).
    box.append(h('span', 'sb-sub',
      calc.unknownCosts.length ? 'maliyet girilmemiş' : 'set fiyatı yok'));
    box.title = calc.unknownCosts.length
      ? `Maliyeti girilmemiş bileşen: ${calc.unknownCosts.join(', ')}`
      : 'Set fiyatı belirlenmedi.';
    return box;
  }
  // Renk tek başına anlam taşımaz: rakamın yanında her zaman yüzde/yazı durur.
  box.append(h('b', calc.profit < 0 ? 'sb-bad' : 'sb-good', money(calc.profit)));
  if (calc.marginPercent !== null && calc.marginPercent !== undefined) {
    box.append(h('span', 'sb-sub', percent(calc.marginPercent)));
  }
  return box;
}

const LIST_COLUMNS = [
  {
    key: 'name',
    label: 'Set',
    width: 'minmax(0, 1.9fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'sb-name');
      const head = h('span', 'sb-name-line');
      head.append(clip(h('b'), row.name, 34));
      if (row.onCarousel) head.append(badge('ana sayfa', 'info'));
      box.append(head);
      const sub = h('span', 'sb-sub');
      sub.textContent = row.warning
        ? row.warning
        : `${row.components.length} bileşen · ${row.sku || 'SKU yok'}`;
      if (row.warning) sub.classList.add('sb-bad');
      box.append(sub);
      return box;
    },
  },
  {
    key: 'setPrice',
    label: 'Set fiyatı',
    width: '104px',
    align: 'num',
    sortable: true,
    sortValue: (row) => row.calc.setPrice || 0,
    cell: (row) => (row.calc.setPrice === null ? '—' : money(row.calc.setPrice)),
  },
  {
    key: 'profit',
    label: 'Kâr',
    width: '110px',
    align: 'num',
    sortable: true,
    sortValue: (row) => (row.calc.profit === null ? 0 : row.calc.profit),
    cell: profitCell,
  },
  {
    key: 'status',
    label: 'Durum',
    width: '78px',
    cell: (row) => badge(row.status ? 'Aktif' : 'Pasif', row.status ? 'good' : 'dim'),
  },
];

function listEmpty() {
  if (!state.connected && !state.items.length) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.items.length) {
    return emptyState({
      title: 'Süzgece uyan set yok',
      text: `${num(state.items.length)} setin hiçbiri bu süzgece uymuyor.`,
      actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
    });
  }
  return emptyState({
    title: 'Henüz set tanımlanmadı',
    text: 'Set = birden çok ürünün tek fiyatla satılması. Mağazada set ürün tipi yoktur; '
      + 'set, “Setler” kategorisindeki bir üründür ve bileşenleri buradan tanımlanır.',
    actions: [button('Yeni set', { variant: 'primary', onClick: newBundle })],
  });
}

function renderList() {
  if (!nodes.listBody) return;
  const rows = visibleRows();
  // TABLO BİR KEZ KURULUR, SONRA `update()` EDİLİR. Her çizimde yenisini
  // yapmak kullanıcının seçtiği sütun sıralamasını her tuş vuruşunda
  // (arama kutusu her harfte `renderList` çağırıyor) sıfırlıyordu; kit
  // `dataTable` tam bu iş için `update({rows, empty})` veriyor.
  if (!nodes.table) {
    nodes.table = dataTable({
      columns: LIST_COLUMNS,
      rows,
      dense: true,
      empty: listEmpty(),
      onRow: (row) => selectBundle(row.id),
    });
  } else {
    nodes.table.update({ rows, empty: listEmpty() });
  }
  nodes.listBody.replaceChildren(nodes.table.node);
  nodes.listCount.textContent = `${num(rows.length)} / ${num(state.items.length)} set`;
}

// --------------------------------------------------------------- düzenleyici

function selectBundle(bundleId) {
  state.selected = bundleId;
  renderEditor();
}

function dropForm() {
  editorForm?.destroy();
  editorForm = null;
}

function renderEditor() {
  const host = nodes.editor;
  if (!host) return;
  dropForm();
  host.replaceChildren();

  const row = selectedRow();
  if (!row) {
    host.append(emptyState({
      title: 'Soldan bir set seçin',
      text: 'Seçilen setin bileşenleri, fiyatı ve kâr hesabı burada açılır.',
      actions: [button('Yeni set', { variant: 'primary', onClick: newBundle })],
    }));
    return;
  }

  draft = {
    id: row.id,
    name: row.name,
    sku: row.sku,
    pricingMode: row.pricingMode,
    discountPercent: row.discountPercent,
    // KÜNYEDEKİ fiyat okunur, hesabın sonucu DEĞİL. `calc.setPrice` künye boşken
    // mağazadaki ürün fiyatını gösteriyor; onu forma yazsaydık ilk kaydetmede
    // o gün geçerli mağaza fiyatı yerele donar, sonraki zam yansımazdı.
    setPrice: row.planPrice ?? null,
    taxRate: row.taxRate,
    validFrom: row.validFrom,
    validTo: row.validTo,
    note: row.note,
    components: row.components.map((item) => ({ ...item })),
    calc: row.calc,
    warning: row.warning,
    storePrice: row.storePrice,
    status: row.status,
    hasPlan: row.hasPlan,
  };

  const tabs = tabBar([
    { key: 'edit', label: 'Düzenleyici' },
    { key: 'history', label: 'İşlem geçmişi' },
  ], 'edit', (key) => paint(key));
  const pane = h('div', 'sb-pane');

  const head = h('div', 'sb-head');
  head.append(
    h('b', 'sb-title', row.name),
    badge(row.status ? 'Aktif' : 'Pasif', row.status ? 'good' : 'dim'),
    badge(VALIDITY[row.validity] || row.validity, row.validity === 'expired' ? 'warn' : 'dim'),
  );
  if (row.sku) head.append(h('code', 'sb-sku', row.sku));
  head.append(h('span', 'kit-spacer'));
  head.append(button('Ürünler ekranında aç', {
    variant: 'ghost',
    title: 'Set fiyatı, görsel ve kategori Ürünler ekranından düzenlenir',
    onClick: () => open?.('store_products', { productId: row.id }),
  }));

  host.append(head, tabs.node, pane);

  function paint(key) {
    dropForm();
    pane.replaceChildren();
    if (key === 'history') paintHistory(pane, row.id);
    else paintEditor(pane, row);
  }
  paint('edit');
}

function paintEditor(pane, row) {
  if (!row.readable) {
    pane.append(alertBox(
      'Bu setin ürün kaydı mağazadan okunamadı; fiyat ve stok bilgisi eksik. '
      + 'Aşağıdaki künye yereldir ve kaybolmadı.', 'warn'));
  }
  if (!row.hasPlan) {
    pane.append(hintBox(
      'Bu setin bileşen künyesi henüz tanımlanmadı. Mağazadaki çapraz satış bağları '
      + 'yalnız “ilişkili ürün” der; adet, bileşen indirimi ve zorunluluk bilgisini '
      + 'taşımaz — o yüzden aşağıda tanımlanır.'));
  }

  editorForm = formGrid({
    fields: [
      { key: 'name', label: 'Set adı', type: 'text', maxLength: 180, wide: true },
      { key: 'pricingMode', label: 'Fiyatlandırma', type: 'select', options: [
        { value: 'fixed', label: 'Sabit set fiyatı' },
        { value: 'percent', label: 'Bileşen toplamı − %x' },
      ], hint: 'Sabit kipte fiyat boş bırakılırsa mağazadaki ürün fiyatı geçerlidir.' },
      { key: 'discountPercent', label: 'Set indirimi (%)', type: 'number', min: 0, max: 100 },
      { key: 'setPrice', label: 'Set fiyatı (KDV dahil)', type: 'money' },
      { key: 'taxRate', label: 'KDV oranı (%)', type: 'number', min: 0, max: 100 },
      { key: 'validFrom', label: 'Geçerlilik başlangıcı', type: 'date' },
      { key: 'validTo', label: 'Geçerlilik bitişi', type: 'date' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 500, wide: true },
    ],
    value: {
      name: draft.name,
      pricingMode: draft.pricingMode,
      discountPercent: draft.discountPercent,
      setPrice: draft.setPrice,
      taxRate: draft.taxRate ?? state.taxRate,
      validFrom: draft.validFrom,
      validTo: draft.validTo,
      note: draft.note,
    },
    onChange: (next) => {
      draft.name = next.name;
      draft.pricingMode = next.pricingMode;
      draft.discountPercent = Number(next.discountPercent) || 0;
      draft.setPrice = next.setPrice === undefined ? null : next.setPrice;
      // KDV %0 geçerli bir orandır; `||` sıfırı ayara düşürürdü.
      draft.taxRate = next.taxRate === null || next.taxRate === undefined
        || next.taxRate === '' ? state.taxRate : Number(next.taxRate);
      draft.validFrom = next.validFrom || '';
      draft.validTo = next.validTo || '';
      draft.note = next.note || '';
      recalc();
    },
  });

  nodes.components = h('div', 'sb-components');
  nodes.calc = h('div', 'sb-calc');

  const componentActions = h('div', 'sb-actions');
  componentActions.append(
    button('Bileşen ekle', { variant: 'primary', onClick: openPicker }),
    button('Çapraz satıştan al', {
      title: 'Mağazadaki çapraz satış bağlarını bileşen olarak ekler (adet 1, indirim yok)',
      onClick: () => importCrossSells(row.id),
    }),
  );

  const saveActions = h('div', 'sb-actions');
  saveActions.append(
    button('Kaydet', { variant: 'primary', onClick: () => saveBundle(row.id) }),
    button(row.status ? 'Vitrinden kaldır' : 'Vitrine geri al', {
      variant: row.status ? 'danger' : '',
      onClick: () => changeStatus(row),
    }),
  );

  pane.append(
    card('Künye', editorForm.node),
    card('Bileşenler', nodes.components, 'Adet · bileşen indirimi · zorunlu/opsiyonel'),
    componentActions,
    nodes.calc,
    saveActions,
    hintBox('Kâr, KDV hariç set fiyatından bileşen maliyetleri düşülerek bulunur. '
      + 'Vitrin fiyatı KDV dahil, mağazadaki “maliyet” alanı KDV hariçtir; ikisini '
      + 'doğrudan çıkarmak seti olduğundan kârlı gösterirdi.'),
  );

  paintComponents();
  renderCalc(draft.calc, draft.warning);
}

function paintComponents() {
  const host = nodes.components;
  if (!host) return;
  host.replaceChildren();
  if (!draft.components.length) {
    host.append(emptyState({
      title: 'Bileşen yok',
      text: 'Set en az bir bileşen ister; bileşensiz bir kayıt normal üründür.',
      actions: [button('Bileşen ekle', { variant: 'primary', onClick: openPicker })],
    }));
    return;
  }

  const head = h('div', 'sb-comp-row sb-comp-head');
  head.append(
    h('span', undefined, 'Ürün'),
    h('span', undefined, 'Adet'),
    h('span', undefined, 'İndirim %'),
    h('span', undefined, 'Zorunlu'),
    h('span', 'sb-right', 'Birim'),
    h('span', 'sb-right', 'Satır'),
    h('span', undefined, ''),
  );
  host.append(head);

  draft.components.forEach((item, index) => {
    const line = h('div', 'sb-comp-row');

    const name = h('span', 'sb-name');
    name.append(clip(h('b'), item.name, 30));
    const sub = h('span', 'sb-sub');
    const marks = [];
    if (item.missing) marks.push('ürün okunamadı');
    else {
      if (!item.status) marks.push('PASİF');
      if (item.stock < item.qty) marks.push(`stok ${num(item.stock)}`);
    }
    sub.textContent = marks.length ? `${item.sku} · ${marks.join(' · ')}` : item.sku;
    if (marks.length) sub.classList.add('sb-bad');
    name.append(sub);

    const qty = h('input', 'kit-input sb-mini');
    qty.type = 'number';
    qty.min = '1';
    qty.value = String(item.qty);
    qty.addEventListener('input', () => {
      draft.components[index].qty = Math.max(1, Number(qty.value) || 1);
      recalc();
    });

    const discount = h('input', 'kit-input sb-mini');
    discount.type = 'number';
    discount.min = '0';
    discount.max = '100';
    discount.value = String(item.discount);
    discount.addEventListener('input', () => {
      draft.components[index].discount = Math.min(100, Math.max(0, Number(discount.value) || 0));
      recalc();
    });

    const required = h('input', 'kit-check');
    required.type = 'checkbox';
    required.checked = item.required !== false;
    required.setAttribute('aria-label', 'Zorunlu bileşen');
    required.addEventListener('change', () => {
      draft.components[index].required = required.checked;
      recalc();
    });

    const unit = h('span', 'sb-right');
    unit.textContent = item.unitPrice === null || item.unitPrice === undefined
      ? '—' : money(item.unitPrice);
    const total = h('span', 'sb-right');
    total.textContent = item.lineNet === null || item.lineNet === undefined
      ? '—' : money(item.lineNet);

    line.append(name, qty, discount, required, unit, total,
      button('Çıkar', {
        variant: 'ghost',
        title: 'Bileşeni setten çıkar (ürün silinmez)',
        onClick: () => {
          draft.components.splice(index, 1);
          paintComponents();
          recalc();
        },
      }));
    host.append(line);
  });
}

// ------------------------------------------------------------ canlı hesap

const recalcSoon = debounce(async () => {
  if (!draft) return;
  let payload;
  try {
    payload = await call(`${BASE}/calc`, {
      method: 'POST',
      body: {
        components: draft.components.map((item) => ({
          productId: item.productId,
          qty: item.qty,
          discount: item.discount,
          required: item.required !== false,
        })),
        pricingMode: draft.pricingMode,
        discountPercent: draft.discountPercent,
        setPrice: draft.pricingMode === 'fixed' && draft.setPrice !== null
          ? Number(draft.setPrice) : null,
        taxRate: draft.taxRate,
      },
    });
  } catch (error) {
    nodes.calc?.replaceChildren(alertBox(`Hesap alınamadı — ${error.message}`, 'warn'));
    return;
  }
  // Fiyat/maliyet SUNUCUDAN gelir: bileşen satırları da onun döndürdüğüyle
  // tazelenir, yoksa ekranda eski birim fiyat kalırdı.
  draft.components = payload.components;
  draft.calc = payload.calc;
  draft.warning = payload.warning;
  paintComponents();
  renderCalc(payload.calc, payload.warning);
}, 320);

function recalc() {
  nodes.calc?.classList.add('sb-stale');
  recalcSoon();
}

function renderCalc(calc, warning) {
  const host = nodes.calc;
  if (!host || !calc) return;
  host.classList.remove('sb-stale');
  host.replaceChildren();

  const line = (label, value, hint) => {
    const item = h('div', 'sb-calc-line');
    item.append(h('span', undefined, label), h('b', undefined, value));
    if (hint) item.append(h('span', 'sb-sub', hint));
    return item;
  };

  // SET İNDİRİMİ EKSİYE DÜŞEBİLİR: set fiyatı bileşen toplamının üstündeyse
  // `setDiscount` negatiftir. Başına sabit `−` koyan sürüm bunu
  // “− −50,00 ₺” diye yazıyor, müşterinin kazandığını söyleyen ipucu da
  // “müşteri %-18,4 kazanıyor” çıkıyordu. İşareti veri belirler.
  const discount = calc.setDiscount;
  const surcharge = discount !== null && discount !== undefined && discount < 0;
  const discountText = discount === null || discount === undefined
    ? '—' : `${surcharge ? '+' : '−'} ${money(Math.abs(discount))}`;
  const savings = calc.savingsPercent;
  const savingsText = savings === null || savings === undefined ? ''
    : (surcharge
      ? `set fiyatı bileşen toplamının ${percent(Math.abs(savings))} ÜSTÜNDE`
      : `müşteri ${percent(savings)} kazanıyor`);

  const box = h('div', 'sb-calc-box');
  box.append(
    line('Bileşen toplamı', money(calc.componentsTotal),
      `${num(calc.requiredCount)} zorunlu bileşen`),
    line('Bileşen indirimi', calc.componentDiscount ? `− ${money(calc.componentDiscount)}` : '—'),
    line('Ara toplam', money(calc.afterComponentDiscount)),
    line(surcharge ? 'Set farkı' : 'Set indirimi', discountText, savingsText),
    line(`KDV (${percent(calc.taxRate)})`, calc.tax === null ? '—' : money(calc.tax)),
    line('SET FİYATI', calc.setPrice === null ? '—' : money(calc.setPrice),
      calc.setPrice === null ? 'mağazadaki ürün fiyatı geçerli' : ''),
  );

  const verdict = h('div', `sb-verdict ${calc.state}`);
  verdict.append(h('span', 'sb-verdict-label', calc.stateLabel));
  verdict.append(h('b', 'sb-verdict-value',
    calc.profit === null ? '—' : money(calc.profit)));
  if (calc.marginPercent !== null && calc.marginPercent !== undefined) {
    verdict.append(h('span', 'sb-sub', `marj ${percent(calc.marginPercent)}`));
  }
  if (calc.profit === null) {
    verdict.append(h('span', 'sb-sub', calc.unknownCosts.length
      ? `maliyeti girilmemiş: ${calc.unknownCosts.join(', ')}`
      : 'set fiyatı belirlenmedi'));
  }

  host.append(kpiRow([
    { label: 'Satılabilir set', value: calc.stockLimit === null ? '—' : num(calc.stockLimit),
      tone: calc.stockLimit === 0 ? 'bad' : '',
      title: calc.stockLimitedBy ? `En kısıtlı bileşen: ${calc.stockLimitedBy}` : '' },
    { label: 'Zorunlu bileşen', value: num(calc.requiredCount) },
    { label: 'Opsiyonel', value: num(calc.optionalCount) },
    { label: 'Opsiyonel toplamı', value: money(calc.optionalTotal) },
  ]), box, verdict);

  if (warning) host.append(alertBox(`Uyarı: ${warning}.`, 'warn'));
  if (calc.stockLimit === 0) {
    host.append(alertBox(
      'Bu set şu an satılamaz: en kısıtlı bileşende yeterli stok yok. Stok Ürünler '
      + 'ekranından yazılır.', 'warn'));
  }
}

// -------------------------------------------------------------- bileşen seç

function openPicker({ onPick } = {}) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog sb-picker');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title', onPick ? 'Set ürününü seçin' : 'Bileşen ekle'));
  box.append(h('p', 'kit-dialog-text', onPick
    ? 'Set bir üründür. Hangi ürünün set olacağını seçin; bileşenleri sonra tanımlanır.'
    : 'Arama mağazada yapılır; liste sunucudan gelir.'));

  const search = h('input', 'kit-input');
  search.type = 'search';
  search.placeholder = 'Ürün adı ara';
  box.append(search);

  const picker = createPicker({
    items: [],
    groupLabel: 'Durum',
    placeholder: 'Listede süz',
    single: Boolean(onPick),
  });
  box.append(picker.node);

  // Diyalog kapanınca kendi kapatıcısını LİSTEDEN DE ÇIKARIR. Her açılışta
  // `closers`'a iki giriş ekleyip hiç silmemek, uzun bir oturumda ölü
  // kapatıcıları biriktiriyordu.
  const close = () => {
    const at = closers.indexOf(release);
    if (at >= 0) closers.splice(at, 1);
    release();
    overlay.remove();
  };
  const release = () => {
    document.removeEventListener('keydown', onKey);
    lookupSoon.cancel();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  closers.push(release);

  const lookupSoon = debounce(async () => {
    let payload;
    try {
      payload = await call(`${BASE}/lookup?q=${encodeURIComponent(search.value)}`);
    } catch (error) {
      toast(error.message, 'bad');
      return;
    }
    picker.setItems((payload.items || []).map((item) => ({
      id: item.id,
      name: item.name,
      group: item.status ? 'Aktif' : 'Pasif',
      // Sunucu fiyatı ONDALIK METİN veriyor ("2363.00"); kuruşa çevirmek
      // kitin `parseMoney` işidir (kit kuralı 5). Elde `Number(x) * 100`
      // yazmak ikinci bir para ayrıştırma kuralı doğurur ve `1234.35` gibi
      // değerlerde bir kuruş aşağı kayar.
      meta: `${item.sku} · ${money(parseMoney(item.price))}`,
    })));
  }, 300);
  closers.push(() => lookupSoon.cancel());
  search.addEventListener('input', lookupSoon);

  const actions = h('div', 'kit-dialog-actions');
  actions.append(
    button('Vazgeç', { onClick: close }),
    button('Ekle', {
      variant: 'primary',
      onClick: () => {
        const chosen = picker.selectedItems();
        if (!chosen.length) { toast('Ürün seçilmedi.', 'warn'); return; }
        close();
        if (onPick) { onPick(Number(chosen[0].id)); return; }
        for (const item of chosen) {
          const productId = Number(item.id);
          if (productId === draft.id) { toast('Set kendi bileşeni olamaz.', 'bad'); continue; }
          if (draft.components.some((part) => part.productId === productId)) continue;
          draft.components.push({
            productId, sku: '', name: item.name, qty: 1, discount: 0, required: true,
            unitPrice: null, cost: null, stock: 0, status: true, missing: false,
          });
        }
        paintComponents();
        recalc();
      },
    }),
  );
  box.append(actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);
  lookupSoon();
}

async function importCrossSells(bundleId) {
  const payload = await withBusy('Çapraz satış bağları okunuyor…',
    () => call(`${BASE}/bundles/${bundleId}`));
  if (!payload) return;
  const links = payload.crossSells || [];
  if (!links.length) {
    toast('Bu ürünün çapraz satış bağı yok.', 'warn');
    return;
  }
  let added = 0;
  for (const item of links) {
    if (item.productId === draft.id) continue;
    if (draft.components.some((part) => part.productId === item.productId)) continue;
    draft.components.push({
      productId: item.productId, sku: item.sku, name: item.name, qty: 1, discount: 0,
      required: true, unitPrice: null, cost: null, stock: 0, status: true, missing: false,
    });
    added += 1;
  }
  paintComponents();
  recalc();
  toast(`${num(added)} bileşen eklendi — adet 1, indirim yok olarak. Çapraz satış bağı bu `
    + 'bilgileri taşımıyor; kontrol edin.', added ? 'good' : 'warn');
}

// ------------------------------------------------------------------- yazma

async function saveBundle(bundleId) {
  if (!draft.components.length) {
    toast('Set en az bir bileşen ister.', 'bad');
    return;
  }
  const calc = draft.calc || {};
  const uyari = calc.state === 'loss'
    ? ' DİKKAT: bu set zararına satılıyor.'
    : '';
  const reason = await askReason({
    title: 'Set künyesini kaydet',
    description: `${draft.name} · ${draft.components.length} bileşen. Bileşen adedi, indirimi `
      + 've zorunluluğu Kontrol Merkezi\'nde saklanır; mağaza ucu yayına girdiğinde oraya da '
      + `yazılır.${uyari}`,
    confirmLabel: 'Kaydet',
  });
  if (!reason) return;

  await withBusy('Set kaydediliyor…', async () => {
    const result = await call(`${BASE}/bundles/${bundleId}`, {
      method: 'PUT',
      body: {
        name: draft.name || '',
        sku: draft.sku || '',
        components: draft.components.map((item) => ({
          productId: item.productId,
          qty: item.qty,
          discount: item.discount,
          required: item.required !== false,
        })),
        pricingMode: draft.pricingMode,
        discountPercent: draft.discountPercent,
        setPrice: draft.pricingMode === 'fixed' && draft.setPrice !== null
          ? Number(draft.setPrice) : null,
        taxRate: draft.taxRate,
        validFrom: draft.validFrom || '',
        validTo: draft.validTo || '',
        note: draft.note || '',
        reason,
        dryRun: false,
      },
    });
    toast(result.stored ? 'Set kaydedildi ve mağazaya yazıldı.' : 'Set künyesi kaydedildi.',
      result.stored ? 'good' : 'warn');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

async function changeStatus(row) {
  const reason = await askReason({
    title: row.status ? 'Seti vitrinden kaldır' : 'Seti vitrine geri al',
    description: row.status
      ? `${row.name} vitrinden kalkar. SİLİNMEZ: geçmiş siparişlerde ve raporlarda kalır, `
        + 'bileşen künyesi de saklanır.'
      : `${row.name} yeniden satışa açılır.`,
    confirmLabel: row.status ? 'Vitrinden kaldır' : 'Geri al',
  });
  if (!reason) return;
  await withBusy('Uygulanıyor…', async () => {
    const result = await call(`${BASE}/bundles/${row.id}/status`, {
      method: 'POST',
      body: { active: !row.status, reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: mağazaya istek gönderilmedi.' : 'Uygulandı.',
      result.dryRun ? 'warn' : 'good');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

function newBundle() {
  openPicker({
    onPick: async (productId) => {
      await refresh({ keepSelection: false });
      if (state.items.some((row) => row.id === productId)) {
        selectBundle(productId);
        return;
      }
      // Seçilen ürün "Setler" kategorisinde değilse listede yoktur; künyesi
      // kaydedilince listeye girer. Sessizce kaybolmasın diye tek başına açılır.
      const payload = await withBusy('Ürün okunuyor…',
        () => call(`${BASE}/bundles/${productId}`));
      if (!payload) return;
      state.items = [...state.items, payload.bundle];
      state.selected = productId;
      renderList();
      renderEditor();
      toast('Bu ürün “Setler” kategorisinde değil. Künyeyi kaydedebilirsiniz; kategoriye '
        + 'eklemek Ürünler ekranından yapılır.', 'warn');
    },
  });
}

// ----------------------------------------------------------------- geçmiş

async function paintHistory(pane, bundleId) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await call(`${BASE}/audit?bundleId=${bundleId}&limit=50`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Bu sete bu ekrandan dokunulmadı',
      text: 'Kontrol Merkezi üzerinden yapılan her yazma gerekçesiyle burada listelenir.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'action', label: 'İşlem', width: '130px' },
      { key: 'actor', label: 'Kim', width: '120px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}`,
  });
  pane.append(table.node,
    hintBox('Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydında gerekçe '
      + 'alanı yok; ağ koparsa “ne yapmaya çalıştık” bilgisi yalnız burada kalır.'));
}

// -------------------------------------------------------------------- CSV

function exportVisible() {
  const rows = visibleRows();
  if (!rows.length) { toast('İndirilecek satır yok.', 'warn'); return; }
  const headers = ['Set', 'SKU', 'Bileşen', 'Set fiyatı', 'Bileşen toplamı', 'Kazanç/kayıp',
    'Kâr', 'Marj %', 'Durum', 'Uyarı'];
  const table = rows.map((row) => [
    row.name, row.sku, row.components.length,
    row.calc.setPrice === null ? '' : money(row.calc.setPrice),
    money(row.calc.afterComponentDiscount),
    row.calc.setDiscount === null ? '' : money(row.calc.setDiscount),
    row.calc.profit === null ? '' : money(row.calc.profit),
    row.calc.marginPercent === null ? '' : percent(row.calc.marginPercent),
    row.status ? 'Aktif' : 'Pasif', row.warning || '',
  ]);
  csvBlob(headers, table, 'setler-gorunen');
  toast(`${num(table.length)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Set listesi yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: {} });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  open = ctx.open;

  const view = h('div', 'kit-panel sb');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Set adı veya SKU ara', width: '220px' },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Tümü' }, { value: '1', label: 'Aktif' }, { value: '0', label: 'Pasif' },
      ] },
      { kind: 'select', key: 'validity', label: 'Geçerlilik', options: [
        { value: '', label: 'Tümü' },
        { value: 'active', label: 'Yürürlükte' },
        { value: 'scheduled', label: 'Başlamadı' },
        { value: 'expired', label: 'Süresi doldu' },
        { value: 'always', label: 'Süresiz' },
      ] },
      { kind: 'toggle', key: 'loss', label: 'Zararına satılıyor' },
      { kind: 'toggle', key: 'outOfStock', label: 'Bileşeni tükenmiş' },
      { kind: 'toggle', key: 'passive', label: 'Bileşeni pasif' },
    ],
    onChange: () => renderList(),
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('Yeni set', { variant: 'primary', onClick: newBundle }),
      button('⤓ Görünen', { title: 'Süzgeçten geçen satırları CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm setleri rapor klasörüne yaz', onClick: exportAll }),
      button('Set listesi', { onClick: () => report.run('sets') }),
      button('Riskli setler', { onClick: () => report.run('risk') }),
    ],
  });

  closers.push(() => recalcSoon.cancel());   // bekleyen hesap isteği iptal edilir
  nodes.table = null;      // `nodes` modül düzeyinde; önceki mount'un tablosu kalmasın
  nodes.status = statusLine();
  nodes.notice = h('div', 'sb-notice');
  nodes.listCount = h('div', 'sb-count');
  nodes.listBody = h('div', 'sb-list-body');
  nodes.editor = h('div', 'sb-editor');

  const left = h('div', 'sb-list');
  left.append(nodes.listCount, nodes.listBody);
  const body = splitView(left, nodes.editor, '380px minmax(0, 1fr)');
  body.classList.add('sb-split');

  view.append(nodes.filters.node, nodes.status.node, nodes.notice, body);

  root.replaceChildren(view);
  nodes.status.set('Setler alınıyor…');
  refresh({ keepSelection: false });

  return () => {
    nodes.filters?.destroy();          // arama alanı bekleyen debounce tutar
    dropForm();                        // formGrid'in tarih alanları global dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    draft = null;
    busy = false;
    nodes.table = null;                // sonraki mount kendi tablosunu kurar
  };
}
