// BBD Akademi — adım adım pencere (sihirbaz) ve toplu iş izleme.
//
// KULLANICI: "karışık olmasın". Çok seçenekli işler (toplu ödev, kitap
// satışı) tek uzun form yerine ÜÇ kısa adımdır: ne → kime → ne zaman / nasıl.
// Her adımın üstte adı ve sırası yazılıdır, "Geri" ile dönülür, son adımda
// önizleme görünür ve tek düğmeyle biter.
//
// ERİŞİLEBİLİRLİK (WCAG 2.2 AA): pencere `role=dialog` + başlık bağı + odak
// tuzağı (kitin `dialogA11y`); adım değişince odak adım başlığına gider ve
// canlı bölge "Adım 2 / 3: Kime?" der; adım listesi `aria-current="step"`
// taşır; hata metni `role=alert`; Esc vazgeçer.
//
// Toplu iş (`BulkJob`): küçük işlem istekte biter (`201 succeeded`), büyüğü
// kuyruğa girer (`202 queued`) ve `admin_getBulkJob` ile 1,5 sn aralıkla
// izlenir (sözleşme: istemci 1–2 sn aralıkla sorar).

import { button, dialogA11y, h, num } from '../../../ui-kit/kit.js';
import { progress } from '../../../ui-kit/layout.js';
import { AkademiError, newKey, problem } from '../api.js';

/**
 * @param {object} app
 * @param {object} spec
 *   title, steps: [{key, label, render(ctl) → Node | {node, enter?(), destroy?()},
 *   validate?() → string|null|Promise<string|null>}], finishLabel,
 *   onFinish({key, progress(text)}) → sonuç (null/undefined = pencere açık kalır),
 *   start = 0 (açılış adımı)
 * @returns {Promise<any|null>} onFinish sonucu ya da vazgeçildiyse null
 */
export function wizard(app, { title, steps, finishLabel = 'Bitir', onFinish, start = 0 } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-dialog wide ak-wiz');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    const heading = h('h3', 'kit-dialog-title', title);

    const list = h('ol', 'ak-wiz-steps');
    list.setAttribute('aria-label', 'Adımlar');
    const marks = steps.map((step, index) => {
      const item = h('li', 'ak-wiz-step');
      item.append(h('span', 'ak-wiz-step-no', String(index + 1)), h('span', 'ak-wiz-step-label', step.label));
      list.append(item);
      return item;
    });
    const live = h('div', 'ak-wiz-live');
    live.setAttribute('role', 'status');
    live.setAttribute('aria-live', 'polite');

    const body = h('div', 'ak-wiz-body');
    const error = h('div', 'kit-dialog-error ak-wiz-error');
    error.setAttribute('role', 'alert');
    const note = h('div', 'ak-wiz-note');
    note.setAttribute('aria-live', 'polite');

    const back = button('Geri', { onClick: () => go(index - 1) });
    const next = button('İleri', { variant: 'primary', onClick: () => forward() });
    const cancel = button('Vazgeç', { onClick: () => close(null) });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(cancel, h('span', 'kit-spacer'), back, next);
    card.append(heading, list, live, body, error, note, actions);

    const key = newKey('km-wiz');
    const built = new Map();     // adım → {slot, title, control}
    let index = -1;
    let busy = false;
    let closed = false;

    const ctl = {
      app,
      /** Adımı yeniden doğrula ve İleri'yi aç/kapa için çağıranlar (şimdilik yalnız hata temizler). */
      clearError: () => { error.textContent = ''; },
      /** Alt bilgi satırı (ör. "3 öğrenci seçildi"). */
      note: (text) => { note.textContent = text || ''; },
      /** Başka adıma geç (özet satırındaki "değiştir" bağlantıları). */
      go: (target) => go(target),
      close: (result) => close(result),
    };

    function build(position) {
      if (built.has(position)) return built.get(position);
      const step = steps[position];
      const slot = h('section', 'ak-wiz-pane');
      const stepTitle = h('h4', 'ak-wiz-title', `${position + 1}. ${step.label}`);
      stepTitle.tabIndex = -1;
      slot.append(stepTitle);
      const made = step.render(ctl);
      const control = made instanceof Node ? { node: made } : (made || { node: h('div') });
      slot.append(control.node);
      body.append(slot);
      const entry = { slot, title: stepTitle, control };
      built.set(position, entry);
      return entry;
    }

    function paint() {
      marks.forEach((item, position) => {
        item.classList.toggle('on', position === index);
        item.classList.toggle('done', position < index);
        if (position === index) item.setAttribute('aria-current', 'step');
        else item.removeAttribute('aria-current');
      });
      back.hidden = index === 0;
      next.textContent = index === steps.length - 1 ? finishLabel : 'İleri';
      next.classList.toggle('ak-wiz-finish', index === steps.length - 1);
    }

    function go(target) {
      if (busy || target < 0 || target >= steps.length || target === index) return;
      error.textContent = '';
      index = target;
      const entry = build(index);
      for (const [position, item] of built) item.slot.hidden = position !== index;
      paint();
      live.textContent = `Adım ${index + 1} / ${steps.length}: ${steps[index].label}`;
      entry.control.enter?.();
      entry.title.focus();
    }

    async function check(position) {
      const step = steps[position];
      if (!step.validate) return null;
      try {
        return await step.validate();
      } catch (failure) {
        return problem(failure);
      }
    }

    async function forward() {
      if (busy) return;
      error.textContent = '';
      setBusy(true);
      const message = await check(index);
      setBusy(false);
      if (closed) return;
      if (message) {
        error.textContent = message;
        return;
      }
      if (index < steps.length - 1) {
        go(index + 1);
        return;
      }
      await finish();
    }

    async function finish() {
      // Önceki adımlar da geçerli olmalı (kullanıcı geri dönüp bir şeyi boşaltmış olabilir).
      for (let position = 0; position < steps.length - 1; position += 1) {
        const message = await check(position); // eslint-disable-line no-await-in-loop
        if (message) {
          go(position);
          error.textContent = message;
          return;
        }
      }
      setBusy(true, 'Gönderiliyor…');
      try {
        const result = await onFinish({ key, progress: (text) => { note.textContent = text || ''; } });
        if (result === null || result === undefined) {
          setBusy(false);
          return;
        }
        close(result);
      } catch (failure) {
        setBusy(false);
        error.textContent = problem(failure);
      }
    }

    function setBusy(on, text = '') {
      busy = on;
      next.disabled = on;
      back.disabled = on;
      if (on && text) next.textContent = text;
      if (!on) paint();
      card.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    let release = () => {};
    function close(result) {
      if (closed) return;
      closed = true;
      document.removeEventListener('keydown', onKey);
      for (const entry of built.values()) {
        try { entry.control.destroy?.(); } catch { /* kapanış yarıda kalmaz */ }
      }
      overlay.remove();
      release();
      resolve(result);
    }
    const onKey = (event) => {
      if (event.key === 'Escape' && !busy) { event.stopPropagation(); close(null); }
    };
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay && !busy) close(null); });
    document.addEventListener('keydown', onKey);
    overlay.append(card);
    app.root.append(overlay);
    release = dialogA11y(card);
    go(Math.min(Math.max(start, 0), steps.length - 1));
  });
}

// ------------------------------------------------------------- toplu iş

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Kuyruktaki toplu işi bitene dek izler. `onProgress(job)` her yoklamada.
 * En çok `limitMs` (10 dk) bekler; sonra iş olduğu gibi döner (arka planda sürer).
 */
export async function followBulkJob(app, job, { onProgress, every = 1500, limitMs = 600_000 } = {}) {
  let current = job;
  const deadline = Date.now() + limitMs;
  while (current && ['queued', 'running'].includes(current.status) && Date.now() < deadline) {
    onProgress?.(current);
    await sleep(every); // eslint-disable-line no-await-in-loop
    current = await app.api.get(`/v1/admin/bulk-jobs/${current.id}`); // eslint-disable-line no-await-in-loop
  }
  onProgress?.(current);
  if (current?.status === 'failed') {
    throw new AkademiError({ code: 'bulk_job_failed', message: current.error || 'Toplu işlem tamamlanamadı.' });
  }
  return current;
}

/** "1.200 / 3.400 işlendi" — iş ilerlemesi metni. */
export function jobProgressText(job, unit = 'kayıt') {
  const done = job?.progress?.doneCount ?? 0;
  const total = job?.progress?.totalCount ?? 0;
  if (job?.status === 'queued') return 'Sıraya alındı; Akademi birazdan başlayacak…';
  return total ? `Arka planda işleniyor: ${num(done)} / ${num(total)} ${unit}` : 'Arka planda işleniyor…';
}

/**
 * Toplu işi başlatan istek + ilerleme penceresi (atama paketi uygula / kaldır).
 * "Arka planda bırak" pencereyi kapatır, iş Akademi'de sürer.
 * @returns {Promise<object|null>} son iş kaydı; hata ya da arka planda bırakılırsa null
 */
export async function bulkJobDialog(app, start, { title = 'İşleniyor…', unit = 'öğrenci' } = {}) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog ak-dialog');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  const bar = progress(['Gönderildi', 'İşleniyor', 'Bitti']);
  const text = h('div', 'ak-muted');
  text.setAttribute('role', 'status');
  let left = false;
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Arka planda bırak', { variant: 'ghost', onClick: () => { left = true; finish(); } }));
  box.append(h('h3', 'kit-dialog-title', title), bar.node, text, actions);
  overlay.append(box);
  app.root.append(overlay);
  const release = dialogA11y(box);
  let finished = false;
  function finish() {
    if (finished) return;
    finished = true;
    overlay.remove();
    release();
  }
  try {
    const job = await start();
    bar.step(0);
    const done = await followBulkJob(app, job, { onProgress: (current) => {
      if (left) return;
      if (current && ['queued', 'running'].includes(current.status)) {
        bar.step(1);
        text.textContent = jobProgressText(current, unit);
      }
    } });
    if (left) {
      app.toast('Toplu işlem tamamlandı.', 'good');
      return null;
    }
    if (done && ['queued', 'running'].includes(done.status)) {
      app.toast('İşlem arka planda sürüyor; birkaç dakika sonra listeyi yenileyin.', 'warn');
      return null;
    }
    bar.done('Bitti');
    return done;
  } catch (failure) {
    app.toast(problem(failure), 'bad');
    return null;
  } finally {
    finish();
  }
}
