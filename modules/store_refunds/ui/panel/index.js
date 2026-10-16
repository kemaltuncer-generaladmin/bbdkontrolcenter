// İadeler paneli — bir iadenin tamamı tek çekmecede biter.
//
// NE YAPAR: kredi notları (para) ile iade taleplerini (süreç) tek listede
// birleştirir; durum çipleri ve KPI (bekleyen tutar · bu ay iade · iade oranı)
// üstte durur; satır açılınca çekmecede sipariş kalemleri adet adet seçilir,
// İADE TUTARI SATIR SATIR gösterilir (ürün + indirim + KDV + kargo), gerekçeyle
// onaylanınca mağazadaki refund listener Kuveyt Türk iadesini otomatik başlatır.
//
// TIK SAYISI (standart iade, açık RMA talebiyle):
//   eskiden  satır → adet yaz → Hesapla → Onayla → gerekçe yaz + onay → "Kontrol ettim"
//   şimdi    satır → (adetler talepten dolar, tutar kendiliğinden gelir) →
//            İadeyi oluştur… → gerekçe çipi → "Kontrol ettim, iadeyi oluştur"
// Korumalar AYNI: tutar ekranda görülmeden onay açılmaz, gerekçe ≥10 karakter
// (ADR 0012), RMA varsa yetkili açıkça onaylar — yalnız ikinci pencere yok.
//
// NE YAPMAZ:
//  · Tutarı istemcide hesaplamaz. Hesap tek yerde (backend `calc.py`) durur;
//    burada ikinci bir aritmetik olsaydı iki sayı arasında sessiz fark çıkardı.
//    Ekran seçim yapılırken YAKLAŞIK bir toplam gösterir ve öyle etiketler;
//    kesin tutar adet değişince sunucudan KENDİLİĞİNDEN gelir.
//  · Her tuşta mağazaya gitmez. Hesap ucu siparişi tazeler ve mağazanın kendi
//    önizlemesini sorar; son değişiklikten 700 ms sonra BİR KEZ sorulur.
//    Her tuşta sormak dakikada 55 istek kovasını tüketir ve ekranı kilitler.
//  · Açılışta pencerenin TAMAMINI çekmez. Her kaynağın ilk sayfası (en yeni
//    kayıtlar) gelir; süzgeç, durum çipi ya da son sayfa tamamını ister.
//    İlk sayfada süzmek "bu müşterinin iadesi yok" gibi YANLIŞ bir sonuç verirdi.
//  · İade faturası KESMEZ. Fatura Fatura ekranının işidir; iki ekranın aynı
//    belgeyi kesmesi çift numara üretir.
//  · Ayrı POS iadesi YAPMAZ. Kredi notu bankaya iadeyi kendisi başlatır.
//  · Kayıt SİLMEZ. Reddedilen talep de kayıttır ve listede kalır (ADR 0012).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Kredi notu ile talep AYNI olayın iki kaydıdır; liste onları tek satırda
//    birleştirir, yoksa bekleyen tutar iki kez sayılır.
//  · Faturalanmamış kalem iade EDİLEMEZ; adet kutusu 0'da kilitlidir.
//  · Kargo bedeli kendiliğinden iade edilmez — kutu her iadede tek tek işaretlenir.
//  · Mağazanın hesabı bizimkinden farklı çıkabilir; uygulanan MAĞAZANINKİDİR ve
//    fark ekranda kırmızı yazar.
//  · `/api/admin/bbd/*` uçları YAYINDA (ölçüm 2026-08-16); bir dönem 404
//    dönüyorlardı ve bu satır "hâlâ yazılıyor" diyordu. Uç yine de yoksa
//    (geri çekilirse) o bölüm KAPALI görünür ve "uç hazır olunca açılacak"
//    der; ekranın geri kalanı çalışır. Bölümün açık/kapalı olduğunu bu dosya
//    KARAR VERMEZ — sunucudan gelen `sources.*.available` çizer; buraya
//    gömülü bir "yayında değil" varsayımı ekranda yanlış durum çizerdi.
//
// SADE GÖRÜNÜM (docs/tasarim-sade-magaza.md — bağlayıcı): görünür süzgeç
// yalnız arama + tarih; gerisi "Filtreler" arkasında. Dışa aktarma tek menüde.
// En çok beş çip, üç KPI, yedi sütun. Bilgi kutusu yok: açıklama gerekiyorsa
// `title` ipucunda ya da bu dosyanın yorumlarında durur. Uyarılar YALNIZ
// eyleme dönüşen ya da hata olan durumlardır ve tek satırdır. Taşınan
// sütunlar (Adet · Yöntem · Kargo) çekmecenin başında ve CSV'de durur.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_refunds/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_refunds/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, csvBlob, debounce, h, loadStyles, menuButton, money, num, parseMoney, percent,
  toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import { applyChoiceFilter } from '../../ui-kit/choice.js';
import { RANGE_PRESETS } from '../../ui-kit/datefield.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, kpiRow,
  skeletonRows, statusLine,
} from '../../ui-kit/layout.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_refunds';

//: Gerekçenin en az uzunluğu — backend de aynısını ister (K9, ADR 0012).
const MIN_REASON = 10;

const STATUSES = [
  { key: 'requested', label: 'Talep', tone: 'info' },
  { key: 'approved', label: 'Onaylandı', tone: 'info' },
  { key: 'awaiting', label: 'Ürün bekleniyor', tone: 'warn' },
  { key: 'received', label: 'Ürün geldi', tone: 'warn' },
  { key: 'refunded', label: 'İade edildi', tone: 'good' },
  { key: 'rejected', label: 'Reddedildi', tone: 'dim' },
  { key: 'other', label: 'Bilinmiyor', tone: 'dim' },
];

//: Çip şeridi — EN ÇOK BEŞ (sade kural §6), iş akışının sırasıyla.
//: "Ürün bekleniyor" ayrı çip DEĞİL, "Onaylandı"nın içindedir: ikisi de
//: "onaylandı, ürün yolda" demek ve aynı işi (beklemek) ister; satırdaki
//: rozet tam durumu yine yazar. "Bilinmiyor" çipi yok: o satırlar süzgeçsiz
//: listede rozetleriyle görünür.
const CHIPS = [
  { key: 'requested', label: 'Talep', statuses: ['requested'] },
  { key: 'approved', label: 'Onaylandı', statuses: ['approved', 'awaiting'] },
  { key: 'received', label: 'Ürün geldi', statuses: ['received'] },
  { key: 'refunded', label: 'İade edildi', statuses: ['refunded'] },
  { key: 'rejected', label: 'Reddedildi', statuses: ['rejected'] },
];

//: Talebi hâlâ AÇIK olan durumlar. Çekmece bunlarda talebin kalemlerini sorar
//: ve iade adetlerini talepteki adetlerle önerir; kapanmış talepte öneri yok.
const OPEN_KEYS = new Set(['requested', 'approved', 'awaiting', 'received']);

const REASONS = [
  { value: 'disliked', label: 'Beğenmedi' },
  { value: 'defective', label: 'Ayıplı' },
  { value: 'wrong_item', label: 'Yanlış ürün' },
  { value: 'late', label: 'Geç teslim' },
  { value: 'withdrawal', label: 'Cayma hakkı' },
  { value: 'other', label: 'Diğer' },
];

const METHODS = [
  { value: 'pos', label: 'POS iadesi' },
  { value: 'transfer', label: 'Havale' },
  { value: 'credit', label: 'Mağaza kredisi' },
  { value: 'exchange', label: 'Değişim' },
  { value: 'unknown', label: 'Belirtilmedi' },
];

//: Süreç ucundan yazılabilen durumlar. `refunded` BİLEREK yok: para gerçekten
//: geri verilmeden "iade edildi" yazmak mutabakatta kapatılamayan kayıt bırakır.
const FLOW = [
  { key: 'approved', label: 'Onayla' },
  { key: 'awaiting', label: 'Ürün bekleniyor' },
  { key: 'received', label: 'Ürün geldi' },
  { key: 'rejected', label: 'Reddet' },
];

//: Gerekçe ÇİPLERİ: tıklayınca kutuyu DOLDURUR, onaylamaz; metin yine
//: düzenlenebilir. Hepsi en az 10 karakterdir — kapıyı delmezler (ADR 0012).
const REFUND_CHIPS = ['Müşteri iade etti', 'Hasarlı ürün', 'Yanlış ürün gönderildi',
  'Cayma hakkı kullanıldı', 'Sipariş iptal edildi'];
const SHIP_CHIPS = ['Müşteri iade etti', 'Hasarlı ürün', 'Yanlış ürün gönderildi'];
const FLOW_CHIPS = {
  approved: ['İade talebi uygun bulundu'],
  awaiting: ['Müşteri kargoya verecek'],
  received: ['Ürün depoya ulaştı'],
  rejected: ['Cayma süresi dolmuş', 'Ürün kullanılmış'],
};
const NOTIFY_CHIPS = ['İade durumu bildirildi'];

//: POS bölümünün TEK satırı (sunucu göndermezse). Uzun paragraf okunmuyordu.
const POS_NOTE = 'Banka iadesi, iade (kredi notu) oluşturulunca Kuveyt Türk’e otomatik gider.';

//: Durum süzgeci burada DEĞİL: çip birden çok durumu kapsayabildiği için
//: `applyView` içinde ayrıca uygulanır.
const FILTER_SPEC = {
  q: { kind: 'search', fields: ['number', 'orderNumber', 'customer', 'email', 'tracking', 'itemNames'] },
  reason: { kind: 'equals', field: 'reason' },
  method: { kind: 'equals', field: 'method' },
  carrier: { kind: 'equals', field: 'carrier' },
  amount: { kind: 'numRange', field: 'amount' },
  withdrawal: { kind: 'toggle', test: (row) => Boolean(row.flags?.withinWithdrawal) },
  posFailed: { kind: 'toggle', test: (row) => Boolean(row.flags?.posFailed) },
  late: { kind: 'toggle', test: (row) => Boolean(row.flags?.itemLate) },
};

const EMPTY_STATE = {
  items: [], filtered: [], summary: null, warnings: [], sources: {},
  settings: { freeReturnDays: 14, returnWaitDays: 7, shippingDefault: false, pageSize: 50 },
  range: { from: '', to: '' }, connected: false, error: '', truncated: false,
  // Durum süzgeci çiplerdedir, filtre şeridinde DEĞİL: aynı süzgeci iki yerde
  // tutmak, birinden temizleyip diğerinde asılı kalmasına yol açıyordu.
  chip: null, page: 1, size: 50, loaded: false,
  // İlk sayfa mı, pencerenin tamamı mı yüklü. `partial` iken süzmek eksik
  // sonuç verir; süzgeç dokunulunca tamamı istenir.
  full: false, partial: false, partialNote: '',
  // Para çıktıktan sonra oluşan ve YENİLEMEYLE KAYBOLMAMASI gereken uyarı.
  notice: '',
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let loadSeq = 0;
let state = { ...EMPTY_STATE };

const nodes = {};

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

/**
 * Yıkıcı ve para etkileyen her işlem buradan geçer: gerekçe backend'e gider.
 *
 * Kitin `confirmWithReason`ı ile AYNI kapı (en az 10 karakter, Enter onaylar,
 * Escape ve dışarı tıklama vazgeçer). İki ek: gerekçe ÇİPLERİ kutuyu tek
 * tıkla doldurur, `warning` aynı pencerede durur. RMA uyarısı eskiden ikinci
 * bir onay penceresiydi — aynı karar için iki pencere, iki tık.
 */
function askReason({ title, description, warning = '', confirmLabel = 'Onayla', chips = [] }) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const dialog = h('div', 'kit-dialog');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.append(h('h3', 'kit-dialog-title', title));
    if (description) dialog.append(h('p', 'kit-dialog-text', description));
    if (warning) dialog.append(alertBox(warning, 'warn'));

    const input = h('input', 'kit-input');
    input.type = 'text';
    input.maxLength = 255;
    input.placeholder = `Gerekçe (en az ${MIN_REASON} karakter) — denetim kaydına yazılır`;
    const error = h('div', 'kit-dialog-error');

    if (chips.length) {
      const row = h('div', 'rf-chips');
      for (const text of chips) {
        const chip = h('button', 'kit-chip', text);
        chip.type = 'button';
        chip.addEventListener('click', () => {
          input.value = text;
          error.textContent = '';
          input.focus();
        });
        row.append(chip);
      }
      dialog.append(row);
    }
    dialog.append(input, error);

    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    const submit = () => {
      const reason = input.value.trim();
      if (reason.length < MIN_REASON) {
        error.textContent = `Gerekçe en az ${MIN_REASON} karakter olmalı.`;
        input.focus();
        return;
      }
      close(reason);
    };
    // Enter YALNIZ kutudayken onaylar: çipe klavyeyle basmak pencereyi
    // kapatıp parayı çıkarmamalı.
    function onKey(event) {
      if (event.key === 'Escape') close(null);
      if (event.key === 'Enter' && event.target === input) submit();
    }

    const actions = h('div', 'kit-dialog-actions');
    actions.append(
      button('Vazgeç', { onClick: () => close(null) }),
      button(confirmLabel, { variant: 'danger', onClick: submit }),
    );
    dialog.append(actions);
    overlay.append(dialog);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    nodes.root.append(overlay);
    input.focus();
  });
}

function statusOf(key) {
  return STATUSES.find((item) => item.key === key) || STATUSES[STATUSES.length - 1];
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const window = `${state.range.from} – ${state.range.to}`;
  const shown = state.filtered.length;
  const all = state.items.length;
  const scope = shown === all ? `${num(all)} kayıt` : `${num(shown)} / ${num(all)} kayıt`;
  return `${window} · ${scope}${state.partial ? ' (en yeniler)' : ''}`;
}

/** Tarih dışında bir süzgeç dolu mu — doluysa ilk sayfa YETMEZ. */
function hasActiveFilter(values) {
  return Object.entries(values || {}).some(([key, value]) => {
    if (key === 'range' || value === null || value === undefined || value === '' || value === false) {
      return false;
    }
    if (typeof value === 'object') {
      return Object.values(value).some((part) => part !== null && part !== undefined && part !== '');
    }
    return true;
  });
}

// -------------------------------------------------------------------- veri

function currentRange() {
  const values = nodes.filters ? nodes.filters.values() : {};
  return values.range || {};
}

async function refresh({ full = state.full, fresh = false, keepPage = false } = {}) {
  const mine = ++loadSeq;
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 8));
  nodes.status?.set(full ? 'Aralığın tamamı alınıyor…' : 'İadeler alınıyor…');
  const range = currentRange();
  const query = new URLSearchParams();
  if (range.start) query.set('dateFrom', range.start);
  if (range.end) query.set('dateTo', range.end);
  if (full) query.set('full', '1');
  if (fresh) query.set('fresh', '1');

  let payload;
  try {
    // Tam tarama mağazanın TAMAMINI sayfa sayfa tarayabiliyor. Kabuğun 60
    // saniyelik varsayılanı taramayı ortasında kesip hatayı "çekirdeğe
    // bağlanılamadı" diye gösteriyordu; süre açıkça istenir.
    payload = await api(`${BASE}/refunds?${query.toString()}`,
      { timeoutSeconds: full ? 300 : 120 });
  } catch (error) {
    if (mine !== loadSeq) return;
    state = { ...state, connected: false, error: error.message, items: [], filtered: [] };
    renderAll();
    nodes.status?.set(statusText(), true);
    return;
  }
  // Bu arada daha yeni bir istek yola çıktıysa (tarih değişti, "tümü" istendi)
  // eski yanıt ekranı EZMEZ.
  if (mine !== loadSeq) return;

  state = {
    ...state,
    items: payload.items || [],
    summary: payload.summary || null,
    warnings: payload.warnings || [],
    sources: payload.sources || {},
    settings: payload.settings || state.settings,
    range: payload.range || state.range,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    truncated: Boolean(payload.truncated),
    full: Boolean(payload.full),
    partial: Boolean(payload.partial),
    partialNote: payload.partialNote || '',
    page: keepPage ? state.page : 1,
    size: (payload.settings && payload.settings.pageSize) || state.size,
    loaded: true,
  };
  fillCarriers();
  applyView();
}

/** İlk sayfa yüklüyse pencerenin tamamını ister; zaten tamsa bir şey yapmaz. */
function loadFull({ keepPage = false } = {}) {
  if (!state.partial) return;
  refresh({ full: true, keepPage });
}

/** Taşıyıcı listesi VERİDEN gelir: sabit liste yazmak, yeni taşıyıcı
 *  eklendiğinde satırları süzgeç dışında bırakırdı. Tek ya da hiç taşıyıcı
 *  yoksa kutu çizilmez (`choice.js` — tek cevaplı soru sorulmaz). */
function fillCarriers() {
  const seen = [...new Set(state.items.map((row) => row.carrier).filter(Boolean))].sort();
  applyChoiceFilter(nodes.filters, 'carrier', seen, { allLabel: 'Tümü — taşıyıcı' });
}

/** Süzme İSTEMCİDE: ölçek "yüzler"; sunucuya gitmek gereksiz gecikme olurdu. */
function applyView() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const chip = CHIPS.find((item) => item.key === state.chip);
  const pool = chip ? state.items.filter((row) => chip.statuses.includes(row.status)) : state.items;
  state.filtered = applyFilters(pool, values, FILTER_SPEC);
  const pages = Math.max(1, Math.ceil(state.filtered.length / state.size));
  if (state.page > pages) state.page = pages;
  renderAll();
}

function renderAll() {
  renderKpi();
  renderChips();
  renderTable();
  nodes.pager.update({ total: state.filtered.length, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
}

// ------------------------------------------------------------------- çizim

function renderKpi() {
  if (!nodes.kpi) return;
  const summary = state.summary;
  nodes.kpi.replaceChildren();
  if (state.notice) nodes.kpi.append(alertBox(state.notice, 'bad'));
  if (!summary) return;

  const partial = state.partial ? ' — yalnız yüklü kayıtlardan' : '';
  const rate = summary.refundRate;
  nodes.kpi.append(kpiRow([
    {
      label: 'Bekleyen tutar',
      value: money(summary.pendingAmount),
      tone: summary.pendingCount ? 'warn' : '',
      title: `${num(summary.pendingCount)} açık kayıt — parası henüz çıkmadı${partial}`,
    },
    {
      label: 'Bu ay iade',
      value: money(summary.monthAmount),
      title: `${summary.monthStart} – ${summary.monthEnd} · ${num(summary.monthCount)} iade${partial}`,
    },
    {
      // Oran hesaplanamazsa "—" ve NEDENİ ipucunda; ayrı bilgi kutusu yok.
      // Uydurulmuş bir oran (ör. %0) göstermekten "—" iyidir.
      label: 'İade oranı',
      value: rate === null || rate === undefined ? '—' : percent(rate),
      tone: rate !== null && rate !== undefined && rate >= 10 ? 'bad' : '',
      title: summary.rateNote || `Bu ayın iadesi / bu ayın satışı (${money(summary.salesTotal)})${partial}`,
    },
  ]));

  // Eyleme dönüşen TEK satır: yalnız en yeniler yüklüyse "Tümünü yükle".
  // Uzun açıklama (özetin de bu kayıtlardan hesaplandığı) ipucundadır.
  if (state.partial) {
    const line = h('div', 'rf-partial');
    line.title = state.partialNote;
    line.append(h('span', 'rf-sub', 'Yalnız en yeni kayıtlar yüklü.'),
      button('Tümünü yükle', { variant: 'ghost', onClick: () => loadFull() }));
    nodes.kpi.append(line);
  }
  // Hata satırları KALIR (K7) ama tek satır: ne oldu. Talepler okunamadıysa
  // listenin yalnız kredi notlarını gösterdiği bu satırdan anlaşılır.
  const requests = state.sources.requests;
  if (requests && requests.available === false) {
    nodes.kpi.append(alertBox(`İade talepleri okunamadı — ${requests.error}`, 'warn'));
  }
  for (const warning of state.warnings) {
    if (requests && requests.available === false && warning.includes('İade talepleri')) continue;
    nodes.kpi.append(alertBox(warning, 'warn'));
  }
  if (state.truncated) {
    nodes.kpi.append(alertBox('Liste tavana dayandı; tarih aralığını daraltın.', 'warn'));
  }
}

function renderChips() {
  const counts = {};
  for (const chip of CHIPS) {
    counts[chip.key] = state.items.filter((row) => chip.statuses.includes(row.status)).length;
  }
  nodes.chips.counts(counts);
}

function amountCell(row) {
  const box = h('span', 'rf-amount');
  box.append(h('b', undefined, money(row.amount)));
  // Talep aşamasında tutar TAHMİNDİR; kesin rakam kalem seçilince hesaplanır.
  if (row.source === 'request') box.append(h('span', 'rf-sub', 'beklenen'));
  return box;
}

function statusCell(row) {
  const box = h('span', 'rf-status');
  const info = statusOf(row.status);
  // Renk tek başına anlam taşımaz: rozetin içinde her zaman yazı var.
  box.append(badge(row.statusLabel || info.label, info.tone));
  const marks = row.flags || {};
  if (marks.posFailed) box.append(badge('POS hatası', 'bad'));
  if (marks.itemLate) box.append(badge(`${num(marks.waitingDays)} gündür yolda`, 'warn'));
  if (marks.withinWithdrawal) box.append(badge('cayma süresinde', 'info'));
  return box;
}

//: YEDİ SÜTUN (sade kural §5). Adet · Yöntem · Kargo çekmecenin başına
//: taşındı; CSV'de hâlâ ayrı sütun olarak durur. Kargo sütunu zaten çoğu
//: satırda boştu: liste uçları taşıyıcı/takip numarası vermiyor.
const COLUMNS = [
  { key: 'number', label: 'İade no', width: '84px', className: 'mono' },
  { key: 'orderNumber', label: 'Sipariş', width: '96px', className: 'mono' },
  { key: 'date', label: 'Tarih', width: '96px', sortable: true },
  {
    key: 'customer',
    label: 'Müşteri',
    width: 'minmax(0, 1.8fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'rf-name');
      box.append(clip(h('b'), row.customer || '(isimsiz)', 32));
      if (row.itemNames) box.append(clip(h('span', 'rf-sub'), row.itemNames, 44));
      return box;
    },
  },
  { key: 'reasonLabel', label: 'Sebep', width: '110px',
    cell: (row) => row.reasonLabel || '—' },
  { key: 'amount', label: 'Tutar', width: '116px', align: 'num', sortable: true,
    cell: amountCell },
  { key: 'status', label: 'Durum', width: 'minmax(170px, 1.4fr)', cell: statusCell },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh({ fresh: true }) })],
    });
  }
  if (state.items.length === 0) {
    return emptyState({
      title: 'Bu aralıkta iade yok',
      text: `${state.range.from} – ${state.range.to} · tarih aralığını genişletebilirsiniz.`,
    });
  }
  const actions = [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })];
  if (state.chip) {
    actions.unshift(button('Durum çipini kaldır', {
      onClick: () => { nodes.chips.set(null); state.chip = null; applyView(); },
    }));
  }
  return emptyState({
    title: 'Bu filtreye uyan iade yok',
    text: `${num(state.items.length)} kayıttan hiçbiri uymadı.`,
    actions,
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  const start = (state.page - 1) * state.size;
  const rows = state.filtered.slice(start, start + state.size);

  nodes.table = dataTable({
    columns: COLUMNS,
    rows,
    empty: emptyNode(),
    rowKey: (row) => row.key,
    onRow: (row) => openRefund(row),
  });
  wrap.replaceChildren(nodes.table.node);
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['İade no', 'Sipariş', 'Tarih', 'Müşteri', 'Adet', 'Sebep', 'Tutar',
    'Yöntem', 'Taşıyıcı', 'Takip no', 'Durum'];
  const rows = state.filtered.map((row) => [
    row.number, row.orderNumber, row.date, row.customer, row.itemCount,
    row.reasonLabel || '', money(row.amount), row.methodLabel, row.carrier,
    row.tracking, row.statusLabel,
  ]);
  const written = csvBlob(headers, rows, `iadeler-${todayIso()}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Dosya yazılıyor…', async () => {
    const range = currentRange();
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { dateFrom: range.start || '', dateTo: range.end || '' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ================================================================ çekmece

async function openRefund(row) {
  const box = drawer(nodes.root, {
    title: 'Sipariş yükleniyor…',
    subtitle: `${row.number} · ${row.orderNumber || `#${row.orderId}`}`,
    // Çekmece `document`'e Escape dinleyicisi bağlar ve yalnız `close()` onu
    // söker. Panel çekmece açıkken değiştirilirse dinleyici asılı kalırdı.
    onClose: () => { nodes.drawer = null; },
  });
  nodes.drawer = box;
  box.body.append(skeletonRows(6, 4));

  if (!row.orderId) {
    box.body.replaceChildren(emptyState({
      title: 'Sipariş bağlantısı yok',
      text: 'Bu kayıt bir siparişe bağlı değil; kalem seçimi ve iade hesabı yapılamaz.',
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  // Açık bir talepten gelindiyse talebin kalemleri de istenir: iade adetleri
  // talepteki adetlerle dolar ve tutar kendiliğinden hesaplanır.
  const ask = row.requestId && OPEN_KEYS.has(row.status) ? `?requestId=${row.requestId}` : '';
  let payload;
  try {
    payload = await call(`${BASE}/orders/${row.orderId}${ask}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Sipariş okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  const order = payload.order;
  box.setTitle(`${order.orderNumber || `#${order.orderId}`} · ${order.customer}`);
  box.body.replaceChildren();

  // Listeden taşınan sütunlar (Adet · Yöntem · Kargo) burada, başlıkta durur.
  const head = h('div', 'rf-head');
  const info = statusOf(row.status);
  head.append(
    badge(row.statusLabel || info.label, info.tone),
    badge(row.methodLabel, 'info'),
  );
  if (row.reasonLabel) head.append(badge(row.reasonLabel, ''));
  head.append(
    h('span', 'rf-sub', `Sipariş ${money(order.grandTotal)}`),
    h('span', 'rf-sub', `Daha önce iade edilen ${money(order.totalRefunded)}`),
  );
  if (row.itemCount) head.append(h('span', 'rf-sub', `${num(row.itemCount)} adet`));
  if (row.carrier) head.append(h('span', 'rf-sub', row.carrier));
  if (row.tracking) head.append(h('code', 'rf-code', row.tracking));
  box.body.append(head);

  if (payload.warnings?.length) {
    box.body.append(alertBox(`Bazı parçalar okunamadı — ${payload.warnings.join(' · ')}`, 'warn'));
  }
  if (payload.basisNotice) {
    // Sunucunun iki cümlelik açıklaması ipucunda; ekranda tek satır.
    const notice = alertBox('Faturalanmış adet yok — tahsil edilmemiş kalemi iade etmeyin.', 'warn');
    notice.title = payload.basisNotice;
    box.body.append(notice);
  }

  box.body.append(...[
    refundSection(row, payload, box),
    processSection(row),
    posSection(payload),
    shippingSection(row, payload),
    notifySection(row),
    historySection(payload),
  ].filter(Boolean));
  auditSection(box.body, row.orderId);
}

/** Çekmecedeki tek satırlık açıklama — kutu değil, sönük yazı. */
function note(text, title = '') {
  const line = h('div', 'rf-note', text);
  if (title) line.title = title;
  return line;
}

// ---------------------------------------------------- kalem seçimi + hesap

function refundSection(row, payload, box) {
  const order = payload.order;
  const rma = payload.rma || {};
  const rmaRows = rma.items || [];
  const suggested = rma.suggested || {};
  const hasSuggestion = Object.keys(suggested).length > 0;
  const wanted = new Map();          // kalemId → adet
  for (const [id, qty] of Object.entries(suggested)) wanted.set(Number(id), Number(qty) || 0);

  // Ekrandaki seçimin SON hesabı. Onay YALNIZ bunun jetonunu gönderir; seçim
  // değişince silinir ve düğme yeni hesap gelene kadar kapanır.
  let latest = null;
  let calcSeq = 0;

  const rough = h('div', 'rf-rough');
  const result = h('div', 'rf-result');

  const approveButton = button('İadeyi oluştur…', {
    variant: 'danger',
    title: 'Kredi notu oluşur; para hareketi denetim kaydına yazılır',
    disabled: true,
    onClick: () => approve(),
  });

  const shippingBox = h('input', 'kit-check');
  shippingBox.type = 'checkbox';
  // Geçmiş iadeler okunamadıysa "kargo daha önce verilmiş mi" bilinmiyor:
  // kutu KENDİLİĞİNDEN işaretlenmez, personel bilerek işaretler.
  shippingBox.checked = Boolean(payload.shippingDefault) && payload.shippingVerified !== false
    && order.shippingAvailable > 0;
  shippingBox.disabled = order.shippingAvailable <= 0;

  const extra = h('input', 'kit-input');
  extra.type = 'text';
  extra.placeholder = '0,00';
  extra.style.width = '110px';

  const fee = h('input', 'kit-input');
  fee.type = 'text';
  fee.placeholder = '0,00';
  fee.style.width = '110px';

  // Hesap KENDİLİĞİNDEN gelir — ama her tuşta değil: son değişiklikten 700 ms
  // sonra bir kez. Hesap ucu siparişi tazeler ve mağazaya önizleme sorar.
  const scheduleCalc = debounce(() => runCalc(), 700);

  function selection() {
    const quantities = {};
    let units = 0;
    for (const [id, qty] of wanted) {
      if (qty > 0) {
        quantities[String(id)] = qty;
        units += qty;
      }
    }
    return { quantities, units };
  }

  function paintRough() {
    // YAKLAŞIK: indirim ve KDV payları burada hesaplanmaz. İkinci bir aritmetik
    // yazmak, iki sayı arasında sessiz fark üretirdi; kesin tutar sunucudan
    // gelir ve etiket bunu açıkça söyler.
    let units = 0;
    let approx = 0;
    for (const line of order.lines) {
      const qty = wanted.get(line.id) || 0;
      units += qty;
      approx += qty * line.unitPrice;
    }
    rough.replaceChildren();
    if (!units) {
      rough.append(h('span', 'rf-sub', 'Kalem seçilmedi.'));
      return;
    }
    rough.append(
      h('b', undefined, `${num(units)} adet seçildi`),
      h('span', 'rf-sub', ` · yaklaşık ${money(approx)} (KDV ve indirim payı hariç)`),
    );
  }

  /** Seçim değişti: eski hesap ve jeton geçersiz, yenisi bekletilerek istenir. */
  function changed() {
    latest = null;
    calcSeq += 1;                    // yoldaki eski hesap ekrana yazılmasın
    approveButton.disabled = true;
    result.replaceChildren();
    paintRough();
    const { units } = selection();
    if (units > 0 || shippingBox.checked || extra.value.trim()) {
      rough.append(h('span', 'rf-sub', ' · hesaplanıyor…'));
      scheduleCalc();
    } else {
      scheduleCalc.cancel();
    }
  }

  const table = dataTable({
    columns: [
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2.2fr)' },
      { key: 'unitPrice', label: 'Birim', width: '110px', align: 'num',
        cell: (line) => money(line.unitPrice) },
      { key: 'qtyOrdered', label: 'Sipariş', width: '80px', align: 'num',
        cell: (line) => num(line.qtyOrdered) },
      { key: 'qtyRefunded', label: 'İade edilen', width: '96px', align: 'num',
        cell: (line) => num(line.qtyRefunded) },
      {
        key: 'refundable',
        label: 'İade edilecek',
        width: '150px',
        align: 'num',
        cell: (line) => qtyPicker(line),
      },
    ],
    rows: order.lines,
    dense: true,
    rowKey: (line) => String(line.id),
    empty: emptyState({ title: 'Kalem yok', text: 'Bu siparişte iade edilecek kalem yok.' }),
  });

  function qtyPicker(line) {
    const wrap = h('div', 'rf-qty');
    if (line.refundable <= 0) {
      // Faturalanmamış ya da tamamı iade edilmiş kalem: kutu YOK, sebebi yazılı.
      wrap.append(h('span', 'rf-sub',
        line.qtyInvoiced === 0 ? 'faturalanmadı' : 'tamamı iade edildi'));
      return wrap;
    }
    const input = h('input', 'kit-input');
    input.type = 'number';
    input.min = '0';
    input.max = String(line.refundable);
    input.value = String(Math.min(line.refundable, wanted.get(line.id) || 0));
    input.style.width = '70px';
    input.setAttribute('aria-label', `${line.name} iade adedi`);
    input.addEventListener('input', () => {
      const value = Math.max(0, Math.min(line.refundable, Number(input.value) || 0));
      if (String(value) !== input.value) input.value = String(value);
      wanted.set(line.id, value);
      changed();
    });
    wrap.append(input, h('span', 'rf-sub', `/ ${num(line.refundable)}`));
    return wrap;
  }

  shippingBox.addEventListener('change', changed);
  extra.addEventListener('input', changed);
  fee.addEventListener('input', changed);

  const controls = h('div', 'rf-controls');
  const shipLabel = h('label', 'rf-check');
  shipLabel.append(shippingBox, h('span', undefined,
    order.shippingAvailable > 0
      ? `Kargo bedelini de iade et (${money(order.shippingAvailable)})`
      : 'Kargo bedeli zaten iade edilmiş'));
  controls.append(shipLabel, labelled('Ek iade', extra), labelled('Kesinti', fee));

  async function runCalc() {
    if (!box.body.isConnected) return;          // çekmece kapandı
    const { quantities, units } = selection();
    const extraKurus = extra.value.trim() === '' ? 0 : parseMoney(extra.value);
    const feeKurus = fee.value.trim() === '' ? 0 : parseMoney(fee.value);
    if (extraKurus === null || feeKurus === null) {
      paintRough();
      result.replaceChildren(alertBox('Ek iade / kesinti tutarını 25,00 gibi yazın.', 'bad'));
      return;
    }
    if (!units && !shippingBox.checked && !extraKurus) {
      paintRough();                             // "hesaplanıyor…" asılı kalmasın
      return;
    }
    const mine = ++calcSeq;
    result.replaceChildren(skeletonRows(4, 3));
    let data;
    try {
      data = await call(`${BASE}/calculate`, {
        method: 'POST',
        body: {
          orderId: order.orderId,
          quantities,
          refundShipping: shippingBox.checked,
          adjustmentRefund: extraKurus,
          adjustmentFee: feeKurus,
        },
      });
    } catch (error) {
      if (mine !== calcSeq) return;
      paintRough();
      result.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    if (mine !== calcSeq) return;               // bu arada seçim değişti
    paintRough();
    paintResult(data);
  }

  function paintResult(data) {
    const detail = data.calc;
    result.replaceChildren();

    if (rma.available === false) {
      result.append(alertBox(
        'Bu siparişin RMA kayıtları okunamadı; RMA kontrolü tamamlanmadan kredi notu oluşturulamaz.',
        'bad'));
    } else if (rmaRows.length) {
      result.append(alertBox(
        `Bu siparişte RMA ${rmaRows.map((item) =>
          `#${item.requestId}${item.statusLabel ? ` · ${item.statusLabel}` : ''}`).join(', ')} var; `
        + 'onaylarken çifte iade kontrolünü yapın.', 'warn'));
    }

    const lines = h('div', 'rf-lines');
    for (const line of detail.lines) {
      lines.append(breakdownRow(
        `${line.sku} · ${line.name}`,
        `${num(line.qty)} × ${money(line.unitPrice)}`,
        line.total,
      ));
    }
    lines.append(breakdownRow('Ürün tutarı', '', detail.base, 'sum'));
    if (detail.discount) lines.append(breakdownRow('İndirim payı', 'düşülür', -detail.discount));
    lines.append(breakdownRow('KDV payı', 'eklenir', detail.tax));
    // Kargo hariçken "iade edilebilir" rakamı ÇEKMECENİN doğrulanmış değeridir:
    // hesap ucu, kutu işaretli değilken geçmiş iadeleri sormaz (istek kovası)
    // ve oradaki rakam daha önce iade edilmiş kargoyu düşmemiş olabilir.
    lines.append(breakdownRow(
      'Kargo iadesi',
      detail.shippingIncluded
        ? 'dahil'
        : `HARİÇ (iade edilebilir ${money(order.shippingAvailable)})`,
      detail.shipping,
    ));
    if (detail.adjustmentRefund) lines.append(breakdownRow('Ek iade', '', detail.adjustmentRefund));
    if (detail.adjustmentFee) lines.append(breakdownRow('Kesinti', 'düşülür', -detail.adjustmentFee));
    lines.append(breakdownRow('İADE TOPLAMI', `${num(detail.unitCount)} adet`, detail.total,
      'total'));
    result.append(card('İade tutarı — satır satır', lines));

    if (detail.clamped?.length) {
      result.append(alertBox(
        `Şu kalemlerde istenen adet iade edilebilir adedi aşıyordu ve düşürüldü: `
        + `${detail.clamped.join(', ')}.`, 'warn'));
    }
    // Sunucunun doğrulayamadığı şeyler (ör. daha önce iade edilmiş kargo)
    // burada yazar: para ekranında "sanırım" susarak geçilmez.
    for (const warning of data.warnings || []) result.append(alertBox(warning, 'warn'));
    // Mağaza karşılaştırması TEK satır; sunucunun uzun cümlesi ipucunda.
    // Üç cevabın üçü de yazılır: uyuşuyor · uyuşmuyor · sorulamadı.
    const verdict = data.store;
    let verdictLine;
    if (verdict.matches === true) {
      verdictLine = alertBox('Mağaza aynı tutarı hesapladı.', 'good');
    } else if (verdict.matches === false) {
      verdictLine = alertBox(
        `Mağaza ${money(verdict.storeTotal)} hesapladı, biz ${money(detail.total)} `
        + `(fark ${money(Math.abs(verdict.diff))}) — farkı çözmeden onaylamayın.`, 'bad');
    } else {
      verdictLine = alertBox('Mağaza önizlemesi alınamadı; tutar Kontrol Merkezi hesabıdır.',
        'warn');
    }
    verdictLine.title = verdict.message;
    result.append(verdictLine);

    latest = data;
    approveButton.disabled = rma.available === false;
    // Onay EKRANDA GÖRÜLEN hesabı (jetonu) gönderir; tutar yeniden hesaplanmaz.
    approveButton.title = 'Ekrandaki hesap gönderilir; kredi notu oluşur ve banka iadesi '
      + 'otomatik başlar';
    const actions = h('div', 'rf-actions');
    actions.append(approveButton);
    result.append(actions);
  }

  async function approve() {
    const data = latest;
    if (!data) return;
    if (rma.available === false) {
      toast('RMA kayıtları doğrulanmadan kredi notu oluşturulamaz.', 'bad');
      return;
    }
    const hasRma = rmaRows.length > 0;
    const ids = rmaRows.map((item) => `#${item.requestId}`).join(', ');
    // RMA onayı AYNI pencerededir: uyarı gerekçenin üstünde durur ve onay
    // düğmesi kontrolün yapıldığını söyler. Backend yine RMA kimliklerinin
    // açıkça gönderilmesini ister — kontrol atlanmadı, ikinci pencere kalktı.
    const reason = await askReason({
      title: 'İadeyi onayla',
      description: `${data.order.number || `#${data.orderId}`} · ${money(data.calc.total)} iade `
        + `edilecek (${num(data.calc.unitCount)} adet, kargo `
        + `${data.calc.shippingIncluded ? 'dahil' : 'hariç'}). Kredi notu oluşur ve banka `
        + 'iadesi otomatik başlar; geri alınamaz.',
      warning: hasRma
        ? `Bu siparişte RMA ${ids} var. RMA akışı bankaya ayrıca iade başlatmış olabilir; `
          + 'çifte iade olmadığını kontrol edin.'
        : '',
      confirmLabel: hasRma ? 'Kontrol ettim, iadeyi oluştur' : 'İadeyi oluştur',
      chips: REFUND_CHIPS,
    });
    if (!reason || latest !== data) return;
    await withBusy('İade oluşturuluyor…', async () => {
      const done = await call(`${BASE}/approve`, {
        method: 'POST', body: { token: data.token, reason, dryRun: false,
          rmaAcknowledgedIds: hasRma ? rmaRows.map((item) => item.requestId) : [] },
      });
      toast(done.dryRun ? 'Kuru prova: istek gönderilmedi.' : `${money(done.total)} iade oluştu.`,
        done.dryRun ? 'warn' : 'good');
      if (done.notice) toast(done.notice, 'warn');
      // Para çıktıktan SONRA oluşan yerel kayıt hatası: kullanıcı aynı hesabı
      // ikinci kez onaylamasın diye toast değil, kalıcı uyarı olarak durur.
      if (done.warning) {
        toast(done.warning, 'bad');
        state.notice = done.warning;      // `refresh()` KPI'ı yeniden çizer, uyarı kalır
      }
      box.close();
      refresh();                          // sunucu önbelleği yazmayla boşaldı
    });
  }

  paintRough();
  const body = h('div', 'rf-pane');
  body.append(table.node, rough, controls, result);
  // Talepten gelen öneri varsa tutar açılışta kendiliğinden hesaplanır.
  if (hasSuggestion) changed();
  return card('İade edilecek kalemler', body,
    hasSuggestion
      ? `Adetler RMA #${rma.suggestedFrom} talebinden dolduruldu — kontrol edin`
      : 'Faturalanmamış kalem iade edilemez');
}

function breakdownRow(label, note, amount, kind = '') {
  const line = h('div', `rf-line${kind ? ` ${kind}` : ''}`);
  line.append(h('span', 'rf-line-label', label));
  line.append(h('span', 'rf-sub', note || ''));
  const value = h('b', 'rf-line-value');
  value.textContent = money(amount);
  if (amount < 0) value.classList.add('minus');
  line.append(value);
  return line;
}

function labelled(label, control) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), control);
  return wrap;
}

// ------------------------------------------------------------------ süreç

function processSection(row) {
  // Talebi olmayan kayıt (doğrudan kesilmiş kredi notu): süreç adımı yok,
  // bölüm de çizilmez — boş bir bölümü açıklamak yerine hiç göstermemek.
  if (!row.requestId) return null;
  const body = h('div', 'rf-pane');

  const actions = h('div', 'rf-actions');
  // "İade edildi" buradan YAZILMAZ: o durum ancak para gerçekten geri
  // verildiğinde, onay adımıyla oluşur.
  actions.title = '"İade edildi" durumu yalnız iade oluşturulunca yazılır';
  for (const step of FLOW) {
    actions.append(button(step.label, {
      variant: step.key === 'rejected' ? 'danger' : '',
      onClick: async () => {
        const reason = await askReason({
          title: `Talebi "${step.label}" durumuna al`,
          description: `${row.number} talebinin durumu değişecek ve müşteri panelinde `
            + 'görünecek. Gerekçe denetim kaydına yazılır.',
          confirmLabel: step.label,
          chips: FLOW_CHIPS[step.key] || [],
        });
        if (!reason) return;
        await withBusy('Durum yazılıyor…', async () => {
          const done = await call(`${BASE}/requests/${row.requestId}/status`, {
            method: 'POST', body: { status: step.key, reason, dryRun: false },
          });
          toast(`Durum: ${done.statusLabel}`, 'good');
          refresh();
        });
      },
    }));
  }
  body.append(actions);
  return card('Talep süreci', body);
}

// ------------------------------------------------------------------- POS

function posSection(payload) {
  const body = h('div', 'rf-pane');
  const pos = payload.payments;
  // AYRI POS İADESİ YOKTUR: kredi notu mağazada bankaya iadeyi kendisi
  // başlatır. Düğme çizilmez (kit kuralı: düğme ya çalışır ya hiç çizilmez);
  // hangi karta ne çekildiğini gösteren liste kalır.
  const line = note(pos.note || POS_NOTE, pos.refundReason || '');

  if (!pos.available) {
    body.append(alertBox(`POS işlemleri okunamadı — ${pos.error}`, 'warn'), line);
    return card('POS işlemleri', body);
  }
  if (!pos.items.length) {
    body.append(note('Bu siparişte sanal POS işlemi yok.',
      'Para havaleyle ya da kapıda alınmış olabilir; iade yolunu muhasebe belirler.'));
    return card('POS işlemleri', body);
  }

  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '128px',
        cell: (item) => (item.createdAt || '').slice(0, 16) },
      { key: 'bank', label: 'POS', width: 'minmax(70px, 1fr)' },
      { key: 'card', label: 'Kart', width: '136px', className: 'mono',
        cell: (item) => item.card || '—' },
      { key: 'status', label: 'Sonuç', width: '96px' },
      { key: 'amount', label: 'Çekilen', width: '92px', align: 'num',
        cell: (item) => money(item.amount) },
      { key: 'refunded', label: 'İade edilen', width: '92px', align: 'num',
        cell: (item) => money(item.refunded) },
    ],
    rows: pos.items,
    dense: true,
    rowKey: (item) => String(item.id),
  });
  body.append(table.node, line);
  return card('POS işlemleri', body);
}

// ----------------------------------------------------------------- kargo

function returnBadge(item) {
  const box = h('span', 'rf-ship-action');
  box.append(badge(item.purchased ? 'İade etiketi alındı' : 'İade taslağı',
    item.purchased ? 'good' : 'warn'));
  if (item.tracking) box.append(h('code', 'rf-code', item.tracking));
  return box;
}

function shippingSection(row, payload) {
  const body = h('div', 'rf-pane');
  const box = payload.shipments;
  const title = 'Ürünü geri getir';

  if (!box.available) {
    body.append(alertBox(`Gönderiler okunamadı — ${box.error}`, 'warn'));
    return card(title, body);
  }
  const outbound = box.items.filter((item) => !item.isReturn);
  const returns = box.items.filter((item) => item.isReturn);
  if (!outbound.length && !returns.length) {
    body.append(note('Bu siparişin kargo kaydı yok; iade etiketi alınamaz.'));
    return card(title, body);
  }

  // Açık bir RMA varsa iade gönderisi O TALEBE bağlanır: "ürün geldi" bilgisi
  // mağazada talebe düşer. Talep mağazada "Onaylandı" olmalıdır.
  const rmaRows = payload.rma?.items || [];
  const openRma = rmaRows.find((item) => OPEN_KEYS.has(item.status)
    && (!row.requestId || item.requestId === row.requestId));
  const rmaId = openRma ? openRma.requestId : 0;

  async function buyReturn(item, slot) {
    const reason = await askReason({
      title: 'İade etiketi al',
      description: `${item.carrier || 'Kargo'} ile müşteriden geri alım gönderisi açılır ve `
        + 'iade etiketi SATIN ALINIR; kargo ücreti mağazaya yansır. Takip numarası burada görünür.',
      warning: rmaId ? `Gönderi RMA #${rmaId} talebine bağlanır; talep mağazada "Onaylandı" `
        + 'durumunda olmalı.' : '',
      confirmLabel: 'Etiketi satın al',
      chips: SHIP_CHIPS,
    });
    if (!reason) return;
    await withBusy('İade etiketi alınıyor…', async () => {
      const done = await call(`${BASE}/orders/${row.orderId}/return-shipment`, {
        method: 'POST',
        body: { shipmentId: item.id, requestId: rmaId, reason, dryRun: false },
      });
      if (done.purchased) {
        toast(`İade etiketi alındı${done.tracking ? ` · Takip: ${done.tracking}` : ''}.`, 'good');
        slot.replaceChildren(returnBadge({ purchased: true, tracking: done.tracking }));
        nodes.status?.set(statusText());
      } else {
        // Etiket alınmadıysa ekran "alındı" DEMEZ (taslak, kuru prova…).
        toast(done.message || 'İade gönderisi açıldı; etiket satın alınmadı.', 'warn');
      }
    });
  }

  const list = h('div', 'rf-lines');
  for (const item of outbound) {
    const line = h('div', 'rf-line');
    line.append(
      h('b', 'rf-line-label', item.carrier || 'Kargo'),
      h('code', 'rf-code', item.tracking || '—'),
      h('span', 'rf-sub', item.status || ''),
    );
    const done = returns.find((ret) => ret.parentId === item.id && ret.purchased);
    let slot = h('span', 'rf-ship-action');
    if (done) {
      slot = returnBadge(done);
    } else if (!item.purchased) {
      // Mağaza kuralı: iade gönderisi yalnız etiketi alınmış gönderinin üzerine açılır.
      slot.append(h('span', 'rf-sub', 'Giden etiketi alınmamış; iade açılamaz'));
    } else {
      const target = slot;
      slot.append(button('İade etiketi al', {
        variant: 'primary',
        title: 'Etiket satın alınır; ücret mağazaya yansır',
        onClick: () => buyReturn(item, target),
      }));
    }
    line.append(slot);
    list.append(line);
  }
  for (const item of returns) {
    if (item.purchased && outbound.some((out) => out.id === item.parentId)) continue;
    const line = h('div', 'rf-line');
    line.append(h('b', 'rf-line-label', `İade · ${item.carrier || 'Kargo'}`),
      h('span', 'rf-sub', item.status || ''), returnBadge(item));
    list.append(line);
  }
  body.append(list);
  return card(title, body);
}

// -------------------------------------------------------------- bildirim

function notifySection(row) {
  const body = h('div', 'rf-pane');
  const area = h('textarea', 'kit-textarea');
  area.rows = 3;
  area.maxLength = 2000;
  area.placeholder = 'Müşteriye gidecek metin — iade durumu, tutar, ne zaman hesabına geçer…';

  // Şablonlu bildirimler Bildirimler ekranındadır; aynı metnin iki yerde
  // yaşamaması için burada şablon tutulmaz.
  const actions = h('div', 'rf-actions');
  actions.append(button('Müşteriye gönder', {
    variant: 'primary',
    title: 'Metin siparişe not düşer ve müşteriye e-posta gider',
    onClick: async () => {
      const message = area.value.trim();
      if (message.length < 10) { toast('Mesaj en az 10 karakter olmalı.', 'bad'); return; }
      const reason = await askReason({
        title: 'Müşteriye bildir',
        description: 'Metin siparişe not olarak düşer ve müşteriye e-posta gönderilir.',
        confirmLabel: 'Gönder',
        chips: NOTIFY_CHIPS,
      });
      if (!reason) return;
      await withBusy('Bildirim gönderiliyor…', async () => {
        await call(`${BASE}/orders/${row.orderId}/notify`, {
          method: 'POST', body: { message, reason, dryRun: false },
        });
        toast('Müşteriye gönderildi.', 'good');
        area.value = '';
      });
    },
  }));

  body.append(area, actions);
  return card('Müşteri bildirimi', body);
}

// ---------------------------------------------------------------- geçmiş

function historySection(payload) {
  const body = h('div', 'rf-pane');
  if (!payload.history.length) {
    body.append(note('Bu siparişte daha önce kesilmiş kredi notu yok.'));
    return card('Bu siparişin önceki iadeleri', body);
  }
  const table = dataTable({
    columns: [
      { key: 'number', label: 'İade no', width: '120px', className: 'mono' },
      { key: 'date', label: 'Tarih', width: '110px' },
      { key: 'itemCount', label: 'Kalem', width: '80px', align: 'num' },
      { key: 'amount', label: 'Tutar', width: '130px', align: 'num',
        cell: (item) => money(item.amount) },
    ],
    rows: payload.history,
    dense: true,
    rowKey: (item) => item.key,
  });
  body.append(table.node);
  return card('Bu siparişin önceki iadeleri', body);
}

async function auditSection(host, orderId) {
  // Bu iz YERELDİR ve gerekçeyi tutar: mağazanın denetim kaydında "neden iade
  // edildi" alanı yok; ağ koparsa ne yapmaya çalıştığımız yalnız burada kalır.
  const body = h('div', 'rf-pane');
  const box = card('Bu ekrandan yapılan işlemler', body);
  box.title = 'Gerekçeleriyle birlikte bu ekrandan yapılan işlemler';
  host.append(box);
  body.append(skeletonRows(3, 3));

  let result;
  try {
    result = await call(`${BASE}/audit?orderId=${orderId}&limit=50`);
  } catch (error) {
    body.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  body.replaceChildren();
  if (!result.items.length) {
    body.append(note('Bu siparişe buradan henüz işlem yapılmadı.'));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'action', label: 'İşlem', width: '140px' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (item) => `${item.createdAt}-${item.action}`,
  });
  body.append(table.node);
}

// ================================================================== mount

/** Koyu temada kâğıt da koyulaşır: ekran yalnız kitin jetonlarını kullanır (ADR 0032, 63-duzeltici). */
export const darkReady = true;

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel rf');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  let lastRange = '';

  // SADE KİP (docs/tasarim-sade-magaza.md §1-2): görünür süzgeç yalnız arama
  // ve tarih; Sebep/Yöntem/Taşıyıcı/Tutar ve üç anahtar "Filtreler" arkasında
  // katlanır (dolu olanı düğme sayar). Dışa aktarma üç seçenekli tek menüde.
  nodes.filters = filterBar({
    compact: true,
    fields: [
      { kind: 'search', key: 'q', placeholder: 'İade no, sipariş no ya da müşteri ara',
        width: '240px', primary: true },
      // "Bugün"/"Dün" çipleri yok: iade haftalar içinde işlenir ve şerit dar
      // pencerede taşıp "Dışa aktar" menüsünü alt satıra itiyordu.
      { kind: 'dateRange', key: 'range', label: 'Tarih', start: todayIso(-89), end: todayIso(),
        presets: RANGE_PRESETS.filter((preset) => preset.days >= 7), primary: true },
      { kind: 'select', key: 'reason', label: 'Sebep', options: [
        { value: '', label: 'Tümü — sebep' }, ...REASONS] },
      { kind: 'select', key: 'method', label: 'Yöntem', options: [
        { value: '', label: 'Tümü — yöntem' }, ...METHODS] },
      { kind: 'select', key: 'carrier', label: 'Taşıyıcı', options: [
        { value: '', label: 'Tümü — taşıyıcı' }] },
      { kind: 'numRange', key: 'amount', label: 'Tutar', money: true },
      { kind: 'toggle', key: 'withdrawal', label: 'Cayma süresi içinde' },
      { kind: 'toggle', key: 'posFailed', label: 'POS iadesi başarısız' },
      { kind: 'toggle', key: 'late', label: 'Ürün gelmedi' },
    ],
    onChange: (values) => {
      // Tarih aralığı SUNUCU penceresidir (yeniden çekilir); gerisi istemcide
      // süzülür. İkisini ayırmamak her tuşta mağazaya gitmek olurdu.
      const stamp = `${values.range?.start || ''}|${values.range?.end || ''}`;
      if (stamp !== lastRange) {
        lastRange = stamp;
        // Yeni pencere: önce ilk sayfa; süzgeç ya da çip açıksa doğrudan tamamı.
        if (state.loaded) { refresh({ full: hasActiveFilter(values) || Boolean(state.chip) }); return; }
      }
      if (!state.loaded) return;
      // İlk sayfada süzmek EKSİK sonuç verir; süzgeç dokunulunca tamamı istenir.
      if (state.partial && hasActiveFilter(values)) { loadFull(); return; }
      applyView();
    },
    actions: [
      button('Yenile', {
        variant: 'ghost',
        title: 'Mağazadaki güncel iadeleri yeniden okur',
        onClick: () => refresh({ fresh: true }),
      }),
      menuButton('Dışa aktar', [
        { label: 'Görünen — Excel',
          title: 'Ekranda görünen listeyi Excel ile açılan CSV olarak indirir',
          onClick: exportVisible },
        { label: 'Tümü — Excel',
          title: 'Tarih aralığının tamamını rapor klasörüne CSV olarak yazar',
          onClick: exportAll },
        { label: 'İade icmali — PDF',
          title: 'Tarih aralığının yazdırılabilir özetini hazırlar',
          onClick: () => {
            const range = currentRange();
            report.run('summary', { dateFrom: range.start || '', dateTo: range.end || '' });
          } },
      ], { title: 'Excel ya da yazdırılabilir PDF olarak indir' }),
    ],
  });
  lastRange = `${nodes.filters.values().range?.start || ''}|${nodes.filters.values().range?.end || ''}`;

  nodes.chips = chipRow(
    CHIPS.map((item) => ({ key: item.key, label: item.label, count: 0 })),
    null,
    (key) => {
      state.chip = key;
      state.page = 1;
      // Çip sayaçları da ilk sayfadan sayılır: çip seçilince tamamı istenir.
      if (key && state.partial) { loadFull(); return; }
      applyView();
    },
  );

  nodes.status = statusLine();
  nodes.kpi = h('div', 'rf-kpi');
  nodes.tableWrap = h('div', 'rf-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => {
      state.page = page;
      state.size = size;
      renderTable();
      // Yüklü kayıtların son sayfasına gelindi: devamı istenir (sayfa korunur).
      if (state.partial && page * size >= state.filtered.length) loadFull({ keepPage: true });
    },
  });

  nodes.body = h('div', 'rf-body');
  nodes.body.append(nodes.kpi, nodes.tableWrap, nodes.pager.node);
  view.append(nodes.filters.node, nodes.chips.node, nodes.status.node, nodes.body);

  root.replaceChildren(view);
  nodes.status.set('İadeler alınıyor…');
  refresh({ full: false });

  return () => {
    nodes.filters?.destroy();     // tarih alanları ve arama debounce'u global dinleyici tutar
    nodes.drawer?.close();        // açık çekmecenin Escape dinleyicisi document'te
    nodes.drawer = null;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
    loadSeq += 1;                 // yoldaki yanıt sökülmüş panele yazmasın
  };
}
