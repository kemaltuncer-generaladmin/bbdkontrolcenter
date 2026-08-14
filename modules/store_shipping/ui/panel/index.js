// Kargo paneli — kargoya hazır siparişten teslim edilmiş gönderiye kadar tek ekran.
//
// NE YAPAR: altı sekme. `Kargoya hazır` ödemesi alınmış ama kargolanmamış
// siparişleri toplu seçtirir ve GÖNDERİ SİHİRBAZINI açar (taşıyıcı · desi
// otomatik+elle · paket · ödeme tipi · kapıda ödeme) → taslak → taşıyıcı
// teklifleri → ETİKET SATIN AL. `Gönderiler` takip, hareket geçmişi, toplu
// etiket PDF'i (A4 4'lü / termal 100×150), teslimat manifestosu ve senkron.
// `Taşıyıcılar` maskeli kimlikler ve bağlantı sınaması. `Ücretlendirme`
// desi→ücret matrisi ve ücretsiz kargo eşiği. `Bölgeler` il/ilçe eşlemesi.
// `Performans` teslim süresi, teslim edilemeyen oranı ve kargo kâr/zararı.
//
// «KARGOYA VER» — TEK TIK, ARA ONAY YOK. Kullanıcının kararı: "sipariş seçince
// 'kargoya ver' dedik mi o sipariş yola çıkacak zaten. PARA HARCASIN."
// Düğme satırda durur; bir tıkla gönderi açılır, teklif alınır, MÜŞTERİNİN
// ödediği firmadan etiket SATIN ALINIR, takip numarası siparişe yazılır ve iki
// belge (KARGO ETİKETİ + FATURA) varsayılan yazıcıya gider. Onay penceresi
// AÇILMAZ; gerekçe alanı listenin üstünde durur, boş bırakılabilir ve akışı
// durdurmaz. Sihirbaz (adım adım, ölçü düzeltmeli) DURUYOR: ölçüsü şüpheli
// gönderi oradan geçirilir.
//
// NE YAPMAZ:
//  · ETİKET ÇİZMEZ. Barkod taşıyıcının kendi numaralandırmasıdır; kendi
//    çizdiğimiz barkod şubede okunmazsa gönderi elde kalır. Etiketin kendisi
//    her zaman taşıyıcıdan gelir, biz yalnız kâğıda yerleştiririz.
//  · TAKİP NUMARASI SORMAZ. Elle girilen numara zincirin çıktısı değil,
//    kullanıcının tahminidir; ekranda görünen numara siparişe YAZILMIŞ olandır.
//  · SİHİRBAZDA TASLAK AÇARKEN PARA HARCAMAZ. "Gönderi oluştur" ile "Etiket
//    satın al" orada bilerek iki ayrı adımdır: ilki ücretsiz ve düzeltilebilir.
//  · TESLİM FİŞİNİ OTOMATİK BASMAZ. Kullanıcı "fiş yok" dedi; düğmesi
//    sihirbazda duruyor ve elle basılıyor.
//  · GÖNDERİ SİLMEZ. İptal ve iade vardır; ikisi de gerekçe ister.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Desi ≠ ağırlık. Faturalanan, ikisinin büyüğünün tavana yuvarlanmışıdır;
//    ekran hem ham ölçüyü hem faturalanacak desiyi gösterir.
//  · Ücret dökümü TAHMİNDİR; kesin tutar taşıyıcı teklifinden gelir. İkisi
//    ayrı gösterilir, tahmin gerçek fiyat gibi sunulmaz.
//  · `Geciken` / `Adres sorunu` / `Tahsil edilmemiş` süzgeçleri SAYFA İÇİNDE
//    çalışır (taşıyıcıda böyle bir alan yok); ekran bunu yazar.
//  · Bölge eşlemesi YEREL: ücret dökümünü ve uyarıyı etkiler, vitrini değil.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_shipping/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_shipping/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, copyText, csvBlob, h, loadStyles,
  money, num, percent, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { barChart, groupedBar } from '../../ui-kit/charts.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_shipping';

const STATUS_LABELS = {
  created: 'Etiket oluşturuldu',
  picked_up: 'Teslim alındı',
  in_transit: 'Yolda',
  at_branch: 'Şubede',
  out_for_delivery: 'Dağıtımda',
  delivered: 'Teslim edildi',
  undelivered: 'Teslim edilemedi',
  returning: 'İade dönüyor',
  returned: 'İade teslim',
  cancelled: 'İptal',
};

const STATUS_TONES = {
  created: 'info', picked_up: 'info', in_transit: 'info', at_branch: 'info',
  out_for_delivery: 'warn', delivered: 'good', undelivered: 'bad',
  returning: 'warn', returned: 'dim', cancelled: 'dim',
};

const FLAG_LABELS = {
  late: 'Geciken', address: 'Adres sorunu', cod: 'Tahsil edilmemiş',
  unknown: 'Bilinmeyen durum',
};

const FLAG_CHIPS = [
  { key: 'late', label: 'Geciken' },
  { key: 'address', label: 'Adres sorunu' },
  { key: 'cod', label: 'Kapıda ödeme tahsil edilmemiş' },
];

const PAYERS = [
  { value: 'sender', label: 'Gönderici öder' },
  { value: 'receiver', label: 'Alıcı öder (kapıda)' },
];

const TABS = [
  { key: 'ready', label: 'Kargoya hazır' },
  { key: 'shipments', label: 'Gönderiler' },
  { key: 'carriers', label: 'Taşıyıcılar' },
  { key: 'rates', label: 'Ücretlendirme' },
  { key: 'zones', label: 'Bölgeler' },
  { key: 'performance', label: 'Performans' },
  // KURULUM EN SONDA. Sekme sırası kullanım sıklığına göredir: burası günde
  // bir kez değil, ayda bir kez açılır. Öne almak, her gün kargo çıkaran
  // personeli canlı mod anahtarının yanından geçirirdi.
  { key: 'geliver', label: 'Geliver kurulumu' },
];

/** Sınama satırlarının tonu. `unknown` yeşile DÖNMEZ (bkz. `check_lines`). */
const CHECK_TONES = { ok: 'good', fail: 'bad', unknown: 'dim' };
const CHECK_MARKS = { ok: '✓', fail: '✕', unknown: '—' };

/**
 * Başlangıç durumu FONKSİYONDUR, sabit değil.
 *
 * Tek bir sabit nesneyi `{...EMPTY}` ile kopyalamak İÇ nesneleri paylaştırır:
 * `state.shipments.flag = 'late'` sabitin kendisini değiştirir ve panel
 * kapanıp yeniden açıldığında süzgeç üzerinde kalırdı.
 */
function freshState() {
  return {
    tab: 'ready',
    ready: { items: [], blocked: [], total: 0, shown: 0, page: 1, size: 50, pages: 0,
      connected: false, error: '', loaded: false },
    shipments: { items: [], total: 0, shown: 0, page: 1, size: 50, pages: 0, connected: false,
      error: '', totals: {}, flag: '', idleLimit: 3, loaded: false },
    carriers: [],
    rates: null,
    zones: [],
    settings: null,
    geliver: null,
    selection: [],
    readySelection: [],
  };
}

let api = null;
let toast = null;
let report = null;
let openPanel = null;
let busy = false;
let state = freshState();

const nodes = {};
const closers = [];        // cleanup'ta çağrılacak GERÇEK kaynak bırakıcılar

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

/** Yıkıcı işlemin arayüz kapısı; asıl kapı backend'de (K9). */
function askReason({ title, description, confirmLabel = 'Onayla', minLength = 10 }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength,
    placeholder: `Gerekçe (en az ${minLength} karakter) — denetim kaydına yazılır`,
  });
}

/** PARA HARCAYAN işlemde gerekçe 20 karakter; backend de 20 istiyor. */
function askPurchaseReason(options) {
  return askReason({ ...options, minLength: 20 });
}

/** Takip numarası — tıklanınca panoya kopyalanır (depoda en çok yapılan iş). */
function track(row) {
  const box = h('span', 'sh-track');
  if (!row.trackingNo) {
    box.append(h('i', 'sh-sub', 'etiket yok'));
    return box;
  }
  const code = h('code', 'sh-code', row.trackingNo);
  code.title = 'Takip numarasını kopyalamak için tıklayın';
  code.addEventListener('click', async (event) => {
    // Satır tıklaması çekmeceyi açıyor; kopyalama onu tetiklememeli.
    event.stopPropagation();
    await copyText(row.trackingNo);
    toast('Takip numarası kopyalandı.', 'good');
  });
  box.append(code);
  return box;
}

function statusCell(row) {
  const box = h('span', 'sh-status');
  box.append(badge(STATUS_LABELS[row.status] || row.statusLabel,
    STATUS_TONES[row.status] || 'warn'));
  for (const flag of row.flags || []) {
    if (FLAG_LABELS[flag]) box.append(badge(FLAG_LABELS[flag], flag === 'cod' ? 'warn' : 'bad'));
  }
  return box;
}

function desiCell(row) {
  const box = h('span', 'sh-desi');
  // Ham ölçü ve FATURALANACAK desi ayrı gösterilir: taşıyıcı tavana yuvarlar.
  box.append(h('b', undefined, `${num(row.units)} desi`));
  box.append(h('span', 'sh-sub', `${num(row.desi, 1)} / ${num(row.weight, 1)} kg`));
  box.title = 'Üstte faturalanacak desi, altta ölçülen desi ve ağırlık.';
  return box;
}

// ==================================================================== veri

async function loadSettings() {
  try {
    state.settings = await api(`${BASE}/settings`);
  } catch {
    state.settings = null;         // ayar okunamadı; varsayılanlar iş görür (K7)
  }
}

async function loadReady({ page = state.ready.page, size = state.ready.size } = {}) {
  nodes.readyHost?.replaceChildren(skeletonRows(8, 7));
  const q = nodes.readyFilters ? (nodes.readyFilters.values().q || '') : '';
  const query = new URLSearchParams({ q, page: String(page), size: String(size) });
  let payload;
  try {
    payload = await api(`${BASE}/ready?${query}`);
  } catch (error) {
    // `blocked` de sıfırlanır: eski çağrının "bekleyen siparişler" kartı
    // bağlantı koptuktan sonra da ekranda kalırdı.
    state.ready = { ...state.ready, connected: false, error: error.message, items: [],
      blocked: [], total: 0, shown: 0, loaded: true };
    renderReady();
    return;
  }
  state.ready = { ...state.ready, ...payload, loaded: true };
  state.readySelection = [];
  renderReady();
}

function shipmentQuery({ page, size } = {}) {
  const values = nodes.shipFilters ? nodes.shipFilters.values() : {};
  const range = values.range || {};
  const desi = values.desi || {};
  const params = new URLSearchParams();
  const put = (key, value) => {
    if (value === null || value === undefined || value === '' || value === 0) return;
    params.set(key, String(value));
  };
  put('q', values.q);
  put('carrier', values.carrier);
  put('status', values.status);
  put('city', values.city);
  put('payer', values.payer);
  put('dateFrom', range.start);
  put('dateTo', range.end);
  put('dateField', values.dateField || 'created');
  put('desiMin', desi.min);
  put('desiMax', desi.max);
  put('flag', state.shipments.flag);
  put('page', page ?? state.shipments.page);
  put('size', size ?? state.shipments.size);
  return params.toString();
}

async function loadShipments({ page, size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set('Gönderiler alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/shipments?${shipmentQuery({ page, size })}`);
  } catch (error) {
    state.shipments = { ...state.shipments, connected: false, error: error.message,
      items: [], total: 0, loaded: true };
    renderShipTable();
    nodes.status?.set(shipStatusText(), true);
    return;
  }
  state.shipments = { ...state.shipments, ...payload, loaded: true };
  state.selection = [];
  renderShipTable();
  nodes.pager?.update({ total: payload.total || 0, page: payload.page || 1,
    size: payload.size || 50 });
  nodes.status?.set(shipStatusText(), !payload.connected);
}

function shipStatusText() {
  const data = state.shipments;
  // "Mağazaya ulaşılamadı" DENMEZ: en sık sebep mağazanın düşmesi değil,
  // BBD kargo ucunun henüz yayında olmaması. Geçidin metni olduğu gibi
  // gösterilir, üstüne yanlış bir teşhis yazılmaz.
  if (!data.connected) return `Gönderi listesi okunamadı — ${data.error}`;
  const filtered = data.flag
    ? ` · "${FLAG_LABELS[data.flag]}" işaretiyle sayfa içinde ${num(data.shown)} kayıt eşleşti`
    : '';
  return `Bağlı · ${num(data.total)} gönderi · sayfa ${data.page}/${Math.max(1, data.pages)}${filtered}`;
}

// ================================================================ sekme 1

function renderReady() {
  const host = nodes.readyHost;
  if (!host) return;
  host.replaceChildren();
  const data = state.ready;

  if (!data.connected) {
    host.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: data.error || 'Bağlantı kurulamadı; kargoya hazır siparişler okunamıyor.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadReady() })],
    }));
    return;
  }

  const bar = h('div', 'sh-selbar');
  nodes.readyBar = bar;

  const table = dataTable({
    columns: [
      { key: 'orderNumber', label: 'Sipariş', width: '110px', className: 'mono' },
      {
        key: 'customer',
        label: 'Müşteri',
        width: 'minmax(0, 1.6fr)',
        cell: (row) => {
          const box = h('span', 'sh-name');
          // AD BOŞ OLABİLİR: canlıda 18 siparişin 4'ünde müşteri adı yok.
          // `displayName` bu boşluğu e-postayla doldurur; "(isimsiz)" yazmak
          // kullanıcıya "bu sipariş bozuk" dedirtirdi.
          const yer = `${row.city} / ${row.district}`.replace(/^ \/ | \/ $/, '');
          box.append(clip(h('b'), row.displayName || row.customer || `Sipariş ${row.orderNumber}`, 34),
            h('span', 'sh-sub', yer || 'adres sipariş açılınca görünür'));
          return box;
        },
      },
      // KARGO FİRMASI: sipariş listesi ucu bunu HİÇ vermiyor, `bbd/orders`
      // veriyor ve servis tek istekle eşliyor. Sütun olmadan kullanıcı hangi
      // firmayla göndereceğini panelden göremiyordu.
      { key: 'carrierTitle', label: 'Kargo firması', width: '160px',
        cell: (row) => (row.carrierTitle
          ? clip(h('span'), row.carrierTitle, 22)
          : h('span', 'sh-sub', 'seçilmemiş')) },
      { key: 'total', label: 'Tutar', width: '110px', align: 'num',
        cell: (row) => money(row.total) },
      {
        key: 'measures',
        label: 'Otomatik desi',
        width: '150px',
        cell: (row) => {
          const box = h('span', 'sh-desi');
          box.append(h('b', undefined, `${num(row.measures.units)} desi`));
          // Eksik ölçü SESSİZ KALMAZ: yanlış desi doğrudan yanlış ücrettir.
          box.append(row.measures.complete
            ? h('span', 'sh-sub', `${num(row.measures.weight, 1)} kg`)
            : badge('ölçü eksik', 'warn'));
          return box;
        },
      },
      { key: 'pending', label: 'Kalan', width: '84px', align: 'num',
        cell: (row) => `${num(row.pending)} / ${num(row.ordered)}` },
      { key: 'waitingDays', label: 'Bekleyen gün', width: '110px', align: 'num',
        cell: (row) => (row.waitingDays === null ? '—' : num(row.waitingDays)) },
      { key: 'zone', label: 'Bölge', width: '120px',
        cell: (row) => (row.delivers ? (row.zone || '—') : badge('teslimat yok', 'bad')) },
      // TEK TIK. Sihirbazın üç adımı burada tek düğmeye iner: kullanıcı
      // "sipariş seçince 'kargoya ver' dedik mi o sipariş yola çıksın" dedi.
      { key: 'dispatch', label: 'Kargoya ver', width: '150px', cell: dispatchCell },
    ],
    rows: data.items,
    selectable: true,
    empty: emptyState({
      title: 'Kargo bekleyen sipariş yok',
      text: 'Ödemesi alınmış her sipariş kargolanmış görünüyor. Yeni sipariş geldiğinde '
        + 'burada listelenir.',
      actions: [button('Yenile', { onClick: () => loadReady() })],
    }),
    onSelect: (ids) => {
      state.readySelection = ids;
      renderReadyBar(table);
    },
  });
  nodes.readyTable = table;

  const notice = hintBox(
    `Mağaza "gönderisi olmayan sipariş" süzgeci sunmuyor: durum süzgeci sunucuda, `
    + `kalan adet kontrolü SAYFA İÇİNDE yapılıyor. Sunucu ${num(data.total)} sipariş döndürdü, `
    + `bunların ${num(data.shown)} tanesi gerçekten kargoya hazır.`);

  host.append(notice, reasonBar());
  // Sipariş LİSTESİ ucu faturalanan/kargolanan adedi vermiyor; o bilgi
  // olmadan hazırlık DOĞRULANMIŞ sayılmaz. Sessiz kalmak, kısmen kargolanmış
  // bir siparişi ikinci kez kargoya vermeye yol açardı.
  if (data.verified === false && data.items.length) {
    host.append(alertBox(
      'Faturalanan ve kargolanan adet sipariş listesinde gelmiyor; "Kalan" sütunu '
      + 'sipariş edilen adedi gösteriyor. Sihirbaz siparişi tek tek okuyup yeniden '
      + 'denetler — daha önce kısmen kargolanmış olabilir.', 'warn'));
  }
  host.append(bar, table.node);
  if (data.blocked && data.blocked.length) {
    host.append(card('Bekleyen siparişler',
      blockedList(data.blocked),
      `${num(data.blocked.length)} sipariş neden hazır değil`));
  }
  renderReadyBar(table);
}

function blockedList(rows) {
  const box = h('div', 'sh-list');
  for (const row of rows.slice(0, 30)) {
    const line = h('div', 'sh-list-row');
    line.append(h('code', 'sh-code', row.orderNumber),
      h('span', undefined, row.customer || '—'),
      h('span', 'kit-spacer'),
      badge(row.blocked, 'dim'));
    box.append(line);
  }
  return box;
}

function renderReadyBar(table) {
  const bar = nodes.readyBar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.readySelection.length;
  if (!count) { bar.classList.remove('on'); return; }
  bar.classList.add('on');
  bar.append(h('b', undefined, `${num(count)} sipariş seçildi`), h('span', 'kit-spacer'));
  bar.append(
    button('Gönderi sihirbazı', {
      variant: 'primary',
      title: count > 1 ? 'Seçilen siparişler tek tek geçilir' : '',
      onClick: () => openWizard(table.selectedRows()),
    }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => { table.clearSelection(); state.readySelection = []; renderReadyBar(table); },
    }),
  );
}

// ------------------------------------------------ KARGOYA VER (tek tık)

/**
 * Gerekçe alanı — listenin üstünde DURUR, akışı DURDURMAZ.
 *
 * Diğer para harcayan işlemlerde gerekçe bir onay penceresinde istenir ve
 * yazılmadan iş ilerlemez. Burada öyle değil: kullanıcı "kargoya ver dedik mi
 * o sipariş yola çıkacak" dedi ve araya bir pencere koymak tam olarak
 * istemediği şey. Alan boş bırakılırsa sunucu denetim defterine otomatik bir
 * metin yazar; defter boş kalmaz, kullanıcı da durdurulmaz.
 *
 * Alan HER ÇİZİMDE yeniden kurulur ama DEĞERİ korunur: liste yenilenince
 * kullanıcının yazdığı gerekçe silinseydi, ikinci siparişte kimse yeniden
 * yazmazdı.
 */
function reasonBar() {
  const bar = h('div', 'sh-reason');
  const input = h('input', 'kit-input');
  input.type = 'text';
  input.maxLength = 255;
  input.placeholder = 'Gerekçe — denetim kaydına yazılır, boş bırakılabilir';
  input.value = nodes.readyReason?.value || '';
  nodes.readyReason = input;
  bar.append(
    h('span', 'sh-sub', 'Gerekçe'),
    input,
    h('span', 'sh-sub', 'Boş bırakırsanız otomatik bir metin yazılır; '
      + '"Kargoya ver" onay sormadan yürür ve PARA HARCAR.'),
  );
  return bar;
}

function dispatchReason() {
  return (nodes.readyReason?.value || '').trim();
}

/** Satırdaki tek tık düğmesi. Tıklanınca kendini kilitler (çift istek olmasın). */
function dispatchCell(row) {
  const box = h('span', 'sh-dispatch');
  const go = button('🚚 Kargoya ver', {
    variant: 'danger',
    title: 'Gönderi açılır → teklif alınır → müşterinin firmasından ETİKET SATIN '
      + 'ALINIR → takip numarası siparişe yazılır → etiket ve fatura yazdırılır. '
      + 'PARA HARCAR ve onay sormaz.',
    onClick: () => dispatchOrder(row, go),
  });
  box.append(go);
  return box;
}

/**
 * "Kargoya ver" — tek istek, ara onay YOK.
 *
 * `extra` sihirbazdan gelen ölçü/taşıyıcı düzeltmelerini taşır; satırdan
 * tıklandığında boştur ve sunucu müşterinin ödediği firmayı kendisi bulur.
 */
async function dispatchOrder(row, trigger, extra = {}) {
  const label = trigger?.textContent;
  if (trigger) { trigger.disabled = true; trigger.textContent = 'Gönderiliyor…'; }
  const result = await withBusy(`${row.orderNumber} kargoya veriliyor…`, () => call(
    `${BASE}/orders/${row.orderId}/dispatch`, {
      method: 'POST',
      // dryRun AÇIKÇA false: sunucu tarafındaki varsayılan true ve
      // gönderilmezse gerçek istek sessizce kuru provaya düşerdi.
      body: { reason: dispatchReason(), dryRun: false, ...extra },
    }));
  if (trigger) { trigger.disabled = false; trigger.textContent = label; }
  if (!result) return null;

  const parts = [];
  if (result.trackingNo) parts.push(`takip no ${result.trackingNo}`);
  if (result.printed?.length) parts.push(`${num(result.printed.length)} belge yazdırıldı`);
  toast(result.dryRun
    ? 'Kuru prova: mağazaya gerçek istek gitmedi.'
    : `Kargoya verildi${parts.length ? ` · ${parts.join(' · ')}` : ''}`,
  result.dryRun ? 'warn' : 'good');
  for (const line of result.warnings || []) toast(line, 'warn');

  showDispatchResult(row, result);
  loadReady();
  return result;
}

/**
 * Sonuç çekmecesi. TAKİP NUMARASI EKRANDA GÖRÜNÜR ve tıklanınca kopyalanır —
 * kimse onu elle girmez, zincirin çıktısıdır ve siparişe yazılmıştır.
 */
function showDispatchResult(row, result) {
  const box = drawer(nodes.root, {
    title: result.dryRun ? 'Kuru prova sonucu' : 'Kargoya verildi',
    subtitle: `Sipariş ${row.orderNumber} · ${row.displayName || row.customer || ''}`.trim(),
  });

  const head = h('div', 'sh-dispatch-head');
  if (result.trackingNo) {
    const code = h('code', 'sh-code sh-track-big', result.trackingNo);
    code.title = 'Takip numarasını kopyalamak için tıklayın';
    code.addEventListener('click', async () => {
      await copyText(result.trackingNo);
      toast('Takip numarası kopyalandı.', 'good');
    });
    head.append(h('span', 'sh-sub', 'Takip numarası'), code);
  } else {
    head.append(alertBox(
      'Takip numarası yanıtta gelmedi. Gönderi açılmış olabilir; "Gönderiler" '
      + 'sekmesinden doğrulayın. Numara UYDURULMADI.', 'warn'));
  }
  head.append(
    badge(result.provider || 'taşıyıcı bildirilmedi', 'info'),
    badge(result.purchased ? 'Etiket satın alındı' : 'Etiket satın alınmadı',
      result.purchased ? 'good' : 'warn'),
  );
  if (result.dryRun) head.append(badge('KURU PROVA — para harcanmadı', 'warn'));
  box.body.append(head);

  if (result.labelReady === false) {
    box.body.append(alertBox(
      'Etiket PDF’i indirilemedi; kâğıda dökülemedi. Gönderi açıldıysa etiketi '
      + 'aşağıdaki düğmeyle yeniden alabilirsiniz.', 'warn'));
    if (result.shipmentId) {
      box.body.append(button('Etiketi yeniden al ve yazdır', {
        variant: 'primary',
        onClick: () => report.run('labels', {
          shipmentIds: [result.shipmentId],
          format: state.settings?.labelFormat || 'thermal-100x150',
        }),
      }));
    }
  }

  box.body.append(card('Basılan belgeler', documentList(result),
    result.autoPrint === false
      ? 'Otomatik basım kapalı — belgeler yalnız diske yazıldı'
      : 'Kargo etiketi ve fatura. Teslim fişi bu akışa girmez.'));
  if (result.printSkipped) box.body.append(alertBox(result.printSkipped, 'info'));
}

/** Belge satırları: basıldı mı, basılamadıysa dosya nerede, tekrar yazdır. */
function documentList(result) {
  const list = h('div', 'sh-list');
  const rows = result.documents || [];
  if (!rows.length) {
    list.append(h('div', 'sh-sub', 'Bu akışta belge üretilmedi.'));
    return list;
  }
  for (const doc of rows) {
    const line = h('div', 'sh-list-row');
    line.append(h('b', undefined, doc.label));
    if (doc.printed) line.append(badge('Yazdırıldı', 'good'));
    else if (doc.error) line.append(badge('Üretilemedi', 'bad'));
    else line.append(badge('Yazdırılmadı', 'warn'));
    line.append(h('span', 'kit-spacer'));

    if (doc.error) {
      line.append(h('span', 'sh-sub', doc.error));
    } else {
      // YAZICI YOKSA İŞ DURMAZ (K7): dosya nerede, açıkça yazılır.
      if (doc.printError) line.append(h('span', 'sh-sub', `Yazdırılamadı: ${doc.printError}`));
      line.append(h('code', 'sh-code', doc.path));
      line.append(button('Tekrar yazdır', {
        // ÇİFT BASIM KORUMASI otomatik basımı kapsar, bunu değil: kasıtlı
        // tekrar ile kazara ikinci basım ayrı şeylerdir.
        onClick: async () => {
          const sent = await withBusy('Yazıcıya gönderiliyor…', () => call(`${BASE}/print`, {
            method: 'POST', body: { path: doc.path, copies: 1 },
          }));
          if (sent) toast(`${sent.printer || 'Yazıcı'} yazıcısına gönderildi.`, 'good');
        },
      }));
    }
    list.append(line);
  }
  return list;
}

// ------------------------------------------------------------- sihirbaz

/**
 * Gönderi sihirbazı. Üç adım tek çekmecede, sırayla açılır:
 *   1. Ölçü ve taşıyıcı (otomatik değer + elle düzeltme) + ücret dökümü
 *   2. Taslak oluştur  → gönderi kaydı açılır, PARA HARCANMAZ
 *   3. Teklifler       → ETİKET SATIN AL (ayrı izin, 20 karakter gerekçe)
 *
 * Birden çok sipariş seçildiyse sırayla geçilir; her biri kendi ölçüsüyle
 * onaylanır. Toplu "hepsini gönder" düğmesi YOKTUR: yanlış desi doğrudan
 * yanlış faturadır ve toplu iş onu gizler.
 */
function openWizard(rows, index = 0) {
  const row = rows[index];
  if (!row) return;

  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  // Sipariş listesi adresi vermiyor: yer bilgisi boşsa satır "· / " diye
  // görünmesin, hiç yazılmasın.
  const where = [row.city, row.district].filter(Boolean).join(' / ');
  const box = drawer(nodes.root, {
    title: `Gönderi sihirbazı — ${row.customer || row.orderNumber}`,
    subtitle: `${index + 1}/${rows.length} · sipariş ${row.orderNumber}`
      + (where ? ` · ${where}` : ''),
    onClose: dropForms,
  });
  closers.push(dropForms);

  const carriers = (state.carriers || []).filter((item) => item.active);
  const carrierOptions = carriers.length
    ? carriers.map((item) => ({ value: item.code, label: item.label }))
    : [{ value: '', label: 'Taşıyıcı listesi okunamadı' }];

  const form = formGrid({
    fields: [
      // YOL SEÇİMİ EN ÜSTTE: aşağıdaki alanların hangisinin anlamlı olduğunu
      // bu belirliyor. Altta olsaydı kullanıcı desi/kapıda ödeme doldurup
      // sonra "test" seçer ve girdiği hiçbir şeyin kullanılmadığını görürdü.
      { key: 'provider', label: 'Kargo yolu', type: 'select', required: true,
        options: [
          { value: 'geliver', label: 'Geliver — GERÇEK (etiket satın alınır, para harcar)' },
          { value: 'bagisto', label: 'Bagisto — TEST (taşıyıcıya çıkmaz, ücretsiz)' },
        ],
        hint: 'Test yolunda takip numarasını biz üretiriz (TEST- öneki) ve fiş basılır.' },
      { key: 'carrier', label: 'Taşıyıcı', type: 'select', required: true,
        options: carrierOptions },
      { key: 'packages', label: 'Paket sayısı', type: 'number', min: 1, max: 99,
        required: true },
      { key: 'desi', label: 'Desi', type: 'number', min: 0, max: 9999,
        hint: 'Otomatik hesap ürün ölçülerinden gelir; ölçü eksikse elle girin.' },
      { key: 'weight', label: 'Ağırlık (kg)', type: 'number', min: 0, max: 9999 },
      { key: 'payer', label: 'Ödeme tipi', type: 'select', options: PAYERS },
      { key: 'cod', label: 'Kapıda tahsil edilecek', type: 'money', min: 0,
        hint: 'Alıcı ödemeli gönderide taşıyıcının tahsil edeceği tutar.' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 255, wide: true },
    ],
    value: {
      // GERÇEK yol varsayılan. Test yolunu varsayılan yapmak, bir gün birinin
      // gerçek sandığı gönderinin hiç yola çıkmamasına yol açardı.
      provider: state.settings?.provider || 'geliver',
      carrier: state.settings?.defaultCarrier || carrierOptions[0]?.value || '',
      packages: 1,
      desi: row.measures.desi || 0,
      weight: row.measures.weight || 0,
      payer: 'sender',
      cod: 0,
      note: '',
    },
    onChange: () => refreshQuote(),
  });
  forms.push(form);

  const quoteBox = h('div', 'sh-quote');
  const offerBox = h('div', 'sh-offers');
  // BELGE KUTUSU AYRI: `showOffers` teklif kutusunu her çağrıda
  // `replaceChildren` ile siliyor; yazdırma düğmesi orada dursaydı
  // teklifler gelir gelmez kaybolurdu.
  const docBox = h('div', 'sh-docs');
  const actions = h('div', 'sh-actions');

  if (!row.measures.complete) {
    box.body.append(alertBox(
      `Ürün ölçüleri eksik (${row.measures.missing.join(', ')}); otomatik desi yalnız `
      + 'ağırlıktan hesaplandı. Kutuyu ölçüp desiyi elle düzeltin — yanlış desi doğrudan '
      + 'yanlış faturadır.', 'warn'));
  }
  if (!row.delivers) {
    box.body.append(alertBox(
      'Bu bölge "teslimat yapılmıyor" olarak işaretli. Yine de gönderebilirsiniz; '
      + 'işaret bir uyarıdır, engel değil.', 'warn'));
  }

  let quoteTimer = null;
  function refreshQuote() {
    window.clearTimeout(quoteTimer);
    quoteTimer = window.setTimeout(async () => {
      const draft = form.draft();
      let payload;
      try {
        payload = await call(`${BASE}/quote`, {
          method: 'POST',
          body: {
            orderId: row.orderId,
            carrier: draft.carrier || '',
            desi: Number(draft.desi) || 0,
            weight: Number(draft.weight) || 0,
            payer: draft.payer || 'sender',
            cod: Number(draft.cod) || 0,
          },
        });
      } catch (error) {
        quoteBox.replaceChildren(alertBox(`Ücret hesaplanamadı: ${error.message}`, 'warn'));
        return;
      }
      quoteBox.replaceChildren(quoteView(payload));
    }, 220);
  }
  closers.push(() => window.clearTimeout(quoteTimer));

  actions.append(
    button('Taslak oluştur', {
      variant: 'primary',
      onClick: async () => {
        const draft = form.draft();
        if (!form.valid()) { form.showErrors(); toast('Eksik alan var.', 'warn'); return; }
        const reason = await askReason({
          title: 'Gönderi taslağı oluştur',
          description: `${row.orderNumber} siparişi için ${draft.packages} paket, `
            + `${num(Number(draft.desi) || 0, 1)} desi. Bu adımda PARA HARCANMAZ; etiket `
            + 'ayrıca satın alınır.',
          confirmLabel: 'Taslağı oluştur',
        });
        if (!reason) return;
        const result = await withBusy('Taslak oluşturuluyor…', () => call(
          `${BASE}/orders/${row.orderId}/shipments`, {
            method: 'POST',
            body: {
              provider: draft.provider || 'geliver',
              carrier: draft.carrier, packages: Number(draft.packages) || 1,
              desi: Number(draft.desi) || 0, weight: Number(draft.weight) || 0,
              payer: draft.payer, cod: Number(draft.cod) || 0, note: draft.note || '',
              reason, dryRun: false,
            },
          }));
        if (!result) return;
        toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Taslak açıldı.',
          result.dryRun ? 'warn' : 'good');
        for (const line of result.warnings || []) toast(line, 'warn');
        // TEST YOLUNDA TEKLİF YOKTUR — taşıyıcıya çıkılmadı, satın alınacak
        // etiket de yok. Teklif kutusunu açmak, olmayan bir adımı varmış gibi
        // gösterirdi. Onun yerine fiş basılır.
        if (result.test) {
          docBox.replaceChildren(
            alertBox(`TEST gönderisi açıldı — takip no ${result.trackNumber}. `
              + 'Taşıyıcıya çıkılmadı, etiket satın alınmadı, para harcanmadı.', 'good'),
            button('Kargo fişini yazdır', {
              onClick: () => report.run('receipt', {
                orderId: row.orderId, trackNumber: result.trackNumber,
                carrier: result.carrier,
              }),
            }),
          );
          return;
        }
        // GERÇEK YOL: kullanıcı "fatura + kargoya teslim fişi tek A4 çıksın,
        // katlayıp kargo cebine koyacağız" dedi. Düğme teklif kutusunun
        // ÜSTÜNDE durur; etiket satın alınmadan da basılabilmeli, çünkü paket
        // hazırlanırken fişe ihtiyaç var.
        docBox.replaceChildren(button('Teslim fişi + fatura yazdır (A4)', {
          onClick: () => report.run('handover', {
            orderId: row.orderId,
            trackNumber: result.trackNumber || '',
            carrier: draft.carrier || '',
          }),
        }));
        if (result.shipmentId) await showOffers(result.shipmentId);
      },
    }),
  );
  if (index + 1 < rows.length) {
    actions.append(button('Sonraki sipariş →', {
      onClick: () => { box.close(); openWizard(rows, index + 1); },
    }));
  }

  async function showOffers(shipmentId) {
    offerBox.replaceChildren(skeletonRows(3, 4));
    let payload;
    try {
      payload = await call(`${BASE}/shipments/${shipmentId}/offers`);
    } catch (error) {
      offerBox.replaceChildren(alertBox(`Teklifler alınamadı: ${error.message}`, 'bad'));
      return;
    }
    offerBox.replaceChildren();
    if (!payload.items.length) {
      offerBox.append(emptyState({
        title: 'Taşıyıcı teklifi gelmedi',
        text: 'Ölçüler veya adres taşıyıcı tarafından kabul edilmemiş olabilir. '
          + 'Taslağı düzeltip yeniden deneyin.',
      }));
      return;
    }
    offerBox.append(hintBox(
      'Aşağıdaki fiyatlar TAŞIYICININ verdiği kesin tutardır; yukarıdaki döküm '
      + 'bizim tahminimizdir. Fark varsa taşıyıcının fiyatı geçerlidir.'));
    for (const offer of payload.items) {
      const line = h('div', 'sh-offer');
      line.append(
        h('b', undefined, offer.carrierLabel),
        h('span', 'sh-sub', offer.service || '—'),
        h('span', 'kit-spacer'),
        h('b', 'sh-price', money(offer.price)),
        h('span', 'sh-sub', offer.promise || ''),
        button('Etiketi satın al', {
          variant: 'danger',
          title: 'PARA HARCAR — ayrı izin ve en az 20 karakter gerekçe ister',
          onClick: () => purchase(shipmentId, offer),
        }),
      );
      offerBox.append(line);
    }
  }

  async function purchase(shipmentId, offer) {
    const reason = await askPurchaseReason({
      title: 'Etiketi satın al — PARA HARCAR',
      description: `${offer.carrierLabel} · ${money(offer.price)}. Bu işlem taşıyıcıdan `
        + 'etiket satın alır ve GERİ ALINAMAZ. Gerekçe denetim kaydına yazılır; iki ay '
        + 'sonra "bu tutar neden harcandı" sorusunun cevabı bu metin olacak.',
      confirmLabel: 'Satın al',
    });
    if (!reason) return;
    const result = await withBusy('Etiket satın alınıyor…', () => call(
      `${BASE}/shipments/${shipmentId}/purchase`, {
        method: 'POST',
        body: { offerId: offer.id, reason, dryRun: false },
      }));
    if (!result) return;
    toast(result.dryRun
      ? 'Kuru prova: istek gönderilmedi, etiket alınmadı.'
      : `Etiket alındı · takip no ${result.trackingNo} · ${money(result.fee)}`,
    result.dryRun ? 'warn' : 'good');
    box.close();
    loadReady();
  }

  // TEK TIK ÇEKMECEDE DE DURUR. Ölçüyü düzeltip yine tek düğmeyle
  // gönderebilmek gerekiyor; aşağıdaki üç adım "önce bakayım" diyen için.
  const oneClick = h('div', 'sh-actions');
  oneClick.append(button('🚚 Kargoya ver (tek tık)', {
    variant: 'danger',
    title: 'Yukarıdaki ölçülerle gönderi açılır, ETİKET SATIN ALINIR, takip '
      + 'numarası siparişe yazılır ve etiket + fatura yazdırılır. Onay sorulmaz.',
    onClick: async () => {
      const draft = form.draft();
      const sent = await dispatchOrder(row, null, {
        provider: draft.provider || '',
        carrier: draft.carrier || '',
        packages: Number(draft.packages) || 1,
        desi: Number(draft.desi) || 0,
        weight: Number(draft.weight) || 0,
        payer: draft.payer || 'sender',
        cod: Number(draft.cod) || 0,
        note: draft.note || '',
      });
      if (sent) box.close();
    },
  }));

  box.body.append(
    card('Kargoya ver', oneClick,
      'Ara onay yok: gönderi açılır, etiket satın alınır, belgeler basılır'),
    card('1 · Ölçü ve taşıyıcı', form.node,
      'Otomatik değerler ürün kaydından gelir, elle düzeltilebilir'),
    card('2 · Ücret dökümü (tahmin)', quoteBox, 'Kesin tutar taşıyıcı teklifinden gelir'),
    actions,
    card('3 · Belgeler', docBox,
      'Teslim fişi + fatura tek A4; katlanıp kargo cebine konur'),
    card('4 · Taşıyıcı teklifleri', offerBox, 'Etiket burada satın alınır'),
  );
  refreshQuote();
}

function quoteView(payload) {
  const box = h('div', 'sh-quote-lines');
  const quote = payload.quote || {};
  for (const line of quote.lines || []) {
    const row = h('div', 'sh-quote-row');
    row.append(h('span', undefined, line.label), h('span', 'kit-spacer'),
      h('b', undefined, money(line.amount)));
    if (line.note) row.append(h('span', 'sh-sub', line.note));
    box.append(row);
  }
  const total = h('div', 'sh-quote-total');
  total.append(h('b', undefined, `Toplam: ${money(quote.total)}`), h('span', 'kit-spacer'),
    badge(`${num(payload.units)} desi`, 'info'), badge(quote.payerLabel || '', 'dim'));
  box.append(total);
  if (quote.tierFound === false) {
    box.append(alertBox(
      'Bu desi için taşıyıcı sözleşmesinde kademe tanımlı değil; taban ücret 0 sayıldı. '
      + 'Ücretlendirme sekmesinden matrisi tamamlayın.', 'warn'));
  }
  if (payload.zone && payload.zone.found && !payload.zone.delivers) {
    box.append(alertBox('Bu bölgeye teslimat yapılmıyor olarak işaretli.', 'warn'));
  }
  return box;
}

// ================================================================ sekme 2

const SHIP_COLUMNS = [
  { key: 'trackingNo', label: 'Takip no', width: 'minmax(0, 1.2fr)',
    cell: (row) => track(row) },
  { key: 'orderNumber', label: 'Sipariş', width: '96px', className: 'mono' },
  {
    key: 'customer',
    label: 'Müşteri',
    width: 'minmax(0, 1.5fr)',
    cell: (row) => {
      const box = h('span', 'sh-name');
      box.append(clip(h('b'), row.customer || '(isimsiz)', 30),
        h('span', 'sh-sub', `${row.city} / ${row.district}`.replace(/^ \/ | \/ $/, '')));
      return box;
    },
  },
  { key: 'carrierLabel', label: 'Taşıyıcı', width: '120px' },
  { key: 'units', label: 'Desi', width: '110px', align: 'num', cell: desiCell },
  { key: 'fee', label: 'Ücret', width: '104px', align: 'num',
    cell: (row) => money(row.fee) },
  { key: 'createdAt', label: 'Oluşturma', width: '104px',
    cell: (row) => (row.createdAt ? row.createdAt.slice(0, 10) : '—') },
  { key: 'idleDays', label: 'Hareketsiz', width: '100px', align: 'num',
    cell: (row) => (row.idleDays === null ? '—' : `${num(row.idleDays)} gün`) },
  { key: 'status', label: 'Durum', width: 'minmax(0, 1.4fr)', cell: statusCell },
];

function shipEmpty() {
  const data = state.shipments;
  if (!data.connected) {
    return emptyState({
      title: 'Gönderi listesi okunamadı',
      text: data.error || 'Kargo verisi okunamıyor.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadShipments() })],
    });
  }
  if (data.flag) {
    return emptyState({
      title: 'Bu bulguda gönderi yok',
      text: 'Seçili işarete uyan gönderi bu sayfada bulunamadı. İşaret sayfa içinde '
        + 'uygulanıyor; başka sayfada olabilir.',
      actions: [button('İşareti kaldır', { onClick: () => applyFlag(null) })],
    });
  }
  return emptyState({
    title: 'Bu filtreye uyan gönderi yok',
    text: 'Süzgeçleri gevşetin ya da tarih aralığını genişletin.',
    actions: [button('Filtreyi temizle', { onClick: () => nodes.shipFilters.reset() })],
  });
}

function renderShipTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  const data = state.shipments;
  if (data.flag) {
    wrap.append(alertBox(
      `"${FLAG_LABELS[data.flag]}" işareti taşıyıcıda bir alan değil, bizim türettiğimiz `
      + 'bulgudur ve SAYFA İÇİNDE uygulanır. Diğer sayfalarda da eşleşen gönderi olabilir.',
      'info'));
  }
  nodes.shipTable = dataTable({
    columns: SHIP_COLUMNS,
    rows: data.items,
    selectable: true,
    empty: shipEmpty(),
    onRow: (row) => openShipment(row.id),
    onSelect: (ids) => { state.selection = ids; renderShipBar(); },
  });
  wrap.append(nodes.shipTable.node);
  renderKpi();
  renderShipBar();
}

function renderKpi() {
  if (!nodes.kpi) return;
  const counts = state.shipments.totals || {};
  if (!state.shipments.connected) { nodes.kpi.replaceChildren(); return; }
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Sayfadaki gönderi', value: num(counts.total ?? 0) },
    { label: 'Yolda', value: num(counts.inTransit ?? 0), tone: 'info' },
    { label: 'Teslim', value: num(counts.delivered ?? 0), tone: 'good' },
    { label: 'Geciken', value: num(counts.late ?? 0), tone: 'warn',
      title: `${state.shipments.idleLimit} gündür hareket görmeyen gönderiler` },
    { label: 'Teslim edilemedi', value: num(counts.undelivered ?? 0), tone: 'bad' },
    { label: 'Tahsil edilmemiş', value: num(counts.cod ?? 0), tone: 'warn' },
  ]));
}

function renderShipBar() {
  const bar = nodes.shipBar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.selection.length;
  if (!count) { bar.classList.remove('on'); return; }
  bar.classList.add('on');
  bar.append(h('b', undefined, `${num(count)} gönderi seçildi`), h('span', 'kit-spacer'));

  const ready = state.settings?.labelLibrary?.ready !== false;
  const labelBtn = (fmt, label) => button(label, {
    title: ready ? '' : state.settings?.labelLibrary?.message || '',
    disabled: !ready,
    onClick: () => report.run('labels', { shipmentIds: selectedIds(), format: fmt }),
  });

  bar.append(
    labelBtn('thermal-100x150', '🏷 Termal 100×150'),
    labelBtn('a4-4up', '🏷 A4 · 4’lü'),
    button('Manifesto', {
      title: 'Şoföre imzalatılan teslimat listesi (imza satırı boş basılır)',
      onClick: () => report.run('manifest', { shipmentIds: selectedIds() }),
    }),
    button('Senkron', { onClick: () => syncSelected() }),
    button('⤓ Görünen CSV', { onClick: exportVisible }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => { nodes.shipTable.clearSelection(); state.selection = []; renderShipBar(); },
    }),
  );
  if (!ready) {
    bar.append(h('span', 'sh-sub', 'Toplu etiket kapalı: '
      + (state.settings?.labelLibrary?.message || 'gerekli paket kurulu değil.')));
  }
}

function selectedIds() {
  return state.selection.map((id) => Number(id)).filter(Boolean);
}

async function syncSelected() {
  const reason = await askReason({
    title: 'Taşıyıcıdan durum tazele',
    description: `${num(state.selection.length)} gönderi için taşıyıcıya sorulur. `
      + 'Yazma değildir ama taşıyıcı kotasını kullanır.',
    confirmLabel: 'Senkronla',
  });
  if (!reason) return;
  const result = await withBusy('Taşıyıcıdan durum alınıyor…', () => call(
    `${BASE}/shipments/sync`, {
      method: 'POST',
      body: { shipmentIds: selectedIds(), reason, dryRun: false },
    }));
  if (!result) return;
  toast(`${num(result.done)} gönderi tazelendi`
    + (result.failed.length ? ` · ${num(result.failed.length)} tanesi alınamadı` : ''),
  result.failed.length ? 'warn' : 'good');
  loadShipments();
}

function exportVisible() {
  const headers = ['Takip no', 'Sipariş', 'Müşteri', 'Şehir', 'Taşıyıcı', 'Desi', 'Ücret',
    'Ödeme', 'Kapıda ödeme', 'Oluşturma', 'Durum'];
  const rows = state.shipments.items.map((row) => [
    row.trackingNo, row.orderNumber, row.customer, `${row.city}/${row.district}`,
    row.carrierLabel, row.units, money(row.fee), row.payerLabel,
    row.cod ? money(row.cod) : '', row.createdAt, row.statusLabel,
  ]);
  const written = csvBlob(headers, rows, `kargo-sayfa-${state.shipments.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  const values = nodes.shipFilters ? nodes.shipFilters.values() : {};
  const range = values.range || {};
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm gönderileri dışa aktar',
    description: 'Süzgeçlere uyan gönderiler sayfa sayfa çekilir ve rapor klasörüne CSV '
      + 'olarak yazılır. Birkaç dakika sürebilir.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Gönderiler taranıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: { carrier: values.carrier || '', status: values.status || '',
        dateFrom: range.start || '', dateTo: range.end || '' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) toast('Liste tavana dayandı; dosya eksik olabilir.', 'warn');
  });
}

function applyFlag(key) {
  state.shipments.flag = key || '';
  loadShipments({ page: 1 });
}

// ------------------------------------------------------- gönderi çekmecesi

async function openShipment(shipmentId) {
  const box = drawer(nodes.root, { title: 'Gönderi yükleniyor…', subtitle: `#${shipmentId}` });
  box.body.append(skeletonRows(5, 3));

  let payload;
  try {
    payload = await call(`${BASE}/shipments/${shipmentId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Gönderi okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const row = payload.shipment;
  box.setTitle(row.customer || row.trackingNo || `#${shipmentId}`);
  box.body.replaceChildren();

  const head = h('div', 'sh-drawer-head');
  head.append(statusCell(row), track(row),
    badge(row.carrierLabel, 'info'),
    h('span', 'sh-sub', `${row.city} / ${row.district} · ${row.payerLabel}`));
  box.body.append(head);

  if (row.error) box.body.append(alertBox(`Taşıyıcı hatası: ${row.error}`, 'bad'));
  if (!row.known) {
    box.body.append(alertBox(
      `Taşıyıcı "${row.statusLabel}" durumunu bildirdi; bu kod sözlüğümüzde yok ve `
      + '"yolda" varsayılmadı. Gerçek durumu taşıyıcıdan doğrulayın.', 'warn'));
  }

  const facts = h('div', 'sh-facts');
  const fact = (label, value) => {
    const cell = h('div', 'sh-fact');
    cell.append(h('span', 'sh-sub', label), h('b', undefined, value));
    return cell;
  };
  facts.append(
    fact('Sipariş', row.orderNumber || '—'),
    fact('Faturalanan desi', `${num(row.units)} desi`),
    fact('Ölçülen', `${num(row.desi, 1)} desi / ${num(row.weight, 1)} kg`),
    fact('Paket', num(row.packages)),
    fact('Ücret', money(row.fee)),
    fact('Kapıda ödeme', row.cod ? `${money(row.cod)}${row.codCollected ? ' (tahsil edildi)' : ' (tahsil edilmedi)'}` : '—'),
    fact('Oluşturma', row.createdAt || '—'),
    fact('Son hareket', `${row.lastMoveAt || '—'}`),
    fact('Hareketsiz', row.idleDays === null ? '—' : `${num(row.idleDays)} gün`),
  );
  box.body.append(card('Künye', facts));

  const timeline = h('div', 'sh-timeline');
  if (!payload.movements.length) {
    timeline.append(h('div', 'sh-sub', 'Taşıyıcı henüz hareket bildirmedi.'));
  }
  for (const move of payload.movements) {
    const line = h('div', 'sh-move');
    line.append(h('span', 'sh-move-dot'),
      h('b', undefined, move.statusLabel),
      h('span', 'sh-sub', move.location || ''),
      h('span', 'kit-spacer'),
      h('span', 'sh-sub', move.at.replace('T', ' ')));
    if (move.note) line.append(h('span', 'sh-sub', move.note));
    timeline.append(line);
  }
  box.body.append(card('Hareket geçmişi', timeline));

  const actions = h('div', 'sh-actions');
  actions.append(
    button('Siparişi aç', {
      title: 'Siparişler ekranına geçer',
      disabled: !row.orderId,
      onClick: () => openPanel?.('store_orders', { orderId: row.orderId }),
    }),
    button('Etiketi yeniden bas', {
      onClick: () => report.run('labels', {
        shipmentIds: [row.id],
        format: state.settings?.labelFormat || 'thermal-100x150',
      }),
    }),
    button('Takip bağlantısını kopyala', {
      disabled: !row.trackingUrl,
      onClick: async () => {
        await copyText(row.trackingUrl);
        toast('Bağlantı kopyalandı.', 'good');
      },
    }),
    button('Durumu tazele', {
      onClick: async () => {
        const reason = await askReason({
          title: 'Taşıyıcıdan durum tazele',
          description: 'Taşıyıcıya bu gönderi sorulur.',
          confirmLabel: 'Senkronla',
        });
        if (!reason) return;
        await withBusy('Senkronlanıyor…', () => call(`${BASE}/shipments/sync`, {
          method: 'POST', body: { shipmentIds: [row.id], reason, dryRun: false },
        }));
        box.close();
        openShipment(row.id);
      },
    }),
  );
  if (payload.notify) {
    actions.append(button('Müşteriye bildir', {
      onClick: async () => {
        const reason = await askReason({
          title: 'Müşteriye kargo bildirimi',
          description: 'Takip numarası ve güncel durum müşteriye gönderilir.',
          confirmLabel: 'Gönder',
        });
        if (!reason) return;
        const result = await withBusy('Bildirim gönderiliyor…', () => call(
          `${BASE}/shipments/${row.id}/notify`, {
            method: 'POST', body: { template: 'shipment_status', reason, dryRun: false },
          }));
        if (result) toast('Bildirim gönderildi.', 'good');
      },
    }));
  }
  actions.append(
    button('İade gönderisi aç', {
      variant: 'danger',
      title: 'İade etiketi de faturalanır — ayrı izin ve 20 karakter gerekçe ister',
      onClick: async () => {
        const reason = await askPurchaseReason({
          title: 'İade gönderisi aç — PARA HARCAR',
          description: 'Taşıyıcıdan iade etiketi alınır ve faturalanır.',
          confirmLabel: 'İade aç',
        });
        if (!reason) return;
        const result = await withBusy('İade gönderisi açılıyor…', () => call(
          `${BASE}/shipments/${row.id}/return`, {
            method: 'POST', body: { reason, dryRun: false },
          }));
        if (result) { toast('İade gönderisi açıldı.', 'good'); box.close(); loadShipments(); }
      },
    }),
    button('Gönderiyi iptal et', {
      variant: 'danger',
      onClick: async () => {
        const reason = await askReason({
          title: 'Gönderiyi iptal et',
          description: 'Taşıyıcı paketi teslim aldıysa iptali reddedebilir; sonucu '
            + 'olduğu gibi göstereceğiz.',
          confirmLabel: 'İptal et',
        });
        if (!reason) return;
        const result = await withBusy('İptal ediliyor…', () => call(
          `${BASE}/shipments/${row.id}/cancel`, {
            method: 'POST', body: { reason, dryRun: false },
          }));
        if (result) { toast('İptal isteği gönderildi.', 'good'); box.close(); loadShipments(); }
      },
    }),
  );
  box.body.append(actions);
}

// ================================================================ sekme 3

let settingsForm = null;

/**
 * Ekran tercihleri: varsayılan taşıyıcı, etiket biçimi, gecikme eşiği.
 *
 * Taşıyıcılar sekmesinin altındadır çünkü üçünün de karşılığı burada
 * görünür. VİTRİNİ ETKİLEMEZ — mağaza ayarı değil, bu panelin davranışıdır;
 * kart başlığı bunu yazar ki "kaydettim, müşteri de görecek" sanılmasın.
 */
function settingsCard(host) {
  settingsForm?.destroy();
  const data = state.settings || {};
  const carrierOptions = [
    { value: '', label: 'Seçilmemiş' },
    ...(state.carriers || []).map((item) => ({ value: item.code, label: item.label })),
  ];
  settingsForm = formGrid({
    fields: [
      { key: 'defaultCarrier', label: 'Varsayılan taşıyıcı', type: 'select',
        options: carrierOptions, hint: 'Sihirbaz açılırken seçili gelir.' },
      { key: 'labelFormat', label: 'Etiket biçimi', type: 'select',
        options: (data.formats || []).map((item) => ({ value: item.value,
          label: item.label })) },
      { key: 'idleDays', label: 'Gecikme eşiği (gün)', type: 'number', min: 1, max: 30,
        hint: 'Kaç gün hareketsiz kalan gönderi "Geciken" sayılır.' },
      // Kapatılırsa belgeler YİNE üretilir ve rapor klasörüne yazılır; yalnız
      // yazıcıya gitmez. "Üretme" ile "basma" ayrı şeyler.
      { key: 'autoPrint', label: '"Kargoya ver" sonrası etiket ve faturayı yazdır',
        type: 'checkbox',
        hint: 'Kapalıyken belgeler yine üretilir, yalnız yazıcıya gönderilmez. '
          + 'Kargoya teslim fişi bu akışa hiç girmez.' },
    ],
    value: {
      defaultCarrier: data.defaultCarrier || '',
      labelFormat: data.labelFormat || 'thermal-100x150',
      idleDays: data.idleDays || 3,
      autoPrint: data.autoPrint !== false,
    },
  });

  const save = button('Tercihleri kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!settingsForm.valid()) { settingsForm.showErrors(); return; }
      const draft = settingsForm.draft();
      const reason = await askReason({
        title: 'Ekran tercihlerini kaydet',
        description: 'Bu tercihler yalnız Kontrol Merkezi’nin kargo ekranını etkiler; '
          + 'vitrindeki kargo ücreti ve taşıyıcı seçimi değişmez.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Tercihler kaydediliyor…', () => call(
        `${BASE}/settings`, {
          method: 'POST',
          body: { defaultCarrier: draft.defaultCarrier || '',
            labelFormat: draft.labelFormat || '',
            idleDays: Number(draft.idleDays) || null,
            autoPrint: Boolean(draft.autoPrint),
            reason },
        }));
      if (!result) return;
      toast(result.changed?.length ? `Kaydedildi: ${result.changed.join(', ')}.`
        : 'Değişiklik yok.', 'good');
      await loadSettings();
      renderCarriers(host);
    },
  });

  const box = h('div');
  box.append(settingsForm.node, save);
  if (data.labelLibrary && data.labelLibrary.ready === false) {
    box.append(alertBox(data.labelLibrary.message, 'warn'));
  }
  return card('Ekran tercihleri', box,
    'Yalnız bu paneli etkiler — vitrine yansımaz');
}

async function renderCarriers(host) {
  host.replaceChildren(skeletonRows(4, 3));
  let payload;
  try {
    payload = await api(`${BASE}/carriers`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.carriers = payload.items || [];
  host.replaceChildren();

  // Taşıyıcı listesi gelmese bile ekran tercihleri düzenlenebilir: onlar
  // yerel tabloda durur, mağazaya bağlı değildir (K7).
  if (!payload.connected) {
    host.append(alertBox(
      `Taşıyıcı tanımları okunamadı — ${payload.error}. Gönderi sihirbazı taşıyıcı `
      + 'listesi olmadan çalışmaz.', 'bad'), settingsCard(host));
    return;
  }
  if (!state.carriers.length) {
    host.append(emptyState({
      title: 'Tanımlı taşıyıcı yok',
      text: 'Mağaza tarafında henüz taşıyıcı tanımlanmamış. Tanım yapılmadan gönderi '
        + 'oluşturulamaz.',
    }), settingsCard(host));
    return;
  }

  host.append(hintBox(
    'API kimlik bilgileri kasada durur ve buraya MASKELİ gelir; tam değeri hiçbir ekran '
    + 'göstermez. Değişiklik mağaza tarafında yapılır.'));

  for (const carrier of state.carriers) {
    const body = h('div', 'sh-carrier');
    const line = h('div', 'sh-carrier-head');
    line.append(
      badge(carrier.active ? 'Aktif' : 'Pasif', carrier.active ? 'good' : 'dim'),
      carrier.default ? badge('Varsayılan', 'info') : h('span'),
      h('span', 'sh-sub', `Müşteri no: ${carrier.customerNo || '—'}`),
      h('span', 'sh-sub', `API anahtarı: ${carrier.apiKey || '—'}`),
    );
    body.append(line);

    const test = h('div', 'sh-carrier-test');
    test.append(
      button('Bağlantıyı sına', {
        onClick: async () => {
          const reason = await askReason({
            title: `${carrier.label} bağlantısını sına`,
            description: 'Taşıyıcıya kimlik doğrulama isteği gider; gönderi oluşmaz.',
            confirmLabel: 'Sına',
          });
          if (!reason) return;
          const result = await withBusy('Sınanıyor…', () => call(
            `${BASE}/carriers/${carrier.code}/test`, {
              method: 'POST', body: { reason, dryRun: false },
            }));
          if (result) {
            toast(`${carrier.label}: ${result.message || 'bağlantı kuruldu'}`, 'good');
            renderCarriers(host);
          }
        },
      }),
      h('span', 'sh-sub', carrier.testedAt
        ? `Son sınama: ${carrier.testedAt.replace('T', ' ')} · `
          + (carrier.testedOk ? 'başarılı' : 'başarısız')
          + (carrier.testAgeDays !== null ? ` (${num(carrier.testAgeDays)} gün önce)` : '')
        : 'Hiç sınanmamış'),
    );
    body.append(test);

    body.append(tierTable(carrier.tiers, carrier.problems));
    host.append(card(carrier.label, body, `${carrier.tiers.length} desi kademesi`));
  }
  host.append(settingsCard(host));
}

function tierTable(tiers, problems) {
  const box = h('div', 'sh-tiers');
  if (!tiers.length) {
    box.append(alertBox('Bu taşıyıcıda desi kademesi tanımlı değil; ücret hesaplanamaz.',
      'warn'));
    return box;
  }
  for (const tier of tiers) {
    const line = h('div', 'sh-tier');
    line.append(
      h('span', undefined, `${num(tier.min, 1)} – ${tier.max ? num(tier.max, 1) : '∞'} desi`),
      h('span', 'kit-spacer'),
      h('b', undefined, money(tier.price)),
    );
    box.append(line);
  }
  for (const problem of problems || []) box.append(alertBox(problem, 'warn'));
  return box;
}

// ================================================================ sekme 4

let rateForms = [];

async function renderRates(host) {
  host.replaceChildren(skeletonRows(4, 3));
  rateForms.forEach((form) => form.destroy());
  rateForms = [];

  let payload;
  try {
    payload = await api(`${BASE}/rates`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.rates = payload;
  host.replaceChildren();

  if (!payload.connected) {
    // İKİ AYRI DURUM, İKİ AYRI CÜMLE. "Uç yayında değil" beklenecek bir şeydir;
    // "okunamadı" ise tekrar denenecek bir arıza. Aynı metne sığdırmak,
    // personeli olmayan bir arızayı kovalarken bırakırdı. Her iki hâlde de
    // matris ve iki yazma düğmesi HİÇ ÇİZİLMEZ: okunamayan bir tarifenin
    // üstüne yazmak mevcut ücretleri silerdi.
    host.append(alertBox(payload.endpointPending
      ? `Kargo ücretlendirme ucu mağazada henüz yayında değil — ${payload.error} `
        + 'Matris ve kaydetme düğmeleri çizilmedi; paket yayınlanınca bu sekme '
        + 'kendiliğinden açılacak.'
      : `Ücretlendirme okunamadı — ${payload.error} Yazma da kapalıdır; okunamayan bir `
        + 'matrisin üstüne yazmak mevcut tarifeyi silerdi.',
    payload.endpointPending ? 'info' : 'bad'));
    return;
  }

  host.append(hintBox(
    'Bu sekme VİTRİNİ doğrudan etkiler: buradaki matris müşterinin ödeyeceği kargo '
    + 'ücretini belirler. Kademelerde boşluk kalırsa kaydetme reddedilir — açıkta kalan '
    + 'bir desi aralığı vitrinde "ücret hesaplanamadı" hatasına dönüşür.'));

  const generalForm = formGrid({
    fields: [
      { key: 'freeThreshold', label: 'Ücretsiz kargo eşiği', type: 'money', min: 0,
        hint: 'Sepet bu tutarı geçerse gönderici ödemeli kargo ücretsizdir. 0 = kapalı.' },
      { key: 'codFee', label: 'Kapıda ödeme hizmet bedeli', type: 'money', min: 0 },
      { key: 'promise', label: 'Teslimat süresi vaadi', type: 'text', maxLength: 255,
        wide: true, placeholder: 'ör. 2-3 iş günü' },
    ],
    value: { freeThreshold: payload.freeThreshold, codFee: payload.codFee,
      promise: payload.promise },
  });
  rateForms.push(generalForm);
  host.append(card('Genel kurallar', generalForm.node));

  const editors = [];
  for (const carrier of payload.carriers) {
    const editor = tierEditor(carrier);
    editors.push(editor);
    host.append(card(`${carrier.label} · desi ücret matrisi`, editor.node,
      'Ücretler kuruş değil, ekranda TL olarak yazılır'));
  }

  const actions = h('div', 'sh-actions');
  const problemBox = h('div', 'sh-problems');
  actions.append(
    button('Ücretlendirmeyi kaydet', {
      variant: 'primary',
      onClick: async () => {
        const draft = generalForm.draft();
        const body = {
          carriers: editors.map((editor) => editor.value()),
          freeThreshold: Number(draft.freeThreshold) || 0,
          codFee: Number(draft.codFee) || 0,
          promise: draft.promise || '',
        };
        const reason = await askReason({
          title: 'Kargo ücretlendirmesini kaydet',
          description: 'Bu değişiklik VİTRİNDEKİ kargo ücretini değiştirir ve her yeni '
            + 'siparişe uygulanır.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        problemBox.replaceChildren();
        let result;
        try {
          result = await api(`${BASE}/rates`, {
            method: 'POST', body: { ...body, reason, dryRun: false },
          });
        } catch (error) {
          toast(error.message, 'bad');
          return;
        }
        if (!result.ok) {
          toast(result.error, 'bad');
          for (const line of result.problems || []) problemBox.append(alertBox(line, 'bad'));
          return;
        }
        toast('Ücretlendirme kaydedildi.', 'good');
        renderRates(host);
      },
    }),
    button('Taşıyıcı fiyat listesini tazele', {
      onClick: async () => {
        const reason = await askReason({
          title: 'Fiyat listesini tazele',
          description: 'Taşıyıcının güncel sözleşme fiyatları mağazaya yeniden çekilir.',
          confirmLabel: 'Tazele',
        });
        if (!reason) return;
        const result = await withBusy('Fiyat listesi çekiliyor…', () => call(
          `${BASE}/rates/refresh`, { method: 'POST', body: { reason, dryRun: false } }));
        if (result) { toast('Fiyat listesi tazelendi.', 'good'); renderRates(host); }
      },
    }),
  );
  host.append(actions, problemBox);
}

/** Bir taşıyıcının desi matrisi: satır ekle/çıkar, değer yaz. */
function tierEditor(carrier) {
  const node = h('div', 'sh-tier-editor');
  const rows = carrier.tiers.length
    ? carrier.tiers.map((tier) => ({ ...tier }))
    : [{ min: 0, max: 0, price: 0, label: '' }];

  const list = h('div');
  const paint = () => {
    list.replaceChildren();
    rows.forEach((tier, index) => {
      const line = h('div', 'sh-tier-row');
      const numField = (value, onInput, placeholder) => {
        const input = h('input', 'kit-input');
        input.type = 'text';
        input.inputMode = 'decimal';
        input.value = value;
        input.placeholder = placeholder;
        input.addEventListener('input', () => onInput(input.value));
        return input;
      };
      line.append(
        h('span', 'sh-sub', 'desi'),
        numField(String(tier.min ?? 0), (raw) => { tier.min = Number(raw.replace(',', '.')) || 0; }, 'en az'),
        h('span', 'sh-sub', '–'),
        numField(tier.max ? String(tier.max) : '', (raw) => { tier.max = Number(raw.replace(',', '.')) || 0; }, '∞'),
        h('span', 'sh-sub', 'ücret ₺'),
        numField((tier.price / 100).toFixed(2).replace('.', ','),
          (raw) => { tier.price = Math.round((Number(raw.replace(/\./g, '').replace(',', '.')) || 0) * 100); },
          '0,00'),
        button('Sil', {
          variant: 'ghost',
          title: 'Kademeyi listeden çıkarır; mağazada ancak kaydedince silinir',
          onClick: () => { rows.splice(index, 1); paint(); },
        }),
      );
      list.append(line);
    });
  };
  paint();

  const add = button('Kademe ekle', {
    onClick: () => {
      const last = rows[rows.length - 1];
      rows.push({ min: last ? (last.max || last.min + 1) : 0, max: 0, price: 0, label: '' });
      paint();
    },
  });
  node.append(list, add);
  return {
    node,
    value: () => ({ code: carrier.code, tiers: rows.map((tier) => ({
      min: Number(tier.min) || 0, max: Number(tier.max) || 0,
      price: Number(tier.price) || 0, label: tier.label || '',
    })) }),
  };
}

// ================================================================ sekme 5

let zoneForm = null;

async function renderZones(host) {
  host.replaceChildren(skeletonRows(5, 4));
  zoneForm?.destroy();
  zoneForm = null;

  let payload;
  try {
    payload = await api(`${BASE}/zones`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.zones = payload.items || [];
  host.replaceChildren();

  host.append(hintBox(
    'Bölge eşlemesi Kontrol Merkezi’nde durur: ücret dökümünü ve sihirbaz uyarısını '
    + 'etkiler, VİTRİNİ etkilemez. Mağaza tarafında bölge ucu yayınlanınca buradaki '
    + 'tanımlar oraya da taşınacak.'));

  zoneForm = formGrid({
    fields: [
      { key: 'city', label: 'İl', type: 'text', required: true, maxLength: 64 },
      { key: 'district', label: 'İlçe', type: 'text', maxLength: 64,
        hint: 'Boş bırakılırsa ilin tamamı için geçerlidir. İlçe satırı ili ezer.' },
      { key: 'zone', label: 'Bölge adı', type: 'text', maxLength: 64 },
      { key: 'surcharge', label: 'Bölgesel ek ücret', type: 'money', min: 0 },
      { key: 'delivers', label: 'Bu bölgeye teslimat yapılıyor', type: 'checkbox' },
      { key: 'note', label: 'Not', type: 'text', maxLength: 255, wide: true },
    ],
    value: { city: '', district: '', zone: '', surcharge: 0, delivers: true, note: '' },
  });

  const save = button('Bölgeyi kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!zoneForm.valid()) { zoneForm.showErrors(); return; }
      const draft = zoneForm.draft();
      const reason = await askReason({
        title: 'Bölge tanımını kaydet',
        description: `${draft.city} ${draft.district || '(tüm il)'} için bölge, ek ücret ve `
          + 'teslimat durumu yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Bölge kaydediliyor…', () => call(`${BASE}/zones`, {
        method: 'POST',
        body: { city: draft.city, district: draft.district || '', zone: draft.zone || '',
          surcharge: Number(draft.surcharge) || 0, delivers: Boolean(draft.delivers),
          note: draft.note || '', reason },
      }));
      if (result) { toast('Bölge kaydedildi.', 'good'); renderZones(host); }
    },
  });

  const table = dataTable({
    columns: [
      { key: 'city', label: 'İl', width: 'minmax(0, 1fr)' },
      { key: 'district', label: 'İlçe', width: 'minmax(0, 1fr)' },
      { key: 'zone', label: 'Bölge', width: 'minmax(0, 1fr)' },
      { key: 'surcharge', label: 'Ek ücret', width: '110px', align: 'num',
        cell: (row) => money(row.surcharge) },
      { key: 'delivers', label: 'Teslimat', width: '120px',
        cell: (row) => badge(row.delivers ? 'Yapılıyor' : 'Yapılmıyor',
          row.delivers ? 'good' : 'bad') },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)' },
    ],
    rows: state.zones,
    empty: emptyState({
      title: 'Bölge tanımı yok',
      text: 'Hiç bölge tanımlanmamış; her yere aynı ücret uygulanıyor. Uzak ilçelere ek '
        + 'ücret uygulamak için aşağıdaki formu kullanın.',
    }),
    onRow: (row) => zoneForm.reset({ ...row }),
  });

  const formBox = h('div');
  formBox.append(zoneForm.node, save);
  host.append(card('Tanımlı bölgeler', table.node,
    'Satıra tıklayınca form dolar; silme yoktur, "teslimat yapılmıyor" işaretlenir'));
  host.append(card('Bölge ekle / güncelle', formBox));
}

// ================================================================ sekme 6

async function renderPerformance(host) {
  host.replaceChildren(skeletonRows(6, 5));
  let payload;
  try {
    payload = await api(`${BASE}/performance`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  if (!payload.connected) {
    host.append(emptyState({
      title: 'Performans hesaplanamadı',
      text: payload.error || 'Mağazaya ulaşılamadı.',
      actions: [button('Tekrar dene', { variant: 'primary',
        onClick: () => renderPerformance(host) })],
    }));
    return;
  }

  const counts = payload.totals || {};
  const cash = payload.money || {};
  host.append(kpiRow([
    { label: `Gönderi (${payload.days} gün)`, value: num(counts.total) },
    { label: 'Teslim oranı', value: percent(counts.deliveredRate) },
    { label: 'Geciken', value: num(counts.late), tone: 'warn' },
    { label: 'Teslim edilemedi', value: num(counts.undelivered), tone: 'bad' },
    { label: 'Kargo maliyeti', value: money(cash.cost) },
    { label: 'Tahsil edilen', value: money(cash.collected) },
    { label: 'Fark', value: money(cash.margin), tone: cash.margin < 0 ? 'bad' : 'good' },
  ]));

  if (cash.missingCollected) {
    host.append(alertBox(
      `${num(cash.missingCollected)} gönderide müşteriden tahsil edilen kargo bedeli `
      + 'okunamadı; kâr/zarar bu kadar eksik hesaplandı. Rakamı kesin gibi kullanmayın.',
      'warn'));
  }
  if (cash.codOutstanding) {
    host.append(alertBox(
      `Teslim edildiği hâlde tahsil edilmemiş kapıda ödeme: ${money(cash.codOutstanding)}.`,
      'warn'));
  }

  const rows = payload.carriers || [];
  if (!rows.length) {
    host.append(emptyState({
      title: 'Bu pencerede gönderi yok',
      text: `Son ${payload.days} günde kargo hareketi görünmüyor.`,
    }));
    return;
  }

  // Renk tek başına anlam taşımaz: grafiğin altında her taşıyıcının sayıları
  // tabloda da durur.
  host.append(card('Taşıyıcı karşılaştırması',
    groupedBar(rows.map((row) => ({
      label: row.label,
      values: [row.avgDays ?? 0, row.medianDays ?? 0],
    })), { series: ['Ortalama gün', 'Ortanca gün'], format: (value) => value.toFixed(1) }),
    'Yalnız TESLİM EDİLMİŞ gönderilerden hesaplanır'));

  host.append(card('Teslim süresi dağılımı',
    barChart((payload.delays || []).map((item) => ({
      label: item.label, value: item.count, display: num(item.count),
    })))));

  const table = dataTable({
    columns: [
      { key: 'label', label: 'Taşıyıcı', width: 'minmax(0, 1.4fr)' },
      { key: 'count', label: 'Gönderi', width: '90px', align: 'num',
        cell: (row) => num(row.count) },
      { key: 'delivered', label: 'Teslim', width: '90px', align: 'num',
        cell: (row) => num(row.delivered) },
      { key: 'failRate', label: 'Teslim edilemeyen', width: '150px', align: 'num',
        cell: (row) => (row.failRate === null
          ? '—'
          : `${num(row.undelivered)} · ${percent(row.failRate)}`) },
      { key: 'avgDays', label: 'Ort. gün', width: '96px', align: 'num',
        cell: (row) => (row.avgDays === null ? '—' : num(row.avgDays, 1)) },
      { key: 'cost', label: 'Maliyet', width: '116px', align: 'num',
        cell: (row) => money(row.cost) },
      { key: 'collected', label: 'Tahsil', width: '116px', align: 'num',
        cell: (row) => money(row.collected) },
      { key: 'margin', label: 'Fark', width: '116px', align: 'num',
        cell: (row) => {
          const cell = h('b', row.margin < 0 ? 'sh-bad' : 'sh-good', money(row.margin));
          return cell;
        } },
    ],
    rows,
    rowKey: (row) => row.carrier,
  });
  host.append(card('Taşıyıcı tablosu', table.node));

  if ((payload.late || []).length) {
    const late = h('div', 'sh-list');
    for (const row of payload.late) {
      const line = h('div', 'sh-list-row');
      line.append(track(row), h('span', undefined, row.customer || '—'),
        h('span', 'sh-sub', row.carrierLabel), h('span', 'kit-spacer'),
        badge(`${num(row.idleDays)} gündür hareketsiz`, 'bad'));
      line.tabIndex = 0;
      line.style.cursor = 'pointer';
      line.addEventListener('click', () => openShipment(row.id));
      line.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') openShipment(row.id);
      });
      late.append(line);
    }
    host.append(card('Dikkat gerektirenler', late,
      `${num(payload.late.length)} gönderi uzun süredir hareketsiz`));
  }

  const reportBar = h('div', 'sh-actions');
  reportBar.append(button('Performans raporu (PDF)', {
    variant: 'primary',
    onClick: () => report.run('performance', { days: payload.days }),
  }));
  host.append(reportBar);

  if (payload.truncated) {
    host.append(alertBox('Liste tavana dayandı; rakamlar eksik olabilir.', 'warn'));
  }
}

// ================================================================ sekme 7
//
// GELİVER KURULUMU — "Bagisto admin paneline bir daha girmemek" sekmesi.
//
// NE YAPAR: API tokenını yazar (geri OKUMAZ), "kurulu"/"canlı"/"test"
// anahtarlarını çevirir, gönderici adres kimliğini ayarlar, bağlantıyı sınar
// ve webhook'u Geliver hesabına kaydeder.
//
// NE YAPMAZ:
//  · TOKENI GÖSTERMEZ. Mağaza tokenı hiçbir gövdede döndürmüyor; ekran yalnız
//    son dört karakterlik maskeyi görür. Kutu BOŞ bırakılırsa mevcut token
//    KORUNUR — boşu yazmak mağazanın kargo kimliğini silmek olurdu.
//  · CANLIYA GEÇİŞİ KENDİ KENDİNE ONAYLAMAZ. "Canlı" anahtarı açıkken kaydet
//    denince kararı SUNUCU verir: Geliver'a sorar, geçemezse reddeder ve
//    nedenleri söyler. Ekran o nedenleri olduğu gibi basar; kendi tahminini
//    sunucunun cevabının yerine koymaz.
//  · CHECKOUT BAŞLIĞINI, FİYAT AYARLAMASINI VE FİRMA SEÇİMİNİ değiştirmez —
//    mağaza bu üçünü bu uçtan reddediyor ve gerekçesini söylüyor; ekran o
//    gerekçeleri listeler ki "neden burada yok" sorusu cevapsız kalmasın.

let geliverForm = null;

async function renderGeliver(host) {
  host.replaceChildren(skeletonRows(5, 3));
  geliverForm?.destroy();
  geliverForm = null;

  let payload;
  try {
    payload = await api(`${BASE}/geliver`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.geliver = payload;
  host.replaceChildren();

  if (!payload.connected) {
    host.append(alertBox(
      `Geliver ayarları okunamadı — ${payload.error}. Ayar yazma açılmadı: mevcut değerleri `
      + 'bilmeden yazmak, dokunmadığınız anahtarları da ezerdi.', 'bad'));
    host.append(button('Yeniden dene', { onClick: () => renderGeliver(host) }));
    return;
  }

  host.append(statusCard(payload));
  host.append(checksCard(host, payload));
  host.append(settingsFormCard(host, payload));
  host.append(webhookCard(host, payload));
  host.append(refusedCard(payload));
}

/** "Müşteri şu an ne görüyor" — ekranın ilk bakışta okunan tek cümlesi. */
function statusCard(payload) {
  const status = payload.status || {};
  const token = payload.token || {};
  const box = h('div', 'sh-gl-status');
  box.append(
    badge(status.visible ? 'Müşteriye AÇIK' : 'Müşteriye kapalı',
      status.tone || 'dim'),
    h('span', 'sh-sub', status.label || ''),
  );
  const line = h('div', 'sh-gl-line');
  line.append(
    h('span', 'sh-sub', `API tokenı: ${token.hasToken ? token.mask || 'girilmiş' : 'GİRİLMEMİŞ'}`),
    h('span', 'sh-sub', `Test modu: ${payload.settings?.testMode ? 'AÇIK — gönderiler sahtedir'
      : 'kapalı'}`),
  );
  box.append(line);

  const blockers = payload.blockers || [];
  if (blockers.length) {
    // ENGELLER KAYDETMEDEN ÖNCE GÖRÜNÜR. Kullanıcıyı önce reddedilecek bir
    // isteğe göndermek kötü bir öğretmendir.
    const list = h('div', 'sh-gl-blockers');
    for (const item of blockers) {
      const row = h('div', 'sh-gl-blocker');
      row.append(h('b', undefined, item.message));
      if (item.action) row.append(h('span', 'sh-sub', item.action));
      list.append(row);
    }
    box.append(alertBox('Canlıya geçiş şu an mümkün değil:', 'warn'), list);
  }
  return card('Durum', box, 'Üçü birden açık olmadan müşteri Geliver fiyatını görmez');
}

/** Geliver'a sorulan iki sorunun cevabı + "şimdi dene" düğmesi. */
function checksCard(host, payload) {
  const box = h('div', 'sh-gl-checks');
  for (const check of payload.checks || []) {
    const row = h('div', 'sh-gl-check');
    row.append(
      badge(`${CHECK_MARKS[check.state] || '—'} ${check.label}`,
        CHECK_TONES[check.state] || 'dim'),
      h('span', 'sh-sub', check.detail || ''),
    );
    box.append(row);
  }
  box.append(button('Bağlantıyı sına', {
    title: 'Geliver’a yalnız OKUMA çağrısı gider; gönderi, etiket ya da iade oluşmaz.',
    onClick: async () => {
      const result = await withBusy('Sınanıyor…', () => call(`${BASE}/geliver/test`));
      if (!result) return;
      const failed = (result.checks || []).filter((item) => item.state !== 'ok');
      toast(failed.length ? `${failed.length} denetim geçmedi.` : 'Bağlantı kuruldu.',
        failed.length ? 'warn' : 'good');
      renderGeliver(host);
    },
  }));
  return card('Bağlantı denetimi', box,
    'Önbellek atlanır — o anki gerçeği sorar');
}

function settingsFormCard(host, payload) {
  const data = payload.settings || {};
  const token = payload.token || {};
  geliverForm = formGrid({
    fields: [
      { key: 'apiToken', label: 'API tokenı', type: 'text', wide: true, maxLength: 512,
        placeholder: token.hasToken
          ? `Kayıtlı: ${token.mask || '****'} — değiştirmeyecekseniz BOŞ bırakın`
          : 'Geliver panelindeki tokenı yapıştırın',
        hint: 'Kaydedilen token bir daha OKUNAMAZ; ekran yalnız son dört karakterini '
          + 'gösterir. Boş bırakmak mevcut tokenı SİLMEZ — silmenin yolu yenisiyle '
          + 'değiştirmek ya da entegrasyonu kapatmaktır.' },
      { key: 'active', label: 'Entegrasyon kurulu', type: 'checkbox',
        hint: 'Kapatmak kargoyu kaldırmaz: checkout mağazanın kendi desi tablosundaki '
          + 'ücretlere döner.' },
      { key: 'goLive', label: 'Müşteriye açık (canlı)', type: 'checkbox',
        hint: 'Açmak checkout’ta Geliver’ın CANLI fiyatını gösterir ve sipariş ekranından '
          + 'gerçek gönderi açılmasını başlatır. Sunucu önce denetler; geçemezse reddeder.' },
      { key: 'testMode', label: 'Test modu', type: 'checkbox',
        hint: 'Açıkken oluşturulan gönderiler SAHTEDİR; gerçek kargo çağrılmaz ve etiket '
          + 'taşınmaz.' },
      { key: 'senderAddressId', label: 'Gönderici adres kimliği', type: 'text', maxLength: 64,
        hint: 'Boş = hesabın varsayılan çıkış adresi. Buraya adresin kendisi değil, '
          + 'Geliver’daki kimliği yazılır.' },
      { key: 'sourceIdentifier', label: 'Mağaza adresi (salt gösterilir)', type: 'static' },
      { key: 'title', label: 'Checkout başlığı (salt gösterilir)', type: 'static' },
    ],
    value: {
      apiToken: '',
      active: Boolean(data.active),
      goLive: Boolean(data.goLive),
      testMode: Boolean(data.testMode),
      senderAddressId: data.senderAddressId || '',
      sourceIdentifier: data.sourceIdentifier || '—',
      title: data.title || '—',
    },
  });

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: () => saveGeliver(host, payload, false),
  });
  const preview = button('Önce prova et', {
    title: 'Hiçbir şey yazılmaz; yalnız ne gideceği gösterilir.',
    onClick: () => saveGeliver(host, payload, true),
  });

  const box = h('div');
  const actions = h('div', 'sh-actions');
  actions.append(preview, save);
  box.append(geliverForm.node, actions);
  return card('Ayarlar', box, 'Gerekçe en az 20 karakter — canlıya geçiş para hareketi başlatır');
}

async function saveGeliver(host, payload, dryRun) {
  if (!geliverForm.valid()) { geliverForm.showErrors(); return; }
  const draft = geliverForm.draft();
  const body = {
    apiToken: String(draft.apiToken || '').trim(),
    active: Boolean(draft.active),
    goLive: Boolean(draft.goLive),
    testMode: Boolean(draft.testMode),
    senderAddressId: String(draft.senderAddressId || '').trim(),
  };
  const opening = body.goLive && !payload.settings?.goLive;
  const reason = await askPurchaseReason({
    title: dryRun ? 'Geliver ayarlarını prova et' : 'Geliver ayarlarını kaydet',
    description: opening
      ? 'CANLIYA ALIYORSUNUZ: checkout Geliver’ın gerçek fiyatını göstermeye, sipariş '
        + 'ekranı gerçek gönderi açmaya başlar. Sunucu önce token, erişim ve gönderici '
        + 'adresi denetler; geçemezse istek reddedilir ve neden söylenir.'
      : 'Kargo entegrasyonunun kurulum ayarları değişecek. Gerekçe denetim kaydına yazılır.',
    confirmLabel: dryRun ? 'Prova et' : 'Kaydet',
  });
  if (!reason) return;

  // `call` DEĞİL, ham `api`: `call` `ok:false` yanıtını istisnaya çevirir ve
  // ön denetim engelleri (gövdedeki `blockers`) yolda kaybolurdu. Bu ekranın
  // asıl işi tam olarak o engelleri göstermek.
  const result = await withBusy(dryRun ? 'Prova ediliyor…' : 'Kaydediliyor…', () => api(
    `${BASE}/geliver`, { method: 'POST', body: { ...body, reason, dryRun } }));
  if (!result) return;

  if (result.ok === false) {
    // SUNUCUNUN REDDİ OLDUĞU GİBİ GÖSTERİLİR. Ön denetim engelleri hata
    // metnine sığmıyor; ayrı satırlar hâlinde gelirler ve her biri ne
    // yapılacağını söyler.
    toast(result.error, 'bad');
    for (const item of result.blockers || []) {
      toast(`${item.message}${item.action ? ` → ${item.action}` : ''}`, 'warn');
    }
    return;
  }

  if (result.dryRun) {
    toast(`Prova: ${(result.labels || []).join(' · ') || 'değişen alan yok'}. Hiçbir şey yazılmadı.`,
      'warn');
    return;
  }
  toast(`Kaydedildi: ${(result.labels || []).join(' · ')}.`, 'good');
  renderGeliver(host);
}

function webhookCard(host, payload) {
  const hook = payload.webhook || {};
  const box = h('div');
  box.append(alertBox(hook.message || 'Webhook durumu bilinmiyor.',
    { ok: 'good', missing: 'bad', unknown: 'warn' }[hook.state] || 'info'));
  if (hook.url) box.append(h('code', 'sh-gl-url', hook.url));

  box.append(button('Webhook’u kaydet', {
    variant: hook.state === 'missing' ? 'primary' : '',
    title: 'Geliver hesabında bir webhook kaydı açar; gönderi durumu bundan sonra '
      + 'kendiliğinden güncellenir.',
    onClick: async () => {
      const reason = await askReason({
        title: 'Webhook’u Geliver hesabına kaydet',
        description: 'Geliver hesabınızda bir kayıt açılır. Gönderi durumu bundan sonra '
          + 'taşıyıcıdan kendiliğinden gelir; bugün her gönderi elle senkron bekliyor.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const result = await withBusy('Webhook kaydediliyor…', () => call(
        `${BASE}/geliver/webhook`, { method: 'POST', body: { reason, dryRun: false } }));
      if (!result) return;
      toast('Webhook kaydedildi.', 'good');
      renderGeliver(host);
    },
  }));
  return card('Webhook', box, 'Gönderi durumunun kendiliğinden güncellenmesini sağlar');
}

/** Bu uçtan DEĞİŞTİRİLEMEYEN alanlar — sessizce yok olmazlar. */
function refusedCard(payload) {
  const refused = payload.refused || {};
  const keys = Object.keys(refused);
  if (!keys.length) return h('span');
  const box = h('div', 'sh-gl-refused');
  for (const key of keys) {
    const row = h('div');
    row.append(h('code', undefined, key), h('span', 'sh-sub', ` — ${refused[key]}`));
    box.append(row);
  }
  return card('Buradan değiştirilemeyenler', box,
    'Mağaza panelinden düzenlenir; gerekçeleri mağazanın kendi cevabıdır');
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  openPanel = ctx.open;

  const view = h('div', 'kit-panel sh');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar(TABS, 'ready', (key) => showTab(key));
  nodes.status = statusLine();
  nodes.kpi = h('div', 'sh-kpi');
  nodes.shipBar = h('div', 'sh-selbar');
  nodes.tableWrap = h('div', 'sh-table');
  nodes.body = h('div', 'sh-body');

  nodes.readyFilters = filterBar({
    fields: [{ kind: 'search', key: 'q', placeholder: 'Müşteri adı ara', width: '260px' }],
    onChange: () => loadReady({ page: 1 }),
    actions: [button('Yenile', { onClick: () => loadReady() })],
  });

  nodes.shipFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Takip no, sipariş, müşteri, adres',
        width: '260px' },
      { kind: 'select', key: 'carrier', label: 'Taşıyıcı',
        options: [{ value: '', label: 'Tümü — taşıyıcı' }] },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Tümü' },
        ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
      ] },
      { kind: 'select', key: 'dateField', label: 'Tarih', options: [
        { value: 'created', label: 'Oluşturma' }, { value: 'delivered', label: 'Teslim' },
      ] },
      // `<input type="date">` KULLANILMAZ (WebKitGTK'da bozuk) — kitin dateRange'i.
      { kind: 'dateRange', key: 'range', label: 'Aralık',
        start: todayIso(-30), end: todayIso() },
      { kind: 'search', key: 'city', placeholder: 'Şehir', width: '130px' },
      { kind: 'numRange', key: 'desi', label: 'Desi' },
      { kind: 'select', key: 'payer', label: 'Ödeme', options: [
        { value: '', label: 'Tümü' }, ...PAYERS.map((item) => ({ ...item })),
      ] },
    ],
    onChange: () => loadShipments({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => loadShipments() }),
      button('⤓ Tümü', { title: 'Tüm kayıtları rapor klasörüne yaz', onClick: exportAll }),
    ],
  });

  nodes.flagChips = chipRow(FLAG_CHIPS, null, (key) => applyFlag(key));
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => loadShipments({ page, size }),
  });

  const shipView = [nodes.shipFilters.node, nodes.flagChips.node, nodes.status.node,
    nodes.kpi, nodes.shipBar, nodes.tableWrap, nodes.pager.node];

  view.append(tabs.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    nodes.body.replaceChildren();
    if (key === 'ready') {
      nodes.readyHost = h('div', 'sh-pane');
      nodes.body.append(nodes.readyFilters.node, nodes.readyHost);
      loadReady();
      return;
    }
    if (key === 'shipments') {
      for (const node of shipView) nodes.body.append(node);
      if (!state.shipments.loaded) loadShipments();
      else renderShipTable();
      return;
    }
    const host = h('div', 'sh-pane');
    nodes.body.append(host);
    if (key === 'carriers') renderCarriers(host);
    else if (key === 'rates') renderRates(host);
    else if (key === 'zones') renderZones(host);
    else if (key === 'geliver') renderGeliver(host);
    else renderPerformance(host);
  }

  root.replaceChildren(view);
  // Taşıyıcılar ÖNCE okunur: gönderi sihirbazı taşıyıcı listesi olmadan
  // açılamıyor ve kullanıcı ilk seçtiği siparişte boş bir açılır liste görürdü.
  loadSettings()
    .then(() => api(`${BASE}/carriers`).catch(() => null))
    .then((payload) => {
      state.carriers = payload?.items || [];
      nodes.shipFilters.options('carrier', [
        { value: '', label: 'Tümü — taşıyıcı' },
        ...state.carriers.map((item) => ({ value: item.code, label: item.label })),
      ]);
    })
    // Hazırlık ne olursa olsun ekran AÇILIR (K7): taşıyıcı listesi gelmezse
    // sihirbaz kısıtlı çalışır, ama panel boş kalmaz.
    .catch(() => null)
    .then(() => showTab('ready'));

  return () => {
    // Gerekçe alanı panelin ömrü boyunca korunur ama ötesine TAŞINMAZ: bir
    // sonraki oturum başka bir işin gerekçesiyle açılmamalı.
    nodes.readyReason = null;
    nodes.readyFilters?.destroy();      // arama alanı bekleyen debounce tutar
    nodes.shipFilters?.destroy();       // tarih aralığı global dinleyici tutar
    zoneForm?.destroy();
    zoneForm = null;
    settingsForm?.destroy();
    settingsForm = null;
    geliverForm?.destroy();
    geliverForm = null;
    rateForms.forEach((form) => form.destroy());
    rateForms = [];
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = freshState();
    busy = false;
  };
}
