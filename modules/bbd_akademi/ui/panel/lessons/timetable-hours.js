// BBD Akademi — Ders programı › DERS SAATLERİ (Akademi README §14 madde 128, iş 71).
//
// KULLANICI (17.10.2026): "kontrol merkezinde ders programı kısmını zil sisteminden bağımsız hale getiriyoruz. her hafta
// için ders başlangıç ders süresi tenefüs süresi gibi parametreler girilecek."
//
// Takvimin satırları artık KM Zil Sistemi'nden değil, haftanın DERS SAATLERİNDEN gelir (`TimetableWeek.hours`). Hafta
// okunun yanındaki "Ders saatleri" düğmesi küçük bir panel açar: Başlangıç, Ders süresi, Teneffüs, Günlük ders sayısı,
// Öğle arası (isteğe bağlı), "Günlere göre farklı" (gün satırları: başlangıç, ders sayısı, ders yok) ve CANLI ÖNİZLEME.
// Kaydet o haftadan itibaren (kendi saati olan sonraki haftaya kadar) geçerlidir. Ders sayısı azalınca Akademi
// `409 timetable_hours_removes_lessons` döner; panel "N ders 7.–8. saatte; bu haftadan itibaren kaldırılacak" diye sorar.
// Hesap Akademi'dekiyle (`program/week_hours.py`) birebir aynıdır — önizleme sunucuya gitmeden çizilir.

import { button, dialogA11y, h } from '../../../ui-kit/kit.js';
import { AkademiError } from '../api.js';
import { DAY_LABEL, num, problem } from '../common.js';
import { DAY_KEYS, SHORT_DAY, clockOf, minutesOf } from './timetable-shared.js';

export const PUT_HOURS = 'admin_putTimetableWeekHours';
export const MAX_LESSONS = 16;

export const DEFAULT_HOURS = {
  startTime: '09:00', lessonMinutes: 40, breakMinutes: 10, lessonsPerDay: 8, lunch: null,
  days: [{ dayOfWeek: 'sun', startTime: null, lessonsPerDay: null, off: true }],
};

// ------------------------------------------------------------------ saf hesap (Node'da sınanır)

function dayParams(settings, dayKey) {
  const day = (settings.days || []).find((item) => item.dayOfWeek === dayKey);
  if (day?.off) return null;
  return {
    start: minutesOf(day?.startTime || settings.startTime),
    count: Number(day?.lessonsPerDay || settings.lessonsPerDay) || 0,
  };
}

/**
 * Ayar → dilimler (Akademi `periods_of` ile aynı): `L<n>` ders, `B<n>` teneffüs, `OA` öğle arası.
 * @returns {Array<{id, dayOfWeek, order, lessonNo, kind, startTime, endTime, label}>}
 */
export function computePeriods(settings) {
  const out = [];
  const lesson = Number(settings.lessonMinutes) || 0;
  const pause = Number(settings.breakMinutes) || 0;
  const lunch = settings.lunch || null;
  const cap = (value) => Math.min(value, 24 * 60 - 1);
  for (const dayKey of DAY_KEYS) {
    const params = dayParams(settings, dayKey);
    if (!params) continue;
    let t = params.start;
    let order = 0;
    for (let n = 1; n <= params.count; n += 1) {
      order += 1;
      const end = cap(t + lesson);
      out.push({ id: `L${n}`, dayOfWeek: dayKey, order, lessonNo: n, kind: 'lesson', startTime: clockOf(t),
        endTime: clockOf(end), label: `${n}. ders` });
      t = end;
      if (n === params.count) break;
      if (lunch && Number(lunch.afterLesson) === n) {
        order += 1;
        const stop = cap(t + Number(lunch.minutes));
        out.push({ id: 'OA', dayOfWeek: dayKey, order, lessonNo: null, kind: 'lunch', startTime: clockOf(t),
          endTime: clockOf(stop), label: 'Öğle arası' });
        t = stop;
      } else if (pause > 0) {
        order += 1;
        const stop = cap(t + pause);
        out.push({ id: `B${n}`, dayOfWeek: dayKey, order, lessonNo: null, kind: 'break', startTime: clockOf(t),
          endTime: clockOf(stop), label: 'Teneffüs' });
        t = stop;
      }
    }
  }
  return out;
}

/** Dilimler → takvimin zil dilimi biçimi (`slotLayout`): öğle arası teneffüs satırı olarak, etiketiyle. */
export function periodsAsSlots(periods) {
  return (periods || []).map((item) => ({ id: item.id, dayOfWeek: item.dayOfWeek, order: item.order,
    startTime: item.startTime, endTime: item.endTime, kind: item.kind === 'lesson' ? 'lesson' : 'break',
    label: item.label, lunch: item.kind === 'lunch' }));
}

/** Ayarı doğrular; sorun yoksa ''. Gece yarısını geçen gün adıyla söylenir. */
export function hoursProblem(settings) {
  const time = /^([01]\d|2[0-3]):[0-5]\d$/;
  if (!time.test(settings.startTime || '')) return 'Başlangıç saatini SS:DD biçiminde girin.';
  const lesson = Number(settings.lessonMinutes);
  const pause = Number(settings.breakMinutes);
  const count = Number(settings.lessonsPerDay);
  if (!Number.isInteger(lesson) || lesson < 10 || lesson > 180) return 'Ders süresi 10–180 dk olmalı.';
  if (!Number.isInteger(pause) || pause < 0 || pause > 60) return 'Teneffüs 0–60 dk olmalı.';
  if (!Number.isInteger(count) || count < 1 || count > MAX_LESSONS) return `Günlük ders sayısı 1–${MAX_LESSONS} olmalı.`;
  if (settings.lunch) {
    const after = Number(settings.lunch.afterLesson);
    const minutes = Number(settings.lunch.minutes);
    if (!Number.isInteger(after) || after < 1 || after > 15) return 'Öğle arası 1.–15. dersten sonra olabilir.';
    if (!Number.isInteger(minutes) || minutes < 5 || minutes > 180) return 'Öğle arası 5–180 dk olmalı.';
  }
  for (const day of settings.days || []) {
    if (day.off) continue;
    if (day.startTime && !time.test(day.startTime)) return `${DAY_LABEL[day.dayOfWeek]} başlangıcını SS:DD girin.`;
    const n = day.lessonsPerDay;
    if (n !== null && n !== undefined && (!Number.isInteger(n) || n < 1 || n > MAX_LESSONS)) {
      return `${DAY_LABEL[day.dayOfWeek]} ders sayısı 1–${MAX_LESSONS} olmalı.`;
    }
  }
  for (const dayKey of DAY_KEYS) {
    const params = dayParams(settings, dayKey);
    if (!params || !params.count) continue;
    let end = params.start + params.count * lesson + (params.count - 1) * pause;
    if (settings.lunch && Number(settings.lunch.afterLesson) < params.count) end += Number(settings.lunch.minutes) - pause;
    if (end > 24 * 60) return `${DAY_LABEL[dayKey]} dersleri gece yarısını geçiyor; ders sayısını ya da başlangıcı azaltın.`;
  }
  return '';
}

/** Önizleme tablosu: satır = ders sırası (öğle arası araya), sütun = dersi olan günler. */
export function previewRows(periods) {
  const days = DAY_KEYS.filter((key) => periods.some((item) => item.dayOfWeek === key));
  const lessons = Math.max(0, ...periods.map((item) => item.lessonNo || 0));
  const rows = [];
  for (let n = 1; n <= lessons; n += 1) {
    rows.push({ label: `${n}. ders`, kind: 'lesson', cells: days.map((key) => {
      const item = periods.find((p) => p.dayOfWeek === key && p.lessonNo === n);
      return item ? `${item.startTime}–${item.endTime}` : '';
    }) });
    const lunchAfter = days.map((key) => {
      const item = periods.find((p) => p.dayOfWeek === key && p.kind === 'lunch'
        && p.order === (periods.find((x) => x.dayOfWeek === key && x.lessonNo === n)?.order ?? -9) + 1);
      return item ? `${item.startTime}–${item.endTime}` : '';
    });
    if (lunchAfter.some(Boolean)) rows.push({ label: 'Öğle arası', kind: 'lunch', cells: lunchAfter });
  }
  return { days, rows };
}

/** 409 `timetable_hours_removes_lessons` ayrıntısı → onay cümlesi. */
export function removalText(details) {
  const count = Number(details?.count) || 0;
  const nos = details?.lessonNumbers || [];
  const where = nos.length === 1 ? `${nos[0]}. saatte` : nos.length ? `${nos[0]}.–${nos[nos.length - 1]}. saatlerde` : '';
  const classes = [...new Set((details?.items || []).map((item) => item.classGroup?.name).filter(Boolean))];
  return `${num(count)} ders ${where}${classes.length ? ` (${classes.slice(0, 4).join(', ')}${classes.length > 4 ? '…' : ''})` : ''}; `
    + 'yeni saatlerde bu sıralar yok. Kaydederseniz bu haftadan itibaren programdan kaldırılır (geçmiş korunur).';
}

/** Ayarın nereden geldiği (panelin üstündeki sade not). `weekLabel` takvimden gelir. */
export function sourceNote(hours, weekLabel) {
  if (!hours) return '';
  if (hours.source === 'inherited' && hours.inheritedFrom) {
    return `${weekLabel(hours.inheritedFrom)} haftasının saatleri kullanılıyor.`;
  }
  if (hours.source === 'default') return 'Kurumun varsayılan ders saatleri kullanılıyor.';
  return 'Bu haftanın kendi ders saatleri.';
}

/** "Bu hafta ve sonraki haftalar (… haftasına kadar) bu saatleri kullanır." */
export function scopeNote(hours, weekLabel) {
  const until = hours?.source === 'week' ? hours.appliesUntil : (hours?.appliesUntil || null);
  return until
    ? `Kaydedince bu hafta ve sonraki haftalar ${weekLabel(addOne(until))} haftasına kadar bu saatleri kullanır.`
    : 'Kaydedince bu hafta ve sonraki bütün haftalar bu saatleri kullanır (kendi saati girilen haftaya kadar).';
}

function addOne(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  const next = new Date(Date.UTC(y, m - 1, d + 1));
  return next.toISOString().slice(0, 10);
}

/** Taslak (form değerleri) → sözleşme ayarı. Boş gün ezmesi düşer. */
export function settingsOf(draft) {
  const days = [];
  if (draft.perDay) {
    for (const dayKey of DAY_KEYS) {
      const row = draft.days?.[dayKey] || {};
      const item = { dayOfWeek: dayKey, startTime: row.off ? null : (row.startTime || null),
        lessonsPerDay: row.off || row.lessonsPerDay === '' || row.lessonsPerDay === null || row.lessonsPerDay === undefined
          ? null : Number(row.lessonsPerDay), off: Boolean(row.off) };
      if (item.off || (item.startTime && item.startTime !== draft.startTime)
        || (item.lessonsPerDay && item.lessonsPerDay !== Number(draft.lessonsPerDay))) days.push(item);
    }
  }
  return {
    startTime: draft.startTime,
    lessonMinutes: Number(draft.lessonMinutes),
    breakMinutes: Number(draft.breakMinutes),
    lessonsPerDay: Number(draft.lessonsPerDay),
    lunch: draft.lunchOn ? { afterLesson: Number(draft.lunchAfter), minutes: Number(draft.lunchMinutes) } : null,
    days,
  };
}

/** Sözleşme ayarı → taslak. */
export function draftOf(settings) {
  const s = settings || DEFAULT_HOURS;
  const days = {};
  for (const dayKey of DAY_KEYS) {
    const item = (s.days || []).find((d) => d.dayOfWeek === dayKey) || {};
    days[dayKey] = { startTime: item.startTime || '', lessonsPerDay: item.lessonsPerDay ?? '', off: Boolean(item.off) };
  }
  return {
    startTime: s.startTime, lessonMinutes: s.lessonMinutes, breakMinutes: s.breakMinutes, lessonsPerDay: s.lessonsPerDay,
    lunchOn: Boolean(s.lunch), lunchAfter: s.lunch?.afterLesson ?? 4, lunchMinutes: s.lunch?.minutes ?? 40,
    perDay: (s.days || []).length > 0, days,
  };
}

// ------------------------------------------------------------------ panel

/**
 * "Ders saatleri" paneli. Kaydedilirse `true`.
 * @param {object} app
 * @param {{week: string, hours: object|null, editable: boolean, weekLabel: (iso: string) => string}} options
 */
export function openHoursPanel(app, { week, hours, editable, weekLabel }) {
  return new Promise((resolve) => {
    const draft = draftOf(hours?.settings);
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-dialog ak-cal-dialog ak-hours');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.append(h('h3', 'kit-dialog-title', 'Ders saatleri'));
    card.append(h('p', 'kit-dialog-text', `${weekLabel(week)} · ${sourceNote(hours, weekLabel)}`));

    const field = (label, control, hint = '') => {
      const wrap = h('label', 'kit-field ak-hours-field');
      wrap.append(h('span', 'kit-field-label', label), control);
      if (hint) wrap.append(h('small', 'ak-muted', hint));
      return wrap;
    };
    const input = (type, value, aria, { min, max, step } = {}) => {
      const node = h('input', 'kit-input');
      node.type = type;
      node.value = value ?? '';
      node.setAttribute('aria-label', aria);
      if (min !== undefined) node.min = String(min);
      if (max !== undefined) node.max = String(max);
      if (step !== undefined) node.step = String(step);
      node.disabled = !editable;
      node.addEventListener('input', () => { read(); paintPreview(); });
      node.addEventListener('change', () => { read(); paintPreview(); });
      return node;
    };
    const start = input('time', draft.startTime, 'Başlangıç');
    const lesson = input('number', draft.lessonMinutes, 'Ders süresi (dk)', { min: 10, max: 180 });
    const pause = input('number', draft.breakMinutes, 'Teneffüs (dk)', { min: 0, max: 60 });
    const count = input('number', draft.lessonsPerDay, 'Günlük ders sayısı', { min: 1, max: MAX_LESSONS });
    const grid = h('div', 'ak-hours-grid');
    grid.append(field('Başlangıç', start), field('Ders süresi (dk)', lesson), field('Teneffüs (dk)', pause),
      field('Günlük ders sayısı', count));

    const lunchOn = input('checkbox', '', 'Öğle arası var');
    lunchOn.className = 'kit-check';
    lunchOn.checked = draft.lunchOn;
    const lunchAfter = input('number', draft.lunchAfter, 'Kaçıncı dersten sonra', { min: 1, max: 15 });
    const lunchMinutes = input('number', draft.lunchMinutes, 'Öğle arası (dk)', { min: 5, max: 180 });
    const lunchRow = h('div', 'ak-hours-lunch');
    const lunchLabel = h('label', 'ak-tt-check');
    lunchLabel.append(lunchOn, h('span', '', 'Öğle arası'));
    const lunchAfterWrap = h('span', 'ak-hours-inline');
    lunchAfterWrap.append(lunchAfter, h('span', '', '. dersten sonra'), lunchMinutes, h('span', '', 'dk'));
    lunchRow.append(lunchLabel, lunchAfterWrap);

    const details = h('details', 'ak-cal-details ak-hours-days');
    details.open = draft.perDay;
    details.append(h('summary', '', 'Günlere göre farklı (ör. hafta sonu sabah)'));
    const dayInputs = {};
    const dayTable = h('div', 'ak-hours-daytable');
    for (const dayKey of DAY_KEYS) {
      const row = draft.days[dayKey];
      const dayStart = input('time', row.startTime, `${DAY_LABEL[dayKey]} başlangıç`);
      const dayCount = input('number', row.lessonsPerDay, `${DAY_LABEL[dayKey]} ders sayısı`, { min: 1, max: MAX_LESSONS });
      dayCount.placeholder = 'aynı';
      const off = input('checkbox', '', `${DAY_LABEL[dayKey]} ders yok`);
      off.className = 'kit-check';
      off.checked = row.off;
      const offLabel = h('label', 'ak-tt-check');
      offLabel.append(off, h('span', '', 'Ders yok'));
      dayInputs[dayKey] = { dayStart, dayCount, off };
      dayTable.append(h('b', 'ak-hours-day', DAY_LABEL[dayKey]), dayStart, dayCount, offLabel);
    }
    details.append(dayTable);
    details.addEventListener('toggle', () => { read(); paintPreview(); });

    const preview = h('div', 'ak-hours-preview');
    preview.setAttribute('aria-live', 'polite');
    const note = h('p', 'ak-cal-hint ak-muted', editable ? scopeNote(hours, weekLabel)
      : 'Geçmiş hafta ya da yetkiniz yok — salt okunur.');
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    card.append(grid, lunchRow, details, h('b', 'ak-hours-title', 'Önizleme'), preview, note, error);

    function read() {
      draft.startTime = start.value;
      draft.lessonMinutes = lesson.value;
      draft.breakMinutes = pause.value;
      draft.lessonsPerDay = count.value;
      draft.lunchOn = lunchOn.checked;
      draft.lunchAfter = lunchAfter.value;
      draft.lunchMinutes = lunchMinutes.value;
      draft.perDay = details.open;
      for (const dayKey of DAY_KEYS) {
        const item = dayInputs[dayKey];
        draft.days[dayKey] = { startTime: item.dayStart.value, lessonsPerDay: item.dayCount.value, off: item.off.checked };
        item.dayStart.disabled = !editable || item.off.checked;
        item.dayCount.disabled = !editable || item.off.checked;
      }
      lunchAfter.disabled = !editable || !lunchOn.checked;
      lunchMinutes.disabled = !editable || !lunchOn.checked;
    }

    function paintPreview() {
      const settings = settingsOf(draft);
      const issue = hoursProblem(settings);
      preview.replaceChildren();
      error.textContent = '';
      if (issue) {
        preview.append(h('p', 'ak-muted', issue));
        return;
      }
      const { days, rows } = previewRows(computePeriods(settings));
      if (!days.length) {
        preview.append(h('p', 'ak-muted', 'Bu hafta hiç ders yok.'));
        return;
      }
      const table = h('table', 'ak-hours-table');
      const head = h('tr');
      head.append(h('th', '', ''));
      for (const dayKey of days) head.append(h('th', '', SHORT_DAY[dayKey]));
      table.append(head);
      for (const row of rows) {
        const tr = h('tr', row.kind === 'lunch' ? 'ak-hours-lunchrow' : '');
        tr.append(h('th', '', row.label));
        for (const value of row.cells) tr.append(h('td', '', value || '—'));
        table.append(tr);
      }
      preview.append(table);
    }

    let release = () => {};
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(value);
    };
    const save = button('Kaydet', { variant: 'primary', onClick: () => submit(false) });
    save.disabled = !editable;
    async function submit(confirmRemovals) {
      read();
      const settings = settingsOf(draft);
      const issue = hoursProblem(settings);
      if (issue) { error.textContent = issue; return; }
      save.disabled = true;
      save.textContent = 'Kaydediliyor…';
      try {
        const body = { settings, expectedVersion: hours?.version ?? 0 };
        if (confirmRemovals) body.confirmRemovals = true;
        const saved = await app.api.put(`/v1/admin/timetable/weeks/${week}/hours`, body);
        close(saved || true);
      } catch (failure) {
        save.disabled = false;
        save.textContent = 'Kaydet';
        if (failure instanceof AkademiError && failure.code === 'timetable_hours_removes_lessons' && !confirmRemovals) {
          error.textContent = '';
          askRemoval(failure.details);
          return;
        }
        error.textContent = problem(failure, 'Ders saatleri kaydedilemedi.');
      }
    }
    function askRemoval(details) {
      const box = h('div', 'ak-tt-notice bad ak-hours-confirm');
      box.append(h('b', '', removalText(details)));
      const tools = h('div', 'ak-inline');
      tools.append(button('Vazgeç', { onClick: () => box.remove() }),
        button('Kaldır ve kaydet', { variant: 'danger', onClick: () => { box.remove(); submit(true); } }));
      box.append(tools);
      error.replaceChildren(box);
    }
    const onKey = (event) => {
      if (event.key === 'Escape') { event.stopPropagation(); close(null); }
    };
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button(editable ? 'Vazgeç' : 'Kapat', { onClick: () => close(null) }));
    if (editable) actions.append(save);
    card.append(actions);
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    read();
    paintPreview();
    start.focus();
  });
}

/** Kaydedilen sonucun bildirimi. */
export function savedText(saved) {
  const bits = ['Ders saatleri kaydedildi.'];
  if (saved?.removedLessonCount) bits.push(`${num(saved.removedLessonCount)} ders kaldırıldı.`);
  if (saved?.updatedSessionCount) bits.push(`${num(saved.updatedSessionCount)} dersin saati güncellendi.`);
  return bits.join(' ');
}
