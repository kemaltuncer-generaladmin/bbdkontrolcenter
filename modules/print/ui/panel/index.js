// Çıktı Merkezi paneli — üretilen her raporun tek listesi (ADR 0019).
//
// NE YAPAR: kayıtları süzer (tarih, tür, üreten, modül, arama), dosyanın
// KENDİSİNİ önizler, yeniden bastırır ve klasörünü açar.
//
// NE YAPMAZ:
//  · RAPOR ÜRETMEZ. Üretim raporu doğuran ekranın işidir; burada üretmek aynı
//    adı taşıyan ama içeriği farklı ikinci bir dosya doğururdu — ADR 0019'un
//    çıkış noktası tam olarak buydu ("yeniden basılamıyor, yeniden üretiliyor").
//  · KAYIT SİLMEZ. Dosya kaybolduğunda satır GİZLENMEZ: durumu "dosya
//    bulunamadı" olur, yeniden bas düğmesi kapanır ve nedeni yazılır. Kaydı
//    sessizce gizlemek, "ben bu raporu almıştım" diyen kullanıcıyı izsiz
//    bırakırdı.
//  · Kullanıcının dosyasını SİLMEZ. Saklama süresi (varsayılan 30 gün) yalnız
//    KAYIT satırlarını budar; masaüstündeki klasör kullanıcının alanıdır.
//
// TUZAK — "BASILDI" DEĞİL "DENENDİ" (ADR 0014). Windows/macOS'ta yeniden basma
// kâğıt çıkarmaz, sistem yazdırma penceresini açar; kullanıcı iptal edebilir ve
// uygulama bunu bilemez. Bu yüzden sayacın başlığı "Deneme" ve satır altındaki
// cümle pencereyi anlatır. Linux'ta sessizce basılır ve aynı sayaç artar.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (ADR 0011). Import yolu KOPYALANMIŞ
// konuma göredir: shell/panels/print/ → shell/ui-kit/. Bu dosyanın KAYNAĞI
// modules/print/ui/panel/ altındadır; orada '../../ui-kit/' dosya sisteminde
// ÇÖZÜLMEZ — normaldir.

import {
  blockedButton, bytes as sizeText, button, h, loadStyles, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { alertBox, badge, emptyState, hintBox, skeletonRows, statusLine } from '../../ui-kit/layout.js';

const BASE = '/api/print';

const EMPTY = {
  items: [], total: 0, page: 1, size: 50,
  facets: { sources: [], kinds: [], users: [] },
  canReprint: false, printerAvailable: false, keepDays: 30, maxCopies: 20, missing: 0,
};

let api = null;
let toast = null;
let state = { ...EMPTY };
let loaded = false;
let busy = false;

const nodes = {};

// ------------------------------------------------------------------ araçlar

/** Sunucu `{ok:false, error}` de dönebilir; iki hata biçimi tek yerde okunur. */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) throw new Error(result.error);
  return result;
}

function query() {
  const values = nodes.filters?.values() || {};
  const range = values.created || {};
  const parts = [
    ['search', values.search || ''],
    ['start', range.start || ''],
    ['end', range.end || ''],
    ['kind', values.kind || ''],
    ['source', values.source || ''],
    ['user', values.user || ''],
    ['page', String(state.page)],
    ['size', String(state.size)],
  ].filter(([, value]) => value !== '');
  return new URLSearchParams(parts).toString();
}

function statusText() {
  if (!loaded) return 'Yükleniyor…';
  const parts = [`${num(state.total)} kayıt`];
  if (state.missing) parts.push(`${num(state.missing)} dosya bulunamadı`);
  parts.push(`kayıtlar ${num(state.keepDays)} gün saklanır (dosyalar silinmez)`);
  return parts.join(' · ');
}

// -------------------------------------------------------------------- veri

async function refresh() {
  nodes.status.set('Kayıtlar okunuyor…');
  try {
    const result = await call(`${BASE}/outputs?${query()}`);
    state = { ...EMPTY, ...result };
    loaded = true;
    fillFacets();
    nodes.status.set(statusText(), Boolean(state.missing));
  } catch (error) {
    state = { ...EMPTY };
    loaded = true;
    nodes.status.set(error.message || 'Kayıtlar okunamadı.', true);
  }
  renderTable();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  renderBanner();
}

/**
 * Süzgeç seçenekleri VERİDEN gelir ve tek seçenekli kutu çizilmez (ADR 0011,
 * `choice.js` ile aynı kural): tek modül çıktı üretiyorsa "modül" süzgeci
 * seçilecek bir şey sormaz. Kullanıcı kimliği hiç kaydedilmemişse "üreten"
 * kutusu da hiç görünmez.
 */
function fillFacets() {
  const fill = (key, list, allLabel) => {
    const options = [{ value: '', label: allLabel }, ...list.map((item) => ({
      value: item.value, label: `${item.label} (${num(item.count)})`,
    }))];
    nodes.filters.options(key, options);
    nodes.filters.visible(key, list.length > 1);
  };
  fill('kind', state.facets.kinds || [], 'Tür: tümü');
  fill('source', state.facets.sources || [], 'Modül: tümü');
  fill('user', (state.facets.users || []).filter((item) => item.value), 'Üreten: tümü');
}

// ------------------------------------------------------------------- çizim

function renderBanner() {
  nodes.banner.replaceChildren();
  if (!state.printerAvailable) {
    nodes.banner.append(alertBox(
      'Yazıcı yeteneği bu kurulumda yok; liste ve önizleme çalışır, yeniden basma kapalıdır.',
      'warn'));
  }
  if (!state.canReprint) {
    nodes.banner.append(alertBox(
      'Yeniden basma yetkiniz yok (print.reprint). Kayıtları görebilir ve önizleyebilirsiniz.',
      'info'));
  }
}

function reprintButton(row) {
  if (!state.canReprint) {
    return blockedButton('Yeniden bas', 'Bu işlem için print.reprint izni gerekiyor.');
  }
  if (!state.printerAvailable) {
    return blockedButton('Yeniden bas', 'Yazıcı yeteneği bu kurulumda yok.');
  }
  if (!row.exists) return blockedButton('Yeniden bas', row.missingReason);
  if (!row.printable) {
    return blockedButton('Yeniden bas',
      `“${(row.kind || '').toUpperCase()}” dosyası yazıcıya gönderilmez; yalnız PDF basılır.`);
  }
  return button('Yeniden bas', { variant: 'primary', onClick: () => reprint(row) });
}

function previewButton(row) {
  if (!row.exists) return blockedButton('Önizle', row.missingReason);
  if (!row.previewable) {
    return blockedButton('Önizle', 'Bu tür önizlenemez; klasöründen açabilirsiniz.');
  }
  return button('Önizle', { onClick: () => preview(row) });
}

function actionCell(row) {
  const cell = h('div', 'ck-actions');
  cell.append(
    previewButton(row),
    reprintButton(row),
    button('Klasör', { variant: 'ghost', title: row.folder, onClick: () => openFolder(row) }),
  );
  return cell;
}

function stateCell(row) {
  if (row.exists) {
    const printed = Number(row.printedCount || 0);
    if (!printed) return badge('hazır', 'good');
    // Renk tek başına anlam taşımaz (kit kuralı 7): rozetin yanında sayı var.
    return badge(`${num(printed)} deneme`, 'good');
  }
  const cell = h('div', 'ck-missing');
  cell.append(badge('dosya bulunamadı', 'bad'), h('span', 'ck-why', row.missingReason));
  return cell;
}

function renderTable() {
  nodes.table.update({
    rows: state.items,
    empty: emptyState({
      title: loaded ? 'Kayıt yok' : 'Yükleniyor',
      text: loaded
        ? 'Bu süzgeçlerle çıktı bulunamadı. Rapor üreten ekranlardan bir çıktı alındığında '
          + 'kaydı burada kendiliğinden görünür.'
        : '',
    }),
  });
}

// ------------------------------------------------------------------ eylem

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.status.set(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    nodes.status.set(error.message || 'İşlem başarısız.', true);
    return null;
  } finally {
    busy = false;
  }
}

async function reprint(row) {
  const result = await withBusy(`${row.title} yazıcıya gönderiliyor…`, () => call(
    `${BASE}/reprint`, { method: 'POST', body: { id: row.id, copies: 1 } }));
  if (!result) return;

  // ADR 0014: `system` kipinde kâğıt ÇIKMADI, pencere açıldı.
  const message = result.mode === 'system'
    ? 'Sistem yazdırma penceresi açıldı — yazıcıyı ve kâğıdı orada seçin. '
      + 'Sayaç "denendi" sayar.'
    : `${result.printer || 'Yazıcı'} kuyruğuna gönderildi.`;
  toast(message, 'good');
  await refresh();
}

async function openFolder(row) {
  const result = await withBusy('Klasör açılıyor…', () => call(
    `${BASE}/folder`, { method: 'POST', body: { id: row.id } }));
  if (result) toast(`Klasör açıldı: ${result.folder}`, 'good');
  nodes.status.set(statusText(), Boolean(state.missing));
}

async function preview(row) {
  const result = await withBusy(`${row.title} önizleniyor…`, () => call(
    `${BASE}/preview`, { method: 'POST', body: { id: row.id } }));
  nodes.status.set(statusText(), Boolean(state.missing));
  if (!result) return;
  openPreview(row, result);
}

/**
 * Önizleme katmanı. Overlay PANEL KÖKÜNE eklenir, `document.body`'ye değil:
 * panel değişince kabuk kökü temizliyor, body'deki katman asılı kalırdı.
 */
function openPreview(row, result) {
  const overlay = h('div', 'kit-overlay ck-overlay');
  const frame = h('div', 'kit-preview-frame');
  frame.setAttribute('role', 'dialog');
  frame.setAttribute('aria-modal', 'true');
  frame.setAttribute('aria-label', `${row.title} önizlemesi`);

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  const head = h('div', 'kit-preview-head');
  head.append(
    h('span', 'kit-preview-name', row.title),
    h('span', 'kit-spacer'),
    h('span', 'ck-note', `${row.source} · ${stampIso(row.createdAt)}`),
    button('Kapat', { variant: 'ghost', onClick: close }),
  );

  const body = h('div', 'kit-preview-body');
  if (result.mode === 'pages') {
    for (const page of result.pages || []) {
      const image = h('img', 'kit-preview-page');
      image.src = page;
      image.alt = '';
      body.append(image);
    }
  } else if (result.mode === 'text') {
    const pre = h('pre', 'ck-text');
    pre.textContent = result.text || '';
    body.append(pre);
    if (result.truncated) {
      body.append(hintBox('Dosyanın yalnız başı gösteriliyor; tamamı için klasörden açın.'));
    }
  }

  const foot = h('div', 'kit-preview-foot');
  foot.append(h('span', undefined, 'Dosya: '), h('code', 'kit-preview-path', row.path));

  frame.append(head, body, foot);
  overlay.append(frame);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  nodes.root.append(overlay);
}

// ------------------------------------------------------------------- mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);
  api = ctx.api;

  const view = h('div', 'kit-panel ck');
  nodes.root = view;
  toast = toaster(view);

  const bar = h('div', 'ck-bar');
  bar.append(
    h('span', 'ck-brand', 'Çıktı Merkezi'),
    h('span', 'ck-brand-note', 'üretilen her rapor, dışa aktarım ve yedek — tek listede'),
  );

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'search', placeholder: 'Dosya adı ya da yol', width: '260px' },
      // Boş aralık bilerek: varsayılan "son 7 gün" olsaydı eski çıktılar ilk
      // açılışta gizlenir ve "raporum kayboldu" sanılırdı.
      { kind: 'dateRange', key: 'created', label: 'Tarih', start: '', end: '' },
      { kind: 'select', key: 'kind', options: [{ value: '', label: 'Tür: tümü' }], hidden: true },
      { kind: 'select', key: 'source', options: [{ value: '', label: 'Modül: tümü' }], hidden: true },
      { kind: 'select', key: 'user', options: [{ value: '', label: 'Üreten: tümü' }], hidden: true },
    ],
    onChange: () => { state.page = 1; refresh(); },
    actions: [button('Yenile', { onClick: () => refresh() })],
  });

  nodes.banner = h('div', 'ck-banner');
  nodes.status = statusLine();
  nodes.table = dataTable({
    dense: true,
    columns: [
      { key: 'createdAt', label: 'Tarih', width: '150px', cell: (row) => stampIso(row.createdAt) },
      { key: 'title', label: 'Ad', width: 'minmax(0, 2fr)' },
      { key: 'kind', label: 'Tür', width: '80px', cell: (row) => (row.kind || '').toUpperCase() },
      { key: 'source', label: 'Modül', width: 'minmax(0, 1fr)' },
      { key: 'user', label: 'Üreten', width: 'minmax(0, 1fr)', cell: (row) => row.user || 'bilinmiyor' },
      { key: 'bytes', label: 'Boyut', width: '90px', align: 'num', cell: (row) => sizeText(row.bytes) },
      { key: 'pages', label: 'Sayfa', width: '70px', align: 'num' },
      { key: 'state', label: 'Durum', width: 'minmax(0, 1fr)', cell: stateCell },
      { key: 'actions', label: '', width: '260px', cell: actionCell },
    ],
    rows: [],
    empty: skeletonRows(6, 9),
  });
  nodes.pager = pager({
    total: 0,
    page: 1,
    size: state.size,
    onChange: ({ page, size }) => { state.page = page; state.size = size; refresh(); },
  });

  // Şerit ve süzgeç sabit durur; kaydırılan yalnız gövdedir (`kit-body`).
  const body = h('div', 'kit-body');
  body.append(nodes.banner, nodes.status.node, nodes.table.node, nodes.pager.node);
  view.append(bar, nodes.filters.node, body);
  root.replaceChildren(view);
  refresh();

  return () => {
    nodes.filters?.destroy();       // tarih alanları global dinleyici tutuyor
    root.replaceChildren();
    state = { ...EMPTY };
    loaded = false;
    busy = false;
  };
}
