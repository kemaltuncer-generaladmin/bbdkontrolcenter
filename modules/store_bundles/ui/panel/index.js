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
  always: 'Her zaman geçerli',
  active: 'Şu an geçerli',
  scheduled: 'Tarihi gelmedi',
  expired: 'Süresi doldu',
};

// KELİMELER İŞ DİLİNDE — "bileşen" DEĞİL "içindeki ürün".
//
// Bu ekranı kullanan kişi yazılım bilmiyor. "Bileşen", "düzenleyici",
// "künye", "bileşen toplamı − %x" hepsi doğruydu ve hiçbiri kullanıcının
// sözlüğünde yoktu; en kötüsü "Bileşen toplamı − %x" idi: bir formül gibi
// duruyor ama ne yaptığını söylemiyordu.
//
// ENGELLER — NEDEN + SIRADAKİ ADIM, tek yerde.
// Desen `store_shipping/backend/geliver.py` içindeki `BLOCKER_ACTIONS`'tan
// gelir: bir iş yapılamıyorsa ekran hem neden yapılamadığını hem de
// kullanıcının ŞİMDİ ne yapacağını söyler.
const BLOCKERS = {
  NO_COMPONENTS: {
    why: 'Bu sette hiç ürün yok. Bir set, en az bir üründen oluşur; içi boş bir kayıt '
      + 'zaten sıradan bir üründür.',
    next: 'Sıradaki adım: “Ürün ekle” deyip setin içine gireceklerini seçin.',
  },
  NO_SET_PRICE: {
    why: 'Set fiyatı yazılmadığı için kâr hesaplanamıyor; şu an mağazadaki ürün fiyatı '
      + 'geçerli.',
    next: 'Sıradaki adım: “Set fiyatı” kutusuna müşterinin ödeyeceği tutarı yazın.',
  },
  NO_COST: {
    why: 'İçindeki ürünlerden birinin ALIŞ fiyatı girilmemiş; eksik bilgiyle kâr hesabı '
      + 'yanıltıcı olurdu, bu yüzden çizgi gösteriyoruz.',
    next: 'Sıradaki adım: o ürünü Ürünler ekranında açıp “Maliyet” alanını doldurun.',
  },
  OUT_OF_STOCK: {
    why: 'Bu set şu an satılamıyor: içindeki ürünlerden birinin stoğu yetmiyor.',
    next: 'Sıradaki adım: Ürünler ekranından o ürünün stoğunu girin ya da ürünü setten '
      + 'çıkarın.',
  },
  OFFLINE: {
    why: 'Mağazaya ulaşılamadı; fiyat ve stok bilgisi okunamıyor.',
    next: 'Sıradaki adım: internet bağlantısını kontrol edip “Tekrar dene” deyin.',
  },
  UNREADABLE: {
    why: 'Bu setin ürün kaydı mağazadan okunamadı; fiyat ve stok bilgisi eksik.',
    next: 'Sıradaki adım: “Yenile” deyin. Aşağıdaki set bilgileri burada kayıtlı ve '
      + 'kaybolmadı.',
  },
};

/** Engelin iki cümlesini tek kutuda gösterir (neden + sıradaki adım). */
function blockerBox(key, tone = 'warn') {
  const item = BLOCKERS[key];
  const box = h('div', `kit-alert ${tone} sb-blocker`);
  box.append(h('div', 'sb-blocker-why', item.why));
  box.append(h('div', 'sb-blocker-next', item.next));
  return box;
}

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
    placeholder: 'Neden değiştiriyorsunuz? (en az 10 karakter) — "Okul listesi güncellendi" '
      + 'gibi. Bu not kayda geçer.',
  });
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const loss = state.items.filter((row) => row.flags.loss).length;
  const risk = state.items.filter((row) => row.warning).length;
  return `Mağazaya bağlı · ${num(state.items.length)} set · ${num(loss)} tanesi zararına `
    + `satılıyor · ${num(risk)} tanesinde dikkat edilecek bir durum var`;
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
      calc.unknownCosts.length ? 'alış fiyatı girilmemiş' : 'set fiyatı yazılmamış'));
    box.title = calc.unknownCosts.length
      ? `${BLOCKERS.NO_COST.why} Eksik olan: ${calc.unknownCosts.join(', ')}. `
        + BLOCKERS.NO_COST.next
      : `${BLOCKERS.NO_SET_PRICE.why} ${BLOCKERS.NO_SET_PRICE.next}`;
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
      if (row.onCarousel) head.append(badge('ana sayfada gösteriliyor', 'info'));
      box.append(head);
      const sub = h('span', 'sb-sub');
      sub.textContent = row.warning
        ? row.warning
        : `içinde ${row.components.length} ürün · ${row.sku || 'stok kodu yok'}`;
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
    width: '104px',
    cell: (row) => {
      const chip = badge(row.status ? 'Satışta' : 'Vitrinde yok',
        row.status ? 'good' : 'dim');
      chip.title = row.status
        ? 'Müşteri bu seti görüyor ve satın alabiliyor.'
        : 'Müşteri bu seti göremiyor. Silinmedi; istediğiniz gün geri alırsınız.';
      return chip;
    },
  },
];

function listEmpty() {
  if (!state.connected && !state.items.length) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: `${BLOCKERS.OFFLINE.why} ${BLOCKERS.OFFLINE.next}`
        + (state.error ? ` (Mağazanın verdiği cevap: ${state.error})` : ''),
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.items.length) {
    return emptyState({
      title: 'Aramanıza uyan set yok',
      text: `${num(state.items.length)} setin hiçbiri seçtiğiniz süzgeçlere uymuyor. `
        + 'Süzgeçleri temizleyip yeniden bakın.',
      actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
    });
  }
  return emptyState({
    title: 'Henüz hiç set yok',
    text: 'SET NEDİR: birkaç ürünü tek paket hâlinde, tek fiyata satmak. Örnek: '
      + '“1. sınıf kırtasiye seti” — içinde defter, kalem ve silgi var, müşteri tek '
      + 'fiyat ödüyor. Buradan setin içine hangi üründen kaç adet gireceğini ve '
      + 'müşterinin ne kadar kazandığını belirlersiniz.',
    actions: [button('Yeni set oluştur', { variant: 'primary', onClick: newBundle })],
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
  nodes.listCount.textContent = rows.length === state.items.length
    ? `${num(state.items.length)} set`
    : `${num(state.items.length)} setin ${num(rows.length)} tanesi gösteriliyor`;
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
      title: 'Soldaki listeden bir set seçin',
      text: 'Seçtiğiniz setin içinde hangi ürünler olduğu, kaça satıldığı ve o setten '
        + 'kazanıp kazanmadığınız burada açılır.',
      actions: [button('Yeni set oluştur', { variant: 'primary', onClick: newBundle })],
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
    { key: 'edit', label: 'Set bilgileri' },
    { key: 'history', label: 'Kim ne değiştirmiş' },
  ], 'edit', (key) => paint(key));
  const pane = h('div', 'sb-pane');

  const head = h('div', 'sb-head');
  head.append(
    h('b', 'sb-title', row.name),
    badge(row.status ? 'Satışta' : 'Vitrinde yok', row.status ? 'good' : 'dim'),
    badge(VALIDITY[row.validity] || row.validity, row.validity === 'expired' ? 'warn' : 'dim'),
  );
  if (row.sku) head.append(h('code', 'sb-sku', row.sku));
  head.append(h('span', 'kit-spacer'));
  head.append(button('Bu ürünü Ürünler ekranında aç', {
    variant: 'ghost',
    title: 'Setin fotoğrafı, kategorisi ve mağazadaki fiyatı Ürünler ekranından değişir',
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
    pane.append(blockerBox('UNREADABLE'));
  }
  if (!row.hasPlan) {
    pane.append(hintBox(
      'Bu setin içinde ne olduğu henüz yazılmadı. Mağaza yalnız “bu ürünle şunlar da '
      + 'alınıyor” bilgisini tutuyor; hangi üründen KAÇ ADET gireceğini, hangisine '
      + 'indirim yapılacağını ve hangisinin zorunlu olduğunu buradan siz belirlersiniz.'));
  }

  editorForm = formGrid({
    fields: [
      { key: 'name', label: 'Setin adı', type: 'text', maxLength: 180, wide: true,
        hint: 'Müşterinin vitrinde göreceği ad. Örnek: “1. sınıf kırtasiye seti”.' },
      // VARSAYILAN “Fiyatı ben yazacağım”: iki seçenek de meşru ama biri
      // kullanıcının kafasında zaten var (“şu kadara satacağım”), öteki
      // hesap gerektiriyor. Karar gereken yerde kolay olan seçili gelir.
      { key: 'pricingMode', label: 'Set fiyatı nasıl belirlensin?', type: 'select', options: [
        { value: 'fixed', label: 'Fiyatı ben yazacağım' },
        { value: 'percent', label: 'İçindekilerin toplamından yüzde indir' },
      ], hint: '“Fiyatı ben yazacağım” derseniz aşağıdaki kutuya tutarı yazarsınız. '
        + 'Diğerinde fiyat, içindekilerin toplamından indirim düşülerek kendiliğinden '
        + 'hesaplanır.' },
      { key: 'discountPercent', label: 'İçindekilerin toplamından % kaç inelim?',
        type: 'number', min: 0, max: 100,
        hint: 'Yalnız “yüzde indir” seçtiyseniz kullanılır. 10 yazarsanız müşteri, '
          + 'ürünleri tek tek almaya göre %10 kazanır.' },
      { key: 'setPrice', label: 'Set fiyatı (müşterinin ödeyeceği, KDV dâhil)',
        type: 'money',
        hint: 'Boş bırakırsanız mağazadaki ürün fiyatı geçerli olur.' },
      { key: 'taxRate', label: 'KDV oranı (%)', type: 'number', min: 0, max: 100,
        hint: 'Kitapta genelde %0 ya da %1. Emin değilseniz olduğu gibi bırakın.' },
      { key: 'validFrom', label: 'Bu fiyat ne zaman başlasın?', type: 'date',
        hint: 'Boş bırakırsanız hemen geçerli olur.' },
      { key: 'validTo', label: 'Ne zaman bitsin?', type: 'date',
        hint: 'Boş bırakırsanız siz değiştirene kadar sürer.' },
      { key: 'note', label: 'Kendinize not', type: 'text', maxLength: 500, wide: true,
        hint: 'MÜŞTERİ BU NOTU GÖRMEZ. “Okul listesinden geldi” gibi kendi hatırlatmanız.' },
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
    button('Ürün ekle', {
      variant: 'primary',
      title: 'Setin içine girecek ürünü listeden seçin',
      onClick: openPicker,
    }),
    button('Mağazanın önerdiklerinden getir', {
      title: 'Mağazada “bu ürünle şunlar da alınıyor” diye bağlanmış ürünleri setin '
        + 'içine ekler (her birinden 1 adet, indirimsiz)',
      onClick: () => importCrossSells(row.id),
    }),
  );

  const saveActions = h('div', 'sb-actions');
  saveActions.append(
    button('Kaydet', {
      variant: 'primary',
      title: 'Setin içeriğini ve fiyatını kaydeder',
      onClick: () => saveBundle(row.id),
    }),
    button(row.status ? 'Satıştan kaldır' : 'Yeniden satışa aç', {
      variant: row.status ? 'danger' : '',
      title: row.status
        ? 'Müşteri bu seti göremez olur. SİLİNMEZ; istediğiniz gün geri alırsınız.'
        : 'Müşteri bu seti yeniden görmeye ve satın almaya başlar.',
      onClick: () => changeStatus(row),
    }),
  );

  pane.append(
    card('Set bilgileri', editorForm.node),
    card('Setin içinde ne var?', nodes.components,
      'Her satırda: hangi üründen kaç adet, o ürüne ne kadar indirim, zorunlu mu'),
    componentActions,
    nodes.calc,
    saveActions,
    hintBox('KÂR NASIL BULUNUR: setin KDV’siz satış fiyatından, içindeki ürünlerin alış '
      + 'fiyatları düşülür. Vitrindeki fiyat KDV DÂHİL, mağazadaki “maliyet” alanı KDV '
      + 'HARİÇ yazılıyor; ikisini doğrudan çıkarmak seti olduğundan kârlı gösterirdi, '
      + 'bu yüzden hesap KDV ayıklanarak yapılır.'),
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
      title: 'Setin içi boş',
      text: `${BLOCKERS.NO_COMPONENTS.why} ${BLOCKERS.NO_COMPONENTS.next}`,
      actions: [button('Ürün ekle', { variant: 'primary', onClick: openPicker })],
    }));
    return;
  }

  const head = h('div', 'sb-comp-row sb-comp-head');
  const col = (text, title, cls) => {
    const node = h('span', cls, text);
    node.title = title;
    return node;
  };
  head.append(
    col('Ürün', 'Setin içine giren ürün'),
    col('Adet', 'Bu üründen sette kaç tane var'),
    col('İndirim %', 'Yalnız bu ürüne yapılan indirim. Boş bırakabilirsiniz.'),
    col('Zorunlu', 'İşaretliyse müşteri bu ürünü setten çıkaramaz.'),
    col('Birim fiyat', 'Ürünün tek başına satış fiyatı', 'sb-right'),
    col('Satır tutarı', 'Adet × birim fiyat − indirim', 'sb-right'),
    h('span', undefined, ''),
  );
  host.append(head);

  draft.components.forEach((item, index) => {
    const line = h('div', 'sb-comp-row');

    const name = h('span', 'sb-name');
    name.append(clip(h('b'), item.name, 30));
    const sub = h('span', 'sb-sub');
    const marks = [];
    if (item.missing) marks.push('bu ürün mağazadan okunamadı');
    else {
      if (!item.status) marks.push('VİTRİNDE YOK');
      if (item.stock < item.qty) marks.push(`stokta yalnız ${num(item.stock)} adet var`);
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
    required.setAttribute('aria-label', 'Bu ürün sette zorunlu olsun');
    required.title = 'İşaretliyse müşteri bu ürünü setten çıkaramaz.';
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
      button('Setten çıkar', {
        variant: 'ghost',
        title: 'Bu ürünü setin içinden çıkarır. ÜRÜN SİLİNMEZ, mağazada durmaya devam eder.',
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
    nodes.calc?.replaceChildren(alertBox(
      `Kâr hesabı yapılamadı — ${error.message}. Sıradaki adım: “Yenile” deyip yeniden `
      + 'deneyin.', 'warn'));
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
      ? `DİKKAT: set fiyatı, ürünleri tek tek almaktan ${percent(Math.abs(savings))} PAHALI`
      : `müşteri, ürünleri tek tek almaya göre ${percent(savings)} kazanıyor`);

  // HESABIN HER SATIRI TEK CÜMLEYLE AÇIKLANIR. Eskiden "Bileşen toplamı",
  // "Ara toplam", "Set indirimi" yazıyordu: doğru ama toplamların birbirinden
  // nasıl çıktığı ekranda hiçbir yerde yazmıyordu ve kullanıcı sayıyı görüp
  // "bu nereden geldi" diye kalıyordu.
  const box = h('div', 'sb-calc-box');
  box.append(
    line('İçindekilerin toplamı', money(calc.componentsTotal),
      `ürünler tek tek alınsa bu kadar tutardı · ${num(calc.requiredCount)} zorunlu ürün`),
    line('İçindekilere verilen indirim',
      calc.componentDiscount ? `− ${money(calc.componentDiscount)}` : '—',
      calc.componentDiscount ? 'tek tek ürünlere yazdığınız indirimlerin toplamı' : ''),
    line('Ara toplam', money(calc.afterComponentDiscount),
      'indirim düşüldükten sonra kalan'),
    line(surcharge ? 'Set fiyatı fazlası' : 'Sete verilen indirim', discountText, savingsText),
    line(`KDV (${percent(calc.taxRate)})`, calc.tax === null ? '—' : money(calc.tax),
      'fiyatın içindeki vergi'),
    line('MÜŞTERİNİN ÖDEYECEĞİ', calc.setPrice === null ? '—' : money(calc.setPrice),
      calc.setPrice === null
        ? 'set fiyatı yazılmadı; mağazadaki ürün fiyatı geçerli' : 'KDV dâhil'),
  );

  const verdict = h('div', `sb-verdict ${calc.state}`);
  verdict.append(h('span', 'sb-verdict-label', calc.stateLabel));
  verdict.append(h('b', 'sb-verdict-value',
    calc.profit === null ? '—' : money(calc.profit)));
  if (calc.marginPercent !== null && calc.marginPercent !== undefined) {
    verdict.append(h('span', 'sb-sub', `her 100 TL satışın ${percent(calc.marginPercent)} `
      + 'kadarı kâr'));
  }
  // KÂR NEDEN BİLİNMİYOR — NEDEN + SIRADAKİ ADIM. Eskiden yalnız "maliyeti
  // girilmemiş" yazıyordu: doğru bir tespit ama kullanıcı nereye gideceğini
  // bilmiyordu.
  if (calc.profit === null) {
    const engel = calc.unknownCosts.length ? 'NO_COST' : 'NO_SET_PRICE';
    const detay = calc.unknownCosts.length
      ? ` Eksik olan: ${calc.unknownCosts.join(', ')}.` : '';
    verdict.append(h('span', 'sb-sub',
      `${BLOCKERS[engel].why}${detay} ${BLOCKERS[engel].next}`));
  }

  host.append(kpiRow([
    { label: 'Kaç set satılabilir', value: calc.stockLimit === null ? '—' : num(calc.stockLimit),
      tone: calc.stockLimit === 0 ? 'bad' : '',
      title: calc.stockLimitedBy
        ? `Stoğu en önce biten ürün: ${calc.stockLimitedBy}. Set sayısını o belirliyor.`
        : 'İçindeki ürünlerin stoğuna göre kaç set çıkar.' },
    { label: 'Zorunlu ürün', value: num(calc.requiredCount),
      title: 'Müşterinin setten çıkaramayacağı ürünler.' },
    { label: 'İsteğe bağlı ürün', value: num(calc.optionalCount),
      title: 'Müşteri isterse setten çıkarabilir.' },
    { label: 'İsteğe bağlıların tutarı', value: money(calc.optionalTotal),
      title: 'Zorunlu olmayan ürünlerin toplam tutarı.' },
  ]), box, verdict);

  if (warning) host.append(alertBox(`Dikkat: ${warning}.`, 'warn'));
  if (calc.stockLimit === 0) host.append(blockerBox('OUT_OF_STOCK'));
}

// -------------------------------------------------------------- bileşen seç

function openPicker({ onPick } = {}) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog sb-picker');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title',
    onPick ? 'Hangi ürün set olacak?' : 'Setin içine ürün ekle'));
  box.append(h('p', 'kit-dialog-text', onPick
    ? 'Mağazada “set” diye ayrı bir ürün türü yok; set de bir üründür. Önce hangi ürünün '
      + 'set olacağını seçin, içine ne gireceğini sonraki adımda belirlersiniz.'
    : 'Aradığınız ürünün adının bir parçasını yazın; liste mağazadan gelir.'));

  const search = h('input', 'kit-input');
  search.type = 'search';
  search.placeholder = 'Ürün adının bir parçasını yazın';
  box.append(search);

  const picker = createPicker({
    items: [],
    groupLabel: 'Durum',
    placeholder: 'Gelen listede ara',
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
      group: item.status ? 'Satışta' : 'Vitrinde yok',
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
    button(onPick ? 'Bu ürünü seç' : 'Sete ekle', {
      variant: 'primary',
      onClick: () => {
        const chosen = picker.selectedItems();
        if (!chosen.length) {
          toast('Hiçbir ürün işaretlemediniz. Listeden en az bir ürün seçin.', 'warn');
          return;
        }
        close();
        if (onPick) { onPick(Number(chosen[0].id)); return; }
        for (const item of chosen) {
          const productId = Number(item.id);
          if (productId === draft.id) {
            toast('Bir set kendi kendisinin içinde olamaz; o satır atlandı.', 'bad');
            continue;
          }
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
  const payload = await withBusy('Mağazanın önerdiği ürünler okunuyor…',
    () => call(`${BASE}/bundles/${bundleId}`));
  if (!payload) return;
  const links = payload.crossSells || [];
  if (!links.length) {
    toast('Mağazada bu ürünle birlikte önerilen başka ürün tanımlanmamış. Setin içine '
      + '“Ürün ekle” ile tek tek ekleyebilirsiniz.', 'warn');
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
  toast(`${num(added)} ürün eklendi — her birinden 1 adet, indirimsiz. Mağazanın önerdiği `
    + 'bağlar adet ve indirim bilgisi taşımıyor; adetleri gözden geçirin.',
  added ? 'good' : 'warn');
}

// ------------------------------------------------------------------- yazma

async function saveBundle(bundleId) {
  if (!draft.components.length) {
    toast(`${BLOCKERS.NO_COMPONENTS.why} ${BLOCKERS.NO_COMPONENTS.next}`, 'bad');
    return;
  }
  const calc = draft.calc || {};
  const uyari = calc.state === 'loss'
    ? ' DİKKAT: BU SET ZARARINA SATILIYOR — her satışta para kaybediyorsunuz. Yine de '
      + 'kaydedebilirsiniz, ama önce fiyatı gözden geçirmenizi öneririz.'
    : '';
  const reason = await askReason({
    title: 'Set bilgilerini kaydet',
    description: `“${draft.name}” · içinde ${draft.components.length} ürün var. Hangi üründen `
      + 'kaç adet gireceği, indirimleri ve zorunlu olup olmadıkları burada saklanır.'
      + `${uyari}`,
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
    toast(result.stored
      ? 'Set kaydedildi ve mağazaya yazıldı.'
      : 'Set bilgileri kaydedildi. Mağazadaki “Setler” kategorisine eklenmesi ayrı bir '
        + 'adım — aşağıdaki nota bakın.',
    result.stored ? 'good' : 'warn');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

async function changeStatus(row) {
  const reason = await askReason({
    title: row.status ? 'Seti satıştan kaldır' : 'Seti yeniden satışa aç',
    description: row.status
      ? `“${row.name}” müşteriye görünmez olur. SİLİNMEZ: geçmiş siparişlerde ve `
        + 'raporlarda kalır, içindeki ürünlerin listesi de saklanır. İstediğiniz gün '
        + 'tek düğmeyle geri alırsınız.'
      : `“${row.name}” yeniden satışa açılır; müşteri görmeye başlar.`,
    confirmLabel: row.status ? 'Satıştan kaldır' : 'Satışa aç',
  });
  if (!reason) return;
  await withBusy('Uygulanıyor…', async () => {
    const result = await call(`${BASE}/bundles/${row.id}/status`, {
      method: 'POST',
      body: { active: !row.status, reason, dryRun: false },
    });
    toast(result.dryRun
      ? 'DENEME yapıldı: mağazaya hiçbir şey yazılmadı.'
      : 'Yapıldı.', result.dryRun ? 'warn' : 'good');
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
      toast('Bu ürün mağazadaki “Setler” kategorisinde değil. Set bilgilerini yine de '
        + 'kaydedebilirsiniz; kategoriye eklemek Ürünler ekranından yapılır.', 'warn');
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
      title: 'Bu sete bu ekrandan hiç dokunulmamış',
      text: 'Buradan yapılan her değişiklik; kimin, ne zaman ve neden yaptığıyla birlikte '
        + 'bu listeye yazılır.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Ne zaman', width: '150px' },
      { key: 'action', label: 'Ne yapıldı', width: '130px' },
      { key: 'actor', label: 'Kim yaptı', width: '120px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Neden yaptı', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}`,
  });
  pane.append(table.node,
    hintBox('Bu liste bu bilgisayarda tutulur ve “neden” notunu da saklar. Mağazanın '
      + 'kendi kaydında böyle bir not alanı yok; internet koparsa “ne yapmaya çalışmıştık” '
      + 'bilgisi yalnız burada kalır.'));
}

// -------------------------------------------------------------------- CSV

function exportVisible() {
  const rows = visibleRows();
  if (!rows.length) {
    toast('Ekranda hiç satır yok; indirilecek bir şey bulunamadı.', 'warn');
    return;
  }
  const headers = ['Set', 'Stok kodu', 'Kaç ürün', 'Set fiyatı', 'İçindekilerin toplamı',
    'Müşterinin kazancı', 'Kâr', 'Kâr oranı %', 'Durum', 'Dikkat'];
  const table = rows.map((row) => [
    row.name, row.sku, row.components.length,
    row.calc.setPrice === null ? '' : money(row.calc.setPrice),
    money(row.calc.afterComponentDiscount),
    row.calc.setDiscount === null ? '' : money(row.calc.setDiscount),
    row.calc.profit === null ? '' : money(row.calc.profit),
    row.calc.marginPercent === null ? '' : percent(row.calc.marginPercent),
    row.status ? 'Satışta' : 'Vitrinde yok', row.warning || '',
  ]);
  csvBlob(headers, table, 'setler-gorunen');
  toast(`${num(table.length)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Set listesi yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: {} });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya kaydedildi: ${result.path}`);
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
      { kind: 'search', key: 'q', placeholder: 'Set adı ya da stok kodu ara', width: '220px' },
      // SÜZGEÇLERDE VARSAYILAN "Hepsi": ekran hiçbir şeyi gizlemeden açılır.
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Hepsi' },
        { value: '1', label: 'Satışta' },
        { value: '0', label: 'Vitrinde yok' },
      ] },
      { kind: 'select', key: 'validity', label: 'Fiyat geçerliliği', options: [
        { value: '', label: 'Hepsi' },
        { value: 'active', label: 'Şu an geçerli' },
        { value: 'scheduled', label: 'Tarihi gelmedi' },
        { value: 'expired', label: 'Süresi doldu' },
        { value: 'always', label: 'Her zaman geçerli' },
      ] },
      { kind: 'toggle', key: 'loss', label: 'Zararına satılanlar' },
      { kind: 'toggle', key: 'outOfStock', label: 'İçindeki ürün tükenmiş' },
      { kind: 'toggle', key: 'passive', label: 'İçindeki ürün vitrinde yok' },
    ],
    onChange: () => renderList(),
    actions: [
      button('Yenile', {
        title: 'Mağazadaki güncel fiyat ve stokları yeniden okur',
        onClick: () => refresh(),
      }),
      button('Yeni set oluştur', { variant: 'primary', onClick: newBundle }),
      button('⤓ Ekrandakiler', {
        title: 'Şu an listede görünen satırları Excel dosyası olarak indirir',
        onClick: exportVisible,
      }),
      button('⤓ Hepsi', {
        title: 'Bütün setleri rapor klasörüne yazar',
        onClick: exportAll,
      }),
      button('Set listesi raporu', {
        title: 'Bütün setleri gösteren, yazdırılabilir PDF hazırlar',
        onClick: () => report.run('sets'),
      }),
      button('Sorunlu setler raporu', {
        title: 'Zararına satılan ya da içindeki ürünü tükenmiş setleri listeler',
        onClick: () => report.run('risk'),
      }),
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
