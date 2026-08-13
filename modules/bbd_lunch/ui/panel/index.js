// Öğle Yemeği paneli — CANLI KANTİN VERİSİ.
//
// Kasada tek tek QR okutularak girilen yemeği, takvimden gün seçip toplu işler.
// Yazma kantinin kendi satış ucundan geçer: sonuç kasada elle girilmişten
// ayırt edilemez (aynı işlem satırı, aynı cari borç, aynı stok düşümü).
//
// EKRAN HİÇBİR ŞEYİ KÖRLEMESİNE GÖNDERMEZ. "İşle" düğmesi ancak ön izleme
// alındıktan sonra etkinleşir; ön izleme her öğrenci için engel, limit, stok
// ve "bugün zaten girilmiş mi" sorularını yanıtlar.

import { createCalendar } from './calendar.js';
import {
  button, confirmWithReason, h, loadStyles, longDate, money, moneyInput,
  parseMoney, stampIso, toaster, todayIso,
} from './kit.js';
import { createPicker } from './picker.js';

loadStyles(import.meta.url);

let api = null;
let capability = null;
let toast = null;

let overview = { days: {}, holidays: {}, roster: [], students: [], product: null };
let dayDetail = { batches: [], recorded: {} };
let preview = null;
let selectedDay = todayIso();
let classMap = new Map();
// O gün yemeği zaten girilmiş öğrencilere ikinci porsiyon izni — varsayılan KAPALI.
let allowRepeat = false;
let busy = false;

const nodes = {};
let picker = null;
let calendar = null;

// ------------------------------------------------------------------- veri

async function loadClasses() {
  // Sınıf bilgisi Öğrenci Yönetimi modülünün verisidir; yetenek üzerinden
  // gelir (K3). Yoksa ekran sınıfsız çalışır — hata değil, eksik özelliktir.
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

async function refreshOverview(month) {
  setStatus('Kantinden okunuyor…');
  try {
    overview = await api(`/api/bbd_lunch/overview?month=${encodeURIComponent(month)}`);
    picker.setStudents(overview.students || [], classMap);
    calendar.update({
      days: overview.days || {},
      holidays: overview.holidays || {},
      selected: selectedDay,
    });
    setStatus(overview.connected
      ? `Kantine bağlı · ${(overview.students || []).length} öğrenci`
      : `Kantine ulaşılamadı — ${overview.error || 'bilinmeyen hata'}`, !overview.connected);
  } catch (error) {
    setStatus(`Çekirdek hatası: ${error.message}`, true);
  }
  renderProduct();
}

async function refreshDay() {
  try {
    dayDetail = await api(`/api/bbd_lunch/days/${selectedDay}`);
  } catch (error) {
    dayDetail = { batches: [], recorded: {} };
    console.warn('gün okunamadı', error);
  }
  renderHistory();
  renderDayHead();
}

// ------------------------------------------------------------------ çizim

function setStatus(text, bad = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

function renderProduct() {
  const product = overview.product;
  nodes.product.replaceChildren();

  if (!product) {
    nodes.product.append(h('span', 'lu-product-missing',
      'Kantinde öğle yemeği ürünü bulunamadı — ayardan ürün id\'si verilebilir.'));
    nodes.price.disabled = true;
    return;
  }

  nodes.product.append(
    h('span', 'lu-product-name', product.name),
    h('span', 'lu-product-stock', `stok ${product.stock}`),
  );
  if (!product.isActive) {
    nodes.product.append(h('span', 'lu-tag warn', 'ürün pasif'));
  }
  nodes.price.disabled = false;
  if (!nodes.price.dataset.touched) nodes.price.value = moneyInput(product.price);
}

function renderDayHead() {
  nodes.dayTitle.textContent = longDate(selectedDay);

  const info = overview.days?.[selectedDay];
  const recorded = Object.keys(dayDetail.recorded || {}).length;
  const bits = [];
  if (info?.ok) bits.push(`${info.ok} işlenmiş`);
  if (info?.failed) bits.push(`${info.failed} hatalı`);
  if (info?.reversed) bits.push(`${info.reversed} geri alınmış`);
  if (recorded) bits.push(`kantinde ${recorded} öğrenciye yemek satılmış`);
  nodes.dayMeta.textContent = bits.length ? bits.join(' · ') : 'Bu gün için kayıt yok.';

  const holidayLabel = overview.holidays?.[selectedDay];
  nodes.holiday.textContent = holidayLabel === undefined ? 'Tatil işaretle' : 'Tatili kaldır';
  nodes.holidayNote.textContent = holidayLabel ? `Tatil: ${holidayLabel}` : '';
}

/** Ön izleme özeti — gönderim öncesi tek bakışta ne olacağı. */
function renderPreview() {
  nodes.preview.replaceChildren();
  nodes.commit.disabled = busy || !preview?.ok || !(preview?.summary?.eligible > 0);

  if (!preview) {
    nodes.preview.append(h('div', 'lu-hint',
      'Öğrencileri seçip “Ön izleme” deyin — kimin işleneceğini, kimin neden '
      + 'işlenemeyeceğini ve stoğun yetip yetmediğini göndermeden görürsünüz.'));
    return;
  }

  if (!preview.ok) {
    nodes.preview.append(h('div', 'lu-alert bad', preview.error));
    return;
  }

  const summary = preview.summary;
  const tiles = h('div', 'lu-tiles');
  const tile = (label, value, tone = '') => {
    const box = h('div', 'lu-tile');
    box.append(h('span', 'lu-tile-label', label), h('b', `lu-tile-value${tone ? ` ${tone}` : ''}`, value));
    return box;
  };
  tiles.append(
    tile('İşlenecek', `${summary.eligible} öğrenci`),
    tile('Porsiyon', String(summary.portion)),
    tile('Birim fiyat', money(summary.unitPrice)),
    tile('Toplam tutar', money(summary.totalAmount), 'strong'),
    tile('Stok', `${summary.stock} adet`, summary.stockShort ? 'warn' : ''),
  );
  nodes.preview.append(tiles);

  if (summary.stockShort > 0) {
    const alert = h('div', 'lu-alert warn');
    alert.append(h('span', undefined,
      `Stok ${summary.stockShort} adet eksik. Kantin yetersiz stokta satışı reddeder.`));
    alert.append(button(`${summary.stockShort} adet stok gir`, {
      variant: 'primary',
      onClick: () => topUpStock(summary.stockShort),
    }));
    nodes.preview.append(alert);
  }

  // O gün yemeği zaten girilmiş öğrenciler VARSAYILAN OLARAK işlenmez. İkinci
  // porsiyon bilinçli bir karardır; onay kutusu olmadan kazayla oluşamaz.
  if (summary.already > 0) {
    const box = h('div', 'lu-alert warn lu-repeat');
    const label = h('label', 'lu-repeat-label');
    const check = h('input');
    check.type = 'checkbox';
    check.checked = allowRepeat;
    check.addEventListener('change', () => {
      allowRepeat = check.checked;
      runPreview();
    });
    label.append(check, h('span', undefined,
      `${summary.already} öğrenciye bu gün için yemek zaten işlenmiş — bunlar `
      + 'atlanacak. İkinci porsiyon girmek istiyorsanız işaretleyin.'));
    box.append(label);
    nodes.preview.append(box);
  }

  const problems = preview.rows.filter((row) => !['ok', 'repeat'].includes(row.verdict));
  if (problems.length > 0) {
    const box = h('div', 'lu-problems');
    box.append(h('div', 'lu-problems-head',
      `${problems.length} öğrenci işlenmeyecek`));
    for (const row of problems) {
      const line = h('div', `lu-problem ${row.verdict}`);
      line.append(
        h('span', 'lu-problem-name', row.name || row.kantinId),
        h('span', 'lu-problem-msg', row.message),
      );
      box.append(line);
    }
    nodes.preview.append(box);
  }

  if (summary.eligible > 0) {
    nodes.preview.append(h('div', 'lu-ready',
      `${summary.eligible} öğrenciye ${money(summary.unitPrice * summary.portion)} `
      + `işlenecek — toplam ${money(summary.totalAmount)}.`));
  }
}

/** Geçmiş girdiler — parti parti, geri alma düğmeleriyle. */
function renderHistory() {
  nodes.history.replaceChildren();
  const batches = dayDetail.batches || [];

  if (batches.length === 0) {
    nodes.history.append(h('div', 'lu-hint', 'Bu güne ait işlenmiş parti yok.'));
    return;
  }

  for (const batch of batches) {
    const card = h('div', 'lu-batch');

    const head = h('div', 'lu-batch-head');
    const live = (batch.entries || []).filter(
      (entry) => !entry.reversed_at && ['created', 'duplicate'].includes(entry.status));
    head.append(
      h('span', 'lu-batch-title', `${batch.product_name} · ${batch.portion} porsiyon`),
      h('span', 'lu-batch-meta',
        `${batch.ok_count} işlendi${batch.fail_count ? ` · ${batch.fail_count} hata` : ''} · `
        + `${money(batch.unit_price)} · ${stampIso(batch.created_at)}`
        + (batch.created_by ? ` · ${batch.created_by}` : '')),
    );
    const spacer = h('span', 'lu-spacer');
    head.append(spacer);
    if (live.length > 0) {
      head.append(button(`Partiyi geri al (${live.length})`, {
        variant: 'danger',
        title: 'Kantinde ters kayıt oluşur; satırlar silinmez.',
        onClick: () => reverse({ batchRef: batch.batch_ref, count: live.length }),
      }));
    }
    card.append(head);

    if (batch.note) card.append(h('div', 'lu-batch-note', batch.note));

    const list = h('div', 'lu-entries');
    for (const entry of batch.entries || []) {
      const row = h('div', `lu-entry ${entry.reversed_at ? 'undone' : entry.status}`);
      row.append(h('span', 'lu-entry-name', entry.student_name || entry.kantin_id));
      row.append(h('span', 'lu-entry-amount', money(entry.amount)));

      const state = entry.reversed_at
        ? `geri alındı — ${entry.reversed_reason}`
        : ({ created: 'işlendi', duplicate: 'zaten vardı', pending: 'gönderilmedi' }[entry.status]
          || `hata: ${entry.reason || 'bilinmiyor'}`);
      row.append(h('span', 'lu-entry-state', state));

      if (!entry.reversed_at && ['created', 'duplicate'].includes(entry.status)) {
        const undo = h('button', 'lu-undo', 'geri al');
        undo.type = 'button';
        undo.title = 'Yalnız bu öğrencinin kaydını geri alır.';
        undo.addEventListener('click', () => reverse({
          localId: entry.local_id, count: 1, who: entry.student_name,
        }));
        row.append(undo);
      }
      list.append(row);
    }
    card.append(list);
    nodes.history.append(card);
  }
}

// ------------------------------------------------------------- eylemler

function selectionPayload(extra = {}) {
  const price = parseMoney(nodes.price.value);
  return {
    date: selectedDay,
    students: picker.selection(),
    portion: Math.max(1, Number(nodes.portion.value) || 1),
    unitPrice: price ?? undefined,
    note: nodes.note.value.trim(),
    classes: picker.classMap(),
    allowRepeat,
    ...extra,
  };
}

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.commit.disabled = true;
  nodes.previewBtn.disabled = true;
  setStatus(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    setStatus(error.message, true);
    return null;
  } finally {
    busy = false;
    nodes.previewBtn.disabled = false;
    renderPreview();
  }
}

async function runPreview() {
  if (picker.size() === 0) {
    toast('Önce öğrenci seçin.', 'warn');
    return;
  }
  await withBusy('Ön izleme hazırlanıyor…', async () => {
    preview = await api('/api/bbd_lunch/preview', { method: 'POST', body: selectionPayload() });
    renderPreview();
    setStatus(preview.ok
      ? `${preview.summary.eligible} öğrenci işlenmeye hazır.`
      : preview.error, !preview.ok);
  });
}

async function commit() {
  if (!preview?.ok || !(preview.summary.eligible > 0)) return;

  const summary = preview.summary;
  const confirmed = await confirmWithReason(nodes.root, {
    title: 'Yemek kaydını tamamla',
    description: `${longDate(selectedDay)} · ${summary.eligible} öğrenci · `
      + `toplam ${money(summary.totalAmount)}. Kayıt kantinde kasada girilmiş gibi oluşur. `
      + 'Kısa bir açıklama yazın (kayıt notu olarak saklanır).',
    confirmLabel: 'İşle',
    placeholder: 'Örn. 12 Ağustos öğle yemeği listesi',
    danger: false,
  });
  if (confirmed === null) return;

  await withBusy('Kantine işleniyor…', async () => {
    const result = await api('/api/bbd_lunch/commit', {
      method: 'POST',
      body: selectionPayload({ note: confirmed }),
    });
    if (!result.ok || result.committed === false) {
      toast(result.error || 'İşlenemedi.', 'bad');
      return;
    }
    toast(`${result.okCount} kayıt işlendi${result.failCount ? `, ${result.failCount} hata` : ''}.`,
      result.failCount ? 'warn' : 'good');
    preview = null;
    allowRepeat = false;
    picker.clear();
    nodes.note.value = '';
    await refreshOverview(calendar.month());
    await refreshDay();
    switchTab('history');
  });
}

async function reverse({ batchRef, localId, count, who }) {
  const reason = await confirmWithReason(nodes.root, {
    title: localId ? 'Kaydı geri al' : 'Partiyi geri al',
    description: localId
      ? `${who || 'Öğrenci'} için işlenen yemek kaydı geri alınacak. Kantinde ters cari `
        + 'kayıt oluşur, stok iade edilir. HİÇBİR SATIR SİLİNMEZ.'
      : `${count} kayıt geri alınacak. Kantinde her biri için ters cari kayıt oluşur ve `
        + 'stok iade edilir. HİÇBİR SATIR SİLİNMEZ.',
    confirmLabel: 'Geri al',
  });
  if (reason === null) return;

  await withBusy('Geri alınıyor…', async () => {
    const result = await api('/api/bbd_lunch/reverse', {
      method: 'POST', body: { batchRef, localId, reason },
    });
    const failed = (result.failures || []).length;
    toast(`${result.reversed} kayıt geri alındı${failed ? `, ${failed} başarısız` : ''}.`,
      failed ? 'warn' : 'good');
    await refreshOverview(calendar.month());
    await refreshDay();
  });
}

async function topUpStock(quantity) {
  await withBusy('Stok giriliyor…', async () => {
    const result = await api('/api/bbd_lunch/stock', {
      method: 'POST',
      body: { quantity, reason: `Öğle yemeği stok girişi — ${longDate(selectedDay)}` },
    });
    if (!result.ok) {
      toast(result.error || 'Stok girilemedi.', 'bad');
      return;
    }
    toast(`Stok girildi. Yeni stok: ${result.newStock}`, 'good');
    await refreshOverview(calendar.month());
    await runPreview();
  });
}

async function toggleHoliday(day, isHoliday) {
  const label = isHoliday ? '' : (window.prompt('Tatil açıklaması', 'Tatil') ?? null);
  if (!isHoliday && label === null) return;
  await api('/api/bbd_lunch/holidays', {
    method: 'PUT', body: { day, label: label || 'Tatil', remove: isHoliday },
  });
  toast(isHoliday ? 'Tatil kaldırıldı.' : 'Tatil işaretlendi.', 'good');
  await refreshOverview(calendar.month());
}

async function saveRoster() {
  const ids = picker.selection();
  if (ids.length === 0) {
    toast('Sabit listeye almak için önce öğrenci seçin.', 'warn');
    return;
  }
  await api('/api/bbd_lunch/roster', { method: 'PUT', body: { students: ids } });
  overview.roster = ids;
  toast(`${ids.length} öğrenci sabit listeye alındı.`, 'good');
}

async function commitRange() {
  const end = window.prompt(
    'Hangi güne kadar işlensin? (YYYY-AA-GG)\n'
    + 'Hafta sonları ve tatil işaretli günler atlanır.', selectedDay);
  if (!end || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return;
  if (end < selectedDay) {
    toast('Bitiş günü başlangıçtan önce olamaz.', 'warn');
    return;
  }

  const reason = await confirmWithReason(nodes.root, {
    title: 'Aralığı işle',
    description: `${longDate(selectedDay)} → ${longDate(end)} arasındaki İŞ GÜNLERİNE `
      + `${picker.size()} öğrenci için yemek işlenecek. Bu çok sayıda kayıt üretir.`,
    confirmLabel: 'Aralığı işle',
    placeholder: 'Kayıt notu',
    danger: false,
  });
  if (reason === null) return;

  await withBusy('Aralık işleniyor…', async () => {
    const result = await api('/api/bbd_lunch/commit-range', {
      method: 'POST',
      body: selectionPayload({ endDate: end, note: reason }),
    });
    if (!result.ok) {
      toast(result.error || 'Aralık işlenemedi.', 'bad');
      return;
    }
    const total = (result.results || []).reduce((sum, item) => sum + (item.result?.okCount || 0), 0);
    toast(`${result.days.length} iş gününe toplam ${total} kayıt işlendi.`, 'good');
    preview = null;
    await refreshOverview(calendar.month());
    await refreshDay();
  });
}

function switchTab(name) {
  for (const [key, tab] of Object.entries(nodes.tabs)) {
    tab.classList.toggle('on', key === name);
  }
  nodes.paneSelect.hidden = name !== 'select';
  nodes.paneHistory.hidden = name !== 'history';
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'lu');
  nodes.root = view;
  toast = toaster(view);

  // --- üst şerit -----------------------------------------------------
  const bar = h('div', 'lu-bar');
  nodes.product = h('div', 'lu-product');

  nodes.price = h('input', 'lu-input lu-price');
  nodes.price.type = 'text';
  nodes.price.title = 'Birim fiyat — boş bırakılırsa kantindeki güncel fiyat kullanılır.';
  nodes.price.addEventListener('input', () => {
    nodes.price.dataset.touched = '1';
    preview = null;
    renderPreview();
  });

  nodes.portion = h('input', 'lu-input lu-portion');
  nodes.portion.type = 'number';
  nodes.portion.min = '1';
  nodes.portion.max = '20';
  nodes.portion.value = '1';
  nodes.portion.title = 'Öğrenci başına porsiyon';
  nodes.portion.addEventListener('input', () => { preview = null; renderPreview(); });

  bar.append(
    nodes.product,
    h('span', 'lu-field-label', 'Birim fiyat'), nodes.price,
    h('span', 'lu-field-label', 'Porsiyon'), nodes.portion,
  );
  nodes.status = h('div', 'lu-status');

  // --- gövde ---------------------------------------------------------
  const split = h('div', 'lu-split');

  calendar = createCalendar({
    onPick: async (day, month) => {
      if (day) selectedDay = day;
      preview = null;
      allowRepeat = false;
      calendar.update({ selected: selectedDay });
      await refreshOverview(month);
      await refreshDay();
      renderPreview();
    },
    onToggleHoliday: toggleHoliday,
  });
  const side = h('div', 'lu-side');
  side.append(calendar.node);
  split.append(side);

  const work = h('div', 'lu-work');

  const dayHead = h('div', 'lu-day-head');
  nodes.dayTitle = h('h2', 'lu-day-title');
  nodes.dayMeta = h('div', 'lu-day-meta');
  nodes.holidayNote = h('span', 'lu-tag muted');
  nodes.holiday = button('Tatil işaretle', {
    title: 'Aralık işlemede bu gün atlanır. (Takvimde sağ tık da aynı işi yapar.)',
    onClick: () => toggleHoliday(selectedDay, Object.hasOwn(overview.holidays || {}, selectedDay)),
  });
  const headLeft = h('div', 'lu-day-left');
  headLeft.append(nodes.dayTitle, nodes.dayMeta);
  dayHead.append(headLeft, h('span', 'lu-spacer'), nodes.holidayNote, nodes.holiday);
  work.append(dayHead);

  const tabBar = h('div', 'lu-tabs');
  nodes.tabs = {};
  for (const [key, label] of [['select', 'Seçim ve işleme'], ['history', 'Geçmiş girdiler']]) {
    const tab = h('button', 'lu-tab', label);
    tab.type = 'button';
    tab.addEventListener('click', () => switchTab(key));
    nodes.tabs[key] = tab;
    tabBar.append(tab);
  }
  work.append(tabBar);

  // Seçim sekmesi
  nodes.paneSelect = h('div', 'lu-pane lu-pane-select');
  picker = createPicker({ onChange: () => { preview = null; allowRepeat = false; renderPreview(); } });

  const pickerBox = h('div', 'lu-picker');
  const pickerTools = h('div', 'lu-picker-tools');
  pickerTools.append(
    button('Sabit listeyi uygula', {
      title: 'Her gün yemek yiyen öğrencileri seçer.',
      onClick: () => {
        if (!(overview.roster || []).length) {
          toast('Sabit liste boş. Önce seçip “Sabit liste yap” deyin.', 'warn');
          return;
        }
        picker.select(overview.roster);
        toast(`${overview.roster.length} öğrenci sabit listeden seçildi.`, 'good');
      },
    }),
    button('Sabit liste yap', {
      title: 'Şu anki seçimi sabit liste olarak kaydeder.',
      onClick: saveRoster,
    }),
    button('Dünkü listeyi kopyala', {
      title: 'Bir önceki iş gününde işlenmiş öğrencileri seçer.',
      onClick: copyPreviousDay,
    }),
  );
  pickerBox.append(pickerTools, picker.node);

  const side2 = h('div', 'lu-preview-col');
  nodes.preview = h('div', 'lu-preview');

  nodes.note = h('input', 'lu-input lu-note');
  nodes.note.type = 'text';
  nodes.note.placeholder = 'Parti notu (isteğe bağlı)';
  nodes.note.maxLength = 300;

  nodes.previewBtn = button('Ön izleme', {
    title: 'Gönderim yapmadan sonucu gösterir.',
    onClick: runPreview,
  });
  nodes.commit = button('Yemek kaydını tamamla', { variant: 'primary', onClick: commit });
  nodes.commit.disabled = true;

  const actions = h('div', 'lu-actions');
  actions.append(nodes.previewBtn, nodes.commit,
    button('Aralığa işle…', {
      title: 'Seçili listeyi bir tarih aralığındaki iş günlerine işler.',
      onClick: commitRange,
    }));

  side2.append(nodes.preview, nodes.note, actions);
  nodes.paneSelect.append(pickerBox, side2);

  // Geçmiş sekmesi
  nodes.paneHistory = h('div', 'lu-pane lu-pane-history');
  nodes.history = h('div', 'lu-history');
  nodes.paneHistory.append(nodes.history);

  work.append(nodes.paneSelect, nodes.paneHistory);
  split.append(work);

  view.append(bar, nodes.status, split);
  root.replaceChildren(view);

  switchTab('select');
  renderPreview();
  calendar.setMonthFrom(selectedDay);
  calendar.update({ selected: selectedDay });

  (async () => {
    await loadClasses();
    await refreshOverview(calendar.month());
    await refreshDay();
  })();

  return () => {
    root.replaceChildren();
    preview = null;
    allowRepeat = false;
    busy = false;
  };
}

async function copyPreviousDay() {
  // Bir önceki İŞ GÜNÜ: hafta sonuna denk gelirse geriye doğru yürür.
  const date = new Date(`${selectedDay}T00:00:00`);
  for (let step = 0; step < 10; step += 1) {
    date.setDate(date.getDate() - 1);
    const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    let detail;
    try {
      detail = await api(`/api/bbd_lunch/days/${iso}`);
    } catch {
      continue;
    }
    const ids = new Set();
    for (const batch of detail.batches || []) {
      for (const entry of batch.entries || []) {
        if (!entry.reversed_at && ['created', 'duplicate'].includes(entry.status)) {
          ids.add(entry.kantin_id);
        }
      }
    }
    if (ids.size > 0) {
      picker.select([...ids]);
      toast(`${iso} gününden ${ids.size} öğrenci seçildi.`, 'good');
      return;
    }
  }
  toast('Önceki günlerde işlenmiş kayıt bulunamadı.', 'warn');
}
