// Ana Ekran Görselleri paneli — vitrinin ilk ekranını buradan kurarsınız.
//
// NE YAPAR: üstte ana sayfanın ÖLÇEKLİ TEMSİLİ (duyuru şeridi · slider ·
// bannerlar · koleksiyonlar), altında dört sekme ve her sekmede o şeridin slot
// listesi. Sıra sürükle-bırak İLE VE `Ctrl+↑/↓` ile değişir. Slot düzenleyici
// çekmecede açılır; görsel dosyadan seçilir ya da üstüne bırakılır, tarayıcıda
// base64'e çevrilip gövdeyle gider ve ölçüsü sunucuda ölçülür.
//
// NE YAPMAZ:
//  · Slot SİLMEZ. Yayından kaldırılan slot listede kalır; tıklama geçmişi ve
//    "geçen kampanyada ne asmıştık" bilgisi silinmez (ADR 0012).
//  · Yayına ALMAZ — o ayrı izindir (`store_home_media.publish`) ve ayrı
//    düğmedir. Düzenleyicinin "Kaydet"i slotu vitrine düşürmez.
//  · Görseli KIRPMAZ/BÜYÜTMEZ. Bulanık görseli sessizce büyütmek onu daha da
//    bozardı; ekran ölçüyü ve hangi kenardan ne kadar kırpılacağını söyler,
//    kararı kullanıcı verir ve karar denetim kaydına yazılır.
//  · Süzgeç açıkken sıra değiştirtmez: sıra tüm şeridi ilgilendirir, süzülmüş
//    bir alt kümeyi sıralamak kalan slotları rastgele yerlere atardı.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Mağazanın sıra ucu GLOBAL liste ister; panel yalnız şeridin sırasını
//    gönderir, sunucu onu global sıraya oturtur.
//  · Tauri'de dosya sistemi/dialog eklentisi YOK. Bu yüzden gizli
//    `<input type=file>` + `FileReader` + base64 gövde kullanılır.
//  · `<input type="date">` WebKitGTK'da bozuk; tarih alanları kitin
//    `dateField`'ından gelir (formGrid `type: 'date'`).
//  · Alt metni ZORUNLU: ekran okuyucu ve arama motoru banner'ı yalnız ondan
//    okur. Boşsa sunucu da reddeder (K9).
//  · Görsel önizlemesini SABİT bir çerçeveye sığdırmak, uyarmaya çalıştığımız
//    kırpmayı gizler. Bu yüzden iki kare çizilir: vitrindeki (kırpılmış) hâli
//    ve dosyanın gerçek oranı. Ölçü sunucudan gelir, tarayıcıdan değil.
//  · Yükleme ucu (`POST /image/upload`) mağaza tarafında HENÜZ YOK. Geçit
//    "uç henüz yayında değil" derse ekran bunu hata değil "hazır, bekliyor"
//    diye anlatır; görsel o zamana kadar slot kaydıyla birlikte gider (K7).
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_home_media/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_home_media/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  bytes as formatBytes, button, clip, confirmSimple, confirmWithReason, csvBlob, debounce, h,
  loadStyles, num, toaster,
} from '../../ui-kit/kit.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_home_media';

const AREAS = [
  { key: 'slider', label: 'Slider' },
  { key: 'banner', label: 'Banner alanları' },
  { key: 'collection', label: 'Öne çıkan koleksiyonlar' },
  { key: 'announcement', label: 'Duyuru şeridi' },
];

const STATE_TONES = { published: 'good', scheduled: 'info', expired: 'warn', draft: 'dim' };
const STATE_LABELS = {
  published: 'Yayında', scheduled: 'Zamanlanmış', expired: 'Süresi doldu', draft: 'Taslak',
};
const SIZE_TONES = { ok: 'good', blurry: 'bad', ratio: 'warn', unknown: 'warn', none: 'dim' };

const LINK_KINDS = [
  { value: 'url', label: 'Serbest bağlantı' },
  { value: 'product', label: 'Ürün' },
  { value: 'category', label: 'Kategori' },
  { value: 'cms', label: 'CMS sayfası' },
];

const EMPTY_STATE = {
  items: [], preview: { slider: [], banner: [], collection: [], announcement: [] },
  summary: {}, counts: {}, connected: false, error: '', readOnly: false, source: '',
  recommended: {}, maxImageBytes: 2000000, allowedTypes: ['image/png', 'image/jpeg'],
  notice: '', reference: { channels: [], locales: [], categories: [], pages: [] },
  area: 'slider', order: [], baseOrder: [], device: 'desktop', loaded: false,
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];          // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

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

/** Yıkıcı ve vitrini etkileyen her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

/** Ekran okuyucuya "3. sıraya taşındı" gibi anlık bilgi verir. */
function announce(message) {
  if (nodes.live) nodes.live.textContent = message;
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const stats = state.summary || {};
  const parts = [`Bağlı · ${num(stats.total || 0)} slot`,
    `${num(stats.published || 0)} yayında`];
  if (stats.missingAlt) parts.push(`${num(stats.missingAlt)} slotun alt metni yok`);
  if (state.source === 'themes') parts.push('salt okunur (tema kayıtları)');
  return parts.join(' · ');
}

// -------------------------------------------------------------------- veri

function currentFilters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  return {
    q: values.q || '',
    status: values.status || '',
    device: values.device || '',
    placement: values.placement || '',
    channel: values.channel || '',
    start: range.start || '',
    end: range.end || '',
  };
}

/** Süzgeç açıkken sıra değiştirilemez — sıra TÜM şeridi ilgilendirir. */
function orderEditable() {
  if (state.readOnly || !state.connected) return false;
  return Object.values(currentFilters()).every((value) => !value);
}

function queryString() {
  const params = new URLSearchParams();
  params.set('area', state.area);
  for (const [key, value] of Object.entries(currentFilters())) {
    if (value) params.set(key, String(value));
  }
  return params.toString();
}

async function refresh() {
  nodes.listWrap?.replaceChildren(skeletonRows(5, 4));
  nodes.status?.set('Ana sayfa okunuyor…');
  let payload;
  try {
    payload = await api(`${BASE}/slots?${queryString()}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, items: [] };
    renderAll();
    nodes.status?.set(statusText(), true);
    return;
  }
  const order = (payload.preview?.[state.area] || []).map((row) => row.id);
  state = {
    ...state,
    items: payload.items || [],
    preview: payload.preview || EMPTY_STATE.preview,
    summary: payload.summary || {},
    counts: payload.counts || {},
    connected: Boolean(payload.connected),
    error: payload.error || '',
    readOnly: Boolean(payload.readOnly),
    source: payload.source || '',
    recommended: payload.recommended || {},
    maxImageBytes: payload.maxImageBytes || EMPTY_STATE.maxImageBytes,
    allowedTypes: payload.allowedTypes || EMPTY_STATE.allowedTypes,
    notice: payload.notice || '',
    order,
    baseOrder: order,
    loaded: true,
  };
  renderAll();
  nodes.status?.set(statusText(), !state.connected);
}

async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    return;              // referans gelmezse süzgeçler boş kalır ama ekran çalışır (K7)
  }
  state.reference = {
    channels: payload.channels || [],
    locales: payload.locales || [],
    categories: payload.categories || [],
    pages: payload.pages || [],
  };
  nodes.filters.options('channel', [
    { value: '', label: 'Tümü — kanal' },
    ...state.reference.channels.map((item) => ({ value: item.code, label: item.name })),
  ]);
  if (payload.warnings?.length) {
    toast(`Bazı referans listeler gelmedi: ${payload.warnings.join(' · ')}`, 'warn');
  }
}

// ================================================================ önizleme

/**
 * Ana sayfanın ölçekli temsili.
 *
 * GERÇEK SAYFA DEĞİLDİR ve öyle olduğunu iddia etmez: `iframe` ile canlı
 * sayfayı çekmek CSP'de `frame-src` olmadığı için WebKitGTK'da öngörülemez
 * davranıyor. Bunun yerine şeritler önerilen ORANLARINDA çizilir — hangi
 * görselin nereye düştüğü ve neyin eksik olduğu buradan görünür.
 *
 * Yalnız YAYINDA olan slotlar çizilir: müşterinin gördüğü budur.
 */
function renderPreview() {
  const host = nodes.preview;
  if (!host) return;
  host.replaceChildren();

  const head = h('div', 'hm-preview-head');
  head.append(h('b', undefined, 'Ana sayfa önizlemesi'));
  const toggle = h('div', 'hm-device');
  for (const [key, label] of [['desktop', 'Masaüstü'], ['mobile', 'Mobil']]) {
    const item = button(label, {
      variant: state.device === key ? 'primary' : 'ghost',
      onClick: () => { state.device = key; renderPreview(); },
    });
    item.setAttribute('aria-pressed', state.device === key ? 'true' : 'false');
    toggle.append(item);
  }
  head.append(h('span', 'kit-spacer'), h('span', 'hm-sub',
    state.device === 'desktop' ? 'Ölçek ~%50 · gerçek genişlik 1920 piksel'
      : 'Ölçek ~%95 · gerçek genişlik 390 piksel'), toggle);
  host.append(head);

  if (!state.connected) {
    host.append(alertBox(`Mağazaya ulaşılamadı — ${state.error}`, 'bad'));
    return;
  }

  const stage = h('div', `hm-stage ${state.device}`);
  let drawn = 0;
  let hidden = 0;

  const visible = (row) => {
    if (row.state !== 'published') return false;
    if (state.device === 'mobile' && row.device === 'desktop') return false;
    if (state.device === 'desktop' && row.device === 'mobile') return false;
    return true;
  };

  for (const area of AREAS) {
    const rows = (state.preview[area.key] || []);
    const live = rows.filter(visible);
    hidden += rows.length - live.length;
    drawn += live.length;
    stage.append(previewBand(area, live));
  }

  host.append(stage);
  const note = h('div', 'hm-sub');
  note.textContent = hidden
    ? `${num(drawn)} slot çizildi · ${num(hidden)} slot bu görünümde yayında değil `
      + '(taslak, zamanlanmış, süresi dolmuş ya da başka cihaz).'
    : `${num(drawn)} slot çizildi.`;
  host.append(note);
}

function previewBand(area, rows) {
  const band = h('div', `hm-band hm-band-${area.key}`);
  band.setAttribute('aria-label', area.label);

  if (!rows.length) {
    const empty = h('div', 'hm-band-empty');
    empty.textContent = `${area.label}: boş`;
    band.append(empty);
    return band;
  }

  if (area.key === 'announcement') {
    // Duyuru şeridi metindir; görsel kutusu çizmek yanıltıcı olurdu.
    band.append(h('div', 'hm-strip', rows.map((row) => row.title).join('  ·  ')));
    return band;
  }

  const limit = area.key === 'slider' ? 1 : (area.key === 'banner' ? 3 : 4);
  for (const row of rows.slice(0, limit)) {
    band.append(previewTile(row, area));
  }
  if (rows.length > limit) {
    const more = h('div', 'hm-tile hm-tile-more');
    more.textContent = `+${num(rows.length - limit)}`;
    more.title = area.key === 'slider'
      ? 'Slider döner; ilk görsel çizildi.' : 'Şeride sığmayan slotlar.';
    band.append(more);
  }
  return band;
}

function previewTile(row, area) {
  // Tıklanabilir: önizlemede gördüğünüz kutu, düzenleyicisini açar. Slotu
  // listeden aramak zorunda kalmamak bu ekranın bütün amacı.
  const tile = h('button', 'hm-tile');
  tile.type = 'button';
  tile.title = `${row.title} — düzenlemek için tıklayın`;
  tile.setAttribute('aria-label', `${area.label}: ${row.title}`);
  tile.addEventListener('click', () => openEditor(row));

  if (row.imageUrl) {
    const image = h('img');
    image.loading = 'lazy';
    image.src = row.imageUrl;
    image.alt = row.altText || '';
    image.addEventListener('error', () => {
      tile.classList.add('broken');
      tile.replaceChildren(h('span', 'hm-tile-text', 'görsel açılmıyor'));
    });
    tile.append(image);
  } else {
    tile.classList.add('broken');
    tile.append(h('span', 'hm-tile-text', 'görsel yok'));
  }
  if (row.sizeState === 'blurry' || row.sizeState === 'ratio') {
    tile.append(h('span', 'hm-tile-flag', row.sizeState === 'blurry' ? 'bulanık' : 'oran'));
  }
  return tile;
}

// ==================================================================== liste

function areaRows() {
  const all = state.preview[state.area] || [];
  if (!orderEditable()) return state.items.filter((row) => row.area === state.area);
  const byId = new Map(all.map((row) => [row.id, row]));
  return state.order.map((id) => byId.get(id)).filter(Boolean);
}

function renderKpi() {
  if (!nodes.kpi) return;
  const stats = state.summary || {};
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Slot', value: num(stats.total || 0) },
    { label: 'Yayında', value: num(stats.published || 0), tone: 'good' },
    { label: 'Zamanlanmış', value: num(stats.scheduled || 0), tone: 'info' },
    { label: 'Süresi doldu', value: num(stats.expired || 0), tone: 'warn' },
    { label: 'Taslak', value: num(stats.draft || 0), tone: 'muted' },
    { label: 'Alt metni yok', value: num(stats.missingAlt || 0), tone: 'bad' },
    { label: 'Ölçüsü uygunsuz', value: num(stats.lowRes || 0), tone: 'warn' },
    // "0 tıklama" ile "tıklama ölçülmüyor" aynı şey değil: uç bu alanı
    // taşımıyorsa sıfır yazıp ölçüm varmış gibi göstermeyiz.
    { label: 'Tıklama', value: stats.clicksKnown ? num(stats.clicks || 0) : '—',
      title: stats.clicksKnown ? '' : 'Mağaza tıklama sayısı döndürmüyor.' },
  ]));
}

function renderList() {
  const host = nodes.listWrap;
  if (!host) return;
  host.replaceChildren();

  if (state.readOnly) {
    host.append(alertBox(
      'Bu görünüm mağazanın TEMA KAYITLARINDAN salt okunur geldi: BBD slot ucu henüz '
      + 'yayında değil. Ana sayfada ne olduğu görünür, düzenleme uç yayınlanınca açılacak.',
      'warn',
    ));
  }
  if (!orderEditable() && state.connected && !state.readOnly) {
    host.append(alertBox(
      'Süzgeç açıkken sıra değiştirilemez: sıra tüm şeridi ilgilendirir, süzülmüş bir alt '
      + 'kümeyi sıralamak kalan slotları rastgele yerlere atardı.', 'info',
    ));
  }

  const rows = areaRows();
  if (!rows.length) {
    host.append(emptyNode());
    renderOrderBar();
    return;
  }

  const list = h('div', 'hm-list');
  list.setAttribute('role', 'list');
  rows.forEach((row, index) => list.append(slotRow(row, index, rows.length)));
  host.append(list);
  renderOrderBar();
}

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (!orderEditable()) {
    return emptyState({
      title: 'Bu süzgeçte slot yok',
      text: 'Şeritte slot var ama süzgece uyan yok. Süzgeçleri gevşetin.',
      actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
    });
  }
  const label = AREAS.find((item) => item.key === state.area)?.label || '';
  return emptyState({
    title: `${label} boş`,
    text: state.area === 'slider'
      ? 'Slider’da hiç görsel yok — site ana sayfası boş görünüyor.'
      : `${label} şeridinde hiç slot yok; müşteri bu bölümü hiç görmüyor.`,
    actions: [button(state.area === 'announcement' ? 'Duyuru ekle' : 'Görsel ekle',
      { variant: 'primary', onClick: () => openEditor(null) })],
  });
}

/** Tek slot satırı: sürükle-bırak + `Ctrl+↑/↓` ile taşınır. */
function slotRow(row, index, total) {
  const item = h('div', `hm-slot${row.state === 'draft' ? ' passive' : ''}`);
  item.setAttribute('role', 'listitem');
  item.tabIndex = 0;
  item.dataset.id = String(row.id);
  const movable = orderEditable();
  item.draggable = movable;
  item.setAttribute('aria-label',
    `${index + 1}. ${row.title} — ${STATE_LABELS[row.state] || row.state}`
    + (movable ? '. Taşımak için Ctrl ve yukarı/aşağı ok.' : ''));

  const handle = h('span', 'hm-handle', movable ? '⋮⋮' : '·');
  handle.title = movable
    ? 'Sürükleyerek ya da Ctrl+↑/↓ ile taşıyın'
    : 'Sıra değiştirmek için süzgeci temizleyin';
  handle.setAttribute('aria-hidden', 'true');

  const thumb = h('span', 'hm-thumb');
  if (row.imageUrl) {
    const image = h('img');
    image.loading = 'lazy';
    image.src = row.imageUrl;
    image.alt = '';
    image.addEventListener('error', () => {
      thumb.classList.add('none');
      thumb.replaceChildren(document.createTextNode('!'));
      thumb.title = 'Görsel bağlantısı açılmıyor';
    });
    thumb.append(image);
  } else {
    thumb.classList.add('none');
    thumb.textContent = row.area === 'announcement' ? 'T' : '—';
    thumb.title = row.area === 'announcement' ? 'Duyuru metni' : 'Görsel yok';
  }

  const main = h('div', 'hm-slot-main');
  main.append(clip(h('b'), row.title || '(başlıksız)', 52));
  const meta = h('div', 'hm-sub');
  meta.textContent = [
    row.link || 'hedef yok',
    row.placementLabel,
    row.deviceLabel,
    row.startsAt || row.endsAt ? `${row.startsAt || '…'} → ${row.endsAt || '…'}` : 'süresiz',
  ].join(' · ');
  main.append(meta);
  if (row.issues?.length) {
    main.append(h('div', 'hm-issues', `Eksik: ${row.issues.join(', ')}`));
  }

  const right = h('div', 'hm-slot-right');
  // Renk tek başına anlam taşımaz: rozetin içinde yazı, yanında sayı var.
  right.append(badge(STATE_LABELS[row.state] || row.state, STATE_TONES[row.state] || ''));
  right.append(h('span', 'hm-sub', row.clicksKnown
    ? `${num(row.clicks || 0)} tıklama` : 'tıklama ölçülmüyor'));

  const tools = h('div', 'hm-slot-tools');
  tools.append(
    button('Düzenle', { onClick: () => openEditor(row) }),
    row.status
      ? button('Yayından kaldır', { variant: 'danger', onClick: () => togglePublish(row, false) })
      : button('Yayına al', { variant: 'primary', onClick: () => togglePublish(row, true) }),
  );
  if (movable) {
    tools.append(
      button('↑', { variant: 'ghost', title: 'Yukarı taşı (Ctrl+↑)',
        onClick: () => moveSlot(index, -1) }),
      button('↓', { variant: 'ghost', title: 'Aşağı taşı (Ctrl+↓)',
        onClick: () => moveSlot(index, 1) }),
    );
  }

  item.append(handle, thumb, main, right, tools);

  // KLAVYE: sürükle-bırak tek yol OLAMAZ. Ctrl+ok ile taşıma, fare
  // kullanamayan personel için tek erişim yolu (ve en hızlısı).
  item.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target === item) { openEditor(row); return; }
    if (!movable || !event.ctrlKey) return;
    const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
    if (!step) return;
    event.preventDefault();
    if (moveSlot(index, step)) {
      announce(`${row.title}, ${index + 1 + step}. sıraya taşındı (${total} slot içinde).`);
      focusSlot(row.id);
    }
  });

  if (movable) {
    item.addEventListener('dragstart', (event) => {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(row.id));
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => item.classList.remove('dragging'));
    item.addEventListener('dragover', (event) => {
      event.preventDefault();
      item.classList.add('over');
    });
    item.addEventListener('dragleave', () => item.classList.remove('over'));
    item.addEventListener('drop', (event) => {
      event.preventDefault();
      item.classList.remove('over');
      const dragged = Number(event.dataTransfer.getData('text/plain'));
      dropSlot(dragged, row.id);
    });
  }
  return item;
}

function focusSlot(id) {
  const target = nodes.listWrap?.querySelector(`.hm-slot[data-id="${id}"]`);
  target?.focus();
}

/** Taşıma kuralı tek yerde: klavye de sürükleme de bunu çağırır. */
function moveSlot(index, step) {
  const order = [...state.order];
  const target = index + step;
  if (target < 0 || target >= order.length) return false;
  [order[index], order[target]] = [order[target], order[index]];
  state.order = order;
  renderList();
  return true;
}

function dropSlot(draggedId, targetId) {
  if (draggedId === targetId) return;
  const order = state.order.filter((id) => id !== draggedId);
  const at = order.indexOf(targetId);
  if (at < 0) return;
  order.splice(at, 0, draggedId);
  state.order = order;
  renderList();
  announce('Slot taşındı. Kaydetmek için “Sırayı kaydet”.');
}

function orderDirty() {
  return state.order.join(',') !== state.baseOrder.join(',');
}

function renderOrderBar() {
  const bar = nodes.orderbar;
  if (!bar) return;
  bar.replaceChildren();
  if (!orderDirty()) {
    bar.classList.remove('on');
    return;
  }
  bar.classList.add('on');
  bar.append(h('b', undefined, 'Sıra değişti — henüz kaydedilmedi.'));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Sırayı kaydet', { variant: 'primary', onClick: saveOrder }),
    button('Geri al', {
      variant: 'ghost',
      onClick: () => { state.order = [...state.baseOrder]; renderList(); },
    }),
  );
}

async function saveOrder() {
  const label = AREAS.find((item) => item.key === state.area)?.label || '';
  const reason = await askReason({
    title: 'Şerit sırasını kaydet',
    description: `${label} şeridinin sırası değişecek. Listenin ilk slotu vitrinde en başta `
      + 'görünür; sıra yalnız bu şeridi etkiler.',
    confirmLabel: 'Sırayı kaydet',
  });
  if (!reason) return;
  await withBusy('Sıra yazılıyor…', async () => {
    const result = await call(`${BASE}/reorder`, {
      method: 'POST',
      body: { area: state.area, order: state.order, reason, dryRun: false },
    });
    toast('Sıra kaydedildi.', 'good');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

async function togglePublish(row, published) {
  const reason = await askReason({
    title: published ? 'Slotu yayına al' : 'Slotu yayından kaldır',
    description: published
      ? `“${row.title}” ana sayfada görünmeye başlayacak. Yayın aralığı tanımlıysa slot ancak `
        + 'o aralıkta çizilir.'
      : `“${row.title}” ana sayfadan kalkacak. SİLİNMEZ: listede kalır, tıklama geçmişi ve `
        + 'görseli durur; istediğinizde geri açarsınız.',
    confirmLabel: published ? 'Yayına al' : 'Yayından kaldır',
  });
  if (!reason) return;
  await withBusy('Yayın durumu yazılıyor…', async () => {
    const result = await call(`${BASE}/slots/${row.id}/status`, {
      method: 'POST', body: { published, reason, dryRun: false },
    });
    toast(published ? 'Slot yayına alındı.' : 'Slot yayından kaldırıldı.', 'good');
    if (result.notice) toast(result.notice, 'warn');
    await refresh();
  });
}

// ================================================================ düzenleyici

/**
 * Slot düzenleyici. `row` yoksa yeni slot açılır (TASLAK olarak kaydedilir;
 * yayına almak ayrı düğme ve ayrı izindir).
 */
function openEditor(row) {
  const forms = [];
  const cleaners = [];
  const dropAll = () => {
    forms.forEach((form) => form.destroy());
    cleaners.forEach((fn) => fn());
    forms.length = 0;
    cleaners.length = 0;
  };
  const area = row?.area || state.area;
  const box = drawer(nodes.root, {
    title: row ? row.title || '(başlıksız slot)' : 'Yeni slot',
    subtitle: `${AREAS.find((item) => item.key === area)?.label || ''}`
      + (row ? ` · #${row.id}` : ' · taslak olarak açılır'),
    onClose: dropAll,
  });
  closers.push(dropAll);

  if (state.readOnly) {
    box.body.append(alertBox(
      'Salt okunur görünüm: slot ayrıntıları mağazadaki BBD ucu yayınlanınca düzenlenebilecek.',
      'warn'));
  }

  // Görsel taslağı çekmece kapanana kadar burada durur; kaydedilene dek
  // mağazaya gitmez.
  const picked = { data: '', name: '', bytes: 0, verdict: null, acknowledged: false };

  const form = formGrid({
    fields: [
      { key: 'title', label: 'Başlık', type: 'text', required: true, maxLength: 160, wide: true,
        hint: 'Listede ve raporda bu isimle görünür.' },
      { key: 'altText', label: 'Görselin alt metni', type: 'text', required: area !== 'announcement',
        maxLength: 200, wide: true,
        hint: 'ZORUNLU. Ekran okuyucu ve arama motoru banner’ı yalnız bu metinden okur. '
          + '“banner1.jpg” değil, ne anlattığını yazın.' },
      { key: 'linkKind', label: 'Hedef türü', type: 'select', options: LINK_KINDS },
      { key: 'link', label: 'Hedef bağlantı', type: 'text', maxLength: 400, wide: true,
        hint: 'Serbest bağlantı `https://` ya da `/` ile başlar.' },
      { key: 'placement', label: 'Yerleşim', type: 'select', options: [
        { value: 'slider', label: 'Slider' }, { value: 'top', label: 'Üst' },
        { value: 'middle', label: 'Orta' }, { value: 'bottom', label: 'Alt' },
        { value: 'side', label: 'Yan' },
      ] },
      { key: 'device', label: 'Cihaz', type: 'select', options: [
        { value: 'all', label: 'Tümü' }, { value: 'desktop', label: 'Masaüstü' },
        { value: 'mobile', label: 'Mobil' },
      ] },
      { key: 'channel', label: 'Kanal', type: 'select', options: [
        { value: '', label: 'Varsayılan kanal' },
        ...state.reference.channels.map((item) => ({ value: item.code, label: item.name })),
      ] },
      { key: 'startsAt', label: 'Yayın başlangıcı', type: 'date',
        hint: 'Boşsa hemen başlar.' },
      { key: 'endsAt', label: 'Yayın bitişi', type: 'date', hint: 'Boşsa süresiz.' },
    ],
    value: {
      title: row?.title || '',
      altText: row?.altText || '',
      linkKind: row?.linkKind || 'url',
      link: row?.link || '',
      placement: row?.placement || (area === 'slider' ? 'slider' : 'top'),
      device: row?.device || 'all',
      channel: row?.channel || '',
      startsAt: row?.startsAt || '',
      endsAt: row?.endsAt || '',
    },
    onChange: () => paintTarget(),
  });
  forms.push(form);

  // ------------------------------------------------------------- hedef seçici
  const target = h('div', 'hm-target');
  const searchInput = h('input', 'kit-input');
  searchInput.type = 'search';
  searchInput.placeholder = 'Ürün adı ara';
  const results = h('div', 'hm-target-results');
  const search = debounce(async () => {
    const query = searchInput.value.trim();
    results.replaceChildren();
    if (query.length < 2) return;
    let payload;
    try {
      payload = await api(`${BASE}/link-search?q=${encodeURIComponent(query)}`);
    } catch (error) {
      results.append(alertBox(error.message, 'warn'));
      return;
    }
    if (!payload.items.length) {
      results.append(h('div', 'hm-sub', 'Eşleşen ürün yok.'));
      return;
    }
    for (const item of payload.items) {
      results.append(button(`${item.name} (${item.sku})`, {
        variant: 'ghost',
        onClick: () => { form.set('link', item.url); paintTarget(); },
      }));
    }
  }, 400);
  searchInput.addEventListener('input', search);
  cleaners.push(() => search.cancel());

  function paintTarget() {
    const kind = form.draft().linkKind;
    target.replaceChildren();
    if (kind === 'product') {
      target.append(h('div', 'hm-sub', 'Ürün seçin; seçim hedef bağlantı alanına yazılır.'),
        searchInput, results);
    } else if (kind === 'category' || kind === 'cms') {
      const list = kind === 'category'
        ? state.reference.categories.map((item) => ({ value: item.url, label: item.label }))
        : state.reference.pages.map((item) => ({ value: item.url, label: item.title }));
      const select = h('select', 'kit-select');
      select.setAttribute('aria-label', kind === 'category' ? 'Kategori seç' : 'Sayfa seç');
      for (const option of [{ value: '', label: 'Seçin…' }, ...list]) {
        const node = h('option', undefined, option.label);
        node.value = option.value;
        select.append(node);
      }
      select.addEventListener('change', () => {
        if (select.value) form.set('link', select.value);
      });
      target.append(select);
      if (!list.length) {
        target.append(h('div', 'hm-sub', 'Liste mağazadan gelmedi; bağlantıyı elle yazın.'));
      }
    } else {
      target.append(h('div', 'hm-sub',
        'Serbest bağlantı: `/kampanya` gibi site içi ya da `https://…` tam adres.'));
    }
  }
  paintTarget();

  // ------------------------------------------------------------------ görsel
  const imageBox = imagePane(area, row, picked);

  // ----------------------------------------------------------------- eylemler
  const actions = h('div', 'hm-actions');
  const save = button(row ? 'Kaydet' : 'Slotu oluştur', {
    variant: 'primary',
    onClick: async () => {
      if (!form.valid()) { form.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const draft = form.draft();
      if (area !== 'announcement' && !row?.imageUrl && !picked.data) {
        toast('Görsel seçin: görselsiz banner vitrinde boş bir kutu olarak çizilir.', 'bad');
        return;
      }
      const patch = row ? form.patch() : { ...draft, area };
      if (row) patch.area = area;
      if (!Object.keys(patch).length && !picked.data) {
        toast('Değişen alan yok.', 'warn');
        return;
      }
      const reason = await askReason({
        title: row ? 'Slotu güncelle' : 'Slot oluştur',
        description: row
          ? `${row.title} · ${form.dirty().length} alan değişti`
            + `${picked.data ? ' ve görsel değişiyor' : ''}. Gerekçe denetim kaydına yazılır.`
          : 'Yeni slot TASLAK olarak açılır; vitrinde görünmesi için ayrıca “Yayına al” '
            + 'demeniz gerekir.',
        confirmLabel: row ? 'Kaydet' : 'Oluştur',
      });
      if (!reason) return;
      await withBusy('Kaydediliyor…', async () => {
        const path = row ? `${BASE}/slots/${row.id}` : `${BASE}/slots`;
        const result = await call(path, {
          method: row ? 'PUT' : 'POST',
          body: { patch, image: picked.data || '', reason, dryRun: false },
        });
        toast(row ? 'Slot kaydedildi.' : 'Slot taslak olarak oluşturuldu.', 'good');
        // Ölçü ve kırpma TEK toast'ta: iki ayrı bildirim üst üste binip
        // birbirini yiyordu. "Uygun" durumunda hiç uyarı çıkmaz.
        if (result.sizeNote && result.sizeState !== 'ok') {
          toast([result.sizeNote, result.cropNote].filter(Boolean).join(' '), 'warn');
        }
        if (result.notice) toast(result.notice, 'warn');
        box.close();
        await refresh();
      });
    },
  });
  if (state.readOnly) save.disabled = true;
  actions.append(save, button('Vazgeç', { variant: 'ghost', onClick: box.close }));
  if (row) {
    actions.append(row.status
      ? button('Yayından kaldır', {
        variant: 'danger',
        onClick: async () => { box.close(); await togglePublish(row, false); },
      })
      : button('Yayına al', {
        variant: 'primary',
        onClick: async () => { box.close(); await togglePublish(row, true); },
      }));
  }

  box.body.append(
    imageBox,
    card('Künye', form.node),
    card('Hedef', target),
    actions,
    hintBox('Slot SİLİNMEZ. Yayından kaldırılan slot listede kalır; tıklama geçmişi ve '
      + 'görseli durur. “Kaydet” yayın durumunu değiştirmez — o ayrı bir izindir.'),
  );
}

/**
 * Görsel alanı: gizli `<input type=file>` + sürükle-bırak + `FileReader`.
 *
 * Tauri'de dosya sistemi/dialog eklentisi YOK; dosya tarayıcıda okunup base64
 * olarak gövdeyle taşınır. Seçilen görselin ölçüsü SUNUCUDA ölçülür
 * (`/image/check`): tarayıcının bildirdiği ölçüye güvenilmez ve karar
 * "Önerilen 1920x640; yüklenen 1200x400 — mobilde bulanık." cümlesiyle
 * kutunun altında durur.
 *
 * ÖNİZLEME İKİ KARE ÇİZER — bilerek. Solda görselin VİTRİNDEKİ hâli: önerilen
 * orana `cover` ile oturmuş, yani kırpılmış. Sağda dosyanın GERÇEK oranı. Tek
 * kare göstermek tam da uyarmaya çalıştığımız kırpmayı gizlerdi: kullanıcı
 * ekranda düzgün duran bir görsel görür, vitrinde kenarları kesik olanı bulurdu.
 *
 * İKİ YOL VAR ve ikisi de açıktır:
 *  · “Görseli yükle” — dosyayı mağazanın görsel ucuna gönderir
 *    (`POST /image/upload`). Uç henüz yayında değil; ekran bunu hata değil
 *    "hazır, bekliyor" diye anlatır (K7).
 *  · “Kaydet” — görsel slot gövdesiyle birlikte gider. Bugün çalışan yol budur.
 */
function imagePane(area, row, picked) {
  const wrap = h('div', 'hm-image');
  const recommended = state.recommended[area] || '';
  const wantsImage = recommended && recommended !== '0x0';

  const frame = h('div', 'hm-drop');
  const preview = h('div', 'hm-drop-preview');
  const note = h('div', 'hm-size-note');
  const meta = h('div', 'hm-sub');
  const outcome = h('div', 'hm-upload-note');   // yükleme sonucu / "uç bekleniyor"

  const input = h('input', 'hm-file');
  input.type = 'file';
  input.accept = state.allowedTypes.join(',');
  input.id = `hm-file-${area}-${row?.id || 'yeni'}`;
  // Görsel olarak gizli ama KLAVYEYLE ULAŞILIR: `display:none` verilseydi
  // sekme tuşuyla erişilemez ve dosya seçmenin klavye yolu kalmazdı.
  const label = h('label', 'kit-btn kit-btn-primary hm-file-label', 'Dosya seç');
  label.setAttribute('for', input.id);

  const upload = button('Görseli yükle', {
    title: 'Dosyayı mağazanın görsel ucuna gönderir (slot kaydından ayrı yol).',
    onClick: () => uploadPicked(),
  });

  function refreshTools() {
    upload.disabled = Boolean(state.readOnly) || !wantsImage || !picked.data;
  }

  function paint() {
    preview.replaceChildren();
    const source = picked.data || row?.imageUrl || '';
    if (!source) {
      preview.append(h('span', 'hm-drop-text', wantsImage
        ? 'Görseli buraya sürükleyin ya da “Dosya seç”'
        : 'Bu alan görsel istemez; metin yeterli.'));
      return;
    }
    // Ölçü SUNUCUDAN gelir. Taze seçimde `/image/check` yanıtından, var olan
    // slotta satırın kendisinden; tarayıcının `naturalWidth` değeri kullanılmaz.
    const fresh = picked.data ? picked.verdict : null;
    const width = fresh ? fresh.width : (row?.imageWidth || 0);
    const height = fresh ? fresh.height : (row?.imageHeight || 0);

    if (wantsImage) {
      const shop = h('figure', 'hm-ratio-cell');
      const viewport = h('div', 'hm-ratio-frame');
      viewport.style.aspectRatio = recommended.replace(/x/i, ' / ');
      const shown = h('img');
      shown.src = source;
      shown.alt = '';
      viewport.append(shown);
      shop.append(viewport, h('figcaption', 'hm-sub', `Vitrinde — ${recommended}`));
      preview.append(shop);
    }

    const real = h('figure', 'hm-ratio-cell');
    const image = h('img', 'hm-ratio-true');
    image.src = source;
    image.alt = '';
    const box = fresh?.previewBox;
    if (box?.width) {
      // Kutu sunucunun ölçtüğü en/boydan gelir; panel oran hesabı yapmaz.
      image.style.width = `${box.width}px`;
      image.style.height = `${box.height}px`;
    } else if (width > 0 && height > 0) {
      image.style.aspectRatio = `${width} / ${height}`;
    }
    const ratio = fresh?.aspect ? `Gerçek oranı ${fresh.aspect}` : 'Gerçek oranı';
    real.append(image, h('figcaption', 'hm-sub', width
      ? `${ratio} · ${num(width)}×${num(height)}`
      : 'Ölçü okunamadı — gerçek oran gösterilemiyor.'));
    preview.append(real);
  }

  function showVerdict(verdict) {
    note.replaceChildren();
    if (!verdict) {
      note.append(h('span', 'hm-sub', wantsImage
        ? `Önerilen ölçü ${recommended}.` : 'Bu alan görsel istemez.'));
      return;
    }
    note.append(badge(
      { ok: 'Uygun', blurry: 'Düşük çözünürlük', ratio: 'Oran farklı',
        unknown: 'Ölçü okunamadı', none: 'Görsel gerekmiyor' }[verdict.sizeState]
        || verdict.sizeState,
      SIZE_TONES[verdict.sizeState] || '',
    ));
    // ZORUNLU ALT METİN: karar her zaman yazıyla da durur, renge bırakılmaz.
    note.append(h('span', 'hm-size-text', verdict.sizeNote));
    // Kırpma ayrı cümledir: "oran farklı" bir tespit, "soldan ve sağdan %25
    // kırpılacak" ise kullanıcının afişi yeniden kesmesini sağlayan bilgi.
    if (verdict.cropNote) note.append(h('span', 'hm-crop-text', verdict.cropNote));
  }

  async function accept(file) {
    if (!file) return;
    if (!state.allowedTypes.includes(file.type)) {
      toast(`${file.type || 'Bilinmeyen tür'} kabul edilmiyor: `
        + `${state.allowedTypes.join(', ')}.`, 'bad');
      return;
    }
    if (file.size > state.maxImageBytes) {
      toast(`Dosya ${formatBytes(file.size)}; tavan ${formatBytes(state.maxImageBytes)}. `
        + 'Yüklemeden önce küçültün.', 'bad');
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => toast('Dosya okunamadı.', 'bad');
    reader.onload = async () => {
      picked.data = String(reader.result || '');
      picked.name = file.name;
      picked.bytes = file.size;
      picked.acknowledged = false;
      outcome.replaceChildren();
      meta.textContent = `${file.name} · ${formatBytes(file.size)}`;
      paint();
      refreshTools();
      try {
        const verdict = await call(`${BASE}/image/check`, {
          method: 'POST', body: { data: picked.data, area },
        });
        picked.verdict = verdict;
        showVerdict(verdict);
        paint();                       // ölçü geldi: gerçek oranlı kare yeniden çizilir
        // ORAN DENETİMİ ZORUNLU: hem bulanıklık hem kırpma sesli uyarı verir.
        // Eskiden yalnız bulanıklık uyarırdı ve "oran farklı" sessizce geçerdi.
        if (verdict.needsConfirm) toast(verdict.sizeNote, 'warn');
      } catch (error) {
        picked.data = '';
        picked.verdict = null;
        note.replaceChildren(alertBox(error.message, 'bad'));
        paint();
        refreshTools();
      }
    };
    reader.readAsDataURL(file);
  }

  /** Dosyayı mağazanın görsel ucuna gönderir. Slot kaydından AYRI yoldur. */
  async function uploadPicked() {
    if (!picked.data) { toast('Önce dosya seçin.', 'bad'); return; }
    if (picked.verdict?.needsConfirm && !picked.acknowledged) {
      // Onay burada KOLAYLIK, kapı değil: sunucu `acknowledged` bayrağı
      // olmadan yüklemeyi zaten reddediyor (K9).
      const go = await confirmSimple(nodes.root, {
        title: 'Ölçü önerilenden farklı',
        description: `${picked.verdict.sizeNote} ${picked.verdict.cropNote || ''} `.trim()
          + ' Yine de yüklensin mi? Karar denetim kaydına yazılır.',
        confirmLabel: 'Yine de yükle',
        danger: true,
      });
      if (!go) return;
      picked.acknowledged = true;
    }
    const reason = await askReason({
      title: 'Görseli mağazaya yükle',
      description: 'Dosya mağazanın görsel ucuna gönderilir; slotun künyesi değişmez. '
        + 'Gerekçe denetim kaydına yazılır.',
      confirmLabel: 'Yükle',
    });
    if (!reason) return;
    await withBusy('Görsel yükleniyor…', async () => {
      // `call` KULLANILMAZ: burada `ok:false` her zaman hata değil. Uç henüz
      // yayında değilse `pending` gelir ve ekran onu anlatır, patlamaz (K7).
      const result = await api(`${BASE}/image/upload`, {
        method: 'POST',
        body: {
          data: picked.data, area, slotId: row?.id || 0, filename: picked.name,
          acknowledged: Boolean(picked.acknowledged), reason, dryRun: false,
        },
      });
      outcome.replaceChildren();
      if (result.pending) {
        outcome.append(alertBox(result.error, 'info'));
        toast('Yükleme ucu hazır olunca açılacak; görsel şimdilik “Kaydet” ile gidiyor.',
          'warn');
        return;
      }
      if (result.ok === false) {
        outcome.append(alertBox(result.error, 'bad'));
        if (result.needsConfirm) picked.acknowledged = false;
        toast(result.error, 'bad');
        return;
      }
      outcome.append(alertBox(`Görsel yüklendi: ${result.file}`, 'good'));
      toast('Görsel yüklendi.', 'good');
      if (result.notice) toast(result.notice, 'warn');
    });
  }

  input.addEventListener('change', () => accept(input.files?.[0]));
  frame.addEventListener('dragover', (event) => {
    event.preventDefault();
    frame.classList.add('over');
  });
  frame.addEventListener('dragleave', () => frame.classList.remove('over'));
  frame.addEventListener('drop', (event) => {
    event.preventDefault();
    frame.classList.remove('over');
    accept(event.dataTransfer?.files?.[0]);
  });

  const tools = h('div', 'hm-image-tools');
  // Sıra önemli: gizli girdi ETİKETTEN ÖNCE gelir, yoksa odak halkasını
  // etikete taşıyan kardeş seçici (.hm-file:focus-visible + .hm-file-label)
  // eşleşmez ve klavye kullanıcısı nereye bastığını göremez.
  tools.append(input, label, upload, button('Seçimi bırak', {
    variant: 'ghost',
    onClick: () => {
      picked.data = '';
      picked.verdict = null;
      picked.acknowledged = false;
      meta.textContent = '';
      outcome.replaceChildren();
      paint();
      showVerdict(null);
      refreshTools();
    },
  }));

  frame.append(preview);
  paint();
  refreshTools();
  showVerdict(row && row.imageUrl
    ? { sizeState: row.sizeState, sizeNote: row.sizeNote } : null);
  wrap.append(card('Görsel', frame, wantsImage ? `Önerilen ${recommended}` : 'Görsel istemez'),
    tools, meta, note, outcome);
  return wrap;
}

// -------------------------------------------------------------------- CSV

function exportVisible() {
  const rows = areaRows();
  const written = csvBlob(
    ['Başlık', 'Alt metni', 'Hedef', 'Yerleşim', 'Cihaz', 'Başlangıç', 'Bitiş', 'Durum',
      'Tıklama', 'Uyarı'],
    rows.map((row) => [row.title, row.altText, row.link, row.placementLabel, row.deviceLabel,
      row.startsAt, row.endsAt, row.stateLabel,
      row.clicksKnown ? row.clicks : '', (row.issues || []).join(', ')]),
    `ana-sayfa-${state.area}`,
  );
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: {} });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ------------------------------------------------------------------- çizim

function renderAll() {
  renderPreview();
  renderKpi();
  renderList();
  nodes.tabs?.badge('slider', state.counts.slider || undefined);
  nodes.tabs?.badge('banner', state.counts.banner || undefined);
  nodes.tabs?.badge('collection', state.counts.collection || undefined);
  nodes.tabs?.badge('announcement', state.counts.announcement || undefined);
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel hm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  // `reverting` bayrağı ZORUNLU: `select()` geri alırken `onChange` bir kez
  // daha çağrılır ve bayrak olmasaydı onay kutusu sonsuz döngüye girerdi.
  let reverting = false;
  nodes.tabs = tabBar(AREAS, 'slider', (key) => {
    if (reverting) { reverting = false; return; }
    if (orderDirty()
      && !window.confirm('Kaydedilmemiş sıra değişikliği var; sekme değişince gider.')) {
      reverting = true;
      nodes.tabs.select(state.area);
      return;
    }
    state.area = key;
    refresh();
  });

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Başlık ya da hedef ara', width: '240px' },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Tümü' },
        { value: 'published', label: 'Yayında' },
        { value: 'scheduled', label: 'Zamanlanmış' },
        { value: 'expired', label: 'Süresi doldu' },
        { value: 'draft', label: 'Taslak' },
      ] },
      { kind: 'select', key: 'placement', label: 'Yerleşim', options: [
        { value: '', label: 'Tümü' }, { value: 'slider', label: 'Slider' },
        { value: 'top', label: 'Üst' }, { value: 'middle', label: 'Orta' },
        { value: 'bottom', label: 'Alt' }, { value: 'side', label: 'Yan' },
      ] },
      { kind: 'select', key: 'device', label: 'Cihaz', options: [
        { value: '', label: 'Tümü' }, { value: 'desktop', label: 'Masaüstü' },
        { value: 'mobile', label: 'Mobil' },
      ] },
      { kind: 'select', key: 'channel', label: 'Kanal',
        options: [{ value: '', label: 'Tümü — kanal' }] },
      { kind: 'dateRange', key: 'range', label: 'Yayın aralığı' },
    ],
    onChange: () => refresh(),
    actions: [
      button('Slot ekle', { variant: 'primary', onClick: () => openEditor(null) }),
      button('Yenile', { onClick: () => refresh() }),
      button('⤓ Görünen', { title: 'Ekrandaki listeyi CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm slotları rapor klasörüne yaz', onClick: exportAll }),
      button('Yerleşim raporu', { onClick: () => report.run('layout', { area: '' }) }),
    ],
  });

  nodes.preview = h('div', 'hm-preview');
  nodes.kpi = h('div', 'hm-kpi');
  nodes.orderbar = h('div', 'hm-orderbar');
  nodes.listWrap = h('div', 'hm-listwrap');
  nodes.status = statusLine();
  // Klavyeyle taşımanın sesli karşılığı: ekran okuyucu "3. sıraya taşındı" der.
  nodes.live = h('div', 'hm-live');
  nodes.live.setAttribute('role', 'status');
  nodes.live.setAttribute('aria-live', 'polite');

  nodes.body = h('div', 'hm-body');
  nodes.body.append(nodes.preview, nodes.kpi, nodes.orderbar, nodes.listWrap);

  view.append(nodes.tabs.node, nodes.filters.node, nodes.status.node, nodes.live, nodes.body);
  root.replaceChildren(view);

  nodes.status.set('Ana sayfa okunuyor…');
  // Referans listeler ÖNCE gelir: kanal süzgeci dolmadan liste çekmek,
  // kullanıcının seçtiği kanalı kaybettiriyordu.
  loadReference().then(() => refresh());

  return () => {
    nodes.filters?.destroy();          // arama alanı ve tarih aralığı dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
