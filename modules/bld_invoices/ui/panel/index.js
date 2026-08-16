// Faturalar paneli — BLD bilgi belgelerinin listesi, kesilmesi, iptali ve arşivi.
//
// NE YAPAR: sunucu tarafında sayfalanmış belge listesi ve süzgeçleri; satırdan
// çekmecede belgenin DONMUŞ içeriği (`snapshot_json`); sipariş ya da dönem
// belgesi kesme (önce prova, sonra gerçek); belge iptali (gerekçeli onay);
// A4 üretme → önizleme → CUPS'a basma; bu makinede üretilmiş dosyaların
// künyesi ve yerel denetim izi.
//
// BU BELGENİN MALİ DEĞERİ YOKTUR. Ekranın tepesinde KAPATILAMAZ bir bant
// durur ve üretilen her dosyanın her sayfasında aynı dipnot basılır. "GİB'e
// gönder", "e-Fatura" ya da "KDV" diye bir şey YOKTUR — olmayan bir uca bağlı
// düğme, kullanıcıya gönderdiğini sandırırdı.
//
// NE YAPMAZ — ve bunu gizlemez:
//  · BELGE DÜZENLEMEZ. Sözleşmede `PATCH` yok: kesilmiş bir belgenin içeriği
//    değiştirilemez. Yanlışsa İPTAL edilir ve yenisi kesilir; çekmece bu yolu
//    açıkça gösterir ("Düzelt" değil, "İptal et ve yenisini kes").
//  · BELGE SİLMEZ. Sözleşmede `DELETE` yok: numara boşluğu bırakan bir seri,
//    "44 nerede" sorusunu cevapsız bırakır. İptal edilmiş belge listede
//    `İptal` rozetiyle DURMAYA DEVAM EDER.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//  · UZAK VERİYİ KOPYALAMAZ. Liste her açılışta sunucudan gelir; yereldeki tek
//    şey ÜRETİLEN DOSYANIN künyesi ve yazma denemesinin izidir.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · ALTTAKİ TOPLAM SAYFANIN DEĞİL, SÜZGECİN TOPLAMIDIR (`issued_total_kurus`)
//    ve iptal edilmiş belgeler ona girmez. Satırlardan toplasaydık sayfa
//    değiştikçe "genel toplam" değişirdi.
//  · BELGE KESMEK İKİ ADIMDIR: önce prova (`dryRun: true`) — numara ÜRETMEZ,
//    yalnız kalem sayısını ve toplamı söyler — sonra gerçek çağrı
//    (`dryRun: false`). Bayrak her iki çağrıda da AÇIKÇA gönderilir; geçidin
//    varsayılanı `config/local.yaml` ile değişebilir ve o dosya git dışıdır.
//  · YANITTAKİ `dry_run` OKUNUR. Bir kurulum provayı ayardan geri açarsa ekran
//    "belge kesildi" DEMEMELİ; sunucunun söylediği ile istediğimiz ayrıysa
//    uyarı çıkar.
//  · AYNI SİPARİŞ/DÖNEM İÇİN İKİNCİ BELGE KESİLMEZ. Sunucu 409 verir; ekran
//    "önce eskisini iptal edin" der ve numarayı yazar.
//  · SUNUCU UÇLARI HENÜZ YAYINDA OLMAYABİLİR. `control_endpoint_missing`
//    beklenen bir durumdur, hata değil: ekran bunu ayrı bir cümleyle söyler ve
//    yerel arşiv sekmesi çalışmaya devam eder (K7).
//  · YAZICI İSTEĞE BAĞLI. Yoksa belge yine üretilir ve klasöre yazılır; yalnız
//    baskı düğmesi kapanır ve nedenini söyler.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_invoices/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_invoices/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, copyText, h,
  loadStyles, money, num, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { dateField } from '../../ui-kit/datefield.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/bld_invoices';

/** Gerekçe alt sınırı — sunucu ve geçit de denetler; bu erken geri bildirim. */
const REASON_MIN = 10;

/** Gerekçe üst sınırı (`invoices.md` → 500). */
const REASON_MAX = 500;

/** Dönem belgesinde aralık tavanı (sözleşme). */
const MAX_PERIOD_DAYS = 62;

/**
 * ÜRETİLEN HER BELGEDE geçen zorunlu dipnot. Ekranda kapatılamaz bir bant,
 * PDF'te her sayfanın altı. Metin backend'de de sabittir (`documents.NOTICE`);
 * iki yerde durması bilinçli — bant, sunucu düşükken de görünmelidir.
 */
const NOTICE = 'Bu belge mali değeri olmayan bilgi amaçlı bir dokümandır; '
  + 'fatura yerine geçmez.';

const STATUS_OPTIONS = [
  { value: '', label: 'Tümü — durum' },
  { value: 'issued', label: 'Geçerli' },
  { value: 'void', label: 'İptal' },
];

/** Renk TEK BAŞINA anlam taşımaz: rozetin içinde yazı da var. */
const STATUS_TONE = { issued: 'good', void: 'bad' };
const STATUS_LABEL = { issued: 'Geçerli', void: 'İptal' };

const KIND_LABEL = { pdf: 'A4 belge', html: 'Sunucu HTML', list: 'Liste dökümü' };

let api = null;
let toast = null;
let report = null;
let state = freshState();
const nodes = {};
const closers = [];

/**
 * Başlangıç durumu FONKSİYONDUR: sabit nesne olsaydı iç içe alanlar yayılırken
 * referansla kopyalanır ve panel kapanıp açıldığında önceki oturumun sayfası
 * ve süzgeci geri gelirdi.
 */
function freshState() {
  return {
    tab: 'invoices',
    items: [],
    meta: {},
    page: 1,
    perPage: 25,
    connected: false,
    error: '',
    code: '',
    printerAvailable: false,
    filters: { q: '', status: '', range: { start: '', end: '' } },
    // Başka ekrandan `open('bld_invoices', {order_id})` ile gelinince dolar.
    // Süzgeç şeridinde karşılığı yok; kendi şeridiyle gösterilir ve
    // bırakılabilir — yoksa kullanıcı "belgelerin yarısı nerede" derdi.
    pinned: { order_id: 0, subscription_id: 0, customer_id: 0 },
    archive: [],
    audit: [],
    archiveLoaded: false,
  };
}

// --------------------------------------------------------------- yardımcı

function statusBadge(row) {
  const key = String(row?.status || '');
  return badge(STATUS_LABEL[key] || key || '—', STATUS_TONE[key] || 'dim');
}

/** `12345` → `12.345` (kit `num`), boş değerde tire. */
function count(value) {
  return value === null || value === undefined ? '—' : num(value);
}

function pinnedLabel() {
  const { order_id: orderId, subscription_id: subId, customer_id: custId } = state.pinned;
  if (orderId) return `Sipariş #${orderId}`;
  if (subId) return `Abonelik #${subId}`;
  if (custId) return `Müşteri #${custId}`;
  return '';
}

/** Süzgeçlerin sunucuya gidecek hâli. Yerel süzme YOKTUR. */
function query() {
  const { q, status, range } = state.filters;
  return {
    q: q || '',
    status: status || '',
    date_from: range?.start || '',
    date_to: range?.end || '',
    order_id: state.pinned.order_id || 0,
    subscription_id: state.pinned.subscription_id || 0,
    customer_id: state.pinned.customer_id || 0,
  };
}

function queryString(extra = {}) {
  const merged = { ...query(), ...extra };
  const parts = [];
  for (const [key, value] of Object.entries(merged)) {
    if (value === '' || value === 0 || value === null || value === undefined) continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

/**
 * Uç çağrısı. İSTİSNA YUTULMAZ, ÇEVRİLİR: panel her yerde aynı `{ok, error}`
 * biçimini görsün diye ağ hatası da aynı zarfa konur.
 */
async function call(path, options) {
  try {
    return await api(`${BASE}${path}`, options);
  } catch (error) {
    return { ok: false, error: error?.message || 'İstek gönderilemedi.' };
  }
}

/** K7: bağlantı yoksa ekranın tepesine ne yazılacak. */
function connectionNotice() {
  if (state.connected) return null;
  if (state.code === 'control_endpoint_missing') {
    return alertBox('Sunucudaki fatura uçları bu turda henüz yayında değil. '
      + 'Bu beklenen bir durumdur: ekran hazır, sunucu tarafı yazıldığında liste '
      + 'kendiliğinden dolar. Yerel arşiv sekmesi çalışmaya devam ediyor.', 'warn');
  }
  return alertBox(state.error || 'BLD sunucusuna ulaşılamadı.', 'bad');
}

// ==================================================================== liste

async function refresh() {
  nodes.status.set('Belgeler okunuyor…');
  const payload = await call(`/invoices${queryString({ page: state.page, per_page: state.perPage })}`);
  state.connected = Boolean(payload?.connected);
  state.error = payload?.error || '';
  state.code = payload?.code || '';
  state.items = payload?.items || [];
  state.meta = payload?.meta || {};
  state.page = payload?.page || state.page;
  state.perPage = payload?.per_page || state.perPage;
  state.printerAvailable = Boolean(payload?.printer_available);
  // Arşiv sekmesi açıkken listeyi çizmek, kullanıcının baktığı ekranı altından
  // çekmek olurdu; veri yine tazelenir, çizim sekmesine döndüğünde olur.
  if (state.tab === 'invoices') paintInvoices();
  else nodes.status.set(statusText(), !state.connected);
}

function totalLine() {
  const line = h('div', 'bi-total');
  const issued = state.meta?.issued_total_kurus;
  line.append(h('span', 'bi-total-label', 'Süzgeçlenmiş geçerli belge toplamı'));
  line.append(h('b', 'bi-total-value',
    issued === null || issued === undefined ? '—' : money(issued)));
  line.append(h('span', 'bi-sub',
    'İptal edilmiş belgeler bu toplama girmez ve toplam sayfa değiştirince değişmez.'));
  return line;
}

function paintInvoices() {
  const body = nodes.body;
  body.replaceChildren();
  body.append(alertBox(NOTICE, 'warn'));

  const warning = connectionNotice();
  if (warning) body.append(warning);

  if (pinnedLabel()) {
    const strip = h('div', 'bi-pinned');
    strip.append(h('span', undefined, `Bağlam süzgeci: ${pinnedLabel()}`));
    strip.append(button('Süzgeci bırak', {
      onClick: () => {
        state.pinned = { order_id: 0, subscription_id: 0, customer_id: 0 };
        state.page = 1;
        refresh();
      },
    }));
    body.append(strip);
  }

  body.append(nodes.filters.node);

  const table = dataTable({
    columns: [
      { key: 'invoice_no', label: 'Belge no', width: 'minmax(0, 1.2fr)' },
      { key: 'status', label: 'Durum', width: '110px', cell: (row) => statusBadge(row) },
      { key: 'customer_label', label: 'Müşteri', width: 'minmax(0, 2fr)' },
      { key: 'source_label', label: 'Kaynak', width: 'minmax(0, 1.6fr)' },
      { key: 'issued_at_label', label: 'Düzenlenme', width: 'minmax(0, 1.2fr)' },
      {
        key: 'total_kurus',
        label: 'Tutar',
        width: '130px',
        align: 'num',
        cell: (row) => money(row.total_kurus),
      },
    ],
    rows: state.items,
    onRow: (row) => openCard(row.id),
    empty: emptyState({
      title: state.connected ? 'Bu süzgeçte belge yok' : 'Liste okunamadı',
      text: state.connected
        ? 'Süzgeci genişletin ya da yeni bir belge kesin.'
        : 'Sunucuya ulaşıldığında liste kendiliğinden dolar.',
      actions: [button('Süzgeci temizle', { onClick: () => nodes.filters.reset() })],
    }),
  });
  body.append(card('Belgeler', table.node));

  const pages = pager({
    total: Number(state.meta?.total || state.items.length || 0),
    page: state.page,
    size: state.perPage,
    onChange: ({ page, size }) => {
      state.page = page;
      state.perPage = size;
      refresh();
    },
  });
  body.append(pages.node, totalLine(), nodes.lastFile.node);

  nodes.status.set(statusText(), !state.connected);
}

function statusText() {
  if (!state.connected) return state.error || 'BLD sunucusuna ulaşılamadı.';
  const total = Number(state.meta?.total || state.items.length || 0);
  return `${count(total)} belge · sayfa ${state.page}`;
}

// ================================================================== çekmece

async function openCard(invoiceId) {
  const view = drawer(nodes.root, { title: 'Belge', subtitle: 'Okunuyor…' });
  closers.push(view.close);
  view.body.append(skeletonRows(6, 2));

  const payload = await call(`/invoices/${invoiceId}`);
  view.body.replaceChildren();

  if (!payload?.connected) {
    view.body.append(alertBox(payload?.error || 'Belge okunamadı.', 'bad'));
    return;
  }
  const row = payload.data || {};
  view.setTitle(`${row.invoice_no || `#${invoiceId}`}`);
  paintCard(view, row);
}

function paintCard(view, row) {
  const snapshot = row.snapshot || {};
  view.body.append(alertBox(NOTICE, 'warn'));

  if (row.status === 'void') {
    view.body.append(alertBox(
      `BU BELGE İPTAL EDİLMİŞTİR — ${row.void_at_label || '—'}. Geçerli değildir. `
      + `Gerekçe: ${row.void_reason || '—'}`, 'bad'));
  }

  view.body.append(kpiRow([
    { label: 'Durum', value: STATUS_LABEL[row.status] || row.status || '—' },
    { label: 'Düzenlenme', value: row.issued_at_label || '—' },
    { label: 'Kaynak', value: row.source_label || '—' },
    { label: 'Toplam', value: money(row.total_kurus) },
  ]));

  view.body.append(card('Düzenleyen', pairList([
    ['Unvan', snapshot.issuer?.name],
    ['Adres', snapshot.issuer?.address],
    ['Telefon', snapshot.issuer?.phone],
    ['E-posta', snapshot.issuer?.email],
  ]), 'Belgenin donmuş içeriğinden okunur; şirket bilgisi sonradan değişse bile '
    + 'bu belge eski bilgiyi gösterir.'));

  view.body.append(card('Alıcı', pairList([
    ['Unvan / ad', snapshot.customer?.label],
    ['Yetkili', snapshot.customer?.contact_person],
    ['Vergi dairesi', snapshot.customer?.tax_office],
    ['Vergi / TC no', snapshot.customer?.tax_no],
    ['Adres', snapshot.customer?.address],
    ['Telefon', snapshot.customer?.phone],
  ])));

  const lines = dataTable({
    dense: true,
    columns: [
      { key: 'description', label: 'Açıklama', width: 'minmax(0, 2fr)' },
      { key: 'order_number', label: 'Sipariş', width: 'minmax(0, 1fr)' },
      { key: 'quantity', label: 'Adet', width: '70px', align: 'num' },
      {
        key: 'unit_price_kurus',
        label: 'Birim',
        width: '110px',
        align: 'num',
        cell: (line) => money(line.unit_price_kurus),
      },
      {
        key: 'line_total_kurus',
        label: 'Tutar',
        width: '120px',
        align: 'num',
        cell: (line) => money(line.line_total_kurus),
      },
    ],
    rows: snapshot.lines || [],
    empty: emptyState({ title: 'Kalem yok', text: 'Belgede satır bulunmuyor.' }),
  });
  view.body.append(card('Kalemler', lines.node));

  view.body.append(card('Toplam', pairList([
    ['Ara toplam', money(snapshot.totals?.subtotal_kurus)],
    ['Teslimat', money(snapshot.totals?.delivery_fee_kurus)],
    ['Genel toplam', money(snapshot.totals?.total_kurus)],
    ['Ödeme', paymentText(snapshot.payment)],
  ]), 'Vergi satırı YOKTUR: KDV hesaplamak belgeye mali değer atfetmek olurdu.'));

  // Belgenin KENDİ ibaresi de gösterilir: sonradan değişse bile bu belge eski
  // cümleyi taşır.
  if (snapshot.notice && snapshot.notice !== NOTICE) {
    view.body.append(hintBox(snapshot.notice));
  }

  view.body.append(cardActions(view, row));
}

function paymentText(payment) {
  if (!payment) return '';
  const method = { online: 'Online', cash: 'Nakit' }[payment.method] || payment.method || '';
  const status = {
    paid: 'Ödendi', pending: 'Bekliyor', failed: 'Başarısız', refunded: 'İade edildi',
  }[payment.status] || payment.status || '';
  return [method, status].filter(Boolean).join(' · ');
}

function pairList(pairs) {
  const list = h('dl', 'bi-pairs');
  let written = 0;
  for (const [label, value] of pairs) {
    const text = value === null || value === undefined ? '' : String(value);
    if (!text.trim()) continue;              // boş satır tire ile doldurulmaz
    list.append(h('dt', undefined, label));
    list.append(h('dd', undefined, text));
    written += 1;
  }
  if (!written) list.append(h('dd', 'bi-sub', 'Bu blok belgede boş.'));
  return list;
}

function cardActions(view, row) {
  const box = h('div', 'bi-actions');

  box.append(button('A4 üret ve yazdır', {
    variant: 'primary',
    onClick: async () => {
      const produced = await report.run('invoice', { invoice_id: row.id });
      if (produced?.path) nodes.lastFile.set(produced.path);
      if (produced?.ok) loadArchive(true);
    },
  }));

  if (!state.printerAvailable) {
    // K7: yazıcı yeteneği yoksa ekran ÇALIŞMAYA DEVAM EDER. Belge yine üretilir
    // ve klasöre yazılır; yalnız önizlemedeki "Yazdır" düğmesi kapalı gelir.
    box.append(hintBox('Bu kurulumda yazıcı yeteneği yok: belge üretilir ve rapor '
      + 'klasörüne yazılır, ama buradan kâğıda basılamaz.'));
  }

  box.append(button('Sunucu belgesini kaydet (HTML)', {
    title: 'Sunucunun yazdırılabilir HTML dosyasını rapor klasörüne yazar.',
    onClick: async () => {
      const saved = await call(`/invoices/${row.id}/html`, { method: 'POST' });
      if (!saved?.ok) {
        toast(saved?.error || 'Belge kaydedilemedi.', 'bad');
        return;
      }
      nodes.lastFile.set(saved.path);
      toast(`${saved.name} kaydedildi.`, 'good');
      loadArchive(true);
    },
  }));

  if (row.status === 'void') {
    box.append(hintBox('Bu belge iptal edilmiş. Düzeltme, iptalden sonra kesilen '
      + 'YENİ bir belgedir; iptal geri alınmaz.'));
  } else {
    box.append(button('İptal et ve yenisini kes', {
      variant: 'danger',
      onClick: () => voidInvoice(view, row),
    }));
    box.append(hintBox('Belge DÜZENLENEMEZ. Yanlış bir belge iptal edilir ve yerine '
      + 'yenisi kesilir; iptal edilen numara seride ölü kalır, boşluk bırakılmaz.'));
  }
  return box;
}

// =================================================================== iptal

async function voidInvoice(view, row) {
  const reason = await confirmWithReason(nodes.root, {
    title: `${row.invoice_no} iptal edilsin mi?`,
    description: 'GERİ ALINAMAZ. Müşterinin elindeki kâğıt geçersiz olur, numara '
      + 'seride ölü kalır ve yeniden kullanılmaz. Bu gerekçe belgenin üzerine '
      + 'basılacak ve denetim izine yazılacak.',
    confirmLabel: 'İptal et',
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;
  if (reason.length > REASON_MAX) {
    toast(`Gerekçe en çok ${REASON_MAX} karakter olabilir.`, 'bad');
    return;
  }

  // AÇIK BAYRAK: `dryRun: false`. Alanı hiç göndermemek, kurulumun
  // varsayılanına güvenmek olurdu ve o dosya git dışıdır.
  const result = await call(`/invoices/${row.id}/void`, {
    method: 'POST',
    body: { reason, dryRun: false },
  });
  if (!result?.ok) {
    toast(result?.error || 'Belge iptal edilemedi.', 'bad');
    return;
  }
  if (result.server_dry_run) {
    // Sunucu "prova" dediyse HİÇBİR ŞEY DEĞİŞMEDİ; "iptal edildi" demek yalan olurdu.
    toast('Sunucu bu isteği kuru prova saydı: belge İPTAL EDİLMEDİ. '
      + 'Kurulumdaki dry_run ayarı açık olabilir.', 'warn');
    return;
  }
  toast(`${result.data?.invoice_no || row.invoice_no} iptal edildi.`, 'good');
  view.close();
  refresh();
}

// ============================================================== belge kesme

function createDrawer() {
  const view = drawer(nodes.root, {
    title: 'Yeni belge kes',
    subtitle: 'Sipariş belgesi ya da dönem belgesi',
  });
  closers.push(view.close);

  const form = { mode: 'order', order_id: '', subscription_id: '', subscription_payment_id: '' };
  const periodStart = dateField({ value: '', label: 'Dönem başlangıcı', onChange: () => {} });
  const periodEnd = dateField({ value: '', label: 'Dönem bitişi', onChange: () => {} });
  closers.push(() => { periodStart.destroy(); periodEnd.destroy(); });

  view.body.append(alertBox(NOTICE, 'warn'));
  view.body.append(hintBox('Belge iki kipten biriyle kesilir ve sonradan DÜZENLENEMEZ. '
    + 'Aynı sipariş ya da aynı dönem için geçerli bir belge varsa ikincisi kesilmez; '
    + 'önce eskisi iptal edilir.'));

  const modeRow = h('div', 'bi-mode');
  const orderBox = h('div', 'bi-mode-fields');
  const periodBox = h('div', 'bi-mode-fields');

  const modeSelect = h('select', 'kit-select');
  modeSelect.setAttribute('aria-label', 'Belge kipi');
  for (const option of [
    { value: 'order', label: 'Sipariş belgesi' },
    { value: 'subscription', label: 'Dönem belgesi (abonelik)' },
  ]) {
    const item = h('option', undefined, option.label);
    item.value = option.value;
    modeSelect.append(item);
  }
  modeSelect.addEventListener('change', () => {
    form.mode = modeSelect.value;
    paintMode();
  });
  modeRow.append(h('span', 'kit-filter-label', 'Kip'), modeSelect);

  const orderInput = numberInput('Sipariş kimliği', (value) => { form.order_id = value; });
  orderBox.append(orderInput.node);

  const subInput = numberInput('Abonelik kimliği', (value) => { form.subscription_id = value; });
  const payInput = numberInput('Dönem ödemesi kimliği (isteğe bağlı)',
    (value) => { form.subscription_payment_id = value; });
  periodBox.append(subInput.node);
  periodBox.append(labelled('Dönem başlangıcı', periodStart.node));
  periodBox.append(labelled('Dönem bitişi', periodEnd.node));
  periodBox.append(payInput.node);
  periodBox.append(h('div', 'bi-sub',
    `Aralık en çok ${MAX_PERIOD_DAYS} gün olabilir; daha uzun dönem birden çok belgedir.`));

  function paintMode() {
    orderBox.hidden = form.mode !== 'order';
    periodBox.hidden = form.mode !== 'subscription';
  }
  paintMode();

  const actions = h('div', 'bi-actions');
  actions.append(button('Provayı al ve belgeyi kes', {
    variant: 'primary',
    onClick: () => submitCreate(view, form, periodStart, periodEnd),
  }));

  view.body.append(card('Belge kipi', modeRow));
  view.body.append(orderBox, periodBox, actions);
}

function labelled(text, node) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', text));
  wrap.append(node);
  return wrap;
}

function numberInput(label, onChange) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label));
  const input = h('input', 'kit-input');
  input.type = 'number';
  input.min = '0';
  input.step = '1';
  input.setAttribute('aria-label', label);
  input.addEventListener('input', () => onChange(input.value));
  wrap.append(input);
  return { node: wrap, input };
}

/**
 * İKİ ADIM, TEK GEREKÇE: önce prova (`dryRun: true`) — numara ÜRETMEZ, yalnız
 * kalem sayısını ve toplamı söyler — sonra onay ve gerçek çağrı
 * (`dryRun: false`). Provayı atlamak, kullanıcının kaç kalemlik ve kaç liralık
 * bir belge kestiğini ancak kâğıda bakınca öğrenmesi demekti.
 */
async function submitCreate(view, form, periodStart, periodEnd) {
  const payload = {
    order_id: form.mode === 'order' ? Number(form.order_id || 0) : 0,
    subscription_id: form.mode === 'subscription' ? Number(form.subscription_id || 0) : 0,
    period_start: form.mode === 'subscription' ? periodStart.get() : '',
    period_end: form.mode === 'subscription' ? periodEnd.get() : '',
    subscription_payment_id: form.mode === 'subscription'
      ? Number(form.subscription_payment_id || 0) : 0,
  };
  const problem = createProblem(form.mode, payload);
  if (problem) {
    toast(problem, 'bad');
    return;
  }

  const reason = await confirmWithReason(nodes.root, {
    title: 'Belge kesilecek',
    description: 'Önce kuru prova alınır (numara üretmez), sonucu onaylarsanız belge '
      + 'kesilir. Kesilen belge DÜZENLENEMEZ ve SİLİNEMEZ; yanlışsa iptal edilir.',
    confirmLabel: 'Provayı al',
    danger: false,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter)`,
  });
  if (!reason) return;

  const dry = await call('/invoices', {
    method: 'POST',
    body: { ...payload, reason, dryRun: true },
  });
  if (!dry?.ok) {
    toast(dry?.error || 'Prova alınamadı.', 'bad');
    return;
  }
  const lines = dry.data?.line_count;
  const total = dry.data?.total_kurus;
  const existing = dry.data?.existing_invoice_id;
  if (existing) {
    toast(`Bu kaynağın zaten geçerli bir belgesi var (#${existing}). `
      + 'İkinci belge kesilmez; önce eskisini iptal edin.', 'warn');
    return;
  }

  const go = await confirmSimple(nodes.root, {
    title: 'Belge kesilsin mi?',
    description: `Prova: ${count(lines)} kalem · ${money(total)}. `
      + 'Onaylarsanız belge numarası üretilir ve geri alınamaz.',
    confirmLabel: 'Belgeyi kes',
    danger: true,
  });
  if (!go) return;

  // AÇIK BAYRAK: `dryRun: false`.
  const result = await call('/invoices', {
    method: 'POST',
    body: { ...payload, reason, dryRun: false },
  });
  if (!result?.ok) {
    toast(result?.error || 'Belge kesilemedi.', 'bad');
    return;
  }
  if (result.server_dry_run) {
    toast('Sunucu bu isteği kuru prova saydı: BELGE KESİLMEDİ. '
      + 'Kurulumdaki dry_run ayarı açık olabilir.', 'warn');
    return;
  }
  toast(`${result.data?.invoice_no || 'Belge'} kesildi.`, 'good');
  view.close();
  await refresh();
  if (result.data?.id) openCard(result.data.id);
}

function createProblem(mode, payload) {
  if (mode === 'order') {
    return payload.order_id > 0 ? '' : 'Sipariş kimliği gerekli.';
  }
  if (!(payload.subscription_id > 0)) return 'Abonelik kimliği gerekli.';
  if (!payload.period_start || !payload.period_end) {
    return 'Dönem başlangıcı ve bitişi gerekli.';
  }
  if (payload.period_end < payload.period_start) {
    return 'Dönem bitişi başlangıcından önce olamaz.';
  }
  // Gün farkı ISO dizeden hesaplanır; `Date` üzerinden gitmek yaz saati
  // geçişlerinde bir gün kaydırabiliyor.
  const span = Math.round(
    (Date.parse(`${payload.period_end}T00:00:00Z`)
      - Date.parse(`${payload.period_start}T00:00:00Z`)) / 86400000) + 1;
  if (span > MAX_PERIOD_DAYS) {
    return `Dönem aralığı en çok ${MAX_PERIOD_DAYS} gün olabilir; ${span} gün istendi.`;
  }
  return '';
}

// ==================================================================== arşiv

async function loadArchive(force = false) {
  if (state.archiveLoaded && !force) return;
  const [archive, audit] = await Promise.all([
    call('/archive?limit=100'),
    call('/audit?limit=100'),
  ]);
  state.archive = archive?.items || [];
  state.audit = audit?.items || [];
  state.archiveLoaded = true;
  if (state.tab === 'archive') paintArchive();
}

function paintArchive() {
  const body = nodes.body;
  body.replaceChildren();
  body.append(alertBox(NOTICE, 'warn'));
  body.append(hintBox('Buradaki kayıtlar BELGE DEĞİL, bu makinede ÜRETİLMİŞ DOSYALARDIR: '
    + 'yol, özet (sha256), boyut ve basıldığı an. Belgenin kendisi her zaman '
    + 'sunucudan okunur; dosya silinse bile künyesi burada kalır.'));

  const files = dataTable({
    dense: true,
    columns: [
      { key: 'name', label: 'Dosya', width: 'minmax(0, 2fr)' },
      {
        key: 'kind',
        label: 'Tür',
        width: '130px',
        cell: (row) => KIND_LABEL[row.kind] || row.kind || '—',
      },
      { key: 'invoice_no', label: 'Belge no', width: 'minmax(0, 1.1fr)' },
      { key: 'created_at', label: 'Üretildi', width: 'minmax(0, 1.2fr)' },
      {
        key: 'printed_at',
        label: 'Basıldı',
        width: 'minmax(0, 1.2fr)',
        cell: (row) => (row.printed_at
          ? `${row.printed_at} (${count(row.print_copies)} kopya)`
          : 'Basılmadı'),
      },
      {
        key: 'sha256',
        label: 'Özet',
        width: '150px',
        cell: (row) => {
          const chip = h('button', 'bi-hash', clipped(row.sha256));
          chip.type = 'button';
          chip.title = `${row.sha256}\n${row.path}`;
          chip.addEventListener('click', async (event) => {
            event.stopPropagation();
            const done = await copyText(row.path);
            toast(done ? 'Dosya yolu panoya kopyalandı.' : 'Kopyalanamadı.',
              done ? 'good' : 'bad');
          });
          return chip;
        },
      },
    ],
    rows: state.archive,
    empty: emptyState({
      title: 'Henüz dosya üretilmemiş',
      text: 'Bir belgeyi açıp "A4 üret ve yazdır" dediğinizde künyesi buraya düşer.',
    }),
  });
  body.append(card('Üretilen dosyalar', files.node));

  const trail = dataTable({
    dense: true,
    columns: [
      { key: 'created_at', label: 'An', width: 'minmax(0, 1.2fr)' },
      { key: 'action', label: 'İşlem', width: '150px' },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)' },
      { key: 'result', label: 'Sonuç', width: '110px' },
      {
        key: 'reason',
        label: 'Gerekçe',
        width: 'minmax(0, 2fr)',
        cell: (row) => {
          const cell = h('span');
          clip(cell, row.reason || '', 60);
          return cell;
        },
      },
    ],
    rows: state.audit,
    empty: emptyState({
      title: 'Yerel iz boş',
      text: 'Belge kesme ve iptal denemeleri buraya yazılır; satır silinmez.',
    }),
  });
  body.append(card('Yerel denetim izi', trail.node,
    'Sunucudaki denetim tablosunun yerine değil, ONDAN ÖNCE yazılır: ağ koparsa '
    + '"kim neyi denedi" sorusunun cevabı yalnız burada kalır.'));

  nodes.status.set(`${count(state.archive.length)} dosya · ${count(state.audit.length)} iz`);
}

function clipped(value) {
  const text = String(value || '');
  return text ? `${text.slice(0, 10)}…` : '—';
}

// ==================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = freshState();

  const payload = ctx.payload || {};
  state.pinned = {
    order_id: Number(payload.order_id || 0),
    subscription_id: Number(payload.subscription_id || 0),
    customer_id: Number(payload.customer_id || 0),
  };

  const view = h('div', 'kit-panel bi');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  // Katmanlar PANEL KÖKÜNE eklenir, `document.body`'ye değil.
  report = reportChain({ api, root: view, toast, base: BASE });
  nodes.lastFile = report.lastFileLine();

  nodes.tabs = tabBar([
    { key: 'invoices', label: 'Belgeler' },
    { key: 'archive', label: 'Arşiv ve iz' },
  ], 'invoices', (key) => showTab(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px', placeholder: 'Belge no veya müşteri' },
      { kind: 'select', key: 'status', label: 'Durum', options: STATUS_OPTIONS },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // dateRange kitin kendi takvimini kullanır.
      { kind: 'dateRange', key: 'range', label: 'Düzenlenme', start: '', end: '' },
    ],
    onChange: (values) => {
      state.filters = values;
      state.page = 1;             // süzgeç değişti; 7. sayfada kalmak boş liste gösterirdi
      refresh();
    },
    actions: [
      button('Yeni belge', { variant: 'primary', onClick: () => createDrawer() }),
      button('Döküm al', {
        title: 'Süzgeçlenmiş belgelerin tek PDF dökümünü üretir ve yazdırır.',
        onClick: async () => {
          const produced = await report.run('list', query());
          if (produced?.path) nodes.lastFile.set(produced.path);
          if (produced?.ok) loadArchive(true);
        },
      }),
      button('Yenile', { onClick: () => refresh() }),
    ],
  });

  nodes.status = statusLine();
  nodes.body = h('div', 'bi-body');

  const bar = h('div', 'bi-topbar');
  bar.append(nodes.tabs.node);
  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    if (key === 'archive') {
      loadArchive();
      paintArchive();
      return;
    }
    paintInvoices();
  }

  root.replaceChildren(view);
  showTab('invoices');
  refresh();

  return () => {
    nodes.filters?.destroy();     // takvim ve arama GLOBAL dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = freshState();
    report = null;
    toast = null;
  };
}
