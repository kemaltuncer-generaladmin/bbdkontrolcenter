// Vergilendirme paneli — KDV oranları, vergi kategorileri, ürün eşlemesi ve
// dönem KDV icmali.
//
// NE YAPAR: beş sekme. `Vergi oranları` oranları kapsamlarıyla listeler ve
// çakışanları işaretler; `Kategoriler` oran demetlerini yönetir; `Bölgeler`
// oranlardan türetilmiş ülke/eyalet görünümü verir; `Ürün eşlemesi` ürünleri
// vergi kategorisine toplu bağlar (ÖNCE FARK TABLOSU); `KDV icmali` dönem
// matrah/KDV/iade kırılımını çıkarır ve yazdırılabilir PDF üretir.
//
// KDV İCMALİ BU EKRANIN ASIL İŞİDİR. `accountant` rolü panele girince oraya
// düşer: rakamlar mali müşavire gidiyor.
//
// NE YAPMAZ:
//  · Öncelik/bileşik vergi göstermez. Bagisto çekirdeğinde bu kavramlar YOK;
//    boş bir sütun koymak "ayarlayabilirim" izlenimi verirdi.
//  · Vergi oranı SİLMEZ. Silinen oran geçmiş faturaların dayanağını görünmez
//    kılar; kategoriden çıkarmak yeter ve geri alınabilir.
//  · Geçmişe dönük geçerlilik tarihi kabul etmez — mağaza oranı geriye dönük
//    uygulamaz, tarihi geçmişe yazmak yalan bir kayıt üretir.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Oran değişikliği GEÇMİŞ faturaları etkilemez. Her yazma diyaloğunda ve
//    her rapor dipnotunda yazar; en sık sorulan soru budur.
//  · Geçerlilik tarihi YEREL bir nottur. Bagisto'da böyle bir alan yok; ekran
//    bunu "Kontrol Merkezi notu" diye işaretler, mağaza davranışı gibi değil.
//  · Ürün listesi vergi kategorisi alanını taşımayabilir; o zaman satır
//    "Bilinmiyor" der. "Atanmamış" YAZMAZ — o bir yalan olurdu.
//  · Toplu eşlemede mağazada toplu uç yok: ürünler sırayla yazılır ve sınır
//    küçüktür. Ekran kaç dakika süreceğini söyler.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_tax/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_tax/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmWithReason, csvBlob, h, loadStyles, money, num, percent, toaster,
  todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { applyChoiceFilter, resolveChoice } from '../../ui-kit/choice.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { createPicker } from '../../ui-kit/picker.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_tax';

const TABS = [
  { key: 'rates', label: 'Vergi oranları' },
  { key: 'categories', label: 'Kategoriler' },
  { key: 'regions', label: 'Bölgeler' },
  { key: 'mapping', label: 'Ürün eşlemesi' },
  { key: 'vat', label: 'KDV icmali' },
];

// `count` alanı BOŞ DA OLSA verilir: kit çipin sayaç kutusunu yalnız `count`
// tanımlıysa oluşturuyor, sonradan `counts()` çağırmak sessizce hiçbir şey
// yapmıyordu — sayılar hiç görünmüyordu.
const RATE_CHIPS = [
  { key: 'unassigned', label: 'Hiçbir ürüne atanmamış', count: 0 },
  { key: 'conflict', label: 'Çakışan kural', count: 0 },
];

// FONKSİYON, sabit değil: `{...EMPTY}` sığ kopyaladığı için içteki diziler
// mountlar arasında PAYLAŞILIRDI ve panel ikinci açılışında eski seçimle
// gelirdi.
const empty = () => ({
  tab: 'rates',
  rates: { items: [], conflicts: [], counts: {}, regions: [], connected: false, error: '',
    scannedAt: '', notice: '' },
  categories: { items: [], rates: [], connected: false, error: '' },
  mapping: { items: [], total: 0, page: 1, size: 50, pages: 0, categories: [],
    taxKnown: false, limit: 0, connected: false, error: '' },
  selection: [],
  vat: null,
  channelNames: [],
});

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = empty();

const nodes = {};
const disposers = [];      // cleanup'ta çağrılacak GERÇEK kaynak bırakıcılar

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

/** Yazan her işlem buradan geçer: gerekçe backend'e gider ve denetime yazılır. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function track(disposable) {
  disposers.push(() => disposable.destroy());
  return disposable;
}

/**
 * Açık bir çekmece/diyalog için kaynak bırakıcı.
 *
 * Panel kapanırken çağrılması ŞART (form ve dinleyici tutuyorlar) ama pencere
 * kendi kapandığında listeden ÇIKARILMALI: her açılışta bir kayıt ekleyip hiç
 * silmemek, uzun bir oturumda kapanmış pencerelerin bırakıcılarını sonsuza
 * kadar biriktiriyordu. Dönen işlev hem bırakır hem listeden düşürür ve iki kez
 * çağrılmaya dayanıklıdır.
 */
function trackWhileOpen(release) {
  let done = false;
  const entry = () => {
    if (done) return;
    done = true;
    release();
  };
  disposers.push(entry);
  return () => {
    entry();
    const at = disposers.indexOf(entry);
    if (at >= 0) disposers.splice(at, 1);
  };
}

/** Sekme değişince o sekmenin kaynakları bırakılır; birikmesin. */
function dropTabResources() {
  while (nodes.tabDisposers && nodes.tabDisposers.length) {
    const drop = nodes.tabDisposers.pop();
    try { drop(); } catch { /* kapanışta hata yutulur */ }
  }
}

function tabTrack(disposable) {
  nodes.tabDisposers.push(() => disposable.destroy());
  return disposable;
}

// =================================================== 1 · VERGİ ORANLARI

function rateFilters() {
  const values = nodes.rateBar ? nodes.rateBar.values() : {};
  const range = values.percent || {};
  return {
    q: values.q || '',
    minPercent: range.min ?? null,
    maxPercent: range.max ?? null,
    region: values.region || '',
    flag: nodes.rateChips?.active || '',
  };
}

async function loadRates() {
  nodes.body.replaceChildren(nodes.rateBar.node, nodes.rateChips.node, nodes.status.node,
    skeletonRows(6, 6));
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(rateFilters())) {
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  let payload;
  try {
    payload = await api(`${BASE}/rates?${params.toString()}`);
  } catch (error) {
    state.rates = { ...state.rates, connected: false, error: error.message, items: [] };
    renderRates();
    return;
  }
  state.rates = payload;
  nodes.rateBar.options('region', [
    { value: '', label: 'Tümü — bölge' },
    ...(payload.regions || []).map((item) => ({ value: item, label: item })),
  ]);
  nodes.rateChips.counts({
    unassigned: payload.counts?.unassigned ?? 0,
    conflict: payload.counts?.conflict ?? 0,
  });
  renderRates();
}

function rateStatus() {
  const data = state.rates;
  if (!data.connected) return `Mağazaya ulaşılamadı — ${data.error}`;
  const scanned = data.scannedAt
    ? ` · ürün sayıları ${data.scannedAt.slice(0, 10)} taramasından`
    : ' · ürün sayıları henüz taranmadı';
  return `Bağlı · ${num(data.total)}/${num(data.all)} oran${scanned}`;
}

function renderRates() {
  const data = state.rates;
  const host = h('div', 'tx-stack');

  // Bu uyarı her açılışta görünür ve KAPATILAMAZ: muhasebenin en sık sorduğu
  // soru budur ve cevabı ekranda durmalı.
  host.append(alertBox(data.notice || 'Oran değişikliği geçmiş faturaları etkilemez.', 'info'));

  // İlişki okunamıyorsa çakışma/bağlantısız süzgeçleri hiçbir şey bulamaz;
  // sessizce boş çip göstermek "sorun yok" izlenimi verirdi.
  if (data.connected && data.membershipKnown === false) {
    host.append(alertBox(data.membershipNotice, 'warn'));
  }
  nodes.rateChips.node.hidden = data.connected && data.membershipKnown === false;

  const clashes = (data.conflicts || []).filter((item) => item.kind === 'conflict');
  if (clashes.length) {
    host.append(alertBox(
      `${clashes.length} çakışan kural var: aynı vergi kategorisinde iki oran aynı bölgeyi `
      + 'karşılıyor ve mağaza hangisini uyguladığını söylemiyor. «Çakışan kural» çipiyle '
      + 'süzebilirsiniz.', 'warn'));
  }

  if (!data.connected) {
    host.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: data.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadRates() })],
    }));
  } else {
    nodes.rateTable = dataTable({
      columns: [
        { key: 'name', label: 'Ad', width: 'minmax(0, 2fr)', sortable: true,
          cell: (row) => clip(h('b'), row.name, 40) },
        { key: 'percent', label: 'Oran', width: '90px', align: 'num', sortable: true,
          cell: (row) => h('b', 'tx-rate', row.percentLabel) },
        { key: 'region', label: 'Bölge', width: 'minmax(0, 1.4fr)' },
        { key: 'zipLabel', label: 'Posta kodu', width: 'minmax(0, 1.2fr)' },
        { key: 'categoryCount', label: 'Vergi kategorisi', width: '130px', align: 'num',
          cell: (row) => categoryCell(row, data.membershipKnown !== false) },
        { key: 'effectiveFrom', label: 'Geçerlilik', width: '120px',
          title: 'Kontrol Merkezi notu — Bagisto vergi oranında tarih alanı yoktur',
          cell: (row) => (row.effectiveFrom
            ? h('span', 'tx-local', row.effectiveFrom)
            : h('span', 'tx-sub', '—')) },
        { key: 'flags', label: 'Durum', width: '160px', cell: (row) => flagCell(row) },
      ],
      rows: data.items || [],
      onRow: (row) => openRate(row.id),
      empty: emptyState({
        title: 'Bu süzgece uyan oran yok',
        text: `Mağazada ${num(data.all)} oran tanımlı. Süzgeci gevşetin ya da temizleyin.`,
        actions: [button('Filtreyi temizle', { onClick: () => nodes.rateBar.reset() })],
      }),
    });
    host.append(nodes.rateTable.node);
  }

  host.append(hintBox(
    'Vergi oranı SİLİNMEZ: silinen oran geçmiş faturaların dayanağını görünmez kılar. '
    + 'Kullanımdan kaldırmak için oranı vergi kategorisinden çıkarın — geri alınabilir.'));

  nodes.body.replaceChildren(nodes.rateBar.node, nodes.rateChips.node, nodes.status.node, host);
  nodes.status.set(rateStatus(), !data.connected);
}

function categoryCell(row, known) {
  // İlişki okunamıyorsa 0 YAZILMAZ: sıfır "hiçbir kategoride yok" demektir ve
  // canlıda liste ucu ilişkiyi hiç döndürmüyor.
  if (!known) {
    const cell = h('span', 'tx-sub', 'okunamadı');
    cell.title = 'Mağazanın vergi kategorisi liste ucu oran bağlantısını döndürmüyor.';
    return cell;
  }
  const box = h('span', 'tx-flags');
  box.append(h('b', undefined, num(row.categoryCount)));
  if (!row.categoryCount) box.append(badge('bağlantısız', 'warn'));
  return box;
}

function flagCell(row) {
  // Renk tek başına anlam taşımaz: her rozetin yanında yazı var.
  const box = h('span', 'tx-flags');
  if (row.conflict) box.append(badge('çakışma', 'bad'));
  if (row.unassigned) box.append(badge('ürünsüz', 'warn'));
  if (!row.conflict && !row.unassigned) {
    box.append(h('span', 'tx-sub', row.unassignedReason || 'temiz'));
  }
  if (row.unassignedReason) box.title = row.unassignedReason;
  return box;
}

async function openRate(rateId) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  let release = () => {};
  const box = drawer(nodes.root, {
    title: rateId ? 'Oran yükleniyor…' : 'Yeni vergi oranı',
    subtitle: rateId ? `#${rateId}` : 'Mağazaya yeni oran eklenir',
    onClose: () => release(),
  });
  release = trackWhileOpen(dropForms);

  let current = { name: '', percent: null, country: 'TR', state: '', isRange: false,
    zipCode: '*', zipFrom: '', zipTo: '', effectiveFrom: '', effectiveNote: '' };

  if (rateId) {
    box.body.append(skeletonRows(5, 2));
    let payload;
    try {
      payload = await call(`${BASE}/rates/${rateId}`);
    } catch (error) {
      box.body.replaceChildren(emptyState({
        title: 'Oran okunamadı',
        text: error.message,
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }
    const row = payload.rate;
    if (!row) {
      // İkinci emniyet kemeri. Künyesiz yanıtı servis zaten `ok:false` ile
      // reddediyor (yukarıdaki catch dalına düşer); burası servis sözleşmesi
      // değişirse çekmecenin iskeletle ASILI KALMASINI engeller — sessiz
      // bekleyen bir pencere, hata mesajından daha kötüdür.
      box.body.replaceChildren(emptyState({
        title: 'Oran künyesi okunamadı',
        text: 'Mağaza beklenen alanları döndürmedi; düzenleme açılmadı.',
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }
    box.setTitle(row.name);
    current = {
      name: row.name, percent: row.percent, country: row.country || 'TR', state: row.state,
      isRange: row.zip.kind === 'range', zipCode: row.zip.kind === 'single' ? row.zip.from : '*',
      zipFrom: row.zip.kind === 'range' ? row.zip.from : '',
      zipTo: row.zip.kind === 'range' ? row.zip.to : '',
      effectiveFrom: row.effectiveFrom, effectiveNote: row.effectiveNote,
    };
  }
  box.body.replaceChildren();

  const form = formGrid({
    fields: [
      { key: 'name', label: 'Oran adı', type: 'text', required: true, maxLength: 120, wide: true,
        hint: 'Muhasebe raporunda bu ad görünür.' },
      { key: 'percent', label: 'Oran (%)', type: 'number', required: true, min: 0, max: 100,
        hint: 'Ondalık yazılabilir: 8,5' },
      { key: 'country', label: 'Ülke kodu', type: 'text', required: true, maxLength: 2,
        hint: 'İki harf: TR' },
      { key: 'state', label: 'Eyalet/il kodu', type: 'text', maxLength: 64,
        hint: 'Boş bırakılırsa ülkenin tamamı.' },
      { key: 'isRange', label: 'Posta kodu ARALIĞI kullan', type: 'checkbox',
        hint: 'Kapalıyken tek posta kodu ya da joker (*) geçerlidir.' },
      { key: 'zipCode', label: 'Posta kodu', type: 'text', maxLength: 24,
        hint: '* = tüm posta kodları.' },
      { key: 'zipFrom', label: 'Aralık başlangıcı', type: 'text', maxLength: 24 },
      { key: 'zipTo', label: 'Aralık bitişi', type: 'text', maxLength: 24 },
    ],
    value: current,
  });
  forms.push(form);

  const localForm = formGrid({
    fields: [
      { key: 'effectiveFrom', label: 'Geçerlilik başlangıcı', type: 'date',
        hint: 'Bugünden önceye verilemez.' },
      { key: 'effectiveNote', label: 'Not (tebliğ, karar no)', type: 'text', maxLength: 255,
        wide: true },
    ],
    value: { effectiveFrom: current.effectiveFrom, effectiveNote: current.effectiveNote },
  });
  forms.push(localForm);

  const actions = h('div', 'tx-actions');
  actions.append(button(rateId ? 'Oranı kaydet' : 'Oranı ekle', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const draft = form.draft();
      const local = localForm.draft();
      if (local.effectiveFrom && local.effectiveFrom < todayIso()) {
        toast('Geçerlilik tarihi geçmişe verilemez.', 'bad');
        return;
      }
      const reason = await askReason({
        title: rateId ? 'Vergi oranını güncelle' : 'Vergi oranı ekle',
        description: `${draft.name} · %${draft.percent}. Değişiklik YALNIZ bundan sonraki `
          + 'hesaplamalarda kullanılır; kesilmiş faturalar hangi orandan kesildiyse öyle kalır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Oran kaydediliyor…', async () => {
        const body = {
          name: draft.name,
          percent: Number(draft.percent),
          country: String(draft.country || '').toUpperCase(),
          state: draft.state || '',
          isRange: Boolean(draft.isRange),
          zipCode: draft.zipCode || '*',
          zipFrom: draft.zipFrom || '',
          zipTo: draft.zipTo || '',
          effectiveFrom: local.effectiveFrom || '',
          effectiveNote: local.effectiveNote || '',
          reason,
          dryRun: false,
        };
        const result = rateId
          ? await call(`${BASE}/rates/${rateId}`, { method: 'PUT', body })
          : await call(`${BASE}/rates`, { method: 'POST', body });
        toast('Oran kaydedildi.', 'good');
        if (result.notice) toast(result.notice, 'warn');
        box.close();
        loadRates();
      });
    },
  }));

  if (rateId) {
    actions.append(button('Yalnız geçerlilik notunu kaydet', {
      title: 'Mağazaya hiç istek gitmez; bu bir defter notudur',
      onClick: async () => {
        const local = localForm.draft();
        await withBusy('Not kaydediliyor…', async () => {
          const result = await call(`${BASE}/rates/${rateId}/effective`, {
            method: 'POST',
            body: { effectiveFrom: local.effectiveFrom || '', note: local.effectiveNote || '' },
          });
          toast(result.note, 'good');
          loadRates();
        });
      },
    }));
  }

  const history = h('div', 'tx-history');
  box.body.append(
    alertBox('Oran değişikliği GEÇMİŞ faturaları etkilemez. Mağaza yeni oranı yalnız bundan '
      + 'sonraki hesaplamalarda kullanır.', 'info'),
    card('Oran ve kapsam', form.node),
    card('Geçerlilik (Kontrol Merkezi notu)', localForm.node,
      'Bagisto vergi oranında tarih tutmaz'),
    actions,
    hintBox('Öncelik ve bileşik vergi alanları YOKTUR: Bagisto çekirdeğinde bu kavramlar '
      + 'bulunmuyor ve gönderilen alan sessizce yok sayılıyor. Boş bir sütun koymak '
      + '«ayarlayabilirim» izlenimi verirdi.'),
  );

  // GEREKÇE NEREDE GÖRÜLÜR: yerel denetim izi yalnız bu ekranda okunabilir
  // (K5 — başka modül bu tabloyu okuyamaz). Uç açık olup hiçbir ekranda
  // görünmemesi, kaydı yazıp kimseye göstermemek demekti.
  if (rateId) {
    box.body.append(card('Bu oranın işlem geçmişi', history, 'Kontrol Merkezi denetim izi'));
    loadHistory(history, `rate:${rateId}`);
  }
}

/** Yerel denetim izini çeker; boşsa "kayıt yok" der, sessiz kalmaz. */
async function loadHistory(host, target) {
  host.replaceChildren(skeletonRows(3, 3));
  let payload;
  try {
    payload = await api(`${BASE}/audit?target=${encodeURIComponent(target)}&limit=20`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'warn'));
    return;
  }
  const rows = payload.items || [];
  if (!rows.length) {
    host.replaceChildren(h('div', 'tx-sub', 'Bu kayıt için henüz işlem geçmişi yok.'));
    return;
  }
  host.replaceChildren(dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '160px',
        // Kayıt UTC ISO tutuluyor (`2026-08-13T19:54:…`); 'T' ayracı okunmuyor.
        cell: (row) => h('span', 'tx-sub',
          String(row.createdAt || '').slice(0, 16).replace('T', ' ')) },
      { key: 'action', label: 'İşlem', width: 'minmax(0,1fr)' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'actor', label: 'Kim', width: 'minmax(0,1fr)' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0,2fr)',
        cell: (row) => clip(h('span'), row.reason, 60) },
    ],
    rows,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}-${row.result}`,
  }).node);
}

// ==================================================== 2 · KATEGORİLER

async function loadCategories() {
  nodes.body.replaceChildren(nodes.status.node, skeletonRows(5, 5));
  let payload;
  try {
    payload = await api(`${BASE}/categories`);
  } catch (error) {
    state.categories = { items: [], rates: [], connected: false, error: error.message };
    renderCategories();
    return;
  }
  state.categories = payload;
  renderCategories();
}

function renderCategories() {
  const data = state.categories;
  const host = h('div', 'tx-stack');

  const bar = h('div', 'tx-bar');
  bar.append(
    button('Yeni vergi kategorisi', { variant: 'primary', onClick: () => openCategory(null) }),
    button('Ürün sayılarını tara', {
      title: '1.419 ürün sayfa sayfa okunur; birkaç dakika sürebilir',
      onClick: () => scanUsage(),
    }),
    h('span', 'kit-spacer'),
    h('span', 'tx-sub', data.scannedAt
      ? `Ürün sayıları: ${data.scannedAt.slice(0, 10)} taraması`
      : 'Ürün sayıları henüz taranmadı'),
  );
  host.append(bar);

  if (data.connected && data.membershipKnown === false) {
    host.append(alertBox(data.membershipNotice, 'warn'));
  }

  if (!data.connected) {
    host.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: data.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadCategories() })],
    }));
  } else if (!(data.items || []).length) {
    host.append(emptyState({
      title: 'Hiç vergi kategorisi yok',
      text: 'Vergi kategorisi, oranların demetidir ve ürüne o bağlanır. Kategorisiz üründen '
        + 'vergi hesaplanmaz.',
      actions: [button('Yeni vergi kategorisi', { variant: 'primary',
        onClick: () => openCategory(null) })],
    }));
  } else {
    const table = dataTable({
      columns: [
        { key: 'name', label: 'Ad', width: 'minmax(0, 1.8fr)', sortable: true },
        { key: 'code', label: 'Kod', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'percentLabel', label: 'Oranlar', width: 'minmax(0, 1.4fr)' },
        { key: 'rateCount', label: 'Oran', width: '80px', align: 'num',
          // `rateCount === null` = ilişki okunamadı. "oransız" yazmak yalandır.
          cell: (row) => rateCountCell(row) },
        { key: 'productCount', label: 'Ürün', width: '110px', align: 'num',
          cell: (row) => productCountCell(row) },
      ],
      rows: data.items,
      onRow: (row) => openCategory(row.id),
    });
    host.append(table.node);
  }

  host.append(hintBox(
    'Ürün sayısı YEREL bir tarama sonucudur, canlı değer değildir: 1.419 ürünü her açılışta '
    + 'okumak mağazanın dakikalık istek payını tüketirdi. Sayının ne zaman çıkarıldığı '
    + 'yukarıda yazar.'));

  nodes.body.replaceChildren(nodes.status.node, host);
  nodes.status.set(data.connected
    ? `Bağlı · ${num((data.items || []).length)} vergi kategorisi`
    : `Mağazaya ulaşılamadı — ${data.error}`, !data.connected);
}

function rateCountCell(row) {
  if (row.ratesKnown === false || row.rateCount === null || row.rateCount === undefined) {
    const cell = h('span', 'tx-sub', 'okunamadı');
    cell.title = 'Mağazanın liste ucu bu kategorinin oranlarını döndürmüyor.';
    return cell;
  }
  return row.rateCount ? num(row.rateCount) : badge('oransız', 'bad');
}

function productCountCell(row) {
  // "Taranmadı" ile "ürünü yok" ayrı şeylerdir; sıfır uydurulmaz.
  if (row.productCount === null || row.productCount === undefined) {
    const cell = h('span', 'tx-sub', 'taranmadı');
    cell.title = 'Ürün sayıları için «Ürün sayılarını tara» düğmesini kullanın.';
    return cell;
  }
  const box = h('span', 'tx-flags');
  box.append(h('b', undefined, num(row.productCount)));
  if (!row.productCount) box.append(badge('ürünsüz', 'warn'));
  return box;
}

async function scanUsage() {
  await withBusy('Katalog taranıyor…', async () => {
    const result = await call(`${BASE}/mapping/scan`, { method: 'POST', body: {} });
    toast(`${num(result.products)} ürün tarandı · ${num(result.unassigned)} ürünün vergi `
      + 'kategorisi yok.', 'good');
    if (result.truncated) toast('Katalog tavana dayandı; sayılar eksik olabilir.', 'warn');
    loadCategories();
  });
}

async function openCategory(categoryId) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const existing = (state.categories.items || []).find((item) => item.id === categoryId);
  let release = () => {};
  const box = drawer(nodes.root, {
    title: existing ? existing.name : 'Yeni vergi kategorisi',
    subtitle: existing ? `#${existing.id} · ${existing.code}` : 'Oranların demeti',
    onClose: () => release(),
  });
  release = trackWhileOpen(dropForms);

  const form = formGrid({
    fields: [
      { key: 'code', label: 'Kod', type: 'text', required: true, maxLength: 64,
        hint: 'Ürünün vergi özniteliği bu kodla eşleşir.' },
      { key: 'name', label: 'Ad', type: 'text', required: true, maxLength: 120 },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500, wide: true },
    ],
    value: existing
      ? { code: existing.code, name: existing.name, description: existing.description }
      : { code: '', name: '', description: '' },
  });
  forms.push(form);

  const picker = createPicker({
    items: (state.categories.rates || []).map((item) => ({
      id: item.id,
      name: `${item.name} — ${item.percentLabel}`,
      group: item.region,
      meta: item.zipLabel,
    })),
    groupLabel: 'Bölge',
    placeholder: 'Oran ara',
  });
  if (existing) picker.select(existing.rateIds);

  // MEVCUT KATEGORİ, OKUNAMAYAN İLİŞKİ = KAYDETME YOK. Bagisto kısmi oran
  // listesi kabul etmiyor: göndermediğimiz oran kategoriden düşer. Seçili
  // gelmeyen oranları kaydetmek onları sessizce silerdi. Düğme gizlenmez,
  // AÇIKÇA kapalı gösterilir ve nedeni yazar (sessiz ölü düğme bırakılmaz).
  const locked = Boolean(existing) && existing.ratesKnown === false;

  const actions = h('div', 'tx-actions');
  if (locked) {
    box.body.append(alertBox(
      'Bu kategori DÜZENLENEMİYOR: bağlı oranları mağazanın liste ucundan okunamıyor ve '
      + 'kaydetmek onları kategoriden düşürürdü. Aşağıdaki seçim yalnız bilgi amaçlıdır.',
      'bad'));
  }
  actions.append(button(existing ? 'Kategoriyi kaydet' : 'Kategoriyi ekle', {
    disabled: locked,
    title: locked ? 'Oran bağlantısı okunamadığı için kapalı' : '',
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const ids = picker.selection().map(Number);
      if (!ids.length) {
        toast('En az bir oran seçin: oransız kategori vergi hesaplamaz.', 'bad');
        return;
      }
      const draft = form.draft();
      const reason = await askReason({
        title: existing ? 'Vergi kategorisini güncelle' : 'Vergi kategorisi ekle',
        description: `${draft.name} · ${ids.length} oran bağlanacak. Oran listesi TAM `
          + 'gönderilir: seçimden çıkardığınız oran kategoriden kalkar.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Kategori kaydediliyor…', async () => {
        const body = { ...draft, rateIds: ids, reason, dryRun: false };
        const result = existing
          ? await call(`${BASE}/categories/${existing.id}`, { method: 'PUT', body })
          : await call(`${BASE}/categories`, { method: 'POST', body });
        toast('Vergi kategorisi kaydedildi.', 'good');
        if (result.notice) toast(result.notice, 'warn');
        box.close();
        loadCategories();
      });
    },
  }));

  box.body.append(
    form.node,
    card('Bağlı oranlar', picker.node, 'Seçili olmayan oran kategoriden kalkar'),
    actions,
    hintBox('Aynı kategoride aynı bölgeyi karşılayan iki oran ÇAKIŞMADIR: mağaza hangisini '
      + 'uyguladığını söylemez. Oranlar sekmesindeki «Çakışan kural» çipi bunları listeler.'),
  );
}

// ======================================================= 3 · BÖLGELER

async function loadRegions() {
  nodes.body.replaceChildren(nodes.status.node, skeletonRows(5, 4));
  let payload;
  try {
    payload = await api(`${BASE}/regions`);
  } catch (error) {
    nodes.body.replaceChildren(nodes.status.node, alertBox(error.message, 'bad'));
    nodes.status.set(error.message, true);
    return;
  }

  const host = h('div', 'tx-stack');
  host.append(alertBox(payload.notice, 'info'));

  if (!payload.connected) {
    host.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: payload.error,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadRegions() })],
    }));
  } else if (!(payload.items || []).length) {
    host.append(emptyState({
      title: 'Tanımlı bölge yok',
      text: 'Bölgeler oranlardan türer; henüz hiç vergi oranı tanımlanmadı.',
      actions: [button('Oran ekle', { variant: 'primary', onClick: () => openRate(null) })],
    }));
  } else {
    for (const region of payload.items) {
      const list = h('div', 'tx-region');
      for (const rate of region.rates) {
        const line = h('div', 'tx-region-row');
        line.append(
          h('b', 'tx-rate', rate.percentLabel),
          h('span', undefined, rate.name),
          h('span', 'tx-sub', rate.zipLabel),
        );
        list.append(line);
      }
      host.append(card(region.label, list, `${num(region.rateCount)} oran`));
    }
  }

  nodes.body.replaceChildren(nodes.status.node, host);
  nodes.status.set(payload.connected
    ? `Bağlı · ${num((payload.items || []).length)} bölge (oranlardan türetildi)`
    : `Mağazaya ulaşılamadı — ${payload.error}`, !payload.connected);
}

// ================================================= 4 · ÜRÜN EŞLEMESİ

async function loadMapping({ page = state.mapping.page, size = state.mapping.size } = {}) {
  const values = nodes.mapBar ? nodes.mapBar.values() : {};
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (values.q) params.set('q', values.q);
  if (values.taxCategory) params.set('taxCategoryId', String(values.taxCategory));

  nodes.mapTableWrap.replaceChildren(skeletonRows(8, 4));
  let payload;
  try {
    payload = await api(`${BASE}/mapping?${params.toString()}`);
  } catch (error) {
    // `note` de sıfırlanır: bağlantı hatasında önceki süzgeç açıklaması ekranda
    // asılı kalmasın.
    state.mapping = { ...state.mapping, connected: false, error: error.message, items: [],
      note: '' };
    renderMapping();
    return;
  }
  state.mapping = payload;
  state.selection = [];
  // TEK VERGİ KATEGORİLİ MAĞAZADA SÜZGEÇ KUTUSU ÇİZİLMEZ (`choice.js`):
  // "KDV" seçmek 1.422 ürünün tamamını gösterir, yani hiçbir şey elemez.
  // İkinci kategori açılırsa kutu kendiliğinden geri gelir. Kategori LİSTESİ
  // (bu ekranın 2. sekmesi) elbette durur — kaldırılan şey seçim kutusu.
  applyChoiceFilter(nodes.mapBar, 'taxCategory', payload.categories,
                    { allLabel: 'Tümü — vergi kategorisi' });
  renderMapping();
  nodes.mapPager.update({ total: payload.total, page: payload.page, size: payload.size });
}

function renderMapping() {
  const data = state.mapping;
  nodes.mapTableWrap.replaceChildren();

  // Süzgeç uygulanamadıysa BOŞ TABLO SESSİZ KALMAZ: "bu kategoride ürün yok"
  // sanılmasın.
  if (data.note) nodes.mapTableWrap.append(alertBox(data.note, 'bad'));

  if (!data.taxKnown && (data.items || []).length) {
    nodes.mapTableWrap.append(alertBox(
      'Ürün listesi vergi kategorisi alanını taşımıyor; sütun «Bilinmiyor» gösteriyor. '
      + '«Atanmamış» YAZILMADI çünkü bu bir yalan olurdu — kesin değer, seçim yapıp '
      + 'önizleme aldığınızda ürün tek tek okunarak gelir.', 'warn'));
  }

  nodes.mapTable = dataTable({
    columns: [
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2.4fr)',
        cell: (row) => clip(h('span'), row.name, 52) },
      { key: 'price', label: 'Fiyat', width: '120px', align: 'num',
        cell: (row) => money(row.price) },
      { key: 'taxCategoryName', label: 'Vergi kategorisi', width: 'minmax(0, 1.4fr)',
        cell: (row) => (row.taxKnown
          ? h('span', row.taxCategoryId ? '' : 'tx-warn', row.taxCategoryName)
          : h('span', 'tx-sub', 'Bilinmiyor')) },
    ],
    rows: data.items || [],
    selectable: true,
    empty: data.connected
      ? emptyState({
        title: 'Bu süzgece uyan ürün yok',
        text: 'Aramayı gevşetin ya da vergi kategorisi süzgecini kaldırın.',
        actions: [button('Filtreyi temizle', { onClick: () => nodes.mapBar.reset() })],
      })
      : emptyState({
        title: 'Mağazaya ulaşılamadı',
        text: data.error || 'Bağlantı kurulamadı.',
        actions: [button('Tekrar dene', { variant: 'primary', onClick: () => loadMapping() })],
      }),
    onSelect: (ids) => { state.selection = ids; renderMapSelection(); },
  });
  nodes.mapTableWrap.append(nodes.mapTable.node);
  renderMapSelection();
  nodes.status.set(data.connected
    ? `Bağlı · ${num(data.total)} ürün · sayfa ${data.page}/${Math.max(1, data.pages)}`
    : `Mağazaya ulaşılamadı — ${data.error}`, !data.connected);
}

function renderMapSelection() {
  const bar = nodes.mapSelbar;
  bar.replaceChildren();
  const count = state.selection.length;
  if (!count) { bar.classList.remove('on'); return; }
  bar.classList.add('on');

  const limit = state.mapping.limit || 0;
  // Kategori listesi okunamadıysa hedef seçilemez. Düğme SESSİZ ÖLÜ BIRAKILMAZ:
  // açıkça kapatılır ve nedeni hem başlıkta hem çubukta yazar.
  const options = state.mapping.categories || [];
  const choice = resolveChoice(options);
  const blocked = choice.mode === 'none';

  // HEDEF TEK İSE SORULMAZ. Kutu "seçin" diyordu ama seçilecek tek bir şey
  // vardı; kullanıcı olmayan bir karar veriyordu. Tek kategoride hedef yazıyla
  // gösterilir ve kendiliğinden seçilir — işin kendisi (fark tablosu + toplu
  // eşleme) aynen çalışır. İkinci kategori açılırsa kutu geri gelir.
  let target = null;
  let chosenId = () => Number(choice.value) || 0;
  if (choice.mode === 'many') {
    target = h('select', 'kit-select');
    for (const option of [{ value: '', label: 'Hedef vergi kategorisi seçin' },
      ...choice.options]) {
      const item = h('option', undefined, option.label);
      item.value = String(option.value);
      target.append(item);
    }
    chosenId = () => Number(target.value) || 0;
  }

  bar.append(h('b', undefined, `${num(count)} ürün seçildi`));
  if (target) bar.append(target);
  else if (choice.mode === 'single') {
    bar.append(h('span', 'tx-sub', `Hedef: ${choice.options[0].label}`));
  }
  if (limit && count > limit) {
    bar.append(badge(`sınır ${num(limit)}`, 'bad'));
  }
  if (blocked) {
    bar.append(h('span', 'tx-warn', state.mapping.categoriesError
      ? 'Vergi kategorileri okunamadı — toplu eşleme kapalı.'
      : 'Mağazada tanımlı vergi kategorisi yok — toplu eşleme kapalı.'));
  }
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Fark tablosunu göster', {
      variant: 'primary',
      disabled: blocked,
      title: blocked ? 'Hedef vergi kategorisi listesi okunamadı' : '',
      onClick: () => {
        const wanted = chosenId();
        if (!wanted) { toast('Hedef vergi kategorisi seçin.', 'bad'); return; }
        assignDialog(wanted);
      },
    }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => { nodes.mapTable.clearSelection(); state.selection = []; renderMapSelection(); },
    }),
  );
}

function assignDialog(taxCategoryId) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog tx-assign');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title', 'Toplu vergi kategorisi eşlemesi'));
  box.append(h('p', 'kit-dialog-text',
    `${num(state.selection.length)} ürün seçili. Fark tablosu görülmeden hiçbir şey `
    + 'uygulanmaz; uygulanan şey önizlediğiniz şeydir, yeniden hesaplanmaz.'));

  // Kit'in `drawer`'ı bu iş için DAR kalıyor (760px): fark tablosunun
  // «önce/sonra» sütunları kesiliyor ve önizleme amacını kaybediyor
  // (panel.css `.tx-assign` = 860px). Bu yüzden overlay elle kuruluyor ama
  // kit'in kuralına uyuyor: `nodes.root`'a eklenir, `document.body`'ye DEĞİL.
  const close = () => {
    release();
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  const release = trackWhileOpen(() => document.removeEventListener('keydown', onKey));

  const result = h('div', 'tx-assign-result');
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Vazgeç', { onClick: close }), h('span', 'tx-sub', 'Önizleniyor…'));
  box.append(result, actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);

  result.replaceChildren(skeletonRows(5, 4));
  call(`${BASE}/mapping/preview`, {
    method: 'POST',
    body: { productIds: state.selection.map(Number), taxCategoryId },
  }).then((preview) => paintDiff(preview)).catch((error) => {
    result.replaceChildren(alertBox(error.message, 'bad'));
    actions.replaceChildren(button('Kapat', { onClick: close }));
  });

  function paintDiff(preview) {
    result.replaceChildren();
    const summary = preview.summary;
    result.append(kpiRow([
      { label: 'Satır', value: num(summary.total) },
      { label: 'Değişecek', value: num(summary.changed), tone: 'good' },
      { label: 'Atlanan', value: num(summary.skipped), tone: 'muted' },
      { label: 'Kategorisizken', value: num(summary.fromUnassigned) },
    ]));
    const table = dataTable({
      columns: [
        { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
        { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
        { key: 'before', label: 'Önce', width: 'minmax(0, 1.2fr)' },
        { key: 'after', label: 'Sonra', width: 'minmax(0, 1.2fr)',
          cell: (row) => (row.skipped
            ? h('span', 'tx-sub', '—')
            : h('b', 'tx-good', row.after)) },
      ],
      rows: preview.rows,
      dense: true,
      rowKey: (row) => String(row.id),
    });
    result.append(table.node);
    if (preview.missing && preview.missing.length) {
      result.append(alertBox(`${preview.missing.length} ürün okunamadı ve önizlemeye girmedi.`,
        'warn'));
    }
    result.append(alertBox(preview.note, 'warn'));

    actions.replaceChildren(
      button('Vazgeç', { onClick: close }),
      button('Fark tablosunu CSV al', {
        onClick: () => {
          csvBlob(['SKU', 'Ürün', 'Önce', 'Sonra'],
            preview.rows.map((row) => [row.sku, row.name, row.before,
              row.skipped ? row.before : row.after]),
            'vergi-esleme-farki');
          toast('Fark tablosu indirildi.', 'good');
        },
      }),
      button(`${num(summary.changed)} ürüne uygula`, {
        variant: 'danger',
        disabled: summary.changed === 0,
        onClick: () => apply(preview),
      }),
    );
  }

  async function apply(preview) {
    const reason = await askReason({
      title: 'Toplu vergi kategorisi eşlemesi',
      description: `${num(preview.summary.changed)} ürünün vergi kategorisi değişecek ve bu `
        + 'ürünler bundan sonra yeni orandan satılacak. Geçmiş faturalar etkilenmez.',
      confirmLabel: 'Uygula',
    });
    if (!reason) return;
    await withBusy('Ürünler yazılıyor…', async () => {
      const applied = await call(`${BASE}/mapping/apply`, {
        method: 'POST', body: { token: preview.token, reason, dryRun: false },
      });
      toast(`${num(applied.applied)} ürün eşlendi`
        + (applied.failed ? ` · ${num(applied.failed)} başarısız` : ''),
      applied.failed ? 'warn' : 'good');
      if (applied.notice) toast(applied.notice, 'warn');
      close();
      loadMapping();
    });
  }
}

// ====================================================== 5 · KDV İCMALİ

function vatParams() {
  const values = nodes.vatBar ? nodes.vatBar.values() : {};
  const range = values.range || {};
  return { start: range.start || '', end: range.end || '', channel: values.channel || '' };
}

async function loadVat() {
  nodes.vatResult.replaceChildren(skeletonRows(7, 6));
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(vatParams())) {
    if (value) params.set(key, value);
  }
  let payload;
  try {
    payload = await api(`${BASE}/summary?${params.toString()}`);
  } catch (error) {
    state.vat = null;
    nodes.vatResult.replaceChildren(alertBox(error.message, 'bad'));
    nodes.status.set(error.message, true);
    return;
  }
  state.vat = payload;
  // Kanal listesi VERİDEN gelir: mağazada kanal süzgeci çalışmadığı için
  // ayıklama burada değil serviste, dönen kanal adlarıyla yapılıyor. Seçenekler
  // doldurulmazsa açılır kutu ömrü boyunca «Tüm kanallar» ile kalır ve
  // kullanıcı süzemediğini anlamaz.
  // Kanal adları BİRİKTİRİLİR: süzgeç uygulanınca yanıt yalnız seçili kanalı
  // taşır; listeyi her yanıtla sıfırlamak kullanıcıyı seçtiği kanala kilitlerdi.
  const current = vatParams().channel;
  for (const row of payload.channels || []) {
    if (!state.channelNames.includes(row.channel)) state.channelNames.push(row.channel);
  }
  if (current && !state.channelNames.includes(current)) state.channelNames.push(current);
  // TEK KANALLI MAĞAZADA KUTU ÇİZİLMEZ. İcmalde kanal ayrımı ancak birden çok
  // kanal varken bir soru; tek kanalda "Tüm kanallar" ile "default" aynı
  // icmali verir ve kullanıcı olmayan bir ayrımı arar.
  const shown = applyChoiceFilter(nodes.vatBar, 'channel', state.channelNames,
                                  { allLabel: 'Tüm kanallar' });
  if (shown.mode === 'many') nodes.vatBar.set('channel', current);
  renderVat();
}

function renderVat() {
  const data = state.vat;
  nodes.vatResult.replaceChildren();
  if (!data) return;

  if (!data.ok) {
    // İcmal ÜRETİLMEDİ. Sebep gösterilir ve yanlış bir rakam ekrana KONMAZ:
    // eksik veriyle hesaplanmış bir matrahı göstermek, onu doğru sanmaya davettir.
    nodes.vatResult.append(emptyState({
      title: 'KDV icmali üretilmedi',
      text: data.error,
      actions: [button('Aralığı daralt', { onClick: () => nodes.vatBar.reset() }),
        button('Tekrar dene', { variant: 'primary', onClick: () => loadVat() })],
    }));
    nodes.status.set(data.error, true);
    return;
  }

  const totals = data.totals;
  nodes.vatResult.append(kpiRow([
    { label: 'Net matrah', value: money(totals.netBase) },
    { label: 'Net KDV', value: money(totals.netTax), tone: 'good' },
    { label: 'Net toplam', value: money(totals.netTotal) },
    { label: 'İade KDV', value: money(totals.refundTax), tone: 'bad' },
    { label: 'Fatura', value: num(data.documents.invoices) },
    { label: 'İade', value: num(data.documents.refunds) },
  ]));

  const rateTable = dataTable({
    columns: [
      { key: 'label', label: 'Oran', width: '140px',
        cell: (row) => h('b', 'tx-rate', row.label) },
      { key: 'base', label: 'Matrah', width: 'minmax(0,1fr)', align: 'num',
        cell: (row) => money(row.base) },
      { key: 'tax', label: 'KDV', width: 'minmax(0,1fr)', align: 'num',
        cell: (row) => money(row.tax) },
      { key: 'total', label: 'Toplam', width: 'minmax(0,1fr)', align: 'num',
        cell: (row) => money(row.total) },
      { key: 'refundTax', label: 'İade KDV', width: 'minmax(0,1fr)', align: 'num',
        cell: (row) => (row.refundTax ? h('span', 'tx-bad', `−${money(row.refundTax)}`) : '—') },
      { key: 'netTax', label: 'Net KDV', width: 'minmax(0,1fr)', align: 'num',
        cell: (row) => h('b', undefined, money(row.netTax)) },
      { key: 'documents', label: 'Belge', width: '110px', align: 'num',
        cell: (row) => documentCell(row) },
    ],
    rows: data.rates,
    rowKey: (row) => row.key,
    empty: emptyState({
      title: 'Bu aralıkta fatura yok',
      text: 'Seçilen tarih aralığında kesilmiş fatura bulunmuyor — beyana girecek matrah yok.',
    }),
  });
  nodes.vatResult.append(card('Oran bazlı kırılım', rateTable.node,
    `${data.range.start} – ${data.range.end}`));

  if (data.unresolved.documents) {
    const box = h('div', 'tx-stack');
    box.append(alertBox(
      `${num(data.unresolved.documents)} belgenin oranı çözülemedi (`
      + `${data.unresolved.reasons.join(' · ')}). Tutarlar toplamlara GİRDİ ama oran `
      + 'kırılımında yok; beyan öncesi bu belgelere elle bakılmalı.', 'warn'));
    box.append(kpiRow([
      { label: 'Çözülemeyen matrah', value: money(data.unresolved.base) },
      { label: 'Çözülemeyen KDV', value: money(data.unresolved.tax) },
      { label: 'İade matrah', value: money(data.unresolved.refundBase) },
      { label: 'İade KDV', value: money(data.unresolved.refundTax) },
    ]));
    nodes.vatResult.append(card('Oranı çözülemeyen belgeler', box));
  }

  if (data.channels.length) {
    const channelTable = dataTable({
      columns: [
        { key: 'channel', label: 'Kanal', width: 'minmax(0,1.6fr)' },
        { key: 'base', label: 'Matrah', width: 'minmax(0,1fr)', align: 'num',
          cell: (row) => money(row.base) },
        { key: 'tax', label: 'KDV', width: 'minmax(0,1fr)', align: 'num',
          cell: (row) => money(row.tax) },
        { key: 'refundTax', label: 'İade KDV', width: 'minmax(0,1fr)', align: 'num',
          cell: (row) => (row.refundTax ? h('span', 'tx-bad', `−${money(row.refundTax)}`) : '—') },
        { key: 'netTotal', label: 'Net toplam', width: 'minmax(0,1fr)', align: 'num',
          cell: (row) => h('b', undefined, money(row.netTotal)) },
        { key: 'documents', label: 'Belge', width: '90px', align: 'num',
          cell: (row) => num(row.documents) },
      ],
      rows: data.channels,
      dense: true,
      rowKey: (row) => row.channel,
    });
    nodes.vatResult.append(card('Kanal kırılımı', channelTable.node));
  }

  for (const warning of data.warnings) {
    nodes.vatResult.append(alertBox(warning, 'warn'));
  }
  nodes.vatResult.append(alertBox(data.notice, 'info'));
  nodes.vatResult.append(hintBox(
    'Kaynak belge FATURADIR, sipariş değil: sipariş satış vaadi, fatura vergi doğuran '
    + 'olaydır. Faturası kesilmemiş sipariş beyana girmez. İptaller de düşülmez — faturası '
    + 'kesilmiş bir iptalin düşümü iade kaydıyla zaten gelir.'));

  const ratio = totals.netBase
    ? Math.round((totals.netTax * 1000) / totals.netBase) / 10
    : null;
  nodes.status.set(
    `Bağlı · ${data.range.start} – ${data.range.end} · net KDV ${money(totals.netTax)}`
    + (ratio === null ? '' : ` · ortalama efektif oran ${percent(ratio)}`));
}

function documentCell(row) {
  const box = h('span', 'tx-flags');
  box.append(h('b', undefined, num(row.documents)));
  if (row.derived) {
    const mark = badge(`${num(row.derived)} türetildi`, 'warn');
    mark.title = 'Bu belgelerin kalem kırılımı gelmedi; oran belge toplamından türetildi.';
    box.append(mark);
  }
  return box;
}

// ================================================================== mount

function showTab(key) {
  dropTabResources();
  state.tab = key;
  nodes.body.replaceChildren();
  const painter = { rates: mountRates, categories: loadCategories, regions: loadRegions,
    mapping: mountMapping, vat: mountVat }[key];
  painter?.();
}

function mountRates() {
  nodes.rateBar = tabTrack(filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Oran adı, yüzde ya da bölge', width: '240px' },
      { kind: 'numRange', key: 'percent', label: 'Oran %' },
      { kind: 'select', key: 'region', label: 'Bölge',
        options: [{ value: '', label: 'Tümü — bölge' }] },
    ],
    onChange: () => loadRates(),
    actions: [
      button('Yeni oran', { variant: 'primary', onClick: () => openRate(null) }),
      button('Yenile', { onClick: () => loadRates() }),
      button('⤓ Görünen', { title: 'Ekrandaki oranları CSV indir', onClick: exportVisibleRates }),
      button('⤓ Tümü', { title: 'Tüm oranları rapor klasörüne yaz',
        onClick: () => exportAll('rates') }),
      button('Oran listesi PDF', { onClick: () => report.run('rates', {}) }),
    ],
  }));
  nodes.rateChips = chipRow(RATE_CHIPS, null, () => loadRates());
  loadRates();
}

function exportVisibleRates() {
  // İlişki okunamıyorsa CSV'ye 0 YAZILMAZ — ekran «okunamadı» derken dosyanın
  // "0 kategori" demesi, aynı veriden iki farklı cevap üretmek olurdu.
  const known = state.rates.membershipKnown !== false;
  const rows = (state.rates.items || []).map((row) => [
    row.name, row.percentLabel, row.region, row.zipLabel,
    known ? row.categoryCount : 'okunamadı',
    row.effectiveFrom || '', row.conflict ? 'Çakışma' : '',
  ]);
  const written = csvBlob(
    ['Ad', 'Oran', 'Bölge', 'Posta kodu', 'Vergi kategorisi', 'Geçerlilik (yerel)', 'Durum'],
    rows, 'vergi-oranlari');
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll(kind) {
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { kind, ...vatParams() },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

function mountMapping() {
  nodes.mapBar = tabTrack(filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ürün adı ara', width: '260px' },
      // Başlangıçta GİZLİ: kategori listesi ilk `mapping` yanıtıyla geliyor.
      { kind: 'select', key: 'taxCategory', label: 'Vergi kategorisi',
        hidden: true, options: [] },
    ],
    onChange: () => loadMapping({ page: 1 }),
    actions: [button('Yenile', { onClick: () => loadMapping() })],
  }));
  nodes.mapSelbar = h('div', 'tx-selbar');
  nodes.mapTableWrap = h('div', 'tx-table');
  nodes.mapPager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => loadMapping({ page, size }),
  });
  nodes.body.replaceChildren(nodes.mapBar.node, nodes.status.node, nodes.mapSelbar,
    nodes.mapTableWrap, nodes.mapPager.node,
    hintBox('Ürünün vergi kategorisini toplu yazan bir uç mağazada YOK: ürünler tek tek ve '
      + 'sırayla yazılır. Bu yüzden seçim sınırı küçüktür ve önizleme zorunludur.'));
  loadMapping({ page: 1 });
}

function mountVat() {
  nodes.vatBar = tabTrack(filterBar({
    fields: [
      { kind: 'dateRange', key: 'range', label: 'Dönem',
        start: todayIso(-29), end: todayIso() },
      // Başlangıçta GİZLİ: kanal adları icmal yanıtından toplanıyor.
      { kind: 'select', key: 'channel', label: 'Kanal', hidden: true, options: [] },
    ],
    onChange: () => loadVat(),
    actions: [
      button('Yenile', { onClick: () => loadVat() }),
      button('⤓ CSV', { title: 'İcmali rapor klasörüne CSV olarak yaz',
        onClick: () => exportAll('vat') }),
      button('KDV icmali PDF', {
        variant: 'primary',
        title: 'Üretir, önizler ve yazıcıya gönderir',
        onClick: () => report.run('vat', vatParams()),
      }),
    ],
  }));
  nodes.vatResult = h('div', 'tx-stack');
  nodes.body.replaceChildren(nodes.vatBar.node, nodes.status.node, nodes.vatResult);
  loadVat();
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel tx');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  nodes.tabDisposers = [];
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar(TABS, 'rates', (key) => showTab(key));
  nodes.status = statusLine();
  nodes.body = h('div', 'tx-body');
  view.append(tabs.node, nodes.body);

  root.replaceChildren(view);
  // `accountant` rolü panele girince KDV icmaline düşer: onun ana ekranı orası.
  // Rolü sormuyoruz (K10) — oranları yazma yetkisi yoksa ekran zaten okuma
  // sekmesinde işe yarar; varsayılan sekme herkes için oranlardır.
  showTab('rates');

  return () => {
    dropTabResources();
    disposers.forEach((drop) => { try { drop(); } catch { /* kapanışta hata yutulur */ } });
    disposers.length = 0;
    root.replaceChildren();
    state = empty();
    busy = false;
  };
}
