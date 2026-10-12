// BBD Akademi — Ders programı bölümünün ortak parçaları (timetable*.js).
//
// ZİL DİLİMİ DÜZENİ. Akademi zil dilimlerini GÜN BAŞINA verir (`dayOfWeek` +
// `order`); günler farklı saatte başlayabilir, cumartesi daha kısa olabilir.
// Izgarada satır = "günün n'inci dilimi": her gün kendi sırasıyla dizilir ve
// aynı sıradaki dilimler yan yana gelir. Bir satırın bütün dilimleri teneffüs
// ise satır teneffüs şeridi olarak çizilir.
//
// ÇAKIŞMA METNİ. `409 timetable_conflict` yanıtının `details` öğeleri
// (`TimetableConflict`) hem program kaydında hem etüt oluştururken aynı
// biçimde gelir; cümle tek yerde kurulur.

import { h } from '../../ui-kit/kit.js';
import { AkademiError } from './api.js';
import { DAY_LABEL, WEEKDAYS } from './common.js';

export const DAY_KEYS = WEEKDAYS.map(([key]) => key);
export const SHORT_DAY = Object.fromEntries(WEEKDAYS.map(([key, short]) => [key, short]));

const JS_DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

/** 'YYYY-MM-DD' → Akademi gün anahtarı ('mon' …). */
export function dayKeyOf(iso) {
  const [year, month, day] = String(iso || '').slice(0, 10).split('-').map(Number);
  if (!year || !month || !day) return null;
  return JS_DAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
}

export const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/** 'HH:MM' → dakika. */
export function minutesOf(clock) {
  const [hour, minute] = String(clock || '').split(':').map(Number);
  return hour * 60 + minute;
}

/** Dakika → 'HH:MM' (gün içinde kalır). */
export function clockOf(minutes) {
  const value = Math.max(0, Math.min(23 * 60 + 59, Math.round(minutes)));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

export function slotTime(slot) {
  return slot ? `${slot.startTime}–${slot.endTime}` : '';
}

/** Hücre anahtarı: gün + zil dilimi. */
export const cellKey = (dayOfWeek, bellSlotId) => `${dayOfWeek}|${bellSlotId}`;

/**
 * Zil dilimleri → ızgara düzeni.
 * @returns {{days: string[], rows: Array<{index, cells: Object, isBreak, ref, lessonNo, label}>,
 *   slot(day, id), place(day, id) → {row, slot} | null, lessonsOf(day)}}
 */
export function slotLayout(slots = []) {
  const byDay = new Map();
  for (const slot of slots || []) {
    if (!byDay.has(slot.dayOfWeek)) byDay.set(slot.dayOfWeek, []);
    byDay.get(slot.dayOfWeek).push(slot);
  }
  const days = DAY_KEYS.filter((key) => byDay.has(key));
  for (const list of byDay.values()) {
    list.sort((a, b) => (a.order - b.order) || a.startTime.localeCompare(b.startTime));
  }
  const rowCount = Math.max(0, ...[...byDay.values()].map((list) => list.length));
  const rows = [];
  const places = new Map();
  let lessonNo = 0;
  for (let index = 0; index < rowCount; index += 1) {
    const cells = {};
    for (const day of days) cells[day] = byDay.get(day)[index] || null;
    const present = Object.values(cells).filter(Boolean);
    const isBreak = present.length > 0 && present.every((slot) => slot.kind === 'break');
    // Satır başlığındaki saat: en çok günde görülen saat (hafta içi çoğunluk).
    const tally = new Map();
    for (const slot of present) {
      const key = slotTime(slot);
      tally.set(key, (tally.get(key) || 0) + 1);
    }
    const common = [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
    const ref = present.find((slot) => slotTime(slot) === common) || present[0] || null;
    if (!isBreak) lessonNo += 1;
    const label = isBreak ? (ref?.label || 'Teneffüs') : (ref?.label || `${lessonNo}. ders`);
    const row = { index, cells, isBreak, ref, lessonNo: isBreak ? null : lessonNo, label };
    rows.push(row);
    for (const slot of present) places.set(cellKey(slot.dayOfWeek, slot.id), { row, slot });
  }
  return {
    days,
    rows,
    empty: rows.length === 0,
    place: (day, id) => places.get(cellKey(day, id)) || null,
    slot: (day, id) => places.get(cellKey(day, id))?.slot || null,
    /** Günün ders dilimleri (teneffüs hariç), sırasıyla. */
    lessonsOf(day) {
      return rows.map((row) => ({ row, slot: row.cells[day] }))
        .filter((item) => item.slot && item.slot.kind === 'lesson');
    },
  };
}

/** "Pazartesi 3. ders (09:20–10:00)". */
export function slotName(layout, day, bellSlotId) {
  const place = layout?.place(day, bellSlotId);
  const dayText = DAY_LABEL[day] || day || '';
  if (!place) return `${dayText} (zil dilimi bulunamadı)`;
  const { row, slot } = place;
  const name = slot.kind === 'break' ? (slot.label || 'Teneffüs')
    : (slot.label || (row.lessonNo ? `${row.lessonNo}. ders` : 'Ders'));
  return `${dayText} ${name} (${slotTime(slot)})`;
}

/** Tek çakışma → Türkçe cümle. */
export function conflictText(item, layout) {
  const where = slotName(layout, item.dayOfWeek, item.bellSlotId);
  const group = item.conflictingClassGroup?.name;
  const place = item.conflictingLessonEventId
    ? `bir etütte${group ? ` (${group})` : ''}`
    : `${group || 'başka bir'} sınıfında`;
  if (item.kind === 'room') {
    return `${where}: ${item.room || 'Derslik'} dersliği aynı saatte ${place} kullanılıyor.`;
  }
  return `${where}: ${item.teacher?.displayName || 'Öğretmen'} aynı saatte ${place} derste.`;
}

/**
 * `timetable_conflict` hatasını okunur cümleli hataya çevirir (pencerelerde
 * gösterilsin diye). Başka hata olduğu gibi döner.
 */
export function readableConflict(failure, layout) {
  if (!(failure instanceof AkademiError) || failure.code !== 'timetable_conflict'
    || !Array.isArray(failure.details) || !failure.details.length) return failure;
  const lines = failure.details.map((item) => conflictText(item, layout));
  return new AkademiError({ code: failure.code, status: failure.status,
    message: `Çakışma var, hiçbir şey kaydedilmedi. ${lines.join(' ')}` });
}

// ------------------------------------------------------------- seçiciler

const SUBJECT_LEVELS = [['ortaokul', 'Ortaokul'], ['lise', 'Lise'], ['exam', 'Sınav hazırlık'],
  ['custom', 'Kuruma özel']];

/** Sınıf düzeyine uyan ders grubu önce gelir (5–8 ortaokul, 9+ lise). */
function levelOrder(gradeLevel) {
  const grade = Number(gradeLevel);
  const first = grade >= 9 || gradeLevel === 'graduate' ? 'lise' : (grade >= 5 ? 'ortaokul' : null);
  const keys = SUBJECT_LEVELS.map(([key]) => key);
  return first ? [first, ...keys.filter((key) => key !== first)] : keys;
}

/**
 * Ders açılır kutusu — düzeye göre `<optgroup>`lu. `extra`: listede olmayan
 * (ör. kaldırılmış) mevcut ders `{id, name}`.
 */
export function subjectSelect(subjects, value, { gradeLevel = null, empty = 'Ders seçilmedi',
  extra = null, onChange, label = 'Ders' } = {}) {
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', label);
  const blank = h('option', '', empty);
  blank.value = '';
  select.append(blank);
  const names = { ...Object.fromEntries(SUBJECT_LEVELS) };
  const known = new Set();
  for (const level of levelOrder(gradeLevel)) {
    const list = (subjects || []).filter((item) => (item.level || 'custom') === level);
    if (!list.length) continue;
    const group = h('optgroup');
    group.label = names[level] || level;
    for (const subject of list) {
      const option = h('option', '', subject.name);
      option.value = subject.id;
      known.add(subject.id);
      group.append(option);
    }
    select.append(group);
  }
  const rest = (subjects || []).filter((item) => !known.has(item.id));
  for (const subject of rest) {
    const option = h('option', '', subject.name);
    option.value = subject.id;
    known.add(subject.id);
    select.append(option);
  }
  if (extra?.id && !known.has(extra.id)) {
    const option = h('option', '', `${extra.name || extra.id} (listede yok)`);
    option.value = extra.id;
    select.append(option);
  }
  select.value = value || '';
  if (onChange) select.addEventListener('change', () => onChange(select.value));
  return select;
}

/** Öğretmen seçenekleri; listede olmayan mevcut öğretmen de eklenir. */
export function teacherOptions(teachers, current = null, empty = 'Öğretmen yok') {
  const options = [{ value: '', label: empty },
    ...(teachers || []).map((item) => ({ value: item.id, label: item.displayName || item.name || item.id }))];
  if (current?.id && !options.some((item) => item.value === current.id)) {
    options.push({ value: current.id, label: `${current.displayName || current.id} (pasif)` });
  }
  return options;
}

/**
 * Ders başına sabit renk tonu (0–359): aynı ders ızgarada hep aynı renkte
 * görünür. Renk tek başına anlam taşımaz; ders adı her zaman yazılıdır.
 */
export function subjectHue(subjectId) {
  let hash = 7;
  for (const char of String(subjectId || '')) hash = (hash * 31 + char.charCodeAt(0)) % 3600;
  return Math.round(hash / 10) % 360;
}

/** Ders kimliği → ders nesnesi araması. */
export function subjectIndex(subjects) {
  return new Map((subjects || []).map((item) => [item.id, item]));
}
