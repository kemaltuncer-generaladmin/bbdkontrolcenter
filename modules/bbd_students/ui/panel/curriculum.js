// Seviye / alan / hedef sınav — saf model, DOM bilmez.
//
// KONTROL MERKEZİ ANA KAYNAKTIR: BBD Akademi öğrencinin müfredatını buradan
// türetir. Kural backend'de (`backend/curriculum.py`); burası yalnız ekranın
// aynı kuralı ÖNCEDEN göstermesi içindir — asıl kapı backend'dir.
//
//   gradeLevel  5..12 | 'graduate' | null
//   track       'SAY' | 'EA' | 'SOZ' | 'DIL' | null   (10. sınıf ve üstü / mezun)
//   examTarget  'LGS' | 'YKS' | null
//
// Sınıf adından gelen öneri (`curriculumSuggestion`) ASLA kendiliğinden
// yazılmaz; personel tek tıkla kabul eder.

export const GRADE_CHOICES = [
  ['', '—'],
  ...[5, 6, 7, 8, 9, 10, 11, 12].map((grade) => [String(grade), `${grade}. sınıf`]),
  ['graduate', 'Mezun'],
];

export const TRACK_CHOICES = [
  ['', '—'], ['SAY', 'Sayısal'], ['EA', 'Eşit ağırlık'], ['SOZ', 'Sözel'], ['DIL', 'Dil'],
];

export const EXAM_CHOICES = [['', '—'], ['LGS', 'LGS'], ['YKS', 'YKS']];

/** Seçim kutusunun değeri → model değeri. */
export function parseGrade(value) {
  if (value === '' || value === null || value === undefined) return null;
  if (value === 'graduate') return 'graduate';
  const number = Number(value);
  return Number.isInteger(number) && number >= 5 && number <= 12 ? number : null;
}

export function parseChoice(value) {
  return value === '' || value === undefined ? null : value;
}

/** Bu seviyede alan tutulabilir mi? Bilinmeyen seviyede evet (backend ile aynı). */
export function trackAllowed(grade) {
  return grade === null || grade === undefined || grade === 'graduate' || Number(grade) >= 10;
}

/** Seviyeye göre önerilen sınav: 8 → LGS, 11/12/mezun → YKS. */
export function suggestedExam(grade) {
  if (Number(grade) === 8) return 'LGS';
  if (grade === 'graduate' || Number(grade) === 11 || Number(grade) === 12) return 'YKS';
  return null;
}

export function gradeLabel(grade) {
  if (grade === 'graduate') return 'Mezun';
  return grade === null || grade === undefined ? '—' : `${grade}. sınıf`;
}

const TRACK_LABEL = Object.fromEntries(TRACK_CHOICES);

/** "12. sınıf · Sayısal · YKS" */
export function describe(plan) {
  if (!plan) return '';
  return [
    plan.gradeLevel !== null && plan.gradeLevel !== undefined ? gradeLabel(plan.gradeLevel) : '',
    plan.track ? TRACK_LABEL[plan.track] || plan.track : '',
    plan.examTarget || '',
  ].filter(Boolean).join(' · ');
}

/**
 * Önerinin BOŞ alanlara uygulanmış hâli — dolu alana dokunmaz.
 * Dönüş: yalnız değişecek anahtarlar; hiçbiri yoksa boş nesne.
 */
export function fillFromSuggestion(student) {
  const plan = student.curriculumSuggestion;
  if (!plan) return {};
  const changes = {};
  const grade = student.gradeLevel ?? null;
  const nextGrade = grade ?? plan.gradeLevel ?? null;
  if (grade === null && plan.gradeLevel !== null && plan.gradeLevel !== undefined) {
    changes.gradeLevel = plan.gradeLevel;
  }
  if (!student.track && plan.track && trackAllowed(nextGrade)) changes.track = plan.track;
  if (!student.examTarget && plan.examTarget) changes.examTarget = plan.examTarget;
  return changes;
}
