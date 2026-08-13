// Kantin Ürünleri paneli — CANLI KANTİN VERİSİ.
//
// Kiosk'un yönetim ekranından yapılabilen her ürün işlemi burada da yapılır:
// ekleme, düzenleme, barkod, görsel, fiyat, stok, pasifleştirme. Ürünler
// kantinde durur; bu ekran kopya tutmaz, her açılışta oradan okur.
//
// SİLME YOKTUR. "Sil" demek pasifleştirmek demektir: kasada satışa çıkmaz ama
// satır ve geçmiş satışlardaki bağı korunur. Kantinde silme ucu da yoktur.

import {
  button, confirmWithReason, foldText, h, loadStyles, money, moneyInput,
  parseMoney, stampIso, toaster,
} from './kit.js';

loadStyles(import.meta.url);

let api = null;
let toast = null;

let state = { products: [], summary: {}, health: [], connected: false, error: '' };
let filters = { text: '', status: '', flaw: '' };
let sortKey = 'name';
let selected = new Set();
let editing = null;          // düzenlenen ürünün taslağı
let imageDraft = null;       // {name, base64, previewUrl}
let busy = false;

const nodes = {};

// ------------------------------------------------------------------- veri

async function refresh() {
  setStatus('Kantinden okunuyor…');
  try {
    state = await api('/api/bbd_canteen_products/products');
    setStatus(state.connected
      ? `Kantine bağlı · ${state.products.length} ürün`
      : `Kantine ulaşılamadı — ${state.error || 'bilinmeyen hata'}`, !state.connected);
  } catch (error) {
    state = { products: [], summary: {}, health: [], connected: false, error: error.message };
    setStatus(`Çekirdek hatası: ${error.message}`, true);
  }
  // Listeden düşen ürünü seçili bırakma.
  const alive = new Set(state.products.map((product) => product.id));
  for (const id of [...selected]) if (!alive.has(id)) selected.delete(id);

  renderSummary();
  renderHealth();
  renderList();
  if (editing?.id) {
    const fresh = state.products.find((product) => product.id === editing.id);
    if (fresh) openEditor(fresh);
  }
}

function visible() {
  const needle = foldText(filters.text);
  return state.products
    .filter((product) => {
      if (filters.status === 'active' && !product.isActive) return false;
      if (filters.status === 'passive' && product.isActive) return false;
      if (filters.flaw === 'no_barcode' && product.barcode) return false;
      if (filters.flaw === 'no_image' && product.imageUrl) return false;
      if (filters.flaw === 'out_of_stock' && Number(product.stock || 0) > 0) return false;
      if (!needle) return true;
      return foldText(`${product.name} ${product.barcode || ''}`).includes(needle);
    })
    .sort((a, b) => {
      if (sortKey === 'price') return Number(b.price) - Number(a.price);
      if (sortKey === 'stock') return Number(a.stock) - Number(b.stock);
      if (sortKey === 'recent') return Number(b.updatedAt || 0) - Number(a.updatedAt || 0);
      return String(a.name).localeCompare(String(b.name), 'tr');
    });
}

// ------------------------------------------------------------------ çizim

function setStatus(text, bad = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

function renderSummary() {
  const summary = state.summary || {};
  nodes.kpi.replaceChildren();
  const tiles = [
    ['Toplam ürün', String(summary.total ?? '—')],
    ['Aktif', String(summary.active ?? '—')],
    ['Pasif', String(summary.passive ?? '—')],
    ['Stoğu biten', String(summary.outOfStock ?? '—'), summary.outOfStock ? 'warn' : ''],
    ['Raftaki adet', String(summary.stockUnits ?? '—')],
    ['Stok değeri', money(summary.stockValue)],
  ];
  for (const [label, value, tone] of tiles) {
    const box = h('div', 'cp-kpi-tile');
    box.append(h('span', 'cp-kpi-label', label),
      h('b', `cp-kpi-value${tone ? ` ${tone}` : ''}`, value));
    nodes.kpi.append(box);
  }
}

function renderHealth() {
  nodes.health.replaceChildren();
  const issues = (state.health || []).filter((item) => item.products.length > 0);
  if (issues.length === 0) {
    nodes.health.append(h('span', 'cp-health-ok', 'Katalog temiz — düzeltme bekleyen ürün yok.'));
    return;
  }
  for (const issue of issues) {
    const chip = h('button', 'cp-health-chip');
    chip.type = 'button';
    chip.title = issue.hint;
    chip.append(h('span', undefined, issue.label), h('b', undefined, String(issue.products.length)));
    chip.addEventListener('click', () => {
      filters.flaw = issue.key === 'passive_with_stock' ? '' : issue.key;
      filters.status = issue.key === 'passive_with_stock' ? 'passive' : filters.status;
      nodes.flaw.value = filters.flaw;
      nodes.statusFilter.value = filters.status;
      renderList();
      toast(issue.hint, 'warn');
    });
    nodes.health.append(chip);
  }
}

function renderList() {
  const list = visible();
  nodes.list.replaceChildren();
  nodes.count.textContent = `${list.length} ürün görünüyor · ${selected.size} seçili`;
  nodes.bulkBar.hidden = selected.size === 0;
  nodes.bulkCount.textContent = `${selected.size} ürün seçili`;

  if (list.length === 0) {
    nodes.list.append(h('div', 'cp-empty', 'Filtreye uyan ürün yok.'));
    return;
  }

  for (const product of list) {
    const card = h('div', `cp-card${product.isActive ? '' : ' passive'}${editing?.id === product.id ? ' on' : ''}`);

    const check = h('input', 'cp-card-check');
    check.type = 'checkbox';
    check.checked = selected.has(product.id);
    check.title = 'Toplu işlem için seç';
    check.addEventListener('click', (event) => event.stopPropagation());
    check.addEventListener('change', () => {
      if (check.checked) selected.add(product.id);
      else selected.delete(product.id);
      renderList();
    });

    const thumb = h('div', 'cp-thumb');
    if (product.imageUrl) {
      const image = h('img');
      image.src = product.imageUrl;
      image.alt = '';
      image.loading = 'lazy';
      // Görsel gelmezse baş harfe düş — kırık ikon gösterme.
      image.addEventListener('error', () => {
        thumb.replaceChildren(h('span', 'cp-thumb-letter', (product.name || '?').charAt(0)));
      });
      thumb.append(image);
    } else {
      thumb.append(h('span', 'cp-thumb-letter', (product.name || '?').charAt(0)));
    }

    const body = h('div', 'cp-card-body');
    body.append(h('div', 'cp-card-name', product.name));

    const meta = h('div', 'cp-card-meta');
    meta.append(h('span', 'cp-price', money(product.price)));
    const stock = h('span', `cp-stock${Number(product.stock) <= 0 ? ' zero' : ''}`,
      `stok ${product.stock}`);
    meta.append(stock);
    if (!product.isActive) meta.append(h('span', 'cp-tag passive', 'pasif'));
    if (!product.barcode) meta.append(h('span', 'cp-tag muted', 'barkodsuz'));
    body.append(meta);
    if (product.barcode) body.append(h('div', 'cp-card-barcode', product.barcode));

    const quick = h('div', 'cp-card-quick');
    for (const delta of [-1, +1, +10]) {
      const node = h('button', 'cp-quick', delta > 0 ? `+${delta}` : String(delta));
      node.type = 'button';
      node.title = `Stoğu ${delta > 0 ? 'artır' : 'azalt'}`;
      node.addEventListener('click', (event) => {
        event.stopPropagation();
        quickStock(product, delta);
      });
      quick.append(node);
    }

    card.append(check, thumb, body, quick);
    card.addEventListener('click', () => openEditor(product));
    nodes.list.append(card);
  }
}

// --------------------------------------------------------------- düzenleyici

function openEditor(product) {
  editing = product ? { ...product } : {
    id: null, name: '', barcode: '', price: 0, stock: 0, isActive: true, imageUrl: null,
  };
  imageDraft = null;
  renderEditor();
  renderList();
  loadAudit(editing.id);
}

function renderEditor() {
  nodes.editor.replaceChildren();
  if (!editing) {
    nodes.editor.append(h('div', 'cp-hint',
      'Soldan bir ürüne tıklayın ya da “Yeni ürün” deyin. '
      + 'Değişiklikler kantine yazılır; kasa aynı veriyi görür.'));
    return;
  }

  const isNew = !editing.id;
  const form = h('div', 'cp-form');
  form.append(h('h3', 'cp-form-title', isNew ? 'Yeni ürün' : editing.name));

  // --- görsel ---
  const imageBox = h('div', 'cp-image');
  const preview = h('div', 'cp-image-preview');
  const source = imageDraft?.previewUrl || editing.imageUrl;
  if (source) {
    const image = h('img');
    image.src = source;
    image.alt = '';
    preview.append(image);
  } else {
    preview.append(h('span', 'cp-image-empty', 'Görsel yok'));
  }

  const file = h('input');
  file.type = 'file';
  file.accept = 'image/jpeg,image/png,image/webp';
  file.hidden = true;
  file.addEventListener('change', () => pickImage(file.files?.[0]));

  const imageActions = h('div', 'cp-image-actions');
  imageActions.append(
    button(imageDraft ? 'Başka görsel seç' : 'Görsel seç', { onClick: () => file.click() }),
  );
  if (imageDraft) {
    imageActions.append(button('Vazgeç', {
      variant: 'ghost',
      onClick: () => { imageDraft = null; renderEditor(); },
    }));
  }
  imageActions.append(h('span', 'cp-image-note',
    'JPG/PNG/WEBP · kantin 1280 piksele küçültüp kaydeder'));

  // Sürükle-bırak: dosya seçmeden de olsun.
  preview.addEventListener('dragover', (event) => {
    event.preventDefault();
    preview.classList.add('drop');
  });
  preview.addEventListener('dragleave', () => preview.classList.remove('drop'));
  preview.addEventListener('drop', (event) => {
    event.preventDefault();
    preview.classList.remove('drop');
    pickImage(event.dataTransfer?.files?.[0]);
  });

  imageBox.append(preview, imageActions, file);
  form.append(imageBox);

  // --- alanlar ---
  const field = (label, node, hint) => {
    const wrap = h('label', 'cp-field');
    wrap.append(h('span', 'cp-field-label', label), node);
    if (hint) wrap.append(h('span', 'cp-field-hint', hint));
    return wrap;
  };

  const name = h('input', 'cp-input');
  name.type = 'text';
  name.maxLength = 120;
  name.value = editing.name || '';
  name.addEventListener('input', () => { editing.name = name.value; });

  const barcode = h('input', 'cp-input');
  barcode.type = 'text';
  barcode.maxLength = 64;
  barcode.value = editing.barcode || '';
  barcode.placeholder = 'Okutun ya da yazın — boş bırakılabilir';
  barcode.addEventListener('input', () => { editing.barcode = barcode.value; });
  // Barkod okuyucu klavye taklidi yapar ve Enter ile biter: alanı kilitlemeden kaydet.
  barcode.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); save(); }
  });

  const price = h('input', 'cp-input');
  price.type = 'text';
  price.value = moneyInput(editing.price);
  price.addEventListener('input', () => { editing.priceText = price.value; });

  const stock = h('input', 'cp-input');
  stock.type = 'number';
  stock.min = '0';
  stock.value = String(editing.stock ?? 0);
  stock.addEventListener('input', () => { editing.stock = Number(stock.value); });

  const active = h('input');
  active.type = 'checkbox';
  active.checked = editing.isActive !== false;
  active.addEventListener('change', () => { editing.isActive = active.checked; });
  const activeWrap = h('label', 'cp-check');
  activeWrap.append(active, h('span', undefined, 'Kasada satışa açık'));

  form.append(
    field('Ürün adı', name),
    field('Barkod', barcode, 'Aynı barkod varsa o ürün güncellenir — çift kayıt oluşmaz.'),
    field('Fiyat (₺)', price, 'Kantin kuruş saklar; buraya 12,50 gibi yazın.'),
    field('Stok', stock, isNew ? '' : 'Buradan mutlak değer yazılır. Giriş/çıkış için aşağıdaki stok işlemini kullanın.'),
    activeWrap,
  );

  const actions = h('div', 'cp-form-actions');
  actions.append(
    button('Kaydet', { variant: 'primary', onClick: save }),
    button('Vazgeç', { onClick: () => { editing = null; imageDraft = null; renderEditor(); renderList(); } }),
  );
  if (!isNew) {
    actions.append(h('span', 'cp-spacer'));
    actions.append(button(editing.isActive === false ? 'Geri aç' : 'Pasifleştir', {
      variant: editing.isActive === false ? '' : 'danger',
      title: editing.isActive === false
        ? 'Ürünü yeniden satışa açar.'
        : 'Kasada satışa çıkmaz. Ürün SİLİNMEZ, geçmiş satışlar korunur.',
      onClick: () => toggleActive(editing),
    }));
  }
  form.append(actions);

  if (!isNew) {
    form.append(h('div', 'cp-sep', 'Stok işlemi'));
    const stockRow = h('div', 'cp-stock-row');
    const delta = h('input', 'cp-input cp-delta');
    delta.type = 'number';
    delta.value = '1';
    delta.title = 'Artı giriş, eksi çıkış';
    const reason = h('input', 'cp-input');
    reason.type = 'text';
    reason.placeholder = 'Sebep (zorunlu) — örn. mal kabul';
    reason.maxLength = 255;
    stockRow.append(delta, reason, button('Uygula', {
      onClick: () => {
        const amount = Number(delta.value);
        if (!amount) { toast('Değişim sıfır olamaz.', 'warn'); return; }
        if (reason.value.trim().length < 2) { toast('Sebep yazın.', 'warn'); return; }
        adjustStock(editing, amount, reason.value.trim());
      },
    }));
    form.append(stockRow);

    form.append(h('div', 'cp-sep', 'Değişiklik günlüğü'));
    nodes.audit = h('div', 'cp-audit');
    form.append(nodes.audit);
  }

  nodes.editor.append(form);
}

function pickImage(file) {
  if (!file) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
    toast('Yalnız JPG, PNG veya WEBP yüklenebilir.', 'warn');
    return;
  }
  if (file.size > 24 * 1024 * 1024) {
    toast('Görsel 24 MB\'ı aşamaz.', 'warn');
    return;
  }
  const reader = new FileReader();
  reader.addEventListener('load', () => {
    const result = String(reader.result || '');
    imageDraft = {
      name: file.name,
      base64: result.split(',')[1] || '',
      previewUrl: result,
    };
    renderEditor();
  });
  reader.readAsDataURL(file);
}

async function loadAudit(productId) {
  if (!productId || !nodes.audit) return;
  try {
    const payload = await api(`/api/bbd_canteen_products/audit?productId=${productId}&limit=25`);
    const entries = payload.entries || [];
    nodes.audit.replaceChildren();
    if (entries.length === 0) {
      nodes.audit.append(h('div', 'cp-audit-empty', 'Bu ürün için kayıt yok.'));
      return;
    }
    for (const entry of entries) {
      const row = h('div', 'cp-audit-row');
      row.append(h('span', 'cp-audit-action', LABELS[entry.action] || entry.action));
      row.append(h('span', 'cp-audit-diff', describeDiff(entry)));
      row.append(h('span', 'cp-audit-when', `${stampIso(entry.created_at)}${entry.actor ? ` · ${entry.actor}` : ''}`));
      nodes.audit.append(row);
    }
  } catch (error) {
    console.warn('günlük okunamadı', error);
  }
}

const LABELS = {
  create: 'eklendi', update: 'düzenlendi', stock: 'stok', price_bulk: 'toplu fiyat',
  deactivate: 'pasifleştirildi', activate: 'geri açıldı',
};

/** İki hâli karşılaştırıp yalnız DEĞİŞEN alanları yazar. */
function describeDiff(entry) {
  let before = {};
  let after = {};
  try { before = JSON.parse(entry.before_json || '{}'); } catch { /* boş */ }
  try { after = JSON.parse(entry.after_json || '{}'); } catch { /* boş */ }

  const bits = [];
  const format = (key, value) => {
    if (key === 'price') return money(value);
    if (key === 'isActive') return value ? 'aktif' : 'pasif';
    return String(value ?? '—');
  };
  for (const key of ['name', 'barcode', 'price', 'stock', 'isActive']) {
    if (!(key in after)) continue;
    if (String(before[key] ?? '') === String(after[key] ?? '')) continue;
    const label = { name: 'ad', barcode: 'barkod', price: 'fiyat', stock: 'stok', isActive: 'durum' }[key];
    bits.push(Object.hasOwn(before, key)
      ? `${label}: ${format(key, before[key])} → ${format(key, after[key])}`
      : `${label}: ${format(key, after[key])}`);
  }
  if (entry.note) bits.push(entry.note);
  return bits.join(' · ') || '—';
}

// ------------------------------------------------------------- eylemler

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  setStatus(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    setStatus(error.message, true);
    return null;
  } finally {
    busy = false;
  }
}

async function save() {
  if (!editing) return;
  const price = editing.priceText !== undefined
    ? parseMoney(editing.priceText)
    : Number(editing.price);

  if (!String(editing.name || '').trim()) { toast('Ürün adı boş olamaz.', 'warn'); return; }
  if (price === null || price < 1) { toast('Fiyatı 12,50 gibi yazın.', 'warn'); return; }

  await withBusy('Kantine yazılıyor…', async () => {
    const body = {
      id: editing.id || undefined,
      name: String(editing.name).trim(),
      barcode: String(editing.barcode || '').trim() || undefined,
      price,
      stock: Number(editing.stock ?? 0),
      isActive: editing.isActive !== false,
    };
    if (imageDraft) {
      body.imageBase64 = imageDraft.base64;
      body.imageName = imageDraft.name;
    }
    const result = await api('/api/bbd_canteen_products/products', { method: 'POST', body });
    if (!result.ok) { toast(result.error || 'Kaydedilemedi.', 'bad'); return; }

    const imageNote = {
      stored: ' Görsel yüklendi.',
      invalid: ' Görsel reddedildi (biçim/boyut) — ürün yine kaydedildi.',
      upload_failed: ' Görsel yüklenemedi — ürün yine kaydedildi.',
      store_failed: ' Görsel diske yazılamadı — ürün yine kaydedildi.',
    }[result.imageStatus] || '';
    toast(`Ürün kaydedildi.${imageNote}`,
      result.imageStatus && result.imageStatus !== 'stored' && result.imageStatus !== 'none'
        ? 'warn' : 'good');

    imageDraft = null;
    editing = result.product;
    await refresh();
  });
}

async function toggleActive(product) {
  const turningOff = product.isActive !== false;
  const reason = await confirmWithReason(nodes.root, {
    title: turningOff ? 'Ürünü pasifleştir' : 'Ürünü geri aç',
    description: turningOff
      ? `“${product.name}” kasada satışa çıkmayacak. ÜRÜN SİLİNMEZ: satırı, `
        + 'geçmiş satışlardaki bağı ve raporlardaki payı olduğu gibi kalır. '
        + 'İstediğiniz an geri açabilirsiniz.'
      : `“${product.name}” yeniden satışa açılacak.`,
    confirmLabel: turningOff ? 'Pasifleştir' : 'Geri aç',
    placeholder: 'Gerekçe — günlüğe yazılır',
    danger: turningOff,
  });
  if (reason === null) return;

  await withBusy('Uygulanıyor…', async () => {
    const result = await api(`/api/bbd_canteen_products/products/${product.id}/active`, {
      method: 'PUT', body: { isActive: !turningOff, note: reason },
    });
    if (!result.ok) { toast(result.error || 'Uygulanamadı.', 'bad'); return; }
    toast(turningOff ? 'Ürün pasifleştirildi.' : 'Ürün geri açıldı.', 'good');
    editing = result.product;
    await refresh();
  });
}

async function quickStock(product, delta) {
  await adjustStock(product, delta, delta > 0 ? 'Hızlı stok girişi' : 'Hızlı stok çıkışı');
}

async function adjustStock(product, delta, reason) {
  await withBusy('Stok yazılıyor…', async () => {
    const result = await api(`/api/bbd_canteen_products/products/${product.id}/stock`, {
      method: 'POST', body: { delta, reason },
    });
    if (!result.ok) { toast(result.error || 'Stok yazılamadı.', 'bad'); return; }
    toast(`${product.name}: stok ${result.newStock} oldu (${delta > 0 ? '+' : ''}${delta}).`, 'good');
    await refresh();
  });
}

async function bulkPrice() {
  const raw = window.prompt(
    'Toplu fiyat güncelleme\n\n'
    + 'Yüzde için: %10 (zam) veya %-15 (indirim)\n'
    + 'Sabit tutar için: +2,50 veya -1,00 (₺)',
    '%10');
  if (!raw) return;

  const text = raw.trim();
  let body = { products: [...selected], dryRun: true };
  if (text.startsWith('%')) {
    const percent = Number(text.slice(1).replace(',', '.'));
    if (!Number.isFinite(percent)) { toast('Yüzde çözülemedi.', 'warn'); return; }
    body.percent = percent;
  } else {
    const amount = parseMoney(text.replace('+', '')) * (text.startsWith('-') ? -1 : 1);
    if (!Number.isFinite(amount) || amount === 0) { toast('Tutar çözülemedi.', 'warn'); return; }
    body.amount = amount;
  }

  const dry = await withBusy('Önizleniyor…', () =>
    api('/api/bbd_canteen_products/bulk-price', { method: 'POST', body }));
  if (!dry?.ok) return;

  const lines = dry.rows.slice(0, 12)
    .map((row) => `  ${row.name}: ${money(row.old)} → ${money(row.new)}`
      + (row.status === 'skipped' ? '  (atlanır)' : ''))
    .join('\n');
  const more = dry.rows.length > 12 ? `\n  … ve ${dry.rows.length - 12} ürün daha` : '';

  const confirmed = await confirmWithReason(nodes.root, {
    title: 'Toplu fiyat güncelleme',
    description: `${dry.rows.length} ürünün fiyatı değişecek:\n${lines}${more}`,
    confirmLabel: 'Uygula',
    placeholder: 'Gerekçe — günlüğe yazılır',
    danger: false,
  });
  if (confirmed === null) return;

  await withBusy('Fiyatlar yazılıyor…', async () => {
    const result = await api('/api/bbd_canteen_products/bulk-price', {
      method: 'POST', body: { ...body, dryRun: false },
    });
    toast(`${result.applied} ürün güncellendi${result.skipped ? `, ${result.skipped} atlandı` : ''}.`,
      result.skipped ? 'warn' : 'good');
    selected.clear();
    await refresh();
  });
}

/**
 * Görsel sağlığı — her `imageUrl` gerçekten yükleniyor mu?
 *
 * Veritabanında yol yazıyor olması dosyanın durduğu anlamına gelmez: disk
 * dolabilir, kalıcı disk yanlış bağlanmış olabilir, dosya elle silinmiş
 * olabilir. Kırık görsel kasada ürünü tanınmaz yapar; sessiz kalmamalı.
 */
async function auditImages() {
  const withImage = state.products.filter((product) => product.imageUrl);
  if (withImage.length === 0) {
    toast('Görseli olan ürün yok.', 'warn');
    return;
  }

  setStatus(`${withImage.length} görsel sınanıyor…`);
  const broken = [];
  // Aynı anda en çok 6 istek: sunucuyu boğmadan makul hızda.
  const queue = [...withImage];
  const probe = (product) => new Promise((resolve) => {
    const image = new Image();
    const done = (ok) => { image.onload = image.onerror = null; resolve(ok); };
    image.onload = () => done(true);
    image.onerror = () => done(false);
    // 10 saniyede yanıt yoksa kırık say.
    setTimeout(() => done(false), 10000);
    image.src = product.imageUrl;
  });

  await Promise.all(Array.from({ length: Math.min(6, queue.length) }, async () => {
    while (queue.length) {
      const product = queue.shift();
      if (!(await probe(product))) broken.push(product);
    }
  }));

  if (broken.length === 0) {
    toast(`${withImage.length} görselin hepsi yükleniyor.`, 'good');
    setStatus(`Görsel denetimi temiz — ${withImage.length} görsel sağlam.`);
    return;
  }

  toast(`${broken.length} ürünün görseli yüklenemedi.`, 'bad');
  setStatus(`Görsel denetimi: ${broken.length} kırık / ${withImage.length}`, true);
  // Kırıkları listeye getir ki tek tek düzeltilebilsin.
  filters.text = '';
  filters.status = '';
  filters.flaw = '';
  const brokenIds = new Set(broken.map((product) => product.id));
  state.products = state.products.map((product) => (
    brokenIds.has(product.id) ? { ...product, imageUrl: null } : product));
  renderList();
}

/** Excel'in Türkçe yerelinde doğru açılsın diye BOM + noktalı virgül. */
function exportCsv() {
  const rows = visible();
  const head = ['id', 'barkod', 'ad', 'fiyat_kurus', 'fiyat_tl', 'stok', 'aktif', 'gorsel'];
  const body = rows.map((product) => [
    product.id, product.barcode || '', product.name, product.price,
    (product.price / 100).toFixed(2).replace('.', ','),
    product.stock, product.isActive ? 'evet' : 'hayir', product.imageUrl || '',
  ]);
  const csv = [head, ...body]
    .map((line) => line.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(';'))
    .join('\r\n');

  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = h('a');
  link.href = url;
  link.download = `kantin-urunler-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
  toast(`${rows.length} ürün CSV olarak indirildi.`, 'good');
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  api = ctx.api;

  const view = h('div', 'cp');
  nodes.root = view;
  toast = toaster(view);

  nodes.kpi = h('div', 'cp-kpi');
  nodes.health = h('div', 'cp-health');

  // --- şerit ---
  const bar = h('div', 'cp-bar');
  nodes.search = h('input', 'cp-search');
  nodes.search.type = 'search';
  nodes.search.placeholder = 'Ürün ara — ad veya barkod';
  nodes.search.addEventListener('input', () => { filters.text = nodes.search.value; renderList(); });

  nodes.statusFilter = h('select', 'cp-select');
  for (const [value, label] of [['', 'Tümü'], ['active', 'Aktif'], ['passive', 'Pasif']]) {
    const option = h('option', undefined, label);
    option.value = value;
    nodes.statusFilter.append(option);
  }
  nodes.statusFilter.addEventListener('change', () => {
    filters.status = nodes.statusFilter.value;
    renderList();
  });

  nodes.flaw = h('select', 'cp-select');
  for (const [value, label] of [
    ['', 'Eksik filtresi yok'],
    ['no_barcode', 'Barkodsuz'],
    ['no_image', 'Görselsiz'],
    ['out_of_stock', 'Stoğu biten'],
  ]) {
    const option = h('option', undefined, label);
    option.value = value;
    nodes.flaw.append(option);
  }
  nodes.flaw.addEventListener('change', () => { filters.flaw = nodes.flaw.value; renderList(); });

  nodes.sort = h('select', 'cp-select');
  for (const [value, label] of [
    ['name', 'Ada göre'], ['price', 'Fiyat (yüksek)'],
    ['stock', 'Stok (düşük)'], ['recent', 'Son değişen'],
  ]) {
    const option = h('option', undefined, label);
    option.value = value;
    nodes.sort.append(option);
  }
  nodes.sort.addEventListener('change', () => { sortKey = nodes.sort.value; renderList(); });

  bar.append(nodes.search, nodes.statusFilter, nodes.flaw, nodes.sort,
    h('span', 'cp-spacer'),
    button('Yenile', { onClick: refresh }),
    button('CSV indir', { title: 'Görünen listeyi Excel uyumlu CSV olarak indirir.', onClick: exportCsv }),
    button('Görsel denetimi', {
      title: 'Her ürün görselinin sunucudan gerçekten yüklenip yüklenmediğini sınar.',
      onClick: auditImages,
    }),
    button('Yeni ürün', { variant: 'primary', onClick: () => openEditor(null) }));

  nodes.status = h('div', 'cp-status');
  nodes.count = h('div', 'cp-count');

  // --- toplu işlem şeridi ---
  nodes.bulkBar = h('div', 'cp-bulk');
  nodes.bulkCount = h('span', 'cp-bulk-count');
  nodes.bulkBar.append(nodes.bulkCount,
    button('Toplu fiyat…', { onClick: bulkPrice }),
    button('Seçimi temizle', { variant: 'ghost', onClick: () => { selected.clear(); renderList(); } }));
  nodes.bulkBar.hidden = true;

  // --- gövde ---
  const split = h('div', 'cp-split');
  nodes.list = h('div', 'cp-list');
  nodes.editor = h('div', 'cp-editor');
  split.append(nodes.list, nodes.editor);

  view.append(nodes.kpi, nodes.health, bar, nodes.count, nodes.bulkBar, nodes.status, split);
  root.replaceChildren(view);

  renderEditor();
  refresh();

  return () => {
    root.replaceChildren();
    editing = null;
    imageDraft = null;
    selected.clear();
    busy = false;
  };
}
