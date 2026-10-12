// Öğrenci kartı — "Kantin tahsilatı" ve "Ödeme talebi" (tek öğrenci).
//
// BU DOSYA BİR ARAYÜZ YETENEĞİDİR: `bbd_payment_request.student_card`.
// Öğrenci Yönetimi ekranı onu `ctx.capability(...)` ile çözer ve kendi
// öğrenci detayının içine çizer (K3 — modül modülü import etmez). Tahsilat ve
// talep akışı TEK YERDE, bu modülde kalır; öğrenci ekranı kopyasını yazmaz.
//
// Uçlar bu modülündür ve izinlerini backend yeniden sorar (K9):
//   GET  /api/bbd_payment_request/students/{id}/state     view
//   POST /api/bbd_payment_request/collections             collect
//   POST /api/bbd_payment_request/students/{id}/request   send
//
// PARA KURUŞTUR; ekranda ₺ ve Türkçe biçim. Kantin parası kurum (Akademi)
// parasıyla KARIŞMAZ — bu kart yalnız kantin defterine yazar.

import {
  balanceText, button, confirmWithReason, h, loadStyles, money, moneyInput, parseMoney,
} from './kit.js';

const BASE = '/api/bbd_payment_request';

/** İstemci tarafı idempotens anahtarı: ağ koparsa aynı anahtarla yeniden denenir. */
function newLocalId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `km-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Kartı `container` içine çizer. Dönüş: `{ reload, destroy }`.
 *
 * `options`: `{ api, studentId, displayName, dialogRoot?, onChanged? }`
 */
export function mountStudentCard(container, options) {
  loadStyles(import.meta.url);
  const { api, studentId } = options;
  const dialogRoot = options.dialogRoot || container;
  let permissions = null;          // null = okunamadı; karar backend'de
  let state = null;
  let collectKey = null;           // başarısız denemede KORUNUR (idempotens)
  let destroyed = false;

  const root = h('div', 'pr-sc');
  container.replaceChildren(root);

  const can = (key) => permissions === null || permissions.has(key);

  async function loadPermissions() {
    try {
      const me = await api('/auth/me');
      permissions = new Set(Array.isArray(me?.permissions) ? me.permissions : []);
    } catch {
      permissions = null;
    }
  }

  function message(node, text, tone = '') {
    node.textContent = text;
    node.className = `pr-sc-msg${tone ? ` ${tone}` : ''}`;
  }

  async function reload() {
    root.replaceChildren(h('p', 'pr-hint', 'Kantinden okunuyor…'));
    try {
      if (permissions === null) await loadPermissions();
      state = await api(`${BASE}/students/${encodeURIComponent(studentId)}/state`);
    } catch (error) {
      state = { ok: false, error: error.message || String(error) };
    }
    if (!destroyed) render();
  }

  function render() {
    root.replaceChildren();
    if (!state?.ok) {
      root.append(h('div', 'pr-alert bad',
        `Kantin bilgisi okunamadı — ${state?.error || 'bilinmeyen hata'}`));
      return;
    }
    const student = state.student;
    const pre = state.prepayment || {};
    const balance = Number(student.balance || 0);

    const head = h('div', 'pr-sc-head');
    head.append(
      h('b', `pr-sc-balance${balance > 0 ? ' debt' : balance < 0 ? ' credit' : ''}`,
        balance > 0 ? `Güncel borç ${money(balance)}`
          : balance < 0 ? `Öğrencinin ${balanceText(balance)}` : 'Borç yok'),
    );
    if (pre.allowPrepayment) {
      head.append(h('span', 'pr-hint',
        `Ön ödeme açık: borçtan fazlası en çok ${money(pre.maxPrepayment)} alacak olarak yazılır.`));
    }
    if (state.pendingRequest) {
      const pending = state.pendingRequest;
      head.append(h('span', 'pr-badge',
        `Açık talep ${pending.ref} · ${money(pending.amount)}`
        + (pending.amountFixed ? ' · sabit tutar' : '')));
    }
    root.append(head);

    root.append(collectSection(student));
    root.append(requestSection(student));

    const rows = state.collections || [];
    if (rows.length) {
      const list = h('div', 'pr-sc-history');
      list.append(h('span', 'pr-sc-label', 'Son nakit tahsilatlar (bu panelden)'));
      for (const row of rows) {
        list.append(h('div', `pr-sc-history-row ${row.status}`,
          `${String(row.created_at || '').slice(0, 16).replace('T', ' ')} · ${money(row.amount)}`
          + ` · kalan ${balanceText(row.balance_after)}`
          + (row.status === 'ok' ? '' : ` · ${row.reason || 'başarısız'}`)));
      }
      root.append(list);
    }
  }

  // ------------------------------------------------------------ tahsilat

  function collectSection(student) {
    const box = h('div', 'pr-sc-block');
    box.append(h('span', 'pr-sc-label', 'Kantin tahsilatı (elden nakit)'));
    const max = Number(state.maxCollect || 0);

    if (max <= 0) {
      box.append(h('p', 'pr-hint', 'Tahsil edilecek borç yok.'));
      return box;
    }

    const row = h('div', 'pr-sc-row');
    const amount = h('input', 'kit-input pr-sc-amount');
    amount.type = 'text';
    amount.inputMode = 'decimal';
    amount.placeholder = '0,00';
    amount.setAttribute('aria-label', 'Tahsil edilen tutar (₺)');
    if (student.balance > 0) amount.value = moneyInput(student.balance);
    const note = h('input', 'kit-input pr-sc-note');
    note.type = 'text';
    note.maxLength = 255;
    note.placeholder = 'Not (isteğe bağlı)';
    const all = h('button', 'pr-mini', 'Borcun tamamı');
    all.type = 'button';
    all.disabled = student.balance <= 0;
    all.addEventListener('click', () => { amount.value = moneyInput(student.balance); });
    const msg = h('div', 'pr-sc-msg');

    const go = button('Tahsil et', {
      variant: 'primary',
      onClick: async () => {
        const kurus = parseMoney(amount.value);
        if (kurus === null || kurus < 1) { message(msg, 'Tutarı 120,50 gibi yazın.', 'bad'); return; }
        if (kurus > max) {
          message(msg, `En çok ${money(max)} tahsil edilebilir.`, 'bad');
          return;
        }
        const over = kurus - Math.max(0, student.balance);
        const reason = await confirmWithReason(dialogRoot, {
          title: 'Kantin nakit tahsilatı',
          description: `${student.displayName} adına ${money(kurus)} elden alındı olarak `
            + 'KANTİN defterine işlenecek (kurum ödemelerinden ayrıdır). '
            + (over > 0 ? `Borçtan fazlası (${money(over)}) alacak olarak yazılır. ` : '')
            + 'Geri alınamaz.',
          confirmLabel: 'Tahsil et',
          placeholder: 'Gerekçe (teyit içindir)',
        });
        if (reason === null) return;
        collectKey = collectKey || newLocalId();
        go.disabled = true;
        try {
          const result = await api(`${BASE}/collections`, {
            method: 'POST',
            body: { kantinId: student.kantinId, amount: kurus, localId: collectKey,
              note: note.value.trim() || reason },
          });
          if (!result.ok) { message(msg, result.error || 'Tahsilat yazılamadı.', 'bad'); return; }
          collectKey = null;
          message(msg, `${money(result.amount)} tahsil edildi. Bakiye: ${balanceText(result.balance)}.`,
            'good');
          options.onChanged?.();
          await reload();
        } catch (error) {
          // Ağ hatası: anahtar KORUNUR — yeniden denemek çift alacak yazmaz.
          message(msg, `Tahsilat yazılamadı: ${error.message}. Yeniden deneyebilirsiniz.`, 'bad');
        } finally {
          go.disabled = !can('bbd_payment_request.collect');
        }
      },
    });
    if (!can('bbd_payment_request.collect')) {
      go.disabled = true;
      go.title = 'Nakit tahsilat izniniz yok.';
    }

    row.append(amount, all, note, go);
    box.append(row, h('span', 'pr-hint',
      `En çok ${money(max)}${state.prepayment?.allowPrepayment ? ' (borç + izinli ön ödeme)' : ' (borç)'}.`),
    msg);
    return box;
  }

  // ------------------------------------------------------------- talep

  function requestSection(student) {
    const box = h('div', 'pr-sc-block');
    box.append(h('span', 'pr-sc-label', 'Ödeme talebi (veliye SMS ile link)'));
    const max = Number(state.maxRequest || 0);
    if (!student.hasPhone) {
      box.append(h('p', 'pr-hint', 'Veli telefonu tanımlı değil — talep gönderilemez.'));
      return box;
    }
    if (max < 100) {
      box.append(h('p', 'pr-hint', 'Güncel borç yok — talep açılmaz.'));
      return box;
    }

    const name = `pr-sc-mode-${studentId}`;
    const fullOption = h('label', 'pr-sc-radio');
    const full = h('input');
    full.type = 'radio';
    full.name = name;
    full.checked = student.balance > 0;
    full.disabled = student.balance <= 0;
    fullOption.append(full, h('span', null,
      student.balance > 0 ? `Borcun tamamı (${money(student.balance)})` : 'Borcun tamamı'));

    const freeOption = h('label', 'pr-sc-radio');
    const free = h('input');
    free.type = 'radio';
    free.name = name;
    free.checked = !full.checked;
    const amount = h('input', 'kit-input pr-sc-amount');
    amount.type = 'text';
    amount.inputMode = 'decimal';
    amount.placeholder = '0,00';
    amount.setAttribute('aria-label', 'Serbest tutar (₺)');
    amount.addEventListener('focus', () => { free.checked = true; });
    freeOption.append(free, h('span', null, 'Serbest tutar'), amount);

    const msg = h('div', 'pr-sc-msg');
    const go = button('Ödeme talebi gönder', {
      variant: 'primary',
      onClick: async () => {
        let body = {};
        if (free.checked) {
          const kurus = parseMoney(amount.value);
          if (kurus === null || kurus < 100) {
            message(msg, 'Serbest tutar en az 1,00 ₺ olmalı.', 'bad');
            return;
          }
          if (kurus > max) { message(msg, `En çok ${money(max)} istenebilir.`, 'bad'); return; }
          body = { amount: kurus };
        }
        const total = body.amount ?? student.balance;
        const reason = await confirmWithReason(dialogRoot, {
          title: 'Ödeme talebi gönderilsin mi?',
          description: `${student.displayName} velisine ${money(total)} tutarında kantin ödeme `
            + 'linki SMS ile gider. GERÇEK SMS — geri alınamaz.'
            + (state.pendingRequest && !body.amount
              ? ' Açık talep varsa güncel borca göre yeniden fiyatlanır.' : ''),
          confirmLabel: 'Gönder',
          placeholder: 'Gerekçe (teyit içindir)',
        });
        if (reason === null) return;
        go.disabled = true;
        try {
          const result = await api(`${BASE}/students/${encodeURIComponent(studentId)}/request`,
            { method: 'POST', body });
          if (!result.ok) { message(msg, result.error || 'Talep gönderilemedi.', 'bad'); return; }
          message(msg, `Talep ${result.ref} gönderildi (${money(result.amount)}).`, 'good');
          options.onChanged?.();
          await reload();
        } catch (error) {
          message(msg, `Talep gönderilemedi: ${error.message}`, 'bad');
        } finally {
          go.disabled = !can('bbd_payment_request.send');
        }
      },
    });
    if (!can('bbd_payment_request.send')) {
      go.disabled = true;
      go.title = 'Ödeme talebi gönderme izniniz yok.';
    }

    box.append(fullOption, freeOption, h('div', 'pr-sc-row', go),
      h('span', 'pr-hint', `Serbest tutar en az 1,00 ₺, en çok ${money(max)}. `
        + 'Sabit tutarlı talep sonradan yeniden fiyatlanmaz.'),
      msg);
    return box;
  }

  reload();
  return {
    reload,
    destroy() {
      destroyed = true;
      container.replaceChildren();
    },
  };
}
