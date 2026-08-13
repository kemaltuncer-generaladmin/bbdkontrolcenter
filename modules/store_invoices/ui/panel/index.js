// Fatura paneli — mağaza faturaları, irsaliyeler, seri denetimi ve dönem icmali.
//
// NE YAPAR: sunucu tarafında sayfalanmış fatura listesi; satırdan çekmecede
// kalemler ve YASAL FATURA NO eşleme formu; seçili siparişlere toplu fatura
// kesme; seçili faturaların tek dosyada birleşik PDF'i (yazdırılır); mali
// müşavir için dönem icmali PDF'i; seri tanımları ve NUMARA BOŞLUĞU uyarısı.
//
// NE YAPMAZ — ve bunu gizlemez:
//  · YASAL FATURA KESMEZ. Depoda e-Fatura/e-Arşiv (GİB) entegrasyonu YOKTUR
//    ve Bagisto'nun PDF'i mali belge değildir. Ekranın tepesinde KAPATILAMAZ
//    bir uyarı bandı durur; yasal belge dış sistemde kesilir ve numarası
//    buradan eşlenir. "GİB'e gönder" düğmesi YOK — olmayan bir uca bağlı
//    düğme koymak, kullanıcıya gönderdiğini sandırırdı.
//  · Fatura SİLMEZ. Kesilen belge silinmez; iptali iade faturasıdır.
//  · Tam listeyi çekip istemcide süzmez; sayfalama sunucudadır.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · KDV oranı KALEMDEDİR ve canlıda kalemde de oran ALANI YOKTUR; oran KDV
//    tutarının matraha bölümünden TÜRETİLİR ve "türetildi" rozetiyle
//    gösterilir. Tutar hiç gelmezse "Ayrıştırılamadı" — sıfıra yazmak
//    muafiyet gibi görünürdü.
//  · Seri/numara Bagisto'da YOKTUR; yerel tablodadır. Boşluk uyarısı ("A2026
//    serisinde 145-147 eksik") bizim kaydımıza bakar, mağazaya değil.
//  · ÜÇ SÜZGEÇ YEREL: arama, tutar aralığı ve "yasal no girilmemiş". İlk
//    ikisini mağaza sessizce yok sayıyor (canlıda denendi), üçüncüsünün
//    mağazada karşılığı yok. Yalnız GELEN SAYFA süzülür ve bu ekranda
//    yazılır — eksik liste sessizce gösterilmez.
//  · Toplu fatura kesme adayları, sipariş satırındaki `invoices` dizisinden
//    DEĞİL fatura listesinden çapraz denetlenir: canlı sipariş listesi o
//    diziyi hiç taşımıyor ve herkes "faturasız" görünüyordu.
//  · İrsaliye sekmesi Bagisto gönderi kaydıdır; üretilen döküm taslaktır.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_invoices/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_invoices/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, csvBlob, h, loadStyles, money, num,
  toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_invoices';

const STATE_OPTIONS = [
  { value: '', label: 'Tümü — durum' },
  { value: 'paid', label: 'Ödendi' },
  { value: 'pending', label: 'Bekliyor' },
  { value: 'overdue', label: 'Gecikti' },
  { value: 'refunded', label: 'İade edildi' },
];

//: Mağazaya YAZILABİLEN durumlar. Listede olmayanı göndermek 422 üretir.
const WRITABLE_STATES = [
  { value: 'paid', label: 'Ödendi' },
  { value: 'pending', label: 'Bekliyor' },
  { value: 'overdue', label: 'Gecikti' },
];

/** Başlangıç durumu FONKSİYONDUR: sabit nesne olsaydı iç içe alanlar
 *  (`loaded`, `ships`, `series`) yayılırken referansla kopyalanır ve panel
 *  kapanıp açıldığında önceki oturumun "yüklendi" bayrağı geri gelirdi. */
function freshState() {
  return {
    tab: 'invoices',
    items: [], total: 0, page: 1, size: 50, pages: 0,
    connected: false, error: '', stateFilter: null, hiddenByLocal: 0, localFilters: [],
    // Sipariş ekranından `open('store_invoices', {orderId})` ile gelinince
    // dolar. Süzgeç şeridinde karşılığı yok; kendi şeridiyle gösterilir ve
    // bırakılabilir, yoksa kullanıcı "faturaların yarısı nerede" derdi.
    orderId: 0,
    selection: [],
    ships: { items: [], total: 0, page: 1, size: 50, pages: 0, connected: false, error: '',
      hiddenByLocal: 0, localFilters: [] },
    shipSelection: [],
    series: { items: [], orphans: [], defaultSeries: '', matchedCount: 0 },
    loaded: { invoices: false, shipments: false, series: false },
  };
}

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = freshState();

const nodes = {};
// Açık formGrid'ler. Çekmece kapanınca kendi formlarını bırakır; bu liste
// panel çekmece AÇIKKEN kapatılırsa devreye giren güvenlik ağıdır (kabuk
// `root.replaceChildren()` yaptığında çekmecenin `onClose`'u çalışmaz).
const forms = [];

function trackForm(form) {
  forms.push(form);
  return form;
}

/** Verilen formları bırakır ve ağdan çıkarır — iki kez destroy edilmesin. */
function releaseForms(list) {
  for (const form of list) {
    const index = forms.indexOf(form);
    if (index >= 0) forms.splice(index, 1);
    try {
      form.destroy();
    } catch { /* kapanışta hata yutulur */ }
  }
  list.length = 0;
}

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

/** Yıkıcı ve dışarıya iş çıkaran her işlem buradan geçer: gerekçe backend'e gider. */
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
  const hidden = state.hiddenByLocal
    ? ` · ${num(state.hiddenByLocal)} satır yerel süzgeçle elendi`
    : '';
  return `Bağlı · ${num(state.total)} fatura · sayfa ${state.page}/${pages}${hidden}`;
}

// -------------------------------------------------------------------- veri

function filterValues() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  const total = values.total || {};
  return {
    q: values.q || '',
    state: values.state || '',
    start: range.start || '',
    end: range.end || '',
    minTotal: total.min ?? null,
    maxTotal: total.max ?? null,
    unmatched: Boolean(values.unmatched),
  };
}

function queryString(extra = {}) {
  const params = new URLSearchParams();
  const base = { ...filterValues(), ...extra };
  if (state.orderId) base.orderId = state.orderId;
  for (const [key, value] of Object.entries(base)) {
    if (value === null || value === undefined || value === '' || value === false) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refresh({ page = state.page, size = state.size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('Faturalar alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/invoices?${queryString({ page, size })}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, items: [], total: 0,
      hiddenByLocal: 0, localFilters: [] };
    renderInvoiceTable();
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
    stateFilter: payload.stateFilter,
    hiddenByLocal: payload.hiddenByLocal || 0,
    localFilters: payload.localFilters || [],
    selection: [],
  };
  state.loaded.invoices = true;
  renderInvoiceTable();
  renderKpi();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
}

async function refreshShipments({ page = state.ships.page, size = state.ships.size } = {}) {
  nodes.shipWrap?.replaceChildren(skeletonRows(6, 6));
  const values = filterValues();
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (values.q) params.set('q', values.q);
  if (values.start) params.set('start', values.start);
  if (values.end) params.set('end', values.end);
  let payload;
  try {
    payload = await api(`${BASE}/shipments?${params.toString()}`);
  } catch (error) {
    state.ships = { ...state.ships, connected: false, error: error.message, items: [], total: 0 };
    renderShipTable();
    return;
  }
  state.ships = {
    items: payload.items || [],
    total: payload.total || 0,
    page: payload.page || page,
    size: payload.size || size,
    pages: payload.pages || 0,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    hiddenByLocal: payload.hiddenByLocal || 0,
    localFilters: payload.localFilters || [],
  };
  state.shipSelection = [];
  state.loaded.shipments = true;
  renderShipTable();
  nodes.shipPager.update({ total: state.ships.total, page: state.ships.page,
    size: state.ships.size });
}

async function refreshSeries() {
  let payload;
  try {
    payload = await api(`${BASE}/series`);
  } catch (error) {
    toast(error.message, 'bad');
    return;
  }
  state.series = {
    items: payload.items || [],
    orphans: payload.orphans || [],
    defaultSeries: payload.defaultSeries || '',
    matchedCount: payload.matchedCount || 0,
  };
  state.loaded.series = true;
  renderSeries();
}

// ------------------------------------------------------------------- çizim

/** KAPATILAMAZ uyarı bandı. Bu ekranın en önemli tek cümlesi budur. */
function legalBanner() {
  const box = h('div', 'iv-legal');
  box.append(h('b', undefined, 'Bu ekran yasal fatura kesmez.'));
  box.append(h('span', undefined,
    'Bagisto PDF’i sipariş dökümüdür; e-Arşiv/e-Fatura belgesi değildir ve sistemde '
    + 'GİB entegrasyonu yoktur. Yasal belgeyi dış sistemde kesin, numarasını satırdaki '
    + '“Yasal fatura no” alanından eşleyin.'));
  return box;
}

function renderKpi() {
  if (!nodes.kpi) return;
  const rows = state.items;
  const missing = rows.filter((row) => !row.legalMatched).length;
  const tax = rows.reduce((sum, row) => sum + (row.tax || 0), 0);
  const total = rows.reduce((sum, row) => sum + (row.total || 0), 0);
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Sayfadaki fatura', value: num(rows.length),
      title: `Toplam ${num(state.total)} kayıt` },
    { label: 'Sayfa toplamı', value: money(total) },
    { label: 'Sayfa KDV', value: money(tax) },
    { label: 'Yasal no eşlenmemiş', value: num(missing), tone: missing ? 'bad' : 'good',
      title: 'Bu faturaların mali belgesi dış sistemde bulunamaz.' },
  ]));
}

function legalCell(row) {
  const box = h('span', 'iv-legalno');
  if (row.legalMatched) {
    box.append(h('b', 'mono', row.legalNo));
    if (row.legalDate) box.append(h('span', 'iv-sub', row.legalDate));
    return box;
  }
  // Renk tek başına anlam taşımaz: rozetin yanında yazı da var.
  box.append(badge('eşlenmedi', 'bad'));
  return box;
}

const INVOICE_COLUMNS = [
  { key: 'number', label: 'Fatura no', width: 'minmax(0, 1.1fr)', className: 'mono',
    sortable: true },
  { key: 'legalNo', label: 'Yasal fatura no', width: 'minmax(0, 1.3fr)', cell: legalCell },
  { key: 'createdAt', label: 'Tarih', width: '124px', sortable: true },
  { key: 'orderNumber', label: 'Sipariş', width: 'minmax(0, 1fr)', className: 'mono' },
  {
    key: 'customer',
    label: 'Müşteri',
    width: 'minmax(0, 2fr)',
    cell: (row) => {
      const box = h('span', 'iv-name');
      box.append(clip(h('b'), row.customer, 34));
      box.append(h('span', 'iv-sub',
        row.taxId ? `${row.partyKindLabel} · ${row.taxId}` : row.partyKindLabel));
      return box;
    },
  },
  { key: 'net', label: 'Matrah', width: '112px', align: 'num', sortable: true,
    cell: (row) => money(row.net) },
  { key: 'tax', label: 'KDV', width: '104px', align: 'num', cell: (row) => money(row.tax) },
  { key: 'total', label: 'Toplam', width: '118px', align: 'num', sortable: true,
    cell: (row) => money(row.total) },
  { key: 'state', label: 'Durum', width: '96px',
    cell: (row) => badge(row.stateLabel, row.stateTone) },
];

function invoiceEmpty() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  return emptyState({
    title: 'Bu filtreye uyan fatura yok',
    text: 'Tarih aralığını genişletin ya da süzgeçleri temizleyin.',
    actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
  });
}

function renderInvoiceTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  if (state.orderId) {
    const line = h('div', 'iv-focus');
    line.append(h('b', undefined, `Yalnız #${state.orderId} numaralı siparişin faturaları`));
    line.append(h('span', 'kit-spacer'));
    line.append(button('Tüm faturalara dön', {
      onClick: () => { state.orderId = 0; refresh({ page: 1 }); },
    }));
    wrap.append(line);
  }

  if (state.stateFilter === false) {
    wrap.append(alertBox(
      'Durum süzgeci mağaza tarafında uygulanmadı; liste SÜZÜLMEMİŞTİR. Sayfada süzmek '
      + 'tüm dönemi temsil etmeyeceği için yapılmadı.', 'warn'));
  }
  // Yerel süzgeç sessiz kalırsa kullanıcı eksik listeyi tam sanar. Hangi
  // süzgecin yerel çalıştığı ve kaç satırın elendiği HER ZAMAN yazılır.
  if (state.localFilters.length) {
    wrap.append(alertBox(
      `Şu süzgeç(ler) YALNIZ BU SAYFADA uygulandı: ${state.localFilters.join(', ')}. `
      + 'Arama ve tutar aralığını mağaza sorgu olarak kabul etmiyor, yasal numara ise '
      + `Kontrol Merkezi'nin kaydı. Bu sayfadan ${num(state.hiddenByLocal)} satır elendi; `
      + 'toplam ve sayfa sayısı mağazanın rakamıdır, elenenleri de sayar. Tam sonuç için '
      + 'tarih aralığını ve durumu daraltın — bu ikisi mağaza tarafında süzülüyor.',
      'warn'));
  }

  nodes.table = dataTable({
    columns: INVOICE_COLUMNS,
    rows: state.items,
    selectable: true,
    empty: invoiceEmpty(),
    onRow: (row) => openInvoice(row),
    onSelect: (ids) => { state.selection = ids; renderSelectionBar(); },
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
  bar.append(h('b', undefined, `${num(count)} fatura seçildi`));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Birleşik PDF', {
      variant: 'primary',
      title: 'Seçili faturaların PDF’lerini tek dosyada birleştirir ve yazdırır',
      onClick: () => report.run('combined', { invoiceIds: state.selection.map(Number) }),
    }),
    button('Durum değiştir', { onClick: () => stateDialog() }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => { nodes.table.clearSelection(); state.selection = []; renderSelectionBar(); },
    }),
  );
}

// -------------------------------------------------------------- irsaliyeler

const SHIP_COLUMNS = [
  { key: 'id', label: 'Gönderi', width: '92px', className: 'mono',
    cell: (row) => `#${row.id}` },
  { key: 'createdAt', label: 'Tarih', width: '124px' },
  { key: 'orderNumber', label: 'Sipariş', width: 'minmax(0, 1fr)', className: 'mono' },
  { key: 'customer', label: 'Müşteri', width: 'minmax(0, 2fr)',
    cell: (row) => clip(h('span'), row.customer, 38) },
  { key: 'carrier', label: 'Taşıyıcı', width: 'minmax(0, 1fr)' },
  { key: 'trackNumber', label: 'Takip no', width: 'minmax(0, 1.2fr)', className: 'mono' },
  { key: 'itemCount', label: 'Kalem', width: '84px', align: 'num',
    cell: (row) => num(row.itemCount || row.totalQty) },
];

function renderShipTable() {
  const wrap = nodes.shipWrap;
  if (!wrap) return;
  wrap.replaceChildren();
  if (!state.ships.connected) {
    wrap.append(emptyState({
      title: 'Gönderi listesi alınamadı',
      text: state.ships.error || 'Mağazaya ulaşılamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshShipments() })],
    }));
    return;
  }
  if (state.ships.localFilters.length) {
    wrap.append(alertBox(
      'Arama YALNIZ BU SAYFADA uygulandı: mağaza gönderi listesinde arama sorgusunu '
      + `kabul etmiyor. Bu sayfadan ${num(state.ships.hiddenByLocal)} satır elendi; `
      + 'tarih aralığı mağaza tarafında süzülüyor, onu daraltın.', 'warn'));
  }
  nodes.shipTable = dataTable({
    columns: SHIP_COLUMNS,
    rows: state.ships.items,
    selectable: true,
    empty: emptyState({ title: 'Bu aralıkta gönderi yok',
      text: 'Tarih aralığını genişletin.' }),
    onSelect: (ids) => { state.shipSelection = ids; renderShipBar(); },
  });
  wrap.append(nodes.shipTable.node);
  renderShipBar();
}

function renderShipBar() {
  const bar = nodes.shipBar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.shipSelection.length;
  if (!count) {
    bar.classList.remove('on');
    return;
  }
  bar.classList.add('on');
  bar.append(h('b', undefined, `${num(count)} gönderi seçildi`));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Sevk dökümü', {
      variant: 'primary',
      title: 'Seçili gönderiler için taslak sevk dökümü üretir',
      onClick: () => report.run('delivery', { shipmentIds: state.shipSelection.map(Number) }),
    }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => {
        nodes.shipTable.clearSelection();
        state.shipSelection = [];
        renderShipBar();
      },
    }),
  );
}

// ----------------------------------------------------- seriler & numaralar

function renderSeries() {
  const host = nodes.seriesWrap;
  if (!host) return;
  host.replaceChildren();

  const warnings = [];
  for (const item of state.series.items) {
    if (item.gapMessage) warnings.push(item.gapMessage);
  }
  for (const item of state.series.orphans) {
    warnings.push(`${item.gapMessage || ''} (${item.code} serisi tanımlı değil, `
      + `${num(item.usedCount)} kayıt var)`.trim());
  }
  if (warnings.length) {
    const box = h('div', 'iv-gaps');
    box.append(h('b', undefined, 'Numara boşluğu'));
    for (const line of warnings) box.append(h('div', 'iv-gap-line', line));
    box.append(h('div', 'iv-sub',
      'Atlanan numara ya iptal edilmiş bir faturadır ya da kaydı hiç girilmemiştir. '
      + 'Denetimde ilk sorulan budur.'));
    host.append(box);
  } else {
    host.append(hintBox('Tanımlı serilerde numara boşluğu yok.'));
  }

  const table = dataTable({
    columns: [
      { key: 'code', label: 'Seri', width: '110px', className: 'mono' },
      { key: 'label', label: 'Açıklama', width: 'minmax(0, 2fr)' },
      { key: 'usedCount', label: 'Kayıt', width: '84px', align: 'num',
        cell: (row) => num(row.usedCount) },
      { key: 'lastNumber', label: 'Son no', width: '104px', align: 'num',
        cell: (row) => (row.lastNumber ? num(row.lastNumber) : '—') },
      { key: 'nextNumber', label: 'Sıradaki', width: '104px', align: 'num',
        cell: (row) => num(row.nextNumber) },
      { key: 'gaps', label: 'Boşluk', width: '96px', align: 'num',
        cell: (row) => (row.gaps.length ? badge(`${row.gaps.length} aralık`, 'bad')
          : badge('yok', 'good')) },
      { key: 'isDefault', label: 'Varsayılan', width: '104px',
        cell: (row) => (row.isDefault ? badge('varsayılan', 'info') : '—') },
    ],
    rows: state.series.items,
    empty: emptyState({
      title: 'Seri tanımlı değil',
      text: 'Yasal fatura numarası eşlemek için önce bir seri tanımlayın (ör. A2026).',
      actions: [button('Seri ekle', { variant: 'primary', onClick: () => seriesDialog() })],
    }),
    onRow: (row) => seriesDialog(row),
  });

  const actions = h('div', 'iv-actions');
  actions.append(
    button('Seri ekle', { variant: 'primary', onClick: () => seriesDialog() }),
    button('Yenile', { onClick: () => refreshSeries() }),
  );

  host.append(
    card('Seriler ve numaralandırma', table.node,
      `${num(state.series.matchedCount)} fatura eşlenmiş`),
    actions,
    hintBox('Seri ve sıra numarası Bagisto’da YOKTUR; bu tablo Kontrol Merkezi’nin kendi '
      + 'kaydıdır. Sıradaki numara yalnızca ÖNERİDİR — gerçek numara yasal belgeyi kesen '
      + 'dış sistemden gelir ve elle yazılabilir.'),
  );
}

function seriesDialog(existing = null) {
  const form = formGrid({
    fields: [
      { key: 'code', label: 'Seri kodu', type: 'text', required: true, maxLength: 16,
        readOnly: Boolean(existing), placeholder: 'A2026',
        hint: 'Kod sonradan değiştirilemez: geçmiş eşlemeler bu koda bağlıdır.' },
      { key: 'label', label: 'Açıklama', type: 'text', maxLength: 64, wide: true },
      { key: 'startNo', label: 'Başlangıç no', type: 'number' },
      { key: 'pad', label: 'Basamak', type: 'number',
        hint: 'Numaranın kaç haneye tamamlanacağı (GİB biçiminde 9).' },
      { key: 'yearReset', label: 'Yıl başında sayaç sıfırlansın', type: 'checkbox' },
      { key: 'isDefault', label: 'Varsayılan seri', type: 'checkbox' },
      { key: 'note', label: 'Not', type: 'textarea', maxLength: 255, wide: true },
    ],
    value: existing
      ? { code: existing.code, label: existing.label, startNo: existing.startNo,
        pad: existing.pad, yearReset: existing.yearReset, isDefault: existing.isDefault,
        note: existing.note }
      : { code: state.series.defaultSeries || '', label: '', startNo: 1, pad: 9,
        yearReset: true, isDefault: state.series.items.length === 0, note: '' },
  });
  const open = [trackForm(form)];

  const box = drawer(nodes.root, {
    title: existing ? `Seri: ${existing.code}` : 'Yeni seri',
    subtitle: 'Numaralandırma yereldir; mağazaya yazılmaz.',
    onClose: () => releaseForms(open),
  });
  box.body.append(form.node);

  const save = button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); return; }
      const draft = form.draft();
      const reason = await askReason({
        title: 'Seriyi kaydet',
        description: 'Seri tanımı numara boşluğu denetimini besler; gerekçe denetim kaydına '
          + 'yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Seri kaydediliyor…', async () => {
        await call(`${BASE}/series`, {
          method: 'POST',
          body: {
            code: String(draft.code || '').toUpperCase(),
            label: draft.label || '',
            startNo: Number(draft.startNo) || 1,
            pad: Number(draft.pad) || 9,
            yearReset: Boolean(draft.yearReset),
            isDefault: Boolean(draft.isDefault),
            note: draft.note || '',
            reason,
          },
        });
        toast('Seri kaydedildi.', 'good');
        box.close();
        await refreshSeries();
      });
    },
  });
  const actions = h('div', 'iv-actions');
  actions.append(save);
  box.body.append(actions);
}

// ------------------------------------------------------------- fatura çekmecesi

async function openInvoice(row) {
  const open = [];
  const box = drawer(nodes.root, {
    title: `Fatura ${row.number}`,
    subtitle: `${row.customer} · ${money(row.total)}`,
    onClose: () => releaseForms(open),
  });
  box.body.append(skeletonRows(6, 4));

  let payload;
  try {
    payload = await api(`${BASE}/invoices/${row.id}`);
  } catch (error) {
    box.body.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  if (!payload.ok) {
    box.body.replaceChildren(alertBox(payload.error || 'Fatura okunamadı.', 'bad'));
    return;
  }

  const invoice = payload.invoice;
  box.body.replaceChildren();
  box.body.append(legalBanner());

  // --- künye
  const head = h('div', 'iv-grid');
  const line = (label, value) => {
    const cell = h('div', 'iv-cell');
    cell.append(h('span', 'iv-sub', label), h('b', undefined, value || '—'));
    return cell;
  };
  head.append(
    line('Sipariş', invoice.orderNumber),
    line('Tarih', invoice.createdAt),
    line('Müşteri', invoice.customer),
    line('VKN/TCKN', invoice.taxId),
    line('Tip', invoice.partyKindLabel),
    line('Durum', invoice.stateLabel),
    line('Matrah', money(invoice.net)),
    line('KDV', money(invoice.tax)),
    line('Toplam', money(invoice.total)),
  );
  box.body.append(card('Künye', head));

  // --- kalemler
  const items = dataTable({
    columns: [
      { key: 'name', label: 'Kalem', width: 'minmax(0, 2.4fr)',
        cell: (item) => clip(h('span'), item.name, 44) },
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
      { key: 'qty', label: 'Adet', width: '72px', align: 'num' },
      // Oran mağazadan gelmiyorsa tutardan türetilir; rozet bunu SÖYLER —
      // renk tek başına anlam taşımaz, yanında yazı var.
      { key: 'rate', label: 'KDV oranı', width: '132px', align: 'num',
        cell: (item) => {
          if (item.rate === null) return badge('ayrıştırılamadı', 'warn');
          const box = h('span', 'iv-legalno');
          box.append(h('b', undefined, `%${item.rate}`));
          if (item.rateDerived) box.append(badge('türetildi', 'warn'));
          return box;
        } },
      { key: 'net', label: 'Matrah', width: '110px', align: 'num',
        cell: (item) => money(item.net) },
      { key: 'tax', label: 'KDV tutarı', width: '110px', align: 'num',
        cell: (item) => money(item.tax) },
    ],
    rows: payload.items || [],
    dense: true,
    empty: hintBox('Kalem bilgisi gelmedi; oran kırılımı bu fatura için '
      + '“Ayrıştırılamadı” satırına düşer.'),
  });
  box.body.append(card('Kalemler', items.node));

  // --- yasal numara eşleme (ekranın asıl işi)
  const seriesOptions = state.series.items.map((item) => ({ value: item.code,
    label: `${item.code} — sıradaki ${item.nextNumber}` }));
  if (!seriesOptions.length) {
    box.body.append(card('Yasal fatura no', alertBox(
      'Seri tanımlı değil. “Seriler & numaralandırma” sekmesinden bir seri ekleyin; '
      + 'numara boşluğu denetimi seriye göre çalışır.', 'warn')));
  } else {
    const suggested = state.series.items.find((item) => item.code === invoice.legalSeries)
      || state.series.items.find((item) => item.isDefault)
      || state.series.items[0];
    const legalForm = formGrid({
      fields: [
        { key: 'series', label: 'Seri', type: 'select', required: true,
          options: seriesOptions },
        { key: 'number', label: 'Sıra no', type: 'number', required: true,
          hint: 'Dış sistemde kesilen belgenin sıra numarası.' },
        { key: 'legalNo', label: 'Yasal fatura no', type: 'text', maxLength: 32, wide: true,
          hint: 'Boş bırakılırsa seri + sıra numarasından üretilir. Farklı biçimdeyse '
            + 'olduğu gibi yazın.' },
        { key: 'issuedAt', label: 'Düzenlenme günü', type: 'date' },
        { key: 'note', label: 'Not', type: 'textarea', maxLength: 255, wide: true },
      ],
      value: {
        series: invoice.legalSeries || suggested.code,
        number: invoice.legalNumber || suggested.nextNumber,
        legalNo: invoice.legalNo || '',
        issuedAt: invoice.legalDate || todayIso(),
        note: '',
      },
    });
    open.push(trackForm(legalForm));

    const saveLegal = button(invoice.legalMatched ? 'Eşlemeyi güncelle' : 'Eşle', {
      variant: 'primary',
      onClick: async () => {
        if (!legalForm.valid()) { legalForm.showErrors(); return; }
        const draft = legalForm.draft();
        const reason = await askReason({
          title: 'Yasal fatura numarasını eşle',
          description: 'Bu işlem MAĞAZAYA YAZMAZ; Kontrol Merkezi’nin eşleme kaydını '
            + 'günceller. Denetimde “kim, neden değiştirdi” sorusunun cevabı gerekçedir.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        await withBusy('Eşleme kaydediliyor…', async () => {
          const result = await call(`${BASE}/invoices/${invoice.id}/legal-no`, {
            method: 'POST',
            body: {
              series: draft.series,
              number: Number(draft.number) || 0,
              legalNo: draft.legalNo || '',
              issuedAt: draft.issuedAt || '',
              note: draft.note || '',
              reason,
            },
          });
          toast(`Eşlendi: ${result.legalNo}`, 'good');
          box.close();
          await Promise.all([refresh(), refreshSeries()]);
        });
      },
    });
    const legalActions = h('div', 'iv-actions');
    legalActions.append(saveLegal);
    const legalBox = h('div');
    legalBox.append(legalForm.node, legalActions);
    box.body.append(card('Yasal fatura no', legalBox,
      invoice.legalMatched ? `Eşli: ${invoice.legalNo}` : 'Eşlenmedi'));
  }

  // --- oran kırılımı
  if ((payload.rates || []).length) {
    const rates = dataTable({
      columns: [
        { key: 'rateLabel', label: 'Oran', width: 'minmax(0, 1fr)' },
        { key: 'net', label: 'Matrah', width: '120px', align: 'num',
          cell: (item) => money(item.net) },
        { key: 'tax', label: 'KDV', width: '120px', align: 'num',
          cell: (item) => money(item.tax) },
        { key: 'total', label: 'Toplam', width: '120px', align: 'num',
          cell: (item) => money(item.total) },
      ],
      rows: payload.rates,
      dense: true,
    });
    const rateBox = h('div');
    rateBox.append(rates.node);
    if ((payload.rates || []).some((item) => item.derived)) {
      rateBox.append(hintBox(
        'Mağaza fatura kalemlerinde KDV ORANI ALANI VERMİYOR; buradaki oran, KDV '
        + 'tutarının matraha bölümünden hesaplandı. Beyan öncesi doğrulayın.'));
    }
    box.body.append(card('Oran kırılımı', rateBox));
  }

  // --- eylemler
  const actions = h('div', 'iv-actions');
  actions.append(
    button('PDF görüntüle', {
      title: 'Mağazadan indirilir, rapor klasörüne yazılır ve önizlenir',
      onClick: () => report.run('single', { invoiceId: invoice.id }),
    }),
    button('Müşteriye kopya gönder', {
      onClick: async () => {
        const reason = await askReason({
          title: 'Fatura kopyasını e-postala',
          description: `${invoice.email || 'Müşteri'} adresine mağaza üzerinden e-posta `
            + 'gider. Dışarıya çıkan bir iştir; gerekçe denetim kaydına yazılır.',
          confirmLabel: 'Gönder',
        });
        if (!reason) return;
        await withBusy('E-posta gönderiliyor…', async () => {
          await call(`${BASE}/invoices/${invoice.id}/send`, {
            method: 'POST', body: { reason, dryRun: false },
          });
          toast('Kopya gönderildi.', 'good');
        });
      },
    }),
  );
  box.body.append(actions);

  // --- yerel geçmiş
  if ((payload.history || []).length) {
    const history = dataTable({
      columns: [
        { key: 'createdAt', label: 'Zaman', width: '160px' },
        { key: 'action', label: 'İşlem', width: 'minmax(0, 1fr)' },
        { key: 'actor', label: 'Kullanıcı', width: 'minmax(0, 1fr)' },
        { key: 'result', label: 'Sonuç', width: '92px' },
        { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)',
          cell: (item) => clip(h('span'), item.reason, 60) },
      ],
      rows: payload.history,
      dense: true,
    });
    box.body.append(card('Bu ekrandan yapılanlar', history.node, 'Yerel denetim izi'));
  }
}

// ------------------------------------------------------------ toplu işlemler

async function stateDialog() {
  const form = formGrid({
    fields: [{
      key: 'state', label: 'Yeni durum', type: 'select', required: true,
      options: WRITABLE_STATES,
      hint: 'Yalnız bu üç durum mağazaya yazılabilir; diğerleri Bagisto’da 422 üretir.',
    }],
    value: { state: 'paid' },
  });
  const open = [trackForm(form)];

  const box = drawer(nodes.root, {
    title: 'Toplu durum değişikliği',
    subtitle: `${num(state.selection.length)} fatura`,
    onClose: () => releaseForms(open),
  });
  box.body.append(
    alertBox('Fatura SİLİNMEZ. Durum değişir, kayıt yerinde kalır.', 'info'),
    form.node,
  );
  const actions = h('div', 'iv-actions');
  actions.append(button('Uygula', {
    variant: 'danger',
    onClick: async () => {
      const wanted = form.draft().state;
      const reason = await askReason({
        title: 'Fatura durumunu değiştir',
        description: `${num(state.selection.length)} faturanın durumu mağazada `
          + 'değiştirilecek. Muhasebe kayıtlarını etkiler.',
        confirmLabel: 'Uygula',
      });
      if (!reason) return;
      await withBusy('Durum yazılıyor…', async () => {
        const result = await call(`${BASE}/invoices/state`, {
          method: 'POST',
          body: {
            invoiceIds: state.selection.map(Number),
            state: wanted,
            reason,
            dryRun: false,
          },
        });
        toast(`${num(result.count)} fatura güncellendi.`, 'good');
        box.close();
        await refresh();
      });
    },
  }));
  box.body.append(actions);
}

async function issueDialog() {
  const box = drawer(nodes.root, {
    title: 'Toplu fatura kes',
    subtitle: 'Faturası olmayan siparişler',
  });
  box.body.append(
    alertBox('Kesilen fatura SİLİNEMEZ; iptali iade faturasıdır (İadeler ekranı). '
      + 'Kesilen belge yasal fatura değildir — numarasını ayrıca eşleyin.', 'warn'),
    skeletonRows(6, 4),
  );

  let payload;
  try {
    payload = await api(`${BASE}/uninvoiced?size=100`);
  } catch (error) {
    box.body.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  box.body.replaceChildren();
  box.body.append(alertBox('Kesilen fatura SİLİNEMEZ; iptali iade faturasıdır.', 'warn'));

  if (!payload.connected) {
    box.body.append(alertBox(payload.error || 'Sipariş listesi alınamadı.', 'bad'));
    return;
  }

  // Çapraz denetim yapılamadıysa liste GİZLENMEZ ama açıkça damgalanır:
  // "faturasız" görünen sipariş aslında faturalı olabilir.
  if (!payload.verified) {
    box.body.append(alertBox(
      'Bu liste DOĞRULANAMADI: faturası kesilmiş siparişleri elemek için fatura listesi '
      + `okunmak zorunda ve okunamadı (${payload.verifyError || 'mağazaya ulaşılamadı'}). `
      + 'Aşağıdaki siparişlerin bir kısmının faturası zaten kesilmiş olabilir. Kesmeden '
      + 'önce kuru prova yapın; kesilen fatura silinemez.', 'bad'));
  }

  const table = dataTable({
    columns: [
      { key: 'number', label: 'Sipariş', width: 'minmax(0, 1.2fr)', className: 'mono' },
      { key: 'createdAt', label: 'Tarih', width: '124px' },
      { key: 'customer', label: 'Müşteri', width: 'minmax(0, 2fr)',
        cell: (row) => clip(h('span'), row.customer, 36) },
      { key: 'status', label: 'Durum', width: '110px' },
      { key: 'total', label: 'Tutar', width: '120px', align: 'num',
        cell: (row) => money(row.total) },
    ],
    rows: payload.items || [],
    selectable: true,
    empty: emptyState({ title: 'Faturasız sipariş yok',
      text: `${num(payload.scanned)} sipariş tarandı; hepsinin faturası kesilmiş `
        + 'ya da durumu faturalanmaya uygun değil.' }),
  });
  box.body.append(table.node);
  box.body.append(hintBox(
    `Mağaza “faturası yok” süzgeci tanımıyor ve sipariş listesi fatura bilgisini hiç `
    + `taşımıyor: ${num(payload.scanned)} sipariş tarandı, fatura listesiyle çapraz `
    + `denetlendi ve faturası olan ${num(payload.invoicedSkipped || 0)} sipariş elendi. `
    + 'İptal/kapalı siparişler de gösterilmez. Daha eski siparişler için tarih '
    + 'aralığını daraltın.'));

  const actions = h('div', 'iv-actions');
  actions.append(
    button('Kuru prova', {
      title: 'Mağazaya yazmadan dener; her siparişin sonucu listelenir',
      onClick: () => runIssue(table, box, true),
    }),
    button('Fatura kes', { variant: 'danger', onClick: () => runIssue(table, box, false) }),
  );
  box.body.append(actions);
}

async function runIssue(table, box, dryRun) {
  const ids = table.selection().map(Number);
  if (!ids.length) { toast('Sipariş seçilmedi.', 'warn'); return; }
  const reason = await askReason({
    title: dryRun ? 'Kuru prova' : 'Fatura kes',
    description: `${num(ids.length)} siparişe fatura kesilecek. `
      + (dryRun ? 'Kuru provada mağazaya yazılmaz.' : 'GERİ ALINAMAZ.'),
    confirmLabel: dryRun ? 'Prova et' : 'Kes',
  });
  if (!reason) return;
  await withBusy(dryRun ? 'Prova ediliyor…' : 'Fatura kesiliyor…', async () => {
    const result = await api(`${BASE}/invoices/issue`, {
      method: 'POST', body: { orderIds: ids, reason, dryRun },
    });
    const failed = (result.results || []).filter((item) => !item.ok);
    toast(`${num(result.created)} fatura ${dryRun ? 'provada geçti' : 'kesildi'}`
      + (result.failed ? `, ${num(result.failed)} başarısız.` : '.'),
    result.failed ? 'warn' : 'good');
    if (failed.length) {
      const list = h('div', 'iv-fails');
      list.append(h('b', undefined, 'Kesilemeyenler'));
      for (const item of failed.slice(0, 20)) {
        list.append(h('div', 'iv-gap-line', `Sipariş #${item.orderId}: ${item.error}`));
      }
      box.body.append(list);
    }
    if (!dryRun) await refresh();
  });
}

// -------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['Fatura no', 'Yasal fatura no', 'Tarih', 'Sipariş', 'Müşteri', 'VKN/TCKN',
    'Matrah', 'KDV', 'Toplam', 'Durum'];
  const rows = state.items.map((row) => [
    row.number, row.legalNo || '', row.createdAt, row.orderNumber, row.customer,
    row.taxId || '', money(row.net), money(row.tax), money(row.total), row.stateLabel,
  ]);
  const written = csvBlob(headers, rows, `faturalar-sayfa-${state.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  const values = filterValues();
  const ok = await confirmSimple(nodes.root, {
    title: 'Muhasebe CSV’si',
    description: `${values.start || 'başlangıçtan'} – ${values.end || 'bugüne'} aralığındaki `
      + 'faturalar sayfa sayfa çekilir ve yasal numara sütunuyla rapor klasörüne yazılır.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('CSV üretiliyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { start: values.start, end: values.end, state: values.state },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.lastFile.set(result.path);
    if (result.truncated) toast('Liste tavana dayandı; aralığı daraltın.', 'warn');
  });
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  // Sipariş/iade ekranı `open('store_invoices', {orderId: 12})` ile gelir.
  // Sipariş süzgeci MAĞAZA TARAFINDA uygulanıyor (canlıda denendi), yerel
  // eleme gerekmez.
  state.orderId = Number(ctx.payload?.orderId) || 0;

  const view = h('div', 'kit-panel iv');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar([
    { key: 'invoices', label: 'Faturalar' },
    { key: 'shipments', label: 'İrsaliyeler' },
    { key: 'series', label: 'Seriler & numaralandırma' },
  ], 'invoices', (key) => showTab(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Fatura no, sipariş no, müşteri', width: '260px' },
      { kind: 'dateRange', key: 'range', label: 'Tarih', start: todayIso(-29), end: todayIso() },
      { kind: 'select', key: 'state', label: 'Durum', options: STATE_OPTIONS },
      { kind: 'numRange', key: 'total', label: 'Tutar', money: true },
      { kind: 'toggle', key: 'unmatched', label: 'Yasal no girilmemiş' },
    ],
    onChange: () => {
      if (state.tab === 'shipments') refreshShipments({ page: 1 });
      else refresh({ page: 1 });
    },
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('Toplu fatura kes', { onClick: () => issueDialog() }),
      button('Dönem icmali', {
        variant: 'primary',
        title: 'Mali müşavir için oran kırılımlı PDF',
        onClick: () => {
          const values = filterValues();
          report.run('period', { start: values.start, end: values.end, state: values.state });
        },
      }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Muhasebe CSV', { title: 'Aralığın tamamını rapor klasörüne yaz',
        onClick: exportAll }),
    ],
  });

  nodes.status = statusLine();
  nodes.kpi = h('div', 'iv-kpi');
  nodes.selbar = h('div', 'iv-selbar');
  nodes.tableWrap = h('div', 'iv-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size }),
  });
  nodes.lastFile = report.lastFileLine();

  nodes.shipBar = h('div', 'iv-selbar');
  nodes.shipWrap = h('div', 'iv-table');
  nodes.shipPager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refreshShipments({ page, size }),
  });
  nodes.seriesWrap = h('div', 'iv-series');

  // Tek gövde kabı; sekme değişince İÇERİĞİ değişir. İki ayrı kap tutup
  // değiştirmek kaydırma konumunu kaybettiriyor.
  nodes.body = h('div', 'iv-body');
  const invoiceView = [nodes.kpi, nodes.selbar, nodes.tableWrap, nodes.pager.node,
    nodes.lastFile.node];
  const shipView = [
    hintBox('İrsaliye kaydı Bagisto’nun gönderi izidir; yasal sevk irsaliyesi değildir. '
      + 'Üretilen döküm depo/kargo çıkışı içindir.'),
    nodes.shipBar, nodes.shipWrap, nodes.shipPager.node,
  ];
  nodes.body.append(...invoiceView);

  view.append(legalBanner(), tabs.node, nodes.filters.node, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    const listing = key !== 'series';
    nodes.filters.node.hidden = !listing;
    nodes.status.node.hidden = key !== 'invoices';
    if (key === 'invoices') {
      nodes.body.replaceChildren(...invoiceView);
      if (!state.loaded.invoices) refresh();
    } else if (key === 'shipments') {
      nodes.body.replaceChildren(...shipView);
      if (!state.loaded.shipments) refreshShipments();
    } else {
      nodes.body.replaceChildren(nodes.seriesWrap);
      renderSeries();
      if (!state.loaded.series) refreshSeries();
    }
  }

  root.replaceChildren(view);
  nodes.status.set('Faturalar alınıyor…');
  // Seriler ÖNCE gelir: çekmecedeki eşleme formu seri listesine dayanıyor ve
  // liste boşken kullanıcıya "seri tanımlı değil" demek gerekiyor.
  refreshSeries().then(() => refresh());

  return () => {
    nodes.filters?.destroy();          // tarih alanları global dinleyici tutar
    forms.forEach((form) => { try { form.destroy(); } catch { /* kapanışta yutulur */ } });
    forms.length = 0;
    root.replaceChildren();
    state = freshState();
    busy = false;
  };
}
