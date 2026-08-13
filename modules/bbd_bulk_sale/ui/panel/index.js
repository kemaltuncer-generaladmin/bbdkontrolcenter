// Toplu Satış paneli — CANLI KANTİN VERİSİ.
//
// İKİ KİP:
//   · Aynı sepet herkese — öğrencileri seç, tek sepet kur, hepsine işle.
//   · Öğrenci başına ayrı sepet — öğrenci seç, sepetini kur, kuyruğa ekle;
//     sonunda hepsi tek seferde işlenir. ("Profiline girip ürün ekleme" akışı.)
//
// Tarih seçilebilir: kasada sorun yaşanan güne geriye dönük yazmak için.
// Gönderim öncesi ön izleme zorunludur — engel, limit, stok ve tutar görünür.

import { createCart } from './cart.js';
import { dateField } from './datefield.js';
import {
  button, confirmWithReason, h, loadStyles, longDate, money, stampIso, toaster, todayIso,
} from './kit.js';
import { createPicker } from './picker.js';

loadStyles(import.meta.url);

let api = null;
let capability = null;
let toast = null;

let state = { students: [], products: [], presets: [], batches: [], connected: false, error: '' };
let mode = 'shared';
let queue = [];                 // per_student kipi: [{kantinId, name, items, amount}]
let preview = null;
let saleDate = todayIso();
let classMap = new Map();
let busy = false;

const nodes = {};
let picker = null;
let cart = null;

// ------------------------------------------------------------------- veri

async function loadClasses() {
  const provider = capability?.('bbd_students.list');
  if (!provider) return;
  try {
    const list = await provider();
    classMap = new Map(list.map((student) => [student.id, student.className || '']));
    picker.setClasses(classMap);
  } catch (error) {
    console.warn('sınıf bilgisi alınamadı', error);
  }
}

async function refresh() {
  setStatus('Kantinden okunuyor…');
  try {
    state = await api('/api/bbd_bulk_sale/workspace');
    picker.setStudents(state.students || [], classMap);
    cart.setProducts(state.products || []);
    setStatus(state.connected
      ? `Kantine bağlı · ${state.students.length} öğrenci · ${state.products.length} aktif ürün`
      : `Kantine ulaşılamadı — ${state.error || 'bilinmeyen hata'}`, !state.connected);
  } catch (error) {
    setStatus(`Çekirdek hatası: ${error.message}`, true);
  }
  renderPresets();
  renderBatches();
}

// ------------------------------------------------------------------ çizim

function setStatus(text, bad = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

function payload(extra = {}) {
  const base = { date: saleDate, mode, note: nodes.note.value.trim(), ...extra };
  if (mode === 'per_student') {
    return {
      ...base,
      orders: queue.map((order) => ({
        kantinId: order.kantinId,
        items: order.items.map(({ productId, qty, unitPrice }) => ({ productId, qty, unitPrice })),
      })),
    };
  }
  return {
    ...base,
    students: picker.selection(),
    cart: cart.lines().map(({ productId, qty, unitPrice }) => ({ productId, qty, unitPrice })),
  };
}

function renderMode() {
  for (const [key, tab] of Object.entries(nodes.modeTabs)) {
    tab.classList.toggle('on', key === mode);
  }
  nodes.sharedHint.hidden = mode !== 'shared';
  nodes.queueBox.hidden = mode !== 'per_student';
  nodes.queueBtn.hidden = mode !== 'per_student';
  nodes.pickerBox.classList.toggle('single', mode === 'per_student');
  preview = null;
  renderPreview();
}

function renderQueue() {
  nodes.queue.replaceChildren();
  if (queue.length === 0) {
    nodes.queue.append(h('div', 'bs-hint',
      'Kuyruk boş. Bir öğrenci seçip sepetini kurun, sonra “Kuyruğa ekle” deyin. '
      + 'Her öğrenciye farklı ürün girebilirsiniz.'));
    nodes.queueTotal.textContent = '';
    return;
  }

  for (const order of queue) {
    const row = h('div', 'bs-queue-row');
    row.append(h('span', 'bs-queue-name', order.name));
    row.append(h('span', 'bs-queue-items',
      order.items.map((line) => `${line.qty}× ${line.name}`).join(', ')));
    row.append(h('span', 'bs-queue-amount', money(order.amount)));

    const edit = h('button', 'bs-queue-act', 'sepete al');
    edit.type = 'button';
    edit.title = 'Bu sepeti düzenlemek için geri yükler ve kuyruktan çıkarır.';
    edit.addEventListener('click', () => {
      cart.load(order.items);
      picker.select([order.kantinId]);
      queue = queue.filter((item) => item !== order);
      preview = null;
      renderQueue();
      renderPreview();
    });

    const drop = h('button', 'bs-queue-act danger', 'çıkar');
    drop.type = 'button';
    drop.addEventListener('click', () => {
      queue = queue.filter((item) => item !== order);
      preview = null;
      renderQueue();
      renderPreview();
    });

    row.append(edit, drop);
    nodes.queue.append(row);
  }

  const sum = queue.reduce((acc, order) => acc + order.amount, 0);
  nodes.queueTotal.replaceChildren(
    h('span', undefined, `${queue.length} öğrenci`),
    h('b', undefined, money(sum)),
  );
}

function renderPreview() {
  nodes.preview.replaceChildren();
  const ready = mode === 'per_student'
    ? queue.length > 0
    : picker.size() > 0 && cart.size() > 0;

  nodes.previewBtn.disabled = busy || !ready;
  nodes.commit.disabled = busy || !preview?.ok || !(preview?.summary?.eligible > 0);

  if (!preview) {
    nodes.preview.append(h('div', 'bs-hint',
      mode === 'per_student'
        ? 'Kuyruğu hazırlayın, sonra “Ön izleme” deyin — kimin ne alacağı, '
          + 'kimin limiti aşacağı ve stoğun yetip yetmediği gönderilmeden görünür.'
        : 'Öğrencileri seçip sepeti kurun, sonra “Ön izleme” deyin — kimin işleneceği, '
          + 'toplam tutar ve stok yeterliliği gönderilmeden görünür.'));
    return;
  }

  if (!preview.ok) {
    nodes.preview.append(h('div', 'bs-alert bad', preview.error));
    return;
  }

  const summary = preview.summary;
  const tiles = h('div', 'bs-tiles');
  const tile = (label, value, tone = '') => {
    const box = h('div', 'bs-tile');
    box.append(h('span', 'bs-tile-label', label),
      h('b', `bs-tile-value${tone ? ` ${tone}` : ''}`, value));
    return box;
  };
  tiles.append(
    tile('İşlenecek', `${summary.eligible} öğrenci`),
    tile('Toplam tutar', money(summary.totalAmount), 'strong'),
    tile('Tarih', longDate(summary.date).split(' ').slice(0, 3).join(' ')),
    tile('Stok eksiği', summary.stockShort ? `${summary.stockShort} adet` : 'yok',
      summary.stockShort ? 'warn' : ''),
  );
  nodes.preview.append(tiles);

  if ((preview.stock || []).length > 0) {
    const box = h('div', 'bs-stock');
    box.append(h('div', 'bs-stock-head', 'Stok karşılığı'));
    for (const row of preview.stock) {
      const line = h('div', `bs-stock-row${row.short ? ' short' : ''}`);
      line.append(
        h('span', 'bs-stock-name', row.name),
        h('span', 'bs-stock-need', `${row.need} gerekli`),
        h('span', 'bs-stock-have', `${row.have} var`),
        h('span', 'bs-stock-flag', row.short ? `${row.short} eksik` : 'yeterli'),
      );
      box.append(line);
    }
    nodes.preview.append(box);
  }

  const problems = preview.rows.filter((row) => row.verdict !== 'ok');
  if (problems.length > 0) {
    const box = h('div', 'bs-problems');
    box.append(h('div', 'bs-problems-head', `${problems.length} öğrenci işlenemeyecek`));
    for (const row of problems) {
      const line = h('div', `bs-problem ${row.verdict}`);
      line.append(h('span', 'bs-problem-name', row.name || row.kantinId),
        h('span', 'bs-problem-msg', row.message));
      box.append(line);
    }
    nodes.preview.append(box);
  }

  if (summary.eligible > 0) {
    nodes.preview.append(h('div', 'bs-ready',
      `${summary.eligible} öğrenciye toplam ${money(summary.totalAmount)} işlenecek.`));
  }
}

function renderPresets() {
  nodes.presets.replaceChildren();
  if ((state.presets || []).length === 0) {
    nodes.presets.append(h('span', 'bs-preset-empty',
      'Kayıtlı sepet yok — sepeti kurup “Şablon yap” diyebilirsiniz.'));
    return;
  }
  for (const preset of state.presets) {
    const chip = h('span', 'bs-preset');
    const load = h('button', 'bs-preset-load', preset.name);
    load.type = 'button';
    load.title = preset.cart.map((line) => `${line.qty}× ${line.name || line.productId}`).join(', ');
    load.addEventListener('click', () => {
      cart.load(preset.cart);
      toast(`“${preset.name}” sepete yüklendi.`, 'good');
    });
    const drop = h('button', 'bs-preset-drop', '×');
    drop.type = 'button';
    drop.title = 'Şablonu sil (sepet şablonu kısayoldur, satış verisi değildir)';
    drop.addEventListener('click', async () => {
      await api(`/api/bbd_bulk_sale/presets/${preset.id}`, { method: 'DELETE' });
      await refresh();
    });
    chip.append(load, drop);
    nodes.presets.append(chip);
  }
}

function renderBatches() {
  nodes.batches.replaceChildren();
  const batches = state.batches || [];
  if (batches.length === 0) {
    nodes.batches.append(h('div', 'bs-hint', 'Henüz işlenmiş parti yok.'));
    return;
  }

  for (const batch of batches) {
    const card = h('div', 'bs-batch');
    const head = h('div', 'bs-batch-head');
    head.append(
      h('span', 'bs-batch-title',
        `${longDate(batch.sale_date)} · ${batch.ok_count} öğrenci`),
      h('span', 'bs-batch-meta',
        `${money(batch.total_amount)}${batch.fail_count ? ` · ${batch.fail_count} hata` : ''} · `
        + `${batch.mode === 'per_student' ? 'ayrı sepet' : 'ortak sepet'} · `
        + `${stampIso(batch.created_at)}${batch.created_by ? ` · ${batch.created_by}` : ''}`),
      h('span', 'bs-spacer'),
    );

    const openBtn = button('Aç', {
      onClick: async () => {
        if (card.dataset.open === '1') {
          card.dataset.open = '0';
          card.querySelector('.bs-entries')?.remove();
          return;
        }
        const detail = await api(`/api/bbd_bulk_sale/batches/${batch.batch_ref}`);
        if (!detail.ok) return;
        card.dataset.open = '1';
        card.append(renderEntries(batch, detail.entries || []));
      },
    });
    head.append(openBtn);
    if (batch.ok_count > 0) {
      head.append(button('Partiyi geri al', {
        variant: 'danger',
        title: 'Kantinde ters kayıt oluşur; satırlar silinmez.',
        onClick: () => reverse({ batchRef: batch.batch_ref, count: batch.ok_count }),
      }));
    }
    card.append(head);
    if (batch.note) card.append(h('div', 'bs-batch-note', batch.note));
    nodes.batches.append(card);
  }
}

function renderEntries(batch, entries) {
  const list = h('div', 'bs-entries');
  for (const entry of entries) {
    const row = h('div', `bs-entry ${entry.reversed_at ? 'undone' : entry.status}`);
    row.append(h('span', 'bs-entry-name', entry.student_name || entry.kantin_id));
    row.append(h('span', 'bs-entry-items',
      (entry.items || []).map((line) => `${line.qty}× ${line.name}`).join(', ')));
    row.append(h('span', 'bs-entry-amount', money(entry.amount)));
    row.append(h('span', 'bs-entry-state', entry.reversed_at
      ? `geri alındı — ${entry.reversed_reason}`
      : ({ created: 'işlendi', duplicate: 'zaten vardı', pending: 'gönderilmedi' }[entry.status]
        || `hata: ${entry.reason || 'bilinmiyor'}`)));

    if (!entry.reversed_at && ['created', 'duplicate'].includes(entry.status)) {
      const undo = h('button', 'bs-undo', 'geri al');
      undo.type = 'button';
      undo.addEventListener('click', () => reverse({
        localId: entry.local_id, count: 1, who: entry.student_name,
      }));
      row.append(undo);
    }
    list.append(row);
  }
  return list;
}

// ------------------------------------------------------------- eylemler

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.previewBtn.disabled = true;
  nodes.commit.disabled = true;
  setStatus(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    setStatus(error.message, true);
    return null;
  } finally {
    busy = false;
    renderPreview();
  }
}

function enqueue() {
  const ids = picker.selection();
  if (ids.length !== 1) {
    toast('Bu kipte tek öğrenci seçin — her öğrencinin sepeti ayrıdır.', 'warn');
    return;
  }
  if (cart.size() === 0) {
    toast('Önce sepete ürün ekleyin.', 'warn');
    return;
  }
  const kantinId = ids[0];
  const student = state.students.find((item) => item.kantinId === kantinId);
  queue = queue.filter((order) => order.kantinId !== kantinId);
  queue.push({
    kantinId,
    name: student?.displayName || kantinId,
    items: cart.lines(),
    amount: cart.amount(),
  });
  cart.clear();
  picker.clear();
  preview = null;
  renderQueue();
  renderPreview();
  toast(`${student?.displayName || kantinId} kuyruğa eklendi.`, 'good');
}

async function runPreview() {
  await withBusy('Ön izleme hazırlanıyor…', async () => {
    preview = await api('/api/bbd_bulk_sale/preview', { method: 'POST', body: payload() });
    renderPreview();
    setStatus(preview.ok
      ? `${preview.summary.eligible} öğrenci işlenmeye hazır — ${money(preview.summary.totalAmount)}.`
      : preview.error, !preview.ok);
  });
}

async function commit() {
  if (!preview?.ok) return;
  const summary = preview.summary;

  const note = await confirmWithReason(nodes.root, {
    title: 'Toplu satışı işle',
    description: `${longDate(saleDate)} · ${summary.eligible} öğrenci · toplam `
      + `${money(summary.totalAmount)}. Kayıtlar kantinde kasada girilmiş gibi oluşur ve `
      + 'öğrencilerin cari borcuna yazılır. Kısa bir açıklama yazın.',
    confirmLabel: 'İşle',
    placeholder: 'Örn. kasa arızası — 13 Ağustos satışları',
    danger: false,
  });
  if (note === null) return;

  await withBusy('Kantine işleniyor…', async () => {
    const result = await api('/api/bbd_bulk_sale/commit', {
      method: 'POST', body: payload({ note }),
    });
    if (!result.ok || result.committed === false) {
      toast(result.error || 'İşlenemedi.', 'bad');
      return;
    }
    toast(`${result.okCount} satış işlendi${result.failCount ? `, ${result.failCount} hata` : ''}.`,
      result.failCount ? 'warn' : 'good');
    preview = null;
    queue = [];
    cart.clear();
    picker.clear();
    nodes.note.value = '';
    renderQueue();
    await refresh();
    switchTab('history');
  });
}

async function reverse({ batchRef, localId, count, who }) {
  const reason = await confirmWithReason(nodes.root, {
    title: localId ? 'Satışı geri al' : 'Partiyi geri al',
    description: localId
      ? `${who || 'Öğrenci'} için işlenen satış geri alınacak. Kantinde ters cari kayıt `
        + 'oluşur, stok iade edilir. HİÇBİR SATIR SİLİNMEZ.'
      : `${count} satış geri alınacak. Her biri için ters cari kayıt oluşur ve stok iade `
        + 'edilir. HİÇBİR SATIR SİLİNMEZ.',
    confirmLabel: 'Geri al',
  });
  if (reason === null) return;

  await withBusy('Geri alınıyor…', async () => {
    const result = await api('/api/bbd_bulk_sale/reverse', {
      method: 'POST', body: { batchRef, localId, reason },
    });
    const failed = (result.failures || []).length;
    toast(`${result.reversed} satış geri alındı${failed ? `, ${failed} başarısız` : ''}.`,
      failed ? 'warn' : 'good');
    await refresh();
  });
}

async function savePreset() {
  if (cart.size() === 0) {
    toast('Önce sepeti kurun.', 'warn');
    return;
  }
  const name = window.prompt('Şablon adı', 'Kahvaltı paketi');
  if (!name) return;
  const result = await api('/api/bbd_bulk_sale/presets', {
    method: 'PUT', body: { name, cart: cart.lines() },
  });
  if (!result.ok) { toast(result.error || 'Kaydedilemedi.', 'bad'); return; }
  toast(`“${result.name}” şablonu kaydedildi.`, 'good');
  await refresh();
}

function switchTab(name) {
  for (const [key, tab] of Object.entries(nodes.tabs)) tab.classList.toggle('on', key === name);
  nodes.paneWork.hidden = name !== 'work';
  nodes.paneHistory.hidden = name !== 'history';
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'bs');
  nodes.root = view;
  toast = toaster(view);

  // --- şerit ---
  const bar = h('div', 'bs-bar');
  // Yerli `<input type=date>` WebKitGTK'da açılır takvimi kapatmıyor; alan
  // takılı kalıyordu. Kendi takvimimiz kullanılıyor.
  nodes.date = dateField({
    value: saleDate,
    label: 'Satış günü',
    onChange: (iso) => {
      saleDate = iso || todayIso();
      preview = null;
      renderPreview();
    },
  });
  nodes.date.node.title =
    'Satışın işleneceği gün. Geçmiş güne yazılabilir (kasa arızası vb.).';

  nodes.modeTabs = {};
  const modeBox = h('div', 'bs-mode');
  for (const [key, label, hint] of [
    ['shared', 'Aynı sepet herkese', 'Seçili tüm öğrencilere aynı ürünler işlenir.'],
    ['per_student', 'Öğrenci başına ayrı sepet', 'Her öğrenci için ayrı sepet kurup kuyruğa alın.'],
  ]) {
    const tab = h('button', 'bs-mode-tab', label);
    tab.type = 'button';
    tab.title = hint;
    tab.addEventListener('click', () => { mode = key; renderMode(); });
    nodes.modeTabs[key] = tab;
    modeBox.append(tab);
  }

  bar.append(h('span', 'bs-field-label', 'Tarih'), nodes.date.node, modeBox,
    h('span', 'bs-spacer'), button('Yenile', { onClick: refresh }));
  nodes.status = h('div', 'bs-status');

  // --- sekmeler ---
  const tabBar = h('div', 'bs-tabs');
  nodes.tabs = {};
  for (const [key, label] of [['work', 'Satış girişi'], ['history', 'Geçmiş partiler']]) {
    const tab = h('button', 'bs-tab', label);
    tab.type = 'button';
    tab.addEventListener('click', () => switchTab(key));
    nodes.tabs[key] = tab;
    tabBar.append(tab);
  }

  // --- çalışma alanı ---
  nodes.paneWork = h('div', 'bs-pane bs-pane-work');

  picker = createPicker({ onChange: () => { preview = null; renderPreview(); } });
  nodes.pickerBox = h('div', 'bs-picker');
  nodes.sharedHint = h('div', 'bs-col-hint',
    'Birden çok öğrenci seçebilirsiniz — sınıf başlığından toplu seçim yapılır.');
  nodes.pickerBox.append(h('div', 'bs-col-title', 'Öğrenciler'), nodes.sharedHint, picker.node);

  cart = createCart({ onChange: () => { preview = null; renderPreview(); } });
  const cartCol = h('div', 'bs-cart-col');
  nodes.presets = h('div', 'bs-presets');
  nodes.queueBtn = button('Kuyruğa ekle', {
    variant: 'primary',
    title: 'Seçili öğrenciyi bu sepetle kuyruğa alır.',
    onClick: enqueue,
  });
  const cartTools = h('div', 'bs-cart-tools');
  cartTools.append(
    button('Şablon yap', { title: 'Sepeti kayıtlı şablon olarak saklar.', onClick: savePreset }),
    button('Sepeti boşalt', { variant: 'ghost', onClick: () => cart.clear() }),
    nodes.queueBtn,
  );
  cartCol.append(h('div', 'bs-col-title', 'Sepet'), nodes.presets, cart.node, cartTools);

  nodes.queueBox = h('div', 'bs-queue-box');
  nodes.queue = h('div', 'bs-queue');
  nodes.queueTotal = h('div', 'bs-queue-total');
  nodes.queueBox.append(h('div', 'bs-col-title', 'Kuyruk'), nodes.queue, nodes.queueTotal);

  const sideCol = h('div', 'bs-side');
  nodes.preview = h('div', 'bs-preview');
  nodes.note = h('input', 'bs-input');
  nodes.note.type = 'text';
  nodes.note.placeholder = 'Parti notu (isteğe bağlı)';
  nodes.note.maxLength = 300;
  nodes.previewBtn = button('Ön izleme', { onClick: runPreview });
  nodes.commit = button('Satışları işle', { variant: 'primary', onClick: commit });
  nodes.commit.disabled = true;
  const actions = h('div', 'bs-actions');
  actions.append(nodes.previewBtn, nodes.commit);
  sideCol.append(nodes.queueBox, nodes.preview, nodes.note, actions);

  nodes.paneWork.append(nodes.pickerBox, cartCol, sideCol);

  nodes.paneHistory = h('div', 'bs-pane bs-pane-history');
  nodes.batches = h('div', 'bs-batches');
  nodes.paneHistory.append(nodes.batches);

  view.append(bar, nodes.status, tabBar, nodes.paneWork, nodes.paneHistory);
  root.replaceChildren(view);

  switchTab('work');
  renderMode();
  renderQueue();
  renderPreview();

  (async () => {
    await loadClasses();
    await refresh();
  })();

  return () => {
    root.replaceChildren();
    queue = [];
    preview = null;
    busy = false;
  };
}
