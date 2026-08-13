// SMS Sistemi paneli — SERBEST SMS, CANLI GÖNDERİM.
//
// Kurumun Netgsm başlığından velilere serbest metin gönderir. Ödeme linki
// göndermez; o Ödeme Talebi ekranının işidir.
//
// ALICI DAİMA VELİDİR. Kantinde öğrenci telefonu diye bir alan yok; SMS ucu
// öğrenci verildiğinde serbest telefonu yok sayıp veli numarasını kullanır.
// Ekran her satırda hangi numaraya gideceğini yazar.
//
// GERÇEK PARA HARCANIR: gönderim geri alınamaz. "Gönder" ancak kuru provadan
// sonra etkinleşir ve onay penceresi ister.

import {
  button, confirmWithReason, h, loadStyles, money, stampIso, toaster,
} from './kit.js';
import { createPicker } from './picker.js';

loadStyles(import.meta.url);

let api = null;
let capability = null;
let toast = null;

let state = { students: [], presets: [], netgsm: {}, queue: {}, limits: {}, connected: false };
let preview = null;
let classMap = new Map();
let tab = 'send';
let simplify = false;
let busy = false;
let measureTimer = null;

const nodes = {};
let picker = null;

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
    state = await api('/api/bbd_sms/workspace');
    picker.setStudents(state.students.map((item) => ({
      kantinId: item.kantinId,
      displayName: item.displayName,
      balance: item.balance,
      spendingLimit: null,
      isBlocked: item.isBlocked,
    })), classMap);

    const netgsm = state.netgsm || {};
    setStatus(state.connected
      ? `${state.students.length} öğrenci · ${state.withPhone} veli telefonu kayıtlı`
        + (netgsm.ready ? ` · başlık ${netgsm.header}` : ' · NETGSM KURULU DEĞİL')
      : `Kantine ulaşılamadı — ${state.error || 'bilinmeyen hata'}`,
      !state.connected || !netgsm.ready);
  } catch (error) {
    setStatus(`Çekirdek hatası: ${error.message}`, true);
  }
  renderPresets();
  renderNetgsm();
  if (tab === 'history') renderHistory();
  if (tab === 'queue') renderQueue();
}

// ------------------------------------------------------------------ çizim

/** Numaralı adım başlığı — akışın nerede olduğunu tek bakışta söyler. */
function step(number, title, hint) {
  const box = h('div', 'sm-step');
  const head = h('div', 'sm-step-head');
  head.append(h('span', 'sm-step-no', String(number)), h('b', 'sm-step-title', title));
  box.append(head);
  if (hint) box.append(h('p', 'sm-step-hint', hint));
  return box;
}

/**
 * Seçili ilk öğrenci için mesajın çözülmüş hâli.
 *
 * Yer tutucu kavramını anlatmanın en kısa yolu göstermektir: kullanıcı
 * "{ad}" yazdığı anda altta "Sayın veli, AHMET EREN…" belirir.
 */
function renderSample() {
  nodes.sample.replaceChildren();
  const body = nodes.body.value;
  if (!body.trim()) return;

  const first = (state.students || []).find(
    (item) => picker.selection().includes(item.kantinId));
  if (!first) {
    nodes.sample.append(h('div', 'sm-sample-hint',
      'Alıcı seçtiğinizde mesajın o kişiye nasıl gideceğini burada göreceksiniz.'));
    return;
  }

  const filled = body
    .replaceAll('{ad}', first.displayName || '')
    .replaceAll('{borc}', money(first.balance))
    .replaceAll('{sinif}', classMap.get(first.kantinId) || '')
    .replaceAll('{okul}', state.schoolName || '');

  nodes.sample.append(
    h('div', 'sm-sample-label', `Örnek — ${first.displayName} velisine gidecek metin`),
    h('div', 'sm-sample-text', filled),
  );
}

/** Kaç kişi seçili, kaçının velisinde numara var. */
function renderSelectionNote() {
  if (!nodes.selectionNote) return;
  const ids = new Set(picker.selection());
  if (ids.size === 0) {
    nodes.selectionNote.textContent = 'Henüz kimse seçilmedi.';
    nodes.selectionNote.classList.remove('bad');
    return;
  }
  const chosen = (state.students || []).filter((item) => ids.has(item.kantinId));
  const withPhone = chosen.filter((item) => item.hasPhone).length;
  const missing = chosen.length - withPhone;
  nodes.selectionNote.textContent = missing
    ? `${chosen.length} öğrenci seçili · ${withPhone} veliye gidebilir, `
      + `${missing} öğrencinin veli telefonu yok (bunlar gönderilmez).`
    : `${chosen.length} öğrenci seçili · hepsinin veli telefonu kayıtlı.`;
  nodes.selectionNote.classList.toggle('bad', missing > 0);
}

function setStatus(text, bad = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

function payload(extra = {}) {
  return {
    students: picker.selection(),
    body: nodes.body.value,
    title: nodes.title.value.trim(),
    includeDebt: nodes.includeDebt.checked,
    includeDaily: nodes.includeDaily.checked,
    simplify,
    classes: picker.classMap(),
    ...extra,
  };
}

/** Yazarken canlı segment/kredi hesabı — gönderim maliyeti yazarken görünür. */
function scheduleMeasure() {
  window.clearTimeout(measureTimer);
  measureTimer = window.setTimeout(async () => {
    const text = nodes.body.value;
    if (!text.trim()) {
      nodes.meter.replaceChildren(h('span', 'sm-meter-hint',
        'Mesaj yazın — karakter seti, segment ve kredi burada görünecek.'));
      return;
    }
    try {
      const result = await api('/api/bbd_sms/measure', {
        method: 'POST', body: { text, simplify },
      });
      renderMeter(result.measure);
    } catch (error) {
      console.warn('ölçüm alınamadı', error);
    }
  }, 260);
}

function renderMeter(measure) {
  nodes.meter.replaceChildren();
  const ucs2 = !measure.gsm7;

  const chip = (label, value, tone = '') => {
    const box = h('span', `sm-chip${tone ? ` ${tone}` : ''}`);
    box.append(h('span', 'sm-chip-label', label), h('b', undefined, String(value)));
    return box;
  };

  nodes.meter.append(
    chip('Karakter seti', measure.encoding, ucs2 ? 'warn' : 'good'),
    chip('Karakter', `${measure.units} / ${measure.perSegment}`),
    chip('Segment', measure.segments, measure.segments > 1 ? 'warn' : ''),
    chip('Kalan', measure.remaining),
  );

  if (ucs2) {
    const alert = h('div', 'sm-alert warn');
    const chars = (measure.offending || []).join(' ');
    alert.append(h('span', undefined,
      `Türkçe karakter (${chars}) mesajı UCS-2'ye düşürdü: segment 160 yerine `
      + `70 karakter. Sadeleştirilirse ${measure.simplifiedSegments} segment olur `
      + `(şu an ${measure.segments}).`));
    alert.append(button(simplify ? 'Sadeleştirme açık' : 'Sadeleştir', {
      variant: simplify ? '' : 'primary',
      title: 'ğ ı İ ş Ş ç ö ü karakterlerini ASCII karşılığına çevirir.',
      onClick: () => {
        simplify = !simplify;
        preview = null;
        scheduleMeasure();
        renderPreview();
      },
    }));
    nodes.meter.append(alert);
  } else if (simplify) {
    nodes.meter.append(h('div', 'sm-alert good',
      'Sadeleştirme açık — mesaj GSM-7, segment 160 karakter.'));
  }
}

function renderPreview() {
  renderSample();
  renderSelectionNote();
  nodes.preview.replaceChildren();
  const ready = picker.size() > 0 && (nodes.body.value.trim() || nodes.includeDebt.checked);
  nodes.previewBtn.disabled = busy || !ready;
  nodes.send.disabled = busy || !preview?.ok || !(preview?.summary?.eligible > 0);

  if (!preview) {
    nodes.preview.append(h('div', 'sm-hint',
      'Alıcıları seçip mesajı yazın, sonra “Kontrol et” deyin. Kime, hangi veli '
      + 'numarasına ve tam olarak hangi metnin gideceğini göndermeden görürsünüz.'));
    return;
  }

  if (!preview.ok) {
    nodes.preview.append(h('div', 'sm-alert bad', preview.error));
    return;
  }

  const summary = preview.summary;
  const tiles = h('div', 'sm-tiles');
  const tile = (label, value, tone = '') => {
    const box = h('div', 'sm-tile');
    box.append(h('span', 'sm-tile-label', label),
      h('b', `sm-tile-value${tone ? ` ${tone}` : ''}`, value));
    return box;
  };
  tiles.append(
    tile('Gidecek', `${summary.eligible} veli`),
    tile('Atlanan', String(summary.skipped), summary.skipped ? 'warn' : ''),
    tile('Toplam kredi', String(summary.credits),
      summary.overCredit ? 'warn' : 'strong'),
  );
  nodes.preview.append(tiles);

  if (summary.overCredit) {
    nodes.preview.append(h('div', 'sm-alert warn',
      `Bu gönderim ${summary.credits} SMS kredisi harcayacak `
      + `(uyarı eşiği ${summary.creditWarning}). Metni sadeleştirmek maliyeti yarıya indirir.`));
  }
  if (summary.overLimit) {
    nodes.preview.append(h('div', 'sm-alert bad',
      `Alıcı sayısı sınırı aşıyor: ${summary.eligible} > ${summary.maxRecipients}.`));
  }

  const skipped = preview.rows.filter((row) => row.verdict === 'skipped');
  if (skipped.length > 0) {
    const box = h('div', 'sm-problems');
    box.append(h('div', 'sm-problems-head', `${skipped.length} kişi atlanacak`));
    for (const row of skipped) {
      const line = h('div', 'sm-problem');
      line.append(h('span', 'sm-problem-name', row.name || row.kantinId),
        h('span', 'sm-problem-msg', row.message));
      box.append(line);
    }
    nodes.preview.append(box);
  }

  const ready_ = preview.rows.filter((row) => row.verdict === 'ready');
  if (ready_.length > 0) {
    const box = h('div', 'sm-recipients');
    box.append(h('div', 'sm-recipients-head',
      `Gidecek mesajlar (${ready_.length}) — veli numarasına`));
    for (const row of ready_.slice(0, 40)) {
      const line = h('div', 'sm-recipient');
      line.append(
        h('span', 'sm-recipient-name', row.name),
        h('span', 'sm-recipient-phone', row.phone),
        h('span', 'sm-recipient-seg', `${row.segments} kredi`),
        h('div', 'sm-recipient-text', row.text),
      );
      box.append(line);
    }
    if (ready_.length > 40) {
      box.append(h('div', 'sm-more', `… ve ${ready_.length - 40} alıcı daha`));
    }
    nodes.preview.append(box);
  }
}

function renderPresets() {
  nodes.presets.replaceChildren();
  if ((state.presets || []).length === 0) {
    nodes.presets.append(h('span', 'sm-preset-empty',
      'Hazır mesaj yok — metni yazıp “Hazır mesaj yap” diyebilirsiniz.'));
    return;
  }
  for (const preset of state.presets) {
    const chip = h('span', 'sm-preset');
    const load = h('button', 'sm-preset-load', preset.name);
    load.type = 'button';
    load.title = preset.body;
    load.addEventListener('click', () => {
      nodes.body.value = preset.body;
      nodes.title.value = preset.name;
      preview = null;
      scheduleMeasure();
      renderPreview();
    });
    const drop = h('button', 'sm-preset-drop', '×');
    drop.type = 'button';
    drop.title = 'Hazır mesajı sil';
    drop.addEventListener('click', async () => {
      await api(`/api/bbd_sms/presets/${preset.id}`, { method: 'DELETE' });
      await refresh();
    });
    chip.append(load, drop);
    nodes.presets.append(chip);
  }
}

function renderNetgsm() {
  const netgsm = state.netgsm || {};
  nodes.netgsmBox.replaceChildren();

  if (!netgsm.ready) {
    nodes.netgsmBox.append(h('div', 'sm-alert bad',
      'Netgsm kimlik bilgileri eksik — bu hâlde HİÇBİR SMS gönderilemez. '
      + 'Kullanıcı kodu, parola ve başlık üçü de dolu olmalı.'));
  }

  const field = (label, node, hint) => {
    const wrap = h('label', 'sm-field');
    wrap.append(h('span', 'sm-field-label', label), node);
    if (hint) wrap.append(h('span', 'sm-field-hint', hint));
    return wrap;
  };

  nodes.usercode = h('input', 'sm-input');
  nodes.usercode.type = 'text';
  nodes.usercode.maxLength = 64;
  nodes.usercode.value = netgsm.usercode || '';

  nodes.password = h('input', 'sm-input');
  nodes.password.type = 'password';
  nodes.password.maxLength = 255;
  nodes.password.placeholder = netgsm.passwordConfigured
    ? 'Kayıtlı — boş bırakılırsa değişmez' : 'Parola girilmemiş';

  nodes.header = h('input', 'sm-input');
  nodes.header.type = 'text';
  nodes.header.maxLength = 11;
  nodes.header.value = netgsm.header || '';

  const confirmed = h('input');
  confirmed.type = 'checkbox';
  confirmed.checked = Boolean(netgsm.paymentConfirmedEnabled);
  const confirmedWrap = h('label', 'sm-check');
  confirmedWrap.append(confirmed, h('span', undefined,
    'Veli ödeme yapınca teşekkür SMS\'i gönder'));

  nodes.netgsmBox.append(
    field('Netgsm kullanıcı kodu', nodes.usercode),
    field('Netgsm parola', nodes.password,
      'Sunucu parolayı asla geri vermez; boş bırakılırsa mevcut korunur.'),
    field('Gönderici başlığı', nodes.header,
      'Netgsm\'de onaylı başlık, en çok 11 karakter.'),
    confirmedWrap,
    button('Ayarları kaydet', {
      variant: 'primary',
      onClick: async () => {
        const body = {
          netgsmUsercode: nodes.usercode.value.trim(),
          netgsmHeader: nodes.header.value.trim(),
          smsPaymentConfirmedEnabled: confirmed.checked,
        };
        if (nodes.password.value) body.netgsmPassword = nodes.password.value;
        const result = await api('/api/bbd_sms/netgsm', { method: 'PUT', body });
        if (!result.ok) { toast(result.error || 'Kaydedilemedi.', 'bad'); return; }
        toast('Netgsm ayarları kaydedildi.', 'good');
        await refresh();
      },
    }),
  );
}

async function renderHistory() {
  nodes.history.replaceChildren(h('div', 'sm-hint', 'Yükleniyor…'));
  try {
    const payload_ = await api('/api/bbd_sms/history?limit=200');
    const batches = payload_.batches || [];
    nodes.history.replaceChildren();
    if (batches.length === 0) {
      nodes.history.append(h('div', 'sm-hint',
        'Bu panelden henüz SMS gönderilmedi. (Kantin manuel SMS geçmişi tutmaz; '
        + 'buradaki kayıt tek kaynaktır.)'));
      return;
    }
    for (const batch of batches) {
      const card = h('div', 'sm-batch');
      const head = h('div', 'sm-batch-head');
      head.append(
        h('span', 'sm-batch-title', batch.title || '(başlıksız gönderim)'),
        h('span', 'sm-batch-meta',
          `${batch.sent_count} gitti${batch.fail_count ? ` · ${batch.fail_count} hata` : ''}`
          + ` · ${batch.credits} kredi · ${batch.encoding} · ${stampIso(batch.created_at)}`
          + (batch.created_by ? ` · ${batch.created_by}` : '')),
        h('span', 'sm-spacer'),
      );
      head.append(button('Aç', {
        onClick: async () => {
          if (card.dataset.open === '1') {
            card.dataset.open = '0';
            card.querySelector('.sm-messages')?.remove();
            return;
          }
          const detail = await api(`/api/bbd_sms/batches/${batch.batch_ref}`);
          if (!detail.ok) return;
          card.dataset.open = '1';
          const list = h('div', 'sm-messages');
          for (const message of detail.messages || []) {
            const row = h('div', `sm-message ${message.status}`);
            row.append(
              h('span', 'sm-message-name', message.student_name || message.kantin_id),
              h('span', 'sm-message-phone', message.phone || '—'),
              h('span', 'sm-message-state',
                { sent: 'gitti', failed: `hata: ${message.reason}`, skipped: message.reason }[message.status]
                || message.status),
              h('div', 'sm-message-text', message.text || ''),
            );
            list.append(row);
          }
          card.append(list);
        },
      }));
      card.append(head, h('div', 'sm-batch-body', batch.body));
      nodes.history.append(card);
    }
  } catch (error) {
    nodes.history.replaceChildren(h('div', 'sm-alert bad', error.message));
  }
}

async function renderQueue() {
  nodes.queue.replaceChildren(h('div', 'sm-hint', 'Yükleniyor…'));
  try {
    const payload_ = await api('/api/bbd_sms/queue');
    nodes.queue.replaceChildren();

    const summary = payload_.summary || {};
    const tiles = h('div', 'sm-tiles');
    const tile = (label, value, tone = '') => {
      const box = h('div', 'sm-tile');
      box.append(h('span', 'sm-tile-label', label),
        h('b', `sm-tile-value${tone ? ` ${tone}` : ''}`, String(value ?? 0)));
      return box;
    };
    tiles.append(
      tile('Bekliyor', summary.pending, summary.pending ? 'warn' : ''),
      tile('Gönderildi', summary.sent, 'good'),
      tile('Başarısız', summary.failed, summary.failed ? 'bad' : ''),
    );
    nodes.queue.append(tiles);
    nodes.queue.append(h('div', 'sm-hint',
      'Bu kuyruk kantinin ÖDEME SMS\'lerinindir (ödeme linki ve ödeme onayı). '
      + 'Buradan gönderdiğiniz serbest SMS\'ler kuyruğa girmez, anında gider.'));

    const rows = payload_.data || [];
    if (rows.length === 0) return;

    const table = h('div', 'sm-queue-table');
    for (const row of rows) {
      const line = h('div', `sm-queue-row ${row.state.toLowerCase()}`);
      line.append(
        h('span', 'sm-queue-to', row.to),
        h('span', 'sm-queue-template', row.template),
        h('span', 'sm-queue-state', row.state),
        h('span', 'sm-queue-attempts', row.attempts ? `${row.attempts} deneme` : ''),
        h('span', 'sm-queue-when', stampIso(row.sentAt ? new Date(row.sentAt).toISOString() : '')
          || '—'),
      );
      table.append(line);
    }
    nodes.queue.append(table);
  } catch (error) {
    nodes.queue.replaceChildren(h('div', 'sm-alert bad', error.message));
  }
}

// ------------------------------------------------------------- eylemler

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.previewBtn.disabled = true;
  nodes.send.disabled = true;
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

async function runPreview() {
  await withBusy('Prova hazırlanıyor…', async () => {
    preview = await api('/api/bbd_sms/preview', { method: 'POST', body: payload() });
    renderPreview();
    setStatus(preview.ok
      ? `${preview.summary.eligible} veliye gidecek · ${preview.summary.credits} kredi`
      : preview.error, !preview.ok);
  });
}

async function send() {
  if (!preview?.ok) return;
  const summary = preview.summary;

  const note = await confirmWithReason(nodes.root, {
    title: 'SMS gönder',
    description: `${summary.eligible} VELİYE gerçek SMS gidecek ve ${summary.credits} kredi `
      + 'harcanacak. GÖNDERİM GERİ ALINAMAZ. Bu gönderime bir başlık verin '
      + '(geçmişte bu adla görünecek).',
    confirmLabel: `${summary.eligible} SMS gönder`,
    placeholder: 'Örn. 15 Ağustos veli toplantısı duyurusu',
    danger: true,
  });
  if (note === null) return;

  await withBusy('SMS gönderiliyor…', async () => {
    const result = await api('/api/bbd_sms/send', {
      method: 'POST', body: payload({ title: note }),
    });
    if (!result.ok || result.sent === false) {
      toast(result.error || 'Gönderilemedi.', 'bad');
      return;
    }
    toast(`${result.sentCount} SMS gönderildi${result.failCount ? `, ${result.failCount} hata` : ''}`
      + ` · ${result.credits} kredi.`, result.failCount ? 'warn' : 'good');
    preview = null;
    picker.clear();
    nodes.body.value = '';
    nodes.title.value = '';
    await refresh();
    switchTab('history');
  });
}

async function savePreset() {
  const body = nodes.body.value.trim();
  if (!body) { toast('Önce mesajı yazın.', 'warn'); return; }
  const name = window.prompt('Hazır mesaj adı', nodes.title.value || 'Duyuru');
  if (!name) return;
  const result = await api('/api/bbd_sms/presets', { method: 'PUT', body: { name, body } });
  if (!result.ok) { toast(result.error || 'Kaydedilemedi.', 'bad'); return; }
  toast(`“${result.name}” kaydedildi.`, 'good');
  await refresh();
}

function switchTab(name) {
  tab = name;
  for (const [key, node] of Object.entries(nodes.tabs)) node.classList.toggle('on', key === name);
  nodes.paneSend.hidden = name !== 'send';
  nodes.paneHistory.hidden = name !== 'history';
  nodes.paneQueue.hidden = name !== 'queue';
  nodes.paneSettings.hidden = name !== 'settings';
  if (name === 'history') renderHistory();
  if (name === 'queue') renderQueue();
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'sm');
  nodes.root = view;
  toast = toaster(view);

  const bar = h('div', 'sm-bar');
  bar.append(
    h('span', 'sm-brand', 'Serbest SMS'),
    h('span', 'sm-brand-note', 'velilere duyuru · ödeme linki değil'),
    h('span', 'sm-spacer'),
    button('Yenile', { onClick: refresh }),
  );
  nodes.status = h('div', 'sm-status');

  const tabBar = h('div', 'sm-tabs');
  nodes.tabs = {};
  for (const [key, label] of [
    ['send', 'Gönder'], ['history', 'Geçmiş'],
    ['queue', 'Kuyruk'], ['settings', 'Netgsm ayarları'],
  ]) {
    const node = h('button', 'sm-tab', label);
    node.type = 'button';
    node.addEventListener('click', () => switchTab(key));
    nodes.tabs[key] = node;
    tabBar.append(node);
  }

  // --- gönder sekmesi: ÜÇ ADIM -----------------------------------------
  //
  // Bu ekranı yazılımdan anlamayan biri kullanacak. O yüzden akış numaralı
  // adımlara bölündü ve teknik sözcükler ("kuru prova", "yer tutucu",
  // "segment") ekrandan kaldırıldı; karşılıkları düz Türkçe.
  nodes.paneSend = h('div', 'sm-pane sm-pane-send');

  // ---- ADIM 1: kime -----------------------------------------------------
  picker = createPicker({ onChange: () => { preview = null; renderPreview(); } });
  const pickerBox = h('div', 'sm-picker');
  pickerBox.append(step(1, 'Kime gidecek?',
    'Seçtiğiniz her öğrencinin VELİSİNE gider. Öğrencinin kendisine asla SMS gitmez.'));

  // Sık kullanılan seçimler tek tıkla — tek tek işaretlemek zorunda kalmasın.
  const quick = h('div', 'sm-quick');
  const quickPick = (label, hint, filter) => {
    const node = h('button', 'sm-quick-btn', label);
    node.type = 'button';
    node.title = hint;
    node.addEventListener('click', () => {
      const ids = (state.students || []).filter(filter).map((item) => item.kantinId);
      picker.select(ids);
      preview = null;
      renderPreview();
    });
    return node;
  };
  quick.append(
    quickPick('Borcu olanlar', 'Bakiyesi borçta olan tüm öğrencilerin velileri.',
      (item) => Number(item.balance) > 0),
    quickPick('Veli telefonu olan herkes', 'Numarası kayıtlı tüm veliler.',
      (item) => Boolean(item.hasPhone)),
    quickPick('Seçimi temizle', 'Hiç kimse seçili kalmaz.', () => false),
  );
  pickerBox.append(quick, picker.node);
  nodes.selectionNote = h('div', 'sm-selection-note');
  pickerBox.append(nodes.selectionNote);

  // ---- ADIM 2: ne yazacaksınız -----------------------------------------
  const composeCol = h('div', 'sm-compose');
  composeCol.append(step(2, 'Ne yazacaksınız?',
    'Hazır bir mesajla başlayabilir ya da kendiniz yazabilirsiniz.'));

  nodes.presets = h('div', 'sm-presets');

  nodes.title = h('input', 'sm-input');
  nodes.title.type = 'text';
  nodes.title.placeholder = 'Bu gönderime bir ad verin (örn. Ağustos borç hatırlatması)';
  nodes.title.maxLength = 120;

  nodes.body = h('textarea', 'sm-textarea');
  nodes.body.rows = 6;
  nodes.body.maxLength = 480;
  nodes.body.placeholder = 'Sayın veli, …';
  nodes.body.addEventListener('input', () => {
    preview = null;
    scheduleMeasure();
    renderPreview();
  });

  // Yer tutucular DÜZ TÜRKÇE adlarıyla sunulur: kullanıcı "{borc}" yazmayı
  // bilmek zorunda değil, "Güncel borcu" düğmesine basar.
  const chipsBox = h('div', 'sm-insert');
  chipsBox.append(h('span', 'sm-insert-label', 'Mesaja ekle:'));
  const chips = h('div', 'sm-placeholders');
  for (const [token, label, hint] of [
    ['{ad}', 'Öğrencinin adı', 'Her veliye kendi çocuğunun adı yazılır.'],
    ['{borc}', 'Güncel borcu', 'Gönderim anındaki borç tutarı, her öğrenci için ayrı.'],
    ['{sinif}', 'Sınıfı', 'Öğrencinin sınıfı.'],
    ['{okul}', 'Kurum adı', 'Ayarlardaki kurum adı.'],
  ]) {
    const chip = h('button', 'sm-ph', label);
    chip.type = 'button';
    chip.title = hint;
    chip.addEventListener('click', () => {
      const start = nodes.body.selectionStart ?? nodes.body.value.length;
      nodes.body.value = nodes.body.value.slice(0, start) + token + nodes.body.value.slice(start);
      nodes.body.focus();
      nodes.body.selectionStart = nodes.body.selectionEnd = start + token.length;
      preview = null;
      scheduleMeasure();
      renderPreview();
    });
    chips.append(chip);
  }
  chipsBox.append(chips);

  // Metnin BAŞINA kantinin eklediği hazır cümleler.
  nodes.includeDebt = h('input');
  nodes.includeDebt.type = 'checkbox';
  const debtWrap = h('label', 'sm-check');
  debtWrap.append(nodes.includeDebt,
    h('span', undefined, 'Mesajın başına güncel borç cümlesi eklensin'));
  nodes.includeDebt.addEventListener('change', () => {
    preview = null; scheduleMeasure(); renderPreview();
  });

  nodes.includeDaily = h('input');
  nodes.includeDaily.type = 'checkbox';
  const dailyWrap = h('label', 'sm-check');
  dailyWrap.append(nodes.includeDaily,
    h('span', undefined, 'Mesajın başına bugünkü alışveriş özeti eklensin'));
  nodes.includeDaily.addEventListener('change', () => {
    preview = null; scheduleMeasure(); renderPreview();
  });

  nodes.meter = h('div', 'sm-meter');

  // Yazarken canlı örnek: seçili ilk öğrenciye tam olarak ne gidecek.
  nodes.sample = h('div', 'sm-sample');

  composeCol.append(
    nodes.presets,
    nodes.title,
    nodes.body,
    chipsBox,
    debtWrap,
    dailyWrap,
    nodes.sample,
    nodes.meter,
  );

  const composeTools = h('div', 'sm-compose-tools');
  composeTools.append(button('Bu mesajı hazır mesaj olarak kaydet', { onClick: savePreset }));
  composeCol.append(composeTools);

  // ---- ADIM 3: gönder ---------------------------------------------------
  const sideCol = h('div', 'sm-side');
  sideCol.append(step(3, 'Kontrol edin ve gönderin',
    'Gönderilmeden önce kime, hangi numaraya, hangi metnin gideceğini gösterir.'));

  nodes.preview = h('div', 'sm-preview');
  nodes.previewBtn = button('Kontrol et', {
    title: 'Hiçbir şey gönderilmez; ne gideceğini gösterir.',
    onClick: runPreview,
  });
  nodes.send = button('Gönder', { variant: 'danger', onClick: send });
  nodes.send.disabled = true;
  const actions = h('div', 'sm-actions');
  actions.append(nodes.previewBtn, nodes.send);
  sideCol.append(nodes.preview, actions);

  nodes.paneSend.append(pickerBox, composeCol, sideCol);

  // --- diğer sekmeler ---
  nodes.paneHistory = h('div', 'sm-pane sm-pane-scroll');
  nodes.history = h('div', 'sm-history');
  nodes.paneHistory.append(nodes.history);

  nodes.paneQueue = h('div', 'sm-pane sm-pane-scroll');
  nodes.queue = h('div', 'sm-queue');
  nodes.paneQueue.append(nodes.queue);

  nodes.paneSettings = h('div', 'sm-pane sm-pane-scroll');
  nodes.netgsmBox = h('div', 'sm-settings');
  nodes.paneSettings.append(nodes.netgsmBox);

  view.append(bar, nodes.status, tabBar,
    nodes.paneSend, nodes.paneHistory, nodes.paneQueue, nodes.paneSettings);
  root.replaceChildren(view);

  switchTab('send');
  renderPreview();
  scheduleMeasure();

  (async () => {
    await loadClasses();
    await refresh();
  })();

  return () => {
    window.clearTimeout(measureTimer);
    root.replaceChildren();
    preview = null;
    busy = false;
  };
}
