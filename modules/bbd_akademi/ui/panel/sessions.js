// BBD Akademi — Ders oturumları (KM-3; Akademi F-Y2/F-Y3/F-Y22).
//
// Program ve etütlerden Akademi'nin ürettiği TEK TEK DERSLER (`ClassSession`, önümüzdeki 14 gün
// gece işiyle; program/takvim/etüt değişince yeniden). Program satırını değiştirmeden tek dersin:
//   · yerine öğretmen, derslik, saat kaydırma, zil çağrısı (`admin_patchClassSession`),
//   · gerekçeli iptali (`admin_cancelClassSession`) ve geri alınması (`admin_restoreClassSession`),
//   · iptal edilen derse telafi (`admin_createMakeupSession`)
// buradan yapılır. Üç sekme:
//   · Oturum takvimi — süzgeçli, imleçli liste (`admin_listClassSessions`; aralık en çok 31 gün),
//   · Öğretmen haftası — bir öğretmenin haftası ve ders yükü (`admin_getTeacherWeek`),
//   · Aylık ders dökümü (puantaj) — öğretmen × ay programdaki / gerçekleşen / iptal / telafi / yerine
//     girdiği ders ve etüt (`admin_getTeacherMonthlyLoad`, Akademi README §14 madde 79; yalnız
//     personel yönetimi izni) + Excel uyumlu CSV: özet ve ders ders döküm (`admin_listClassSessions`).
//     Gerçekleşen = yoklaması alınmış, iptal / tatil olmayan ders; dersi VEREN öğretmene yazılır.
//
// ZİL: iptal sınıf çağrısını zil planından düşürür, saat kaydırma ve telafi kendi saatinde çağrı
// üretir — planı Akademi yeniden üretir, KM Zil Sistemi her zamanki eşitlemesiyle alır (Ders
// programı → "Zil planı durumu"). Bu ekran zil modülüne yazmaz; yalnız etkisini söyler.
//
// ÇOKLU SINIF (sınıf = grup, README §14 madde 70): oturumun bütün sınıfları `groups`'tadır
// ("8-A + 8-B"); eski Akademi yalnız `group` verir.
//
// Derin bağlantılar: `{classSessionId}` oturum çekmecesini, `{tab: 'calendar'|'teacher'|'load'}` sekmeyi,
// `{staffId}` öğretmen haftasını, `{tab: 'load', month: 'YYYY-MM'}` o ayın dökümünü açar.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow, tabBar } from '../../ui-kit/layout.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { dataTable } from '../../ui-kit/table.js';
import { saveBlob } from './api.js';
import {
  DAY_LABEL, actionButton, addDays, clockInput, confirmAction, dateInput, empty, fmtDate, fmtDateTime, fmtTime,
  formDialog, istanbulStamp, kv, loader, num, openDrawer, optionsOf, pagedList, personName, problem, runAction,
  sectionHead, selectBox, statusBadge, todayIst, toolbar, weekStart,
} from './common.js';
import { dayKeyOf } from './timetable-shared.js';

export const SESSION_KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik ders', makeup: 'Telafi dersi' };
export const SESSION_STATUS = { scheduled: ['Planlı', 'good'], cancelled: ['İptal', 'bad'], holiday: ['Tatil', 'dim'],
  orphaned: ['Programdan kalktı', 'warn'] };
const TAKEN = { '': 'Yoklama: tümü', true: 'Yoklaması alınan', false: 'Yoklaması alınmayan' };
const TABS = [{ key: 'calendar', label: 'Oturum takvimi' }, { key: 'teacher', label: 'Öğretmen haftası' },
  { key: 'load', label: 'Aylık ders dökümü' }];
const BELL_NOTE = 'Zil: iptal sınıf çağrısını zil planından düşürür; saat değişikliği ve telafi kendi saatinde çağrı '
  + 'üretir. Planı Akademi yeniden üretir, KM Zil Sistemi bir sonraki eşitlemede alır.';
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Oturumun sınıf adları: "8-A + 8-B" (eski Akademi: tek `group`). */
export function sessionGroups(session) {
  const list = (session?.groups || []).map((group) => group?.name).filter(Boolean);
  if (list.length) return list.join(' + ');
  return session?.group?.name || '';
}

export function sessionTitle(session) {
  return session?.subject?.name || SESSION_KIND[session?.kind] || 'Ders';
}

function clockOf(stamp) {
  return fmtTime(stamp);
}

function minutesText(minutes) {
  const value = Number(minutes || 0);
  const hours = Math.floor(value / 60);
  const rest = value % 60;
  return hours ? `${num(hours)} sa${rest ? ` ${num(rest)} dk` : ''}` : `${num(rest)} dk`;
}

function teacherCell(session) {
  const node = h('div', 'ak-cell-stack');
  if (!session.effectiveTeacher) {
    node.append(badge('Öğretmen atanmadı', 'warn'));
    return node;
  }
  node.append(h('span', '', personName(session.effectiveTeacher)));
  if (session.isSubstitute) node.append(h('small', '', `yerine · programda ${personName(session.scheduledTeacher)}`));
  return node;
}

function attendanceCell(session) {
  const attendance = session.attendance || {};
  if (['cancelled', 'holiday'].includes(session.status)) return '—';
  if (!attendance.taken) return badge('Alınmadı', Date.parse(session.startsAt) <= Date.now() ? 'warn' : 'dim');
  const counts = attendance.counts || {};
  return `${num((counts.present || 0) + (counts.late || 0))}/${num(counts.enrolled || 0)} geldi`;
}

/** Çakışma ayrıntısı → okunur cümle (gün/zil dilimi yerine oturumun kendi saati bilinir). */
function conflictMessage(failure) {
  if (failure?.code !== 'timetable_conflict' || !Array.isArray(failure.details) || !failure.details.length) {
    return problem(failure);
  }
  const lines = failure.details.map((item) => (item.kind === 'room'
    ? `${item.room || 'Derslik'} aynı saatte ${item.conflictingClassGroup?.name || 'başka bir'} sınıfında kullanılıyor.`
    : `${item.teacher?.displayName || 'Öğretmen'} aynı saatte ${item.conflictingClassGroup?.name || 'başka bir'} sınıfında derste.`));
  return `Çakışma var, kaydedilmedi. ${lines.join(' ')}`;
}

// ================================================================= bölüm

export function section(app) {
  loadStyles(import.meta.url, 'timetable.css');
  const node = h('div', 'ak-stack ak-ses');
  node.append(sectionHead('Ders oturumları', {
    subtitle: 'Tek tek dersler: yerine öğretmen, derslik ve saat değişikliği, iptal, telafi, öğretmen haftası ve aylık '
      + 'ders dökümü (puantaj). Haftalık program değişmez.' }));
  const bodies = {};
  const builders = {
    calendar: () => calendarTab(app),
    teacher: () => teacherTab(app),
    load: () => loadTab(app, { openSessions: (filter) => go('calendar').apply(filter) }),
  };
  const tabs = tabBar(TABS, 'calendar', (key) => show(key));
  const slot = h('div', 'ak-stack');
  node.append(tabs.node, slot);

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    return bodies[key];
  }
  function go(key) {
    tabs.select(key, false);
    return show(key);
  }

  return {
    node,
    shown(payload) {
      if (payload?.staffId) go('teacher').open(payload.staffId, payload.week);
      else if (payload?.tab === 'load' && payload.month) go('load').open(payload.month);
      else if (payload?.tab && builders[payload.tab]) go(payload.tab);
      else if (!bodies[tabs.active]) go(tabs.active);
      if (payload?.classSessionId) {
        openClassSession(app, payload.classSessionId, { onChange: () => bodies.calendar?.reload() });
      }
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// --------------------------------------------------------- oturum takvimi

function calendarTab(app) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_listClassSessions')) {
    node.append(alertBox('Ders oturumlarını görmek için yetkiniz yok.', 'info'));
    return { node, reload() {}, apply() {}, destroy() {} };
  }
  const start = weekStart();
  const filters = { from: start, to: addDays(start, 6), groupId: '', teacherId: '', status: '', kind: '', taken: '' };
  const warn = h('div');
  const range = dateRange({ start: filters.from, end: filters.to, label: 'Aralık', presets: [], onChange: (value) => {
    filters.from = value.start;
    filters.to = value.end;
    reload();
  } });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => { filters.groupId = value; reload(); },
    { label: 'Sınıf' });
  const teacherSelect = selectBox([{ value: '', label: 'Tüm öğretmenler' }], '', (value) => { filters.teacherId = value; reload(); },
    { label: 'Öğretmen' });
  const statusSelect = selectBox(optionsOf(SESSION_STATUS, { empty: 'Tüm durumlar' }), '', (value) => {
    filters.status = value; reload();
  }, { label: 'Durum' });
  const kindSelect = selectBox(optionsOf(SESSION_KIND, { empty: 'Tüm türler' }), '', (value) => { filters.kind = value; reload(); },
    { label: 'Tür' });
  const takenSelect = selectBox(optionsOf(TAKEN), '', (value) => { filters.taken = value; reload(); }, { label: 'Yoklama' });
  const thisWeek = button('Bu hafta', { variant: 'ghost', onClick: () => {
    filters.from = weekStart();
    filters.to = addDays(filters.from, 6);
    range.set({ start: filters.from, end: filters.to });
    reload();
  } });
  node.append(toolbar(range.node, thisWeek), toolbar(classSelect.node, teacherSelect.node, statusSelect.node,
    kindSelect.node, takenSelect.node, h('span', 'kit-spacer'), button('Yenile', { variant: 'ghost', onClick: () => reload() })),
  warn, alertBox(BELL_NOTE, 'info'));

  const list = pagedList({
    app,
    path: '/v1/admin/sessions',
    limit: 50,
    query: () => ({ from: filters.from, to: filters.to, groupId: filters.groupId || undefined,
      teacherId: filters.teacherId || undefined, status: filters.status || undefined, kind: filters.kind || undefined,
      attendanceTaken: filters.taken === '' ? undefined : filters.taken === 'true' }),
    columns: [
      { key: 'when', label: 'Gün ve saat', width: '150px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', `${fmtDate(row.date)} ${(DAY_LABEL[dayKeyOf(row.date)] || '').slice(0, 3)}`),
          h('small', '', `${clockOf(row.startsAt)}–${clockOf(row.endsAt)}`));
        return cell;
      } },
      { key: 'subject', label: 'Ders', width: 'minmax(0, 1.4fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(h('span', '', sessionTitle(row)));
        if (row.kind !== 'lesson') cell.append(badge(SESSION_KIND[row.kind] || row.kind, 'info'));
        return cell;
      } },
      { key: 'groups', label: 'Sınıf', width: 'minmax(0, 1.2fr)', cell: (row) => sessionGroups(row) || '—' },
      { key: 'teacher', label: 'Öğretmen', width: 'minmax(0, 1.3fr)', cell: (row) => teacherCell(row) },
      { key: 'status', label: 'Durum', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(statusBadge(SESSION_STATUS, row.status));
        if (row.statusReason) cell.append(h('small', '', row.statusReason));
        else if (row.makeupSessionId) cell.append(h('small', '', 'telafisi var'));
        return cell;
      } },
      { key: 'attendance', label: 'Yoklama', cell: (row) => attendanceCell(row) },
    ],
    onRow: (row) => openClassSession(app, row.id, { initial: row, onChange: (next) => {
      if (next) list.replace((item) => item.id === next.id, next);
      else reload();
    } }),
    empty: { title: 'Bu süzgece uyan ders yok', text: 'Aralığı ya da süzgeçleri değiştirin. Oturumlar önümüzdeki 14 gün '
      + 'için üretilir.' },
  });
  node.append(list.node);

  Promise.all([app.lookups.options('classGroups', { empty: 'Tüm sınıflar' }).catch(() => null),
    app.lookups.teachers().catch(() => null)]).then(([classes, teachers]) => {
    if (classes) classSelect.options(classes);
    if (teachers) {
      teacherSelect.options([{ value: '', label: 'Tüm öğretmenler' },
        ...teachers.map((item) => ({ value: item.id, label: item.displayName || item.id }))]);
      teacherSelect.set(filters.teacherId);
    }
  });

  function reload() {
    const message = !filters.from || !filters.to ? 'Başlangıç ve bitiş seçin.'
      : filters.to < filters.from ? 'Bitiş başlangıçtan önce olamaz.'
        : addDays(filters.from, 31) <= filters.to ? 'Aralık en çok 31 gün olabilir.' : '';
    warn.replaceChildren(message ? alertBox(message, 'warn') : '');
    if (!message) list.reload();
  }
  /** Dışarıdan süzgeç (aylık dökümden "bu öğretmenin dersleri"): aralık + öğretmen, diğerleri sıfırlanır. */
  function apply({ from, to, teacherId = '' }) {
    Object.assign(filters, { from, to, teacherId, groupId: '', status: '', kind: '', taken: '' });
    range.set({ start: from, end: to });
    teacherSelect.set(teacherId);
    for (const control of [classSelect, statusSelect, kindSelect, takenSelect]) control.set('');
    reload();
  }
  return { node, reload, apply, destroy() { list.destroy(); range.destroy(); } };
}

// -------------------------------------------------------- öğretmen haftası

function teacherTab(app) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_getTeacherWeek')) {
    node.append(alertBox('Öğretmen haftasını görmek için yetkiniz yok.', 'info'));
    return { node, reload() {}, open() {}, destroy() {} };
  }
  const state = { staffId: '', week: weekStart() };
  const teacherSelect = selectBox([{ value: '', label: 'Öğretmen seçin' }], '', (value) => { state.staffId = value; view.reload(); },
    { label: 'Öğretmen' });
  const weekField = dateInput(state.week, (iso) => { if (iso) { state.week = weekStart(iso); weekField.set(state.week); view.reload(); } },
    'Hafta');
  node.append(toolbar(teacherSelect.node,
    button('‹', { variant: 'ghost', title: 'Önceki hafta', onClick: () => shift(-7) }), weekField.node,
    button('›', { variant: 'ghost', title: 'Sonraki hafta', onClick: () => shift(7) }),
    button('Bu hafta', { variant: 'ghost', onClick: () => { state.week = weekStart(); weekField.set(state.week); view.reload(); } })));
  const view = loader(async () => {
    if (!state.staffId) return null;
    return app.api.get(`/v1/admin/staff/${state.staffId}/week`, { week: state.week });
  }, (data) => {
    if (!data) return empty('Öğretmen seçin', 'Seçilen öğretmenin haftası ve ders yükü burada görünür.');
    const sessions = [...(data.sessions || [])].sort((a, b) => String(a.startsAt).localeCompare(String(b.startsAt)));
    const days = new Map();
    for (const session of sessions) {
      if (!days.has(session.date)) days.set(session.date, []);
      days.get(session.date).push(session);
    }
    const blocks = [];
    for (const [date, items] of days) {
      const list = h('div', 'ak-stack ak-ses-day');
      for (const session of items) list.append(sessionRow(session));
      blocks.push(card(`${DAY_LABEL[dayKeyOf(date)] || ''} ${fmtDate(date)}`, list));
    }
    return [kpiRow([
      { label: 'Ders', value: num(data.sessionCount) },
      { label: 'Ders süresi', value: minutesText(data.lessonMinutes) },
      { label: 'Yerine girdiği', value: num(data.substituteCount), tone: data.substituteCount ? 'info' : '' },
    ]), h('small', 'ak-muted', `${personName(data.teacher)} · ${fmtDate(data.weekStart)} haftası`),
    ...(blocks.length ? blocks : [empty('Bu hafta ders yok')])];
  }, { rows: 6, columns: 4 });
  node.append(view.node);

  function sessionRow(session) {
    const row = h('button', `ak-ses-row ak-ses-is-${session.status}`);
    row.type = 'button';
    const main = h('div', 'ak-cell-stack');
    main.append(h('b', '', `${clockOf(session.startsAt)}–${clockOf(session.endsAt)} · ${sessionTitle(session)}`),
      h('small', '', [sessionGroups(session), session.room, session.isSubstitute ? 'yerine ders' : null]
        .filter(Boolean).join(' · ')));
    row.append(main, statusBadge(SESSION_STATUS, session.status));
    row.addEventListener('click', () => openClassSession(app, session.id, { initial: session, onChange: () => view.reload() }));
    return row;
  }

  function shift(days) {
    state.week = addDays(state.week, days);
    weekField.set(state.week);
    view.reload();
  }

  app.lookups.teachers().then((teachers) => teacherSelect.options([{ value: '', label: 'Öğretmen seçin' },
    ...teachers.map((item) => ({ value: item.id, label: item.displayName || item.id }))])).catch(() => {});

  return {
    node,
    reload: () => view.reload(),
    open(staffId, week) {
      state.staffId = staffId;
      teacherSelect.set(staffId);
      if (week) { state.week = weekStart(week); weekField.set(state.week); }
      view.reload();
    },
    destroy() { weekField.destroy(); },
  };
}

// ------------------------------------------------- aylık ders dökümü (puantaj)

const MONTH_NAMES = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim',
  'Kasım', 'Aralık'];
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
export const LOAD_RULE = 'Gerçekleşen ders: yoklaması alınmış, iptal ya da tatil olmayan ders. Yerine girilen ders '
  + 'dersi verene yazılır; programdaki öğretmende “Yerine başkası girdi” olarak görünür. Yoklaması alınmamış geçmiş '
  + 'ders sayılmaz; yoklama tamamlanınca kendiliğinden girer.';

/** Ders oturumunun puantaj karşılığı (Akademi kuralıyla aynı; README §14 madde 79). */
export const LOAD_STATE = {
  delivered: 'Gerçekleşti', pending: 'Yoklama bekliyor', upcoming: 'Gelecek', cancelled: 'İptal', holiday: 'Tatil',
  orphaned: 'Programdan kalktı', unassigned: 'Öğretmen atanmadı',
};

export function thisMonth() {
  return todayIst().slice(0, 7);
}

export function shiftMonth(month, delta) {
  const [year, value] = month.split('-').map(Number);
  const index = year * 12 + (value - 1) + delta;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
}

export function monthLabel(month) {
  const [year, value] = String(month || '').split('-').map(Number);
  return year && value ? `${MONTH_NAMES[value - 1]} ${year}` : String(month || '');
}

export function monthRange(month) {
  const [year, value] = month.split('-').map(Number);
  const last = new Date(Date.UTC(year, value, 0)).getUTCDate();
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, '0')}` };
}

/** "12 sa 40 dk" yerine CSV için ondalık saat: 760 dk → "12,67". */
function hoursDecimal(minutes) {
  return (Math.round((Number(minutes || 0) / 60) * 100) / 100).toFixed(2).replace('.', ',');
}

/** Excel (Türkçe) uyumlu CSV: UTF-8 BOM, `;` ayraç, CRLF; formül gibi başlayan metin `'` ile etkisizleşir. */
export function csvText(header, rows) {
  const cell = (value) => {
    if (value === null || value === undefined) return '';
    let text = String(value);
    if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return /[";\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return `\uFEFF${[header, ...rows].map((row) => row.map(cell).join(';')).join('\r\n')}\r\n`;
}

const LOAD_COLUMNS = [
  ['plannedCount', 'Programdaki'], ['deliveredCount', 'Gerçekleşen'], ['lessonCount', 'Ders'],
  ['makeupCount', 'Telafi'], ['etutCount', 'Etüt'], ['substituteCount', 'Yerine girdiği'],
  ['cancelledCount', 'İptal'], ['coveredByOthersCount', 'Yerine başkası girdi'], ['pendingCount', 'Yoklama bekleyen'],
  ['upcomingCount', 'Gelecek'],
];

/** Özet CSV (öğretmen başına bir satır + TOPLAM). */
export function loadCsv(data) {
  const header = ['Ay', 'Öğretmen', ...LOAD_COLUMNS.map(([, label]) => label), 'Gerçekleşen süre (dk)',
    'Gerçekleşen süre (saat)', 'Yerine girdiği süre (dk)'];
  const line = (name, counts) => [data.month, name, ...LOAD_COLUMNS.map(([key]) => Number(counts?.[key] || 0)),
    Number(counts?.deliveredMinutes || 0), hoursDecimal(counts?.deliveredMinutes), Number(counts?.substituteMinutes || 0)];
  return csvText(header, [...(data.rows || []).map((row) => line(personName(row.teacher), row)),
    line('TOPLAM', data.totals || {})]);
}

/** Bir oturumun puantaj durumu: kime yazılır ve neden. `now` ms. */
export function loadState(session, now = Date.now()) {
  if (session.status === 'holiday') return 'holiday';
  if (session.status === 'cancelled') return 'cancelled';
  const taken = Boolean(session.attendance?.taken);
  if (session.status === 'orphaned' && !taken) return 'orphaned';
  if (!session.effectiveTeacher) return 'unassigned';
  if (taken) return 'delivered';
  return Date.parse(session.startsAt) <= now ? 'pending' : 'upcoming';
}

/** Ders ders döküm CSV'si (`admin_listClassSessions` öğeleri). */
export function sessionsCsv(items, now = Date.now()) {
  const header = ['Tarih', 'Gün', 'Başlangıç', 'Bitiş', 'Süre (dk)', 'Tür', 'Ders', 'Sınıf', 'Programdaki öğretmen',
    'Dersi veren', 'Yerine', 'Durum', 'Yoklama', 'Puantaj', 'Puantaja yazılan', 'Gerekçe / not'];
  const rows = [...items].sort((a, b) => String(a.startsAt).localeCompare(String(b.startsAt))).map((session) => {
    const state = loadState(session, now);
    const minutes = Math.max(0, Math.round((Date.parse(session.endsAt) - Date.parse(session.startsAt)) / 60000));
    const counts = session.attendance?.counts || {};
    return [session.date, DAY_LABEL[dayKeyOf(session.date)] || '', clockOf(session.startsAt), clockOf(session.endsAt),
      minutes, SESSION_KIND[session.kind] || session.kind, sessionTitle(session), sessionGroups(session),
      session.scheduledTeacher ? personName(session.scheduledTeacher) : '',
      session.effectiveTeacher ? personName(session.effectiveTeacher) : '', session.isSubstitute ? 'Evet' : '',
      (SESSION_STATUS[session.status] || [session.status])[0],
      session.attendance?.taken ? `${(counts.present || 0) + (counts.late || 0)}/${counts.enrolled || 0} geldi` : 'Alınmadı',
      LOAD_STATE[state], state === 'delivered' ? personName(session.effectiveTeacher) : '',
      session.statusReason || session.note || ''];
  });
  return csvText(header, rows);
}

function saveCsv(name, text) {
  saveBlob({ filename: name, blob: new Blob([text], { type: 'text/csv;charset=utf-8' }) });
}

function loadTab(app, { openSessions } = {}) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_getTeacherMonthlyLoad')) {
    node.append(alertBox('Aylık ders dökümü ücret verisi taşıdığı için yalnız personel yönetimi yetkisi olanlara '
      + 'açıktır.', 'info'));
    return { node, reload() {}, open() {}, destroy() {} };
  }
  const state = { month: thisMonth(), data: null };
  const label = h('b', 'ak-ses-month', monthLabel(state.month));
  label.setAttribute('aria-live', 'polite');
  const csvButton = button('CSV indir', { variant: 'ghost', title: 'Öğretmen başına özet (Excel uyumlu)',
    onClick: () => downloadSummary() });
  const detailButton = button('Ders ders döküm (CSV)', { variant: 'ghost',
    title: 'Ayın bütün dersleri: kim verdi, yoklama, puantaja yazılıp yazılmadığı', onClick: () => downloadDetail() });
  node.append(toolbar(
    button('‹', { variant: 'ghost', title: 'Önceki ay', onClick: () => go(shiftMonth(state.month, -1)) }), label,
    button('›', { variant: 'ghost', title: 'Sonraki ay', onClick: () => go(shiftMonth(state.month, 1)) }),
    button('Bu ay', { variant: 'ghost', onClick: () => go(thisMonth()) }),
    h('span', 'kit-spacer'), csvButton, detailButton,
  ), alertBox(LOAD_RULE, 'info'));

  const table = dataTable({
    columns: [
      { key: 'teacher', label: 'Öğretmen', width: 'minmax(0, 1.6fr)', cell: (row) => (row.total
        ? h('b', '', 'Toplam') : personName(row.teacher)) },
      ...LOAD_COLUMNS.map(([key, title]) => ({ key, label: title, align: 'num', cell: (row) => {
        const value = num(row[key] || 0);
        if (key === 'pendingCount' && row[key]) return badge(value, 'warn');
        return row.total ? h('b', '', value) : value;
      } })),
      { key: 'deliveredMinutes', label: 'Süre', width: '110px', align: 'num',
        cell: (row) => (row.total ? h('b', '', minutesText(row.deliveredMinutes)) : minutesText(row.deliveredMinutes)) },
    ],
    rows: [],
    rowKey: (row) => (row.total ? 'toplam' : String(row.teacher?.id || '')),
    onRow: (row) => {
      if (row.total || !openSessions) return;
      openSessions({ ...monthRange(state.month), teacherId: row.teacher.id });
    },
    empty: empty('Bu ayda ders yok', 'Seçilen ayda öğretmene yazılmış ya da planlanmış ders bulunmuyor.'),
  });

  const view = loader(() => app.api.get('/v1/admin/teacher-load', { month: state.month }), (data) => {
    state.data = data;
    const totals = data.totals || {};
    const rows = data.rows || [];
    table.update({ rows: rows.length ? [...rows, { ...totals, total: true }] : [] });
    const notes = [];
    if (totals.pendingCount) {
      notes.push(alertBox(`${num(totals.pendingCount)} dersin saati geçti ama yoklaması alınmadı; yoklama alınana kadar `
        + 'puantaja girmez.', 'warn'));
    }
    if (data.unassignedCount) {
      notes.push(alertBox(`${num(data.unassignedCount)} derste öğretmen atanmamış; bu dersler kimseye yazılmaz.`, 'warn'));
    }
    return [kpiRow([
      { label: 'Gerçekleşen ders', value: num(totals.deliveredCount || 0) },
      { label: 'Ders süresi', value: minutesText(totals.deliveredMinutes) },
      { label: 'Yerine girilen', value: num(totals.substituteCount || 0), tone: totals.substituteCount ? 'info' : '' },
      { label: 'İptal', value: num(totals.cancelledCount || 0) },
      { label: 'Yoklama bekleyen', value: num(totals.pendingCount || 0), tone: totals.pendingCount ? 'warn' : '' },
    ]), ...notes, h('small', 'ak-muted', `${monthLabel(data.month)} · ${fmtDate(data.from)} – ${fmtDate(data.to)} · `
      + `hesap ${fmtDateTime(data.asOf)}. Satıra tıklayınca öğretmenin o ayki dersleri açılır.`), table.node];
  }, { rows: 6, columns: 8 });
  node.append(view.node);

  function go(month) {
    if (!MONTH_RE.test(String(month || ''))) return;
    state.month = month;
    state.data = null;
    label.textContent = monthLabel(month);
    view.reload();
  }

  function downloadSummary() {
    if (!state.data || state.data.month !== state.month) {
      app.toast('Döküm henüz yüklenmedi.', 'warn');
      return;
    }
    saveCsv(`ogretmen-ders-dokumu-${state.month}.csv`, loadCsv(state.data));
  }

  async function downloadDetail() {
    const month = state.month;
    const { from, to } = monthRange(month);
    detailButton.disabled = true;
    try {
      const items = await app.api.all('/v1/admin/sessions', { from, to }, { limit: 100, max: 20000 });
      saveCsv(`ders-ders-dokum-${month}.csv`, sessionsCsv(items));
      app.toast(`${num(items.length)} ders indirildi.`, 'good');
    } catch (failure) {
      app.toast(problem(failure, 'Ders listesi alınamadı.'), 'bad');
    } finally {
      detailButton.disabled = false;
    }
  }

  return { node, reload: () => view.reload(), open: (month) => go(month), destroy() {} };
}

// ---------------------------------------------------------- oturum çekmecesi

/**
 * Tek oturum: ayrıntı + değiştir / iptal / telafi / geri al. `onChange(session|null)` her
 * yazmadan sonra çağrılır (liste yerinde güncellenir; telafide yeni oturum açıldığı için null).
 */
export function openClassSession(app, classSessionId, { initial = null, onChange } = {}) {
  const panel = openDrawer(app, { title: initial ? sessionTitle(initial) : 'Ders oturumu', subtitle: 'Yükleniyor…' });
  const subtitle = panel.node.querySelector('.kit-drawer-sub');
  let current = null;
  const view = loader(() => app.api.get(`/v1/admin/sessions/${classSessionId}`), (session) => render(session),
    { rows: 6, columns: 2 });
  panel.body.append(view.node);
  view.reload();

  function render(session) {
    current = session;
    panel.setTitle(`${sessionTitle(session)}${sessionGroups(session) ? ` · ${sessionGroups(session)}` : ''}`);
    if (subtitle) subtitle.textContent = `${fmtDate(session.date)} ${DAY_LABEL[dayKeyOf(session.date)] || ''} · `
      + `${clockOf(session.startsAt)}–${clockOf(session.endsAt)}`;
    const attendance = session.attendance || {};
    const counts = attendance.counts || {};
    const nodes = [];
    if (session.status === 'orphaned') {
      nodes.push(alertBox('Bu dersin program satırı ya da sınıfı değişti; ders artık kaynağına bağlı değil. Yoklaması '
        + 'korunur.', 'warn'));
    }
    if (session.status === 'holiday') {
      nodes.push(alertBox(`Kurum takvimi tatili${session.statusReason ? `: ${session.statusReason}` : ''}. Yoklama beklenmez, `
        + 'zil çalmaz.', 'info'));
    }
    if (!session.effectiveTeacher) nodes.push(alertBox('Bu derse öğretmen atanmadı.', 'warn'));
    const tools = toolbar(...actions(session));
    nodes.push(tools, kv([
      ['Tür', SESSION_KIND[session.kind] || session.kind],
      ['Ders', session.subject?.name],
      session.topic ? ['Konu', session.topic.name] : null,
      ['Sınıf', sessionGroups(session) || 'Sınıfsız etüt'],
      ['Öğrenci', num(session.studentCount)],
      ['Programdaki öğretmen', session.scheduledTeacher ? personName(session.scheduledTeacher) : 'Atanmadı'],
      ['Dersi veren', session.effectiveTeacher ? `${personName(session.effectiveTeacher)}${session.isSubstitute
        ? ' (yerine)' : ''}` : 'Atanmadı'],
      ['Derslik', session.room],
      ['Durum', statusBadge(SESSION_STATUS, session.status)],
      session.statusReason ? ['Gerekçe', session.statusReason] : null,
      ['Zil çağrısı', session.announce ? 'Açık' : 'Kapalı'],
      ['Yoklama', attendance.taken ? `${personName(attendance.takenBy)} · ${fmtDateTime(attendance.takenAt)} — `
        + `${num((counts.present || 0) + (counts.late || 0))}/${num(counts.enrolled || 0)} geldi` : 'Alınmadı'],
      attendance.locked ? ['Düzeltme', 'Öğretmenin penceresi kapandı; yalnız gerekçeli düzeltme'] : null,
      ['Son değişiklik', session.updatedAt ? `${fmtDateTime(session.updatedAt)} · sürüm ${num(session.version)}` : '—'],
    ]), h('small', 'ak-muted', BELL_NOTE));
    return nodes;
  }

  function actions(session) {
    const list = [];
    const openAttendance = button('Yoklamayı aç', { variant: 'ghost', onClick: () => {
      panel.close();
      app.go('attendance', { attendanceSessionId: session.occurrenceKey || session.id, date: session.date });
    } });
    if (session.status === 'scheduled') {
      list.push(actionButton(app, 'admin_patchClassSession', 'Değiştir', { variant: 'primary', onClick: () => edit(session) }));
      list.push(actionButton(app, 'admin_cancelClassSession', 'Dersi iptal et', { onClick: () => cancel(session) }));
    }
    if (session.status === 'cancelled') {
      if (!session.makeupSessionId) {
        list.push(actionButton(app, 'admin_createMakeupSession', 'Telafi ver', { variant: 'primary', onClick: () => makeup(session) }));
      }
      list.push(actionButton(app, 'admin_restoreClassSession', 'İptali geri al', { onClick: () => restore(session) }));
    }
    if (session.makeupSessionId) {
      list.push(button('Telafi dersini aç', { variant: 'ghost', onClick: () => {
        panel.close();
        openClassSession(app, session.makeupSessionId, { onChange });
      } }));
    }
    if (session.makeupOfSessionId) {
      list.push(button('İptal edilen dersi aç', { variant: 'ghost', onClick: () => {
        panel.close();
        openClassSession(app, session.makeupOfSessionId, { onChange });
      } }));
    }
    if (!['cancelled', 'holiday'].includes(session.status)) list.push(openAttendance);
    return list;
  }

  function changed(next, message) {
    if (!next) return;
    app.toast(message, 'good');
    view.reload();
    onChange?.(next);
  }

  async function edit(session) {
    const teachers = await app.lookups.teachers().catch(() => []);
    const taken = Boolean(session.attendance?.taken);
    const scheduled = session.scheduledTeacher;
    const teacherOptions = [{ value: '', label: scheduled ? `Programdaki öğretmen (${personName(scheduled)})`
      : 'Programdaki öğretmen (atanmadı)' },
    ...teachers.filter((item) => item.id !== scheduled?.id)
      .map((item) => ({ value: item.id, label: item.displayName || item.id }))];
    const currentSub = session.isSubstitute ? session.effectiveTeacher?.id || '' : '';
    if (currentSub && !teacherOptions.some((item) => item.value === currentSub)) {
      teacherOptions.push({ value: currentSub, label: personName(session.effectiveTeacher) });
    }
    const value = { substituteTeacherId: currentSub, room: session.room || '', start: clockOf(session.startsAt),
      end: clockOf(session.endsAt), announce: Boolean(session.announce), note: '', notifyStudents: false };
    const clockField = (key, label) => ({ key, label, type: 'custom', required: true,
      render: (draft, set) => {
        const control = clockInput(draft[key], (next) => set(next), label);
        control.node.disabled = taken;
        return control.node;
      },
      validate: (raw) => (CLOCK.test(String(raw || '')) ? null : 'Saati 09:30 gibi yazın.') });
    const saved = await formDialog(app, {
      title: 'Bu dersi değiştir',
      description: taken
        ? 'Yoklaması alınmış derste yalnız not ve zil çağrısı değişir. Öğretmen ya da saat için önce yoklamayı silin.'
        : 'Yalnız bu ders değişir; haftalık program aynı kalır. Öğretmen(ler)e bildirim gider.',
      value,
      fields: [
        taken ? null : { key: 'substituteTeacherId', label: 'Dersi veren (yerine öğretmen)', type: 'select', options: teacherOptions },
        taken ? null : { key: 'room', label: 'Derslik', maxLength: 40 },
        taken ? null : clockField('start', 'Başlangıç saati'),
        taken ? null : clockField('end', 'Bitiş saati'),
        { key: 'announce', label: 'Zil planında sınıf çağrısı', type: 'checkbox' },
        { key: 'note', label: 'Not (isteğe bağlı)', type: 'textarea', maxLength: 300, wide: true },
        taken ? null : { key: 'notifyStudents', label: 'Öğrenci ve veliye bildir', type: 'checkbox' },
      ],
      onSubmit: (draft) => {
        const body = {};
        if (!taken) {
          if ((draft.substituteTeacherId || '') !== currentSub) body.substituteTeacherId = draft.substituteTeacherId || null;
          const room = String(draft.room || '').trim();
          if (room !== (session.room || '')) body.room = room || null;
          if (draft.start !== value.start || draft.end !== value.end) {
            if (draft.end <= draft.start) throw Object.assign(new Error('Bitiş saati başlangıçtan sonra olmalı.'), { code: 'validation_error' });
            body.startsAt = istanbulStamp(session.date, draft.start);
            body.endsAt = istanbulStamp(session.date, draft.end);
          }
          if (draft.notifyStudents) body.notifyStudents = true;
        }
        if (Boolean(draft.announce) !== value.announce) body.announce = Boolean(draft.announce);
        const note = String(draft.note || '').trim();
        if (note) body.note = note;
        if (!Object.keys(body).length) throw Object.assign(new Error('Değişiklik yok.'), { code: 'validation_error' });
        if (Number.isInteger(session.version)) body.expectedVersion = session.version;
        return app.api.patch(`/v1/admin/sessions/${session.id}`, body).catch((failure) => {
          if (failure?.code === 'version_conflict') {
            throw Object.assign(new Error('Ders bu arada başka biri tarafından değiştirildi; pencereyi kapatıp yeniden açın.'),
              { code: failure.code });
          }
          if (failure?.code === 'has_attendance') {
            throw Object.assign(new Error('Bu dersin yoklaması alınmış; yalnız not ve zil çağrısı değişebilir.'), { code: failure.code });
          }
          throw Object.assign(new Error(conflictMessage(failure)), { code: failure?.code, details: failure?.details });
        });
      },
    });
    changed(saved, 'Ders güncellendi.');
  }

  async function cancel(session) {
    if (session.attendance?.taken) {
      const ok = await confirmAction(app, { title: 'Yoklaması alınmış ders', confirmLabel: 'Yoklamayı aç',
        description: 'Yoklaması alınmış ders iptal edilemez. Yanlış derse alındıysa önce yoklamayı gerekçeyle silin '
          + '(Yoklama → listede “Yoklamayı sil”), sonra iptal edin.' });
      if (ok) {
        panel.close();
        app.go('attendance', { attendanceSessionId: session.occurrenceKey || session.id, date: session.date });
      }
      return;
    }
    const saved = await formDialog(app, {
      title: 'Dersi iptal et',
      description: `${sessionTitle(session)} · ${sessionGroups(session)} · ${fmtDate(session.date)} ${clockOf(session.startsAt)}. `
        + 'Öğretmene bildirim gider; sınıf çağrısı zil planından düşer. Sonra telafi verebilirsiniz.',
      value: { reason: '', notifyStudents: true },
      danger: true,
      fields: [
        { key: 'reason', label: 'Gerekçe', type: 'textarea', maxLength: 300, required: true, wide: true,
          validate: (raw) => (String(raw || '').trim().length < 3 ? 'En az 3 karakter yazın.' : null) },
        { key: 'notifyStudents', label: 'Öğrenci ve veliye ders iptali bildir', type: 'checkbox', wide: true },
      ],
      submitLabel: 'Dersi iptal et',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/sessions/${session.id}/cancel`,
        { reason: String(draft.reason).trim(), notifyStudents: Boolean(draft.notifyStudents) }, { key }),
    });
    changed(saved, 'Ders iptal edildi.');
  }

  async function makeup(session) {
    const teachers = await app.lookups.teachers().catch(() => []);
    const options = [{ value: '', label: `İptal edilen dersin öğretmeni (${personName(session.effectiveTeacher || session.scheduledTeacher)})` },
      ...teachers.map((item) => ({ value: item.id, label: item.displayName || item.id }))];
    const clock = (key, label) => ({ key, label, type: 'custom', required: true,
      render: (draft, set) => clockInput(draft[key], (next) => set(next), label).node,
      validate: (raw) => (CLOCK.test(String(raw || '')) ? null : 'Saati 15:30 gibi yazın.') });
    const saved = await formDialog(app, {
      title: 'Telafi dersi ver',
      description: 'Telafi iptal edilen derse bağlanır (tek telafi) ve kendi gün ve saatinde zil çağrısı üretir.',
      value: { date: addDays(todayIst(), 1), start: clockOf(session.startsAt), end: clockOf(session.endsAt), teacherId: '',
        room: session.room || '', note: '', notifyStudents: true },
      fields: [
        { key: 'date', label: 'Gün', type: 'date', required: true },
        clock('start', 'Başlangıç saati'),
        clock('end', 'Bitiş saati'),
        { key: 'teacherId', label: 'Öğretmen', type: 'select', options },
        { key: 'room', label: 'Derslik', maxLength: 40 },
        { key: 'note', label: 'Not (isteğe bağlı)', type: 'textarea', maxLength: 300, wide: true },
        { key: 'notifyStudents', label: 'Öğrenci ve veliye bildir', type: 'checkbox', wide: true },
      ],
      submitLabel: 'Telafiyi oluştur',
      onSubmit: (draft, { key }) => {
        if (draft.end <= draft.start) throw Object.assign(new Error('Bitiş saati başlangıçtan sonra olmalı.'), { code: 'validation_error' });
        return app.api.post(`/v1/admin/sessions/${session.id}/makeup`, {
          startsAt: istanbulStamp(draft.date, draft.start), endsAt: istanbulStamp(draft.date, draft.end),
          teacherId: draft.teacherId || null, room: String(draft.room || '').trim() || null,
          note: String(draft.note || '').trim() || null, notifyStudents: Boolean(draft.notifyStudents),
        }, { key }).catch((failure) => {
          throw Object.assign(new Error(failure?.code === 'makeup_exists' ? 'Bu dersin telafisi zaten var.'
            : conflictMessage(failure)), { code: failure?.code });
        });
      },
    });
    if (!saved) return;
    app.toast(`Telafi dersi oluşturuldu: ${fmtDate(saved.date)} ${clockOf(saved.startsAt)}.`, 'good');
    view.reload();
    onChange?.(null);
  }

  async function restore(session) {
    const ok = await confirmAction(app, { title: 'İptali geri al', confirmLabel: 'Geri al',
      description: 'Ders yeniden planlı olur ve zil çağrısı geri gelir. Telafisi olan dersin iptali geri alınamaz.' });
    if (!ok) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/sessions/${session.id}/restore`, {})
      .catch((failure) => {
        throw Object.assign(new Error(failure?.code === 'makeup_exists'
          ? 'Bu dersin telafisi var; önce telafi dersini iptal edin.' : conflictMessage(failure)), { code: failure?.code });
      }));
    changed(next, 'İptal geri alındı.');
  }

  return { panel, get session() { return current; } };
}
