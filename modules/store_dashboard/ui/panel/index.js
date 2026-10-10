// Kontrol Paneli — mağazanın canlı özeti ve dağıtılmış mağaza ayarları.
//
// SADE EKRAN (docs/tasarim-sade-magaza.md — bağlayıcı). İki sekme:
//
//  · Pano: tarih aralığı + "Yenile" + "Dışa aktar" menüsü; dört KPI (Ciro,
//    Sipariş, Bekleyen sipariş, Kargolanmayan — son ikisi Siparişler / Kargo
//    Yönetimi ekranına kısayol) ve altında sessiz bir satır (sepet, yeni
//    müşteri, iade); altı kart: günlük ciro, sipariş dağılımı (durum + saat),
//    en çok satan, son siparişler, kritik stok, bekleyen işler. Her kart
//    başlığındaki `→` ilgili (YAŞAYAN) ekrana götürür.
//  · Ayarlar: alt başlıklar — Genel (çalışma kanalı/dil, yerelleştirme, rapor
//    klasörü; mağazada anahtarı bulunan kimlik/SEO/bakım alanları), "Ödeme,
//    kargo ve mağaza" (eski Yapılandırma sekmesi: `core_config` beyaz listesi,
//    kaynak rozeti, "varsayılana dön", arama, sabit kaydet çubuğu) ve Sistem
//    durumu (eski Pano kartı).
//
// NE YAPMAZ:
//  · Kendi kendine yenilenmez. Otomatik yenileme, kullanıcı bir sayıyı
//    okurken altından veriyi değiştirir ve "az önce farklı yazıyordu"
//    şüphesi üretir. Yenileme elle yapılır ve ne zaman yenilendiği yazar.
//    TEK İSTİSNA: açılışta/süzgeç değişince istenen özet arka planda hâlâ
//    kuruluyorsa, eldeki özet "tazeleniyor" notuyla gösterilir ve yenisi
//    gelince BİR KEZ yerine konur. Periyodik yenileme yoktur.
//  · Rakamları iki kaynaktan almaz. Sipariş KPI'ları da grafikler de AYNI
//    sipariş taramasından çıkar. Tek istisna "Yeni müşteri": sipariş
//    listesinde kayıt tarihi yok; sayı mağazanın toplu sayımından gelir
//    (eskiden her açılışta ~36 sayfalık tam müşteri taraması demekti).
//  · Özeti beklemez. Özet arka planda kurulur; ekran son bilinen özeti yaşıyla
//    gösterir, hiç yoksa "hesaplanıyor" der ve kısa aralıkla yeniden sorar.
//    Yoklama mağazaya gitmez — süren hesabın sonucunu okur.
//  · Kapatılmış ekranlara götüren düğme bırakmaz. Raporlar, Yedekleme ve
//    UDİT ekranları menüden kaldırıldı; onlara giden `→` düğmeleri de
//    kaldırıldı (tıklanınca hiçbir şey yapmayan düğme kitin kuralına aykırı).
//  · Sipariş/ürün DÜZENLEMEZ. Pano okur ve yol gösterir; işlem kendi
//    ekranında yapılır.
//  · Mağazanın 345 ayarını dökmez (2026-08-16 ölçümü; bir dönem 344'tü —
//    alan sayısı mağazaya eklenti girdikçe kayar). Yapılandırma sekmesi beyaz
//    listedir; sanal POS parolası, kargo API belirteci gibi sır taşıyan
//    alanlar ekrana HİÇ GELMEZ.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · `/api/admin/bbd/*` uçları YAYINDA (canlıda ölçüldü 2026-08-16). Burada
//    bir dönem "mağazada YAZILMAKTADIR" yazıyordu ve kartların "uç hazır
//    olunca açılacak" dediği söyleniyordu; artık öyle değil — yedek, POS,
//    kargo ve BLD kartları doluyor, kritik stok ise 2026-08-14'te Bagisto'nun
//    kendi `dashboard/stats` ucuna taşındı. Okunamama dalı yine de duruyor
//    (K7): uç geri çekilirse kart boş kalır, sıfır GÖSTERMEZ — sıfır
//    göstermek olmayan bir sağlığı var göstermek olurdu.
//  · Mağaza tarih süzgecini yok sayabiliyor; sunucu sonucu yerelde süzüyor ve
//    bunu bir not olarak gönderiyor. Not ekranda AYNEN gösterilir.
//  · İptal edilen sipariş ciroya girmez ama durum dağılımında görünür; bu
//    yüzden çubuktaki toplam ile "Sipariş" KPI'sı bilerek farklıdır ve kart
//    ipucu bunu söyler.
//  · Bagisto bir ayarın `core_config` satırı olup olmadığını API'den
//    söylemiyor. Kaynak rozeti bu yüzden "satır var mı"yı değil "değer
//    varsayılandan farklı mı"yı anlatır; ipucu kutusu bunu aynen yazar.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_dashboard/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_dashboard/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, confirmSimple, confirmWithReason, debounce, foldText, h, loadStyles, menuButton,
  money, num, todayIso, toaster,
} from '../../ui-kit/kit.js';
import { applyChoiceFilter } from '../../ui-kit/choice.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, emptyState, kpiRow, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { barChart, hourStrip, lineChart } from '../../ui-kit/charts.js';
import { fillDays } from '../../ui-kit/util.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_dashboard';

const COMPARE_OPTIONS = [
  { value: 'previous', label: 'Önceki dönem' },
  { value: 'lastYear', label: 'Geçen yıl aynı dönem' },
  { value: 'none', label: 'Karşılaştırma yok' },
];

const COMPARE_LABELS = {
  previous: 'önceki dönem', lastYear: 'geçen yıl aynı dönem', none: '',
};

//: Sağlık kartı durumları. Renk TEK BAŞINA anlam taşımaz: her satırda yazı var.
const STATE_TONES = { good: 'good', warn: 'warn', bad: 'bad', unknown: 'dim' };
const STATE_LABELS = { good: 'iyi', warn: 'dikkat', bad: 'sorun', unknown: 'bilinmiyor' };

//: Kaynak rozeti. Metni backend'in `source` alanı belirler; rozetin yanında
//: her zaman yazı durur (renk tek başına anlam taşımaz).
const SOURCE_BADGES = {
  stored: { label: 'veritabanından', tone: 'info',
    title: 'Etkin değer mağazanın ilan ettiği varsayılandan farklı: mağazada saklanıyor.' },
  default: { label: 'varsayılan', tone: 'dim',
    title: 'Etkin değer ilan edilen varsayılanla aynı.' },
  unset: { label: 'tanımsız', tone: 'dim',
    title: 'Ne mağazada bir değer var ne de ilan edilmiş bir varsayılan.' },
};

const EMPTY_STATE = { summary: null, loadedAt: '', fresh: false, pending: false };

//: Üst şeritteki dört KPI (docs/tasarim-sade-magaza.md §6) ve altındaki sessiz
//: satır. Sıra ekrandaki sıradır.
const PRIMARY_KPIS = ['revenue', 'orders', 'pending', 'unshipped'];
const MINOR_KPIS = ['basket', 'customers', 'refunds'];

//: Kısayol kutuları → işin yapıldığı ekran. YALNIZ yaşayan ekranlar (menüdeki
//: yedi ekran); kapatılmış ekrana giden kutu boş ekran açardı.
const KPI_TARGETS = {
  pending: { panel: 'store_orders', label: 'Siparişler' },
  unshipped: { panel: 'store_shipping', label: 'Kargo Yönetimi' },
};

/**
 * Yapılandırma sekmesinin boş durumu — HER ÇAĞRIDA YENİ nesne.
 *
 * Sabit bir nesneyi `{...SABIT}` ile kopyalamak `rows` (Map) ve `cards`
 * (dizi) alanlarının REFERANSINI paylaştırırdı: sekme ikinci kez açıldığında
 * önceki açılışın satırları hâlâ içinde olurdu ve arama, ekranda olmayan
 * düğümleri gizlemeye çalışırdı.
 */
const emptyConfig = () => ({ payload: null, base: {}, draft: {}, rows: new Map(), cards: [] });

let api = null;
let open = null;
let capability = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };
let config = emptyConfig();
let configFilter = null;
let mounted = false;

//: Özet arka planda hesaplanırken yoklama zamanlayıcısı. Yoklama mağazaya
//: GİTMEZ: sunucu süren hesabı yeniden başlatmaz, yalnız sonucunu verir.
let summaryTimer = null;
let summaryPolls = 0;
const SUMMARY_POLL_MS = 5_000;
//: ~2 dakika. Hesap bundan uzun sürüyorsa kart "Tekrar dene" der; yoklama
//: süresiz dönmez.
const SUMMARY_POLL_LIMIT = 24;

const nodes = {};
const forms = [];

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

/** Yıkıcı ve mağazayı etkileyen her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function dropForms() {
  forms.forEach((form) => form.destroy());
  forms.length = 0;
}

/** Kit kartı + başlıkta hedef ekrana götüren `→` düğmesi. */
function panelCard(title, content, { hint = '', target = '', payload = null,
  targetLabel = '' } = {}) {
  const box = card(title, content, hint);
  if (target) {
    const head = box.querySelector('.kit-card-head');
    head?.append(h('span', 'kit-spacer'), button('→', {
      variant: 'ghost',
      title: targetLabel || 'İlgili ekrana git',
      onClick: () => open(target, payload),
    }));
  }
  return box;
}

/**
 * Aralık çipleri. Kitin hazır listesine "Bu ay" ve "Geçen ay" eklenir.
 *
 * Kit çipi `days` + `offset` ile çalışıyor (bugünden geriye sayar); ay
 * uzunlukları değiştiği için ikisi de AÇILIŞTA hesaplanır. Sabit 30/31 yazmak,
 * ayın son gününde bir gün kaydırırdı.
 */
function rangePresets() {
  // SADE: dört çip (docs/tasarim-sade-magaza.md). "Dün" ve "Geçen ay" tarih
  // alanlarından yine seçilebilir; çip olarak durmaları şeridi kalabalıklaştırıyordu.
  const dayOfMonth = new Date().getDate();
  return [
    { key: 'today', label: 'Bugün', days: 1 },
    { key: 'week', label: '7 gün', days: 7 },
    { key: 'month', label: '30 gün', days: 30 },
    { key: 'thisMonth', label: 'Bu ay', days: dayOfMonth },
  ];
}

function filters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  return {
    start: range.start || todayIso(-29),
    end: range.end || todayIso(),
    channel: values.channel || '',
    compare: values.compare || 'previous',
  };
}

function query(extra = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters(), ...extra })) {
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  return params.toString();
}

// -------------------------------------------------------------------- veri

/**
 * Kartlar BİRBİRİNİ BEKLEMEZ: biri patlarsa diğerleri dolar (K7).
 *
 * `fresh` YALNIZ "Yenile" düğmesinden gelir. Backend panonun yanıtlarını kısa
 * süre rafta tutuyor; yenileme de raftan cevaplansaydı düğme hiçbir şey
 * yapmayan bir düğme olurdu. Açılış ve süzgeç değişimi rafı kullanır — asıl
 * hızlanma oradan gelir.
 */
function refreshAll({ fresh = false } = {}) {
  state.fresh = fresh;
  nodes.status?.set(fresh ? 'Pano yenileniyor…' : 'Pano yükleniyor…');
  // Sistem durumu Pano'da değil (Ayarlar → Sistem durumu): açılış mağazaya o
  // kartın dört isteğini atmaz.
  return Promise.allSettled([
    loadSummary(), loadRecent(), loadStock(), loadPending(),
  ]).then(() => {
    state.loadedAt = new Date().toLocaleTimeString('tr-TR',
      { hour: '2-digit', minute: '2-digit' });
    paintStatus();
  });
}

/**
 * Raf atlanacaksa uçlara `fresh=1` eklenir.
 *
 * `force`, patlamış bir kartın "Tekrar dene" düğmesinden gelir: raftan
 * cevaplanan bir yeniden deneme aynı hatayı geri verirdi.
 */
function freshParam(force = false) {
  return (force || state.fresh) ? { fresh: 1 } : {};
}

/** `?a=b` ya da boş — sorgu parçası olmayan uçlara güvenle eklenir. */
function freshQuery(force = false) {
  return (force || state.fresh) ? '?fresh=1' : '';
}

function paintStatus() {
  const summary = state.summary;
  if (!summary) {
    nodes.status?.set(state.pending
      ? 'Pano hesaplanıyor — dönemin siparişleri arka planda okunuyor…'
      : `Veri alınamadı · son deneme ${state.loadedAt}`, !state.pending);
    return;
  }
  const span = summary.range;
  const compare = COMPARE_LABELS[summary.compare];
  // `ageSeconds` verinin kaç saniyelik olduğunu söyler. Yazılmasaydı ekran
  // raftan gelen bir sayının yanına "şimdi" saatini basar ve tazeliği
  // hakkında yanlış bilgi verirdi.
  // SADE (§3): "N sipariş tarandı", "kanal default" gibi iç ayrıntı yazılmaz.
  const age = Number(summary.ageSeconds) || 0;
  const when = age >= 60 ? `${Math.round(age / 60)} dk önce güncellendi`
    : age > 0 ? `${age} sn önce güncellendi` : `${state.loadedAt} itibarıyla`;
  nodes.status?.set(
    `${span.start} – ${span.end}`
    + (compare ? ` · karşılaştırma: ${compare}` : '')
    + ` · ${when}`
    + (summary.refreshing ? ' · tazeleniyor' : '')
    + (summary.refreshError ? ` · güncellenemedi: ${summary.refreshError}` : ''),
    Boolean(summary.refreshError),
  );
}

function scheduleSummaryPoll() {
  if (summaryTimer || !mounted) return;
  if (summaryPolls >= SUMMARY_POLL_LIMIT) {
    // Süresiz yoklanmaz. Hâlâ değer yoksa kart nedenini söyler ve elle
    // yeniden denemeyi önerir.
    if (!state.summary) {
      state.pending = false;
      failCard(nodes.kpi, 'Pano özeti iki dakikadır hesaplanıyor; mağaza yoğun olabilir.',
        () => loadSummary({ fresh: true }));
      paintStatus();
    }
    return;
  }
  summaryTimer = setTimeout(() => {
    summaryTimer = null;
    if (!mounted) return;
    summaryPolls += 1;
    loadSummary({ poll: true });
  }, SUMMARY_POLL_MS);
}

function stopSummaryPoll() {
  if (summaryTimer) { clearTimeout(summaryTimer); summaryTimer = null; }
  summaryPolls = 0;
}

function skeletonSummary() {
  nodes.kpi.replaceChildren(skeletonRows(2, 4));
  nodes.notes.replaceChildren();
  for (const key of ['daily', 'statuses', 'top', 'hours']) {
    nodes[key].replaceChildren(skeletonRows(3, 3));
  }
}

/**
 * Özet. `poll` YALNIZ yoklamadan gelir: iskelet çizilmez (ekran titremesin),
 * `fresh` GÖNDERİLMEZ — gönderilseydi her yoklama hesabı baştan başlatırdı.
 */
async function loadSummary({ fresh = false, poll = false } = {}) {
  if (!poll) {
    stopSummaryPoll();
    skeletonSummary();
    // Süzgeç değiştiyse eldeki özet artık BAŞKA bir dönemin özetidir; durum
    // satırı onu yeni dönemmiş gibi yazmasın.
    state.summary = null;
  }

  let payload;
  try {
    // Özet artık hesabı BEKLEMEZ: sunucu son bilinen değeri hemen döner ya da
    // birkaç saniye içinde `pending` der. Uzun süre burada bekleten eski
    // davranış (sayfa sayfa ~45 istek) geride kaldı; süre sınırı yine geniş
    // tutulur — `store.scan` olmayan kurulumda özet istekte hesaplanır.
    payload = await api(`${BASE}/summary?${query(poll ? {} : freshParam(fresh))}`,
      { timeoutSeconds: 300 });
  } catch (error) {
    if (poll && state.summary) { scheduleSummaryPoll(); return; }
    state.summary = null;
    state.pending = false;
    failCard(nodes.kpi, error.message, () => loadSummary({ fresh: true }));
    for (const key of ['daily', 'statuses', 'top', 'hours']) nodes[key].replaceChildren();
    return;
  }

  if (payload.pending) {
    // Hiç özet yok, hesap sürüyor. SIFIR KPI ÇİZİLMEZ: "bu dönem satış yok"
    // diye okunurdu.
    // İskelet yerinde kalır; ne olduğunu durum satırı söyler (kutu yok, §3).
    state.pending = true;
    if (!state.summary) {
      if (!nodes.kpi.querySelector('.kit-skel')) {
        nodes.kpi.replaceChildren(skeletonRows(2, 4));
      }
      paintStatus();
    }
    scheduleSummaryPoll();
    return;
  }
  state.pending = false;

  if (!payload.connected) {
    if (poll && state.summary) return;      // eldeki özet kalır, durum satırı yazar
    state.summary = null;
    failCard(nodes.kpi, payload.error || 'Mağazaya ulaşılamadı.',
      () => loadSummary({ fresh: true }));
    for (const key of ['daily', 'statuses', 'top', 'hours']) nodes[key].replaceChildren();
    paintStatus();
    return;
  }

  // Yoklamada aynı (hâlâ tazelenmekte olan) özet geldiyse grafikler yeniden
  // çizilmez; yalnız durum satırı güncellenir.
  const same = poll && state.summary && payload.refreshing
    && state.summary.ageSeconds <= payload.ageSeconds;
  state.summary = payload;
  if (!same) {
    paintKpi(payload);
    paintNotes(payload);
    paintDaily(payload);
    paintStatuses(payload);
    paintTop(payload);
    paintHours(payload);
  }
  if (poll) paintStatus();
  if (payload.refreshing) scheduleSummaryPoll();
  else stopSummaryPoll();
}

async function loadRecent({ fresh = false } = {}) {
  await fill(nodes.recent, `${BASE}/orders/recent${freshQuery(fresh)}`, (payload) => {
    if (!payload.connected) throw new Error(payload.error || 'Siparişler okunamadı.');
    if (!payload.items.length) {
      return emptyState({
        title: 'Sipariş yok',
        text: 'Bu mağazada henüz sipariş görünmüyor.',
      });
    }
    return dataTable({
      columns: [
        { key: 'number', label: 'Sipariş', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'createdAt', label: 'Tarih', width: '128px',
          cell: (row) => (row.createdAt || '—').replace('T', ' ').slice(0, 16) },
        { key: 'customer', label: 'Müşteri', width: 'minmax(0, 1.4fr)' },
        { key: 'statusLabel', label: 'Durum', width: '104px',
          cell: (row) => badge(row.statusLabel, statusTone(row.status)) },
        { key: 'total', label: 'Tutar', width: '110px', align: 'num',
          cell: (row) => money(row.total) },
      ],
      rows: payload.items,
      dense: true,
      // Satır Siparişler ekranını AÇAR ve sipariş kimliğini taşır; pano
      // üzerinde sipariş düzenlenmez.
      onRow: (row) => open('store_orders', { orderId: row.id }),
    }).node;
  }, () => loadRecent({ fresh: true }));
}

async function loadStock({ fresh = false } = {}) {
  await fill(nodes.stock, `${BASE}/stock/critical${freshQuery(fresh)}`, (payload) => {
    if (!payload.available) throw new Error(payload.error);
    if (!payload.items.length) {
      return emptyState({
        title: 'Kritik stok yok',
        text: 'Eşiğin altına düşen ürün bulunmuyor — katalog bu başlıkta temiz.',
      });
    }
    // Ürün künyesi yeteneği VARSA künye yerinde açılır; yoksa Ürünler ekranına
    // gidilir. Yetenek gelene kadar satır yine tıklanabilir kalır.
    const productCard = capability('store.product.card');
    const onRow = productCard && typeof productCard.open === 'function'
      ? (row) => productCard.open(row.id)
      : (row) => open('store_products', { productId: row.id });
    return dataTable({
      columns: [
        { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
        { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'stock', label: 'Stok', width: '80px', align: 'num',
          cell: (row) => {
            const box = h('span', 'sd-stock');
            box.append(h('b', undefined, num(row.stock)),
              badge(row.stock > 0 ? 'kritik' : 'tükendi', row.stock > 0 ? 'warn' : 'bad'));
            return box;
          } },
      ],
      rows: payload.items,
      dense: true,
      onRow,
    }).node;
  }, () => loadStock({ fresh: true }));
}

async function loadPending({ fresh = false } = {}) {
  await fill(nodes.pending, `${BASE}/pending${freshQuery(fresh)}`, (payload) => {
    const list = h('div', 'sd-list');
    for (const row of payload.items) {
      const line = h('div', 'sd-line');
      line.append(h('span', 'sd-line-label', row.label));
      if (row.error) {
        const warn = h('span', 'sd-line-value');
        warn.append(badge('okunamadı', 'dim'));
        warn.title = row.error;
        line.append(warn);
      } else {
        const value = h('span', 'sd-line-value');
        value.append(h('b', undefined, num(row.count)),
          badge(row.count > 0 ? 'bekliyor' : 'temiz', row.count > 0 ? 'warn' : 'good'));
        line.append(value);
      }
      line.append(button('Aç', { variant: 'ghost', onClick: () => open(row.target) }));
      list.append(line);
    }
    return list;
  }, () => loadPending({ fresh: true }));
}

async function loadSystem({ fresh = false } = {}) {
  await fill(nodes.system, `${BASE}/system${freshQuery(fresh)}`, (payload) => {
    const list = h('div', 'sd-list');
    for (const row of payload.items) {
      const line = h('div', 'sd-line');
      line.append(h('span', 'sd-line-label', row.label));
      const value = h('span', 'sd-line-value');
      // Rozetin yanında her zaman yazı durur: renk tek başına anlam taşımaz.
      value.append(h('b', undefined, row.value),
        badge(STATE_LABELS[row.state] || row.state, STATE_TONES[row.state] || ''));
      line.append(value);
      const detail = h('span', 'sd-line-detail', row.detail || '');
      list.append(line, detail);
    }
    return list;
  }, () => loadSystem({ fresh: true }));
}

/** Kart doldurma kalıbı: iskelet → veri ya da "tekrar dene" kartı. */
async function fill(host, path, paint, retry) {
  host.replaceChildren(skeletonRows(4, 3));
  let payload;
  try {
    payload = await api(path);
  } catch (error) {
    failCard(host, error.message, retry);
    return;
  }
  try {
    host.replaceChildren(paint(payload));
  } catch (error) {
    failCard(host, error.message, retry);
  }
}

function failCard(host, message, retry) {
  host.replaceChildren(emptyState({
    title: 'Bu kart okunamadı',
    text: message || 'Bilinmeyen hata.',
    actions: [button('Tekrar dene', { variant: 'primary', onClick: () => retry() })],
  }));
}

// ------------------------------------------------------------------- çizim

function statusTone(status) {
  if (['canceled', 'cancelled', 'fraud'].includes(status)) return 'bad';
  if (['pending', 'pending_payment'].includes(status)) return 'warn';
  if (status === 'completed') return 'good';
  return 'info';
}

function paintKpi(payload) {
  const compare = COMPARE_LABELS[payload.compare];
  // Ciro kutusunun altındaki eğilim şeridi AYNI seriden gelir: `paintDaily`
  // grafiği de bu diziyi çiziyor. İkinci bir kaynak (ör. mağazanın kendi pano
  // ucu) kullanılsaydı iki gösterim zamanla ayrışır ve kullanıcı hangisine
  // güveneceğini bilemezdi — panonun temel kuralı budur.
  const seri = fillDays(payload.daily, payload.range.start, payload.range.end)
    .map((point) => point.value);

  const tileBox = (tile) => {
    const known = tile.value !== null && tile.value !== undefined;
    const shown = known
      ? (tile.kind === 'money' ? money(tile.value) : num(tile.value))
      : '—';
    const box = {
      label: tile.label,
      value: shown,
      title: tile.note
        || (tile.previous !== undefined && tile.previous !== null
          ? `${compare}: ${tile.kind === 'money' ? money(tile.previous) : num(tile.previous)}`
          : ''),
    };
    if (!known) box.tone = 'muted';
    else if (tile.key === 'outOfStock' && tile.value > 0) box.tone = 'bad';
    else if (['pending', 'unshipped'].includes(tile.key) && tile.value > 0) box.tone = 'warn';
    // ŞERİT YALNIZ CİRODA. Elimizde gün gün seri olan tek rakam ciro; sipariş
    // sayısının, sepetin ya da iadenin günlük dökümü uçtan gelmiyor. Diğer
    // kutulara da şerit koymak ciro eğilimini onların eğilimiymiş gibi
    // göstermek olurdu — bakan kişi bunu fark etmez, çünkü şeridin ekseni yok.
    if (tile.key === 'revenue' && known && seri.length > 1) {
      box.spark = seri;
      box.sparkTitle = `${seri.length} günün günlük cirosu — aynı seri "Günlük ciro" `
        + 'grafiğinde çizilidir.';
    }
    if (tile.delta && tile.delta.percent !== null) {
      box.delta = { percent: tile.delta.percent, title: box.title };
    }
    return box;
  };

  // ÜST ŞERİT EN ÇOK DÖRT KUTU (§6). Kalanlar (sepet, yeni müşteri, iade)
  // altında sessiz tek satırda durur. "Tükenen ürün" çizilmez: kaynağı yok,
  // her zaman "—" idi (bkz. service `OUT_OF_STOCK_NOTE`); stok sorusunun
  // cevabı "Kritik stok" kartıdır.
  const byKey = new Map(payload.kpis.map((tile) => [tile.key, tile]));
  const primary = PRIMARY_KPIS.map((key) => byKey.get(key)).filter(Boolean);
  const row = kpiRow(primary.map(tileBox));
  // Bekleyen/Kargolanmayan kutuları İŞİN YAPILDIĞI ekrana kısayoldur. Kit
  // kutusu tıklama almıyor; burada erişilebilir düğmeye dönüştürülür.
  primary.forEach((tile, index) => {
    const target = KPI_TARGETS[tile.key];
    const box = row.children[index];
    if (!target || !box) return;
    box.classList.add('sd-kpi-link');
    box.setAttribute('role', 'button');
    box.tabIndex = 0;
    box.title = `${box.title ? `${box.title} · ` : ''}${target.label} ekranında aç`;
    const go = () => open(target.panel);
    box.addEventListener('click', go);
    box.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); go(); }
    });
  });

  const minor = h('div', 'sd-kpi-minor');
  for (const key of MINOR_KPIS) {
    const tile = byKey.get(key);
    if (!tile) continue;
    const box = tileBox(tile);
    const item = h('span', 'sd-kpi-minor-item');
    item.append(h('span', 'sd-kpi-minor-label', tile.label), h('b', undefined, box.value));
    if (box.delta) {
      const up = Number(box.delta.percent) >= 0;
      item.append(h('span', `sd-kpi-minor-delta ${up ? 'up' : 'down'}`,
        `${up ? '▲' : '▼'} %${Math.abs(Number(box.delta.percent)).toLocaleString('tr-TR')}`));
    }
    if (box.title) item.title = box.title;
    minor.append(item);
  }
  nodes.kpi.replaceChildren(row, minor);
}

//: Rakamı DEĞİŞTİRMEYEN, yalnız nasıl hesaplandığını anlatan iç notlar —
//: ekranda gösterilmez (§3), PDF raporunda durmaya devam eder. Eksik/okunamayan
//: rakam uyarıları ("EKSİK", "okunamadı") her zaman gösterilir.
const QUIET_NOTES = ['yerelde süzüldü'];

function paintNotes(payload) {
  nodes.notes.replaceChildren();
  for (const note of payload.notes || []) {
    if (QUIET_NOTES.some((part) => note.includes(part))) continue;
    nodes.notes.append(alertBox(note, 'warn'));
  }
}

function paintDaily(payload) {
  const points = fillDays(payload.daily, payload.range.start, payload.range.end);
  nodes.daily.replaceChildren(lineChart(points));
}

function paintStatuses(payload) {
  const box = h('div');
  // `barChart`, `stackedBar` DEĞİL: kitin `stackedBar`ı veri varken patlıyor
  // (charts.js: `svg.append(rect).append(title)` — `append` hiçbir şey
  // döndürmez). İstisna `loadSummary` içinde yükseldiği için bu kartla
  // birlikte "En çok satan" ve "Saat yoğunluğu" da hiç çizilmiyordu. Kit
  // düzelince geri alınabilir; yatay çubuk sayıyı yine yanında yazar.
  box.append(barChart(payload.statuses.map((part) => ({
    label: part.label, value: part.value, display: num(part.value),
  }))));
  if (payload.cancelled) {
    box.append(h('div', 'sd-note', `${num(payload.cancelled)} iptal sipariş ciroya dahil değil.`));
  }
  nodes.statuses.replaceChildren(box);
}

function paintTop(payload) {
  if (!payload.topProducts.length) {
    nodes.top.replaceChildren(emptyState({
      title: 'Satış kırılımı yok',
      text: 'Bu aralıkta satılan ürün bulunamadı ya da sipariş kayıtları kalem bilgisi '
        + 'taşımıyor.',
    }));
    return;
  }
  nodes.top.replaceChildren(barChart(payload.topProducts.map((item) => ({
    label: item.name,
    value: item.qty,
    display: `${num(item.qty)} adet · ${money(item.total)}`,
  }))));
}

function paintHours(payload) {
  nodes.hours.replaceChildren(hourStrip(payload.hours));
}

// ================================================================== ayarlar

async function renderSettings(host) {
  dropForms();
  host.replaceChildren(skeletonRows(6, 2));
  let payload;
  try {
    payload = await call(`${BASE}/settings`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  // Hata kalır ama kısa (§3): ne oldu + ne yapılabilir.
  if (!payload.connected || !payload.storeAvailable) {
    host.append(alertBox(`Mağaza ayarları okunamadı — ${payload.error || 'bağlantı yok'}. `
      + 'Aşağıdaki tercihler yine kaydedilebilir.', 'warn'));
  }

  // TEK SEÇENEKLİ SORU SORULMAZ (choice.js): tek kanallı/tek dilli mağazada
  // kanal ve dil kutusu çizilmez. Kutu yoksa tercih değişmez; mevcut değer
  // yerinde kalır.
  const many = (items) => (items || []).length > 1;
  const localForm = formGrid({
    fields: [
      many(payload.channels) && { key: 'channel', label: 'Çalışma kanalı', type: 'select',
        options: payload.channels
          .map((item) => ({ value: item.code, label: `${item.name} (${item.code})` })),
        hint: 'Panonun ve raporların hangi kanalı okuduğunu belirler.' },
      many(payload.locales) && { key: 'locale', label: 'Dil', type: 'select',
        options: payload.locales
          .map((item) => ({ value: item.code, label: `${item.name} (${item.code})` })) },
      { key: 'compare', label: 'Karşılaştırma', type: 'select', options: COMPARE_OPTIONS },
      { key: 'timezone', label: 'Saat dilimi', type: 'text', maxLength: 64,
        hint: 'Kontrol Merkezi gösterimi içindir; vitrinin saat dilimi mağazadadır.' },
      { key: 'dateFormat', label: 'Tarih biçimi', type: 'text', maxLength: 32 },
      { key: 'weekStart', label: 'Hafta başlangıcı', type: 'select', options: [
        { value: 1, label: 'Pazartesi' }, { value: 0, label: 'Pazar' },
      ] },
    ],
    value: payload.local,
  });
  forms.push(localForm);

  const currency = (payload.channels.find((item) => item.code === payload.local.channel)
    || payload.channels[0] || {}).currency;
  const currencyLine = h('div', 'sd-note', `Para birimi: ${currency || '—'}`);

  const identityForm = groupForm(payload.identity, [
    ['name', 'text', 180], ['email', 'text', 180], ['phone', 'text', 40],
    ['address', 'textarea', 400], ['taxNumber', 'text', 64],
  ]);
  const seoForm = groupForm(payload.seo, [
    ['metaTitle', 'text', 120], ['metaDescription', 'textarea', 320],
    ['metaKeywords', 'text', 240],
  ]);

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      const body = {
        local: localForm.patch(),
        identity: identityForm.form ? identityForm.form.patch() : {},
        seo: seoForm.form ? seoForm.form.patch() : {},
      };
      if (!Object.keys(body.local).length && !Object.keys(body.identity).length
        && !Object.keys(body.seo).length) {
        toast('Değişen alan yok.', 'warn');
        return;
      }
      const reason = await askReason({
        title: 'Ayarları kaydet',
        description: 'Mağaza ayarları vitrini doğrudan etkiler; gerekçe denetim kaydına '
          + 'yazılır ve mağazaya başlıkla gider.',
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
        renderSettings(host);
      });
    },
  });

  const actions = h('div', 'sd-actions');
  actions.append(save);

  // Anahtarı mağazada BULUNMAYAN bölüm hiç çizilmez (ne form ne uyarı kutusu):
  // bu kurulumda mağaza kimliği "Ödeme, kargo ve mağaza" alt başlığındadır.
  host.append(...[
    identityForm.node && card('Mağaza kimliği', identityForm.node,
      'Vitrini ve belgeleri etkiler'),
    card('Kanal, para birimi ve dil', wrap(localForm.node, currencyLine)),
    seoForm.node && card('SEO', seoForm.node, 'Vitrinin varsayılan meta alanları'),
    actions,
    maintenanceCard(payload, host),
    reportDirCard(payload),
  ].filter(Boolean));
}

function wrap(...children) {
  const box = h('div', 'sd-stack');
  box.append(...children);
  return box;
}

/**
 * Mağaza ayar grubunu forma çevirir.
 *
 * Anahtarı mağazada BULUNAMAYAN alan forma konmaz: bulunmayan anahtara yazmak
 * `core_config` içinde etkisiz bir satır açar ve kullanıcı ayarı
 * değiştirdiğini sanır. Alan yerine neden gösterilmediği yazar.
 */
function groupForm(group, spec) {
  const box = h('div', 'sd-stack');
  const fields = [];
  const value = {};
  const missing = [];

  for (const [key, type, maxLength] of spec) {
    const entry = (group.fields || {})[key];
    if (!entry) continue;
    if (!entry.found) {
      missing.push(entry.label);
      continue;
    }
    fields.push({ key, label: entry.label, type, maxLength, wide: type === 'textarea' });
    value[key] = entry.value ?? '';
  }

  if (!fields.length) {
    // Bulunmayan anahtara yazmak hiçbir şeyi değiştirmez; bölüm HİÇ çizilmez
    // (eskiden yerine uzun bir uyarı kutusu çıkıyordu — §3).
    return { node: null, form: null };
  }

  const form = formGrid({ fields, value });
  forms.push(form);
  box.append(form.node);
  if (missing.length) {
    box.append(alertBox(`Mağazada bulunamayan alanlar açılmadı: ${missing.join(', ')}.`,
      'warn'));
  }
  return { node: box, form };
}

function maintenanceCard(payload, host) {
  const box = h('div', 'sd-stack');
  const status = (payload.maintenance.fields || {}).status || {};
  const enabled = payload.maintenance.enabled;

  if (!status.found) {
    // Anahtar bu kurulumda yok (bakım modu satış kanalı kaydında tutuluyor);
    // yazılsaydı vitrin kapanmaz, yalnız kapattığınızı sandırırdı. Kart çizilmez;
    // durum "Ödeme, kargo ve mağaza" alt başlığında salt okunur görünür.
    return null;
  }

  const line = h('div', 'sd-line');
  line.append(h('span', 'sd-line-label', 'Vitrin durumu'));
  const value = h('span', 'sd-line-value');
  value.append(h('b', undefined, enabled ? 'Bakım modunda' : 'Açık'),
    badge(enabled ? 'kapalı' : 'yayında', enabled ? 'bad' : 'good'));
  line.append(value);

  const form = formGrid({
    fields: [
      { key: 'allowedIps', label: 'İzinli IP listesi', type: 'text', maxLength: 500,
        hint: 'Virgülle ayrılır. Boş bırakılırsa mevcut liste korunur.' },
      { key: 'message', label: 'Gösterilecek metin', type: 'textarea', maxLength: 500,
        wide: true },
    ],
    value: {
      allowedIps: (payload.maintenance.fields.allowedIps || {}).value ?? '',
      message: (payload.maintenance.fields.message || {}).value ?? '',
    },
  });
  forms.push(form);

  const run = async (dryRun) => {
    const next = !enabled;
    const reason = await askReason({
      title: next ? 'Bakım modunu aç' : 'Bakım modunu kapat',
      description: next
        ? 'Vitrin TÜM ziyaretçilere kapanır; yalnız izinli IP\'ler girebilir. Sepetteki '
          + 'müşteriler alışverişi tamamlayamaz. Gerekçe denetim kaydına yazılır.'
        : 'Vitrin yeniden herkese açılır. Gerekçe denetim kaydına yazılır.',
      confirmLabel: dryRun ? 'Kuru prova' : (next ? 'Vitrini kapat' : 'Vitrini aç'),
    });
    if (!reason) return;
    await withBusy('Bakım modu yazılıyor…', async () => {
      const draft = form.draft();
      const result = await call(`${BASE}/settings/maintenance`, {
        method: 'POST',
        body: {
          enabled: next, allowedIps: draft.allowedIps || '', message: draft.message || '',
          reason, dryRun,
        },
      });
      toast(result.dryRun ? 'Kuru prova: mağazaya yazılmadı.' : 'Bakım modu güncellendi.',
        result.dryRun ? 'warn' : 'good');
      if (result.notice) toast(result.notice, 'warn');
      if (!result.dryRun) renderSettings(host);
    });
  };

  const actions = h('div', 'sd-actions');
  actions.append(
    button('Kuru prova', {
      title: 'Ne yazılacağını gösterir, mağazaya dokunmaz',
      onClick: () => run(true),
    }),
    button(enabled ? 'Vitrini yeniden aç' : 'Vitrini kapat', {
      variant: 'danger', onClick: () => run(false),
    }),
  );

  box.append(line, form.node, actions);
  return card('Bakım modu', box, 'Vitrini kapatır — ayrı izin ister');
}

function reportDirCard(payload) {
  const dir = payload.reportDir || {};
  const box = h('div', 'sd-stack');
  const line = h('div', 'sd-line');
  line.append(h('span', 'sd-line-label', 'Rapor klasörü'));
  const value = h('span', 'sd-line-value');
  value.append(h('code', 'sd-path', dir.path || '—'),
    badge(dir.ready ? 'yazılabilir' : 'yazılamıyor', dir.ready ? 'good' : 'bad'));
  line.append(value);
  box.append(line);
  if (dir.freeBytes !== null && dir.freeBytes !== undefined) {
    box.append(h('div', 'sd-note',
      `Diskte ${Math.round(dir.freeBytes / (1024 * 1024 * 1024) * 10) / 10} GB boş yer var.`));
  }
  // Yol modül ayarındaki `export_path` ile sabitlenir; ekrandan yazılmaz
  // (çalışırken yazılsaydı sonraki açılışta geri dönen bir "ayar" olurdu).
  // Bunu anlatan uzun not kısaldı (§3).
  box.append(h('div', 'sd-note',
    dir.configured
      ? 'Raporlar hep bu klasöre yazılır.'
      : 'Raporlar her ay kendi klasörüne yazılır: Raporlar/Mağaza/Satış/<yıl>/<ay>.'));
  return card('Dışa aktarma ve rapor klasörü', box);
}

// ============================================================ yapılandırma

// NE GÖSTERİR. Mağazanın ayar ağacı 344 alan taşıyor (menü ucu 156 KB); bu
// sekme işletmenin gerçekten dokunduğu ~30 alanı gösterir. Beyaz liste
// `backend/config_map.py` içindedir; ağacın tamamı servise gelir, ekrana
// çıkmaz.
//
// PARA ALANLARI KURUŞ DEĞİLDİR. Kitin `money()`/`parseMoney` çifti burada
// BİLEREK kullanılmaz: "ücretsiz kargo eşiği" ve "minimum sepet tutarı"
// mağazada Bagisto'nun ondalık LİRA metni olarak duruyor. Kuruşa çevirip geri
// yazmak, kullanıcı hiçbir şeye dokunmasa bile ayarın anlamını değiştirirdi.

/** Karşılaştırma normalizasyonu — backend'deki `config_map.norm` ile aynı kural. */
function normValue(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? '1' : '0';
  return String(value).trim();
}

function configDirty() {
  return Object.keys(config.draft)
    .filter((code) => normValue(config.draft[code]) !== normValue(config.base[code]));
}

async function renderConfig(host) {
  host.replaceChildren(skeletonRows(8, 2));
  config = emptyConfig();

  let payload;
  try {
    payload = await call(`${BASE}/config`);
  } catch (error) {
    host.replaceChildren(emptyState({
      title: 'Yapılandırma okunamadı',
      text: error.message || 'Mağazaya ulaşılamadı.',
      actions: [button('Tekrar dene', { variant: 'primary',
        onClick: () => renderConfig(host) })],
    }));
    return;
  }

  config.payload = payload;
  host.replaceChildren();

  if (!payload.connected) {
    host.append(alertBox(
      `Mağaza ayar ağacı okunamadı — ${payload.error || 'bağlantı yok'}. Hangi anahtarın `
      + 'gerçekten var olduğu doğrulanamadığı için hiçbir alan açılmadı.', 'bad'));
    // `h(tag, class, text)` ÜÇÜNCÜ ARGÜMANI METİNDİR: düğüm verilince
    // `[object HTMLButtonElement]` yazılır ve düğme hiç çizilmez — yani
    // bağlantı koptuğunda "Tekrar dene" tam da gerektiği anda yok olurdu.
    const retry = h('div', 'sd-actions');
    retry.append(button('Tekrar dene', { variant: 'primary', onClick: () => renderConfig(host) }));
    host.append(retry);
    return;
  }

  const box = h('div', 'sd-cfg');
  box.append(searchBox(), ...payload.groups.map(groupCard),
    registerCard(maintenanceReadOnly(payload), [],
      'bakım modu vitrin kapalı yayında maintenance kanal'));

  if (payload.missing.length) {
    box.append(alertBox(`Mağazada bulunamadığı için değiştirilemeyen: `
      + `${payload.missing.join(', ')}.`, 'warn'));
  }
  // Ağaçtaki alan sayısını ve kaynak rozetinin nasıl okunacağını anlatan
  // bilgi kutusu kaldırıldı (§3). Rozetin ipucu (`title`) anlamını söyler.

  host.append(box, saveBar(host));
  paintConfigCount();
  applyConfigSearch('');
}

function searchBox() {
  const wrap = h('div', 'sd-cfg-search');
  const input = h('input', 'kit-input');
  input.type = 'search';
  input.placeholder = 'Ayar ara — alan etiketlerinde arar (ör. "kargo", "misafir")';
  input.setAttribute('aria-label', 'Ayar ara');
  // Kaydetmeden sonra sekme yeniden çizilir; ÖNCEKİ debounce hâlâ bekleyen bir
  // çağrı tutuyor olabilir ve o çağrı artık ekranda olmayan satırları
  // gizlemeye çalışırdı (kit kuralı 4: cleanup gerçek kaynak bırakır).
  configFilter?.cancel?.();
  configFilter = debounce((text) => applyConfigSearch(text), 200);
  input.addEventListener('input', () => configFilter(input.value));
  wrap.append(input);
  return wrap;
}

/** Arama alan ETİKETİNDE arar; mağazanın kendi başlığı ve kodu da taranır. */
function applyConfigSearch(text) {
  const needle = foldText(String(text || '').trim());
  let shown = 0;
  for (const entry of config.rows.values()) {
    const hit = !needle || entry.haystack.includes(needle);
    entry.row.hidden = !hit;
    if (hit) shown += 1;
  }
  for (const box of config.cards) {
    // Alanı olan kart, görünür satırı kaldıysa durur. ALANI OLMAYAN kart
    // (bakım modu salt okunur kutusu) kendi metniyle aranır: aksi hâlde
    // "kuveyt" aramasında ilgisiz bir kart ekranda kalır, hiç sonuç vermeyen
    // aramada ise "bulunamadı" yazısı dolu bir kartın altında belirirdi.
    const hit = box.codes.length
      ? box.codes.some((code) => !config.rows.get(code)?.row.hidden)
      : !needle || box.haystack.includes(needle);
    box.node.hidden = !hit;
    if (hit && !box.codes.length) shown += 1;
  }
  if (nodes.cfgEmpty) {
    nodes.cfgEmpty.hidden = shown > 0;
    nodes.cfgEmpty.textContent = shown > 0 ? ''
      : `"${text}" için ayar bulunamadı. Bu ekran mağazanın tüm ayarlarını değil, `
        + 'işletmenin dokunduklarını gösterir.';
  }
}

function groupCard(group) {
  const body = h('div', 'sd-cfg-body');
  if (group.note) body.append(h('div', 'sd-note', group.note));
  for (const field of group.fields) body.append(configRow(field));
  return registerCard(card(group.title, body), group.fields.map((field) => field.code),
    group.title);
}

/** Kartı aramaya kaydeder. `codes` boşsa kart kendi metniyle aranır. */
function registerCard(node, codes, haystack) {
  config.cards.push({ node, codes, haystack: foldText(haystack) });
  return node;
}

function configRow(field) {
  const row = h('div', 'sd-cfg-row');
  const label = h('label', 'sd-cfg-label');
  label.append(h('span', 'sd-cfg-name', field.label));
  if (field.hint) label.append(h('span', 'sd-cfg-hint', field.hint));
  // Ayar KODU (`sales.carriers…`) ekrana yazılmaz (§3: teknik dil yok); arama
  // onu yine bulur (`register` → haystack).

  const control = h('div', 'sd-cfg-control');
  const meta = h('div', 'sd-cfg-meta');

  const badgeSpec = SOURCE_BADGES[field.source] || SOURCE_BADGES.unset;
  const mark = badge(badgeSpec.label, badgeSpec.tone);
  mark.title = badgeSpec.title;
  meta.append(mark);

  if (!field.editable) {
    // Salt okunur alan: değeri gösterilir, NEDENİ yazılır. Sessizce gizlemek,
    // "ayarı bulamadım" ile "bu ayarı yazmam tehlikeli" arasındaki farkı siler.
    // Mantıksal alan "1"/"0" değil, günlük dille yazılır (§7).
    const shown = field.kind === 'checkbox'
      ? (normValue(field.value) === '1' ? 'Açık' : 'Kapalı')
      : (field.value || '—');
    control.append(h('div', 'sd-cfg-static', shown));
    row.append(label, control, meta);
    row.append(h('div', 'sd-cfg-why', field.reason));
    register(field, row, null);
    return row;
  }

  config.base[field.code] = field.value;
  config.draft[field.code] = field.value;

  let input;
  if (field.kind === 'checkbox') {
    input = h('input', 'kit-check');
    input.type = 'checkbox';
    input.checked = normValue(field.value) === '1';
    input.addEventListener('change', () => write(field.code, input.checked ? '1' : '0'));
  } else if (field.kind === 'textarea') {
    input = h('textarea', 'kit-textarea');
    input.maxLength = 1000;
    input.value = field.value;
    input.addEventListener('input', () => write(field.code, input.value));
  } else {
    input = h('input', 'kit-input');
    input.type = 'text';
    input.maxLength = 500;
    if (field.kind === 'number') input.inputMode = 'decimal';
    input.value = field.value;
    input.addEventListener('input', () => write(field.code, input.value));
  }
  input.id = `sd-cfg-${field.code.replace(/\W+/g, '-')}`;
  label.htmlFor = input.id;
  control.append(input);

  const set = (value) => {
    if (field.kind === 'checkbox') input.checked = normValue(value) === '1';
    else input.value = value ?? '';
  };

  if (field.hasDefault) {
    meta.append(button('Varsayılana dön', {
      variant: 'ghost',
      title: `İlan edilen varsayılan: ${field.default || '(boş)'}`,
      onClick: () => {
        set(field.default);
        write(field.code, field.default);
      },
    }));
  }

  row.append(label, control, meta);
  register(field, row, set);
  return row;
}

function register(field, row, set) {
  config.rows.set(field.code, {
    row,
    set,
    haystack: foldText(`${field.label} ${field.hint} ${field.storeLabel} ${field.code}`),
  });
}

function write(code, value) {
  config.draft[code] = value;
  paintConfigCount();
  config.rows.get(code)?.row.classList.toggle('dirty',
    normValue(value) !== normValue(config.base[code]));
}

function paintConfigCount() {
  if (!nodes.cfgCount) return;
  const count = configDirty().length;
  nodes.cfgCount.textContent = count ? `${count} alan değişti` : 'değişiklik yok';
  nodes.cfgBar?.classList.toggle('on', count > 0);
  if (nodes.cfgSave) nodes.cfgSave.disabled = count === 0;
  if (nodes.cfgUndo) nodes.cfgUndo.disabled = count === 0;
}

function saveBar(host) {
  nodes.cfgCount = h('span', 'sd-cfg-count', 'değişiklik yok');
  nodes.cfgUndo = button('Geri al', {
    title: 'Değişen alanları mağazadaki değerine döndürür',
    onClick: () => {
      for (const [code, entry] of config.rows) {
        if (!entry.set) continue;
        config.draft[code] = config.base[code];
        entry.set(config.base[code]);
        entry.row.classList.remove('dirty');
      }
      paintConfigCount();
      toast('Değişiklikler geri alındı; mağazaya hiçbir şey yazılmadı.', 'good');
    },
  });
  nodes.cfgSave = button('Kaydet', { variant: 'primary', onClick: () => saveConfig(host) });
  nodes.cfgEmpty = h('div', 'sd-cfg-none');
  nodes.cfgEmpty.hidden = true;

  nodes.cfgBar = h('div', 'sd-cfg-bar');
  nodes.cfgBar.append(nodes.cfgSave, nodes.cfgUndo, nodes.cfgCount);
  const wrap = h('div', 'sd-cfg-foot');
  wrap.append(nodes.cfgEmpty, nodes.cfgBar);
  return wrap;
}

async function saveConfig(host) {
  const dirty = configDirty();
  if (!dirty.length) {
    toast('Değişen alan yok.', 'warn');
    return;
  }
  const changes = {};
  for (const code of dirty) changes[code] = normValue(config.draft[code]);

  // Kapanan ödeme/kargo yöntemi ONAY METNİNDE ADIYLA sayılır: "üç alan
  // değişti" demek, kredi kartıyla ödemenin kapandığını söylemez.
  const dark = dirty
    .filter((code) => config.rows.get(code) && changes[code] === '0'
      && isCritical(code))
    .map((code) => labelOf(code));

  const reason = await askReason({
    title: dark.length ? 'Ödeme/kargo yöntemi kapatılıyor' : 'Yapılandırmayı kaydet',
    description: (dark.length
      ? `Bu kaydetme şunları KAPATIR: ${dark.join(', ')}. Kapalı yöntem ödeme adımında `
        + 'görünmez ve müşteri o yolla ödeyemez. '
      : '')
      + `${dirty.length} alan mağazaya yazılacak; değişiklik vitrini anında etkiler. `
      + 'Gerekçe denetim kaydına yazılır ve mağazaya başlıkla gider.',
    confirmLabel: dark.length ? 'Yine de kaydet' : 'Kaydet',
  });
  if (!reason) return;

  await withBusy('Yapılandırma yazılıyor…', async () => {
    const result = await call(`${BASE}/config`, {
      method: 'POST', body: { changes, reason, dryRun: false },
    });
    toast(`${num(result.written.length)} ayar yazıldı.`, 'good');
    if (result.skipped.length) {
      toast(`Yazılamayan: ${result.skipped.map((item) => item.label).join(', ')}.`, 'warn');
    }
    renderConfig(host);
  });
}

function isCritical(code) {
  const groups = config.payload?.groups || [];
  return groups.some((group) => group.fields.some((f) => f.code === code && f.critical));
}

function labelOf(code) {
  for (const group of config.payload?.groups || []) {
    for (const field of group.fields) if (field.code === code) return field.label;
  }
  return code;
}

/**
 * Bakım modu — SALT OKUNUR ve nedeni yazılı.
 *
 * Bu kurulumda bakım modu `core_config` içinde değil, satış kanalı kaydında.
 * Kanal yazma ucunun gövdesi (dile bağlı `translations[]`) canlı mağazada
 * denenmediği için buradan yazılmıyor: yanlış gövde kanal adını ve vitrin SEO
 * alanlarını boşaltabilirdi.
 */
function maintenanceReadOnly(payload) {
  const info = payload.maintenance || {};
  const box = h('div', 'sd-stack');

  if (!info.available) {
    box.append(alertBox(info.error || 'Bakım modu durumu okunamadı.', 'warn'));
    return card('Bakım modu', box, 'salt okunur');
  }

  const line = h('div', 'sd-line');
  line.append(h('span', 'sd-line-label', `Vitrin durumu (${info.channel} kanalı)`));
  const value = h('span', 'sd-line-value');
  value.append(h('b', undefined, info.enabled ? 'Bakım modunda' : 'Açık'),
    badge(info.enabled ? 'kapalı' : 'yayında', info.enabled ? 'bad' : 'good'));
  line.append(value);
  box.append(line);

  if (info.message) {
    box.append(h('div', 'sd-line-detail', `Gösterilen metin: ${info.message}`));
  }
  if (info.allowedIps) {
    box.append(h('div', 'sd-line-detail', `İzinli IP'ler: ${info.allowedIps}`));
  }
  // Neden buradan yazılmadığını anlatan uzun teknik not (`MAINTENANCE_NOTE`)
  // ekrana basılmaz (§3); gerekçe backend'de, kodda durur.
  box.append(h('div', 'sd-note', 'Bakım modu mağaza yönetiminden açılıp kapatılır.'));
  return card('Bakım modu', box, 'salt okunur');
}

// ================================================================== çıktı

async function exportCsv() {
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: filters() });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  open = ctx.open;
  capability = ctx.capability;
  mounted = true;

  const view = h('div', 'kit-panel sd');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  // İKİ SEKME (docs/tasarim-sade-magaza.md §4). Eski "Yapılandırma" sekmesi
  // teknik bir ayar ekranıydı; "Ayarlar"ın içine alt başlık olarak girdi.
  // Sistem sağlığı kartı da oraya taşındı: günlük iş için değil, bir şey
  // ters gittiğinde bakılan bir ekran.
  const tabs = tabBar([
    { key: 'board', label: 'Pano' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'board', (key) => showView(key));

  // SADE KİP (§1-2): görünür olan yalnız tarih aralığı ve hazır çipleri.
  // "Karşılaştır" katlanır; "Kanal" tek kanallı mağazada HİÇ çizilmez
  // (`applyChoiceFilter`, `loadReference`). İki çıktı tek "Dışa aktar"
  // menüsünde; "Yenile" hayalet düğme.
  nodes.filters = filterBar({
    compact: true,
    fields: [
      { kind: 'dateRange', key: 'range', label: 'Aralık', primary: true,
        start: todayIso(-29), end: todayIso(), presets: rangePresets() },
      { kind: 'select', key: 'channel', label: 'Kanal', hidden: true, options: [] },
      { kind: 'select', key: 'compare', label: 'Karşılaştır', options: COMPARE_OPTIONS },
    ],
    onChange: () => refreshAll(),
    actions: [
      button('Yenile', {
        variant: 'ghost',
        title: 'Pano kendiliğinden yenilenmez; rakamları mağazadan yeniden okur',
        onClick: () => refreshAll({ fresh: true }),
      }),
      menuButton('Dışa aktar', [
        { label: 'KPI ve günlük ciro — CSV',
          title: 'Rapor klasörüne CSV olarak yazar', onClick: exportCsv },
        { label: 'Günlük özet raporu — PDF',
          title: 'Yazdırılabilir PDF hazırlar', onClick: () => report.run('daily', filters()) },
      ], { title: 'CSV ya da yazdırılabilir PDF olarak indir' }),
    ],
  });

  nodes.status = statusLine();
  nodes.kpi = h('div', 'sd-kpi');
  nodes.notes = h('div', 'sd-notes');

  nodes.daily = h('div', 'sd-slot');
  nodes.statuses = h('div', 'sd-slot');
  nodes.top = h('div', 'sd-slot');
  nodes.hours = h('div', 'sd-slot');
  nodes.recent = h('div', 'sd-slot');
  nodes.stock = h('div', 'sd-slot');
  nodes.pending = h('div', 'sd-slot');
  nodes.system = null;               // Ayarlar → Sistem durumu açılınca kurulur

  // ALTI KART (§6 ruhu: ilk görünüşte yalnız günlük işe gereken). Saat
  // yoğunluğu ayrı kart olmaktan çıkıp durum dağılımının altına girdi.
  //
  // KAPATILMIŞ EKRANA `→` YOK. Raporlar, Yedekleme ve UDİT ekranları menüden
  // kaldırıldı; kabuk panele "şu ekran kayıtlı mı" bilgisini vermiyor, yani
  // düğmeyi koşula bağlamanın yolu yok. Hedefler yalnız yaşayan ekranlardır.
  const distribution = h('div', 'sd-stack');
  distribution.append(nodes.statuses, h('div', 'sd-caption', 'Saat yoğunluğu'), nodes.hours);
  const grid = h('div', 'sd-grid');
  grid.append(
    panelCard('Günlük ciro', nodes.daily, { hint: 'iptaller hariç' }),
    panelCard('Sipariş dağılımı', distribution,
      { target: 'store_orders', targetLabel: 'Siparişler' }),
    panelCard('En çok satan ürünler', nodes.top,
      { hint: 'adede göre', target: 'store_products', targetLabel: 'Ürün Yönetimi' }),
    panelCard('Son siparişler', nodes.recent,
      { target: 'store_orders', targetLabel: 'Siparişler' }),
    panelCard('Kritik stok', nodes.stock,
      { target: 'store_products', targetLabel: 'Ürün Yönetimi' }),
    panelCard('Bekleyen işler', nodes.pending),
  );

  // Denetim izi kartı YALNIZCA "son işlemler" verebilen bir yetenek varsa
  // çizilir; yoksa kart HİÇ görünmez.
  //
  // BULUNAN HATA (2026-10-10): kart `store.audit.for` var mı diye bakıyor ve
  // `auditFor.recent?.(10)` çağırıyordu. O yetenek DÜZ BİR FONKSİYONDUR —
  // `(varlık, kayıtNo, seçenekler)` alır, tek kaydın geçmişini verir;
  // `recent` diye bir alanı yok. Çağrı her seferinde `undefined` → `[]`
  // dönüyor, kart HER AÇILIŞTA boş tablo gösteriyordu. Ayrıca UDİT ekranı
  // menüden kaldırıldığı için yeteneğin kendisi de artık yüklenmiyor.
  // Koşul bu yüzden yeteneğin VARLIĞINA değil, `recent` fonksiyonuna bakar.
  const auditFor = capability('store.audit.for');
  if (typeof auditFor?.recent === 'function') {
    const host = h('div', 'sd-slot');
    grid.append(panelCard('Son mağaza işlemleri', host));
    // "Tekrar dene" GERÇEKTEN yeniden dener; eskiden boş bir fonksiyona
    // bağlıydı ve tıklanınca hiçbir şey yapmıyordu.
    const loadAudit = () => Promise.resolve(auditFor.recent(10))
      .then((rows) => {
        host.replaceChildren(dataTable({
          columns: [
            { key: 'createdAt', label: 'Zaman', width: '140px' },
            { key: 'action', label: 'İşlem', width: 'minmax(0, 1fr)' },
            { key: 'actor', label: 'Kim', width: '120px' },
          ],
          rows: rows || [],
          dense: true,
          rowKey: (row) => `${row.createdAt}-${row.action}`,
        }).node);
      })
      .catch((error) => failCard(host, error.message, loadAudit));
    loadAudit();
  }

  nodes.body = h('div', 'sd-body');
  const boardView = [nodes.kpi, nodes.notes, grid];
  nodes.body.append(...boardView);

  view.append(tabs.node, nodes.filters.node, nodes.status.node, nodes.body);

  let current = 'board';
  let section = 'general';

  /** Kaydedilmemiş yapılandırma değişikliği varsa çıkmadan önce sorar. */
  async function mayLeaveConfig() {
    if (section !== 'config' || !configDirty().length) return true;
    return confirmSimple(nodes.root, {
      title: 'Kaydedilmemiş değişiklik var',
      description: `${configDirty().length} alan değişti ve mağazaya yazılmadı. `
        + 'Çıkarsanız bu değişiklikler kaybolur.',
      confirmLabel: 'Çık, kaydetme',
      danger: true,
    });
  }

  /**
   * Sekme değişimi. `tabBar` seçimi kendisi yapıp SONRA haber veriyor, yani
   * veto edilemiyor: kaydedilmemiş değişiklik varsa kullanıcıya sorulur ve
   * "kal" denirse sekme geri alınır. Yapılandırma DOM'una dokunulmadığı için
   * yazdıkları yerinde kalır.
   */
  async function showView(key) {
    if (current === 'settings' && key !== 'settings' && !(await mayLeaveConfig())) {
      tabs.select('settings', false);
      return;
    }
    current = key;
    const detached = key !== 'board';
    nodes.filters.node.hidden = detached;
    nodes.status.node.hidden = detached;
    dropForms();
    configFilter?.cancel?.();
    if (key === 'settings') {
      config = emptyConfig();
      nodes.body.replaceChildren();
      renderSettingsArea(nodes.body);
    } else {
      section = 'general';
      config = emptyConfig();
      nodes.body.replaceChildren(...boardView);
      if (!state.summary) refreshAll();
    }
  }

  /**
   * Ayarlar — ALT BAŞLIKLARLA tek sekme (§4): Genel (kanal, dil, kimlik,
   * bakım modu, rapor klasörü) · Ödeme, kargo ve mağaza (eski Yapılandırma
   * sekmesi) · Sistem durumu (eski Pano kartı).
   */
  function renderSettingsArea(host) {
    section = 'general';
    const area = h('div', 'sd-section');
    const paint = () => {
      dropForms();
      configFilter?.cancel?.();
      config = emptyConfig();
      area.replaceChildren();
      if (section === 'config') {
        renderConfig(area);
      } else if (section === 'system') {
        const slot = h('div', 'sd-slot');
        area.append(card('Sistem durumu', slot,
          'Bağlantı, yedek, sanal POS, kargo ve fiş kuyruğu'));
        nodes.system = slot;
        loadSystem();
      } else {
        renderSettings(area);
      }
    };
    const sub = tabBar([
      { key: 'general', label: 'Genel' },
      { key: 'config', label: 'Ödeme, kargo ve mağaza' },
      { key: 'system', label: 'Sistem durumu' },
    ], 'general', async (key) => {
      if (key !== 'config' && !(await mayLeaveConfig())) {
        sub.select('config', false);
        return;
      }
      section = key;
      paint();
    });
    sub.node.classList.add('sd-subtabs');
    host.append(sub.node, area);
    paint();
  }

  root.replaceChildren(view);
  nodes.status.set('Pano yükleniyor…');
  loadReference().then(() => refreshAll());

  return () => {
    mounted = false;
    stopSummaryPoll();               // kapanmış panel yoklama atmasın
    nodes.filters?.destroy();        // tarih alanları global dinleyici tutar
    configFilter?.cancel?.();        // bekleyen arama çağrısı iptal
    configFilter = null;
    dropForms();
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    config = emptyConfig();
    nodes.system = null;
    busy = false;
  };
}

/**
 * Kanal listesi ve varsayılan tercihler — süzgeçler dolmadan veri çekilmez.
 *
 * `/reference` HAFİF uçtur: eskiden burada `/settings` çağrılıyordu ve o uç
 * mağazanın ayar bölümünü de okuduğu için her pano açılışı mağazaya fazladan
 * bir istek atıyordu. Kanal listesi geçidin önbellekli anlık görüntüsünden
 * gelir.
 *
 * TEK KANALLI MAĞAZADA "Kanal" KUTUSU ÇİZİLMEZ (`applyChoiceFilter`): seçecek
 * bir şey olmayan soru sorulmaz, değeri de gönderilmez — pano çalışma
 * kanalını (yerel tercih) okur.
 */
async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    // Ayarlar okunamazsa süzgeç varsayılanla çalışır; pano yine açılır (K7).
    return;
  }
  applyChoiceFilter(nodes.filters, 'channel', payload.channels || [],
    { allLabel: `Çalışma kanalı (${payload.local.channel})` });
  nodes.filters.set('compare', payload.local.compare || 'previous', { baseline: true });
}
