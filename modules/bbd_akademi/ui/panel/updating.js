// BBD Akademi — "Akademi şu an güncelleniyor" bandı (dağıtım anı, 2026-10-15).
//
// Akademi yeni sürüme geçerken ağ geçidi kısa süre 502/503 verir. Köprü OKUMALARI
// sunucuda ≤ 20 sn yeniden dener; yine olmazsa panel bu bandı gösterir:
//   · geri sayım (5 sn, sonra 10, 15 … en çok 30) → son başarısız okumayı yeniden dener (`probe`)
//   · "Şimdi dene" düğmesi
//   · Akademi dönünce (herhangi bir başarılı yanıt) bant kalkar ve açık ekran yeniden kurulur
//     (`reload`). Pencere/çekmece açıksa (`busy`) kullanıcının girdisi kaybolmasın diye ekran
//     kurulmaz; yerine "Yenile" düğmeli bilgi bandı kalır.
//
// Bilerek import'suz: ui-kit'e bağlı değil, Node'da sahte DOM ile sınanır
// (tests/test_akademi_guncelleniyor_bandi.py).

export const UPDATING_TEXT = 'Akademi şu an güncelleniyor, birkaç saniye sonra otomatik yeniden denenecek.';
export const RECOVERED_TEXT = 'Akademi yeniden erişilebilir. Açık penceredeki bilgiler korunuyor; hazır olduğunuzda ekranı yenileyin.';
const WAITS = [5, 10, 15, 30];

/**
 * @param {{probe: () => Promise<boolean>, reload: () => void, busy?: () => boolean,
 *   doc?: Document, timers?: {setInterval: Function, clearInterval: Function}}} options
 */
export function updatingBand({ probe, reload, busy = () => false, doc = globalThis.document,
  timers = globalThis } = {}) {
  const node = doc.createElement('div');
  node.className = 'ak-updating';
  node.hidden = true;
  node.setAttribute('role', 'status');
  node.setAttribute('aria-live', 'polite');

  let state = 'hidden';               // hidden | waiting | probing | recovered
  let remaining = 0;
  let round = 0;
  let timer = null;

  const stop = () => {
    if (timer !== null) timers.clearInterval(timer);
    timer = null;
  };

  function paint() {
    node.hidden = state === 'hidden';
    node.className = `ak-updating kit-alert ${state === 'recovered' ? 'info' : 'warn'}`;
    const text = doc.createElement('span');
    text.className = 'ak-updating-text';
    const action = doc.createElement('button');
    action.type = 'button';
    action.className = 'kit-btn kit-btn-ghost ak-updating-action';
    if (state === 'recovered') {
      text.textContent = RECOVERED_TEXT;
      action.textContent = 'Yenile';
      action.addEventListener('click', () => { hide(); reload(); });
    } else {
      text.textContent = state === 'probing'
        ? `${UPDATING_TEXT} Deneniyor…`
        : `${UPDATING_TEXT} (${remaining} sn)`;
      action.textContent = 'Şimdi dene';
      action.disabled = state === 'probing';
      action.addEventListener('click', () => attempt());
    }
    node.replaceChildren(text, action);
  }

  function countdown() {
    stop();
    state = 'waiting';
    remaining = WAITS[Math.min(round, WAITS.length - 1)];
    paint();
    timer = timers.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) attempt();
      else paint();
    }, 1000);
  }

  async function attempt() {
    if (state === 'probing' || state === 'hidden' || state === 'recovered') return;
    stop();
    state = 'probing';
    paint();
    let ok = false;
    try {
      ok = await probe();
    } catch {
      ok = false;
    }
    if (state !== 'probing') return;  // bu arada başka bir istek başarıyla döndü (recovered)
    if (ok) {
      recovered();
      return;
    }
    round += 1;
    countdown();
  }

  function hide() {
    stop();
    state = 'hidden';
    round = 0;
    paint();
  }

  /** Bir okuma "güncelleniyor" hatasıyla bitti. Bant zaten açıksa sayaç sürer. */
  function show() {
    if (state === 'waiting' || state === 'probing') return;
    round = 0;
    countdown();
  }

  /** Akademi'den başarılı yanıt geldi: bant kalkar, açık ekran tazelenir. */
  function recovered() {
    if (state !== 'waiting' && state !== 'probing') return;
    stop();
    if (busy()) {
      state = 'recovered';
      paint();
      return;
    }
    hide();
    reload();
  }

  return {
    node,
    show,
    recovered,
    attempt,
    get state() { return state; },
    dispose: stop,
  };
}
