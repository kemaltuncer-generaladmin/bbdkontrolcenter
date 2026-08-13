// Link ile Ödeme paneli — uzaktaki müşteriden karttan tahsilat.
//
// NE YAPAR: personel formu doldurur (ad soyad, telefon, e-posta, il/ilçe,
// adres, açıklama ve TUTAR ya da ÜRÜN); sağdaki ön izleme tutarı kırar ve
// gidecek SMS'i harfi harfine gösterir; gerekçeli onaydan sonra mağazada
// ödeme bağlantısı üretilir, SMS gider, müşteri kendi kartıyla öder ve
// yoklamayla durum görünür. Kart bilgisi BU EKRANA HİÇ GİRİLMEZ.
//
// NE YAPMAZ:
//  · POS izlemez. Banka mutabakatı, terminal ayarı, taksit/komisyon matrisi
//    bu ekranda yoktur; burada tek bir iş vardır.
//  · Kart numarası, CVV, son kullanma istemez. İstenseydi ekran PCI kapsamına
//    girer ve personelin gördüğü hiçbir yerde saklanmaması gereken veri
//    saklanırdı.
//  · Kayıt silmez. İptal bağlantıyı öldürür, satır kalır (BBD veri silme
//    yasağı).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · SERBEST TUTAR KDV'SİZ gider; ürün seçilirse ürünün KENDİ vergi
//    kategorisi geçerlidir. Kırılım her ikisini ayrı satırda gösterir.
//  · "Durum okunamadı" ve "Provizyon açık" ASLA "başarısız" yazılmaz: para
//    çekilmiş olabilir. O satırda yeni link üretimi KİLİTLİDİR.
//  · SMS'in üç katmanlı freni vardır; hangi katmanın tuttuğu ekranda yazar.
//    "Gönderildi" yazısı yalnız gerçekten gidince çıkar.
//  · Türkçe harf SMS'i pahalılaştırır; sayaç parça sayısını ve suçlu
//    karakterleri gösterir, "Sadeleştir" bunları ASCII'ye indirir.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_payment_gateway/ →
// shell/ui-kit/. Bu dosyanın KAYNAĞI modules/store_payment_gateway/ui/panel/
// altındadır; orada '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, confirmWithReason, copyText, csvBlob, debounce, h, loadStyles,
  money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_payment_gateway';

const EMPTY = {
  store: {}, sms: {}, payments: {},
  standalone: true, standaloneNotice: '', defaultEmail: '',
  orgName: '', linkBase: '',
  lines: [], quote: null, products: [], productQuery: '', statuses: [],
  items: [], total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', summary: {}, loaded: false,
  template: '', simplified: '', tab: 'new',
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY, lines: [] };

const nodes = {};
const forms = [];          // cleanup'ta destroy edilecek formGrid'ler
const closers = [];        // açık çekmece / iptal edilecek debounce

// ------------------------------------------------------------------ araçlar

/** Sunucu `{ok:false, error}` de dönebilir; ikisi de tek yerde okunur. */
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

/** Para hareketi başlatan her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel, danger = true }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function statusText() {
  const parts = [];
  const store = state.store || {};
  parts.push(store.connected ? 'Mağaza bağlı' : `Mağazaya ulaşılamadı — ${store.error || '—'}`);
  if (store.readOnly) parts.push('ACİL FREN AÇIK: mağazaya yazma kapalı');
  if (store.connected && state.payments && state.payments.available === false) {
    parts.push('Ödeme uçları kapalı');
  }
  const sms = state.sms || {};
  if (!sms.available) parts.push('SMS yok');
  else if (sms.moduleDryRun || sms.platformDryRun) parts.push('SMS kuru provada');
  else parts.push('SMS CANLI');
  if (state.loaded) parts.push(`${num(state.total)} talep`);
  return parts.join(' · ');
}

function paintStatus() {
  const store = state.store || {};
  nodes.status?.set(statusText(), !store.connected);
}

/** Açık çekmece/işleyiciyi cleanup listesine ekler ve kapanınca geri alır. */
function registerCloser(fn) {
  closers.push(fn);
  return () => {
    const at = closers.indexOf(fn);
    if (at >= 0) closers.splice(at, 1);
  };
}

/**
 * KDV etiketi. `rateKnown === false` → oran OKUNAMADI; "%0" yazmak sessizce
 * yanlış olur, sıfır ile bilinmiyor aynı şey değildir.
 */
function taxLabel(line) {
  if (line.taxFree) return 'KDV‑siz';
  if (line.rateKnown === false) return 'KDV okunamadı';
  return `KDV %${num(line.taxRate, line.taxRate % 1 ? 1 : 0)}`;
}

// --------------------------------------------------------------- kalemler

/** Formdaki serbest tutar + seçilen ürünler → sunucunun beklediği kalem listesi. */
function currentLines() {
  const draft = nodes.form ? nodes.form.draft() : {};
  const lines = [];
  const free = Number(draft.amount || 0);
  if (free > 0) {
    lines.push({
      kind: 'free',
      label: draft.freeLabel || 'Serbest tutar',
      quantity: 1,
      amount: free,
      productId: 0,
      sku: '',
    });
  }
  for (const line of state.lines) {
    lines.push({
      kind: 'product',
      label: line.label,
      quantity: line.quantity,
      amount: line.amount,
      productId: line.productId,
      sku: line.sku || '',
    });
  }
  return lines;
}

function formBody() {
  const draft = nodes.form ? nodes.form.draft() : {};
  return {
    fullName: draft.fullName || '',
    phone: draft.phone || '',
    email: draft.email || '',
    city: draft.city || '',
    district: draft.district || '',
    address: draft.address || '',
    note: draft.note || '',
    orderId: Number(draft.orderId || 0) || 0,
    lines: currentLines(),
    template: state.template || '',
  };
}

// -------------------------------------------------------------------- veri

async function loadState() {
  try {
    const payload = await api(`${BASE}/state`);
    state.store = payload.store || {};
    state.sms = payload.sms || {};
    state.payments = payload.payments || {};
    state.standalone = Boolean(payload.standalone);
    state.standaloneNotice = payload.standaloneNotice || '';
    state.defaultEmail = payload.defaultEmail || '';
    state.orgName = payload.orgName || '';
    state.linkBase = payload.linkBase || '';
  } catch (error) {
    // Durum ucu bile cevap vermiyorsa ekran yine ayakta kalır (K7).
    state.store = { connected: false, error: error.message };
    state.sms = { available: false, error: error.message };
    state.payments = { available: false, error: error.message };
  }
  paintStatus();
  paintNotices();
}

/**
 * Süzgecin durum listesi SUNUCUDAN gelir (`/reference`). Panelde ikinci bir
 * kopya tutmak, arkada durum eklendiğinde süzgeci sessizce eksik bırakırdı.
 * Uç cevap vermezse süzgeç "Tümü" ile çalışmaya devam eder.
 */
async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch (error) {
    nodes.status?.set(`Durum listesi okunamadı — ${error.message}`, true);
    return;
  }
  state.statuses = payload.statuses || [];
  nodes.filters.options('status', [
    { value: '', label: 'Tümü — durum' },
    ...state.statuses.map((item) => ({ value: item.value, label: item.label })),
  ]);
}

const requestQuote = debounce(async () => {
  if (state.tab !== 'new') return;
  let payload;
  try {
    payload = await api(`${BASE}/quote`, { method: 'POST', body: formBody() });
  } catch (error) {
    nodes.quote?.replaceChildren(alertBox(`Ön izleme alınamadı — ${error.message}`, 'bad'));
    return;
  }
  state.quote = payload;
  renderQuote();
}, 320);
closers.push(() => requestQuote.cancel());

async function refresh({ page = state.page, size = state.size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(6, 6));
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  const amount = values.amount || {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.status) params.set('status', values.status);
  if (range.start) params.set('start', range.start);
  if (range.end) params.set('end', range.end);
  if (amount.min !== null && amount.min !== undefined) params.set('minAmount', String(amount.min));
  if (amount.max !== null && amount.max !== undefined) params.set('maxAmount', String(amount.max));
  params.set('page', String(page));
  params.set('size', String(size));

  let payload;
  try {
    payload = await api(`${BASE}/requests?${params.toString()}`);
  } catch (error) {
    state.connected = false;
    state.error = error.message;
    state.items = [];
    renderTable();
    paintStatus();
    return;
  }
  Object.assign(state, {
    items: payload.items || [],
    total: payload.total || 0,
    page: payload.page || page,
    size: payload.size || size,
    pages: payload.pages || 0,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    summary: payload.summary || {},
    loaded: true,
  });
  renderTable();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  paintStatus();
  if (state.tab === 'settle') renderSettle();
}

async function searchProducts(query) {
  state.productQuery = query;
  const params = new URLSearchParams({ q: query, size: '20' });
  try {
    const payload = await api(`${BASE}/products?${params.toString()}`);
    state.products = payload.items || [];
    if (!payload.connected) {
      toast(`Ürün listesi okunamadı — ${payload.error}`, 'warn');
    }
  } catch (error) {
    state.products = [];
    toast(error.message, 'bad');
  }
  renderProductResults();
}

const searchProductsSoon = debounce((value) => searchProducts(value), 320);
closers.push(() => searchProductsSoon.cancel());

// ------------------------------------------------------------------- çizim

function paintNotices() {
  if (!nodes.notices) return;
  const list = [];
  const store = state.store || {};
  if (store.readOnly) {
    list.push(alertBox(
      'Acil fren açık: mağazaya yazma kapalı. Talep kaydedilir ama ödeme bağlantısı '
      + 'üretilmez (modules.store_api.read_only).', 'bad'));
  }
  // Ödeme uçları hiç yayında değilse ÖNCE bu söylenir: "serbest tahsilat ucu
  // yok" uyarısı, hiçbir bağlantının üretilemediği bir mağazada yanıltıcıdır.
  const payments = state.payments || {};
  if (payments.notice) {
    list.push(alertBox(`${payments.notice}${payments.error ? ` (${payments.error})` : ''}`,
      'bad'));
  } else if (!state.standalone) {
    list.push(alertBox(state.standaloneNotice, 'warn'));
  }
  const sms = state.sms || {};
  if (!sms.available) {
    list.push(alertBox(
      `SMS gönderilemiyor: ${sms.error || 'bildirim yeteneği yok'}. Bağlantı yine üretilir; `
      + 'listeden kopyalayıp müşteriye elden iletebilirsiniz.', 'warn'));
  } else if (sms.moduleDryRun || sms.platformDryRun) {
    const which = [
      sms.moduleDryRun ? 'modül ayarı (sms_dry_run)' : '',
      sms.platformDryRun ? 'platform ayarı (platform.notify.sms.dry_run)' : '',
    ].filter(Boolean).join(' + ');
    list.push(hintBox(
      `SMS KURU PROVADA — ${which}. Mesaj hazırlanır, sayacı görünür ama müşteriye `
      + 'GİTMEZ. Canlıya geçmeden önce kendi numaranızı beyaz listeye yazıp deneyin.'));
  } else if ((sms.allowlist || []).length) {
    list.push(hintBox(
      `SMS canlı ama beyaz liste açık: yalnız ${sms.allowlist.length} numaraya gerçek `
      + 'mesaj gider, diğerleri kuru provaya düşer.'));
  }
  nodes.notices.replaceChildren(...list);
}

function statusBadge(row) {
  const box = h('span', 'pg-status');
  // Renk tek başına anlam taşımaz: rozetin yanında her zaman yazı durur.
  box.append(badge(row.status.label, row.status.tone));
  if (row.status.moneyMayBeTaken) box.append(h('span', 'pg-warnmark', '⚠ doğrula'));
  if (row.status.note) box.title = row.status.note;
  return box;
}

function personCell(row) {
  const box = h('span', 'pg-person');
  box.append(h('b', undefined, row.fullName || '—'));
  box.append(h('span', 'pg-sub', `${row.phone || '—'} · ${row.code}`));
  return box;
}

const COLUMNS = [
  { key: 'fullName', label: 'Müşteri', width: 'minmax(0, 2.2fr)', cell: personCell },
  {
    key: 'gross',
    label: 'Toplam',
    width: '130px',
    align: 'num',
    cell: (row) => money(row.gross),
  },
  {
    key: 'tax',
    label: 'KDV',
    width: '110px',
    align: 'num',
    cell: (row) => (row.tax ? money(row.tax) : 'KDV‑siz'),
  },
  { key: 'status', label: 'Durum', width: 'minmax(0, 1.4fr)', cell: statusBadge },
  {
    key: 'sms',
    label: 'SMS',
    width: '120px',
    cell: (row) => ({
      sent: 'Gönderildi', dry_run: 'Kuru prova', error: 'Hata', '': '—',
    }[row.smsState] ?? row.smsState),
  },
  {
    key: 'createdAt',
    label: 'Açılış',
    width: '150px',
    cell: (row) => stampIso(row.createdAt),
  },
];

function renderTable() {
  if (!nodes.tableWrap) return;
  if (!state.connected && state.error) {
    nodes.tableWrap.replaceChildren(alertBox(
      `Talep listesi okunamadı — ${state.error}`, 'bad'));
    return;
  }
  if (!state.items.length) {
    nodes.tableWrap.replaceChildren(emptyState({
      title: 'Bu süzgeçte tahsilat talebi yok',
      text: 'Süzgeci temizleyin ya da “Yeni Tahsilat” sekmesinden ilk talebi açın.',
      actions: [
        button('Süzgeci temizle', { onClick: () => nodes.filters.reset() }),
        button('Yeni tahsilat', { variant: 'primary', onClick: () => nodes.tabs.select('new') }),
      ],
    }));
    return;
  }
  const table = dataTable({
    columns: COLUMNS,
    rows: state.items,
    rowKey: (row) => String(row.id),
    onRow: (row) => openDrawer(row.id),
  });
  nodes.tableWrap.replaceChildren(table.node);

  const counts = state.summary.counts || {};
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Sayfadaki talep', value: num(state.items.length) },
    { label: 'Tahsil edilen', value: money(state.summary.collected || 0), tone: 'good' },
    { label: 'Bekleyen', value: money(state.summary.waiting || 0) },
    {
      label: 'Doğrulanmalı',
      value: num((counts.unknown || 0) + (counts.void_required || 0)),
      tone: 'warn',
      title: 'Para çekilmiş olabilir; bu satırlara yeni link gönderilmez.',
    },
  ]));
}

// ------------------------------------------------------- ön izleme (sağ)

function amountTable(amounts) {
  const box = h('div', 'pg-amounts');
  for (const line of amounts.lines || []) {
    const row = h('div', 'pg-amount-row');
    const label = h('span', undefined, line.label);
    if (line.quantity > 1) label.append(h('span', 'pg-sub', ` × ${line.quantity}`));
    row.append(label);
    row.append(h('span', `pg-tag${line.rateKnown === false ? ' pg-bad' : ''}`, taxLabel(line)));
    row.append(h('b', 'pg-num', money(line.gross)));
    box.append(row);
  }
  const total = h('div', 'pg-total');
  const line = (label, value, strong) => {
    const row = h('div', `pg-total-row${strong ? ' strong' : ''}`);
    row.append(h('span', undefined, label), h('b', 'pg-num', money(value)));
    return row;
  };
  total.append(
    line('Matrah', amounts.net),
    line('KDV', amounts.tax),
    line('Tahsil edilecek', amounts.gross, true),
  );
  box.append(total);
  if (amounts.pricesIncludeTax) {
    box.append(hintBox('Mağaza ayarı: ürün fiyatları KDV DÂHİL girilmiş; KDV brütten '
      + 'ayrıştırıldı. Serbest tutar her hâlükârda KDV’sizdir.'));
  }
  return box;
}

function smsPreview(sms) {
  const box = h('div', 'pg-sms');
  const body = h('pre', 'pg-sms-text', sms.text || '');
  box.append(body);

  const meter = h('div', 'pg-meter');
  meter.append(h('span', undefined, `${num(sms.length)} karakter`));
  meter.append(h('span', undefined, `${num(sms.units)} birim`));
  meter.append(h('b', sms.parts > 1 ? 'pg-bad' : 'pg-good', `${num(sms.parts)} SMS`));
  meter.append(h('span', 'pg-sub', `${num(sms.remaining)} birim sonra yeni parça`));
  if (sms.unicode) meter.append(badge('UCS‑2 (emoji) — sınır 70', 'bad'));
  else if (sms.encoding) meter.append(badge('Türkçe kodlama', 'warn'));
  box.append(meter);

  if ((sms.offending || []).length) {
    const row = h('div', 'pg-chips');
    row.append(h('span', 'pg-sub', 'Pahalılaştıran karakterler:'));
    for (const ch of sms.offending) row.append(h('code', 'pg-chip', ch));
    box.append(row);
  }
  if (sms.savedParts > 0) {
    const row = h('div', 'pg-actions');
    row.append(h('span', 'pg-sub',
      `Sadeleştirilirse ${num(sms.savedParts)} SMS kredisi tasarruf edilir.`));
    box.append(row);
  }
  if ((sms.missing || []).length) {
    box.append(alertBox(
      `Şablonda doldurulamayan yer tutucu: ${sms.missing.map((k) => `{${k}}`).join(', ')} — `
      + 'müşteriye süslü parantezle gider. Alanı doldurun ya da şablonu düzeltin.', 'warn'));
  }
  if ((sms.unknown || []).length) {
    box.append(alertBox(
      `Tanınmayan yer tutucu: ${sms.unknown.map((k) => `{${k}}`).join(', ')}`, 'bad'));
  }
  return box;
}

function renderQuote() {
  if (!nodes.quote) return;
  const quote = state.quote;
  if (!quote) {
    nodes.quote.replaceChildren(hintBox(
      'Formu doldurdukça tutar kırılımı ve gidecek SMS burada canlı görünür.'));
    return;
  }
  const parts = [card('Tutar kırılımı', amountTable(quote.amounts))];
  parts.push(card('Gidecek SMS', smsPreview(quote.sms), state.orgName));

  if ((quote.problems || []).length) {
    const list = h('ul', 'pg-problems');
    for (const problem of quote.problems) list.append(h('li', undefined, problem));
    parts.push(alertBox('Eksikler var; tahsilat başlatılamaz.', 'warn'));
    parts.push(list);
  }
  if (quote.standaloneNotice) parts.push(alertBox(quote.standaloneNotice, 'warn'));

  const actions = h('div', 'pg-actions');
  const startBtn = button('Tahsilat başlat', {
    variant: 'primary',
    title: 'Talebi kaydeder, ödeme bağlantısı üretir ve SMS gönderir',
    onClick: startCollection,
  });
  startBtn.disabled = !quote.ready;
  const draftBtn = button('Yalnız kaydet', {
    title: 'Talebi taslak olarak kaydeder; bağlantı üretilmez',
    onClick: () => saveDraft(),
  });
  actions.append(startBtn, draftBtn);
  parts.push(actions);

  nodes.quote.replaceChildren(...parts);
}

// ------------------------------------------------------------- ürün seçimi

function renderChosenLines() {
  if (!nodes.chosen) return;
  nodes.chosen.replaceChildren();
  if (!state.lines.length) {
    nodes.chosen.append(h('div', 'pg-sub',
      'Ürün seçilmedi. Serbest tutar KDV’siz gider; ürün seçilirse ürünün kendi '
      + 'vergi kategorisi uygulanır. İkisi birlikte de kullanılabilir.'));
    return;
  }
  for (const line of state.lines) {
    const row = h('div', 'pg-line');
    const name = h('span', 'pg-line-name');
    name.append(h('b', undefined, line.label));
    name.append(h('span', 'pg-sub', `${line.sku || '—'} · ${money(line.amount)} / adet`));
    row.append(name);

    const qty = h('input', 'kit-input pg-qty');
    qty.type = 'text';
    qty.inputMode = 'numeric';
    qty.value = String(line.quantity);
    qty.setAttribute('aria-label', `${line.label} adedi`);
    qty.addEventListener('input', () => {
      const value = Math.max(1, Math.min(999, Number(qty.value.replace(/\D/g, '')) || 1));
      line.quantity = value;
      requestQuote();
    });
    row.append(qty);
    row.append(h('b', 'pg-num', money(line.amount * line.quantity)));
    row.append(button('Çıkar', {
      variant: 'ghost',
      onClick: () => {
        state.lines = state.lines.filter((item) => item !== line);
        renderChosenLines();
        requestQuote();
      },
    }));
    nodes.chosen.append(row);
  }
}

function renderProductResults() {
  if (!nodes.results) return;
  nodes.results.replaceChildren();
  if (!state.productQuery) {
    nodes.results.append(h('div', 'pg-sub', 'Ürün adı yazın; sonuçlar mağazadan gelir.'));
    return;
  }
  if (!state.products.length) {
    nodes.results.append(h('div', 'pg-sub', 'Aramaya uyan ürün yok.'));
    return;
  }
  for (const product of state.products) {
    const row = h('button', 'pg-result');
    row.type = 'button';
    row.append(h('b', undefined, product.name));
    // Oran okunamadıysa "%0" YAZILMAZ: personel vergisiz sanıp tahsil eder.
    const tax = product.taxRate === null || product.taxRate === undefined
      ? 'KDV okunamadı'
      : `KDV %${num(product.taxRate, product.taxRate % 1 ? 1 : 0)}`;
    const price = `${money(product.price)}${product.discounted ? ' (indirimli)' : ''}`;
    row.append(h('span', 'pg-sub', `${product.sku || '—'} · ${price} · ${tax}`));
    if (product.taxNote) row.title = product.taxNote;
    row.addEventListener('click', () => {
      if (product.taxNote) toast(product.taxNote, 'warn');
      const existing = state.lines.find((line) => line.productId === product.id);
      if (existing) existing.quantity += 1;
      else {
        state.lines.push({
          kind: 'product',
          productId: product.id,
          label: product.name,
          sku: product.sku,
          amount: product.price,
          quantity: 1,
        });
      }
      renderChosenLines();
      requestQuote();
    });
    nodes.results.append(row);
  }
}

// ---------------------------------------------------------------- eylemler

async function saveDraft() {
  return withBusy('Talep kaydediliyor…', async () => {
    const result = await call(`${BASE}/requests`, { method: 'POST', body: formBody() });
    toast(`Talep açıldı: ${result.code}`, 'good');
    resetForm();
    await refresh({ page: 1 });
    return result;
  });
}

async function startCollection() {
  const quote = state.quote;
  if (!quote || !quote.ready) return;
  const body = formBody();
  const smsLive = state.sms.available && !state.sms.moduleDryRun && !state.sms.platformDryRun;
  const reason = await askReason({
    title: 'Tahsilat başlat',
    description: `${body.fullName} adına ${money(quote.amounts.gross)} tahsil edilecek. `
      + `Ödeme bağlantısı üretilecek ve ${body.phone} numarasına `
      + `${smsLive ? 'GERÇEK SMS gidecek' : 'SMS kuru provada hazırlanacak'}. `
      + 'Gerekçe denetim kaydına yazılır.',
    confirmLabel: smsLive ? 'Bağlantı üret ve SMS gönder' : 'Bağlantı üret',
    danger: smsLive,
  });
  if (!reason) return null;

  return withBusy('Tahsilat başlatılıyor…', async () => {
    const created = await call(`${BASE}/requests`, { method: 'POST', body });
    const started = await api(`${BASE}/requests/${created.id}/start`, {
      method: 'POST',
      body: { reason, dryRun: false, sendSms: true },
    });
    if (!started.ok) {
      // Talep KAYITLI kaldı: personel bilgiyi ikinci kez sormasın.
      toast(started.error, 'bad');
      nodes.status?.set(started.error, true);
      await refresh({ page: 1 });
      nodes.tabs.select('list');
      openDrawer(created.id);
      return null;
    }
    if (started.dryRun) toast(started.notice || 'Kuru prova: bağlantı üretilmedi.', 'warn');
    else toast(`Bağlantı üretildi: ${created.code}`, 'good');
    if (started.sms && !started.sms.sent) {
      toast(started.sms.notice || 'SMS gönderilmedi.', 'warn');
    }
    resetForm();
    await refresh({ page: 1 });
    nodes.tabs.select('list');
    openDrawer(created.id);
    return started;
  });
}

function resetForm() {
  state.lines = [];
  state.quote = null;
  nodes.form?.reset({ email: state.defaultEmail });
  renderChosenLines();
  renderQuote();
}

// ------------------------------------------------------------- çekmece

async function openDrawer(requestId) {
  let payload;
  try {
    payload = await call(`${BASE}/requests/${requestId}`);
  } catch (error) {
    toast(error.message, 'bad');
    return;
  }
  const row = payload.request;
  let unregister = () => {};
  const box = drawer(nodes.root, {
    title: `${row.fullName} · ${money(row.gross)}`,
    subtitle: `${row.code} · ${row.phone}`,
    // Kapanınca cleanup listesinden DÜŞER: her açılışta yeni bir kapatıcı
    // biriktirmek, uzun süre açık kalan panelde listeyi şişiriyordu.
    onClose: () => { nodes.drawer = null; unregister(); },
  });
  nodes.drawer = box;
  unregister = registerCloser(() => box.close());
  paintDrawer(box, payload);
}

function paintDrawer(box, payload) {
  const row = payload.request;
  box.body.replaceChildren();

  const head = h('div', 'pg-drawer-head');
  head.append(statusBadge(row));
  if (row.orderId) head.append(badge(`Sipariş #${row.orderId}`, 'info'));
  if (row.invoiceId) head.append(badge(`Fatura #${row.invoiceId}`, 'good'));
  box.body.append(head);

  if (row.status.note) {
    box.body.append(alertBox(row.status.note,
      row.status.moneyMayBeTaken ? 'warn' : 'info'));
  }
  if (payload.warning) box.body.append(alertBox(payload.warning, 'warn'));

  // Kimlik ve adres
  const info = h('div', 'pg-info');
  const pair = (label, value) => {
    const cell = h('div', 'pg-info-cell');
    cell.append(h('span', 'pg-sub', label), h('b', undefined, value || '—'));
    return cell;
  };
  info.append(
    pair('E-posta', row.email),
    pair('İl / İlçe', [row.city, row.district].filter(Boolean).join(' / ')),
    pair('Adres', row.address),
    pair('Açıklama', row.note),
    pair('Matrah', money(row.net)),
    pair('KDV', row.tax ? money(row.tax) : 'KDV‑siz'),
    pair('Açan', row.actor),
    pair('Son gerekçe', row.reason),
  );
  box.body.append(card('Talep', info));

  // Kalemler
  if ((payload.items || []).length) {
    const lines = h('div', 'pg-amounts');
    for (const line of payload.items) {
      const item = h('div', 'pg-amount-row');
      item.append(h('span', undefined, `${line.label} × ${line.quantity}`));
      item.append(h('span', 'pg-tag', taxLabel(line)));
      item.append(h('b', 'pg-num', money(line.gross)));
      lines.append(item);
    }
    box.body.append(card('Kalemler', lines));
  }

  // Bağlantı
  const linkBox = h('div', 'pg-linkbox');
  if (row.link) {
    linkBox.append(h('code', 'pg-link', row.link));
    linkBox.append(button('Kopyala', {
      onClick: async () => {
        toast(await copyText(row.link) ? 'Bağlantı kopyalandı.' : 'Kopyalanamadı.',
          'good');
      },
    }));
  } else {
    linkBox.append(h('span', 'pg-sub', 'Henüz bağlantı üretilmedi.'));
  }
  box.body.append(card('Ödeme bağlantısı', linkBox,
    row.smsState ? `SMS: ${row.smsState} · ${stampIso(row.smsAt)}` : ''));

  // Eylemler
  const actions = h('div', 'pg-actions');
  actions.append(button('Durumu yokla', {
    onClick: () => withBusy('Mağazadan durum okunuyor…', async () => {
      const result = await call(`${BASE}/requests/${row.id}/poll`, { method: 'POST' });
      if (!result.connected) toast(`Mağazaya ulaşılamadı — ${result.error}`, 'warn');
      else if (result.notice) toast(result.notice, result.request.status.moneyMayBeTaken
        ? 'warn' : 'good');
      else toast('Durum güncel.', 'good');
      await refresh();
      const fresh = await call(`${BASE}/requests/${row.id}`);
      paintDrawer(box, fresh);
      return result;
    }),
  }));

  if (payload.canRelink) {
    actions.append(button(row.link ? 'Yeni bağlantı üret' : 'Bağlantı üret', {
      variant: 'primary',
      onClick: () => runStart(box, row),
    }));
  } else {
    actions.append(hintBox(payload.relinkBlock));
  }

  if (row.link && !['paid', 'settled', 'cancelled'].includes(row.status.code)) {
    actions.append(button('SMS gönder', { onClick: () => runSms(box, row) }));
    actions.append(button('Bağlantıyı iptal et', {
      variant: 'danger',
      onClick: () => runCancel(box, row),
    }));
  }
  box.body.append(actions);

  // POS denemeleri — kart verisi sunucuda maskelidir, burada maskelenmez.
  if ((payload.attempts || []).length) {
    const attempts = h('div', 'pg-amounts');
    for (const attempt of payload.attempts) {
      const item = h('div', 'pg-amount-row');
      item.append(h('span', undefined,
        `${stampIso(attempt.createdAt)} · ${attempt.bank || '—'} · ${attempt.card || '—'}`));
      item.append(badge(attempt.status.label, attempt.status.tone));
      item.append(h('b', 'pg-num', money(attempt.amount)));
      if (attempt.errorMessage) item.title = `${attempt.errorCode} ${attempt.errorMessage}`;
      attempts.append(item);
    }
    box.body.append(card('POS denemeleri', attempts, 'Kart verisi mağazada maskelidir'));
  }

  // Olay zinciri
  const timeline = h('div', 'pg-timeline');
  for (const event of payload.events || []) {
    const item = h('div', 'pg-event');
    item.append(h('b', undefined, `${event.action} · ${event.result}`));
    item.append(h('span', 'pg-sub',
      `${stampIso(event.createdAt)} · ${event.actor || '—'}`));
    if (event.reason) item.append(h('span', 'pg-sub', event.reason));
    timeline.append(item);
  }
  box.body.append(card('Olay zinciri', timeline.childElementCount
    ? timeline
    : h('div', 'pg-sub', 'Kayıt yok.')));
}

async function runStart(box, row) {
  const reason = await askReason({
    title: 'Ödeme bağlantısı üret',
    description: `${row.fullName} için ${money(row.gross)} tutarında bağlantı üretilecek.`,
    confirmLabel: 'Üret',
  });
  if (!reason) return;
  await withBusy('Bağlantı üretiliyor…', async () => {
    const result = await api(`${BASE}/requests/${row.id}/start`, {
      method: 'POST', body: { reason, dryRun: false, sendSms: false },
    });
    if (!result.ok) { toast(result.error, 'bad'); return null; }
    toast(result.dryRun ? (result.notice || 'Kuru prova.') : 'Bağlantı üretildi.',
      result.dryRun ? 'warn' : 'good');
    await refresh();
    paintDrawer(box, await call(`${BASE}/requests/${row.id}`));
    return result;
  });
}

async function runSms(box, row) {
  const live = state.sms.available && !state.sms.moduleDryRun && !state.sms.platformDryRun;
  const reason = await askReason({
    title: 'SMS gönder',
    description: live
      ? `${row.phone} numarasına GERÇEK SMS gidecek.`
      : `SMS kuru provada: mesaj hazırlanır ama ${row.phone} numarasına gitmez.`,
    confirmLabel: 'Gönder',
    danger: live,
  });
  if (!reason) return;
  await withBusy('SMS gönderiliyor…', async () => {
    const result = await api(`${BASE}/requests/${row.id}/sms`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    if (!result.ok) { toast(result.error, 'bad'); return null; }
    toast(result.sent ? `SMS gönderildi (${num(result.parts)} parça).`
      : (result.notice || 'SMS gönderilmedi.'), result.sent ? 'good' : 'warn');
    await refresh();
    paintDrawer(box, await call(`${BASE}/requests/${row.id}`));
    return result;
  });
}

async function runCancel(box, row) {
  const reason = await askReason({
    title: 'Bağlantıyı iptal et',
    description: 'Bağlantı ölür ve müşteri ödeyemez. KAYIT SİLİNMEZ; talep listede kalır.',
    confirmLabel: 'İptal et',
  });
  if (!reason) return;
  await withBusy('Bağlantı iptal ediliyor…', async () => {
    const result = await api(`${BASE}/requests/${row.id}/cancel`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    if (!result.ok) { toast(result.error, 'bad'); return null; }
    toast('Bağlantı iptal edildi.', 'good');
    await refresh();
    paintDrawer(box, await call(`${BASE}/requests/${row.id}`));
    return result;
  });
}

// ------------------------------------------------------------ elden kapatma

function renderSettle() {
  const host = nodes.settleHost;
  if (!host) return;
  host.replaceChildren();
  host.append(hintBox(
    'Müşteri havale/EFT yaptı ya da nakit ödedi. Burada BEYAN edilir: tahsilat '
    + 'karttan geçmeden kapanır. Mağaza ödeme kaydını FATURAYA işliyor; talebin '
    + 'siparişi ve faturası varsa kayıt oraya da yazılır, yoksa beyan yalnız '
    + 'burada durur. Dekont/makbuz numarasının mağazada karşılığı yoktur, bu '
    + 'ekranda ve denetim kaydında saklanır. Karttan para çekilmiş OLABİLECEK '
    + 'talepler (durum okunamadı / provizyon açık) elden kapatılamaz — önce '
    + 'banka durumu netleşmelidir.'));

  const open = state.items.filter(
    (row) => ['draft', 'linked', 'sent', 'expired', 'failed'].includes(row.status.code));
  if (!open.length) {
    host.append(emptyState({
      title: 'Elden kapatılacak açık talep yok',
      text: 'Talepler sekmesindeki süzgeç bu listeyi de besliyor; aralığı genişletin.',
      actions: [button('Talepler', { onClick: () => nodes.tabs.select('list') })],
    }));
    return;
  }

  const table = dataTable({
    columns: [
      { key: 'fullName', label: 'Müşteri', width: 'minmax(0, 2fr)', cell: personCell },
      { key: 'gross', label: 'Toplam', width: '130px', align: 'num', cell: (r) => money(r.gross) },
      { key: 'status', label: 'Durum', width: 'minmax(0, 1.2fr)', cell: statusBadge },
      {
        key: 'act',
        label: '',
        width: '150px',
        cell: (row) => button('Elden kapat', { onClick: () => openSettle(row) }),
      },
    ],
    rows: open,
    rowKey: (row) => String(row.id),
  });
  host.append(card(`Açık talepler (${num(open.length)})`, table.node));
}

function openSettle(row) {
  let unregister = () => {};
  let form = null;
  const box = drawer(nodes.root, {
    title: `Elden kapatma · ${row.fullName}`,
    subtitle: `${row.code} · ${money(row.gross)}`,
    onClose: () => {
      nodes.drawer = null;
      unregister();
      // formGrid global dinleyici tutuyor; çekmece kapanınca hemen bırakılır.
      try { form?.destroy(); } catch { /* kapanışta yutulur */ }
      const at = forms.indexOf(form);
      if (at >= 0) forms.splice(at, 1);
    },
  });
  nodes.drawer = box;
  unregister = registerCloser(() => box.close());

  form = formGrid({
    fields: [
      {
        key: 'method',
        label: 'Yöntem',
        type: 'select',
        required: true,
        options: [{ value: 'havale', label: 'Havale / EFT' }, { value: 'nakit', label: 'Nakit' }],
      },
      {
        key: 'reference',
        label: 'Dekont / makbuz no',
        type: 'text',
        maxLength: 120,
        hint: 'Mutabakatta bu numara aranır; boş bırakmak sonra iş çıkarır.',
      },
      {
        key: 'amount',
        label: 'Tahsil edilen tutar',
        type: 'money',
        hint: 'Boş bırakılırsa talebin toplamı yazılır.',
      },
    ],
    value: { method: 'havale', amount: row.gross },
  });
  forms.push(form);
  box.body.append(form.node);

  const actions = h('div', 'pg-actions');
  actions.append(button('Beyanı kaydet', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      const reason = await askReason({
        title: 'Elden kapatma beyanı',
        description: `${row.fullName} için ${money(Number(draft.amount) || row.gross)} `
          + `${draft.method === 'nakit' ? 'nakit' : 'havale'} olarak tahsil edildi beyanı. `
          + 'Talep kapanır. Mağaza ödemeyi FATURAYA işliyor: talebin faturası '
          + 'varsa kayıt oraya yazılır, yoksa beyan yalnız burada durur ve '
          + 'sonuç ekranda yazar.',
        confirmLabel: 'Beyan et',
      });
      if (!reason) return;
      await withBusy('Beyan kaydediliyor…', async () => {
        const result = await api(`${BASE}/requests/${row.id}/settle`, {
          method: 'POST',
          body: {
            method: draft.method || 'havale',
            reference: draft.reference || '',
            amount: Number(draft.amount) || 0,
            reason,
            dryRun: false,
          },
        });
        if (!result.ok) { toast(result.error, 'bad'); return null; }
        toast(result.notice || 'Tahsilat elden kapatıldı.', result.notice ? 'warn' : 'good');
        box.close();
        await refresh();
        return result;
      });
    },
  }));
  box.body.append(actions);
}

// --------------------------------------------------------------- şablon

async function renderTemplate() {
  const host = nodes.templateHost;
  if (!host) return;
  host.replaceChildren(skeletonRows(3, 2));
  let payload;
  try {
    payload = await call(`${BASE}/template`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.template = payload.body;
  host.replaceChildren();

  const area = h('textarea', 'kit-textarea pg-template');
  area.maxLength = 1000;
  area.value = payload.body;
  area.setAttribute('aria-label', 'SMS şablonu');

  const meter = h('div', 'pg-meter');
  const sample = h('pre', 'pg-sms-text');

  const repaint = async () => {
    state.template = area.value;
    // Örnek metin ve sayaç SUNUCUDAN gelir: parça hesabı ile gönderim aynı
    // kodu kullanmalı, yoksa ekranda 1 SMS görünüp faturaya 3 yazılır.
    let quote;
    try {
      quote = await api(`${BASE}/quote`, {
        method: 'POST',
        body: {
          fullName: 'Ayşe Yılmaz', phone: '5321234567', note: 'Deneme sınavı ücreti',
          lines: [{ kind: 'free', label: 'Serbest tutar', amount: 125000, quantity: 1 }],
          template: area.value,
        },
      });
    } catch (error) {
      meter.replaceChildren(h('span', 'pg-bad', error.message));
      return;
    }
    sample.textContent = quote.sms.text;
    meter.replaceChildren();
    meter.append(h('span', undefined, `${num(quote.sms.length)} karakter`));
    meter.append(h('b', quote.sms.parts > 1 ? 'pg-bad' : 'pg-good',
      `${num(quote.sms.parts)} SMS`));
    if (quote.sms.unicode) meter.append(badge('UCS‑2 — sınır 70', 'bad'));
    else if (quote.sms.encoding) meter.append(badge('Türkçe kodlama', 'warn'));
    if ((quote.sms.offending || []).length) {
      meter.append(h('span', 'pg-sub',
        `pahalı karakterler: ${quote.sms.offending.join(' ')}`));
    }
    if (quote.sms.savedParts > 0) {
      meter.append(h('span', 'pg-sub',
        `Sadeleştir → ${num(quote.sms.savedParts)} SMS tasarruf`));
    }
    state.simplified = quote.sms.simplified;
  };
  // Bekleyen çağrı panel kapanırken iptal edilmeli: aksi hâlde sekme
  // değiştikten sonra kaldırılmış düğümlere yazmaya çalışır.
  const repaintSoon = debounce(repaint, 320);
  closers.push(() => repaintSoon.cancel());
  area.addEventListener('input', repaintSoon);

  const chips = h('div', 'pg-chips');
  chips.append(h('span', 'pg-sub', 'Yer tutucular (tıklayın, imlecin yerine eklenir):'));
  for (const item of payload.placeholders || []) {
    const chip = h('button', 'pg-chip');
    chip.type = 'button';
    chip.textContent = `{${item.key}}`;
    chip.title = item.hint;
    chip.addEventListener('click', () => {
      const at = area.selectionStart ?? area.value.length;
      area.value = `${area.value.slice(0, at)}{${item.key}}${area.value.slice(at)}`;
      area.focus();
      repaint();
    });
    chips.append(chip);
  }

  const actions = h('div', 'pg-actions');
  actions.append(button('Sadeleştir', {
    title: 'Türkçe harfleri ASCII karşılıklarına indirir; parça sayısı düşer',
    onClick: () => {
      if (!state.simplified) return;
      area.value = state.simplified;
      repaint();
      toast('Sadeleştirildi. Anlam değişmediyse kaydedin.', 'good');
    },
  }));
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      const reason = await askReason({
        title: 'SMS şablonunu kaydet',
        description: 'Bundan sonra gönderilecek tüm tahsilat SMS’leri bu metni kullanır.',
        confirmLabel: 'Kaydet',
        danger: false,
      });
      if (!reason) return;
      await withBusy('Şablon kaydediliyor…', async () => {
        const result = await api(`${BASE}/template`, {
          method: 'POST', body: { body: area.value, reason },
        });
        if (!result.ok) { toast(result.error, 'bad'); return null; }
        toast('Şablon kaydedildi.', 'good');
        return result;
      });
    },
  }));

  host.append(
    card('Şablon', area, 'Değişiklik yalnız yeni gönderimleri etkiler'),
    chips,
    card('Örnek (Ayşe Yılmaz · 1.250,00 TL)', sample),
    meter,
    actions,
    hintBox('{link} zorunludur: bağlantısız bir SMS müşteriye hiçbir işe yaramaz. '
      + 'Sunucu kaydederken bunu denetler.'),
  );
  repaint();
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  if (!state.items.length) { toast('Dışa aktarılacak kayıt yok.', 'warn'); return; }
  const headers = ['Talep no', 'Müşteri', 'Telefon', 'Toplam', 'KDV', 'Durum', 'SMS', 'Açılış'];
  const rows = state.items.map((row) => [
    row.code, row.fullName, row.phone, money(row.gross), money(row.tax),
    row.status.label, row.smsState || '—', row.createdAt,
  ]);
  csvBlob(headers, rows, `tahsilat-sayfa-${state.page}`);
  toast(`${rows.length} satır indirildi.`, 'good');
}

async function exportAll() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: {
        q: values.q || '', status: values.status || '',
        start: range.start || '', end: range.end || '',
      },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    if (result.truncated) toast('Liste tavana dayandı; aralığı daraltın.', 'warn');
    return result;
  });
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel pg');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  nodes.tabs = tabBar([
    { key: 'new', label: 'Yeni Tahsilat' },
    { key: 'list', label: 'Talepler' },
    { key: 'settle', label: 'Elden Kapatma' },
    { key: 'template', label: 'SMS Şablonu' },
  ], 'new', (key) => showView(key));

  nodes.status = statusLine();
  nodes.notices = h('div', 'pg-notices');
  nodes.body = h('div', 'pg-body');

  // ------------------------------------------------------- yeni tahsilat
  nodes.form = formGrid({
    fields: [
      { key: 'fullName', label: 'Ad soyad', type: 'text', required: true, maxLength: 120 },
      { key: 'phone', label: 'Cep telefonu', type: 'phone', required: true,
        hint: 'Ödeme bağlantısı bu numaraya SMS ile gider.' },
      { key: 'email', label: 'E-posta', type: 'email', maxLength: 160,
        hint: 'Fatura bu adrese düşer.' },
      { key: 'city', label: 'İl', type: 'text', maxLength: 60 },
      { key: 'district', label: 'İlçe', type: 'text', maxLength: 60 },
      { key: 'orderId', label: 'Sipariş no (varsa)', type: 'number', min: 0,
        hint: 'Mevcut bir siparişin tahsilatıysa numarasını yazın.' },
      { key: 'address', label: 'Adres', type: 'textarea', wide: true, maxLength: 400 },
      { key: 'note', label: 'Açıklama', type: 'textarea', wide: true, maxLength: 400,
        hint: 'SMS’te {aciklama} olarak kullanılabilir.' },
      { key: 'amount', label: 'Serbest tutar', type: 'money', min: 0,
        hint: 'KDV’SİZ gider: yazdığınız rakam neyse müşteriden o çekilir.' },
    ],
    value: {},
    onChange: () => requestQuote(),
  });
  forms.push(nodes.form);

  const emailShortcut = button('fatura@bbdstore.com.tr', {
    title: 'Varsayılan fatura adresini yaz',
    onClick: () => {
      nodes.form.set('email', state.defaultEmail || 'fatura@bbdstore.com.tr');
      requestQuote();
    },
  });

  const search = h('input', 'kit-input');
  search.type = 'search';
  search.placeholder = 'Ürün ara (ad)';
  search.setAttribute('aria-label', 'Ürün ara');
  search.addEventListener('input', () => searchProductsSoon(search.value.trim()));

  nodes.results = h('div', 'pg-results');
  nodes.chosen = h('div', 'pg-chosen');
  nodes.quote = h('div', 'pg-quote');

  const left = h('div', 'pg-col');
  left.append(
    card('Müşteri ve tutar', nodes.form.node, 'Tutar ve ürün birlikte kullanılabilir'),
    (() => { const row = h('div', 'pg-actions'); row.append(emailShortcut); return row; })(),
    card('Ürün ekle', (() => {
      const box = h('div', 'pg-picker');
      box.append(search, nodes.results);
      return box;
    })(), 'Ürünün kendi vergi kategorisi uygulanır'),
    card('Seçilen kalemler', nodes.chosen),
  );
  const right = h('div', 'pg-col pg-col-right');
  right.append(nodes.quote);
  nodes.newView = h('div', 'pg-split');
  nodes.newView.append(left, right);

  // ------------------------------------------------------------ talepler
  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ad, telefon, talep no', width: '240px' },
      { kind: 'select', key: 'status', label: 'Durum', options: [{ value: '', label: 'Tümü' }] },
      // Aralık BOŞ başlar. Kitin varsayılanı son 7 gündür; bu ekranda o
      // varsayılan, on gün önce link gönderilmiş ve HÂLÂ ÖDENMEMİŞ talebi
      // sessizce gizler — hem listede hem Elden Kapatma sekmesinde. Bekleyen
      // parayı görünmez yapan bir varsayılan kabul edilemez; daraltmak
      // isteyen hazır çipleri (7/30/90 gün) kullanır.
      { kind: 'dateRange', key: 'range', label: 'Açılış', start: '', end: '' },
      { kind: 'numRange', key: 'amount', label: 'Tutar', money: true },
    ],
    onChange: () => refresh({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm kayıtları rapor klasörüne yaz', onClick: exportAll }),
      button('Tahsilat icmali', { onClick: () => report.run('collection', reportRange()) }),
      button('Açık linkler', { onClick: () => report.run('openlinks', reportRange()) }),
    ],
  });

  nodes.kpi = h('div', 'pg-kpi');
  nodes.tableWrap = h('div', 'pg-table');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size }),
  });
  nodes.listView = h('div', 'pg-list');
  nodes.listView.append(nodes.filters.node, nodes.kpi, nodes.tableWrap, nodes.pager.node);

  nodes.settleHost = h('div', 'pg-col');
  nodes.templateHost = h('div', 'pg-col');

  view.append(nodes.tabs.node, nodes.status.node, nodes.notices, nodes.body);
  nodes.body.append(nodes.newView);

  function reportRange() {
    const values = nodes.filters ? nodes.filters.values() : {};
    const range = values.range || {};
    return { start: range.start || '', end: range.end || '' };
  }

  function showView(key) {
    state.tab = key;
    if (key === 'new') {
      nodes.body.replaceChildren(nodes.newView);
      requestQuote();
    } else if (key === 'list') {
      nodes.body.replaceChildren(nodes.listView);
      if (!state.loaded) refresh();
    } else if (key === 'settle') {
      nodes.body.replaceChildren(nodes.settleHost);
      if (!state.loaded) refresh();
      else renderSettle();
    } else {
      nodes.body.replaceChildren(nodes.templateHost);
      renderTemplate();
    }
  }

  root.replaceChildren(view);
  nodes.status.set('Durum okunuyor…');
  renderChosenLines();
  renderProductResults();
  renderQuote();
  loadState().then(() => {
    nodes.form.set('email', state.defaultEmail);
    return loadReference();
  }).then(() => refresh());

  return () => {
    nodes.filters?.destroy();          // arama alanı bekleyen debounce tutar
    forms.forEach((form) => { try { form.destroy(); } catch { /* kapanışta yutulur */ } });
    forms.length = 0;
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY, lines: [] };
    busy = false;
  };
}
