// BBD Akademi — Yoklama (F-A7).
//
// GÜNÜN OTURUMLARI (ders + etüt + tek seferlik ders) tek istekle gelir
// (`admin_listAttendanceSessions`; sözleşmede imleç yok, gün sınırlı bir küme).
// Sınıf süzgeci sunucuya gider; öğretmen ve "alındı / alınmadı" süzgeçleri
// yüklenmiş liste üzerinde çalışır — ek istek atılmaz. Sayılar liste yükündeki
// `counts` alanından gelir.
//
// YOKLAMA LİSTESİ sağda açılır: öğrenci başına geldi / gelmedi / geç / izinli
// + not. Onaylı izni olan öğrenci Akademi'den kendiliğinden "izinli" gelir
// (`autoExcused`) ve öyle işaretlenir. "Hepsi geldi" izinli olmayan herkesi
// "geldi" yapar (yalnız ekranda; "Kaydet" ile gider). YENİ "gelmedi" varsa
// kayıttan önce adlarıyla onay sorulur.
//
// VELİ BİLDİRİMİ ŞİMDİLİK KAPALI (ürün kararı 12.10.2026): veli yoklamayı
// uygulamada görür, devamsızlık için anlık bildirim gitmez. Ekran bildirim
// gittiğini söylemez.
//
// Sözleşmede geç kalma süresi alanı ve yoklama sürüm kilidi yoktur: süre nota
// yazılır; geçmiş günün düzeltmesi aynı `PUT` ile yapılır ve kim/ne zaman
// bilgisi oturumda görünür. Oturum kimliği opaktır (`tt:…` / `ev:…`), çözülmez.
//
// Derin bağlantılar: `{attendanceSessionId}` listeyi açar (gün de ona geçer),
// `{date}` günü seçer.

import { button, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, chipRow, kpiRow, skeletonRows, tabBar } from '../../../ui-kit/layout.js';
import {
  DAY_LABEL, actionButton, addDays, clockInput, confirmAction, dateInput, empty, errorBox, fmtDate,
  fmtDateTime, fmtTime, formDialog, istanbulStamp, num, personName, problem, reasonDialog, sectionHead,
  selectBox, todayIst, toolbar,
} from '../common.js';
import { documentButtons } from '../documents.js';
import { absenteeListDoc, attendanceDayDoc, attendanceMatrixDoc, attendanceSheetDoc } from '../documents-logic.js';
import {
  boardTab, openHistory, remindTeacher, reportsTab, reviewsTab, settingsTab,
} from './attendance-manage.js';
import { enqueue, syncBanner } from './attendance-sync.js';

/** [anahtar, etiket, ton] — Akademi `AttendanceStatus`. */
export const MARKS = [
  ['present', 'Geldi', 'good'],
  ['absent', 'Gelmedi', 'bad'],
  ['late', 'Geç', 'warn'],
  ['excused', 'İzinli', 'info'],
];
const KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik ders' };
const STATE = { taken: ['Alındı', 'good'], missing: ['Alınmadı', 'warn'], upcoming: ['Başlamadı', 'dim'],
  cancelled: ['İptal', 'dim'] };
const NOTE_HINT = { present: 'Not', absent: 'Not (ör. haber verdi)', late: 'Kaç dk geç? (ör. 10 dk)',
  excused: 'İzin notu' };

const JS_DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
function dayName(iso) {
  const [year, month, day] = String(iso || '').split('-').map(Number);
  if (!year) return '';
  return DAY_LABEL[JS_DAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()]] || '';
}

const hasStarted = (item) => Date.parse(item?.scheduleItem?.startsAt || '') <= Date.now();

function sessionState(item) {
  if (item?.scheduleItem?.status === 'cancelled') return 'cancelled';
  if (item?.taken) return 'taken';
  return hasStarted(item) ? 'missing' : 'upcoming';
}

/**
 * Oturumun sınıf adları (sınıf = grup, README §14 madde 68): çoklu sınıflı derste
 * `classGroups` hepsini taşır ("8-A + 8-B"); eski Akademi yalnız `classGroup` verir.
 */
export function classNames(scheduleItem) {
  const list = (scheduleItem?.classGroups || []).map((group) => group?.name).filter(Boolean);
  if (list.length) return list.join(' + ');
  return scheduleItem?.classGroup?.name || '';
}

function sessionTitle(scheduleItem) {
  return scheduleItem?.subject?.name || KIND[scheduleItem?.kind] || 'Ders';
}

/**
 * Yoklama bölümü: beş sekme (ilk açıldığında kurulur). "Günlük yoklama" günün dersleri ve
 * yoklama listesi; diğerleri yoklama yönetimidir (attendance-manage.js).
 * Derin bağlantı: `{tab: 'day'|'board'|'reports'|'reviews'|'settings'}`, `{attendanceSessionId, date}`.
 */
const TABS = [
  { key: 'day', label: 'Günlük yoklama' },
  { key: 'board', label: 'Pano' },
  { key: 'reports', label: 'Raporlar' },
  { key: 'reviews', label: 'Düzeltme kuyruğu' },
  { key: 'settings', label: 'Ayarlar' },
];

export function section(app) {
  loadStyles(import.meta.url, 'attendance.css');
  const node = h('div', 'ak-stack ak-att');
  node.append(sectionHead('Yoklama', {
    subtitle: 'Günün dersleri ve etütleri, pano, raporlar, düzeltme kuyruğu ve ayarlar. Kaydedilen yoklamayı '
      + 'veliler uygulamada görür.' }));
  const bodies = {};
  let day = null;
  const openSession = (attendanceSessionId, date) => go('day', { attendanceSessionId, date });
  const builders = {
    day: () => (day = dayView(app)),
    board: () => boardTab(app, { openSession }),
    reports: () => reportsTab(app),
    reviews: () => reviewsTab(app, { openSession, onCount: (count) => tabs.badge('reviews', count) }),
    settings: () => settingsTab(app),
  };
  const tabs = tabBar(TABS, 'day', (key) => show(key));
  const slot = h('div', 'ak-att-tabbody');
  node.append(tabs.node, slot);

  function show(key, payload = null) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      if (key !== 'day') bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    if (key === 'day') day.shown(payload || {});
    return bodies[key];
  }
  function go(key, payload = null) {
    tabs.select(key, false);
    return show(key, payload);
  }

  return {
    node,
    shown(payload) {
      if (payload?.attendanceSessionId || payload?.date) go('day', payload);
      else if (payload?.tab && builders[payload.tab]) go(payload.tab);
      else if (!bodies[tabs.active]) go(tabs.active);
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

/** "Günlük yoklama" sekmesi: günün oturumları + sağda yoklama listesi. */
function dayView(app) {
  const node = h('div', 'ak-stack');
  const state = { date: todayIst(), classGroupId: '', teacherId: '', show: 'all', items: [] };
  const disposers = [];
  let started = false;
  let token = 0;
  const offline = syncBanner(app, { onSynced: () => load() });
  node.append(offline.node);

  // ------------------------------------------------------------ araç şeridi
  const day = dateInput(state.date, (iso) => setDate(iso), 'Gün');
  disposers.push(() => day.destroy());
  const dayLabel = h('span', 'ak-att-dayname');
  const nav = h('div', 'ak-att-daynav');
  nav.append(
    button('‹', { variant: 'ghost', title: 'Önceki gün', onClick: () => setDate(addDays(state.date, -1)) }),
    day.node,
    button('›', { variant: 'ghost', title: 'Sonraki gün', onClick: () => setDate(addDays(state.date, 1)) }),
    button('Bugün', { variant: 'ghost', onClick: () => setDate(todayIst()) }),
    dayLabel,
  );
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    state.classGroupId = value;
    load();
  }, { label: 'Sınıf' });
  const teacherSelect = selectBox([{ value: '', label: 'Tüm öğretmenler' }], '', (value) => {
    state.teacherId = value;
    paintList();
  }, { label: 'Öğretmen' });
  node.append(toolbar(nav, classSelect.node, teacherSelect.node, h('span', 'kit-spacer'),
    button('Yenile', { variant: 'ghost', onClick: () => load() })));

  // ------------------------------------------------------------- belgeler
  // Günlük tahta ekrandaki listeden (ek istek yok); aylık çizelge seçili sınıfın
  // gösterilen ayı; devamsızlık listesi gösterilen günden geriye 30 gün.
  const className = () => (state.classGroupId ? classSelect.node.selectedOptions?.[0]?.textContent || '' : '');
  const needClass = 'Aylık çizelge için önce sınıf seçin.';
  const docs = h('div', 'ak-toolbar ak-att-docs');
  docs.append(
    h('span', 'ak-muted', 'Belgeler:'),
    documentButtons(app, { title: 'Günlük yoklama tahtası', label: 'Günlük tahta',
      build: async () => attendanceDayDoc(state.date, state.items, { classGroupName: className() }) }),
    app.can('admin_getAttendanceMatrix') ? documentButtons(app, { title: 'Aylık yoklama çizelgesi', label: 'Aylık çizelge',
      build: async () => {
        if (!state.classGroupId) throw new Error(needClass);
        return attendanceMatrixDoc(await app.api.get('/v1/admin/attendance/matrix',
          { classGroupId: state.classGroupId, month: state.date.slice(0, 7) }));
      } }) : null,
    app.can('admin_listChronicAbsentees') ? documentButtons(app, { title: 'Devamsızlık listesi', label: 'Devamsızlık',
      build: async (progress) => {
        progress(0, 0, 'Devamsızlık listesi okunuyor…');
        const from = addDays(state.date, -30);
        const items = await app.api.all('/v1/admin/attendance/chronic',
          { from, to: state.date, groupId: state.classGroupId || undefined }, { limit: 100, max: 2000 });
        return absenteeListDoc(items, { from, to: state.date, groupName: className() });
      } }) : null,
  );
  node.append(docs);

  const kpis = h('div', 'ak-att-kpis');
  const chips = chipRow([
    { key: 'all', label: 'Tümü', count: 0 },
    { key: 'pending', label: 'Alınmadı', count: 0 },
    { key: 'taken', label: 'Alındı', count: 0 },
  ], 'all', (key) => {
    state.show = key || 'all';
    if (!key) chips.set('all');
    paintList();
  });
  const listNode = h('div', 'ak-att-list');
  const sheet = sheetPanel(app, {
    onSaved: (session) => replaceSession(session),
    onSelect: () => paintList(),
    onQueued: () => offline.refresh(),
  });
  const left = h('div', 'ak-stack ak-att-left');
  left.append(chips.node, listNode);
  const split = h('div', 'ak-att-split');
  split.append(left, sheet.node);
  node.append(kpis, split);

  // ----------------------------------------------------------- veri
  function paintDay() {
    dayLabel.textContent = `${dayName(state.date)}${state.date === todayIst() ? ' · bugün' : ''}`;
  }

  async function load() {
    const mine = ++token;
    paintDay();
    listNode.replaceChildren(skeletonRows(6, 3));
    try {
      const data = await app.api.get('/v1/admin/attendance/sessions', { date: state.date,
        classGroupId: state.classGroupId || undefined });
      if (mine !== token) return;
      state.items = (data?.items || [])
        .filter((item) => item.scheduleItem?.kind !== 'break')
        .sort((a, b) => String(a.scheduleItem?.startsAt).localeCompare(String(b.scheduleItem?.startsAt))
          || classNames(a.scheduleItem).localeCompare(classNames(b.scheduleItem), 'tr'));
      paintKpis();
      paintList();
    } catch (failure) {
      if (mine !== token) return;
      kpis.replaceChildren();
      listNode.replaceChildren(errorBox(failure, () => load()));
    }
  }

  function setDate(iso) {
    if (!iso || iso === state.date) {
      day.set(state.date);
      return;
    }
    state.date = iso;
    day.set(iso);
    load();
  }

  function replaceSession(session) {
    if (!session?.id) return;
    state.items = state.items.map((item) => (item.id === session.id ? session : item));
    paintKpis();
    paintList();
  }

  // ----------------------------------------------------------- çizim
  function byTeacher() {
    return state.items.filter((item) => !state.teacherId || item.scheduleItem?.teacher?.id === state.teacherId);
  }

  function paintKpis() {
    const live = state.items.filter((item) => item.scheduleItem?.status !== 'cancelled');
    const taken = live.filter((item) => item.taken);
    const missing = live.filter((item) => !item.taken && hasStarted(item));
    const sum = (key) => taken.reduce((acc, item) => acc + (item.counts?.[key] || 0), 0);
    kpis.replaceChildren(kpiRow([
      { label: 'Ders ve etüt', value: num(live.length) },
      { label: 'Yoklaması alınan', value: `${num(taken.length)} / ${num(live.length)}` },
      { label: 'Alınması gereken', value: num(missing.length), tone: missing.length ? 'warn' : '',
        title: 'Başlamış ama yoklaması alınmamış dersler' },
      { label: 'Gelmedi', value: num(sum('absent')), tone: sum('absent') ? 'bad' : '' },
      { label: 'Geç', value: num(sum('late')) },
      { label: 'İzinli', value: num(sum('excused')) },
    ]));
  }

  function paintList() {
    const base = byTeacher();
    const pending = base.filter((item) => !item.taken && item.scheduleItem?.status !== 'cancelled');
    const taken = base.filter((item) => item.taken);
    chips.counts({ all: base.length, pending: pending.length, taken: taken.length });
    const rows = state.show === 'pending' ? pending : (state.show === 'taken' ? taken : base);
    listNode.replaceChildren();
    if (!state.items.length) {
      listNode.append(empty('Bu gün ders yok', `${fmtDate(state.date)} ${dayName(state.date)} için `
        + 'programda ders ya da etüt görünmüyor.'));
      return;
    }
    if (!rows.length) {
      listNode.append(empty('Bu süzgece uyan ders yok', 'Öğretmen ya da durum süzgecini değiştirin.'));
      return;
    }
    for (const item of rows) listNode.append(itemNode(item));
  }

  function itemNode(item) {
    const si = item.scheduleItem || {};
    const status = sessionState(item);
    const row = h('button', `ak-att-item ak-att-is-${status}${item.id === sheet.currentId() ? ' on' : ''}`);
    row.type = 'button';
    const time = h('div', 'ak-att-time');
    time.append(h('b', '', fmtTime(si.startsAt)), h('small', '', fmtTime(si.endsAt)));
    const main = h('div', 'ak-att-main');
    const title = h('div', 'ak-att-title');
    title.append(h('b', '', sessionTitle(si)));
    if (si.kind && si.kind !== 'lesson') title.append(badge(KIND[si.kind] || si.kind, 'info'));
    main.append(title, h('small', '', [classNames(si), personName(si.teacher) !== '—' ? personName(si.teacher) : null,
      si.room].filter(Boolean).join(' · ') || '—'));
    if (si.status === 'changed') main.append(h('small', 'ak-att-change', `Değişti${si.changeNote ? `: ${si.changeNote}` : ''}`));
    const side = h('div', 'ak-att-state');
    const [label, tone] = STATE[status];
    side.append(badge(label, tone));
    const counts = item.counts || {};
    if (item.taken) {
      side.append(h('small', '', `${num((counts.present || 0) + (counts.late || 0))}/${num(counts.enrolled || 0)} geldi`
        + `${counts.absent ? ` · ${num(counts.absent)} yok` : ''}`));
    } else {
      side.append(h('small', '', `${num(counts.enrolled || 0)} öğrenci`));
    }
    row.append(time, main, side);
    row.setAttribute('aria-label', `${fmtTime(si.startsAt)} ${sessionTitle(si)} ${classNames(si)}: ${label}`);
    row.addEventListener('click', () => sheet.open(item.id));
    return row;
  }

  async function loadFilters() {
    const [classes, teachers] = await Promise.all([
      app.lookups.options('classGroups', { empty: 'Tüm sınıflar' }).catch(() => null),
      app.lookups.teachers().catch(() => null),
    ]);
    if (classes) classSelect.options(classes);
    if (teachers) {
      teacherSelect.options([{ value: '', label: 'Tüm öğretmenler' },
        ...teachers.map((item) => ({ value: item.id, label: item.displayName || item.id }))]);
    }
  }

  return {
    node,
    async shown(payload) {
      const wantedDate = /^\d{4}-\d{2}-\d{2}$/.test(String(payload?.date || '')) ? payload.date : null;
      if (wantedDate && wantedDate !== state.date) {
        state.date = wantedDate;
        day.set(wantedDate);
        if (started) load();
      }
      if (!started) {
        started = true;
        loadFilters();
        load();
        offline.refresh();
      }
      if (payload?.attendanceSessionId) {
        const data = await sheet.open(String(payload.attendanceSessionId));
        const sessionDate = data?.session?.scheduleItem?.date;
        if (!wantedDate && sessionDate && sessionDate !== state.date) setDate(sessionDate);
      }
    },
    destroy() {
      token += 1;
      for (const fn of disposers.splice(0)) fn();
    },
  };
}

// ------------------------------------------------------------- yoklama listesi

function sheetPanel(app, { onSaved, onSelect, onQueued }) {
  const node = h('section', 'ak-att-sheet');
  // Pencere kapandıysa (`locked`) değişiklik yalnız gerekçeli düzeltmedir (`admin_correctAttendance`).
  let editable = app.can('admin_putAttendanceSheet');
  let sheet = null;
  let currentId = null;
  let marks = new Map();       // studentId → {status, note}
  let original = new Map();
  let refs = null;
  let saving = false;
  let token = 0;

  function placeholder() {
    node.replaceChildren(empty('Yoklama listesi', 'Soldan bir ders seçin; öğrenci listesi burada açılır.'));
  }
  placeholder();

  function apply(data) {
    sheet = data;
    editable = data?.locked ? app.can('admin_correctAttendance') : app.can('admin_putAttendanceSheet');
    original = new Map((data?.rows || []).map((row) => [row.student.id,
      { status: row.status || 'present', note: row.note || '' }]));
    marks = new Map([...original].map(([id, value]) => [id, { ...value }]));
  }

  function changedIds() {
    const list = [];
    for (const [id, value] of marks) {
      const before = original.get(id);
      if (!before || before.status !== value.status
        || String(before.note || '').trim() !== String(value.note || '').trim()) list.push(id);
    }
    return list;
  }

  async function leaveOk() {
    if (!sheet || !changedIds().length) return true;
    return confirmAction(app, { title: 'Kaydedilmemiş yoklama',
      description: 'Açık yoklama listesinde kaydedilmemiş değişiklikler var. Devam ederseniz kaybolur.',
      confirmLabel: 'Değişiklikleri bırak', danger: true });
  }

  async function open(id) {
    if (!id) return null;
    if (id === currentId && sheet) return sheet;
    if (!(await leaveOk())) return null;
    currentId = id;
    onSelect?.(id);
    const mine = ++token;
    refs = null;
    node.replaceChildren(skeletonRows(8, 3));
    try {
      const data = await app.api.get(`/v1/admin/attendance/sessions/${id}`);
      if (mine !== token) return null;
      apply(data);
      render();
      return data;
    } catch (failure) {
      if (mine !== token) return null;
      sheet = null;
      node.replaceChildren(errorBox(failure, () => {
        currentId = null;
        open(id);
      }));
      return null;
    }
  }

  function render() {
    const session = sheet?.session || {};
    const si = session.scheduleItem || {};
    const rows = sheet?.rows || [];
    const head = h('div', 'ak-att-sheet-head');
    const titleLine = h('div', 'ak-att-sheet-title');
    titleLine.append(h('span', '', `${sessionTitle(si)}${classNames(si) ? ` · ${classNames(si)}` : ''}`));
    if (si.kind && si.kind !== 'lesson') titleLine.append(badge(KIND[si.kind] || si.kind, 'info'));
    head.append(titleLine,
      h('div', 'ak-att-sheet-sub', [`${fmtDate(si.date)} ${dayName(si.date)}`, `${fmtTime(si.startsAt)}–${fmtTime(si.endsAt)}`,
        personName(si.teacher) !== '—' ? personName(si.teacher) : null, si.room].filter(Boolean).join(' · ')),
      h('div', `ak-att-taken${session.taken ? ' on' : ''}`, session.taken
        ? `Yoklamayı ${personName(session.takenBy)} aldı · ${fmtDateTime(session.takenAt)}`
        : 'Yoklama henüz alınmadı.'));

    const notes = h('div', 'ak-stack');
    if (si.status === 'cancelled') {
      notes.append(alertBox(`Bu ders iptal edildi${si.changeNote ? `: ${si.changeNote}` : ''}. Yoklama gerekmiyorsa kaydetmeyin.`, 'warn'));
    } else if (si.status === 'changed') {
      notes.append(alertBox(`Bu derste değişiklik var${si.changeNote ? `: ${si.changeNote}` : ''}.`, 'info'));
    }
    if (session.taken && si.date && si.date < todayIst()) {
      notes.append(alertBox('Geçmiş günün yoklamasını düzeltiyorsunuz. Kaydedilen değişiklik eskisinin yerine '
        + 'geçer ve velinin uygulamasındaki yoklama da güncellenir.', 'info'));
    } else if (!session.taken && !hasStarted(session)) {
      notes.append(alertBox('Ders henüz başlamadı; yoklamayı ders başladıktan sonra almanız önerilir.', 'info'));
    }
    if (sheet?.locked) {
      notes.append(alertBox(`Öğretmenin düzeltme penceresi kapandı${sheet.editableUntil
        ? ` (${fmtDateTime(sheet.editableUntil)})` : ''}. Değişiklik gerekçeyle “düzeltme” olarak kaydedilir ve `
        + 'denetim kaydına yazılır.', 'info'));
    }
    const autoCount = rows.filter((row) => row.autoExcused).length;
    if (autoCount) {
      notes.append(h('div', 'ak-muted ak-att-hint', `${num(autoCount)} öğrencinin onaylı izni var; `
        + '“izinli” olarak işaretli geldi.'));
    }

    const counters = h('div', 'ak-att-counters');
    const allPresent = editable ? button('Hepsi geldi', { title: 'İzinli olmayan herkesi “geldi” yap',
      onClick: () => markAllPresent() }) : null;
    const tools = h('div', 'ak-att-tools');
    tools.append(counters, h('span', 'kit-spacer'));
    if (allPresent) tools.append(allPresent);
    // Çizelge KAYITLI yoklamayı basar (ekrandaki kaydedilmemiş işaretler girmez).
    const sessionId = session.id;
    tools.append(documentButtons(app, { title: 'Yoklama çizelgesi', label: 'Çizelge',
      build: async () => attendanceSheetDoc(await app.api.get(`/v1/admin/attendance/sessions/${sessionId}`)) }));
    const actions = h('div', 'ak-att-actions');
    if (session.taken) {
      if (app.can('admin_listAttendanceHistory')) {
        actions.append(button('Geçmiş', { variant: 'ghost', title: 'Kim, ne zaman, hangi kanaldan değiştirdi',
          onClick: () => openHistory(app, sessionId, `Yoklama geçmişi · ${sessionTitle(si)}`) }));
      }
      actions.append(actionButton(app, 'admin_refreshAttendanceRoster', 'Listeyi yenile', { variant: 'ghost',
        title: 'Sonradan sınıfa eklenen öğrencileri listeye ekler', onClick: () => refreshRoster() }));
      actions.append(actionButton(app, 'admin_voidAttendance', 'Yoklamayı sil', { variant: 'ghost',
        title: 'Yanlış derse alınmış yoklamayı gerekçeyle siler', onClick: () => voidSheet() }));
    } else if (hasStarted(session) && si.status !== 'cancelled') {
      const remind = actionButton(app, 'admin_remindAttendance', 'Öğretmene hatırlat', { variant: 'ghost',
        onClick: () => remindTeacher(app, sessionId, remind).then((result) => { if (result === 'taken') reopen(); }) });
      actions.append(remind);
    }

    const list = h('div', 'ak-att-rows');
    const rowRefs = new Map();
    if (!rows.length) {
      list.append(empty('Bu derste öğrenci yok', 'Sınıf listesi boş ya da etüde öğrenci eklenmemiş.'));
    }
    rows.forEach((row, index) => {
      const refsForRow = rowNode(row, index, si);
      rowRefs.set(row.student.id, refsForRow);
      list.append(refsForRow.line);
    });

    const foot = h('div', 'ak-att-foot');
    const dirtyText = h('span', 'ak-att-dirty');
    const revert = button('Vazgeç', { variant: 'ghost', onClick: () => {
      apply(sheet);
      render();
    } });
    const save = actionButton(app, 'admin_putAttendanceSheet', session.taken ? 'Düzeltmeyi kaydet' : 'Yoklamayı kaydet',
      { variant: 'primary', onClick: () => submit() });
    foot.append(dirtyText, h('span', 'kit-spacer'), revert, save);

    node.replaceChildren(head, notes, tools, actions, list, foot);
    refs = { counters, rowRefs, dirtyText, revert, save };
    paintCounts();
    paintFoot();
  }

  function rowNode(row, index, si) {
    const id = row.student.id;
    const value = marks.get(id);
    const line = h('div', 'ak-att-row');
    const who = h('div', 'ak-att-who');
    const name = h('div', 'ak-att-name');
    name.append(h('span', '', row.student.displayName || '—'));
    if (row.autoExcused) name.append(badge('Onaylı izin', 'info'));
    if (row.student.status === 'missing') name.append(badge('KM dizininde yok', 'warn'));
    if (row.leftEarlyAt) name.append(badge(`Çıktı ${fmtTime(row.leftEarlyAt)}`, 'warn'));
    // Öğrencinin sınıfı oturumun sınıflarından biri değilse yazılır (çoklu sınıflı derste
    // hangi sınıftan geldiği görünür).
    const sessionClasses = new Set([si.classGroup?.name, ...(si.classGroups || []).map((group) => group?.name)]
      .filter(Boolean));
    const ownClass = row.student.classGroup?.name;
    const otherClass = ownClass && (!sessionClasses.has(ownClass) || sessionClasses.size > 1) ? ownClass : null;
    who.append(name, h('small', '', [otherClass, row.student.externalRef].filter(Boolean).join(' · ')));

    const group = h('div', 'ak-att-marks');
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', `${row.student.displayName || 'Öğrenci'}: durum`);
    const buttons = new Map();
    for (const [key, label, tone] of MARKS) {
      const mark = h('button', `ak-att-mark ${tone}`, label);
      mark.type = 'button';
      mark.setAttribute('role', 'radio');
      mark.disabled = !editable;
      mark.addEventListener('click', () => setMark(id, key));
      buttons.set(key, mark);
      group.append(mark);
    }
    const note = h('input', 'kit-input ak-att-note');
    note.type = 'text';
    note.maxLength = 200;
    note.value = value?.note || '';
    note.disabled = !editable;
    note.setAttribute('aria-label', `${row.student.displayName || 'Öğrenci'}: not`);
    note.addEventListener('input', () => {
      marks.get(id).note = note.value;
      paintFoot();
    });
    const warn = h('small', 'ak-att-warn');
    who.append(warn);
    if (sheet?.session?.taken && !row.leftEarlyAt && ['present', 'late'].includes(row.status)
      && app.can('admin_markStudentLeft')) {
      const left = button('Çıktı', { variant: 'ghost', title: 'Öğrenci dersten erken çıktı',
        onClick: () => markLeft(row) });
      left.classList.add('ak-att-left');
      who.append(left);
    }
    line.append(h('span', 'ak-att-no', String(index + 1)), who, group, note);
    const rowRef = { line, buttons, note, warn, autoExcused: Boolean(row.autoExcused) };
    paintRow(id, rowRef);
    return rowRef;
  }

  function paintRow(id, rowRef = refs?.rowRefs.get(id)) {
    if (!rowRef) return;
    const value = marks.get(id);
    for (const [key, mark] of rowRef.buttons) {
      const on = value?.status === key;
      mark.classList.toggle('on', on);
      mark.setAttribute('aria-checked', on ? 'true' : 'false');
    }
    rowRef.line.className = `ak-att-row${value?.status ? ` ak-att-is-${value.status}` : ''}`;
    rowRef.note.placeholder = NOTE_HINT[value?.status] || 'Not';
    rowRef.warn.textContent = rowRef.autoExcused && value?.status !== 'excused'
      ? 'Onaylı izni var; “izinli” yerine başka durum seçildi.' : '';
  }

  function setMark(id, key) {
    const value = marks.get(id);
    if (!value || value.status === key) return;
    value.status = key;
    paintRow(id);
    paintCounts();
    paintFoot();
  }

  function markAllPresent() {
    for (const [id, value] of marks) {
      if (value.status === 'excused') continue;
      value.status = 'present';
      paintRow(id);
    }
    paintCounts();
    paintFoot();
  }

  function paintCounts() {
    if (!refs) return;
    const tally = { present: 0, absent: 0, late: 0, excused: 0 };
    for (const value of marks.values()) tally[value.status] = (tally[value.status] || 0) + 1;
    refs.counters.replaceChildren();
    for (const [key, label, tone] of MARKS) {
      const item = h('span', `ak-att-count ${tone}`);
      item.append(h('b', '', num(tally[key] || 0)), h('span', '', label));
      refs.counters.append(item);
    }
    refs.counters.append(h('span', 'ak-muted', `/ ${num(marks.size)} öğrenci`));
  }

  function paintFoot() {
    if (!refs) return;
    const changed = changedIds().length;
    const taken = Boolean(sheet?.session?.taken);
    refs.dirtyText.textContent = changed ? `${num(changed)} öğrencide değişiklik — kaydedilmedi`
      : (taken ? 'Değişiklik yok' : 'İşaretleri kontrol edip kaydedin.');
    refs.dirtyText.classList.toggle('on', changed > 0);
    refs.revert.hidden = !changed;
    if (editable) {
      refs.save.disabled = saving || (!changed && taken) || !marks.size;
      refs.save.textContent = saving ? 'Kaydediliyor…' : (taken ? 'Düzeltmeyi kaydet' : 'Yoklamayı kaydet');
    }
  }

  async function submit() {
    if (!sheet || saving) return;
    const session = sheet.session || {};
    const rows = sheet.rows || [];
    const fresh = rows.filter((row) => marks.get(row.student.id)?.status === 'absent'
      && (!session.taken || original.get(row.student.id)?.status !== 'absent'));
    if (fresh.length) {
      const names = fresh.slice(0, 6).map((row) => row.student.displayName).join(', ')
        + (fresh.length > 6 ? ` ve ${num(fresh.length - 6)} öğrenci daha` : '');
      const ok = await confirmAction(app, {
        title: session.taken ? 'Düzeltmeyi kaydet' : 'Yoklamayı kaydet',
        description: `“Gelmedi” işaretli ${num(fresh.length)} öğrenci: ${names}. Devamsızlık kaydedilir; `
          + 'veliler yoklamayı uygulamada görür.',
        confirmLabel: 'Kaydet',
      });
      if (!ok) return;
    }
    const rowOf = (row) => {
      const value = marks.get(row.student.id);
      return { studentId: row.student.id, status: value.status, note: String(value.note || '').trim() || null };
    };
    let reason = null;
    if (sheet.locked) {
      reason = await reasonDialog(app, { title: 'Gerekçeli düzeltme', confirmLabel: 'Düzeltmeyi kaydet', danger: false,
        description: 'Düzeltme penceresi kapandı. Gerekçe değişmez yoklama geçmişine eski ve yeni değerle yazılır.',
        presets: ['Öğretmen yanlış işaretlemiş', 'Veli izin belgesi getirdi', 'Öğrenci derse geç katıldı'] });
      if (!reason) return;
    }
    const body = {
      markAllPresent: false,
      rows: rows.map(rowOf),
    };
    saving = true;
    paintFoot();
    const id = currentId;
    try {
      const changed = new Set(changedIds());
      const next = sheet.locked
        ? await app.api.post(`/v1/admin/attendance/sessions/${id}/corrections`, { reason,
          rows: rows.filter((row) => changed.has(row.student.id)).map(rowOf),
          ...(Number.isInteger(sheet.version) ? { expectedVersion: sheet.version } : {}) })
        : await app.api.put(`/v1/admin/attendance/sessions/${id}`, body);
      saving = false;
      if (id !== currentId) return;
      apply(next);
      render();
      onSaved?.(next?.session);
      app.toast(fresh.length ? `Yoklama kaydedildi; ${num(fresh.length)} öğrenci gelmedi olarak işlendi.`
        : 'Yoklama kaydedildi.', 'good');
    } catch (failure) {
      saving = false;
      paintFoot();
      if (failure?.code === 'student_not_in_session') {
        app.toast(`${problem(failure)} Liste değişmiş olabilir; yenileyip tekrar deneyin.`, 'bad');
      } else if (failure?.code === 'km_unavailable' && !sheet.locked && app.can('admin_syncAttendance')) {
        // Sunucuya ulaşılamadı: işaretler bu bilgisayarda bekler, bağlantı gelince gönderilir.
        const si = session.scheduleItem || {};
        enqueue({ sessionId: id, baseVersion: sheet.version || 0, rows: body.rows,
          label: `${sessionTitle(si)}${classNames(si) ? ` · ${classNames(si)}` : ''}, ${fmtDate(si.date)} ${fmtTime(si.startsAt)}` });
        app.toast('Sunucuya ulaşılamadı; yoklama bu bilgisayarda bekletiliyor, bağlantı gelince gönderilecek.', 'warn');
        onQueued?.();
      } else if (failure?.code === 'version_conflict') {
        app.toast('Yoklama bu arada başka biri tarafından güncellendi. Listeyi yenileyip tekrar deneyin.', 'warn');
      } else {
        app.toast(problem(failure, 'Yoklama kaydedilemedi.'), 'bad');
      }
    }
  }

  /** Açık listeyi sunucudan yeniden okur (yazma işleminden sonra). */
  async function reopen() {
    const id = currentId;
    currentId = null;
    sheet = null;
    return open(id);
  }

  function took(next, message) {
    if (!next) return;
    apply(next);
    render();
    onSaved?.(next.session);
    if (message) app.toast(message, 'good');
  }

  async function refreshRoster() {
    const before = (sheet?.rows || []).length;
    try {
      const next = await app.api.post(`/v1/admin/attendance/sessions/${currentId}/refresh-roster`, {});
      const added = (next?.rows || []).length - before;
      took(next, added > 0 ? `${num(added)} öğrenci listeye eklendi; işaretleyip kaydedin.` : 'Liste güncel; yeni öğrenci yok.');
    } catch (failure) {
      app.toast(problem(failure, 'Liste yenilenemedi.'), 'bad');
    }
  }

  async function voidSheet() {
    const reason = await reasonDialog(app, { title: 'Yoklamayı sil', confirmLabel: 'Yoklamayı sil',
      description: 'Yanlış derse alınmış yoklama içindir. Satırlar silinmez, geçmişe “silindi” olarak yazılır ve ders '
        + 'yeniden “yoklama bekliyor” olur. Dersin iptali ancak bundan sonra yapılabilir.',
      presets: ['Yanlış derse alındı', 'Ders yapılmadı'] });
    if (!reason) return;
    try {
      took(await app.api.post(`/v1/admin/attendance/sessions/${currentId}/void`, { reason }), 'Yoklama silindi.');
    } catch (failure) {
      app.toast(problem(failure, 'Yoklama silinemedi.'), 'bad');
    }
  }

  async function markLeft(row) {
    const now = new Date().toLocaleTimeString('tr-TR', { timeZone: 'Europe/Istanbul', hour: '2-digit', minute: '2-digit' });
    const date = sheet?.session?.scheduleItem?.date || todayIst();
    const result = await formDialog(app, {
      title: `${row.student.displayName}: dersten çıktı`,
      description: 'Onaylı erken çıkış bildirimi varsa günün sonraki dersleri kendiliğinden “izinli” olur. '
        + 'Habersiz çıkışta yalnız bu dersin kaydına yazılır.',
      value: { clock: now, note: '' },
      fields: [
        { key: 'clock', label: 'Çıkış saati', type: 'custom', required: true,
          render: (draft, set) => clockInput(draft.clock, (value) => set(value), 'Çıkış saati').node,
          validate: (raw) => (/^([01]\d|2[0-3]):[0-5]\d$/.test(String(raw || '')) ? null : 'Saati 14:20 gibi yazın.') },
        { key: 'note', label: 'Not (isteğe bağlı)', maxLength: 200 },
      ],
      submitLabel: 'Kaydet',
      onSubmit: (draft) => app.api.post(`/v1/admin/attendance/sessions/${currentId}/students/${row.student.id}/left`,
        { leftAt: istanbulStamp(date, draft.clock), note: String(draft.note || '').trim() || null }),
    });
    if (!result) return;
    const excused = (result.excusedSessionIds || []).length;
    took(result.sheet, excused ? `Çıkış kaydedildi; günün sonraki ${num(excused)} dersi “izinli” oldu.`
      : 'Çıkış kaydedildi.');
  }

  return {
    node,
    open,
    currentId: () => currentId,
  };
}
