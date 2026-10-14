// BBD Akademi — Koçluk sekmelerinin paylaştığı DOM yardımcıları.
//
// `coachingJob`: koçluk arka plan işi (`202 CoachingJob`: hafta PDF'i, koçluk
// dönem raporu, şablonla toplu atama) → `GET /v1/admin/coaching/jobs/{id}`
// artan aralıkla yoklanır; PDF yeni pencerede açılır.
// `coachOptions`: koç seçeneği (`admin_listCoaches`; öğretmen = koç, ETag'li).

import { button, h } from '../../ui-kit/kit.js';
import { badge, progress } from '../../ui-kit/layout.js';
import { AkademiError, openBlob, problem, saveBlob } from './api.js';
import { statusBadge } from './common.js';
import { RISK } from './coaching-logic.js';

export const COACHES = '/v1/admin/coaching/coaches';

/** Öğrenci hücresi: ad + sınıf. */
export function studentCell(student) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', student?.displayName || '—'), h('small', '', student?.classGroup?.name || ''));
  return cell;
}

export function riskBadge(risk) {
  return statusBadge(RISK, risk || 'none');
}

/** Koç listesi (yük satırları) — 60 sn önbellek. */
export async function coachRows(app, sort = 'name') {
  const data = await app.api.getCached(COACHES, { sort });
  return Array.isArray(data?.items) ? data.items : [];
}

/** Koç seçenekleri `[{value, label}]`. */
export async function coachOptions(app, { empty } = {}) {
  let rows = [];
  try {
    rows = await coachRows(app);
  } catch {
    rows = [];
  }
  const list = rows.map((row) => ({ value: row.coach?.id, label: row.coach?.displayName || '—' }))
    .filter((item) => item.value);
  return empty !== undefined ? [{ value: '', label: empty }, ...list] : list;
}

/** Düğmeyi aç / kapat — yetkisizlik için kapalı çizilmiş düğme (`blockedButton`) hep kapalı kalır. */
export function setEnabled(control, on) {
  if (!control || control.dataset?.blocked) return;
  control.disabled = !on;
}

/** "Bilgi yok" yerine sakin bir ibare. */
export function muted(text) {
  return h('span', 'ak-muted', text);
}

/** Sayaç rozeti (0 → sönük). */
export function countBadge(value, tone = 'warn') {
  return badge(String(value ?? 0), value ? tone : 'dim');
}

/**
 * Koçluk arka plan işi. `start()` → `CoachingJob`; bitince PDF açılır
 * (`week_pdf`, `coaching_report`) ya da iş kaydı döner (`template_apply`).
 * @returns {Promise<object|null>} son iş kaydı ya da hatada null
 */
export async function coachingJob(app, start, { name = 'kocluk', label = 'Hazırlanıyor…', download = true } = {}) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog ak-dialog');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-label', label);
  const bar = progress(['Sıraya alındı', 'Hazırlanıyor', download ? 'İndiriliyor' : 'Tamamlandı']);
  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Arka planda bırak', { variant: 'ghost', onClick: () => overlay.remove() }));
  box.append(h('h3', 'kit-dialog-title', label), bar.node, actions);
  overlay.append(box);
  app.root.append(overlay);
  try {
    let job = await start();
    bar.step(0);
    const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
    let delay = 700;
    const deadline = Date.now() + 180_000;
    while (job && ['queued', 'running'].includes(job.status) && Date.now() < deadline) {
      await wait(delay); // eslint-disable-line no-await-in-loop
      delay = Math.min(delay * 1.5, 5000);
      job = await app.api.get(`/v1/admin/coaching/jobs/${job.id}`); // eslint-disable-line no-await-in-loop
      bar.step(job?.status === 'running' ? 1 : 0);
    }
    if (!job || job.status !== 'done') {
      throw new AkademiError({ code: 'job_failed', message: job?.error
        || (job && ['queued', 'running'].includes(job.status)
          ? 'İş hâlâ sürüyor; biraz sonra tekrar deneyin.' : 'İş tamamlanamadı.') });
    }
    if (download && job.url) {
      bar.step(2);
      const file = await app.api.fetchFile(job.url, `${name}.pdf`);
      if (/\.pdf$/i.test(file.filename) || file.blob.type === 'application/pdf') openBlob(file);
      else saveBlob(file);
      app.toast('Dosya hazır.', 'good');
    }
    bar.done('Tamamlandı');
    return job;
  } catch (failure) {
    app.toast(problem(failure, 'İş tamamlanamadı.'), 'bad');
    return null;
  } finally {
    overlay.remove();
  }
}

/** İmzalı dosya bağlantısını (foto / PDF) KM köprüsünden açar. */
export async function openFile(app, file, fallback = 'dosya') {
  if (!file?.url) return;
  try {
    const got = await app.api.fetchFile(file.url, fallback);
    openBlob(got);
  } catch (failure) {
    app.toast(problem(failure, 'Dosya açılamadı.'), 'bad');
  }
}
