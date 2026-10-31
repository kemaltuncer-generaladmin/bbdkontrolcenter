// BBD Akademi — kurum finansı kilidi (iş 83, Akademi README §14 madde 143).
//
// KULLANICI (31.10.2026): finansa girişte iki adımlı doğrulama — kim girmek isterse (kendisi dahil) finans
// yöneticisinin telefonuna SMS gider, kod girilince finans işlemleri açılır. Kantin kapsam DIŞI.
//
// KARAR SUNUCUDA (K9): izin KM sunucusunda bu KM oturumu için tutulur (30 dk HAREKETSİZLİKTE kapanır, her
// finans isteği uzatır); kilitliyken vekil `403 finance_access_required` döner. Bu dosya yalnız akışı çizer:
//   · `lockScreen()`  — Finans ekranının yerinde duran kilit kartı (kod iste → 6 haneli kod → doğrula)
//   · `ensure()`      — başka ekrandan gelen kilit hatasında aynı akış pencerede; açılırsa true
//   · `badge()`       — "Finans açık · 28 dk" + "Finansı kilitle"
// Durum tek nesnede (`app.financeLock`): vekil yanıtındaki `financeAccessUntil` süreyi tazeler, kilit hatası
// kapatır; dinleyiciler (`onChange`) Finans ekranını kilit kartı ↔ içerik arasında çevirir.

import { button, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox } from '../../../ui-kit/layout.js';
import { navIcon } from '../nav-icons.js';
import { TEXT, badgeText, cleanCode, codeProblem, minutesLeft, sentText } from './finance-lock-logic.js';

const BASE = '/finance-access';

export function createFinanceLock(app, initial = null) {
  loadStyles(import.meta.url, 'finance.css');
  let state = normalize(initial);
  const listeners = new Set();
  let timer = null;
  let pending = null;                 // açık kilit penceresinin sözü (aynı anda tek pencere)

  // `known: false` = durum okunamadı (eski KM sunucusu, açılış hatası): arayüz kilit çizmez, karar vekilde —
  // ilk `finance_access_required` yanıtı durumu öğretir (`noteLocked`).
  function normalize(raw) {
    const known = raw && typeof raw === 'object' ? (raw.known ?? ('active' in raw)) : false;
    return { known: Boolean(known), active: Boolean(raw?.active), until: raw?.until || null,
      idleMinutes: raw?.idleMinutes || 30,
      approvers: Array.isArray(raw?.approvers) ? raw.approvers : [], managerMissing: Boolean(raw?.managerMissing) };
  }

  function emit() {
    for (const fn of [...listeners]) {
      try { fn(state); } catch (failure) { console.error('[bbd_akademi] finans kilidi dinleyicisi', failure); }
    }
  }

  function schedule() {
    clearInterval(timer);
    timer = null;
    if (!state.active) return;
    // Rozet dakikayı gösterir; süre dolunca istemci de kilide döner (sunucu zaten reddeder).
    timer = setInterval(() => {
      if (minutesLeft(state.until) <= 0) set({ ...state, active: false, until: null });
      else emit();
    }, 30_000);
  }

  function set(next) {
    const was = state.active;
    // Sunucu durumu (`active` taşıyan) geldiyse durum artık BİLİNİYOR.
    const known = next?.known ?? (next && 'active' in next ? true : state.known);
    state = normalize({ ...state, ...next, known });
    if (state.active && minutesLeft(state.until) <= 0 && state.until) state.active = false;
    if (was && !state.active) app.api?.invalidate?.('/v1/admin/dashboard');
    schedule();
    emit();
  }

  async function refresh() {
    try {
      set(await app.api.km(BASE));
    } catch { /* durum okunamadı: eldeki kalır, karar vekilde */ }
    return state;
  }

  async function request() {
    try {
      const challenge = await app.api.km(`${BASE}/request`, { method: 'POST', body: {} });
      set({ approvers: challenge?.approvers || state.approvers, managerMissing: false });
      return challenge;
    } catch (failure) {
      if (failure?.status === 409) set({ managerMissing: true, approvers: [] });
      throw failure;
    }
  }

  async function verify(challengeId, code) {
    const next = await app.api.km(`${BASE}/verify`, { method: 'POST', body: { challengeId, code } });
    set(next);
    return state;
  }

  async function end() {
    try {
      await app.api.km(`${BASE}/end`, { method: 'POST', body: {} });
    } finally {
      set({ active: false, until: null });
    }
    app.toast?.(TEXT.closed, 'good');
  }

  /** Kod akışının gövdesi (kart ya da pencere içinde). `onDone()` kilit açılınca. */
  function flow({ onDone, onCancel } = {}) {
    const node = h('div', 'ak-fin-lock');
    const icon = h('div', 'ak-fin-lock-icon');
    icon.append(navIcon('lock'));
    icon.setAttribute('aria-hidden', 'true');
    const title = h('h3', 'ak-fin-lock-title', TEXT.lockedTitle);
    const text = h('p', 'ak-fin-lock-text', TEXT.lockedText);
    const who = h('p', 'ak-fin-lock-who');
    const note = h('div', 'ak-fin-lock-note');
    note.setAttribute('role', 'status');
    const codeRow = h('div', 'ak-fin-lock-code');
    codeRow.hidden = true;
    const input = h('input', 'kit-input ak-fin-lock-input');
    input.inputMode = 'numeric';
    input.autocomplete = 'one-time-code';
    input.maxLength = 6;
    input.placeholder = '••••••';
    input.setAttribute('aria-label', TEXT.codeLabel);
    const verifyButton = button(TEXT.verify, { variant: 'primary', onClick: () => submit() });
    codeRow.append(input, verifyButton);
    const askButton = button(TEXT.request, { variant: 'primary', onClick: () => ask() });
    const actions = h('div', 'ak-fin-lock-actions');
    actions.append(askButton);
    if (onCancel) actions.append(button('Vazgeç', { variant: 'ghost', onClick: onCancel }));
    node.append(icon, title, text, who, codeRow, actions, note);

    let challengeId = null;
    let resendTimer = null;
    let busy = false;

    function paintWho() {
      if (state.managerMissing) {
        who.replaceChildren(alertBox(TEXT.managerMissing, 'warn'));
        return;
      }
      const names = state.approvers.map((item) => [item.name, item.maskedPhone ? `(${item.maskedPhone})` : '']
        .filter(Boolean).join(' ')).filter(Boolean);
      who.textContent = names.length ? `Onaylayan: ${names.join(', ')}` : '';
    }

    function countdown(seconds) {
      clearInterval(resendTimer);
      let left = Math.max(0, Number(seconds) || 0);
      const paint = () => {
        askButton.disabled = left > 0;
        askButton.textContent = left > 0 ? `${TEXT.resend} (${left} sn)` : TEXT.resend;
      };
      paint();
      resendTimer = setInterval(() => {
        left -= 1;
        paint();
        if (left <= 0) clearInterval(resendTimer);
      }, 1000);
    }

    async function ask() {
      if (busy) return;
      busy = true;
      askButton.disabled = true;
      note.replaceChildren();
      try {
        const challenge = await request();
        challengeId = challenge.challengeId;
        codeRow.hidden = false;
        note.replaceChildren(alertBox(sentText(challenge.approvers), 'info'));
        paintWho();
        countdown(challenge.resendAfterSeconds ?? 60);
        input.value = '';
        input.focus();
      } catch (failure) {
        askButton.disabled = false;
        paintWho();
        note.replaceChildren(alertBox(codeProblem(failure), state.managerMissing ? 'warn' : 'bad'));
      } finally {
        busy = false;
      }
    }

    async function submit() {
      const code = cleanCode(input.value);
      input.value = code;
      if (!challengeId) return;
      if (code.length !== 6) {
        note.replaceChildren(alertBox('Onay kodu 6 haneli olmalı.', 'bad'));
        input.setAttribute('aria-invalid', 'true');
        input.focus();
        return;
      }
      if (busy) return;
      busy = true;
      verifyButton.disabled = true;
      try {
        await verify(challengeId, code);
        clearInterval(resendTimer);
        app.toast?.(TEXT.opened, 'good');
        onDone?.();
      } catch (failure) {
        input.setAttribute('aria-invalid', 'true');
        note.replaceChildren(alertBox(codeProblem(failure), 'bad'));
        input.select();
      } finally {
        busy = false;
        verifyButton.disabled = false;
      }
    }

    // Yapıştırma ya da 6. hane: kendiliğinden doğrula (tek tık daha az).
    input.addEventListener('input', () => {
      input.removeAttribute('aria-invalid');
      const code = cleanCode(input.value);
      if (input.value !== code) input.value = code;
      if (code.length === 6) submit();
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); submit(); }
    });

    paintWho();
    return { node, dispose: () => clearInterval(resendTimer) };
  }

  /** Finans ekranındaki kilit kartı. */
  function lockScreen({ onDone } = {}) {
    const view = flow({ onDone });
    view.node.classList.add('ak-fin-lock-card');
    return view;
  }

  /**
   * Başka ekrandan kilit hatası: aynı akış pencerede. Açılırsa true, vazgeçilirse false. Aynı anda tek pencere.
   */
  function ensure() {
    if (state.known && state.active && minutesLeft(state.until) > 0) return Promise.resolve(true);
    if (pending) return pending;
    pending = new Promise((resolve) => {
      const overlay = h('div', 'kit-overlay');
      const card = h('div', 'kit-dialog ak-fin-lock-dialog');
      card.setAttribute('role', 'dialog');
      let release = () => {};
      const close = (value) => {
        document.removeEventListener('keydown', onKey);
        view.dispose();
        overlay.remove();
        release();
        pending = null;
        resolve(value);
      };
      const view = flow({ onDone: () => close(true), onCancel: () => close(false) });
      const onKey = (event) => {
        if (event.key === 'Escape') { event.stopPropagation(); close(false); }
      };
      card.append(view.node);
      overlay.append(card);
      document.addEventListener('keydown', onKey);
      app.root.append(overlay);
      release = dialogA11y(card);
      card.querySelector('button')?.focus();
    });
    return pending;
  }

  /** "Finans açık · 28 dk" rozeti + "Finansı kilitle". */
  function badge() {
    const node = h('div', 'ak-fin-open');
    const label = h('span', 'ak-fin-open-badge');
    label.setAttribute('aria-live', 'polite');
    const lockButton = button(TEXT.lock, { variant: 'ghost', onClick: () => end() });
    node.append(label, lockButton);
    const paint = () => {
      node.hidden = !state.active;
      label.textContent = badgeText(state.until);
      label.title = `Kod girildikten sonra ${state.idleMinutes} dk işlem yapılmazsa finans kendiliğinden kilitlenir.`;
    };
    paint();
    const off = onChange(paint);
    return { node, dispose: off };
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  schedule();

  return {
    get state() { return state; },
    active: () => !state.known || (state.active && (!state.until || minutesLeft(state.until) > 0)),
    set,
    refresh,
    request,
    verify,
    end,
    ensure,
    lockScreen,
    badge,
    onChange,
    /** Vekil yanıtındaki `financeAccessUntil` (her başarılı finans isteği süreyi uzatır). */
    noteUntil: (until) => { if (until && until !== state.until) set({ known: true, active: true, until }); },
    /** Vekil `finance_access_required` döndü: sunucuda izin kapanmış. */
    noteLocked: () => { if (state.active || !state.known) set({ known: true, active: false, until: null }); },
    dispose: () => { clearInterval(timer); listeners.clear(); },
  };
}
