// Ödeme Talebi paneli — CANLI, GERÇEK SMS.
//
// Veliye borç bildirimi ve ödeme bağlantısı gönderir. Nakit tahsilat da burada:
// ödeme akışının parçasıdır ve borç kapanınca bekleyen talepler süresi dolmuş olur.
//
// TUTAR SEÇİLEMEZ. Kantin her talepte öğrencinin O ANKİ borcunu kullanır; açık
// bir talep varsa yenisi açılmaz, mevcut talep yeniden fiyatlanır ve aynı ref kalır.
//
// KANTİNDE ÇİFT GÖNDERİM KORUMASI YOK: aynı öğrenciye arka arkaya talep açmak
// her seferinde yeni SMS kuyruğa atar. Bu ekran son gönderimi bilir ve yakın
// zamanda gönderilmiş öğrenciyi varsayılan olarak atlar.

import {
  button, confirmWithReason, foldText, h, loadStyles, money, moneyInput,
  parseMoney, stamp, stampIso, toaster,
} from './kit.js';
import { createPicker } from './picker.js';

loadStyles(import.meta.url);

let api = null;
let capability = null;
let toast = null;

let state = { requests: [], students: [], templates: {}, collections: [], summary: {}, limits: {} };
let preview = null;
let classMap = new Map();
let tab = 'requests';
let statusFilter = '';
let query = '';
let allowRepeat = false;
let busy = false;

const nodes = {};
let picker = null;

const STATUS = {
  PENDING: { label: 'Bekliyor', tone: 'pending' },
  PAID: { label: 'Ödendi', tone: 'paid' },
  FAILED: { label: 'Başarısız', tone: 'failed' },
  EXPIRED: { label: 'Süresi doldu', tone: 'expired' },
};

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
    state = await api('/api/bbd_payment_request/workspace');
    picker.setStudents(state.students.map((item) => ({
      kantinId: item.kantinId,
      displayName: item.displayName,
      balance: item.balance,
      spendingLimit: item.spendingLimit,
      isBlocked: item.isBlocked,
    })), classMap);
    const summary = state.summary || {};
    setStatus(state.connected
      ? `${summary.debtors} borçlu · açık alacak ${money(summary.openDebt)} · `
        + `${summary.pending} bekleyen talep`
      : `Kantine ulaşılamadı — ${state.error || 'bilinmeyen hata'}`, !state.connected);
  } catch (error) {
    setStatus(`Çekirdek hatası: ${error.message}`, true);
  }
  renderSummary();
  renderRequests();
  renderTemplates();
  renderCollections();
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
    ['Borçlu öğrenci', String(summary.debtors ?? '—')],
    ['Açık alacak', money(summary.openDebt), 'debt'],
    ['Bekleyen talep', String(summary.pending ?? '—'), summary.pending ? 'warn' : ''],
    ['Ödenmiş talep', String(summary.paid ?? '—'), 'good'],
  ];
  for (const [label, value, tone] of tiles) {
    const box = h('div', 'pr-kpi-tile');
    box.append(h('span', 'pr-kpi-label', label),
      h('b', `pr-kpi-value${tone ? ` ${tone}` : ''}`, value));
    nodes.kpi.append(box);
  }
}

function visibleRequests() {
  const needle = foldText(query);
  return (state.requests || []).filter((item) => {
    if (statusFilter && item.status !== statusFilter) return false;
    if (!needle) return true;
    return foldText(`${item.studentName} ${item.ref}`).includes(needle);
  });
}

function renderRequests() {
  const rows = visibleRequests();
  nodes.requests.replaceChildren();
  nodes.requestCount.textContent =
    `${rows.length} talep görünüyor · kantin son 100 talebi veriyor`;

  if (rows.length === 0) {
    nodes.requests.append(h('div', 'pr-empty', 'Filtreye uyan talep yok.'));
    return;
  }

  const head = h('div', 'pr-row pr-head');
  for (const label of ['Öğrenci', 'Tutar', 'Durum', 'Yaş', 'Referans', 'Oluşturma', '']) {
    head.append(h('span', 'pr-cell', label));
  }
  nodes.requests.append(head);

  for (const item of rows) {
    const meta = STATUS[item.status] || { label: item.status, tone: '' };
    const row = h('div', `pr-row ${meta.tone}`);
    row.append(
      h('span', 'pr-cell strong', item.studentName),
      h('span', 'pr-cell num', money(item.amount)),
      h('span', 'pr-cell', meta.label),
      h('span', `pr-cell num${item.ageDays > 7 && item.status === 'PENDING' ? ' warn' : ''}`,
        item.ageDays === null ? '—' : `${item.ageDays} gün`),
      h('span', 'pr-cell ref', item.ref),
      h('span', 'pr-cell', stamp(item.createdAt)),
    );

    const actions = h('span', 'pr-cell pr-actions');

    const info = h('button', 'pr-mini', 'ayrıntı');
    info.type = 'button';
    info.title = 'Talebin bütün hikâyesi: SMS veliye ulaştı mı, kim gönderdi, borç değişti mi.';
    info.addEventListener('click', () => openDetail(item.ref));
    actions.append(info);

    if (item.status === 'PENDING') {
      const again = h('button', 'pr-mini', 'yeniden gönder');
      again.type = 'button';
      again.title = 'Güncel borca göre yeniden gönderir; aynı referans kalır.';
      again.addEventListener('click', () => resend(item));
      const stop = h('button', 'pr-mini danger', 'iptal');
      stop.type = 'button';
      stop.title = 'Talebi süresi dolmuş yapar; ödeme linki ölür. Satır silinmez.';
      stop.addEventListener('click', () => cancel(item));
      actions.append(again, stop);
    } else {
      // Kapanmış talep "yeniden gönderilemez" — kantin `not_pending` der.
      // Kullanıcının istediği şey aslında AYNI ÖĞRENCİYE yeni talep açmaktır.
      const fresh = h('button', 'pr-mini', 'yeni talep aç');
      fresh.type = 'button';
      fresh.title = 'Bu öğrenciye yeni bir ödeme talebi açar. Tutar eski tutar değil, '
        + 'kantindeki GÜNCEL borçtur.';
      fresh.addEventListener('click', () => reopen(item));
      actions.append(fresh);
    }
    row.append(actions);
    nodes.requests.append(row);
  }
}

function renderPreview() {
  nodes.preview.replaceChildren();
  nodes.previewBtn.disabled = busy || picker.size() === 0;
  nodes.send.disabled = busy || !preview?.ok || !(preview?.summary?.eligible > 0);

  if (!preview) {
    nodes.preview.append(h('div', 'pr-hint',
      'Öğrencileri seçip “Kuru prova” deyin. Tutar seçilmez — kantin gönderim '
      + 'anındaki güncel borcu kullanır. Borcu olmayan ve veli telefonu bulunmayan '
      + 'öğrenciler listeden düşer.'));
    return;
  }

  if (!preview.ok) {
    nodes.preview.append(h('div', 'pr-alert bad', preview.error));
    return;
  }

  const summary = preview.summary;
  const tiles = h('div', 'pr-tiles');
  const tile = (label, value, tone = '') => {
    const box = h('div', 'pr-tile');
    box.append(h('span', 'pr-tile-label', label),
      h('b', `pr-tile-value${tone ? ` ${tone}` : ''}`, value));
    return box;
  };
  tiles.append(
    tile('Gidecek', `${summary.eligible} veli`),
    tile('Toplam borç', money(summary.totalAmount), 'strong'),
    tile('Atlanan', String(summary.noDebt + summary.noPhone + summary.recent),
      (summary.noDebt + summary.noPhone + summary.recent) ? 'warn' : ''),
  );
  nodes.preview.append(tiles);

  if (summary.recent > 0) {
    const box = h('div', 'pr-alert warn pr-repeat');
    const label = h('label', 'pr-repeat-label');
    const check = h('input');
    check.type = 'checkbox';
    check.checked = allowRepeat;
    check.addEventListener('change', () => {
      allowRepeat = check.checked;
      runPreview();
    });
    label.append(check, h('span', undefined,
      `${summary.recent} öğrenciye son ${summary.cooldownMinutes} dakika içinde zaten `
      + 'talep gönderilmiş — bunlar atlanacak. Kantinde çift gönderim koruması yoktur, '
      + 'her istek veliye yeni bir SMS demektir. Yine de göndermek için işaretleyin.'));
    box.append(label);
    nodes.preview.append(box);
  }

  const problems = preview.rows.filter((row) => !['ok', 'repeat'].includes(row.verdict));
  if (problems.length > 0) {
    const box = h('div', 'pr-problems');
    box.append(h('div', 'pr-problems-head', `${problems.length} öğrenci işlenmeyecek`));
    for (const row of problems) {
      const line = h('div', `pr-problem ${row.verdict}`);
      line.append(h('span', 'pr-problem-name', row.name || row.kantinId),
        h('span', 'pr-problem-msg', row.message));
      box.append(line);
    }
    nodes.preview.append(box);
  }

  const ready = preview.rows.filter((row) => ['ok', 'repeat'].includes(row.verdict));
  if (ready.length > 0) {
    const box = h('div', 'pr-recipients');
    box.append(h('div', 'pr-recipients-head', `Gidecek talepler (${ready.length})`));
    for (const row of ready.slice(0, 60)) {
      const line = h('div', 'pr-recipient');
      line.append(
        h('span', 'pr-recipient-name', row.name),
        h('span', 'pr-recipient-phone', row.phone),
        h('span', 'pr-recipient-amount', money(row.amount)),
      );
      box.append(line);
    }
    if (ready.length > 60) {
      box.append(h('div', 'pr-more', `… ve ${ready.length - 60} kişi daha`));
    }
    nodes.preview.append(box);
  }
}

// ------------------------------------------------------------- şablonlar

/** Şablon kutularının ortak künyesi. */
const TEMPLATE_META = {
  payment_link: {
    title: 'Ödeme linki',
    hint: 'Talep açılınca veliye giden SMS. Ödeme bağlantısı bu mesajla gider.',
    tokens: [
      ['{name}', 'Öğrencinin adı'],
      ['{amount}', 'Borç tutarı'],
      ['{linkUrl}', 'Ödeme bağlantısı'],
    ],
    required: ['{linkUrl}'],
    sample: { '{name}': 'AHMET EREN', '{amount}': '240,00 ₺',
      '{linkUrl}': 'https://ode.bbd.k12.tr/x/A7K2' },
    fallback: 'Sayın veli, {name} için güncel borç {amount}. Ödemek için: {linkUrl}',
  },
  payment_confirmed: {
    title: 'Ödeme onayı',
    hint: 'Veli ödemeyi yapınca giden teşekkür SMS\'i.',
    tokens: [['{name}', 'Öğrencinin adı'], ['{amount}', 'Ödenen tutar']],
    required: [],
    sample: { '{name}': 'AHMET EREN', '{amount}': '240,00 ₺' },
    fallback: 'Sayın veli, {name} için {amount} tutarındaki ödemeniz alınmıştır. Teşekkür ederiz.',
  },
  cash_collected: {
    title: 'Nakit tahsilat',
    hint: 'Kantin nakit tahsilatta SMS GÖNDERMİYOR; şablon eski kayıtlar için duruyor.',
    tokens: [['{name}', 'Öğrencinin adı'], ['{amount}', 'Alınan tutar'],
      ['{balance}', 'Kalan borç']],
    required: [],
    sample: { '{name}': 'AHMET EREN', '{amount}': '100,00 ₺', '{balance}': '140,00 ₺' },
    fallback: 'Sayın veli, {name} için {amount} nakit tahsil edilmiştir. Kalan borç {balance}.',
  },
};

/** Türkçe karakter GSM-7 alfabesinde yok; mesajı UCS-2'ye düşürür. */
const UCS2 = /[ğĞıİşŞ]/;

/** Metnin kaç SMS'e böleneceği — kaba ama doğru hesap. */
function segmentsOf(text) {
  const unicode = UCS2.test(text);
  const single = unicode ? 70 : 160;
  const multi = unicode ? 67 : 153;
  const length = text.length;
  if (length === 0) return { unicode, count: 0, length, limit: single };
  if (length <= single) return { unicode, count: 1, length, limit: single };
  return { unicode, count: Math.ceil(length / multi), length, limit: multi };
}

/**
 * Ödeme SMS şablonları — düzenlenebilir, denetlenir, önizlenir.
 *
 * ÖNCEDEN SADECE BOŞ BİR METİN KUTUSUYDU: hangi yer tutucunun ne demek
 * olduğu, metnin veliye nasıl görüneceği ve kaç SMS tutacağı görünmüyordu.
 * Şablonu yanlış yazmanın bedeli gerçek: `{linkUrl}` düşerse veliye linksiz
 * SMS gider ve kimse ödeyemez.
 */
function renderTemplates() {
  nodes.templates.replaceChildren();
  const templates = state.templates || {};

  nodes.templates.append(h('div', 'pr-hint',
    'Bu şablonlar KANTİNDE saklanır ve ödeme akışında kullanılır. Tablet '
    + 'uygulamasında şablon düzenleme ekranı yoktur — buradan düzenlenir.'));

  const inputs = {};
  const dirty = new Set();

  const saveBtn = button('Şablonları kaydet', {
    variant: 'primary',
    onClick: async () => {
      const body = { templates: Object.fromEntries(
        Object.entries(inputs).map(([key, node]) => [key, node.value])) };
      const result = await api('/api/bbd_payment_request/templates', { method: 'PUT', body });
      if (!result.ok) { toast(result.error || 'Kaydedilemedi.', 'bad'); return; }
      toast('Şablonlar kaydedildi.', 'good');
      dirty.clear();
      await refresh();
    },
  });

  const syncSave = () => {
    const broken = Object.entries(inputs).some(([key, node]) => {
      const meta = TEMPLATE_META[key] || { required: [] };
      const value = node.value.trim();
      return !value || meta.required.some((token) => !value.includes(token));
    });
    saveBtn.disabled = broken || dirty.size === 0;
    saveBtn.title = broken
      ? 'Bir şablon boş ya da zorunlu alanı eksik; kaydedilemez.'
      : (dirty.size ? `${dirty.size} şablon değişti.` : 'Değişiklik yok.');
  };

  for (const [key, value] of Object.entries(templates)) {
    const meta = TEMPLATE_META[key] || {
      title: key, hint: '', tokens: [], required: [], sample: {}, fallback: '',
    };

    const box = h('div', 'pr-template');
    box.append(h('div', 'pr-template-title', meta.title));
    box.append(h('div', 'pr-template-hint', meta.hint));

    const area = h('textarea', 'pr-textarea');
    area.rows = 3;
    area.maxLength = 480;
    area.value = value;
    inputs[key] = area;

    // Yer tutucular TIKLANABİLİR: imlecin olduğu yere eklenir. Elle
    // "{linkUrl}" yazdırmak, yazım hatasıyla linki düşürmenin en kolay yolu.
    const chips = h('div', 'pr-tokens');
    for (const [token, label] of meta.tokens) {
      const chip = h('button', 'pr-token', label);
      chip.type = 'button';
      chip.title = `${token} — imlecin olduğu yere eklenir`;
      chip.addEventListener('click', () => {
        const at = area.selectionStart ?? area.value.length;
        area.value = area.value.slice(0, at) + token + area.value.slice(at);
        area.focus();
        area.selectionStart = area.selectionEnd = at + token.length;
        area.dispatchEvent(new Event('input'));
      });
      chips.append(chip);
    }
    box.append(chips, area);

    const problem = h('div', 'pr-template-problem');
    const sample = h('div', 'pr-template-sample');
    const meter = h('div', 'pr-template-meter');
    box.append(problem, meter, sample);

    const paint = () => {
      const text = area.value;
      const trimmed = text.trim();

      // Denetim
      const missing = meta.required.filter((token) => !trimmed.includes(token));
      if (!trimmed) {
        problem.textContent = 'Şablon boş bırakılamaz — kantin onu varsayılana '
          + 'düşürür ve ödeme linki SMS\'ten sessizce kaybolur.';
        problem.className = 'pr-template-problem bad';
      } else if (missing.length) {
        problem.textContent = `${missing.join(', ')} eksik. Bu olmadan veliye `
          + 'linksiz SMS gider ve ödeme yapılamaz.';
        problem.className = 'pr-template-problem bad';
      } else {
        problem.textContent = '';
        problem.className = 'pr-template-problem';
      }

      // Önizleme: veliye tam olarak böyle görünecek.
      let filled = text;
      for (const [token, replacement] of Object.entries(meta.sample || {})) {
        filled = filled.split(token).join(replacement);
      }
      sample.replaceChildren(
        h('div', 'pr-template-sample-label', 'Veliye böyle görünür'),
        h('div', 'pr-template-sample-text', filled || '—'),
      );

      // Maliyet: Türkçe karakter mesajı 70 karaktere düşürür.
      const seg = segmentsOf(filled);
      meter.replaceChildren(
        h('span', 'pr-template-meter-item',
          `${seg.length} karakter`),
        h('span', 'pr-template-meter-item',
          seg.unicode ? 'Türkçe karakter var (70/SMS)' : 'Standart alfabe (160/SMS)'),
        h('span', `pr-template-meter-item${seg.count > 1 ? ' warn' : ''}`,
          `${seg.count} SMS`),
      );

      dirty.add(key);
      syncSave();
    };

    area.addEventListener('input', paint);

    const tools = h('div', 'pr-template-tools');
    const reset = h('button', 'pr-mini', 'Örnek metni koy');
    reset.type = 'button';
    reset.title = 'Bu şablonu önerilen metinle doldurur.';
    reset.addEventListener('click', () => {
      area.value = meta.fallback;
      area.dispatchEvent(new Event('input'));
    });
    if (meta.fallback) tools.append(reset);
    box.append(tools);

    nodes.templates.append(box);

    // İlk çizim: kaydetme düğmesini kirletmeden.
    const wasDirty = new Set(dirty);
    paint();
    dirty.clear();
    for (const item of wasDirty) dirty.add(item);
  }

  syncSave();
  nodes.templates.append(saveBtn);
}

// -------------------------------------------------------- nakit tahsilat

/**
 * Nakit tahsilat sekmesinin ÜST YARISI: öğrenci ara → tutar → tahsil et.
 *
 * ÖNCEDEN BU SEKME YALNIZ GEÇMİŞİ GÖSTERİYORDU. Tahsil düğmesi "Toplu talep
 * gönder" sekmesindeki seçicinin yanındaydı; nakit tahsilat sekmesine giren
 * kullanıcı tahsilat EKLEYEMİYORDU. Sekme artık kendi kendine yetiyor.
 */
function renderCollectForm() {
  const box = nodes.collectForm;
  box.replaceChildren();

  const debtors = (state.students || [])
    .filter((item) => item.balance > 0)
    .sort((a, b) => b.balance - a.balance);

  if (!state.connected) {
    box.append(h('div', 'pr-alert bad',
      'Kantine ulaşılamıyor — güncel borç okunamadığı için tahsilat yapılamaz.'));
    return;
  }
  if (debtors.length === 0) {
    box.append(h('div', 'pr-hint',
      'Borcu olan öğrenci yok — tahsil edilecek bir şey görünmüyor.'));
    return;
  }

  box.append(h('p', 'pr-detail-hint',
    'Elden alınan parayı buraya yazın. Tutar borçtan fazla olamaz; borç tamamen '
    + 'kapanırsa o öğrencinin BEKLEYEN ödeme talepleri süresi dolmuş olur.'));

  const form = h('div', 'pr-collect-form');

  const search = h('input', 'kit-input pr-collect-search');
  search.type = 'search';
  search.placeholder = `Borçlu öğrenci ara (${debtors.length} kişi)`;
  search.setAttribute('aria-label', 'Borçlu öğrenci ara');

  const list = h('div', 'pr-collect-list');
  const amountRow = h('div', 'pr-collect-amount');
  const amount = h('input', 'kit-input');
  amount.type = 'text';
  amount.inputMode = 'decimal';
  amount.placeholder = '0,00';
  amount.setAttribute('aria-label', 'Tahsil edilen tutar');
  const note = h('input', 'kit-input pr-collect-note');
  note.type = 'text';
  note.placeholder = 'Not (isteğe bağlı)';
  note.maxLength = 255;
  const error = h('div', 'kit-dialog-error');
  const chosen = h('div', 'pr-collect-chosen');

  let selected = null;

  const quick = h('div', 'pr-collect-quick');
  const quickButton = (label, factor) => {
    const node = h('button', 'pr-mini', label);
    node.type = 'button';
    node.addEventListener('click', () => {
      if (!selected) return;
      amount.value = moneyInput(Math.round(selected.balance * factor));
      error.textContent = '';
      amount.focus();
    });
    return node;
  };
  quick.append(quickButton('Borcun tamamı', 1), quickButton('Yarısı', 0.5));

  const collectBtn = button('Tahsil et', {
    variant: 'primary',
    onClick: async () => {
      if (!selected) { error.textContent = 'Önce bir öğrenci seçin.'; return; }
      const kurus = parseMoney(amount.value);
      if (kurus === null || kurus < 1) { error.textContent = 'Tutarı 120,50 gibi yazın.'; return; }
      if (kurus > selected.balance) {
        error.textContent = `Tutar borçtan fazla olamaz (borç ${money(selected.balance)}).`;
        return;
      }
      const student = selected;
      const confirmed = await confirmWithReason(nodes.root, {
        title: 'Nakit tahsilat',
        description: `${student.displayName} adına ${money(kurus)} elden alındı olarak `
          + `işlenecek. Güncel borç ${money(student.balance)}. `
          + (kurus >= student.balance
            ? 'Borç TAMAMEN kapanacak ve bekleyen ödeme talepleri süresi dolacak. '
            : `Kalan borç ${money(student.balance - kurus)} olacak. `)
          + 'Kayıt kantine yazılır ve geri alınamaz.',
        confirmLabel: 'Tahsil et',
        placeholder: 'Gerekçe (teyit içindir)',
        danger: true,
      });
      if (confirmed === null) return;

      await withBusy('Tahsilat işleniyor…', async () => {
        const result = await api('/api/bbd_payment_request/collections', {
          method: 'POST',
          body: { kantinId: student.kantinId, amount: kurus, note: note.value.trim() },
        });
        if (!result.ok) { toast(result.error || 'Tahsilat yazılamadı.', 'bad'); return; }
        toast(`${money(result.amount)} tahsil edildi. Kalan borç ${money(result.balance)}.`
          + (result.settled ? ' Borç kapandı, bekleyen talepler süresi doldu.' : ''), 'good');
        selected = null;
        amount.value = '';
        note.value = '';
        await refresh();
      });
    },
  });

  const paintChosen = () => {
    chosen.replaceChildren();
    if (!selected) {
      chosen.append(h('span', 'pr-hint', 'Öğrenci seçilmedi.'));
      return;
    }
    chosen.append(
      h('b', 'pr-collect-chosen-name', selected.displayName),
      h('span', 'pr-collect-chosen-debt', `güncel borç ${money(selected.balance)}`),
    );
  };

  const paint = () => {
    const needle = foldText(search.value);
    const rows = debtors
      .filter((item) => !needle || foldText(item.displayName).includes(needle))
      .slice(0, 40);
    list.replaceChildren();
    if (rows.length === 0) {
      list.append(h('div', 'pr-hint', 'Aramaya uyan borçlu yok.'));
      return;
    }
    for (const item of rows) {
      const row = h('button', 'pr-collect-item');
      row.type = 'button';
      row.classList.toggle('on', selected?.kantinId === item.kantinId);
      row.append(
        h('span', 'pr-collect-name', item.displayName),
        h('span', 'pr-collect-debt', money(item.balance)),
      );
      if (!item.hasPhone) row.append(h('span', 'pr-badge dim', 'veli tel. yok'));
      row.addEventListener('click', () => {
        selected = item;
        amount.value = moneyInput(item.balance);
        error.textContent = '';
        paintChosen();
        paint();
        amount.focus();
      });
      list.append(row);
    }
  };

  search.addEventListener('input', paint);
  paint();
  paintChosen();

  amountRow.append(amount, note, collectBtn);
  form.append(search, list, chosen, quick, amountRow, error);
  box.append(form);
}

function renderCollections() {
  renderCollectForm();
  nodes.collections.replaceChildren();
  const rows = state.collections || [];
  if (rows.length === 0) {
    nodes.collections.append(h('div', 'pr-hint',
      'Bu panelden yapılmış nakit tahsilat yok.'));
    return;
  }
  const head = h('div', 'pr-row pr-head pr-row-collection');
  for (const label of ['Öğrenci', 'Tutar', 'Kalan borç', 'Not', 'Zaman', 'Kullanıcı']) {
    head.append(h('span', 'pr-cell', label));
  }
  nodes.collections.append(head);
  for (const row of rows) {
    const line = h('div', `pr-row pr-row-collection ${row.status}`);
    line.append(
      h('span', 'pr-cell strong', row.student_name || row.kantin_id),
      h('span', 'pr-cell num', money(row.amount)),
      h('span', 'pr-cell num', money(row.balance_after)),
      h('span', 'pr-cell', row.note || (row.status === 'ok' ? '' : row.reason)),
      h('span', 'pr-cell', stampIso(row.created_at)),
      h('span', 'pr-cell', row.created_by || ''),
    );
    nodes.collections.append(line);
  }
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
    renderPreview();
  }
}

async function runPreview() {
  await withBusy('Prova hazırlanıyor…', async () => {
    preview = await api('/api/bbd_payment_request/preview', {
      method: 'POST', body: { students: picker.selection(), allowRepeat },
    });
    renderPreview();
    setStatus(preview.ok
      ? `${preview.summary.eligible} veliye talep gidecek · toplam ${money(preview.summary.totalAmount)}`
      : preview.error, !preview.ok);
  });
}

async function send() {
  if (!preview?.ok) return;
  const summary = preview.summary;

  const note = await confirmWithReason(nodes.root, {
    title: 'Ödeme talebi gönder',
    description: `${summary.eligible} VELİYE ödeme linki SMS'i gidecek. Toplam borç `
      + `${money(summary.totalAmount)}. Tutarı kantin gönderim anında yeniden hesaplar. `
      + 'GÖNDERİM GERİ ALINAMAZ. Bu gönderim için kısa bir not yazın.',
    confirmLabel: `${summary.eligible} talep gönder`,
    placeholder: 'Örn. Ağustos ayı borç bildirimi',
    danger: true,
  });
  if (note === null) return;

  await withBusy('Talepler gönderiliyor…', async () => {
    const result = await api('/api/bbd_payment_request/send', {
      method: 'POST', body: { students: picker.selection(), allowRepeat, note },
    });
    if (!result.ok || result.sent === false) {
      toast(result.error || 'Gönderilemedi.', 'bad');
      return;
    }
    toast(`${result.okCount} talep gönderildi${result.failCount ? `, ${result.failCount} hata` : ''}.`,
      result.failCount ? 'warn' : 'good');
    preview = null;
    allowRepeat = false;
    picker.clear();
    await refresh();
    switchTab('requests');
  });
}

async function resend(item) {
  const ok = await confirmWithReason(nodes.root, {
    title: 'Talebi yeniden gönder',
    description: `${item.studentName} için ödeme linki SMS'i tekrar gidecek. Tutar `
      + 'güncel borca göre yeniden hesaplanır ve aynı referans korunur.',
    confirmLabel: 'Yeniden gönder',
    placeholder: 'Gerekçe (kayda geçmez, teyit içindir)',
    danger: false,
  });
  if (ok === null) return;

  await withBusy('Gönderiliyor…', async () => {
    const result = await api(`/api/bbd_payment_request/requests/${item.ref}/resend`,
      { method: 'POST' });
    if (!result.ok) { toast(result.error || 'Gönderilemedi.', 'bad'); return; }
    toast(`Yeniden gönderildi · ${money(result.amount)}`, 'good');
    await refresh();
  });
}

// ------------------------------------------------------------ ayrıntı

const SMS_STATE = {
  SENT: ['gönderildi', 'good'],
  PENDING: ['kuyrukta', 'warn'],
  FAILED: ['gönderilemedi', 'bad'],
  CANCELLED: ['iptal', 'dim'],
};

/** Ayrıntı penceresindeki bir bölüm. */
function detailSection(title, hint) {
  const box = h('section', 'pr-detail-section');
  box.append(h('h4', 'pr-detail-h', title));
  if (hint) box.append(h('p', 'pr-detail-hint', hint));
  return box;
}

/** Etiket–değer satırı. */
function detailRow(label, value, tone = '') {
  const row = h('div', 'pr-detail-row');
  row.append(h('span', 'pr-detail-label', label),
    h('span', `pr-detail-value${tone ? ` ${tone}` : ''}`, value));
  return row;
}

/**
 * Talebin bütün hikâyesini gösterir.
 *
 * Üç ayrı kaynağı yan yana koyar: kantinin talep kaydı, kantinin SMS kuyruğu
 * ve bu panelin kendi gönderim kaydı. "Talep açıldı" ile "veli mesajı aldı"
 * aynı şey değildir; ekran ikisini karıştırmaz.
 */
async function openDetail(ref) {
  const overlay = h('div', 'kit-overlay');
  const card = h('div', 'kit-dialog pr-detail');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  card.append(h('h3', 'kit-dialog-title', 'Talep ayrıntısı'));
  const body = h('div', 'pr-detail-body');
  body.append(h('div', 'pr-hint', 'Okunuyor…'));
  card.append(body);

  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Kapat', { onClick: close }));
  card.append(actions);

  overlay.append(card);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  nodes.root.append(overlay);

  let data;
  try {
    data = await api(`/api/bbd_payment_request/requests/${ref}`);
  } catch (error) {
    body.replaceChildren(h('div', 'pr-alert bad', error.message || 'Okunamadı.'));
    return;
  }
  if (!data.ok) {
    body.replaceChildren(h('div', 'pr-alert bad', data.error || 'Talep bulunamadı.'));
    return;
  }

  body.replaceChildren();
  const request = data.request || {};
  const student = data.student || {};
  const meta = STATUS[request.status] || { label: request.status, tone: '' };

  // --- künye ---------------------------------------------------------
  const head = detailSection('Talep');
  head.append(
    detailRow('Öğrenci', student.displayName || request.studentName || '—', 'strong'),
    detailRow('Veli telefonu', student.parentPhone || 'tanımlı değil',
      student.parentPhone ? '' : 'bad'),
    detailRow('Tutar', money(request.amount), 'strong'),
    detailRow('Durum', meta.label, meta.tone),
    detailRow('Referans', request.ref || ref, 'mono'),
    detailRow('Oluşturma', stamp(request.createdAt)),
    detailRow('Ödeme', request.paidAt ? stamp(request.paidAt) : '—'),
    detailRow('Yaş', request.ageDays === null || request.ageDays === undefined
      ? '—' : `${request.ageDays} gün`),
  );
  body.append(head);

  // --- borç kayması --------------------------------------------------
  // Talep tutarı DONMUŞTUR; borç akmaya devam eder. Aradaki fark, "veliye
  // gönderilen tutar artık doğru değil" demektir ve ekranda görünmelidir.
  if (student.balance !== null && student.balance !== undefined) {
    const drift = data.drift ?? 0;
    const box = detailSection('Güncel borç');
    box.append(detailRow('Şu anki borç', money(student.balance),
      student.balance > 0 ? 'debt' : 'good'));
    if (request.status === 'PENDING' && drift !== 0) {
      box.append(h('div', `pr-alert ${drift > 0 ? 'warn' : ''}`,
        drift > 0
          ? `Talep açıldığından beri borç ${money(drift)} ARTTI. Velinin elindeki `
            + 'link eski tutarı gösteriyor olabilir; “yeniden gönder” tutarı tazeler.'
          : `Talep açıldığından beri borç ${money(-drift)} AZALDI (tahsilat ya da `
            + 'ödeme). Bu talep artık güncel borçtan fazlasını istiyor.'));
    }
    body.append(box);
  }

  // --- SMS akıbeti ---------------------------------------------------
  const messages = data.messages || [];
  const sms = detailSection('Veliye giden SMS',
    'Kantinin SMS kuyruğundan okunur. “Talep açıldı” ile “veli mesajı aldı” '
    + 'aynı şey değildir — mesaj kuyrukta bekliyor ya da başarısız olmuş olabilir.');
  if (messages.length === 0) {
    sms.append(h('div', 'pr-hint', 'Bu öğrenci için kuyrukta kayıt görünmüyor. '
      + 'Kantin son 200 kaydı verir; eski mesajlar listede olmayabilir.'));
  } else {
    for (const item of messages.slice(0, 10)) {
      const [label, tone] = SMS_STATE[item.status] || [item.status || '—', ''];
      const line = h('div', 'pr-detail-sms');
      line.append(
        h('span', `pr-badge ${tone}`, label),
        h('span', 'pr-detail-sms-when', stamp(item.sentAt || item.createdAt)),
        h('span', 'pr-detail-sms-to', item.phone || ''),
      );
      if (item.template) line.append(h('span', 'pr-detail-sms-tpl', item.template));
      if (Number(item.attempts || 0) > 1) {
        line.append(h('span', 'pr-detail-sms-try', `${item.attempts} deneme`));
      }
      if (item.error || item.lastError) {
        line.append(h('span', 'pr-detail-sms-err', item.error || item.lastError));
      }
      sms.append(line);
    }
  }
  body.append(sms);

  // --- bu panelden yapılanlar ----------------------------------------
  const entries = data.entries || [];
  if (entries.length) {
    const box = detailSection('Bu panelden gönderimler',
      'Kim, ne zaman, hangi partide gönderdi.');
    for (const row of entries.slice(0, 10)) {
      box.append(detailRow(
        stampIso(row.created_at),
        `${row.status === 'queued' ? 'kuyruğa alındı' : row.status}`
        + `${row.reason ? ` · ${row.reason}` : ''}`
        + `${row.batch_ref ? ` · ${row.batch_ref}` : ''}`,
        row.status === 'queued' ? '' : 'bad'));
    }
    body.append(box);
  }

  const collections = data.collections || [];
  if (collections.length) {
    const box = detailSection('Bu öğrenciden nakit tahsilat');
    for (const row of collections.slice(0, 10)) {
      box.append(detailRow(stampIso(row.created_at),
        `${money(row.amount)} · kalan ${money(row.balance_after)} · ${row.created_by || ''}`));
    }
    body.append(box);
  }
}

/** Kapanmış talebin öğrencisine YENİ talep açar. Tutar güncel borçtur. */
async function reopen(item) {
  const student = state.students.find((row) => row.kantinId === item.studentId);
  const debt = student ? student.balance : null;

  if (debt !== null && debt <= 0) {
    toast(`${item.studentName} için güncel borç yok — kantin talep açmaz.`, 'warn');
    return;
  }

  const ok = await confirmWithReason(nodes.root, {
    title: 'Yeni ödeme talebi aç',
    description: `${item.studentName} için YENİ bir talep açılacak ve veliye ödeme linki `
      + `SMS'i gidecek. Tutar eski talebin ${money(item.amount)} tutarı DEĞİL, `
      + `kantindeki güncel borçtur${debt === null ? '' : ` (${money(debt)})`}. `
      + 'GÖNDERİM GERİ ALINAMAZ.',
    confirmLabel: 'Yeni talep gönder',
    placeholder: 'Örn. ikinci hatırlatma',
    danger: true,
  });
  if (ok === null) return;

  await withBusy('Talep açılıyor…', async () => {
    const result = await api(`/api/bbd_payment_request/requests/${item.ref}/reopen`,
      { method: 'POST' });
    if (!result.ok || result.sent === false) {
      toast(result.error || 'Talep açılamadı.', 'bad');
      return;
    }
    const failed = result.failCount || 0;
    toast(failed
      ? `Talep açılamadı: ${(result.entries || [])[0]?.reason || 'bilinmeyen sebep'}`
      : 'Yeni talep açıldı, veliye SMS kuyruğa alındı.', failed ? 'bad' : 'good');
    await refresh();
  });
}

async function cancel(item) {
  const ok = await confirmWithReason(nodes.root, {
    title: 'Talebi iptal et',
    description: `${item.studentName} için ${money(item.amount)} tutarındaki talep `
      + 'süresi dolmuş sayılacak ve velinin elindeki ödeme linki çalışmayacak. '
      + 'KAYIT SİLİNMEZ, yalnız durumu değişir.',
    confirmLabel: 'İptal et',
    placeholder: 'Gerekçe (teyit içindir)',
  });
  if (ok === null) return;

  await withBusy('İptal ediliyor…', async () => {
    const result = await api(`/api/bbd_payment_request/requests/${item.ref}/cancel`,
      { method: 'POST' });
    if (!result.ok) { toast(result.error || 'İptal edilemedi.', 'bad'); return; }
    toast('Talep iptal edildi.', 'good');
    await refresh();
  });
}

function switchTab(name) {
  tab = name;
  for (const [key, node] of Object.entries(nodes.tabs)) node.classList.toggle('on', key === name);
  nodes.paneRequests.hidden = name !== 'requests';
  nodes.paneSend.hidden = name !== 'send';
  nodes.paneCollections.hidden = name !== 'collections';
  nodes.paneTemplates.hidden = name !== 'templates';
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'pr');
  nodes.root = view;
  toast = toaster(view);

  nodes.kpi = h('div', 'pr-kpi');
  nodes.status = h('div', 'pr-status');

  const tabBar = h('div', 'pr-tabs');
  nodes.tabs = {};
  for (const [key, label] of [
    ['requests', 'Talepler'], ['send', 'Toplu talep gönder'],
    ['collections', 'Nakit tahsilat'], ['templates', 'SMS şablonları'],
  ]) {
    const node = h('button', 'pr-tab', label);
    node.type = 'button';
    node.addEventListener('click', () => switchTab(key));
    nodes.tabs[key] = node;
    tabBar.append(node);
  }

  // --- talepler ---
  nodes.paneRequests = h('div', 'pr-pane pr-pane-scroll');
  const tools = h('div', 'pr-tools');
  const search = h('input', 'pr-input');
  search.type = 'search';
  search.placeholder = 'Öğrenci ya da referans ara';
  search.addEventListener('input', () => { query = search.value; renderRequests(); });

  const filter = h('select', 'pr-select');
  for (const [value, label] of [
    ['', 'Tüm durumlar'], ['PENDING', 'Bekliyor'], ['PAID', 'Ödendi'],
    ['FAILED', 'Başarısız'], ['EXPIRED', 'Süresi doldu'],
  ]) {
    const option = h('option', undefined, label);
    option.value = value;
    filter.append(option);
  }
  filter.addEventListener('change', () => { statusFilter = filter.value; renderRequests(); });

  tools.append(search, filter, h('span', 'pr-spacer'), button('Yenile', { onClick: refresh }));
  nodes.requestCount = h('div', 'pr-count');
  nodes.requests = h('div', 'pr-table');
  nodes.paneRequests.append(tools, nodes.requestCount, nodes.requests);

  // --- toplu gönderim ---
  nodes.paneSend = h('div', 'pr-pane pr-pane-send');
  picker = createPicker({ onChange: () => { preview = null; allowRepeat = false; renderPreview(); } });
  const pickerBox = h('div', 'pr-picker');
  pickerBox.append(
    h('div', 'pr-col-title', 'Öğrenciler'),
    h('div', 'pr-col-hint',
      'Talep VELİYE gider. Tutar seçilmez — gönderim anındaki güncel borç kullanılır. '
      + 'Borçlu olmayan ve veli telefonu bulunmayan öğrenciler provada ayrılır.'),
    picker.node,
  );

  const sideCol = h('div', 'pr-side');
  nodes.preview = h('div', 'pr-preview');
  nodes.previewBtn = button('Kuru prova', {
    title: 'Hiçbir şey gönderilmez; ne olacağını gösterir.',
    onClick: runPreview,
  });
  nodes.send = button('Talepleri gönder', { variant: 'danger', onClick: send });
  nodes.send.disabled = true;
  const actions = h('div', 'pr-actions');
  actions.append(nodes.previewBtn, nodes.send);
  sideCol.append(nodes.preview, actions,
    button('Nakit tahsilat', {
      title: 'Nakit tahsilat sekmesine geçer; orada öğrenci arayıp tutarı yazarsınız.',
      onClick: () => switchTab('collections'),
    }));
  nodes.paneSend.append(pickerBox, sideCol);

  // --- tahsilat geçmişi ---
  nodes.paneCollections = h('div', 'pr-pane pr-pane-scroll');
  nodes.collectForm = h('div', 'pr-collect-card');
  nodes.collections = h('div', 'pr-table');
  nodes.paneCollections.append(
    h('div', 'pr-col-title', 'Nakit tahsil et'),
    nodes.collectForm,
    h('div', 'pr-col-title', 'Bu panelden yapılan nakit tahsilatlar'),
    nodes.collections);

  // --- şablonlar ---
  nodes.paneTemplates = h('div', 'pr-pane pr-pane-scroll');
  nodes.templates = h('div', 'pr-templates');
  nodes.paneTemplates.append(nodes.templates);

  view.append(nodes.kpi, nodes.status, tabBar,
    nodes.paneRequests, nodes.paneSend, nodes.paneCollections, nodes.paneTemplates);
  root.replaceChildren(view);

  switchTab('requests');
  renderPreview();

  (async () => {
    await loadClasses();
    await refresh();
  })();

  return () => {
    root.replaceChildren();
    preview = null;
    allowRepeat = false;
    busy = false;
  };
}
