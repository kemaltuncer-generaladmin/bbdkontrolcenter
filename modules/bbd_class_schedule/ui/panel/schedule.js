// Ders takvimi — saf model. DOM bilmez, bu yüzden ayrı test edilebilir.
//
// Tasarım kararı: bir ders SAAT + AD'dır. Ad zorunlu değil — saatleri hızlıca
// girip adları sonra doldurmak isteyen kullanıcı engellenmez; ad boşsa blok
// yalnızca saatiyle görünür ve sıra numarası konumdan gelir.

export const DAYS = [
  { key: 'mon', name: 'Pazartesi', short: 'Pzt' },
  { key: 'tue', name: 'Salı', short: 'Sal' },
  { key: 'wed', name: 'Çarşamba', short: 'Çar' },
  { key: 'thu', name: 'Perşembe', short: 'Per' },
  { key: 'fri', name: 'Cuma', short: 'Cum' },
];

/** Varsayılan ders süresi (dk) — ilk blok eklenirken kullanılır. */
export const DEFAULT_DURATION = 40;

/**
 * Kullanıcının yazdığını saate çevirir. Klavyeden hızlı girişi hedefler:
 *   "9" → 09:00 · "930" → 09:30 · "9:3" → 09:30 · "0930" → 09:30
 * ("9:3" yazan kişi 9:30 demek istiyordur; eksik hane sağa sıfırla tamamlanır.)
 * Anlamlandıramazsa null döner; çağıran yerde hata gösterilir.
 */
export function parseTime(input) {
  const raw = String(input ?? '').trim();
  if (raw === '') return null;

  const digits = raw.replace(/\D/g, '');
  if (digits === '' || digits.length > 4) return null;

  let hours;
  let minutes;

  if (raw.includes(':') || raw.includes('.')) {
    const [left, right = ''] = raw.split(/[:.]/);
    hours = Number(left.replace(/\D/g, ''));
    minutes = right === '' ? 0 : Number(right.replace(/\D/g, '').padEnd(2, '0'));
  } else if (digits.length <= 2) {
    hours = Number(digits);
    minutes = 0;
  } else {
    hours = Number(digits.slice(0, digits.length - 2));
    minutes = Number(digits.slice(-2));
  }

  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
  if (hours > 23 || minutes > 59) return null;

  return toClock(hours * 60 + minutes);
}

/** Dakikayı "HH:MM" biçimine getirir. */
export function toClock(totalMinutes) {
  const wrapped = ((totalMinutes % 1440) + 1440) % 1440;
  const hours = Math.floor(wrapped / 60);
  const minutes = wrapped % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

/** "HH:MM" → gün başından beri geçen dakika. */
export function toMinutes(clock) {
  const [hours, minutes] = clock.split(':').map(Number);
  return hours * 60 + minutes;
}

export function duration(block) {
  return toMinutes(block.end) - toMinutes(block.start);
}

/** "1 sa 30 dk" · "45 dk" · "2 sa" */
export function formatDuration(totalMinutes) {
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes} dk`;
  if (minutes === 0) return `${hours} sa`;
  return `${hours} sa ${minutes} dk`;
}

export function sortBlocks(blocks) {
  return [...blocks].sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
}

export function totalMinutes(blocks) {
  return blocks.reduce((sum, block) => sum + duration(block), 0);
}

/**
 * Çakışan blokların kimlikleri. Uyarı içindir, engel değil: gerçek hayatta
 * geçici olarak üst üste binen plan girilebilir, ekran bunu sessizce
 * kaybetmez ama işaretler.
 */
export function overlappingIds(blocks) {
  const sorted = sortBlocks(blocks);
  const clashing = new Set();
  for (let i = 1; i < sorted.length; i += 1) {
    const previous = sorted[i - 1];
    const current = sorted[i];
    if (toMinutes(current.start) < toMinutes(previous.end)) {
      clashing.add(previous.id);
      clashing.add(current.id);
    }
  }
  return clashing;
}

/** Ders adının üst sınırı — sütuna sığması için. */
export const NAME_MAX = 40;

export function cleanName(input) {
  return String(input ?? '').trim().replace(/\s+/g, ' ').slice(0, NAME_MAX);
}

/** Yeni blok. Geçersizse `{ error }` döner — çağıran yer karar verir. */
export function makeBlock(startInput, endInput, { fallbackDuration = DEFAULT_DURATION, name = '' } = {}) {
  const start = parseTime(startInput);
  if (start === null) return { error: 'Başlangıç saati anlaşılmadı.' };

  // Bitiş boş bırakılabilir: son kullanılan süre kadar eklenir.
  const end = String(endInput ?? '').trim() === ''
    ? toClock(toMinutes(start) + fallbackDuration)
    : parseTime(endInput);

  if (end === null) return { error: 'Bitiş saati anlaşılmadı.' };
  if (toMinutes(end) <= toMinutes(start)) return { error: 'Bitiş, başlangıçtan sonra olmalı.' };

  return { block: { id: newId(), start, end, name: cleanName(name) } };
}

/** Grupta daha önce kullanılmış ders adları — öneri listesi için. */
export function usedNames(week) {
  const names = new Set();
  for (const day of DAYS) {
    for (const block of week[day.key] || []) {
      if (block.name) names.add(block.name);
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b, 'tr'));
}

export function newId() {
  return `b${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

/** Boş haftalık plan. */
export function emptyWeek() {
  return Object.fromEntries(DAYS.map((day) => [day.key, []]));
}
