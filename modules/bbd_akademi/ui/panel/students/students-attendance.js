// BBD Akademi — öğrenci kartı › Program ve yoklama › Yoklama: devam takvimi, dönem karnesi ve
// veli adına izin (KM-2, Akademi F-Y11/F-Y14/F-Y15).
//
//   · Devam takvimi  — `students_getAttendanceCalendar` (ay, ön hesaplı günlük özet; öğrenci ve
//                      velinin uygulamada gördüğü takvimle AYNI veri). Güne tıklayınca
//                      `students_getAttendanceDay` o günün derslerini (saat, öğretmen, durum, geç,
//                      erken çıkış, izin kaynağı) açar — gün başına tek istek, yalnız tıklanınca.
//   · Devam karnesi  — `admin_getStudentAttendanceCard` (dönem seçilebilir): oran, ders bazında
//                      devamsızlık ve aylık oran.
//   · Veli adına izin — telefonla / yüz yüze gelen izni yönetici veli adına girer
//                      (`admin_createLeaveRequest`); "onayla" işaretliyse ilgili dersler kendiliğinden
//                      "İzinli" olur. Veli kartın başlığındaki veliler arasından seçilir.
//                      MAZERET (V15, README §14 madde 83): tek gün + o günün "Gelmedi" dersleri
//                      işaretlenir (`students_getAttendanceDay`; uygulamadaki yönetici formuyla aynı),
//                      seçilenler `attendanceSessionIds` olarak gider; kurumun geriye dönük penceresi
//                      (`retroExcuseMaxDays`) formda yazılır, pencere dışı gün gönderilmez.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { barChart } from '../../../ui-kit/charts.js';
import {
  actionButton, clockInput, empty, errorBox, fmtDate, fmtTime, formDialog, istanbulStamp, loader, num,
  optionsOf, personName, selectBox, todayIst,
} from '../common.js';
import { LEAVE_KINDS, LEAVE_REASONS, MARK_LABEL } from '../attendance-manage.js';
import { guardianRole } from './students-detail.js';

const DAY_STATUS = { present: ['Geldi', 'good'], partial: ['Kısmen', 'warn'], absent: ['Gelmedi', 'bad'],
  excused: ['İzinli', 'info'], none: ['Ders yok', 'dim'] };
const EXCUSE_SOURCE = { leave_request: 'izin talebi', early_leave: 'erken çıkış', retro_excuse: 'mazeret',
  manual: 'elle' };
const SESSION_STATUS = { cancelled: 'İptal', holiday: 'Tatil', orphaned: 'Programdan kalktı' };
const WEEK_HEAD = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz'];
const CHANNEL = { phone: 'Telefonla', in_person: 'Yüz yüze' };
const pct = (value) => (value === null || value === undefined ? '—' : `%${num(value, 1)}`);
/** Kurum ayarı okunana dek varsayılan mazeret penceresi (`AttendanceSettings.leave.retroExcuseMaxDays`). */
const RETRO_DEFAULT_DAYS = 7;

/** Mazerete uygun ders: "Gelmedi", iptal/tatil değil, bekleyen başka bildirimi yok (React `RetroExcuseRules`). */
export function retroEligible(item) {
  return item?.status === 'absent' && item.sessionStatus !== 'cancelled' && item.sessionStatus !== 'holiday'
    && !item.pendingLeaveRequestId;
}

/**
 * Mazeret formunun durumu → gönderilemiyorsa ileti (yoksa null). `day`:
 * `students_getAttendanceDay` yanıtı; `picked`: seçilen oluşum anahtarları.
 */
export function retroProblem(day, picked) {
  if (!day) return 'Günün yoklaması yüklenmedi; günü yeniden seçin.';
  if (!day.retroExcuseOpen) {
    return `Mazeret en çok ${num(day.retroExcuseMaxDays ?? RETRO_DEFAULT_DAYS)} gün geriye bildirilebilir `
      + '(ya da mazeret kurumda kapalı: Yoklama → Ayarlar → Açık izin türleri).';
  }
  const eligible = (day.sessions || []).filter(retroEligible);
  if (!eligible.length) return 'Bu gün "Gelmedi" işaretli (mazereti bekleyen olmayan) ders yok.';
  if (!picked?.length) return 'Mazeret bildirilecek en az bir ders seçin.';
  return null;
}

/** Ay takvimi + seçilen günün dersleri. `month`: 'YYYY-MM' (dışarıdan değişir). */
export function attendanceCalendar(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let month = todayIst().slice(0, 7);
  let picked = null;
  const dayBox = h('div', 'ak-stack');
  const view = loader(() => app.api.get(`/v1/students/${id}/attendance/calendar`, { month }), (data) => render(data),
    { rows: 5, columns: 7 });
  node.append(view.node, dayBox);

  function render(data) {
    const summary = data.summary || {};
    const grid = h('div', 'ak-att-calendar');
    for (const label of WEEK_HEAD) grid.append(h('span', 'ak-att-calendar-head', label));
    const days = data.days || [];
    const first = days[0]?.date || `${month}-01`;
    const [year, mon, dd] = first.split('-').map(Number);
    const lead = (new Date(Date.UTC(year, mon - 1, dd)).getUTCDay() + 6) % 7;
    for (let index = 0; index < lead; index += 1) grid.append(h('span'));
    for (const item of days) {
      const cell = h('button', `ak-att-day ak-att-is-${item.status}${item.date === picked ? ' on' : ''}`);
      cell.type = 'button';
      const [label] = DAY_STATUS[item.status] || [item.status];
      cell.append(h('b', '', String(Number(item.date.slice(8, 10)))),
        h('small', '', item.sessionCount ? `${num(item.attendedCount)}/${num(item.sessionCount)}` : '—'));
      cell.setAttribute('aria-label', `${fmtDate(item.date)}: ${label}${item.sessionCount
        ? `, ${num(item.attendedCount)}/${num(item.sessionCount)} ders` : ''}${item.lateMinutes
        ? `, ${num(item.lateMinutes)} dk geç` : ''}`);
      cell.disabled = item.status === 'none';
      cell.addEventListener('click', () => {
        picked = item.date;
        for (const other of grid.querySelectorAll('.ak-att-day')) other.classList.remove('on');
        cell.classList.add('on');
        openDay(item.date);
      });
      grid.append(cell);
    }
    const today = data.today ? h('div', 'ak-muted', `Bugün: ${num(data.today.sessionCount ?? 0)} dersin `
      + `${num(data.today.attendedCount ?? 0)}'ine katıldı${data.today.lateCount ? ` · ${num(data.today.lateCount)} geç` : ''}`) : null;
    return [kpiRow([
      { label: 'Devam', value: pct(summary.ratePercent) },
      { label: 'Geldi', value: num(summary.present) },
      { label: 'Gelmedi', value: num(summary.absent), tone: summary.absent ? 'bad' : '' },
      { label: 'Geç', value: num(summary.late), tone: summary.late ? 'warn' : '',
        title: summary.lateMinutesTotal ? `Toplam ${num(summary.lateMinutesTotal)} dk` : '' },
      { label: 'İzinli', value: num(summary.excused) },
    ]), today, grid, h('small', 'ak-muted', 'Güne tıklayınca o günün dersleri açılır.')];
  }

  async function openDay(date) {
    dayBox.replaceChildren(h('div', 'ak-muted', 'Yükleniyor…'));
    try {
      const data = await app.api.get(`/v1/students/${id}/attendance/days/${date}`);
      const rows = [...(data.sessions || [])].sort((a, b) => String(a.startsAt).localeCompare(String(b.startsAt)));
      const table = dataTable({ dense: true, rows, rowKey: (row) => row.sessionId, columns: [
        { key: 'time', label: 'Saat', width: '110px', cell: (row) => `${fmtTime(row.startsAt)}–${fmtTime(row.endsAt)}` },
        { key: 'subject', label: 'Ders', width: 'minmax(0, 1.4fr)', cell: (row) => row.subject?.name || '—' },
        { key: 'teacher', label: 'Öğretmen', cell: (row) => personName(row.teacher) },
        { key: 'status', label: 'Durum', cell: (row) => {
          if (SESSION_STATUS[row.sessionStatus]) return badge(SESSION_STATUS[row.sessionStatus], 'dim');
          if (!row.status) return badge('Alınmadı', 'dim');
          const extra = [row.status === 'late' && row.lateMinutes ? `${num(row.lateMinutes)} dk` : null,
            row.leftEarlyAt ? `çıktı ${fmtTime(row.leftEarlyAt)}` : null,
            row.excuseSource ? EXCUSE_SOURCE[row.excuseSource] : null].filter(Boolean).join(' · ');
          return `${MARK_LABEL[row.status] || row.status}${extra ? ` (${extra})` : ''}`;
        } },
      ], empty: empty('Bu gün ders yok') });
      const [label, tone] = DAY_STATUS[data.status] || [data.status, 'dim'];
      const head = h('div', 'ak-inline');
      head.append(h('b', '', fmtDate(date)), badge(label, tone),
        button('Kapat', { variant: 'ghost', onClick: () => dayBox.replaceChildren() }));
      dayBox.replaceChildren(head, table.node);
    } catch (failure) {
      dayBox.replaceChildren(errorBox(failure, () => openDay(date)));
    }
  }

  return {
    node,
    setMonth(next) {
      month = next;
      picked = null;
      dayBox.replaceChildren();
      view.reload();
    },
    reload: () => view.reload(),
  };
}

/** Dönem devam karnesi. */
export function attendanceTermCard(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  let termId = '';
  const terms = selectBox([{ value: '', label: 'Güncel dönem' }], '', (value) => { termId = value; view.reload(); },
    { label: 'Dönem' });
  app.lookups.terms().then((list) => terms.options([{ value: '', label: 'Güncel dönem' },
    ...list.map((term) => ({ value: term.id, label: term.name }))])).catch(() => {});
  const view = loader(() => app.api.get(`/v1/admin/students/${id}/attendance-card`, { termId: termId || undefined }),
    (data) => {
      const subjects = dataTable({ dense: true, rows: data.bySubject || [], rowKey: (row) => row.subject?.id || 'x',
        columns: [
          { key: 'subject', label: 'Ders', width: 'minmax(0, 1.6fr)', cell: (row) => row.subject?.name || 'Dersi yok' },
          { key: 'rate', label: 'Devam', align: 'num', cell: (row) => pct(row.ratePercent) },
          { key: 'absent', label: 'Gelmedi', align: 'num', cell: (row) => num(row.absent) },
          { key: 'late', label: 'Geç', align: 'num', cell: (row) => num(row.late) },
        ], empty: empty('Ders kaydı yok') });
      const monthly = barChart((data.monthly || []).filter((row) => row.ratePercent !== null && row.ratePercent !== undefined)
        .map((row) => ({ label: row.month, value: Number(row.ratePercent), display: pct(row.ratePercent) })), { max: 12 });
      return [
        h('div', 'ak-muted', `${fmtDate(data.from)} – ${fmtDate(data.to)}`),
        kpiRow([
          { label: 'Devam oranı', value: pct(data.ratePercent) },
          { label: 'Ders', value: num(data.sessionCount) },
          { label: 'Geldi', value: num(data.present) },
          { label: 'Gelmedi', value: num(data.absent), tone: data.absent ? 'bad' : '' },
          { label: 'Geç', value: num(data.late), title: `Toplam ${num(data.lateMinutesTotal)} dk` },
          { label: 'İzinli', value: num(data.excused) },
        ]),
        subjects.node, monthly,
      ];
    }, { rows: 4, columns: 4 });
  node.append(terms.node, view.node);
  view.reload();
  return { node, reload: () => view.reload() };
}

/** Veli adına izin gir. `guardians`: kart başlığındaki veliler. */
export async function leaveOnBehalf(app, studentId, guardians = []) {
  const options = (guardians || []).filter((row) => row?.id).map((row) => ({ value: row.id,
    label: [row.displayName, row.relationLabel].filter(Boolean).join(' · ') || row.id }));
  if (!options.length) {
    app.toast('Bu öğrencinin kayıtlı velisi yok; izin veli adına girilemez. Veliyi KM öğrenci kaydından ekleyin.', 'warn');
    return null;
  }
  const today = todayIst();
  const clockRule = (raw) => (/^([01]\d|2[0-3]):[0-5]\d$/.test(String(raw || '')) ? null : 'Saati 14:30 gibi yazın.');
  const clockField = (key, label, show) => ({ key, label, type: 'custom', render: (draft, set) => {
    const control = clockInput(draft[key] || '', (value) => set(value), label);
    const wrap = h('div');
    wrap.append(control.node);
    const paint = (next) => { wrap.hidden = !show(next); };
    paint(draft);
    return { node: wrap, update: paint };
  }, validate: (raw, draft) => (show(draft) ? clockRule(raw) : null) });
  // Mazeret: seçili günün yoklaması (gün başına bir istek, yalnız tür "Mazeret"ken).
  const retro = { date: null, day: null, failure: null };
  const retroField = { key: 'attendanceSessionIds', label: '', type: 'custom', render: (draft, set) => {
    const wrap = h('div', 'ak-stack ak-retro');
    const head = h('b', '', 'Mazeret bildirilecek dersler');
    const hint = h('small', 'ak-muted');
    const body = h('div', 'ak-stack');
    wrap.append(head, hint, body);
    let picked = [];
    let token = 0;
    const paintHint = () => {
      hint.textContent = `Mazeret, yoklamada "Gelmedi" görünen geçmiş bir gün içindir (bugün dahil en çok `
        + `${num(retro.day?.retroExcuseMaxDays ?? RETRO_DEFAULT_DAYS)} gün geriye). Seçilen dersler onayda `
        + '"İzinli (mazeret)" olur; günün diğer dersleri değişmez.';
    };
    const paintSessions = () => {
      body.replaceChildren();
      const day = retro.day;
      if (retro.failure) { body.append(errorBox(retro.failure, () => load(retro.date, true))); return; }
      if (!day) { body.append(h('div', 'ak-muted', 'Yükleniyor…')); return; }
      const absent = (day.sessions || []).filter((item) => item.status === 'absent'
        && item.sessionStatus !== 'cancelled' && item.sessionStatus !== 'holiday')
        .sort((a, b) => String(a.startsAt).localeCompare(String(b.startsAt)));
      if (!absent.length) {
        body.append(h('div', 'ak-muted', `${fmtDate(day.date)}: "Gelmedi" işaretli ders yok. Yoklamada "Gelmedi" görünen bir gün seçin.`));
        return;
      }
      for (const item of absent) {
        const pending = Boolean(item.pendingLeaveRequestId);
        const row = h('label', 'ak-inline ak-retro-row');
        const box = h('input', 'kit-check');
        box.type = 'checkbox';
        box.checked = !pending && picked.includes(item.occurrenceKey);
        box.disabled = pending || !day.retroExcuseOpen;
        box.addEventListener('change', () => {
          picked = box.checked ? [...picked.filter((key) => key !== item.occurrenceKey), item.occurrenceKey]
            : picked.filter((key) => key !== item.occurrenceKey);
          set(picked);
        });
        row.append(box, h('span', '', `${fmtTime(item.startsAt)}–${fmtTime(item.endsAt)} ${item.subject?.name || 'Ders'}`),
          h('small', 'ak-muted', pending ? 'Mazeret bekliyor' : personName(item.teacher)));
        body.append(row);
      }
      if (!day.retroExcuseOpen) body.append(alertBox(retroProblem(day, picked), 'warn'));
    };
    async function load(date, force = false) {
      if (!force && retro.date === date && (retro.day || retro.failure)) return;
      const mine = ++token;
      retro.date = date;
      retro.day = null;
      retro.failure = null;
      picked = [];
      set(picked);
      paintSessions();
      try {
        const day = await app.api.get(`/v1/students/${studentId}/attendance/days/${date}`);
        if (mine !== token) return;
        retro.day = day;
        // İlk seçim: günün mazerete uygun bütün dersleri (uygulamadaki formla aynı).
        picked = day.retroExcuseOpen ? (day.sessions || []).filter(retroEligible).map((item) => item.occurrenceKey) : [];
        set(picked);
      } catch (failure) {
        if (mine !== token) return;
        retro.failure = failure;
      }
      paintHint();
      paintSessions();
    }
    const paint = (next) => {
      const on = next.kind === 'retro_excuse';
      wrap.hidden = !on;
      if (!on) return;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(String(next.from || ''))) return;
      load(next.from);
    };
    paintHint();
    paint(draft);
    return { node: wrap, update: paint };
  }, validate: (raw, draft) => (draft.kind === 'retro_excuse' ? retroProblem(retro.date === draft.from ? retro.day
    : null, raw) : null) };
  return formDialog(app, {
    title: 'Veli adına izin gir',
    description: 'Telefonla ya da yüz yüze gelen izin ya da geçmiş devamsızlık için mazeret. Onaylanırsa ilgili '
      + 'dersler kendiliğinden “İzinli” olur; öğretmen gerekçeyi görmez.',
    wide: true,
    value: { kind: 'full_day', from: today, to: today, reason: 'illness', guardianId: options[0].value,
      channel: 'phone', approve: true, description: '', pickupPersonName: '', startTime: '', endTime: '', leaveAt: '',
      attendanceSessionIds: [] },
    fields: [
      { key: 'guardianId', label: 'Adına girilen veli', type: 'select', options, required: true },
      { key: 'channel', label: 'Nasıl geldi', type: 'select', options: optionsOf(CHANNEL) },
      { key: 'kind', label: 'İzin türü', type: 'select', options: optionsOf(LEAVE_KINDS), required: true },
      { key: 'reason', label: 'Gerekçe', type: 'select', options: optionsOf(LEAVE_REASONS), required: true },
      { key: 'from', label: 'Başlangıç günü (mazerette devamsızlık günü)', type: 'date', required: true },
      { key: 'to', label: 'Bitiş günü', type: 'date', required: true,
        hint: 'Erken çıkış, saat aralığı ve mazerette kullanılmaz (tek gün).' },
      clockField('startTime', 'Başlangıç saati', (d) => d.kind === 'partial'),
      clockField('endTime', 'Bitiş saati', (d) => d.kind === 'partial'),
      clockField('leaveAt', 'Çıkış saati', (d) => d.kind === 'early_leave'),
      retroField,
      { key: 'pickupPersonName', label: 'Teslim alacak kişi (erken çıkış, isteğe bağlı)', maxLength: 80 },
      { key: 'description', label: 'Açıklama (isteğe bağlı)', type: 'textarea', maxLength: 500, wide: true },
      { key: 'approve', label: 'Girişle birlikte onayla', type: 'checkbox', wide: true },
    ],
    submitLabel: 'İzni kaydet',
    onSubmit: (draft, { key }) => {
      const sameDay = draft.kind === 'partial' || draft.kind === 'early_leave' || draft.kind === 'retro_excuse';
      const to = sameDay ? draft.from : draft.to;
      if (to < draft.from) throw Object.assign(new Error('Bitiş günü başlangıçtan önce olamaz.'), { code: 'validation_error' });
      if (draft.kind === 'partial' && draft.endTime <= draft.startTime) {
        throw Object.assign(new Error('Bitiş saati başlangıçtan sonra olmalı.'), { code: 'validation_error' });
      }
      if (draft.kind === 'retro_excuse' && draft.from > today) {
        throw Object.assign(new Error('Mazeret geçmiş (ya da bugünkü) bir gün içindir.'), { code: 'validation_error' });
      }
      const body = {
        kind: draft.kind, from: draft.from, to, reason: draft.reason, guardianId: draft.guardianId,
        channel: draft.channel || 'phone', approve: Boolean(draft.approve),
        description: String(draft.description || '').trim() || null,
        startTime: draft.kind === 'partial' ? draft.startTime : null,
        endTime: draft.kind === 'partial' ? draft.endTime : null,
        leaveAt: draft.kind === 'early_leave' ? istanbulStamp(draft.from, draft.leaveAt) : null,
        pickupPersonName: draft.kind === 'early_leave' ? (String(draft.pickupPersonName || '').trim() || null) : null,
      };
      if (draft.kind === 'retro_excuse') body.attendanceSessionIds = [...(draft.attendanceSessionIds || [])];
      return app.api.post(`/v1/admin/students/${studentId}/leave-requests`, body, { key });
    },
  });
}

/** Yoklama bölümünün üstündeki araç: "Veli adına izin gir" + karne/takvim kartları. */
export function attendanceExtras(ctx, { onLeaveSaved } = {}) {
  const { app, id } = ctx;
  const leave = actionButton(app, 'admin_createLeaveRequest', 'Veli adına izin gir', { variant: 'primary',
    onClick: async () => {
      const data = await ctx.ready;
      const guardians = (data?.guardians || []).map((row) => ({ ...row, relationLabel: guardianRole(row) }));
      const saved = await leaveOnBehalf(app, id, guardians);
      if (!saved) return;
      app.toast(saved.status === 'approved' ? 'İzin kaydedildi ve onaylandı; ilgili dersler “İzinli” oldu.'
        : 'İzin kaydedildi; onay bekliyor.', 'good');
      onLeaveSaved?.(saved);
    } });
  const calendar = app.can('students_getAttendanceCalendar') ? attendanceCalendar(ctx) : null;
  const term = app.can('admin_getStudentAttendanceCard') ? attendanceTermCard(ctx) : null;
  return {
    leave,
    calendar,
    cards: [calendar ? card('Devam takvimi', calendar.node, 'Öğrenci ve velinin uygulamada gördüğüyle aynı') : null,
      term ? card('Devam karnesi (dönem)', term.node) : null].filter(Boolean),
    note: !calendar && !term ? alertBox('Devam takvimi ve karnesi için yetkiniz yok.', 'info') : null,
  };
}
