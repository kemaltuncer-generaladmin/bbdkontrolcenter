// Ana Ekran Görselleri paneli — siteye ilk girişte dönen görseller.
//
// NE YAPAR: tek bir liste. Üstte müşterinin gördüğü sıranın şeridi, altında o
// görsellerin listesi. Her satırda görselin kendisi, adı ve tıklanınca
// gideceği adres var. Sıra sürükle-bırak İLE VE `Ctrl+↑/↓` ile değişir.
// "Kaydet" listenin tamamını (sıra dâhil) tek seferde yazar.
//
// EKRAN 18.08.2026'DA TEK İŞE İNDİ. Dört sekme vardı — kayan görseller,
// tanıtım görselleri, öne çıkan ürün grupları, üst duyuru yazısı — ve kodun
// çoğu o dördünü ayırmaya, yayın tarihi/cihaz/durum alanlarını çizmeye ve
// "bu bölüm şu an düzenlenemiyor" demeye harcanıyordu. Kullanıcı kararı üç
// sekmenin de kaldırılması oldu; onlarla birlikte süzgeçler, durum rozetleri,
// yayın tarihleri, cihaz seçimi, yerleşim raporu ve CSV de kalktı.
//
// NE YAPMAZ:
//  · Görseli KIRPMAZ/BÜYÜTMEZ. Bulanık bir görseli sessizce büyütmek onu daha
//    da bozardı; ekran ölçüyü ve hangi kenardan ne kadar kırpılacağını söyler,
//    kararı kullanıcı verir ve karar denetim kaydına yazılır.
//  · Listeyi BOŞALTMAZ. Son görsel de çıkarılırsa ana sayfanın en üstü bomboş
//    kalır; hem ekran hem sunucu bunu reddeder.
//  · Gerekçesiz yazmaz (ADR 0012). Her kaydetme bir not ister ve o not
//    "değişiklik geçmişi"nde durur.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Sıra ayrı bir işlem DEĞİLDİR: mağaza listeyi olduğu gibi çizdiği için
//    sırayı değiştirmek de listeyi yazmak demektir. Bu yüzden tek "Kaydet".
//  · Tauri'de dosya sistemi/dialog eklentisi YOK. Bu yüzden gizli
//    `<input type=file>` + `FileReader` + base64 gövde kullanılır.
//  · Görselin ölçüsü SUNUCUDA ölçülür (`/image/check`); tarayıcının bildirdiği
//    `naturalWidth` değerine güvenilmez.
//  · Görsel önizlemesini SABİT bir çerçeveye sığdırmak, uyarmaya çalıştığımız
//    kırpmayı gizler. Bu yüzden iki kare çizilir: vitrindeki (kırpılmış) hâli
//    ve dosyanın gerçek oranı.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_home_media/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_home_media/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  bytes as formatBytes, button, clip, confirmSimple, confirmWithReason, debounce, h,
  loadStyles, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, skeletonRows, statusLine,
} from '../../ui-kit/layout.js';

const BASE = '/api/store_home_media';

const SIZE_TONES = { ok: 'good', blurry: 'bad', ratio: 'warn', unknown: 'warn' };
const SIZE_LABELS = {
  ok: 'Ölçü uygun', blurry: 'Bulanık çıkar', ratio: 'Kenarları kesilir',
  unknown: 'Ölçü okunamadı',
};

// ENGELLER — NEDEN + SIRADAKİ ADIM, tek yerde.
//
// Desen `store_shipping/backend/geliver.py` içindeki `BLOCKER_ACTIONS`'tan
// gelir ve aynı kuralı taşır: BİR İŞ YAPILAMIYORSA EKRAN İKİ ŞEY SÖYLER —
// neden yapılamadığı, ve kullanıcının ŞİMDİ ne yapacağı. Tek cümlelik ret
// metinleri kullanıcıyı ekranda bırakıyordu: doğru ama işe yaramaz.
//
// `why` kullanıcının SUÇLU OLMADIĞINI da söyler; `next` her zaman bir eylemle
// başlar.
const BLOCKERS = {
  OFFLINE: {
    why: 'Mağazaya ulaşılamadı; ekran şu an mağazadaki gerçek durumu göremiyor.',
    next: 'Sıradaki adım: internet bağlantısını kontrol edip “Tekrar dene” deyin.',
  },
  NEEDS_IMAGE: {
    why: 'Görselsiz satır kaydedilemez: ana sayfada boş bir çerçeve olarak çizilir.',
    next: 'Sıradaki adım: satırdaki “Görseli değiştir” ile bir dosya seçin ya da satırı '
      + '“Çıkar” deyip listeden kaldırın.',
  },
  LAST_ONE: {
    why: 'Listede tek bir görsel kaldı; onu da çıkarırsanız ana sayfanın en üstü bomboş kalır.',
    next: 'Sıradaki adım: önce yerine koyacağınız görseli ekleyin, sonra bunu çıkarın.',
  },
};

/** Engelin iki cümlesini ekranda tek kutuda gösterir (neden + sıradaki adım). */
function blockerBox(key, tone = 'warn') {
  const item = BLOCKERS[key];
  const box = h('div', `kit-alert ${tone} hm-blocker`);
  box.append(h('div', 'hm-blocker-why', item.why));
  box.append(h('div', 'hm-blocker-next', item.next));
  return box;
}

/** Kapalı düğmenin nedenini fare ipucuna VE ekran okuyucuya yazar. */
function blockedReason(node, key) {
  const item = BLOCKERS[key];
  const text = `${item.why} ${item.next}`;
  node.title = text;
  node.setAttribute('aria-label', `${node.textContent} — kapalı: ${text}`);
  node.dataset.blocked = '1';
  return node;
}

const EMPTY_STATE = {
  rows: [], base: '', connected: false, error: '', recommended: '',
  maxImageBytes: 2000000, allowedTypes: ['image/png', 'image/jpeg'],
  maxSlides: 30, notice: '', loaded: false,
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE, rows: [] };

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
    // "İşlem başarısız" hiçbir şey anlatmıyordu. Sunucunun cevabı VARSA
    // olduğu gibi gösterilir; yoksa en azından ne yapılacağı yazılır.
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

/** Vitrini etkileyen her yazma buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Neden değiştiriyorsunuz? (en az 10 karakter) — "Eylül kampanyası '
      + 'başladı" gibi. Bu not kayda geçer.',
  });
}

/** Ekran okuyucuya "3. sıraya taşındı" gibi anlık bilgi verir. */
function announce(message) {
  if (nodes.live) nodes.live.textContent = message;
}

/** Kaydedilmemiş değişikliğin ölçütü: liste + görseller + adresler. */
function snapshot(rows) {
  return JSON.stringify(rows.map((row) => [row.title, row.link, row.image, row.pendingData]));
}

function dirty() {
  return snapshot(state.rows) !== state.base;
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const eksik = state.rows.filter((row) => row.issues?.length).length;
  const parts = [`Mağazaya bağlı · ana ekranda ${num(state.rows.length)} görsel dönüyor`];
  if (eksik) parts.push(`${num(eksik)} tanesinde düzeltilecek bir şey var`);
  if (dirty()) parts.push('kaydedilmemiş değişiklik var');
  return parts.join(' · ');
}

// -------------------------------------------------------------------- veri

/** Sunucudan gelen satırı ekranın taslak nesnesine çevirir. */
function toRow(item) {
  return {
    title: item.title || '',
    link: item.link || '',
    image: item.image || '',
    imageUrl: item.imageUrl || item.image || '',
    issues: item.issues || [],
    sizeState: item.sizeState || '',
    sizeNote: item.sizeNote || '',
    // Seçilmiş ama HENÜZ YÜKLENMEMİŞ dosya. Kaydetme anında yüklenir ve
    // dönen yol `image` alanına yazılır.
    pendingData: '',
    pendingName: '',
    verdict: null,
    acknowledged: false,
  };
}

async function refresh() {
  nodes.listWrap?.replaceChildren(skeletonRows(5, 4));
  nodes.status?.set('Ana ekranda ne olduğu okunuyor…');
  let payload;
  try {
    payload = await api(`${BASE}/slides`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, rows: [] };
    renderAll();
    nodes.status?.set(statusText(), true);
    return;
  }
  const rows = (payload.items || []).map(toRow);
  state = {
    ...state,
    rows,
    base: snapshot(rows),
    connected: Boolean(payload.connected),
    error: payload.error || '',
    recommended: payload.recommended || '',
    maxImageBytes: payload.maxImageBytes || EMPTY_STATE.maxImageBytes,
    allowedTypes: payload.allowedTypes || EMPTY_STATE.allowedTypes,
    maxSlides: payload.maxSlides || EMPTY_STATE.maxSlides,
    notice: payload.notice || '',
    loaded: true,
  };
  renderAll();
  nodes.status?.set(statusText(), !state.connected);
}

// ================================================================ önizleme

/**
 * Müşterinin gördüğü sıranın şeridi.
 *
 * GERÇEK SAYFA DEĞİLDİR ve öyle olduğunu iddia etmez: `iframe` ile canlı
 * sayfayı çekmek CSP'de `frame-src` olmadığı için WebKitGTK'da öngörülemez
 * davranıyor. Bunun yerine görseller önerilen ORANLARINDA, soldan sağa sırayla
 * çizilir — sıranın doğru olup olmadığı buradan tek bakışta görünür.
 */
function renderPreview() {
  const host = nodes.preview;
  if (!host) return;
  host.replaceChildren();

  const head = h('div', 'hm-preview-head');
  head.append(h('b', undefined, 'Müşteri siteye girince bunları bu sırayla görüyor'));
  head.append(h('span', 'kit-spacer'));
  head.append(h('span', 'hm-sub', 'Küçültülmüş temsil; gerçek sayfada her biri tam '
    + 'genişlikte ve sırayla döner.'));
  host.append(head);

  if (!state.connected) {
    host.append(alertBox(`Mağazaya ulaşılamadı — ${state.error}`, 'bad'));
    return;
  }
  if (!state.rows.length) return;

  const band = h('div', 'hm-band hm-band-slider');
  band.setAttribute('aria-label', 'Ana ekranda dönen görseller');
  state.rows.forEach((row, index) => {
    // Tıklanabilir: şeritte gördüğünüz kutu, o satırın görsel penceresini açar.
    const tile = h('button', 'hm-tile');
    tile.type = 'button';
    tile.title = `${index + 1}. sıra: ${row.title || '(adsız)'} — görseli değiştirmek için tıklayın`;
    tile.setAttribute('aria-label', `${index + 1}. sıra: ${row.title || 'adsız'}`);
    tile.addEventListener('click', () => openImage(index));
    const source = row.pendingData || row.imageUrl;
    if (source) {
      const image = h('img');
      image.loading = 'lazy';
      image.src = source;
      image.alt = row.title || '';
      image.addEventListener('error', () => {
        tile.classList.add('broken');
        tile.replaceChildren(h('span', 'hm-tile-text', 'görsel açılmıyor'));
      });
      tile.append(image);
    } else {
      tile.classList.add('broken');
      tile.append(h('span', 'hm-tile-text', 'görsel yok'));
    }
    if (row.pendingData) tile.append(h('span', 'hm-tile-flag', 'kaydedilmedi'));
    band.append(tile);
  });
  host.append(band);
}

// ==================================================================== liste

function renderList() {
  const host = nodes.listWrap;
  if (!host) return;
  host.replaceChildren();

  if (!state.connected) {
    host.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: `${BLOCKERS.OFFLINE.why} ${BLOCKERS.OFFLINE.next}`
        + (state.error ? ` (Mağazanın verdiği cevap: ${state.error})` : ''),
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    }));
    renderOrderBar();
    return;
  }

  if (!state.rows.length) {
    host.append(emptyState({
      title: 'Ana ekranda hiç görsel yok',
      text: 'Müşteri siteye girdiğinde en üstte boş bir alan görüyor. Buraya bir kampanya '
        + 'görseli ekleyin.',
      actions: [button('Görsel ekle', { variant: 'primary', onClick: addRow })],
    }));
    renderOrderBar();
    return;
  }

  const list = h('div', 'hm-list');
  list.setAttribute('role', 'list');
  state.rows.forEach((row, index) => list.append(slideRow(row, index)));
  host.append(list);
  renderOrderBar();
}

/** Tek satır: sürükle-bırak + `Ctrl+↑/↓` ile taşınır. */
function slideRow(row, index) {
  const total = state.rows.length;
  const item = h('div', 'hm-slot');
  item.setAttribute('role', 'listitem');
  item.tabIndex = 0;
  item.dataset.index = String(index);
  item.draggable = true;
  item.setAttribute('aria-label',
    `${index + 1}. görsel: ${row.title || 'adsız'}. `
    + 'Sırasını değiştirmek için Ctrl ile yukarı/aşağı ok tuşu.');

  const handle = h('span', 'hm-handle', '⋮⋮');
  handle.title = 'Sürükleyerek ya da Ctrl+↑/↓ ile yukarı-aşağı taşıyın. Üstteki, ana '
    + 'ekranda da ilk gösterilendir.';
  handle.setAttribute('aria-hidden', 'true');

  const thumb = h('button', 'hm-thumb');
  thumb.type = 'button';
  thumb.title = 'Görseli değiştirmek için tıklayın';
  thumb.addEventListener('click', () => openImage(index));
  const source = row.pendingData || row.imageUrl;
  if (source) {
    const image = h('img');
    image.loading = 'lazy';
    image.src = source;
    image.alt = '';
    image.addEventListener('error', () => {
      thumb.classList.add('none');
      thumb.replaceChildren(document.createTextNode('!'));
      thumb.title = 'Görsel açılmıyor — dosya silinmiş ya da adresi değişmiş olabilir. '
        + 'Yeni bir dosya seçin.';
    });
    thumb.append(image);
  } else {
    thumb.classList.add('none');
    thumb.textContent = '—';
    thumb.title = 'Görsel seçilmemiş — ana ekranda boş bir çerçeve çizilir. Seçmek için '
      + 'tıklayın.';
  }

  const main = h('div', 'hm-slot-main');
  const title = h('input', 'kit-input');
  title.type = 'text';
  title.maxLength = 160;
  title.value = row.title;
  title.placeholder = 'Bu görsele ne ad verelim? (yalnız siz görürsünüz)';
  title.setAttribute('aria-label', `${index + 1}. görselin adı`);
  title.addEventListener('input', () => {
    row.title = title.value;
    renderOrderBar();
    nodes.status?.set(statusText());
  });

  const linkRow = h('div', 'hm-target');
  const link = h('input', 'kit-input');
  link.type = 'text';
  link.maxLength = 400;
  link.value = row.link;
  link.placeholder = 'Tıklayınca nereye gitsin? — /kampanya ya da https://…';
  link.setAttribute('aria-label', `${index + 1}. görsele tıklanınca gidilecek adres`);
  link.addEventListener('input', () => {
    row.link = link.value;
    renderOrderBar();
  });
  const find = button('Ürün ara', {
    variant: 'ghost',
    title: 'Ürünü aratıp seçin; adresi bu kutuya kendiliğinden yazılır',
    onClick: () => openLinkPicker(row, link),
  });
  linkRow.append(link, find);

  main.append(title, linkRow);
  if (row.issues?.length) {
    // "Düzeltilecek" listesi SONUCU söyler ("kenarları kesilir"), teknik
    // tespiti değil: kullanıcının kararı sonuca göre değişiyor.
    main.append(h('div', 'hm-issues', `Düzeltilecek: ${row.issues.join(' · ')}`));
  }
  if (row.pendingData) {
    main.append(h('div', 'hm-sub',
      `Yeni dosya seçildi (${row.pendingName}) — “Kaydet” demeden gönderilmez.`));
  }

  const right = h('div', 'hm-slot-right');
  right.append(h('b', undefined, `${index + 1}. sıra`));
  if (row.sizeState && row.sizeState !== 'ok') {
    const flag = badge(SIZE_LABELS[row.sizeState] || row.sizeState,
      SIZE_TONES[row.sizeState] || '');
    flag.title = row.sizeNote || '';
    right.append(flag);
  }

  const tools = h('div', 'hm-slot-tools');
  tools.append(
    button('Görseli değiştir', {
      title: 'Bilgisayarınızdan yeni bir dosya seçin',
      onClick: () => openImage(index),
    }),
    button('↑', { variant: 'ghost', title: 'Bir üst sıraya taşı (Ctrl+↑)',
      onClick: () => moveRow(index, -1) }),
    button('↓', { variant: 'ghost', title: 'Bir alt sıraya taşı (Ctrl+↓)',
      onClick: () => moveRow(index, 1) }),
  );
  const remove = button('Çıkar', {
    variant: 'danger',
    title: 'Bu görseli listeden çıkarır. Kaydedene kadar mağazada bir şey değişmez.',
    onClick: () => removeRow(index),
  });
  if (total <= 1) {
    remove.disabled = true;
    blockedReason(remove, 'LAST_ONE');
  }
  tools.append(remove);

  item.append(handle, thumb, main, right, tools);

  // KLAVYE: sürükle-bırak tek yol OLAMAZ. Ctrl+ok ile taşıma, fare
  // kullanamayan personel için tek erişim yolu (ve en hızlısı).
  item.addEventListener('keydown', (event) => {
    if (!event.ctrlKey) return;
    const step = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
    if (!step) return;
    event.preventDefault();
    if (moveRow(index, step)) {
      announce(`${row.title || 'Adsız görsel'}, ${index + 1 + step}. sıraya taşındı `
        + `(${total} kayıt içinde). Kalıcı olması için “Kaydet” demeniz gerekir.`);
      focusRow(index + step);
    }
  });

  item.addEventListener('dragstart', (event) => {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(index));
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
    dropRow(Number(event.dataTransfer.getData('text/plain')), index);
  });
  return item;
}

function focusRow(index) {
  nodes.listWrap?.querySelector(`.hm-slot[data-index="${index}"]`)?.focus();
}

/** Taşıma kuralı tek yerde: klavye de sürükleme de bunu çağırır. */
function moveRow(index, step) {
  const target = index + step;
  if (target < 0 || target >= state.rows.length) return false;
  const rows = [...state.rows];
  [rows[index], rows[target]] = [rows[target], rows[index]];
  state.rows = rows;
  renderAll();
  return true;
}

function dropRow(from, to) {
  if (from === to || Number.isNaN(from)) return;
  const rows = [...state.rows];
  const [moved] = rows.splice(from, 1);
  rows.splice(to, 0, moved);
  state.rows = rows;
  renderAll();
  announce('Taşındı. Kalıcı olması için “Kaydet” demeniz gerekir.');
}

function addRow() {
  if (state.rows.length >= state.maxSlides) {
    toast(`En çok ${num(state.maxSlides)} görsel olabilir.`, 'warn');
    return;
  }
  state.rows = [...state.rows, toRow({ title: '' })];
  renderAll();
  // Yeni satır GÖRSELSİZ doğar ve kaydedilemez; kullanıcıyı doğrudan dosya
  // seçmeye götürmek, "neden kaydedilmiyor" sorusunu hiç doğurmaz.
  openImage(state.rows.length - 1);
}

function removeRow(index) {
  if (state.rows.length <= 1) {
    toast(`${BLOCKERS.LAST_ONE.why} ${BLOCKERS.LAST_ONE.next}`, 'bad');
    return;
  }
  const rows = [...state.rows];
  rows.splice(index, 1);
  state.rows = rows;
  renderAll();
  announce('Listeden çıkarıldı. Kalıcı olması için “Kaydet” demeniz gerekir.');
}

function renderOrderBar() {
  const bar = nodes.orderbar;
  if (!bar) return;
  bar.replaceChildren();
  if (!dirty()) {
    bar.classList.remove('on');
    return;
  }
  bar.classList.add('on');
  bar.append(h('b', undefined,
    'Değişiklikleriniz HENÜZ KAYDEDİLMEDİ. Müşteri hâlâ eski hâlini görüyor.'));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Kaydet', {
      variant: 'primary',
      title: 'Listeyi (sıra, görseller ve adresler) mağazaya yazar',
      onClick: save,
    }),
    button('Değişiklikleri geri al', {
      variant: 'ghost',
      title: 'Ekranı mağazadaki kayıtlı hâline döndürür',
      onClick: () => refresh(),
    }),
  );
}

// ================================================================== yazma

async function save() {
  const eksik = state.rows.findIndex((row) => !row.image && !row.pendingData);
  if (eksik >= 0) {
    toast(`${eksik + 1}. sıra — ${BLOCKERS.NEEDS_IMAGE.why} ${BLOCKERS.NEEDS_IMAGE.next}`, 'bad');
    focusRow(eksik);
    return;
  }
  const adsiz = state.rows.findIndex((row) => !row.title.trim());
  if (adsiz >= 0) {
    toast(`${adsiz + 1}. sıradaki görsele bir ad verin. Müşteri bu adı görmez; siz `
      + 'listede bunu görürsünüz.', 'bad');
    focusRow(adsiz);
    return;
  }

  const yeni = state.rows.filter((row) => row.pendingData).length;
  const reason = await askReason({
    title: 'Ana ekran görsellerini kaydet',
    description: `${num(state.rows.length)} görsel bu sırayla yazılacak`
      + `${yeni ? ` ve ${num(yeni)} yeni dosya yüklenecek` : ''}. Kaydettiğiniz anda `
      + 'müşteri yeni hâlini görmeye başlar.',
    confirmLabel: 'Kaydet',
  });
  if (!reason) return;

  await withBusy('Kaydediliyor…', async () => {
    // ÖNCE DOSYALAR, SONRA LİSTE. Mağaza liste gövdesinde serbest yol kabul
    // etmiyor: yalnız kendi yüklediği klasördeki dosyayı yazıyor. Yani her
    // yeni görsel önce yüklenip yolunu almalı.
    for (const [index, row] of state.rows.entries()) {
      if (!row.pendingData) continue;
      nodes.status?.set(`${index + 1}. görsel yükleniyor…`);
      const result = await api(`${BASE}/image/upload`, {
        method: 'POST',
        body: {
          data: row.pendingData, filename: row.pendingName,
          acknowledged: Boolean(row.acknowledged), reason, dryRun: false,
        },
      });
      if (result.ok === false) {
        if (result.needsConfirm) row.acknowledged = false;
        throw new Error(`${index + 1}. görsel yüklenemedi: ${result.error}`);
      }
      row.image = result.image;
      row.imageUrl = result.url || result.image;
      row.pendingData = '';
      row.pendingName = '';
    }

    const written = await call(`${BASE}/slides`, {
      method: 'PUT',
      body: {
        slides: state.rows.map((row) => ({
          title: row.title.trim(), link: row.link.trim(), image: row.image,
        })),
        reason,
        dryRun: false,
      },
    });
    toast(`${num(written.count)} görsel kaydedildi.`, 'good');
    if (written.notice) toast(written.notice, 'warn');
    await refresh();
    return written;
  });
}

// ================================================================== görsel

/**
 * Görsel penceresi: gizli `<input type=file>` + sürükle-bırak + `FileReader`.
 *
 * Tauri'de dosya sistemi/dialog eklentisi YOK; dosya tarayıcıda okunup base64
 * olarak gövdeyle taşınır. Seçilen görselin ölçüsü SUNUCUDA ölçülür
 * (`/image/check`): tarayıcının bildirdiği ölçüye güvenilmez ve karar
 * "Önerilen 1920x640; yüklenen 1200x400 — mobilde bulanık." cümlesiyle
 * kutunun altında durur.
 *
 * ÖNİZLEME İKİ KARE ÇİZER — bilerek. Solda görselin ANA EKRANDAKİ hâli:
 * önerilen orana `cover` ile oturmuş, yani kırpılmış. Sağda dosyanın GERÇEK
 * oranı. Tek kare göstermek tam da uyarmaya çalıştığımız kırpmayı gizlerdi.
 */
function openImage(index) {
  const row = state.rows[index];
  if (!row) return;
  const cleaners = [];
  const box = drawer(nodes.root, {
    title: `${index + 1}. görsel`,
    subtitle: row.title || 'Bu görsele henüz bir ad verilmedi',
    onClose: () => cleaners.forEach((fn) => fn()),
  });
  closers.push(() => cleaners.forEach((fn) => fn()));

  const recommended = state.recommended;
  const frame = h('div', 'hm-drop');
  const preview = h('div', 'hm-drop-preview');
  const note = h('div', 'hm-size-note');
  const meta = h('div', 'hm-sub');

  const input = h('input', 'hm-file');
  input.type = 'file';
  input.accept = state.allowedTypes.join(',');
  input.id = `hm-file-${index}`;
  // Görsel olarak gizli ama KLAVYEYLE ULAŞILIR: `display:none` verilseydi
  // sekme tuşuyla erişilemez ve dosya seçmenin klavye yolu kalmazdı.
  const label = h('label', 'kit-btn kit-btn-primary hm-file-label', 'Dosya seç');
  label.setAttribute('for', input.id);
  label.title = 'Bilgisayarınızdan bir görsel dosyası seçin (PNG, JPG ya da WebP)';

  function paint() {
    preview.replaceChildren();
    const source = row.pendingData || row.imageUrl || '';
    if (!source) {
      preview.append(h('span', 'hm-drop-text',
        `Görseli buraya sürükleyip bırakın ya da “Dosya seç” deyin. Ana ekran için `
        + `${recommended} piksel ölçüsünde bir görsel yakışır.`));
      return;
    }
    const fresh = row.pendingData ? row.verdict : null;

    const shop = h('figure', 'hm-ratio-cell');
    const viewport = h('div', 'hm-ratio-frame');
    if (recommended) viewport.style.aspectRatio = recommended.replace(/x/i, ' / ');
    const shown = h('img');
    shown.src = source;
    shown.alt = '';
    viewport.append(shown);
    shop.append(viewport, h('figcaption', 'hm-sub',
      `MÜŞTERİ BÖYLE GÖRECEK (${recommended} piksel). Kenarları kesildiyse burada `
      + 'görürsünüz.'));
    preview.append(shop);

    const real = h('figure', 'hm-ratio-cell');
    const image = h('img', 'hm-ratio-true');
    image.src = source;
    image.alt = '';
    const size = fresh?.previewBox;
    if (size?.width) {
      // Kutu sunucunun ölçtüğü en/boydan gelir; panel oran hesabı yapmaz.
      image.style.width = `${size.width}px`;
      image.style.height = `${size.height}px`;
    }
    real.append(image, h('figcaption', 'hm-sub', fresh?.width
      ? `SEÇTİĞİNİZ DOSYA (en-boy ${fresh.aspect}) · ${num(fresh.width)}×${num(fresh.height)} piksel`
      : 'ŞU AN KAYITLI OLAN GÖRSEL'));
    preview.append(real);
  }

  function showVerdict(verdict) {
    note.replaceChildren();
    if (!verdict) {
      note.append(h('span', 'hm-sub', `Ana ekran için en uygun ölçü: ${recommended} piksel.`));
      return;
    }
    note.append(badge(SIZE_LABELS[verdict.sizeState] || verdict.sizeState,
      SIZE_TONES[verdict.sizeState] || ''));
    // ZORUNLU ALT METİN: karar her zaman yazıyla da durur, renge bırakılmaz.
    note.append(h('span', 'hm-size-text', verdict.sizeNote));
    // Kırpma ayrı cümledir: "ölçü tutmuyor" bir tespit, "soldan ve sağdan %25
    // kırpılacak" ise kullanıcının afişi yeniden kesmesini sağlayan bilgi.
    if (verdict.cropNote) note.append(h('span', 'hm-crop-text', verdict.cropNote));
  }

  async function accept(file) {
    if (!file) return;
    if (!state.allowedTypes.includes(file.type)) {
      const kinds = state.allowedTypes
        .map((item) => String(item).split('/')[1]?.toUpperCase()).filter(Boolean).join(' ya da ');
      toast(`Bu dosya türü kullanılamaz. Yalnız ${kinds} dosyası yükleyebilirsiniz — `
        + 'PDF, Word ya da başka bir dosya olmaz.', 'bad');
      return;
    }
    if (file.size > state.maxImageBytes) {
      toast(`Dosya çok büyük: ${formatBytes(file.size)}. En fazla `
        + `${formatBytes(state.maxImageBytes)} olabilir. Görseli küçültüp yeniden deneyin.`,
      'bad');
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => toast('Dosya okunamadı; bozuk olabilir. Başka bir dosya deneyin.',
      'bad');
    reader.onload = async () => {
      row.pendingData = String(reader.result || '');
      row.pendingName = file.name;
      row.acknowledged = false;
      meta.textContent = `${file.name} · ${formatBytes(file.size)}`;
      paint();
      try {
        const verdict = await call(`${BASE}/image/check`, {
          method: 'POST', body: { data: row.pendingData },
        });
        row.verdict = verdict;
        showVerdict(verdict);
        paint();                       // ölçü geldi: gerçek oranlı kare yeniden çizilir
        if (verdict.needsConfirm) {
          // ONAY BURADA KOLAYLIK, KAPI DEĞİL: sunucu `acknowledged` bayrağı
          // olmadan yüklemeyi zaten reddediyor (K9).
          const go = await confirmSimple(nodes.root, {
            title: 'Görselin ölçüsü tam tutmuyor',
            description: `${verdict.sizeNote} ${verdict.cropNote || ''}`.trim()
              + ' Yine de kullanılsın mı? İsterseniz vazgeçip görseli doğru ölçüde '
              + 'hazırlatabilirsiniz.',
            confirmLabel: 'Yine de kullan',
            danger: true,
          });
          if (!go) {
            row.pendingData = '';
            row.pendingName = '';
            row.verdict = null;
            meta.textContent = '';
            paint();
            showVerdict(null);
            renderAll();
            return;
          }
          row.acknowledged = true;
        }
        renderAll();
      } catch (error) {
        row.pendingData = '';
        row.pendingName = '';
        row.verdict = null;
        note.replaceChildren(alertBox(error.message, 'bad'));
        paint();
        renderAll();
      }
    };
    reader.readAsDataURL(file);
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
  tools.append(input, label, button('Seçtiğim dosyayı bırak', {
    variant: 'ghost',
    title: 'Seçtiğiniz dosyayı iptal eder; mağazadaki görsel değişmez',
    onClick: () => {
      row.pendingData = '';
      row.pendingName = '';
      row.verdict = null;
      row.acknowledged = false;
      meta.textContent = '';
      paint();
      showVerdict(null);
      renderAll();
    },
  }));

  frame.append(preview);
  paint();
  showVerdict(null);
  // Görseli olmayan satır kaydedilemez; nedenini ve sıradaki adımı, kullanıcı
  // "Kaydet"e basıp reddedilmeden ÖNCE söylüyoruz.
  if (!row.image && !row.pendingData) box.body.append(blockerBox('NEEDS_IMAGE', 'info'));
  box.body.append(
    card('Görsel', frame, `En uygun ölçü: ${recommended} piksel`),
    tools, meta, note,
    hintBox('Seçtiğiniz dosya “Kaydet” demeden mağazaya gitmez. Kaydettiğiniz anda '
      + 'müşteri yeni görseli görmeye başlar.'),
  );
}

// ============================================================== hedef seçici

/** Ürünü aratıp adresini kutuya yazar. Kategori/sayfa adresi elle yazılır. */
function openLinkPicker(row, input) {
  const cleaners = [];
  const box = drawer(nodes.root, {
    title: 'Tıklayınca açılacak ürün',
    subtitle: 'Ürünü seçin; adresi kutuya kendiliğinden yazılır',
    onClose: () => cleaners.forEach((fn) => fn()),
  });
  closers.push(() => cleaners.forEach((fn) => fn()));

  const field = h('input', 'kit-input');
  field.type = 'search';
  field.placeholder = 'Ürün adının bir parçasını yazın';
  field.setAttribute('aria-label', 'Ürün ara');
  const results = h('div', 'hm-target-results');

  const search = debounce(async () => {
    const query = field.value.trim();
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
      results.append(h('div', 'hm-sub',
        'Bu adda ürün bulunamadı. Adın daha kısa bir parçasını yazmayı deneyin.'));
      return;
    }
    for (const item of payload.items) {
      results.append(button(`${item.name} (${item.sku})`, {
        variant: 'ghost',
        onClick: () => {
          row.link = item.url;
          input.value = item.url;
          box.close();
          renderAll();
        },
      }));
    }
  }, 400);
  field.addEventListener('input', search);
  cleaners.push(() => search.cancel());

  box.body.append(
    card('Ürün ara', h('div', 'hm-target', field, results)),
    hintBox('Kategori ya da bilgi sayfası için adresi doğrudan kutuya yazabilirsiniz: '
      + 'kendi sitemizde kalacaksa `/kampanya` gibi, başka bir siteye gidecekse '
      + '`https://…` diye tam adresini yazın.'),
  );
}

// ========================================================== değişiklik izi

async function openHistory() {
  const box = drawer(nodes.root, {
    title: 'Değişiklik geçmişi',
    subtitle: 'Bu ekrandan yapılan yazmalar ve gerekçeleri',
  });
  box.body.replaceChildren(skeletonRows(5, 3));
  let payload;
  try {
    payload = await call(`${BASE}/audit?limit=100`);
  } catch (error) {
    box.body.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  if (!payload.items.length) {
    box.body.replaceChildren(hintBox('Bu ekrandan henüz bir değişiklik yapılmamış.'));
    return;
  }
  const list = h('div', 'hm-list');
  for (const item of payload.items) {
    const line = h('div', 'hm-slot');
    line.append(
      h('span', 'hm-handle', '·'),
      h('span', 'hm-sub', stampIso(item.createdAt)),
      clip(h('div', 'hm-slot-main'), item.reason || '(gerekçe yazılmamış)', 90),
      h('span', 'hm-sub', item.actor || ''),
      badge(item.result === 'ok' ? 'yazıldı' : item.result,
        item.result === 'ok' ? 'good' : item.result === 'hata' ? 'bad' : ''),
    );
    list.append(line);
  }
  box.body.replaceChildren(list);
}

// ------------------------------------------------------------------- çizim

function renderAll() {
  renderPreview();
  renderList();
  nodes.status?.set(statusText(), !state.connected);
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel hm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  const tools = h('div', 'hm-actions');
  tools.append(
    button('Görsel ekle', {
      variant: 'primary',
      title: 'Listenin sonuna yeni bir görsel ekler',
      onClick: addRow,
    }),
    button('Kaydet', {
      title: 'Listeyi (sıra, görseller ve adresler) mağazaya yazar',
      onClick: save,
    }),
    button('Yenile', {
      title: 'Mağazadaki güncel hâli yeniden okur; kaydedilmemiş değişiklikler gider',
      onClick: () => refresh(),
    }),
    button('Değişiklik geçmişi', {
      title: 'Bu ekrandan kimin ne zaman ne değiştirdiğini gösterir',
      onClick: () => openHistory(),
    }),
  );

  nodes.preview = h('div', 'hm-preview');
  nodes.orderbar = h('div', 'hm-orderbar');
  nodes.listWrap = h('div', 'hm-listwrap');
  nodes.status = statusLine();
  // Klavyeyle taşımanın sesli karşılığı: ekran okuyucu "3. sıraya taşındı" der.
  nodes.live = h('div', 'hm-live');
  nodes.live.setAttribute('role', 'status');
  nodes.live.setAttribute('aria-live', 'polite');

  nodes.body = h('div', 'hm-body');
  nodes.body.append(nodes.preview, nodes.orderbar, nodes.listWrap);

  view.append(
    hintBox('Bu ekranın tek işi var: siteye ilk girişte dönen görselleri değiştirmek, '
      + 'sıralarını belirlemek ve tıklanınca nereye gideceklerini seçmek.'),
    tools, nodes.status.node, nodes.live, nodes.body,
  );
  root.replaceChildren(view);

  nodes.status.set('Ana ekranda ne olduğu okunuyor…');
  refresh();

  return () => {
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE, rows: [] };
    busy = false;
  };
}
