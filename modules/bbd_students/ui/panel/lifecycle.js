// Öğrenci yaşam döngüsü ekranları — pasifleştir, yeniden etkinleştir, kalıcı sil
// (bbd-akademi docs/integrations/km-merkezi-senkron.md §5).
//
// KARAR BACKEND'DEDİR (K9): düğmeler izne göre gizlenir ama her uç kendi iznini
// (`bbd_students.lifecycle`, `bbd_students.delete`) yeniden sorar; ön koşullar
// (bakiye 0, açık talep, kurum cari borcu) sunucuda denetlenir. Bu dosya yalnız
// soruyu sorar ve cevabı okunur kılar.

// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { button, dialogA11y } from '../../ui-kit/kit.js';

export const REASONS = [
  ['left', 'Ayrıldı'],
  ['graduated', 'Mezun'],
  ['suspended', 'Geçici durdurma'],
  ['other', 'Diğer'],
];

export const REASON_LABEL = Object.fromEntries(REASONS);

const ACTION_LABEL = {
  created: 'KM’de eklendi',
  created_at_canteen: 'Kasadan eklendi',
  scheduled: 'Pasifleştirme planlandı',
  schedule_cancelled: 'Plan iptal edildi',
  deactivated: 'Pasifleştirildi',
  reactivated: 'Yeniden etkinleştirildi',
  deleted: 'Kalıcı silindi',
};

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function todayLocal() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function lira(kurus) {
  return `${(Math.abs(Number(kurus) || 0) / 100).toLocaleString('tr-TR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })} ₺`;
}

/** Ortak pencere iskeleti (kit-dialog sınıfları; kabuğun stili). */
function dialog(root, title, description) {
  const overlay = h('div', 'kit-overlay');
  const card = h('div', 'kit-dialog st-lc-dialog');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.append(h('h3', 'kit-dialog-title', title));
  if (description) card.append(h('p', 'kit-dialog-text', description));
  overlay.append(card);
  root.append(overlay);
  // Başlık bağı, odak tuzağı ve kapanınca odağın geri dönüşü (0.5.3).
  return { overlay, card, release: dialogA11y(card) };
}

/**
 * Pasifleştir penceresi. `students` bir ya da çok öğrenci (toplu). Borçlu
 * öğrenci varsa uyarır ve "ödeme talebi gönder" önerir (§11 karar 2b).
 *
 * @returns {Promise<{reason, effectiveAt, note}|null>}
 */
export function askDeactivate(root, { students }) {
  return new Promise((resolve) => {
    const many = students.length > 1;
    const name = many ? `${students.length} öğrenci` : (students[0]?.displayName || 'Öğrenci');
    const { overlay, card, release } = dialog(root, many ? `${name} pasifleştirilecek` : `${name} — pasifleştir`,
      'Kantinde satış kapanır (bakiye ve borç korunur, tahsilat ve ödeme linki çalışır), '
      + 'tablet girişi kısıtlanır, BBD Akademi girişi ve yalnız bu öğrenciye bağlı veli '
      + 'girişi kapanır. Açık sınıf üyelikleri kapanır. Veriler saklanır; yeniden '
      + 'etkinleştirilebilir.');

    const reason = h('select', 'kit-input st-input st-select');
    for (const [value, label] of REASONS) reason.append(new Option(label, value));
    const when = h('input', 'kit-input st-input');
    when.type = 'date';
    when.min = todayLocal();
    when.value = todayLocal();
    const note = h('textarea', 'kit-input st-input st-textarea');
    note.maxLength = 500;
    note.placeholder = 'Not (isteğe bağlı; yalnız KM’de kalır)';

    const grid = h('div', 'st-lc-grid');
    const label = (text, input) => {
      const wrap = h('label', 'st-field');
      wrap.append(h('span', 'st-label', text), input);
      return wrap;
    };
    grid.append(label('Gerekçe', reason), label('Geçerlilik (bugün ya da ileri tarih)', when));
    card.append(grid, label('Not', note));

    const debtors = students.filter((student) => Number(student.balance) > 0);
    if (debtors.length) {
      const total = debtors.reduce((sum, student) => sum + Number(student.balance), 0);
      const warn = h('div', 'st-lc-warn');
      warn.append(h('b', null, many
        ? `${debtors.length} öğrencinin kantin borcu var (toplam ${lira(total)}).`
        : `Kantin borcu: ${lira(total)}.`));
      warn.append(h('span', null,
        ' Borç korunur; kasada nakit ya da SMS ödeme linkiyle kapanabilir. Pasifleştirmeden '
        + 'önce öğrenci kartındaki “Kantin tahsilatı ve ödeme talebi” bölümünden ödeme '
        + 'talebi göndermeniz önerilir.'));
      card.append(warn);
    }

    const error = h('div', 'kit-dialog-error');
    card.append(error);
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(value);
    };
    const submit = () => {
      if (!when.value || when.value < todayLocal()) {
        error.textContent = 'Geçmiş bir güne pasifleştirme yazılamaz.';
        return;
      }
      close({
        reason: reason.value,
        effectiveAt: when.value === todayLocal() ? null : when.value,
        note: note.value.trim(),
      });
    };
    const onKey = (event) => {
      if (event.key === 'Escape') close(null);
    };
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }),
      button(many ? `${name} pasifleştir` : 'Pasifleştir', { variant: 'danger', onClick: submit }));
    card.append(actions);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    reason.focus();
  });
}

const BLOCKER_LINK = {
  not_inactive: ['deactivate', 'Pasifleştir'],
  balance_not_zero: ['canteen', 'Kantin bölümüne git'],
  open_payment_request: ['canteen', 'Ödeme talebine git'],
  institution_debt: ['finance', 'Kurum carisini aç'],
};

/**
 * Ön kontrol sonucu: silmeyi engelleyenler, her biri açık Türkçe ve (varsa)
 * ilgili yere giden düğmeyle. `links`: {deactivate, canteen, finance} → fn | null.
 */
export function deleteBlockers(check, links = {}) {
  const box = h('div', 'st-lc-warn bad st-del-blockers');
  box.setAttribute('role', 'alert');
  box.append(h('b', null, 'Kalıcı silinemez — önce şunlar çözülmeli:'));
  const list = h('ul', 'st-del-list');
  for (const item of check.blockers || []) {
    const row = h('li');
    row.append(h('span', null, item.message));
    const [key, label] = BLOCKER_LINK[item.code] || [];
    if (key && typeof links[key] === 'function') {
      row.append(' ', button(label, { variant: key === 'deactivate' ? 'primary' : 'ghost', onClick: () => links[key]() }));
    }
    list.append(row);
  }
  box.append(list);
  for (const note of check.notes || []) box.append(h('p', 'st-hint', note.message));
  return box;
}

/**
 * Kalıcı silme penceresi — ön kontrolden GEÇMİŞ öğrenci için: neyin silineceği,
 * geri alınamaz uyarısı, adı aynen yazdırır ve kendi PIN'ini ister. Enter yalnız
 * alanlardayken onaylar ("Vazgeç"te Enter onaylamaz); Esc vazgeçer.
 *
 * @returns {Promise<{name: string, pin: string}|null>}
 */
export function askDelete(root, { student, check = null }) {
  return new Promise((resolve) => {
    const name = check?.displayName || student.displayName || '';
    const { overlay, card, release } = dialog(root, `${name || 'Öğrenci'} — kalıcı olarak sil`,
      'GERİ ALINAMAZ. Ön kontrol temiz: öğrenci pasif, kantin bakiyesi 0, açık ödeme talebi ve kurum '
      + 'cari borcu yok.');
    const items = check?.willDelete || [];
    if (items.length) {
      const what = h('ul', 'st-del-list');
      for (const text of items) what.append(h('li', null, text));
      card.append(h('b', null, 'Silinecekler:'), what);
    }
    for (const note of check?.notes || []) card.append(h('p', 'st-lc-warn', note.message));
    const field = (text, input) => {
      const wrap = h('label', 'st-field');
      wrap.append(h('span', 'st-label', text), input);
      return wrap;
    };
    const input = h('input', 'kit-input');
    input.type = 'text';
    input.autocomplete = 'off';
    input.maxLength = 160;
    const pin = h('input', 'kit-input st-pin');
    pin.type = 'password';
    pin.inputMode = 'numeric';
    pin.autocomplete = 'off';
    pin.maxLength = 12;
    card.append(field(`Onay için öğrencinin adını aynen yazın: ${name}`, input),
      field('Kendi PIN’iniz (yıkıcı işlem teyidi)', pin));
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    card.append(error);

    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(value);
    };
    const fold = (text) => String(text || '').trim().toLocaleLowerCase('tr').replace(/\s+/g, ' ');
    const submit = () => {
      if (fold(input.value) !== fold(name)) {
        error.textContent = 'Ad eşleşmiyor.';
        input.setAttribute('aria-invalid', 'true');
        input.focus();
        return;
      }
      if (!/^\d{4,12}$/.test(pin.value.trim())) {
        error.textContent = 'PIN’inizi yazın.';
        pin.focus();
        return;
      }
      close({ name: input.value.trim(), pin: pin.value.trim() });
    };
    const onKey = (event) => {
      if (event.key === 'Escape') close(null);
      if (event.key === 'Enter' && (event.target === input || event.target === pin)) submit();
    };
    const go = button('Kalıcı olarak sil', { variant: 'danger', onClick: submit });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }), go);
    card.append(actions);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    input.focus();
  });
}

/**
 * Toplu kalıcı silme onayı: seçilen SAYI aynen yazılır + PIN. Her öğrenci
 * sunucuda ayrı denetlenir; engelli olan silinmez (sonuç penceresi gösterir).
 *
 * @returns {Promise<{count: string, pin: string}|null>}
 */
export function askBulkDelete(root, { students }) {
  return new Promise((resolve) => {
    const count = students.length;
    const { overlay, card, release } = dialog(root, `${count} öğrenci — kalıcı olarak sil`,
      'GERİ ALINAMAZ. Her öğrenci ayrı denetlenir: pasif olmayan, kantin bakiyesi 0 olmayan, açık ödeme '
      + 'talebi ya da kurum cari borcu olan öğrenci SİLİNMEZ ve sonuçta nedeniyle listelenir. Kurum '
      + 'carisindeki alacak silmeyi engellemez. Silinen öğrencinin KM kaydı silinir, kantinde '
      + 'anonimleşir, BBD Akademi kişisel veriyi KVKK planına göre siler; uygulama hesabı kapanır.');
    const names = h('ul', 'st-del-list st-del-names');
    for (const student of students.slice(0, 12)) names.append(h('li', null, student.displayName || student.kantinId));
    if (count > 12) names.append(h('li', 'st-hint', `+${count - 12} öğrenci daha`));
    card.append(names);
    const field = (text, input) => {
      const wrap = h('label', 'st-field');
      wrap.append(h('span', 'st-label', text), input);
      return wrap;
    };
    const input = h('input', 'kit-input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.autocomplete = 'off';
    input.maxLength = 6;
    const pin = h('input', 'kit-input st-pin');
    pin.type = 'password';
    pin.inputMode = 'numeric';
    pin.autocomplete = 'off';
    pin.maxLength = 12;
    card.append(field(`Onay için silinecek öğrenci sayısını yazın: ${count}`, input),
      field('Kendi PIN’iniz (yıkıcı işlem teyidi)', pin));
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    card.append(error);
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(value);
    };
    const submit = () => {
      if (input.value.trim() !== String(count)) {
        error.textContent = 'Sayı eşleşmiyor.';
        input.focus();
        return;
      }
      if (!/^\d{4,12}$/.test(pin.value.trim())) {
        error.textContent = 'PIN’inizi yazın.';
        pin.focus();
        return;
      }
      close({ count: input.value.trim(), pin: pin.value.trim() });
    };
    const onKey = (event) => {
      if (event.key === 'Escape') close(null);
      if (event.key === 'Enter' && (event.target === input || event.target === pin)) submit();
    };
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }),
      button(`${count} öğrenciyi kalıcı sil`, { variant: 'danger', onClick: submit }));
    card.append(actions);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    input.focus();
  });
}

/**
 * Toplu silme sonucu: öğrenci başına "silindi" ya da engelin nedeni. `documents`
 * (BBD Akademi yeteneği `bbd_akademi.deletionDocument`; yoksa null) kurum logolu
 * sonuç belgesinin "Önizle / Yazdır" düğmelerini verir.
 */
export function showBulkDeleteResult(root, result, { documents = null } = {}) {
  return new Promise((resolve) => {
    const { overlay, card, release } = dialog(root,
      `${result.deleted} öğrenci silindi · ${result.blocked} öğrenci silinmedi`,
      result.blocked ? 'Silinmeyen öğrencilerin nedeni aşağıda; engel çözülünce tek tek ya da yeniden toplu '
        + 'silinebilir.' : 'Kantin kayıtları arka planda anonimleşiyor (uzlaştırma ≤ 1 dk).');
    const list = h('ul', 'st-del-list st-del-result');
    for (const row of result.results || []) {
      const item = h('li', row.deleted ? 'ok' : 'bad');
      item.append(h('b', null, row.displayName || row.studentId), h('span', null,
        ` — ${row.deleted ? 'silindi' : `silinmedi: ${row.message}`}`));
      list.append(item);
    }
    card.append(list);
    const close = () => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve();
    };
    const onKey = (event) => { if (event.key === 'Escape') close(); };
    const actions = h('div', 'kit-dialog-actions');
    const ok = button('Kapat', { variant: 'primary', onClick: close });
    if (typeof documents === 'function') {
      const docs = documents(root, { result });
      if (docs) actions.append(docs);
    }
    actions.append(ok);
    card.append(actions);
    document.addEventListener('keydown', onKey);
    ok.focus();
  });
}

/** Öğrenci kartındaki durum bölümü: durum, gerekçe, tarih ve geçmiş. */
export function mountStatus(container, { api, student }) {
  let destroyed = false;
  container.replaceChildren();
  const line = h('p', 'st-lc-line');
  if (student.status === 'inactive') {
    line.append(h('i', 'st-badge st-badge-inactive', 'Pasif'));
    line.append(h('span', null,
      ` ${REASON_LABEL[student.statusReason] || 'gerekçe yok'}`
      + `${student.statusSince ? ` · ${new Date(student.statusSince).toLocaleString('tr-TR')}` : ''}`));
  } else {
    line.append(h('i', 'st-badge st-badge-active', 'Etkin'));
    if (student.scheduledDeactivation) {
      line.append(h('span', null,
        ` · ${student.scheduledDeactivation} günü pasifleşecek`));
    }
  }
  container.append(line);
  if (student.canteenSync === 'pending') {
    container.append(h('p', `st-status ${student.canteenStale ? 'bad' : ''}`,
      student.canteenStale
        ? 'Kantine 24 saattir iletilemedi — kantin bağlantısını denetleyin.'
        : 'Kantine iletilemedi — otomatik yeniden denenecek.'));
  }
  const list = h('ul', 'st-lc-history');
  container.append(list);
  api(`/api/bbd_students/students/${encodeURIComponent(student.kantinId)}/status-history`)
    .then((payload) => {
      if (destroyed) return;
      const items = payload?.items || [];
      if (!items.length) {
        list.append(h('li', 'st-hint', 'Durum geçmişi yok.'));
        return;
      }
      for (const item of items) {
        const row = h('li');
        row.append(h('b', null, ACTION_LABEL[item.action] || item.action));
        row.append(h('span', null, ` · ${new Date(item.at).toLocaleString('tr-TR')}`
          + `${item.actor ? ` · ${item.actor}` : ''}`
          + `${item.reason ? ` · ${REASON_LABEL[item.reason] || item.reason}` : ''}`
          + `${item.effectiveAt && item.action === 'scheduled' ? ` · ${item.effectiveAt}` : ''}`
          + `${item.note ? ` — ${item.note}` : ''}`));
        list.append(row);
      }
    })
    .catch(() => {
      if (!destroyed) list.append(h('li', 'st-hint', 'Durum geçmişi okunamadı.'));
    });
  return { destroy() { destroyed = true; container.replaceChildren(); } };
}

/**
 * KM paneli canlı yenileme (km-merkezi-senkron §8, ADR 0029): uzun yoklama
 * `GET /api/events/wait`. Veritabanı yoklanmaz; açık ekran başına TEK bağlantı.
 * Hata olursa artan beklemeyle yeniden dener; ekran kapanınca durur.
 */
export function watchEvents({ api, types, onEvents }) {
  let stopped = false;
  let after = null;
  let backoff = 1000;
  (async () => {
    while (!stopped) {
      try {
        const query = after === null
          ? `/api/events/wait?types=${encodeURIComponent(types)}`
          : `/api/events/wait?after=${after}&types=${encodeURIComponent(types)}&timeout=25`;
        const payload = await api(query, { timeoutSeconds: 40 });
        if (stopped) return;
        const previous = after;
        after = Number(payload?.lastSeq ?? after ?? 0);
        backoff = 1000;
        if (payload?.reset && previous !== null) onEvents([], { reset: true });
        else if (payload?.items?.length) onEvents(payload.items, { reset: false });
      } catch {
        if (stopped) return;
        await new Promise((done) => setTimeout(done, backoff));
        backoff = Math.min(backoff * 2, 30000);
      }
    }
  })();
  return () => { stopped = true; };
}
