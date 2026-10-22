// BBD Akademi — öğrencinin koçları: ata / ekle / değiştir / bitir (F-K2; Akademi README §14 madde 134).
//
// Öğrenci başına EN ÇOK 3 açık koç eşlemesi; aynı koç iki kez olmaz (`409 coach_already_assigned`), dördüncü
// `409 coach_limit_reached`. İlk atanan BİRİNCİL'dir (`CoachAssignment.primary`; eski tekil `coach` alanları onu
// döner). Değiştir / bitir BELİRLİ eşlemeye gider (`admin_changeCoach` gövdesinde `assignmentId`,
// `admin_endCoachAssignment` yolunda kimlik): yalnız o koçun görevleri devredilir ve yalnız onunla yazışmalar
// arşive geçer; öğrencinin diğer koçları sürer. Hem öğrenci kartı (Koçluk → Koç) hem eşleme tablosu (Koçluk →
// Koçlar → Eşleme tablosu, satırdaki koç çipleri) bu pencereleri kullanır. Metinler web ile aynı
// (`web/src/features/admin/coaching/strings.ts`).

import { formDialog, todayIst } from '../common.js';

/** Öğrenci başına en çok açık koç eşlemesi (sunucu `409 coach_limit_reached`). */
export const MAX_COACHES = 3;
export const COACH_LIMIT_TEXT = 'En çok 3 koç atanabilir';
export const PRIMARY_HINT = 'Birincil koç: ilk atanan; öğrencinin varsayılan koçu (eski sürüm uygulamalar bunu görür).';

/** Eşleme açık mı (bitişi yazılmamış, bitmemiş) — "Eşlemeyi bitir" yalnız bunda. */
export const isOpen = (row) => Boolean(row) && !row.endsOn && row.status !== 'ended';

/**
 * Güncel eşlemeler (bitmemiş): birincil önce, sonra bitişi yazılmamış, sonra başlangıç sırası. Eski yanıtta
 * (`primary` yok) sıra başlangıca göre.
 */
export function currentAssignments(items) {
  const rank = (row) => (row.primary ? 0 : 1);
  return (items || []).filter((row) => row && row.status !== 'ended')
    .sort((a, b) => rank(a) - rank(b) || Number(Boolean(a.endsOn)) - Number(Boolean(b.endsOn))
      || String(a.startsOn || '').localeCompare(String(b.startsOn || ''))
      || String(a.createdAt || '').localeCompare(String(b.createdAt || '')));
}

/** "Koç ekle" açık mı: açık eşleme sayısı sınırın altında. */
export const canAddCoach = (items) => (items || []).filter(isOpen).length < MAX_COACHES;

/** Eşleme tablosu satırının eşlemeleri (birincil önce); eski yanıtta (`assignments` yok) tekil alan. */
export const rowAssignments = (row) => currentAssignments(Array.isArray(row?.assignments) ? row.assignments
  : row?.assignment ? [row.assignment] : []);

/** Koç seçeneği: "Ad · 12/20 öğrenci" (kapasite kurum ya da koç sınırı). */
export function coachChoice(row) {
  const load = row.maxStudents ? `${row.studentCount}/${row.maxStudents}` : String(row.studentCount);
  return { value: row.coach.id, label: `${row.coach.displayName} · ${load} öğrenci` };
}

/** Koç seçenekleri (yükleriyle) — `admin_listCoaches`. */
export async function loadCoachChoices(app) {
  const data = await app.api.get('/v1/admin/coaching/coaches', { sort: 'name' });
  return (data?.items || []).filter((row) => row?.coach?.id).map(coachChoice);
}

const coachField = (options, label = 'Koç') => ({ key: 'coachId', label, type: 'select', required: true,
  options: [{ value: '', label: options.length ? 'Öğretmen / Koç seçin' : 'Seçilecek öğretmen / koç yok' }, ...options],
  hint: 'Öğretmen / Koç rolündeki etkin personel; yanında üstlendiği öğrenci sayısı.' });
const reasonCheck = (value) => (String(value || '').trim().length < 3 ? 'En az 3 karakter yazın.' : null);
const without = (options, current) => {
  const taken = new Set((current || []).map((row) => row.coach?.id).filter(Boolean));
  return options.filter((option) => !taken.has(option.value));
};

function capacityToast(app, capacity) {
  if (capacity?.exceeded) {
    app.toast(`${capacity.coach?.displayName || 'Koç'} kapasiteyi aştı (${capacity.studentCount}/`
      + `${capacity.maxStudents}); atama yine yapıldı.`, 'warn');
  }
}

/**
 * Koç ata (koçsuz öğrenci) / Koç ekle (ek koç). `current`: öğrencinin güncel eşlemeleri (seçiciden düşer).
 * @returns {Promise<object|null>} `CoachAssignmentResult` ya da vazgeçildiyse null
 */
export async function addCoachDialog(app, studentId, { options, current = [] }) {
  if (!canAddCoach(current)) {
    app.toast(`${COACH_LIMIT_TEXT}.`, 'warn');
    return null;
  }
  const extra = current.length > 0;
  const result = await formDialog(app, {
    title: extra ? 'Koç ekle' : 'Koç ata',
    description: extra
      ? 'Öğrencinin diğer koçları sürer; yeni koç da öğrenci ve veliye "koçunuz" bildirimiyle duyurulur. '
        + `${COACH_LIMIT_TEXT}.`
      : 'Öğrenci ve veliye "koçunuz" bildirimi gider; koç bu öğrencinin koçluk ekranlarını görür.',
    value: { coachId: '', startsOn: todayIst() },
    fields: [coachField(without(options, current)), { key: 'startsOn', label: 'Başlangıç', type: 'date', required: true }],
    submitLabel: extra ? 'Ekle' : 'Ata',
    onSubmit: (draft, { key }) => {
      if (!draft.coachId) throw new Error('Koç seçin.');
      return app.api.post('/v1/admin/coach-assignments',
        { studentId, coachId: draft.coachId, startsOn: draft.startsOn || undefined }, { key });
    },
  });
  if (!result) return null;
  app.toast(`${result.assignment?.coach?.displayName || 'Koç'} ${extra ? 'eklendi' : 'atandı'}.`, 'good');
  capacityToast(app, result.capacity);
  return result;
}

/** Belirli eşlemenin koçunu değiştirir (`assignmentId`); yalnız o koçun görevleri ve yazışmaları etkilenir. */
export async function changeCoachDialog(app, studentId, assignment, { options, current = [] }) {
  const coach = assignment.coach?.displayName || '—';
  const others = current.filter((row) => row.id !== assignment.id);
  const result = await formDialog(app, {
    title: 'Koçu değiştir',
    description: others.length
      ? `Değişen koç: ${coach}. Yalnız onun açık görevleri yeni koça devredilir ve yalnız onunla yazışmalar arşive `
        + 'geçer; öğrencinin diğer koçları sürer. Devir notu yeni koça görünür.'
      : `Şu anki koç: ${coach}. Açık koç görevleri ve yazışmalar yeni koça devredilir; devir notu yeni koça görünür.`,
    value: { coachId: '', effectiveOn: todayIst(), reason: '', handoverNote: '' },
    fields: [
      coachField(without(options, current), 'Yeni koç'),
      { key: 'effectiveOn', label: 'Geçerlilik', type: 'date', required: true },
      { key: 'reason', label: 'Gerekçe', type: 'text', required: true, maxLength: 300, wide: true,
        validate: reasonCheck },
      { key: 'handoverNote', label: 'Devir notu', type: 'textarea', required: true, maxLength: 2000, wide: true,
        validate: reasonCheck },
    ],
    submitLabel: 'Değiştir',
    onSubmit: (draft, { key }) => {
      if (!draft.coachId) throw new Error('Yeni koçu seçin.');
      return app.api.post(`/v1/admin/students/${studentId}/coach-change`, {
        newCoachId: draft.coachId, assignmentId: assignment.id || undefined,
        effectiveOn: draft.effectiveOn || undefined,
        reason: String(draft.reason).trim(), handoverNote: String(draft.handoverNote).trim(),
      }, { key });
    },
  });
  if (!result) return null;
  app.toast(`Koç değişti: ${result.created?.coach?.displayName || ''}.`, 'good');
  capacityToast(app, result.capacity);
  return result;
}

/** Belirli eşlemeyi bitirir; öğrencinin diğer koçları sürer. */
export async function endCoachDialog(app, assignment, { current = [] } = {}) {
  const coach = assignment.coach?.displayName || 'Koç';
  const others = current.filter((row) => row.id !== assignment.id);
  const result = await formDialog(app, {
    title: 'Eşlemeyi bitir',
    description: others.length
      ? `${coach} ile eşleme biter; öğrencinin diğer koçları sürer. Yalnız ${coach} ile yazışmalar arşive geçer.`
      : `${coach} ile eşleme biter; öğrenci koçsuz kalır.`,
    value: { endsOn: todayIst(), reason: '' },
    fields: [
      { key: 'endsOn', label: 'Bitiş', type: 'date', required: true },
      { key: 'reason', label: 'Gerekçe', type: 'text', required: true, maxLength: 300, wide: true,
        validate: reasonCheck },
    ],
    submitLabel: 'Bitir',
    onSubmit: (draft, { key }) => app.api.post(`/v1/admin/coach-assignments/${assignment.id}/end`,
      { endsOn: draft.endsOn || undefined, reason: String(draft.reason).trim() }, { key }),
  });
  if (!result) return null;
  app.toast('Eşleme bitti.', 'good');
  return result;
}
