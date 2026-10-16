// connection.js — bağlantı durumu, üst şerit ve yazma için "Yeniden dene"
// penceresi (ADR 0032 §Bağlantı).
//
// ESKİDEN: internet ya da sunucu gidince uygulama dururdu (ADR 0026 notu);
// açılışta 20 sn bekleyip "ulaşılamadı" der, çalışırken her ekran ham hata
// basardı. ŞİMDİ:
//  · üstte ince bir şerit: "Bağlantı yok, yeniden deneniyor…" (+ Şimdi dene);
//  · okumalar son bilinen yanıttan gelir (`net.js`);
//  · yazmalar AÇIK hata verir. Bağlantı HİÇ kurulamadıysa (istek sunucuya
//    ulaşmadı) kullanıcıya "Yeniden dene / Vazgeç" sorulur; istek gitmiş
//    olabilirse (zaman aşımı) yeniden deneme ÖNERİLMEZ, "kaydedilmiş olabilir,
//    kontrol edin" denir. SESSİZ KUYRUK YOK: para ve yoklama gibi işlemlerde
//    kullanıcının haberi olmadan sonradan gönderilen istek yanlıştır.
//  · bağlantı gelince şerit kısa bir "geri geldi" der ve etkin ekran tazelenir.
//
// DOM'a yalnız çağrıldığında dokunur; Node sınamasında `document` verilir.

const PROBE_STEPS_MS = [1500, 3000, 5000, 8000, 12000, 15000];

/**
 * @param {object} o
 *   probe()      → Promise<boolean>: sunucu ayakta mı (`/health`, önbelleksiz)
 *   onRestore()  → bağlantı geri gelince (etkin ekranı tazele)
 *   doc          → document (sınama için)
 *   timers       → { set, clear } (sınama için)
 */
export function createConnection({ probe, onRestore = () => {}, doc = globalThis.document,
  timers = { set: (fn, ms) => setTimeout(fn, ms), clear: (id) => clearTimeout(id) } } = {}) {
  let online = true;
  let attempt = 0;
  let timer = null;
  let probing = false;
  let banner = null;
  let lastKind = '';
  let pendingRetry = null;
  const listeners = new Set();

  function emit() {
    for (const fn of listeners) {
      try { fn(online); } catch (error) { console.error('[bağlantı] dinleyici hata verdi:', error); }
    }
  }

  function ensureBanner() {
    if (banner || !doc?.body) return banner;
    const node = doc.createElement('div');
    node.className = 'km-conn';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    node.hidden = true;
    const text = doc.createElement('span');
    text.className = 'km-conn-text';
    const now = doc.createElement('button');
    now.type = 'button';
    now.className = 'km-conn-btn';
    now.textContent = 'Şimdi dene';
    now.addEventListener('click', () => { attempt = 0; schedule(0); });
    node.append(text, now);
    doc.body.prepend(node);
    banner = { node, text, now, hideTimer: null };
    return banner;
  }

  function paint(mode) {
    const b = ensureBanner();
    if (!b) return;
    if (b.hideTimer) { timers.clear(b.hideTimer); b.hideTimer = null; }
    const root = doc.documentElement;
    if (mode === 'hidden') {
      b.node.hidden = true;
      root?.classList?.remove('km-banner');
      return;
    }
    b.node.hidden = false;
    root?.classList?.add('km-banner');
    b.node.classList.toggle('ok', mode === 'restored');
    b.now.hidden = mode === 'restored';
    b.text.textContent = mode === 'restored'
      ? 'Bağlantı geri geldi — ekran tazelendi.'
      : (lastKind === 'gateway'
        ? 'Sunucuya ulaşılamıyor, yeniden deneniyor… Okumalar son bilinen veriden; kayıt işlemleri bağlantı gelince yapılabilir.'
        : 'Bağlantı yok, yeniden deneniyor… Okumalar son bilinen veriden; kayıt işlemleri bağlantı gelince yapılabilir.');
    if (mode === 'restored') {
      b.hideTimer = timers.set(() => paint('hidden'), 2600);
    }
  }

  function schedule(ms) {
    if (timer) timers.clear(timer);
    timer = timers.set(runProbe, ms);
  }

  async function runProbe() {
    timer = null;
    if (online || probing) return;
    probing = true;
    let ok = false;
    try { ok = Boolean(await probe()); } catch { ok = false; }
    probing = false;
    if (ok) {
      restore();
      return;
    }
    attempt = Math.min(attempt + 1, PROBE_STEPS_MS.length - 1);
    schedule(PROBE_STEPS_MS[attempt]);
  }

  function restore() {
    if (online) return;
    online = true;
    attempt = 0;
    if (timer) { timers.clear(timer); timer = null; }
    paint('restored');
    emit();
    try { onRestore(); } catch (error) { console.error('[bağlantı] tazeleme hata verdi:', error); }
  }

  const api = {
    get online() { return online; },

    /** Bir istek bağlantı yüzünden düştü. `kind`: connect · timeout · network · gateway */
    reportFailure(kind = 'connect') {
      lastKind = kind;
      if (!online) return;
      online = false;
      attempt = 0;
      paint('offline');
      emit();
      schedule(PROBE_STEPS_MS[0]);
    },

    /** Bir istek sunucudan yanıt aldı. */
    reportSuccess() {
      if (!online) restore();
    },

    /**
     * Yazma isteği sunucuya HİÇ ULAŞMADI: kullanıcıya açıkça sorulur.
     * Aynı anda düşen birden çok yazma TEK pencereyi paylaşır.
     * @returns {Promise<boolean>} true → yeniden gönder
     */
    askRetry() {
      if (pendingRetry) return pendingRetry;
      pendingRetry = new Promise((resolve) => {
        if (!doc?.body) { resolve(false); return; }
        const overlay = doc.createElement('div');
        overlay.className = 'km-modal-backdrop';
        const card = doc.createElement('div');
        card.className = 'km-modal';
        card.setAttribute('role', 'alertdialog');
        card.setAttribute('aria-modal', 'true');
        card.setAttribute('aria-labelledby', 'km-retry-title');
        card.setAttribute('aria-describedby', 'km-retry-text');
        const title = doc.createElement('h2');
        title.id = 'km-retry-title';
        title.textContent = 'İşlem gönderilemedi';
        const text = doc.createElement('p');
        text.id = 'km-retry-text';
        text.textContent = 'Bağlantı yok; istek sunucuya ulaşmadı ve HİÇBİR ŞEY KAYDEDİLMEDİ. '
          + 'Bağlantınızı denetleyip yeniden deneyebilir ya da vazgeçebilirsiniz.';
        const row = doc.createElement('div');
        row.className = 'km-modal-actions';
        const cancel = doc.createElement('button');
        cancel.type = 'button';
        cancel.className = 'km-btn';
        cancel.textContent = 'Vazgeç';
        const retry = doc.createElement('button');
        retry.type = 'button';
        retry.className = 'km-btn km-btn-primary';
        retry.textContent = 'Yeniden dene';
        row.append(cancel, retry);
        card.append(title, text, row);
        overlay.append(card);
        doc.body.append(overlay);
        const previous = doc.activeElement;
        const finish = (answer) => {
          overlay.remove();
          doc.removeEventListener('keydown', onKey, true);
          pendingRetry = null;
          try { previous?.focus?.(); } catch { /* odak geri verilemezse sorun değil */ }
          resolve(answer);
        };
        const onKey = (event) => {
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
          else if (event.key === 'Tab') {
            // Odak pencerede kalır (iki düğme arasında döner).
            event.preventDefault();
            (doc.activeElement === retry ? cancel : retry).focus();
          }
        };
        cancel.addEventListener('click', () => finish(false));
        retry.addEventListener('click', () => finish(true));
        doc.addEventListener('keydown', onKey, true);
        retry.focus();
      });
      return pendingRetry;
    },

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    /** Sınama ve "Şimdi dene" için: yoklamayı hemen koştur. */
    probeNow() { return runProbe(); },
  };
  return api;
}
