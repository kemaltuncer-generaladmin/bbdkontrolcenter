// BBD Akademi — Yoklama yönetimi (KM-2; Akademi F-Y7/F-Y8/F-Y9/F-Y11/F-Y12/F-Y13/F-Y15/F-Y17/F-Y19).
//
// Yoklama bölümünün "Günlük yoklama" dışındaki sekmeleri:
//   · Pano            — günün tahtası (`admin_getAttendanceBoard`: beklenen / gelen / gelmeyen /
//                       alınmamış), alınmamış derste öğretmene hatırlatma (`admin_remindAttendance`),
//                       günün erken çıkışları (`admin_listEarlyLeaves`) ve varlık ipucu
//                       tutarsızlıkları (`admin_listAttendanceInconsistencies`, bayrak
//                       `attendance_signals` — kapalıysa açık "kapalı" notu).
//   · Raporlar        — devam oranı (ders / öğretmen / sınıf), öğretmen uyumu, izin nedenleri,
//                       dışa aktarım (`admin_exportAttendance` → ortak `exportJob`).
//   · Düzeltme kuyruğu — pencere dışında senkronlanan yoklamalar (`admin_listAttendanceReviews`)
//                       ve onay / ret (`admin_decideAttendanceReview`).
//   · Ayarlar         — `admin_getAttendanceSettings` + KM'ye özel `admin_putAttendanceSettings`.
//
// Hepsi ön hesaplı günlük özetlerden okunur (Akademi PERFORMANCE §3.6); satır başına istek atılmaz.
// Yetkisi olmayan kullanıcı sekmeyi görür ama içinde gerekçeli "yetki yok" notu vardır.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { barChart } from '../../ui-kit/charts.js';
import {
  actionButton, addDays, dateInput, empty, exportJob, fmtDate, fmtDateTime, fmtTime, formDialog, kv, loader,
  multiChips, num, openDrawer, optionsOf, pagedList, personName, problem, reasonDialog, staticList, statusBadge,
  todayIst, toolbar,
} from './common.js';

// ------------------------------------------------------------- etiketler

export const LEAVE_KINDS = { full_day: 'Tam gün', partial: 'Saat aralığı', early_leave: 'Erken çıkış',
  retro_excuse: 'Mazeret' };
export const LEAVE_REASONS = { illness: 'Hastalık', family: 'Aile', travel: 'Seyahat', official: 'Resmî',
  other: 'Diğer' };
export const CHANNELS = { mobile: 'Uygulama', km: 'Kontrol Merkezi', offline_sync: 'Çevrimdışı senkron',
  system_leave: 'Onaylı izin (otomatik)', system: 'Sistem' };
export const MARK_LABEL = { present: 'Geldi', absent: 'Gelmedi', late: 'Geç', excused: 'İzinli' };
const REVIEW_STATUS = { pending: ['Bekliyor', 'warn'], approved: ['Onaylandı', 'good'], rejected: ['Reddedildi', 'dim'] };
const RATE_GROUPS = [{ key: 'subject', label: 'Derse göre' }, { key: 'teacher', label: 'Öğretmene göre' },
  { key: 'group', label: 'Sınıfa göre' }];
export const EXPORT_KINDS = {
  group_month: 'Sınıf aylık çizelgesi', student_cards: 'Öğrenci devam karneleri',
  session_audit: 'Oturum denetim kaydı', teacher_compliance: 'Öğretmen uyumu', chronic: 'Devamsızlık listesi',
  leave_stats: 'İzin istatistikleri',
};
const EXPORT_FORMATS = { xlsx: 'Excel (XLSX)', pdf: 'PDF', csv: 'CSV' };
const SIGNAL_SOURCE = { tablet: 'Tablet', canteen: 'Kantin' };

const pct = (value) => (value === null || value === undefined ? '—' : `%${num(value, 1)}`);

/** Erişimi olmayan sekmenin gövdesi (gizlenmez — kullanıcı neden boş olduğunu görür). */
export function noAccess(text = 'Bu bölüm için yetkiniz yok (yöneticinizden izin isteyin).') {
  const node = h('div', 'ak-off');
  node.append(alertBox(text, 'info'));
  return node;
}

/** Oturum özeti (`AttendanceSessionSummary`) → "Matematik · 8-A + 8-B". */
export function summaryTitle(session) {
  const si = session?.scheduleItem || {};
  const classes = (si.classGroups || []).map((item) => item?.name).filter(Boolean);
  const names = classes.length ? classes.join(' + ') : (si.classGroup?.name || '');
  return [si.subject?.name || (si.kind === 'etut' ? 'Etüt' : 'Ders'), names].filter(Boolean).join(' · ');
}

function sessionWhen(session) {
  const si = session?.scheduleItem || {};
  return `${fmtDate(si.date)} ${fmtTime(si.startsAt)}–${fmtTime(si.endsAt)}`;
}

function stack(main, sub) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('span', '', main || '—'));
  if (sub) node.append(h('small', 'ak-muted', sub));
  return node;
}

/** Aralık en çok 1 yıl (sözleşme `range_too_long`). */
export function rangeProblem(from, to) {
  if (!from || !to) return 'Başlangıç ve bitiş seçin.';
  if (to < from) return 'Bitiş başlangıçtan önce olamaz.';
  if (addDays(from, 366) <= to) return 'Aralık en çok 1 yıl olabilir.';
  return null;
}

// =================================================================== PANO

export function boardTab(app, { openSession } = {}) {
  const node = h('div', 'ak-stack ak-att-board');
  if (!app.can('admin_getAttendanceBoard')) {
    node.append(noAccess());
    return { node, reload() {}, destroy() {} };
  }
  let date = todayIst();
  const day = dateInput(date, (iso) => { if (iso) { date = iso; reload(); } }, 'Gün');
  node.append(toolbar(day.node, button('Bugün', { variant: 'ghost', onClick: () => { date = todayIst(); day.set(date); reload(); } }),
    h('span', 'kit-spacer'), button('Yenile', { variant: 'ghost', onClick: () => reload() })));

  const board = loader(() => app.api.get('/v1/admin/attendance/board', { date }), (data) => renderBoard(data),
    { rows: 4, columns: 6 });
  const leaves = staticList({
    load: () => app.api.get('/v1/admin/attendance/early-leaves', { date }),
    dense: true,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.student?.displayName,
        row.student?.classGroup?.name) },
      { key: 'at', label: 'Çıkış saati', cell: (row) => fmtTime(row.leaveAt) },
      { key: 'pickup', label: 'Teslim alacak', cell: (row) => row.pickupPersonName || '—' },
      { key: 'left', label: 'Durum', cell: (row) => (row.leftAt ? badge(`Çıktı ${fmtTime(row.leftAt)}`, 'good')
        : badge('Henüz çıkmadı', 'warn')) },
      { key: 'sessions', label: 'Etkilenen ders', align: 'num', cell: (row) => num((row.sessionIds || []).length) },
    ],
    empty: { title: 'Bu gün bildirilmiş erken çıkış yok' },
  });
  const signalsOff = app.api.featureOff?.('admin_listAttendanceInconsistencies');
  const inconsistencies = signalsOff || !app.can('admin_listAttendanceInconsistencies') ? null : pagedList({
    app,
    path: '/v1/admin/attendance/inconsistencies',
    query: () => ({ date }),
    limit: 50,
    dense: true,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => stack(row.student?.displayName,
        row.student?.classGroup?.name) },
      { key: 'session', label: 'Ders', width: 'minmax(0, 1.6fr)', cell: (row) => stack(summaryTitle(row.session),
        sessionWhen(row.session)) },
      { key: 'signals', label: 'Varlık ipucu', width: 'minmax(0, 1.4fr)', cell: (row) => (row.signals || [])
        .map((signal) => `${SIGNAL_SOURCE[signal.source] || signal.source} ${fmtTime(signal.at)}`).join(' · ') || '—' },
    ],
    onRow: (row) => openSession?.(row.session?.id, row.date),
    empty: { title: 'Tutarsızlık yok', text: '“Gelmedi” işaretli olup aynı gün tablet ya da kantin kaydı olan öğrenci yok.' },
  });

  const signalsBody = signalsOff
    ? noAccess('Varlık ipuçları (tablet ve kantin saatleri) kurumda kapalı. Ayarlar → Kurum ayarları → '
      + 'Özellikler\'den “attendance_signals” açılınca burada listelenir.')
    : (inconsistencies ? inconsistencies.node : noAccess());
  node.append(board.node,
    card('Erken çıkışlar', leaves.node, 'Veli ya da yönetici önceden bildirdi'),
    card('Tutarsızlıklar', signalsBody, 'Yoklamayı asla kendiliğinden değiştirmez'));

  function renderBoard(data) {
    const tiles = kpiRow([
      { label: 'Beklenen öğrenci', value: num(data.expectedStudents) },
      { label: 'Geldi', value: num(data.presentStudents), tone: 'good' },
      { label: 'Gelmedi', value: num(data.absentStudents), tone: data.absentStudents ? 'bad' : '' },
      { label: 'Geç', value: num(data.lateStudents), tone: data.lateStudents ? 'warn' : '' },
      { label: 'İzinli', value: num(data.excusedStudents) },
      { label: 'Yoklaması alınan', value: `${num(data.sessionsTaken)} / ${num(data.sessionsTotal)}` },
      { label: 'Alınmamış', value: num(data.sessionsPending), tone: data.sessionsPending ? 'warn' : '',
        title: 'Başlamış ama yoklaması alınmamış dersler' },
    ]);
    const pending = data.pending || [];
    const list = h('div', 'ak-stack');
    if (!pending.length) {
      list.append(empty('Alınmamış yoklama yok', `${fmtDate(data.date)} için başlamış her dersin yoklaması alındı.`));
    }
    for (const item of pending) list.append(pendingRow(item));
    return [tiles, h('small', 'ak-muted', `Son hesaplama: ${fmtDateTime(data.computedAt)}`),
      card(`Yoklaması alınmamış dersler (${num(pending.length)})`, list)];
  }

  function pendingRow(item) {
    const si = item.scheduleItem || {};
    const row = h('div', 'ak-att-pending');
    const main = h('div', 'ak-att-main');
    main.append(h('b', '', `${fmtTime(si.startsAt)}–${fmtTime(si.endsAt)} · ${summaryTitle(item)}`),
      h('small', '', [personName(si.teacher) !== '—' ? personName(si.teacher) : 'Öğretmen atanmadı', si.room,
        `${num(item.counts?.enrolled || 0)} öğrenci`].filter(Boolean).join(' · ')));
    const tools = h('div', 'ak-inline');
    const remind = actionButton(app, 'admin_remindAttendance', 'Öğretmene hatırlat', { variant: 'ghost',
      onClick: () => remindTeacher(app, item.id, remind).then((ok) => { if (ok === 'taken') reload(); }) });
    tools.append(remind, button('Listeyi aç', { variant: 'ghost', onClick: () => openSession?.(item.id, si.date) }));
    row.append(main, tools);
    return row;
  }

  function reload() {
    board.reload();
    leaves.reload();
    inconsistencies?.reload();
  }
  return { node, reload, destroy() { day.destroy(); inconsistencies?.destroy(); } };
}

/**
 * Öğretmene "yoklama alınmadı" hatırlatması. Akademi oturum başına 10 dk'da bir gönderir;
 * yoklama bu arada alındıysa `409 attendance_already_taken` → 'taken'.
 */
export async function remindTeacher(app, sessionId, control = null) {
  if (control) control.disabled = true;
  try {
    const result = await app.api.post(`/v1/admin/attendance/sessions/${sessionId}/remind`, {});
    const count = Number(result?.notifiedCount || 0);
    app.toast(count ? 'Öğretmene hatırlatma gönderildi.' : 'Hatırlatma gönderilecek etkin öğretmen yok.',
      count ? 'good' : 'warn');
    return 'sent';
  } catch (failure) {
    if (failure?.code === 'attendance_already_taken') {
      app.toast('Bu dersin yoklaması bu arada alındı.', 'good');
      return 'taken';
    }
    app.toast(problem(failure, 'Hatırlatma gönderilemedi.'), 'bad');
    return null;
  } finally {
    if (control) control.disabled = false;
  }
}

// ================================================================= RAPORLAR

export function reportsTab(app) {
  const node = h('div', 'ak-stack ak-att-reports');
  if (!app.can('admin_getAttendanceRates') && !app.can('admin_getTeacherCompliance')
    && !app.can('admin_getLeaveStats')) {
    node.append(noAccess());
    return { node, reload() {}, destroy() {} };
  }
  const range = { from: todayIst(-29), to: todayIst() };
  let groupBy = 'subject';
  const picker = dateRange({ start: range.from, end: range.to, label: 'Aralık', onChange: (value) => {
    range.from = value.start;
    range.to = value.end;
    reload();
  } });
  const exportButton = actionButton(app, 'admin_exportAttendance', 'Dışa aktar…', { onClick: () => exportDialog(app, range) });
  const warn = h('div');
  node.append(toolbar(picker.node, h('span', 'kit-spacer'), exportButton), warn);

  const chips = chipRow(RATE_GROUPS, groupBy, (key) => {
    groupBy = key || 'subject';
    if (!key) chips.set('subject');
    rates.reload();
  });
  const rates = loader(() => app.api.get('/v1/admin/attendance/rates', { from: range.from, to: range.to, groupBy }),
    (data) => {
      const rows = data?.rows || [];
      if (!rows.length) return empty('Bu aralıkta yoklama yok');
      const table = dataTable({ dense: true, rows, rowKey: (row) => row.key, columns: [
        { key: 'label', label: RATE_GROUPS.find((item) => item.key === groupBy)?.label.replace(' göre', '') || 'Ad',
          width: 'minmax(0, 1.6fr)', cell: (row) => row.label },
        { key: 'sessions', label: 'Oturum', align: 'num', cell: (row) => num(row.sessions) },
        { key: 'rate', label: 'Devam', align: 'num', cell: (row) => pct(row.ratePercent) },
        { key: 'absent', label: 'Gelmedi', align: 'num', cell: (row) => num(row.absent) },
        { key: 'late', label: 'Geç', align: 'num', cell: (row) => num(row.late) },
        { key: 'excused', label: 'İzinli', align: 'num', cell: (row) => num(row.excused) },
      ] });
      const chart = barChart(rows.filter((row) => row.ratePercent !== null && row.ratePercent !== undefined)
        .sort((a, b) => a.ratePercent - b.ratePercent)
        .map((row) => ({ label: row.label, value: Number(row.ratePercent), display: pct(row.ratePercent) })), { max: 15 });
      return [chart, table.node];
    }, { rows: 5, columns: 6 });

  const compliance = staticList({
    load: () => app.api.get('/v1/admin/attendance/teacher-compliance', { from: range.from, to: range.to }),
    dense: true,
    map: (items) => [...items].sort((a, b) => (a.onTimePercent ?? 101) - (b.onTimePercent ?? 101)),
    rowKey: (row) => row.teacher?.id,
    columns: [
      { key: 'teacher', label: 'Öğretmen', width: 'minmax(0, 1.6fr)', cell: (row) => personName(row.teacher) },
      { key: 'sessions', label: 'Ders', align: 'num', cell: (row) => num(row.sessions) },
      { key: 'onTime', label: 'Zamanında', align: 'num', cell: (row) => num(row.takenOnTime) },
      { key: 'late', label: 'Geç alındı', align: 'num', cell: (row) => num(row.takenLate) },
      { key: 'missing', label: 'Alınmadı', align: 'num', cell: (row) => (row.notTaken
        ? badge(num(row.notTaken), 'bad') : '0') },
      { key: 'pct', label: 'Zamanında %', align: 'num', cell: (row) => pct(row.onTimePercent) },
      { key: 'delay', label: 'Ort. gecikme', align: 'num', cell: (row) => (row.avgDelayMinutes === null
        || row.avgDelayMinutes === undefined ? '—' : `${num(row.avgDelayMinutes, 0)} dk`) },
    ],
    empty: { title: 'Bu aralıkta öğretmen kaydı yok' },
  });

  const leaves = loader(() => app.api.get('/v1/admin/attendance/leave-stats', { from: range.from, to: range.to }),
    (data) => {
      const reasons = (data.byReason || []).map((row) => ({ label: LEAVE_REASONS[row.reason] || row.reason,
        value: Number(row.count || 0), display: num(row.count) }));
      const kinds = (data.byKind || []).map((row) => [LEAVE_KINDS[row.kind] || row.kind, num(row.count)]);
      if (!data.requestCount) return empty('Bu aralıkta izin talebi yok');
      return [h('div', 'ak-muted', `Toplam ${num(data.requestCount)} izin talebi`), barChart(reasons, { max: 8 }),
        kv(kinds)];
    }, { rows: 3, columns: 3 });

  node.append(...[
    app.can('admin_getAttendanceRates') ? card('Devam oranı', (() => {
      const wrap = h('div', 'ak-stack');
      wrap.append(chips.node, rates.node);
      return wrap;
    })()) : null,
    app.can('admin_getTeacherCompliance') ? card('Öğretmen uyumu', compliance.node,
      '“Ders yapıldı” kanıtı: yoklamanın zamanında alınması') : null,
    app.can('admin_getLeaveStats') ? card('İzin nedenleri', leaves.node) : null,
  ].filter(Boolean));

  function reload() {
    const message = rangeProblem(range.from, range.to);
    warn.replaceChildren(message ? alertBox(message, 'warn') : '');
    if (message) return;
    if (app.can('admin_getAttendanceRates')) rates.reload();
    if (app.can('admin_getTeacherCompliance')) compliance.reload();
    if (app.can('admin_getLeaveStats')) leaves.reload();
  }
  return { node, reload, destroy() { picker.destroy?.(); } };
}

/** Dışa aktarım penceresi: tür + biçim + aralık (+ isteğe bağlı sınıflar). */
export async function exportDialog(app, range, { studentIds = null } = {}) {
  const classes = await app.lookups.classGroups().catch(() => []);
  const request = await formDialog(app, {
    title: 'Yoklama dışa aktar',
    description: 'Dosya arka planda hazırlanır; hazır olunca iner. Erişim denetim kaydına yazılır.',
    value: { kind: studentIds ? 'student_cards' : 'group_month', format: 'xlsx', from: range.from, to: range.to,
      groupIds: [] },
    fields: [
      { key: 'kind', label: 'Rapor', type: 'select', options: optionsOf(EXPORT_KINDS), required: true },
      { key: 'format', label: 'Biçim', type: 'select', options: optionsOf(EXPORT_FORMATS), required: true },
      { key: 'from', label: 'Başlangıç', type: 'date', required: true },
      { key: 'to', label: 'Bitiş', type: 'date', required: true },
      studentIds ? null : { key: 'groupIds', label: 'Sınıflar (boş = hepsi)', type: 'custom', wide: true,
        render: (draft, set) => multiChips(classes.map((item) => ({ value: item.id, label: item.name })),
          draft.groupIds || [], (list) => set(list)).node },
    ],
    submitLabel: 'Hazırla',
    onSubmit: (draft) => {
      const message = rangeProblem(draft.from, draft.to);
      if (message) throw Object.assign(new Error(message), { code: 'validation_error' });
      return { ...draft };
    },
  });
  if (!request) return null;
  const body = { kind: request.kind, format: request.format, from: request.from, to: request.to };
  if (studentIds?.length) body.studentIds = studentIds;
  else if (request.groupIds?.length) body.groupIds = request.groupIds;
  return exportJob(app, () => app.api.post('/v1/admin/attendance/exports', body),
    { name: `yoklama-${request.kind}-${request.from}`, label: 'Yoklama raporu hazırlanıyor…' });
}

// ========================================================= DÜZELTME KUYRUĞU

/** Onay kuyruğu satırları: öğrenci adına göre sıralı; dizinde olmayanlar sonda. */
export function reviewRows(review) {
  return [...(review?.rows || [])].sort((a, b) => (a.student ? 0 : 1) - (b.student ? 0 : 1)
    || String(a.student?.displayName || a.studentId).localeCompare(String(b.student?.displayName || b.studentId), 'tr'));
}

/** Öğrenci hücresi: ad + sınıf; `student: null` → "Dizinde yok" + kimlik. */
export function reviewStudentCell(row) {
  const cell = h('div', 'ak-cell-stack');
  if (row.student) {
    cell.append(h('span', '', row.student.displayName || `${row.student.firstName || ''} ${row.student.lastName || ''}`.trim()),
      row.student.classGroup?.name ? h('small', '', row.student.classGroup.name) : '');
  } else {
    cell.append(h('span', '', 'Dizinde yok'), h('small', '', row.studentId || '—'));
  }
  return cell;
}

export function reviewsTab(app, { onCount, openSession } = {}) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_listAttendanceReviews')) {
    node.append(noAccess());
    return { node, reload() {}, destroy() {} };
  }
  let status = 'pending';
  node.append(alertBox('Öğretmenin cihazında çevrimdışı alınıp düzeltme penceresi kapandıktan sonra gelen '
    + 'yoklamalar burada onay bekler. Onaylanınca işaretler yazılır; reddedilirse mevcut yoklama kalır.', 'info'));
  const chips = chipRow(Object.entries(REVIEW_STATUS).map(([key, [label]]) => ({ key, label })), status, (key) => {
    status = key || 'pending';
    if (!key) chips.set('pending');
    list.reload();
  });
  const list = pagedList({
    app,
    path: '/v1/admin/attendance/reviews',
    query: () => ({ status }),
    limit: 50,
    columns: [
      { key: 'session', label: 'Ders', width: 'minmax(0, 2fr)', cell: (row) => stack(summaryTitle(row.session),
        sessionWhen(row.session)) },
      { key: 'by', label: 'Gönderen', cell: (row) => personName(row.submittedBy) },
      { key: 'marked', label: 'Cihazda işaretlendi', cell: (row) => fmtDateTime(row.markedAt) },
      { key: 'received', label: 'Ulaştı', cell: (row) => fmtDateTime(row.receivedAt) },
      { key: 'rows', label: 'Öğrenci', align: 'num', cell: (row) => num((row.rows || []).length) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(REVIEW_STATUS, row.status) },
    ],
    onRow: (row) => openReview(row),
    onLoaded: (rows) => { if (status === 'pending') onCount?.(rows.length); },
    empty: { title: status === 'pending' ? 'Onay bekleyen yoklama yok' : 'Kayıt yok' },
  });
  node.append(chips.node, list.node);

  function openReview(review) {
    const panel = openDrawer(app, { title: 'Geç senkron yoklaması', subtitle: `${summaryTitle(review.session)} · ${sessionWhen(review.session)}` });
    // Satır öğrencinin kısa görünümünü taşır (`AttendanceReviewRow.student`, README §14 madde 72):
    // adlar buradan; oturum listesi ayrıca okunmaz. `student: null` = kimlik dizinde yok → kimlik gösterilir.
    const view = loader(async () => ({}), () => {
      const rows = reviewRows(review);
      const table = dataTable({ dense: true, rows, rowKey: (row) => row.studentId, columns: [
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (row) => reviewStudentCell(row) },
        { key: 'sent', label: 'Gönderilen', cell: (row) => markText(row) },
      ] });
      const tools = toolbar(
        review.status === 'pending' ? actionButton(app, 'admin_decideAttendanceReview', 'Onayla ve yaz',
          { variant: 'primary', onClick: () => decide('approve') }) : null,
        review.status === 'pending' ? actionButton(app, 'admin_decideAttendanceReview', 'Reddet',
          { variant: 'ghost', onClick: () => decide('reject') }) : null,
        h('span', 'kit-spacer'),
        button('Yoklama listesini aç', { variant: 'ghost', onClick: () => {
          panel.close();
          openSession?.(review.session.id, review.session.scheduleItem?.date);
        } }),
      );
      return [
        kv([['Gönderen', personName(review.submittedBy)], ['Cihazda işaretlendi', fmtDateTime(review.markedAt)],
          ['Akademi\'ye ulaştı', fmtDateTime(review.receivedAt)], ['Durum', statusBadge(REVIEW_STATUS, review.status)],
          review.decidedAt ? ['Karar', `${personName(review.decidedBy)} · ${fmtDateTime(review.decidedAt)}`] : null]),
        h('div', 'ak-muted', `${num(rows.length)} öğrenci işareti. Şu anki yoklamayı görmek için `
          + '“Yoklama listesini aç”.'),
        tools, table.node,
      ];
    }, { rows: 6, columns: 3 });
    panel.body.append(view.node);
    view.reload();

    async function decide(decision) {
      let note = null;
      if (decision === 'reject') {
        note = await reasonDialog(app, { title: 'Yoklamayı reddet', confirmLabel: 'Reddet',
          description: 'Mevcut yoklama değişmez. Gerekçe öğretmene bildirimle gider.' });
        if (!note) return;
      }
      try {
        const next = await app.api.post(`/v1/admin/attendance/reviews/${review.id}/decision`, { decision, note });
        app.toast(decision === 'approve' ? 'Yoklama onaylandı ve yazıldı.' : 'Yoklama reddedildi.', 'good');
        list.replace((row) => row.id === review.id, next);
        if (status === 'pending') list.remove((row) => row.id === review.id);
        panel.close();
      } catch (failure) {
        if (failure?.code === 'already_decided') {
          app.toast('Bu kayıt için zaten karar verilmiş.', 'warn');
          list.reload();
          panel.close();
          return;
        }
        app.toast(problem(failure), 'bad');
      }
    }
  }

  return { node, reload: () => list.reload(), destroy() { list.destroy(); } };
}

function markText(row) {
  if (!row?.status) return 'İşaretsiz';
  const extra = [row.status === 'late' && row.lateMinutes ? `${num(row.lateMinutes)} dk` : null,
    row.leftEarlyAt ? `çıktı ${fmtTime(row.leftEarlyAt)}` : null].filter(Boolean).join(', ');
  return `${MARK_LABEL[row.status] || row.status}${extra ? ` (${extra})` : ''}`;
}

// ================================================================== AYARLAR

const EDIT_WINDOW = { same_day: 'Aynı gün 23:59\'a kadar', hours: 'Ders bitişinden N saat' };

/** Sunucu ayarı → form taslağı (düz alanlar). */
export function settingsDraft(settings) {
  const s = settings || {};
  return {
    lateSuggestAfterMinutes: s.lateSuggestAfterMinutes ?? 10,
    absentSuggestAfterMinutes: s.absentSuggestAfterMinutes ?? 40,
    editKind: s.teacherEditWindow?.kind || 'same_day',
    editHours: s.teacherEditWindow?.hours ?? '',
    teacherAfterStartMinutes: s.reminders?.teacherAfterStartMinutes ?? 10,
    adminAfterEndMinutes: s.reminders?.adminAfterEndMinutes ?? 15,
    endOfDayDigestAt: s.reminders?.endOfDayDigestAt || '',
    absenceRatePercent: s.thresholds?.absenceRatePercent ?? 10,
    absenceRateWindowDays: s.thresholds?.absenceRateWindowDays ?? 30,
    consecutiveAbsenceDays: s.thresholds?.consecutiveAbsenceDays ?? 2,
    lateCountPerMonth: s.thresholds?.lateCountPerMonth ?? 3,
    unexcusedSteps: (s.thresholds?.unexcusedSteps || [5, 10, 15]).join(', '),
    autoApproveSameDayFullDay: Boolean(s.leave?.autoApproveSameDayFullDay),
    attachmentsEnabled: Boolean(s.leave?.attachmentsEnabled),
    retroExcuseMaxDays: s.leave?.retroExcuseMaxDays ?? 7,
    enabledKinds: s.leave?.enabledKinds || Object.keys(LEAVE_KINDS),
    noShowsBeforeApproval: s.etut?.noShowsBeforeApproval ?? '',
    delayMinutes: s.guardianNotifications?.delayMinutes ?? 5,
    notifyLate: s.guardianNotifications?.late !== false,
    notifyEarlyLeave: s.guardianNotifications?.earlyLeave !== false,
    notifyCorrections: s.guardianNotifications?.corrections !== false,
    notifyThresholds: s.guardianNotifications?.thresholds !== false,
  };
}

/** Form taslağı → `AttendanceSettingsPut`. Geçersiz değerde Türkçe hata fırlatır. */
export function settingsBody(draft, version) {
  const int = (key, label, min, max) => {
    const value = Number(String(draft[key] ?? '').trim());
    if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${label} ${min}–${max} arasında tam sayı olmalı.`);
    return value;
  };
  const steps = String(draft.unexcusedSteps || '').split(/[,\s]+/).filter(Boolean).map(Number);
  if (steps.some((value) => !Number.isInteger(value) || value < 1) || steps.length > 5) {
    throw new Error('Habersiz devamsızlık kademeleri en çok 5 tam sayı olmalı (ör. 5, 10, 15).');
  }
  const digest = String(draft.endOfDayDigestAt || '').trim();
  if (digest && !/^([01]\d|2[0-3]):[0-5]\d$/.test(digest)) throw new Error('Gün sonu özeti saatini 17:30 gibi yazın.');
  const late = int('lateSuggestAfterMinutes', '“Geç” önerisi', 0, 120);
  const absent = int('absentSuggestAfterMinutes', '“Gelmedi” önerisi', 1, 240);
  if (absent <= late) throw new Error('“Gelmedi” önerisi “Geç” önerisinden sonra olmalı.');
  const etut = String(draft.noShowsBeforeApproval ?? '').trim();
  return {
    lateSuggestAfterMinutes: late,
    absentSuggestAfterMinutes: absent,
    teacherEditWindow: draft.editKind === 'hours'
      ? { kind: 'hours', hours: int('editHours', 'Düzeltme penceresi', 1, 72) } : { kind: 'same_day', hours: null },
    reminders: {
      teacherAfterStartMinutes: int('teacherAfterStartMinutes', 'Öğretmen hatırlatması', 0, 120),
      adminAfterEndMinutes: int('adminAfterEndMinutes', 'Yönetici uyarısı', 0, 240),
      endOfDayDigestAt: digest || null,
    },
    thresholds: {
      absenceRatePercent: int('absenceRatePercent', 'Devamsızlık oranı', 1, 100),
      absenceRateWindowDays: int('absenceRateWindowDays', 'Oran penceresi', 7, 120),
      consecutiveAbsenceDays: int('consecutiveAbsenceDays', 'Art arda devamsızlık', 1, 30),
      lateCountPerMonth: int('lateCountPerMonth', 'Aylık geç kalma', 1, 60),
      unexcusedSteps: steps,
    },
    leave: {
      autoApproveSameDayFullDay: Boolean(draft.autoApproveSameDayFullDay),
      attachmentsEnabled: Boolean(draft.attachmentsEnabled),
      retroExcuseMaxDays: int('retroExcuseMaxDays', 'Geriye dönük mazeret', 0, 60),
      enabledKinds: Object.keys(LEAVE_KINDS).filter((key) => (draft.enabledKinds || []).includes(key)),
    },
    etut: { noShowsBeforeApproval: etut ? int('noShowsBeforeApproval', 'Etüt onay eşiği', 1, 20) : null },
    guardianNotifications: {
      delayMinutes: int('delayMinutes', 'Bildirim gecikmesi', 0, 60),
      late: Boolean(draft.notifyLate),
      earlyLeave: Boolean(draft.notifyEarlyLeave),
      corrections: Boolean(draft.notifyCorrections),
      thresholds: Boolean(draft.notifyThresholds),
    },
    ...(Number.isInteger(version) ? { expectedVersion: version } : {}),
  };
}

export function settingsTab(app) {
  const node = h('div', 'ak-stack');
  if (!app.can('admin_getAttendanceSettings')) {
    node.append(noAccess());
    return { node, reload() {}, destroy() {} };
  }
  const view = loader(() => app.api.get('/v1/admin/attendance/settings'), (settings) => render(settings),
    { rows: 6, columns: 2 });
  node.append(view.node);

  function render(settings) {
    const d = settingsDraft(settings);
    const onOff = (on) => (on ? badge('Açık', 'good') : badge('Kapalı', 'dim'));
    const flags = card('Bildirim bayrakları', kv([
      ['Veliye yoklama bildirimi', onOff(settings.guardianNotificationsEnabled)],
      ['İzin kararı bildirimi', onOff(settings.leaveDecisionNotificationsEnabled)],
    ]), 'Ayarlar → Kurum ayarları → Özellikler\'den değişir');
    const rules = card('Yoklama kuralları', kv([
      ['“Geç” önerisi', `ders başından ${num(d.lateSuggestAfterMinutes)} dk sonra`],
      ['“Gelmedi” önerisi', `ders başından ${num(d.absentSuggestAfterMinutes)} dk sonra`],
      ['Öğretmenin düzeltme penceresi', d.editKind === 'hours' ? `ders bitişinden ${num(d.editHours)} saat`
        : EDIT_WINDOW.same_day],
      ['Öğretmene hatırlatma', `ders başından ${num(d.teacherAfterStartMinutes)} dk sonra`],
      ['Yöneticiye uyarı', `ders bitişinden ${num(d.adminAfterEndMinutes)} dk sonra`],
      ['Gün sonu özeti', d.endOfDayDigestAt || 'Kapalı'],
    ]));
    const thresholds = card('Devam eşikleri (erken uyarı)', kv([
      ['Devamsızlık oranı', `%${num(d.absenceRatePercent)} · son ${num(d.absenceRateWindowDays)} gün`],
      ['Art arda devamsızlık', `${num(d.consecutiveAbsenceDays)} gün`],
      ['Aylık geç kalma', `${num(d.lateCountPerMonth)} kez`],
      ['Habersiz devamsızlık kademeleri', d.unexcusedSteps || '—'],
    ]));
    const leave = card('İzin', kv([
      ['Aynı gün tam gün izin', d.autoApproveSameDayFullDay ? 'Kendiliğinden onaylanır' : 'Onay bekler'],
      ['Rapor görseli', d.attachmentsEnabled ? 'Açık' : 'Kapalı'],
      ['Geriye dönük mazeret', `${num(d.retroExcuseMaxDays)} gün`],
      ['Açık izin türleri', d.enabledKinds.map((key) => LEAVE_KINDS[key] || key).join(', ') || '—'],
      ['Etüt onay eşiği', d.noShowsBeforeApproval ? `${num(d.noShowsBeforeApproval)} gelmeme sonrası` : 'Kapalı'],
    ]));
    const guardian = card('Veli bildirimleri', kv([
      ['Gecikme', `${num(d.delayMinutes)} dk`],
      ['Geç kalma', d.notifyLate ? 'Gönderilir' : 'Gönderilmez'],
      ['Erken çıkış', d.notifyEarlyLeave ? 'Gönderilir' : 'Gönderilmez'],
      ['Düzeltme', d.notifyCorrections ? 'Gönderilir' : 'Gönderilmez'],
      ['Eşik bilgilendirmesi', d.notifyThresholds ? 'Gönderilir' : 'Gönderilmez'],
    ]), settings.guardianNotificationsEnabled ? '' : 'Bayrak kapalı — şu an hiçbiri gitmez');
    const tools = toolbar(h('span', 'ak-muted', `Sürüm ${num(settings.version || 0)}${settings.updatedAt
      ? ` · son değişiklik ${fmtDateTime(settings.updatedAt)}` : ''}`), h('span', 'kit-spacer'),
    actionButton(app, 'admin_putAttendanceSettings', 'Düzenle', { variant: 'primary', onClick: () => edit(settings) }));
    return [tools, flags, rules, thresholds, leave, guardian];
  }

  async function edit(settings) {
    const saved = await formDialog(app, {
      title: 'Yoklama ayarları',
      wide: true,
      value: settingsDraft(settings),
      fields: [
        { key: 'lateSuggestAfterMinutes', label: '“Geç” önerisi (dk)', type: 'number', min: 0, max: 120, required: true },
        { key: 'absentSuggestAfterMinutes', label: '“Gelmedi” önerisi (dk)', type: 'number', min: 1, max: 240, required: true },
        { key: 'editKind', label: 'Öğretmenin düzeltme penceresi', type: 'select', options: optionsOf(EDIT_WINDOW) },
        { key: 'editHours', label: 'Saat (yalnız “N saat” seçiliyse)', type: 'number', min: 1, max: 72 },
        { key: 'teacherAfterStartMinutes', label: 'Öğretmene hatırlatma (ders başından dk)', type: 'number', min: 0, max: 120 },
        { key: 'adminAfterEndMinutes', label: 'Yöneticiye uyarı (ders bitişinden dk)', type: 'number', min: 0, max: 240 },
        { key: 'endOfDayDigestAt', label: 'Gün sonu özeti saati (boş = kapalı)', maxLength: 5, placeholder: 'SS:DD' },
        { key: 'absenceRatePercent', label: 'Devamsızlık oranı eşiği (%)', type: 'number', min: 1, max: 100 },
        { key: 'absenceRateWindowDays', label: 'Oran penceresi (gün)', type: 'number', min: 7, max: 120 },
        { key: 'consecutiveAbsenceDays', label: 'Art arda devamsızlık (gün)', type: 'number', min: 1, max: 30 },
        { key: 'lateCountPerMonth', label: 'Aylık geç kalma (kez)', type: 'number', min: 1, max: 60 },
        { key: 'unexcusedSteps', label: 'Habersiz devamsızlık kademeleri', placeholder: '5, 10, 15',
          hint: 'Virgülle en çok 5 sayı; her kademe erken uyarıya düşer.' },
        { key: 'autoApproveSameDayFullDay', label: 'Aynı gün tam gün izin kendiliğinden onaylansın', type: 'checkbox', wide: true },
        { key: 'attachmentsEnabled', label: 'İzinde rapor görseli eklenebilsin (sağlık verisi)', type: 'checkbox', wide: true },
        { key: 'retroExcuseMaxDays', label: 'Geriye dönük mazeret (gün)', type: 'number', min: 0, max: 60 },
        { key: 'noShowsBeforeApproval', label: 'Etüt onay eşiği (boş = kapalı)', type: 'number', min: 1, max: 20 },
        { key: 'enabledKinds', label: 'Açık izin türleri', type: 'custom', wide: true,
          render: (draft, set) => multiChips(Object.entries(LEAVE_KINDS).map(([value, label]) => ({ value, label })),
            draft.enabledKinds || [], (list) => set(list)).node },
        { key: 'delayMinutes', label: 'Veli bildirimi gecikmesi (dk)', type: 'number', min: 0, max: 60 },
        { key: 'notifyLate', label: 'Veliye geç kalma bildirimi', type: 'checkbox' },
        { key: 'notifyEarlyLeave', label: 'Veliye erken çıkış bildirimi', type: 'checkbox' },
        { key: 'notifyCorrections', label: 'Veliye düzeltme bildirimi', type: 'checkbox' },
        { key: 'notifyThresholds', label: 'Veliye eşik bilgilendirmesi', type: 'checkbox' },
      ],
      onSubmit: async (draft) => {
        let body;
        try {
          body = settingsBody(draft, settings.version);
        } catch (failure) {
          throw Object.assign(new Error(failure.message), { code: 'validation_error' });
        }
        return app.api.put('/v1/admin/attendance/settings', body);
      },
    });
    if (!saved) return;
    app.toast('Yoklama ayarları kaydedildi.', 'good');
    view.reload();
  }

  return { node, reload: () => view.reload(), destroy() {} };
}

// ================================================================ GEÇMİŞ

/** Oturumun değişmez yoklama geçmişi (kim, ne zaman, hangi kanal, eski → yeni). */
export function openHistory(app, sessionId, title = 'Yoklama geçmişi') {
  const panel = openDrawer(app, { title, subtitle: 'Denetim kaydı — değiştirilemez' });
  const list = pagedList({
    app,
    path: `/v1/admin/attendance/sessions/${sessionId}/history`,
    limit: 50,
    dense: true,
    columns: [
      { key: 'at', label: 'Zaman', cell: (row) => fmtDateTime(row.at) },
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => row.student?.displayName || '—' },
      { key: 'change', label: 'Değişiklik', width: 'minmax(0, 1.4fr)', cell: (row) => `${row.from ? markText(row.from)
        : 'Yok'} → ${row.to ? markText(row.to) : 'Silindi'}` },
      { key: 'by', label: 'Kim', cell: (row) => stack(row.actor ? personName(row.actor) : 'Sistem',
        row.onBehalfOf ? `KM: ${row.onBehalfOf.replace(/^km:user:/, '')}` : '') },
      { key: 'channel', label: 'Kanal', cell: (row) => CHANNELS[row.channel] || row.channel || '—' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.2fr)', cell: (row) => row.reason || '—' },
    ],
    empty: { title: 'Geçmiş yok', text: 'Bu oturumda henüz yoklama değişikliği yok.' },
  });
  panel.body.append(list.node);
  list.reload();
  return panel;
}

